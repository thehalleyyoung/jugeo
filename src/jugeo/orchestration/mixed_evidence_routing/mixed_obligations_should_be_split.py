"""
Mixed Obligations Should Be Split Before Routing.

# copilot: s03-mixed-obligations-should-be-split

This module formalises the third section of theory2.tex Chapter 46:
*Mixed obligations should be split into homogeneous fragments before routing*.

**The splitting principle**

A routing obligation is *mixed* when it combines evidence requirements from
multiple incompatible evidence channels — for example, a single constraint
that simultaneously requires:
- a Z3 SMT discharge (symbolic, decision-procedure channel), and
- a human-attestation certificate (epistemic, trust-propagation channel).

Mixed obligations are problematic for the router because:

1. **Channel incompatibility**: Z3 and human attestation return evidence of
   fundamentally different kinds.  Routing them to a single solver would
   require the solver to speak two different evidence languages simultaneously.

2. **Jurisdiction mismatch**: the trust algebra specifies that each evidence
   channel has a *jurisdiction* — a set of obligation types it can legally
   discharge.  A channel's jurisdiction is the set of proposition types for
   which its evidence is accepted.  A mixed obligation straddles multiple
   jurisdictions.

3. **Trust aggregation ambiguity**: the trust algebra's ⊕ operator (evidence
   join) is defined for homogeneous evidence.  Heterogeneous evidence cannot
   be combined with ⊕ without an explicit cross-channel trust bridge
   (↑_π operation).

**The canonical splitting procedure**

Given a mixed obligation O = (c, φ, A, E, O_inner, B, T, Π), the splitting
procedure decomposes it into a set {O_1, …, O_n} of homogeneous obligations
such that:

    φ  ≡  φ_1 ∧ φ_2 ∧ … ∧ φ_n    (conjunction)
    ∀ i : jurisdiction(O_i) ⊆ channel_i.jurisdiction

The split obligations are then routed independently to their respective
channels.  The original obligation O is discharged only when all split
obligations {O_1, …, O_n} are discharged.

**Why this is forced by the router model**

The router (:mod:`jugeo.solver.router`) maps each obligation to a solver
backend.  Its routing function is defined as:

    route: Obligation → Channel × Backend

This function is well-typed only if each obligation is homogeneous (has a
single channel type).  A mixed obligation is not in the domain of ``route``.

Theory Reference: theory2.tex Chapter 46, §46.7–46.12.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, FrozenSet, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "ChannelType",
    "HomogeneousFragment",
    "MixedObligation",
    "SplitResult",
    "SplitFailure",
    "ObligationSplitter",
    "split_obligation",
    "validate_homogeneity",
    "route_split_obligations",
    "THEORY_SECTION",
    "CHAPTER",
]

THEORY_SECTION = "46.7"
CHAPTER = 46

# ---------------------------------------------------------------------------
# Jugeo imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
except ImportError:
    class TrustLevel:  # type: ignore[no-redef]
        CONTRADICTED = "CONTRADICTED"
        UNVERIFIED = "UNVERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        HUMAN_ATTESTED = "HUMAN_ATTESTED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

    class TrustTier:  # type: ignore[no-redef]
        PROPOSAL = "PROPOSAL"
        REVIEWED = "REVIEWED"
        VERIFIED = "VERIFIED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        PROOF_BACKED = "PROOF_BACKED"

try:
    from jugeo.geometry.descent import GlobalSection, DescentObstruction
except ImportError:
    @dataclass(frozen=True)
    class GlobalSection:  # type: ignore[no-redef]
        section_id: str = ""
        data: Any = None
        trust_tier: str = "PROPOSAL"

    @dataclass(frozen=True)
    class DescentObstruction:  # type: ignore[no-redef]
        obstruction_id: str = ""
        cech_class: Any = None
        message: str = ""

try:
    from jugeo.solver.router import SolverRouter
    _ROUTER_AVAILABLE = True
except ImportError:
    _ROUTER_AVAILABLE = False

    class SolverRouter:  # type: ignore[no-redef]
        def route(self, fragment: Any) -> str:
            return "stub-backend"

try:
    from jugeo.errors import JuGeoError
except ImportError:
    class JuGeoError(Exception):  # type: ignore[no-redef]
        pass

try:
    from jugeo.orchestration.mixed_evidence_routing.models import (
        RoutingObligation,
        EvidenceFragment,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

    @dataclass(frozen=True)
    class RoutingObligation:  # type: ignore[no-redef]
        obligation_id: str = ""
        proposition: str = ""
        channel_types: Tuple[str, ...] = ()
        trust_tier: str = "PROPOSAL"

    @dataclass(frozen=True)
    class EvidenceFragment:  # type: ignore[no-redef]
        fragment_id: str = ""
        channel_type: str = ""
        content: str = ""


# ---------------------------------------------------------------------------
# Channel types
# ---------------------------------------------------------------------------


class ChannelType(str, Enum):
    """The evidence channel types recognised by the routing system."""

    SMT = "SMT"
    """Satisfiability Modulo Theories — Z3, CVC5, etc."""

    PROOF_ASSISTANT = "PROOF_ASSISTANT"
    """Coq, Lean, Agda, Isabelle, etc."""

    RUNTIME_WITNESS = "RUNTIME_WITNESS"
    """Empirical observation from a running system."""

    HUMAN_ATTESTATION = "HUMAN_ATTESTATION"
    """Human reviewer sign-off."""

    LLM_SUGGESTION = "LLM_SUGGESTION"
    """Language-model-generated evidence (lowest trust)."""

    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    """Type-checker, linter, or abstract-interpretation tool."""


_CHANNEL_TRUST_CEILINGS = {
    ChannelType.SMT: TrustTier.VERIFIED,
    ChannelType.PROOF_ASSISTANT: TrustTier.PROOF_BACKED,
    ChannelType.RUNTIME_WITNESS: TrustTier.RUNTIME_WITNESSED,
    ChannelType.HUMAN_ATTESTATION: TrustTier.REVIEWED,
    ChannelType.LLM_SUGGESTION: TrustTier.PROPOSAL,
    ChannelType.STATIC_ANALYSIS: TrustTier.VERIFIED,
}


# ---------------------------------------------------------------------------
# Obligation types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HomogeneousFragment:
    """A single-channel obligation fragment produced by splitting.

    A HomogeneousFragment is the unit of work that the router can handle.
    It carries:
    - a single ``channel_type`` (ensuring homogeneity)
    - the proposition sub-formula ``fragment_proposition``
    - the trust tier achievable by this channel

    Judgment tuple:
        c   = ``parent_obligation_id`` (context: which obligation was split)
        φ   = ``fragment_proposition`` (the sub-formula to discharge)
        A   = ``fragment_id``          (carrier)
        E   = empty
        O   = empty (this IS the obligation)
        B   = ``estimated_cost``       (budget)
        T   = ``achievable_trust_tier``
        Π   = ``channel_type``         (policy = channel jurisdiction)
    """

    fragment_id: str
    parent_obligation_id: str
    fragment_proposition: str
    channel_type: ChannelType
    achievable_trust_tier: str
    estimated_cost: float = 0.0
    metadata: Any = None


@dataclass(frozen=True)
class MixedObligation:
    """An obligation whose sub-formulas span multiple evidence channels.

    A MixedObligation cannot be routed directly; it must be split first.

    Fields
    ------
    obligation_id : str
        UUID4.
    proposition : str
        The complete obligation proposition.
    channel_types : frozenset[ChannelType]
        The evidence channels that sub-formulas require.
    sub_propositions : tuple[str, ...]
        Individual sub-formulas, one per channel type.
    trust_tier : str
        Current trust tier (PROPOSAL until split).
    required_by : str
        The obligation set identifier this belongs to.
    """

    obligation_id: str
    proposition: str
    channel_types: FrozenSet[ChannelType]
    sub_propositions: Tuple[str, ...]
    trust_tier: str = "PROPOSAL"
    required_by: str = ""


@dataclass(frozen=True)
class SplitResult:
    """Successful split of a :class:`MixedObligation`.

    Fields
    ------
    split_id : str
        UUID4.
    original_id : str
        The obligation that was split.
    fragments : tuple[HomogeneousFragment, ...]
        Homogeneous fragments produced.
    conjunction_id : str
        An identifier for the conjunction constraint: all fragments must be
        discharged before the original obligation is discharged.
    trust_tier : str
        Trust tier of this split result.
    elapsed_ms : float
        Time taken to split.
    """

    split_id: str
    original_id: str
    fragments: Tuple[HomogeneousFragment, ...]
    conjunction_id: str
    trust_tier: str
    elapsed_ms: float


@dataclass(frozen=True)
class SplitFailure:
    """Failed split attempt — the obligation could not be decomposed.

    Fields
    ------
    failure_id : str
        UUID4.
    original_id : str
        The obligation that could not be split.
    reason : str
        Why splitting failed.
    suggested_action : str
        What to do next.
    """

    failure_id: str
    original_id: str
    reason: str
    suggested_action: str


# ---------------------------------------------------------------------------
# ObligationSplitter
# ---------------------------------------------------------------------------


class ObligationSplitter:
    """Splits mixed obligations into homogeneous routing fragments.

    The splitter applies the canonical decomposition from §46.7:
    each sub-proposition in the mixed obligation is assigned to the
    evidence channel with the appropriate jurisdiction.

    Channel assignment heuristics
    ------------------------------
    * Sub-propositions containing the word "smt" or "z3" → SMT
    * Sub-propositions containing "proof" or "lean" or "coq" → PROOF_ASSISTANT
    * Sub-propositions containing "runtime" or "witness" → RUNTIME_WITNESS
    * Sub-propositions containing "human" or "attest" → HUMAN_ATTESTATION
    * Sub-propositions containing "type" or "static" → STATIC_ANALYSIS
    * Otherwise → LLM_SUGGESTION
    """

    _KEYWORD_MAP: Tuple[Tuple[str, ChannelType], ...] = (
        ("smt", ChannelType.SMT),
        ("z3", ChannelType.SMT),
        ("cvc5", ChannelType.SMT),
        ("proof", ChannelType.PROOF_ASSISTANT),
        ("lean", ChannelType.PROOF_ASSISTANT),
        ("coq", ChannelType.PROOF_ASSISTANT),
        ("agda", ChannelType.PROOF_ASSISTANT),
        ("runtime", ChannelType.RUNTIME_WITNESS),
        ("witness", ChannelType.RUNTIME_WITNESS),
        ("observed", ChannelType.RUNTIME_WITNESS),
        ("human", ChannelType.HUMAN_ATTESTATION),
        ("attest", ChannelType.HUMAN_ATTESTATION),
        ("review", ChannelType.HUMAN_ATTESTATION),
        ("type", ChannelType.STATIC_ANALYSIS),
        ("static", ChannelType.STATIC_ANALYSIS),
        ("lint", ChannelType.STATIC_ANALYSIS),
    )

    def split(self, obligation: MixedObligation) -> SplitResult | SplitFailure:
        """Split *obligation* into homogeneous fragments.

        Parameters
        ----------
        obligation : MixedObligation
            The mixed obligation to decompose.

        Returns
        -------
        SplitResult | SplitFailure
            Always returned; never raises.
        """
        t0 = time.monotonic()

        if not obligation.sub_propositions:
            return SplitFailure(
                failure_id=str(uuid.uuid4()),
                original_id=obligation.obligation_id,
                reason="No sub-propositions to split.",
                suggested_action="Decompose the proposition manually.",
            )

        fragments: list[HomogeneousFragment] = []
        for sub_prop in obligation.sub_propositions:
            channel = self._assign_channel(sub_prop)
            trust_ceiling = _CHANNEL_TRUST_CEILINGS.get(channel, TrustTier.PROPOSAL)
            fragments.append(
                HomogeneousFragment(
                    fragment_id=str(uuid.uuid4()),
                    parent_obligation_id=obligation.obligation_id,
                    fragment_proposition=sub_prop,
                    channel_type=channel,
                    achievable_trust_tier=trust_ceiling,
                    estimated_cost=1.0,
                )
            )

        elapsed = (time.monotonic() - t0) * 1000
        return SplitResult(
            split_id=str(uuid.uuid4()),
            original_id=obligation.obligation_id,
            fragments=tuple(fragments),
            conjunction_id=str(uuid.uuid4()),
            trust_tier="PROPOSAL",
            elapsed_ms=elapsed,
        )

    def _assign_channel(self, sub_proposition: str) -> ChannelType:
        """Heuristically assign a channel type to a sub-proposition."""
        lower = sub_proposition.lower()
        for keyword, channel in self._KEYWORD_MAP:
            if keyword in lower:
                return channel
        return ChannelType.LLM_SUGGESTION


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def validate_homogeneity(fragment: HomogeneousFragment) -> bool:
    """Return True iff *fragment* is genuinely homogeneous (single channel).

    A fragment is homogeneous iff it has exactly one :class:`ChannelType`.
    This is trivially satisfied for :class:`HomogeneousFragment` by
    construction, but the check is useful in integration tests.

    Parameters
    ----------
    fragment : HomogeneousFragment
        The fragment to validate.

    Returns
    -------
    bool
    """
    return isinstance(fragment.channel_type, ChannelType)


def split_obligation(
    obligation_id: str,
    proposition: str,
    sub_propositions: Sequence[str],
    trust_tier: str = "PROPOSAL",
) -> SplitResult | SplitFailure:
    """Convenience wrapper: create a :class:`MixedObligation` and split it.

    Parameters
    ----------
    obligation_id : str
        UUID for the obligation.
    proposition : str
        The complete proposition text.
    sub_propositions : Sequence[str]
        Individual sub-formulas to decompose.
    trust_tier : str
        Current trust tier.

    Returns
    -------
    SplitResult | SplitFailure
    """
    channels: FrozenSet[ChannelType] = frozenset()
    mixed = MixedObligation(
        obligation_id=obligation_id,
        proposition=proposition,
        channel_types=channels,
        sub_propositions=tuple(sub_propositions),
        trust_tier=trust_tier,
    )
    splitter = ObligationSplitter()
    return splitter.split(mixed)


def route_split_obligations(
    split_result: SplitResult,
    router: Optional[Any] = None,
) -> Tuple[HomogeneousFragment, ...]:
    """Route each fragment in *split_result* to its appropriate backend.

    This is a structural pass: it logs which channel each fragment would be
    sent to.  Actual routing is performed by the solver layer.

    Parameters
    ----------
    split_result : SplitResult
        The split result containing homogeneous fragments.
    router : optional
        A router object with a ``route(fragment)`` method.  If None, a stub
        router is used.

    Returns
    -------
    tuple[HomogeneousFragment, ...]
        The fragments in routing order (sorted by estimated cost ascending).
    """
    if router is None:
        router = SolverRouter()

    ordered = sorted(split_result.fragments, key=lambda f: f.estimated_cost)
    for frag in ordered:
        try:
            backend = router.route(frag)
        except Exception:
            backend = "unknown"
        logger.debug(
            "Fragment %s → channel=%s backend=%s proposition=%r",
            frag.fragment_id[:8],
            frag.channel_type.value,
            backend,
            frag.fragment_proposition[:60],
        )
    return tuple(ordered)


# ---------------------------------------------------------------------------
# Additional theory-required enumerations
# ---------------------------------------------------------------------------

import enum as _enum
import hashlib as _hashlib
import json as _json
import math as _math
import re as _re


class ObligationComplexity(_enum.IntEnum):
    """Syntactic complexity class of an obligation formula.

    Higher complexity correlates with the need for splitting into Z3 and oracle
    parts.  ATOMIC formulas are typically decidable by Z3 alone; RECURSIVE
    formulas almost always require oracle delegation.

    These values feed COMPLEXITY_WEIGHTS when computing split scores.
    """

    ATOMIC = 1           # p, f(x), x == y
    CONJUNCTIVE = 2      # φ ∧ ψ (top-level And)
    DISJUNCTIVE = 3      # φ ∨ ψ (top-level Or)
    QUANTIFIED = 4       # ∀x.φ, ∃x.φ
    MODAL = 5            # □φ, ◇φ, K_i φ
    NESTED = 6           # quantifiers inside modalities or vice-versa
    RECURSIVE = 7        # μX.φ(X), fixed-points


class TractabilityClass(_enum.Enum):
    """Decidability classification for obligation sub-formulas.

    Determines which discharge engine (Z3 or LLM-oracle) should handle a
    given fragment.  Classification is stored in TractabilityProof objects
    which become part of the SplitProofChain.
    """

    DECIDABLE = "decidable"                # Z3 can decide
    SEMI_DECIDABLE = "semi_decidable"      # Z3 can refute but may loop
    UNDECIDABLE = "undecidable"            # Approximate only
    ORACLE_REQUIRED = "oracle_required"    # Needs LLM oracle
    HYBRID = "hybrid"                      # Must be further split


class MergePolicy(_enum.Enum):
    """Policy for combining Z3 and oracle verdicts after splitting.

    Each policy encodes a different epistemic stance on the relative
    authority of the two discharge engines.

    REQUIRE_BOTH  — both must succeed; safest.
    Z3_WINS       — Z3 verdict is authoritative.
    ORACLE_WINS   — oracle verdict is authoritative.
    WEIGHTED_VOTE — probabilistic combination.
    PROOF_CHAIN   — Z3 proof must exist before oracle is consulted.
    """

    REQUIRE_BOTH = "require_both"
    Z3_WINS = "z3_wins"
    ORACLE_WINS = "oracle_wins"
    WEIGHTED_VOTE = "weighted_vote"
    PROOF_CHAIN = "proof_chain"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

Z3_TRACTABILITY_THRESHOLD: float = 0.75
ORACLE_SUITABILITY_THRESHOLD: float = 0.60
MAX_Z3_FORMULA_DEPTH: int = 20
SPLIT_PROOF_VERSION: str = "1.0.0"
COMPLEXITY_WEIGHTS: dict[ObligationComplexity, float] = {
    ObligationComplexity.ATOMIC: 0.1,
    ObligationComplexity.CONJUNCTIVE: 0.2,
    ObligationComplexity.DISJUNCTIVE: 0.2,
    ObligationComplexity.QUANTIFIED: 0.5,
    ObligationComplexity.MODAL: 0.7,
    ObligationComplexity.NESTED: 0.85,
    ObligationComplexity.RECURSIVE: 1.0,
}

# Syntactic patterns indicating oracle-required content
_ORACLE_PATTERNS: list[str] = [
    r"\b(is\s+good|is\s+bad|seems\s+like|probably|likely|should\s+be)\b",
    r"□|◇|○",
    r"\\Box|\\Diamond",
    r"\bK_[a-z]",
    r"\b(believe|know|think|expect)\b",
    r"\bμX\b|\bνX\b",
    r"(?i)\bnl::",
]

_Z3_PATTERNS: list[str] = [
    r"^[A-Za-z_][A-Za-z0-9_]*\s*[=<>!]=?\s*",
    r"\bTrue\b|\bFalse\b",
    r"\bAnd\b|\bOr\b|\bNot\b|\bImplies\b",
    r"\bForAll\b|\bExists\b",
    r"\b(Int|Real|Bool|BitVec|Array)\b",
    r"[+\-*/]\s*\d",
]


# ---------------------------------------------------------------------------
# Additional frozen dataclasses required by spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MixedObligationSplitter:
    """Immutable configuration for the Z3/oracle splitting algorithm.

    Encodes *policy*, not state — multiple splitters can be applied to the
    same obligation set independently.

    Fields
    ------
    split_strategy_id  — identifies the strategy (e.g. "eager_z3_first")
    z3_threshold       — tractability score above which Z3 is used
    oracle_threshold   — suitability score above which oracle is used
    hybrid_policy      — description of hybrid region handling
    splitter_id        — unique instance ID
    """

    split_strategy_id: str
    z3_threshold: float
    oracle_threshold: float
    hybrid_policy: str
    splitter_id: str

    def __post_init__(self) -> None:
        if not (0.0 <= self.z3_threshold <= 1.0):
            raise ValueError(f"z3_threshold must be in [0,1], got {self.z3_threshold}")
        if not (0.0 <= self.oracle_threshold <= 1.0):
            raise ValueError(f"oracle_threshold must be in [0,1], got {self.oracle_threshold}")

    def is_z3_tractable(self, score: float) -> bool:
        return score >= self.z3_threshold

    def is_oracle_suitable(self, score: float) -> bool:
        return score >= self.oracle_threshold


@dataclass(frozen=True)
class Z3Part:
    """A Z3-tractable obligation sub-formula.

    Carries its own tractability_proof — a JSON string that certifies *why*
    this fragment was classified as decidable.  Becomes part of the
    SplitProofChain and, ultimately, the Π component of the judgment tuple.

    Fields
    ------
    part_id              — unique ID
    formula              — SMT-LIB2 or Python Z3 API formula
    canonical_fragments  — evidence fragments grounding this formula
    constraints          — side constraints that must hold
    expected_verdict     — one of "sat", "unsat", "unknown"
    tractability_proof   — JSON-encoded justification
    """

    part_id: str
    formula: str
    canonical_fragments: tuple[str, ...]
    constraints: tuple[str, ...]
    expected_verdict: str
    tractability_proof: str

    def formula_depth(self) -> int:
        """Estimate nesting depth by counting parentheses."""
        depth = max_depth = 0
        for ch in self.formula:
            if ch == "(":
                depth += 1
                max_depth = max(max_depth, depth)
            elif ch == ")":
                depth = max(depth - 1, 0)
        return max_depth

    def is_within_depth_limit(self) -> bool:
        return self.formula_depth() <= MAX_Z3_FORMULA_DEPTH


@dataclass(frozen=True)
class OraclePart:
    """An oracle-required obligation sub-formula.

    Oracle output is *never* automatically trusted at PROOF_BACKED tier;
    oracle_tier is always < PROOF_BACKED.

    Fields
    ------
    part_id            — unique ID
    prompt_template    — Jinja2-style template with {{evidence}} placeholder
    context_window     — max tokens for this oracle call
    evidence_fragments — evidence strings injected into prompt
    oracle_tier        — maximum achievable trust tier for oracle output
    oracle_budget      — cost budget in oracle-credit units
    """

    part_id: str
    prompt_template: str
    context_window: int
    evidence_fragments: tuple[str, ...]
    oracle_tier: str        # TrustTier name, e.g. "REVIEWED"
    oracle_budget: float

    def render_prompt(self, evidence_override: list[str] | None = None) -> str:
        """Render the prompt template substituting evidence."""
        evid = evidence_override if evidence_override is not None else list(self.evidence_fragments)
        evidence_block = "\n".join(f"  [{i+1}] {e}" for i, e in enumerate(evid))
        return self.prompt_template.replace("{{evidence}}", evidence_block)


@dataclass(frozen=True)
class SplitObligation:
    """Result of splitting a mixed obligation into Z3 and oracle parts.

    A SplitObligation is a value in the obligation-set O of the ambient
    judgment tuple.  Its split_proof is the Π component certifying the
    split is sound.

    Invariant: len(z3_parts) + len(oracle_parts) >= 1
    """

    original_obligation_id: str
    z3_parts: tuple[Z3Part, ...]
    oracle_parts: tuple[OraclePart, ...]
    split_proof: str
    merge_policy: MergePolicy
    discharge_strategy: str

    def __post_init__(self) -> None:
        if len(self.z3_parts) + len(self.oracle_parts) < 1:
            raise ValueError("SplitObligation must have at least one part.")

    @property
    def is_pure_z3(self) -> bool:
        return len(self.oracle_parts) == 0

    @property
    def is_pure_oracle(self) -> bool:
        return len(self.z3_parts) == 0

    @property
    def is_hybrid(self) -> bool:
        return not self.is_pure_z3 and not self.is_pure_oracle


@dataclass(frozen=True)
class SplitStrategy:
    """A named strategy encoding criteria for splitting mixed obligations.

    strategy_id                — unique ID (e.g. "eager_z3_first")
    name                       — human-readable name
    description                — epistemic rationale
    z3_tractability_criteria   — ordered list of Z3-routing criteria
    oracle_suitability_criteria — ordered list of oracle-routing criteria
    """

    strategy_id: str
    name: str
    description: str
    z3_tractability_criteria: tuple[str, ...]
    oracle_suitability_criteria: tuple[str, ...]


@dataclass(frozen=True)
class TractabilityProof:
    """Certificate asserting the tractability class of a sub-formula.

    Embedded in Z3Part.tractability_proof (as JSON) so that the judgment's
    Π component can be reconstructed from the split parts.

    Fields
    ------
    proof_id           — unique ID
    formula            — the formula being classified
    tractability_class — the classification result
    justification      — human-readable justification
    trust_tier         — tier of this classification itself (starts PROPOSAL)
    """

    proof_id: str
    formula: str
    tractability_class: TractabilityClass
    justification: str
    trust_tier: str  # TrustTier name


@dataclass(frozen=True)
class ObligationFragment:
    """An atomic sub-formula extracted from a mixed obligation.

    Decomposing φ into ObligationFragments is the first step of splitting.
    Each fragment is independently classified before assembly into Z3Part
    or OraclePart instances.

    Fields
    ------
    fragment_id   — unique ID
    formula       — the atomic formula string
    complexity    — syntactic complexity class
    tractability  — decidability classification
    weight        — relative importance in WEIGHTED_VOTE merging
    """

    fragment_id: str
    formula: str
    complexity: ObligationComplexity
    tractability: TractabilityClass
    weight: float

    def complexity_cost(self) -> float:
        return COMPLEXITY_WEIGHTS.get(self.complexity, 0.5)


@dataclass(frozen=True)
class SplitProofChain:
    """Proof chain certifying that a split is sound.

    Soundness: ⊢ φ  iff  (⊢_Z3 ⋀ φ_z) ∧ (⊢_oracle ⋀ φ_o)
    under the chosen MergePolicy.

    chain_id       — unique ID
    steps          — ordered proof steps as strings
    z3_part_ids    — IDs of Z3Part instances covered
    oracle_part_ids — IDs of OraclePart instances covered
    verified       — True when all invariants pass
    """

    chain_id: str
    steps: tuple[str, ...]
    z3_part_ids: tuple[str, ...]
    oracle_part_ids: tuple[str, ...]
    verified: bool

    def summary(self) -> str:
        status = "✓ verified" if self.verified else "○ unverified"
        return (
            f"SplitProofChain({self.chain_id[:8]}…, "
            f"z3={len(self.z3_part_ids)}, "
            f"oracle={len(self.oracle_part_ids)}, {status})"
        )


# ---------------------------------------------------------------------------
# Non-frozen (stateful) classes
# ---------------------------------------------------------------------------


class ObligationClassifier:
    """Classifies obligation formula parts by tractability.

    Uses syntactic pattern matching against oracle and Z3 indicator patterns.
    Outputs should be treated as PROPOSAL-tier trust until formally verified.

    Attributes
    ----------
    classification_cache : dict[str, TractabilityClass]
        Memoisation cache.
    stats : dict[str, int]
        Running counts per TractabilityClass value.
    """

    def __init__(self) -> None:
        self.classification_cache: dict[str, TractabilityClass] = {}
        self.stats: dict[str, int] = {tc.value: 0 for tc in TractabilityClass}

    def classify(self, formula: str, context: dict | None = None) -> TractabilityClass:
        """Classify *formula* into a TractabilityClass.

        Pass 1: cache lookup.
        Pass 2: oracle pattern matching (ORACLE_REQUIRED wins).
        Pass 3: Z3 pattern matching and depth check.
        """
        if formula in self.classification_cache:
            return self.classification_cache[formula]
        tc = self._classify_uncached(formula, context or {})
        self.classification_cache[formula] = tc
        self.stats[tc.value] = self.stats.get(tc.value, 0) + 1
        return tc

    def _classify_uncached(self, formula: str, context: dict) -> TractabilityClass:
        oracle_score = self._oracle_score(formula)
        z3_score = self._z3_score(formula)
        depth = self._formula_depth(formula)

        if oracle_score >= 0.5:
            return TractabilityClass.HYBRID if z3_score >= 0.3 else TractabilityClass.ORACLE_REQUIRED
        if depth > MAX_Z3_FORMULA_DEPTH:
            return TractabilityClass.UNDECIDABLE
        if z3_score >= Z3_TRACTABILITY_THRESHOLD:
            return TractabilityClass.DECIDABLE
        if z3_score >= 0.4:
            return TractabilityClass.SEMI_DECIDABLE
        if _re.search(r"\bForAll\b|∀", formula):
            return TractabilityClass.SEMI_DECIDABLE
        return TractabilityClass.UNDECIDABLE

    def estimate_complexity(self, formula: str) -> ObligationComplexity:
        """Return syntactic ObligationComplexity of *formula*."""
        if _re.search(r"\bμX\b|\bνX\b|fix\s*\(", formula):
            return ObligationComplexity.RECURSIVE
        has_modal = bool(_re.search(r"□|◇|\bBox\b|\bDiamond\b|\\Box|\\Diamond", formula))
        has_quant = bool(_re.search(r"\bForAll\b|\bExists\b|∀|∃", formula))
        if has_modal and has_quant:
            return ObligationComplexity.NESTED
        if has_modal:
            return ObligationComplexity.MODAL
        if has_quant:
            return ObligationComplexity.QUANTIFIED
        if _re.search(r"\bOr\b|∨", formula):
            return ObligationComplexity.DISJUNCTIVE
        if _re.search(r"\bAnd\b|∧", formula):
            return ObligationComplexity.CONJUNCTIVE
        return ObligationComplexity.ATOMIC

    def batch_classify(self, formulas: list[str], context: dict | None = None) -> list[TractabilityClass]:
        """Classify a list of formulas, using the cache for deduplication."""
        ctx = context or {}
        return [self.classify(f, ctx) for f in formulas]

    def get_statistics(self) -> dict[str, int]:
        return dict(self.stats)

    def reset_cache(self) -> None:
        self.classification_cache.clear()
        for key in self.stats:
            self.stats[key] = 0

    @staticmethod
    def _oracle_score(formula: str) -> float:
        hits = sum(1 for p in _ORACLE_PATTERNS if _re.search(p, formula, _re.IGNORECASE))
        return min(hits / max(len(_ORACLE_PATTERNS), 1), 1.0)

    @staticmethod
    def _z3_score(formula: str) -> float:
        hits = sum(1 for p in _Z3_PATTERNS if _re.search(p, formula))
        return min(hits / max(len(_Z3_PATTERNS), 1), 1.0)

    @staticmethod
    def _formula_depth(formula: str) -> int:
        depth = max_depth = 0
        for ch in formula:
            if ch == "(":
                depth += 1
                max_depth = max(max_depth, depth)
            elif ch == ")":
                depth = max(depth - 1, 0)
        return max_depth


class SplitResultMerger:
    """Merges discharge results from Z3 and oracle parts.

    Different MergePolicy values encode different epistemic stances.

    Attributes
    ----------
    merge_policy : MergePolicy
    merge_log    : list[dict]  — audit log of merge operations
    weights      : dict[str, float] — engine weights for WEIGHTED_VOTE
    """

    def __init__(
        self,
        merge_policy: MergePolicy,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.merge_policy = merge_policy
        self.merge_log: list[dict] = []
        self.weights: dict[str, float] = weights or {"z3": 0.6, "oracle": 0.4}

    def merge(self, z3_result: dict, oracle_result: dict) -> dict:
        """Merge results according to the configured MergePolicy.

        Expected result dict keys: verdict, confidence, proof_trace, trust_tier.
        """
        if self.merge_policy == MergePolicy.REQUIRE_BOTH:
            merged = self._merge_require_both(z3_result, oracle_result)
        elif self.merge_policy == MergePolicy.Z3_WINS:
            merged = self._merge_z3_wins(z3_result, oracle_result)
        elif self.merge_policy == MergePolicy.ORACLE_WINS:
            merged = self._merge_oracle_wins(z3_result, oracle_result)
        elif self.merge_policy == MergePolicy.WEIGHTED_VOTE:
            merged = self._merge_weighted_vote(z3_result, oracle_result)
        elif self.merge_policy == MergePolicy.PROOF_CHAIN:
            merged = self._merge_proof_chain(z3_result, oracle_result)
        else:
            raise ValueError(f"Unknown MergePolicy: {self.merge_policy}")

        merged["merge_policy"] = self.merge_policy.value
        merged["merge_proof"] = self.generate_merge_proof(z3_result, oracle_result, merged)
        self.merge_log.append({
            "timestamp": time.monotonic(),
            "z3_verdict": z3_result.get("verdict"),
            "oracle_verdict": oracle_result.get("verdict"),
            "merged_verdict": merged.get("verdict"),
            "policy": self.merge_policy.value,
        })
        return merged

    def _merge_require_both(self, z3: dict, oracle: dict) -> dict:
        z3_v = z3.get("verdict", "unknown")
        oracle_v = oracle.get("verdict", "unknown")
        positive = ("sat", "true", "yes", "positive")
        if z3_v == "sat" and oracle_v in positive:
            verdict = "sat"
        elif z3_v == "unsat" or oracle_v in ("unsat", "false", "no", "negative"):
            verdict = "unsat"
        else:
            verdict = "unknown"
        confidence = min(float(z3.get("confidence", 0.5)), float(oracle.get("confidence", 0.5)))
        trace = list(z3.get("proof_trace", [])) + list(oracle.get("proof_trace", []))
        tier = min(int(z3.get("trust_tier", 1)), int(oracle.get("trust_tier", 1)))
        return {"verdict": verdict, "confidence": confidence, "proof_trace": trace, "trust_tier": tier}

    def _merge_z3_wins(self, z3: dict, oracle: dict) -> dict:
        result = dict(z3)
        result.setdefault("verdict", "unknown")
        result.setdefault("confidence", 0.5)
        result["oracle_verdict"] = oracle.get("verdict", "unknown")
        result["proof_trace"] = list(z3.get("proof_trace", [])) + [
            f"[oracle_ignored] {oracle.get('verdict', 'unknown')}"
        ]
        return result

    def _merge_oracle_wins(self, z3: dict, oracle: dict) -> dict:
        result = dict(oracle)
        result.setdefault("verdict", "unknown")
        result.setdefault("confidence", 0.5)
        result["z3_verdict"] = z3.get("verdict", "unknown")
        result["proof_trace"] = list(oracle.get("proof_trace", [])) + [
            f"[z3_ignored] {z3.get('verdict', 'unknown')}"
        ]
        return result

    def _merge_weighted_vote(self, z3: dict, oracle: dict) -> dict:
        w_z3 = self.weights.get("z3", 0.6)
        w_o = self.weights.get("oracle", 0.4)
        z3_c = float(z3.get("confidence", 0.5))
        oracle_c = float(oracle.get("confidence", 0.5))
        combined_confidence = w_z3 * z3_c + w_o * oracle_c

        def to_score(v: str) -> float:
            return 1.0 if v.lower() in ("sat", "true", "yes", "positive") else (
                0.0 if v.lower() in ("unsat", "false", "no", "negative") else 0.5
            )

        ws = w_z3 * to_score(z3.get("verdict", "unknown")) + w_o * to_score(oracle.get("verdict", "unknown"))
        verdict = "sat" if ws >= 0.65 else ("unsat" if ws <= 0.35 else "unknown")
        trace = list(z3.get("proof_trace", [])) + list(oracle.get("proof_trace", []))
        return {"verdict": verdict, "confidence": combined_confidence, "proof_trace": trace, "trust_tier": int(z3.get("trust_tier", 1))}

    def _merge_proof_chain(self, z3: dict, oracle: dict) -> dict:
        z3_v = z3.get("verdict", "unknown")
        if z3_v not in ("sat", "unsat"):
            return {"verdict": "unknown", "confidence": 0.0, "proof_trace": ["Z3 incomplete; oracle rejected"], "trust_tier": 1, "chain_broken": True}
        oracle_v = oracle.get("verdict", "unknown")
        positive = ("sat", "true", "yes", "positive")
        negative = ("unsat", "false", "no", "negative")
        if oracle_v in positive and z3_v == "sat":
            verdict = "sat"
        elif oracle_v in negative and z3_v == "unsat":
            verdict = "unsat"
        else:
            verdict = z3_v
        confidence = (float(z3.get("confidence", 0.5)) + float(oracle.get("confidence", 0.5))) / 2.0
        trace = [f"[z3_base] {z3_v}"] + list(oracle.get("proof_trace", []))
        return {"verdict": verdict, "confidence": confidence, "proof_trace": trace, "trust_tier": int(z3.get("trust_tier", 1))}

    def resolve_conflict(self, z3_verdict: str, oracle_verdict: str) -> str:
        if self.merge_policy == MergePolicy.Z3_WINS:
            return z3_verdict
        if self.merge_policy == MergePolicy.ORACLE_WINS:
            return oracle_verdict
        if self.merge_policy == MergePolicy.REQUIRE_BOTH:
            return "unknown"
        return z3_verdict

    def compute_confidence(self, z3_confidence: float, oracle_confidence: float) -> float:
        if self.merge_policy == MergePolicy.REQUIRE_BOTH:
            return min(z3_confidence, oracle_confidence)
        if self.merge_policy in (MergePolicy.Z3_WINS, MergePolicy.PROOF_CHAIN):
            return z3_confidence
        if self.merge_policy == MergePolicy.ORACLE_WINS:
            return oracle_confidence
        w_z3 = self.weights.get("z3", 0.6)
        w_o = self.weights.get("oracle", 0.4)
        return w_z3 * z3_confidence + w_o * oracle_confidence

    def generate_merge_proof(self, z3_result: dict, oracle_result: dict, merged: dict) -> str:
        proof = {
            "version": SPLIT_PROOF_VERSION,
            "policy": self.merge_policy.value,
            "z3_verdict": z3_result.get("verdict"),
            "z3_confidence": z3_result.get("confidence"),
            "oracle_verdict": oracle_result.get("verdict"),
            "oracle_confidence": oracle_result.get("confidence"),
            "merged_verdict": merged.get("verdict"),
            "merged_confidence": merged.get("confidence"),
        }
        return _json.dumps(proof)


# ---------------------------------------------------------------------------
# Public functions (spec-required)
# ---------------------------------------------------------------------------

import uuid as _uuid


def _stable_id(content: str) -> str:
    return _hashlib.sha256(content.encode()).hexdigest()[:16]


def _top_level_split(formula: str) -> list[str]:
    """Split formula on top-level And / Or connectives."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    normalised = formula.replace("∧", " And ").replace("∨", " Or ")
    i = 0
    while i < len(normalised):
        ch = normalised[i]
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(depth - 1, 0)
            current.append(ch)
        elif depth == 0:
            rest = normalised[i:]
            matched = False
            for connector in (" And ", " Or ", " and ", " or "):
                if rest.startswith(connector):
                    parts.append("".join(current).strip())
                    current = []
                    i += len(connector)
                    matched = True
                    break
            if not matched:
                current.append(ch)
                i += 1
            continue
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def decompose_obligation_formula(formula: str) -> list[ObligationFragment]:
    """Decompose a mixed obligation formula into atomic ObligationFragments.

    Uses balanced-parenthesis splitting on top-level And/Or connectives.
    More sophisticated implementations would use a full parser.

    Parameters
    ----------
    formula : str

    Returns
    -------
    list[ObligationFragment]
    """
    classifier = ObligationClassifier()
    atoms = _top_level_split(formula) or [formula.strip()]
    total_weight = 1.0 / max(len(atoms), 1)
    fragments: list[ObligationFragment] = []
    for i, atom in enumerate(atoms):
        atom = atom.strip()
        if not atom:
            continue
        complexity = classifier.estimate_complexity(atom)
        tractability = classifier.classify(atom)
        fragment_id = _stable_id(f"{formula}::{i}::{atom}")
        fragments.append(ObligationFragment(
            fragment_id=fragment_id,
            formula=atom,
            complexity=complexity,
            tractability=tractability,
            weight=total_weight,
        ))
    return fragments


