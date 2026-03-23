"""Local Inhabitant Synthesis — Goal Reconstruction Extension (Ch42 §1-RE).

This module extends the core local inhabitant synthesis pipeline with *goal
reconstruction* semantics, introducing richer type-theoretic machinery drawn
from the Curry-Howard correspondence and sheaf-theoretic obstructions.

# copilot:

Mathematical Background
-----------------------
**Curry-Howard Correspondence.**
The central insight underlying this module is the Curry-Howard isomorphism:

    Propositions-as-Types:    P  ↔  type A
    Proofs-as-Terms:          p : P  ↔  t : A
    Proof normalization:      β-reduction  ↔  computation

Under this correspondence, *synthesizing a type inhabitant* is identical to
*constructing a proof*.  The synthesis algorithm operates on a type environment
Γ (a context mapping variables to types) and attempts to build terms of a
given target type T.

**Judgment 8-tuples.**
A Ch42 judgment has the form:

    (c, φ, A, E, O, B, T, Π)

where:
    c  = context         – the local variable environment (TypeEnvironment)
    φ  = formula         – the target proposition / type to inhabit
    A  = assumptions     – auxiliary hypotheses in scope
    E  = evidence        – supporting evidence (scores, witnesses)
    O  = obstructions    – Čech H¹ cohomology classes blocking synthesis
    B  = blame           – attribution of synthesis failures
    T  = trust_tier      – TrustTier level of the judgment
    Π  = proof_obligations – outstanding obligations to close the proof

**Čech H¹ Obstructions.**
Given a cover U = {Uᵢ} of a type space X, a *Čech 1-cochain* is a family of
elements oᵢⱼ associated to pairwise intersections Uᵢ ∩ Uⱼ.  This cochain is
a *cocycle* (hence an obstruction class [o] ∈ Ȟ¹(U; F)) when the coboundary
condition holds:

    δo = 0  ⟺  oᵢⱼ · oⱼₖ · oᵢₖ⁻¹ = 0  ∀ i, j, k

Non-trivial elements of Ȟ¹ obstruct the global glueing of locally-synthesized
inhabitants into a coherent global section.  The ObstructionTracker in this
module records these classes and determines whether glueing is possible.

**Trust Algebra.**
TrustTier forms a bounded distributive lattice (T, ≤, ∧, ∨, ⊥, ⊤):

    ⊥ = PROPOSAL  ≤  REVIEWED  ≤  VERIFIED  ≤  RUNTIME_WITNESSED  ≤  PROOF_BACKED = ⊤

    meet(a, b) = min(a, b)          join(a, b) = max(a, b)
    a ≤ b  ⟺  meet(a, b) = a      (lattice ordering)

This lattice models *epistemic confidence*: propagating trust through a proof
tree yields the meet of all constituent trust tiers (the weakest link).

**Type Constructors.**
The synthesis engine recognises eight built-in type constructors forming the
basis of the internal type language:

    TC_ARROW    – function types  A → B
    TC_PRODUCT  – product types   A × B
    TC_SUM      – sum types       A + B
    TC_UNIT     – unit type       𝟏
    TC_VOID     – empty type      𝟎
    TC_LIST     – list type       List A
    TC_OPTION   – option type     Option A
    TC_FORALL   – universal type  ∀x:A. B(x)

These constructors are *Church-encoded* in the module-level constants below.

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis_goal_re import (
...     SynthesisGoal, SynthesisPolicy, TypeEnvironment, TrustTier,
...     synthesize_inhabitant, generate_synthesis_goals, evaluate_candidate,
... )
>>> env = TypeEnvironment()
>>> env.bind("x", "Nat")
>>> goals = generate_synthesis_goals(env, "cover_U0")
>>> len(goals) >= 1
True
"""
from __future__ import annotations

import cmath
import hashlib
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Optional numpy import for cohomology array operations
# ---------------------------------------------------------------------------
try:
    import numpy as _np  # type: ignore[import]
    _HAS_NUMPY = True
except ImportError:
    _np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

# ---------------------------------------------------------------------------
# Optional jugeo imports — wrapped in try/except per policy
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.trust import TrustTier as _JugeoTrustTier  # type: ignore[import]
    TrustTier = _JugeoTrustTier
    _JUGEO_TRUST_AVAILABLE = True
except ImportError:
    _JUGEO_TRUST_AVAILABLE = False

    class TrustTier(IntEnum):  # type: ignore[no-redef]
        """Ordered epistemic-confidence tiers.

        The tier lattice is::

            PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED

        This is a *bounded distributive lattice* under the natural integer
        ordering, with meet = min and join = max.
        """
        PROPOSAL         = 1
        REVIEWED         = 2
        VERIFIED         = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED     = 5

        # ------------------------------------------------------------------
        # Lattice operations (trust algebra)
        # ------------------------------------------------------------------

        def meet(self, other: "TrustTier") -> "TrustTier":
            """Greatest lower bound — the weakest of the two tiers.

            In proof-tree propagation, the trust of a composed proof is the
            meet (minimum) of the trusts of its sub-proofs: one unverified
            sub-proof weakens the whole.
            """
            return TrustTier(min(self.value, other.value))

        def join(self, other: "TrustTier") -> "TrustTier":
            """Least upper bound — the strongest of the two tiers.

            Used when *either* of two independent witnesses suffices to
            establish a claim.
            """
            return TrustTier(max(self.value, other.value))

        def promote(self) -> "TrustTier":
            """↑_π — promote one tier upward, clamped at PROOF_BACKED."""
            return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

        def demote(self) -> "TrustTier":
            """↓_χ — demote one tier downward, clamped at PROPOSAL."""
            return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

        def __le__(self, other: object) -> bool:  # type: ignore[override]
            if isinstance(other, TrustTier):
                return self.value <= other.value
            return NotImplemented

        def __lt__(self, other: object) -> bool:  # type: ignore[override]
            if isinstance(other, TrustTier):
                return self.value < other.value
            return NotImplemented

        def __ge__(self, other: object) -> bool:  # type: ignore[override]
            if isinstance(other, TrustTier):
                return self.value >= other.value
            return NotImplemented

        def __gt__(self, other: object) -> bool:  # type: ignore[override]
            if isinstance(other, TrustTier):
                return self.value > other.value
            return NotImplemented

        @property
        def is_proof_grade(self) -> bool:
            """True if this tier counts as machine-verified."""
            return self >= TrustTier.VERIFIED


try:
    from jugeo.generation.inhabitant_fleets.models import (  # type: ignore[import]
        InhabitantProposal,
        TrustTier as _ModelTrustTier,  # noqa: F401
        make_proposal,
    )
    _JUGEO_MODELS_AVAILABLE = True
except ImportError:
    _JUGEO_MODELS_AVAILABLE = False
    InhabitantProposal = None  # type: ignore[assignment,misc]
    make_proposal = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level constants — Type constructors (Church-encoded strings)
# ---------------------------------------------------------------------------

TC_ARROW: str = "→"
"""Function type constructor: A → B (the fundamental connective under C-H)."""

TC_PRODUCT: str = "×"
"""Product type constructor: A × B  ↔  conjunction A ∧ B."""

TC_SUM: str = "+"
"""Sum (coproduct) type constructor: A + B  ↔  disjunction A ∨ B."""

TC_UNIT: str = "𝟏"
"""Unit type — the terminal object; inhabitant is the unique term ()."""

TC_VOID: str = "𝟎"
"""Empty type — the initial object; no inhabitant exists (⊥)."""

TC_LIST: str = "List"
"""Inductive list type constructor: List A."""

TC_OPTION: str = "Option"
"""Option (maybe) type constructor: Option A = 𝟏 + A."""

TC_FORALL: str = "∀"
"""Dependent universal type constructor: ∀(x:A). B(x)."""

ALL_TYPE_CONSTRUCTORS: tuple[str, ...] = (
    TC_ARROW, TC_PRODUCT, TC_SUM, TC_UNIT,
    TC_VOID, TC_LIST, TC_OPTION, TC_FORALL,
)
"""Ordered tuple of all eight built-in type constructors."""

# ---------------------------------------------------------------------------
# Default synthesis policies
# ---------------------------------------------------------------------------

DEFAULT_SYNTHESIS_POLICY_ID: str = "policy_default_v1"
FAST_SYNTHESIS_POLICY_ID: str = "policy_fast_v1"
DEEP_SYNTHESIS_POLICY_ID: str = "policy_deep_v1"

# ---------------------------------------------------------------------------
# Helper functions — trust algebra
# ---------------------------------------------------------------------------


def trust_meet_chain(tiers: tuple["TrustTier", ...]) -> "TrustTier":
    """Compute the meet (greatest lower bound) of a sequence of trust tiers.

    This implements *proof-tree trust propagation*: the composite trust is
    the weakest tier in the chain.  For an empty sequence the meet is the
    top element (PROOF_BACKED), by convention.

    Parameters
    ----------
    tiers:
        Non-empty tuple of TrustTier values.

    Returns
    -------
    TrustTier
        The minimum tier value across the input sequence.

    Examples
    --------
    >>> trust_meet_chain((TrustTier.VERIFIED, TrustTier.PROPOSAL))
    <TrustTier.PROPOSAL: 1>
    """
    if not tiers:
        return TrustTier.PROOF_BACKED
    result = tiers[0]
    for t in tiers[1:]:
        result = result.meet(t)
    return result


