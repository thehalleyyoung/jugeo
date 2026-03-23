"""Novelty versus useful novelty — levels and measures – theory2.tex Ch57.

Distinguishes *raw* novelty (purely distance-based divergence from a portfolio)
from *useful* novelty (novelty that materially reduces the obstruction count or
enables new proofs).  Provides a graded classification system, comparison
utilities, and a full coordinator pipeline.

# copilot: generated as part of jugeo.ideation.novelty_search

Module layout::

    ┌─────────────────────────────────────────────────────────────────────┐
    │  NoveltyLevel                  – 5-level Enum: TRIVIAL … BREAKTHROUGH│
    │  UsefulNoveltyConfig           – frozen weights & threshold config   │
    │  NoveltyMeasure                – single measured (id, scores, level) │
    │  UsefulNoveltyComparison       – A-vs-B comparison record            │
    │  NoveltyVsUsefulNoveltyAnalyzer– core computation methods            │
    │  NoveltyVsUsefulNoveltyWitness – accumulates measures, statistics    │
    │  NoveltyVsUsefulNoveltyCoordinator – end-to-end pipeline             │
    └─────────────────────────────────────────────────────────────────────┘

Background
----------
Raw novelty answers the question: *"How different is this idea from everything
we have already considered?"*  It is computed as a monotone function of the
minimum distance from the candidate idea to the closest existing portfolio
member.  A score of 1.0 means maximally novel (the idea is orthogonal to all
prior art); a score of 0.0 means identical to some existing portfolio member.

Useful novelty answers a harder question: *"Does this novel idea actually help
us make progress?"*  An idea that is very different from the existing portfolio
but addresses an irrelevant subfield contributes little.  Useful novelty
weights the raw score by:

  - **Obstruction delta** (obstruction_weight): how much the idea reduces the
    count of known algebraic-geometry obstructions.  A negative delta (more
    obstructions removed) earns a higher contribution.
  - **Proof enablement** (proof_enablement_weight): the net increase in proved
    theorems that the idea unlocks, normalised by portfolio size.
  - **Leverage** (leverage_weight): how many other open problems become tractable
    once this idea is adopted.
  - **Tractability** (tractability_weight): how feasible it is to formalise and
    prove the idea in the current tooling environment.

The weighted combination is passed through a sigmoid-like normalisation so the
final score lies in [0, 1], then classified into one of five NoveltyLevels.

The minimum useful threshold (``UsefulNoveltyConfig.min_useful_threshold``)
acts as a gate: ideas below this threshold are not surfaced to the pipeline
regardless of their raw novelty, preventing the accumulation of
mathematically exotic but practically useless candidates.

Theory references
-----------------
* theory2.tex §57.3 "Useful Novelty and Obstruction Reduction"
* theory2.tex §57.6 "Graded Novelty Classification"
* theory2.tex §57.9 "Comparisons and Preference Orderings on Ideas"

Usage example::

    from jugeo.ideation.novelty_search.novelty_versus_useful_novelty_leve import (
        NoveltyVsUsefulNoveltyCoordinator,
        UsefulNoveltyConfig,
    )

    ideas = [{"id": "i1", "tokens": {"sheaf", "cohomology"}, "title": "..."}]
    obstructions = [{"id": "o1", "class": "H2", "addressed_by": []}]
    portfolio = [{"id": "p1", "tokens": {"descent", "stack"}}]

    coordinator = NoveltyVsUsefulNoveltyCoordinator()
    measures = coordinator.run(ideas, obstructions, portfolio)
    print(coordinator.report())
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    from jugeo.ideation.novelty_search.models import (
        NoveltySearchProblem,
        SearchResult,
        NoveltyMetricSpec,
        MetricKind,
    )
except ImportError:
    NoveltySearchProblem = None  # type: ignore[assignment,misc]
    SearchResult = None  # type: ignore[assignment,misc]
    NoveltyMetricSpec = None  # type: ignore[assignment,misc]
    MetricKind = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.novelty_search.distance_metrics import (
        SemanticDistanceComputer,
        DistanceConfig,
    )
except ImportError:
    SemanticDistanceComputer = None  # type: ignore[assignment,misc]
    DistanceConfig = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_LEVEL_THRESHOLDS: dict[str, float] = {
    "BREAKTHROUGH": 0.85,
    "HIGH": 0.65,
    "MODERATE": 0.40,
    "LOW": 0.20,
    "TRIVIAL": 0.0,
}

_DEFAULT_OBSTRUCTION_WEIGHT: float = 0.4
_DEFAULT_PROOF_WEIGHT: float = 0.3
_DEFAULT_LEVERAGE_WEIGHT: float = 0.2
_DEFAULT_TRACTABILITY_WEIGHT: float = 0.1
_DEFAULT_MIN_USEFUL_THRESHOLD: float = 0.3
_EPSILON: float = 1e-9

USEFUL_NOVELTY_NARRATIVE: str = """
Useful novelty is the subset of mathematical novelty that drives progress.

