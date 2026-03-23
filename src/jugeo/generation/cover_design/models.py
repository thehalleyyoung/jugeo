r"""Core dataclass models for the cover_design sub-package.

Theory (theory2.tex §40 — Cover Design):
    Chapter 40 of theory2.tex develops the theory of *cover design* — the
    process of selecting and organising the set of local coordinate patches
    that together form a cover of the judgment site.  A *cover* in the
    categorical sense is a collection of morphisms

        { φ_i : U_i → X }_{i ∈ I}

    such that the induced map ∐_i U_i → X is an effective epimorphism.  In
    the JuGeo generation context, X is the full judgment tree, and each U_i
    is a *patch* — a contextually coherent sub-tree that can be processed
    independently by a local construction loop (§39).

    §40.2 — Čech Conditions
    -----------------------
    The central coherence requirement for a cover is the *Čech condition* on
    every non-empty pairwise overlap U_i ∩ U_j.  Formally, if s_i and s_j
    are sections over U_i and U_j respectively, they must agree on the
    overlap:

        s_i |_{U_i ∩ U_j}  ≡  s_j |_{U_i ∩ U_j}

    A violation of this condition means that two independently generated
    patches are incompatible at their boundary, and the overall section
    cannot be assembled into a global section of the sheaf.  The JuGeo
    runtime raises :class:`CechConditionViolation` when such incompatibility
    is detected.  The overlap compatibility check is performed by
    :meth:`OverlapRecord.check_cech_condition` and called systematically
    in :meth:`CoverDesignPlan.validate_cech_conditions`.

    §40.3 — Budget as a First-Class Object
    ----------------------------------------
    Per §40.3 of theory2.tex, a computational *budget* is not merely an
    integer token count but a structured object that can be:

    * *Allocated*: sub-budgets earmarked for specific patches.
    * *Split*: divided into n equal sub-budgets for parallel work.
    * *Merged*: two budgets combined into one (e.g., after reclaiming
      unspent allocation from a failed patch).

    The :class:`Budget` frozen dataclass models this first-class object with
    full allocation tracking.  The invariant

        remaining  ≤  total

    must always hold; methods that would violate it raise
    :class:`BudgetExhaustedError`.

    §40.4 — Trust Tiers
    --------------------
    Theory2.tex §40.4 requires that all generated code (and by extension all
    generated section data) enters the system at the ``PROPOSAL`` trust tier.
    Trust can only be upgraded by an external verification step; it can never
    be self-reported.  The :class:`TrustTier` enum encodes the four tiers:

    * ``PROPOSAL``  — freshly generated; not yet reviewed.
    * ``PROVISIONAL`` — passed automated checks; pending human review.
    * ``VERIFIED``   — reviewed and approved by a trusted human verifier.
    * ``CANONICAL``  — promoted to the canonical section database.

    :class:`PatchDescriptor` carries a ``trust_tier`` field that defaults to
    ``TrustTier.PROPOSAL``, enforcing the §40.4 invariant automatically.

    §40.5 — Dependency Order and Parallelism Groups
    -------------------------------------------------
    §40.5 defines the *dependency graph* D = (V, E) where an edge (i, j)
    means patch i depends on an already-computed output of patch j.  A
    *topological sort* of D yields the ``dependency_order`` tuple in
    :class:`CoverDesignPlan`.  Independent patches (those with no edges
    between them) may be placed in the same *parallelism group* and processed
    concurrently.  The ``parallelism_groups`` tuple encodes this partition.

    §40.6 — Cover Design Phases
    ----------------------------
    The cover design process itself is a five-phase state machine:

    1. ``ANALYSIS``    — examine the judgment site; infer patch boundaries.
    2. ``SELECTION``   — choose which patches to include in the cover.
    3. ``ALLOCATION``  — assign budget fractions to individual patches.
    4. ``EXECUTION``   — run the local construction loops for each patch.
    5. ``VERIFICATION`` — check Čech conditions on all pairwise overlaps.

    Each phase is represented by a member of :class:`CoverDesignPhase`.

    copilot: models-marker

Usage::

    from jugeo.generation.cover_design.models import (
        TrustTier,
        PatchStatus,
        CoverDesignPhase,
        OverlapCompatibility,
        Budget,
        PatchDescriptor,
        OverlapRecord,
        CoverDesignPlan,
        QualityMetric,
        CoverDesignResult,
    )
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo imports — guarded so that models.py can be imported even
# when the wider jugeo package is only partially installed or during testing.
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.goals import GenerationGoal as _GenerationGoal  # noqa: F401
    _HAS_GENERATION_GOAL = True
except ImportError:
    _HAS_GENERATION_GOAL = False

try:
    from jugeo.generation.construction import ConstructionContext as _ConstructionContext  # noqa: F401
    _HAS_CONSTRUCTION_CONTEXT = True
except ImportError:
    _HAS_CONSTRUCTION_CONTEXT = False

try:
    from jugeo.trust import TrustVerifier as _TrustVerifier  # noqa: F401
    _HAS_TRUST_VERIFIER = True
except ImportError:
    _HAS_TRUST_VERIFIER = False

__all__ = [
    # Enumerations
    "TrustTier",
    "PatchStatus",
    "CoverDesignPhase",
    "OverlapCompatibility",
    # Exceptions
    "CoverDesignError",
    "CechConditionViolation",
    "BudgetExhaustedError",
    "PatchSelectionError",
    # Frozen dataclasses
    "Budget",
    "PatchDescriptor",
    "OverlapRecord",
    "CoverDesignPlan",
    "QualityMetric",
    "CoverDesignResult",
    # Constants
    "DEFAULT_QUALITY_THRESHOLD",
    "DEFAULT_BUDGET_UNIT",
    "MAX_PARALLELISM_GROUPS",
    # Helper functions
    "validate_patch_id",
    "make_overlap_key",
    "compute_overlap_fraction",
    "topological_sort",
    "build_parallelism_groups",
    "summarise_budget_allocation",
    "format_cech_violation",
    "patch_priority_key",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_QUALITY_THRESHOLD: float = 0.8
"""Default minimum quality score required before a cover is accepted.

Corresponds to theory2.tex §40.6 Definition 40.6.4 where the acceptance
criterion for the verification phase is expressed as a numerical threshold
in [0, 1].
"""

DEFAULT_BUDGET_UNIT: str = "tokens"
"""Default unit for :class:`Budget` objects.

Token counts are the primary budget currency in the JuGeo generation
pipeline; other units (e.g., "seconds", "flops") are supported but less
common.
"""

MAX_PARALLELISM_GROUPS: int = 64
"""Upper bound on the number of parallelism groups in a :class:`CoverDesignPlan`.

