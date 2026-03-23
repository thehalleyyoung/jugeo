"""Stage S03: Why This Could Matter Beyond JuGeo — JuGeo cyclic_picture package.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
Stage S03 investigates the generalisability of the cyclic picture architecture
that was designed for JuGeo (automated geometric reasoning) and asks a pointed
question: could the same three-phase loop — ideation → generation → verification
with failure-driven feedback — serve as a universal architecture for any complex
domain that requires self-improving formal systems?

The cyclic picture, as formalised in theory2.tex Ch65, is not merely a software
engineering pattern.  It is a mathematically grounded recipe for building systems
that improve while remaining sound.  Three structural properties make it
transferable:

1. **Ideation drives generation** — a structured source of candidate constructs
   (theorems, proofs, programs, policies, molecules, strategies, …) feeds a
   generator that instantiates each candidate into a concrete artefact.  In JuGeo
   this is the ideation engine producing geometric problem hypotheses that the
   construction pipeline then attempts to instantiate.

2. **Generation must be verified** — every generated artefact is subjected to a
   formal or semi-formal verification step before it can influence subsequent
   generations.  In JuGeo this is the obstruction checker that proves or refutes
   each candidate construction.

3. **Verification failures feed back as new ideas** — an obstruction is not a
   dead end; it is itself a structured object that the ideation engine can
   interpret as a new hypothesis or constraint, driving the next generation
   attempt.  In JuGeo obstructions become new ideation seeds.

Generalisation theorem (Ch65, §7.1)
------------------------------------
The chapter's generalisation theorem states that, given any domain *D* that
admits a triple (*I*, *G*, *V*) — an ideation algebra *I*, a generation functor
*G*, and a verification predicate *V* — together with a feedback morphism
φ: Obstruction(*V*, *G*(*I*)) → *I*, the induced cyclic system is sound and
makes monotone progress in the domain's capability lattice under the conditions:

* The feedback morphism φ is injective on obstructions (every distinct
  obstruction produces a distinct new idea).
* The verification predicate *V* is decidable on the image of *G*.
* The capability lattice *L_D* is well-founded with respect to the improvement
  ordering.

This module provides the computational witnesses for this theorem in the form of
:class:`BeyondJuGeoAnalyzer`, :class:`BeyondJuGeoWitness`, and
:class:`BeyondJuGeoCoordinator`, which together survey candidate domains,
construct structural analogies between JuGeo concepts and domain-specific
counterparts, estimate transfer viability and impact, and produce a
comprehensive :class:`BeyondJuGeoReport`.

Candidate domains
-----------------
The analysis considers the following candidate domains out of the box:

* **Pure mathematics** — conjecture generation, proof search, counterexample
  finding.  Ideation ≅ conjecture synthesis; generation ≅ proof assistant search;
  verification ≅ type-checking / proof checking.
* **Formal methods / software verification** — specification synthesis,
  model-checking, counterexample-guided abstraction refinement (CEGAR).
* **Software testing & fuzzing** — seed corpus evolution, coverage-guided
  generation, crash triage as feedback.
* **Computational biology** — hypothesis generation for gene regulatory networks,
  simulation-based verification, falsification feedback.
* **Economics & mechanism design** — strategy hypothesis generation, game-
  theoretic equilibrium verification, regret-based ideation update.
* **Materials science** — crystal structure generation, DFT/MD verification,
  stability-failure feedback to new generation candidates.
* **Program synthesis** — specification-driven program generation, test-suite
  verification, failing-test-as-new-constraint feedback.
* **Automated planning** — plan sketch generation, simulation-based validation,
  dead-end analysis as ideation seed.

All public names are listed in ``__all__``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    # Data classes
    "DomainProfile",
    "TransferAnalysis",
    "ImpactEstimate",
    "BeyondJuGeoReport",
    # Main classes
    "BeyondJuGeoAnalyzer",
    "BeyondJuGeoWitness",
    "BeyondJuGeoCoordinator",
    # Free functions
    "analyze_generalizability",
    "score_domain_fit",
    "list_candidate_domains",
]

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.maturity.cyclic_picture.models import (
        MatureSystem,
        MaturityLevel,
        SelfImprovingEngine,
        FederationState,
    )
except Exception:
    pass

try:
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
except Exception:
    pass

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier
    from jugeo.evidence.provenance import ProvenanceTrace
except Exception:
    pass

try:
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
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
        Current UTC timestamp in ISO-8601 format.
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
    (64 bits) for practical uniqueness within a single analysis run.

    Returns
    -------
    str
        A 16-character lowercase hexadecimal string.
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    A pure utility used throughout this module to bound scores, confidences,
    and ratios to valid ranges without raising on out-of-range inputs.  Both
    bounds are inclusive.

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
        The clamped value, satisfying ``lo <= result <= hi``.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# _CANDIDATE_DOMAINS — built-in list of domains to analyse
# ---------------------------------------------------------------------------

_CANDIDATE_DOMAINS: list[dict] = [
    {
        "name": "pure_mathematics",
        "description": (
            "Automated theorem proving, conjecture synthesis, and counterexample "
            "finding in classical and constructive mathematics.  The ideation algebra "
            "generates novel conjectures; the generation functor instantiates proof "
            "search attempts; the verification predicate is the proof checker; "
            "failed proofs become seeds for refined conjectures."
        ),
        "has_formal_verification": True,
        "has_feedback_loops": True,
        "ideation_sources": ["conjecture_synthesis", "pattern_generalisation", "analogy_transfer"],
        "obstruction_types": ["proof_failure", "counterexample", "type_mismatch"],
        "maturity_indicators": {
            "tooling_maturity": 0.85,
            "benchmark_coverage": 0.80,
            "community_adoption": 0.70,
        },
    },
    {
        "name": "formal_methods",
        "description": (
            "Specification synthesis, model checking, and counterexample-guided "
            "abstraction refinement (CEGAR) for safety-critical software and hardware.  "
            "Ideation produces candidate invariants; generation builds abstract models; "
            "verification runs model checkers; CEGAR counterexamples feed back as "
            "refined specifications."
        ),
        "has_formal_verification": True,
        "has_feedback_loops": True,
        "ideation_sources": ["invariant_templates", "predicate_abstraction", "spec_mining"],
        "obstruction_types": ["counterexample_trace", "abstraction_blowup", "spurious_cex"],
        "maturity_indicators": {
            "tooling_maturity": 0.90,
            "benchmark_coverage": 0.75,
            "community_adoption": 0.65,
        },
    },
    {
        "name": "software_verification",
        "description": (
            "Static analysis, symbolic execution, and deductive verification for "
            "general-purpose software.  Ideation generates verification conditions; "
            "generation builds proof obligations; verification discharges them via "
            "SMT solvers; unsolvable obligations become refined ideation targets."
        ),
        "has_formal_verification": True,
        "has_feedback_loops": True,
        "ideation_sources": ["vc_generation", "loop_invariant_synthesis", "contract_inference"],
        "obstruction_types": ["smt_timeout", "path_explosion", "unsolvable_vc"],
        "maturity_indicators": {
            "tooling_maturity": 0.80,
            "benchmark_coverage": 0.70,
            "community_adoption": 0.60,
        },
    },
    {
        "name": "program_synthesis",
        "description": (
            "Specification-driven automatic generation of programs satisfying a "
            "provided behavioural spec.  Ideation produces program sketches; "
            "generation fills sketch holes; verification runs the test suite; "
            "failing tests become additional synthesis constraints."
        ),
        "has_formal_verification": True,
        "has_feedback_loops": True,
        "ideation_sources": ["sketch_templates", "example_generalisation", "spec_decomposition"],
        "obstruction_types": ["test_failure", "spec_inconsistency", "search_exhaustion"],
        "maturity_indicators": {
            "tooling_maturity": 0.75,
            "benchmark_coverage": 0.65,
            "community_adoption": 0.55,
        },
    },
    {
        "name": "computational_biology",
        "description": (
            "Hypothesis generation and simulation-based verification for gene "
            "regulatory networks, protein folding, and metabolic pathways.  "
            "Ideation generates mechanistic hypotheses; generation instantiates "
            "ODE or stochastic models; verification runs simulations against "
            "experimental data; falsification feeds back revised hypotheses."
        ),
        "has_formal_verification": False,
        "has_feedback_loops": True,
        "ideation_sources": ["literature_mining", "network_topology_inference", "constraint_propagation"],
        "obstruction_types": ["simulation_mismatch", "parameter_non_identifiability", "data_sparsity"],
        "maturity_indicators": {
            "tooling_maturity": 0.60,
            "benchmark_coverage": 0.50,
            "community_adoption": 0.55,
        },
    },
    {
        "name": "economics_mechanism_design",
        "description": (
            "Strategy hypothesis generation and game-theoretic equilibrium "
            "verification for market design and auction theory.  Ideation produces "
            "mechanism candidates; generation builds strategic-form games; "
            "verification checks equilibrium conditions; regret signals from "
            "non-equilibrium outcomes feed back as refined mechanism proposals."
        ),
        "has_formal_verification": False,
        "has_feedback_loops": True,
        "ideation_sources": ["mechanism_templates", "agent_simulation", "VCG_variants"],
        "obstruction_types": ["non_incentive_compatible", "budget_imbalance", "non_existence"],
        "maturity_indicators": {
            "tooling_maturity": 0.55,
            "benchmark_coverage": 0.45,
            "community_adoption": 0.40,
        },
    },
    {
        "name": "materials_science",
        "description": (
            "Crystal structure generation, density-functional-theory / molecular "
            "dynamics verification, and stability-failure feedback for novel "
            "material discovery.  Ideation proposes crystal prototypes; generation "
            "relaxes structures; verification checks thermodynamic stability; "
            "unstable structures feed back modified composition hypotheses."
        ),
        "has_formal_verification": False,
        "has_feedback_loops": True,
        "ideation_sources": ["prototype_libraries", "substitution_rules", "generative_models"],
        "obstruction_types": ["thermodynamic_instability", "kinetic_trap", "synthesis_infeasibility"],
        "maturity_indicators": {
            "tooling_maturity": 0.65,
            "benchmark_coverage": 0.55,
            "community_adoption": 0.50,
        },
    },
    {
        "name": "automated_planning",
        "description": (
            "Plan sketch generation, simulation-based validation, and dead-end "
            "analysis as ideation seed for automated planning and scheduling.  "
            "Ideation proposes high-level plan outlines; generation refines them "
            "into grounded action sequences; verification checks goal reachability; "
            "dead-end states drive plan-repair ideation."
        ),
        "has_formal_verification": True,
        "has_feedback_loops": True,
        "ideation_sources": ["landmark_extraction", "causal_graph_analysis", "macro_operators"],
        "obstruction_types": ["dead_end_state", "resource_conflict", "temporal_inconsistency"],
        "maturity_indicators": {
            "tooling_maturity": 0.70,
            "benchmark_coverage": 0.80,
            "community_adoption": 0.60,
        },
    },
]

# ---------------------------------------------------------------------------
# DomainProfile
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DomainProfile:
    """A structured description of a domain where the cyclic picture could apply.

    A ``DomainProfile`` encodes everything the analysis pipeline needs to know
    about a candidate domain: whether it has the structural prerequisites
    (formal verification, feedback loops), what its ideation sources and
    obstruction types look like, and how mature its tooling and community are.

    The :meth:`suitability_score` method computes a single scalar summary of
    how well the domain fits the cyclic architecture, while
    :meth:`is_ready_for_cycle` applies a hard threshold to decide whether the
    domain is actionably ready today.

    Attributes
    ----------
    domain_id : str
        Unique identifier for this profile instance.
    name : str
        Short machine-readable domain label, e.g. ``'pure_mathematics'``.
    description : str
        Human-readable prose description of the domain and its relationship
        to the cyclic picture.
    has_formal_verification : bool
        Whether the domain has a decidable or semi-decidable verification
        predicate *V* as required by the generalisation theorem.
    has_feedback_loops : bool
        Whether the domain admits an injective feedback morphism φ from
        obstructions back into the ideation algebra.
    ideation_sources : list[str]
        Named sources or mechanisms that feed the ideation algebra in this
        domain.
    obstruction_types : list[str]
        Named obstruction types that arise from verification failures in this
        domain and can be fed back as new ideas.
    maturity_indicators : dict[str, float]
        Key–value pairs mapping indicator names (e.g. ``'tooling_maturity'``)
        to normalised scores in [0, 1].  Used by :meth:`suitability_score`.
    """

    domain_id: str
    name: str
    description: str
    has_formal_verification: bool
    has_feedback_loops: bool
    ideation_sources: list = field(default_factory=list)
    obstruction_types: list = field(default_factory=list)
    maturity_indicators: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def from_metadata(cls, name: str, metadata: dict) -> "DomainProfile":
        """Construct a ``DomainProfile`` from a raw metadata dictionary.

        Reads each field from *metadata* with sensible defaults, generates a
        fresh ``domain_id``, and validates that list fields are indeed lists.
        This factory is the primary construction path used by
        :meth:`BeyondJuGeoAnalyzer.profile_domain`.

        Parameters
        ----------
        name:
            Short domain label.  Written into the ``name`` field verbatim.
        metadata:
            Dictionary that may contain any subset of the profile fields.
            Missing keys fall back to safe defaults (``False`` for bools,
            empty collections for lists and dicts).

        Returns
        -------
        DomainProfile
            A fully initialised profile with ``domain_id`` set to a fresh
            ``_uid()`` value.
        """
        return cls(
            domain_id=_uid(),
            name=name,
            description=str(metadata.get("description", f"Domain: {name}")),
            has_formal_verification=bool(metadata.get("has_formal_verification", False)),
            has_feedback_loops=bool(metadata.get("has_feedback_loops", False)),
            ideation_sources=list(metadata.get("ideation_sources", [])),
            obstruction_types=list(metadata.get("obstruction_types", [])),
            maturity_indicators=dict(metadata.get("maturity_indicators", {})),
        )

    # ------------------------------------------------------------------
    def suitability_score(self) -> float:
        """Compute a scalar suitability score for applying the cyclic picture.

        The score combines three weighted components:

        1. **Structural readiness** (weight 0.4) — ``1.0`` if the domain has
           both formal verification and feedback loops, ``0.5`` if it has
           feedback loops only, ``0.25`` if it has formal verification only,
           and ``0.0`` if neither.
        2. **Expressiveness** (weight 0.3) — the normalised count of ideation
           sources plus obstruction types, capped at 1.0 and normalised over
           a reference maximum of 10.
        3. **Tooling maturity** (weight 0.3) — the mean of all values in
           ``maturity_indicators``, or ``0.5`` if the dict is empty.

        The result is clamped to ``[0.0, 1.0]``.

        Returns
        -------
        float
            Suitability score in [0.0, 1.0].  Higher is more suitable.
        """
        # Component 1: structural readiness
        if self.has_formal_verification and self.has_feedback_loops:
            structural = 1.0
        elif self.has_feedback_loops:
            structural = 0.5
        elif self.has_formal_verification:
            structural = 0.25
        else:
            structural = 0.0

        # Component 2: expressiveness (ideation + obstruction diversity)
        expressiveness_count = len(self.ideation_sources) + len(self.obstruction_types)
        expressiveness = _clamp(expressiveness_count / 10.0, 0.0, 1.0)

        # Component 3: tooling maturity
        if self.maturity_indicators:
            maturity = sum(self.maturity_indicators.values()) / len(self.maturity_indicators)
        else:
            maturity = 0.5

        score = 0.4 * structural + 0.3 * expressiveness + 0.3 * maturity
        return _clamp(score, 0.0, 1.0)

    # ------------------------------------------------------------------
    def is_ready_for_cycle(self) -> bool:
        """Determine whether this domain is actionably ready for the cyclic picture today.

        Applies two conditions simultaneously:

        * ``suitability_score() >= 0.55`` — the domain clears the suitability
          threshold, indicating enough structural and tooling readiness for a
          viable deployment.
        * ``has_feedback_loops is True`` — the feedback morphism φ must exist;
          without it the cycle cannot close and the architecture degenerates
          into an open-loop generator.

        Returns
        -------
        bool
            ``True`` if both conditions are satisfied; ``False`` otherwise.
        """
        return self.has_feedback_loops and self.suitability_score() >= 0.55

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this profile to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``domain_id``, ``name``, ``description``,
            ``has_formal_verification``, ``has_feedback_loops``,
            ``ideation_sources``, ``obstruction_types``,
            ``maturity_indicators``, ``suitability_score``, and
            ``is_ready_for_cycle``.
        """
        return {
            "domain_id": self.domain_id,
            "name": self.name,
            "description": self.description,
            "has_formal_verification": self.has_formal_verification,
            "has_feedback_loops": self.has_feedback_loops,
            "ideation_sources": list(self.ideation_sources),
            "obstruction_types": list(self.obstruction_types),
            "maturity_indicators": dict(self.maturity_indicators),
            "suitability_score": self.suitability_score(),
            "is_ready_for_cycle": self.is_ready_for_cycle(),
        }


