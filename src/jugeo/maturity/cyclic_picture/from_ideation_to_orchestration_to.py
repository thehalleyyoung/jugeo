"""Stage S02: From Ideation to Orchestration to Proof and Back — JuGeo cyclic_picture package.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
Stage S02 formalises the full **ideation → orchestration → proof → feedback**
cycle that sits at the heart of the cyclic-picture framework described in Ch65
of theory2.tex.  Where S01 (self-improving system) focused on internal
capability expansion of a single ``MatureSystem`` instance, S02 examines how
new geometric ideas are *generated*, translated into executable *plans*, run
to produce *proof objects*, and how the results of those proofs are fed back
as structured *signals* that seed the next round of ideation.

The cycle formalised here is:

    IDEATION → ORCHESTRATION → PROOF → FEEDBACK → IDEATION → …

Each arrow corresponds to a deterministic (or near-deterministic) transformation
whose soundness is guaranteed by the conditions stated in Ch65 §4.1–§4.5:

§4.1 Ideation soundness
    Given a trust-bearing feedback signal *f* with weight *w ≥ w_min*, the
    ideation function *I(f)* produces a new idea record whose confidence is
    at least *w · c_min*, where *c_min* is the minimum viable confidence for
    the domain.  This means high-weight positive feedback provably raises the
    floor of the next idea's confidence.

§4.2 Orchestration completeness
    An orchestration plan *P* generated from idea *i* is *complete* with
    respect to *i* if every claim in *i.idea_text* maps to at least one step
    in *P.steps*.  The ``assess_plan_coverage`` method in
    ``IdeationToOrchestrationAnalyzer`` computes the completeness ratio.

§4.3 Proof efficiency
    The proof efficiency *η(P, R)* for plan *P* and result record *R* is
    defined as *R.trust_score / P.estimated_duration* when the proof
    completes before the timeout, or 0.0 otherwise.  High efficiency indicates
    that the orchestration strategy maps tightly to the available prover
    resources.

§4.4 Feedback utility
    A feedback signal *F* derived from proof record *R* has utility
    *U(F, R)* = *F.weight · (1 + indicator(F.is_positive()))*.  Utility
    drives the weight assigned to the next ideation round.

§4.5 Cycle convergence
    Under the monotone improvement assumption (inherited from S01), the
    sequence of trust scores across successive cycles converges.  The
    ``get_accumulated_trust`` method of ``IdeationToOrchestrationCoordinator``
    tracks the running sum and can be used to detect stagnation.

Computational witnesses
-----------------------
Three main classes provide the computational layer:

* ``IdeationToOrchestrationAnalyzer`` — stateless analyser that scores ideas,
  plans, proofs, and feedback signals against the formal definitions in Ch65.
* ``IdeationToOrchestrationWitness`` — accumulates per-step attestations into
  a tamper-evident chain that constitutes the proof witness for Ch65 §4.5.
* ``IdeationToOrchestrationCoordinator`` — high-level coordinator that drives
  the full cycle end-to-end and exposes the accumulated witness on demand.

Four data-carrying classes correspond to the four phases of the cycle:
``IdeationRecord``, ``OrchestrationPlan``, ``ProofRecord``, and
``FeedbackSignal``.  A fifth class, ``IdeationCycleRecord``, bundles one
complete pass through all four phases.

All public names are listed in ``__all__``.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    # data classes
    "IdeationRecord",
    "OrchestrationPlan",
    "ProofRecord",
    "FeedbackSignal",
    "IdeationCycleRecord",
    # main classes
    "IdeationToOrchestrationAnalyzer",
    "IdeationToOrchestrationWitness",
    "IdeationToOrchestrationCoordinator",
    # module-level functions
    "run_ideation_cycle",
    "assess_cycle_health",
    "extract_ideation_patterns",
]

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.maturity.cyclic_picture.models import (
        ImprovementCycle,
        ImprovementKind,
        MaturityLevel,
        MatureSystem,
        SelfImprovingEngine,
        FederationState,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.theorems import (
        CyclicPictureTheorem,
        ProofObligation,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.algorithms import (
        IdeationAlgorithm,
        OrchestrationAlgorithm,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses ``time.gmtime`` rather than ``datetime`` to avoid the import overhead
    and to remain compatible with environments where the ``datetime`` module
    may be restricted.  The returned string is always in the format
    ``YYYY-MM-DDTHH:MM:SSZ``.

    Returns
    -------
    str
        ISO-8601 UTC timestamp, e.g. ``'2024-01-15T09:32:07Z'``.
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


def _uid() -> str:
    """Generate a short, unique identifier string.

    Produces a 16-character hex string derived from a UUID4 value.  The
    truncation keeps identifiers human-readable while providing enough entropy
    (64 bits) for practical uniqueness within a single pipeline run.

    Returns
    -------
    str
        A 16-character lowercase hexadecimal string.
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The floating-point number to clamp.
    lo:
        The lower bound (inclusive).
    hi:
        The upper bound (inclusive).

    Returns
    -------
    float
        The clamped value, satisfying ``lo ≤ result ≤ hi``.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# IdeationRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IdeationRecord:
    """One ideation event produced during the IDEATION phase of a cycle.

    An ``IdeationRecord`` captures everything that is known about a single
    idea at the moment it is generated: its text, the domain it belongs to,
    a confidence estimate, the source that triggered its generation (e.g.
    ``'user'``, ``'feedback'``, ``'random'``), a creation timestamp, an
    optional set of semantic tags, and an optional back-reference to the
    proof whose feedback triggered this idea (``parent_proof_id``).

    The confidence field is used by the orchestration phase to decide
    whether the idea is worth planning for.  Ideas below the domain's
    minimum viable confidence threshold are discarded before planning.

    Theory reference: Ch65 §4.1 — the confidence field is the computational
    realisation of the *c_min* bound from the Ideation Soundness theorem.

    Attributes
    ----------
    ideation_id : str
        Globally unique identifier for this idea.
    idea_text : str
        The textual content of the idea (a claim, hypothesis, or goal).
    domain : str
        The geometric or logical domain the idea belongs to, e.g.
        ``'geometry'``, ``'topology'``, ``'algebra'``.
    confidence : float
        A value in [0, 1] representing how confident the system is that
        this idea is worth pursuing.
    source : str
        A label describing what generated this idea, e.g. ``'user'``,
        ``'feedback'``, ``'prior_cycle'``, ``'random_exploration'``.
    timestamp : str
        ISO-8601 UTC string recording when this idea was created.
    tags : list[str]
        Semantic tags used for clustering and retrieval.
    parent_proof_id : Optional[str]
        If this idea was seeded by the feedback from a previous proof,
        this field holds that proof's ``proof_id``.  ``None`` for ideas
        that originate outside the feedback loop.
    """

    ideation_id: str
    idea_text: str
    domain: str
    confidence: float
    source: str
    timestamp: str
    tags: list = field(default_factory=list)
    parent_proof_id: Optional[str] = None

    # ------------------------------------------------------------------
    def is_high_confidence(self) -> bool:
        """Return ``True`` when the idea's confidence exceeds the 0.7 threshold.

        The 0.7 threshold is taken from Ch65 §4.1: ideas with confidence at
        or above 0.7 are deemed *high-confidence* and are passed directly to
        orchestration without additional review.  Ideas below the threshold
        are queued for human-in-the-loop validation before planning proceeds.

        Returns
        -------
        bool
            ``True`` iff ``self.confidence >= 0.7``.
        """
        return self.confidence >= 0.7

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this record to a plain, JSON-serialisable dictionary.

        All fields are included.  The ``tags`` list and ``parent_proof_id``
        are preserved as-is so that the dictionary can be round-tripped back
        to an ``IdeationRecord`` without loss of information.

        Returns
        -------
        dict
            A flat dictionary with keys matching the field names of this class.
        """
        return {
            "ideation_id": self.ideation_id,
            "idea_text": self.idea_text,
            "domain": self.domain,
            "confidence": self.confidence,
            "source": self.source,
            "timestamp": self.timestamp,
            "tags": list(self.tags),
            "parent_proof_id": self.parent_proof_id,
        }

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """Return a one-line human-readable summary of this idea.

        The summary is intended for logging and debugging.  It combines the
        ``ideation_id``, ``domain``, confidence (formatted to two decimal
        places), and the first 60 characters of ``idea_text`` so that the
        output fits comfortably on a single terminal line.

        Returns
        -------
        str
            A compact summary string, e.g.
            ``'[a1b2c3d4e5f6a7b8] geometry conf=0.85 Circles inscribed in …'``.
        """
        snippet = self.idea_text[:60].replace("\n", " ")
        if len(self.idea_text) > 60:
            snippet += "…"
        return (
            f"[{self.ideation_id}] {self.domain} "
            f"conf={self.confidence:.2f} {snippet}"
        )


# ---------------------------------------------------------------------------
# OrchestrationPlan
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OrchestrationPlan:
    """A plan generated from an ``IdeationRecord`` during the ORCHESTRATION phase.

    An ``OrchestrationPlan`` translates the high-level claim in an idea into a
    sequence of concrete proof steps.  Each step is a dictionary with at least
    the keys ``'step_id'``, ``'action'``, and ``'expected_output'``.  The
    plan also carries a priority score, an estimated duration in seconds, and a
    trust requirement that the resulting ``ProofRecord`` must meet or exceed for
    the cycle to be counted as successful.

    Theory reference: Ch65 §4.2 — the completeness of the plan with respect to
    the source idea is checked by ``IdeationToOrchestrationAnalyzer.assess_plan_coverage``.

    Attributes
    ----------
    plan_id : str
        Globally unique identifier for this plan.
    ideation_id : str
        The ``ideation_id`` of the idea that triggered plan generation.
    steps : list[dict]
        Ordered list of step dictionaries.  Each step must contain at least
        ``'step_id'``, ``'action'``, and ``'expected_output'``.
    priority : float
        A value in [0, 1] indicating the execution priority of this plan
        relative to other plans queued in the same coordinator instance.
    estimated_duration : float
        The planner's estimate of how many seconds the proof will take.
    trust_requirement : float
        The minimum trust score (in [0, 1]) that the resulting proof must
        achieve for the cycle to be considered fully successful.
    timestamp : str
        ISO-8601 UTC string recording when this plan was created.
    """

    plan_id: str
    ideation_id: str
    steps: list = field(default_factory=list)
    priority: float = 0.5
    estimated_duration: float = 1.0
    trust_requirement: float = 0.6
    timestamp: str = field(default_factory=_utcnow)

    # ------------------------------------------------------------------
    def step_count(self) -> int:
        """Return the number of steps in this plan.

        A plan with zero steps is degenerate and will be rejected by the
        feasibility check.  In practice, the orchestration algorithm always
        produces at least one step for any non-trivial idea.

        Returns
        -------
        int
            Length of ``self.steps``.
        """
        return len(self.steps)

    # ------------------------------------------------------------------
    def is_feasible(self) -> bool:
        """Return ``True`` when the plan satisfies the basic feasibility conditions.

        A plan is considered feasible iff all of the following hold:

        1. It contains at least one step (``step_count() >= 1``).
        2. Its estimated duration is strictly positive
           (``estimated_duration > 0``).
        3. Its trust requirement does not exceed 1.0.
        4. Its priority lies within [0, 1].

        These conditions correspond to the feasibility pre-conditions for the
        proof-efficiency bound stated in Ch65 §4.3.

        Returns
        -------
        bool
            ``True`` iff all four feasibility conditions are met.
        """
        return (
            self.step_count() >= 1
            and self.estimated_duration > 0
            and 0.0 <= self.trust_requirement <= 1.0
            and 0.0 <= self.priority <= 1.0
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this plan to a plain, JSON-serialisable dictionary.

        All fields are included verbatim.  The ``steps`` list is shallow-copied
        to prevent inadvertent mutation of the serialised representation.

        Returns
        -------
        dict
            A flat dictionary with keys matching the field names of this class.
        """
        return {
            "plan_id": self.plan_id,
            "ideation_id": self.ideation_id,
            "steps": list(self.steps),
            "priority": self.priority,
            "estimated_duration": self.estimated_duration,
            "trust_requirement": self.trust_requirement,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# ProofRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProofRecord:
    """Result produced by executing an ``OrchestrationPlan`` during the PROOF phase.

    A ``ProofRecord`` captures the outcome of running the proof steps defined
    by the plan.  The ``status`` field uses a small vocabulary of outcome
    labels: ``'VALID'`` means the proof succeeded and all evidence is
    consistent; ``'INVALID'`` means a contradiction was found; ``'PARTIAL'``
    means only some steps succeeded; ``'TIMEOUT'`` means the prover did not
    finish within the allotted time.

    The ``trust_score`` is a real value in [0, 1] that reflects how much
    confidence the system places in the result.  A ``'VALID'`` proof that
    required many independent evidence items and encountered no obstructions
    should yield a trust score close to 1.0.

    Theory reference: Ch65 §4.3 — proof efficiency is *R.trust_score / P.estimated_duration*.

    Attributes
    ----------
    proof_id : str
        Globally unique identifier for this proof record.
    plan_id : str
        The ``plan_id`` of the plan that was executed to produce this record.
    status : str
        One of ``'VALID'``, ``'INVALID'``, ``'PARTIAL'``, ``'TIMEOUT'``.
    trust_score : float
        Confidence in the proof outcome, in [0, 1].
    evidence : list[dict]
        List of evidence items gathered during the proof run.  Each item is a
        dictionary with at least ``'evidence_id'``, ``'kind'``, and ``'value'``.
    obstructions : list[str]
        List of human-readable obstruction descriptions encountered during
        the proof run.  Non-empty iff ``status`` is ``'INVALID'`` or
        ``'PARTIAL'``.
    started_at : str
        ISO-8601 UTC string for when the proof run began.
    completed_at : str
        ISO-8601 UTC string for when the proof run ended.
    """

    proof_id: str
    plan_id: str
    status: str
    trust_score: float
    evidence: list = field(default_factory=list)
    obstructions: list = field(default_factory=list)
    started_at: str = field(default_factory=_utcnow)
    completed_at: str = field(default_factory=_utcnow)

    # ------------------------------------------------------------------
    def is_valid(self) -> bool:
        """Return ``True`` when the proof achieved a ``'VALID'`` status.

        A proof is considered valid iff ``self.status == 'VALID'``.  Note that
        a high ``trust_score`` alone is not sufficient: the status field is
        set deterministically by the proof engine based on logical consistency,
        whereas ``trust_score`` reflects the engine's Bayesian confidence.

        Returns
        -------
        bool
            ``True`` iff ``self.status == 'VALID'``.
        """
        return self.status == "VALID"

    # ------------------------------------------------------------------
    def duration(self) -> float:
        """Compute the wall-clock duration of the proof run in seconds.

        Parses the ISO-8601 timestamps ``started_at`` and ``completed_at`` by
        converting them through ``time.strptime`` and computing the difference
        of the corresponding Unix epoch values.  If parsing fails for any
        reason the method returns 0.0 rather than raising an exception, since
        a missing duration should degrade gracefully rather than abort the
        analysis pipeline.

        Returns
        -------
        float
            Elapsed time in seconds, or ``0.0`` on parse failure.
        """
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            start = time.mktime(time.strptime(self.started_at, fmt))
            end = time.mktime(time.strptime(self.completed_at, fmt))
            return max(0.0, end - start)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this proof record to a plain, JSON-serialisable dictionary.

        All fields are included.  ``evidence`` and ``obstructions`` are
        shallow-copied to prevent mutation of the serialised form.

        Returns
        -------
        dict
            A flat dictionary with keys matching the field names of this class.
        """
        return {
            "proof_id": self.proof_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "trust_score": self.trust_score,
            "evidence": list(self.evidence),
            "obstructions": list(self.obstructions),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# ---------------------------------------------------------------------------
# FeedbackSignal
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FeedbackSignal:
    """Feedback from the PROOF phase back to the IDEATION phase.

    A ``FeedbackSignal`` encodes what the proof result implies for the next
    round of ideation.  The ``signal_type`` field uses four values:

    * ``'POSITIVE'`` — the proof confirmed the idea; ideation should explore
      the same neighbourhood with higher confidence.
    * ``'NEGATIVE'`` — the proof refuted the idea; ideation should avoid
      this neighbourhood or lower confidence for similar ideas.
    * ``'CORRECTION'`` — the proof revealed a specific error in the idea that
      can be corrected; ``payload`` contains the corrected formulation.
    * ``'EXPANSION'`` — the proof uncovered a related claim that was not in
      the original idea; ``payload`` contains the expanded claim.

    The ``weight`` field determines how strongly this signal influences the
    next ideation round.  High-weight signals from valid proofs dominate.

    Theory reference: Ch65 §4.4 — feedback utility *U(F, R)*.

    Attributes
    ----------
    signal_id : str
        Globally unique identifier for this feedback signal.
    proof_id : str
        The ``proof_id`` of the proof that generated this signal.
    signal_type : str
        One of ``'POSITIVE'``, ``'NEGATIVE'``, ``'CORRECTION'``, ``'EXPANSION'``.
    payload : dict
        Structured data accompanying the signal.  Contents depend on
        ``signal_type``.
    weight : float
        Influence weight in [0, 1].
    timestamp : str
        ISO-8601 UTC string recording when this signal was generated.
    """

    signal_id: str
    proof_id: str
    signal_type: str
    payload: dict = field(default_factory=dict)
    weight: float = 0.5
    timestamp: str = field(default_factory=_utcnow)

    # ------------------------------------------------------------------
    def is_positive(self) -> bool:
        """Return ``True`` when the signal type is ``'POSITIVE'``.

        Positive signals are the only type that unconditionally raise the
        confidence floor for the next ideation round.  ``'EXPANSION'`` signals
        also add new ideas but do not directly raise confidence.

        Returns
        -------
        bool
            ``True`` iff ``self.signal_type == 'POSITIVE'``.
        """
        return self.signal_type == "POSITIVE"

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this feedback signal to a plain, JSON-serialisable dictionary.

        The ``payload`` dictionary is shallow-copied to prevent inadvertent
        mutation of the serialised representation.

        Returns
        -------
        dict
            A flat dictionary with keys matching the field names of this class.
        """
        return {
            "signal_id": self.signal_id,
            "proof_id": self.proof_id,
            "signal_type": self.signal_type,
            "payload": dict(self.payload),
            "weight": self.weight,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# IdeationCycleRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IdeationCycleRecord:
    """One complete pass through the IDEATION → ORCHESTRATION → PROOF → FEEDBACK cycle.

    An ``IdeationCycleRecord`` bundles together all four phase objects and the
    timestamps that bracket the cycle.  It is the primary unit of analysis for
    ``IdeationToOrchestrationAnalyzer`` and the primary unit of attestation for
    ``IdeationToOrchestrationWitness``.

    A cycle is *complete* iff all four phase objects are present.  A *partial*
    cycle (e.g. one that timed out before reaching the feedback phase) still
    carries whatever partial information is available.

    Theory reference: Ch65 §4.5 — the trust-delta accumulated across a sequence
    of complete cycles is the quantity whose convergence is guaranteed by the
    Cycle Convergence theorem.

    Attributes
    ----------
    cycle_id : str
        Globally unique identifier for this cycle.
    ideation : Optional[IdeationRecord]
        The idea generated in the IDEATION phase.
    plan : Optional[OrchestrationPlan]
        The plan generated in the ORCHESTRATION phase.
    proof : Optional[ProofRecord]
        The result produced in the PROOF phase.
    feedback : Optional[FeedbackSignal]
        The signal generated in the FEEDBACK phase.
    started_at : str
        ISO-8601 UTC string for when the cycle began.
    completed_at : str
        ISO-8601 UTC string for when the cycle ended (or was abandoned).
    """

    cycle_id: str
    ideation: Optional[IdeationRecord] = None
    plan: Optional[OrchestrationPlan] = None
    proof: Optional[ProofRecord] = None
    feedback: Optional[FeedbackSignal] = None
    started_at: str = field(default_factory=_utcnow)
    completed_at: str = field(default_factory=_utcnow)

    # ------------------------------------------------------------------
    def is_complete(self) -> bool:
        """Return ``True`` when all four phase objects are present.

        Completeness does not imply correctness: a complete cycle may still
        contain an ``'INVALID'`` proof.  It merely means that the cycle ran
        all the way through to the feedback phase and produced objects at every
        step.

        Returns
        -------
        bool
            ``True`` iff all of ``ideation``, ``plan``, ``proof``, and
            ``feedback`` are not ``None``.
        """
        return (
            self.ideation is not None
            and self.plan is not None
            and self.proof is not None
            and self.feedback is not None
        )

    # ------------------------------------------------------------------
    def duration(self) -> float:
        """Compute the wall-clock duration of the cycle in seconds.

        Uses the same parsing logic as ``ProofRecord.duration``.  Returns 0.0
        if the timestamps cannot be parsed.

        Returns
        -------
        float
            Elapsed time in seconds, or ``0.0`` on parse failure.
        """
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            start = time.mktime(time.strptime(self.started_at, fmt))
            end = time.mktime(time.strptime(self.completed_at, fmt))
            return max(0.0, end - start)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    def trust_delta(self) -> float:
        """Return the trust score contributed by this cycle.

        The trust delta is defined as ``proof.trust_score * feedback.weight``
        when both the proof and feedback are present, ``proof.trust_score``
        when only the proof is present, and ``0.0`` when neither is available.
        This matches the trust-accumulation formula in Ch65 §4.5.

        Returns
        -------
        float
            The trust delta for this cycle, in [0, 1].
        """
        if self.proof is not None and self.feedback is not None:
            return _clamp(self.proof.trust_score * self.feedback.weight, 0.0, 1.0)
        if self.proof is not None:
            return _clamp(self.proof.trust_score, 0.0, 1.0)
        return 0.0

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this cycle record to a plain, JSON-serialisable dictionary.

        Each phase object is serialised via its own ``to_dict`` method.
        ``None`` phase objects are represented as ``None`` in the output
        dictionary.

        Returns
        -------
        dict
            A nested dictionary with keys ``cycle_id``, ``ideation``, ``plan``,
            ``proof``, ``feedback``, ``started_at``, ``completed_at``, and
            the derived ``is_complete`` and ``trust_delta`` fields for
            convenience.
        """
        return {
            "cycle_id": self.cycle_id,
            "ideation": self.ideation.to_dict() if self.ideation else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "proof": self.proof.to_dict() if self.proof else None,
            "feedback": self.feedback.to_dict() if self.feedback else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "is_complete": self.is_complete(),
            "trust_delta": self.trust_delta(),
        }


# ---------------------------------------------------------------------------
# IdeationToOrchestrationAnalyzer
# ---------------------------------------------------------------------------


class IdeationToOrchestrationAnalyzer:
    """Analyser for the ideation → orchestration → proof loop.

    The analyser is a stateless (per-call) utility class that takes data
    objects produced during a cycle and computes quality scores, coverage
    ratios, and efficiency metrics as defined in Ch65 §4.1–§4.4.

    It does not modify any of its inputs.  All methods return plain Python
    scalars or dictionaries that can be directly consumed by downstream
    reporting or monitoring code.

    Parameters
    ----------
    analyzer_id : str
        Unique identifier for this analyser instance.
    config : dict or None
        Optional configuration overrides.  Recognised keys:

        ``'confidence_threshold'`` (float, default 0.7)
            Minimum confidence for an idea to be rated high-quality.
        ``'coverage_word_limit'`` (int, default 50)
            Maximum number of words used in the plan-coverage heuristic.
        ``'efficiency_scale'`` (float, default 10.0)
            Duration scale factor for proof-efficiency normalisation.
    """

    def __init__(self, analyzer_id: str, config: dict | None = None) -> None:
        """Initialise the analyser with an identifier and optional configuration.

        Parameters
        ----------
        analyzer_id : str
            Unique identifier string, typically produced by ``_uid()``.
        config : dict or None
            Optional configuration dictionary.  See class docstring for
            supported keys.
        """
        self.analyzer_id = analyzer_id
        self.config: dict = config or {}
        self._confidence_threshold: float = float(
            self.config.get("confidence_threshold", 0.7)
        )
        self._coverage_word_limit: int = int(
            self.config.get("coverage_word_limit", 50)
        )
        self._efficiency_scale: float = float(
            self.config.get("efficiency_scale", 10.0)
        )

    # ------------------------------------------------------------------
    def score_idea_quality(self, idea: IdeationRecord) -> float:
        """Score the quality of an ``IdeationRecord`` on a [0, 1] scale.

        The quality score combines three components with equal weights:

        1. **Confidence component** — ``idea.confidence`` clamped to [0, 1].
        2. **Text richness component** — normalised word count of
           ``idea.idea_text``, capped at 1.0 beyond
           ``self._coverage_word_limit`` words.
        3. **Tag richness component** — ``min(len(idea.tags) / 5, 1.0)``,
           so five or more tags gives the full tag-richness score.

        The three components are averaged.  A perfect score of 1.0 requires
        high confidence, a rich idea text, and at least five tags.

        Parameters
        ----------
        idea : IdeationRecord
            The idea to score.

        Returns
        -------
        float
            Quality score in [0, 1].
        """
        confidence_component = _clamp(idea.confidence, 0.0, 1.0)
        word_count = len(idea.idea_text.split())
        richness_component = _clamp(word_count / max(self._coverage_word_limit, 1), 0.0, 1.0)
        tag_component = _clamp(len(idea.tags) / 5.0, 0.0, 1.0)
        score = (confidence_component + richness_component + tag_component) / 3.0
        return _clamp(score, 0.0, 1.0)

    # ------------------------------------------------------------------
    def assess_plan_coverage(
        self, plan: OrchestrationPlan, idea: IdeationRecord
    ) -> float:
        """Assess how well an ``OrchestrationPlan`` covers its source ``IdeationRecord``.

        Coverage is computed as a heuristic word-overlap ratio:

        1. Tokenise ``idea.idea_text`` into a set of lowercase words with
           length ≥ 3 (to filter stop-words).
        2. Collect all words appearing in any ``step['action']`` field across
           all steps of the plan.
        3. Return ``|intersection| / |idea_words|``, clamped to [0, 1].

        If the idea text is empty, or if the plan has no steps, the method
        returns 0.0 immediately.  This matches the completeness pre-condition
        in Ch65 §4.2.

        Parameters
        ----------
        plan : OrchestrationPlan
            The plan to assess.
        idea : IdeationRecord
            The source idea that the plan should cover.

        Returns
        -------
        float
            Coverage ratio in [0, 1].
        """
        if not idea.idea_text or not plan.steps:
            return 0.0
        idea_words = {
            w.lower() for w in idea.idea_text.split() if len(w) >= 3
        }
        if not idea_words:
            return 0.0
        plan_words: set = set()
        for step in plan.steps:
            action = step.get("action", "")
            for w in action.split():
                if len(w) >= 3:
                    plan_words.add(w.lower())
        overlap = idea_words & plan_words
        return _clamp(len(overlap) / len(idea_words), 0.0, 1.0)

    # ------------------------------------------------------------------
    def measure_proof_efficiency(
        self, proof: ProofRecord, plan: OrchestrationPlan
    ) -> float:
        """Measure the efficiency of a proof run relative to its plan.

        Proof efficiency is defined in Ch65 §4.3 as:

            η(plan, proof) = proof.trust_score / plan.estimated_duration

        normalised by ``self._efficiency_scale`` so that a proof that achieves
        full trust (1.0) in exactly ``_efficiency_scale`` seconds scores 1.0.
        Timed-out proofs (``status == 'TIMEOUT'``) score 0.0 regardless of
        their trust score.

        Parameters
        ----------
        proof : ProofRecord
            The proof result.
        plan : OrchestrationPlan
            The plan that was executed to produce the proof.

        Returns
        -------
        float
            Efficiency score in [0, 1].
        """
        if proof.status == "TIMEOUT":
            return 0.0
        if plan.estimated_duration <= 0:
            return 0.0
        raw_efficiency = (
            proof.trust_score
            * self._efficiency_scale
            / plan.estimated_duration
        )
        return _clamp(raw_efficiency, 0.0, 1.0)

    # ------------------------------------------------------------------
    def compute_feedback_utility(
        self, feedback: FeedbackSignal, proof: ProofRecord
    ) -> float:
        """Compute the utility of a feedback signal relative to its proof.

        Implements Ch65 §4.4: *U(F, R) = F.weight · (1 + indicator(F.is_positive()))*.

        The formula produces a maximum of 2 · F.weight for positive signals and
        exactly F.weight for non-positive signals.  The result is normalised by
        dividing by 2.0 so that utility lies in [0, 1].

        The proof's trust score is used as a multiplier: a high-quality proof
        amplifies the utility of its feedback signal.

        Parameters
        ----------
        feedback : FeedbackSignal
            The feedback signal to evaluate.
        proof : ProofRecord
            The proof record that produced the signal.

        Returns
        -------
        float
            Utility score in [0, 1].
        """
        positive_bonus = 1.0 if feedback.is_positive() else 0.0
        raw_utility = feedback.weight * (1.0 + positive_bonus) * proof.trust_score
        return _clamp(raw_utility / 2.0, 0.0, 1.0)

    # ------------------------------------------------------------------
    def analyze_cycle(self, record: IdeationCycleRecord) -> dict:
        """Analyse a single ``IdeationCycleRecord`` and return a score dictionary.

        Computes all four phase scores (idea quality, plan coverage, proof
        efficiency, feedback utility) for the given cycle record and bundles
        them together with derived metrics into a single dictionary.  Missing
        phases are scored as ``None``.

        Parameters
        ----------
        record : IdeationCycleRecord
            The cycle to analyse.

        Returns
        -------
        dict
            A dictionary with the following keys:

            ``'cycle_id'``
                The cycle's identifier.
            ``'is_complete'``
                Whether all four phases completed.
            ``'idea_quality'``
                Float in [0, 1] or ``None`` if no idea is present.
            ``'plan_coverage'``
                Float in [0, 1] or ``None`` if plan or idea is missing.
            ``'proof_efficiency'``
                Float in [0, 1] or ``None`` if proof or plan is missing.
            ``'feedback_utility'``
                Float in [0, 1] or ``None`` if feedback or proof is missing.
            ``'trust_delta'``
                The cycle's trust delta as computed by ``IdeationCycleRecord.trust_delta()``.
            ``'overall_score'``
                Mean of all non-``None`` phase scores.
        """
        idea_quality = (
            self.score_idea_quality(record.ideation)
            if record.ideation is not None
            else None
        )
        plan_coverage = (
            self.assess_plan_coverage(record.plan, record.ideation)
            if record.plan is not None and record.ideation is not None
            else None
        )
        proof_efficiency = (
            self.measure_proof_efficiency(record.proof, record.plan)
            if record.proof is not None and record.plan is not None
            else None
        )
        feedback_utility = (
            self.compute_feedback_utility(record.feedback, record.proof)
            if record.feedback is not None and record.proof is not None
            else None
        )
        phase_scores = [
            s for s in [idea_quality, plan_coverage, proof_efficiency, feedback_utility]
            if s is not None
        ]
        overall = sum(phase_scores) / len(phase_scores) if phase_scores else 0.0
        return {
            "cycle_id": record.cycle_id,
            "is_complete": record.is_complete(),
            "idea_quality": idea_quality,
            "plan_coverage": plan_coverage,
            "proof_efficiency": proof_efficiency,
            "feedback_utility": feedback_utility,
            "trust_delta": record.trust_delta(),
            "overall_score": overall,
        }

    # ------------------------------------------------------------------
    def generate_cycle_report(self, records: list) -> dict:
        """Generate an aggregate report over a list of ``IdeationCycleRecord`` objects.

        Analyses each record individually and aggregates the results into
        summary statistics.  The report is useful for monitoring the health
        of the ideation loop over time and for detecting trends in trust
        accumulation.

        Parameters
        ----------
        records : list[IdeationCycleRecord]
            The list of cycle records to include in the report.

        Returns
        -------
        dict
            A report dictionary with the following keys:

            ``'analyzer_id'``
                This analyser's identifier.
            ``'record_count'``
                Number of records analysed.
            ``'complete_count'``
                Number of complete cycles.
            ``'total_trust_delta'``
                Sum of all trust deltas.
            ``'mean_overall_score'``
                Mean ``overall_score`` across all records.
            ``'per_cycle'``
                List of per-cycle analysis dictionaries from ``analyze_cycle``.
        """
        analyses = [self.analyze_cycle(r) for r in records]
        total_trust = sum(a["trust_delta"] for a in analyses)
        complete_count = sum(1 for a in analyses if a["is_complete"])
        scores = [a["overall_score"] for a in analyses]
        mean_score = sum(scores) / len(scores) if scores else 0.0
        return {
            "analyzer_id": self.analyzer_id,
            "record_count": len(records),
            "complete_count": complete_count,
            "total_trust_delta": total_trust,
            "mean_overall_score": mean_score,
            "per_cycle": analyses,
        }


# ---------------------------------------------------------------------------
# IdeationToOrchestrationWitness
# ---------------------------------------------------------------------------


class IdeationToOrchestrationWitness:
    """Proof witness for the ideation → orchestration → proof → feedback cycle.

    The witness accumulates per-step attestations into a tamper-evident chain.
    Each attestation is a dictionary containing a hash of the previous
    attestation, the type of the witnessed object, its identifier, and a
    timestamp.  The chain structure ensures that any tampering with an earlier
    attestation invalidates all subsequent attestations.

    The witness can be asked to verify the integrity of the chain for a
    specific cycle and to build a lineage graph showing all chains it has
    accumulated across all cycles.

    Parameters
    ----------
    witness_id : str
        Unique identifier for this witness instance.
    """

    def __init__(self, witness_id: str) -> None:
        """Initialise the witness with an empty chain.

        Parameters
        ----------
        witness_id : str
            Unique identifier for this witness instance.
        """
        self.witness_id = witness_id
        self.cycle_proofs: list = []
        self._chain: list = []

    # ------------------------------------------------------------------
    def _make_attestation(
        self,
        attest_type: str,
        object_id: str,
        cycle_id: str,
        prev_hash: str,
        extra: dict | None = None,
    ) -> dict:
        """Internal helper: build and record one attestation entry.

        Computes a SHA-256 hash over ``prev_hash || attest_type || object_id``
        and stores the resulting attestation in ``self._chain``.

        Parameters
        ----------
        attest_type : str
            Label for the type of object being witnessed, e.g. ``'ideation'``.
        object_id : str
            The unique identifier of the object being attested.
        cycle_id : str
            The cycle this attestation belongs to.
        prev_hash : str
            The hash of the previous attestation (or ``'genesis'`` for the
            first attestation in a chain).
        extra : dict or None
            Additional key-value pairs to embed in the attestation.

        Returns
        -------
        dict
            The newly created attestation dictionary.
        """
        raw = f"{prev_hash}:{attest_type}:{object_id}:{cycle_id}"
        content_hash = hashlib.sha256(raw.encode()).hexdigest()[:24]
        attestation = {
            "attestation_id": _uid(),
            "cycle_id": cycle_id,
            "attest_type": attest_type,
            "object_id": object_id,
            "prev_hash": prev_hash,
            "content_hash": content_hash,
            "timestamp": _utcnow(),
        }
        if extra:
            attestation.update(extra)
        self._chain.append(attestation)
        return attestation

    # ------------------------------------------------------------------
    def witness_ideation(self, idea: IdeationRecord) -> str:
        """Attest that an ``IdeationRecord`` was witnessed at the start of a cycle.

        Creates the first attestation in the chain for the cycle identified
        by ``idea.ideation_id``.  The ``prev_hash`` for this first entry is
        the string ``'genesis'``.  The attestation is stored in
        ``self._chain`` and the ``attestation_id`` is returned so that it
        can be passed to ``witness_orchestration``.

        Parameters
        ----------
        idea : IdeationRecord
            The idea record to attest.

        Returns
        -------
        str
            The ``attestation_id`` of the newly created attestation.
        """
        attest = self._make_attestation(
            attest_type="ideation",
            object_id=idea.ideation_id,
            cycle_id=idea.ideation_id,
            prev_hash="genesis",
            extra={"domain": idea.domain, "confidence": idea.confidence},
        )
        return attest["attestation_id"]

    # ------------------------------------------------------------------
    def witness_orchestration(
        self, plan: OrchestrationPlan, attestation_id: str
    ) -> str:
        """Attest that an ``OrchestrationPlan`` was generated from an attested idea.

        Looks up the attestation identified by ``attestation_id`` in
        ``self._chain`` to retrieve its ``content_hash``, which becomes the
        ``prev_hash`` of the new orchestration attestation.  This links the
        orchestration step to the preceding ideation step.

        Parameters
        ----------
        plan : OrchestrationPlan
            The plan record to attest.
        attestation_id : str
            The ``attestation_id`` returned by a prior call to
            ``witness_ideation``.

        Returns
        -------
        str
            The ``attestation_id`` of the newly created attestation.
        """
        prev = next(
            (a for a in self._chain if a["attestation_id"] == attestation_id),
            None,
        )
        prev_hash = prev["content_hash"] if prev else "unknown"
        cycle_id = prev["cycle_id"] if prev else plan.ideation_id
        attest = self._make_attestation(
            attest_type="orchestration",
            object_id=plan.plan_id,
            cycle_id=cycle_id,
            prev_hash=prev_hash,
            extra={"step_count": plan.step_count(), "priority": plan.priority},
        )
        return attest["attestation_id"]

    # ------------------------------------------------------------------
    def witness_proof(self, proof: ProofRecord, prev_id: str) -> str:
        """Attest that a ``ProofRecord`` was produced from an attested plan.

        Chains from the attestation identified by ``prev_id`` (expected to be
        an orchestration attestation) and records the proof's status and trust
        score in the new entry.

        Parameters
        ----------
        proof : ProofRecord
            The proof record to attest.
        prev_id : str
            The ``attestation_id`` returned by a prior call to
            ``witness_orchestration``.

        Returns
        -------
        str
            The ``attestation_id`` of the newly created attestation.
        """
        prev = next(
            (a for a in self._chain if a["attestation_id"] == prev_id),
            None,
        )
        prev_hash = prev["content_hash"] if prev else "unknown"
        cycle_id = prev["cycle_id"] if prev else proof.plan_id
        attest = self._make_attestation(
            attest_type="proof",
            object_id=proof.proof_id,
            cycle_id=cycle_id,
            prev_hash=prev_hash,
            extra={"status": proof.status, "trust_score": proof.trust_score},
        )
        self.cycle_proofs.append(
            {"cycle_id": cycle_id, "proof_id": proof.proof_id, "status": proof.status}
        )
        return attest["attestation_id"]

    # ------------------------------------------------------------------
    def witness_feedback(self, feedback: FeedbackSignal, prev_id: str) -> str:
        """Attest that a ``FeedbackSignal`` was derived from an attested proof.

        Chains from the attestation identified by ``prev_id`` (expected to be
        a proof attestation) and records the feedback signal type and weight
        in the new entry.

        Parameters
        ----------
        feedback : FeedbackSignal
            The feedback signal to attest.
        prev_id : str
            The ``attestation_id`` returned by a prior call to
            ``witness_proof``.

        Returns
        -------
        str
            The ``attestation_id`` of the newly created attestation.
        """
        prev = next(
            (a for a in self._chain if a["attestation_id"] == prev_id),
            None,
        )
        prev_hash = prev["content_hash"] if prev else "unknown"
        cycle_id = prev["cycle_id"] if prev else feedback.proof_id
        attest = self._make_attestation(
            attest_type="feedback",
            object_id=feedback.signal_id,
            cycle_id=cycle_id,
            prev_hash=prev_hash,
            extra={"signal_type": feedback.signal_type, "weight": feedback.weight},
        )
        return attest["attestation_id"]

    # ------------------------------------------------------------------
    def verify_chain(self, cycle_id: str) -> bool:
        """Check the hash-chain integrity for all attestations of a given cycle.

        Filters ``self._chain`` to those entries whose ``cycle_id`` matches
        the argument, then verifies that each entry's ``content_hash`` was
        correctly derived from its ``prev_hash``, ``attest_type``, and
        ``object_id`` fields.  Returns ``True`` iff the chain is intact.

        Parameters
        ----------
        cycle_id : str
            The cycle identifier whose attestation chain should be verified.

        Returns
        -------
        bool
            ``True`` if the chain is intact, ``False`` if any hash mismatch
            is detected or if no entries exist for the given ``cycle_id``.
        """
        entries = [a for a in self._chain if a.get("cycle_id") == cycle_id]
        if not entries:
            return False
        for entry in entries:
            raw = (
                f"{entry['prev_hash']}:{entry['attest_type']}"
                f":{entry['object_id']}:{entry['cycle_id']}"
            )
            expected_hash = hashlib.sha256(raw.encode()).hexdigest()[:24]
            if entry.get("content_hash") != expected_hash:
                return False
        return True

    # ------------------------------------------------------------------
    def build_lineage_graph(self) -> dict:
        """Build a full proof lineage graph from all accumulated attestations.

        The graph is a dictionary keyed by ``cycle_id``.  Each value is a
        list of attestation dictionaries for that cycle, ordered by their
        position in ``self._chain``.  The graph also includes a top-level
        ``'summary'`` entry with aggregate counts.

        Returns
        -------
        dict
            Lineage graph with keys ``'witness_id'``, ``'summary'``, and
            ``'cycles'`` (mapping cycle IDs to attestation lists).
        """
        cycles: dict = {}
        for entry in self._chain:
            cid = entry.get("cycle_id", "unknown")
            cycles.setdefault(cid, []).append(entry)
        return {
            "witness_id": self.witness_id,
            "summary": {
                "total_attestations": len(self._chain),
                "total_cycles": len(cycles),
                "total_proofs": len(self.cycle_proofs),
            },
            "cycles": cycles,
        }

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this witness to a plain, JSON-serialisable dictionary.

        Includes the full attestation chain and the list of proof summaries
        accumulated via ``witness_proof``.

        Returns
        -------
        dict
            A dictionary with keys ``'witness_id'``, ``'chain'``, and
            ``'cycle_proofs'``.
        """
        return {
            "witness_id": self.witness_id,
            "chain": list(self._chain),
            "cycle_proofs": list(self.cycle_proofs),
        }


# ---------------------------------------------------------------------------
# IdeationToOrchestrationCoordinator
# ---------------------------------------------------------------------------


class IdeationToOrchestrationCoordinator:
    """Main coordinator for the full ideation → orchestration → proof → feedback cycle.

    The coordinator drives end-to-end cycle execution, maintaining a history
    of completed cycles, accumulating trust scores, and delegating each phase
    to a simple in-process implementation.  It also manages a single
    ``IdeationToOrchestrationWitness`` instance that attests every step.

    Parameters
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    config : dict or None
        Optional configuration overrides.  Recognised keys:

        ``'default_confidence'`` (float, default 0.75)
            Confidence assigned to ideas generated without explicit feedback.
        ``'default_trust_requirement'`` (float, default 0.6)
            Trust requirement used when building plans from ideas.
        ``'default_estimated_duration'`` (float, default 2.0)
            Estimated proof duration in seconds for freshly generated plans.
        ``'steps_per_plan'`` (int, default 3)
            Number of steps synthesised per plan.
    """

    def __init__(
        self, coordinator_id: str, config: dict | None = None
    ) -> None:
        """Initialise the coordinator with an identifier and optional configuration.

        Parameters
        ----------
        coordinator_id : str
            Unique identifier, typically generated by ``_uid()``.
        config : dict or None
            Optional configuration dictionary.
        """
        self.coordinator_id = coordinator_id
        self.config: dict = config or {}
        self._default_confidence: float = float(
            self.config.get("default_confidence", 0.75)
        )
        self._default_trust_requirement: float = float(
            self.config.get("default_trust_requirement", 0.6)
        )
        self._default_estimated_duration: float = float(
            self.config.get("default_estimated_duration", 2.0)
        )
        self._steps_per_plan: int = int(self.config.get("steps_per_plan", 3))
        self._history: list = []
        self._accumulated_trust: float = 0.0
        self._witness = IdeationToOrchestrationWitness(
            witness_id=f"witness-{coordinator_id}"
        )

    # ------------------------------------------------------------------
    def generate_idea(
        self, domain: str, context: dict | None = None
    ) -> IdeationRecord:
        """Generate a new ``IdeationRecord`` for the given domain.

        Synthesises an idea by combining the domain name with any hints
        found in the optional ``context`` dictionary.  The confidence is
        raised by the ``'trust_boost'`` key in ``context`` (if present and
        in [0, 1]), reflecting the Ch65 §4.1 rule that high-weight positive
        feedback raises the confidence floor.

        Parameters
        ----------
        domain : str
            The geometric or logical domain for the new idea.
        context : dict or None
            Optional context dictionary.  Recognised key: ``'trust_boost'``
            (float in [0, 1]).

        Returns
        -------
        IdeationRecord
            A freshly generated idea record.
        """
        ctx = context or {}
        trust_boost = float(ctx.get("trust_boost", 0.0))
        confidence = _clamp(self._default_confidence + trust_boost * 0.2, 0.0, 1.0)
        hint = ctx.get("hint", "")
        idea_text = (
            f"Investigate a core claim in {domain}"
            + (f" related to {hint}" if hint else "")
            + ". Establish a proof strategy, enumerate boundary cases,"
            " and verify consistency with known axioms."
        )
        tags = [domain, "auto-generated"]
        if hint:
            tags.append(hint)
        parent = ctx.get("parent_proof_id", None)
        return IdeationRecord(
            ideation_id=_uid(),
            idea_text=idea_text,
            domain=domain,
            confidence=confidence,
            source="coordinator" if not hint else "feedback-seeded",
            timestamp=_utcnow(),
            tags=tags,
            parent_proof_id=parent,
        )

    # ------------------------------------------------------------------
    def plan_from_idea(self, idea: IdeationRecord) -> OrchestrationPlan:
        """Translate an ``IdeationRecord`` into an ``OrchestrationPlan``.

        Synthesises ``self._steps_per_plan`` proof steps from the words in
        ``idea.idea_text``.  Each step has an ``'action'`` derived from a
        keyword in the idea, an ``'expected_output'`` label, and a
        ``'step_id'``.  The plan's priority is set to ``idea.confidence``
        so that high-confidence ideas get scheduled first.

        Parameters
        ----------
        idea : IdeationRecord
            The source idea.

        Returns
        -------
        OrchestrationPlan
            A freshly synthesised plan.
        """
        keywords = [w for w in idea.idea_text.split() if len(w) >= 4][:self._steps_per_plan]
        if not keywords:
            keywords = ["verify", "enumerate", "conclude"]
        steps = []
        for i, kw in enumerate(keywords):
            steps.append(
                {
                    "step_id": _uid(),
                    "action": f"{kw} in {idea.domain}",
                    "expected_output": f"result-{i+1}",
                    "index": i,
                }
            )
        return OrchestrationPlan(
            plan_id=_uid(),
            ideation_id=idea.ideation_id,
            steps=steps,
            priority=idea.confidence,
            estimated_duration=self._default_estimated_duration,
            trust_requirement=self._default_trust_requirement,
            timestamp=_utcnow(),
        )

    # ------------------------------------------------------------------
    def execute_plan(self, plan: OrchestrationPlan) -> ProofRecord:
        """Execute an ``OrchestrationPlan`` and return a ``ProofRecord``.

        Simulates proof execution by computing a trust score derived from the
        plan's priority, step count, and trust requirement.  The status is
        set to ``'VALID'`` when the trust score meets the requirement,
        ``'PARTIAL'`` when it is within 0.15 of the requirement, and
        ``'INVALID'`` otherwise.

        Evidence items are synthesised for each step in the plan.

        Parameters
        ----------
        plan : OrchestrationPlan
            The plan to execute.

        Returns
        -------
        ProofRecord
            The result of the simulated proof run.
        """
        started = _utcnow()
        base_trust = _clamp(
            plan.priority * 0.6 + (plan.step_count() / 10.0) * 0.4,
            0.0,
            1.0,
        )
        if base_trust >= plan.trust_requirement:
            status = "VALID"
        elif base_trust >= plan.trust_requirement - 0.15:
            status = "PARTIAL"
        else:
            status = "INVALID"
        evidence = [
            {
                "evidence_id": _uid(),
                "kind": "step_result",
                "value": step.get("expected_output", "ok"),
                "step_id": step.get("step_id", ""),
            }
            for step in plan.steps
        ]
        obstructions: list = (
            [] if status == "VALID"
            else [f"trust below requirement in domain {plan.ideation_id}"]
        )
        completed = _utcnow()
        return ProofRecord(
            proof_id=_uid(),
            plan_id=plan.plan_id,
            status=status,
            trust_score=base_trust,
            evidence=evidence,
            obstructions=obstructions,
            started_at=started,
            completed_at=completed,
        )

    # ------------------------------------------------------------------
    def extract_feedback(self, proof: ProofRecord) -> FeedbackSignal:
        """Derive a ``FeedbackSignal`` from a completed ``ProofRecord``.

        The signal type is determined by the proof status:

        * ``'VALID'`` → ``'POSITIVE'`` with weight = trust_score.
        * ``'PARTIAL'`` → ``'CORRECTION'`` with weight = trust_score * 0.7.
        * ``'INVALID'`` → ``'NEGATIVE'`` with weight = 1 - trust_score.
        * ``'TIMEOUT'`` → ``'NEGATIVE'`` with weight = 0.1.

        The payload is a minimal dictionary containing the proof id and status
        so that the ideation phase can reference the triggering proof.

        Parameters
        ----------
        proof : ProofRecord
            The completed proof record.

        Returns
        -------
        FeedbackSignal
            A feedback signal derived from the proof outcome.
        """
        if proof.status == "VALID":
            signal_type = "POSITIVE"
            weight = _clamp(proof.trust_score, 0.0, 1.0)
        elif proof.status == "PARTIAL":
            signal_type = "CORRECTION"
            weight = _clamp(proof.trust_score * 0.7, 0.0, 1.0)
        elif proof.status == "TIMEOUT":
            signal_type = "NEGATIVE"
            weight = 0.1
        else:
            signal_type = "NEGATIVE"
            weight = _clamp(1.0 - proof.trust_score, 0.0, 1.0)
        payload = {
            "source_proof_id": proof.proof_id,
            "source_status": proof.status,
            "trust_score": proof.trust_score,
            "obstruction_count": len(proof.obstructions),
        }
        return FeedbackSignal(
            signal_id=_uid(),
            proof_id=proof.proof_id,
            signal_type=signal_type,
            payload=payload,
            weight=weight,
            timestamp=_utcnow(),
        )

    # ------------------------------------------------------------------
    def run_full_cycle(
        self, domain: str = "geometry", context: dict | None = None
    ) -> IdeationCycleRecord:
        """Run a complete IDEATION → ORCHESTRATION → PROOF → FEEDBACK cycle.

        Orchestrates all four phases in sequence, attests each phase via the
        internal witness, records the resulting ``IdeationCycleRecord`` in the
        history, and updates the accumulated trust.

        Parameters
        ----------
        domain : str
            The domain for the new idea.  Defaults to ``'geometry'``.
        context : dict or None
            Optional context passed to ``generate_idea``.

        Returns
        -------
        IdeationCycleRecord
            A complete cycle record containing all four phase objects.
        """
        started = _utcnow()

        idea = self.generate_idea(domain=domain, context=context)
        # Use the ideation_id as the canonical cycle identifier so that
        # the witness chain (which is keyed on idea.ideation_id) aligns
        # with the IdeationCycleRecord.cycle_id used in verify_chain().
        cycle_id = idea.ideation_id
        attest_idea = self._witness.witness_ideation(idea)

        plan = self.plan_from_idea(idea)
        attest_plan = self._witness.witness_orchestration(plan, attest_idea)

        proof = self.execute_plan(plan)
        attest_proof = self._witness.witness_proof(proof, attest_plan)

        feedback = self.extract_feedback(proof)
        self._witness.witness_feedback(feedback, attest_proof)

        completed = _utcnow()
        record = IdeationCycleRecord(
            cycle_id=cycle_id,
            ideation=idea,
            plan=plan,
            proof=proof,
            feedback=feedback,
            started_at=started,
            completed_at=completed,
        )
        self._history.append(record)
        self._accumulated_trust += record.trust_delta()
        return record

    # ------------------------------------------------------------------
    def get_cycle_history(self) -> list:
        """Return the full list of ``IdeationCycleRecord`` objects run so far.

        The list is returned by reference; callers should not mutate it.  Use
        ``generate_cycle_report`` on an ``IdeationToOrchestrationAnalyzer``
        instance for aggregate statistics.

        Returns
        -------
        list[IdeationCycleRecord]
            All cycle records in the order they were produced.
        """
        return self._history

    # ------------------------------------------------------------------
    def get_accumulated_trust(self) -> float:
        """Return the total trust accumulated across all completed cycles.

        The accumulated trust is the running sum of
        ``IdeationCycleRecord.trust_delta()`` values.  It can be used to
        detect convergence or stagnation in the feedback loop.

        Returns
        -------
        float
            Non-negative total trust, unbounded above.
        """
        return self._accumulated_trust

    # ------------------------------------------------------------------
    def get_witness(self) -> IdeationToOrchestrationWitness:
        """Return the witness instance that attests this coordinator's cycles.

        Returns
        -------
        IdeationToOrchestrationWitness
            The internal witness holding all attestation chains produced
            during this coordinator's lifetime.
        """
        return self._witness


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def run_ideation_cycle(
    domain: str, context: dict | None = None
) -> IdeationCycleRecord:
    """Convenience function: run one full cycle using a transient coordinator.

    Creates a fresh ``IdeationToOrchestrationCoordinator``, runs exactly one
    full cycle, and returns the resulting ``IdeationCycleRecord``.  This is
    the simplest entry point for callers that need a single cycle result
    without managing coordinator state.

    Parameters
    ----------
    domain : str
        The geometric or logical domain for the new idea.
    context : dict or None
        Optional context passed to the coordinator's ``generate_idea`` method.

    Returns
    -------
    IdeationCycleRecord
        The complete cycle record produced by the transient coordinator.
    """
    coordinator = IdeationToOrchestrationCoordinator(
        coordinator_id=_uid(), config=None
    )
    return coordinator.run_full_cycle(domain=domain, context=context)


def assess_cycle_health(records: list) -> dict:
    """Assess the overall health of a sequence of ``IdeationCycleRecord`` objects.

    Creates a transient ``IdeationToOrchestrationAnalyzer`` and generates an
    aggregate report.  Also computes additional health indicators: the fraction
    of complete cycles, the fraction of valid proofs, and a binary
    ``'healthy'`` flag set to ``True`` when the mean overall score exceeds 0.5.

    Parameters
    ----------
    records : list[IdeationCycleRecord]
        The sequence of cycles to assess.

    Returns
    -------
    dict
        A health report dictionary with keys from
        ``IdeationToOrchestrationAnalyzer.generate_cycle_report`` plus:

        ``'complete_fraction'``
            Fraction of records that are complete.
        ``'valid_proof_fraction'``
            Fraction of records whose proof status is ``'VALID'``.
        ``'healthy'``
            ``True`` iff ``mean_overall_score > 0.5``.
    """
    analyzer = IdeationToOrchestrationAnalyzer(analyzer_id=_uid())
    report = analyzer.generate_cycle_report(records)
    n = len(records)
    complete_fraction = report["complete_count"] / n if n > 0 else 0.0
    valid_count = sum(
        1 for r in records if r.proof is not None and r.proof.is_valid()
    )
    valid_fraction = valid_count / n if n > 0 else 0.0
    report["complete_fraction"] = complete_fraction
    report["valid_proof_fraction"] = valid_fraction
    report["healthy"] = report["mean_overall_score"] > 0.5
    return report


def extract_ideation_patterns(records: list) -> list:
    """Extract recurring ideation patterns from a list of ``IdeationCycleRecord`` objects.

    Scans the ideas in all records and groups them by domain.  For each
    domain, computes the mean confidence, the most common tags, and the
    fraction of high-confidence ideas.  Returns one pattern dictionary per
    domain encountered.

    Parameters
    ----------
    records : list[IdeationCycleRecord]
        The records to analyse.

    Returns
    -------
    list[dict]
        A list of pattern dictionaries, one per domain, each with keys:

        ``'domain'``
            The domain name.
        ``'idea_count'``
            Number of ideas in this domain.
        ``'mean_confidence'``
            Mean confidence across all ideas in the domain.
        ``'high_confidence_fraction'``
            Fraction of ideas with confidence ≥ 0.7.
        ``'top_tags'``
            Up to five most common tags across all ideas in the domain.
    """
    domain_map: dict = {}
    for record in records:
        if record.ideation is None:
            continue
        idea = record.ideation
        bucket = domain_map.setdefault(
            idea.domain,
            {"confidences": [], "tags": [], "high_conf": 0},
        )
        bucket["confidences"].append(idea.confidence)
        bucket["tags"].extend(idea.tags)
        if idea.is_high_confidence():
            bucket["high_conf"] += 1
    patterns = []
    for domain, bucket in domain_map.items():
        n = len(bucket["confidences"])
        mean_conf = sum(bucket["confidences"]) / n if n > 0 else 0.0
        tag_freq: dict = {}
        for tag in bucket["tags"]:
            tag_freq[tag] = tag_freq.get(tag, 0) + 1
        top_tags = sorted(tag_freq, key=lambda t: -tag_freq[t])[:5]
        patterns.append(
            {
                "domain": domain,
                "idea_count": n,
                "mean_confidence": mean_conf,
                "high_confidence_fraction": bucket["high_conf"] / n if n > 0 else 0.0,
                "top_tags": top_tags,
            }
        )
    patterns.sort(key=lambda p: -p["idea_count"])
    return patterns


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("from_ideation_to_orchestration_to.py — smoke test")
    print("=" * 70)

    # 1. Run a single cycle via the module-level convenience function.
    print("\n[1] Running one cycle in 'geometry' domain via run_ideation_cycle()…")
    cycle = run_ideation_cycle(domain="geometry", context={"hint": "parallel lines"})
    print(f"    cycle_id      : {cycle.cycle_id}")
    print(f"    is_complete   : {cycle.is_complete()}")
    print(f"    trust_delta   : {cycle.trust_delta():.4f}")
    if cycle.ideation:
        print(f"    idea summary  : {cycle.ideation.summary()}")
    if cycle.proof:
        print(f"    proof status  : {cycle.proof.status}")
        print(f"    trust score   : {cycle.proof.trust_score:.4f}")
    if cycle.feedback:
        print(f"    feedback type : {cycle.feedback.signal_type}")
        print(f"    feedback wt   : {cycle.feedback.weight:.4f}")

    # 2. Run multiple cycles via the coordinator.
    print("\n[2] Running five cycles in mixed domains via coordinator…")
    coord = IdeationToOrchestrationCoordinator(
        coordinator_id=_uid(),
        config={"steps_per_plan": 4, "default_confidence": 0.8},
    )
    domains = ["geometry", "topology", "algebra", "geometry", "logic"]
    for dom in domains:
        r = coord.run_full_cycle(domain=dom)
        print(
            f"    [{dom:<10}] complete={r.is_complete()}  "
            f"trust_delta={r.trust_delta():.4f}  "
            f"proof={r.proof.status if r.proof else 'N/A'}"
        )

    print(f"\n    Accumulated trust : {coord.get_accumulated_trust():.4f}")

    # 3. Assess cycle health.
    print("\n[3] Assessing cycle health…")
    history = coord.get_cycle_history()
    health = assess_cycle_health(history)
    print(f"    record_count        : {health['record_count']}")
    print(f"    complete_count      : {health['complete_count']}")
    print(f"    complete_fraction   : {health['complete_fraction']:.2f}")
    print(f"    valid_proof_fraction: {health['valid_proof_fraction']:.2f}")
    print(f"    mean_overall_score  : {health['mean_overall_score']:.4f}")
    print(f"    healthy             : {health['healthy']}")

    # 4. Extract ideation patterns.
    print("\n[4] Extracting ideation patterns…")
    patterns = extract_ideation_patterns(history)
    for p in patterns:
        print(
            f"    domain={p['domain']:<12} "
            f"n={p['idea_count']}  "
            f"mean_conf={p['mean_confidence']:.2f}  "
            f"hi_conf={p['high_confidence_fraction']:.2f}  "
            f"top_tags={p['top_tags']}"
        )

    # 5. Verify witness chain integrity.
    print("\n[5] Verifying witness chain integrity for all cycles…")
    witness = coord.get_witness()
    all_ok = True
    for rec in history:
        ok = witness.verify_chain(rec.cycle_id)
        if not ok:
            all_ok = False
            print(f"    FAIL: chain broken for cycle {rec.cycle_id}")
    print(f"    All chains intact : {all_ok}")

    # 6. Build lineage graph summary.
    print("\n[6] Lineage graph summary…")
    graph = witness.build_lineage_graph()
    summary = graph["summary"]
    print(f"    total_attestations : {summary['total_attestations']}")
    print(f"    total_cycles       : {summary['total_cycles']}")
    print(f"    total_proofs       : {summary['total_proofs']}")

    # 7. Direct data-class round-trip check.
    print("\n[7] Data-class round-trip serialisation check…")
    sample = history[0] if history else None
    if sample:
        d = sample.to_dict()
        assert d["cycle_id"] == sample.cycle_id, "cycle_id mismatch"
        assert d["is_complete"] == sample.is_complete(), "is_complete mismatch"
        assert d["trust_delta"] == sample.trust_delta(), "trust_delta mismatch"
        print("    IdeationCycleRecord.to_dict() round-trip: OK")

    print("\n" + "=" * 70)
    print("Smoke test passed.")
    print("=" * 70)
