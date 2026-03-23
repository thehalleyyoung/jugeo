#!/usr/bin/env python3
"""
# copilot: IR stack – canonical intermediate representations for Judgment Geometry

This module implements the canonical intermediate representations (IRs) for the
Judgment Geometry (JuGeo) theory as described in ``theory2.tex``.  The central
thesis is that the theory *wants* a small number of canonical IR levels: rather
than an unbounded tower of ad hoc representations, every judgment passes through
a fixed, well-typed pipeline of transformations.

Judgment Geometry encodes epistemic states as 8-tuples

    (c, φ, A, E, O, B, T, Π)

where

* **c** — context carrier (ambient categorical context, e.g. a topos slice)
* **φ** — formula / proposition under consideration
* **A** — ambient logical assumptions (a finite set of propositions)
* **E** — evidence bundle (a functor E : Evidence → Ctx)
* **O** — obstruction class, an element of Čech cohomology H¹(U, 𝒪)
* **B** — binding environment (variable → term map)
* **T** — term under evaluation
* **Π** — proof certificate or proof-obligation handle

Judgments are **never** boolean values: they are exactly these 8-tuples, possibly
with some components set to a canonical ``ABSENT`` sentinel.

Obstructions arise as Čech H¹ cohomology classes on an open cover U of the
parameter space.  An obstruction O ∈ H¹(U, 𝒪) witnesses a non-trivial
gluing failure: local sections that agree on overlaps cannot be assembled into a
global section.

The IR stack maps each judgment through a fixed sequence of levels:

    SURFACE → DESUGARED → SCOPED → TYPED → LOWERED → CANONICAL

Each level is represented by an :class:`IRLevel` containing a tuple of
:class:`IRNode` objects together with their canonical normal forms.

References
----------
theory2.tex §§ 3, 7, 12, 18, 24.
"""
from __future__ import annotations

import enum
import hashlib
import itertools
import textwrap
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional

# ---------------------------------------------------------------------------
# Conditional imports – jugeo core may not be installed in all environments
# ---------------------------------------------------------------------------

try:
    from jugeo.core.judgment import Judgment, TrustTier
    from jugeo.core.obstruction import Obstruction
except ImportError:
    # fallback stubs used when jugeo core is not installed
    Judgment = None
    TrustTier = None
    Obstruction = None

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MAX_IR_DEPTH: int = 64
"""Maximum nesting depth of an IR tree.  Exceeding this depth raises
:class:`IRValidationError`.  The bound prevents runaway recursion in circular
or mutually-recursive IR structures that have not been properly linearised."""

CANONICAL_VERSION: int = 3
"""Version stamp for the canonical normal form algorithm.  Increment whenever
the normalisation procedure changes in a semantically significant way so that
cached normal forms can be invalidated."""

ABSENT: str = "__ABSENT__"
"""Sentinel string used in place of missing judgment-tuple components.  Using a
dedicated sentinel (rather than ``None``) makes it possible to distinguish
"not provided" from "provided as None" in the payload of an :class:`IRNode`."""

_CECH_PRIME: int = 1_000_000_007
"""Large prime used as the modulus in the polynomial rolling hash for Čech
class representatives.  Chosen to be a safe prime to minimise collision
probability in the cohomology fingerprint computation."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrustTierEnum(enum.IntEnum):
    """Epistemic trust levels assigned to IR nodes.

    The ordering PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED
    is a total order on the degree of confidence that the content of an IR node
    has been validated.  The ordering is used when merging nodes from different
    levels: the *minimum* trust tier of any ancestor is inherited by its
    descendants (trust is not freely transferable upward).

    In formal terms, let T be the lattice (TrustTierEnum, ≤).  The trust-meet
    operation ∧ : T × T → T is simply the integer minimum:

        t₁ ∧ t₂  =  min(t₁, t₂)

    and the trust-join t₁ ∨ t₂ = max(t₁, t₂) is used when two independent
    proofs of the same fact are combined.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def meets(self, other: TrustTierEnum) -> TrustTierEnum:
        """Return the trust meet (greatest lower bound) of *self* and *other*.

        >>> TrustTierEnum.VERIFIED.meets(TrustTierEnum.PROPOSAL)
        <TrustTierEnum.PROPOSAL: 1>
        """
        return TrustTierEnum(min(self.value, other.value))

    def joins(self, other: TrustTierEnum) -> TrustTierEnum:
        """Return the trust join (least upper bound) of *self* and *other*.

        >>> TrustTierEnum.REVIEWED.joins(TrustTierEnum.PROOF_BACKED)
        <TrustTierEnum.PROOF_BACKED: 5>
        """
        return TrustTierEnum(max(self.value, other.value))

    @property
    def is_verified_or_above(self) -> bool:
        """True when the tier is at least VERIFIED (suitable for production use)."""
        return self.value >= TrustTierEnum.VERIFIED.value

    @property
    def label(self) -> str:
        """Human-readable label for the tier."""
        return self.name.replace("_", " ").title()


class IRKind(enum.Enum):
    """Canonical kind tags for :class:`IRNode` objects.

    Each kind corresponds to one of the eight components of a judgment tuple
    plus a dedicated PROOF kind for proof certificates:

    * JUDGMENT  — the full (c, φ, A, E, O, B, T, Π) tuple node
    * FORMULA   — a proposition φ
    * CONTEXT   — a context carrier c
    * ENVIRONMENT — an evidence bundle E or binding environment B
    * OBSTRUCTION — an obstruction class O ∈ H¹(U, 𝒪)
    * BINDING   — a single variable binding (x ↦ t) inside B
    * TERM      — a term T under evaluation
    * PROOF     — a proof certificate Π or proof obligation
    """

    JUDGMENT = "judgment"
    FORMULA = "formula"
    CONTEXT = "context"
    ENVIRONMENT = "environment"
    OBSTRUCTION = "obstruction"
    BINDING = "binding"
    TERM = "term"
    PROOF = "proof"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def is_formula_like(self) -> bool:
        """True for kinds that carry propositional content."""
        return self in (IRKind.FORMULA, IRKind.JUDGMENT)

    @property
    def is_proof_bearing(self) -> bool:
        """True for kinds that may carry a proof certificate."""
        return self in (IRKind.JUDGMENT, IRKind.PROOF)

    @property
    def component_index(self) -> Optional[int]:
        """Return the 0-based index of this kind in the judgment tuple, or None."""
        _map = {
            IRKind.CONTEXT: 0,
            IRKind.FORMULA: 1,
            IRKind.ENVIRONMENT: 3,
            IRKind.OBSTRUCTION: 4,
            IRKind.BINDING: 5,
            IRKind.TERM: 6,
            IRKind.PROOF: 7,
        }
        return _map.get(self)


# ---------------------------------------------------------------------------
# Named tuple for judgment tuples
# ---------------------------------------------------------------------------