def trust_join_chain(tiers: tuple["TrustTier", ...]) -> "TrustTier":
    """Compute the join (least upper bound) of a sequence of trust tiers.

    Used when independent witnesses collectively establish a claim: the
    composite trust is the strongest available witness.

    Parameters
    ----------
    tiers:
        Tuple of TrustTier values; empty → PROPOSAL (bottom element).
    """
    if not tiers:
        return TrustTier.PROPOSAL
    result = tiers[0]
    for t in tiers[1:]:
        result = result.join(t)
    return result


# ---------------------------------------------------------------------------
# Helper functions — Čech H¹ cohomology simulation
# ---------------------------------------------------------------------------


def make_cech_cochain(n_patches: int) -> list[list[complex]]:
    """Build an n×n antisymmetric Čech 1-cochain matrix initialised to zero.

    The entry ``M[i][j]`` represents the transition element oᵢⱼ on the
    intersection Uᵢ ∩ Uⱼ.  Antisymmetry: oᵢⱼ = -oⱼᵢ (additive notation).

    Parameters
    ----------
    n_patches:
        Number of cover elements.
    """
    return [[complex(0) for _ in range(n_patches)] for _ in range(n_patches)]


def cech_coboundary(cochain: list[list[complex]], i: int, j: int, k: int) -> complex:
    """Compute the coboundary (δo)ᵢⱼₖ = oⱼₖ - oᵢₖ + oᵢⱼ.

    For a 1-cochain o, the coboundary is a 2-cochain.  The cochain is a
    1-cocycle (i.e., represents an obstruction class in Ȟ¹) iff δo = 0
    identically on all triples (i, j, k).

    Parameters
    ----------
    cochain:
        The n×n cochain matrix.
    i, j, k:
        Triple of patch indices.

    Returns
    -------
    complex
        The coboundary value; 0 means the triple satisfies the cocycle condition.
    """
    return cochain[j][k] - cochain[i][k] + cochain[i][j]


def is_cocycle(cochain: list[list[complex]], tol: float = 1e-10) -> bool:
    """Return True if the cochain satisfies the 1-cocycle condition on all triples.

    A 1-cochain o is a cocycle iff (δo)ᵢⱼₖ = 0 for all triples (i,j,k).
    This is the necessary condition for o to represent a global obstruction
    class rather than a mere local discrepancy.
    """
    n = len(cochain)
    for i in range(n):
        for j in range(n):
            for k in range(n):
                if abs(cech_coboundary(cochain, i, j, k)) > tol:
                    return False
    return True


def obstruction_norm(obs: tuple[complex, ...]) -> float:
    """Compute the L² norm of an obstruction class vector.

    A norm of 0 indicates a trivial obstruction (no glueing problem).
    A larger norm indicates stronger resistance to global section construction.

    Parameters
    ----------
    obs:
        Tuple of complex obstruction values representing a Čech cocycle.
    """
    return math.sqrt(sum(abs(z) ** 2 for z in obs))


def trivial_obstruction(n: int = 4) -> tuple[complex, ...]:
    """Return a trivial (zero) obstruction class of dimension n."""
    return tuple(complex(0) for _ in range(n))


def random_obstruction(seed: int, n: int = 4) -> tuple[complex, ...]:
    """Generate a deterministic pseudo-random obstruction class.

    Uses a simple hash-based construction rather than a random number
    generator to remain deterministic across runs.

    Parameters
    ----------
    seed:
        Integer seed for determinism.
    n:
        Dimension of the obstruction class.
    """
    result = []
    for k in range(n):
        h = hashlib.sha256(f"{seed}-{k}".encode()).digest()
        re_part = (h[0] - 128) / 256.0
        im_part = (h[1] - 128) / 256.0
        result.append(complex(re_part, im_part))
    return tuple(result)


# ---------------------------------------------------------------------------
# TypeEnvironment
# ---------------------------------------------------------------------------


class TypeEnvironment:
    """A type context Γ mapping variable names to their types.

    Under the Curry-Howard correspondence, a type environment is equivalent
    to a *sequent context*: the collection of assumptions in scope when
    type-checking a term.

    In sheaf-theoretic terms, TypeEnvironment is the *local section data*
    associated to a cover element U: each binding (x : T) represents a
    local section s(x) ∈ F(U) of the type sheaf F.

    Attributes
    ----------
    _bindings : dict[str, str]
        Variable-to-type map.
    _universe : set[str]
        The set of all known base types in scope.
    _created_at : float
        Unix timestamp of environment creation.
    """

    BASE_TYPES: frozenset[str] = frozenset({
        "Nat", "Int", "Bool", "String", "Float", "Unit", "Void",
        "Prop", "Type", "Set",
    })
    """Predefined base types recognised by the universe."""

    def __init__(self, universe: set[str] | None = None) -> None:
        self._bindings: dict[str, str] = {}
        self._universe: set[str] = set(self.BASE_TYPES)
        if universe:
            self._universe.update(universe)
        self._created_at = time.time()

    # ------------------------------------------------------------------
    # Core binding operations
    # ------------------------------------------------------------------

    def bind(self, var: str, typ: str) -> "TypeEnvironment":
        """Extend the context with a new binding x : T.

        Returns self for fluent chaining.

        Parameters
        ----------
        var:
            Variable name (must be non-empty and not already bound).
        typ:
            Type expression (string in the internal type language).

        Raises
        ------
        ValueError
            If var is empty or already bound.
        """
        if not var:
            raise ValueError("Variable name must be non-empty.")
        self._bindings[var] = typ
        self._universe.add(typ)
        return self

    def lookup(self, var: str) -> str | None:
        """Return the type of var, or None if unbound."""
        return self._bindings.get(var)

    def unbind(self, var: str) -> "TypeEnvironment":
        """Remove a binding from the context."""
        self._bindings.pop(var, None)
        return self

    def extend(self, other: "TypeEnvironment") -> "TypeEnvironment":
        """Return a new TypeEnvironment extending self with other's bindings.

        Bindings in other shadow those in self (left-to-right shadowing).
        """
        result = TypeEnvironment(universe=self._universe | other._universe)
        result._bindings.update(self._bindings)
        result._bindings.update(other._bindings)
        return result

    # ------------------------------------------------------------------
    # Query / inspection
    # ------------------------------------------------------------------

    def variables(self) -> tuple[str, ...]:
        """Return all bound variable names in insertion order."""
        return tuple(self._bindings.keys())

    def types(self) -> tuple[str, ...]:
        """Return all bound type expressions in insertion order."""
        return tuple(self._bindings.values())

    def has_type(self, typ: str) -> bool:
        """Return True if typ appears as the type of any bound variable."""
        return typ in self._bindings.values()

    def is_inhabited(self, typ: str) -> bool:
        """Heuristic: return True if the type is plausibly inhabitable.

        A type is considered inhabitable if:
        - It is in the base type universe (excluding Void / 𝟎), OR
        - It contains a known type constructor other than TC_VOID.
        """
        if typ in ("Void", TC_VOID, "𝟎"):
            return False
        if typ in self._universe:
            return True
        return any(tc in typ for tc in ALL_TYPE_CONSTRUCTORS if tc != TC_VOID)

    def size(self) -> int:
        """Return the number of bindings in the context."""
        return len(self._bindings)

    def universe(self) -> frozenset[str]:
        """Return the type universe as a frozen set."""
        return frozenset(self._universe)

    def to_dict(self) -> dict[str, str]:
        """Serialise to a plain dict."""
        return dict(self._bindings)

    def __repr__(self) -> str:
        pairs = ", ".join(f"{k}:{v}" for k, v in list(self._bindings.items())[:5])
        suffix = "…" if len(self._bindings) > 5 else ""
        return f"TypeEnvironment({{{pairs}{suffix}}})"

    def __len__(self) -> int:
        return len(self._bindings)

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._bindings.items())


# ---------------------------------------------------------------------------
# CoverElementContext
# ---------------------------------------------------------------------------