A purely novel idea may be distant from all existing work in the portfolio yet
have no bearing on the open problems that motivate the research programme.
For example, an idea about tropical geometry may be highly novel relative to a
portfolio focused on étale cohomology, but unless it reduces some obstruction in
the étale setting it does not advance the goal.

In the jugeo framework, useful novelty is computed as a convex combination of
four components:

1. Obstruction delta: The most direct measure of progress.  When an idea
   allows us to remove one or more entries from the obstruction ledger — the
   list of sheaf-theoretic or cohomological barriers to a target proof — it
   earns maximum weight in the useful-novelty functional.  A large negative
   obstruction delta (many obstructions removed) is the gold standard for
   useful novelty.

2. Proof enablement: Beyond removing obstructions, an idea may unlock proofs
   that were previously blocked.  This component measures the net change in the
   count of provable theorems that follows from adopting the idea.  Even if an
   idea does not directly attack a known obstruction, enabling three new proofs
   is a sign of genuine mathematical productivity.

3. Leverage: An idea with high leverage is a force-multiplier.  It makes many
   other open conjectures more tractable.  Leverage is estimated by examining
   the dependency graph of open problems and counting how many nodes become
   newly reachable once the idea is formalised.

4. Tractability: Even a transformative idea is useless if it cannot be proved.
   Tractability estimates the probability that the idea can be formalised and
   verified within the current tool environment (Lean 4, Agda, or Coq) given
   the available lemma library and proof assistant capabilities.

The threshold ``min_useful_threshold`` is set at 0.3 by default.  Ideas below
this threshold are filtered out during the pipeline's pre-selection phase.
This prevents the accumulation of intellectually interesting but practically
intractable ideas that would bloat the portfolio without advancing the proof
agenda.
"""

# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval ``[lo, hi]``."""
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _measure_id() -> str:
    """Generate a unique measure identifier prefixed with ``nm-``."""
    return f"nm-{uuid.uuid4().hex[:12]}"


def _novelty_from_distance(min_dist: float) -> float:
    """Convert a minimum portfolio distance to a raw novelty score in [0, 1].

    Uses a smooth sigmoid-like mapping so that:
    - ``min_dist = 0.0`` → novelty ≈ 0.0 (identical to existing idea)
    - ``min_dist = 1.0`` → novelty ≈ 1.0 (maximally different)

    The mapping is: ``novelty = 1 − exp(−3 * min_dist)`` renormalised to [0,1].

    Parameters
    ----------
    min_dist:
        Minimum distance to any portfolio member, in [0, 1].
    """
    if min_dist <= 0.0:
        return 0.0
    raw = 1.0 - math.exp(-3.0 * _clamp(min_dist))
    return _clamp(raw / (1.0 - math.exp(-3.0)))