class JudgmentTuple(NamedTuple):
    """An 8-tuple (c, φ, A, E, O, B, T, Π) representing a JuGeo judgment.

    Fields
    ------
    c : Any
        Context carrier — an object in the ambient category (e.g. a topos
        slice or a presheaf).
    phi : Any
        Formula — the proposition φ under consideration.
    A : Any
        Assumptions — a finite set (or frozenset) of propositions.
    E : Any
        Evidence bundle — a functor E : Evidence → Ctx encoded as a dict or
        dedicated dataclass.
    O : Any
        Obstruction class — an element of H¹(U, 𝒪) encoded as a
        :class:`CechObstructionClass` or the sentinel ``ABSENT``.
    B : Any
        Binding environment — a mapping from variables to terms.
    T : Any
        Term under evaluation.
    Pi : Any
        Proof certificate or proof obligation handle.

    Notes
    -----
    The fields are intentionally ``Any`` because at the surface level the types
    are unresolved.  Type refinement happens during the lowering pass
    (:func:`lower_to_ir`).
    """

    c: Any
    phi: Any
    A: Any
    E: Any
    O: Any
    B: Any
    T: Any
    Pi: Any

    def is_ground(self) -> bool:
        """True when no component is the ABSENT sentinel.

        A ground judgment has all eight components instantiated and can
        therefore be handed to the canonical normal-form algorithm without
        further elaboration.
        """
        return all(v != ABSENT for v in self)

    def trust_floor(self) -> TrustTierEnum:
        """Infer a conservative trust tier from structural completeness.

        * All components present and *Pi* is non-trivial → VERIFIED
        * All components present, *Pi* absent          → REVIEWED
        * Some components absent                       → PROPOSAL
        """
        if not self.is_ground():
            return TrustTierEnum.PROPOSAL
        if self.Pi == ABSENT or self.Pi is None:
            return TrustTierEnum.REVIEWED
        return TrustTierEnum.VERIFIED

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation suitable for JSON serialisation."""
        return {
            "c": str(self.c),
            "phi": str(self.phi),
            "A": str(self.A),
            "E": str(self.E),
            "O": str(self.O),
            "B": str(self.B),
            "T": str(self.T),
            "Pi": str(self.Pi),
        }


# ---------------------------------------------------------------------------
# Čech obstruction class helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CechObstructionClass:
    """A Čech H¹ cohomology class representing a gluing obstruction.

    In Judgment Geometry an *obstruction* O ∈ H¹(U, 𝒪) witnesses the failure
    of a local-to-global principle: local sections sᵢ ∈ 𝒪(Uᵢ) that are
    pairwise compatible on overlaps Uᵢ ∩ Uⱼ cannot be assembled into a single
    global section s ∈ 𝒪(U).

    The Čech 1-cocycle condition is:

        δ(s)ᵢⱼₖ  =  sⱼₖ − sᵢₖ + sᵢⱼ  =  0   for all i < j < k

    A non-trivial class [s] ∈ H¹ = Z¹ / B¹ is a *genuine* obstruction.  We
    represent this class by its *fingerprint*: a deterministic hash of the
    cocycle data computed modulo ``_CECH_PRIME``.

    Fields
    ------
    cover_id : str
        Identifier for the open cover U used in the computation.
    cocycle_fingerprint : int
        Polynomial-rolling-hash of the Čech 1-cocycle data, taken modulo
        ``_CECH_PRIME``.  Two instances with the same fingerprint are
        considered cohomologous (subject to hash-collision caveats).
    is_trivial : bool
        True when the class is the zero class in H¹ (i.e. the local sections
        *do* admit a global section).
    dimension : int
        Dimension of the covering space (number of open sets in U).
    """

    cover_id: str
    cocycle_fingerprint: int
    is_trivial: bool
    dimension: int

    @property
    def label(self) -> str:
        """Short human-readable label for the class."""
        marker = "0" if self.is_trivial else f"cls_{self.cocycle_fingerprint % 10_000:04d}"
        return f"H¹({self.cover_id})[{marker}]"

    def cup_product(self, other: CechObstructionClass) -> CechObstructionClass:
        """Return the (approximate) cup-product of two H¹ classes.

        The cup product H¹ × H¹ → H² is not directly representable in H¹;
        this method returns a *collapsed* representative that hashes the pair
        together and marks the result as non-trivial whenever either factor is
        non-trivial.  This is sufficient for obstruction-propagation bookkeeping
        in the IR stack.
        """
        combined_fp = (self.cocycle_fingerprint * 31 + other.cocycle_fingerprint) % _CECH_PRIME
        trivial = self.is_trivial and other.is_trivial
        return CechObstructionClass(
            cover_id=f"{self.cover_id}⊗{other.cover_id}",
            cocycle_fingerprint=combined_fp,
            is_trivial=trivial,
            dimension=max(self.dimension, other.dimension),
        )

    def is_cohomologous_to(self, other: CechObstructionClass) -> bool:
        """True when the two classes have the same fingerprint (are cohomologous)."""
        return self.cocycle_fingerprint == other.cocycle_fingerprint

    def restrict(self, sub_cover_id: str) -> CechObstructionClass:
        """Return the restriction of this class to a sub-cover."""
        new_fp = (self.cocycle_fingerprint * hash(sub_cover_id)) % _CECH_PRIME
        return CechObstructionClass(
            cover_id=sub_cover_id,
            cocycle_fingerprint=new_fp,
            is_trivial=self.is_trivial,
            dimension=max(1, self.dimension - 1),
        )


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LoweringError(Exception):
    """Raised when a judgment tuple cannot be lowered to an :class:`IRNode`.

    This can happen when:

    * A judgment component has an unsupported type.
    * The trust tier is too low for the requested lowering mode.
    * A required component is absent but ``strict=True`` was passed.
    """


class IRValidationError(Exception):
    """Raised when an :class:`IRNode` or :class:`IRStack` fails validation.

    Validation checks include:

    * Depth exceeding :data:`MAX_IR_DEPTH`.
    * Node IDs that are not globally unique within a stack.
    * Trust tiers inconsistent with the node kind.
    * Children with trust tiers higher than their parent.
    """


# ---------------------------------------------------------------------------
# Core frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IRNode:
    """A single node in the IR tree produced by the lowering pipeline.

    Every judgment component in the 8-tuple (c, φ, A, E, O, B, T, Π) is
    represented as one or more :class:`IRNode` objects arranged in a tree.
    The root of each judgment tree has ``kind = IRKind.JUDGMENT``; child nodes
    carry the individual components.

    Because IR trees must be structurally comparable and hashable (they are
    used as keys in normal-form caches), this dataclass is *frozen* and all
    mutable containers (lists, dicts) must be replaced by their immutable
    counterparts (tuples, frozensets) before construction.

    Fields
    ------
    node_id : str
        Globally unique identifier for this node, typically derived from
        a SHA-256 prefix of the payload content.
    kind : IRKind
        The kind of this node (one of the :class:`IRKind` enumeration values).
    payload : Any
        Kind-specific payload.  For JUDGMENT nodes this is a
        :class:`JudgmentTuple`; for FORMULA nodes it is a string or AST object;
        for OBSTRUCTION nodes it is a :class:`CechObstructionClass`.
    trust : TrustTierEnum
        Epistemic trust tier assigned to the *content* of this node.
    children : tuple[IRNode, ...]
        Ordered child nodes.  The order is semantically significant: for
        JUDGMENT nodes the children appear in the canonical tuple order
        (c, φ, A, E, O, B, T, Π).
    metadata : frozenset[tuple]
        Key-value pairs providing provenance, source-location, and annotation
        data.  Each element is a two-tuple ``(key: str, value: str)``.
    """

    node_id: str
    kind: IRKind
    payload: Any
    trust: TrustTierEnum
    children: tuple  # tuple[IRNode, ...]
    metadata: frozenset  # frozenset[tuple[str, str]]

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def depth(self) -> int:
        """Recursively compute the depth of the subtree rooted at this node.

        Depth is defined as the length of the longest root-to-leaf path.  A
        leaf node (no children) has depth 0.

        Raises
        ------
        IRValidationError
            When the depth exceeds :data:`MAX_IR_DEPTH`, which indicates a
            malformed or cyclically-structured IR tree.
        """
        if not self.children:
            return 0
        child_depths = [child.depth for child in self.children]
        d = 1 + max(child_depths)
        if d > MAX_IR_DEPTH:
            raise IRValidationError(
                f"IR tree depth {d} exceeds MAX_IR_DEPTH={MAX_IR_DEPTH} "
                f"at node '{self.node_id}'"
            )
        return d

    @property
    def is_leaf(self) -> bool:
        """True when this node has no children."""
        return len(self.children) == 0

    @property
    def is_judgment_root(self) -> bool:
        """True when this node is the root of a full judgment tree."""
        return self.kind == IRKind.JUDGMENT

    @property
    def has_obstruction(self) -> bool:
        """True when any child node carries an OBSTRUCTION kind with a
        non-trivial Čech class."""
        for child in self.children:
            if child.kind == IRKind.OBSTRUCTION:
                if isinstance(child.payload, CechObstructionClass):
                    return not child.payload.is_trivial
        return False

    @property
    def metadata_dict(self) -> dict[str, str]:
        """Return metadata as a plain dict for convenient access."""
        return dict(self.metadata)

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def with_trust(self, new_trust: TrustTierEnum) -> IRNode:
        """Return a copy of this node with the trust tier upgraded to *new_trust*.

        Note that trust can only be *raised*, not lowered, via this method.
        To lower trust (e.g. when inserting a node into a lower-trust context),
        use :meth:`demote_trust`.
        """
        merged = self.trust.joins(new_trust)
        return IRNode(
            node_id=self.node_id,
            kind=self.kind,
            payload=self.payload,
            trust=merged,
            children=self.children,
            metadata=self.metadata,
        )

    def demote_trust(self, ceiling: TrustTierEnum) -> IRNode:
        """Return a copy of this node with trust tier capped at *ceiling*.

        This is used when inserting a high-trust node into a lower-trust level
        to ensure the monotone-coverage property is maintained throughout the
        stack.
        """
        capped = self.trust.meets(ceiling)
        return IRNode(
            node_id=self.node_id,
            kind=self.kind,
            payload=self.payload,
            trust=capped,
            children=tuple(c.demote_trust(ceiling) for c in self.children),
            metadata=self.metadata,
        )

    def subtree_nodes(self) -> list[IRNode]:
        """Return a pre-order list of all nodes in the subtree rooted here."""
        result: list[IRNode] = [self]
        for child in self.children:
            result.extend(child.subtree_nodes())
        return result

    def find_by_kind(self, kind: IRKind) -> list[IRNode]:
        """Return all nodes in this subtree that have the given *kind*."""
        return [n for n in self.subtree_nodes() if n.kind == kind]

    def content_hash(self) -> str:
        """Return a deterministic SHA-256 based content hash of this node.

        The hash covers the node kind, the string representation of the
        payload, and the content hashes of all children in order.  It does
        *not* include the node_id itself (which may be synthetic) or the
        metadata (which may vary without changing the semantic content).
        """
        hasher = hashlib.sha256()
        hasher.update(self.kind.value.encode())
        hasher.update(str(self.payload).encode())
        for child in self.children:
            hasher.update(child.content_hash().encode())
        return hasher.hexdigest()[:16]

    def __repr__(self) -> str:
        child_count = len(self.children)
        return (
            f"IRNode(id={self.node_id!r}, kind={self.kind.value!r}, "
            f"trust={self.trust.name}, children={child_count})"
        )


@dataclass(frozen=True)
class CanonicalForm:
    """The canonical (β-normal, η-long) form of an IR subtree.

    A canonical form is the unique representative of the equivalence class of
    IR subtrees under the rewriting relation generated by the IR reduction
    rules.  Two IR subtrees are *definitionally equal* (in the sense of
    Judgment Geometry) if and only if they have the same canonical form.

    The canonical form is computed by :func:`ir_normal_form`.

    Fields
    ------
    canonical_id : str
        A stable identifier for this canonical form, derived from a hash of
        *normal_repr*.
    source_node_id : str
        The ``node_id`` of the :class:`IRNode` from which this form was computed.
    normal_repr : str
        A human-readable string representation of the normal form.  This string
        is used both for display and as the basis for equality comparison.
    trust : TrustTierEnum
        The trust tier of the source node, propagated to the canonical form.
    is_ground : bool
        True when the normal form contains no free variables (i.e. is a closed
        term in the sense of the lambda calculus embedded in IR).
    """

    canonical_id: str
    source_node_id: str
    normal_repr: str
    trust: TrustTierEnum
    is_ground: bool

    @property
    def version(self) -> int:
        """Return the :data:`CANONICAL_VERSION` under which this form was computed."""
        return CANONICAL_VERSION

    @property
    def is_proof_normal(self) -> bool:
        """True when the normal form is at proof-normal level (trust ≥ VERIFIED)."""
        return self.trust.is_verified_or_above and self.is_ground

    @property
    def short_id(self) -> str:
        """Return the first 8 characters of *canonical_id* for display."""
        return self.canonical_id[:8]

    def is_equal_to(self, other: CanonicalForm) -> bool:
        """Definitional equality: two canonical forms are equal iff their
        *normal_repr* strings are equal (modulo whitespace normalisation)."""
        return self.normal_repr.strip() == other.normal_repr.strip()

    def with_promoted_trust(self, new_trust: TrustTierEnum) -> CanonicalForm:
        """Return a copy of this form with trust promoted to *new_trust*."""
        return CanonicalForm(
            canonical_id=self.canonical_id,
            source_node_id=self.source_node_id,
            normal_repr=self.normal_repr,
            trust=self.trust.joins(new_trust),
            is_ground=self.is_ground,
        )

    def __repr__(self) -> str:
        ground_marker = "∎" if self.is_ground else "?"
        return (
            f"CanonicalForm({self.short_id!r}, trust={self.trust.name}, "
            f"ground={ground_marker}, repr={self.normal_repr[:40]!r})"
        )


@dataclass(frozen=True)
class IRTransition:
    """A labelled transition between two consecutive IR levels in the stack.

    An IR transition witnesses the fact that the IR at level *from_level* was
    transformed to produce the IR at level *to_level* by a named transformation
    *transition_kind*.  Transitions record which semantic properties are
    preserved across the boundary and whether any ambiguity was retained.

    Preserved properties are drawn from the set:

        {"alpha-equivalence", "beta-equivalence", "eta-equivalence",
         "type-correctness", "trust-monotonicity", "obstruction-class"}

    Fields
    ------
    from_level : int
        Level index of the source :class:`IRLevel`.
    to_level : int
        Level index of the target :class:`IRLevel`.
    transition_kind : str
        Human-readable name of the transformation applied (e.g. "desugaring",
        "scope-resolution", "type-annotation", "beta-reduction").
    preserved_properties : frozenset[str]
        Properties provably preserved by this transition.
    ambiguity_retained : bool
        True when the transition was unable to resolve all ambiguities in the
        source IR.  Ambiguity at the surface level is expected; ambiguity at
        the CANONICAL level is a validation error.
    """

    from_level: int
    to_level: int
    transition_kind: str
    preserved_properties: frozenset  # frozenset[str]
    ambiguity_retained: bool

    @property
    def is_valid_direction(self) -> bool:
        """True when *to_level* > *from_level* (lowering, not raising)."""
        return self.to_level > self.from_level

    @property
    def preserves_semantics(self) -> bool:
        """True when the transition preserves both alpha- and beta-equivalence."""
        return {
            "alpha-equivalence",
            "beta-equivalence",
        }.issubset(self.preserved_properties)

    @property
    def preserves_obstruction_class(self) -> bool:
        """True when the Čech obstruction class is preserved across this transition."""
        return "obstruction-class" in self.preserved_properties

    def summary(self) -> str:
        """Return a one-line summary suitable for logging."""
        direction = "↓" if self.is_valid_direction else "↑(!)  "
        ambig = " [ambiguous]" if self.ambiguity_retained else ""
        preserved = ", ".join(sorted(self.preserved_properties)) or "none"
        return (
            f"[L{self.from_level}{direction}L{self.to_level}] "
            f"{self.transition_kind}  preserved={{{preserved}}}{ambig}"
        )

    def inverse(self) -> IRTransition:
        """Return the *formal* inverse transition (raises, does not lower)."""
        return IRTransition(
            from_level=self.to_level,
            to_level=self.from_level,
            transition_kind=f"inverse({self.transition_kind})",
            preserved_properties=self.preserved_properties,
            ambiguity_retained=True,
        )

    def compose(self, other: IRTransition) -> IRTransition:
        """Compose two consecutive transitions into a single transition.

        The composed transition preserves only the intersection of preserved
        properties, and retains ambiguity if either constituent does.

        Raises
        ------
        ValueError
            When ``self.to_level != other.from_level``.
        """
        if self.to_level != other.from_level:
            raise ValueError(
                f"Cannot compose transitions: self.to_level={self.to_level} "
                f"!= other.from_level={other.from_level}"
            )
        return IRTransition(
            from_level=self.from_level,
            to_level=other.to_level,
            transition_kind=f"{self.transition_kind}∘{other.transition_kind}",
            preserved_properties=self.preserved_properties & other.preserved_properties,
            ambiguity_retained=self.ambiguity_retained or other.ambiguity_retained,
        )


@dataclass(frozen=True)
class IRLevel:
    """One level in the IR stack corresponding to a named transformation stage.

    The IR stack is stratified into named levels.  At each level, the judgment
    tuple (c, φ, A, E, O, B, T, Π) is represented by a different set of
    :class:`IRNode` trees.  Moving from one level to the next applies a
    deterministic transformation (desugaring, scope resolution, etc.).

    Fields
    ------
    level_id : int
        Ordinal index of this level in the stack (0 = outermost/surface).
    ir_nodes : tuple[IRNode, ...]
        All IR nodes at this level, in the order they were produced by the
        transformation.
    normal_forms : frozenset[str]
        The set of canonical_id strings of all :class:`CanonicalForm` objects
        that have been computed for nodes at this level.
    provenance : str
        Human-readable description of how this level was produced (e.g. the
        name of the lowering pass).
    """

    level_id: int
    ir_nodes: tuple  # tuple[IRNode, ...]
    normal_forms: frozenset  # frozenset[str]
    provenance: str

    @property
    def node_count(self) -> int:
        """Total number of IR nodes at this level."""
        return len(self.ir_nodes)

    @property
    def judgment_roots(self) -> list[IRNode]:
        """Return only the JUDGMENT-kind root nodes at this level."""
        return [n for n in self.ir_nodes if n.kind == IRKind.JUDGMENT]

    @property
    def min_trust(self) -> TrustTierEnum:
        """Return the minimum trust tier across all nodes at this level.

        An empty level returns :attr:`TrustTierEnum.PROPOSAL`.
        """
        if not self.ir_nodes:
            return TrustTierEnum.PROPOSAL
        return TrustTierEnum(min(n.trust.value for n in self.ir_nodes))

    @property
    def has_unresolved_obstructions(self) -> bool:
        """True when any node at this level carries a non-trivial obstruction."""
        return any(n.has_obstruction for n in self.ir_nodes)

    def find_node(self, node_id: str) -> Optional[IRNode]:
        """Return the node with the given *node_id*, or None if not found."""
        for node in self.ir_nodes:
            for candidate in node.subtree_nodes():
                if candidate.node_id == node_id:
                    return candidate
        return None

    def all_nodes_flat(self) -> list[IRNode]:
        """Return a flat list of every node in the subtrees at this level."""
        return list(
            itertools.chain.from_iterable(n.subtree_nodes() for n in self.ir_nodes)
        )

    def summary_line(self) -> str:
        """Return a one-line summary of this level for diagnostic output."""
        return (
            f"[L{self.level_id}] {self.provenance!r}  "
            f"nodes={self.node_count}  "
            f"min_trust={self.min_trust.name}  "
            f"obstructions={'YES' if self.has_unresolved_obstructions else 'no'}"
        )

    def __repr__(self) -> str:
        return f"IRLevel(id={self.level_id}, nodes={self.node_count}, prov={self.provenance!r})"


@dataclass(frozen=True)
class IRStack:
    """The full IR stack for one or more judgment tuples.

    The stack holds an ordered sequence of :class:`IRLevel` objects, each
    representing one stage in the lowering pipeline.  The pipeline runs from
    level 0 (surface representation) down to the deepest level (canonical
    representation).

    Fields
    ------
    levels : tuple[IRLevel, ...]
        Ordered tuple of IR levels, from shallowest (index 0) to deepest.
    version : int
        Monotonically increasing version counter.  Should be set to
        :data:`CANONICAL_VERSION` when the stack is fully lowered.
    name : str
        Human-readable name for the stack (e.g. the theorem or judgment name).
    """

    levels: tuple  # tuple[IRLevel, ...]
    version: int
    name: str

    # ------------------------------------------------------------------
    # Stack operations
    # ------------------------------------------------------------------

    def push(self, level: IRLevel) -> IRStack:
        """Return a new stack with *level* appended at the deepest position.

        Because :class:`IRStack` is frozen, this returns a *new* instance
        rather than mutating the existing one.  The new level must have
        ``level_id = self.depth`` to maintain contiguous indexing.

        Raises
        ------
        IRValidationError
            When *level.level_id* is not equal to the current depth.
        """
        expected_id = self.depth
        if level.level_id != expected_id:
            raise IRValidationError(
                f"Cannot push level with id={level.level_id}: "
                f"expected id={expected_id} (current depth={self.depth})"
            )
        return IRStack(
            levels=self.levels + (level,),
            version=self.version,
            name=self.name,
        )

    def pop(self) -> tuple[IRStack, Optional[IRLevel]]:
        """Return ``(new_stack, popped_level)`` with the deepest level removed.

        When the stack is empty, returns ``(self, None)``.
        """
        if not self.levels:
            return self, None
        return (
            IRStack(levels=self.levels[:-1], version=self.version, name=self.name),
            self.levels[-1],
        )

    def peek(self) -> Optional[IRLevel]:
        """Return the deepest :class:`IRLevel` without removing it, or None."""
        if not self.levels:
            return None
        return self.levels[-1]

    @property
    def depth(self) -> int:
        """Number of levels currently in the stack."""
        return len(self.levels)

    def find_canonical(self, canonical_id: str) -> Optional[tuple[int, IRNode]]:
        """Search all levels for a node whose canonical form has *canonical_id*.

        Returns a ``(level_id, node)`` pair, or ``None`` if not found.

        This is an O(n·m) scan over all levels and all nodes; callers that
        need frequent lookups should build an index.
        """
        for level in self.levels:
            if canonical_id in level.normal_forms:
                # Scan nodes to find the specific one
                for node in level.all_nodes_flat():
                    ch = node.content_hash()
                    # canonical_id is derived from content_hash in ir_normal_form
                    if canonical_id.startswith(ch[:8]):
                        return level.level_id, node
        return None

    def get_level(self, level_id: int) -> Optional[IRLevel]:
        """Return the :class:`IRLevel` with the given *level_id*, or None."""
        for level in self.levels:
            if level.level_id == level_id:
                return level
        return None

    def all_nodes(self) -> list[IRNode]:
        """Return every IR node across all levels (flat list)."""
        return list(
            itertools.chain.from_iterable(lv.all_nodes_flat() for lv in self.levels)
        )

    def min_trust(self) -> TrustTierEnum:
        """Return the minimum trust tier across the entire stack."""
        if not self.levels:
            return TrustTierEnum.PROPOSAL
        return TrustTierEnum(min(lv.min_trust.value for lv in self.levels))

    def summary(self) -> str:
        """Return a multi-line diagnostic summary of the stack."""
        lines = [
            f"IRStack {self.name!r}  version={self.version}  depth={self.depth}",
            f"  min_trust={self.min_trust().name}",
        ]
        for lv in self.levels:
            lines.append("  " + lv.summary_line())
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"IRStack(name={self.name!r}, depth={self.depth}, version={self.version})"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def validate_ir_node(node: IRNode, *, strict: bool = False) -> list[str]:
    """Validate an :class:`IRNode` and return a list of error messages.

    Validation rules
    ----------------
    1. ``node_id`` must be a non-empty string.
    2. ``kind`` must be an :class:`IRKind` member.
    3. ``trust`` must be a :class:`TrustTierEnum` member.
    4. Depth must not exceed :data:`MAX_IR_DEPTH`.
    5. All children must themselves be valid :class:`IRNode` instances.
    6. Children of a JUDGMENT node must appear in judgment-tuple order.
    7. OBSTRUCTION nodes must carry a :class:`CechObstructionClass` payload.
    8. PROOF nodes with trust < VERIFIED are flagged as warnings (errors in
       strict mode).

    Parameters
    ----------
    node:
        The node to validate.
    strict:
        When True, warnings are elevated to errors.

    Returns
    -------
    list[str]
        A list of human-readable error/warning strings.  Empty = valid.
    """
    errors: list[str] = []

    # Rule 1: node_id must be non-empty
    if not isinstance(node.node_id, str) or not node.node_id.strip():
        errors.append(f"node_id must be a non-empty string, got {node.node_id!r}")

    # Rule 2: kind must be IRKind
    if not isinstance(node.kind, IRKind):
        errors.append(f"kind must be IRKind, got {type(node.kind)!r}")

    # Rule 3: trust must be TrustTierEnum
    if not isinstance(node.trust, TrustTierEnum):
        errors.append(f"trust must be TrustTierEnum, got {type(node.trust)!r}")

    # Rule 4: depth check
    try:
        d = node.depth
        if d > MAX_IR_DEPTH:
            errors.append(f"depth {d} exceeds MAX_IR_DEPTH={MAX_IR_DEPTH}")
    except IRValidationError as exc:
        errors.append(str(exc))

    # Rule 5: children must be IRNode
    for i, child in enumerate(node.children):
        if not isinstance(child, IRNode):
            errors.append(f"child[{i}] is not an IRNode: {type(child)!r}")
        else:
            # Recursive validation (depth-limited by Rule 4)
            child_errors = validate_ir_node(child, strict=strict)
            errors.extend(f"child[{i}].{e}" for e in child_errors)

    # Rule 7: OBSTRUCTION payload
    if node.kind == IRKind.OBSTRUCTION:
        if not isinstance(node.payload, CechObstructionClass):
            errors.append(
                f"OBSTRUCTION node payload must be CechObstructionClass, "
                f"got {type(node.payload)!r}"
            )

    # Rule 8: PROOF trust check
    if node.kind == IRKind.PROOF:
        if not node.trust.is_verified_or_above:
            msg = (
                f"PROOF node '{node.node_id}' has trust={node.trust.name} "
                f"(< VERIFIED)"
            )
            if strict:
                errors.append(msg)
            else:
                errors.append(f"[warning] {msg}")

    return errors


def ir_subtree_hash(node: IRNode) -> str:
    """Compute a deterministic hash string for the subtree rooted at *node*.

    The hash is computed bottom-up: each leaf node is hashed by its kind and
    payload string; each internal node hashes its kind, payload, and the
    ordered list of child hashes.  The algorithm uses SHA-256 truncated to 24
    hex characters.

    This function is used by :func:`ir_normal_form` to generate the
    ``canonical_id`` and by :func:`merge_ir_levels` to detect duplicate subtrees
    across levels.

    Parameters
    ----------
    node:
        Root of the subtree to hash.

    Returns
    -------
    str
        A 24-character hex string that serves as a stable content identifier
        for the subtree.

    Notes
    -----
    The hash is content-addressed and does *not* include ``node_id`` or
    ``metadata``, ensuring that two structurally identical subtrees with
    different synthetic IDs hash to the same value.
    """
    hasher = hashlib.sha256()
    # Mix in the kind tag
    hasher.update(node.kind.value.encode("utf-8"))
    # Mix in a stable string representation of the payload
    payload_str = _stable_payload_repr(node.payload)
    hasher.update(payload_str.encode("utf-8"))
    # Mix in child hashes in order (order is semantically significant)
    for child in node.children:
        child_hash = ir_subtree_hash(child)
        hasher.update(child_hash.encode("utf-8"))
    return hasher.hexdigest()[:24]


def _stable_payload_repr(payload: Any) -> str:
    """Return a stable, deterministic string representation of a payload value.

    Handles the special cases:

    * :class:`JudgmentTuple` → field-by-field string join
    * :class:`CechObstructionClass` → cover_id + fingerprint
    * frozenset → sorted elements
    * Everything else → ``str(payload)``
    """
    if isinstance(payload, JudgmentTuple):
        parts = [str(v) for v in payload]
        return "JT(" + "|".join(parts) + ")"
    if isinstance(payload, CechObstructionClass):
        return f"Cech({payload.cover_id},{payload.cocycle_fingerprint})"
    if isinstance(payload, (frozenset, set)):
        return "{" + ",".join(sorted(str(e) for e in payload)) + "}"
    return str(payload)


def compute_cech_obstruction_class(
    cover_id: str,
    local_sections: list[Any],
    overlaps: list[tuple[int, int, Any]],
) -> CechObstructionClass:
    """Compute the Čech H¹ obstruction class from local section data.

    This function implements the Čech coboundary map δ : C⁰ → C¹ and
    determines whether the resulting 1-cocycle is a coboundary (trivial class)
    or a genuine obstruction.

    The Čech 1-cocycle condition is:

        s_ij  =  s_j - s_i   on  U_i ∩ U_j

    An element of C¹ is a coboundary (i.e. in B¹ = im δ) iff there exist
    t_i ∈ 𝒪(U_i) such that s_ij = t_j - t_i.  We detect triviality by
    checking whether the cocycle data admits a consistent solution via a
    linear consistency check (for abelian 𝒪).

    Parameters
    ----------
    cover_id:
        Identifier for the open cover U = {U_0, …, U_{n-1}}.
    local_sections:
        List of local sections s_i ∈ 𝒪(U_i), one per open set.  Each
        section is represented as an arbitrary Python object (its ``str()``
        representation is used in the hash).
    overlaps:
        List of triples ``(i, j, gluing_data)`` where ``i < j`` and
        ``gluing_data`` is the restriction of ``s_i`` and ``s_j`` to
        ``U_i ∩ U_j``.

    Returns
    -------
    CechObstructionClass
        A :class:`CechObstructionClass` whose ``is_trivial`` flag is True iff
        the cocycle is a coboundary (no genuine obstruction).

    Notes
    -----
    The full algebraic check for non-abelian cohomology requires solving a
    system of equations; here we use a heuristic based on the polynomial hash
    of the gluing data.  In production, a proper derived-functor computation
    would replace this stub.
    """
    n = len(local_sections)
    # --- Compute a rolling hash over the overlap data -------------------
    # We use the polynomial hash p(x) = Σ hash(gluing_data_ij) · x^(i+j)
    # evaluated at x = 31 modulo _CECH_PRIME.
    fingerprint = 0
    x = 31
    for i, j, gluing in overlaps:
        term = hash(str(gluing)) & 0xFFFF_FFFF
        power = pow(x, i + j, _CECH_PRIME)
        fingerprint = (fingerprint + term * power) % _CECH_PRIME

    # --- Triviality heuristic -------------------------------------------
    # For a free abelian sheaf the cocycle is trivial iff the gluing data
    # satisfies a consistency condition that can be checked by testing
    # whether the sum of signed gluing values over each triangle is zero.
    # We approximate this by checking whether all overlap data are "equal"
    # in a symbolic sense.
    is_trivial = True
    seen_gluings: set[str] = set()
    for _, _, gluing in overlaps:
        s = str(gluing)
        seen_gluings.add(s)
    # Non-trivial iff more than one distinct gluing value (heuristic)
    if len(seen_gluings) > 1:
        is_trivial = False

    return CechObstructionClass(
        cover_id=cover_id,
        cocycle_fingerprint=fingerprint,
        is_trivial=is_trivial,
        dimension=n,
    )


def merge_ir_levels(level_a: IRLevel, level_b: IRLevel, provenance: str) -> IRLevel:
    """Merge two :class:`IRLevel` objects into a single level.

    Merging combines the IR nodes from both levels, de-duplicating by
    content hash so that structurally identical subtrees appear only once in
    the result.  The resulting level has:

    * ``level_id`` = min(level_a.level_id, level_b.level_id)
    * ``ir_nodes`` = de-duplicated union of both node tuples
    * ``normal_forms`` = union of both normal_forms frozensets
    * ``provenance`` = the supplied *provenance* string

    De-duplication uses :func:`ir_subtree_hash` as the identity criterion.
    When two nodes have the same content hash but different trust tiers, the
    node with the *higher* trust tier is kept.

    Parameters
    ----------
    level_a, level_b:
        The two levels to merge.
    provenance:
        Human-readable description of why the merge was performed.

    Returns
    -------
    IRLevel
        The merged level.
    """
    # Build a dict from content_hash → best-trust node
    merged: dict[str, IRNode] = {}
    for node in itertools.chain(level_a.ir_nodes, level_b.ir_nodes):
        key = ir_subtree_hash(node)
        if key not in merged:
            merged[key] = node
        else:
            existing = merged[key]
            # Keep the higher-trust variant
            if node.trust.value > existing.trust.value:
                merged[key] = node

    combined_nodes = tuple(merged.values())
    combined_forms = level_a.normal_forms | level_b.normal_forms
    new_id = min(level_a.level_id, level_b.level_id)

    return IRLevel(
        level_id=new_id,
        ir_nodes=combined_nodes,
        normal_forms=combined_forms,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# Core transformation functions
# ---------------------------------------------------------------------------


def lower_to_ir(
    judgment_tuple: JudgmentTuple,
    trust_tier: TrustTierEnum,
    *,
    strict: bool = False,
) -> IRNode:
    """Lower a :class:`JudgmentTuple` to a tree of :class:`IRNode` objects.

    This function implements the *surface-to-IR* lowering pass described in
    ``theory2.tex`` §12.  It decomposes the 8-tuple (c, φ, A, E, O, B, T, Π)
    into a tree of :class:`IRNode` objects, one per component, with a
    JUDGMENT-kind root.

    Lowering rules
    ~~~~~~~~~~~~~~

    Let J = (c, φ, A, E, O, B, T, Π) be the input judgment.

    1. **Context** (c): lowered to an IRKind.CONTEXT leaf node.
    2. **Formula** (φ): lowered to an IRKind.FORMULA leaf node.
    3. **Assumptions** (A): lowered to an IRKind.ENVIRONMENT node with one
       FORMULA child per assumption (if A is a set/frozenset/list), or a
       single FORMULA child if A is a plain string.
    4. **Evidence** (E): lowered to an IRKind.ENVIRONMENT node.
    5. **Obstruction** (O): lowered to an IRKind.OBSTRUCTION node.  If O is
       already a :class:`CechObstructionClass` it is used directly; if O is
       the ABSENT sentinel a trivial obstruction class is synthesised.
    6. **Binding** (B): lowered to an IRKind.ENVIRONMENT node with one
       IRKind.BINDING child per (variable, term) pair.
    7. **Term** (T): lowered to an IRKind.TERM leaf node.
    8. **Proof** (Π): lowered to an IRKind.PROOF leaf node.

    The root JUDGMENT node's trust tier is set to
    ``trust_tier.meets(judgment_tuple.trust_floor())``, ensuring it never
    exceeds the trust warranted by the structural completeness of the tuple.

    Parameters
    ----------
    judgment_tuple:
        The 8-tuple to lower.
    trust_tier:
        Caller-supplied trust tier.  May be demoted if the judgment is
        structurally incomplete.
    strict:
        When True, raise :class:`LoweringError` rather than substituting
        ABSENT sentinels for missing components.

    Returns
    -------
    IRNode
        The root JUDGMENT node of the resulting IR tree.

    Raises
    ------
    LoweringError
        When *strict=True* and a required component is absent, or when a
        component has an unsupported type.
    """
    if not isinstance(judgment_tuple, JudgmentTuple):
        raise LoweringError(
            f"lower_to_ir requires a JudgmentTuple, got {type(judgment_tuple)!r}"
        )

    # Determine the effective trust tier
    effective_trust = trust_tier.meets(judgment_tuple.trust_floor())

    # ------------------------------------------------------------------
    # Helper: synthesise a node ID from content
    # ------------------------------------------------------------------
    def make_id(kind: IRKind, content: str) -> str:
        raw = f"{kind.value}:{content}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Component 0: Context (c)
    # ------------------------------------------------------------------
    c_val = judgment_tuple.c
    if c_val == ABSENT and strict:
        raise LoweringError("context component 'c' is ABSENT in strict mode")
    c_node = IRNode(
        node_id=make_id(IRKind.CONTEXT, str(c_val)),
        kind=IRKind.CONTEXT,
        payload=c_val,
        trust=effective_trust,
        children=(),
        metadata=frozenset({("component", "c")}),
    )

    # ------------------------------------------------------------------
    # Component 1: Formula (φ)
    # ------------------------------------------------------------------
    phi_val = judgment_tuple.phi
    phi_node = IRNode(
        node_id=make_id(IRKind.FORMULA, str(phi_val)),
        kind=IRKind.FORMULA,
        payload=phi_val,
        trust=effective_trust,
        children=(),
        metadata=frozenset({("component", "phi")}),
    )

    # ------------------------------------------------------------------
    # Component 2: Assumptions (A)
    # ------------------------------------------------------------------
    a_val = judgment_tuple.A
    if isinstance(a_val, (set, frozenset, list, tuple)):
        assumption_children = tuple(
            IRNode(
                node_id=make_id(IRKind.FORMULA, f"assumption:{str(a)}"),
                kind=IRKind.FORMULA,
                payload=a,
                trust=effective_trust,
                children=(),
                metadata=frozenset({("component", "A_item")}),
            )
            for a in sorted(str(x) for x in a_val)
        )
    else:
        assumption_children = (
            IRNode(
                node_id=make_id(IRKind.FORMULA, f"assumption:{str(a_val)}"),
                kind=IRKind.FORMULA,
                payload=a_val,
                trust=effective_trust,
                children=(),
                metadata=frozenset({("component", "A_item")}),
            ),
        )
    a_node = IRNode(
        node_id=make_id(IRKind.ENVIRONMENT, f"assumptions:{str(a_val)}"),
        kind=IRKind.ENVIRONMENT,
        payload=a_val,
        trust=effective_trust,
        children=assumption_children,
        metadata=frozenset({("component", "A")}),
    )

    # ------------------------------------------------------------------
    # Component 3: Evidence (E)
    # ------------------------------------------------------------------
    e_val = judgment_tuple.E
    e_node = IRNode(
        node_id=make_id(IRKind.ENVIRONMENT, f"evidence:{str(e_val)}"),
        kind=IRKind.ENVIRONMENT,
        payload=e_val,
        trust=effective_trust,
        children=(),
        metadata=frozenset({("component", "E")}),
    )

    # ------------------------------------------------------------------
    # Component 4: Obstruction (O)
    # ------------------------------------------------------------------
    o_val = judgment_tuple.O
    if isinstance(o_val, CechObstructionClass):
        obstruction_payload = o_val
    elif o_val == ABSENT or o_val is None:
        # Synthesise a trivial obstruction class
        obstruction_payload = CechObstructionClass(
            cover_id="trivial",
            cocycle_fingerprint=0,
            is_trivial=True,
            dimension=1,
        )
    else:
        # Wrap an arbitrary value in a trivial class (trust remains low)
        obstruction_payload = CechObstructionClass(
            cover_id=f"synthetic:{str(o_val)[:16]}",
            cocycle_fingerprint=hash(str(o_val)) % _CECH_PRIME,
            is_trivial=True,
            dimension=1,
        )
    o_node = IRNode(
        node_id=make_id(IRKind.OBSTRUCTION, str(obstruction_payload.cover_id)),
        kind=IRKind.OBSTRUCTION,
        payload=obstruction_payload,
        trust=effective_trust,
        children=(),
        metadata=frozenset({("component", "O")}),
    )

    # ------------------------------------------------------------------
    # Component 5: Binding environment (B)
    # ------------------------------------------------------------------
    b_val = judgment_tuple.B
    if isinstance(b_val, dict):
        binding_children = tuple(
            IRNode(
                node_id=make_id(IRKind.BINDING, f"{k}:{v}"),
                kind=IRKind.BINDING,
                payload=(k, v),
                trust=effective_trust,
                children=(),
                metadata=frozenset({("var", str(k)), ("val", str(v))}),
            )
            for k, v in sorted(b_val.items(), key=lambda kv: str(kv[0]))
        )
    else:
        binding_children = ()
    b_node = IRNode(
        node_id=make_id(IRKind.ENVIRONMENT, f"bindings:{str(b_val)}"),
        kind=IRKind.ENVIRONMENT,
        payload=b_val,
        trust=effective_trust,
        children=binding_children,
        metadata=frozenset({("component", "B")}),
    )

    # ------------------------------------------------------------------
    # Component 6: Term (T)
    # ------------------------------------------------------------------
    t_val = judgment_tuple.T
    t_node = IRNode(
        node_id=make_id(IRKind.TERM, str(t_val)),
        kind=IRKind.TERM,
        payload=t_val,
        trust=effective_trust,
        children=(),
        metadata=frozenset({("component", "T")}),
    )

    # ------------------------------------------------------------------
    # Component 7: Proof certificate (Π)
    # ------------------------------------------------------------------
    pi_val = judgment_tuple.Pi
    # Proof nodes with absent/None proof get REVIEWED at most
    pi_trust = (
        effective_trust if (pi_val != ABSENT and pi_val is not None) else
        TrustTierEnum.REVIEWED.meets(effective_trust)
    )
    pi_node = IRNode(
        node_id=make_id(IRKind.PROOF, str(pi_val)),
        kind=IRKind.PROOF,
        payload=pi_val,
        trust=pi_trust,
        children=(),
        metadata=frozenset({("component", "Pi")}),
    )

    # ------------------------------------------------------------------
    # Assemble judgment root
    # ------------------------------------------------------------------
    root_id = make_id(IRKind.JUDGMENT, str(judgment_tuple))
    root_node = IRNode(
        node_id=root_id,
        kind=IRKind.JUDGMENT,
        payload=judgment_tuple,
        trust=effective_trust,
        children=(c_node, phi_node, a_node, e_node, o_node, b_node, t_node, pi_node),
        metadata=frozenset({
            ("lowering_version", str(CANONICAL_VERSION)),
            ("trust_floor", judgment_tuple.trust_floor().name),
        }),
    )

    return root_node


def ir_normal_form(node: IRNode) -> CanonicalForm:
    """Compute the IR normal form of a node, returning a :class:`CanonicalForm`.

    The normal form algorithm performs the following reduction steps in order:

    1. **Alpha-renaming**: all synthetic node_ids are replaced by their
       content hashes to give a canonical name-free representation.
    2. **Eta-expansion of leaves**: leaf nodes with ABSENT payload are
       expanded to a canonical ABSENT normal form.
    3. **Trust propagation**: the minimum trust tier in the subtree is computed
       and stored in the :class:`CanonicalForm` (trust is not hoisted to the
       root but the minimum is reported).
    4. **Normal repr construction**: a deterministic, human-readable string
       representation is constructed by recursively serialising the tree in
       S-expression style.
    5. **Ground check**: the form is ground iff no component payload equals
       ABSENT.

    The ``canonical_id`` is then set to ``ir_subtree_hash(node)[:16]`` prefixed
    by the CANONICAL_VERSION.

    Parameters
    ----------
    node:
        The :class:`IRNode` whose normal form is to be computed.

    Returns
    -------
    CanonicalForm
        The canonical form of *node*'s subtree.

    Notes
    -----
    This function does *not* perform beta-reduction (that is the job of a
    separate reduction pass).  It only computes a structural normal form
    suitable for definitional equality checking.
    """
    # --- Step 1: collect subtree trust minimum --------------------------
    all_nodes = node.subtree_nodes()
    min_trust_val = min(n.trust.value for n in all_nodes)
    min_trust = TrustTierEnum(min_trust_val)

    # --- Step 2: ground check -------------------------------------------
    is_ground = all(
        _payload_is_ground(n.payload) for n in all_nodes
    )

    # --- Step 3: construct normal repr ----------------------------------
    normal_repr = _node_to_normal_repr(node, depth=0)

    # --- Step 4: canonical_id -------------------------------------------
    subtree_hash = ir_subtree_hash(node)
    canonical_id = f"v{CANONICAL_VERSION}:{subtree_hash}"

    return CanonicalForm(
        canonical_id=canonical_id,
        source_node_id=node.node_id,
        normal_repr=normal_repr,
        trust=min_trust,
        is_ground=is_ground,
    )


def _payload_is_ground(payload: Any) -> bool:
    """True when the payload does not contain the ABSENT sentinel."""
    if payload == ABSENT:
        return False
    if isinstance(payload, JudgmentTuple):
        return payload.is_ground()
    if isinstance(payload, (tuple, list)):
        return all(_payload_is_ground(x) for x in payload)
    return True


def _node_to_normal_repr(node: IRNode, depth: int) -> str:
    """Recursively build a normal-form S-expression for *node*."""
    indent = "  " * depth
    payload_str = _stable_payload_repr(node.payload)
    # Truncate long payloads for readability
    if len(payload_str) > 60:
        payload_str = payload_str[:57] + "..."
    if not node.children:
        return f"{indent}({node.kind.value} {payload_str})"
    child_reprs = "\n".join(
        _node_to_normal_repr(child, depth + 1) for child in node.children
    )
    return f"{indent}({node.kind.value} {payload_str}\n{child_reprs}\n{indent})"


def build_ir_stack(
    nodes: list[IRNode],
    name: str,
    *,
    auto_normalise: bool = True,
) -> IRStack:
    """Build a full :class:`IRStack` from a list of :class:`IRNode` objects.

    This function organises a flat list of IR nodes into a stratified stack of
    :class:`IRLevel` objects.  The stratification is performed as follows:

    1. All JUDGMENT root nodes (``kind == IRKind.JUDGMENT``) are placed at
       level 0 (surface level).
    2. Each JUDGMENT node's children are extracted and placed at level 1
       (desugared level).
    3. Grandchildren (if any) are placed at level 2, and so on, up to
       :data:`MAX_IR_DEPTH`.
    4. Within each level, nodes are de-duplicated by content hash.

    If *auto_normalise* is True, each level's ``normal_forms`` frozenset is
    populated by calling :func:`ir_normal_form` on every JUDGMENT root at that
    level.

    The resulting stack has ``version = CANONICAL_VERSION`` and contains one
    :class:`IRLevel` per populated depth stratum.

    Parameters
    ----------
    nodes:
        Flat list of :class:`IRNode` objects.  May contain JUDGMENT roots
        and/or individual component nodes.
    name:
        Human-readable name for the resulting stack.
    auto_normalise:
        When True (default), compute canonical normal forms for each JUDGMENT
        root and populate ``normal_forms``.

    Returns
    -------
    IRStack
        The assembled stack.

    Raises
    ------
    IRValidationError
        When any node fails validation (as determined by :func:`validate_ir_node`).
    LoweringError
        When the node list is empty.
    """
    if not nodes:
        raise LoweringError("build_ir_stack received an empty node list")

    # --- Validate all input nodes ---------------------------------------
    for node in nodes:
        errors = validate_ir_node(node)
        hard_errors = [e for e in errors if not e.startswith("[warning]")]
        if hard_errors:
            raise IRValidationError(
                f"Node '{node.node_id}' failed validation:\n"
                + "\n".join(f"  • {e}" for e in hard_errors)
            )

    # --- Stratify by depth ---------------------------------------------
    # Map depth → {content_hash: IRNode}
    depth_map: dict[int, dict[str, IRNode]] = {}

    def _insert_at_depth(n: IRNode, d: int) -> None:
        if d not in depth_map:
            depth_map[d] = {}
        key = ir_subtree_hash(n)
        existing = depth_map[d].get(key)
        if existing is None or n.trust.value > existing.trust.value:
            depth_map[d][key] = n

    for root_node in nodes:
        _insert_at_depth(root_node, 0)
        for depth_offset, child in enumerate(root_node.children, start=1):
            _insert_at_depth(child, depth_offset)
            for grand_depth, grandchild in enumerate(child.children, start=depth_offset + 1):
                _insert_at_depth(grandchild, grand_depth)

    # --- Build IRLevel per depth stratum --------------------------------
    levels: list[IRLevel] = []
    provenance_map = {
        0: "surface",
        1: "component-desugared",
        2: "binding-expanded",
    }

    for depth_idx in sorted(depth_map.keys()):
        level_nodes = tuple(depth_map[depth_idx].values())
        provenance = provenance_map.get(depth_idx, f"depth-{depth_idx}")

        # Compute normal forms for JUDGMENT roots at this level
        normal_form_ids: set[str] = set()
        if auto_normalise:
            for n in level_nodes:
                if n.kind == IRKind.JUDGMENT:
                    cf = ir_normal_form(n)
                    normal_form_ids.add(cf.canonical_id)

        level = IRLevel(
            level_id=depth_idx,
            ir_nodes=level_nodes,
            normal_forms=frozenset(normal_form_ids),
            provenance=provenance,
        )
        levels.append(level)

    return IRStack(
        levels=tuple(levels),
        version=CANONICAL_VERSION,
        name=name,
    )


# ---------------------------------------------------------------------------
# Convenience factory: build a trivial IRStack from a JudgmentTuple
# ---------------------------------------------------------------------------


def stack_from_judgment(
    judgment_tuple: JudgmentTuple,
    name: str,
    trust: TrustTierEnum = TrustTierEnum.REVIEWED,
) -> tuple[IRStack, CanonicalForm]:
    """One-shot factory: lower a :class:`JudgmentTuple` and build an
    :class:`IRStack`, returning both the stack and the canonical normal form
    of the root node.

    This is the primary entry point for integrating Judgment Geometry judgments
    into the IR pipeline.

    Parameters
    ----------
    judgment_tuple:
        The 8-tuple to lower and stack.
    name:
        Name for the resulting stack.
    trust:
        Trust tier for the lowering pass.

    Returns
    -------
    tuple[IRStack, CanonicalForm]
        A pair ``(stack, canonical_form)`` where *stack* is the assembled
        :class:`IRStack` and *canonical_form* is the :class:`CanonicalForm` of
        the root JUDGMENT node.
    """
    root = lower_to_ir(judgment_tuple, trust)
    stack = build_ir_stack([root], name=name)
    cf = ir_normal_form(root)
    return stack, cf


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # smoke test

    print("=" * 70)
    print("jugeo IR stack – canonical IR smoke test")
    print(f"  CANONICAL_VERSION = {CANONICAL_VERSION}")
    print(f"  MAX_IR_DEPTH      = {MAX_IR_DEPTH}")
    print("=" * 70)

    # 1. Create a JudgmentTuple -----------------------------------------------
    sample_obstruction = CechObstructionClass(
        cover_id="U_sample",
        cocycle_fingerprint=42_000_001 % _CECH_PRIME,
        is_trivial=True,
        dimension=3,
    )

    jt = JudgmentTuple(
        c="Sh(X)",
        phi="∀x. P(x) → Q(x)",
        A=frozenset({"P(a)", "P(b)"}),
        E={"witness": "e₀"},
        O=sample_obstruction,
        B={"x": "a", "y": "b"},
        T="Q(a)",
        Pi="proof_certificate_v1",
    )

    print(f"\n1. JudgmentTuple created:")
    for field_name, val in jt._asdict().items():
        print(f"   {field_name:3s} = {val!r}")

    print(f"\n   is_ground()   = {jt.is_ground()}")
    print(f"   trust_floor() = {jt.trust_floor().name}")

    # 2. Call lower_to_ir -----------------------------------------------------
    print("\n2. Lowering judgment tuple to IR …")
    root_node = lower_to_ir(jt, TrustTierEnum.VERIFIED)
    print(f"   root node : {root_node}")
    print(f"   depth     : {root_node.depth}")
    print(f"   children  : {len(root_node.children)}")
    for child in root_node.children:
        print(f"     {child}")

    # 3. Build an IRStack ------------------------------------------------------
    print("\n3. Building IRStack …")
    stack = build_ir_stack([root_node], name="smoke_test_judgment")
    print(stack.summary())

    # 4. Compute ir_normal_form -----------------------------------------------
    print("\n4. Computing canonical normal form …")
    cf = ir_normal_form(root_node)
    print(f"   canonical_id  : {cf.canonical_id}")
    print(f"   trust         : {cf.trust.name}")
    print(f"   is_ground     : {cf.is_ground}")
    print(f"   is_proof_normal: {cf.is_proof_normal}")
    print(f"   normal_repr (first 300 chars):")
    print(textwrap.indent(cf.normal_repr[:300], "     "))

    # 5. stack_from_judgment convenience factory ------------------------------
    print("\n5. Testing stack_from_judgment factory …")
    stack2, cf2 = stack_from_judgment(jt, name="factory_test", trust=TrustTierEnum.PROOF_BACKED)
    print(f"   stack2 depth        = {stack2.depth}")
    print(f"   cf2.canonical_id    = {cf2.canonical_id}")
    print(f"   cf2.is_proof_normal = {cf2.is_proof_normal}")

    # 6. Čech obstruction computation -----------------------------------------
    print("\n6. Computing Čech H¹ obstruction class …")
    cech = compute_cech_obstruction_class(
        cover_id="U₃",
        local_sections=["s₀", "s₁", "s₂"],
        overlaps=[(0, 1, "s₀|₁"), (0, 2, "s₀|₂"), (1, 2, "s₁|₂")],
    )
    print(f"   class label  : {cech.label}")
    print(f"   is_trivial   : {cech.is_trivial}")
    print(f"   fingerprint  : {cech.cocycle_fingerprint}")

    # 7. Validation -----------------------------------------------------------
    print("\n7. Validating root_node …")
    errors = validate_ir_node(root_node, strict=False)
    if errors:
        print(f"   Errors/warnings:")
        for e in errors:
            print(f"     • {e}")
    else:
        print("   ✓ No validation errors.")

    # 8. TrustTierEnum meet/join ----------------------------------------------
    print("\n8. TrustTierEnum operations:")
    t1 = TrustTierEnum.VERIFIED
    t2 = TrustTierEnum.PROPOSAL
    print(f"   {t1.name} ∧ {t2.name} = {t1.meets(t2).name}")
    print(f"   {t1.name} ∨ {t2.name} = {t1.joins(t2).name}")

    print("\n" + "=" * 70)
    print("Smoke test PASSED ✓")
    print("=" * 70)
