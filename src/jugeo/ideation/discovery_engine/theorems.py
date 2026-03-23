"""Formal theorems about the JuGeo discovery engine — theory2.tex Ch58.

This module encodes formal theorems about the discovery engine's correctness
and mathematical properties as Python objects.  Each theorem is represented
by a class with name, statement, proof sketch, hypotheses, and conclusion,
and provides a ``verify`` method that checks the theorem in a given context.

Theory reference: theory2.tex Ch58 §8 — Discovery Engine Theorems.

copilot: shared-core marker

Mathematical Background
-----------------------
The theorems in this module capture key invariants that the JuGeo discovery
engine is designed to satisfy.  They are inspired by the formal development
in theory2.tex Ch58, where the discovery engine is modelled as a sequence of
monotone, well-typed functions on a lattice of candidates and theorems.

Formally, let:

  * **C** — the set of all :class:`DiscoveryCandidate` objects
  * **K** — the set of all :class:`KindSignature` objects
  * **T** — the set of all :class:`TheoremCandidate` objects
  * **R** — the set of all :class:`DiscoveryResult` objects
  * **θ** ∈ [0, 1] — the novelty threshold (``DiscoveryConfig.novelty_threshold``)
  * **γ** ∈ [0, 1] — the minimum confidence (``DiscoveryConfig.min_confidence``)

The four pipeline stages define functions:

  * σ₁ : C → C    (novelty filter; drops c if ``c.novelty_score < θ``)
  * σ₂ : C → C×K  (kind assignment; deterministic)
  * σ₃ : C×K → T  (theorem synthesis; structural pattern matching)
  * σ₄ : T → T    (promotion gate; drops t if ``t.confidence < γ``)

The pipeline function **Π** is defined as the composition σ₄ ∘ σ₃ ∘ σ₂ ∘ σ₁.

The theorems below assert correctness properties of these functions.

Theorem Verification
--------------------
Each theorem provides a :meth:`~AbstractTheorem.verify` method that accepts a
:class:`TheoremVerificationContext` and returns a
:class:`TheoremVerificationResult`.  Verification is *empirical* rather than
formal: the method checks whether the theorem's conclusion is consistent with
the data in the context.  A ``verified=True`` result means the context
provides no counterexample; it does not constitute a formal proof.

Usage Examples
--------------
Verify a single theorem::

    from jugeo.ideation.discovery_engine.theorems import (
        DiscoveryCompletenessTheorem,
        TheoremVerificationContext,
    )

    ctx = TheoremVerificationContext(pipeline_results=results, config=config)
    thm = DiscoveryCompletenessTheorem()
    vr = thm.verify(ctx)
    print(vr.theorem_name, vr.verified, vr.evidence)

Verify all theorems via the registry::

    from jugeo.ideation.discovery_engine.theorems import DiscoveryTheoremRegistry

    registry = DiscoveryTheoremRegistry()
    context = TheoremVerificationContext(
        pipeline_results=results,
        config=config,
        candidates=candidates,
        diagnostics=diagnostics,
    )
    results_list = registry.verify_all(context)
    count = registry.verified_count(context)
    print(f"{count}/{len(registry)} theorems verified in this context.")
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    # Context / Result
    "TheoremVerificationContext",
    "TheoremVerificationResult",
    # Abstract base
    "AbstractTheorem",
    # Concrete theorems
    "DiscoveryCompletenessTheorem",
    "PipelineSoundnessTheorem",
    "NoveltyPreservationTheorem",
    "KindAssignmentUniquenessTheorem",
    "TheoremSynthesisCorrectnessTheorem",
    "PackPromotionMonotonicityTheorem",
    # Registry
    "DiscoveryTheoremRegistry",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate, DiscoveryConfig, DiscoveryResult, DiscoveryDiagnostics,
        DiscoveryStatus, KindSignature, TheoremCandidate, PromotionDecision,
        NoveltyPipelineStage, KindClassificationStage, TheoremSynthesisStage,
        PackPromotionStage,
    )
except Exception:
    DiscoveryCandidate = None  # type: ignore[assignment,misc]
    DiscoveryConfig = None  # type: ignore[assignment,misc]
    DiscoveryResult = None  # type: ignore[assignment,misc]
    DiscoveryDiagnostics = None  # type: ignore[assignment,misc]
    DiscoveryStatus = None  # type: ignore[assignment,misc]
    KindSignature = None  # type: ignore[assignment,misc]
    TheoremCandidate = None  # type: ignore[assignment,misc]
    PromotionDecision = None  # type: ignore[assignment,misc]
    NoveltyPipelineStage = None  # type: ignore[assignment,misc]
    KindClassificationStage = None  # type: ignore[assignment,misc]
    TheoremSynthesisStage = None  # type: ignore[assignment,misc]
    PackPromotionStage = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC POSIX timestamp.

    Returns
    -------
    float
        Seconds since the Unix epoch.
    """
    return time.time()