Enforced to prevent pathological plans that create one group per patch.
See theory2.tex §40.5 Remark 40.5.2.
"""

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def validate_patch_id(patch_id: str) -> bool:
    """Return ``True`` iff *patch_id* is a non-empty, stripped string.

    Raises :class:`ValueError` if the patch_id is empty or not a string so
    that downstream code can catch it early rather than silently producing
    incorrect behaviour when an identifier is malformed.

    Args:
        patch_id: The candidate patch identifier to validate.

    Returns:
        ``True`` if valid.

    Raises:
        ValueError: If ``patch_id`` is not a non-empty string.

    Examples:
        >>> validate_patch_id("patch-alpha")
        True
        >>> validate_patch_id("")
        Traceback (most recent call last):
            ...
        ValueError: Invalid patch_id: ''
    """
    if not isinstance(patch_id, str) or not patch_id.strip():
        raise ValueError(f"Invalid patch_id: {patch_id!r}")
    return True


def make_overlap_key(patch_a: str, patch_b: str) -> str:
    """Return a canonical, order-independent key for the overlap of two patches.

    The key is formed by sorting the two IDs lexicographically and joining
    them with ``":"``.  This ensures that ``make_overlap_key("a", "b")`` and
    ``make_overlap_key("b", "a")`` produce the same result, which is
    important for consistent dict lookups.

    Args:
        patch_a: First patch identifier.
        patch_b: Second patch identifier.

    Returns:
        A string of the form ``"<smaller_id>:<larger_id>"``.

    Examples:
        >>> make_overlap_key("beta", "alpha")
        'alpha:beta'
        >>> make_overlap_key("alpha", "beta")
        'alpha:beta'
    """
    lo, hi = (patch_a, patch_b) if patch_a <= patch_b else (patch_b, patch_a)
    return f"{lo}:{hi}"


def compute_overlap_fraction(overlap_size: int, context_size_a: int, context_size_b: int) -> float:
    """Return the fraction of the smaller patch that the overlap occupies.

    This heuristic measure is used to gauge how tightly two patches are
    coupled.  An overlap fraction near 1.0 means the smaller patch is almost
    entirely contained in the larger one; near 0.0 means the patches barely
    touch.

    Theory2.tex §40.2 uses this value when ranking Čech violations by
    severity: a violation on a large-fraction overlap is more critical than
    one on a small-fraction overlap.

    Args:
        overlap_size: Number of tokens (or nodes) in the shared region.
        context_size_a: Size of patch A.
        context_size_b: Size of patch B.

    Returns:
        A float in [0.0, 1.0].

    Raises:
        ValueError: If any argument is negative, or if both context sizes
            are zero.
    """
    if overlap_size < 0 or context_size_a < 0 or context_size_b < 0:
        raise ValueError("Sizes must be non-negative.")
    smaller = min(context_size_a, context_size_b)
    if smaller == 0:
        return 0.0
    return min(overlap_size / smaller, 1.0)


def topological_sort(dependencies: dict[str, frozenset[str]]) -> list[str]:
    """Return a topological ordering of the nodes in *dependencies*.

    Uses Kahn's algorithm (BFS-based topological sort).  The returned list
    guarantees that for every edge (u → v) — meaning u depends on v — v
    appears *before* u in the list.

    This function is used by :class:`CoverDesignPlan` to compute the
    ``dependency_order`` field.  It corresponds to theory2.tex §40.5
    Algorithm 40.5.1.

    Args:
        dependencies: A dict mapping each node to the frozenset of nodes it
            directly depends on.  Nodes that appear only as values (i.e.,
            have no dependents and no dependencies of their own) are also
            included in the output.

    Returns:
        A list of node identifiers in topological order (leaves first).

    Raises:
        CoverDesignError: If the dependency graph contains a cycle.
    """
    # Build reverse graph: predecessor_count and adjacency for successors
    all_nodes: set[str] = set(dependencies.keys())
    for deps in dependencies.values():
        all_nodes |= deps

    in_degree: dict[str, int] = {n: 0 for n in all_nodes}
    successors: dict[str, list[str]] = {n: [] for n in all_nodes}

    for node, deps in dependencies.items():
        for dep in deps:
            in_degree[node] += 1
            successors[dep].append(node)

    queue = [n for n in all_nodes if in_degree[n] == 0]
    order: list[str] = []

    while queue:
        current = queue.pop(0)
        order.append(current)
        for succ in successors[current]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    if len(order) != len(all_nodes):
        raise CoverDesignError(
            "Dependency graph contains a cycle; topological sort is impossible. "
            f"Unresolved nodes: {sorted(all_nodes - set(order))}"
        )
    return order


def build_parallelism_groups(
    dependency_order: list[str],
    dependencies: dict[str, frozenset[str]],
) -> list[frozenset[str]]:
    """Partition *dependency_order* into groups of independently runnable patches.

    Two patches land in the same group iff neither depends (directly or
    transitively) on the other.  This partitioning is used to maximise
    parallelism during the execution phase (§40.5).

    The algorithm assigns each node to the *earliest* group consistent with
    its dependencies.  Group 0 contains all patches with no dependencies;
    group k contains all patches whose deepest dependency is in group k−1.

    Args:
        dependency_order: A topologically sorted list of patch IDs (leaves
            first), as produced by :func:`topological_sort`.
        dependencies: The original dependency dict.

    Returns:
        A list of :class:`frozenset` objects; groups appear in execution
        order (group 0 must finish before group 1 can start, etc.).
    """
    group_of: dict[str, int] = {}
    for node in dependency_order:
        deps = dependencies.get(node, frozenset())
        if not deps:
            group_of[node] = 0
        else:
            group_of[node] = max((group_of.get(d, 0) for d in deps), default=0) + 1

    max_group = max(group_of.values(), default=0)
    groups: list[set[str]] = [set() for _ in range(max_group + 1)]
    for node, g in group_of.items():
        groups[g].add(node)
    return [frozenset(g) for g in groups if g]


def summarise_budget_allocation(budget: "Budget") -> str:
    """Return a multi-line human-readable summary of a :class:`Budget`.

    Useful for logging and debugging during the allocation phase.

    Args:
        budget: The budget object to summarise.

    Returns:
        A multi-line string showing total, remaining, and per-patch allocations.
    """
    lines = [
        f"Budget ({budget.unit}):",
        f"  Total     : {budget.total:.2f}",
        f"  Remaining : {budget.remaining:.2f}  ({budget.fraction_remaining() * 100:.1f}%)",
        f"  Used      : {budget.total - budget.remaining:.2f}  ({budget.fraction_used() * 100:.1f}%)",
        f"  Allocations ({len(budget.allocated)}):",
    ]
    for patch_id, amount in sorted(budget.allocated.items()):
        pct = (amount / budget.total * 100) if budget.total > 0 else 0.0
        lines.append(f"    {patch_id}: {amount:.2f}  ({pct:.1f}%)")
    return "\n".join(lines)


def format_cech_violation(overlap: "OverlapRecord", detail: str = "") -> str:
    """Return a formatted error message for a Čech condition violation.

    The message includes the overlap key, overlap size, and an optional
    detail string that the caller may use to describe the specific
    incompatibility detected.

    Args:
        overlap: The :class:`OverlapRecord` whose Čech condition was violated.
        detail: Optional extra detail to append to the message.

    Returns:
        A human-readable error string.
    """
    key = make_overlap_key(overlap.patch_a, overlap.patch_b)
    msg = (
        f"Čech condition violated on overlap '{key}' "
        f"(size={overlap.overlap_size}, "
        f"compatibility={overlap.compatibility.value})"
    )
    if detail:
        msg += f": {detail}"
    return msg


def patch_priority_key(descriptor: "PatchDescriptor") -> float:
    """Return a numeric sort key for a :class:`PatchDescriptor`.

    Higher priority patches should be processed first, so the key is the
    *negated* priority value (so that ``sorted(..., key=patch_priority_key)``
    produces descending priority order).

    Args:
        descriptor: The patch descriptor to evaluate.

    Returns:
        ``-descriptor.priority`` as a float.
    """
    return -float(descriptor.priority)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrustTier(str, Enum):
    """Trust tier of a generated section or patch.

    Theory2.tex §40.4 specifies a strict lattice of trust levels.  All
    generated code enters at ``PROPOSAL``; elevation requires external
    verification and is never self-reported.

    The ordering is::

        PROPOSAL < PROVISIONAL < VERIFIED < CANONICAL

    Attributes:
        PROPOSAL:    Freshly generated content; has not been reviewed or
                     validated by any external process.
        PROVISIONAL: Passed automated static checks (type checking, linting,
                     basic test suite); not yet reviewed by a human.
        VERIFIED:    Reviewed and approved by a trusted human verifier; safe
                     to use in production.
        CANONICAL:   Promoted to the canonical section database; considered
                     ground truth for downstream patches.
    """

    PROPOSAL = "proposal"
    PROVISIONAL = "provisional"
    VERIFIED = "verified"
    CANONICAL = "canonical"


class PatchStatus(str, Enum):
    """Processing status of an individual patch during cover execution.

    The lifecycle of a patch is::

        PENDING ──start──► IN_PROGRESS ──finish──► COMPLETED
        IN_PROGRESS ──error──► FAILED
        PENDING ──skip──► SKIPPED

    Attributes:
        PENDING:     Patch has been selected but not yet started.
        IN_PROGRESS: Local construction loop for this patch is running.
        COMPLETED:   Patch was successfully processed and accepted.
        FAILED:      Patch processing failed (budget, error, or rejection).
        SKIPPED:     Patch was intentionally omitted (e.g., below threshold).
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CoverDesignPhase(str, Enum):
    """Phase of the cover design state machine.

    The five phases map to the stages described in theory2.tex §40.6::

        ANALYSIS ──► SELECTION ──► ALLOCATION ──► EXECUTION ──► VERIFICATION

    Attributes:
        ANALYSIS:     Examining the judgment site to identify natural patch
                      boundaries and assess context sizes.
        SELECTION:    Choosing the set of patches that will form the cover,
                      based on coverage analysis and priority signals.
        ALLOCATION:   Distributing the available budget across selected patches
                      according to their priority and estimated cost.
        EXECUTION:    Running local construction loops for each patch in the
                      order specified by the dependency graph.
        VERIFICATION: Checking Čech conditions on all pairwise overlaps;
                      producing the :class:`CoverDesignResult`.
    """

    ANALYSIS = "analysis"
    SELECTION = "selection"
    ALLOCATION = "allocation"
    EXECUTION = "execution"
    VERIFICATION = "verification"