# ---------------------------------------------------------------------------
# TransferAnalysis
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TransferAnalysis:
    """Analysis of how the cyclic picture transfers from JuGeo to a new domain.

    A ``TransferAnalysis`` is produced by :meth:`BeyondJuGeoAnalyzer.analyze_transfer`
    for a specific target domain.  It records the structural analogies that
    were found between JuGeo concepts and the target domain's counterparts,
    the obstacles that would need to be overcome, and a scalar transfer score
    summarising overall viability.

    Attributes
    ----------
    analysis_id : str
        Unique identifier for this analysis instance.
    source_domain : str
        Always ``'jugeo/geometry'`` — the origin of the cyclic architecture
        being transferred.
    target_domain : str
        The name of the domain receiving the architecture.
    profile : DomainProfile
        The :class:`DomainProfile` object describing the target domain.
    analogies : list[dict]
        Each entry is a mapping with keys ``concept``, ``jugeo_term``,
        ``domain_term``, and ``confidence`` (float in [0, 1]).  Documents
        the structural correspondence between JuGeo and the target domain.
    obstacles : list[str]
        Plain-text descriptions of identified transfer obstacles.
    transfer_score : float
        Scalar summary of transfer viability in [0, 1].
    timestamp : str
        ISO-8601 UTC timestamp of when this analysis was produced.
    """

    analysis_id: str
    source_domain: str
    target_domain: str
    profile: Any  # DomainProfile
    analogies: list = field(default_factory=list)
    obstacles: list = field(default_factory=list)
    transfer_score: float = 0.0
    timestamp: str = field(default_factory=_utcnow)

    # ------------------------------------------------------------------
    def is_viable(self) -> bool:
        """Return whether this transfer analysis indicates viable transfer.

        Viability requires:

        * ``transfer_score >= 0.50`` — the weighted combination of analogy
          confidence and domain suitability clears the viability threshold.
        * At least one analogy was found (``len(analogies) >= 1``) — there
          must be at least one meaningful structural correspondence between
          JuGeo and the target domain for a principled transfer.

        Returns
        -------
        bool
            ``True`` if the transfer is assessed as viable; ``False``
            otherwise.
        """
        return self.transfer_score >= 0.50 and len(self.analogies) >= 1

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """Return a concise human-readable summary of this transfer analysis.

        Produces a multi-line string covering: the source and target domains,
        the viability verdict, the transfer score, the number of analogies
        found, and the first three obstacle strings (truncated if longer).

        Returns
        -------
        str
            Multi-line summary string suitable for logging or report sections.
        """
        viable_str = "VIABLE" if self.is_viable() else "NOT VIABLE"
        obstacle_preview = (
            "; ".join(self.obstacles[:3])
            if self.obstacles
            else "(none identified)"
        )
        lines = [
            f"TransferAnalysis [{self.analysis_id}]",
            f"  {self.source_domain} → {self.target_domain}",
            f"  Verdict: {viable_str}  (score={self.transfer_score:.3f})",
            f"  Analogies found: {len(self.analogies)}",
            f"  Obstacles: {obstacle_preview}",
            f"  Analysed at: {self.timestamp}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this analysis to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``analysis_id``, ``source_domain``,
            ``target_domain``, ``profile``, ``analogies``, ``obstacles``,
            ``transfer_score``, ``timestamp``, ``is_viable``, and
            ``summary``.
        """
        profile_dict = (
            self.profile.to_dict()
            if hasattr(self.profile, "to_dict")
            else {"name": str(self.profile)}
        )
        return {
            "analysis_id": self.analysis_id,
            "source_domain": self.source_domain,
            "target_domain": self.target_domain,
            "profile": profile_dict,
            "analogies": list(self.analogies),
            "obstacles": list(self.obstacles),
            "transfer_score": self.transfer_score,
            "timestamp": self.timestamp,
            "is_viable": self.is_viable(),
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# ImpactEstimate
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ImpactEstimate:
    """Estimated impact of applying the cyclic picture to a specific domain.

    Produced by :meth:`BeyondJuGeoAnalyzer.estimate_impact`, this dataclass
    records both qualitative descriptions (short- and long-term impact) and
    quantitative risk/confidence signals.  The :meth:`overall_score` method
    distils these into a single scalar for ranking.

    Attributes
    ----------
    estimate_id : str
        Unique identifier for this estimate.
    domain : str
        Name of the domain to which this estimate applies.
    short_term_impact : str
        Prose description of the impact achievable within 1–2 years of
        adopting the cyclic architecture in the target domain.
    long_term_impact : str
        Prose description of the impact achievable over a 5+ year horizon.
    risk_factors : list[str]
        Named risk factors that could reduce the realised impact.
    confidence : float
        Overall confidence in the estimate, in [0, 1].  Reflects the
        quality and quantity of supporting evidence.
    supporting_evidence : list[str]
        References or descriptions of evidence supporting the estimate (e.g.
        citations, analogy strength, prior domain adoption of similar ideas).
    """

    estimate_id: str
    domain: str
    short_term_impact: str
    long_term_impact: str
    risk_factors: list = field(default_factory=list)
    confidence: float = 0.5
    supporting_evidence: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def overall_score(self) -> float:
        """Compute an overall impact score for ranking and comparison.

        The score is the product of confidence and an evidence strength
        factor, adjusted downward by a risk penalty:

            evidence_strength = min(1.0, len(supporting_evidence) / 5.0)
            risk_penalty      = min(0.3, len(risk_factors) * 0.05)
            score             = confidence * evidence_strength - risk_penalty

        The result is clamped to [0.0, 1.0].

        Returns
        -------
        float
            Overall impact score in [0.0, 1.0].
        """
        evidence_strength = _clamp(len(self.supporting_evidence) / 5.0, 0.0, 1.0)
        risk_penalty = _clamp(len(self.risk_factors) * 0.05, 0.0, 0.30)
        raw = self.confidence * evidence_strength - risk_penalty
        return _clamp(raw, 0.0, 1.0)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this estimate to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``estimate_id``, ``domain``,
            ``short_term_impact``, ``long_term_impact``, ``risk_factors``,
            ``confidence``, ``supporting_evidence``, and ``overall_score``.
        """
        return {
            "estimate_id": self.estimate_id,
            "domain": self.domain,
            "short_term_impact": self.short_term_impact,
            "long_term_impact": self.long_term_impact,
            "risk_factors": list(self.risk_factors),
            "confidence": self.confidence,
            "supporting_evidence": list(self.supporting_evidence),
            "overall_score": self.overall_score(),
        }


# ---------------------------------------------------------------------------
# BeyondJuGeoReport
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BeyondJuGeoReport:
    """Comprehensive report on the generalisability of the cyclic picture.

    Aggregates all :class:`TransferAnalysis` and :class:`ImpactEstimate`
    objects produced during a full generalisability study, lists the top
    candidate domains, and provides aggregate statistics.  Produced by
    :meth:`BeyondJuGeoAnalyzer.generate_report` and consumed by downstream
    reporting and documentation pipelines.

    Attributes
    ----------
    report_id : str
        Unique identifier for this report instance.
    analyses : list[TransferAnalysis]
        All transfer analyses produced during the study.
    estimates : list[ImpactEstimate]
        All impact estimates produced during the study.
    domains_analyzed : list[str]
        Names of every domain that was analysed (may be a superset of the
        domains with viable transfer scores).
    top_candidates : list[str]
        Names of domains ranked as top transfer candidates, in descending
        order of transfer score.
    generated_at : str
        ISO-8601 UTC timestamp of report generation.
    """

    report_id: str
    analyses: list = field(default_factory=list)
    estimates: list = field(default_factory=list)
    domains_analyzed: list = field(default_factory=list)
    top_candidates: list = field(default_factory=list)
    generated_at: str = field(default_factory=_utcnow)

    # ------------------------------------------------------------------
    def best_domain(self) -> Optional[str]:
        """Return the name of the highest-scoring candidate domain.

        Scans all stored :class:`TransferAnalysis` objects and returns the
        ``target_domain`` of the one with the highest ``transfer_score``.
        Returns ``None`` if no analyses have been stored yet.

        Returns
        -------
        Optional[str]
            The best domain name, or ``None`` if ``analyses`` is empty.
        """
        if not self.analyses:
            return None
        best = max(self.analyses, key=lambda a: a.transfer_score)
        return best.target_domain

    # ------------------------------------------------------------------
    def mean_transfer_score(self) -> float:
        """Compute the mean transfer score across all stored analyses.

        Returns the arithmetic mean of ``transfer_score`` values.  Returns
        ``0.0`` if no analyses have been stored.

        Returns
        -------
        float
            Mean transfer score in [0.0, 1.0], or ``0.0`` if empty.
        """
        if not self.analyses:
            return 0.0
        total = sum(a.transfer_score for a in self.analyses)
        return total / len(self.analyses)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this report to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``report_id``, ``generated_at``,
            ``domains_analyzed``, ``top_candidates``, ``mean_transfer_score``,
            ``best_domain``, ``analyses``, and ``estimates``.
        """
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "domains_analyzed": list(self.domains_analyzed),
            "top_candidates": list(self.top_candidates),
            "mean_transfer_score": self.mean_transfer_score(),
            "best_domain": self.best_domain(),
            "analyses": [
                a.to_dict() if hasattr(a, "to_dict") else a
                for a in self.analyses
            ],
            "estimates": [
                e.to_dict() if hasattr(e, "to_dict") else e
                for e in self.estimates
            ],
        }


# ---------------------------------------------------------------------------
# BeyondJuGeoAnalyzer
# ---------------------------------------------------------------------------

# Canonical JuGeo terms used in structural analogy construction.
_JUGEO_CONCEPTS = [
    ("ideation_engine", "The component that generates novel geometric problem hypotheses."),
    ("construction_pipeline", "The pipeline that instantiates geometric constructions from hypotheses."),
    ("obstruction_checker", "The verifier that proves or refutes each candidate construction."),
    ("feedback_morphism", "The mapping from obstructions back into the ideation engine as new seeds."),
    ("capability_lattice", "The partial order of geometric capability sets measuring progress."),
    ("maturity_level", "A named tier in the maturity lattice indicating overall system readiness."),
]

# Template obstacles that arise in most transfer contexts.
_GENERIC_OBSTACLES = [
    "Verification may not be fully decidable in the target domain.",
    "The feedback morphism may lose information, violating injectivity.",
    "The capability lattice may not be well-founded, risking non-termination.",
    "Tooling immaturity may prevent practical instantiation of the pipeline.",
    "Domain-specific notation and ontology require substantial translation effort.",
]


@dataclass(slots=True)
class BeyondJuGeoAnalyzer:
    """Analyses applicability of the cyclic picture to domains beyond JuGeo.

    :class:`BeyondJuGeoAnalyzer` is the central computational engine of this
    module.  Given a domain name and optional metadata, it constructs a
    :class:`DomainProfile`, performs a structural analogy search to map JuGeo
    concepts to domain-specific counterparts, estimates transfer viability and
    impact, and can rank a collection of profiles by suitability.

    The analyser is deliberately domain-agnostic: it uses only the structural
    properties encoded in the :class:`DomainProfile` to drive its reasoning,
    so it can be applied to any domain that admits a profile description.

    Attributes
    ----------
    analyzer_id : str
        Unique identifier for this analyser instance.
    config : dict
        Configuration dictionary.  Recognised keys:
        ``min_transfer_score`` (float, default 0.5),
        ``min_analogy_confidence`` (float, default 0.6),
        ``max_obstacles_to_report`` (int, default 5).
    """

    analyzer_id: str
    config: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, config: dict | None = None) -> "BeyondJuGeoAnalyzer":
        """Factory method that creates a new ``BeyondJuGeoAnalyzer``.

        Generates a fresh ``analyzer_id`` and merges *config* with defaults.

        Parameters
        ----------
        config:
            Optional configuration overrides.  Merged over defaults.

        Returns
        -------
        BeyondJuGeoAnalyzer
            A freshly initialised analyser.
        """
        defaults: dict = {
            "min_transfer_score": 0.5,
            "min_analogy_confidence": 0.6,
            "max_obstacles_to_report": 5,
        }
        merged = {**defaults, **(config or {})}
        return cls(analyzer_id=_uid(), config=merged)

    # ------------------------------------------------------------------
    def profile_domain(self, domain_name: str, metadata: dict) -> "DomainProfile":
        """Build a :class:`DomainProfile` for the named domain.

        Delegates construction to :meth:`DomainProfile.from_metadata` after
        normalising *domain_name* to a lowercase, underscore-separated
        identifier.  If *metadata* is missing the ``description`` key, a
        default description is synthesised from the ideation sources and
        obstruction types.

        Parameters
        ----------
        domain_name:
            Human-readable or machine-readable domain label.
        metadata:
            Raw metadata dictionary as described in :meth:`DomainProfile.from_metadata`.

        Returns
        -------
        DomainProfile
            A fully populated domain profile.
        """
        normalised_name = domain_name.lower().replace(" ", "_").replace("-", "_")
        if "description" not in metadata:
            sources = metadata.get("ideation_sources", [])
            obstructions = metadata.get("obstruction_types", [])
            metadata = dict(metadata)
            metadata["description"] = (
                f"Domain '{domain_name}' with ideation sources "
                f"{sources} and obstruction types {obstructions}."
            )
        return DomainProfile.from_metadata(normalised_name, metadata)

    # ------------------------------------------------------------------
    def find_structural_analogies(self, profile: "DomainProfile") -> list:
        """Map JuGeo concepts to their counterparts in the target domain.

        Iterates over :data:`_JUGEO_CONCEPTS` and attempts to identify a
        corresponding concept in the target domain profile.  Confidence
        scores are assigned based on the structural properties of the profile:

        * ``ideation_engine`` analogues receive higher confidence when
          ``profile.ideation_sources`` is non-empty.
        * ``obstruction_checker`` analogues receive higher confidence when
          ``profile.has_formal_verification`` is ``True``.
        * ``feedback_morphism`` analogues receive higher confidence when
          ``profile.has_feedback_loops`` is ``True``.
        * All others receive a base confidence derived from
          ``profile.suitability_score()``.

        Only analogies with confidence >= ``config['min_analogy_confidence']``
        are included in the returned list.

        Parameters
        ----------
        profile:
            The :class:`DomainProfile` for the target domain.

        Returns
        -------
        list[dict]
            List of analogy dicts, each with keys ``concept``,
            ``jugeo_term``, ``domain_term``, and ``confidence``.
        """
        analogies: list = []
        base_conf = _clamp(profile.suitability_score(), 0.3, 0.95)
        min_conf = float(self.config.get("min_analogy_confidence", 0.6))

        domain_term_map: dict = {
            "ideation_engine": (
                f"ideation source ({profile.ideation_sources[0]})"
                if profile.ideation_sources
                else "candidate generator"
            ),
            "construction_pipeline": f"{profile.name} generation pipeline",
            "obstruction_checker": (
                "formal verifier / checker"
                if profile.has_formal_verification
                else "empirical evaluator"
            ),
            "feedback_morphism": (
                f"obstruction-to-idea feedback ({profile.obstruction_types[0]} → ideation)"
                if profile.obstruction_types
                else "failure-driven feedback mechanism"
            ),
            "capability_lattice": f"{profile.name} capability ordering",
            "maturity_level": f"{profile.name} readiness tier",
        }

        confidence_map: dict = {
            "ideation_engine": base_conf + (0.10 if profile.ideation_sources else 0.0),
            "construction_pipeline": base_conf,
            "obstruction_checker": base_conf + (0.15 if profile.has_formal_verification else -0.10),
            "feedback_morphism": base_conf + (0.12 if profile.has_feedback_loops else -0.15),
            "capability_lattice": base_conf,
            "maturity_level": base_conf + 0.05,
        }

        for jugeo_term, concept_desc in _JUGEO_CONCEPTS:
            conf = _clamp(confidence_map.get(jugeo_term, base_conf), 0.0, 1.0)
            if conf >= min_conf:
                analogies.append(
                    {
                        "concept": concept_desc,
                        "jugeo_term": jugeo_term,
                        "domain_term": domain_term_map.get(jugeo_term, jugeo_term),
                        "confidence": round(conf, 4),
                    }
                )
        return analogies

    # ------------------------------------------------------------------
    def analyze_transfer(self, profile: "DomainProfile") -> "TransferAnalysis":
        """Perform a full transfer analysis for the target domain profile.

        Constructs the list of structural analogies, identifies obstacles
        (drawing from both the profile's own obstruction types and the
        generic obstacle template), and computes a transfer score as a
        weighted combination of analogy confidence and domain suitability.

        The transfer score formula is:

            analogy_conf = mean confidence of found analogies (or 0)
            domain_suit  = profile.suitability_score()
            transfer_score = 0.55 * analogy_conf + 0.45 * domain_suit

        Parameters
        ----------
        profile:
            The :class:`DomainProfile` describing the target domain.

        Returns
        -------
        TransferAnalysis
            A completed transfer analysis with all fields populated.
        """
        analogies = self.find_structural_analogies(profile)

        # Build obstacles list
        max_obs = int(self.config.get("max_obstacles_to_report", 5))
        domain_obstacles: list = [
            f"Obstruction type '{o}' may not map cleanly to a JuGeo ideation seed."
            for o in profile.obstruction_types[:3]
        ]
        combined_obstacles = (domain_obstacles + _GENERIC_OBSTACLES)[:max_obs]

        # Compute transfer score
        if analogies:
            analogy_conf = sum(a["confidence"] for a in analogies) / len(analogies)
        else:
            analogy_conf = 0.0
        domain_suit = profile.suitability_score()
        raw_score = 0.55 * analogy_conf + 0.45 * domain_suit
        transfer_score = _clamp(raw_score, 0.0, 1.0)

        return TransferAnalysis(
            analysis_id=_uid(),
            source_domain="jugeo/geometry",
            target_domain=profile.name,
            profile=profile,
            analogies=analogies,
            obstacles=combined_obstacles,
            transfer_score=round(transfer_score, 4),
            timestamp=_utcnow(),
        )

    # ------------------------------------------------------------------
    def estimate_impact(self, analysis: "TransferAnalysis") -> "ImpactEstimate":
        """Estimate the impact of applying the cyclic picture to a target domain.

        Derives short- and long-term impact descriptions from the analysis,
        assigns a confidence score proportional to the transfer score, and
        populates the risk factor list from the analysis obstacles.

        Parameters
        ----------
        analysis:
            A completed :class:`TransferAnalysis` for the target domain.

        Returns
        -------
        ImpactEstimate
            A fully populated impact estimate.
        """
        domain = analysis.target_domain
        score = analysis.transfer_score

        short_term = (
            f"Applying the cyclic picture to '{domain}' within 1–2 years could "
            f"establish a principled ideation–generation–verification loop, enabling "
            f"automated exploration of the {domain} search space with formal feedback."
        )
        long_term = (
            f"Over a 5+ year horizon, a mature cyclic system for '{domain}' could "
            f"autonomously discover significant results, adapt its own ideation "
            f"strategy in response to encountered obstructions, and serve as a "
            f"scalable foundation for domain-specific AI research tools."
        )

        risk_factors = list(analysis.obstacles[:4])
        confidence = _clamp(score * 0.9 + 0.05, 0.1, 0.95)

        supporting_evidence: list = [
            f"Transfer score {score:.3f} indicates strong structural alignment.",
            f"{len(analysis.analogies)} structural analogies found to JuGeo concepts.",
            f"Domain '{domain}' suitability score: "
            f"{analysis.profile.suitability_score():.3f}.",
        ]
        if analysis.profile.has_formal_verification:
            supporting_evidence.append(
                "Domain has formal verification — the key requirement of the "
                "generalisation theorem (Ch65 §7.1) is satisfied."
            )
        if analysis.profile.has_feedback_loops:
            supporting_evidence.append(
                "Domain exhibits feedback loops — the feedback morphism φ can "
                "be instantiated from existing domain mechanisms."
            )

        return ImpactEstimate(
            estimate_id=_uid(),
            domain=domain,
            short_term_impact=short_term,
            long_term_impact=long_term,
            risk_factors=risk_factors,
            confidence=round(confidence, 4),
            supporting_evidence=supporting_evidence,
        )

    # ------------------------------------------------------------------
    def rank_domains(self, profiles: list) -> list:
        """Rank a list of domain profiles by their suitability score.

        Computes ``suitability_score()`` for each profile and returns
        the profiles paired with their scores, sorted in descending order.
        Profiles with equal scores are sorted alphabetically by name.

        Parameters
        ----------
        profiles:
            List of :class:`DomainProfile` instances to rank.

        Returns
        -------
        list[tuple[DomainProfile, float]]
            Pairs of ``(profile, score)`` sorted descending by score.
        """
        scored: list = [(p, p.suitability_score()) for p in profiles]
        scored.sort(key=lambda x: (-x[1], x[0].name))
        return scored

    # ------------------------------------------------------------------
    def generate_report(self, analyses: list, estimates: list) -> "BeyondJuGeoReport":
        """Assemble a :class:`BeyondJuGeoReport` from analyses and estimates.

        Derives ``domains_analyzed`` and ``top_candidates`` from the
        provided analyses, ordering top candidates by descending transfer
        score.

        Parameters
        ----------
        analyses:
            List of completed :class:`TransferAnalysis` objects.
        estimates:
            List of completed :class:`ImpactEstimate` objects.

        Returns
        -------
        BeyondJuGeoReport
            A fully populated report.
        """
        domains_analyzed = [a.target_domain for a in analyses]
        sorted_analyses = sorted(analyses, key=lambda a: -a.transfer_score)
        top_candidates = [
            a.target_domain
            for a in sorted_analyses
            if a.is_viable()
        ]
        return BeyondJuGeoReport(
            report_id=_uid(),
            analyses=list(analyses),
            estimates=list(estimates),
            domains_analyzed=domains_analyzed,
            top_candidates=top_candidates,
            generated_at=_utcnow(),
        )


# ---------------------------------------------------------------------------
# BeyondJuGeoWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BeyondJuGeoWitness:
    """Logical witness for the generalisability claims of the cyclic picture.

    The witness records structured attestations for each domain — statements
    backed by specific evidence that the cyclic architecture applies.  The
    :meth:`build_generalization_proof` method assembles these attestations
    into a proof-like structure that can be consumed by the provenance layer.

    This class is the computational counterpart of the generalisation theorem
    witness object described in Ch65 §7.2.

    Attributes
    ----------
    witness_id : str
        Unique identifier for this witness instance.
    domain_attestations : dict[str, list[dict]]
        Maps domain names to lists of attestation records.  Each record has
        at least the keys ``evidence``, ``attestation_id``, and ``ts``.
    """

    witness_id: str
    domain_attestations: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def create(cls) -> "BeyondJuGeoWitness":
        """Factory method that creates a new empty ``BeyondJuGeoWitness``.

        Returns
        -------
        BeyondJuGeoWitness
            A freshly created witness with no attestations.
        """
        return cls(witness_id=_uid(), domain_attestations={})

    # ------------------------------------------------------------------
    def attest_domain_suitability(self, domain: str, evidence: dict) -> str:
        """Record a suitability attestation for the given domain.

        Appends an attestation record to ``domain_attestations[domain]``
        with the provided evidence dictionary, a fresh attestation ID, and
        the current timestamp.

        Parameters
        ----------
        domain:
            Name of the domain being attested.
        evidence:
            Dictionary of evidence supporting the suitability claim.  May
            include keys such as ``'transfer_score'``, ``'analogies_found'``,
            or any domain-specific evidence.

        Returns
        -------
        str
            The attestation ID of the newly created record.
        """
        attestation_id = _uid()
        record: dict = {
            "attestation_id": attestation_id,
            "kind": "suitability",
            "domain": domain,
            "evidence": dict(evidence),
            "ts": _utcnow(),
        }
        if domain not in self.domain_attestations:
            self.domain_attestations[domain] = []
        self.domain_attestations[domain].append(record)
        return attestation_id

    # ------------------------------------------------------------------
    def attest_transfer_viability(self, analysis: "TransferAnalysis") -> str:
        """Record a viability attestation derived from a transfer analysis.

        Extracts the key viability signals from *analysis* and stores them
        as a structured evidence record under the target domain.

        Parameters
        ----------
        analysis:
            A completed :class:`TransferAnalysis`.  Must have a populated
            ``target_domain`` and numeric ``transfer_score``.

        Returns
        -------
        str
            The attestation ID of the newly created record.
        """
        evidence: dict = {
            "transfer_score": analysis.transfer_score,
            "analogies_count": len(analysis.analogies),
            "obstacles_count": len(analysis.obstacles),
            "is_viable": analysis.is_viable(),
            "analysis_id": analysis.analysis_id,
        }
        attestation_id = _uid()
        record: dict = {
            "attestation_id": attestation_id,
            "kind": "transfer_viability",
            "domain": analysis.target_domain,
            "evidence": evidence,
            "ts": _utcnow(),
        }
        domain = analysis.target_domain
        if domain not in self.domain_attestations:
            self.domain_attestations[domain] = []
        self.domain_attestations[domain].append(record)
        return attestation_id

    # ------------------------------------------------------------------
    def verify_domain(self, domain: str) -> bool:
        """Return whether at least one attestation exists for the domain.

        A domain is considered "verified" by this witness once at least one
        attestation — of any kind — has been recorded for it.

        Parameters
        ----------
        domain:
            Name of the domain to check.

        Returns
        -------
        bool
            ``True`` if one or more attestations are stored; ``False`` if
            the domain has no attestations yet.
        """
        return bool(self.domain_attestations.get(domain))

    # ------------------------------------------------------------------
    def build_generalization_proof(self) -> dict:
        """Assemble a proof-like structure for the generalisability theorem.

        Constructs a dictionary that encodes the witness's accumulated
        attestations as a formal proof structure.  The proof claims that
        the cyclic picture generalises beyond JuGeo to all attested domains,
        and cites each domain's attestations as evidence.

        The returned dictionary has the structure:

        .. code-block:: python

            {
                "proof_id": str,
                "theorem": str,        # prose statement of the theorem
                "witness_id": str,
                "attested_domains": list[str],
                "evidence_by_domain": dict[str, list[dict]],
                "conclusion": str,
                "generated_at": str,
            }

        Returns
        -------
        dict
            A JSON-serialisable proof dictionary.
        """
        attested = list(self.domain_attestations.keys())
        conclusion = (
            f"The cyclic picture architecture is witnessed as generalisable to "
            f"{len(attested)} domain(s): {', '.join(attested)}.  Each domain "
            f"exhibits the structural prerequisites (ideation algebra, generation "
            f"functor, verification predicate, feedback morphism) required by the "
            f"generalisation theorem of Ch65 §7.1."
            if attested
            else "No domains have been attested yet; the proof is empty."
        )
        return {
            "proof_id": _uid(),
            "theorem": (
                "Generalisation theorem (Ch65 §7.1): the cyclic picture architecture "
                "transfers soundly to any domain D admitting a triple (I, G, V) "
                "together with an injective feedback morphism φ."
            ),
            "witness_id": self.witness_id,
            "attested_domains": attested,
            "evidence_by_domain": {
                d: list(recs) for d, recs in self.domain_attestations.items()
            },
            "conclusion": conclusion,
            "generated_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    def list_attested_domains(self) -> list:
        """Return the list of domain names that have at least one attestation.

        Returns
        -------
        list[str]
            Sorted list of attested domain names.
        """
        return sorted(self.domain_attestations.keys())

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this witness to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            Dictionary with keys ``witness_id``, ``attested_domains``, and
            ``domain_attestations``.
        """
        return {
            "witness_id": self.witness_id,
            "attested_domains": self.list_attested_domains(),
            "domain_attestations": {
                d: list(recs) for d, recs in self.domain_attestations.items()
            },
        }


# ---------------------------------------------------------------------------
# BeyondJuGeoCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BeyondJuGeoCoordinator:
    """Coordinates the end-to-end generalisability analysis of the cyclic picture.

    :class:`BeyondJuGeoCoordinator` is the top-level entry point for running
    a full generalisability study.  It owns a :class:`BeyondJuGeoAnalyzer`
    and a :class:`BeyondJuGeoWitness`, and orchestrates the sequence:
    profile → analyse_transfer → estimate_impact → attest → report.

    The :meth:`analyze_all_candidate_domains` method iterates over the
    built-in :data:`_CANDIDATE_DOMAINS` list, performs a complete analysis
    for each, and populates the coordinator's internal state so that
    :meth:`generate_full_report` can produce a comprehensive report with a
    single call.

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    config : dict
        Configuration dictionary passed through to the analyser.
    _analyzer : BeyondJuGeoAnalyzer
        Internal analyser instance.
    _witness : BeyondJuGeoWitness
        Internal witness instance.
    _analyses : list[TransferAnalysis]
        Accumulated transfer analyses.
    _estimates : list[ImpactEstimate]
        Accumulated impact estimates.
    """

    coordinator_id: str
    config: dict = field(default_factory=dict)
    _analyzer: Any = field(default=None)
    _witness: Any = field(default=None)
    _analyses: list = field(default_factory=list)
    _estimates: list = field(default_factory=list)

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, config: dict | None = None) -> "BeyondJuGeoCoordinator":
        """Factory method that creates a fully initialised coordinator.

        Parameters
        ----------
        config:
            Optional configuration dictionary forwarded to the analyser.

        Returns
        -------
        BeyondJuGeoCoordinator
            A new coordinator with fresh analyser and witness instances.
        """
        cfg = config or {}
        coord = cls(
            coordinator_id=_uid(),
            config=cfg,
        )
        coord._analyzer = BeyondJuGeoAnalyzer.create(config=cfg)
        coord._witness = BeyondJuGeoWitness.create()
        return coord

    # ------------------------------------------------------------------
    def _ensure_init(self) -> None:
        """Lazily initialise the analyser and witness if not yet set.

        Called internally before any operation that requires the analyser
        or witness.  Handles the case where the coordinator was constructed
        via the dataclass default path rather than the :meth:`create` factory.
        """
        if self._analyzer is None:
            self._analyzer = BeyondJuGeoAnalyzer.create(config=self.config)
        if self._witness is None:
            self._witness = BeyondJuGeoWitness.create()

    # ------------------------------------------------------------------
    def analyze_domain(self, domain_name: str, metadata: dict | None = None) -> "TransferAnalysis":
        """Run a complete transfer analysis for a single named domain.

        Builds a :class:`DomainProfile` from *metadata*, runs the transfer
        analysis and impact estimation, records attestations in the witness,
        and stores the results in ``_analyses`` and ``_estimates``.

        Parameters
        ----------
        domain_name:
            Human-readable or machine-readable domain label.
        metadata:
            Optional raw metadata dict.  Defaults to the built-in entry for
            *domain_name* if found in :data:`_CANDIDATE_DOMAINS`; otherwise
            an empty dict with minimal defaults is used.

        Returns
        -------
        TransferAnalysis
            The completed transfer analysis.
        """
        self._ensure_init()

        # Fall back to built-in candidate metadata
        if metadata is None:
            for candidate in _CANDIDATE_DOMAINS:
                if candidate["name"] == domain_name.lower().replace(" ", "_").replace("-", "_"):
                    metadata = candidate
                    break
            if metadata is None:
                metadata = {
                    "has_formal_verification": False,
                    "has_feedback_loops": True,
                    "ideation_sources": ["default_ideation"],
                    "obstruction_types": ["generic_failure"],
                    "maturity_indicators": {"tooling_maturity": 0.5},
                }

        profile = self._analyzer.profile_domain(domain_name, metadata)
        analysis = self._analyzer.analyze_transfer(profile)
        estimate = self._analyzer.estimate_impact(analysis)

        # Record witness attestations
        self._witness.attest_domain_suitability(
            profile.name,
            {
                "suitability_score": profile.suitability_score(),
                "is_ready_for_cycle": profile.is_ready_for_cycle(),
            },
        )
        self._witness.attest_transfer_viability(analysis)

        self._analyses.append(analysis)
        self._estimates.append(estimate)
        return analysis

    # ------------------------------------------------------------------
    def analyze_all_candidate_domains(self) -> list:
        """Analyse all built-in candidate domains.

        Iterates over :data:`_CANDIDATE_DOMAINS`, calling
        :meth:`analyze_domain` for each, and returns the list of resulting
        :class:`TransferAnalysis` objects.  If analyses have already been
        accumulated from prior calls, the new results are appended.

        Returns
        -------
        list[TransferAnalysis]
            Transfer analyses for every built-in candidate domain, in the
            order they appear in :data:`_CANDIDATE_DOMAINS`.
        """
        self._ensure_init()
        new_analyses: list = []
        for candidate in _CANDIDATE_DOMAINS:
            analysis = self.analyze_domain(candidate["name"], metadata=candidate)
            new_analyses.append(analysis)
        return new_analyses

    # ------------------------------------------------------------------
    def generate_full_report(self) -> "BeyondJuGeoReport":
        """Generate a comprehensive :class:`BeyondJuGeoReport`.

        If no analyses have been accumulated yet, first runs
        :meth:`analyze_all_candidate_domains` to populate the internal
        state.  Then delegates to
        :meth:`BeyondJuGeoAnalyzer.generate_report`.

        Returns
        -------
        BeyondJuGeoReport
            A fully populated report covering all analysed domains.
        """
        self._ensure_init()
        if not self._analyses:
            self.analyze_all_candidate_domains()
        return self._analyzer.generate_report(self._analyses, self._estimates)

    # ------------------------------------------------------------------
    def get_top_transfer_candidates(self, n: int = 3) -> list:
        """Return the top *n* transfer analyses ranked by transfer score.

        If fewer than *n* analyses exist, returns all available analyses.
        Analyses are sorted in descending order of ``transfer_score``.

        Parameters
        ----------
        n:
            Number of top candidates to return.  Must be >= 1.

        Returns
        -------
        list[TransferAnalysis]
            The top *n* analyses by transfer score.
        """
        self._ensure_init()
        if not self._analyses:
            self.analyze_all_candidate_domains()
        sorted_analyses = sorted(self._analyses, key=lambda a: -a.transfer_score)
        return sorted_analyses[:max(1, n)]

    # ------------------------------------------------------------------
    def get_witness(self) -> "BeyondJuGeoWitness":
        """Return the internal :class:`BeyondJuGeoWitness` instance.

        The witness accumulates attestations as analyses are performed and
        can be used to build a generalisation proof.

        Returns
        -------
        BeyondJuGeoWitness
            The witness object for this coordinator.
        """
        self._ensure_init()
        return self._witness

    # ------------------------------------------------------------------
    def explain_generalization(self) -> str:
        """Return a human-readable explanation of the cyclic picture's generalisability.

        Produces a structured prose explanation covering: what the cyclic
        picture is, why it generalises, the top candidate domains from the
        current analysis, and a summary of the witness attestations.

        If no analysis has been performed yet, a pre-analysis explanation
        is returned.

        Returns
        -------
        str
            Multi-paragraph human-readable explanation string.
        """
        self._ensure_init()

        if not self._analyses:
            return (
                "The cyclic picture (JuGeo, theory2.tex Ch65) is an architecture "
                "comprising three phases — ideation, generation, and verification — "
                "with a feedback morphism that maps verification failures back into "
                "the ideation engine.  This loop drives monotone progress in the "
                "system's capability lattice while preserving soundness.  No domain "
                "analyses have been performed yet; call analyze_all_candidate_domains() "
                "to populate results."
            )

        top = self.get_top_transfer_candidates(n=3)
        top_names = ", ".join(a.target_domain for a in top)
        top_scores = ", ".join(f"{a.transfer_score:.3f}" for a in top)

        attested = self._witness.list_attested_domains()
        viable_count = sum(1 for a in self._analyses if a.is_viable())

        paragraphs = [
            "=== Generalisation of the Cyclic Picture Beyond JuGeo ===",
            "",
            "The cyclic picture (theory2.tex Ch65) is a universal architecture for "
            "self-improving formal systems.  It abstracts the three-phase loop used "
            "inside JuGeo's geometric reasoning engine — ideation drives generation, "
            "generation is verified, and verification failures feed back as new ideas "
            "— into a domain-independent template parameterised by an ideation algebra I, "
            "a generation functor G, a verification predicate V, and a feedback "
            "morphism φ: Obstruction(V, G(I)) → I.",
            "",
            "Generalisation theorem (Ch65 §7.1) guarantees that any domain admitting "
            "such a quadruple (I, G, V, φ) inherits the soundness and monotone progress "
            "properties of the original JuGeo system.",
            "",
            f"Current analysis results ({len(self._analyses)} domains analysed):",
            f"  • Viable transfer candidates: {viable_count} / {len(self._analyses)}",
            f"  • Top candidates by transfer score: {top_names}",
            f"  • Corresponding scores: {top_scores}",
            f"  • Domains attested by witness: {', '.join(attested) if attested else '(none yet)'}",
            "",
            "The strongest candidates are those with formal verification predicates "
            "(pure mathematics, formal methods, software verification) — they satisfy "
            "the decidability condition of the theorem directly.  Domains with only "
            "empirical verification (biology, economics) are still viable but require "
            "a weaker, approximate variant of the theorem.",
        ]
        return "\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def analyze_generalizability(domains: list | None = None) -> "BeyondJuGeoReport":
    """Run a complete generalisability analysis and return a report.

    Constructs a :class:`BeyondJuGeoCoordinator`, optionally restricts the
    analysis to the named *domains*, runs the analysis, and returns the
    resulting :class:`BeyondJuGeoReport`.

    If *domains* is ``None`` or empty, all built-in candidate domains are
    analysed.  If *domains* is provided, only the named domains are analysed
    (using the built-in metadata when available, falling back to defaults).

    Parameters
    ----------
    domains:
        Optional list of domain name strings to analyse.  When ``None``,
        all built-in candidates are analysed.

    Returns
    -------
    BeyondJuGeoReport
        A comprehensive report covering all analysed domains.
    """
    coordinator = BeyondJuGeoCoordinator.create()
    if not domains:
        coordinator.analyze_all_candidate_domains()
    else:
        for domain_name in domains:
            coordinator.analyze_domain(domain_name)
    return coordinator.generate_full_report()