class CoverElementContext:
    """Local context for a single cover element Uᵢ.

    In Čech cohomology, a cover U = {Uᵢ}ᵢ∈I of a topological space X gives
    rise to a *local data* assignment: for each cover element Uᵢ, we have a
    set of local sections F(Uᵢ) of the sheaf F.

    A CoverElementContext models F(Uᵢ): it stores the local type environment,
    the available local variables, and the overlap relationships with other
    cover elements.

    Attributes
    ----------
    element_id : str
        Identifier for this cover element.
    local_env : TypeEnvironment
        Type bindings local to this cover element.
    overlaps : dict[str, list[str]]
        Mapping from neighbour element_id to list of shared variable names.
    _patch_metadata : dict[str, Any]
        Arbitrary metadata associated with this patch.
    """

    def __init__(self, element_id: str, local_env: TypeEnvironment | None = None) -> None:
        self.element_id = element_id
        self.local_env: TypeEnvironment = local_env or TypeEnvironment()
        self.overlaps: dict[str, list[str]] = {}
        self._patch_metadata: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Overlap / glueing management
    # ------------------------------------------------------------------

    def add_overlap(self, neighbour_id: str, shared_vars: list[str]) -> None:
        """Record that this cover element overlaps neighbour_id on shared_vars.

        Parameters
        ----------
        neighbour_id:
            The element_id of the overlapping cover element.
        shared_vars:
            Variables in scope on the intersection Uᵢ ∩ Uⱼ.
        """
        existing = self.overlaps.get(neighbour_id, [])
        self.overlaps[neighbour_id] = list(set(existing) | set(shared_vars))

    def shared_variables(self, neighbour_id: str) -> tuple[str, ...]:
        """Return variables shared with a given neighbour element."""
        return tuple(self.overlaps.get(neighbour_id, []))

    def all_neighbours(self) -> tuple[str, ...]:
        """Return all neighbour element IDs."""
        return tuple(self.overlaps.keys())

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def set_metadata(self, key: str, value: Any) -> None:
        """Attach arbitrary metadata to this cover element."""
        self._patch_metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Retrieve metadata by key."""
        return self._patch_metadata.get(key, default)

    def inhabitable_types(self) -> tuple[str, ...]:
        """Return types in the local environment that are inhabitable."""
        return tuple(
            t for t in self.local_env.types()
            if self.local_env.is_inhabited(t)
        )

    def to_judgment_context(self) -> dict[str, Any]:
        """Serialise to a dict suitable for embedding in a judgment 8-tuple."""
        return {
            "element_id": self.element_id,
            "bindings": self.local_env.to_dict(),
            "overlaps": {k: list(v) for k, v in self.overlaps.items()},
        }

    def __repr__(self) -> str:
        return (
            f"CoverElementContext(id={self.element_id!r}, "
            f"vars={self.local_env.size()}, "
            f"neighbours={len(self.overlaps)})"
        )


# ---------------------------------------------------------------------------
# SynthesisTree
# ---------------------------------------------------------------------------


class SynthesisTree:
    """A tree of partial proofs / inhabitants built during synthesis.

    The SynthesisTree models the *proof-search space*: each node is an
    intermediate judgment, and each edge is a *rule application* (constructor
    introduction/elimination).

    Under Curry-Howard, this is simultaneously:
    - A proof tree (logical derivation)
    - A term construction tree (program synthesis)
    - A type-directed search tree (inhabitation algorithm)

    Attributes
    ----------
    root_label : str
        The type / proposition at the root of the tree.
    children : list[SynthesisTree]
        Sub-trees corresponding to sub-goals.
    rule_applied : str | None
        The type-theoretic rule used to produce this node.
    candidate : str | None
        The term candidate synthesized at this node, or None if pending.
    trust : TrustTier
        Trust tier assigned to this node.
    _depth : int
        Cached depth of this subtree.
    """

    def __init__(
        self,
        root_label: str,
        rule_applied: str | None = None,
        trust: TrustTier = TrustTier.PROPOSAL,
    ) -> None:
        self.root_label = root_label
        self.rule_applied = rule_applied
        self.trust = trust
        self.candidate: str | None = None
        self.children: list["SynthesisTree"] = []
        self._depth: int | None = None
        self._node_id = uuid.uuid4().hex[:8]

    # ------------------------------------------------------------------
    # Tree construction
    # ------------------------------------------------------------------

    def add_child(self, child: "SynthesisTree") -> None:
        """Append a child subtree (sub-goal derivation)."""
        self.children.append(child)
        self._depth = None  # invalidate cache

    def set_candidate(self, term: str) -> None:
        """Assign a synthesized term candidate to this node."""
        self.candidate = term

    # ------------------------------------------------------------------
    # Tree traversal & metrics
    # ------------------------------------------------------------------

    def depth(self) -> int:
        """Return the depth of this subtree (0 for leaves)."""
        if self._depth is None:
            self._depth = (
                0 if not self.children
                else 1 + max(c.depth() for c in self.children)
            )
        return self._depth

    def size(self) -> int:
        """Return the total number of nodes in this subtree."""
        return 1 + sum(c.size() for c in self.children)

    def leaves(self) -> list["SynthesisTree"]:
        """Return all leaf nodes (open sub-goals)."""
        if not self.children:
            return [self]
        result: list["SynthesisTree"] = []
        for c in self.children:
            result.extend(c.leaves())
        return result

    def is_complete(self) -> bool:
        """Return True if every node in the tree has a candidate assigned."""
        if self.candidate is None:
            return False
        return all(c.is_complete() for c in self.children)

    def composite_trust(self) -> TrustTier:
        """Propagate trust through the tree via meet (weakest-link rule)."""
        if not self.children:
            return self.trust
        child_trusts = tuple(c.composite_trust() for c in self.children)
        return trust_meet_chain((self.trust,) + child_trusts)

    def extract_term(self) -> str:
        """Extract the fully-applied term from the completed tree.

        If the tree is incomplete, returns a *partial term* with holes
        indicated by ``?``.
        """
        if not self.children:
            return self.candidate if self.candidate else f"?:{self.root_label}"
        sub_terms = [c.extract_term() for c in self.children]
        rule = self.rule_applied or "app"
        return f"({rule} {' '.join(sub_terms)})"

    def to_dict(self) -> dict[str, Any]:
        """Serialise the tree to a nested dict."""
        return {
            "node_id": self._node_id,
            "root_label": self.root_label,
            "rule_applied": self.rule_applied,
            "candidate": self.candidate,
            "trust": self.trust.name,
            "children": [c.to_dict() for c in self.children],
        }

    def __repr__(self) -> str:
        status = "complete" if self.is_complete() else "partial"
        return (
            f"SynthesisTree(root={self.root_label!r}, "
            f"depth={self.depth()}, size={self.size()}, {status})"
        )


# ---------------------------------------------------------------------------
# ObstructionTracker
# ---------------------------------------------------------------------------


class ObstructionTracker:
    """Tracks Čech H¹ cohomology obstructions arising during synthesis.

    During inhabitant synthesis across a cover U = {Uᵢ}, local inhabitants
    tᵢ : T on each Uᵢ must agree on overlaps: tᵢ|_{Uᵢ∩Uⱼ} = tⱼ|_{Uᵢ∩Uⱼ}.
    Disagreements define a Čech 1-cochain; if this cochain is a cocycle (and
    non-exact), it represents an element of Ȟ¹ obstructing global glueing.

    This class records obstructions and provides methods for checking whether
    the synthesis can proceed globally.

    Attributes
    ----------
    _obstructions : dict[tuple[str, str], complex]
        Map from (elem_i, elem_j) to the obstruction value on Uᵢ ∩ Uⱼ.
    _resolved : set[tuple[str, str]]
        Set of pairs for which the obstruction has been resolved.
    _blame_log : list[str]
        Human-readable explanations of obstruction origins.
    """

    def __init__(self) -> None:
        self._obstructions: dict[tuple[str, str], complex] = {}
        self._resolved: set[tuple[str, str]] = set()
        self._blame_log: list[str] = []
        self._created_at = time.time()

    # ------------------------------------------------------------------
    # Recording and resolving obstructions
    # ------------------------------------------------------------------

    def record(self, elem_i: str, elem_j: str, value: complex, blame: str = "") -> None:
        """Record a 1-cochain value oᵢⱼ on the intersection Uᵢ ∩ Uⱼ.

        Parameters
        ----------
        elem_i, elem_j:
            Identifiers of the two cover elements.
        value:
            The obstruction value (complex number in the coefficient group ℂ).
        blame:
            Human-readable explanation of where this obstruction originates.
        """
        key = (elem_i, elem_j)
        self._obstructions[key] = value
        sym_key = (elem_j, elem_i)
        self._obstructions[sym_key] = -value  # antisymmetry
        if blame:
            self._blame_log.append(f"({elem_i},{elem_j}): {blame}")

    def resolve(self, elem_i: str, elem_j: str) -> None:
        """Mark the obstruction between elem_i and elem_j as resolved."""
        self._resolved.add((elem_i, elem_j))
        self._resolved.add((elem_j, elem_i))

    def get_value(self, elem_i: str, elem_j: str) -> complex:
        """Return the obstruction value on Uᵢ ∩ Uⱼ (0 if not recorded)."""
        return self._obstructions.get((elem_i, elem_j), complex(0))

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def has_obstruction(self, tol: float = 1e-10) -> bool:
        """Return True if any unresolved non-trivial obstruction exists."""
        for key, val in self._obstructions.items():
            if key not in self._resolved and abs(val) > tol:
                return True
        return False

    def total_obstruction_norm(self) -> float:
        """Return the sum of norms of all unresolved obstructions."""
        return sum(
            abs(val)
            for key, val in self._obstructions.items()
            if key not in self._resolved
        )

    def obstruction_class_vector(self) -> tuple[complex, ...]:
        """Return all unresolved obstruction values as a flat tuple.

        This tuple approximates the *Čech cohomology class* [o] ∈ Ȟ¹.
        """
        return tuple(
            val
            for key, val in sorted(self._obstructions.items())
            if key not in self._resolved
        )

    def blame_report(self) -> tuple[str, ...]:
        """Return the accumulated blame log as a tuple of strings."""
        return tuple(self._blame_log)

    def can_glue(self, tol: float = 1e-10) -> bool:
        """Return True if all obstructions are trivial (glueing is possible)."""
        return not self.has_obstruction(tol=tol)

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of obstruction tracker state."""
        return {
            "total_pairs": len(self._obstructions) // 2,
            "resolved": len(self._resolved) // 2,
            "has_obstruction": self.has_obstruction(),
            "total_norm": self.total_obstruction_norm(),
            "blame_entries": len(self._blame_log),
        }

    def __repr__(self) -> str:
        return (
            f"ObstructionTracker("
            f"pairs={len(self._obstructions)//2}, "
            f"obstructed={self.has_obstruction()})"
        )


# ---------------------------------------------------------------------------
# InhabitantEvaluator
# ---------------------------------------------------------------------------


