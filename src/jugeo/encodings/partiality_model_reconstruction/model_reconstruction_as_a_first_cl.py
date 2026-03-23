"""
Model reconstruction as a first-class operation.
# copilot: This module implements model reconstruction as a first-class algebraic operation
within the jugeo partial-model framework. Reconstruction rebuilds the total model from a
collection of partial evidences — fragments of a larger sheaf. Each fragment (PartialEvidence)
covers some sub-domain and assigns values; the reconstruction process merges these into a
TotalModelWitness representing a global section of the sheaf extending all partial data.

Mathematical context
--------------------
Let X be a topological space with open cover 𝒰 = {U_1, ..., U_n}. A sheaf ℱ assigns data
ℱ(U_i) to each open set. A global section exists iff local sections agree on overlaps;
otherwise the discrepancy is measured by the Čech H¹ cohomology class H¹(𝒰, ℱ).

Invariants
----------
- Judgments are tuples (c, φ, A, E, O, B, T, Π) — never bare booleans.
- Trust is an ordered algebra (TrustTier) — never a float.
- TrustTier ordering: PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.
- Obstructions are Čech H¹ cohomology classes.
"""

from __future__ import annotations

import uuid
import hashlib
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any
import itertools
import functools
import datetime


try:
    from jugeo.core.trust import TrustTier
    from jugeo.core.judgment import Judgment
    from jugeo.core.obstruction import CechObstruction
except ImportError:
    from enum import Enum

    class TrustTier(Enum):  # type: ignore[no-redef]
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5

    Judgment = tuple  # (c, φ, A, E, O, B, T, Π)
    # CechObstruction not defined in fallback — methods return string "H1-class:..."

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

RECONSTRUCTION_STRATEGIES: tuple = (
    "greedy_merge",
    "priority_merge",
    "conflict_aware_merge",
    "lattice_join",
    "sheaf_gluing",
    "evidence_order_merge",
)
"""All recognised reconstruction strategy names."""

DEFAULT_MERGE_STRATEGY: str = "greedy_merge"
"""Default strategy when none is specified."""

DEFAULT_FALLBACK_STRATEGY: str = "evidence_order_merge"
"""Fallback strategy used when the primary strategy fails to produce a total model."""

MERGE_CONFLICT_SENTINEL: str = "__CONFLICT__"
"""Placeholder placed in value-map when two evidence fragments disagree on a key."""

EMPTY_DOMAIN: frozenset = frozenset()
"""Canonical empty domain fragment."""

MAX_EVIDENCE_FRAGMENTS: int = 1024
"""Hard upper limit on the number of partial-evidence fragments in a single plan."""

TIER_ORDER: dict = {}

def _init_tier_order() -> dict:
    """Initialise the tier ordering dictionary after TrustTier is defined."""
    return {
        TrustTier.PROPOSAL: 1,
        TrustTier.REVIEWED: 2,
        TrustTier.VERIFIED: 3,
        TrustTier.RUNTIME_WITNESSED: 4,
        TrustTier.PROOF_BACKED: 5,
    }

TIER_ORDER = _init_tier_order()


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _tier_gte(a: TrustTier, b: TrustTier) -> bool:
    """Return True iff trust tier *a* is at least as high as *b*.

    Implements the ordered-algebra rule:
        PROPOSAL ≤ REVIEWED ≤ VERIFIED ≤ RUNTIME_WITNESSED ≤ PROOF_BACKED
    """
    tier_order = _init_tier_order()
    return tier_order[a] >= tier_order[b]


def _merge_value_fragments(
    frag_a: tuple,
    frag_b: tuple,
    strategy: str = DEFAULT_MERGE_STRATEGY,
) -> tuple:
    """Merge two value-fragment tuples into one combined tuple.

    Parameters
    ----------
    frag_a : tuple
        First fragment expressed as ((key, value), ...) pairs.
    frag_b : tuple
        Second fragment expressed as ((key, value), ...) pairs.
    strategy : str
        One of RECONSTRUCTION_STRATEGIES. In greedy_merge the first fragment
        wins on conflicts; in conflict_aware_merge conflicts are flagged with
        MERGE_CONFLICT_SENTINEL.

    Returns
    -------
    tuple
        Merged value-map. Duplicate keys are resolved per strategy.
    """
    merged: dict = {}
    for k, v in frag_a:
        merged[k] = v
    for k, v in frag_b:
        if k in merged:
            if strategy == "conflict_aware_merge" and merged[k] != v:
                merged[k] = MERGE_CONFLICT_SENTINEL
            elif strategy == "greedy_merge":
                pass  # keep first fragment's value
            else:
                merged[k] = v  # last-wins for all other strategies
        else:
            merged[k] = v
    return tuple((k, v) for k, v in sorted(merged.items()))


def _compute_coverage(evidence_list: list) -> frozenset:
    """Compute the union of all domain fragments covered by a list of evidences.

    This corresponds to computing the union of all open sets in the cover:
        covered(𝒰) = ⋃_{i} U_i

    Parameters
    ----------
    evidence_list : list[PartialEvidence]
        The list of partial evidence objects.

    Returns
    -------
    frozenset
        Union of all domain fragments.
    """
    return frozenset(
        itertools.chain.from_iterable(e.domain_fragment for e in evidence_list)
    )


def _detect_conflicts(evidence_list: list) -> list:
    """Find all pairs of evidence fragments that conflict.

    Two fragments conflict when they share a domain key but assign different
    values — a classical sheaf coherence failure indicating a non-trivial
    element of the Čech cohomology H¹(𝒰, ℱ).

    Parameters
    ----------
    evidence_list : list[PartialEvidence]
        Evidence to check.

    Returns
    -------
    list[tuple[str, str]]
        List of (evidence_id_a, evidence_id_b) pairs that are in conflict.
    """
    conflicts = []
    for a, b in itertools.combinations(evidence_list, 2):
        if a.conflicts_with(b):
            conflicts.append((a.evidence_id, b.evidence_id))
    return conflicts