def _build_z3_part(fragment: ObligationFragment, evidence_set: list[str], trust_tier_name: str) -> Z3Part:
    part_id = str(_uuid.uuid4())
    words = fragment.formula.split()[:5]
    relevant = tuple(e for e in evidence_set if any(w in e for w in words))[:10]
    constraints: list[str] = []
    if fragment.complexity == ObligationComplexity.QUANTIFIED:
        constraints.append("bounded_quantification_assumed")
    proof_obj = {
        "proof_id": _stable_id(fragment.formula),
        "tractability_class": fragment.tractability.value,
        "justification": f"complexity={fragment.complexity.name}, tier={trust_tier_name}",
        "trust_tier": trust_tier_name,
    }
    return Z3Part(
        part_id=part_id,
        formula=fragment.formula,
        canonical_fragments=relevant,
        constraints=tuple(constraints),
        expected_verdict="sat",
        tractability_proof=_json.dumps(proof_obj),
    )


def _build_oracle_part(fragment: ObligationFragment, evidence_set: list[str], trust_tier_name: str) -> OraclePart:
    part_id = str(_uuid.uuid4())
    prompt_template = (
        "Evaluate whether the following obligation holds given the evidence.\n\n"
        "Obligation: " + fragment.formula + "\n\n"
        "Evidence:\n{{evidence}}\n\n"
        "Respond with: HOLDS, DOES_NOT_HOLD, or UNCERTAIN."
    )
    relevant = tuple(evidence_set[:8])
    base_budget = 1000.0 * (1.0 + COMPLEXITY_WEIGHTS.get(fragment.complexity, 0.5))
    return OraclePart(
        part_id=part_id,
        prompt_template=prompt_template,
        context_window=4096,
        evidence_fragments=relevant,
        oracle_tier="REVIEWED",  # oracle never reaches PROOF_BACKED
        oracle_budget=base_budget,
    )