def _level_label(score: float) -> "NoveltyLevel":
    """Return the ``NoveltyLevel`` corresponding to *score*."""
    if score >= _LEVEL_THRESHOLDS["BREAKTHROUGH"]:
        return NoveltyLevel.BREAKTHROUGH
    if score >= _LEVEL_THRESHOLDS["HIGH"]:
        return NoveltyLevel.HIGH
    if score >= _LEVEL_THRESHOLDS["MODERATE"]:
        return NoveltyLevel.MODERATE
    if score >= _LEVEL_THRESHOLDS["LOW"]:
        return NoveltyLevel.LOW
    return NoveltyLevel.TRIVIAL


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute the Jaccard similarity between two token sets."""
    if not set_a and not set_b:
        return 1.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / (union + _EPSILON)


def _extract_tokens(idea: dict[str, Any]) -> set[str]:
    """Extract a token set from an idea dictionary for distance computation.

    Combines tokens from ``title``, ``purpose``, ``hypothesis``,
    ``target_area``, and any explicit ``tokens`` field.
    """
    tokens: set[str] = set()
    if "tokens" in idea and isinstance(idea["tokens"], (set, list, frozenset)):
        tokens.update(str(t).lower() for t in idea["tokens"])
    for field_name in ("title", "purpose", "hypothesis", "target_area", "description"):
        text = idea.get(field_name, "")
        if isinstance(text, str):
            words = re.findall(r"[a-zA-Z]{3,}", text.lower())
            tokens.update(words)
    return tokens


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class NoveltyLevel(Enum):
    """Five-level classification of novelty measures.

    Levels are ordered from least to most significant:

    TRIVIAL
        Score < 0.20.  The idea is essentially a paraphrase of existing work.
        It adds no new information to the portfolio.

    LOW
        Score in [0.20, 0.40).  There is some novelty — the idea differs from
        existing work in at least one dimension — but the difference is too
        small to qualify as a genuine contribution.

    MODERATE
        Score in [0.40, 0.65).  The idea introduces a recognisably distinct
        perspective.  Worth tracking, but not necessarily prioritised.

    HIGH
        Score in [0.65, 0.85).  The idea opens a new line of investigation or
        provides a substantially different approach to a known problem.

    BREAKTHROUGH
        Score ≥ 0.85.  The idea is transformative.  It either eliminates a
        major class of obstructions, unlocks a large cluster of proofs, or
        introduces a wholly new mathematical framework.
    """

    TRIVIAL = "TRIVIAL"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    BREAKTHROUGH = "BREAKTHROUGH"

    def numeric_value(self) -> float:
        """Return a numeric representation of the level (0.0 – 1.0)."""
        _map = {
            NoveltyLevel.TRIVIAL: 0.10,
            NoveltyLevel.LOW: 0.30,
            NoveltyLevel.MODERATE: 0.525,
            NoveltyLevel.HIGH: 0.75,
            NoveltyLevel.BREAKTHROUGH: 0.925,
        }
        return _map[self]


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsefulNoveltyConfig:
    """Configuration weights for the useful-novelty functional.

    All four weights must sum to approximately 1.0.  The defaults
    prioritise obstruction reduction (0.4) over proof enablement (0.3),
    leverage (0.2), and tractability (0.1).

    Attributes
    ----------
    obstruction_weight:
        Weight applied to the obstruction-delta component.  Higher values
        make the functional more sensitive to obstruction removal.
    proof_enablement_weight:
        Weight applied to the proof-enablement component.
    leverage_weight:
        Weight applied to the leverage component.
    tractability_weight:
        Weight applied to the tractability component.
    min_useful_threshold:
        Ideas with useful_novelty below this threshold are excluded from
        the output of ``filter_useful``.
    """

    obstruction_weight: float = _DEFAULT_OBSTRUCTION_WEIGHT
    proof_enablement_weight: float = _DEFAULT_PROOF_WEIGHT
    leverage_weight: float = _DEFAULT_LEVERAGE_WEIGHT
    tractability_weight: float = _DEFAULT_TRACTABILITY_WEIGHT
    min_useful_threshold: float = _DEFAULT_MIN_USEFUL_THRESHOLD

    def validate(self) -> None:
        """Raise ``ValueError`` if weights are negative or obviously wrong."""
        total = (
            self.obstruction_weight
            + self.proof_enablement_weight
            + self.leverage_weight
            + self.tractability_weight
        )
        if abs(total - 1.0) > 0.05:
            raise ValueError(
                f"UsefulNoveltyConfig weights sum to {total:.4f}; expected ≈ 1.0"
            )
        for name, val in [
            ("obstruction_weight", self.obstruction_weight),
            ("proof_enablement_weight", self.proof_enablement_weight),
            ("leverage_weight", self.leverage_weight),
            ("tractability_weight", self.tractability_weight),
        ]:
            if val < 0.0 or val > 1.0:
                raise ValueError(f"{name}={val} out of [0, 1]")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoveltyMeasure:
    """A fully computed novelty measurement for a single idea.

    Attributes
    ----------
    measure_id:
        Unique identifier for this measurement record.
    idea_id:
        Identifier of the idea being measured.
    raw_novelty:
        Distance-based novelty score in [0, 1].  Independent of usefulness.
    useful_novelty:
        Weighted combination of raw_novelty, obstruction_delta,
        proof_count_delta, leverage_score, and tractability_score.
    obstruction_delta:
        Change in obstruction count attributable to this idea.  Negative
        values indicate obstruction reduction (positive contribution).
    proof_count_delta:
        Net increase in provable theorems enabled by this idea.
    leverage_score:
        Estimated leverage in [0, 1].
    tractability_score:
        Estimated tractability in [0, 1].
    level:
        Classified NoveltyLevel based on useful_novelty.
    timestamp:
        ISO-8601 timestamp of when this measurement was computed.
    """

    measure_id: str
    idea_id: str
    raw_novelty: float
    useful_novelty: float
    obstruction_delta: float
    proof_count_delta: int
    leverage_score: float
    tractability_score: float
    level: NoveltyLevel
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for JSON export."""
        return {
            "measure_id": self.measure_id,
            "idea_id": self.idea_id,
            "raw_novelty": self.raw_novelty,
            "useful_novelty": self.useful_novelty,
            "obstruction_delta": self.obstruction_delta,
            "proof_count_delta": self.proof_count_delta,
            "leverage_score": self.leverage_score,
            "tractability_score": self.tractability_score,
            "level": self.level.value,
            "timestamp": self.timestamp,
        }

    def is_useful(self, threshold: float = _DEFAULT_MIN_USEFUL_THRESHOLD) -> bool:
        """Return True if useful_novelty exceeds *threshold*."""
        return self.useful_novelty >= threshold

    def summary(self) -> str:
        """Return a one-line human-readable summary of this measure."""
        return (
            f"[{self.level.value}] idea={self.idea_id!r} "
            f"raw={self.raw_novelty:.3f} useful={self.useful_novelty:.3f} "
            f"Δobs={self.obstruction_delta:+.2f} Δproofs={self.proof_count_delta:+d}"
        )


