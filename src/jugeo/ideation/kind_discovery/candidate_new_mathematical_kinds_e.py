"""Candidate new mathematical kinds — emergence and hypothesis (S02-CK).

Once obstruction evidence has been gathered (see
``obstruction_fields_as_evidence_of``), the next question is: *what kind
should we introduce to eliminate those obstructions?*  This module answers
that question by generating, scoring, and ranking :class:`KindHypothesis`
objects, then proposing concrete type-constructor sketches via
:class:`TypeConstructorProposal`.

# copilot: generated for jugeo.ideation.kind_discovery — candidate kinds layer

Module layout::

    ┌─────────────────────────────────────────────────────────────────┐
    │  jugeo.ideation.kind_discovery.candidate_new_mathematical_  │
    │  kinds_e                                                        │
    ├─────────────────────────────────────────────────────────────────┤
    │  Helpers                                                        │
    │    _clamp           clamp a float to [lo, hi]                  │
    │    _now_iso         UTC timestamp                               │
    │    _hypothesis_id   fresh UUID for a hypothesis                │
    │    _proposal_id     fresh UUID for a type-constructor proposal  │
    │    _composite_score weighted combination of sub-scores         │
    ├─────────────────────────────────────────────────────────────────┤
    │  Enums                                                          │
    │    AbstractionLevel  ELEMENTARY / INTERMEDIATE / ADVANCED /    │
    │                      FOUNDATIONAL                               │
    ├─────────────────────────────────────────────────────────────────┤
    │  Value objects (frozen dataclasses)                             │
    │    CandidateKindConfig        hyper-parameters                  │
    │    KindHypothesis             a single hypothesis with scores   │
    │    TypeConstructorProposal    the proposed constructor sketch   │
    ├─────────────────────────────────────────────────────────────────┤
    │  Stateful services                                              │
    │    CandidateKindsAnalyzer      generates & ranks hypotheses    │
    │    CandidateKindsWitness       records accepted hypotheses      │
    │    CandidateKindsCoordinator   orchestrator                     │
    └─────────────────────────────────────────────────────────────────┘

Domain motivation
─────────────────
A *kind* in the jugeo sense is a type-level classifier — the analogue of a
Haskell kind like ``* -> *`` but enriched with algebraic laws (associativity,
commutativity, idempotence, …).  When a cluster of program coordinates all
fail for the same structural reason, that reason often points to an absent
kind: the type system lacks a constructor that would make the pattern
expressible.

The hypothesis lifecycle:
  1. A cluster from the evidence stage provides the raw material.
  2. :class:`CandidateKindsAnalyzer` builds a :class:`KindHypothesis` by
     extracting a name, description, and abstraction level.
  3. The hypothesis is scored on three dimensions:
     - *novelty*: how different from existing kinds?
     - *tractability*: how easy would it be to implement?
     - *coverage*: how many coordinates does it explain?
  4. A :class:`TypeConstructorProposal` refines the hypothesis into a
     concrete constructor sketch with parameter types, return type, and laws.
  5. The :class:`CandidateKindsWitness` records accepted hypotheses for
     consumption by the downstream pipeline.

Scoring details
───────────────
The composite score is a weighted linear combination:

    composite = w_n * novelty + w_t * tractability + w_c * coverage

The default weights from :class:`CandidateKindConfig` are 0.4 / 0.3 / 0.3,
reflecting the prior that novelty is slightly more important (we want to
discover genuinely new patterns, not rediscover monad or functor).

Abstraction levels
──────────────────
:class:`AbstractionLevel` encodes how abstract a hypothesised kind is:

  ELEMENTARY   — concrete; could be expressed today with minor syntactic sugar
  INTERMEDIATE — requires a small extension to the kind system
  ADVANCED     — requires significant type-theoretic machinery
  FOUNDATIONAL — touches the metatheory itself

The level influences both tractability (higher abstraction → lower
tractability) and the vocabulary used in the generated code sketch.
"""

from __future__ import annotations

import datetime
import enum
import re
import uuid
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Cross-package imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.kind_discovery.models import (
        KindCandidate,
        KindStatus,
        NewKind,
        KindBootstrapPlan,
    )
except ImportError:
    KindCandidate = None  # type: ignore[assignment,misc]
    KindStatus = None  # type: ignore[assignment,misc]
    NewKind = None  # type: ignore[assignment,misc]
    KindBootstrapPlan = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.kind_discovery.obstruction_fields_as_evidence_of import (
        ObstructionFieldEvidenceRecord,
    )