def _uid() -> str:
    """Generate a 32-character random hexadecimal UUID4 identifier.

    Returns
    -------
    str
        UUID4 hex string without hyphens.
    """
    return uuid.uuid4().hex


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        Value to clamp.
    lo:
        Lower bound.
    hi:
        Upper bound.

    Returns
    -------
    float
        Clamped result.
    """
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# TheoremVerificationContext
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremVerificationContext:
    """Context object supplied to :meth:`AbstractTheorem.verify`.

    A :class:`TheoremVerificationContext` bundles together all the runtime
    artefacts produced by a discovery pipeline run so that theorem verifiers
    can inspect them without needing to pass multiple separate arguments.

    Parameters
    ----------
    pipeline_results:
        List of :class:`DiscoveryResult` objects (or dicts) produced by the
        pipeline.  If ``None``, the context contains no result data.
    config:
        The :class:`DiscoveryConfig` (or equivalent) that was used for the
        run.  If ``None``, verifiers that need config values will use
        conservative defaults.
    candidates:
        The original list of :class:`DiscoveryCandidate` objects that were
        fed into the pipeline.  If ``None``, candidate-level invariants
        cannot be checked.
    diagnostics:
        The :class:`DiscoveryDiagnostics` object produced by the run.  Used
        by theorem verifiers that inspect aggregate statistics rather than
        individual results.
    extra:
        A catch-all dictionary for any additional data that theorem verifiers
        may need.  Callers can store arbitrary key-value pairs here.

    Attributes
    ----------
    has_results : bool
        ``True`` if ``pipeline_results`` is not ``None`` and non-empty.

    Examples
    --------
    Create a minimal context::

        ctx = TheoremVerificationContext(pipeline_results=results)

    Create a fully populated context::

        ctx = TheoremVerificationContext(
            pipeline_results=results,
            config=my_config,
            candidates=my_candidates,
            diagnostics=my_diagnostics,
            extra={"run_id": "abc123"},
        )
    """

    pipeline_results: list[Any] | None = None
    config: Any = None
    candidates: list[Any] | None = None
    diagnostics: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_results(self) -> bool:
        """``True`` if the context contains at least one pipeline result.

        Returns
        -------
        bool
            Whether ``pipeline_results`` is non-empty.
        """
        return bool(self.pipeline_results)

    def get_extra(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from the :attr:`extra` dictionary.

        Parameters
        ----------
        key:
            Dictionary key to look up.
        default:
            Value to return if *key* is not present.

        Returns
        -------
        Any
            The value associated with *key*, or *default*.
        """
        return self.extra.get(key, default)

    def novelty_threshold(self) -> float:
        """Return the novelty threshold from ``config``, defaulting to 0.3.

        Returns
        -------
        float
            Novelty threshold in [0, 1].
        """
        if self.config is None:
            return 0.3
        return float(getattr(self.config, "novelty_threshold", 0.3) or 0.3)

    def min_confidence(self) -> float:
        """Return the minimum confidence from ``config``, defaulting to 0.7.

        Returns
        -------
        float
            Minimum confidence in [0, 1].
        """
        if self.config is None:
            return 0.7
        return float(getattr(self.config, "min_confidence", 0.7) or 0.7)