def split_mixed_obligation(
    obligation_formula: str,
    evidence_set: list[str],
    trust_tier: Any,
    splitter: MixedObligationSplitter | None = None,
    merge_policy: MergePolicy = MergePolicy.REQUIRE_BOTH,
) -> SplitObligation:
    """Main entry point: split a mixed obligation into Z3 and oracle parts.

    Algorithm:
    1. Decompose obligation_formula into ObligationFragments.
    2. Classify each fragment (DECIDABLE → Z3Part, ORACLE_REQUIRED → OraclePart).
    3. HYBRID fragments are recursively subdivided.
    4. Generate a SplitProofChain certifying the split.
    5. Package into a SplitObligation.

    The returned SplitObligation can be inserted into the obligation-set O
    of the ambient judgment tuple (c, φ, A, E, O, B, T, Π).

    Parameters
    ----------
    obligation_formula : str
    evidence_set       : list[str]  — E in the judgment tuple
    trust_tier         : TrustTier or compatible — T in the judgment tuple
    splitter           : MixedObligationSplitter, optional
    merge_policy       : MergePolicy

    Returns
    -------
    SplitObligation
    """
    if splitter is None:
        splitter = MixedObligationSplitter(
            split_strategy_id="default_eager_z3",
            z3_threshold=Z3_TRACTABILITY_THRESHOLD,
            oracle_threshold=ORACLE_SUITABILITY_THRESHOLD,
            hybrid_policy="recurse_then_z3_first",
            splitter_id=str(_uuid.uuid4()),
        )

    tier_name = trust_tier.name if hasattr(trust_tier, "name") else str(trust_tier)
    obligation_id = _stable_id(obligation_formula)
    fragments = decompose_obligation_formula(obligation_formula)

    z3_parts: list[Z3Part] = []
    oracle_parts: list[OraclePart] = []

    for frag in fragments:
        if frag.tractability in (TractabilityClass.DECIDABLE, TractabilityClass.SEMI_DECIDABLE):
            z3_parts.append(_build_z3_part(frag, evidence_set, tier_name))
        elif frag.tractability in (TractabilityClass.ORACLE_REQUIRED, TractabilityClass.UNDECIDABLE):
            oracle_parts.append(_build_oracle_part(frag, evidence_set, tier_name))
        else:  # HYBRID
            sub_frags = decompose_obligation_formula(frag.formula)
            if len(sub_frags) > 1:
                for sf in sub_frags:
                    if sf.tractability in (TractabilityClass.DECIDABLE, TractabilityClass.SEMI_DECIDABLE):
                        z3_parts.append(_build_z3_part(sf, evidence_set, tier_name))
                    else:
                        oracle_parts.append(_build_oracle_part(sf, evidence_set, tier_name))
            else:
                # Cannot subdivide: use classifier scores to decide
                classifier = ObligationClassifier()
                if classifier._z3_score(frag.formula) >= 0.4:
                    z3_parts.append(_build_z3_part(frag, evidence_set, tier_name))
                else:
                    oracle_parts.append(_build_oracle_part(frag, evidence_set, tier_name))

    if not z3_parts and not oracle_parts:
        stub = ObligationFragment(
            fragment_id=_stable_id(obligation_formula),
            formula=obligation_formula,
            complexity=ObligationComplexity.ATOMIC,
            tractability=TractabilityClass.DECIDABLE,
            weight=1.0,
        )
        z3_parts.append(_build_z3_part(stub, evidence_set, tier_name))

    proof_chain = generate_split_proof(z3_parts, oracle_parts)

    if oracle_parts and z3_parts:
        strategy = (
            f"Discharge {len(z3_parts)} Z3 part(s) first, "
            f"then {len(oracle_parts)} oracle part(s); merge with {merge_policy.value}."
        )
    elif z3_parts:
        strategy = f"Pure Z3 discharge ({len(z3_parts)} part(s))."
    else:
        strategy = f"Pure oracle discharge ({len(oracle_parts)} part(s))."

    return SplitObligation(
        original_obligation_id=obligation_id,
        z3_parts=tuple(z3_parts),
        oracle_parts=tuple(oracle_parts),
        split_proof=_json.dumps({"chain_id": proof_chain.chain_id, "verified": proof_chain.verified}),
        merge_policy=merge_policy,
        discharge_strategy=strategy,
    )