except ImportError:
    ObstructionFieldEvidenceRecord = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Label applied when a hypothesis name cannot be inferred from context.
DEFAULT_HYPOTHESIS_NAME: str = "unnamed-kind"

#: Prefix applied to all generated type-constructor names.
CONSTRUCTOR_PREFIX: str = "Mk"

#: Laws that every proposed kind must satisfy unless explicitly overridden.
UNIVERSAL_LAWS: tuple[str, ...] = (
    "identity-law",
    "composition-associativity",
    "type-preservation",
)

#: Maximum length of a generated code sketch (characters).
MAX_SKETCH_LENGTH: int = 500

#: Score below which a hypothesis is considered non-viable.
VIABILITY_FLOOR: float = 0.1

#: Abstraction-level penalty applied to tractability scores.
ABSTRACTION_PENALTIES: dict[str, float] = {
    "ELEMENTARY": 0.0,
    "INTERMEDIATE": 0.15,
    "ADVANCED": 0.30,
    "FOUNDATIONAL": 0.50,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float, hi: float) -> float:
    """Return *v* clamped to [*lo*, *hi*].

    >>> _clamp(2.0, 0.0, 1.0)
    1.0
    >>> _clamp(-1.0, 0.0, 1.0)
    0.0
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return the current UTC instant in ISO-8601 format.

    Example: ``"2024-06-01T09:00:00Z"``
    """
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _hypothesis_id() -> str:
    """Generate a unique hypothesis identifier.

    Format: ``"hyp-<8 hex chars>"``

    Returns
    -------
    str
        A fresh identifier string.
    """
    return "hyp-" + uuid.uuid4().hex[:8]


def _proposal_id() -> str:
    """Generate a unique type-constructor proposal identifier.

    Format: ``"prop-<8 hex chars>"``

    Returns
    -------
    str
        A fresh identifier string.
    """
    return "prop-" + uuid.uuid4().hex[:8]


def _composite_score(
    novelty: float,
    tract: float,
    coverage: float,
    wn: float,
    wt: float,
    wc: float,
) -> float:
    """Compute the weighted composite score for a kind hypothesis.

    The formula is:

        composite = clamp(wn * novelty + wt * tractability + wc * coverage, 0, 1)

    All inputs are assumed to be in [0.0, 1.0].  The weights do not need to
    sum to 1.0; the result is clamped regardless.

    Parameters
    ----------
    novelty, tract, coverage:
        The three sub-scores, each in [0.0, 1.0].
    wn, wt, wc:
        The corresponding weights.

    Returns
    -------
    float
        The composite score in [0.0, 1.0].

    Examples
    --------
    >>> _composite_score(0.8, 0.6, 0.7, 0.4, 0.3, 0.3)
    0.71
    """
    raw = wn * novelty + wt * tract + wc * coverage
    return _clamp(round(raw, 6), 0.0, 1.0)