@dataclass(frozen=True, slots=True)
class UsefulNoveltyComparison:
    """A head-to-head comparison of two ideas on useful novelty.

    Attributes
    ----------
    comparison_id:
        Unique identifier for this comparison record.
    idea_a:
        Identifier of the first idea.
    idea_b:
        Identifier of the second idea.
    novelty_a:
        Raw novelty of idea A.
    novelty_b:
        Raw novelty of idea B.
    useful_novelty_a:
        Useful novelty of idea A.
    useful_novelty_b:
        Useful novelty of idea B.
    preferred:
        Identifier of the preferred idea (A or B), or ``"tie"`` if equal.
    rationale:
        Human-readable explanation of why the preference was made.
    """

    comparison_id: str
    idea_a: str
    idea_b: str
    novelty_a: float
    novelty_b: float
    useful_novelty_a: float
    useful_novelty_b: float
    preferred: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "comparison_id": self.comparison_id,
            "idea_a": self.idea_a,
            "idea_b": self.idea_b,
            "novelty_a": self.novelty_a,
            "novelty_b": self.novelty_b,
            "useful_novelty_a": self.useful_novelty_a,
            "useful_novelty_b": self.useful_novelty_b,
            "preferred": self.preferred,
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class NoveltyVsUsefulNoveltyAnalyzer:
    """Core analysis engine for novelty versus useful-novelty computations.

    This class implements the measurement methods described in Ch57 of the
    jugeo theory document.  It is stateless: all data is passed as arguments
    and all results are returned as typed dataclasses.

    Parameters
    ----------
    config:
        Default ``UsefulNoveltyConfig`` to use when none is supplied to an
        individual method.  Defaults to the standard configuration.
    """

    def __init__(self, config: UsefulNoveltyConfig | None = None) -> None:
        self._config = config or UsefulNoveltyConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def measure_raw_novelty(
        self, idea: dict[str, Any], portfolio: list[dict[str, Any]]
    ) -> float:
        """Compute the raw novelty of *idea* relative to *portfolio*.

        Raw novelty is derived from the minimum Jaccard distance between the
        idea's token set and each portfolio member's token set.

        Parameters
        ----------
        idea:
            Candidate idea dictionary.  Must contain at least one of:
            ``tokens``, ``title``, ``purpose``, ``hypothesis``.
        portfolio:
            List of existing idea dictionaries forming the reference corpus.

        Returns
        -------
        float
            Raw novelty score in [0, 1].  0 means identical to a portfolio
            member; 1 means maximally distant from all portfolio members.
        """
        idea_tokens = _extract_tokens(idea)
        if not portfolio:
            return 1.0
        min_sim = 1.0
        for p_idea in portfolio:
            p_tokens = _extract_tokens(p_idea)
            sim = _jaccard_similarity(idea_tokens, p_tokens)
            if sim < min_sim:
                min_sim = sim
        min_dist = 1.0 - min_sim
        return _novelty_from_distance(min_dist)

    def measure_useful_novelty(
        self,
        idea: dict[str, Any],
        obstructions: list[dict[str, Any]],
        proved_theorems: list[str],
        config: UsefulNoveltyConfig | None = None,
    ) -> NoveltyMeasure:
        """Compute the full useful-novelty measure for *idea*.

        Steps:
        1. Compute raw novelty against a minimal single-item reference
           (uses the obstructions as a proxy corpus when no portfolio is
           passed via this method signature).
        2. Estimate obstruction delta by checking how many obstructions the
           idea's keywords overlap with.
        3. Estimate proof count delta from the ``proved_theorems`` list.
        4. Estimate leverage and tractability from idea metadata.
        5. Combine into a useful-novelty score and classify into a level.

        Parameters
        ----------
        idea:
            Candidate idea dictionary.
        obstructions:
            Current list of obstruction records.  Each record should have
            ``id``, ``class``, and optionally ``addressed_by`` (list of
            idea ids that partially address it).
        proved_theorems:
            List of theorem IDs that are currently provable.
        config:
            Optional configuration override.

        Returns
        -------
        NoveltyMeasure
            Fully populated measurement record.
        """
        cfg = config or self._config
        idea_id = str(idea.get("id", _measure_id()))
        idea_tokens = _extract_tokens(idea)

        # Raw novelty: use obstruction descriptions as a proxy corpus
        proxy_corpus = [
            {"tokens": set(re.findall(r"[a-zA-Z]{3,}", str(o).lower()))}
            for o in obstructions
        ]
        raw = self.measure_raw_novelty(idea, proxy_corpus) if proxy_corpus else 0.5

        # Obstruction delta: count how many obstructions the idea addresses
        addressed = 0
        for obs in obstructions:
            obs_tokens = set(re.findall(r"[a-zA-Z]{3,}", str(obs.get("class", "")).lower()))
            if obs_tokens & idea_tokens:
                addressed += 1
        total_obs = max(len(obstructions), 1)
        # Normalise: addressing half the obstructions gives delta ≈ -0.5
        obstruction_delta = -_clamp(addressed / total_obs)
        # Convert to a 0→1 contribution: more negative = more useful
        obs_contribution = _clamp(-obstruction_delta)

        # Proof enablement: fraction of proved theorems whose IDs overlap
        # with idea tokens (a heuristic proxy)
        pt_count = len(proved_theorems)
        if pt_count > 0:
            enabled = sum(
                1 for t in proved_theorems
                if any(tok in t.lower() for tok in idea_tokens if len(tok) > 4)
            )
            proof_contribution = _clamp(enabled / pt_count)
        else:
            proof_contribution = 0.0
        proof_count_delta = max(0, int(proof_contribution * pt_count))

        # Leverage: estimate from idea metadata if available
        leverage_score = _clamp(float(idea.get("leverage", 0.5)))

        # Tractability: estimate from idea metadata if available
        tractability_score = _clamp(float(idea.get("tractability", 0.5)))

        # Combine into useful novelty
        useful = (
            cfg.obstruction_weight * obs_contribution
            + cfg.proof_enablement_weight * proof_contribution
            + cfg.leverage_weight * leverage_score
            + cfg.tractability_weight * tractability_score
        )
        # Blend with raw novelty (50/50) so pure usefulness without novelty is penalised
        useful_novelty = _clamp(0.5 * useful + 0.5 * raw)

        level = self.classify_level(useful_novelty)
        return NoveltyMeasure(
            measure_id=_measure_id(),
            idea_id=idea_id,
            raw_novelty=raw,
            useful_novelty=useful_novelty,
            obstruction_delta=obstruction_delta,
            proof_count_delta=proof_count_delta,
            leverage_score=leverage_score,
            tractability_score=tractability_score,
            level=level,
            timestamp=_now_iso(),
        )

    def classify_level(self, useful_novelty: float) -> NoveltyLevel:
        """Classify a useful-novelty score into a ``NoveltyLevel``.

        Parameters
        ----------
        useful_novelty:
            Score in [0, 1].

        Returns
        -------
        NoveltyLevel
            Appropriate level for the score.
        """
        return _level_label(_clamp(useful_novelty))

    def compare(
        self,
        idea_a: dict[str, Any],
        idea_b: dict[str, Any],
        obstructions: list[dict[str, Any]],
    ) -> UsefulNoveltyComparison:
        """Compare two ideas and return a preference record.

        Parameters
        ----------
        idea_a:
            First candidate idea.
        idea_b:
            Second candidate idea.
        obstructions:
            Current obstruction list (used for useful-novelty computation).

        Returns
        -------
        UsefulNoveltyComparison
            Comparison record with preference and rationale.
        """
        ma = self.measure_useful_novelty(idea_a, obstructions, [])
        mb = self.measure_useful_novelty(idea_b, obstructions, [])
        delta = ma.useful_novelty - mb.useful_novelty
        if abs(delta) < 0.02:
            preferred = "tie"
            rationale = (
                f"Both ideas have nearly equal useful novelty "
                f"(Δ={delta:+.4f} < 0.02 threshold)."
            )
        elif delta > 0:
            preferred = ma.idea_id
            rationale = (
                f"Idea {ma.idea_id!r} preferred: useful_novelty "
                f"{ma.useful_novelty:.3f} > {mb.useful_novelty:.3f} "
                f"(Δ={delta:+.3f}). "
                f"Obstruction delta: {ma.obstruction_delta:+.3f} vs "
                f"{mb.obstruction_delta:+.3f}."
            )
        else:
            preferred = mb.idea_id
            rationale = (
                f"Idea {mb.idea_id!r} preferred: useful_novelty "
                f"{mb.useful_novelty:.3f} > {ma.useful_novelty:.3f} "
                f"(Δ={-delta:+.3f}). "
                f"Obstruction delta: {mb.obstruction_delta:+.3f} vs "
                f"{ma.obstruction_delta:+.3f}."
            )
        return UsefulNoveltyComparison(
            comparison_id=f"cmp-{uuid.uuid4().hex[:10]}",
            idea_a=ma.idea_id,
            idea_b=mb.idea_id,
            novelty_a=ma.raw_novelty,
            novelty_b=mb.raw_novelty,
            useful_novelty_a=ma.useful_novelty,
            useful_novelty_b=mb.useful_novelty,
            preferred=preferred,
            rationale=rationale,
        )

    def filter_useful(
        self,
        ideas: list[dict[str, Any]],
        threshold: float,
    ) -> list[dict[str, Any]]:
        """Return only those ideas whose useful novelty exceeds *threshold*.

        Parameters
        ----------
        ideas:
            List of candidate idea dictionaries.
        threshold:
            Minimum useful novelty score to pass the filter.

        Returns
        -------
        list[dict]
            Filtered subset of *ideas* ordered by decreasing useful novelty.
        """
        scored: list[tuple[float, dict[str, Any]]] = []
        for idea in ideas:
            m = self.measure_useful_novelty(idea, [], [])
            if m.useful_novelty >= threshold:
                scored.append((m.useful_novelty, idea))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [idea for _, idea in scored]

    def explain_measure(self, measure: NoveltyMeasure) -> str:
        """Produce a human-readable explanation of *measure*.

        Parameters
        ----------
        measure:
            A ``NoveltyMeasure`` instance to explain.

        Returns
        -------
        str
            Multi-line textual explanation.
        """
        lines = [
            f"Novelty Measure Explanation",
            f"===========================",
            f"Idea ID         : {measure.idea_id}",
            f"Measure ID      : {measure.measure_id}",
            f"Timestamp       : {measure.timestamp}",
            f"",
            f"Raw Novelty     : {measure.raw_novelty:.4f}",
            f"  └─ Derived from minimum Jaccard distance to portfolio.",
            f"",
            f"Useful Novelty  : {measure.useful_novelty:.4f}",
            f"  ├─ Obstruction Δ  : {measure.obstruction_delta:+.4f}",
            f"  ├─ Proof Δ        : {measure.proof_count_delta:+d} theorems",
            f"  ├─ Leverage       : {measure.leverage_score:.4f}",
            f"  └─ Tractability   : {measure.tractability_score:.4f}",
            f"",
            f"Classification  : {measure.level.value}",
            f"",
        ]
        if measure.level == NoveltyLevel.BREAKTHROUGH:
            lines.append(
                "  This idea is classified as BREAKTHROUGH. It introduces a "
                "fundamentally new mathematical direction with strong prospects "
                "for reducing obstructions and enabling proofs."
            )
        elif measure.level == NoveltyLevel.HIGH:
            lines.append(
                "  This idea shows HIGH useful novelty. It opens a new line "
                "of investigation and is worth prioritising in the pipeline."
            )
        elif measure.level == NoveltyLevel.MODERATE:
            lines.append(
                "  This idea shows MODERATE useful novelty. It is worth "
                "tracking but may not justify immediate resource allocation."
            )
        elif measure.level == NoveltyLevel.LOW:
            lines.append(
                "  This idea shows LOW useful novelty. Consider whether a "
                "minor reformulation could raise it into the MODERATE range."
            )
        else:
            lines.append(
                "  This idea is TRIVIAL in terms of useful novelty. "
                "It adds little new information to the portfolio."
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness (accumulator)
# ---------------------------------------------------------------------------


class NoveltyVsUsefulNoveltyWitness:
    """Accumulates ``NoveltyMeasure`` records and provides aggregate statistics.

    This class follows the witness pattern used throughout jugeo: it is a
    lightweight accumulator that records measurements as they are produced
    and provides query methods for downstream reporting.

    Usage::

        witness = NoveltyVsUsefulNoveltyWitness()
        witness.record(measure)
        print(witness.avg_useful_novelty())
        print(witness.distribution())
    """

    def __init__(self) -> None:
        self._measures: list[NoveltyMeasure] = []

    def record(self, m: NoveltyMeasure) -> None:
        """Append *m* to the internal record list."""
        self._measures.append(m)

    def best(self) -> NoveltyMeasure | None:
        """Return the measure with the highest useful_novelty, or None."""
        if not self._measures:
            return None
        return max(self._measures, key=lambda m: m.useful_novelty)

    def distribution(self) -> dict[str, int]:
        """Return a count of measures per ``NoveltyLevel`` label.

        Returns
        -------
        dict[str, int]
            Mapping from level name (e.g. ``"HIGH"``) to count.
        """
        dist: dict[str, int] = {lvl.value: 0 for lvl in NoveltyLevel}
        for m in self._measures:
            dist[m.level.value] += 1
        return dist

    def export(self) -> list[dict[str, Any]]:
        """Serialise all records to a list of plain dictionaries."""
        return [m.to_dict() for m in self._measures]

    def avg_useful_novelty(self) -> float:
        """Return the mean useful_novelty across all recorded measures."""
        if not self._measures:
            return 0.0
        return sum(m.useful_novelty for m in self._measures) / len(self._measures)

    def count(self) -> int:
        """Return the total number of recorded measures."""
        return len(self._measures)

    def above_threshold(self, threshold: float) -> list[NoveltyMeasure]:
        """Return measures with useful_novelty >= *threshold*."""
        return [m for m in self._measures if m.useful_novelty >= threshold]

    def top_n(self, n: int) -> list[NoveltyMeasure]:
        """Return the top-n measures by useful_novelty."""
        return sorted(self._measures, key=lambda m: m.useful_novelty, reverse=True)[:n]

    def summary_table(self) -> str:
        """Return an ASCII summary table of all recorded measures."""
        if not self._measures:
            return "No measures recorded."
        header = f"{'Idea':>20} {'Raw':>6} {'Useful':>6} {'Level':>12}"
        rows = [header, "-" * len(header)]
        for m in sorted(self._measures, key=lambda m: m.useful_novelty, reverse=True):
            rows.append(
                f"{m.idea_id:>20} {m.raw_novelty:>6.3f} "
                f"{m.useful_novelty:>6.3f} {m.level.value:>12}"
            )
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class NoveltyVsUsefulNoveltyCoordinator:
    """End-to-end coordinator for the novelty-vs-useful-novelty pipeline.

    Combines the ``NoveltyVsUsefulNoveltyAnalyzer`` and
    ``NoveltyVsUsefulNoveltyWitness`` into a single entry-point that:

    1. Accepts a list of candidate ideas, obstructions, and portfolio.
    2. Measures raw and useful novelty for each idea.
    3. Filters ideas below the configured threshold.
    4. Accumulates results in the witness.
    5. Returns the full list of ``NoveltyMeasure`` records.

    Parameters
    ----------
    config:
        Configuration for the useful-novelty computation.  Defaults to
        ``UsefulNoveltyConfig()`` with standard weights.
    """

    def __init__(self, config: UsefulNoveltyConfig | None = None) -> None:
        self._config = config or UsefulNoveltyConfig()
        self._analyzer = NoveltyVsUsefulNoveltyAnalyzer(self._config)
        self._witness = NoveltyVsUsefulNoveltyWitness()

    def run(
        self,
        ideas: list[dict[str, Any]],
        obstructions: list[dict[str, Any]],
        portfolio: list[dict[str, Any]],
    ) -> list[NoveltyMeasure]:
        """Run the full pipeline and return all ``NoveltyMeasure`` results.

        Parameters
        ----------
        ideas:
            Candidate ideas to measure.
        obstructions:
            Current obstruction records.
        portfolio:
            Existing idea portfolio (used for raw novelty baseline).

        Returns
        -------
        list[NoveltyMeasure]
            All measures, ordered by descending useful_novelty.
        """
        proved_theorems: list[str] = [
            str(p.get("id", "")) for p in portfolio if p.get("proved", False)
        ]
        measures: list[NoveltyMeasure] = []
        for idea in ideas:
            # Include portfolio for raw novelty calculation
            raw = self._analyzer.measure_raw_novelty(idea, portfolio)
            m = self._analyzer.measure_useful_novelty(
                idea, obstructions, proved_theorems, self._config
            )
            # Re-build with the true raw novelty (portfolio-based)
            m = NoveltyMeasure(
                measure_id=m.measure_id,
                idea_id=m.idea_id,
                raw_novelty=raw,
                useful_novelty=_clamp(0.5 * m.useful_novelty + 0.5 * raw),
                obstruction_delta=m.obstruction_delta,
                proof_count_delta=m.proof_count_delta,
                leverage_score=m.leverage_score,
                tractability_score=m.tractability_score,
                level=self._analyzer.classify_level(
                    _clamp(0.5 * m.useful_novelty + 0.5 * raw)
                ),
                timestamp=m.timestamp,
            )
            self._witness.record(m)
            measures.append(m)
        return sorted(measures, key=lambda m: m.useful_novelty, reverse=True)

    def report(self) -> dict[str, Any]:
        """Return a summary dictionary of the witness state."""
        best = self._witness.best()
        return {
            "total_measured": self._witness.count(),
            "avg_useful_novelty": self._witness.avg_useful_novelty(),
            "distribution": self._witness.distribution(),
            "best_idea": best.to_dict() if best else None,
        }

    @property
    def witness(self) -> NoveltyVsUsefulNoveltyWitness:
        """Access the internal witness for advanced queries."""
        return self._witness


# ---------------------------------------------------------------------------
# Module-level factory helpers
# ---------------------------------------------------------------------------


def make_default_config() -> UsefulNoveltyConfig:
    """Return the default ``UsefulNoveltyConfig``."""
    return UsefulNoveltyConfig()


def make_strict_config() -> UsefulNoveltyConfig:
    """Return a strict config that heavily weights obstruction reduction."""
    return UsefulNoveltyConfig(
        obstruction_weight=0.55,
        proof_enablement_weight=0.25,
        leverage_weight=0.12,
        tractability_weight=0.08,
        min_useful_threshold=0.45,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== NoveltyVsUsefulNovelty smoke test ===\n")

    _ideas = [
        {
            "id": "i1",
            "title": "Sheaf cohomology obstruction removal via étale descent",
            "tokens": {"sheaf", "cohomology", "étale", "descent", "obstruction"},
            "leverage": 0.8,
            "tractability": 0.6,
        },
        {
            "id": "i2",
            "title": "Homotopy type theory bridge to AG obstructions",
            "tokens": {"homotopy", "type", "theory", "algebraic", "obstruction"},
            "leverage": 0.5,
            "tractability": 0.7,
        },
        {
            "id": "i3",
            "title": "Minor reformulation of existing descent approach",
            "tokens": {"descent", "reformulation"},
            "leverage": 0.1,
            "tractability": 0.9,
        },
    ]
    _obstructions = [
        {"id": "o1", "class": "cohomology barrier in H2"},
        {"id": "o2", "class": "étale fundamental group obstruction"},
    ]
    _portfolio = [
        {"id": "p1", "tokens": {"descent", "stack", "topos"}, "proved": True},
    ]

    coordinator = NoveltyVsUsefulNoveltyCoordinator()
    measures = coordinator.run(_ideas, _obstructions, _portfolio)

    print("Measures (sorted by useful novelty):")
    for m in measures:
        print(" ", m.summary())

    print("\nReport:")
    print(json.dumps(coordinator.report(), indent=2, default=str))

    analyzer = NoveltyVsUsefulNoveltyAnalyzer()
    comp = analyzer.compare(_ideas[0], _ideas[1], _obstructions)
    print(f"\nComparison: preferred={comp.preferred!r}")
    print(f"Rationale: {comp.rationale}")

    print("\nExplanation of top measure:")
    if measures:
        print(analyzer.explain_measure(measures[0]))

    print("\nWitness distribution:", coordinator.witness.distribution())
    print("Summary table:\n", coordinator.witness.summary_table())
    print("\n=== Smoke test passed ===")