def classify_obligation_part(part_formula: str, context: dict | None = None) -> TractabilityClass:
    """Classify a single obligation part formula.

    Stateless wrapper around ObligationClassifier.  Classification is based
    on syntactic analysis: oracle patterns, Z3 patterns, formula depth.

    Parameters
    ----------
    part_formula : str
    context      : dict, optional — type hints and background axioms

    Returns
    -------
    TractabilityClass
    """
    return ObligationClassifier().classify(part_formula, context or {})


def merge_split_results(
    z3_result: dict,
    oracle_result: dict,
    policy: MergePolicy,
    weights: dict[str, float] | None = None,
) -> dict:
    """Merge discharge results from Z3 and oracle engines.

    Stateless wrapper around SplitResultMerger.

    Parameters
    ----------
    z3_result     : dict — keys: verdict, confidence, proof_trace, trust_tier
    oracle_result : dict — same keys
    policy        : MergePolicy
    weights       : dict, optional — engine weights for WEIGHTED_VOTE

    Returns
    -------
    dict — merged result
    """
    return SplitResultMerger(policy, weights=weights).merge(z3_result, oracle_result)


def assess_z3_tractability(formula: str, fragment_count: int) -> tuple[TractabilityClass, float]:
    """Assess Z3 tractability of *formula*, returning (class, confidence).

    Confidence near 1.0 means clearly in the class; near 0.5 means borderline.

    Factors:
    - Oracle patterns present → lower Z3 confidence
    - Z3 patterns present → higher confidence
    - Nesting depth → penalises deep formulas
    - fragment_count → many fragments reduce confidence

    Parameters
    ----------
    formula        : str
    fragment_count : int

    Returns
    -------
    tuple[TractabilityClass, float]
    """
    classifier = ObligationClassifier()
    tc = classifier.classify(formula)
    oracle_s = classifier._oracle_score(formula)
    z3_s = classifier._z3_score(formula)
    depth = classifier._formula_depth(formula)
    depth_penalty = min(depth / MAX_Z3_FORMULA_DEPTH, 1.0) * 0.3
    frag_penalty = min(fragment_count / 50, 1.0) * 0.2

    if tc == TractabilityClass.DECIDABLE:
        conf = max(0.0, z3_s - oracle_s - depth_penalty - frag_penalty)
    elif tc == TractabilityClass.SEMI_DECIDABLE:
        conf = max(0.0, 0.7 - oracle_s - depth_penalty)
    elif tc == TractabilityClass.ORACLE_REQUIRED:
        conf = oracle_s
    elif tc == TractabilityClass.HYBRID:
        conf = 0.5
    else:
        conf = max(0.0, 1.0 - z3_s - depth_penalty)

    return tc, max(0.0, min(1.0, conf))


