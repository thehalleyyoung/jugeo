"""Synthesis orchestrator — integrates synthesis frontier with JuGeo orchestration.

This module bridges the synthesis_frontier subsystem (which discovers new
mathematical fields and generates papers) with JuGeo's main orchestration
pipeline (controller, frontier, fleet management).

The synthesis orchestrator:
1. Monitors the judgment site for "theory deficit" signals
   (high obstruction density + no code repair available)
2. Triggers synthesis frontier runs when theory gaps are detected
3. Feeds synthesis results back as new propositions/theorems into the
   evidence pipeline (trust tier: PROPOSAL)
4. Manages the lifecycle of synthesis campaigns

# copilot: synthesis orchestrator — theory deficit → synthesis frontier → new theorems
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import dataclasses
import datetime
import json
import logging
import math
import os
import pathlib
import re
import time
import typing
import uuid

from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional, Union

# ---------------------------------------------------------------------------
# Optional third-party / internal imports
# We wrap every internal import in a try/except so the module remains
# importable even when the full JuGeo package is not installed.  The stubs
# below provide just enough structure for type annotations and basic tests.
# ---------------------------------------------------------------------------

# --- synthesis_frontier.models ---
try:
    from jugeo.ideation.synthesis_frontier.models import (  # type: ignore[import]
        FieldNode,
        PropositionRecord,
        TournamentState,
        SynthesisPair,
    )
    _FRONTIER_MODELS_AVAILABLE = True
except ImportError:
    _FRONTIER_MODELS_AVAILABLE = False

    # Minimal stubs so the rest of this file type-checks without errors.
    class FieldNode:  # type: ignore[no-redef]
        """Stub for jugeo.ideation.synthesis_frontier.models.FieldNode."""
        name: str = ""
        propositions: list = dataclasses.field(default_factory=list)

    class PropositionRecord:  # type: ignore[no-redef]
        """Stub for jugeo.ideation.synthesis_frontier.models.PropositionRecord."""
        prop_id: str = ""
        statement: str = ""
        field_name: str = ""
        confidence: float = 0.5

    class TournamentState:  # type: ignore[no-redef]
        """Stub for jugeo.ideation.synthesis_frontier.models.TournamentState."""
        pass

    class SynthesisPair:  # type: ignore[no-redef]
        """Stub for jugeo.ideation.synthesis_frontier.models.SynthesisPair."""
        pass

# --- synthesis_frontier.pipeline ---
try:
    from jugeo.ideation.synthesis_frontier.pipeline import (  # type: ignore[import]
        SynthesisFrontierPipeline,
        PipelineConfig,
        PipelineResult,
    )
    _FRONTIER_PIPELINE_AVAILABLE = True
except ImportError:
    _FRONTIER_PIPELINE_AVAILABLE = False

    class PipelineConfig:  # type: ignore[no-redef]
        """Stub for jugeo.ideation.synthesis_frontier.pipeline.PipelineConfig."""
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class PipelineResult:  # type: ignore[no-redef]
        """Stub for jugeo.ideation.synthesis_frontier.pipeline.PipelineResult."""
        fields: list = []
        paper: Any = None
        rounds_completed: int = 0
        propositions_total: int = 0
        metaphors_total: int = 0

    class SynthesisFrontierPipeline:  # type: ignore[no-redef]
        """Stub for jugeo.ideation.synthesis_frontier.pipeline.SynthesisFrontierPipeline."""
        def __init__(self, config: Any = None) -> None:
            self.config = config

        def run(self) -> PipelineResult:
            """Return a stub result."""
            return PipelineResult()

# --- evidence.trust ---
try:
    from jugeo.evidence.trust import TrustLevel, TrustTier  # type: ignore[import]
    _EVIDENCE_TRUST_AVAILABLE = True
except ImportError:
    _EVIDENCE_TRUST_AVAILABLE = False

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustLevel."""
        ORACLE_PROPOSED: str = "oracle_proposed"
        VERIFIED: str = "verified"
        UNVERIFIED: str = "unverified"

    class TrustTier:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustTier."""
        PROPOSAL: str = "PROPOSAL"
        VERIFIED: str = "VERIFIED"

# --- evidence.channels ---
try:
    from jugeo.evidence.channels import (  # type: ignore[import]
        EvidenceChannel,
        EvidenceRecord,
    )
    _EVIDENCE_CHANNELS_AVAILABLE = True
except ImportError:
    _EVIDENCE_CHANNELS_AVAILABLE = False

    class EvidenceChannel:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.channels.EvidenceChannel."""
        ORACLE_PROPOSED: str = "ORACLE_PROPOSED"

    class EvidenceRecord:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.channels.EvidenceRecord."""
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Standard Python logger for this module.  All diagnostic output is routed
#: through the logging framework so callers can redirect it as needed.
_LOGGER = logging.getLogger(__name__)

#: The canonical trust-level string applied to every proposition that comes
#: out of the synthesis frontier.  This keeps downstream consumers consistent
#: even if the TrustLevel enum changes in the future.
#:
#: theory2.tex §252: theory deficits arise when the obstruction density
#: exceeds a critical threshold and no code-level patch can resolve the gap.
#: New mathematical fields generated in response to such deficits are
#: initially classified as "oracle proposed" until peer validation occurs.
_CANONICAL_TRUST_LEVEL: str = "oracle_proposed"

#: The canonical trust-tier string — one level above UNVERIFIED but below
#: VERIFIED.  PROPOSAL signals that a human or downstream validator should
#: review the proposition before promoting it.
_CANONICAL_TRUST_TIER: str = "PROPOSAL"

#: Hard cap on how many obstruction patterns we load into memory at once.
#: Prevents runaway pattern libraries from degrading detection performance.
_MAX_OBSTRUCTION_PATTERNS: int = 50

# ---------------------------------------------------------------------------
# ObstructionPattern
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObstructionPattern:
    """Represents a single named obstruction pattern detected at the judgment site.

    An obstruction pattern is a lightweight descriptor that maps diagnostic
    keywords (emitted by the proof assistant or code verifier) onto one or
    more mathematical fields that might resolve the underlying theoretical
    gap.

    theory2.tex §247: obstruction patterns are the atomic units of a theory
    deficit; each one corresponds to a class of unprovable obligations that
    arise when the system lacks the requisite mathematical vocabulary.

    Attributes:
        pattern_id: Unique slug identifier, e.g. "type_error_hott".
        keywords: Tuple of lowercase trigger words.  If any of these appear
            in the diagnostic text the pattern is considered to match.
        associated_fields: Tuple of mathematical field names that could
            supply the missing theory (e.g. "Homotopy Type Theory").
        description: Human-readable sentence describing the obstruction.
        severity_weight: Multiplicative weight used when computing the
            aggregate severity score.  Defaults to 1.0 (neutral).
    """

    pattern_id: str
    keywords: tuple[str, ...]
    associated_fields: tuple[str, ...]
    description: str
    severity_weight: float = 1.0

    # ------------------------------------------------------------------
    def matches(self, text: str) -> bool:
        """Return True if any keyword appears in *text* (case-insensitive).

        Args:
            text: The diagnostic or error string to probe.

        Returns:
            True when at least one keyword is found in the lowercased text.

        Notes:
            Matching is substring-based, not word-boundary-based, so a
            keyword "type" would also match "subtype" or "typeof".  This
            is intentional — the patterns are designed to be inclusive.
        """
        # Normalise once to avoid repeated lower() calls inside the loop.
        lowered = text.lower()
        # Iterate over every keyword; short-circuit on the first hit.
        for kw in self.keywords:
            if kw in lowered:
                # At least one keyword matched — report the hit immediately.
                return True
        # No keywords matched.
        return False

    # ------------------------------------------------------------------
    def matching_keywords(self, text: str) -> list[str]:
        """Return the list of keywords that appear in *text*.

        Unlike :py:meth:`matches`, this method does NOT short-circuit; it
        collects *all* matching keywords, which is useful for diagnostics.

        Args:
            text: The diagnostic or error string to probe.

        Returns:
            Ordered list of matching keyword strings (may be empty).
        """
        lowered = text.lower()
        # Collect every keyword that is a substring of the lowered text.
        return [kw for kw in self.keywords if kw in lowered]

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise the pattern to a plain JSON-compatible dict.

        Returns:
            Dictionary with string/float values suitable for JSON encoding.
        """
        return {
            "pattern_id": self.pattern_id,
            "keywords": list(self.keywords),
            "associated_fields": list(self.associated_fields),
            "description": self.description,
            "severity_weight": self.severity_weight,
        }


# ---------------------------------------------------------------------------
# KNOWN_OBSTRUCTION_PATTERNS
# ---------------------------------------------------------------------------