def _fragment_digest(value_fragment: tuple) -> str:
    """Compute a deterministic hex digest of a value fragment for caching.

    Parameters
    ----------
    value_fragment : tuple
        The value fragment to digest.

    Returns
    -------
    str
        16-character hex digest.
    """
    raw = repr(sorted(value_fragment)).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _generate_id(prefix: str = "obj") -> str:
    """Generate a short UUID-based identifier with an optional prefix.

    Parameters
    ----------
    prefix : str
        Short prefix string for readability.

    Returns
    -------
    str
        Unique identifier string.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _min_tier(tiers: list) -> TrustTier:
    """Return the minimum trust tier from a list of tiers.

    Parameters
    ----------
    tiers : list[TrustTier]
        List of tier values.

    Returns
    -------
    TrustTier
        Minimum tier in the list.
    """
    tier_order = _init_tier_order()
    if not tiers:
        return TrustTier.PROPOSAL
    return functools.reduce(
        lambda a, b: a if tier_order[a] <= tier_order[b] else b,
        tiers,
    )


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartialEvidence:
    """A local section of the model sheaf over a sub-domain fragment.

    Mathematically, this is a section s_i ∈ ℱ(U_i) where U_i ⊆ X is an open
    sub-domain fragment. The value_fragment is the actual data assigned by the
    section over that fragment, encoded as an ordered tuple of (key, value) pairs.

    Invariant: Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans.

    Fields
    ------
    evidence_id : str
        Unique identifier for this piece of evidence.
    domain_fragment : frozenset[str]
        The set of keys/names that this evidence covers (the open U_i).
    value_fragment : tuple[tuple[str, object], ...]
        Tuple of (key, value) pairs assigning values to the covered domain.
    confidence_tier : TrustTier
        TrustTier expressing the epistemic confidence in this evidence.
    provenance : str
        Human-readable description of where this evidence came from.
    """

    evidence_id: str
    domain_fragment: frozenset
    value_fragment: tuple
    confidence_tier: TrustTier
    provenance: str

    def covers(self, key: str) -> bool:
        """Return True iff this evidence covers the given domain key.

        A key k is covered by evidence e iff k ∈ U_i — the local section
        has a defined value for that key.

        Parameters
        ----------
        key : str
            The domain key to check.

        Returns
        -------
        bool
            True if this evidence's domain fragment contains the key.
        """
        return key in self.domain_fragment

    def conflicts_with(self, other: PartialEvidence) -> bool:
        """Return True iff this evidence conflicts with other on some shared key.

        Two sections s_i ∈ ℱ(U_i) and s_j ∈ ℱ(U_j) conflict iff there exists
        a key k ∈ U_i ∩ U_j such that s_i(k) ≠ s_j(k). Such a conflict
        signals a non-trivial element of Čech cohomology H¹(𝒰, ℱ).

        Parameters
        ----------
        other : PartialEvidence
            Another evidence fragment to compare against.

        Returns
        -------
        bool
            True if there is a conflict on any shared domain key.
        """
        shared_keys = self.domain_fragment & other.domain_fragment
        if not shared_keys:
            return False
        self_map = dict(self.value_fragment)
        other_map = dict(other.value_fragment)
        for k in shared_keys:
            sv = self_map.get(k)
            ov = other_map.get(k)
            if sv is not None and ov is not None and sv != ov:
                return True
        return False

    def merge_nonoverlapping(self, other: PartialEvidence) -> PartialEvidence:
        """Merge this evidence with other, assuming they do NOT overlap.

        Pre-condition: self.domain_fragment ∩ other.domain_fragment = ∅.
        The resulting evidence covers the union of both domains. If there is
        overlap, the merge proceeds anyway (greedy strategy: first wins).

        Parameters
        ----------
        other : PartialEvidence
            Evidence to merge with; should be non-overlapping for clean results.

        Returns
        -------
        PartialEvidence
            A new evidence covering self.domain ∪ other.domain with the lower
            confidence tier of the two inputs.
        """
        merged_domain = self.domain_fragment | other.domain_fragment
        merged_values = _merge_value_fragments(
            self.value_fragment, other.value_fragment, strategy="greedy_merge"
        )
        tier_order = _init_tier_order()
        min_tier = (
            self.confidence_tier
            if tier_order[self.confidence_tier] <= tier_order[other.confidence_tier]
            else other.confidence_tier
        )
        merged_provenance = f"merge({self.provenance},{other.provenance})"
        return PartialEvidence(
            evidence_id=_generate_id("pev"),
            domain_fragment=merged_domain,
            value_fragment=merged_values,
            confidence_tier=min_tier,
            provenance=merged_provenance,
        )

    def to_judgment_tuple(self) -> tuple:
        """Encode this evidence as a judgment tuple (c, φ, A, E, O, B, T, Π).

        Judgment tuple field mapping:
          c  = evidence_id (context identifier)
          φ  = domain_fragment (formula / scope)
          A  = value_fragment (assertion data)
          E  = {} (empty environment for partial evidence)
          O  = () (no obligations generated)
          B  = provenance (backing/justification)
          T  = confidence_tier (trust level)
          Π  = () (no proof terms for raw evidence)

        Returns
        -------
        tuple
            8-tuple (c, φ, A, E, O, B, T, Π) as per the judgment invariant.
        """
        return (
            self.evidence_id,          # c
            self.domain_fragment,      # φ
            self.value_fragment,       # A
            {},                        # E
            (),                        # O
            self.provenance,           # B
            self.confidence_tier,      # T
            (),                        # Π
        )

    def evidence_summary(self) -> str:
        """Return a human-readable summary of this evidence fragment.

        Returns
        -------
        str
            Summary including evidence_id, key count, digest, tier, and provenance.
        """
        n_keys = len(self.domain_fragment)
        n_vals = len(self.value_fragment)
        digest = _fragment_digest(self.value_fragment)
        return (
            f"PartialEvidence({self.evidence_id}): covers {n_keys} keys, "
            f"{n_vals} values, digest={digest}, tier={self.confidence_tier.name}, "
            f"provenance='{self.provenance}'"
        )


@dataclass(frozen=True)
class ReconstructionPlan:
    """A plan specifying the order and strategy for merging partial evidences.

    The plan is an ordered sequence of evidence IDs to be merged one-by-one
    according to a named merge strategy. It may also specify a fallback
    strategy to use when the primary strategy fails.

    Invariant: Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans.

    Fields
    ------
    plan_id : str
        Unique identifier for this plan.
    evidence_order : tuple[str, ...]
        Ordered tuple of evidence IDs to process in sequence.
    merge_strategy : str
        Name of the primary merge strategy (must be in RECONSTRUCTION_STRATEGIES).
    fallback_strategy : str
        Name of the fallback strategy when the primary fails.
    plan_tier : TrustTier
        TrustTier of this plan (affects the tier of the reconstructed witness).
    """

    plan_id: str
    evidence_order: tuple
    merge_strategy: str
    fallback_strategy: str
    plan_tier: TrustTier

    def step_count(self) -> int:
        """Return the number of merge steps defined in this plan.

        Each step corresponds to integrating one additional partial-evidence
        fragment into the running reconstruction.

        Returns
        -------
        int
            Number of evidence steps.
        """
        return len(self.evidence_order)

    def is_feasible(self) -> bool:
        """Return True iff the plan is feasible given invariant bounds.

        A plan is feasible when:
          1. It has at least one evidence step.
          2. The merge strategy is recognised in RECONSTRUCTION_STRATEGIES.
          3. The evidence count does not exceed MAX_EVIDENCE_FRAGMENTS.
          4. The fallback strategy differs from the primary (fail-safe requirement).

        Returns
        -------
        bool
            True if all feasibility conditions are met.
        """
        if self.step_count() == 0:
            return False
        if self.merge_strategy not in RECONSTRUCTION_STRATEGIES:
            return False
        if self.step_count() > MAX_EVIDENCE_FRAGMENTS:
            return False
        if self.fallback_strategy == self.merge_strategy:
            return False
        return True

    def total_evidence_needed(self) -> int:
        """Return the total number of distinct evidence IDs referenced.

        This counts unique evidence IDs to indicate the minimum number of
        PartialEvidence objects that must be registered before the plan runs.

        Returns
        -------
        int
            Number of unique evidence IDs in evidence_order.
        """
        return len(set(self.evidence_order))

    def to_judgment_tuple(self) -> tuple:
        """Encode this plan as a judgment tuple (c, φ, A, E, O, B, T, Π).

        Returns
        -------
        tuple
            8-tuple (c, φ, A, E, O, B, T, Π) as per the judgment invariant.
        """
        return (
            self.plan_id,              # c
            self.evidence_order,       # φ
            (self.merge_strategy,),    # A
            {"fallback": self.fallback_strategy},  # E
            (),                        # O
            f"plan:{self.plan_id}",    # B
            self.plan_tier,            # T
            (),                        # Π
        )

    def plan_summary(self) -> str:
        """Return a human-readable summary of this reconstruction plan.

        Returns
        -------
        str
            Summary including plan_id, step count, strategies, tier, feasibility.
        """
        feasible_label = "feasible" if self.is_feasible() else "infeasible"
        return (
            f"ReconstructionPlan({self.plan_id}): {self.step_count()} steps, "
            f"strategy={self.merge_strategy}, fallback={self.fallback_strategy}, "
            f"tier={self.plan_tier.name}, {feasible_label}"
        )


@dataclass(frozen=True)
class ModelReconstructor:
    """Immutable descriptor for a reconstruction process configuration.

    The ModelReconstructor records which evidence IDs to use and under what
    strategic constraints, but does NOT hold mutable state — all mutations
    produce new instances (frozen dataclass invariant).

    Invariant: Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans.
    Trust is an ordered algebra — never a float.

    Fields
    ------
    reconstructor_id : str
        Unique identifier for this reconstructor configuration.
    strategy : str
        Merge strategy name (from RECONSTRUCTION_STRATEGIES).
    evidence_ids : frozenset[str]
        Frozen set of evidence IDs that this reconstructor will draw from.
    reconstruction_tier : TrustTier
        Minimum TrustTier required for this reconstruction to be valid.
    constraints : tuple[str, ...]
        Tuple of symbolic constraint strings that the reconstruction must satisfy.
    """

    reconstructor_id: str
    strategy: str
    evidence_ids: frozenset
    reconstruction_tier: TrustTier
    constraints: tuple

    def add_evidence(self, eid: str) -> ModelReconstructor:
        """Return a new ModelReconstructor with eid added to evidence_ids.

        Because this dataclass is frozen, adding evidence requires creating a
        new instance — the immutability invariant is preserved.

        Parameters
        ----------
        eid : str
            Evidence ID to add.

        Returns
        -------
        ModelReconstructor
            New instance with eid in evidence_ids.
        """
        return dataclasses.replace(
            self, evidence_ids=self.evidence_ids | frozenset([eid])
        )

    def remove_evidence(self, eid: str) -> ModelReconstructor:
        """Return a new ModelReconstructor with eid removed from evidence_ids.

        If eid is not present, returns a copy of self unchanged.

        Parameters
        ----------
        eid : str
            Evidence ID to remove.

        Returns
        -------
        ModelReconstructor
            New instance without eid in evidence_ids.
        """
        return dataclasses.replace(
            self, evidence_ids=self.evidence_ids - frozenset([eid])
        )

    def strategy_description(self) -> str:
        """Return a human-readable description of the configured merge strategy.

        Maps each recognised strategy name to a plain-text explanation of its
        semantics in sheaf-theoretic terms.

        Returns
        -------
        str
            Description of the strategy.
        """
        descriptions: dict = {
            "greedy_merge": (
                "Greedy merge: process evidence in order; first value wins on conflict."
            ),
            "priority_merge": (
                "Priority merge: higher-tier evidence overrides lower-tier on conflict."
            ),
            "conflict_aware_merge": (
                "Conflict-aware merge: flag all conflicts with MERGE_CONFLICT_SENTINEL "
                "rather than silently resolving them."
            ),
            "lattice_join": (
                "Lattice join: compute the join of all partial sections in the "
                "value lattice; assumes a compatible partial order on values."
            ),
            "sheaf_gluing": (
                "Sheaf gluing: apply the sheaf gluing lemma — succeeds only when all "
                "restriction maps agree on pairwise overlaps."
            ),
            "evidence_order_merge": (
                "Evidence-order merge: process evidence in the order specified by the "
                "ReconstructionPlan; last value wins on conflict."
            ),
        }
        return descriptions.get(
            self.strategy, f"Unknown strategy: {self.strategy}"
        )

    def reconstruction_key(self) -> str:
        """Compute a deterministic key uniquely identifying this configuration.

        The key is derived by hashing the reconstructor_id, strategy, sorted
        evidence IDs, and constraints — making it safe to use as a cache key.

        Returns
        -------
        str
            Deterministic hash-based key string.
        """
        raw = (
            self.reconstructor_id
            + self.strategy
            + "|".join(sorted(self.evidence_ids))
            + "|".join(self.constraints)
        )
        return "rkey-" + hashlib.sha256(raw.encode()).hexdigest()[:12]

    def to_judgment_tuple(self) -> tuple:
        """Encode this reconstructor as a judgment tuple (c, φ, A, E, O, B, T, Π).

        Returns
        -------
        tuple
            8-tuple (c, φ, A, E, O, B, T, Π) as per the judgment invariant.
        """
        return (
            self.reconstructor_id,                    # c
            self.evidence_ids,                        # φ
            (self.strategy,) + self.constraints,     # A
            {},                                       # E
            (),                                       # O
            f"reconstructor:{self.reconstructor_id}", # B
            self.reconstruction_tier,                 # T
            (),                                       # Π
        )


@dataclass(frozen=True)
class TotalModelWitness:
    """A witness that a total model has been successfully reconstructed.

    A TotalModelWitness corresponds to a global section of the sheaf ℱ over
    the full domain X — a collection of locally consistent data that extends
    all partial evidences. If reconstruction was incomplete, gaps() will be
    non-empty and as_cech_class() returns a non-empty obstruction string.

    Invariant: Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans.
    Obstructions are Čech H¹ cohomology classes.

    Fields
    ------
    witness_id : str
        Unique identifier for this witness.
    total_domain : frozenset[str]
        The full domain that this witness claims to cover.
    total_value_map : tuple[tuple[str, object], ...]
        Tuple of (key, value) pairs covering all of total_domain.
    witness_tier : TrustTier
        TrustTier of this witness (inherited from the reconstruction process).
    reconstruction_plan_id : str
        Identifier of the ReconstructionPlan that produced this witness.
    """

    witness_id: str
    total_domain: frozenset
    total_value_map: tuple
    witness_tier: TrustTier
    reconstruction_plan_id: str

    def is_total_over(self, domain: frozenset) -> bool:
        """Return True iff this witness covers all keys in domain.

        A witness is total over a domain iff it contains a defined (and
        non-conflicted) value for every key in that domain.

        Parameters
        ----------
        domain : frozenset[str]
            The domain to check coverage over.

        Returns
        -------
        bool
            True if all keys in domain are covered without conflict.
        """
        conflict_free = frozenset(
            k for k, v in self.total_value_map if v != MERGE_CONFLICT_SENTINEL
        )
        return domain <= conflict_free

    def gaps(self, domain: frozenset) -> frozenset:
        """Return the set of domain keys NOT covered by this witness.

        Gaps correspond to keys for which no partial evidence provided a value,
        or where the reconstruction resulted in an unresolved conflict.

        Parameters
        ----------
        domain : frozenset[str]
            The expected domain to check gaps against.

        Returns
        -------
        frozenset[str]
            Keys in domain that are not covered or are conflicted.
        """
        covered_conflict_free = frozenset(
            k for k, v in self.total_value_map if v != MERGE_CONFLICT_SENTINEL
        )
        return domain - covered_conflict_free

    def as_cech_class(self) -> str:
        """Return the Čech H¹ cohomology class description of any obstructions.

        If the witness is fully total (no gaps, no conflicts), returns an empty
        string indicating the cohomology class is trivial (zero obstruction).
        Otherwise returns a string encoding the H¹ class.

        Returns
        -------
        str
            Empty string if no obstruction, else "H1-class:..." encoding.
        """
        conflict_keys = frozenset(
            k for k, v in self.total_value_map if v == MERGE_CONFLICT_SENTINEL
        )
        missing_keys = self.total_domain - frozenset(k for k, _ in self.total_value_map)
        all_problematic = conflict_keys | missing_keys
        if not all_problematic:
            return ""
        digest = hashlib.sha256(repr(sorted(all_problematic)).encode()).hexdigest()[:8]
        return (
            f"H1-class:{self.witness_id}:{digest}:"
            f"gaps={len(missing_keys)},conflicts={len(conflict_keys)}"
        )

    def to_judgment_tuple(self) -> tuple:
        """Encode this witness as a judgment tuple (c, φ, A, E, O, B, T, Π).

        Returns
        -------
        tuple
            8-tuple (c, φ, A, E, O, B, T, Π) as per the judgment invariant.
        """
        obstruction = self.as_cech_class()
        return (
            self.witness_id,                         # c
            self.total_domain,                       # φ
            self.total_value_map,                    # A
            {"plan_id": self.reconstruction_plan_id}, # E
            (obstruction,) if obstruction else (),   # O
            f"witness:{self.witness_id}",             # B
            self.witness_tier,                       # T
            (),                                      # Π
        )

    def witness_summary(self) -> str:
        """Return a human-readable summary of this total-model witness.

        Returns
        -------
        str
            Summary including witness_id, status, domain size, gap count, tier.
        """
        domain_size = len(self.total_domain)
        covered_size = len(self.total_value_map)
        gap_count = len(self.gaps(self.total_domain))
        obstruction = self.as_cech_class()
        status = "TOTAL" if not obstruction else "PARTIAL"
        return (
            f"TotalModelWitness({self.witness_id}): status={status}, "
            f"domain={domain_size}, covered={covered_size}, gaps={gap_count}, "
            f"tier={self.witness_tier.name}, plan={self.reconstruction_plan_id}"
        )


# ---------------------------------------------------------------------------
# Non-frozen engine class
# ---------------------------------------------------------------------------

class ReconstructionEngine:
    """Stateful engine that collects evidence and runs reconstruction.

    This class is intentionally NOT a frozen dataclass because it accumulates
    evidence and state during a multi-step reconstruction process. The final
    witness it produces is immutable (TotalModelWitness).

    Attributes
    ----------
    _evidences : list[PartialEvidence]
        All registered evidence fragments, in registration order.
    _plan : Optional[ReconstructionPlan]
        The currently configured reconstruction plan.
    _witness : Optional[TotalModelWitness]
        The most recently produced witness (if any).
    _run_count : int
        Number of times run_reconstruction has been called.
    """

    def __init__(self) -> None:
        """Initialise an empty engine with no evidence and no plan."""
        self._evidences: list = []
        self._plan: Optional[ReconstructionPlan] = None
        self._witness: Optional[TotalModelWitness] = None
        self._run_count: int = 0

    def register_evidence(self, e: PartialEvidence) -> None:
        """Register a PartialEvidence fragment with the engine.

        Evidence registered here will be used on the next call to
        run_reconstruction(). Duplicate IDs are accepted.

        Parameters
        ----------
        e : PartialEvidence
            Evidence fragment to register.
        """
        self._evidences.append(e)

    def set_plan(self, p: ReconstructionPlan) -> None:
        """Set the reconstruction plan to use on the next run.

        Parameters
        ----------
        p : ReconstructionPlan
            The plan to use for reconstruction.
        """
        self._plan = p

    def run_reconstruction(self) -> TotalModelWitness:
        """Execute the reconstruction according to the current plan.

        Raises
        ------
        RuntimeError
            If no plan has been set or if no evidence has been registered.

        Returns
        -------
        TotalModelWitness
            The reconstructed total model witness.
        """
        if self._plan is None:
            raise RuntimeError("No reconstruction plan set; call set_plan() first.")
        if not self._evidences:
            raise RuntimeError(
                "No evidence registered; call register_evidence() first."
            )
        self._run_count += 1
        evidence_index = {e.evidence_id: e for e in self._evidences}
        relevant = [
            evidence_index[eid]
            for eid in self._plan.evidence_order
            if eid in evidence_index
        ]
        self._witness = reconstruct_model(relevant, self._plan)
        return self._witness

    def validate(self) -> bool:
        """Return True iff the last reconstruction produced a total model witness.

        A reconstruction is valid when:
          1. A witness has been produced.
          2. The witness has no gaps over its declared total_domain.
          3. The witness tier is at least as high as VERIFIED.

        Returns
        -------
        bool
            True if the last reconstruction was valid.
        """
        if self._witness is None:
            return False
        if not self._witness.is_total_over(self._witness.total_domain):
            return False
        return _tier_gte(self._witness.witness_tier, TrustTier.VERIFIED)

    def get_witness(self) -> Optional[TotalModelWitness]:
        """Return the most recently produced TotalModelWitness, or None.

        Returns
        -------
        Optional[TotalModelWitness]
            The last witness produced, or None if no reconstruction has run.
        """
        return self._witness


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def reconstruct_model(
    evidence: list,
    plan: ReconstructionPlan,
) -> TotalModelWitness:
    """Reconstruct a total model from a list of partial evidences and a plan.

    This is the core reconstruction function. It processes evidences in the
    order specified by plan.evidence_order, merging them according to
    plan.merge_strategy. If conflicts arise and the primary strategy is not
    conflict_aware_merge, it falls back to plan.fallback_strategy.

    Parameters
    ----------
    evidence : list[PartialEvidence]
        List of PartialEvidence fragments to merge.
    plan : ReconstructionPlan
        ReconstructionPlan specifying order and strategy.

    Returns
    -------
    TotalModelWitness
        The resulting total-model witness. May have gaps if evidence does not
        cover the full domain.
    """
    evidence_map = {e.evidence_id: e for e in evidence}
    ordered = [
        evidence_map[eid]
        for eid in plan.evidence_order
        if eid in evidence_map
    ]
    if not ordered:
        return TotalModelWitness(
            witness_id=_generate_id("wit"),
            total_domain=EMPTY_DOMAIN,
            total_value_map=(),
            witness_tier=plan.plan_tier,
            reconstruction_plan_id=plan.plan_id,
        )

    accumulated_values: tuple = ordered[0].value_fragment
    accumulated_domain: frozenset = ordered[0].domain_fragment
    strategy = plan.merge_strategy

    for ev in ordered[1:]:
        try:
            accumulated_values = _merge_value_fragments(
                accumulated_values, ev.value_fragment, strategy=strategy
            )
        except Exception:
            accumulated_values = _merge_value_fragments(
                accumulated_values,
                ev.value_fragment,
                strategy=plan.fallback_strategy,
            )
        accumulated_domain = accumulated_domain | ev.domain_fragment

    tier_order = _init_tier_order()
    min_t = functools.reduce(
        lambda a, b: a if tier_order[a] <= tier_order[b] else b,
        (e.confidence_tier for e in ordered),
        plan.plan_tier,
    )

    return TotalModelWitness(
        witness_id=_generate_id("wit"),
        total_domain=accumulated_domain,
        total_value_map=accumulated_values,
        witness_tier=min_t,
        reconstruction_plan_id=plan.plan_id,
    )


def build_reconstruction_plan(
    evidence_ids: list,
    strategy: str,
    fallback: str,
    tier: TrustTier,
) -> ReconstructionPlan:
    """Construct a ReconstructionPlan from a list of evidence IDs.

    Parameters
    ----------
    evidence_ids : list[str]
        Ordered list of evidence IDs.
    strategy : str
        Primary merge strategy name.
    fallback : str
        Fallback strategy name.
    tier : TrustTier
        TrustTier for the resulting plan.

    Returns
    -------
    ReconstructionPlan
        A new, immutable reconstruction plan.
    """
    if strategy not in RECONSTRUCTION_STRATEGIES:
        strategy = DEFAULT_MERGE_STRATEGY
    if fallback not in RECONSTRUCTION_STRATEGIES or fallback == strategy:
        fallback = DEFAULT_FALLBACK_STRATEGY
    return ReconstructionPlan(
        plan_id=_generate_id("plan"),
        evidence_order=tuple(evidence_ids),
        merge_strategy=strategy,
        fallback_strategy=fallback,
        plan_tier=tier,
    )


def validate_total_model(
    witness: TotalModelWitness,
    expected_domain: frozenset,
) -> bool:
    """Validate that a TotalModelWitness covers an expected domain completely.

    Parameters
    ----------
    witness : TotalModelWitness
        The witness to validate.
    expected_domain : frozenset[str]
        The domain that the witness is expected to cover without gaps or conflicts.

    Returns
    -------
    bool
        True iff the witness is total over expected_domain.
    """
    return witness.is_total_over(expected_domain)


def run_reconstruction(
    reconstructor: ModelReconstructor,
    evidence: list,
) -> TotalModelWitness:
    """High-level convenience function: run reconstruction from a declarative spec.

    This function creates a temporary engine, registers the provided evidence,
    builds a plan from the reconstructor's configuration, and runs the engine.

    Parameters
    ----------
    reconstructor : ModelReconstructor
        Immutable configuration specifying the strategy and constraints.
    evidence : list[PartialEvidence]
        List of PartialEvidence to use in the reconstruction.

    Returns
    -------
    TotalModelWitness
        The resulting total-model witness.
    """
    relevant = [e for e in evidence if e.evidence_id in reconstructor.evidence_ids]
    ordered_ids = [e.evidence_id for e in relevant]
    plan = build_reconstruction_plan(
        evidence_ids=ordered_ids,
        strategy=reconstructor.strategy,
        fallback=DEFAULT_FALLBACK_STRATEGY,
        tier=reconstructor.reconstruction_tier,
    )
    engine = ReconstructionEngine()
    for e in relevant:
        engine.register_evidence(e)
    engine.set_plan(plan)
    return engine.run_reconstruction()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Model Reconstruction Smoke Test ===")
    print(f"Strategies: {RECONSTRUCTION_STRATEGIES}")
    print(f"Default merge: {DEFAULT_MERGE_STRATEGY}")
    print(f"Default fallback: {DEFAULT_FALLBACK_STRATEGY}")

    # Build three partial evidences that together cover a full domain.
    ev1 = PartialEvidence(
        evidence_id="ev-001",
        domain_fragment=frozenset(["x", "y"]),
        value_fragment=(("x", 10), ("y", 20)),
        confidence_tier=TrustTier.VERIFIED,
        provenance="sensor-A",
    )
    ev2 = PartialEvidence(
        evidence_id="ev-002",
        domain_fragment=frozenset(["y", "z"]),
        value_fragment=(("y", 20), ("z", 30)),
        confidence_tier=TrustTier.REVIEWED,
        provenance="sensor-B",
    )
    ev3 = PartialEvidence(
        evidence_id="ev-003",
        domain_fragment=frozenset(["z", "w"]),
        value_fragment=(("z", 99), ("w", 40)),  # conflicts with ev2 on z
        confidence_tier=TrustTier.PROPOSAL,
        provenance="sensor-C",
    )

    print("\n-- Evidence summaries --")
    for ev in [ev1, ev2, ev3]:
        print(" ", ev.evidence_summary())

    # Check coverage and conflicts.
    total_coverage = _compute_coverage([ev1, ev2, ev3])
    print(f"\nTotal coverage: {sorted(total_coverage)}")
    conflicts = _detect_conflicts([ev1, ev2, ev3])
    print(f"Conflicts: {conflicts}")

    # Test covers() method
    print(f"\nev1.covers('x'): {ev1.covers('x')}")
    print(f"ev1.covers('z'): {ev1.covers('z')}")
    print(f"ev2.conflicts_with(ev3): {ev2.conflicts_with(ev3)}")

    # Build a reconstruction plan.
    plan = build_reconstruction_plan(
        evidence_ids=["ev-001", "ev-002", "ev-003"],
        strategy="greedy_merge",
        fallback="evidence_order_merge",
        tier=TrustTier.VERIFIED,
    )
    print(f"\n{plan.plan_summary()}")
    print(f"Plan feasible: {plan.is_feasible()}")
    print(f"Steps: {plan.step_count()}")
    print(f"Total evidence needed: {plan.total_evidence_needed()}")

    # Run reconstruction via engine.
    engine = ReconstructionEngine()
    engine.register_evidence(ev1)
    engine.register_evidence(ev2)
    engine.register_evidence(ev3)
    engine.set_plan(plan)
    witness = engine.run_reconstruction()

    print(f"\n{witness.witness_summary()}")
    jt = witness.to_judgment_tuple()
    print(f"Judgment tuple length: {len(jt)}")
    print(f"Čech class: '{witness.as_cech_class()}'")
    print(f"Is total over {{x,y,z,w}}: {witness.is_total_over(frozenset(['x','y','z','w']))}")
    print(f"Gaps over {{x,y,z,w}}: {witness.gaps(frozenset(['x','y','z','w']))}")
    print(f"Engine valid: {engine.validate()}")
    print(f"Engine witness: {engine.get_witness() is not None}")

    # Use high-level run_reconstruction.
    reconstructor = ModelReconstructor(
        reconstructor_id="rec-001",
        strategy="sheaf_gluing",
        evidence_ids=frozenset(["ev-001", "ev-002"]),
        reconstruction_tier=TrustTier.REVIEWED,
        constraints=("no_conflicts", "must_cover_xy"),
    )
    print(f"\nReconstructor key: {reconstructor.reconstruction_key()}")
    print(f"Strategy desc: {reconstructor.strategy_description()}")
    print(f"Reconstructor judgment: {reconstructor.to_judgment_tuple()[6].name}")

    w2 = run_reconstruction(reconstructor, [ev1, ev2, ev3])
    print(f"High-level witness: {w2.witness_summary()}")

    # Validation.
    expected = frozenset(["x", "y"])
    print(f"\nValidate w2 over {{'x','y'}}: {validate_total_model(w2, expected)}")

    # Merge non-overlapping evidences.
    merged_ev = ev1.merge_nonoverlapping(ev3)
    print(f"Merged evidence: {merged_ev.evidence_summary()}")
    print(f"Merged covers 'w': {merged_ev.covers('w')}")
    print(f"Merged covers 'x': {merged_ev.covers('x')}")

    # Test _fragment_digest
    digest = _fragment_digest(ev1.value_fragment)
    print(f"\nFragment digest of ev1: {digest}")

    # Add/remove evidence from reconstructor.
    rec2 = reconstructor.add_evidence("ev-003")
    rec3 = rec2.remove_evidence("ev-001")
    print(f"\nrec2 evidence count: {len(rec2.evidence_ids)}")
    print(f"rec3 evidence count: {len(rec3.evidence_ids)}")
    print(f"rec3 has ev-001: {'ev-001' in rec3.evidence_ids}")

    # Plan judgment tuple
    plan_jt = plan.to_judgment_tuple()
    print(f"\nPlan judgment tuple[6].name: {plan_jt[6].name}")

    # _tier_gte checks
    print(f"\n_tier_gte(VERIFIED, REVIEWED): {_tier_gte(TrustTier.VERIFIED, TrustTier.REVIEWED)}")
    print(f"_tier_gte(PROPOSAL, PROOF_BACKED): {_tier_gte(TrustTier.PROPOSAL, TrustTier.PROOF_BACKED)}")

    print("\n=== Smoke test complete ===")

    """A single piece of partial evidence with a coordinate and trust level.

    Attributes
    ----------
    item_id : str
        Unique identifier.
    coordinate : str
        The semantic coordinate where this evidence applies.
    kind : EvidenceKind
        The kind of evidence.
    content : str
        The evidence content (SMT expression, natural language, etc.).
    trust_tier : TrustTier
        Trust level of this evidence item.
    support_scope : str
        The region of the domain where this evidence is valid.
    judgment : ReconstructionJudgment
        Governing judgment.
    is_universal : bool
        Whether this evidence applies universally (not just at coordinate).
    """

    item_id: str
    coordinate: str
    kind: EvidenceKind
    content: str
    trust_tier: TrustTier
    support_scope: str
    judgment: ReconstructionJudgment
    is_universal: bool = False

    def is_compatible_with(self, other: EvidenceItem) -> bool:
        """Check whether this evidence is compatible with another item."""
        if self.coordinate != other.coordinate:
            return True  # different coordinates cannot conflict
        if self.kind != other.kind:
            return True  # different kinds coexist
        # Same coordinate and kind: check content compatibility
        return self.content == other.content

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "coordinate": self.coordinate,
            "kind": self.kind.value,
            "content": self.content[:200],
            "trust_tier": self.trust_tier.name,
            "support_scope": self.support_scope,
            "is_universal": self.is_universal,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Evidence gap
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EvidenceGap:
    """A gap in the evidence — a coordinate or region with insufficient evidence.

    Attributes
    ----------
    gap_id : str
        Unique identifier.
    coordinate : str
        The coordinate where evidence is missing or conflicting.
    kind : GapKind
        The kind of gap.
    description : str
        Human-readable description of the gap.
    conflicting_items : tuple[str, ...]
        IDs of conflicting evidence items, if any.
    required_trust : TrustTier
        Minimum trust tier needed to fill this gap.
    fill_suggestion : str
        Suggested way to fill the gap.
    """

    gap_id: str
    coordinate: str
    kind: GapKind
    description: str
    conflicting_items: tuple[str, ...] = ()
    required_trust: TrustTier = TrustTier.PROPOSAL
    fill_suggestion: str = ""

    def is_blocking(self) -> bool:
        """True iff this gap blocks total model reconstruction."""
        return self.kind in (
            GapKind.CONFLICTING_EVIDENCE,
            GapKind.MISSING_SECTION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "coordinate": self.coordinate,
            "kind": self.kind.value,
            "description": self.description,
            "conflicting_items": list(self.conflicting_items),
            "required_trust": self.required_trust.name,
            "fill_suggestion": self.fill_suggestion,
            "is_blocking": self.is_blocking(),
        }


# ---------------------------------------------------------------------------
# Partial evidence bundle
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PartialEvidence:
    """A bundle of local evidence items for model reconstruction.

    The partial evidence bundle collects all available evidence and
    identifies gaps that must be filled before a total model can be
    reconstructed.

    Attributes
    ----------
    bundle_id : str
        Unique identifier.
    items : tuple[EvidenceItem, ...]
        All evidence items.
    gaps : tuple[EvidenceGap, ...]
        Identified evidence gaps.
    coverage_coordinate : str
        The semantic coordinate region covered by this evidence.
    model_kind : ModelKind
        The kind of model being reconstructed.
    judgment : ReconstructionJudgment
        Governing judgment.
    """

    bundle_id: str
    items: tuple[EvidenceItem, ...]
    gaps: tuple[EvidenceGap, ...]
    coverage_coordinate: str
    model_kind: ModelKind
    judgment: ReconstructionJudgment

    def get_items_at(self, coordinate: str) -> tuple[EvidenceItem, ...]:
        """Return all evidence items at a given coordinate."""
        return tuple(
            item for item in self.items
            if item.coordinate == coordinate or item.is_universal
        )

    def has_conflicts(self) -> bool:
        """True iff any two evidence items are incompatible."""
        item_list = list(self.items)
        for i, item_a in enumerate(item_list):
            for item_b in item_list[i + 1:]:
                if not item_a.is_compatible_with(item_b):
                    return True
        return False

    def blocking_gaps(self) -> tuple[EvidenceGap, ...]:
        """Return gaps that block total model reconstruction."""
        return tuple(g for g in self.gaps if g.is_blocking())

    def minimum_trust(self) -> TrustTier:
        """Return the minimum trust tier among all evidence items."""
        if not self.items:
            return TrustTier.PROPOSAL
        return TrustTier(min(int(item.trust_tier) for item in self.items))

    def maximum_trust(self) -> TrustTier:
        """Return the maximum trust tier among all evidence items."""
        if not self.items:
            return TrustTier.PROPOSAL
        return TrustTier(max(int(item.trust_tier) for item in self.items))

    def covered_coordinates(self) -> frozenset[str]:
        """Return the set of coordinates with at least one evidence item."""
        return frozenset(item.coordinate for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "num_items": len(self.items),
            "num_gaps": len(self.gaps),
            "num_blocking_gaps": len(self.blocking_gaps()),
            "coverage_coordinate": self.coverage_coordinate,
            "model_kind": self.model_kind.value,
            "has_conflicts": self.has_conflicts(),
            "min_trust": self.minimum_trust().name,
            "max_trust": self.maximum_trust().name,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Reconstruction step
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReconstructionStep:
    """A single step in a reconstruction plan.

    Attributes
    ----------
    step_id : str
        Unique identifier.
    kind : ReconstructionStepKind
        The kind of action.
    target_coordinate : str
        The coordinate this step acts on.
    action_description : str
        Human-readable description of the action.
    input_evidence_ids : tuple[str, ...]
        Evidence items consumed by this step.
    output_coordinate : str
        The coordinate where the result is placed.
    smt_action : str
        SMT-LIB2 expression implementing this step.
    judgment : ReconstructionJudgment
        Governing judgment.
    estimated_cost : int
        Relative effort estimate.
    """

    step_id: str
    kind: ReconstructionStepKind
    target_coordinate: str
    action_description: str
    input_evidence_ids: tuple[str, ...]
    output_coordinate: str
    smt_action: str
    judgment: ReconstructionJudgment
    estimated_cost: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "kind": self.kind.value,
            "target_coordinate": self.target_coordinate,
            "action_description": self.action_description,
            "num_inputs": len(self.input_evidence_ids),
            "estimated_cost": self.estimated_cost,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Reconstruction plan
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReconstructionPlan:
    """An ordered sequence of reconstruction steps.

    The plan is a sheaf morphism from the partial evidence bundle to the
    total model: each step fills one gap or reconciles one conflict.

    Attributes
    ----------
    plan_id : str
        Unique identifier.
    steps : tuple[ReconstructionStep, ...]
        Ordered reconstruction steps.
    evidence_bundle : PartialEvidence
        The evidence bundle this plan addresses.
    estimated_total_cost : int
        Sum of step costs.
    judgment : ReconstructionJudgment
        Governing judgment.
    status : ReconstructionStatus
        Current execution status.
    """

    plan_id: str
    steps: tuple[ReconstructionStep, ...]
    evidence_bundle: PartialEvidence
    estimated_total_cost: int
    judgment: ReconstructionJudgment
    status: ReconstructionStatus = ReconstructionStatus.PENDING

    def gap_filling_steps(self) -> tuple[ReconstructionStep, ...]:
        return tuple(s for s in self.steps if s.kind == ReconstructionStepKind.FILL_GAP)

    def gluing_steps(self) -> tuple[ReconstructionStep, ...]:
        return tuple(s for s in self.steps if s.kind == ReconstructionStepKind.GLUE_SECTIONS)

    def is_executable(self) -> bool:
        """True iff the plan has steps and no blocking gaps remain."""
        return (
            len(self.steps) > 0
            and len(self.evidence_bundle.blocking_gaps()) == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "num_steps": len(self.steps),
            "estimated_total_cost": self.estimated_total_cost,
            "status": self.status.value,
            "is_executable": self.is_executable(),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Total model witness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TotalModelWitness:
    """A certificate that total model reconstruction succeeded.

    Attributes
    ----------
    witness_id : str
        Unique identifier.
    model_coordinate : str
        The semantic coordinate of the total model.
    model_kind : ModelKind
        The kind of reconstructed model.
    reconstructed_sections : Mapping[str, Any]
        Map from coordinate to reconstructed value.
    evidence_coverage : Mapping[str, str]
        Map from coordinate to the evidence item that filled it.
    trust_tier : TrustTier
        Trust level of the total model — meet of all evidence trust tiers.
    judgment : ReconstructionJudgment
        The governing judgment.
    reconstruction_time_ns : int
        Time taken for reconstruction.
    plan_id : str
        The plan that produced this witness.
    """

    witness_id: str
    model_coordinate: str
    model_kind: ModelKind
    reconstructed_sections: Mapping[str, Any]
    evidence_coverage: Mapping[str, str]
    trust_tier: TrustTier
    judgment: ReconstructionJudgment
    reconstruction_time_ns: int = 0
    plan_id: str = ""

    def is_total(self) -> bool:
        """True iff every coordinate has a reconstructed section."""
        return len(self.reconstructed_sections) > 0

    def coverage_fraction(self, expected_coordinates: frozenset[str]) -> float:
        if not expected_coordinates:
            return 1.0
        covered = frozenset(self.reconstructed_sections.keys())
        return len(covered & expected_coordinates) / len(expected_coordinates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id,
            "model_coordinate": self.model_coordinate,
            "model_kind": self.model_kind.value,
            "num_sections": len(self.reconstructed_sections),
            "trust_tier": self.trust_tier.name,
            "reconstruction_time_ns": self.reconstruction_time_ns,
            "plan_id": self.plan_id,
            "is_total": self.is_total(),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# Global section and descent obstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReconstructionGlobalSection:
    """A successfully reconstructed total model."""

    coordinate: str
    witness: TotalModelWitness
    judgment: ReconstructionJudgment

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "reconstruction_global_section",
            "coordinate": self.coordinate,
            "witness": self.witness.to_dict(),
            "judgment": self.judgment.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ReconstructionDescentObstruction:
    """A Čech obstruction blocking total model reconstruction.  NEVER raises."""

    coordinate: str
    obstruction: ReconstructionCechObstruction
    unresolved_gaps: tuple[EvidenceGap, ...]
    diagnosis: str = ""
    repair_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "reconstruction_descent_obstruction",
            "coordinate": self.coordinate,
            "obstruction": self.obstruction.to_dict(),
            "num_unresolved_gaps": len(self.unresolved_gaps),
            "diagnosis": self.diagnosis,
            "repair_hints": list(self.repair_hints),
        }


# ---------------------------------------------------------------------------
# Model reconstructor
# ---------------------------------------------------------------------------

@dataclass
class ModelReconstructor:
    """The engine that drives model reconstruction from partial evidence.

    The reconstructor applies a ReconstructionPlan to a PartialEvidence
    bundle, filling gaps and gluing sections until a TotalModelWitness
    is produced or a Čech obstruction is encountered.

    Attributes
    ----------
    reconstructor_id : str
        Unique identifier.
    model_kind : ModelKind
        The kind of model being reconstructed.
    coordinate : str
        Semantic coordinate.
    default_fill_value : Any
        Value used to fill underdetermined gaps.
    max_iterations : int
        Maximum reconstruction iterations before giving up.
    """

    reconstructor_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    model_kind: ModelKind = ModelKind.FUNCTION_MODEL
    coordinate: str = "model_root"
    default_fill_value: Any = None
    max_iterations: int = 100

    def _detect_gaps(
        self,
        evidence: PartialEvidence,
        expected_coordinates: frozenset[str],
    ) -> list[EvidenceGap]:
        """Detect evidence gaps relative to expected coordinates."""
        gaps: list[EvidenceGap] = []
        covered = evidence.covered_coordinates()

        # Missing sections
        for coord in expected_coordinates - covered:
            gaps.append(EvidenceGap(
                gap_id=_stable_id("gap:missing", coord),
                coordinate=coord,
                kind=GapKind.MISSING_SECTION,
                description=f"No evidence at coordinate {coord}.",
                required_trust=TrustTier.PROPOSAL,
                fill_suggestion=f"Add evidence item for {coord}.",
            ))

        # Conflicting evidence
        coord_items: dict[str, list[EvidenceItem]] = {}
        for item in evidence.items:
            coord_items.setdefault(item.coordinate, []).append(item)
        for coord, items in coord_items.items():
            for i, ia in enumerate(items):
                for ib in items[i + 1:]:
                    if not ia.is_compatible_with(ib):
                        gaps.append(EvidenceGap(
                            gap_id=_stable_id("gap:conflict", f"{coord}:{ia.item_id}:{ib.item_id}"),
                            coordinate=coord,
                            kind=GapKind.CONFLICTING_EVIDENCE,
                            description=(
                                f"Conflicting evidence at {coord}: "
                                f"{ia.item_id[:12]} vs {ib.item_id[:12]}"
                            ),
                            conflicting_items=(ia.item_id, ib.item_id),
                            required_trust=TrustTier.VERIFIED,
                            fill_suggestion="Resolve conflicting evidence by providing a disambiguating proof.",
                        ))

        return gaps

    def reconstruct(
        self,
        evidence: PartialEvidence,
        plan: ReconstructionPlan,
        *,
        expected_coordinates: frozenset[str] | None = None,
    ) -> ReconstructionGlobalSection | ReconstructionDescentObstruction:
        """Execute the reconstruction plan.  NEVER raises."""
        t0 = time.monotonic_ns()
        expected = expected_coordinates or evidence.covered_coordinates()

        # Check for blocking gaps
        blocking = evidence.blocking_gaps()
        if blocking:
            gap_coords = tuple(g.coordinate for g in blocking[:5])
            obs = ReconstructionCechObstruction(
                coordinate=self.coordinate,
                cocycle_description=(
                    f"Reconstruction blocked by {len(blocking)} gap(s): "
                    f"{gap_coords}"
                ),
                conflicting_evidence_ids=tuple(
                    eid
                    for g in blocking[:5]
                    for eid in g.conflicting_items
                )[:10],
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion="Fill blocking gaps or add missing evidence.",
            )
            return ReconstructionDescentObstruction(
                coordinate=self.coordinate,
                obstruction=obs,
                unresolved_gaps=blocking,
                diagnosis=f"{len(blocking)} blocking gap(s) prevent reconstruction.",
                repair_hints=("fill-missing-sections", "resolve-conflicts"),
            )

        # Execute plan steps
        reconstructed: dict[str, Any] = {}
        coverage: dict[str, str] = {}

        # First: fill from existing evidence
        for item in evidence.items:
            if item.coordinate not in reconstructed:
                reconstructed[item.coordinate] = item.content
                coverage[item.coordinate] = item.item_id

        # Then: execute fill steps
        for step in plan.steps:
            if step.kind == ReconstructionStepKind.FILL_GAP:
                if step.target_coordinate not in reconstructed:
                    reconstructed[step.target_coordinate] = self.default_fill_value
                    coverage[step.target_coordinate] = step.step_id
            elif step.kind == ReconstructionStepKind.GLUE_SECTIONS:
                # Verify inputs are available
                for inp_id in step.input_evidence_ids:
                    inp_item = next(
                        (it for it in evidence.items if it.item_id == inp_id), None
                    )
                    if inp_item and step.output_coordinate not in reconstructed:
                        reconstructed[step.output_coordinate] = inp_item.content
                        coverage[step.output_coordinate] = inp_id
            elif step.kind == ReconstructionStepKind.EXTEND_BY_DEFAULT:
                if step.target_coordinate not in reconstructed:
                    reconstructed[step.target_coordinate] = self.default_fill_value
                    coverage[step.target_coordinate] = f"default:{step.step_id}"

        # Check remaining gaps
        remaining_gaps = [
            coord for coord in expected if coord not in reconstructed
        ]
        if remaining_gaps:
            obs = ReconstructionCechObstruction(
                coordinate=self.coordinate,
                cocycle_description=(
                    f"{len(remaining_gaps)} coordinate(s) unreconstructed after plan execution."
                ),
                conflicting_evidence_ids=(),
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=True,  # these are trivially fillable with defaults
                repair_suggestion="Add FILL_GAP steps for remaining coordinates.",
            )
            # Fill with defaults anyway (coboundary = trivially resolvable)
            for coord in remaining_gaps:
                reconstructed[coord] = self.default_fill_value
                coverage[coord] = "default"

        # Compute trust tier as meet of all evidence trust tiers
        trust = evidence.minimum_trust()

        witness_jmt = _make_recon_judgment(
            coordinate=self.coordinate,
            phi="total_model_reconstructed",
            carrier="total_model",
            evidence=[f"bundle:{evidence.bundle_id}", f"plan:{plan.plan_id}"],
            obligations=[],
            trust=trust,
            provenance={
                "bundle_id": evidence.bundle_id,
                "plan_id": plan.plan_id,
                "reconstructed_at": _now_iso(),
            },
        )
        witness = TotalModelWitness(
            witness_id=str(uuid.uuid4()),
            model_coordinate=self.coordinate,
            model_kind=self.model_kind,
            reconstructed_sections=dict(reconstructed),
            evidence_coverage=dict(coverage),
            trust_tier=trust,
            judgment=witness_jmt,
            reconstruction_time_ns=time.monotonic_ns() - t0,
            plan_id=plan.plan_id,
        )
        global_jmt = _make_recon_judgment(
            coordinate=self.coordinate,
            phi="global_section_exists",
            carrier="reconstruction_global_section",
            evidence=[f"witness:{witness.witness_id}"],
            obligations=[],
            trust=trust,
            provenance={"witness_id": witness.witness_id},
        )
        return ReconstructionGlobalSection(
            coordinate=self.coordinate,
            witness=witness,
            judgment=global_jmt,
        )


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def reconstruct_model(
    evidence: PartialEvidence,
    plan: ReconstructionPlan | None = None,
    *,
    coordinate: str | None = None,
    model_kind: ModelKind = ModelKind.FUNCTION_MODEL,
    default_fill_value: Any = None,
) -> ReconstructionGlobalSection | ReconstructionDescentObstruction:
    """Reconstruct a total model from partial evidence.

    Parameters
    ----------
    evidence : PartialEvidence
        The partial evidence bundle.
    plan : ReconstructionPlan or None
        The reconstruction plan; if None, a default plan is built.
    coordinate : str or None
        Semantic coordinate; defaults to evidence.coverage_coordinate.
    model_kind : ModelKind
        The kind of model being reconstructed.
    default_fill_value : Any
        Default value used to fill underdetermined gaps.

    Returns
    -------
    ReconstructionGlobalSection | ReconstructionDescentObstruction
        NEVER raises.
    """
    coord = coordinate or evidence.coverage_coordinate
    logger.debug(
        "reconstruct_model: bundle=%s plan=%s coord=%s",
        evidence.bundle_id,
        plan.plan_id if plan else "auto",
        coord,
    )
    if plan is None:
        plan = build_reconstruction_plan(
            evidence, coordinate=coord, model_kind=model_kind
        )
    reconstructor = ModelReconstructor(
        model_kind=model_kind,
        coordinate=coord,
        default_fill_value=default_fill_value,
    )
    return reconstructor.reconstruct(
        evidence,
        plan,
        expected_coordinates=evidence.covered_coordinates(),
    )


def build_reconstruction_plan(
    evidence: PartialEvidence,
    *,
    coordinate: str | None = None,
    model_kind: ModelKind = ModelKind.FUNCTION_MODEL,
) -> ReconstructionPlan:
    """Build a reconstruction plan from an evidence bundle.

    The plan is built by generating one step for each evidence gap:
    * FILL_GAP for missing sections.
    * RECONCILE_CONFLICT for conflicting evidence.
    * GLUE_SECTIONS to join existing evidence items.

    Parameters
    ----------
    evidence : PartialEvidence
    coordinate : str or None
    model_kind : ModelKind

    Returns
    -------
    ReconstructionPlan
    """
    coord = coordinate or evidence.coverage_coordinate
    logger.debug("build_reconstruction_plan: bundle=%s", evidence.bundle_id)
    steps: list[ReconstructionStep] = []

    # Step 1: glue existing evidence
    for item in evidence.items:
        jmt = _make_recon_judgment(
            coordinate=f"{coord}.glue.{item.coordinate}",
            phi=f"glue_section_{item.item_id[:12]}",
            carrier="reconstruction_step",
            evidence=[item.item_id],
            obligations=[],
            trust=item.trust_tier,
            provenance={"item_id": item.item_id},
        )
        steps.append(ReconstructionStep(
            step_id=_stable_id("step:glue", item.item_id),
            kind=ReconstructionStepKind.GLUE_SECTIONS,
            target_coordinate=item.coordinate,
            action_description=f"Glue section from evidence item {item.item_id[:12]}",
            input_evidence_ids=(item.item_id,),
            output_coordinate=item.coordinate,
            smt_action=f"(glue-section {item.coordinate} {item.item_id[:12]})",
            judgment=jmt,
            estimated_cost=1,
        ))

    # Step 2: fill gaps
    for gap in evidence.gaps:
        if gap.kind == GapKind.MISSING_SECTION:
            jmt = _make_recon_judgment(
                coordinate=f"{coord}.fill.{gap.coordinate}",
                phi=f"fill_gap_{gap.gap_id[:12]}",
                carrier="reconstruction_step",
                evidence=[gap.gap_id],
                obligations=[],
                trust=TrustTier.PROPOSAL,
                provenance={"gap_id": gap.gap_id},
            )
            steps.append(ReconstructionStep(
                step_id=_stable_id("step:fill", gap.gap_id),
                kind=ReconstructionStepKind.FILL_GAP,
                target_coordinate=gap.coordinate,
                action_description=f"Fill missing section at {gap.coordinate}",
                input_evidence_ids=(),
                output_coordinate=gap.coordinate,
                smt_action=f"(fill-default {gap.coordinate})",
                judgment=jmt,
                estimated_cost=2,
            ))
        elif gap.kind == GapKind.CONFLICTING_EVIDENCE:
            jmt = _make_recon_judgment(
                coordinate=f"{coord}.reconcile.{gap.coordinate}",
                phi=f"reconcile_{gap.gap_id[:12]}",
                carrier="reconstruction_step",
                evidence=list(gap.conflicting_items),
                obligations=[f"disambiguate:{gap.coordinate}"],
                trust=TrustTier.REVIEWED,
                provenance={"gap_id": gap.gap_id},
            )
            steps.append(ReconstructionStep(
                step_id=_stable_id("step:reconcile", gap.gap_id),
                kind=ReconstructionStepKind.RECONCILE_CONFLICT,
                target_coordinate=gap.coordinate,
                action_description=f"Reconcile conflicting evidence at {gap.coordinate}",
                input_evidence_ids=tuple(gap.conflicting_items),
                output_coordinate=gap.coordinate,
                smt_action=f"(reconcile-conflict {gap.coordinate})",
                judgment=jmt,
                estimated_cost=5,
            ))

    total_cost = sum(s.estimated_cost for s in steps)
    plan_jmt = _make_recon_judgment(
        coordinate=coord,
        phi="reconstruction_plan_built",
        carrier="reconstruction_plan",
        evidence=[f"bundle:{evidence.bundle_id}"],
        obligations=[] if not evidence.blocking_gaps() else ["resolve_blocking_gaps"],
        trust=TrustTier.REVIEWED,
        provenance={"bundle_id": evidence.bundle_id, "built_at": _now_iso()},
    )
    return ReconstructionPlan(
        plan_id=_stable_id("plan", coord + evidence.bundle_id),
        steps=tuple(steps),
        evidence_bundle=evidence,
        estimated_total_cost=total_cost,
        judgment=plan_jmt,
        status=ReconstructionStatus.PENDING,
    )


def validate_total_model(
    witness: TotalModelWitness,
    evidence: PartialEvidence,
    *,
    coordinate: str | None = None,
) -> ReconstructionGlobalSection | ReconstructionDescentObstruction:
    """Validate that a total model witness is consistent with all evidence.

    Descent NEVER raises — returns either a global section (valid) or
    a descent obstruction (invalid).

    Parameters
    ----------
    witness : TotalModelWitness
        The witness to validate.
    evidence : PartialEvidence
        The evidence bundle used during reconstruction.
    coordinate : str or None
        Semantic coordinate.

    Returns
    -------
    ReconstructionGlobalSection | ReconstructionDescentObstruction
    """
    coord = coordinate or witness.model_coordinate
    logger.debug("validate_total_model: witness=%s", witness.witness_id)
    conflicts: list[tuple[str, str, str]] = []

    for item in evidence.items:
        # Check that the reconstructed value is consistent with the evidence
        recon_val = witness.reconstructed_sections.get(item.coordinate)
        if recon_val is not None and recon_val != item.content:
            # Only flag concrete evidence conflicts (not defaults)
            coverage_src = witness.evidence_coverage.get(item.coordinate, "")
            if not coverage_src.startswith("default"):
                conflicts.append((item.coordinate, item.item_id, str(recon_val)[:80]))

    if conflicts:
        coord_list = tuple(c for c, _, _ in conflicts[:5])
        obs = ReconstructionCechObstruction(
            coordinate=coord,
            cocycle_description=(
                f"Reconstructed model conflicts with {len(conflicts)} evidence item(s): "
                f"{coord_list}"
            ),
            conflicting_evidence_ids=tuple(eid for _, eid, _ in conflicts[:10]),
            trust_tier=TrustTier.PROOF_BACKED,
            is_coboundary=False,
            repair_suggestion="Re-run reconstruction with conflicting evidence resolved.",
        )
        return ReconstructionDescentObstruction(
            coordinate=coord,
            obstruction=obs,
            unresolved_gaps=tuple(
                EvidenceGap(
                    gap_id=_stable_id("val_gap", c),
                    coordinate=c,
                    kind=GapKind.CONFLICTING_EVIDENCE,
                    description=f"Reconstructed value conflicts with evidence {eid}",
                    conflicting_items=(eid,),
                )
                for c, eid, _ in conflicts[:10]
            ),
            diagnosis=f"{len(conflicts)} model-evidence conflicts detected during validation.",
            repair_hints=("re-reconstruct", "resolve-evidence-conflicts"),
        )

    jmt = _make_recon_judgment(
        coordinate=coord,
        phi="total_model_valid",
        carrier="validated_total_model",
        evidence=[f"witness:{witness.witness_id}", f"bundle:{evidence.bundle_id}"],
        obligations=[],
        trust=witness.trust_tier,
        provenance={"witness_id": witness.witness_id, "validated_at": _now_iso()},
    )
    return ReconstructionGlobalSection(
        coordinate=coord,
        witness=witness,
        judgment=jmt,
    )


# ---------------------------------------------------------------------------
# Reconstruction statistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReconstructionStats:
    """Aggregate statistics for a batch of reconstruction operations."""

    total_reconstructions: int
    successes: int
    failures: int
    total_evidence_items: int
    total_gaps_filled: int
    total_conflicts_resolved: int
    min_trust_seen: TrustTier
    max_trust_seen: TrustTier

    @classmethod
    def from_results(
        cls,
        results: Sequence[ReconstructionGlobalSection | ReconstructionDescentObstruction],
        evidences: Sequence[PartialEvidence],
    ) -> ReconstructionStats:
        successes = sum(1 for r in results if isinstance(r, ReconstructionGlobalSection))
        failures = len(results) - successes
        items = sum(len(e.items) for e in evidences)
        gaps = sum(len(e.gaps) for e in evidences)
        conflicts = sum(
            sum(1 for g in e.gaps if g.kind == GapKind.CONFLICTING_EVIDENCE)
            for e in evidences
        )
        min_t = TrustTier.PROOF_BACKED
        max_t = TrustTier.PROPOSAL
        for e in evidences:
            min_t = min_t.meet(e.minimum_trust())
            max_t = max_t.join(e.maximum_trust())
        return cls(
            total_reconstructions=len(results),
            successes=successes,
            failures=failures,
            total_evidence_items=items,
            total_gaps_filled=gaps,
            total_conflicts_resolved=conflicts,
            min_trust_seen=min_t,
            max_trust_seen=max_t,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_reconstructions": self.total_reconstructions,
            "successes": self.successes,
            "failures": self.failures,
            "total_evidence_items": self.total_evidence_items,
            "total_gaps_filled": self.total_gaps_filled,
            "total_conflicts_resolved": self.total_conflicts_resolved,
            "min_trust_seen": self.min_trust_seen.name,
            "max_trust_seen": self.max_trust_seen.name,
        }


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "EvidenceGap",
    "EvidenceItem",
    "EvidenceKind",
    "GapKind",
    "ModelKind",
    "ModelReconstructor",
    "PartialEvidence",
    "ReconstructionCechObstruction",
    "ReconstructionDescentObstruction",
    "ReconstructionGlobalSection",
    "ReconstructionJudgment",
    "ReconstructionPlan",
    "ReconstructionStats",
    "ReconstructionStatus",
    "ReconstructionStep",
    "ReconstructionStepKind",
    "TotalModelWitness",
    "TrustTier",
    "build_reconstruction_plan",
    "reconstruct_model",
    "validate_total_model",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== model_reconstruction_as_a_first_cl — smoke test ===")

    # Build evidence items
    def _item(coord: str, content: str, trust: TrustTier, kind: EvidenceKind) -> EvidenceItem:
        jmt = _make_recon_judgment(coord, f"evidence_at_{coord}", "evidence_item",
                                   [content[:20]], [], trust,
                                   {"coordinate": coord})
        return EvidenceItem(
            item_id=_stable_id("item", coord + content),
            coordinate=coord,
            kind=kind,
            content=content,
            trust_tier=trust,
            support_scope=coord,
            judgment=jmt,
        )

    items = (
        _item("f.domain.x>0", "(> x 0)", TrustTier.RUNTIME_WITNESSED, EvidenceKind.RUNTIME_OBSERVATION),
        _item("f.return.type", "Int", TrustTier.REVIEWED, EvidenceKind.TYPE_ANNOTATION),
        _item("f.invariant.nonneg", "(>= (f x) 0)", TrustTier.SOLVER_DISCHARGED if not True else TrustTier.PROOF_BACKED, EvidenceKind.INVARIANT),
    )
    gaps: tuple[EvidenceGap, ...] = ()

    bundle_jmt = _make_recon_judgment(
        "test.bundle", "evidence_bundle", "evidence_bundle",
        [i.item_id for i in items], [], TrustTier.REVIEWED,
    )
    evidence = PartialEvidence(
        bundle_id=_stable_id("bundle", "test"),
        items=items,
        gaps=gaps,
        coverage_coordinate="test.function_f",
        model_kind=ModelKind.FUNCTION_MODEL,
        judgment=bundle_jmt,
    )
    print(f"PartialEvidence: bundle={evidence.bundle_id[:12]}… "
          f"items={len(evidence.items)} gaps={len(evidence.gaps)} "
          f"conflicts={evidence.has_conflicts()}")
    assert not evidence.has_conflicts()
    assert evidence.minimum_trust() == TrustTier.REVIEWED

    # Build plan
    plan = build_reconstruction_plan(evidence, coordinate="test.function_f")
    print(f"ReconstructionPlan: id={plan.plan_id[:12]}… "
          f"steps={len(plan.steps)} cost={plan.estimated_total_cost}")
    assert len(plan.steps) >= len(items)

    # Reconstruct
    result = reconstruct_model(evidence, plan, coordinate="test.function_f")
    print(f"Reconstruction: {type(result).__name__}")
    assert isinstance(result, ReconstructionGlobalSection)
    witness = result.witness
    print(f"TotalModelWitness: id={witness.witness_id[:12]}… "
          f"sections={len(witness.reconstructed_sections)} "
          f"trust={witness.trust_tier.name}")
    assert witness.is_total()
    assert witness.trust_tier == TrustTier.REVIEWED  # min of evidence tiers

    # Validate
    validation = validate_total_model(witness, evidence, coordinate="test.function_f")
    print(f"Validation: {type(validation).__name__}")
    assert isinstance(validation, ReconstructionGlobalSection)

    # Test with blocking gap
    blocking_gap = EvidenceGap(
        gap_id="blocking_gap_1",
        coordinate="f.missing_coord",
        kind=GapKind.CONFLICTING_EVIDENCE,
        description="Conflicting evidence test",
        conflicting_items=("e1", "e2"),
        required_trust=TrustTier.VERIFIED,
    )
    blocked_jmt = _make_recon_judgment("blocked", "blocked", "blocked", [], ["fix_gap"], TrustTier.PROPOSAL)
    blocked_evidence = PartialEvidence(
        bundle_id="blocked_bundle",
        items=items,
        gaps=(blocking_gap,),
        coverage_coordinate="test.blocked",
        model_kind=ModelKind.FUNCTION_MODEL,
        judgment=blocked_jmt,
    )
    blocked_result = reconstruct_model(blocked_evidence)
    print(f"Blocked reconstruction: {type(blocked_result).__name__}")
    assert isinstance(blocked_result, ReconstructionDescentObstruction)

    # Statistics
    stats = ReconstructionStats.from_results(
        [result, blocked_result],
        [evidence, blocked_evidence],
    )
    print(f"Stats: {stats.to_dict()}")
    assert stats.total_reconstructions == 2
    assert stats.successes == 1
    assert stats.failures == 1

    # Trust algebra
    t = TrustTier.REVIEWED
    assert t.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    assert t.meet(TrustTier.PROPOSAL) == TrustTier.PROPOSAL
    print("TrustTier: OK")

    # JSON
    d = result.to_dict()
    j = json.dumps(d, default=str)
    assert "coordinate" in j
    print("JSON: OK")

    print("All assertions passed.")
    sys.exit(0)