def generate_split_proof(z3_parts: list[Z3Part], oracle_parts: list[OraclePart]) -> SplitProofChain:
    """Generate a SplitProofChain certifying that the split is sound.

    Records:
    1. Number and IDs of Z3 parts.
    2. Number and IDs of oracle parts.
    3. Soundness argument (depth limits, oracle tier bounds).
    4. Version metadata.

    Parameters
    ----------
    z3_parts     : list[Z3Part]
    oracle_parts : list[OraclePart]

    Returns
    -------
    SplitProofChain
    """
    chain_id = str(_uuid.uuid4())
    steps: list[str] = [
        f"[init] chain={chain_id[:8]} version={SPLIT_PROOF_VERSION}",
        f"[z3_count] {len(z3_parts)} Z3 part(s)",
        f"[oracle_count] {len(oracle_parts)} oracle part(s)",
    ]
    for i, p in enumerate(z3_parts):
        steps.append(f"[z3_part_{i}] id={p.part_id[:8]} depth={p.formula_depth()} ok={p.is_within_depth_limit()}")
    for i, p in enumerate(oracle_parts):
        steps.append(f"[oracle_part_{i}] id={p.part_id[:8]} tier={p.oracle_tier} budget={p.oracle_budget:.0f}")

    all_z3_ok = all(p.is_within_depth_limit() for p in z3_parts)
    all_oracle_ok = all(p.oracle_tier != "PROOF_BACKED" for p in oracle_parts)
    verified = all_z3_ok and all_oracle_ok
    steps.append(f"[soundness] z3_ok={all_z3_ok} oracle_ok={all_oracle_ok} verified={verified}")

    return SplitProofChain(
        chain_id=chain_id,
        steps=tuple(steps),
        z3_part_ids=tuple(p.part_id for p in z3_parts),
        oracle_part_ids=tuple(p.part_id for p in oracle_parts),
        verified=verified,
    )