#: Canonical library of known obstruction patterns.  Each entry covers a
#: broad class of theoretical gap.  The list is intentionally kept under
#: _MAX_OBSTRUCTION_PATTERNS entries.
#:
#: theory2.tex §248-§260: the taxonomy of obstruction patterns covers the
#: major branches of pure and applied mathematics most commonly encountered
#: when formal verification systems hit unprovable gaps.
KNOWN_OBSTRUCTION_PATTERNS: list[ObstructionPattern] = [
    # ------------------------------------------------------------------
    # 1. Type errors — missing typing vocabulary, universe hierarchies, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="type_error_hott",
        keywords=("type error", "type mismatch", "universe", "univalence",
                  "dependent type", "homotopy type", "typecheck"),
        associated_fields=("Homotopy Type Theory", "Martin-Löf Type Theory",
                           "Higher Category Theory"),
        description=(
            "Type-level proof obligations that require universe polymorphism "
            "or univalence principles unavailable in the current foundation."
        ),
        severity_weight=1.4,
    ),
    # ------------------------------------------------------------------
    # 2. Topology & continuity — limit arguments, open sets, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="topology_continuity",
        keywords=("continuity", "open set", "compactness", "homeomorphism",
                  "homotopy", "fundamental group", "singular homology",
                  "continuous map"),
        associated_fields=("Algebraic Topology", "Differential Geometry",
                           "Point-Set Topology"),
        description=(
            "Continuity or topological structure required to close a proof "
            "gap; fundamental-group or homology arguments missing."
        ),
        severity_weight=1.2,
    ),
    # ------------------------------------------------------------------
    # 3. Algebraic structure — groups, rings, modules, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="algebraic_structure",
        keywords=("group", "ring", "module", "ideal", "lattice", "field extension",
                  "galois", "homomorphism", "exact sequence"),
        associated_fields=("Abstract Algebra", "K-Theory",
                           "Algebraic Geometry", "Commutative Algebra"),
        description=(
            "Algebraic structures (groups, rings, modules) required but not "
            "axiomatised; Galois or extension-field arguments blocked."
        ),
        severity_weight=1.1,
    ),
    # ------------------------------------------------------------------
    # 4. Logic & proof — provability, completeness, consistency, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="logic_proof",
        keywords=("provability", "completeness", "consistency", "decidability",
                  "sequent", "natural deduction", "interpretation", "model"),
        associated_fields=("Mathematical Logic", "Proof Theory",
                           "Model Theory", "Recursion Theory"),
        description=(
            "Meta-logical gaps: the system cannot prove completeness or "
            "consistency within its current formal framework."
        ),
        severity_weight=1.5,
    ),
    # ------------------------------------------------------------------
    # 5. Categorical — functors, natural transformations, limits, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="categorical",
        keywords=("functor", "natural transformation", "adjunction",
                  "monad", "topos", "sheaf", "category", "colimit", "pullback"),
        associated_fields=("Category Theory", "Topos Theory",
                           "Higher Category Theory", "Derived Category Theory"),
        description=(
            "Categorical language (functors, adjoints, monads) required "
            "to express or prove a structural property."
        ),
        severity_weight=1.3,
    ),
    # ------------------------------------------------------------------
    # 6. Numerical / analysis — epsilon-delta, measure, integration, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="numerical_analysis",
        keywords=("convergence", "epsilon", "delta", "measure zero",
                  "lebesgue", "integrable", "norm", "banach", "hilbert space"),
        associated_fields=("Real Analysis", "Measure Theory",
                           "Functional Analysis", "Harmonic Analysis"),
        description=(
            "Analytic arguments requiring measure theory or functional "
            "analysis: convergence, integrability, Banach/Hilbert structure."
        ),
        severity_weight=1.0,
    ),
    # ------------------------------------------------------------------
    # 7. Combinatorial — pigeonhole, Ramsey, graph colouring, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="combinatorial",
        keywords=("pigeonhole", "ramsey", "coloring", "colouring",
                  "matching", "chromatic", "planar graph", "bipartite",
                  "extremal", "enumeration"),
        associated_fields=("Combinatorics", "Graph Theory",
                           "Extremal Combinatorics", "Algebraic Combinatorics"),
        description=(
            "Combinatorial counting or structural arguments (Ramsey, "
            "chromatic, matching theory) blocking a finite proof."
        ),
        severity_weight=0.9,
    ),
    # ------------------------------------------------------------------
    # 8. Probabilistic — random variables, martingales, stochastic processes.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="probabilistic",
        keywords=("probability", "random variable", "martingale",
                  "stochastic", "markov chain", "brownian", "expectation",
                  "variance", "law of large numbers"),
        associated_fields=("Probability Theory", "Stochastic Processes",
                           "Ergodic Theory", "Information Theory"),
        description=(
            "Probabilistic or stochastic arguments (martingales, Markov "
            "chains) needed to handle uncertainty in proof obligations."
        ),
        severity_weight=1.0,
    ),
    # ------------------------------------------------------------------
    # 9. Geometric — manifolds, curvature, geodesics, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="geometric",
        keywords=("manifold", "curvature", "geodesic", "riemannian",
                  "connection", "tensor", "ricci", "embedding", "submanifold"),
        associated_fields=("Differential Geometry", "Riemannian Geometry",
                           "Symplectic Geometry", "Sub-Riemannian Geometry"),
        description=(
            "Geometric proof obligations involving manifold structure, "
            "curvature, or geodesic flow."
        ),
        severity_weight=1.1,
    ),
    # ------------------------------------------------------------------
    # 10. Representation — characters, modules over algebras, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="representation",
        keywords=("representation", "character", "irreducible",
                  "weight", "root system", "lie algebra", "lie group",
                  "dynkin", "semisimple"),
        associated_fields=("Representation Theory", "Lie Theory",
                           "Algebraic Groups", "Quantum Groups"),
        description=(
            "Representation-theoretic gaps: missing decomposition theory "
            "for Lie algebras or algebraic groups."
        ),
        severity_weight=1.2,
    ),
    # ------------------------------------------------------------------
    # 11. Number-theoretic — primes, Diophantine equations, L-functions, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="number_theoretic",
        keywords=("prime", "diophantine", "l-function", "zeta",
                  "class number", "abelian extension", "cyclotomic",
                  "elliptic curve", "modular form"),
        associated_fields=("Number Theory", "Algebraic Number Theory",
                           "Arithmetic Geometry", "Analytic Number Theory"),
        description=(
            "Number-theoretic obstructions: Diophantine, L-function, or "
            "elliptic-curve arguments that are beyond current axioms."
        ),
        severity_weight=1.3,
    ),
    # ------------------------------------------------------------------
    # 12. Set-theoretic — ordinals, cardinals, descriptive set theory, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="set_theoretic",
        keywords=("ordinal", "cardinal", "forcing", "independence",
                  "well-order", "borel", "analytic set", "projective",
                  "large cardinal"),
        associated_fields=("Set Theory", "Descriptive Set Theory",
                           "Inner Model Theory", "Combinatorial Set Theory"),
        description=(
            "Set-theoretic independence results or large-cardinal hypotheses "
            "required to settle an unprovable proposition."
        ),
        severity_weight=1.6,  # Typically highest severity — independence results
    ),
    # ------------------------------------------------------------------
    # 13. Operator / spectral theory — eigenvalues, spectra, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="spectral_operator",
        keywords=("eigenvalue", "spectrum", "self-adjoint", "operator algebra",
                  "c*-algebra", "von neumann", "trace class", "fredholm"),
        associated_fields=("Operator Theory", "Spectral Theory",
                           "Operator Algebras", "Non-commutative Geometry"),
        description=(
            "Spectral or operator-algebraic arguments required; missing "
            "functional calculus or trace-class theory."
        ),
        severity_weight=1.1,
    ),
    # ------------------------------------------------------------------
    # 14. Homological / derived — Ext, Tor, derived functors, etc.
    # ------------------------------------------------------------------
    ObstructionPattern(
        pattern_id="homological_derived",
        keywords=("ext group", "tor functor", "derived functor",
                  "projective resolution", "injective resolution",
                  "spectral sequence", "derived category", "infinity category"),
        associated_fields=("Homological Algebra", "Derived Algebraic Geometry",
                           "Higher Algebra", "Stable Homotopy Theory"),
        description=(
            "Homological obstructions: Ext/Tor computations, derived "
            "functors, or spectral sequences blocking a proof."
        ),
        severity_weight=1.3,
    ),
][:_MAX_OBSTRUCTION_PATTERNS]  # Enforce the module-level cap.

# ---------------------------------------------------------------------------
# _OBSTRUCTION_TO_FIELD_MAP
# ---------------------------------------------------------------------------

#: Large keyword-to-field mapping used as a secondary lookup when the
#: KNOWN_OBSTRUCTION_PATTERNS library does not match.  This covers a broader
#: vocabulary at the cost of losing severity-weight information.
#:
#: Keys are lowercase diagnostic keywords; values are lists of field names.
#:
#: theory2.tex §261: the obstruction-to-field mapping is the principal
#: artefact of the metatheory translation layer; it encodes centuries of
#: mathematical practice into a machine-queryable lookup table.
_OBSTRUCTION_TO_FIELD_MAP: dict[str, list[str]] = {
    # Type theory cluster
    "type":               ["Homotopy Type Theory", "Martin-Löf Type Theory"],
    "universe":           ["Homotopy Type Theory", "Set Theory"],
    "univalence":         ["Homotopy Type Theory"],
    "dependent":          ["Martin-Löf Type Theory", "Proof Theory"],
    "coercion":           ["Category Theory", "Homotopy Type Theory"],
    # Topology cluster
    "topology":           ["Algebraic Topology", "Point-Set Topology"],
    "homology":           ["Algebraic Topology", "Homological Algebra"],
    "cohomology":         ["Algebraic Topology", "Algebraic Geometry"],
    "fibration":          ["Algebraic Topology", "Higher Category Theory"],
    "cofibration":        ["Algebraic Topology", "Stable Homotopy Theory"],
    # Algebra cluster
    "algebra":            ["Abstract Algebra", "Universal Algebra"],
    "ring":               ["Commutative Algebra", "K-Theory"],
    "module":             ["Representation Theory", "Homological Algebra"],
    "ideal":              ["Commutative Algebra", "Algebraic Geometry"],
    "group action":       ["Group Theory", "Equivariant Topology"],
    # Logic / foundations cluster
    "logic":              ["Mathematical Logic", "Proof Theory"],
    "proof":              ["Proof Theory", "Formal Verification"],
    "model":              ["Model Theory", "Mathematical Logic"],
    "completeness":       ["Model Theory", "Proof Theory"],
    "consistency":        ["Set Theory", "Proof Theory"],
    "forcing":            ["Set Theory", "Descriptive Set Theory"],
    # Category theory cluster
    "category":           ["Category Theory", "Higher Category Theory"],
    "functor":            ["Category Theory", "Topos Theory"],
    "adjoint":            ["Category Theory", "Functional Analysis"],
    "monad":              ["Category Theory", "Theoretical Computer Science"],
    "sheaf":              ["Topos Theory", "Algebraic Geometry"],
    # Analysis cluster
    "analysis":           ["Real Analysis", "Functional Analysis"],
    "measure":            ["Measure Theory", "Probability Theory"],
    "integral":           ["Measure Theory", "Harmonic Analysis"],
    "banach":             ["Functional Analysis", "Operator Theory"],
    "hilbert":            ["Functional Analysis", "Quantum Mechanics"],
    # Geometry cluster
    "geometry":           ["Differential Geometry", "Algebraic Geometry"],
    "manifold":           ["Differential Geometry", "Topology"],
    "curvature":          ["Riemannian Geometry", "General Relativity"],
    "symplectic":         ["Symplectic Geometry", "Hamiltonian Mechanics"],
    # Number theory cluster
    "number":             ["Number Theory", "Algebraic Number Theory"],
    "prime":              ["Analytic Number Theory", "Algebraic Number Theory"],
    "zeta":               ["Analytic Number Theory", "Arithmetic Geometry"],
    "modular":            ["Modular Forms", "Arithmetic Geometry"],
    "elliptic":           ["Arithmetic Geometry", "Complex Analysis"],
    # Combinatorics cluster
    "graph":              ["Graph Theory", "Combinatorics"],
    "combinatorics":      ["Combinatorics", "Algebraic Combinatorics"],
    "entropy":            ["Information Theory", "Ergodic Theory"],
    # Stochastics cluster
    "stochastic":         ["Stochastic Processes", "Probability Theory"],
    "markov":             ["Stochastic Processes", "Ergodic Theory"],
    "random":             ["Probability Theory", "Random Matrix Theory"],
}