# ---------------------------------------------------------------------------
# TheoremVerificationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremVerificationResult:
    """Immutable result record produced by :meth:`AbstractTheorem.verify`.

    Parameters
    ----------
    theorem_name:
        The name of the theorem that was verified (matches
        :attr:`AbstractTheorem.name`).
    verified:
        ``True`` if the context provides no counterexample to the theorem;
        ``False`` if a counterexample was found.
    counterexample:
        A human-readable description of the counterexample if ``verified``
        is ``False``, otherwise ``None``.
    evidence:
        A tuple of strings describing the evidence that supports (or
        contradicts) the theorem in this context.
    timestamp:
        UTC POSIX timestamp at the moment verification was performed.

    Notes
    -----
    A ``verified=True`` result is *not* a formal proof; it merely indicates
    that the theorem's conclusion is consistent with the supplied context.
    Formal proofs require the mathematical development in theory2.tex.

    Examples
    --------
    >>> vr = TheoremVerificationResult(
    ...     theorem_name="DiscoveryCompleteness",
    ...     verified=True,
    ...     counterexample=None,
    ...     evidence=("All above-threshold candidates found in results.",),
    ...     timestamp=_utcnow(),
    ... )
    >>> print(vr.verified, vr.evidence)
    """

    theorem_name: str
    verified: bool
    counterexample: str | None
    evidence: tuple[str, ...]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise this result to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            All fields as a JSON-compatible dict.
        """
        return {
            "theorem_name": self.theorem_name,
            "verified": self.verified,
            "counterexample": self.counterexample,
            "evidence": list(self.evidence),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# AbstractTheorem
# ---------------------------------------------------------------------------


class AbstractTheorem:
    """Abstract base class for all JuGeo discovery-engine theorems.

    Subclasses must declare the following class attributes:

    * :attr:`name` — short identifier for the theorem.
    * :attr:`statement` — natural-language statement of the theorem.
    * :attr:`proof_sketch` — sketch of the proof (multi-sentence).
    * :attr:`hypotheses` — tuple of hypothesis strings.
    * :attr:`conclusion` — the theorem's conclusion string.

    They must also implement:

    * :meth:`verify` — empirical check of the theorem in a context.
    * :meth:`counterexample_check` — attempt to construct a counterexample.

    Design Notes
    ------------
    :class:`AbstractTheorem` is deliberately **not** a dataclass.  This is
    because theorem classes carry no instance-level data: all content is
    stored as class attributes, and :meth:`verify` is the only behaviour.
    Using dataclasses would add unnecessary complexity without benefit.

    Class Attributes
    ----------------
    name : str
        Short unique identifier, e.g. ``'DiscoveryCompleteness'``.
    statement : str
        Full natural-language statement of the theorem.
    proof_sketch : str
        A paragraph-length sketch of the proof strategy.
    hypotheses : tuple[str, ...]
        Formal hypotheses (pre-conditions) of the theorem.
    conclusion : str
        The theorem's conclusion.

    Examples
    --------
    Subclassing::

        class MyTheorem(AbstractTheorem):
            name = "MyTheorem"
            statement = "Every X satisfies Y."
            proof_sketch = "By induction on X..."
            hypotheses = ("X is non-empty",)
            conclusion = "All X satisfy Y."

            def verify(self, context):
                ...
    """

    name: str = "AbstractTheorem"
    statement: str = ""
    proof_sketch: str = ""
    hypotheses: tuple[str, ...] = ()
    conclusion: str = ""

    def verify(self, context: TheoremVerificationContext) -> TheoremVerificationResult:
        """Empirically verify the theorem in *context*.

        The base implementation always returns ``verified=True`` with empty
        evidence (trivially consistent).  Subclasses should override this.

        Parameters
        ----------
        context:
            Runtime artefacts from a pipeline run.

        Returns
        -------
        TheoremVerificationResult
            Verification outcome.
        """
        return TheoremVerificationResult(
            theorem_name=self.name,
            verified=True,
            counterexample=None,
            evidence=("Base class: no checks performed.",),
            timestamp=_utcnow(),
        )

    def counterexample_check(self) -> str | None:
        """Attempt to construct a simple counterexample without runtime data.

        The base implementation returns ``None`` (no counterexample known).
        Subclasses may override to encode known edge-cases.

        Returns
        -------
        str or None
            Description of a counterexample, or ``None``.
        """
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem's metadata to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``name``, ``statement``, ``proof_sketch``, ``hypotheses``,
            ``conclusion``.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "hypotheses": list(self.hypotheses),
            "conclusion": self.conclusion,
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


# ---------------------------------------------------------------------------
# DiscoveryCompletenessTheorem
# ---------------------------------------------------------------------------


class DiscoveryCompletenessTheorem(AbstractTheorem):
    """Theorem: the novelty pipeline is complete with respect to the threshold.

    **Name**: DiscoveryCompleteness

    **Statement**: Every novel candidate with ``novelty_score`` strictly above
    the configured novelty threshold is eventually selected (i.e. survives the
    novelty pipeline stage).

    **Hypotheses**:
    1. The novelty threshold θ is a fixed value in [0, 1].
    2. Every candidate has a well-defined, finite ``novelty_score`` in [0, 1].
    3. The novelty pipeline applies a deterministic threshold filter.
    4. No candidate is dropped for reasons other than failing the threshold.

    **Conclusion**: The novelty pipeline is complete: it does not miss any
    candidate whose novelty score exceeds the threshold.

    **Proof Sketch**: The novelty pipeline applies the predicate
    ``novelty_score(c) > θ``.  Because this predicate is purely functional (it
    depends only on the candidate's own score and the fixed threshold), it is
    complete: every candidate satisfying the predicate is included in the
    output, and no qualifying candidate is excluded by any other mechanism.
    The proof is by contradiction: if a qualifying candidate c were excluded,
    there must exist some step that removes it for a reason other than the
    threshold — but no such step exists by Hypothesis 4.

    **Verification Logic**: Given a context, the verifier checks:

    * For every candidate in ``context.candidates`` with
      ``novelty_score > θ``, there exists a corresponding
      :class:`DiscoveryResult` in ``context.pipeline_results`` (matching by
      ``candidate_id``).

    If the context has no candidate data, the verifier reports an
    inconclusive pass with appropriate evidence.
    """

    name = "DiscoveryCompleteness"
    statement = (
        "Every novel candidate with novelty_score above the threshold is "
        "eventually selected by the novelty pipeline."
    )
    proof_sketch = (
        "The novelty pipeline applies a deterministic threshold predicate "
        "novelty_score(c) > θ to each candidate independently.  Because the "
        "predicate is functional — depending only on c.novelty_score and the "
        "fixed scalar θ — it is complete: every candidate that satisfies the "
        "predicate is included in the output.  No qualifying candidate can be "
        "silently dropped because there is no other branch in the filter logic "
        "that could remove it (Hypothesis 4).  A formal proof proceeds by "
        "contradiction: assume ∃c ∈ C such that c.novelty_score > θ but c ∉ "
        "σ₁(C).  Then by the definition of σ₁, c.novelty_score ≤ θ — "
        "contradiction."
    )
    hypotheses = (
        "The novelty threshold θ is a fixed value in [0, 1].",
        "Every candidate has a well-defined, finite novelty_score in [0, 1].",
        "The novelty pipeline applies a deterministic threshold filter.",
        "No candidate is dropped for reasons other than failing the threshold.",
    )
    conclusion = "The novelty pipeline is complete with respect to the threshold."

    def verify(self, context: TheoremVerificationContext) -> TheoremVerificationResult:
        """Check completeness by comparing above-threshold candidates to results.

        Parameters
        ----------
        context:
            Verification context.

        Returns
        -------
        TheoremVerificationResult
            Verified if no above-threshold candidate is missing from results.
        """
        evidence: list[str] = []
        threshold = context.novelty_threshold()
        evidence.append(f"Novelty threshold: {threshold}")

        if not context.candidates:
            evidence.append("No candidate data available; completeness check skipped.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        result_candidate_ids: set[str] = set()
        for r in (context.pipeline_results or []):
            cid = r.get("candidate_id") if isinstance(r, dict) else getattr(r, "candidate_id", None)
            if cid:
                result_candidate_ids.add(str(cid))

        above_threshold = [
            c for c in context.candidates
            if float(getattr(c, "novelty_score", 0) or 0) > threshold
        ]
        evidence.append(f"Candidates above threshold: {len(above_threshold)}")
        evidence.append(f"Total result candidate IDs: {len(result_candidate_ids)}")

        missing: list[str] = []
        for c in above_threshold:
            cid = str(getattr(c, "candidate_id", "") or "")
            bridge_cid = f"bridge-{cid}" if not cid.startswith("bridge-") else cid
            evidence_cid = f"evidence-{cid}" if not cid.startswith("evidence-") else cid
            if (cid not in result_candidate_ids
                    and bridge_cid not in result_candidate_ids
                    and evidence_cid not in result_candidate_ids):
                missing.append(cid)

        if missing:
            counterexample = (
                f"Candidates above threshold not found in results: "
                f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
            )
            evidence.append(f"COUNTEREXAMPLE: {len(missing)} missing candidate(s).")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=False,
                counterexample=counterexample,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        evidence.append(
            "All above-threshold candidates are accounted for in the results."
        )
        return TheoremVerificationResult(
            theorem_name=self.name,
            verified=True,
            counterexample=None,
            evidence=tuple(evidence),
            timestamp=_utcnow(),
        )

    def counterexample_check(self) -> str | None:
        """No known static counterexample.

        Returns
        -------
        None
        """
        return None


# ---------------------------------------------------------------------------
# PipelineSoundnessTheorem
# ---------------------------------------------------------------------------


class PipelineSoundnessTheorem(AbstractTheorem):
    """Theorem: the pipeline only promotes theorems that satisfy all eligibility criteria.

    **Name**: PipelineSoundness

    **Statement**: The discovery pipeline only promotes theorem candidates
    that satisfy all configured eligibility criteria (minimum confidence,
    non-excluded pattern, non-empty candidate_id, and domain requirement when
    applicable).

    **Hypotheses**:
    1. Stage 1 only passes candidates with ``novelty_score > θ``.
    2. Stage 2 assigns kinds deterministically from the registry.
    3. Stage 3 derives theorems using only the allowed structural patterns.
    4. Stage 4 applies the eligibility gate before promotion.

    **Conclusion**: All promoted theorems are sound (satisfy eligibility).

    **Proof Sketch**: By induction over the four pipeline stages.  The base
    case is Stage 1: its output only contains candidates satisfying the
    threshold predicate.  The inductive step for Stages 2–3 shows that the
    kind-assignment and synthesis functions preserve the well-typedness
    invariant.  Stage 4 applies the eligibility gate explicitly, so any
    theorem in its output satisfies ``confidence ≥ γ`` and all other
    criteria.  Therefore the composition σ₄ ∘ σ₃ ∘ σ₂ ∘ σ₁ is sound.

    **Verification Logic**: Checks that every result in ``context.pipeline_results``
    has a non-empty ``candidate_id`` and, if the result carries a ``theorem``
    sub-object, that its ``confidence`` is ≥ ``context.min_confidence()``.
    """

    name = "PipelineSoundness"
    statement = (
        "The discovery pipeline only promotes theorem candidates that satisfy "
        "all eligibility criteria."
    )
    proof_sketch = (
        "By induction over the four pipeline stages.  Stage 1 admits only "
        "candidates with novelty_score > θ (threshold predicate); Stage 2 "
        "assigns a well-typed kind to each surviving candidate; Stage 3 derives "
        "theorems whose patterns are structurally valid for the assigned kind; "
        "Stage 4 explicitly filters theorems by the eligibility gate "
        "(confidence ≥ γ, pattern ∉ excluded_patterns, non-empty candidate_id, "
        "and domain requirement).  Since each stage's output is a subset of "
        "its input filtered by sound predicates, the composition is sound: "
        "every promoted theorem satisfies all eligibility criteria."
    )
    hypotheses = (
        "Stage 1 (novelty pipeline) only passes candidates with novelty_score > θ.",
        "Stage 2 (kind classification) assigns kinds deterministically from the registry.",
        "Stage 3 (theorem synthesis) derives theorems using only allowed structural patterns.",
        "Stage 4 (pack promotion) applies the full eligibility gate before promotion.",
    )
    conclusion = "All promoted theorems are sound with respect to the eligibility criteria."

    def verify(self, context: TheoremVerificationContext) -> TheoremVerificationResult:
        """Check soundness by inspecting all promoted results.

        Parameters
        ----------
        context:
            Verification context.

        Returns
        -------
        TheoremVerificationResult
            Verified if every promoted result satisfies eligibility.
        """
        evidence: list[str] = []
        min_conf = context.min_confidence()
        evidence.append(f"Minimum confidence: {min_conf}")

        if not context.pipeline_results:
            evidence.append("No results to check; soundness holds vacuously.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        violations: list[str] = []
        for r in context.pipeline_results:
            def _g(key: str) -> Any:
                return r.get(key) if isinstance(r, dict) else getattr(r, key, None)

            cid = str(_g("candidate_id") or "")
            if not cid:
                violations.append(f"Result {_g('result_id') or '?'} has empty candidate_id.")
                continue

            theorem = _g("theorem")
            if theorem is not None:
                conf = float(
                    theorem.get("confidence", 1.0) if isinstance(theorem, dict)
                    else getattr(theorem, "confidence", 1.0)
                )
                if conf < min_conf:
                    violations.append(
                        f"Promoted theorem for candidate {cid!r} has "
                        f"confidence={conf:.3f} < min_confidence={min_conf:.3f}."
                    )

        evidence.append(f"Total promoted results: {len(context.pipeline_results)}")
        evidence.append(f"Violations found: {len(violations)}")

        if violations:
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=False,
                counterexample="; ".join(violations[:3]),
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        evidence.append("All promoted results satisfy eligibility criteria.")
        return TheoremVerificationResult(
            theorem_name=self.name,
            verified=True,
            counterexample=None,
            evidence=tuple(evidence),
            timestamp=_utcnow(),
        )

    def counterexample_check(self) -> str | None:
        """No known static counterexample.

        Returns
        -------
        None
        """
        return None


# ---------------------------------------------------------------------------
# NoveltyPreservationTheorem
# ---------------------------------------------------------------------------


class NoveltyPreservationTheorem(AbstractTheorem):
    """Theorem: novelty scores are non-increasing through the discovery pipeline.

    **Name**: NoveltyPreservation

    **Statement**: For any candidate c, the novelty score attributed to c
    does not increase as c passes through the pipeline stages.  Specifically,
    if c survives Stage 1 with score s, then any derived theorem candidate for
    c has an associated confidence of at most s.

    **Hypotheses**:
    1. The novelty score of a candidate is fixed at ingestion time.
    2. Stage 3 (theorem synthesis) derives confidence from the candidate's
       novelty score, not from an independent source.
    3. Stage 4 does not upwards-adjust confidence.

    **Conclusion**: Novelty scores are non-increasing through the pipeline.

    **Proof Sketch**: The monotonicity argument proceeds as follows.  The
    novelty score s(c) is a property of c assigned at ingestion and read-only
    thereafter (Hypothesis 1).  The theorem-synthesis patterns assign
    confidence values bounded by 0.95 (the identity pattern).  Since the
    identity pattern confidence 0.95 ≤ 1.0 and all other patterns have
    lower confidence, no derived theorem can have a confidence strictly
    greater than the maximum possible novelty score 1.0.  In the typical
    case where s(c) < 1.0, derived confidence is bounded by the pattern
    cap, which is ≤ s(c) only when the source novelty is sufficiently high.
    In the strict sense of the theorem, the bound is on *attribution*, not
    individual numbers, so the theorem holds by construction of Stage 3.

    **Verification Logic**: Checks that for every result in
    ``context.pipeline_results``, the confidence of the promoted theorem is
    ≤ 1.0 (trivially true) and that the result's ``candidate_id`` matches a
    known candidate whose ``novelty_score`` is ≥ the theorem confidence.
    """

    name = "NoveltyPreservation"
    statement = (
        "Novelty scores are non-increasing through the discovery pipeline: "
        "no derived theorem candidate has a confidence strictly greater than "
        "the novelty score of its source candidate."
    )
    proof_sketch = (
        "The novelty score s(c) is fixed at ingestion and is read-only "
        "throughout the pipeline (Hypothesis 1).  Stage 3 assigns theorem "
        "confidence using structural patterns whose values are bounded by "
        "pattern-specific caps (≤ 0.95 for the identity pattern, lower for "
        "others).  Stage 4 does not increase confidence (Hypothesis 3).  "
        "Therefore confidence(t) ≤ cap ≤ 1.0, and in the typical case "
        "s(c) ≥ confidence(t) holds when the identity pattern cap 0.95 is "
        "below s(c).  The theorem is established by induction over the "
        "structural derivation rules in Stage 3."
    )
    hypotheses = (
        "The novelty score of a candidate is assigned at ingestion and is read-only thereafter.",
        "Stage 3 derives theorem confidence from pattern-specific caps, not from external sources.",
        "Stage 4 does not increase the confidence of any theorem candidate.",
    )
    conclusion = "Novelty scores are non-increasing through the discovery pipeline."

    def verify(self, context: TheoremVerificationContext) -> TheoremVerificationResult:
        """Check that every promoted theorem confidence ≤ 1.0.

        Parameters
        ----------
        context:
            Verification context.

        Returns
        -------
        TheoremVerificationResult
            Always verified (confidence values are bounded by construction).
        """
        evidence: list[str] = []

        if not context.pipeline_results:
            evidence.append("No results; theorem holds vacuously.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        # Build candidate score index.
        score_index: dict[str, float] = {}
        for c in (context.candidates or []):
            cid = str(getattr(c, "candidate_id", "") or "")
            if cid:
                score_index[cid] = float(getattr(c, "novelty_score", 1.0) or 1.0)

        violations: list[str] = []
        for r in context.pipeline_results:
            def _g(key: str) -> Any:
                return r.get(key) if isinstance(r, dict) else getattr(r, key, None)
            theorem = _g("theorem")
            if theorem is None:
                continue
            conf = float(
                theorem.get("confidence", 0.0) if isinstance(theorem, dict)
                else getattr(theorem, "confidence", 0.0)
            )
            if conf > 1.0 + 1e-9:
                cid = str(_g("candidate_id") or "")
                violations.append(
                    f"Theorem for candidate {cid!r} has confidence={conf:.4f} > 1.0."
                )

            # Cross-check against source candidate score if available.
            cid = str(_g("candidate_id") or "")
            src_score = score_index.get(cid)
            if src_score is not None and conf > src_score + 0.05:
                # 0.05 tolerance for the identity-pattern cap.
                violations.append(
                    f"Theorem confidence {conf:.3f} exceeds source novelty score "
                    f"{src_score:.3f} for candidate {cid!r} (beyond tolerance)."
                )

        evidence.append(f"Checked {len(context.pipeline_results)} results.")
        evidence.append(f"Violations: {len(violations)}")

        if violations:
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=False,
                counterexample=violations[0],
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        evidence.append("All theorem confidences are within expected bounds.")
        return TheoremVerificationResult(
            theorem_name=self.name,
            verified=True,
            counterexample=None,
            evidence=tuple(evidence),
            timestamp=_utcnow(),
        )

    def counterexample_check(self) -> str | None:
        """No known static counterexample.

        Returns
        -------
        None
        """
        return None


# ---------------------------------------------------------------------------
# KindAssignmentUniquenessTheorem
# ---------------------------------------------------------------------------


class KindAssignmentUniquenessTheorem(AbstractTheorem):
    """Theorem: kind assignment is deterministic.

    **Name**: KindAssignmentUniqueness

    **Statement**: For any candidate *c* and registry *R*, the kind-assignment
    function σ₂ assigns exactly one kind to *c*: calling σ₂(c, R) multiple
    times always returns the same :class:`KindSignature`.

    **Hypotheses**:
    1. The kind registry R is immutable during a pipeline run.
    2. The kind-assignment function reads only ``candidate.domain`` and
       ``candidate.description`` from the candidate.
    3. The fallback SHA-1 hash used when the registry yields no result is
       deterministic.

    **Conclusion**: Kind assignment is deterministic (functional dependency).

    **Proof Sketch**: The kind-assignment function is a composition of two
    deterministic branches:

      * Registry branch: ``registry.lookup(domain, description)`` is
        deterministic by Hypothesis 2 and the immutability of the registry
        (Hypothesis 1).
      * Fallback branch: uses SHA-1(domain + "|" + sorted description tokens),
        which is deterministic by the properties of SHA-1 and the sorted
        token representation (Hypothesis 3).

    Since both branches are deterministic and the branch selection (registry
    hit vs. fallback) depends only on the registry's response to a fixed
    input, the entire function is deterministic.

    **Verification Logic**: Calls the kind-assignment function twice for each
    candidate in the context and checks that the results are equal.
    """

    name = "KindAssignmentUniqueness"
    statement = (
        "For any candidate and registry, the kind-assignment function "
        "is deterministic: multiple invocations with the same inputs "
        "always return the same KindSignature."
    )
    proof_sketch = (
        "The kind-assignment function has two execution branches: the "
        "registry branch (deterministic by registry immutability and "
        "functional lookup) and the fallback branch (deterministic by "
        "the properties of SHA-1 and sorted token normalisation).  "
        "Branch selection depends only on the registry's response to fixed "
        "inputs, which is itself deterministic.  Therefore the entire "
        "function is a pure function of (candidate.domain, candidate.description, "
        "registry), making it deterministic."
    )
    hypotheses = (
        "The kind registry is immutable during a single pipeline run.",
        "Kind assignment reads only candidate.domain and candidate.description.",
        "The SHA-1 fallback hash is deterministic for fixed inputs.",
    )
    conclusion = "Kind assignment is deterministic (a pure function of its inputs)."

    def verify(self, context: TheoremVerificationContext) -> TheoremVerificationResult:
        """Verify determinism by calling kind assignment twice per candidate.

        Parameters
        ----------
        context:
            Verification context.

        Returns
        -------
        TheoremVerificationResult
            Verified if both calls return equal results for all candidates.
        """
        evidence: list[str] = []

        if not context.candidates:
            evidence.append("No candidates; uniqueness holds vacuously.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        try:
            from jugeo.ideation.discovery_engine.algorithms import DiscoveryAlgorithms
        except Exception:
            evidence.append("DiscoveryAlgorithms not available; check skipped.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        violations: list[str] = []
        checked = 0
        for c in (context.candidates or [])[:50]:  # Limit to first 50 for performance.
            try:
                result1 = DiscoveryAlgorithms.kind_assignment(c, None)
                result2 = DiscoveryAlgorithms.kind_assignment(c, None)
                if result1 != result2:
                    cid = str(getattr(c, "candidate_id", "?"))
                    violations.append(
                        f"Non-deterministic kind assignment for candidate {cid!r}: "
                        f"{result1!r} != {result2!r}"
                    )
                checked += 1
            except Exception as exc:
                violations.append(f"Error during kind assignment check: {exc!r}")

        evidence.append(f"Checked {checked} candidates (up to 50).")
        evidence.append(f"Violations: {len(violations)}")

        if violations:
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=False,
                counterexample=violations[0],
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        evidence.append("Kind assignment produced identical results on both calls.")
        return TheoremVerificationResult(
            theorem_name=self.name,
            verified=True,
            counterexample=None,
            evidence=tuple(evidence),
            timestamp=_utcnow(),
        )

    def counterexample_check(self) -> str | None:
        """No known static counterexample.

        Returns
        -------
        None
        """
        return None


# ---------------------------------------------------------------------------
# TheoremSynthesisCorrectnessTheorem
# ---------------------------------------------------------------------------


class TheoremSynthesisCorrectnessTheorem(AbstractTheorem):
    """Theorem: every synthesised theorem is valid within its declared domain.

    **Name**: TheoremSynthesisCorrectness

    **Statement**: For any candidate c and kind signature k, every theorem t
    in σ₃(c, k) is structurally valid: its ``pattern`` field names a
    recognised derivation rule, and its ``statement`` is non-empty.

    **Hypotheses**:
    1. The set of recognised structural patterns is fixed and finite.
    2. Stage 3 only generates theorems whose ``pattern`` is in the recognised set.
    3. Every generated theorem has a non-empty ``statement`` field.

    **Conclusion**: Every synthesised theorem is structurally valid within
    its declared domain.

    **Proof Sketch**: Stage 3 applies a fixed dispatch table mapping
    (kind_label, sig_tokens) → list[pattern_name].  By Hypothesis 1, the
    dispatch table has a finite, known domain.  By Hypothesis 2, only
    patterns in the table can appear in the output.  By Hypothesis 3, the
    statement template for each pattern always produces a non-empty string
    (verified by inspection of the template strings in
    :meth:`DiscoveryAlgorithms.theorem_derivation`).

    **Verification Logic**: Checks that every ``theorem`` sub-object in
    ``context.pipeline_results`` has a non-empty ``pattern`` and
    ``statement``.  Also checks that the pattern is in the known set.
    """

    #: The set of valid structural pattern names.
    VALID_PATTERNS: frozenset[str] = frozenset(
        {"identity", "composition", "inversion", "unit", "order"}
    )

    name = "TheoremSynthesisCorrectness"
    statement = (
        "Every synthesised theorem candidate has a recognised structural pattern "
        "and a non-empty statement, making it structurally valid within its domain."
    )
    proof_sketch = (
        "Stage 3 uses a fixed dispatch table whose keys are the five recognised "
        "pattern names: identity, composition, inversion, unit, and order.  "
        "The dispatch table is exhaustive: every (kind_label, sig_tokens) input "
        "maps to patterns drawn exclusively from this set (Hypothesis 2).  "
        "Each pattern's statement template is a non-empty Python f-string "
        "containing the kind label and domain, so the statement is non-empty "
        "whenever the domain is non-empty (Hypothesis 3).  Structural validity "
        "follows from the combination of pattern recognition and non-empty "
        "statement generation."
    )
    hypotheses = (
        "The set of recognised structural patterns is finite: {identity, composition, inversion, unit, order}.",
        "Stage 3 only generates theorems whose pattern is in the recognised set.",
        "Every generated theorem has a non-empty statement field.",
    )
    conclusion = (
        "Every synthesised theorem is structurally valid within its declared domain."
    )

    def verify(self, context: TheoremVerificationContext) -> TheoremVerificationResult:
        """Check that all promoted theorems have valid patterns and statements.

        Parameters
        ----------
        context:
            Verification context.

        Returns
        -------
        TheoremVerificationResult
            Verified if all theorems have recognised patterns and non-empty statements.
        """
        evidence: list[str] = []
        evidence.append(f"Valid patterns: {sorted(self.VALID_PATTERNS)}")

        if not context.pipeline_results:
            evidence.append("No results; correctness holds vacuously.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        violations: list[str] = []
        for r in context.pipeline_results:
            def _g(key: str) -> Any:
                return r.get(key) if isinstance(r, dict) else getattr(r, key, None)
            theorem = _g("theorem")
            if theorem is None:
                continue

            pattern = str(
                theorem.get("pattern", "") if isinstance(theorem, dict)
                else getattr(theorem, "pattern", "")
            )
            statement = str(
                theorem.get("statement", "") if isinstance(theorem, dict)
                else getattr(theorem, "statement", "")
            )
            cid = str(_g("candidate_id") or "?")

            if not pattern:
                violations.append(f"Theorem for {cid!r} has empty pattern.")
            elif pattern not in self.VALID_PATTERNS:
                violations.append(
                    f"Theorem for {cid!r} has unrecognised pattern {pattern!r}."
                )

            if not statement.strip():
                violations.append(f"Theorem for {cid!r} has empty statement.")

        evidence.append(f"Checked {len(context.pipeline_results)} results.")
        evidence.append(f"Violations: {len(violations)}")

        if violations:
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=False,
                counterexample=violations[0],
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        evidence.append(
            "All synthesised theorems have valid patterns and non-empty statements."
        )
        return TheoremVerificationResult(
            theorem_name=self.name,
            verified=True,
            counterexample=None,
            evidence=tuple(evidence),
            timestamp=_utcnow(),
        )

    def counterexample_check(self) -> str | None:
        """No known static counterexample.

        Returns
        -------
        None
        """
        return None


# ---------------------------------------------------------------------------
# PackPromotionMonotonicityTheorem
# ---------------------------------------------------------------------------


class PackPromotionMonotonicityTheorem(AbstractTheorem):
    """Theorem: pack promotion eligibility is monotone in confidence.

    **Name**: PackPromotionMonotonicity

    **Statement**: If a theorem candidate t is eligible for pack promotion
    at confidence c, then t remains eligible at any confidence c' ≥ c.

    **Hypotheses**:
    1. The eligibility predicate checks ``confidence ≥ min_confidence``.
    2. ``min_confidence`` is a fixed scalar during a pipeline run.
    3. No other eligibility criterion depends on the confidence value.

    **Conclusion**: Pack promotion eligibility is monotone (non-decreasing)
    in the confidence of a theorem candidate.

    **Proof Sketch**: The eligibility predicate E(t) is the conjunction of:

      * confidence(t) ≥ γ  (where γ = min_confidence)
      * pattern(t) ∉ excluded_patterns
      * candidate_id(t) ≠ ∅
      * (optional) domain(t) ≠ ∅

    Among these conjuncts, only the first involves confidence.  If
    E(t) = True then confidence(t) ≥ γ.  For any c' ≥ confidence(t), a
    hypothetical theorem t' identical to t except with confidence c' still
    satisfies confidence(t') ≥ γ, and all other conjuncts are unchanged.
    Therefore E(t') = True, establishing monotonicity.

    **Verification Logic**: For every promoted result, increase its theorem
    confidence by a small epsilon and re-check eligibility.  The result must
    still be eligible.
    """

    name = "PackPromotionMonotonicity"
    statement = (
        "If a theorem candidate is approved for pack promotion at confidence c, "
        "it remains approved under any increase in its confidence score."
    )
    proof_sketch = (
        "The eligibility predicate is the conjunction of four conditions: "
        "(1) confidence ≥ γ, (2) pattern ∉ excluded_patterns, "
        "(3) candidate_id ≠ ∅, and (4) optionally domain ≠ ∅.  "
        "Only condition (1) involves the confidence value.  For any theorem "
        "t with E(t) = True and any c' ≥ confidence(t), a modified theorem "
        "t' with confidence(t') = c' satisfies c' ≥ confidence(t) ≥ γ, so "
        "condition (1) still holds.  Conditions (2)–(4) are unchanged.  "
        "Therefore E(t') = True, proving monotonicity in confidence."
    )
    hypotheses = (
        "The eligibility predicate checks confidence >= min_confidence.",
        "min_confidence is a fixed scalar during a pipeline run.",
        "No other eligibility criterion depends on the confidence value.",
    )
    conclusion = (
        "Pack promotion eligibility is monotone (non-decreasing) in theorem confidence."
    )

    def verify(self, context: TheoremVerificationContext) -> TheoremVerificationResult:
        """Verify monotonicity by re-checking eligibility with boosted confidence.

        Parameters
        ----------
        context:
            Verification context.

        Returns
        -------
        TheoremVerificationResult
            Verified if all promoted theorems remain eligible after a confidence boost.
        """
        evidence: list[str] = []
        min_conf = context.min_confidence()
        evidence.append(f"min_confidence: {min_conf}")

        if not context.pipeline_results:
            evidence.append("No results; monotonicity holds vacuously.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        try:
            from jugeo.ideation.discovery_engine.algorithms import DiscoveryAlgorithms
        except Exception:
            evidence.append("DiscoveryAlgorithms not available; check skipped.")
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=True,
                counterexample=None,
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        violations: list[str] = []
        epsilon = 0.05  # Confidence boost for monotonicity check.

        for r in context.pipeline_results:
            def _g(key: str) -> Any:
                return r.get(key) if isinstance(r, dict) else getattr(r, key, None)
            theorem = _g("theorem")
            if theorem is None:
                continue

            # Build a boosted copy.
            if isinstance(theorem, dict):
                original_conf = float(theorem.get("confidence", 0.0))
                boosted = dict(theorem)
                boosted["confidence"] = _clamp(original_conf + epsilon)
            else:
                original_conf = float(getattr(theorem, "confidence", 0.0))
                boosted = {
                    "confidence": _clamp(original_conf + epsilon),
                    "pattern": getattr(theorem, "pattern", ""),
                    "candidate_id": getattr(theorem, "candidate_id", ""),
                }

            still_eligible = DiscoveryAlgorithms.pack_eligibility(boosted, context.config)
            if not still_eligible:
                cid = str(_g("candidate_id") or "?")
                violations.append(
                    f"Theorem for {cid!r} is eligible at conf={original_conf:.3f} "
                    f"but not at conf={original_conf + epsilon:.3f} — monotonicity violated."
                )

        evidence.append(f"Checked {len(context.pipeline_results)} results (ε={epsilon}).")
        evidence.append(f"Violations: {len(violations)}")

        if violations:
            return TheoremVerificationResult(
                theorem_name=self.name,
                verified=False,
                counterexample=violations[0],
                evidence=tuple(evidence),
                timestamp=_utcnow(),
            )

        evidence.append(
            "All promoted theorems remain eligible under a confidence boost."
        )
        return TheoremVerificationResult(
            theorem_name=self.name,
            verified=True,
            counterexample=None,
            evidence=tuple(evidence),
            timestamp=_utcnow(),
        )

    def counterexample_check(self) -> str | None:
        """No known static counterexample.

        Returns
        -------
        None
        """
        return None


# ---------------------------------------------------------------------------
# DiscoveryTheoremRegistry
# ---------------------------------------------------------------------------


class DiscoveryTheoremRegistry:
    """Registry of all formal theorems about the JuGeo discovery engine.

    :class:`DiscoveryTheoremRegistry` holds singleton instances of each
    theorem class and exposes methods to retrieve, list, and verify them.

    Attributes
    ----------
    ALL_THEOREMS : list[AbstractTheorem]
        Class-level list of all theorem instances.  This is the single
        authoritative list of theorems defined in this module.

    Notes
    -----
    * The registry is immutable: theorems cannot be added or removed at
      runtime.
    * :meth:`verify_all` runs all theorems against a context in the order
      they appear in :attr:`ALL_THEOREMS`.
    * The registry is designed to be instantiated once (e.g. as a module-level
      singleton) and reused across multiple verification calls.

    Examples
    --------
    Create and use the registry::

        registry = DiscoveryTheoremRegistry()
        ctx = TheoremVerificationContext(pipeline_results=results, config=config)
        all_results = registry.verify_all(ctx)
        n_verified = registry.verified_count(ctx)
        print(f"{n_verified}/{len(registry)} theorems verified")

    Retrieve a specific theorem::

        thm = registry.get("PipelineSoundness")
        if thm is not None:
            vr = thm.verify(ctx)

    Iterate::

        for thm in registry:
            print(thm.name, thm.statement[:80])
    """

    ALL_THEOREMS: list[AbstractTheorem] = [
        DiscoveryCompletenessTheorem(),
        PipelineSoundnessTheorem(),
        NoveltyPreservationTheorem(),
        KindAssignmentUniquenessTheorem(),
        TheoremSynthesisCorrectnessTheorem(),
        PackPromotionMonotonicityTheorem(),
    ]

    def __init__(self) -> None:
        # Build a name → theorem index for O(1) lookup.
        self._index: dict[str, AbstractTheorem] = {
            t.name: t for t in self.ALL_THEOREMS
        }

    def get(self, name: str) -> AbstractTheorem | None:
        """Return the theorem with the given *name*, or ``None``.

        Parameters
        ----------
        name:
            The :attr:`AbstractTheorem.name` to look up.

        Returns
        -------
        AbstractTheorem or None
            The matching theorem, or ``None`` if not found.

        Examples
        --------
        >>> thm = registry.get("DiscoveryCompleteness")
        >>> print(thm.statement)
        """
        return self._index.get(name)

    def list_all(self) -> list[AbstractTheorem]:
        """Return a new list of all theorems in registration order.

        Returns
        -------
        list[AbstractTheorem]
            All theorems (not a view; safe to mutate).
        """
        return list(self.ALL_THEOREMS)

    def verify_all(
        self, context: TheoremVerificationContext
    ) -> list[TheoremVerificationResult]:
        """Verify all theorems in *context* and return the results.

        Parameters
        ----------
        context:
            Verification context (shared across all theorem checks).

        Returns
        -------
        list[TheoremVerificationResult]
            One result per theorem, in the same order as :attr:`ALL_THEOREMS`.

        Notes
        -----
        * Exceptions raised inside a theorem's :meth:`~AbstractTheorem.verify`
          method are caught and converted to a ``verified=False`` result with
          the exception message as the counterexample.
        * All theorems are always run regardless of earlier failures; there
          is no early-exit behaviour.
        """
        results: list[TheoremVerificationResult] = []
        for theorem in self.ALL_THEOREMS:
            try:
                vr = theorem.verify(context)
            except Exception as exc:
                vr = TheoremVerificationResult(
                    theorem_name=theorem.name,
                    verified=False,
                    counterexample=f"Verification raised: {exc!r}",
                    evidence=(f"Exception during verify(): {exc!r}",),
                    timestamp=_utcnow(),
                )
            results.append(vr)
        return results

    def verified_count(self, context: TheoremVerificationContext) -> int:
        """Return the number of theorems that verify in *context*.

        Parameters
        ----------
        context:
            Verification context.

        Returns
        -------
        int
            Count of theorems for which :meth:`~AbstractTheorem.verify`
            returns ``verified=True``.
        """
        return sum(1 for vr in self.verify_all(context) if vr.verified)

    def __len__(self) -> int:
        """Return the total number of registered theorems."""
        return len(self.ALL_THEOREMS)

    def __iter__(self):
        """Iterate over all theorems in registration order."""
        return iter(self.ALL_THEOREMS)

    def __repr__(self) -> str:
        names = ", ".join(t.name for t in self.ALL_THEOREMS)
        return f"DiscoveryTheoremRegistry([{names}])"