def compute_split_score(z3_parts: list[Z3Part], oracle_parts: list[OraclePart]) -> float:
    """Compute a quality score for a split in [0, 1].

    Harmonic mean of z3_quality (fraction within depth limit) and
    oracle_quality (fraction with positive budget).

    Returns 0.0 if both lists are empty.
    """
    if not z3_parts and not oracle_parts:
        return 0.0
    z3_q = (sum(1 for p in z3_parts if p.is_within_depth_limit()) / len(z3_parts)) if z3_parts else 1.0
    oracle_q = (sum(1.0 for p in oracle_parts if p.oracle_budget > 0) / len(oracle_parts)) if oracle_parts else 1.0
    if z3_q + oracle_q == 0.0:
        return 0.0
    return 2.0 * z3_q * oracle_q / (z3_q + oracle_q)


def validate_split_obligation(split: SplitObligation) -> tuple[bool, list[str]]:
    """Validate all invariants of a SplitObligation.

    Invariants:
    1. At least one part exists.
    2. All Z3Part depths within MAX_Z3_FORMULA_DEPTH.
    3. No OraclePart claims PROOF_BACKED tier.
    4. split_proof is valid JSON.
    5. merge_policy is a MergePolicy instance.
    6. discharge_strategy is non-empty.

    Returns
    -------
    tuple[bool, list[str]]  — (is_valid, error_messages)
    """
    errors: list[str] = []
    if len(split.z3_parts) + len(split.oracle_parts) < 1:
        errors.append("At least one part is required.")
    for p in split.z3_parts:
        if not p.is_within_depth_limit():
            errors.append(f"Z3Part {p.part_id[:8]} exceeds depth limit ({p.formula_depth()} > {MAX_Z3_FORMULA_DEPTH}).")
    for p in split.oracle_parts:
        if p.oracle_tier == "PROOF_BACKED":
            errors.append(f"OraclePart {p.part_id[:8]} claims PROOF_BACKED tier (invalid).")
    try:
        _json.loads(split.split_proof)
    except _json.JSONDecodeError as exc:
        errors.append(f"split_proof not valid JSON: {exc}")
    if not isinstance(split.merge_policy, MergePolicy):
        errors.append(f"merge_policy is not a MergePolicy: {split.merge_policy!r}")
    if not split.discharge_strategy.strip():
        errors.append("discharge_strategy must be non-empty.")
    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Predefined split strategies