# ---------------------------------------------------------------------------
# SynthesisOrchestratorConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SynthesisOrchestratorConfig:
    """Immutable configuration object for :class:`SynthesisOrchestrator`.

    All tunable parameters are collected here so that they can be serialised
    to disk, versioned in git, and reloaded without restarting the process.

    theory2.tex §263: orchestrator configuration represents the policy layer
    of the metatheory management system; changes here affect when and how
    often the synthesis frontier is triggered.

    Attributes:
        density_threshold: Fractional obstruction density above which the
            orchestrator considers the judgment site to be in a theory
            deficit.  Range [0, 1].  Default 0.6 (60 % obstructed).
        min_obstructions: Minimum absolute count of obstruction hits before
            a deficit signal is raised, regardless of density.  Prevents
            false positives when the sample is very small.
        max_concurrent_campaigns: How many synthesis campaigns may run in
            parallel.  Keep at 1 unless the host machine has ample GPU/CPU.
        campaign_cooldown_seconds: Minimum wall-clock seconds between the
            completion of one campaign and the start of the next.  Prevents
            thrashing when deficits are persistent.
        default_strategy: Strategy passed to the synthesis pipeline.  One
            of "diversity", "depth", or "hybrid".
        default_model: The LLM identifier used by the synthesis pipeline
            when generating paper content.
        output_base_dir: Root directory under which campaign artefacts
            (papers, field graphs, evidence records) are written.
        use_llm: When False the synthesis pipeline operates in dry-run mode
            (useful for unit tests and CI).
        max_rounds: Hard cap on the number of synthesis rounds per campaign.
            None means use the pipeline's own default.
        evidence_channel: The channel identifier used when injecting
            synthesis results into the evidence pipeline.
        enable_diagnostics: When True the orchestrator emits a detailed
            diagnostics report after each campaign.
    """

    density_threshold: float = 0.6
    min_obstructions: int = 3
    max_concurrent_campaigns: int = 1
    campaign_cooldown_seconds: float = 300.0
    default_strategy: str = "diversity"
    default_model: str = "claude-sonnet-4.6"
    output_base_dir: str = "outputs/synthesis"
    use_llm: bool = True
    max_rounds: int | None = None
    evidence_channel: str = "ORACLE_PROPOSED"
    enable_diagnostics: bool = True

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise configuration to a plain JSON-compatible dictionary.

        Returns:
            Dict with primitive values; ``max_rounds`` may be ``None``.
        """
        return {
            "density_threshold": self.density_threshold,
            "min_obstructions": self.min_obstructions,
            "max_concurrent_campaigns": self.max_concurrent_campaigns,
            "campaign_cooldown_seconds": self.campaign_cooldown_seconds,
            "default_strategy": self.default_strategy,
            "default_model": self.default_model,
            "output_base_dir": self.output_base_dir,
            "use_llm": self.use_llm,
            "max_rounds": self.max_rounds,
            "evidence_channel": self.evidence_channel,
            "enable_diagnostics": self.enable_diagnostics,
        }

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> SynthesisOrchestratorConfig:
        """Deserialise configuration from a plain dictionary.

        Unknown keys in *d* are silently ignored so that older config files
        remain forward-compatible.

        Args:
            d: Dictionary of configuration values (as produced by
               :py:meth:`to_dict` or loaded from a JSON file).

        Returns:
            A new :class:`SynthesisOrchestratorConfig` instance.

        Notes:
            Uses ``dict.get`` with defaults so partially-specified configs
            are valid.
        """
        return cls(
            density_threshold=float(d.get("density_threshold", 0.6)),
            min_obstructions=int(d.get("min_obstructions", 3)),
            max_concurrent_campaigns=int(d.get("max_concurrent_campaigns", 1)),
            campaign_cooldown_seconds=float(d.get("campaign_cooldown_seconds", 300.0)),
            default_strategy=str(d.get("default_strategy", "diversity")),
            default_model=str(d.get("default_model", "claude-sonnet-4.6")),
            output_base_dir=str(d.get("output_base_dir", "outputs/synthesis")),
            use_llm=bool(d.get("use_llm", True)),
            max_rounds=d.get("max_rounds"),  # May be None.
            evidence_channel=str(d.get("evidence_channel", "ORACLE_PROPOSED")),
            enable_diagnostics=bool(d.get("enable_diagnostics", True)),
        )


# ---------------------------------------------------------------------------
# TheoryDeficitSignal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TheoryDeficitSignal:
    """Immutable signal emitted when a theory deficit is detected.

    A theory deficit signal is a lightweight descriptor that captures the
    state of the judgment site at the moment of detection.  It is passed to
    the campaign scheduler and stored as part of every
    :class:`SynthesisCampaign` for auditability.

    theory2.tex §252–§254: a theory deficit signal is formally a tuple
    (D, O, F, U, T) where D is the obstruction density, O is the set of
    active obstruction identifiers, F is the set of recommended fields,
    U is the urgency level, and T is the detection timestamp.

    Attributes:
        signal_id: UUID-4 string uniquely identifying this signal instance.
        obstruction_density: Fractional density of obstruction hits at the
            judgment site.  Value in [0, 1].
        affected_coordinates: Tuple of coordinate strings (e.g. proof
            obligation labels) that are currently obstructed.
        recommended_fields: Tuple of mathematical field names recommended
            by the detector to address the identified gaps.
        urgency: One of "LOW", "MEDIUM", "HIGH", or "CRITICAL".  Drives
            how quickly the scheduler should respond.
        timestamp: ISO-8601 UTC timestamp string when the signal was raised.
        obstruction_patterns: Tuple of pattern_id strings that matched
            during detection.
        severity_score: A non-negative floating-point aggregate severity
            computed as a weighted sum of matched pattern severities.
        recommended_round_limit: Optional integer cap on the number of
            synthesis rounds to run.  None means no limit.
    """

    signal_id: str
    obstruction_density: float
    affected_coordinates: tuple[str, ...]
    recommended_fields: tuple[str, ...]
    urgency: str
    timestamp: str
    obstruction_patterns: tuple[str, ...]
    severity_score: float
    recommended_round_limit: int | None

    # ------------------------------------------------------------------
    def is_urgent(self) -> bool:
        """Return True when urgency is HIGH or CRITICAL.

        Returns:
            Boolean indicating whether this signal demands rapid response.

        Notes:
            Callers may use this to bypass the normal cooldown when the
            judgment site is severely obstructed.
        """
        # Only the two highest urgency levels qualify as "urgent".
        return self.urgency in ("HIGH", "CRITICAL")

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise to a plain JSON-compatible dictionary.

        Returns:
            Dictionary with primitive values suitable for ``json.dumps``.
        """
        return {
            "signal_id": self.signal_id,
            "obstruction_density": self.obstruction_density,
            "affected_coordinates": list(self.affected_coordinates),
            "recommended_fields": list(self.recommended_fields),
            "urgency": self.urgency,
            "timestamp": self.timestamp,
            "obstruction_patterns": list(self.obstruction_patterns),
            "severity_score": self.severity_score,
            "recommended_round_limit": self.recommended_round_limit,
        }

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """Return a single-line human-readable summary of the signal.

        Returns:
            String of the form
            ``"[URGENCY] density=X.XX  patterns=N  fields=Field1,Field2"``.
        """
        fields_str = ", ".join(self.recommended_fields[:3])
        if len(self.recommended_fields) > 3:
            fields_str += f" (+{len(self.recommended_fields) - 3} more)"
        return (
            f"[{self.urgency}] density={self.obstruction_density:.2f}  "
            f"patterns={len(self.obstruction_patterns)}  "
            f"fields={fields_str}"
        )


# ---------------------------------------------------------------------------
# SynthesisCampaign
# ---------------------------------------------------------------------------

