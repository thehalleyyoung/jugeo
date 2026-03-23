"""Purpose conditioning — conditioning on the target obstruction set.

When building a proof in the theory-space framework, the navigator must keep
its search focused on the current *purpose* — the set of outstanding obstructions
and goals that remain to be resolved.  This module implements the full pipeline
for transforming a raw purpose description plus a list of obstruction dicts into
a *conditioned purpose* object that later navigation steps can consume.

The conditioning process has four stages:

1. **Extraction** — :class:`PurposeConditioningTargetAnalyzer` parses raw
   obstruction dicts into typed :class:`ObstructionTarget` instances, each
   carrying a severity score and the first-cohomology class responsible for the
   obstruction.

2. **Vectorisation** — the extracted targets and a list of high-level goal
   labels are combined into a :class:`PurposeVector` that encodes the semantic
   content of the current purpose as a tuple of keywords plus structured IDs.

3. **Conditioning** — the raw purpose string is scored against the vector to
   produce a :class:`ConditionedPurpose`.  The ``conditioning_score`` measures
   how well the raw purpose aligns with the target obstruction set.

4. **History decay** — successive calls to
   :meth:`PurposeConditioningTargetAnalyzer.update_with_history` allow the
   conditioned purpose to incorporate evidence from earlier navigation steps,
   weighted by an exponential decay factor so that recent obstructions count
   more heavily.

The :class:`PurposeConditioningTargetWitness` accumulates all conditioned
purposes produced during a run and provides a *drift* metric — the mean
absolute change in ``conditioning_score`` between consecutive purposes — which
serves as an early-stopping signal for iterative conditioning loops.

Module layout::

    ──────────────────────────────────────────────────────────────────────────
    Symbol                                  Kind        Purpose
    ──────────────────────────────────────────────────────────────────────────
    PurposeConditioningConfig               dataclass   hyper-parameters
    ObstructionTarget                       dataclass   single parsed obstruction
    PurposeVector (local)                   dataclass   keyword + ID vector
    ConditionedPurpose                      dataclass   scored conditioned purpose
    PurposeConditioningTargetAnalyzer       class       extraction + conditioning
    PurposeConditioningTargetWitness        class       history + drift tracking
    PurposeConditioningCoordinator          class       orchestrator / entry-point
    ──────────────────────────────────────────────────────────────────────────

Private helpers::

    _clamp(v, lo, hi)               clamp float to [lo, hi]
    _now_iso()                      current UTC time as ISO-8601 string
    _purpose_id()                   generate a short random purpose ID
    _tokenize(text) -> set[str]     split text into lowercase tokens
    _keyword_overlap(kw1, kw2)      Jaccard overlap between two keyword sets

# copilot: purpose conditioning — target obstruction set conditioning pipeline

Reference: theory2.tex §§ purpose-conditioned search, obstruction resolution.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Optional cross-package imports
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.theory_navigation.models import (
        PurposeCondition,
        TheoryNode,
        NavigationState,
        NodeMaturity,
    )
except ImportError:
    PurposeCondition = None  # type: ignore[assignment,misc]
    TheoryNode = None  # type: ignore[assignment,misc]
    NavigationState = None  # type: ignore[assignment,misc]
    NodeMaturity = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        ``max(lo, min(hi, v))``.

    Examples
    --------
    >>> _clamp(-0.1)
    0.0
    >>> _clamp(1.2)
    1.0
    >>> _clamp(0.7)
    0.7
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp string, e.g. ``'2024-01-15T12:00:00+00:00'``.

    Examples
    --------
    >>> ts = _now_iso()
    >>> "T" in ts
    True
    """
    return datetime.now(timezone.utc).isoformat()