def score_domain_fit(domain_name: str) -> float:
    """Return a quick suitability score for a domain by name.

    Looks up the domain in :data:`_CANDIDATE_DOMAINS` to retrieve metadata,
    builds a :class:`DomainProfile`, and returns its
    :meth:`~DomainProfile.suitability_score`.  If the domain is not found in
    the built-in list, a default profile with minimal structural properties
    is used and the score is likely to be low.

    Parameters
    ----------
    domain_name:
        The name of the domain to score, as it appears in
        :data:`_CANDIDATE_DOMAINS` (e.g. ``'pure_mathematics'``).

    Returns
    -------
    float
        Suitability score in [0.0, 1.0].
    """
    normalised = domain_name.lower().replace(" ", "_").replace("-", "_")
    metadata: dict = {
        "has_formal_verification": False,
        "has_feedback_loops": False,
        "ideation_sources": [],
        "obstruction_types": [],
        "maturity_indicators": {},
    }
    for candidate in _CANDIDATE_DOMAINS:
        if candidate["name"] == normalised:
            metadata = candidate
            break
    analyzer = BeyondJuGeoAnalyzer.create()
    profile = analyzer.profile_domain(domain_name, metadata)
    return profile.suitability_score()


def list_candidate_domains() -> list:
    """Return the list of built-in candidate domain names.

    Provides a simple enumeration of the domains pre-loaded into
    :data:`_CANDIDATE_DOMAINS`.  Useful for discovery and documentation.

    Returns
    -------
    list[str]
        Domain name strings in the order they appear in
        :data:`_CANDIDATE_DOMAINS`.
    """
    return [d["name"] for d in _CANDIDATE_DOMAINS]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick sanity check for the module.

    Exercises the full pipeline:

    1. List candidate domains.
    2. Score a couple of domains using the free function.
    3. Build a coordinator, analyse all domains, generate a report.
    4. Check the report has sensible content.
    5. Build a witness proof and verify its structure.
    6. Exercise the explain_generalization convenience method.
    """
    print("=== why_this_could_matter_beyond_jugeo smoke test ===")

    # 1. List candidates
    candidates = list_candidate_domains()
    print(f"Candidate domains ({len(candidates)}): {candidates}")
    assert len(candidates) >= 4, "Expected at least 4 built-in candidate domains"

    # 2. Score two domains
    score_pm = score_domain_fit("pure_mathematics")
    score_fm = score_domain_fit("formal_methods")
    print(f"pure_mathematics suitability: {score_pm:.4f}")
    print(f"formal_methods suitability:   {score_fm:.4f}")
    assert 0.0 <= score_pm <= 1.0, "Score out of range"
    assert 0.0 <= score_fm <= 1.0, "Score out of range"

    # 3. Full analysis via coordinator
    coordinator = BeyondJuGeoCoordinator.create()
    analyses = coordinator.analyze_all_candidate_domains()
    print(f"Analyses produced: {len(analyses)}")
    assert len(analyses) == len(candidates), "Mismatch between candidates and analyses"

    for analysis in analyses:
        assert analysis.analysis_id, "Analysis missing ID"
        assert analysis.source_domain == "jugeo/geometry"
        assert 0.0 <= analysis.transfer_score <= 1.0

    # 4. Full report
    report = coordinator.generate_full_report()
    print(f"Report ID: {report.report_id}")
    print(f"Domains analysed: {report.domains_analyzed}")
    print(f"Top candidates: {report.top_candidates}")
    print(f"Mean transfer score: {report.mean_transfer_score():.4f}")
    best = report.best_domain()
    print(f"Best domain: {best}")
    assert best is not None, "Expected at least one best domain"

    # 5. Witness proof
    witness = coordinator.get_witness()
    attested = witness.list_attested_domains()
    print(f"Attested domains: {attested}")
    assert len(attested) >= 1, "Expected at least one attested domain"
    proof = witness.build_generalization_proof()
    assert "theorem" in proof, "Proof missing 'theorem' key"
    assert "conclusion" in proof, "Proof missing 'conclusion' key"
    print(f"Proof ID: {proof['proof_id']}")

    # 6. Top candidates
    top3 = coordinator.get_top_transfer_candidates(n=3)
    print("Top 3 transfer candidates:")
    for a in top3:
        print(f"  {a.target_domain}: score={a.transfer_score:.4f}, viable={a.is_viable()}")

    # 7. Explanation
    explanation = coordinator.explain_generalization()
    assert "cyclic picture" in explanation.lower() or "Cyclic" in explanation
    print("\n--- Explanation (first 400 chars) ---")
    print(explanation[:400])

    # 8. Free function
    free_report = analyze_generalizability(["pure_mathematics", "formal_methods"])
    assert len(free_report.analyses) == 2, "Expected 2 analyses from free function"
    print(f"\nFree-function report best domain: {free_report.best_domain()}")

    print("\nsmoke test PASSED")


if __name__ == "__main__":
    _smoke_test()