class OverlapCompatibility(str, Enum):
    """Compatibility verdict for the shared region of two patches.

    Assigned by :meth:`OverlapRecord.check_cech_condition` after comparing
    the sections produced by the two patches on their overlap.

    Attributes:
        COMPATIBLE:   Sections agree on the overlap; Čech condition satisfied.
        INCOMPATIBLE: Sections disagree on the overlap; Čech condition violated.
        UNKNOWN:      Compatibility has not yet been checked.
    """

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CoverDesignError(Exception):
    """Base exception for the cover_design sub-package.

    All domain-specific errors in this module inherit from this class,
    enabling callers to catch all cover-design failures with a single
    ``except CoverDesignError`` clause while still allowing fine-grained
    handling via the subclasses.
    """


class CechConditionViolation(CoverDesignError):
    """Raised when two patches produce incompatible sections on their overlap.

    Corresponds to theory2.tex §40.2 Condition 40.2.1.  The violation means
    that the sections s_i and s_j disagree on U_i ∩ U_j, preventing the
    construction of a global section via the sheaf gluing axiom.

    The error message should include the patch IDs and a description of the
    specific incompatibility so that the planner can decide whether to
    re-run one of the patches or to widen the overlap boundary.
    """


class BudgetExhaustedError(CoverDesignError):
    """Raised when a :class:`Budget` cannot satisfy a requested allocation.

    This is the cover_design equivalent of the budget exhaustion error in
    theory2.tex §40.3 Condition 40.3.2.  It is raised when
    :meth:`Budget.allocate` would reduce ``remaining`` below zero, or when
    the overall budget for a plan is depleted before all patches are
    processed.
    """


class PatchSelectionError(CoverDesignError):
    """Raised when the patch selection phase fails to produce a valid cover.

    A selection is invalid if:
    * The selected patches do not jointly cover the full judgment site.
    * The dependency graph among selected patches contains a cycle.
    * No patches satisfy the minimum priority threshold.

    Callers should catch this error and either relax the selection criteria
    or abort the cover design run.
    """