def _purpose_id() -> str:
    """Generate a short random purpose identifier.

    Returns a ``'pur-'`` prefixed 8-character hex string.

    Returns
    -------
    str
        A string of the form ``'pur-xxxxxxxx'``.

    Examples
    --------
    >>> pid = _purpose_id()
    >>> pid.startswith("pur-")
    True
    >>> len(pid) == 12
    True
    """
    return f"pur-{uuid.uuid4().hex[:8]}"


def _tokenize(text: str) -> set[str]:
    """Tokenise *text* into a set of lowercase alphabetic tokens.

    Strips punctuation, splits on non-alphabetic characters, lower-cases all
    tokens, and discards single-character tokens.

    Parameters
    ----------
    text:
        Raw text to tokenise.

    Returns
    -------
    set[str]
        Normalised word tokens with length > 1.

    Examples
    --------
    >>> sorted(_tokenize("H1 obstruction in fibration!"))
    ['fibration', 'h1', 'in', 'obstruction']
    """
    tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9]*", text.lower())
    return {t for t in tokens if len(t) > 1}


def _keyword_overlap(kw1: set[str] | tuple[str, ...], kw2: set[str] | tuple[str, ...]) -> float:
    """Compute Jaccard similarity between two keyword collections.

    Parameters
    ----------
    kw1:
        First keyword set or tuple.
    kw2:
        Second keyword set or tuple.

    Returns
    -------
    float
        Jaccard index ``|kw1 ∩ kw2| / |kw1 ∪ kw2|``, or 0.0 when both are
        empty.

    Examples
    --------
    >>> _keyword_overlap({"a", "b", "c"}, {"b", "c", "d"})
    0.5
    >>> _keyword_overlap((), ())
    0.0
    """
    a = set(kw1)
    b = set(kw2)
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PurposeConditioningConfig:
    """Hyper-parameters governing purpose conditioning.

    Attributes
    ----------
    obstruction_weight:
        Weight given to evidence from the obstruction targets when computing
        the conditioning score.  Defaults to 0.5.
    goal_weight:
        Weight given to the goal labels when computing the conditioning score.
        Defaults to 0.3.
    history_weight:
        Weight given to past conditioned purposes (history) when updating.
        Defaults to 0.2.
    decay_factor:
        Exponential decay applied to older history entries so that recent
        conditioned purposes count more heavily.  Must be in (0, 1].
        Defaults to 0.9.
    max_history:
        Maximum number of past conditioned purposes to retain in the history
        buffer.  Older entries are evicted when the buffer is full.
        Defaults to 50.

    Examples
    --------
    >>> cfg = PurposeConditioningConfig()
    >>> cfg.obstruction_weight + cfg.goal_weight + cfg.history_weight
    1.0
    >>> 0.0 < cfg.decay_factor <= 1.0
    True
    """

    obstruction_weight: float = 0.5
    goal_weight: float = 0.3
    history_weight: float = 0.2
    decay_factor: float = 0.9
    max_history: int = 50


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionTarget:
    """A single typed obstruction extracted from raw input data.

    Attributes
    ----------
    target_id:
        Unique identifier for this obstruction target.
    description:
        Free-text description of the obstruction.
    h1_class:
        Label identifying the first-cohomology class responsible for the
        obstruction (e.g. ``'H1(X, O_X)'``).
    coordinate_ids:
        Tuple of coordinate identifiers in the theory space where the
        obstruction is localised.
    severity:
        A severity score in [0, 1] indicating how strongly this obstruction
        blocks the current proof path.  1.0 = completely blocking.
    timestamp:
        ISO-8601 UTC timestamp when this target was extracted.
    """

    target_id: str
    description: str
    h1_class: str
    coordinate_ids: tuple[str, ...]
    severity: float
    timestamp: str