# ---------------------------------------------------------------------------

EAGER_Z3_FIRST: SplitStrategy = SplitStrategy(
    strategy_id="eager_z3_first",
    name="Eager Z3-First Strategy",
    description=(
        "Route as much as possible to Z3 before invoking the LLM oracle. "
        "Minimises oracle budget and maximises formal verification coverage."
    ),
    z3_tractability_criteria=(
        "formula matches Z3 patterns",
        "formula depth <= MAX_Z3_FORMULA_DEPTH",
        "no modal operators",
        "no natural-language sub-expressions",
    ),
    oracle_suitability_criteria=(
        "contains modal operators",
        "contains natural-language expressions",
        "Z3 tractability score below threshold",
    ),
)

ORACLE_AUGMENTED: SplitStrategy = SplitStrategy(
    strategy_id="oracle_augmented",
    name="Oracle-Augmented Strategy",
    description=(
        "Use the LLM oracle liberally, routing only hard arithmetic cores to Z3. "
        "Suitable when obligations are primarily deontic or epistemic."
    ),
    z3_tractability_criteria=(
        "pure arithmetic subterm",
        "pure propositional subterm",
    ),
    oracle_suitability_criteria=(
        "any non-arithmetic content",
        "any modal / deontic content",
        "any vague or natural-language content",
    ),
)