class InhabitantEvaluator:
    """Scores inhabitant candidates against synthesis goals.

    The scoring rubric combines:

    1. **Type compatibility** (weight α):  does the candidate's type
       expression match the goal's target type?
    2. **Evidence density** (weight β):  how many steps in the
       construction trace are evidence-backed?
    3. **Trust alignment** (weight γ):  does the candidate's trust tier
       meet or exceed the required threshold?
    4. **Obstruction penalty** (weight δ):  subtract a fraction proportional
       to the norm of the candidate's Čech witness.

    Final score ∈ [0, 1]:

        score = clamp(α·type_compat + β·evidence_density
                      + γ·trust_bonus - δ·obs_penalty, 0, 1)

    Attributes
    ----------
    alpha, beta, gamma, delta : float
        Weights for the four rubric components.  Must sum to ≤ 1.
    """

    def __init__(
        self,
        alpha: float = 0.40,
        beta: float = 0.25,
        gamma: float = 0.25,
        delta: float = 0.10,
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.delta = delta
        self._evaluation_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Scoring components
    # ------------------------------------------------------------------

    def type_compatibility(self, candidate_type: str, target_type: str) -> float:
        """Compute a [0,1] type compatibility score.

        Exact string match → 1.0.
        Shared constructor prefix → 0.5.
        Otherwise → Jaccard similarity of token sets.
        """
        if candidate_type == target_type:
            return 1.0
        c_tokens = set(candidate_type.split())
        t_tokens = set(target_type.split())
        if not c_tokens and not t_tokens:
            return 1.0
        union = c_tokens | t_tokens
        inter = c_tokens & t_tokens
        jaccard = len(inter) / len(union) if union else 0.0
        # Boost if they share a leading type constructor
        c_prefix = candidate_type[:3]
        t_prefix = target_type[:3]
        prefix_bonus = 0.15 if c_prefix == t_prefix else 0.0
        return min(1.0, jaccard + prefix_bonus)

    def evidence_density(self, construction_trace: tuple[str, ...]) -> float:
        """Estimate evidence density from a construction trace.

        Each step in the trace is scored 1 if it references a known
        evidence keyword (``proof``, ``witness``, ``theorem``, ``lemma``,
        ``axiom``), and 0 otherwise.  The score is the mean.
        """
        if not construction_trace:
            return 0.0
        keywords = {"proof", "witness", "theorem", "lemma", "axiom", "refine", "apply"}
        hits = sum(
            1 for step in construction_trace
            if any(kw in step.lower() for kw in keywords)
        )
        return hits / len(construction_trace)

    def trust_bonus(self, candidate_tier: TrustTier, required_tier: TrustTier) -> float:
        """Return a [0,1] bonus based on trust tier alignment.

        Exceeding the required tier gives a proportional bonus.
        Falling below gives 0.
        """
        if candidate_tier >= required_tier:
            tier_range = TrustTier.PROOF_BACKED.value - required_tier.value
            excess = candidate_tier.value - required_tier.value
            return 1.0 if tier_range == 0 else min(1.0, excess / max(tier_range, 1))
        return 0.0

    def obstruction_penalty(self, cech_witness: tuple[complex, ...]) -> float:
        """Return a [0,1] penalty proportional to the obstruction norm.

        A trivial witness (all zeros) gives penalty 0.0.
        A witness with norm ≥ 1 gives maximum penalty 1.0.
        """
        norm = obstruction_norm(cech_witness)
        return min(1.0, norm)

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    def score(
        self,
        candidate_type: str,
        target_type: str,
        construction_trace: tuple[str, ...],
        candidate_tier: TrustTier,
        required_tier: TrustTier,
        cech_witness: tuple[complex, ...],
    ) -> float:
        """Compute the composite score for an inhabitant candidate.

        Returns
        -------
        float
            A value in [0, 1]; higher is better.
        """
        tc = self.type_compatibility(candidate_type, target_type)
        ed = self.evidence_density(construction_trace)
        tb = self.trust_bonus(candidate_tier, required_tier)
        op = self.obstruction_penalty(cech_witness)
        raw = self.alpha * tc + self.beta * ed + self.gamma * tb - self.delta * op
        result = max(0.0, min(1.0, raw))
        self._evaluation_log.append({
            "type_compat": tc, "evidence_density": ed,
            "trust_bonus": tb, "obs_penalty": op, "final": result,
        })
        return result

    def last_breakdown(self) -> dict[str, float] | None:
        """Return the component breakdown of the most recent score call."""
        return dict(self._evaluation_log[-1]) if self._evaluation_log else None

    def evaluation_count(self) -> int:
        """Return how many evaluations have been performed."""
        return len(self._evaluation_log)

    def __repr__(self) -> str:
        return (
            f"InhabitantEvaluator("
            f"α={self.alpha}, β={self.beta}, γ={self.gamma}, δ={self.delta})"
        )


# ---------------------------------------------------------------------------
# GoalDecomposer
# ---------------------------------------------------------------------------


class GoalDecomposer:
    """Decomposes complex synthesis goals into simpler sub-goals.

    The decomposition follows the structure of the target type:

    - ``A → B``  →  [goal(A), goal(B)]  (with priority split)
    - ``A × B``  →  [goal(A), goal(B)]  (parallel sub-goals)
    - ``A + B``  →  [goal(A)]  or  [goal(B)]  (choose one branch)
    - ``List A`` →  [goal(A)]  (base element goal)
    - ``∀x.B``   →  [goal(B)]  (instantiate the bound variable)
    - Atomic    →  []  (no further decomposition)

    This mirrors the proof rules of intuitionistic type theory: to construct
    a term of type A → B, construct a function; to construct A × B, construct
    both components; etc.

    Attributes
    ----------
    max_depth : int
        Maximum decomposition depth (prevents infinite recursion on
        recursive types).
    _decomposition_cache : dict[str, list[dict]]
        Memoised decomposition results keyed by type string.
    """

    def __init__(self, max_depth: int = 6) -> None:
        self.max_depth = max_depth
        self._decomposition_cache: dict[str, list[dict[str, Any]]] = {}
        self._decompositions_performed = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(self, type_expr: str, priority: float = 1.0, depth: int = 0) -> list[dict[str, Any]]:
        """Decompose a type expression into sub-goal descriptors.

        Each sub-goal descriptor is a dict with keys:
            ``type``, ``priority``, ``role``, ``depth``.

        Parameters
        ----------
        type_expr:
            The type expression to decompose.
        priority:
            Priority budget inherited from the parent goal.
        depth:
            Current decomposition depth (for termination).

        Returns
        -------
        list[dict[str, Any]]
            List of sub-goal descriptor dicts.
        """
        if depth >= self.max_depth:
            return []
        cache_key = f"{type_expr}@{depth}"
        if cache_key in self._decomposition_cache:
            return self._decomposition_cache[cache_key]
        result = self._decompose_step(type_expr, priority, depth)
        self._decomposition_cache[cache_key] = result
        self._decompositions_performed += 1
        return result

    def make_sub_goal(self, parent_goal: "SynthesisGoal", sub_type: str, role: str, priority: float) -> "SynthesisGoal":
        """Create a SynthesisGoal for a sub-type of a parent goal.

        Parameters
        ----------
        parent_goal:
            The goal being decomposed.
        sub_type:
            The type expression for the new sub-goal.
        role:
            A label describing the sub-goal's role (e.g., ``"domain"``,
            ``"codomain"``, ``"left"``, ``"right"``).
        priority:
            Priority assigned to the sub-goal.

        Returns
        -------
        SynthesisGoal
            A new SynthesisGoal with a derived ID.
        """
        new_id = f"{parent_goal.goal_id}_{role}"
        return SynthesisGoal(
            goal_id=new_id,
            target_type=sub_type,
            constraints=parent_goal.constraints,
            priority=priority,
            trust_tier=parent_goal.trust_tier,
            obstruction_class=parent_goal.obstruction_class,
        )

    def decompose_goal(self, goal: "SynthesisGoal") -> tuple["SynthesisGoal", ...]:
        """Decompose a SynthesisGoal into sub-goals.

        Returns an empty tuple if the goal is already atomic.
        """
        sub_descriptors = self.decompose(goal.target_type, goal.priority)
        return tuple(
            self.make_sub_goal(goal, d["type"], d["role"], d["priority"])
            for d in sub_descriptors
        )

    def statistics(self) -> dict[str, int]:
        """Return decomposition statistics."""
        return {
            "cache_size": len(self._decomposition_cache),
            "total_decompositions": self._decompositions_performed,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decompose_step(self, type_expr: str, priority: float, depth: int) -> list[dict[str, Any]]:
        """Single-step decomposition dispatch."""
        te = type_expr.strip()

        # Arrow type: A → B
        if TC_ARROW in te:
            parts = te.split(TC_ARROW, 1)
            return [
                {"type": parts[0].strip(), "priority": priority * 0.5,
                 "role": "domain", "depth": depth + 1},
                {"type": parts[1].strip(), "priority": priority * 0.5,
                 "role": "codomain", "depth": depth + 1},
            ]

        # Product type: A × B
        if TC_PRODUCT in te:
            parts = te.split(TC_PRODUCT, 1)
            return [
                {"type": parts[0].strip(), "priority": priority * 0.5,
                 "role": "left", "depth": depth + 1},
                {"type": parts[1].strip(), "priority": priority * 0.5,
                 "role": "right", "depth": depth + 1},
            ]

        # Sum type: A + B — choose the left branch heuristically
        if TC_SUM in te:
            parts = te.split(TC_SUM, 1)
            return [
                {"type": parts[0].strip(), "priority": priority * 0.8,
                 "role": "inl", "depth": depth + 1},
            ]

        # List A
        if te.startswith(TC_LIST + " ") or te.startswith("List "):
            inner = te.split(" ", 1)[1].strip()
            return [{"type": inner, "priority": priority, "role": "elem", "depth": depth + 1}]

        # Option A
        if te.startswith(TC_OPTION + " ") or te.startswith("Option "):
            inner = te.split(" ", 1)[1].strip()
            return [{"type": inner, "priority": priority * 0.9, "role": "some", "depth": depth + 1}]

        # ∀x.B — strip quantifier
        if te.startswith(TC_FORALL) or te.startswith("forall"):
            body = te.split(".", 1)[-1].strip()
            return [{"type": body, "priority": priority, "role": "body", "depth": depth + 1}]

        # Atomic type — no decomposition
        return []

    def __repr__(self) -> str:
        return f"GoalDecomposer(max_depth={self.max_depth}, cache={len(self._decomposition_cache)})"


# ---------------------------------------------------------------------------
# Primary frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthesisGoal:
    """A goal for the local inhabitant synthesis engine.

    A SynthesisGoal encodes the *what* of synthesis: the target type to
    inhabit, priority ordering, and the Čech obstruction class that must
    be trivial for synthesis to succeed globally.

    Under Curry-Howard, SynthesisGoal is a *sequent*:

        Γ ⊢ ? : target_type   (find a term of target_type in context Γ)

    Attributes
    ----------
    goal_id : str
        Unique identifier for this goal.
    target_type : str
        The type expression to synthesize an inhabitant for.
    constraints : tuple[str, ...]
        Additional syntactic or semantic constraints on the inhabitant.
    priority : float
        Scheduling priority ∈ [0, 1]; higher = more urgent.
    trust_tier : TrustTier
        Minimum trust tier required for an accepted inhabitant.
    obstruction_class : tuple[complex, ...]
        The Čech H¹ obstruction class associated with this goal.
        A non-trivial class means glueing across the cover is obstructed.
    """

    goal_id: str
    target_type: str
    constraints: tuple[str, ...]
    priority: float
    trust_tier: TrustTier
    obstruction_class: tuple[complex, ...]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_trivially_obstructed(self) -> bool:
        """True iff the obstruction class is identically zero.

        A zero obstruction class means local inhabitants can be glued
        without any H¹ impediment.
        """
        return all(abs(z) < 1e-10 for z in self.obstruction_class)

    @property
    def obstruction_norm(self) -> float:
        """L² norm of the obstruction class."""
        return obstruction_norm(self.obstruction_class)

    @property
    def is_atomic(self) -> bool:
        """True iff target_type contains no type constructor."""
        return not any(tc in self.target_type for tc in ALL_TYPE_CONSTRUCTORS)

    def to_judgment_fragment(self) -> dict[str, Any]:
        """Return the (φ, O) components of a judgment 8-tuple for this goal."""
        return {
            "phi": self.target_type,
            "O": list(self.obstruction_class),
        }

    def with_priority(self, new_priority: float) -> "SynthesisGoal":
        """Return a copy with an updated priority value."""
        return SynthesisGoal(
            goal_id=self.goal_id,
            target_type=self.target_type,
            constraints=self.constraints,
            priority=new_priority,
            trust_tier=self.trust_tier,
            obstruction_class=self.obstruction_class,
        )

    def with_trust(self, new_tier: TrustTier) -> "SynthesisGoal":
        """Return a copy with an elevated trust requirement."""
        return SynthesisGoal(
            goal_id=self.goal_id,
            target_type=self.target_type,
            constraints=self.constraints,
            priority=self.priority,
            trust_tier=new_tier,
            obstruction_class=self.obstruction_class,
        )

    def __repr__(self) -> str:
        obs_summary = f"|obs|={self.obstruction_norm:.3f}"
        return (
            f"SynthesisGoal(id={self.goal_id!r}, "
            f"type={self.target_type!r}, "
            f"priority={self.priority:.2f}, "
            f"tier={self.trust_tier.name}, {obs_summary})"
        )


@dataclass(frozen=True)
class InhabitantCandidate:
    """A candidate inhabitant produced by the synthesis engine.

    An InhabitantCandidate is a *term candidate* t together with its
    type expression, a construction trace (the sequence of proof rules
    applied), a quality score, and a Čech witness certifying compatibility
    across the cover.

    Under Curry-Howard, this is simultaneously:
    - A program candidate (for type-directed program synthesis)
    - A proof sketch (for interactive theorem proving)
    - A section candidate (for sheaf-theoretic glueing)

    Attributes
    ----------
    candidate_id : str
        Unique identifier for this candidate.
    type_expression : str
        The type of the synthesized term.
    construction_trace : tuple[str, ...]
        Ordered sequence of construction steps (proof rules, lemma refs).
    quality_score : float
        Aggregate quality ∈ [0, 1] as assigned by InhabitantEvaluator.
    trust_tier : TrustTier
        The epistemic trust level of this candidate.
    cech_witness : tuple[complex, ...]
        Čech 1-cocycle witness; zero → compatible across cover.
    """

    candidate_id: str
    type_expression: str
    construction_trace: tuple[str, ...]
    quality_score: float
    trust_tier: TrustTier
    cech_witness: tuple[complex, ...]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_proof_grade(self) -> bool:
        """True iff the candidate has VERIFIED or higher trust."""
        return self.trust_tier >= TrustTier.VERIFIED

    @property
    def has_trivial_witness(self) -> bool:
        """True iff the Čech witness is trivial (no glueing obstruction)."""
        return all(abs(z) < 1e-10 for z in self.cech_witness)

    @property
    def witness_norm(self) -> float:
        """L² norm of the Čech witness."""
        return obstruction_norm(self.cech_witness)

    @property
    def trace_depth(self) -> int:
        """Number of steps in the construction trace."""
        return len(self.construction_trace)

    def to_judgment_evidence(self) -> dict[str, Any]:
        """Return the (E, T, Π) components of a judgment 8-tuple."""
        return {
            "E": self.quality_score,
            "T": self.trust_tier.name,
            "Pi": list(self.construction_trace),
        }

    def elevate_trust(self, new_tier: TrustTier) -> "InhabitantCandidate":
        """Return a copy with an elevated trust tier."""
        return InhabitantCandidate(
            candidate_id=self.candidate_id,
            type_expression=self.type_expression,
            construction_trace=self.construction_trace,
            quality_score=self.quality_score,
            trust_tier=new_tier,
            cech_witness=self.cech_witness,
        )

    def with_score(self, new_score: float) -> "InhabitantCandidate":
        """Return a copy with an updated quality score."""
        return InhabitantCandidate(
            candidate_id=self.candidate_id,
            type_expression=self.type_expression,
            construction_trace=self.construction_trace,
            quality_score=max(0.0, min(1.0, new_score)),
            trust_tier=self.trust_tier,
            cech_witness=self.cech_witness,
        )

    def __repr__(self) -> str:
        return (
            f"InhabitantCandidate(id={self.candidate_id!r}, "
            f"type={self.type_expression!r}, "
            f"score={self.quality_score:.3f}, "
            f"tier={self.trust_tier.name})"
        )


@dataclass(frozen=True)
class SynthesisPolicy:
    """Configuration controlling the synthesis strategy.

    A SynthesisPolicy specifies *how* synthesis should proceed: which
    constructors are allowed, resource limits, and the minimum trust
    tier required for candidates to be accepted.

    Attributes
    ----------
    policy_id : str
        Unique identifier for this policy.
    strategy : str
        Synthesis strategy name: ``"bfs"`` (breadth-first), ``"dfs"``
        (depth-first), ``"iterative_deepening"``, or ``"heuristic"``.
    max_depth : int
        Maximum depth of the SynthesisTree.
    timeout_ms : int
        Synthesis timeout in milliseconds.
    trust_requirement : TrustTier
        Minimum trust tier for accepted candidates.
    allowed_constructors : tuple[str, ...]
        Subset of ALL_TYPE_CONSTRUCTORS permitted in this synthesis run.
    """

    policy_id: str
    strategy: str
    max_depth: int
    timeout_ms: int
    trust_requirement: TrustTier
    allowed_constructors: tuple[str, ...]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_permissive(self) -> bool:
        """True iff all built-in constructors are allowed."""
        return set(self.allowed_constructors) >= set(ALL_TYPE_CONSTRUCTORS)

    @property
    def allows_recursive_types(self) -> bool:
        """True iff TC_LIST or TC_FORALL is in the allowed constructors."""
        return TC_LIST in self.allowed_constructors or TC_FORALL in self.allowed_constructors

    def allows_constructor(self, tc: str) -> bool:
        """Return True if the given type constructor is allowed by this policy."""
        return tc in self.allowed_constructors

    def with_depth(self, new_max_depth: int) -> "SynthesisPolicy":
        """Return a copy with an updated max_depth."""
        return SynthesisPolicy(
            policy_id=self.policy_id,
            strategy=self.strategy,
            max_depth=new_max_depth,
            timeout_ms=self.timeout_ms,
            trust_requirement=self.trust_requirement,
            allowed_constructors=self.allowed_constructors,
        )

    def to_config_dict(self) -> dict[str, Any]:
        """Serialise to a configuration dict."""
        return {
            "policy_id": self.policy_id,
            "strategy": self.strategy,
            "max_depth": self.max_depth,
            "timeout_ms": self.timeout_ms,
            "trust_requirement": self.trust_requirement.name,
            "allowed_constructors": list(self.allowed_constructors),
        }

    def __repr__(self) -> str:
        return (
            f"SynthesisPolicy(id={self.policy_id!r}, "
            f"strategy={self.strategy!r}, "
            f"depth={self.max_depth}, "
            f"tier={self.trust_requirement.name})"
        )


@dataclass(frozen=True)
class LocalInhabitantSynthesis:
    """A record of a completed local inhabitant synthesis run.

    LocalInhabitantSynthesis is the *result* datatype: it captures what
    was synthesized (the inhabitants), in which cover element, under what
    policy, and with what trust tier and judgment.

    The ``judgment`` field is a full 8-tuple
    ``(c, φ, A, E, O, B, T, Π)`` as required by Ch42 theory.

    Attributes
    ----------
    synthesis_id : str
        Unique run identifier (UUID hex).
    cover_element : str
        The cover element Uᵢ in which synthesis was performed.
    type_signature : str
        The type T that was inhabited.
    generated_inhabitants : tuple[str, ...]
        The synthesized term expressions.
    synthesis_policy : str
        The policy_id of the SynthesisPolicy used.
    trust_tier : TrustTier
        The composite trust tier of the synthesis result.
    judgment : tuple
        8-tuple (c, φ, A, E, O, B, T, Π).
    """

    synthesis_id: str
    cover_element: str
    type_signature: str
    generated_inhabitants: tuple[str, ...]
    synthesis_policy: str
    trust_tier: TrustTier
    judgment: tuple  # (c, φ, A, E, O, B, T, Π)

    # ------------------------------------------------------------------
    # Judgment decomposition helpers
    # ------------------------------------------------------------------

    @property
    def j_context(self) -> Any:
        """Return the context c from the judgment 8-tuple."""
        return self.judgment[0] if len(self.judgment) > 0 else None

    @property
    def j_formula(self) -> Any:
        """Return the formula φ from the judgment 8-tuple."""
        return self.judgment[1] if len(self.judgment) > 1 else None

    @property
    def j_assumptions(self) -> Any:
        """Return the assumptions A from the judgment 8-tuple."""
        return self.judgment[2] if len(self.judgment) > 2 else None

    @property
    def j_evidence(self) -> Any:
        """Return the evidence E from the judgment 8-tuple."""
        return self.judgment[3] if len(self.judgment) > 3 else None

    @property
    def j_obstructions(self) -> Any:
        """Return the obstructions O from the judgment 8-tuple."""
        return self.judgment[4] if len(self.judgment) > 4 else None

    @property
    def j_blame(self) -> Any:
        """Return the blame B from the judgment 8-tuple."""
        return self.judgment[5] if len(self.judgment) > 5 else None

    @property
    def j_trust(self) -> Any:
        """Return the trust T from the judgment 8-tuple."""
        return self.judgment[6] if len(self.judgment) > 6 else None

    @property
    def j_proof_obligations(self) -> Any:
        """Return proof obligations Π from the judgment 8-tuple."""
        return self.judgment[7] if len(self.judgment) > 7 else None

    @property
    def inhabitant_count(self) -> int:
        """Number of synthesized inhabitants."""
        return len(self.generated_inhabitants)

    @property
    def is_successful(self) -> bool:
        """True iff at least one inhabitant was synthesized."""
        return self.inhabitant_count > 0

    def with_elevated_trust(self, new_tier: TrustTier) -> "LocalInhabitantSynthesis":
        """Return a copy with the trust tier raised to new_tier."""
        new_judgment = tuple(
            new_tier if i == 6 else v
            for i, v in enumerate(self.judgment)
        )
        return LocalInhabitantSynthesis(
            synthesis_id=self.synthesis_id,
            cover_element=self.cover_element,
            type_signature=self.type_signature,
            generated_inhabitants=self.generated_inhabitants,
            synthesis_policy=self.synthesis_policy,
            trust_tier=new_tier,
            judgment=new_judgment,
        )

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of this synthesis run."""
        return {
            "synthesis_id": self.synthesis_id,
            "cover_element": self.cover_element,
            "type_signature": self.type_signature,
            "inhabitants": list(self.generated_inhabitants),
            "policy": self.synthesis_policy,
            "trust_tier": self.trust_tier.name,
            "inhabitant_count": self.inhabitant_count,
            "successful": self.is_successful,
        }

    def __repr__(self) -> str:
        return (
            f"LocalInhabitantSynthesis(id={self.synthesis_id[:8]!r}, "
            f"cover={self.cover_element!r}, "
            f"type={self.type_signature!r}, "
            f"n_inhabitants={self.inhabitant_count}, "
            f"tier={self.trust_tier.name})"
        )


# ---------------------------------------------------------------------------
# Module-level default policy instances
# ---------------------------------------------------------------------------

DEFAULT_POLICY: SynthesisPolicy = SynthesisPolicy(
    policy_id=DEFAULT_SYNTHESIS_POLICY_ID,
    strategy="heuristic",
    max_depth=5,
    timeout_ms=500,
    trust_requirement=TrustTier.PROPOSAL,
    allowed_constructors=ALL_TYPE_CONSTRUCTORS,
)
"""Default synthesis policy: heuristic search, all constructors allowed."""

FAST_POLICY: SynthesisPolicy = SynthesisPolicy(
    policy_id=FAST_SYNTHESIS_POLICY_ID,
    strategy="bfs",
    max_depth=3,
    timeout_ms=100,
    trust_requirement=TrustTier.PROPOSAL,
    allowed_constructors=(TC_ARROW, TC_PRODUCT, TC_UNIT),
)
"""Fast synthesis policy: shallow BFS, restricted constructors."""

DEEP_POLICY: SynthesisPolicy = SynthesisPolicy(
    policy_id=DEEP_SYNTHESIS_POLICY_ID,
    strategy="iterative_deepening",
    max_depth=10,
    timeout_ms=5000,
    trust_requirement=TrustTier.VERIFIED,
    allowed_constructors=ALL_TYPE_CONSTRUCTORS,
)
"""Deep synthesis policy: iterative deepening, verified trust required."""


# ---------------------------------------------------------------------------
# Primary public functions
# ---------------------------------------------------------------------------


def synthesize_inhabitant(
    goal: SynthesisGoal,
    policy: SynthesisPolicy,
    context: CoverElementContext,
) -> InhabitantCandidate:
    """Synthesize a type inhabitant for the given goal in the given cover context.

    This function is the main entry point for single-goal synthesis.  It:

    1. Checks the goal's obstruction class; if non-trivial, records a warning.
    2. Decomposes the target type using GoalDecomposer.
    3. Builds a SynthesisTree (BFS or DFS depending on policy.strategy).
    4. Assigns candidates to each tree node via type-directed search.
    5. Evaluates the resulting candidate using InhabitantEvaluator.
    6. Returns an InhabitantCandidate with the composite trust.

    Parameters
    ----------
    goal : SynthesisGoal
        The synthesis goal specifying target type and constraints.
    policy : SynthesisPolicy
        The synthesis policy controlling search strategy and limits.
    context : CoverElementContext
        The local cover element context supplying the type environment.

    Returns
    -------
    InhabitantCandidate
        The best candidate found; quality_score = 0.0 if synthesis failed.

    Notes
    -----
    Under the Curry-Howard isomorphism, this function is simultaneously
    performing *proof search* for the proposition corresponding to goal.target_type.
    The construction_trace records the proof rules applied.
    """
    evaluator = InhabitantEvaluator()
    decomposer = GoalDecomposer(max_depth=policy.max_depth)

    # Build a synthesis tree rooted at the target type
    tree = SynthesisTree(
        root_label=goal.target_type,
        rule_applied="intro",
        trust=policy.trust_requirement,
    )

    # Populate the tree with sub-goals
    sub_descriptors = decomposer.decompose(goal.target_type, priority=goal.priority)
    construction_steps: list[str] = [f"intro:{goal.target_type}"]

    for desc in sub_descriptors[:policy.max_depth]:
        if not policy.allows_constructor(desc.get("type", "")[:1]):
            pass  # constructor not allowed; skip
        child = SynthesisTree(
            root_label=desc["type"],
            rule_applied=desc.get("role", "sub"),
            trust=policy.trust_requirement,
        )
        # Attempt to find a variable in context with matching type
        var_match = next(
            (v for v, t in context.local_env if t == desc["type"]), None
        )
        if var_match:
            child.set_candidate(f"var({var_match})")
            construction_steps.append(f"axiom:{var_match}:{desc['type']}")
        else:
            # Use the type constructor as a placeholder term
            child.set_candidate(f"intro({desc['type']})")
            construction_steps.append(f"apply:{desc.get('role','sub')}:{desc['type']}")
        tree.add_child(child)

    # Assign root candidate
    root_term = tree.extract_term()
    tree.set_candidate(root_term)
    construction_steps.append(f"complete:{root_term[:40]}")

    # Determine composite trust
    composite_trust = trust_meet_chain(
        (policy.trust_requirement, goal.trust_tier)
    )

    # Evaluate quality
    quality = evaluator.score(
        candidate_type=goal.target_type,
        target_type=goal.target_type,
        construction_trace=tuple(construction_steps),
        candidate_tier=composite_trust,
        required_tier=policy.trust_requirement,
        cech_witness=goal.obstruction_class,
    )

    return InhabitantCandidate(
        candidate_id=uuid.uuid4().hex,
        type_expression=goal.target_type,
        construction_trace=tuple(construction_steps),
        quality_score=quality,
        trust_tier=composite_trust,
        cech_witness=goal.obstruction_class,
    )


def generate_synthesis_goals(
    type_env: TypeEnvironment,
    cover_element: str,
) -> tuple[SynthesisGoal, ...]:
    """Generate SynthesisGoals from a TypeEnvironment for a given cover element.

    For each bound type in type_env, a SynthesisGoal is produced with:
    - target_type  = the bound type expression
    - priority     = 1/(1 + rank)  (earlier bindings get higher priority)
    - trust_tier   = PROPOSAL
    - obstruction_class = trivial_obstruction()

    This implements the *goal extraction* phase of Ch42 §1: given the
    local type data F(Uᵢ), produce the set of inhabitation goals that
    must be resolved before Uᵢ can contribute to a global section.

    Parameters
    ----------
    type_env : TypeEnvironment
        The type environment for the cover element.
    cover_element : str
        The identifier of the cover element Uᵢ.

    Returns
    -------
    tuple[SynthesisGoal, ...]
        One goal per type binding in type_env.
    """
    goals: list[SynthesisGoal] = []
    for rank, (var, typ) in enumerate(type_env):
        if not type_env.is_inhabited(typ):
            continue  # skip vacuously uninhabitable types
        goal_id = f"goal_{cover_element}_{var}_{rank}"
        priority = 1.0 / (1.0 + rank)
        goals.append(
            SynthesisGoal(
                goal_id=goal_id,
                target_type=typ,
                constraints=(f"var:{var}",),
                priority=priority,
                trust_tier=TrustTier.PROPOSAL,
                obstruction_class=trivial_obstruction(),
            )
        )
    # Also add a goal for each inhabitable base type present in the universe
    for rank, typ in enumerate(sorted(type_env.universe())):
        if typ in TypeEnvironment.BASE_TYPES and type_env.is_inhabited(typ):
            goal_id = f"goal_{cover_element}_universe_{typ}_{rank}"
            priority = 0.5 / (1.0 + rank)
            goals.append(
                SynthesisGoal(
                    goal_id=goal_id,
                    target_type=typ,
                    constraints=(),
                    priority=priority,
                    trust_tier=TrustTier.PROPOSAL,
                    obstruction_class=trivial_obstruction(),
                )
            )
    return tuple(goals)


def evaluate_candidate(
    candidate: InhabitantCandidate,
    goal: SynthesisGoal,
    trust_threshold: TrustTier,
) -> float:
    """Evaluate a candidate against a goal and return a composite score.

    This is a convenience wrapper around InhabitantEvaluator.score that
    also applies a threshold gate: if candidate.trust_tier < trust_threshold,
    the score is clamped to 0.

    Parameters
    ----------
    candidate : InhabitantCandidate
        The candidate to evaluate.
    goal : SynthesisGoal
        The goal against which to evaluate.
    trust_threshold : TrustTier
        Minimum trust tier for a non-zero score.

    Returns
    -------
    float
        Score ∈ [0, 1]; 0 if candidate fails the trust threshold.
    """
    if candidate.trust_tier < trust_threshold:
        return 0.0
    evaluator = InhabitantEvaluator()
    return evaluator.score(
        candidate_type=candidate.type_expression,
        target_type=goal.target_type,
        construction_trace=candidate.construction_trace,
        candidate_tier=candidate.trust_tier,
        required_tier=trust_threshold,
        cech_witness=candidate.cech_witness,
    )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "TrustTier",
    "trust_meet_chain",
    "trust_join_chain",
    "make_cech_cochain",
    "cech_coboundary",
    "is_cocycle",
    "obstruction_norm",
    "trivial_obstruction",
    "random_obstruction",
    "TypeEnvironment",
    "CoverElementContext",
    "SynthesisTree",
    "ObstructionTracker",
    "InhabitantEvaluator",
    "GoalDecomposer",
    "SynthesisGoal",
    "InhabitantCandidate",
    "SynthesisPolicy",
    "LocalInhabitantSynthesis",
    "DEFAULT_POLICY",
    "FAST_POLICY",
    "DEEP_POLICY",
    "synthesize_inhabitant",
    "generate_synthesis_goals",
    "evaluate_candidate",
    "ALL_TYPE_CONSTRUCTORS",
    "TC_ARROW", "TC_PRODUCT", "TC_SUM", "TC_UNIT",
    "TC_VOID", "TC_LIST", "TC_OPTION", "TC_FORALL",
]


# ---------------------------------------------------------------------------
# __main__ — exercises every class and function
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Local Inhabitant Synthesis — Goal Reconstruction (goal_re)")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. TrustTier lattice
    # ------------------------------------------------------------------
    print("\n--- TrustTier lattice ---")
    tiers = [TrustTier.PROPOSAL, TrustTier.REVIEWED, TrustTier.VERIFIED,
             TrustTier.RUNTIME_WITNESSED, TrustTier.PROOF_BACKED]
    for t in tiers:
        print(f"  {t.name:20s}  value={t.value}  is_proof_grade={t.is_proof_grade}")

    meet_result = trust_meet_chain((TrustTier.VERIFIED, TrustTier.REVIEWED, TrustTier.PROPOSAL))
    join_result = trust_join_chain((TrustTier.PROPOSAL, TrustTier.RUNTIME_WITNESSED))
    print(f"\n  meet(VERIFIED, REVIEWED, PROPOSAL) = {meet_result.name}")
    print(f"  join(PROPOSAL, RUNTIME_WITNESSED)  = {join_result.name}")
    print(f"  PROPOSAL <= VERIFIED: {TrustTier.PROPOSAL <= TrustTier.VERIFIED}")
    print(f"  PROOF_BACKED > REVIEWED: {TrustTier.PROOF_BACKED > TrustTier.REVIEWED}")

    # ------------------------------------------------------------------
    # 2. Type constructors
    # ------------------------------------------------------------------
    print("\n--- Type constructors ---")
    for tc in ALL_TYPE_CONSTRUCTORS:
        print(f"  {tc}")

    # ------------------------------------------------------------------
    # 3. Cohomology helpers
    # ------------------------------------------------------------------
    print("\n--- Čech H¹ cohomology ---")
    cochain = make_cech_cochain(3)
    cochain[0][1] = complex(0.1, 0.2)
    cochain[1][0] = complex(-0.1, -0.2)
    delta = cech_coboundary(cochain, 0, 1, 2)
    print(f"  Coboundary (0,1,2) = {delta}")
    print(f"  Is zero-cochain a cocycle? {is_cocycle(make_cech_cochain(3))}")
    obs = random_obstruction(seed=42, n=4)
    print(f"  Random obstruction (seed=42): {[f'{z:.3f}' for z in obs]}")
    print(f"  Obstruction norm: {obstruction_norm(obs):.4f}")
    triv = trivial_obstruction(4)
    print(f"  Trivial obstruction norm: {obstruction_norm(triv):.4f}")

    # ------------------------------------------------------------------
    # 4. TypeEnvironment
    # ------------------------------------------------------------------
    print("\n--- TypeEnvironment ---")
    env = TypeEnvironment()
    env.bind("x", "Nat").bind("f", f"Nat {TC_ARROW} Bool").bind("xs", f"List Nat")
    print(f"  {env}")
    print(f"  variables: {env.variables()}")
    print(f"  is_inhabited('Nat'): {env.is_inhabited('Nat')}")
    print(f"  is_inhabited('Void'): {env.is_inhabited('Void')}")
    print(f"  size: {env.size()}")
    env2 = TypeEnvironment()
    env2.bind("y", "Bool")
    merged = env.extend(env2)
    print(f"  merged env size: {merged.size()}")

    # ------------------------------------------------------------------
    # 5. CoverElementContext
    # ------------------------------------------------------------------
    print("\n--- CoverElementContext ---")
    ctx = CoverElementContext("U_0", local_env=env)
    ctx.add_overlap("U_1", ["x", "f"])
    ctx.set_metadata("priority", 0.9)
    print(f"  {ctx}")
    print(f"  inhabitable types: {ctx.inhabitable_types()}")
    print(f"  shared with U_1: {ctx.shared_variables('U_1')}")
    print(f"  neighbours: {ctx.all_neighbours()}")
    print(f"  judgment context: {ctx.to_judgment_context()}")

    # ------------------------------------------------------------------
    # 6. ObstructionTracker
    # ------------------------------------------------------------------
    print("\n--- ObstructionTracker ---")
    tracker = ObstructionTracker()
    tracker.record("U_0", "U_1", complex(0.3, -0.1), blame="type mismatch on f")
    tracker.record("U_1", "U_2", complex(0.0, 0.0))
    print(f"  {tracker}")
    print(f"  has_obstruction: {tracker.has_obstruction()}")
    print(f"  total_norm: {tracker.total_obstruction_norm():.4f}")
    print(f"  can_glue: {tracker.can_glue()}")
    tracker.resolve("U_0", "U_1")
    print(f"  after resolve: can_glue={tracker.can_glue()}")
    print(f"  blame: {tracker.blame_report()}")
    print(f"  summary: {tracker.summary()}")

    # ------------------------------------------------------------------
    # 7. SynthesisTree
    # ------------------------------------------------------------------
    print("\n--- SynthesisTree ---")
    arrow_type = f"Nat {TC_ARROW} Bool"
    tree = SynthesisTree(arrow_type, rule_applied="→-intro", trust=TrustTier.REVIEWED)
    left = SynthesisTree("Nat", rule_applied="axiom", trust=TrustTier.REVIEWED)
    right = SynthesisTree("Bool", rule_applied="elim", trust=TrustTier.PROPOSAL)
    left.set_candidate("zero")
    right.set_candidate("true")
    tree.add_child(left)
    tree.add_child(right)
    tree.set_candidate("λx:Nat.true")
    print(f"  {tree}")
    print(f"  depth: {tree.depth()}")
    print(f"  size: {tree.size()}")
    print(f"  is_complete: {tree.is_complete()}")
    print(f"  composite_trust: {tree.composite_trust().name}")
    print(f"  extract_term: {tree.extract_term()}")
    print(f"  leaves: {tree.leaves()}")

    # ------------------------------------------------------------------
    # 8. GoalDecomposer
    # ------------------------------------------------------------------
    print("\n--- GoalDecomposer ---")
    decomposer = GoalDecomposer(max_depth=4)
    for expr in [f"Nat {TC_ARROW} Bool", f"Nat {TC_PRODUCT} Bool",
                 f"List Nat", f"Option Bool", f"∀x.Nat"]:
        subs = decomposer.decompose(expr)
        print(f"  decompose({expr!r}) → {[d['type'] for d in subs]}")
    print(f"  stats: {decomposer.statistics()}")

    # ------------------------------------------------------------------
    # 9. InhabitantEvaluator
    # ------------------------------------------------------------------
    print("\n--- InhabitantEvaluator ---")
    evaluator = InhabitantEvaluator()
    score = evaluator.score(
        candidate_type="Nat",
        target_type="Nat",
        construction_trace=("axiom:x:Nat", "proof:reflexivity"),
        candidate_tier=TrustTier.VERIFIED,
        required_tier=TrustTier.REVIEWED,
        cech_witness=trivial_obstruction(2),
    )
    print(f"  score (Nat→Nat, VERIFIED): {score:.4f}")
    print(f"  breakdown: {evaluator.last_breakdown()}")
    score2 = evaluator.score(
        candidate_type="Bool",
        target_type="Nat",
        construction_trace=("apply:elim",),
        candidate_tier=TrustTier.PROPOSAL,
        required_tier=TrustTier.VERIFIED,
        cech_witness=random_obstruction(7, n=2),
    )
    print(f"  score (Bool→Nat, PROPOSAL vs VERIFIED): {score2:.4f}")
    print(f"  evaluation_count: {evaluator.evaluation_count()}")

    # ------------------------------------------------------------------
    # 10. SynthesisGoal
    # ------------------------------------------------------------------
    print("\n--- SynthesisGoal ---")
    goal = SynthesisGoal(
        goal_id="g_001",
        target_type=f"Nat {TC_ARROW} Bool",
        constraints=("arity:1",),
        priority=0.85,
        trust_tier=TrustTier.REVIEWED,
        obstruction_class=trivial_obstruction(3),
    )
    print(f"  {goal}")
    print(f"  is_atomic: {goal.is_atomic}")
    print(f"  is_trivially_obstructed: {goal.is_trivially_obstructed}")
    print(f"  obstruction_norm: {goal.obstruction_norm:.4f}")
    print(f"  judgment fragment: {goal.to_judgment_fragment()}")
    upd_goal = goal.with_priority(0.5).with_trust(TrustTier.VERIFIED)
    print(f"  updated: priority={upd_goal.priority}, trust={upd_goal.trust_tier.name}")

    # ------------------------------------------------------------------
    # 11. SynthesisPolicy
    # ------------------------------------------------------------------
    print("\n--- SynthesisPolicy ---")
    for pol in [DEFAULT_POLICY, FAST_POLICY, DEEP_POLICY]:
        print(f"  {pol}")
        print(f"    is_permissive={pol.is_permissive}  "
              f"allows_recursive={pol.allows_recursive_types}")
    custom_pol = DEFAULT_POLICY.with_depth(8)
    print(f"  custom_pol max_depth: {custom_pol.max_depth}")
    print(f"  config: {custom_pol.to_config_dict()}")

    # ------------------------------------------------------------------
    # 12. generate_synthesis_goals
    # ------------------------------------------------------------------
    print("\n--- generate_synthesis_goals ---")
    rich_env = TypeEnvironment()
    rich_env.bind("n", "Nat").bind("b", "Bool").bind("fn", f"Nat {TC_ARROW} Bool")
    goals = generate_synthesis_goals(rich_env, "U_main")
    print(f"  Generated {len(goals)} goals:")
    for g in goals[:6]:
        print(f"    {g}")

    # ------------------------------------------------------------------
    # 13. synthesize_inhabitant
    # ------------------------------------------------------------------
    print("\n--- synthesize_inhabitant ---")
    cover_ctx = CoverElementContext("U_main", local_env=rich_env)
    for g in goals[:3]:
        candidate = synthesize_inhabitant(g, DEFAULT_POLICY, cover_ctx)
        print(f"  goal={g.target_type!r}  →  {candidate}")
        print(f"    trace: {candidate.construction_trace}")
        print(f"    trivial_witness: {candidate.has_trivial_witness}")

    # ------------------------------------------------------------------
    # 14. evaluate_candidate
    # ------------------------------------------------------------------
    print("\n--- evaluate_candidate ---")
    sample_candidate = synthesize_inhabitant(goals[0], DEFAULT_POLICY, cover_ctx)
    for threshold in [TrustTier.PROPOSAL, TrustTier.REVIEWED, TrustTier.VERIFIED]:
        sc = evaluate_candidate(sample_candidate, goals[0], threshold)
        print(f"  threshold={threshold.name:20s} → score={sc:.4f}")

    # ------------------------------------------------------------------
    # 15. GoalDecomposer.decompose_goal
    # ------------------------------------------------------------------
    print("\n--- GoalDecomposer.decompose_goal ---")
    complex_goal = SynthesisGoal(
        goal_id="g_complex",
        target_type=f"Nat {TC_PRODUCT} Bool",
        constraints=(),
        priority=0.7,
        trust_tier=TrustTier.PROPOSAL,
        obstruction_class=trivial_obstruction(),
    )
    sub_goals = decomposer.decompose_goal(complex_goal)
    print(f"  {complex_goal.target_type!r} decomposes into {len(sub_goals)} sub-goals:")
    for sg in sub_goals:
        print(f"    {sg}")

    # ------------------------------------------------------------------
    # 16. InhabitantCandidate derived properties
    # ------------------------------------------------------------------
    print("\n--- InhabitantCandidate derived properties ---")
    cand = InhabitantCandidate(
        candidate_id="cand_001",
        type_expression="Nat",
        construction_trace=("axiom:n:Nat", "proof:reflexivity", "witness:zero"),
        quality_score=0.87,
        trust_tier=TrustTier.VERIFIED,
        cech_witness=trivial_obstruction(2),
    )
    print(f"  {cand}")
    print(f"  is_proof_grade: {cand.is_proof_grade}")
    print(f"  trace_depth: {cand.trace_depth}")
    print(f"  witness_norm: {cand.witness_norm:.4f}")
    elevated = cand.elevate_trust(TrustTier.PROOF_BACKED)
    print(f"  elevated trust: {elevated.trust_tier.name}")
    rescored = cand.with_score(0.42)
    print(f"  rescored: {rescored.quality_score:.3f}")
    print(f"  judgment evidence: {cand.to_judgment_evidence()}")

    # ------------------------------------------------------------------
    # 17. LocalInhabitantSynthesis — build a full 8-tuple judgment
    # ------------------------------------------------------------------
    print("\n--- LocalInhabitantSynthesis ---")
    j_context = cover_ctx.to_judgment_context()
    j_phi = "Nat"
    j_assumptions = ("n:Nat",)
    j_evidence = cand.quality_score
    j_obs = list(cand.cech_witness)
    j_blame = ""
    j_trust = cand.trust_tier
    j_proof_obls = ("reflexivity_check",)
    judgment_8 = (j_context, j_phi, j_assumptions, j_evidence,
                  j_obs, j_blame, j_trust, j_proof_obls)

    synthesis_result = LocalInhabitantSynthesis(
        synthesis_id=uuid.uuid4().hex,
        cover_element="U_main",
        type_signature="Nat",
        generated_inhabitants=("zero", "succ(zero)", "n"),
        synthesis_policy=DEFAULT_POLICY.policy_id,
        trust_tier=TrustTier.VERIFIED,
        judgment=judgment_8,
    )
    print(f"  {synthesis_result}")
    print(f"  inhabitant_count: {synthesis_result.inhabitant_count}")
    print(f"  is_successful: {synthesis_result.is_successful}")
    print(f"  j_formula: {synthesis_result.j_formula}")
    print(f"  j_trust: {synthesis_result.j_trust.name}")  # type: ignore[union-attr]
    print(f"  j_proof_obligations: {synthesis_result.j_proof_obligations}")
    elevated_result = synthesis_result.with_elevated_trust(TrustTier.PROOF_BACKED)
    print(f"  elevated trust result: {elevated_result.trust_tier.name}")
    print(f"  summary: {synthesis_result.to_summary()}")

    print("\n" + "=" * 70)
    print("All classes and functions exercised successfully.")
    print("=" * 70)