# ---------------------------------------------------------------------------
# Budget (frozen dataclass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Budget:
    """A first-class budget object as required by theory2.tex §40.3.

    A :class:`Budget` is *immutable*: operations that change it (like
    :meth:`allocate`) return a new :class:`Budget` rather than modifying the
    existing one.  This ensures that budget history is fully auditable and
    that there are no accidental aliasing issues in parallel pipelines.

    Invariant:
        ``0.0 ≤ remaining ≤ total``

    Attributes:
        total: The initial budget amount at creation time.
        remaining: Budget not yet consumed or allocated.  Must satisfy
            ``remaining ≤ total``.
        allocated: A mapping from patch_id to the amount allocated to that
            patch.  Allocations reduce ``remaining`` but are tracked
            separately so that reclamation is possible.
        unit: Human-readable label for the budget currency (default:
            ``"tokens"``).
        created_at: Unix timestamp when this budget object was created.
    """

    total: float
    remaining: float
    allocated: dict[str, float]
    unit: str = DEFAULT_BUDGET_UNIT
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def fraction_used(self) -> float:
        """Return the fraction of the total budget that has been consumed.

        Returns:
            A float in [0.0, 1.0].  Returns 0.0 if ``total`` is zero to
            avoid division by zero.
        """
        if self.total <= 0.0:
            return 0.0
        return (self.total - self.remaining) / self.total

    def fraction_remaining(self) -> float:
        """Return the fraction of the total budget that has not been consumed.

        Returns:
            A float in [0.0, 1.0].  Returns 1.0 if ``total`` is zero.
        """
        return 1.0 - self.fraction_used()

    def is_exhausted(self) -> bool:
        """Return ``True`` iff the remaining budget is effectively zero.

        Uses a small epsilon (1e-9) to guard against floating-point noise.

        Returns:
            ``True`` if ``remaining < 1e-9``.
        """
        return self.remaining < 1e-9

    # ------------------------------------------------------------------
    # Transformations (return new Budget instances)
    # ------------------------------------------------------------------

    def allocate(self, patch_id: str, amount: float) -> "Budget":
        """Return a new :class:`Budget` with *amount* allocated to *patch_id*.

        If the patch already has an existing allocation, the new allocation
        *replaces* it and the difference is applied to ``remaining``.

        Args:
            patch_id: The identifier of the patch to allocate budget for.
            amount: A non-negative amount to allocate.

        Returns:
            A new :class:`Budget` with the allocation recorded.

        Raises:
            BudgetExhaustedError: If allocating *amount* would require more
                budget than is currently ``remaining`` (after accounting for
                any existing allocation for this patch).
            ValueError: If *amount* is negative.
        """
        if amount < 0.0:
            raise ValueError(f"Allocation amount must be non-negative, got {amount!r}.")
        validate_patch_id(patch_id)
        previous = self.allocated.get(patch_id, 0.0)
        delta = amount - previous
        new_remaining = self.remaining - delta
        if new_remaining < -1e-9:
            raise BudgetExhaustedError(
                f"Cannot allocate {amount:.2f} {self.unit} to patch '{patch_id}': "
                f"only {self.remaining:.2f} {self.unit} remaining "
                f"(existing allocation for this patch: {previous:.2f})."
            )
        new_allocated = {**self.allocated, patch_id: amount}
        return Budget(
            total=self.total,
            remaining=max(new_remaining, 0.0),
            allocated=new_allocated,
            unit=self.unit,
            created_at=self.created_at,
        )

    def split(self, n: int) -> list["Budget"]:
        """Return *n* equal sub-budgets whose totals sum to ``remaining``.

        Each sub-budget starts with no allocations and a fresh ``created_at``
        timestamp.  Useful for distributing budget across parallel groups
        during the allocation phase.

        Args:
            n: The number of sub-budgets to create.  Must be ≥ 1.

        Returns:
            A list of exactly *n* :class:`Budget` objects.

        Raises:
            ValueError: If *n* < 1.
        """
        if n < 1:
            raise ValueError(f"Cannot split into {n} sub-budgets; n must be ≥ 1.")
        share = self.remaining / n
        now = time.time()
        return [
            Budget(total=share, remaining=share, allocated={}, unit=self.unit, created_at=now)
            for _ in range(n)
        ]

    def merge(self, other: "Budget") -> "Budget":
        """Return a new :class:`Budget` that combines *self* and *other*.

        The merged budget has:
        * ``total`` = sum of both totals.
        * ``remaining`` = sum of both remainders.
        * ``allocated`` = union of both allocation dicts (self takes priority
          on conflicting keys).
        * ``unit`` = self.unit (a mismatch logs a warning).

        Args:
            other: Another :class:`Budget` instance to merge with.

        Returns:
            A new :class:`Budget`.
        """
        if other.unit != self.unit:
            logger.warning(
                "Merging budgets with different units: %r and %r. "
                "Using unit %r for the merged budget.",
                self.unit, other.unit, self.unit,
            )
        merged_allocated = {**other.allocated, **self.allocated}
        return Budget(
            total=self.total + other.total,
            remaining=self.remaining + other.remaining,
            allocated=merged_allocated,
            unit=self.unit,
            created_at=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representing this budget.

        Returns:
            A dict with keys ``total``, ``remaining``, ``allocated``,
            ``unit``, and ``created_at``.
        """
        return {
            "total": self.total,
            "remaining": self.remaining,
            "allocated": dict(self.allocated),
            "unit": self.unit,
            "created_at": self.created_at,
        }

    @classmethod
    def create(cls, total: float, unit: str = DEFAULT_BUDGET_UNIT) -> "Budget":
        """Convenience factory: create a fresh budget with nothing allocated.

        Args:
            total: The initial total budget amount.
            unit: The currency unit (default: ``"tokens"``).

        Returns:
            A :class:`Budget` with ``remaining == total`` and no allocations.
        """
        return cls(total=total, remaining=total, allocated={}, unit=unit)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Budget(total={self.total:.2f}, remaining={self.remaining:.2f}, "
            f"unit={self.unit!r}, allocations={len(self.allocated)})"
        )


# ---------------------------------------------------------------------------
# PatchDescriptor (frozen dataclass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatchDescriptor:
    """An immutable descriptor for a single local coordinate patch.

    A patch is the atomic unit of work in the cover design pipeline.  Each
    patch corresponds to a sub-tree of the judgment site that can be handled
    independently by a :class:`LocalConstructionLoop` (theory2.tex §39).

    The ``trust_tier`` field always defaults to ``TrustTier.PROPOSAL``,
    enforcing the §40.4 invariant that all generated content enters at the
    lowest trust level.

    Attributes:
        patch_id: A globally unique identifier for this patch.
        coordinate: The coordinate label (e.g., ``"goal/42/body"``) that
            identifies the region of the judgment site this patch covers.
        context_size: Estimated number of tokens in the patch context.  Used
            by the budget allocator in the allocation phase.
        overlap_ids: Frozenset of patch IDs that this patch is known to
            overlap with.  Used when constructing :class:`OverlapRecord`
            objects during the analysis phase.
        priority: A non-negative float indicating how urgently this patch
            should be processed relative to others.  Higher is more urgent.
        trust_tier: The trust level of this patch.  Must be
            ``TrustTier.PROPOSAL`` for freshly generated patches (§40.4).
        metadata: Arbitrary extra data; preserved across serialisation.
    """

    patch_id: str
    coordinate: str
    context_size: int
    overlap_ids: frozenset[str]
    priority: float = 1.0
    trust_tier: TrustTier = TrustTier.PROPOSAL
    metadata: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def overlaps_with(self, other: "PatchDescriptor") -> bool:
        """Return ``True`` iff this patch and *other* have a registered overlap.

        A registered overlap means that *other.patch_id* appears in
        ``self.overlap_ids`` **or** ``self.patch_id`` appears in
        ``other.overlap_ids``.  Both directions are checked because the
        analysis phase may populate overlap_ids asymmetrically before a
        full pass normalises them.

        Args:
            other: Another :class:`PatchDescriptor` to test.

        Returns:
            ``True`` if the patches overlap.
        """
        return other.patch_id in self.overlap_ids or self.patch_id in other.overlap_ids

    def is_boundary_patch(self) -> bool:
        """Return ``True`` iff this patch lies on the boundary of the cover.

        A boundary patch is defined as one that overlaps with at least one
        other patch.  Non-boundary (interior) patches are self-contained and
        have no overlap constraints to satisfy.

        Returns:
            ``True`` if ``overlap_ids`` is non-empty.
        """
        return len(self.overlap_ids) > 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representing this descriptor.

        Returns:
            A dict with all fields; ``overlap_ids`` and ``trust_tier`` are
            serialised as sorted list and string respectively.
        """
        return {
            "patch_id": self.patch_id,
            "coordinate": self.coordinate,
            "context_size": self.context_size,
            "overlap_ids": sorted(self.overlap_ids),
            "priority": self.priority,
            "trust_tier": self.trust_tier.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatchDescriptor":
        """Reconstruct a descriptor from a dict produced by :meth:`to_dict`.

        Args:
            data: A dictionary as returned by :meth:`to_dict`.

        Returns:
            A new :class:`PatchDescriptor`.
        """
        return cls(
            patch_id=data["patch_id"],
            coordinate=data["coordinate"],
            context_size=int(data["context_size"]),
            overlap_ids=frozenset(data.get("overlap_ids", [])),
            priority=float(data.get("priority", 1.0)),
            trust_tier=TrustTier(data.get("trust_tier", TrustTier.PROPOSAL.value)),
            metadata=dict(data.get("metadata", {})),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PatchDescriptor(id={self.patch_id!r}, coord={self.coordinate!r}, "
            f"size={self.context_size}, priority={self.priority:.2f}, "
            f"tier={self.trust_tier.value})"
        )


# ---------------------------------------------------------------------------
# OverlapRecord (frozen dataclass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlapRecord:
    """An immutable record describing the overlap between two patches.

    Created during the analysis phase once the analysis step determines that
    two patches share a non-empty region.  The ``compatibility`` field is
    updated (by creating a new record) once the verification phase has run
    :meth:`check_cech_condition`.

    Attributes:
        patch_a: ID of the first patch.
        patch_b: ID of the second patch.
        overlap_size: Estimated number of tokens in the shared region.
        compatibility: Current Čech compatibility verdict.
        cech_checked: ``True`` iff :meth:`check_cech_condition` has been run.
    """

    patch_a: str
    patch_b: str
    overlap_size: int
    compatibility: OverlapCompatibility = OverlapCompatibility.UNKNOWN
    cech_checked: bool = False

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def key(self) -> str:
        """Return the canonical overlap key (order-independent).

        Uses :func:`make_overlap_key` so that key comparisons are stable
        regardless of the order in which ``patch_a`` and ``patch_b`` were
        supplied.

        Returns:
            A string of the form ``"<smaller_id>:<larger_id>"``.
        """
        return make_overlap_key(self.patch_a, self.patch_b)

    def is_cech_compatible(self) -> bool:
        """Return ``True`` iff the Čech condition has been checked and passed.

        Note that an ``UNKNOWN`` compatibility is treated as *not* compatible
        because the condition has not yet been verified.

        Returns:
            ``True`` only if ``cech_checked is True`` and
            ``compatibility is OverlapCompatibility.COMPATIBLE``.
        """
        return self.cech_checked and self.compatibility is OverlapCompatibility.COMPATIBLE

    def check_cech_condition(
        self,
        section_a: dict[str, Any],
        section_b: dict[str, Any],
    ) -> "OverlapRecord":
        """Return a new :class:`OverlapRecord` with the Čech condition evaluated.

        Compares *section_a* and *section_b* on their shared keys.  If any
        key present in both dicts maps to different values, the sections are
        *incompatible* on the overlap and the returned record will carry
        ``compatibility=OverlapCompatibility.INCOMPATIBLE``.

        This is a simplified model of the full Čech comparison described in
        theory2.tex §40.2.  In a production implementation, *section_a* and
        *section_b* would be typed section objects with a richer comparison
        protocol.

        Args:
            section_a: Serialised section data produced by the patch_a
                local construction loop.
            section_b: Serialised section data produced by the patch_b
                local construction loop.

        Returns:
            A new :class:`OverlapRecord` with ``cech_checked=True`` and
            ``compatibility`` set to the result of the comparison.
        """
        shared_keys = set(section_a.keys()) & set(section_b.keys())
        compatible = all(section_a[k] == section_b[k] for k in shared_keys)
        verdict = (
            OverlapCompatibility.COMPATIBLE
            if compatible
            else OverlapCompatibility.INCOMPATIBLE
        )
        return OverlapRecord(
            patch_a=self.patch_a,
            patch_b=self.patch_b,
            overlap_size=self.overlap_size,
            compatibility=verdict,
            cech_checked=True,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary for this overlap record.

        Returns:
            A dict with all fields; ``compatibility`` is serialised as a
            string value.
        """
        return {
            "patch_a": self.patch_a,
            "patch_b": self.patch_b,
            "overlap_size": self.overlap_size,
            "compatibility": self.compatibility.value,
            "cech_checked": self.cech_checked,
            "key": self.key,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"OverlapRecord({self.patch_a!r}↔{self.patch_b!r}, "
            f"size={self.overlap_size}, compat={self.compatibility.value})"
        )


# ---------------------------------------------------------------------------
# CoverDesignPlan (frozen dataclass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverDesignPlan:
    """An immutable, fully specified plan for executing a cover design run.

    The plan encodes all decisions made during the ANALYSIS, SELECTION, and
    ALLOCATION phases so that the EXECUTION phase can proceed deterministically
    given only this object plus the actual model calls.

    Creating a :class:`CoverDesignPlan` does not execute anything; it is
    purely a value object that the execution engine consumes.

    Theory2.tex §40.6 defines the plan as the tuple

        (C, Ω, B, π, G)

    where C is the patch collection, Ω is the overlap collection, B is the
    budget, π is the quality threshold, and G is the dependency DAG encoded
    here as ``dependency_order`` and ``parallelism_groups``.

    Attributes:
        plan_id: Unique identifier for this plan.
        patches: Tuple of :class:`PatchDescriptor` objects, one per patch in
            the cover.
        overlaps: Tuple of :class:`OverlapRecord` objects, one per pairwise
            overlap detected during analysis.
        budget: The :class:`Budget` allocated for this plan's execution.
        phase: The current :class:`CoverDesignPhase` of this plan.  Freshly
            created plans are typically in ``CoverDesignPhase.ALLOCATION``.
        dependency_order: Tuple of patch IDs in topological order (leaves
            first, roots last).
        parallelism_groups: Tuple of frozensets; patches in the same group
            may be executed concurrently.
        quality_threshold: Minimum acceptable quality score for the overall
            cover.  Patches whose individual score falls below this threshold
            during verification may be flagged for re-generation.
        created_at: Unix timestamp of plan creation.
    """

    plan_id: str
    patches: tuple[PatchDescriptor, ...]
    overlaps: tuple[OverlapRecord, ...]
    budget: Budget
    phase: CoverDesignPhase
    dependency_order: tuple[str, ...]
    parallelism_groups: tuple[frozenset[str], ...]
    quality_threshold: float = DEFAULT_QUALITY_THRESHOLD
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def total_patches(self) -> int:
        """Return the total number of patches in this plan.

        Returns:
            ``len(self.patches)``
        """
        return len(self.patches)

    def total_overlaps(self) -> int:
        """Return the total number of pairwise overlaps in this plan.

        Returns:
            ``len(self.overlaps)``
        """
        return len(self.overlaps)

    def patches_by_priority(self) -> list[PatchDescriptor]:
        """Return patches sorted in descending priority order.

        Uses :func:`patch_priority_key` for consistent, reproducible ordering.
        Patches with equal priority are returned in their original tuple order.

        Returns:
            A list of :class:`PatchDescriptor` objects, highest priority first.
        """
        return sorted(self.patches, key=patch_priority_key)

    def get_patch(self, patch_id: str) -> PatchDescriptor:
        """Return the :class:`PatchDescriptor` for the given *patch_id*.

        Args:
            patch_id: The ID of the patch to look up.

        Returns:
            The matching :class:`PatchDescriptor`.

        Raises:
            KeyError: If no patch with the given ID exists in this plan.
        """
        for patch in self.patches:
            if patch.patch_id == patch_id:
                return patch
        raise KeyError(f"No patch with id {patch_id!r} in plan {self.plan_id!r}.")

    def get_overlaps_for(self, patch_id: str) -> list[OverlapRecord]:
        """Return all :class:`OverlapRecord` objects involving *patch_id*.

        Args:
            patch_id: The patch whose overlaps should be returned.

        Returns:
            A list of :class:`OverlapRecord` objects (may be empty).
        """
        return [
            o for o in self.overlaps
            if o.patch_a == patch_id or o.patch_b == patch_id
        ]

    def validate_cech_conditions(self) -> list[CechConditionViolation]:
        """Check Čech conditions on all overlaps and return any violations.

        Iterates over ``self.overlaps`` and collects any record whose
        ``compatibility`` is ``INCOMPATIBLE``.  Records that are ``UNKNOWN``
        (not yet checked) are not reported as violations but will cause a
        warning to be logged.

        Note that this method does *not* run the actual section comparison
        (that is the job of :meth:`OverlapRecord.check_cech_condition`).  It
        reports the status of comparisons that have already been recorded.

        Returns:
            A list of :class:`CechConditionViolation` exceptions (not
            raised); the list is empty if all checked overlaps are compatible.
        """
        violations: list[CechConditionViolation] = []
        for overlap in self.overlaps:
            if not overlap.cech_checked:
                logger.warning(
                    "Overlap %r has not been Čech-checked; skipping validation.",
                    overlap.key,
                )
            elif overlap.compatibility is OverlapCompatibility.INCOMPATIBLE:
                msg = format_cech_violation(overlap)
                violations.append(CechConditionViolation(msg))
        return violations

    def serialise(self) -> dict[str, Any]:
        """Return a fully JSON-serialisable dictionary for this plan.

        All nested objects are recursively serialised to dicts/lists so the
        result can be passed to ``json.dumps`` without further processing.

        Returns:
            A dict encoding all plan fields.
        """
        return {
            "plan_id": self.plan_id,
            "patches": [p.to_dict() for p in self.patches],
            "overlaps": [o.to_dict() for o in self.overlaps],
            "budget": self.budget.to_dict(),
            "phase": self.phase.value,
            "dependency_order": list(self.dependency_order),
            "parallelism_groups": [sorted(g) for g in self.parallelism_groups],
            "quality_threshold": self.quality_threshold,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoverDesignPlan":
        """Reconstruct a plan from a dict produced by :meth:`serialise`.

        Args:
            data: A dictionary as returned by :meth:`serialise`.

        Returns:
            A new :class:`CoverDesignPlan`.
        """
        raw_budget = data["budget"]
        budget = Budget(
            total=raw_budget["total"],
            remaining=raw_budget["remaining"],
            allocated=raw_budget.get("allocated", {}),
            unit=raw_budget.get("unit", DEFAULT_BUDGET_UNIT),
            created_at=raw_budget.get("created_at", time.time()),
        )
        patches = tuple(PatchDescriptor.from_dict(p) for p in data.get("patches", []))
        overlaps = tuple(
            OverlapRecord(
                patch_a=o["patch_a"],
                patch_b=o["patch_b"],
                overlap_size=o["overlap_size"],
                compatibility=OverlapCompatibility(o.get("compatibility", "unknown")),
                cech_checked=bool(o.get("cech_checked", False)),
            )
            for o in data.get("overlaps", [])
        )
        return cls(
            plan_id=data.get("plan_id", str(uuid.uuid4())),
            patches=patches,
            overlaps=overlaps,
            budget=budget,
            phase=CoverDesignPhase(data.get("phase", CoverDesignPhase.ALLOCATION.value)),
            dependency_order=tuple(data.get("dependency_order", [])),
            parallelism_groups=tuple(
                frozenset(g) for g in data.get("parallelism_groups", [])
            ),
            quality_threshold=float(data.get("quality_threshold", DEFAULT_QUALITY_THRESHOLD)),
            created_at=float(data.get("created_at", time.time())),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CoverDesignPlan(id={self.plan_id[:8]}…, "
            f"patches={len(self.patches)}, "
            f"overlaps={len(self.overlaps)}, "
            f"phase={self.phase.value})"
        )


# ---------------------------------------------------------------------------
# QualityMetric (frozen dataclass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QualityMetric:
    """An immutable record for a single quality measurement.

    Quality metrics are produced during the VERIFICATION phase.  Multiple
    metrics may be combined by :class:`CoverDesignResult` to produce an
    overall quality score.

    Theory2.tex §40.6 Definition 40.6.3 defines a quality metric as a
    weighted evidence function q : Cover → [0, 1] that measures how well
    the cover satisfies a particular criterion (e.g., overlap density,
    trust tier distribution, Čech violation count).

    Attributes:
        metric_id: Unique identifier for this measurement.
        name: Human-readable name (e.g., ``"cech_compliance"``,
            ``"budget_efficiency"``, ``"coverage_density"``).
        value: The measured value of the metric, in [0.0, 1.0].
        weight: Non-negative weight used when computing the weighted average
            in :meth:`CoverDesignResult.overall_quality`.
        threshold: Minimum acceptable value for this metric.  The metric
            ``passed`` field is derived from ``value >= threshold``.
        passed: ``True`` iff ``value >= threshold``.
    """

    metric_id: str
    name: str
    value: float
    weight: float = 1.0
    threshold: float = 0.0
    passed: bool = False

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def weighted_value(self) -> float:
        """Return ``value * weight``.

        Used when computing the overall cover quality score as a weighted
        average of all individual metrics.

        Returns:
            ``self.value * self.weight``
        """
        return self.value * self.weight

    def is_passing(self) -> bool:
        """Return ``True`` iff this metric's value meets its threshold.

        Note: This re-evaluates the threshold comparison rather than reading
        the stored ``passed`` field, so it always reflects the current state
        of ``value`` and ``threshold`` even if those were set inconsistently
        during construction.

        Returns:
            ``True`` if ``value >= threshold``.
        """
        return self.value >= self.threshold

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary for this metric.

        Returns:
            A dict with keys ``metric_id``, ``name``, ``value``, ``weight``,
            ``threshold``, and ``passed``.
        """
        return {
            "metric_id": self.metric_id,
            "name": self.name,
            "value": self.value,
            "weight": self.weight,
            "threshold": self.threshold,
            "passed": self.passed,
        }

    def __repr__(self) -> str:  # pragma: no cover
        status = "✓" if self.is_passing() else "✗"
        return f"QualityMetric({status} {self.name}={self.value:.3f} [threshold={self.threshold:.3f}])"


# ---------------------------------------------------------------------------
# CoverDesignResult (frozen dataclass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverDesignResult:
    """The immutable result of a completed cover design run.

    Produced at the end of the VERIFICATION phase and carries a complete
    audit trail: which patches succeeded, which failed, all quality metrics,
    and the total resources consumed.

    Attributes:
        result_id: Unique identifier for this result.
        plan: The :class:`CoverDesignPlan` that was executed to produce this
            result.
        completed_patches: Frozenset of patch IDs that were successfully
            processed and accepted.
        failed_patches: Frozenset of patch IDs that failed during execution
            or were rejected during verification.
        quality_metrics: Tuple of :class:`QualityMetric` objects captured
            during the verification phase.
        total_budget_used: Total budget consumed across all patches.
        wall_time_seconds: Total elapsed wall-clock time for the run.
    """

    result_id: str
    plan: CoverDesignPlan
    completed_patches: frozenset[str]
    failed_patches: frozenset[str]
    quality_metrics: tuple[QualityMetric, ...]
    total_budget_used: float
    wall_time_seconds: float

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def success_rate(self) -> float:
        """Return the fraction of planned patches that completed successfully.

        Returns:
            A float in [0.0, 1.0].  Returns 1.0 if no patches were planned
            (vacuously successful).
        """
        total = self.plan.total_patches()
        if total == 0:
            return 1.0
        return len(self.completed_patches) / total

    def overall_quality(self) -> float:
        """Return the weighted-average quality score across all metrics.

        Uses :meth:`QualityMetric.weighted_value` divided by the sum of
        weights.  Returns 0.0 if there are no metrics or the total weight
        is zero.

        Returns:
            A float in [0.0, 1.0] (assuming all individual metric values are
            in that range).
        """
        if not self.quality_metrics:
            return 0.0
        total_weight = sum(m.weight for m in self.quality_metrics)
        if total_weight <= 0.0:
            return 0.0
        return sum(m.weighted_value() for m in self.quality_metrics) / total_weight

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this result.

        Includes success rate, quality score, budget efficiency, and
        individual metric pass/fail statuses.

        Returns:
            A multi-line string suitable for logging or display.
        """
        violations = self.plan.validate_cech_conditions()
        lines = [
            f"CoverDesignResult '{self.result_id}'",
            f"  Plan           : {self.plan.plan_id}",
            f"  Phase          : {self.plan.phase.value}",
            f"  Patches        : {self.plan.total_patches()} planned, "
            f"{len(self.completed_patches)} completed, "
            f"{len(self.failed_patches)} failed",
            f"  Success rate   : {self.success_rate() * 100:.1f}%",
            f"  Quality score  : {self.overall_quality():.4f} "
            f"(threshold={self.plan.quality_threshold:.4f})",
            f"  Budget used    : {self.total_budget_used:.2f} "
            f"{self.plan.budget.unit} / {self.plan.budget.total:.2f} total",
            f"  Wall time      : {self.wall_time_seconds:.3f}s",
            f"  Čech violations: {len(violations)}",
        ]
        if self.quality_metrics:
            lines.append(f"  Metrics ({len(self.quality_metrics)}):")
            for m in self.quality_metrics:
                status = "PASS" if m.is_passing() else "FAIL"
                lines.append(f"    [{status}] {m.name}: {m.value:.4f} (w={m.weight:.2f})")
        return "\n".join(lines)

    def passed_quality_threshold(self) -> bool:
        """Return ``True`` iff the overall quality meets the plan's threshold.

        Returns:
            ``True`` if ``overall_quality() >= plan.quality_threshold``.
        """
        return self.overall_quality() >= self.plan.quality_threshold

    def serialise(self) -> dict[str, Any]:
        """Return a fully JSON-serialisable dictionary for this result.

        Returns:
            A dict encoding all result fields.
        """
        return {
            "result_id": self.result_id,
            "plan": self.plan.serialise(),
            "completed_patches": sorted(self.completed_patches),
            "failed_patches": sorted(self.failed_patches),
            "quality_metrics": [m.to_dict() for m in self.quality_metrics],
            "total_budget_used": self.total_budget_used,
            "wall_time_seconds": self.wall_time_seconds,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CoverDesignResult(id={self.result_id[:8]}…, "
            f"success={self.success_rate() * 100:.1f}%, "
            f"quality={self.overall_quality():.3f})"
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("cover_design.models — smoke test")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Budget
    # ------------------------------------------------------------------
    print("\n--- Budget ---")
    b = Budget.create(1000.0)
    b = b.allocate("patch-alpha", 300.0)
    b = b.allocate("patch-beta", 250.0)
    b = b.allocate("patch-gamma", 150.0)
    print(summarise_budget_allocation(b))
    print(f"fraction_used={b.fraction_used():.2%}, is_exhausted={b.is_exhausted()}")

    halves = b.split(2)
    print(f"split(2): {halves[0]}, {halves[1]}")

    merged = halves[0].merge(halves[1])
    print(f"merged remaining={merged.remaining:.2f}")

    try:
        b.allocate("patch-over", 10_000.0)
    except BudgetExhaustedError as exc:
        print(f"BudgetExhaustedError (expected): {exc}")

    # ------------------------------------------------------------------
    # 2. PatchDescriptor
    # ------------------------------------------------------------------
    print("\n--- PatchDescriptor ---")
    p_alpha = PatchDescriptor(
        patch_id="patch-alpha",
        coordinate="goal/1/body",
        context_size=512,
        overlap_ids=frozenset({"patch-beta"}),
        priority=2.5,
    )
    p_beta = PatchDescriptor(
        patch_id="patch-beta",
        coordinate="goal/1/conclusion",
        context_size=256,
        overlap_ids=frozenset({"patch-alpha", "patch-gamma"}),
        priority=1.8,
    )
    p_gamma = PatchDescriptor(
        patch_id="patch-gamma",
        coordinate="goal/2/body",
        context_size=384,
        overlap_ids=frozenset({"patch-beta"}),
        priority=1.0,
    )
    print(repr(p_alpha))
    print(f"p_alpha.overlaps_with(p_beta): {p_alpha.overlaps_with(p_beta)}")
    print(f"p_alpha.is_boundary_patch(): {p_alpha.is_boundary_patch()}")
    print(f"trust_tier (should be PROPOSAL): {p_alpha.trust_tier.value}")

    rt = PatchDescriptor.from_dict(p_alpha.to_dict())
    assert rt.patch_id == p_alpha.patch_id, "round-trip failed"
    print("PatchDescriptor round-trip: OK")

    # ------------------------------------------------------------------
    # 3. OverlapRecord and Čech condition check
    # ------------------------------------------------------------------
    print("\n--- OverlapRecord ---")
    ov_ab = OverlapRecord(patch_a="patch-alpha", patch_b="patch-beta", overlap_size=64)
    print(repr(ov_ab))
    print(f"key={ov_ab.key}, is_cech_compatible={ov_ab.is_cech_compatible()}")

    section_a = {"type": "lambda", "arity": 2, "body": "x + y"}
    section_b_ok = {"type": "lambda", "arity": 2, "body": "x + y", "extra": "foo"}
    section_b_bad = {"type": "lambda", "arity": 3}  # arity mismatch

    ov_ab_checked = ov_ab.check_cech_condition(section_a, section_b_ok)
    print(f"compatible check: {ov_ab_checked.compatibility.value}")

    ov_ab_violated = ov_ab.check_cech_condition(section_a, section_b_bad)
    print(f"incompatible check: {ov_ab_violated.compatibility.value}")
    print(format_cech_violation(ov_ab_violated, "arity differs"))

    ov_bg = OverlapRecord(patch_a="patch-beta", patch_b="patch-gamma", overlap_size=32)

    # ------------------------------------------------------------------
    # 4. Dependency graph helpers
    # ------------------------------------------------------------------
    print("\n--- Topological sort / parallelism groups ---")
    deps: dict[str, frozenset[str]] = {
        "patch-alpha": frozenset(),
        "patch-beta": frozenset({"patch-alpha"}),
        "patch-gamma": frozenset({"patch-alpha"}),
    }
    order = topological_sort(deps)
    print(f"dependency_order: {order}")
    groups = build_parallelism_groups(order, deps)
    print(f"parallelism_groups: {[sorted(g) for g in groups]}")

    # ------------------------------------------------------------------
    # 5. QualityMetric
    # ------------------------------------------------------------------
    print("\n--- QualityMetric ---")
    qm_cech = QualityMetric(
        metric_id="qm-cech",
        name="cech_compliance",
        value=1.0,
        weight=2.0,
        threshold=0.9,
        passed=True,
    )
    qm_budget = QualityMetric(
        metric_id="qm-budget",
        name="budget_efficiency",
        value=0.72,
        weight=1.0,
        threshold=0.5,
        passed=True,
    )
    qm_coverage = QualityMetric(
        metric_id="qm-coverage",
        name="coverage_density",
        value=0.45,
        weight=1.5,
        threshold=0.6,
        passed=False,
    )
    print(repr(qm_cech))
    print(repr(qm_budget))
    print(repr(qm_coverage))
    print(f"qm_cech.weighted_value()={qm_cech.weighted_value():.3f}")
    print(f"qm_coverage.is_passing()={qm_coverage.is_passing()}")

    # ------------------------------------------------------------------
    # 6. CoverDesignPlan
    # ------------------------------------------------------------------
    print("\n--- CoverDesignPlan ---")
    plan_budget = Budget.create(1000.0)
    plan_budget = plan_budget.allocate("patch-alpha", 300.0)
    plan_budget = plan_budget.allocate("patch-beta", 250.0)
    plan_budget = plan_budget.allocate("patch-gamma", 150.0)

    plan = CoverDesignPlan(
        plan_id=str(uuid.uuid4()),
        patches=(p_alpha, p_beta, p_gamma),
        overlaps=(ov_ab_checked, ov_bg),
        budget=plan_budget,
        phase=CoverDesignPhase.VERIFICATION,
        dependency_order=tuple(order),
        parallelism_groups=tuple(groups),
        quality_threshold=DEFAULT_QUALITY_THRESHOLD,
        created_at=time.time(),
    )
    print(repr(plan))
    print(f"total_patches={plan.total_patches()}, total_overlaps={plan.total_overlaps()}")
    by_prio = plan.patches_by_priority()
    print(f"patches_by_priority: {[p.patch_id for p in by_prio]}")
    print(f"get_patch('patch-beta'): {plan.get_patch('patch-beta').coordinate}")
    print(f"get_overlaps_for('patch-beta'): {len(plan.get_overlaps_for('patch-beta'))} overlaps")

    violations = plan.validate_cech_conditions()
    print(f"Čech violations: {len(violations)}")

    serialised = plan.serialise()
    plan_rt = CoverDesignPlan.from_dict(serialised)
    assert plan_rt.plan_id == plan.plan_id, "plan round-trip failed"
    print("CoverDesignPlan round-trip: OK")
    print(f"serialised keys: {sorted(serialised.keys())}")

    # ------------------------------------------------------------------
    # 7. CoverDesignResult
    # ------------------------------------------------------------------
    print("\n--- CoverDesignResult ---")
    result = CoverDesignResult(
        result_id=str(uuid.uuid4()),
        plan=plan,
        completed_patches=frozenset({"patch-alpha", "patch-beta"}),
        failed_patches=frozenset({"patch-gamma"}),
        quality_metrics=(qm_cech, qm_budget, qm_coverage),
        total_budget_used=520.0,
        wall_time_seconds=14.7,
    )
    print(repr(result))
    print(f"success_rate={result.success_rate():.2%}")
    print(f"overall_quality={result.overall_quality():.4f}")
    print(f"passed_quality_threshold={result.passed_quality_threshold()}")
    print(result.summary())

    result_dict = result.serialise()
    print(f"\nserialised result keys: {sorted(result_dict.keys())}")
    print(f"JSON-serialisable: {bool(json.dumps(result_dict))}")

    # ------------------------------------------------------------------
    # 8. Error conditions
    # ------------------------------------------------------------------
    print("\n--- Error conditions ---")
    try:
        topological_sort({"a": frozenset({"b"}), "b": frozenset({"a"})})
    except CoverDesignError as exc:
        print(f"CoverDesignError cycle (expected): {exc}")

    try:
        validate_patch_id("   ")
    except ValueError as exc:
        print(f"ValueError bad patch_id (expected): {exc}")

    try:
        plan.get_patch("nonexistent-patch")
    except KeyError as exc:
        print(f"KeyError missing patch (expected): {exc}")

    print("\nSmoke test complete — all assertions passed.")