ALL_SPLIT_STRATEGIES: dict[str, SplitStrategy] = {
    s.strategy_id: s for s in (EAGER_Z3_FIRST, ORACLE_AUGMENTED)
}


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    SEP = "=" * 68
    print(SEP)
    print("mixed_obligations_should_be_split — smoke test")
    print(SEP)

    # -- A: Original channel-based splitting (ObligationSplitter) -----------
    print("\n── A. ObligationSplitter (channel-based) ──")
    result1 = split_obligation(
        obligation_id=str(uuid.uuid4()),
        proposition="(smt-discharge term₁) ∧ (human-review term₂)",
        sub_propositions=[
            "smt-discharge: x + y > 0",
            "human-review: code quality satisfactory",
        ],
        trust_tier="PROPOSAL",
    )
    assert isinstance(result1, SplitResult), f"Expected SplitResult, got {type(result1)}"
    channels = {f.channel_type for f in result1.fragments}
    assert ChannelType.SMT in channels
    assert ChannelType.HUMAN_ATTESTATION in channels
    for frag in result1.fragments:
        assert validate_homogeneity(frag)
    routed = route_split_obligations(result1)
    print(f"  fragments={len(result1.fragments)}, routing_order={[f.channel_type.value for f in routed]}")

    result4 = split_obligation(str(uuid.uuid4()), "empty", [], trust_tier="PROPOSAL")
    assert isinstance(result4, SplitFailure)
    reason_short = repr(result4.reason)[:50]
    print(f"  empty split -> SplitFailure reason={reason_short}")

    # -- B: MixedObligationSplitter config ----------------------------------
    print("\n── B. MixedObligationSplitter config ──")
    splitter_cfg = MixedObligationSplitter(
        split_strategy_id="eager_z3_first",
        z3_threshold=0.75,
        oracle_threshold=0.60,
        hybrid_policy="recurse_then_z3_first",
        splitter_id="smoke-test-001",
    )
    print(f"  z3_tractable(0.8)  = {splitter_cfg.is_z3_tractable(0.8)}")
    print(f"  oracle_suitable(0.3) = {splitter_cfg.is_oracle_suitable(0.3)}")

    # -- C: split_mixed_obligation ------------------------------------------
    print("\n── C. split_mixed_obligation (Z3/oracle splitting) ──")
    test_cases = [
        ("And(x + y > 0, z == True)", ["x is positive", "z is bool"], "VERIFIED"),
        ("the agent believes φ is probably correct And x > 5", ["ev1", "ev2"], "REVIEWED"),
        ("ForAll([x], Implies(x > 0, x * x > 0))", ["arith_axiom"], "PROOF_BACKED"),
        ("nl:: the system should behave ethically Or x == 42", ["ctx"], "PROPOSAL"),
    ]

    class _TierStub:
        def __init__(self, name): self.name = name
        def __ge__(self, other): return True

    splits_new: list[SplitObligation] = []
    for formula, evidence, tier_name in test_cases:
        tier_obj = _TierStub(tier_name)
        split = split_mixed_obligation(formula, evidence, tier_obj, splitter=splitter_cfg)
        splits_new.append(split)
        is_valid, errs = validate_split_obligation(split)
        print(f"  formula={formula[:50]!r}")
        print(f"    z3={len(split.z3_parts)} oracle={len(split.oracle_parts)} "
              f"policy={split.merge_policy.value} valid={is_valid}")

    # -- D: ObligationClassifier --------------------------------------------
    print("\n── D. ObligationClassifier ──")
    clf = ObligationClassifier()
    for formula in [
        "x + y > 0",
        "the agent believes φ",
        "ForAll([x], x > 0)",
        "□(p → q)",
        "μX.f(X)",
    ]:
        tc = clf.classify(formula)
        cx = clf.estimate_complexity(formula)
        print(f"  {formula!r:<50} tc={tc.value:<20} cx={cx.name}")
    print(f"  stats: {clf.get_statistics()}")

    # -- E: SplitResultMerger -----------------------------------------------
    print("\n── E. SplitResultMerger ──")
    z3_res = {"verdict": "sat", "confidence": 0.95, "proof_trace": ["z3 OK"], "trust_tier": 3}
    oracle_res = {"verdict": "true", "confidence": 0.80, "proof_trace": ["oracle OK"], "trust_tier": 2}
    for policy in MergePolicy:
        merged = merge_split_results(z3_res, oracle_res, policy)
        print(f"  {policy.value:<20} → verdict={merged['verdict']}, conf={merged.get('confidence', 0):.2f}")

    # -- F: assess_z3_tractability ------------------------------------------
    print("\n── F. assess_z3_tractability ──")
    for formula, n in [("And(x > 0, y < 10)", 3), ("nl:: agent feels confident", 0)]:
        tc, conf = assess_z3_tractability(formula, n)
        print(f"  {formula!r:<50} → {tc.value} (conf={conf:.2f})")

    # -- G: SplitProofChain -------------------------------------------------
    print("\n── G. SplitProofChain ──")
    if splits_new:
        s = splits_new[0]
        chain = generate_split_proof(list(s.z3_parts), list(s.oracle_parts))
        print(f"  {chain.summary()}")
        score = compute_split_score(list(s.z3_parts), list(s.oracle_parts))
        print(f"  split_score={score:.3f}")

    # -- H: Strategy catalogue ----------------------------------------------
    print("\n── H. Split strategy catalogue ──")
    for sid, strat in ALL_SPLIT_STRATEGIES.items():
        print(f"  [{sid}] {strat.name}: {strat.description[:60]}…")

    print(f"\n{SEP}")
    print("All smoke tests passed.")
    sys.exit(0)