@dataclass
class SynthesisCampaign:
    """Mutable record tracking the lifecycle of a single synthesis campaign.

    A synthesis campaign is the end-to-end execution of the synthesis
    frontier pipeline in response to a :class:`TheoryDeficitSignal`.  It
    starts in the PLANNED state, transitions to RUNNING when the pipeline
    is invoked, and ends in either COMPLETED or FAILED.

    theory2.tex §265: each synthesis campaign produces a canonical paper
    artefact and a set of proposition records that are injected back into
    the evidence pipeline.

    Attributes:
        campaign_id: UUID-4 string uniquely identifying the campaign.
        trigger_signal: The :class:`TheoryDeficitSignal` that initiated
            this campaign.
        status: Current lifecycle state — one of PLANNED / RUNNING /
            COMPLETED / FAILED.
        rounds_completed: Number of synthesis rounds that have finished.
        rounds_total: Expected total rounds (from the pipeline config).
        propositions_generated: Count of proposition records produced.
        metaphors_found: Count of cross-field metaphors discovered.
        paper_path: Filesystem path to the generated paper file, or None
            if not yet written.
        code_targets: List of code coordinate strings targeted by the
            generated theorems.
        started_at: ISO-8601 UTC string when execution began.
        completed_at: ISO-8601 UTC string when execution ended (or None).
        error_message: Human-readable error description if status is FAILED.
        evidence_records: List of evidence record dicts injected into the
            evidence pipeline after campaign completion.
    """

    campaign_id: str
    trigger_signal: TheoryDeficitSignal
    status: str = "PLANNED"
    rounds_completed: int = 0
    rounds_total: int = 6
    propositions_generated: int = 0
    metaphors_found: int = 0
    paper_path: str | None = None
    code_targets: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str | None = None
    error_message: str | None = None
    evidence_records: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    def mark_running(self) -> None:
        """Transition the campaign to RUNNING status.

        Notes:
            Records the ``started_at`` timestamp using UTC wall-clock time.
            Should be called immediately before the pipeline invocation so
            that ``duration_seconds`` reflects actual pipeline runtime.
        """
        _LOGGER.debug("Campaign %s transitioning PLANNED → RUNNING", self.campaign_id)
        self.status = "RUNNING"
        self.started_at = _format_timestamp()

    # ------------------------------------------------------------------
    def mark_completed(self, result: Any) -> None:
        """Transition the campaign to COMPLETED and absorb pipeline result.

        Args:
            result: A :class:`PipelineResult` (or compatible stub) returned
                by the synthesis pipeline.  If the result has numeric
                attributes they are unpacked into the campaign record.

        Notes:
            Sets ``completed_at`` to the current UTC time.  Safe to call
            even when *result* is ``None`` (e.g. dry-run mode).
        """
        _LOGGER.info(
            "Campaign %s COMPLETED  rounds=%s  props=%s",
            self.campaign_id,
            getattr(result, "rounds_completed", "?"),
            getattr(result, "propositions_total", "?"),
        )
        self.status = "COMPLETED"
        self.completed_at = _format_timestamp()
        # Absorb counters from the result if they are available.
        if result is not None:
            self.rounds_completed = getattr(result, "rounds_completed",
                                            self.rounds_completed)
            self.propositions_generated = getattr(result, "propositions_total",
                                                  self.propositions_generated)
            self.metaphors_found = getattr(result, "metaphors_total",
                                           self.metaphors_found)

    # ------------------------------------------------------------------
    def mark_failed(self, error: Exception) -> None:
        """Transition the campaign to FAILED and record the error.

        Args:
            error: The exception that caused the failure.

        Notes:
            Preserves ``started_at`` so that ``duration_seconds`` still
            returns a meaningful value for post-mortem analysis.
        """
        _LOGGER.error(
            "Campaign %s FAILED: %s", self.campaign_id, error, exc_info=True
        )
        self.status = "FAILED"
        self.completed_at = _format_timestamp()
        self.error_message = str(error)

    # ------------------------------------------------------------------
    def duration_seconds(self) -> float | None:
        """Return elapsed wall-clock seconds between start and completion.

        Returns:
            Float seconds, or ``None`` if the campaign has not yet started
            or ``started_at`` cannot be parsed.

        Notes:
            Uses ISO-8601 parsing via ``datetime.datetime.fromisoformat``.
            If ``completed_at`` is ``None`` the end time defaults to now,
            giving an in-progress elapsed estimate.
        """
        if not self.started_at:
            # Campaign has not yet started — nothing to measure.
            return None
        try:
            start_dt = datetime.datetime.fromisoformat(self.started_at)
            # If not yet finished, use the current wall-clock time.
            end_str = self.completed_at or _format_timestamp()
            end_dt = datetime.datetime.fromisoformat(end_str)
            return (end_dt - start_dt).total_seconds()
        except ValueError:
            # Timestamp parsing failed — return None rather than raising.
            _LOGGER.warning(
                "Could not parse timestamps for campaign %s", self.campaign_id
            )
            return None

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise the campaign to a plain JSON-compatible dictionary.

        Returns:
            Dictionary with primitive values; nested objects use their own
            ``to_dict`` where available.
        """
        return {
            "campaign_id": self.campaign_id,
            "trigger_signal": self.trigger_signal.to_dict(),
            "status": self.status,
            "rounds_completed": self.rounds_completed,
            "rounds_total": self.rounds_total,
            "propositions_generated": self.propositions_generated,
            "metaphors_found": self.metaphors_found,
            "paper_path": self.paper_path,
            "code_targets": self.code_targets,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "evidence_records": self.evidence_records,
        }

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """Return a compact single-line summary of the campaign.

        Returns:
            String of the form
            ``"[STATUS] id=... rounds=N/M props=K dur=Xs"``.
        """
        dur = self.duration_seconds()
        dur_str = f"{dur:.1f}s" if dur is not None else "?"
        return (
            f"[{self.status}] id={self.campaign_id[:8]}...  "
            f"rounds={self.rounds_completed}/{self.rounds_total}  "
            f"props={self.propositions_generated}  "
            f"dur={dur_str}"
        )


# ---------------------------------------------------------------------------
# TheoryDeficitDetector
# ---------------------------------------------------------------------------

class TheoryDeficitDetector:
    """Analyses the judgment site manifest to detect theory deficits.

    The detector examines a manifest dict (produced by the JuGeo controller)
    that contains information about current obstruction states, proof
    obligation counts, and repair availability.  When the obstruction density
    and count exceed configured thresholds it emits a
    :class:`TheoryDeficitSignal`.

    theory2.tex §250–§256: theory deficit detection is a two-phase process.
    Phase 1 (density estimation) computes the fraction of active proof
    obligations that are obstructed.  Phase 2 (field recommendation)
    maps the identified obstruction patterns onto mathematical fields that
    could supply the required theory.

    Attributes:
        density_threshold: Minimum obstruction density to trigger a signal.
        min_obstructions: Minimum absolute obstruction count.
        _patterns: Snapshot of KNOWN_OBSTRUCTION_PATTERNS used at
            construction time.
    """

    def __init__(
        self,
        density_threshold: float = 0.6,
        min_obstructions: int = 3,
    ) -> None:
        """Initialise the detector with tunable thresholds.

        Args:
            density_threshold: Fractional density threshold in [0, 1].
            min_obstructions: Minimum obstruction count threshold.
        """
        # Store thresholds for use in detect() and helper methods.
        self.density_threshold: float = density_threshold
        self.min_obstructions: int = min_obstructions
        # Take a copy of the global pattern list so that the detector is
        # unaffected by external mutation of KNOWN_OBSTRUCTION_PATTERNS.
        self._patterns: list[ObstructionPattern] = list(KNOWN_OBSTRUCTION_PATTERNS)
        _LOGGER.debug(
            "TheoryDeficitDetector initialised  threshold=%.2f  min_obs=%d  patterns=%d",
            density_threshold, min_obstructions, len(self._patterns),
        )

    # ------------------------------------------------------------------
    def detect(self, manifest: dict | None = None) -> TheoryDeficitSignal | None:
        """Analyse *manifest* and return a deficit signal if warranted.

        This is the primary entry point.  It orchestrates the density
        computation, obstruction extraction, urgency assessment, and field
        recommendation sub-steps.

        Args:
            manifest: Optional dict produced by the JuGeo controller.  If
                ``None`` an empty manifest is assumed (no deficit detected).

        Returns:
            A :class:`TheoryDeficitSignal` when a deficit is detected, or
            ``None`` when the site is within acceptable parameters.

        Notes:
            This method is intentionally non-destructive and side-effect-free;
            it may be called repeatedly on the same manifest.
        """
        # An absent manifest means no data — cannot declare a deficit.
        if manifest is None:
            _LOGGER.debug("Manifest is None — no deficit detected.")
            return None

        # --- Phase 1: density estimation ---
        density = self._compute_density(manifest)
        _LOGGER.debug("Obstruction density = %.3f  (threshold=%.2f)", density,
                      self.density_threshold)

        # --- Phase 2: extract individual obstruction strings ---
        obstructions = self._extract_obstructions(manifest)
        _LOGGER.debug("Obstruction count = %d  (min=%d)", len(obstructions),
                      self.min_obstructions)

        # --- Gate check: must exceed BOTH thresholds to proceed ---
        if density < self.density_threshold or len(obstructions) < self.min_obstructions:
            _LOGGER.info(
                "Judgment site within parameters (density=%.2f, obs=%d) — no signal.",
                density, len(obstructions),
            )
            return None

        # --- Phase 3: urgency & severity ---
        urgency = self._assess_urgency(density, len(obstructions))
        severity = self._compute_severity(density, obstructions)

        # --- Phase 4: field recommendation ---
        fields = self._recommend_fields(obstructions)

        # --- Phase 5: round-limit recommendation ---
        round_limit = self._recommend_round_limit(urgency)

        # --- Build the matched pattern IDs from obstructions ---
        matched_pattern_ids: list[str] = []
        for pat in self._patterns:
            for obs in obstructions:
                if pat.matches(obs):
                    if pat.pattern_id not in matched_pattern_ids:
                        matched_pattern_ids.append(pat.pattern_id)
                    break  # No need to check other obstructions for this pattern.

        # Gather the affected coordinate labels from the manifest.
        affected = _coerce_str_list(manifest.get("obstructed_coordinates", []))

        signal = TheoryDeficitSignal(
            signal_id=str(uuid.uuid4()),
            obstruction_density=density,
            affected_coordinates=tuple(affected),
            recommended_fields=tuple(fields),
            urgency=urgency,
            timestamp=_format_timestamp(),
            obstruction_patterns=tuple(matched_pattern_ids),
            severity_score=severity,
            recommended_round_limit=round_limit,
        )
        _LOGGER.warning("Theory deficit signal raised: %s", signal.summary())
        return signal

    # ------------------------------------------------------------------
    def _compute_density(self, manifest: dict) -> float:
        """Compute fractional obstruction density from *manifest*.

        The density is defined as:

            density = obstructed_count / max(total_obligations, 1)

        Args:
            manifest: Controller manifest dict.

        Returns:
            Float in [0, 1].  Returns 0.0 when the manifest lacks the
            required keys.
        """
        # Try to extract total obligation and obstructed counts.
        total: int = int(manifest.get("total_obligations", 0))
        obstructed: int = int(manifest.get("obstructed_count", 0))

        # Avoid division by zero; if no obligations exist density is 0.
        if total == 0:
            _LOGGER.debug("No obligations in manifest — density = 0.0")
            return 0.0

        # Clamp to [0, 1] in case the manifest contains inconsistent data.
        raw_density = obstructed / total
        return max(0.0, min(1.0, raw_density))

    # ------------------------------------------------------------------
    def _extract_obstructions(self, manifest: dict) -> list[str]:
        """Extract the list of obstruction description strings from *manifest*.

        The manifest may store obstructions under several different keys for
        backward compatibility.  This method tries each in order.

        Args:
            manifest: Controller manifest dict.

        Returns:
            List of non-empty obstruction description strings.
        """
        # Primary key: "obstructions" — a list of description dicts or strings.
        raw: list = manifest.get("obstructions", [])

        # Fallback key: "obstruction_messages" — used by older controller versions.
        if not raw:
            raw = manifest.get("obstruction_messages", [])

        # Further fallback: "errors" — generic error list from the verifier.
        if not raw:
            raw = manifest.get("errors", [])

        extracted: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                # Dicts typically have a "message" or "description" key.
                msg = item.get("message") or item.get("description") or str(item)
                if msg:
                    extracted.append(str(msg))
            elif isinstance(item, str) and item.strip():
                extracted.append(item.strip())

        _LOGGER.debug("Extracted %d obstruction strings from manifest.", len(extracted))
        return extracted

    # ------------------------------------------------------------------
    def _assess_urgency(self, density: float, count: int) -> str:
        """Map density and count to an urgency level string.

        The urgency scale is:
        - CRITICAL : density ≥ 0.90 or count ≥ 20
        - HIGH     : density ≥ 0.75 or count ≥ 10
        - MEDIUM   : density ≥ 0.60 or count ≥ 5
        - LOW      : below all of the above

        Args:
            density: Fractional obstruction density.
            count: Absolute obstruction count.

        Returns:
            One of "LOW", "MEDIUM", "HIGH", "CRITICAL".
        """
        # Critical threshold — immediate action required.
        if density >= 0.90 or count >= 20:
            return "CRITICAL"
        # High threshold — action required before the next orchestration cycle.
        if density >= 0.75 or count >= 10:
            return "HIGH"
        # Medium threshold — action required within a few cycles.
        if density >= 0.60 or count >= 5:
            return "MEDIUM"
        # Below all thresholds — monitoring only.
        return "LOW"

    # ------------------------------------------------------------------
    def _recommend_fields(self, obstructions: list[str]) -> list[str]:
        """Map obstruction strings to recommended mathematical fields.

        Uses a two-pass approach:
        1. Match against KNOWN_OBSTRUCTION_PATTERNS (structured, weighted).
        2. Match against _OBSTRUCTION_TO_FIELD_MAP (broad keyword lookup).

        Args:
            obstructions: List of obstruction description strings.

        Returns:
            Deduplicated list of recommended mathematical field names,
            ordered by estimated relevance (most relevant first).
        """
        # Use a dict to accumulate field → frequency counts.
        field_counts: dict[str, float] = {}

        # --- Pass 1: structured pattern library ---
        for pat in self._patterns:
            for obs in obstructions:
                if pat.matches(obs):
                    for fld in pat.associated_fields:
                        # Weight the vote by the pattern's severity_weight
                        # so high-severity patterns dominate the ranking.
                        field_counts[fld] = (
                            field_counts.get(fld, 0.0) + pat.severity_weight
                        )

        # --- Pass 2: broad keyword map ---
        for obs in obstructions:
            obs_lower = obs.lower()
            for kw, flds in _OBSTRUCTION_TO_FIELD_MAP.items():
                if kw in obs_lower:
                    for fld in flds:
                        # Each keyword-map hit contributes 0.5 (half weight of
                        # a structured pattern hit) to prevent the broad map
                        # from overwhelming the structured pass.
                        field_counts[fld] = field_counts.get(fld, 0.0) + 0.5

        # Sort fields descending by accumulated weight, then alphabetically.
        sorted_fields = sorted(
            field_counts.keys(),
            key=lambda f: (-field_counts[f], f),
        )
        return sorted_fields

    # ------------------------------------------------------------------
    def _compute_severity(self, density: float, obstructions: list[str]) -> float:
        """Compute a non-negative aggregate severity score.

        The severity score is computed as:

            severity = density * Σ(pattern.severity_weight)

        where the sum is over all matched patterns.

        Args:
            density: Fractional obstruction density.
            obstructions: List of obstruction description strings.

        Returns:
            Non-negative float severity score.  Typical range [0, ~15].
        """
        total_weight = 0.0
        for pat in self._patterns:
            for obs in obstructions:
                if pat.matches(obs):
                    # Only count each pattern once per detection call.
                    total_weight += pat.severity_weight
                    break  # Move to next pattern.

        # Multiply by density to penalise low-density, high-pattern cases.
        severity = density * total_weight
        _LOGGER.debug("Severity score = %.3f (density=%.2f, weight_sum=%.2f)",
                      severity, density, total_weight)
        return round(severity, 4)

    # ------------------------------------------------------------------
    def _recommend_round_limit(self, urgency: str) -> int | None:
        """Map urgency to an optional round-limit recommendation.

        Args:
            urgency: One of "LOW", "MEDIUM", "HIGH", "CRITICAL".

        Returns:
            Integer recommended round cap, or ``None`` for no limit.

        Notes:
            CRITICAL and HIGH deficits recommend more rounds so that the
            frontier can explore further; LOW deficits use fewer rounds to
            conserve resources.
        """
        # Map each urgency level to a round recommendation.
        urgency_to_rounds: dict[str, int | None] = {
            "CRITICAL": None,   # No limit — run as long as needed.
            "HIGH":     10,
            "MEDIUM":   6,
            "LOW":      3,
        }
        return urgency_to_rounds.get(urgency, 6)


# ---------------------------------------------------------------------------
# EvidenceBridge
# ---------------------------------------------------------------------------

class EvidenceBridge:
    """Converts synthesis pipeline output into JuGeo evidence records.

    The bridge is responsible for translating the mathematical artefacts
    produced by the synthesis frontier (field nodes, propositions, papers)
    into the evidence-record format consumed by the JuGeo evidence pipeline.

    theory2.tex §268: the evidence bridge implements the insertion functor
    from the synthesis category to the evidence category; it maps each
    synthesised proposition to a PROPOSAL-tier evidence record.

    Attributes:
        _record_count: Running count of records produced (for ID generation).
    """

    def __init__(self) -> None:
        """Initialise the bridge with an empty record counter."""
        self._record_count: int = 0
        _LOGGER.debug("EvidenceBridge initialised.")

    # ------------------------------------------------------------------
    def paper_to_evidence_records(
        self, paper: Any, campaign: SynthesisCampaign
    ) -> list[dict]:
        """Convert a synthesised paper object into a list of evidence records.

        Each section of the paper that contains a proposition is converted
        to a separate evidence record so that the evidence pipeline can
        evaluate them independently.

        Args:
            paper: A paper object (or dict) produced by the synthesis
                pipeline.  Expected to have a ``fields`` attribute or key.
            campaign: The campaign that produced this paper.

        Returns:
            List of evidence record dicts; may be empty if *paper* has no
            discernible propositions.

        Notes:
            If *paper* is ``None`` a warning is logged and an empty list is
            returned.
        """
        if paper is None:
            _LOGGER.warning("paper_to_evidence_records: received None paper.")
            return []

        records: list[dict] = []

        # Try to extract fields from the paper object.
        fields_raw = getattr(paper, "fields", None) or (
            paper.get("fields", []) if isinstance(paper, dict) else []
        )

        for fld in fields_raw:
            # Delegate per-field conversion to field_to_evidence_records.
            field_records = self.field_to_evidence_records(fld, campaign)
            records.extend(field_records)

        _LOGGER.info(
            "paper_to_evidence_records: %d records from %d fields.",
            len(records), len(list(fields_raw)),
        )
        return records

    # ------------------------------------------------------------------
    def field_to_evidence_records(
        self, field: Any, campaign: SynthesisCampaign
    ) -> list[dict]:
        """Convert a single FieldNode into evidence records for its propositions.

        Args:
            field: A :class:`FieldNode` (or compatible object/dict).
            campaign: The parent campaign.

        Returns:
            List of evidence record dicts, one per proposition found.
        """
        if field is None:
            return []

        # Extract the proposition list from the field object.
        propositions: list = getattr(field, "propositions", None) or (
            field.get("propositions", []) if isinstance(field, dict) else []
        )

        field_name: str = (
            getattr(field, "name", None)
            or (field.get("name", "unknown") if isinstance(field, dict) else "unknown")
        )

        records: list[dict] = []
        for prop in propositions:
            try:
                rec = self._proposition_to_record(prop, field_name, campaign)
                records.append(rec)
                self._record_count += 1
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Failed to convert proposition to evidence record: %s", exc
                )

        return records

    # ------------------------------------------------------------------
    def _proposition_to_record(
        self, prop: Any, field_name: str, campaign: SynthesisCampaign
    ) -> dict:
        """Convert a single proposition into an evidence record dict.

        Args:
            prop: A :class:`PropositionRecord` or compatible object/dict.
            field_name: The name of the mathematical field containing *prop*.
            campaign: The parent campaign.

        Returns:
            Evidence record dict with keys: record_id, statement, field,
            trust_level, trust_tier, campaign_id, signal_id, channel,
            timestamp, confidence, metadata.
        """
        # Extract core attributes gracefully.
        prop_id: str = (
            getattr(prop, "prop_id", None)
            or (prop.get("prop_id", "") if isinstance(prop, dict) else "")
            or str(uuid.uuid4())
        )
        statement: str = (
            getattr(prop, "statement", None)
            or (prop.get("statement", "") if isinstance(prop, dict) else "")
            or ""
        )
        confidence: float = float(
            getattr(prop, "confidence", 0.5)
            or (prop.get("confidence", 0.5) if isinstance(prop, dict) else 0.5)
        )

        # Determine the trust level for this proposition.
        trust_level = self._trust_level_for_prop(prop)

        # Build a stable record ID.
        record_id = self._make_record_id(prop_id, campaign.campaign_id)

        return {
            "record_id": record_id,
            "statement": statement,
            "field": field_name,
            "trust_level": trust_level,
            "trust_tier": _CANONICAL_TRUST_TIER,
            "campaign_id": campaign.campaign_id,
            "signal_id": campaign.trigger_signal.signal_id,
            "channel": _CANONICAL_TRUST_LEVEL,
            "timestamp": _format_timestamp(),
            "confidence": confidence,
            "metadata": {
                "prop_id": prop_id,
                "urgency": campaign.trigger_signal.urgency,
                "density": campaign.trigger_signal.obstruction_density,
                "severity": campaign.trigger_signal.severity_score,
            },
        }

    # ------------------------------------------------------------------
    def _trust_level_for_prop(self, prop: Any) -> str:
        """Determine the trust level for a proposition.

        Args:
            prop: Proposition object (unused — all synthesis propositions
                receive the canonical oracle_proposed trust level).

        Returns:
            Always returns ``"oracle_proposed"``.

        Notes:
            theory2.tex §269: all propositions produced by the synthesis
            frontier are assigned oracle_proposed trust until they are
            independently validated.  This is a hard policy constraint.
        """
        # Policy: synthesis propositions are always oracle_proposed.
        # We do not inspect prop at all — the trust level is invariant.
        return _CANONICAL_TRUST_LEVEL

    # ------------------------------------------------------------------
    def _make_record_id(self, prop_id: str, campaign_id: str) -> str:
        """Construct a stable, unique record ID for a proposition.

        The ID is formed by combining a prefix, the first 8 characters of
        the campaign ID, a separator, and the first 8 characters of the
        proposition ID.

        Args:
            prop_id: Proposition UUID or slug.
            campaign_id: Campaign UUID.

        Returns:
            String of the form ``"ev-CCCCCCCC-PPPPPPPP"``.
        """
        camp_part = campaign_id[:8] if len(campaign_id) >= 8 else campaign_id
        prop_part = prop_id[:8] if len(prop_id) >= 8 else prop_id
        return f"ev-{camp_part}-{prop_part}"


# ---------------------------------------------------------------------------
# CampaignScheduler
# ---------------------------------------------------------------------------

class CampaignScheduler:
    """Controls when new synthesis campaigns may be started.

    The scheduler enforces two constraints:
    1. **Concurrency limit**: at most ``max_concurrent`` campaigns may be in
       the RUNNING state simultaneously.
    2. **Cooldown window**: at least ``cooldown_seconds`` must elapse between
       the completion of one campaign and the start of the next.

    theory2.tex §266: the campaign scheduler is the rate-limiting component
    of the metatheory management system; without it a persistent deficit could
    trigger an unbounded cascade of synthesis runs.

    Attributes:
        max_concurrent: Maximum number of simultaneously running campaigns.
        cooldown_seconds: Minimum seconds between campaign completions and
            new starts.
        _active: Set of campaign IDs currently in RUNNING state.
        _last_completion_time: Wall-clock time of the most recent completion.
    """

    def __init__(
        self,
        max_concurrent: int = 1,
        cooldown_seconds: float = 300.0,
    ) -> None:
        """Initialise the scheduler.

        Args:
            max_concurrent: Maximum concurrent running campaigns.
            cooldown_seconds: Cooldown window in seconds.
        """
        self.max_concurrent: int = max_concurrent
        self.cooldown_seconds: float = cooldown_seconds
        # Track currently active campaign IDs.
        self._active: set[str] = set()
        # Monotonic timestamp of the last campaign completion (0 = never).
        self._last_completion_time: float = 0.0
        _LOGGER.debug(
            "CampaignScheduler initialised  max_concurrent=%d  cooldown=%.1fs",
            max_concurrent, cooldown_seconds,
        )

    # ------------------------------------------------------------------
    def can_start(self, signal: TheoryDeficitSignal) -> bool:
        """Return True if a new campaign may be started for *signal*.

        A new campaign may start only when:
        - The active campaign count is below ``max_concurrent``, AND
        - The cooldown window has elapsed since the last completion
          (unless the signal is CRITICAL, which bypasses the cooldown).

        Args:
            signal: The deficit signal requesting a new campaign.

        Returns:
            True when a campaign may start; False otherwise.
        """
        # Check concurrency limit first (cheapest check).
        if len(self._active) >= self.max_concurrent:
            _LOGGER.info(
                "CampaignScheduler: at capacity (%d/%d) — cannot start.",
                len(self._active), self.max_concurrent,
            )
            return False

        # CRITICAL signals bypass the cooldown — they are time-sensitive.
        if signal.urgency == "CRITICAL":
            _LOGGER.info(
                "CampaignScheduler: CRITICAL signal — bypassing cooldown."
            )
            return True

        # Check cooldown window for non-critical signals.
        if self._is_in_cooldown():
            remaining = self.cooldown_seconds - (
                time.monotonic() - self._last_completion_time
            )
            _LOGGER.info(
                "CampaignScheduler: in cooldown (%.1fs remaining) — cannot start.",
                remaining,
            )
            return False

        return True

    # ------------------------------------------------------------------
    def record_start(self, campaign: SynthesisCampaign) -> None:
        """Register that *campaign* has started running.

        Args:
            campaign: The campaign transitioning to RUNNING.
        """
        self._active.add(campaign.campaign_id)
        _LOGGER.debug(
            "CampaignScheduler: recorded start of %s  active=%d",
            campaign.campaign_id[:8], len(self._active),
        )

    # ------------------------------------------------------------------
    def record_completion(self, campaign: SynthesisCampaign) -> None:
        """Register that *campaign* has completed (or failed).

        Args:
            campaign: The campaign transitioning out of RUNNING.
        """
        self._active.discard(campaign.campaign_id)
        self._last_completion_time = time.monotonic()
        _LOGGER.debug(
            "CampaignScheduler: recorded completion of %s  active=%d",
            campaign.campaign_id[:8], len(self._active),
        )

    # ------------------------------------------------------------------
    def active_count(self) -> int:
        """Return the number of currently running campaigns.

        Returns:
            Non-negative integer count.
        """
        return len(self._active)

    # ------------------------------------------------------------------
    def _is_in_cooldown(self) -> bool:
        """Return True if the cooldown window is still active.

        Returns:
            True when less than ``cooldown_seconds`` have elapsed since the
            last campaign completion; False otherwise or if no campaign has
            ever completed.
        """
        if self._last_completion_time == 0.0:
            # No campaign has ever completed — no cooldown applies.
            return False
        elapsed = time.monotonic() - self._last_completion_time
        return elapsed < self.cooldown_seconds


# ---------------------------------------------------------------------------
# SynthesisOrchestrator
# ---------------------------------------------------------------------------

class SynthesisOrchestrator:
    """Main entry point for the synthesis orchestration subsystem.

    The orchestrator ties together the :class:`TheoryDeficitDetector`,
    :class:`CampaignScheduler`, :class:`EvidenceBridge`, and the synthesis
    frontier pipeline into a single coherent lifecycle manager.

    theory2.tex §262–§270: the synthesis orchestrator is the executive layer
    of the metatheory management system.  Its primary responsibility is to
    ensure that the synthesis frontier is invoked exactly when and as often
    as the judgment site requires new mathematical vocabulary.

    Attributes:
        config: Immutable configuration object.
        detector: :class:`TheoryDeficitDetector` instance.
        scheduler: :class:`CampaignScheduler` instance.
        bridge: :class:`EvidenceBridge` instance.
        _campaigns: All campaigns (active and historical).
    """

    def __init__(self, config: dict | None = None) -> None:
        """Initialise the orchestrator.

        Args:
            config: Optional configuration dictionary.  If ``None`` default
                values are used.  If a dict is provided it is passed through
                :py:meth:`SynthesisOrchestratorConfig.from_dict`.

        Notes:
            The synthesis pipeline is NOT instantiated here; it is created
            lazily inside :py:meth:`run_campaign` to defer expensive imports
            and GPU initialisation until actually needed.
        """
        # Build configuration from dict or use defaults.
        if config is None:
            self.config = SynthesisOrchestratorConfig()
        else:
            self.config = SynthesisOrchestratorConfig.from_dict(config)

        _LOGGER.info("SynthesisOrchestrator initialising with config: %s",
                     self.config.to_dict())

        # Instantiate sub-components using config values.
        self.detector = TheoryDeficitDetector(
            density_threshold=self.config.density_threshold,
            min_obstructions=self.config.min_obstructions,
        )
        self.scheduler = CampaignScheduler(
            max_concurrent=self.config.max_concurrent_campaigns,
            cooldown_seconds=self.config.campaign_cooldown_seconds,
        )
        self.bridge = EvidenceBridge()

        # Campaign registry: maps campaign_id → SynthesisCampaign.
        self._campaigns: dict[str, SynthesisCampaign] = {}

        _LOGGER.info("SynthesisOrchestrator ready.")

    # ------------------------------------------------------------------
    def detect_theory_deficit(
        self, manifest: dict | None = None
    ) -> TheoryDeficitSignal | None:
        """Delegate deficit detection to the embedded detector.

        Args:
            manifest: Controller manifest dict or ``None``.

        Returns:
            :class:`TheoryDeficitSignal` or ``None``.
        """
        _LOGGER.debug("detect_theory_deficit called.")
        return self.detector.detect(manifest)

    # ------------------------------------------------------------------
    def start_campaign(self, signal: TheoryDeficitSignal) -> SynthesisCampaign:
        """Create and register a new :class:`SynthesisCampaign` for *signal*.

        This method does NOT run the pipeline; it only creates the campaign
        record and registers it with the scheduler.  Call :py:meth:`run_campaign`
        to execute the synthesis.

        Args:
            signal: The deficit signal that triggered this campaign.

        Returns:
            A newly created :class:`SynthesisCampaign` in PLANNED state.

        Raises:
            RuntimeError: If the scheduler prevents a new campaign from
                starting (e.g. at capacity or in cooldown).
        """
        # Ask the scheduler for permission.
        if not self.scheduler.can_start(signal):
            raise RuntimeError(
                f"CampaignScheduler refused to start a new campaign "
                f"(active={self.scheduler.active_count()}, "
                f"signal urgency={signal.urgency})."
            )

        # Determine the number of rounds to run.
        rounds = (
            signal.recommended_round_limit
            or self.config.max_rounds
            or 6  # Sensible default.
        )

        campaign = SynthesisCampaign(
            campaign_id=str(uuid.uuid4()),
            trigger_signal=signal,
            rounds_total=rounds,
        )

        # Register in the campaign registry.
        self._campaigns[campaign.campaign_id] = campaign
        # Notify the scheduler.
        self.scheduler.record_start(campaign)

        _LOGGER.info(
            "Campaign %s created (urgency=%s, rounds=%d).",
            campaign.campaign_id[:8], signal.urgency, rounds,
        )
        return campaign

    # ------------------------------------------------------------------
    def run_campaign(self, campaign: SynthesisCampaign) -> SynthesisCampaign:
        """Execute the synthesis pipeline for *campaign*.

        This is the heavy-lifting method.  It:
        1. Transitions the campaign to RUNNING.
        2. Builds a :class:`PipelineConfig` from the campaign signal.
        3. Instantiates :class:`SynthesisFrontierPipeline` and calls ``run()``.
        4. Feeds the result through the evidence bridge.
        5. Transitions to COMPLETED (or FAILED on error).

        Args:
            campaign: A :class:`SynthesisCampaign` in PLANNED state.

        Returns:
            The same *campaign* object, now in COMPLETED or FAILED state.

        Notes:
            All exceptions from the pipeline are caught and recorded; this
            method never raises.  Callers should inspect ``campaign.status``
            after the call.
        """
        # Mark the campaign as running and record the start time.
        campaign.mark_running()

        try:
            # Build the pipeline configuration from the signal context.
            pipe_cfg = self._build_pipeline_config(campaign.trigger_signal)

            _LOGGER.info(
                "Launching SynthesisFrontierPipeline for campaign %s...",
                campaign.campaign_id[:8],
            )

            # Instantiate the pipeline (lazy import was handled at module top).
            pipeline = SynthesisFrontierPipeline(config=pipe_cfg)

            # Run the pipeline.  This is the long-running blocking call.
            result = pipeline.run()

            _LOGGER.info(
                "Pipeline completed for campaign %s.", campaign.campaign_id[:8]
            )

            # Extract the paper artefact from the result.
            paper = self._extract_paper_from_result(result)

            # Feed propositions into the evidence pipeline.
            if paper is not None:
                ev_records = self.feed_results_to_evidence(paper, campaign)
                campaign.evidence_records = ev_records
                _LOGGER.info(
                    "Injected %d evidence records for campaign %s.",
                    len(ev_records), campaign.campaign_id[:8],
                )

            # Update the output path if the pipeline produced a file.
            output_file = getattr(result, "paper_path", None)
            if output_file:
                campaign.paper_path = str(output_file)

            # Transition to COMPLETED.
            campaign.mark_completed(result)

        except Exception as exc:  # noqa: BLE001
            # Record the failure but do not re-raise.
            campaign.mark_failed(exc)

        finally:
            # Always notify the scheduler that this campaign slot is free.
            self.scheduler.record_completion(campaign)

        return campaign

    # ------------------------------------------------------------------
    def feed_results_to_evidence(
        self, paper: Any, campaign: SynthesisCampaign
    ) -> list[dict]:
        """Delegate paper → evidence conversion to the bridge.

        Args:
            paper: Paper object from the synthesis pipeline.
            campaign: The parent campaign.

        Returns:
            List of evidence record dicts.
        """
        _LOGGER.debug(
            "feed_results_to_evidence called for campaign %s.",
            campaign.campaign_id[:8],
        )
        return self.bridge.paper_to_evidence_records(paper, campaign)

    # ------------------------------------------------------------------
    def run_if_needed(
        self, manifest: dict | None = None
    ) -> SynthesisCampaign | None:
        """Detect a deficit and run a campaign if warranted.

        This is the convenience "one-shot" entry point used by the main
        orchestration loop.  It combines detection, scheduling, and
        execution into a single call.

        Args:
            manifest: Controller manifest dict or ``None``.

        Returns:
            The completed :class:`SynthesisCampaign` if one was run, or
            ``None`` if no deficit was detected or the scheduler refused.

        Notes:
            This method is safe to call on every orchestration tick; it
            will no-op when conditions do not warrant a campaign.
        """
        # Step 1: detect.
        signal = self.detect_theory_deficit(manifest)
        if signal is None:
            _LOGGER.debug("run_if_needed: no deficit detected.")
            return None

        # Step 2: schedule.
        try:
            campaign = self.start_campaign(signal)
        except RuntimeError as exc:
            _LOGGER.info("run_if_needed: scheduler refused — %s", exc)
            return None

        # Step 3: run.
        completed_campaign = self.run_campaign(campaign)
        _LOGGER.info("run_if_needed: %s", completed_campaign.summary())
        return completed_campaign

    # ------------------------------------------------------------------
    def _detect_obstructions_from_manifest(
        self, manifest: dict
    ) -> list[str]:
        """Thin wrapper around the detector's extraction logic.

        Args:
            manifest: Controller manifest dict.

        Returns:
            List of obstruction description strings.
        """
        return self.detector._extract_obstructions(manifest)

    # ------------------------------------------------------------------
    def _recommend_fields_for_obstructions(
        self, obstructions: list[str]
    ) -> list[str]:
        """Thin wrapper around the detector's field-recommendation logic.

        Args:
            obstructions: List of obstruction description strings.

        Returns:
            Ordered list of recommended mathematical field names.
        """
        return self.detector._recommend_fields(obstructions)

    # ------------------------------------------------------------------
    def status(self) -> dict:
        """Return a snapshot of the orchestrator's current state.

        Returns:
            Dict with keys: config, active_campaigns, total_campaigns,
            completed_campaigns, failed_campaigns, scheduler_active,
            frontier_available, evidence_available.
        """
        campaigns = list(self._campaigns.values())
        return {
            "config": self.config.to_dict(),
            "active_campaigns": self.scheduler.active_count(),
            "total_campaigns": len(campaigns),
            "completed_campaigns": sum(
                1 for c in campaigns if c.status == "COMPLETED"
            ),
            "failed_campaigns": sum(
                1 for c in campaigns if c.status == "FAILED"
            ),
            "scheduler_active": self.scheduler.active_count(),
            "frontier_available": _FRONTIER_PIPELINE_AVAILABLE,
            "evidence_available": _EVIDENCE_CHANNELS_AVAILABLE,
        }

    # ------------------------------------------------------------------
    def active_campaigns(self) -> list[SynthesisCampaign]:
        """Return campaigns currently in RUNNING state.

        Returns:
            List of :class:`SynthesisCampaign` with status RUNNING.
        """
        return [c for c in self._campaigns.values() if c.status == "RUNNING"]

    # ------------------------------------------------------------------
    def campaign_history(self) -> list[SynthesisCampaign]:
        """Return all campaigns sorted by start time (most recent first).

        Returns:
            List of all :class:`SynthesisCampaign` objects.
        """
        all_campaigns = list(self._campaigns.values())
        # Sort descending by started_at string (ISO-8601 sorts lexicographically).
        return sorted(all_campaigns, key=lambda c: c.started_at, reverse=True)

    # ------------------------------------------------------------------
    def _build_pipeline_config(
        self, signal: TheoryDeficitSignal
    ) -> Any:
        """Construct a :class:`PipelineConfig` from a deficit signal.

        Args:
            signal: The deficit signal providing field recommendations and
                round limits.

        Returns:
            A :class:`PipelineConfig` (or stub) instance.

        Notes:
            Passes the recommended fields as seed fields so the synthesis
            frontier starts from the most relevant mathematical territory.
        """
        # Derive the round limit: use signal recommendation, then config, then default.
        rounds = (
            signal.recommended_round_limit
            or self.config.max_rounds
            or 6
        )

        # Build the output directory path including a campaign timestamp.
        timestamp_str = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(
            self.config.output_base_dir,
            f"campaign_{timestamp_str}",
        )

        # Build the config.  PipelineConfig may be a stub with **kwargs.
        return PipelineConfig(
            strategy=self.config.default_strategy,
            model=self.config.default_model,
            output_dir=out_dir,
            use_llm=self.config.use_llm,
            max_rounds=rounds,
            seed_fields=list(signal.recommended_fields[:5]),  # Top 5 fields.
        )

    # ------------------------------------------------------------------
    def _extract_paper_from_result(self, result: Any) -> Any:
        """Extract the paper artefact from a :class:`PipelineResult`.

        Args:
            result: Pipeline result object.

        Returns:
            The paper object (or dict), or ``None`` if none is available.
        """
        if result is None:
            return None
        # Try attribute access (dataclass / typed result).
        paper = getattr(result, "paper", None)
        if paper is not None:
            return paper
        # Try dict access (stub result).
        if isinstance(result, dict):
            return result.get("paper")
        return None


# ---------------------------------------------------------------------------
# SynthesisOrchestratorDiagnostics
# ---------------------------------------------------------------------------

class SynthesisOrchestratorDiagnostics:
    """Produces human-readable diagnostic reports for a :class:`SynthesisOrchestrator`.

    Diagnostic reports are written to the log at INFO level and returned as
    strings so that callers can persist them to disk or display them in a UI.

    theory2.tex §271: diagnostics are the observability layer of the
    metatheory management system; they allow operators to understand why
    synthesis campaigns were triggered and what they produced.

    Attributes:
        orchestrator: The orchestrator being diagnosed.
    """

    def __init__(self, orchestrator: SynthesisOrchestrator) -> None:
        """Initialise with a reference to the orchestrator.

        Args:
            orchestrator: The :class:`SynthesisOrchestrator` to report on.
        """
        self.orchestrator = orchestrator

    # ------------------------------------------------------------------
    def report(self) -> str:
        """Produce a full diagnostic report as a multi-line string.

        Returns:
            Human-readable report string covering orchestrator status,
            campaign history, and evidence injection statistics.

        Notes:
            Output is also logged at INFO level for convenience.
        """
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("SYNTHESIS ORCHESTRATOR DIAGNOSTIC REPORT")
        lines.append(f"Generated: {_format_timestamp()}")
        lines.append("=" * 72)

        # --- Status block ---
        status = self.orchestrator.status()
        lines.append(f"  Active campaigns     : {status['active_campaigns']}")
        lines.append(f"  Total campaigns      : {status['total_campaigns']}")
        lines.append(f"  Completed            : {status['completed_campaigns']}")
        lines.append(f"  Failed               : {status['failed_campaigns']}")
        lines.append(f"  Frontier available   : {status['frontier_available']}")
        lines.append(f"  Evidence available   : {status['evidence_available']}")
        lines.append("")

        # --- Campaign table ---
        lines.append(self.campaign_summary_table())
        lines.append("")

        # --- Evidence injection report ---
        lines.append(self.evidence_injection_report())
        lines.append("=" * 72)

        full_report = "\n".join(lines)
        _LOGGER.info("Diagnostics report:\n%s", full_report)
        return full_report

    # ------------------------------------------------------------------
    def campaign_summary_table(self) -> str:
        """Return a tabular summary of all campaigns.

        Returns:
            Multi-line string with one row per campaign.
        """
        history = self.orchestrator.campaign_history()
        if not history:
            return "No campaigns in history."

        # Column widths.
        id_w, status_w, rounds_w, props_w, dur_w = 10, 12, 10, 8, 10

        header = (
            f"{'CAMPAIGN':>{id_w}}  "
            f"{'STATUS':>{status_w}}  "
            f"{'ROUNDS':>{rounds_w}}  "
            f"{'PROPS':>{props_w}}  "
            f"{'DURATION':>{dur_w}}"
        )
        sep = "-" * len(header)

        rows = [header, sep]
        for camp in history:
            dur = camp.duration_seconds()
            dur_str = f"{dur:.1f}s" if dur is not None else "?"
            row = (
                f"{camp.campaign_id[:8]:>{id_w}}  "
                f"{camp.status:>{status_w}}  "
                f"{camp.rounds_completed}/{camp.rounds_total}:>{rounds_w}  "
                f"{camp.propositions_generated:>{props_w}}  "
                f"{dur_str:>{dur_w}}"
            )
            rows.append(row)

        return "\n".join(rows)

    # ------------------------------------------------------------------
    def evidence_injection_report(self) -> str:
        """Summarise evidence record injection across all campaigns.

        Returns:
            Multi-line string summarising total evidence records produced
            and a breakdown by trust tier.
        """
        history = self.orchestrator.campaign_history()
        total_records = sum(len(c.evidence_records) for c in history)

        # Aggregate by trust_tier.
        tier_counts: dict[str, int] = {}
        for camp in history:
            for rec in camp.evidence_records:
                tier = rec.get("trust_tier", "UNKNOWN")
                tier_counts[tier] = tier_counts.get(tier, 0) + 1

        lines: list[str] = [
            "EVIDENCE INJECTION SUMMARY",
            f"  Total evidence records : {total_records}",
        ]
        for tier, count in sorted(tier_counts.items()):
            lines.append(f"    {tier}: {count}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _format_timestamp() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        String of the form ``"2024-01-15T12:34:56.789012"`` (no timezone
        suffix; all timestamps in this module are UTC).

    Notes:
        Using ``datetime.datetime.utcnow()`` rather than ``datetime.datetime.now()``
        ensures consistent UTC behaviour regardless of the host timezone.
    """
    # Use utcnow so that all timestamps are comparable across timezones.
    return datetime.datetime.utcnow().isoformat()