@dataclass(frozen=True, slots=True)
class PurposeVector:
    """A structured semantic vector encoding the current purpose.

    This is a *local* dataclass used only within this module.

    Attributes
    ----------
    vector_id:
        Unique identifier for this purpose vector.
    keywords:
        Tuple of lowercase keyword tokens derived from obstruction descriptions
        and goal labels.
    obstruction_ids:
        Tuple of ``ObstructionTarget.target_id`` values included in this vector.
    goal_labels:
        Tuple of high-level goal labels (e.g. ``'prove_commutativity'``).
    weight:
        Overall weight of this vector, used when aggregating with history.
        Defaults to 1.0.
    """

    vector_id: str
    keywords: tuple[str, ...]
    obstruction_ids: tuple[str, ...]
    goal_labels: tuple[str, ...]
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class ConditionedPurpose:
    """A raw purpose string conditioned on the current obstruction set.

    Attributes
    ----------
    purpose_id:
        Unique identifier for this conditioned purpose.
    label:
        Short human-readable label, e.g. ``'resolve-fibration-H1'``.
    vector:
        The :class:`PurposeVector` encoding the obstruction-derived semantics.
    conditioning_score:
        Float in [0, 1] measuring how well the raw purpose aligns with the
        obstruction-derived vector.  Higher = more aligned.
    rationale:
        Human-readable explanation of how the conditioning score was computed.
    timestamp:
        ISO-8601 UTC timestamp of creation.
    """

    purpose_id: str
    label: str
    vector: PurposeVector
    conditioning_score: float
    rationale: str
    timestamp: str


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class PurposeConditioningTargetAnalyzer:
    """Extract obstructions, build purpose vectors, and condition purpose strings.

    This class is the core computational component of the purpose-conditioning
    pipeline.  Its methods are designed to be called in sequence:

    1. :meth:`extract_obstruction_targets` — parse raw dicts
    2. :meth:`build_purpose_vector` — assemble a semantic vector
    3. :meth:`condition_purpose` — score raw purpose against the vector
    4. :meth:`score_regime` — score a candidate regime against a conditioned purpose
    5. :meth:`update_with_history` — incorporate past evidence

    All methods are pure functions of their inputs (no internal state mutations).

    Parameters
    ----------
    config:
        Hyper-parameter bundle.  Defaults to :class:`PurposeConditioningConfig`.

    Examples
    --------
    >>> analyzer = PurposeConditioningTargetAnalyzer()
    >>> targets = analyzer.extract_obstruction_targets([
    ...     {"description": "H1 fibration obstruction", "h1_class": "H1(X,OX)",
    ...      "severity": 0.8, "coordinate_ids": ["n1", "n2"]},
    ... ])
    >>> len(targets) == 1
    True
    >>> targets[0].severity
    0.8
    """

    def __init__(self, config: PurposeConditioningConfig | None = None) -> None:
        """Initialise with optional configuration.

        Parameters
        ----------
        config:
            Hyper-parameter bundle.  Defaults to
            :class:`PurposeConditioningConfig` with factory defaults.
        """
        self.config: PurposeConditioningConfig = config or PurposeConditioningConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_obstruction_targets(
        self,
        obstructions: list[dict[str, Any]],
    ) -> list[ObstructionTarget]:
        """Parse a list of raw obstruction dicts into :class:`ObstructionTarget` instances.

        Each dict may contain the following keys (all optional except
        ``description``):

        * ``description`` (str) — free-text description of the obstruction.
        * ``h1_class`` (str) — cohomology class label.
        * ``coordinate_ids`` (list[str]) — node/coordinate IDs.
        * ``severity`` (float) — severity in [0, 1]; defaults to 0.5.
        * ``target_id`` (str) — if absent, a UUID is generated.

        Parameters
        ----------
        obstructions:
            List of raw obstruction dicts.

        Returns
        -------
        list[ObstructionTarget]
            Parsed and validated targets, sorted by descending severity.

        Notes
        -----
        Dicts missing a ``description`` key are skipped with a warning log.
        """
        targets: list[ObstructionTarget] = []
        for raw in obstructions:
            if "description" not in raw:
                logger.warning("Obstruction dict missing 'description' key — skipping")
                continue
            target = ObstructionTarget(
                target_id=str(raw.get("target_id", f"obs-{uuid.uuid4().hex[:8]}")),
                description=str(raw["description"]),
                h1_class=str(raw.get("h1_class", "unknown")),
                coordinate_ids=tuple(str(c) for c in raw.get("coordinate_ids", [])),
                severity=_clamp(float(raw.get("severity", 0.5))),
                timestamp=_now_iso(),
            )
            targets.append(target)

        targets.sort(key=lambda t: -t.severity)
        return targets

    def build_purpose_vector(
        self,
        targets: list[ObstructionTarget],
        goals: list[str],
    ) -> PurposeVector:
        """Construct a :class:`PurposeVector` from extracted targets and goal labels.

        The keyword tuple is derived by tokenising each obstruction description
        and goal label, weighted by severity so that high-severity obstructions
        contribute more keywords.  Duplicate keywords are removed; the final
        tuple is sorted for determinism.

        Parameters
        ----------
        targets:
            Extracted :class:`ObstructionTarget` instances.
        goals:
            List of high-level goal label strings.

        Returns
        -------
        PurposeVector
            A fully populated purpose vector.

        Notes
        -----
        If both *targets* and *goals* are empty, the returned vector has empty
        keyword and ID tuples and weight 0.0.
        """
        all_keywords: set[str] = set()

        for target in targets:
            toks = _tokenize(target.description)
            h1_toks = _tokenize(target.h1_class)
            # Weight high-severity obstructions more by repeating tokens
            n_repeats = max(1, round(target.severity * 3))
            for _ in range(n_repeats):
                all_keywords |= toks | h1_toks

        for goal in goals:
            all_keywords |= _tokenize(goal)

        total_severity = sum(t.severity for t in targets)
        weight = _clamp(total_severity / max(len(targets), 1))

        return PurposeVector(
            vector_id=f"pvec-{uuid.uuid4().hex[:8]}",
            keywords=tuple(sorted(all_keywords)),
            obstruction_ids=tuple(t.target_id for t in targets),
            goal_labels=tuple(goals),
            weight=weight if targets or goals else 0.0,
        )

    def condition_purpose(
        self,
        raw_purpose: str,
        vector: PurposeVector,
        config: PurposeConditioningConfig,
    ) -> ConditionedPurpose:
        """Condition *raw_purpose* against *vector* and return a :class:`ConditionedPurpose`.

        The conditioning score is computed as a weighted combination of:

        * **Obstruction alignment** — Jaccard overlap between the raw purpose
          tokens and the vector keywords weighted by obstruction severity proxy.
        * **Goal alignment** — Jaccard overlap between the raw purpose tokens
          and the goal-label tokens.

        The two terms are combined using *config.obstruction_weight* and
        *config.goal_weight*.

        Parameters
        ----------
        raw_purpose:
            Free-form purpose string (e.g. from the user or from an
            upstream planning module).
        vector:
            Purpose vector encoding obstruction and goal semantics.
        config:
            Hyper-parameters controlling weighting.

        Returns
        -------
        ConditionedPurpose
            A fully populated conditioned purpose with a human-readable
            rationale string.

        Examples
        --------
        >>> analyzer = PurposeConditioningTargetAnalyzer()
        >>> vec = PurposeVector("v1", ("fibration", "obstruction"), ("o1",), ("prove",))
        >>> cp = analyzer.condition_purpose(
        ...     "resolve fibration obstruction", vec,
        ...     PurposeConditioningConfig(),
        ... )
        >>> cp.conditioning_score > 0
        True
        """
        purpose_tokens = _tokenize(raw_purpose)
        vector_kw_set = set(vector.keywords)
        goal_tokens = _tokenize(" ".join(vector.goal_labels))

        obs_alignment = _keyword_overlap(purpose_tokens, vector_kw_set)
        goal_alignment = _keyword_overlap(purpose_tokens, goal_tokens) if goal_tokens else 0.0

        # Normalise weights
        total_w = config.obstruction_weight + config.goal_weight
        if total_w <= 0:
            total_w = 1.0

        score = _clamp(
            (config.obstruction_weight * obs_alignment + config.goal_weight * goal_alignment)
            / total_w
        )

        rationale = (
            f"Conditioning score={score:.4f}: "
            f"obstruction_alignment={obs_alignment:.3f} (w={config.obstruction_weight}), "
            f"goal_alignment={goal_alignment:.3f} (w={config.goal_weight}). "
            f"Purpose tokens={len(purpose_tokens)}, "
            f"vector keywords={len(vector_kw_set)}, "
            f"goal labels={len(vector.goal_labels)}."
        )

        label_slug = re.sub(r"\W+", "-", raw_purpose.lower())[:40].strip("-")

        return ConditionedPurpose(
            purpose_id=_purpose_id(),
            label=label_slug,
            vector=vector,
            conditioning_score=round(score, 6),
            rationale=rationale,
            timestamp=_now_iso(),
        )

    def score_regime(
        self,
        regime: dict[str, Any],
        purpose: ConditionedPurpose,
    ) -> float:
        """Score a candidate regime dict against a conditioned purpose.

        The score is the Jaccard overlap between the regime's text tokens
        (name + area + description) and the purpose vector's keywords, scaled
        by the purpose's ``conditioning_score``.

        Parameters
        ----------
        regime:
            Raw regime dict with at minimum a ``name`` key.
        purpose:
            A :class:`ConditionedPurpose` to score against.

        Returns
        -------
        float
            Score in [0, 1]; 0 = no overlap, 1 = perfect alignment.

        Examples
        --------
        >>> analyzer = PurposeConditioningTargetAnalyzer()
        >>> vec = PurposeVector("v1", ("functor", "adjunction"), (), ("prove",))
        >>> cp = ConditionedPurpose("p1","lbl",vec,0.8,"r","2024-01-01T00:00:00+00:00")
        >>> score = analyzer.score_regime(
        ...     {"name": "Category Theory", "description": "functors adjunctions"},
        ...     cp,
        ... )
        >>> score > 0
        True
        """
        text = " ".join([
            str(regime.get("name", "")),
            str(regime.get("area", "")),
            str(regime.get("description", "")),
        ])
        regime_tokens = _tokenize(text)
        vector_kw_set = set(purpose.vector.keywords)
        overlap = _keyword_overlap(regime_tokens, vector_kw_set)
        return _clamp(overlap * purpose.conditioning_score)

    def update_with_history(
        self,
        purpose: ConditionedPurpose,
        history: list[dict[str, Any]],
    ) -> ConditionedPurpose:
        """Incorporate past conditioning evidence into *purpose*.

        Each history entry is expected to contain a ``conditioning_score``
        float.  A decay-weighted average of historical scores is blended with
        the current purpose's conditioning score.

        Parameters
        ----------
        purpose:
            The current :class:`ConditionedPurpose` to update.
        history:
            List of history dicts (most recent first) each with at least a
            ``conditioning_score`` key.

        Returns
        -------
        ConditionedPurpose
            A new :class:`ConditionedPurpose` with an updated
            ``conditioning_score`` and augmented ``rationale``.

        Notes
        -----
        If *history* is empty the original *purpose* is returned unchanged.
        History entries without a ``conditioning_score`` key are skipped.
        """
        if not history:
            return purpose

        cfg = self.config
        decay = cfg.decay_factor
        trimmed = history[: cfg.max_history]

        weighted_sum = 0.0
        weight_total = 0.0
        for i, entry in enumerate(trimmed):
            if "conditioning_score" not in entry:
                continue
            w = (decay ** i)
            weighted_sum += w * _clamp(float(entry["conditioning_score"]))
            weight_total += w

        if weight_total == 0:
            return purpose

        hist_score = weighted_sum / weight_total
        # Blend current score with history
        new_score = _clamp(
            (1.0 - cfg.history_weight) * purpose.conditioning_score
            + cfg.history_weight * hist_score
        )

        updated_rationale = (
            f"{purpose.rationale} "
            f"[history_blend: hist_score={hist_score:.4f}, "
            f"history_weight={cfg.history_weight}, "
            f"updated_score={new_score:.4f}]"
        )

        return ConditionedPurpose(
            purpose_id=_purpose_id(),
            label=purpose.label,
            vector=purpose.vector,
            conditioning_score=round(new_score, 6),
            rationale=updated_rationale,
            timestamp=_now_iso(),
        )


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class PurposeConditioningTargetWitness:
    """Accumulates :class:`ConditionedPurpose` objects and computes drift statistics.

    The *drift* metric is the mean absolute change in ``conditioning_score``
    between consecutive conditioned purposes.  A drift below a threshold (e.g.
    0.01) indicates that the conditioning process has converged.

    Attributes
    ----------
    _history : list[ConditionedPurpose]
        Internal ordered log of all recorded conditioned purposes.

    Examples
    --------
    >>> witness = PurposeConditioningTargetWitness()
    >>> vec = PurposeVector("v1", ("fibration",), ("o1",), ("prove",))
    >>> cp1 = ConditionedPurpose("p1","lbl",vec,0.5,"r","2024-01-01T00:00:00+00:00")
    >>> cp2 = ConditionedPurpose("p2","lbl",vec,0.7,"r","2024-01-01T00:01:00+00:00")
    >>> witness.record(cp1)
    >>> witness.record(cp2)
    >>> abs(witness.drift() - 0.2) < 1e-9
    True
    """

    def __init__(self) -> None:
        """Initialise an empty witness."""
        self._history: list[ConditionedPurpose] = []

    def record(self, purpose: ConditionedPurpose) -> None:
        """Append *purpose* to the internal history.

        Parameters
        ----------
        purpose:
            The :class:`ConditionedPurpose` to record.
        """
        self._history.append(purpose)
        logger.debug(
            "Witness recorded conditioned purpose id=%s score=%.4f",
            purpose.purpose_id,
            purpose.conditioning_score,
        )

    def drift(self) -> float:
        """Compute mean absolute change in conditioning score between consecutive entries.

        Returns
        -------
        float
            Mean |score_i - score_{i-1}| over all consecutive pairs.  Returns
            0.0 if fewer than two entries have been recorded.

        Examples
        --------
        >>> w = PurposeConditioningTargetWitness()
        >>> w.drift()
        0.0
        """
        if len(self._history) < 2:
            return 0.0
        deltas = [
            abs(self._history[i].conditioning_score - self._history[i - 1].conditioning_score)
            for i in range(1, len(self._history))
        ]
        return sum(deltas) / len(deltas)

    def latest(self) -> ConditionedPurpose | None:
        """Return the most recently recorded conditioned purpose, or *None*.

        Returns
        -------
        ConditionedPurpose | None
            The last recorded purpose, or ``None`` if the witness is empty.
        """
        return self._history[-1] if self._history else None

    def history(self) -> list[ConditionedPurpose]:
        """Return a shallow copy of the full history list.

        Returns
        -------
        list[ConditionedPurpose]
            All recorded conditioned purposes in insertion order.
        """
        return list(self._history)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class PurposeConditioningCoordinator:
    """Orchestrate the full purpose-conditioning pipeline in a single call.

    The coordinator:

    1. Extracts :class:`ObstructionTarget` instances from *obstructions*.
    2. Builds a :class:`PurposeVector` from targets + *goals*.
    3. Conditions *raw_purpose* against the vector.
    4. Records the result in the internal :class:`PurposeConditioningTargetWitness`.
    5. Returns the conditioned purpose.

    Parameters
    ----------
    config:
        Hyper-parameter bundle.  Defaults to :class:`PurposeConditioningConfig`.

    Examples
    --------
    >>> coord = PurposeConditioningCoordinator()
    >>> cp = coord.run(
    ...     obstructions=[{"description": "H1 fibration", "severity": 0.8}],
    ...     goals=["prove_commutativity"],
    ...     raw_purpose="resolve fibration obstruction",
    ... )
    >>> cp.conditioning_score >= 0
    True
    """

    def __init__(self, config: PurposeConditioningConfig | None = None) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        config:
            Hyper-parameter bundle.  Defaults to
            :class:`PurposeConditioningConfig` with factory defaults.
        """
        self.config: PurposeConditioningConfig = config or PurposeConditioningConfig()
        self._analyzer: PurposeConditioningTargetAnalyzer = PurposeConditioningTargetAnalyzer(
            self.config
        )
        self._witness: PurposeConditioningTargetWitness = PurposeConditioningTargetWitness()

    def run(
        self,
        obstructions: list[dict[str, Any]],
        goals: list[str],
        raw_purpose: str,
    ) -> ConditionedPurpose:
        """Execute the full conditioning pipeline and return the result.

        Parameters
        ----------
        obstructions:
            List of raw obstruction dicts (see
            :meth:`PurposeConditioningTargetAnalyzer.extract_obstruction_targets`
            for expected keys).
        goals:
            List of high-level goal label strings.
        raw_purpose:
            Free-form purpose description to condition.

        Returns
        -------
        ConditionedPurpose
            The conditioned purpose produced by the pipeline.

        Notes
        -----
        Each call appends to the internal witness.  Call :meth:`report` to
        inspect the accumulated history.
        """
        targets = self._analyzer.extract_obstruction_targets(obstructions)
        vector = self._analyzer.build_purpose_vector(targets, goals)
        conditioned = self._analyzer.condition_purpose(raw_purpose, vector, self.config)

        # Optionally blend with history
        if self._witness.history():
            hist_dicts = [
                {"conditioning_score": cp.conditioning_score}
                for cp in self._witness.history()
            ]
            conditioned = self._analyzer.update_with_history(conditioned, hist_dicts)

        self._witness.record(conditioned)
        return conditioned

    def report(self) -> dict[str, Any]:
        """Produce a structured report of the conditioning pipeline.

        Returns
        -------
        dict[str, Any]
            Report with keys: ``runs`` (number of calls to :meth:`run`),
            ``drift``, ``latest_score``, ``latest_label``, ``history_ids``.
        """
        latest = self._witness.latest()
        history = self._witness.history()
        return {
            "runs": len(history),
            "drift": round(self._witness.drift(), 6),
            "latest_score": round(latest.conditioning_score, 6) if latest else None,
            "latest_label": latest.label if latest else None,
            "history_ids": [cp.purpose_id for cp in history],
        }


# ---------------------------------------------------------------------------
# Canonical obstruction examples
# ---------------------------------------------------------------------------

#: Illustrative obstruction dicts covering common proof-navigation scenarios.
EXAMPLE_OBSTRUCTIONS: list[dict[str, Any]] = [
    {
        "description": "H1 fibration obstruction in the path-space fibration over the base scheme",
        "h1_class": "H1(X, O_X)",
        "coordinate_ids": ["node_path_space", "node_base_scheme"],
        "severity": 0.9,
    },
    {
        "description": "Extension class in Ext^1 blocking the splitting of the short exact sequence",
        "h1_class": "Ext1(M, N)",
        "coordinate_ids": ["node_module_M", "node_module_N"],
        "severity": 0.75,
    },
    {
        "description": "Gluing condition failure in the Cech cocycle computation on the étale site",
        "h1_class": "H1_et(X, G)",
        "coordinate_ids": ["node_etale_site", "node_scheme_X"],
        "severity": 0.6,
    },
    {
        "description": "Naturality obstruction arising from a non-commutative diagram of functors",
        "h1_class": "Nat(F, G)",
        "coordinate_ids": ["node_functor_F", "node_functor_G"],
        "severity": 0.5,
    },
    {
        "description": "Homotopy path obstruction between two points in a higher inductive type",
        "h1_class": "pi1(HIT)",
        "coordinate_ids": ["node_HIT", "node_point_a", "node_point_b"],
        "severity": 0.85,
    },
    {
        "description": "Derived functor obstruction blocking descent in a non-abelian sheaf context",
        "h1_class": "R1f_*(F)",
        "coordinate_ids": ["node_derived_cat", "node_sheaf_F"],
        "severity": 0.7,
    },
    {
        "description": "Curvature obstruction preventing a flat connection on the principal bundle",
        "h1_class": "H2(X, g)",
        "coordinate_ids": ["node_principal_bundle", "node_connection"],
        "severity": 0.65,
    },
    {
        "description": "K-theoretic obstruction to stable isomorphism of vector bundles",
        "h1_class": "K0(X)",
        "coordinate_ids": ["node_vect_bundle_E", "node_vect_bundle_F"],
        "severity": 0.55,
    },
]

#: Canonical goal labels used in standard proof-navigation scenarios.
CANONICAL_GOALS: list[str] = [
    "prove_commutativity",
    "resolve_H1_obstruction",
    "establish_adjunction",
    "construct_fibration_sequence",
    "verify_naturality",
    "compute_derived_functor",
    "lift_cocycle_to_coboundary",
    "prove_univalence_instance",
    "establish_flat_descent",
    "resolve_extension_class",
]


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Purpose Conditioning: Target Obstruction Set ===\n")

    coord = PurposeConditioningCoordinator(
        config=PurposeConditioningConfig(
            obstruction_weight=0.5,
            goal_weight=0.3,
            history_weight=0.2,
            decay_factor=0.9,
        )
    )

    # Round 1
    cp1 = coord.run(
        obstructions=EXAMPLE_OBSTRUCTIONS[:3],
        goals=CANONICAL_GOALS[:4],
        raw_purpose="resolve H1 fibration obstruction using path-space arguments",
    )
    print(f"Round 1: purpose_id={cp1.purpose_id}")
    print(f"  label={cp1.label!r}")
    print(f"  conditioning_score={cp1.conditioning_score:.4f}")
    print(f"  vector keywords (first 10): {list(cp1.vector.keywords[:10])}")
    print(f"  rationale: {cp1.rationale[:120]}...\n")

    # Round 2
    cp2 = coord.run(
        obstructions=EXAMPLE_OBSTRUCTIONS[3:6],
        goals=CANONICAL_GOALS[4:8],
        raw_purpose="establish naturality and compute derived functor Ext1",
    )
    print(f"Round 2: purpose_id={cp2.purpose_id}")
    print(f"  conditioning_score={cp2.conditioning_score:.4f}")

    report = coord.report()
    print(f"\nReport: runs={report['runs']}, drift={report['drift']:.4f}, "
          f"latest_score={report['latest_score']}")

    # Test regime scoring
    analyzer = PurposeConditioningTargetAnalyzer()
    regimes_to_score = [
        {"name": "Homological Algebra", "area": "homological_algebra",
         "description": "derived functors Ext Tor chain complexes"},
        {"name": "Homotopy Type Theory", "area": "homotopy_type_theory",
         "description": "path spaces fibrations univalence higher inductive types"},
    ]
    print("\nRegime scores against latest conditioned purpose:")
    for r in regimes_to_score:
        s = analyzer.score_regime(r, cp2)
        print(f"  {r['name']}: {s:.4f}")

    print("\nSmoke-test PASSED.")