def _tokenize(text: str) -> set[str]:
    """Return a set of lowercase alphabetic tokens from *text*."""
    return set(re.findall(r"[a-zA-Z]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AbstractionLevel(str, enum.Enum):
    """The level of abstraction of a hypothesised kind.

    Attributes
    ----------
    ELEMENTARY:
        Could be expressed today with minor syntactic sugar.  Tractability
        penalty: 0%.
    INTERMEDIATE:
        Requires a small extension to the kind system.  Tractability
        penalty: 15%.
    ADVANCED:
        Requires significant type-theoretic machinery.  Tractability
        penalty: 30%.
    FOUNDATIONAL:
        Touches the metatheory itself.  Tractability penalty: 50%.
    """

    ELEMENTARY = "ELEMENTARY"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"
    FOUNDATIONAL = "FOUNDATIONAL"

    def tractability_penalty(self) -> float:
        """Return the tractability penalty associated with this level."""
        return ABSTRACTION_PENALTIES.get(self.value, 0.0)

    def description(self) -> str:
        """Return a one-line description of what this level implies."""
        descriptions = {
            "ELEMENTARY": "Expressible today with minor syntactic sugar.",
            "INTERMEDIATE": "Requires a small extension to the kind system.",
            "ADVANCED": "Requires significant type-theoretic machinery.",
            "FOUNDATIONAL": "Touches the metatheory itself.",
        }
        return descriptions[self.value]


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateKindConfig:
    """Hyper-parameters for the candidate-kind emergence pipeline.

    Attributes
    ----------
    min_evidence_strength:
        Only evidence records whose strength exceeds this value are used to
        generate hypotheses.
    max_candidates:
        Hard cap on the number of hypotheses that can be generated in a
        single coordinator run.
    novelty_threshold:
        Hypotheses whose novelty score falls below this value are discarded
        as too similar to existing kinds.
    abstraction_level_weight:
        Weight given to abstraction level when estimating tractability.
        Higher values favour elementary kinds.
    tractability_weight:
        Weight of tractability in the composite score.
    coverage_weight:
        Weight of coverage (fraction of coordinates explained) in the
        composite score.
    """

    min_evidence_strength: float = 0.4
    max_candidates: int = 50
    novelty_threshold: float = 0.3
    abstraction_level_weight: float = 0.4
    tractability_weight: float = 0.3
    coverage_weight: float = 0.3

    def novelty_weight(self) -> float:
        """Return the implied novelty weight (1 - tractability - coverage)."""
        return _clamp(
            1.0 - self.tractability_weight - self.coverage_weight, 0.0, 1.0
        )


@dataclass(frozen=True, slots=True)
class KindHypothesis:
    """A single hypothesis about a missing mathematical kind.

    Instances are immutable; use :func:`dataclasses.replace` to create
    modified copies with updated scores.

    Attributes
    ----------
    hypothesis_id:
        Unique identifier for this hypothesis.
    name:
        Short name for the hypothesised kind, e.g. ``"CovariantFunctor"``.
    description:
        A paragraph-length description of what the kind represents and why
        it is missing from the current type system.
    abstraction_level:
        How abstract the kind is; one of :class:`AbstractionLevel`.
    type_constructor_sketch:
        A rough sketch of the kind's type constructor, e.g.
        ``"data F a where { ... }"``.
    evidence_ids:
        IDs of the evidence records that support this hypothesis.
    novelty_score:
        How different this kind is from all known existing kinds.  In [0,1].
    tractability_score:
        How easy this kind would be to implement.  In [0,1].
    coverage_score:
        Fraction of the cluster coordinates that this kind would explain.
        In [0,1].
    composite_score:
        Weighted combination of the three sub-scores.  In [0,1].
    timestamp:
        UTC timestamp at which this hypothesis was created.
    """

    hypothesis_id: str
    name: str
    description: str
    abstraction_level: AbstractionLevel
    type_constructor_sketch: str
    evidence_ids: tuple[str, ...]
    novelty_score: float
    tractability_score: float
    coverage_score: float
    composite_score: float
    timestamp: str

    # ------------------------------------------------------------------
    # Predicates and helpers
    # ------------------------------------------------------------------

    def is_viable(self) -> bool:
        """Return True if the composite score exceeds the viability floor."""
        return self.composite_score >= VIABILITY_FLOOR

    def is_novel_enough(self, threshold: float = 0.3) -> bool:
        """Return True if the novelty score exceeds *threshold*."""
        return self.novelty_score >= threshold

    def short_summary(self) -> str:
        """Return a one-line summary string."""
        return (
            f"[{self.hypothesis_id}] {self.name} "
            f"(composite={self.composite_score:.3f}, "
            f"level={self.abstraction_level.value})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain Python dict."""
        return {
            "hypothesis_id": self.hypothesis_id,
            "name": self.name,
            "description": self.description,
            "abstraction_level": self.abstraction_level.value,
            "type_constructor_sketch": self.type_constructor_sketch,
            "evidence_ids": list(self.evidence_ids),
            "novelty_score": self.novelty_score,
            "tractability_score": self.tractability_score,
            "coverage_score": self.coverage_score,
            "composite_score": self.composite_score,
            "timestamp": self.timestamp,
            "is_viable": self.is_viable(),
        }


@dataclass(frozen=True, slots=True)
class TypeConstructorProposal:
    """A concrete type-constructor sketch derived from a :class:`KindHypothesis`.

    Where the hypothesis is abstract ("we need a kind like X"), the proposal
    is concrete ("here is the Haskell-ish type signature and its laws").

    Attributes
    ----------
    proposal_id:
        Unique identifier for this proposal.
    kind_hypothesis_id:
        The hypothesis from which this proposal was derived.
    constructor_name:
        The proposed name for the type constructor, e.g. ``"MkComonoid"``.
    parameter_types:
        The kinds of the type parameters, e.g. ``("a", "b")``.
    return_type:
        The return kind of the constructor, e.g. ``"* -> *"``.
    laws:
        The algebraic laws the constructed type must satisfy.
    examples:
        Short code strings illustrating usage.
    """

    proposal_id: str
    kind_hypothesis_id: str
    constructor_name: str
    parameter_types: tuple[str, ...]
    return_type: str
    laws: tuple[str, ...]
    examples: tuple[str, ...]

    def arity(self) -> int:
        """Return the number of type parameters."""
        return len(self.parameter_types)

    def signature(self) -> str:
        """Return a type-signature string for this constructor.

        Example: ``"MkFunctor :: (* -> *) -> *"``
        """
        params = " -> ".join(self.parameter_types) if self.parameter_types else "()"
        return f"{self.constructor_name} :: {params} -> {self.return_type}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain Python dict."""
        return {
            "proposal_id": self.proposal_id,
            "kind_hypothesis_id": self.kind_hypothesis_id,
            "constructor_name": self.constructor_name,
            "parameter_types": list(self.parameter_types),
            "return_type": self.return_type,
            "laws": list(self.laws),
            "examples": list(self.examples),
            "arity": self.arity(),
            "signature": self.signature(),
        }


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------


class CandidateKindsAnalyzer:
    """Generates, scores, ranks, and explains kind hypotheses.

    This class is the intellectual heart of the candidate-kinds stage.  It
    is designed to be used with fresh instances per coordinator run so that
    internal caches do not accumulate stale data.

    Parameters
    ----------
    config:
        Hyper-parameters.  If ``None``, uses :class:`CandidateKindConfig`
        with all defaults.

    Examples
    --------
    ::

        cfg = CandidateKindConfig(min_evidence_strength=0.6)
        analyzer = CandidateKindsAnalyzer(cfg)
        clusters = [{"centroid": "non-associative-composition", "size": 5}]
        evidence = {"evidence_id": "ev-abc", "field_id": "f-1", "evidence_strength": 0.75}
        hyp = analyzer.generate_hypothesis(clusters, evidence)
        scored = analyzer.score_hypothesis(hyp, cfg)
    """

    def __init__(self, config: CandidateKindConfig | None = None) -> None:
        self._config = config or CandidateKindConfig()

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def generate_hypothesis(
        self,
        clusters: list[dict],
        evidence: dict,
    ) -> KindHypothesis:
        """Generate an initial (unscored) :class:`KindHypothesis` from clusters and evidence.

        The hypothesis name is derived from the most common token across
        cluster centroid descriptions; the description is assembled from
        the evidence metadata.

        Parameters
        ----------
        clusters:
            List of cluster dicts, each with at least a ``"centroid"`` key.
        evidence:
            The evidence record dict from the upstream stage.

        Returns
        -------
        KindHypothesis
            A newly constructed hypothesis with default/estimated scores.
        """
        # Extract name from cluster centroids
        all_tokens: list[str] = []
        for c in clusters:
            centroid = str(c.get("centroid", c.get("centroid_description", "")))
            all_tokens.extend(_tokenize(centroid))

        from collections import Counter

        stop = {"the", "of", "and", "a", "in", "to", "is", "for", "with", "or", "kind"}
        freq = Counter(t for t in all_tokens if t not in stop and len(t) >= 4)
        top_token = freq.most_common(1)[0][0] if freq else DEFAULT_HYPOTHESIS_NAME
        name = "".join(w.capitalize() for w in top_token.split("-")) + "Kind"

        evidence_id = str(evidence.get("evidence_id", "unknown"))
        field_id = str(evidence.get("field_id", "unknown"))
        cluster_count = len(clusters)
        total_coords = sum(int(c.get("size", 1)) for c in clusters)

        description = (
            f"Hypothesis: the type system is missing a kind '{name}'. "
            f"Evidence drawn from field '{field_id}' with {cluster_count} cluster(s) "
            f"covering {total_coords} coordinate(s).  "
            f"The recurrence of '{top_token}' across obstruction classes "
            f"suggests a structural gap that a new kind constructor could fill."
        )

        # Estimate abstraction level from cluster complexity
        if total_coords < 5:
            level = AbstractionLevel.ELEMENTARY
        elif total_coords < 15:
            level = AbstractionLevel.INTERMEDIATE
        elif total_coords < 40:
            level = AbstractionLevel.ADVANCED
        else:
            level = AbstractionLevel.FOUNDATIONAL

        sketch = (
            f"newtype {name} (f :: * -> *) a = {CONSTRUCTOR_PREFIX}{name} "
            f"{{ run{name} :: f a }}"
        )

        # Placeholder scores — overridden by score_hypothesis
        novelty = _clamp(0.5 + 0.05 * cluster_count, 0.0, 1.0)
        tractability = _clamp(1.0 - level.tractability_penalty(), 0.0, 1.0)
        coverage = _clamp(min(total_coords / 20.0, 1.0), 0.0, 1.0)
        cfg = self._config
        composite = _composite_score(
            novelty, tractability, coverage,
            cfg.novelty_weight(), cfg.tractability_weight, cfg.coverage_weight,
        )

        return KindHypothesis(
            hypothesis_id=_hypothesis_id(),
            name=name,
            description=description,
            abstraction_level=level,
            type_constructor_sketch=sketch,
            evidence_ids=(evidence_id,),
            novelty_score=novelty,
            tractability_score=tractability,
            coverage_score=coverage,
            composite_score=composite,
            timestamp=_now_iso(),
        )

    def score_hypothesis(
        self,
        hyp: KindHypothesis,
        config: CandidateKindConfig,
    ) -> KindHypothesis:
        """Recompute and update the composite score of *hyp* using *config*.

        Since :class:`KindHypothesis` is frozen, this method returns a *new*
        instance with updated ``composite_score``.

        Parameters
        ----------
        hyp:
            The hypothesis to re-score.
        config:
            The configuration whose weights to apply.

        Returns
        -------
        KindHypothesis
            A new :class:`KindHypothesis` with an updated composite score.
        """
        import dataclasses

        penalty = hyp.abstraction_level.tractability_penalty()
        adjusted_tractability = _clamp(hyp.tractability_score - penalty, 0.0, 1.0)
        new_composite = _composite_score(
            hyp.novelty_score,
            adjusted_tractability,
            hyp.coverage_score,
            config.novelty_weight(),
            config.tractability_weight,
            config.coverage_weight,
        )
        return dataclasses.replace(
            hyp,
            tractability_score=adjusted_tractability,
            composite_score=new_composite,
        )

    def rank_hypotheses(
        self, hypotheses: list[KindHypothesis]
    ) -> list[KindHypothesis]:
        """Sort *hypotheses* in descending order of composite score.

        Parameters
        ----------
        hypotheses:
            Unordered list of hypotheses.

        Returns
        -------
        list[KindHypothesis]
            The same hypotheses sorted by composite score, best first.
        """
        return sorted(hypotheses, key=lambda h: h.composite_score, reverse=True)

    def propose_type_constructor(self, hyp: KindHypothesis) -> TypeConstructorProposal:
        """Derive a :class:`TypeConstructorProposal` from a scored hypothesis.

        The proposal fills in the type-constructor details using heuristics
        based on the hypothesis name, abstraction level, and description
        tokens.

        Parameters
        ----------
        hyp:
            The hypothesis to refine.

        Returns
        -------
        TypeConstructorProposal
            A concrete proposal ready for bootstrap consumption.
        """
        cname = CONSTRUCTOR_PREFIX + hyp.name
        # Parameter types scaled by abstraction
        if hyp.abstraction_level == AbstractionLevel.ELEMENTARY:
            params = ("a",)
        elif hyp.abstraction_level == AbstractionLevel.INTERMEDIATE:
            params = ("f :: * -> *", "a")
        elif hyp.abstraction_level == AbstractionLevel.ADVANCED:
            params = ("f :: (* -> *) -> *", "g :: * -> *", "a")
        else:
            params = ("φ :: (Type -> Type) -> Type -> Type", "a")

        return_type = "* -> *" if "functor" in hyp.name.lower() else "*"

        laws = UNIVERSAL_LAWS + (f"{hyp.name.lower()}-coherence",)
        examples = (
            f"-- {hyp.name} example",
            f"instance {hyp.name} Identity where",
            f"  run{hyp.name} (Identity x) = x",
        )

        return TypeConstructorProposal(
            proposal_id=_proposal_id(),
            kind_hypothesis_id=hyp.hypothesis_id,
            constructor_name=cname,
            parameter_types=params,
            return_type=return_type,
            laws=laws,
            examples=examples,
        )

    def filter_novel(
        self,
        hypotheses: list[KindHypothesis],
        existing_kinds: list[str],
    ) -> list[KindHypothesis]:
        """Remove hypotheses that are too similar to existing kinds.

        Similarity is computed as Jaccard overlap between the hypothesis
        name tokens and each existing-kind string.  A hypothesis whose
        maximum overlap with any existing kind exceeds
        ``(1 - novelty_threshold)`` is discarded.

        Parameters
        ----------
        hypotheses:
            Candidates to filter.
        existing_kinds:
            Names or descriptions of kinds that already exist.

        Returns
        -------
        list[KindHypothesis]
            The subset of *hypotheses* that are novel enough.
        """
        threshold = self._config.novelty_threshold
        existing_token_sets = [_tokenize(k) for k in existing_kinds]

        def _is_novel(hyp: KindHypothesis) -> bool:
            hyp_tokens = _tokenize(hyp.name) | _tokenize(hyp.description)
            for ets in existing_token_sets:
                sim = _jaccard(hyp_tokens, ets)
                if sim > (1.0 - threshold):
                    return False
            return True

        return [h for h in hypotheses if _is_novel(h)]

    def explain_hypothesis(self, hyp: KindHypothesis) -> str:
        """Produce a multi-line human-readable explanation of a hypothesis.

        Parameters
        ----------
        hyp:
            The hypothesis to explain.

        Returns
        -------
        str
            A formatted multi-line string suitable for logs or REPL output.
        """
        lines = [
            f"Kind Hypothesis {hyp.hypothesis_id}",
            "=" * 60,
            f"Name:             {hyp.name}",
            f"Abstraction:      {hyp.abstraction_level.value} — "
            f"{hyp.abstraction_level.description()}",
            f"Novelty:          {hyp.novelty_score:.4f}",
            f"Tractability:     {hyp.tractability_score:.4f}",
            f"Coverage:         {hyp.coverage_score:.4f}",
            f"Composite:        {hyp.composite_score:.4f}",
            f"Viable:           {'YES' if hyp.is_viable() else 'NO'}",
            f"Evidence IDs:     {', '.join(hyp.evidence_ids)}",
            f"Recorded at:      {hyp.timestamp}",
            "",
            "Description:",
            f"  {hyp.description}",
            "",
            "Type Constructor Sketch:",
            f"  {hyp.type_constructor_sketch}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class CandidateKindsWitness:
    """Records and queries :class:`KindHypothesis` objects.

    Acts as an append-only log for hypotheses generated during a pipeline
    run.  Queries are read-only and do not mutate state.

    Usage::

        witness = CandidateKindsWitness()
        witness.record(hyp)
        best = witness.top_n(3)
        print(witness.accepted_count())
        data = witness.export()
    """

    def __init__(self) -> None:
        self._hypotheses: list[KindHypothesis] = []

    def record(self, hyp: KindHypothesis) -> None:
        """Append *hyp* to the internal log.

        Parameters
        ----------
        hyp:
            The hypothesis to record.
        """
        self._hypotheses.append(hyp)

    def top_n(self, n: int) -> list[KindHypothesis]:
        """Return the *n* highest-scoring hypotheses.

        The hypotheses are sorted by composite score in descending order.

        Parameters
        ----------
        n:
            Number of hypotheses to return.

        Returns
        -------
        list[KindHypothesis]
            Up to *n* hypotheses, best-first.
        """
        sorted_hyps = sorted(
            self._hypotheses, key=lambda h: h.composite_score, reverse=True
        )
        return sorted_hyps[:n]

    def accepted_count(self) -> int:
        """Return the total number of recorded hypotheses."""
        return len(self._hypotheses)

    def viable_count(self) -> int:
        """Return the number of hypotheses that pass the viability floor."""
        return sum(1 for h in self._hypotheses if h.is_viable())

    def avg_composite(self) -> float:
        """Return the average composite score, or 0.0 if empty."""
        if not self._hypotheses:
            return 0.0
        return sum(h.composite_score for h in self._hypotheses) / len(self._hypotheses)

    def by_level(self, level: AbstractionLevel) -> list[KindHypothesis]:
        """Return all hypotheses with the given abstraction level.

        Parameters
        ----------
        level:
            The :class:`AbstractionLevel` to filter on.

        Returns
        -------
        list[KindHypothesis]
            Matching hypotheses in insertion order.
        """
        return [h for h in self._hypotheses if h.abstraction_level == level]

    def export(self) -> list[dict[str, Any]]:
        """Serialise all recorded hypotheses to a list of dicts.

        Returns
        -------
        list[dict[str, Any]]
            One dict per hypothesis, from :meth:`KindHypothesis.to_dict`.
        """
        return [h.to_dict() for h in self._hypotheses]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class CandidateKindsCoordinator:
    """End-to-end orchestrator for the candidate-kinds emergence stage.

    The coordinator wires together the analyzer and witness and exposes a
    simple :meth:`run` entry point.  Callers typically use this class rather
    than instantiating the analyzer and witness directly.

    Parameters
    ----------
    config:
        Pipeline hyper-parameters.  Defaults to :class:`CandidateKindConfig`.

    Example
    -------
    ::

        coord = CandidateKindsCoordinator()
        clusters = [{"centroid": "non-associative-kind", "size": 6}]
        evidence = {"evidence_id": "ev-001", "field_id": "f-algebra", "evidence_strength": 0.7}
        existing = ["Functor", "Monad", "Comonad"]
        hyps = coord.run(clusters, evidence, existing)
        print(coord.report())
    """

    def __init__(self, config: CandidateKindConfig | None = None) -> None:
        self._config = config or CandidateKindConfig()
        self.analyzer = CandidateKindsAnalyzer(self._config)
        self.witness = CandidateKindsWitness()

    def run(
        self,
        clusters: list[dict],
        evidence: dict,
        existing_kinds: list[str],
    ) -> list[KindHypothesis]:
        """Run the full candidate-kinds emergence pipeline.

        Steps:
        1. Generate a hypothesis from the clusters and evidence.
        2. Score the hypothesis.
        3. Filter out hypotheses that are not novel enough.
        4. Record accepted hypotheses in the witness.

        Parameters
        ----------
        clusters:
            Cluster dicts from the upstream evidence stage.
        evidence:
            The evidence record dict.
        existing_kinds:
            Names of kinds that already exist in the type system.

        Returns
        -------
        list[KindHypothesis]
            The list of accepted, scored, novel hypotheses.
        """
        # Guard on evidence strength
        strength = float(evidence.get("evidence_strength", 0.0))
        if strength < self._config.min_evidence_strength:
            return []

        # Generate and score a single hypothesis from this evidence record
        hyp = self.analyzer.generate_hypothesis(clusters, evidence)
        hyp = self.analyzer.score_hypothesis(hyp, self._config)

        # Filter for novelty
        novel = self.analyzer.filter_novel([hyp], existing_kinds)

        # Rank and cap
        ranked = self.analyzer.rank_hypotheses(novel)
        accepted = ranked[: self._config.max_candidates]

        for h in accepted:
            self.witness.record(h)

        return accepted

    def report(self) -> dict[str, Any]:
        """Return a coordinator snapshot report.

        Returns
        -------
        dict[str, Any]
            A dict with ``total``, ``viable``, ``avg_composite``, and
            ``top_hypotheses`` keys.
        """
        top = self.witness.top_n(5)
        return {
            "total": self.witness.accepted_count(),
            "viable": self.witness.viable_count(),
            "avg_composite": round(self.witness.avg_composite(), 4),
            "top_hypotheses": [h.to_dict() for h in top],
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    _clusters = [
        {"centroid": "non-associative-composition", "size": 5},
        {"centroid": "non-associative-pipe", "size": 4},
        {"centroid": "non-associative-chain", "size": 3},
    ]
    _evidence = {
        "evidence_id": "ev-abc123",
        "field_id": "expr-field",
        "evidence_strength": 0.72,
    }
    _existing = ["Functor", "Monad", "Applicative", "Comonad"]

    _coord = CandidateKindsCoordinator()
    _hyps = _coord.run(_clusters, _evidence, _existing)
    for _h in _hyps:
        print(_coord.analyzer.explain_hypothesis(_h))
        print()
        proposal = _coord.analyzer.propose_type_constructor(_h)
        print(json.dumps(proposal.to_dict(), indent=2))
    print(json.dumps(_coord.report(), indent=2))