def _coerce_str_list(x: Any) -> list[str]:
    """Coerce *x* to a list of non-empty strings.

    This helper normalises the many shapes that list-of-strings values can
    take when arriving from JSON manifests, config files, or API responses.

    Args:
        x: Input value.  May be a list, tuple, set, single string, or None.

    Returns:
        List of non-empty stripped strings.  Never raises.

    Examples:
        >>> _coerce_str_list(None)
        []
        >>> _coerce_str_list("hello")
        ['hello']
        >>> _coerce_str_list(["a", "", "  b  "])
        ['a', 'b']
    """
    if x is None:
        # None → empty list.
        return []
    if isinstance(x, str):
        # Single string → wrap in list.
        stripped = x.strip()
        return [stripped] if stripped else []
    if isinstance(x, (list, tuple, set)):
        # Iterable → flatten, filter empties, strip whitespace.
        result: list[str] = []
        for item in x:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    result.append(stripped)
            else:
                # Non-string items are converted via str().
                converted = str(item).strip()
                if converted:
                    result.append(converted)
        return result
    # Fallback: convert to string and return.
    return [str(x).strip()] if str(x).strip() else []


# ---------------------------------------------------------------------------
# Smoke-test __main__ block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Configure basic logging so the smoke test produces visible output.
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    _LOGGER.info("=== synthesis_orchestrator smoke test ===")

    # --- 1. Create a detector with default thresholds ---
    detector = TheoryDeficitDetector(density_threshold=0.6, min_obstructions=2)
    _LOGGER.info("Detector created: threshold=%.2f", detector.density_threshold)

    # --- 2. Build a fake manifest that should trigger a deficit ---
    fake_manifest: dict = {
        # 12 out of 15 obligations are obstructed → density ≈ 0.80
        "total_obligations": 15,
        "obstructed_count": 12,
        "obstructed_coordinates": ["coord_A", "coord_B", "coord_C"],
        "obstructions": [
            {
                "message": (
                    "type mismatch: expected dependent type, got universe "
                    "level mismatch in coherence proof"
                ),
            },
            {"message": "functor composition fails: adjunction not established"},
            {"message": "continuity of map cannot be established: open set axiom missing"},
            {"message": "prime factorisation non-unique: ring is not a UFD"},
            {"message": "measure zero argument invalid: sigma-algebra not constructed"},
        ],
    }
    _LOGGER.info("Fake manifest built with %d obstructions.",
                 len(fake_manifest["obstructions"]))

    # --- 3. Run detection ---
    signal = detector.detect(fake_manifest)
    if signal is not None:
        _LOGGER.info("Signal detected: %s", signal.summary())
        print("\nDeficit signal:")
        print(json.dumps(signal.to_dict(), indent=2))
    else:
        _LOGGER.info("No deficit detected.")
        print("No deficit detected.")

    # --- 4. Create orchestrator with use_llm=False for dry-run ---
    orchestrator = SynthesisOrchestrator(config={"use_llm": False, "max_rounds": 2})
    _LOGGER.info("Orchestrator created.")

    # --- 5. Print status ---
    print("\nOrchestrator status:")
    print(json.dumps(orchestrator.status(), indent=2))

    # --- 6. Run if needed (dry-run — pipeline is a stub) ---
    _LOGGER.info("Calling run_if_needed with fake manifest...")
    campaign = orchestrator.run_if_needed(fake_manifest)
    if campaign is not None:
        print("\nCampaign result:")
        print(json.dumps(campaign.to_dict(), indent=2))
    else:
        print("\nrun_if_needed returned None (scheduler refused or no deficit).")

    # --- 7. Diagnostics ---
    diagnostics = SynthesisOrchestratorDiagnostics(orchestrator)
    print("\n" + diagnostics.report())

    _LOGGER.info("=== smoke test complete ===")
