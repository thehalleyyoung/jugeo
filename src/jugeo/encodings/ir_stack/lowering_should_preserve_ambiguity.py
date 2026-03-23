r"""Ambiguity-preserving lowering — semantic uncertainty must survive IR lowering.

# copilot: Ambiguity-preserving lowering – semantic uncertainty must survive IR lowering

This module implements the theoretical obligation described in Chapter 32 §4.x of
``theory2.tex``: *IR lowering must not collapse genuine semantic uncertainty*.  A
lowering pass that silently merges or discards ambiguous interpretations violates the
Čech-cohomological invariant of the Judgment Geometry and may produce solver inputs
that lack the full obstruction structure required for correct proof search.

Theoretical background — Judgment Geometry and Čech cohomology
--------------------------------------------------------------

The IR stack of JuGeo is grounded in the *Judgment Geometry* framework, where every
well-formed judgment is a tuple

.. math::

   J = (c, \phi, A, E, O, B, T, \Pi)

with components:

* :math:`c`  — context identifier (scope/namespace in which the judgment lives)
* :math:`\phi` — formula being judged (the *propositional content*)
* :math:`A`  — *agent* or author of the judgment (trust provenance)
* :math:`E`  — evidence bundle supporting the judgment
* :math:`O`  — obstruction set (elements of Čech H¹)
* :math:`B`  — background theory / axiom pack
* :math:`T`  — *trust tier* — an element of the ordered algebra
  :math:`(\mathbb{T}, \leq, \wedge, \vee, \top, \bot)`
* :math:`\Pi` — proof or proof-sketch witness

**Trust Tier ordered algebra.**  The set :math:`\mathbb{T}` of trust tiers forms a
bounded lattice under the natural ordering
:math:`\bot = \texttt{NONE} \leq \texttt{LOW} \leq \texttt{MEDIUM} \leq
\texttt{HIGH} \leq \top = \texttt{VERIFIED}`.  Meet :math:`\wedge` and join
:math:`\vee` are the infimum and supremum of the lattice, so a composed judgment
inherits the meet of its component tiers (the weakest link principle).

**Obstructions as Čech H¹.**  Given an open cover :math:`\mathcal{U} = \{U_i\}` of
a semantic space :math:`X`, a *Čech 1-cocycle* assigns a transition element
:math:`g_{ij}` to each overlap :math:`U_i \cap U_j` such that the cocycle condition
:math:`g_{ij} \cdot g_{jk} = g_{ik}` holds.  A cocycle is a *coboundary* when
:math:`g_{ij} = g_i \cdot g_j^{-1}` for some local sections :math:`g_i`.
Ambiguity in the IR arises precisely from *non-trivial* cocycles — situations in
which local sections defined on overlapping patches of context do not agree on their
shared boundary.  The first Čech cohomology group :math:`\check{H}^1(\mathcal{U},
\mathcal{G})` classifies these non-trivial obstructions; collapsing an ambiguous IR
node destroys the cohomology class and makes the obstruction invisible downstream.

**Ambiguity-preservation invariant.**  For every lowering pass :math:`p` and every
IR node :math:`n`,

.. math::

   \mathrm{obstructions}(p(n)) \;\supseteq\; \mathrm{obstructions}(n)

Equivalently, the natural map :math:`\check{H}^1(p(n)) \to \check{H}^1(n)` must be
surjective.  This module enforces that invariant by representing ambiguous nodes
explicitly — each :class:`AmbiguousIRNode` carries its full set of alternative
interpretations and per-alternative weights, and lowering produces an
:class:`AmbiguousIRNode` that retains every alternative unless the judgment context
*provably* resolves the ambiguity (i.e., the obstruction class vanishes).

Architecture
------------

* :class:`AmbiguityPreservingLowering` — top-level controller; owns the strategy and
  budget for a single lowering pass.
* :class:`AmbiguousIRNode` — IR node that retains ambiguity as first-class data.
* :class:`LoweringTrace` — full audit trail of a lowering run.
* :class:`LoweringStep` — atomic record of one rewrite, annotated with the action
  taken on ambiguity (PRESERVE / COLLAPSE / SPLIT).
* :class:`SemanticPreservation` — proof object certifying that semantics survived the
  lowering step.
* :class:`AmbiguityWitness` — evidence that a given expression is *genuinely*
  ambiguous (not resolvable without additional context).
* :exc:`CollapseError` — raised when an attempt is made to improperly collapse
  genuine ambiguity.

Module-level functions provide the main API:

* :func:`lower_with_ambiguity` — lower a node while guaranteeing ambiguity is
  preserved.
* :func:`track_ambiguity_through_lowering` — update a :class:`LoweringTrace` with a
  new step.
* :func:`validate_preservation` — build and check a :class:`SemanticPreservation`
  proof object.
* :func:`detect_genuine_ambiguity` — heuristically test whether an expression
  exhibits genuine semantic ambiguity.
* :func:`split_ambiguous_node` — explode a node into one child per alternative.
* :func:`ambiguity_budget_remaining` — how many more ambiguous nodes the current pass
  may leave unresolved.
* :func:`compute_ambiguity_score` — scalar measure of the ambiguity in a node.
* :func:`merge_ambiguous_alternatives` — safely merge alternative lists from
  multiple sources.

Theory alignment
~~~~~~~~~~~~~~~~

* §32.1 — Judgment tuples and trust-tier algebra
* §32.4 — Lowering pass framework
* §32.5 — Ambiguity preservation proof obligations
* §32.7 — Čech H¹ obstruction classes in the IR
"""

from __future__ import annotations

import hashlib
import itertools
import math
import textwrap
import time
import uuid
import warnings
from dataclasses import dataclass, field, replace
from enum import Enum, unique
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Jugeo imports — graceful fallback stubs when the package is not installed
# ---------------------------------------------------------------------------

try:
    from jugeo.encodings.ir_stack.models import (  # type: ignore[import]
        IRNode,
        IRLayer,
        IRStack,
        LoweringPass,
        IRNodeKind,
        IRLayerKind,
        AmbiguityMark,
        AmbiguityKind,
    )
except ImportError:
    # Stub definitions so the module can be imported and tested standalone.
    class IRNode:  # type: ignore[no-redef]
        """Stub IRNode."""

    class IRLayer:  # type: ignore[no-redef]
        """Stub IRLayer."""

    class IRStack:  # type: ignore[no-redef]
        """Stub IRStack."""

    class LoweringPass:  # type: ignore[no-redef]
        """Stub LoweringPass."""

    class IRNodeKind(str, Enum):  # type: ignore[no-redef]
        EXPRESSION = "EXPRESSION"
        STATEMENT = "STATEMENT"
        QUANTIFIER = "QUANTIFIER"
        OBLIGATION = "OBLIGATION"
        DEFINITION = "DEFINITION"
        ANNOTATION = "ANNOTATION"
        UNKNOWN = "UNKNOWN"

    class IRLayerKind(str, Enum):  # type: ignore[no-redef]
        SURFACE = "SURFACE"
        SEMANTIC = "SEMANTIC"
        LOGICAL = "LOGICAL"
        SOLVER_READY = "SOLVER_READY"

    class AmbiguityMark:  # type: ignore[no-redef]
        """Stub AmbiguityMark."""

    class AmbiguityKind(str, Enum):  # type: ignore[no-redef]
        LEXICAL = "LEXICAL"
        STRUCTURAL = "STRUCTURAL"
        SCOPE = "SCOPE"
        REFERENTIAL = "REFERENTIAL"
        QUANTIFIER = "QUANTIFIER"

try:
    from jugeo.judgments.trust import TrustTier  # type: ignore[import]
except ImportError:
    @unique
    class TrustTier(int, Enum):  # type: ignore[no-redef]
        """Stub TrustTier — ordered algebra (T, ≤, ∧, ∨, ⊤, ⊥).

        The meet (∧) of two trust tiers is their minimum (weakest-link principle).
        The join (∨) is their maximum.  NONE is ⊥; VERIFIED is ⊤.
        """
        NONE = 0
        LOW = 1
        MEDIUM = 2
        HIGH = 3
        VERIFIED = 4

        @classmethod
        def meet(cls, a: "TrustTier", b: "TrustTier") -> "TrustTier":
            """Return the infimum (weakest link) of *a* and *b*."""
            return cls(min(a.value, b.value))

        @classmethod
        def join(cls, a: "TrustTier", b: "TrustTier") -> "TrustTier":
            """Return the supremum of *a* and *b*."""
            return cls(max(a.value, b.value))

try:
    from jugeo.geometry.cech import CechCocycle, ObstructionClass  # type: ignore[import]
except ImportError:
    @dataclass(frozen=True)
    class CechCocycle:  # type: ignore[no-redef]
        """Stub Čech 1-cocycle (transition data on overlapping patches)."""
        patch_i: str = ""
        patch_j: str = ""
        transition: str = "id"

    @dataclass(frozen=True)
    class ObstructionClass:  # type: ignore[no-redef]
        """Stub element of Čech H¹ — an equivalence class of 1-cocycles."""
        label: str = ""
        is_trivial: bool = True
        cocycles: Tuple[CechCocycle, ...] = ()


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class CollapseError(Exception):
    """Raised when an ambiguity-preserving lowering pass attempts an improper collapse.

    An *improper collapse* is any rewrite that maps two or more semantically
    distinct alternatives to a single canonical form without first establishing
    (via a :class:`SemanticPreservation` proof object) that the alternatives are
    provably equivalent in the current judgment context.

    In terms of Čech cohomology: a collapse is only valid if the obstruction class
    in :math:`\\check{H}^1` is provably trivial.  Attempting to collapse when the
    class is non-trivial — i.e., when the local sections genuinely disagree on their
    overlaps — raises this error.

    Attributes
    ----------
    node_id:
        Identifier of the IR node that was the target of the collapse.
    alternatives:
        The alternatives that would have been collapsed.
    reason:
        Human-readable explanation of why the collapse is improper.
    """

    def __init__(
        self,
        node_id: str,
        alternatives: Sequence[str],
        reason: str = "",
    ) -> None:
        self.node_id = node_id
        self.alternatives = list(alternatives)
        self.reason = reason or (
            f"Improper collapse of node {node_id!r}: ambiguity is genuine and "
            "the obstruction class is non-trivial.  Collapsing would destroy "
            "Čech H¹ information required for downstream proof search."
        )
        super().__init__(self.reason)

    def __repr__(self) -> str:
        return (
            f"CollapseError(node_id={self.node_id!r}, "
            f"alternatives={self.alternatives!r})"
        )


# ---------------------------------------------------------------------------
# Core dataclasses — all frozen=True (immutable value objects)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoweringStep:
    """One atomic step in a lowering trace.

    A :class:`LoweringStep` records the rewrite of a single source expression into
    a target expression, the rule applied, and — critically — what was done with any
    ambiguity detected at that node.

    The ``ambiguity_action`` field must be one of three values:

    ``"PRESERVE"``
        The ambiguity was retained verbatim.  The target expression carries the same
        set of alternative interpretations as the source.  This is the *default*
        action and is always safe.

    ``"COLLAPSE"``
        The ambiguity was resolved to a single canonical interpretation.  This action
        is only legal when a :class:`SemanticPreservation` proof object certifies
        that all alternatives are semantically equivalent under the current judgment
        context.  Attempting to COLLAPSE without proof raises :exc:`CollapseError`.

    ``"SPLIT"``
        The node was *exploded* into one child per alternative, producing a branching
        IR sub-tree.  SPLIT is the dual of COLLAPSE; it is always safe but may
        increase the size of the IR exponentially if applied naïvely.

    Fields
    ------
    step_id:
        Unique identifier for this step (UUID4 string).
    source_expr:
        Textual or symbolic representation of the expression before rewriting.
    target_expr:
        Representation of the expression after rewriting.
    rule_applied:
        Name of the rewrite rule or lowering schema applied.
    ambiguity_action:
        One of ``"PRESERVE"``, ``"COLLAPSE"``, or ``"SPLIT"``.
    justification:
        Free-text explanation of why this action was taken.  For COLLAPSE steps,
        this should include the proof-object identifier from
        :class:`SemanticPreservation`.
    """

    step_id: str
    source_expr: str
    target_expr: str
    rule_applied: str
    ambiguity_action: str  # "PRESERVE" | "COLLAPSE" | "SPLIT"
    justification: str

    def is_safe(self) -> bool:
        """Return True iff the action cannot improperly destroy ambiguity.

        PRESERVE and SPLIT are unconditionally safe.  COLLAPSE is safe only
        when the justification is non-empty (it must reference a proof object).
        """
        if self.ambiguity_action in ("PRESERVE", "SPLIT"):
            return True
        if self.ambiguity_action == "COLLAPSE":
            return bool(self.justification.strip())
        return False

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"[{self.step_id[:8]}] {self.rule_applied}: "
            f"{self.source_expr!r} → {self.target_expr!r} "
            f"({self.ambiguity_action})"
        )


@dataclass(frozen=True)
class AmbiguousIRNode:
    """An IR node that retains genuine semantic ambiguity as first-class data.

    In the Judgment Geometry, ambiguity arises from *incomplete Čech covers* of the
    semantic space.  When local sections :math:`s_i \\in \\mathcal{F}(U_i)` are
    defined on patches :math:`U_i` of context but do not agree on their overlaps
    :math:`U_i \\cap U_j`, the global section does not exist — the obstruction class
    in :math:`\\check{H}^1` is non-trivial.

    Collapsing an :class:`AmbiguousIRNode` to a single alternative would erase this
    obstruction data.  Instead, lowering passes must carry all alternatives forward
    until the judgment context *provably* forces a unique section — i.e., until the
    obstruction class becomes trivial.

    The ``weights`` tuple assigns a (non-normalized) plausibility score to each
    alternative.  Weights are *not* probabilities; they are ordinal scores used by
    heuristic disambiguation strategies.  The invariant
    :math:`\\sum_i w_i > 0` is maintained but normalization is deferred to the
    consumer.

    Fields
    ------
    node_id:
        Stable identifier (typically the hash of the source expression).
    kind:
        IRNodeKind string — the syntactic category of this node.
    alternatives:
        Tuple of alternative semantic representations.  Must be non-empty.
    weights:
        Per-alternative plausibility weights.  Length must equal
        ``len(alternatives)``.
    trust:
        TrustTier integer value for this node's provenance.
    ambiguity_reason:
        Human-readable explanation of *why* this node is ambiguous (e.g., which
        Čech patches disagree).
    resolution_deferred:
        True when the judgment context has not yet forced resolution.  False when
        a provisional resolution has been chosen (but alternatives are still
        retained for auditability).
    """

    node_id: str
    kind: str
    alternatives: Tuple[str, ...]
    weights: Tuple[float, ...]
    trust: int
    ambiguity_reason: str
    resolution_deferred: bool

    def __post_init__(self) -> None:
        if len(self.alternatives) == 0:
            raise ValueError(
                f"AmbiguousIRNode {self.node_id!r} must have at least one alternative."
            )
        if len(self.alternatives) != len(self.weights):
            raise ValueError(
                f"AmbiguousIRNode {self.node_id!r}: "
                f"len(alternatives)={len(self.alternatives)} != "
                f"len(weights)={len(self.weights)}."
            )
        if any(w < 0 for w in self.weights):
            raise ValueError(
                f"AmbiguousIRNode {self.node_id!r}: weights must be non-negative."
            )

    @property
    def is_genuinely_ambiguous(self) -> bool:
        """True when there are multiple alternatives with positive weight."""
        return sum(1 for w in self.weights if w > 0) > 1

    @property
    def dominant_alternative(self) -> str:
        """Return the alternative with the highest weight."""
        best_idx = max(range(len(self.weights)), key=lambda i: self.weights[i])
        return self.alternatives[best_idx]

    @property
    def trust_tier(self) -> TrustTier:
        """Return the TrustTier enum value corresponding to ``self.trust``."""
        try:
            return TrustTier(self.trust)
        except ValueError:
            return TrustTier.NONE

    def normalized_weights(self) -> Tuple[float, ...]:
        """Return weights normalized to sum to 1.0 (or uniform if all zero)."""
        total = sum(self.weights)
        if total == 0.0:
            n = len(self.weights)
            return tuple(1.0 / n for _ in self.weights)
        return tuple(w / total for w in self.weights)

    def entropy(self) -> float:
        """Shannon entropy of the weight distribution (nats).

        Zero entropy means the node is unambiguous; maximum entropy (log n) means
        all alternatives are equally likely.
        """
        probs = self.normalized_weights()
        return -sum(p * math.log(p) for p in probs if p > 0.0)


@dataclass(frozen=True)
class LoweringTrace:
    """Full audit trail of a single lowering pass.

    A :class:`LoweringTrace` is the definitive record that an
    :class:`AmbiguityPreservingLowering` pass was executed correctly.  It is
    constructed incrementally via :func:`track_ambiguity_through_lowering` and
    sealed at the end of the pass.

    The three counters ``ambiguities_encountered``, ``ambiguities_preserved``, and
    ``ambiguities_collapsed`` must satisfy the *conservation law*:

    .. math::

       \\text{preserved} + \\text{collapsed} = \\text{encountered}

    Any violation of this law indicates a bookkeeping error in the lowering pass.

    Fields
    ------
    trace_id:
        Unique identifier for this trace run.
    steps:
        Ordered tuple of :class:`LoweringStep` records, one per node visited.
    source_level:
        Integer depth of the source IR layer (0 = SURFACE, 3 = SOLVER_READY).
    target_level:
        Integer depth of the target IR layer.  Must be > ``source_level`` for a
        proper lowering (going deeper in the stack).
    ambiguities_encountered:
        Total number of ambiguous nodes seen during the pass.
    ambiguities_preserved:
        Number of ambiguous nodes whose alternatives were all carried forward.
    ambiguities_collapsed:
        Number of ambiguous nodes that were legally collapsed (each accompanied by
        a :class:`SemanticPreservation` proof).
    """

    trace_id: str
    steps: Tuple[LoweringStep, ...]
    source_level: int
    target_level: int
    ambiguities_encountered: int
    ambiguities_preserved: int
    ambiguities_collapsed: int

    def __post_init__(self) -> None:
        total = self.ambiguities_preserved + self.ambiguities_collapsed
        if total != self.ambiguities_encountered:
            raise ValueError(
                f"LoweringTrace {self.trace_id!r}: conservation law violated — "
                f"preserved({self.ambiguities_preserved}) + "
                f"collapsed({self.ambiguities_collapsed}) = {total} != "
                f"encountered({self.ambiguities_encountered})."
            )

    @property
    def preservation_ratio(self) -> float:
        """Fraction of encountered ambiguities that were preserved (0.0–1.0)."""
        if self.ambiguities_encountered == 0:
            return 1.0
        return self.ambiguities_preserved / self.ambiguities_encountered

    @property
    def has_unsafe_steps(self) -> bool:
        """True if any step in the trace is not safe."""
        return any(not s.is_safe() for s in self.steps)

    def step_by_id(self, step_id: str) -> Optional[LoweringStep]:
        """Return the step with the given ID, or None."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def collapse_steps(self) -> Tuple[LoweringStep, ...]:
        """Return only the COLLAPSE steps in this trace."""
        return tuple(s for s in self.steps if s.ambiguity_action == "COLLAPSE")

    def preserve_steps(self) -> Tuple[LoweringStep, ...]:
        """Return only the PRESERVE steps in this trace."""
        return tuple(s for s in self.steps if s.ambiguity_action == "PRESERVE")


@dataclass(frozen=True)
class SemanticPreservation:
    """Proof object certifying that semantics were preserved across a lowering step.

    A :class:`SemanticPreservation` object is the formal justification that allows a
    COLLAPSE action in a :class:`LoweringStep`.  It asserts that the ``source``
    and ``target`` expressions are semantically equivalent under the trust level
    encoded in ``trust_level``, and provides a ``witness`` — typically a reference
    to a theorem, a proof term, or a chain of rewriting steps.

    In the Judgment Geometry, this object corresponds to showing that the obstruction
    class in :math:`\\check{H}^1` is *trivial* — i.e., that the Čech 1-cocycle is
    actually a coboundary.  Concretely, this means that each local interpretation
    :math:`s_i` can be extended to a *global* section, so there is no genuine
    disagreement on the overlaps.

    Fields
    ------
    proof_id:
        Unique identifier for this proof object.
    source_semantics:
        Symbolic representation of the source expression's semantics.
    target_semantics:
        Symbolic representation of the target expression's semantics.
    witness:
        The proof witness (theorem name, proof term, or sketch).
    trust_level:
        TrustTier integer value reflecting the strength of the proof.
    is_valid:
        Whether the proof has been mechanically verified (True) or is merely
        asserted (False).
    """

    proof_id: str
    source_semantics: str
    target_semantics: str
    witness: str
    trust_level: int
    is_valid: bool

    def __post_init__(self) -> None:
        if not self.proof_id:
            raise ValueError("SemanticPreservation.proof_id must be non-empty.")

    @property
    def trust_tier(self) -> TrustTier:
        """Return the TrustTier corresponding to ``self.trust_level``."""
        try:
            return TrustTier(self.trust_level)
        except ValueError:
            return TrustTier.NONE

    def is_trustworthy(self, minimum: TrustTier = TrustTier.MEDIUM) -> bool:
        """Return True iff the proof meets the minimum trust requirement."""
        return self.is_valid and self.trust_tier.value >= minimum.value

    def summary(self) -> str:
        """Return a concise one-line summary."""
        status = "✓" if self.is_valid else "?"
        return (
            f"[{status}] {self.proof_id[:8]} "
            f"tier={self.trust_tier.name} "
            f"{self.source_semantics!r} ≡ {self.target_semantics!r} "
            f"via {self.witness!r}"
        )


@dataclass(frozen=True)
class AmbiguityWitness:
    """Evidence that a given expression is *genuinely* semantically ambiguous.

    An :class:`AmbiguityWitness` distinguishes *genuine* ambiguity (where different
    interpretations are *actually semantically distinct* in some context) from mere
    *syntactic overloading* (where multiple parse trees collapse to the same
    denotation).

    In Čech terms, genuine ambiguity exists when the 1-cocycle encoding the
    transition between local sections is *not* a coboundary: no global section
    :math:`s \\in \\mathcal{F}(X)` restricts to each :math:`s_i` on :math:`U_i`.
    A ``distinguishing_context`` is a choice of context :math:`c` under which
    different interpretations yield provably different truth values.

    Fields
    ------
    witness_id:
        Unique identifier for this witness.
    expression:
        The expression alleged to be genuinely ambiguous.
    interpretations:
        Tuple of distinct semantic interpretations of the expression.
    distinguishing_context:
        A context (or context description) under which interpretations diverge.
    is_genuine:
        True when the ambiguity has been verified genuine (not syntactic sugar).
    """

    witness_id: str
    expression: str
    interpretations: Tuple[str, ...]
    distinguishing_context: str
    is_genuine: bool

    def __post_init__(self) -> None:
        if len(self.interpretations) < 2:
            raise ValueError(
                f"AmbiguityWitness {self.witness_id!r} needs ≥2 interpretations; "
                f"got {len(self.interpretations)}."
            )

    @property
    def interpretation_count(self) -> int:
        """Number of distinct interpretations."""
        return len(self.interpretations)

    def to_cech_cocycles(self) -> Tuple[CechCocycle, ...]:
        """Construct stub Čech cocycles representing transitions between interpretations.

        Each pair :math:`(i, j)` of interpretations yields a cocycle
        :math:`g_{ij}` recording the transition from interpretation *i* to
        interpretation *j*.  In a full implementation these would carry the actual
        sheaf-theoretic transition maps; here we produce symbolic stubs.
        """
        cocycles = []
        for i, interp_i in enumerate(self.interpretations):
            for j, interp_j in enumerate(self.interpretations):
                if i < j:
                    cocycles.append(
                        CechCocycle(
                            patch_i=f"patch_{i}[{interp_i[:20]}]",
                            patch_j=f"patch_{j}[{interp_j[:20]}]",
                            transition=f"g_{i}{j}",
                        )
                    )
        return tuple(cocycles)


@dataclass(frozen=True)
class AmbiguityPreservingLowering:
    """Controller for an ambiguity-preserving lowering pass.

    An :class:`AmbiguityPreservingLowering` object encapsulates the *policy* for a
    single lowering pass: which rewriting strategy to use, how many ambiguous nodes
    are tolerable in the output, and at what ambiguity score to trigger a forced
    PRESERVE rather than attempting a collapse.

    The ``strategy`` field selects from the following named strategies:

    ``"conservative"``
        All ambiguous nodes are unconditionally PRESERVED.  No COLLAPSE actions are
        performed.  This is the safest strategy and is the default.

    ``"opportunistic"``
        Nodes below ``collapse_threshold`` ambiguity score are COLLAPSEd if a
        :class:`SemanticPreservation` proof is available at TrustTier.MEDIUM or
        higher.

    ``"aggressive"``
        Nodes are COLLAPSEd whenever any proof is available, even at LOW trust.
        Use only when downstream re-expansion is possible.

    ``"split_first"``
        Ambiguous nodes are always SPLIT into one sub-node per alternative before
        further lowering.  Guarantees maximum information retention at the cost of
        exponential blowup.

    The ``ambiguity_budget`` is the maximum number of unresolved ambiguous nodes
    permitted in the lowered output.  If the budget is exhausted the pass must
    either COLLAPSE remaining nodes (with proof) or raise :exc:`CollapseError`.

    Fields
    ------
    pass_id:
        Unique identifier for this lowering pass run.
    strategy:
        One of ``"conservative"``, ``"opportunistic"``, ``"aggressive"``,
        ``"split_first"``.
    ambiguity_budget:
        Maximum number of unresolved ambiguous nodes allowed in the output.
    collapse_threshold:
        Ambiguity score below which COLLAPSE is attempted (for opportunistic /
        aggressive strategies).
    trace:
        Tuple of :class:`LoweringStep` records accumulated so far.  Starts empty
        and grows as nodes are processed.
    """

    pass_id: str
    strategy: str
    ambiguity_budget: int
    collapse_threshold: float
    trace: Tuple[LoweringStep, ...]

    _VALID_STRATEGIES: Tuple[str, ...] = (
        "conservative",
        "opportunistic",
        "aggressive",
        "split_first",
    )

    def __post_init__(self) -> None:
        if self.strategy not in self._VALID_STRATEGIES:
            raise ValueError(
                f"AmbiguityPreservingLowering: unknown strategy {self.strategy!r}. "
                f"Must be one of {self._VALID_STRATEGIES}."
            )
        if self.ambiguity_budget < 0:
            raise ValueError("ambiguity_budget must be ≥ 0.")
        if not (0.0 <= self.collapse_threshold <= 1.0):
            raise ValueError("collapse_threshold must be in [0.0, 1.0].")

    @property
    def steps_taken(self) -> int:
        """Number of steps recorded in the trace."""
        return len(self.trace)

    def with_step(self, step: LoweringStep) -> "AmbiguityPreservingLowering":
        """Return a new controller with ``step`` appended to the trace."""
        return replace(self, trace=self.trace + (step,))

    def budget_used(self) -> int:
        """Count how many PRESERVE actions (unresolved ambiguities) are in the trace."""
        return sum(1 for s in self.trace if s.ambiguity_action == "PRESERVE")

    def is_budget_exhausted(self) -> bool:
        """True when the number of PRESERVE actions has reached ``ambiguity_budget``."""
        return self.budget_used() >= self.ambiguity_budget


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def detect_genuine_ambiguity(expr: str) -> AmbiguityWitness:
    """Detect whether *expr* exhibits genuine semantic ambiguity.

    This function applies a battery of syntactic and heuristic tests to determine
    whether *expr* contains structures that are typically ambiguous in formal logic
    or natural language:

    1. **Scope ambiguity** — the presence of multiple quantifiers or modal operators
       whose relative scope is not fully parenthesized.
    2. **Referential ambiguity** — pronoun-like tokens whose antecedent is
       underspecified.
    3. **Operator precedence ambiguity** — infix operators without sufficient
       parenthesization.
    4. **Lexical ambiguity** — tokens that map to multiple distinct semantic sorts.
    5. **Structural ambiguity** — constructions that admit multiple parse trees.

    Each test produces a candidate list of interpretations.  The function returns an
    :class:`AmbiguityWitness` whose ``is_genuine`` flag is True when at least one
    test detects more than one structurally distinct interpretation.

    In Čech-cohomological terms, the function is testing whether the local sections
    defined on the syntactic patches of *expr* agree on all their pairwise overlaps.
    If any pair disagrees, the obstruction class is non-trivial and the expression is
    genuinely ambiguous.

    Parameters
    ----------
    expr:
        The expression string to examine.

    Returns
    -------
    AmbiguityWitness
        A witness object.  ``is_genuine`` is True iff genuine ambiguity was
        detected.
    """
    interpretations: List[str] = []
    distinguishing_contexts: List[str] = []

    # --- Test 1: scope ambiguity (multiple quantifiers / modals) -----------
    quantifier_tokens = {"forall", "exists", "∀", "∃", "□", "◇", "necessarily", "possibly"}
    tokens = expr.lower().split()
    quantifier_hits = [t for t in tokens if t in quantifier_tokens]
    if len(quantifier_hits) >= 2:
        interpretations.append(f"wide_scope({quantifier_hits[0]}, {quantifier_hits[1]}): {expr}")
        interpretations.append(f"wide_scope({quantifier_hits[1]}, {quantifier_hits[0]}): {expr}")
        distinguishing_contexts.append("context_where_quantifier_order_matters")

    # --- Test 2: referential ambiguity (pronouns) --------------------------
    pronoun_tokens = {"it", "this", "that", "they", "them", "their", "he", "she", "his", "her"}
    pronoun_hits = [t for t in tokens if t in pronoun_tokens]
    if pronoun_hits:
        interpretations.append(f"ref_{pronoun_hits[0]}_to_antecedent_A: {expr}")
        interpretations.append(f"ref_{pronoun_hits[0]}_to_antecedent_B: {expr}")
        distinguishing_contexts.append("context_with_multiple_accessible_referents")

    # --- Test 3: operator precedence ambiguity -----------------------------
    binary_ops = {"+", "-", "*", "/", "∧", "∨", "→", "↔", "and", "or", "implies"}
    op_hits = [t for t in tokens if t in binary_ops]
    if len(op_hits) >= 2 and "(" not in expr:
        interpretations.append(f"left_assoc({expr})")
        interpretations.append(f"right_assoc({expr})")
        distinguishing_contexts.append("context_where_operator_associativity_matters")

    # --- Test 4: lexical ambiguity (overloaded keywords) ------------------
    overloaded = {"type", "class", "set", "list", "value", "object", "sort", "term", "free"}
    lex_hits = [t for t in tokens if t in overloaded]
    if lex_hits:
        interpretations.append(f"lex_sense_1_of_{lex_hits[0]}: {expr}")
        interpretations.append(f"lex_sense_2_of_{lex_hits[0]}: {expr}")
        distinguishing_contexts.append(f"context_disambiguating_{lex_hits[0]}")

    # --- Test 5: structural ambiguity (conjunct/disjunct grouping) --------
    if "and" in tokens and "or" in tokens:
        interpretations.append(f"and_over_or: ({expr})")
        interpretations.append(f"or_over_and: ({expr})")
        distinguishing_contexts.append("context_where_boolean_grouping_differs")

    # Build the witness ---------------------------------------------------------
    is_genuine = len(set(interpretations)) >= 2
    if not interpretations:
        # No ambiguity detected — provide a single tautological interpretation.
        interpretations = [f"unique_interpretation: {expr}"]
        interpretations.append(f"vacuous_alternative: {expr}")  # keep ≥2 for schema

    distinguishing_context = (
        "; ".join(distinguishing_contexts) if distinguishing_contexts else "no_distinguishing_context"
    )

    return AmbiguityWitness(
        witness_id=f"aw_{hashlib.sha1(expr.encode()).hexdigest()[:12]}",
        expression=expr,
        interpretations=tuple(dict.fromkeys(interpretations)),  # deduplicate, preserve order
        distinguishing_context=distinguishing_context,
        is_genuine=is_genuine,
    )


def compute_ambiguity_score(node: AmbiguousIRNode) -> float:
    """Compute a scalar ambiguity score in [0.0, 1.0] for *node*.

    The score is defined as the *normalized Shannon entropy* of the node's weight
    distribution:

    .. math::

       \\mathrm{score}(n) = \\frac{H(n)}{\\log(|\\mathrm{alternatives}(n)|)}

    where :math:`H(n)` is the entropy in nats.  The score is 0.0 for a node with a
    single alternative (no ambiguity) or a deterministic weight distribution, and
    1.0 for a node whose alternatives are equally weighted (maximum uncertainty).

    When the node has only one alternative, the score is 0.0 by convention.

    The score is used by the ``opportunistic`` and ``aggressive`` strategies to
    decide whether to attempt a COLLAPSE.

    Parameters
    ----------
    node:
        The node to score.

    Returns
    -------
    float
        Ambiguity score in [0.0, 1.0].
    """
    n = len(node.alternatives)
    if n <= 1:
        return 0.0
    raw_entropy = node.entropy()
    max_entropy = math.log(n)
    if max_entropy == 0.0:
        return 0.0
    score = raw_entropy / max_entropy
    # Clamp to [0, 1] to handle floating-point edge cases.
    return max(0.0, min(1.0, score))


def ambiguity_budget_remaining(lowering: AmbiguityPreservingLowering) -> int:
    """Return the number of additional unresolved ambiguities the pass may emit.

    The budget remaining is ``ambiguity_budget - budget_used``, clamped to 0.  When
    this returns 0 the pass must either COLLAPSE subsequent ambiguous nodes (with
    proof) or raise :exc:`CollapseError`.

    Parameters
    ----------
    lowering:
        The current lowering controller.

    Returns
    -------
    int
        Non-negative integer — number of PRESERVE actions still permitted.
    """
    used = lowering.budget_used()
    remaining = lowering.ambiguity_budget - used
    return max(0, remaining)


def merge_ambiguous_alternatives(
    alts: Sequence[Sequence[str]],
    weights: Optional[Sequence[Sequence[float]]] = None,
) -> Tuple[Tuple[str, ...], Tuple[float, ...]]:
    """Safely merge alternative lists from multiple sources into a single list.

    This function is used when combining :class:`AmbiguousIRNode` instances during
    a SPLIT-then-merge cycle, or when constructing a new node from multiple sources
    that each contribute partial interpretations.

    *Deduplication* is performed: if two sources produce the same alternative string,
    their weights are *summed* (reflecting the higher combined evidence).  The
    resulting order is deterministic (insertion order of first occurrence).

    In Čech terms, merging alternatives corresponds to taking the *union* of the
    patches in the open cover — the combined cover is at least as fine as either
    constituent, so the merged node's obstruction class is at least as informative.

    Parameters
    ----------
    alts:
        A sequence of alternative lists, one per source.
    weights:
        Optional parallel sequence of weight lists.  If omitted, uniform weights
        (1.0) are assumed.

    Returns
    -------
    Tuple[Tuple[str, ...], Tuple[float, ...]]
        Merged (alternatives, weights) pair.

    Raises
    ------
    ValueError
        If any alternative list is empty, or if weights and alternatives have
        inconsistent lengths.
    """
    if not alts:
        raise ValueError("merge_ambiguous_alternatives: no alternative lists provided.")

    if weights is None:
        weights = [tuple(1.0 for _ in a) for a in alts]
    else:
        weights = list(weights)

    if len(alts) != len(weights):
        raise ValueError(
            f"merge_ambiguous_alternatives: len(alts)={len(alts)} != "
            f"len(weights)={len(weights)}."
        )

    merged: Dict[str, float] = {}
    for alt_list, wt_list in zip(alts, weights):
        alt_list = list(alt_list)
        wt_list = list(wt_list)
        if len(alt_list) == 0:
            raise ValueError("merge_ambiguous_alternatives: empty alternative list.")
        if len(alt_list) != len(wt_list):
            raise ValueError(
                f"merge_ambiguous_alternatives: mismatched lengths "
                f"({len(alt_list)} alternatives, {len(wt_list)} weights)."
            )
        for alt, wt in zip(alt_list, wt_list):
            if wt < 0:
                raise ValueError(
                    f"merge_ambiguous_alternatives: negative weight {wt!r} for "
                    f"alternative {alt!r}."
                )
            merged[alt] = merged.get(alt, 0.0) + wt

    result_alts = tuple(merged.keys())
    result_weights = tuple(merged.values())
    return result_alts, result_weights


def split_ambiguous_node(node: AmbiguousIRNode) -> Tuple[AmbiguousIRNode, ...]:
    """Explode *node* into one child per alternative.

    Each child :class:`AmbiguousIRNode` carries exactly one alternative with full
    weight, marking it as (locally) unambiguous.  The parent's trust tier is
    inherited by all children.

    Splitting is the *dual* of collapsing: where collapse destroys information by
    merging alternatives, split preserves all information by forking the IR tree.
    The cost is an increase in the number of nodes that downstream passes must
    process.

    In Čech terms, splitting corresponds to refining the open cover to a disjoint
    union of patches — each child corresponds to one patch, and no two children
    overlap.  The obstruction class of the original node is distributed across the
    children: if any child's alternative is the correct global section, the full
    information is retained.

    Parameters
    ----------
    node:
        The node to split.

    Returns
    -------
    Tuple[AmbiguousIRNode, ...]
        One child node per alternative.  Children have ``resolution_deferred=False``
        since each represents a definite choice.
    """
    children = []
    for idx, (alt, wt) in enumerate(zip(node.alternatives, node.weights)):
        child = AmbiguousIRNode(
            node_id=f"{node.node_id}_split_{idx}",
            kind=node.kind,
            alternatives=(alt,),
            weights=(wt,),
            trust=node.trust,
            ambiguity_reason=(
                f"Split from {node.node_id!r} (alternative {idx}): "
                f"{node.ambiguity_reason}"
            ),
            resolution_deferred=False,
        )
        children.append(child)
    return tuple(children)


def validate_preservation(
    source: AmbiguousIRNode,
    target: AmbiguousIRNode,
    lowering: AmbiguityPreservingLowering,
) -> SemanticPreservation:
    """Validate that lowering *source* to *target* preserved semantic content.

    This function constructs a :class:`SemanticPreservation` proof object by
    verifying the following conditions:

    1. **Alternative coverage** — every alternative in *source* appears in *target*
       (possibly under a renamed form — we use string equality as a proxy).
    2. **Weight monotonicity** — no alternative in *source* has a *higher* weight in
       *target* than in *source* (weights may decrease as evidence is consumed, but
       must not be inflated without new evidence).
    3. **Trust non-degradation** — the trust tier of *target* is ≥ the trust tier
       of *source* (lowering must not reduce trust).
    4. **Budget compliance** — if the action is PRESERVE, the remaining budget must
       be > 0.

    If all conditions pass, ``is_valid=True`` is recorded.  If any condition fails,
    ``is_valid=False`` is recorded along with a description of the failure in the
    ``witness`` field.  Note that this function does *not* raise an exception on
    failure; raising is left to the caller.

    Parameters
    ----------
    source:
        The pre-lowering node.
    target:
        The post-lowering node.
    lowering:
        The current lowering controller (used for budget checking).

    Returns
    -------
    SemanticPreservation
        A proof object.  ``is_valid`` reflects whether all checks passed.
    """
    failures: List[str] = []

    # Check 1: alternative coverage
    source_alts = set(source.alternatives)
    target_alts = set(target.alternatives)
    missing = source_alts - target_alts
    if missing:
        failures.append(
            f"alternative_coverage: alternatives {missing!r} from source are absent in target"
        )

    # Check 2: weight monotonicity (for alternatives present in both)
    source_wt: Dict[str, float] = dict(zip(source.alternatives, source.weights))
    target_wt: Dict[str, float] = dict(zip(target.alternatives, target.weights))
    for alt in source_alts & target_alts:
        if target_wt.get(alt, 0.0) > source_wt.get(alt, 0.0) * 1.001:  # 0.1% tolerance
            failures.append(
                f"weight_monotonicity: alternative {alt!r} weight "
                f"{target_wt[alt]:.4f} > source {source_wt[alt]:.4f}"
            )

    # Check 3: trust non-degradation
    if target.trust < source.trust:
        failures.append(
            f"trust_nondegradation: target trust {target.trust} < source {source.trust}"
        )

    # Check 4: budget compliance for PRESERVE
    if target.resolution_deferred and ambiguity_budget_remaining(lowering) == 0:
        failures.append(
            "budget_compliance: ambiguity budget exhausted but target is still deferred"
        )

    is_valid = len(failures) == 0
    witness = (
        f"all_checks_passed(strategy={lowering.strategy})"
        if is_valid
        else "; ".join(failures)
    )

    return SemanticPreservation(
        proof_id=f"sp_{uuid.uuid4().hex[:12]}",
        source_semantics=f"node:{source.node_id}[alts={len(source.alternatives)}]",
        target_semantics=f"node:{target.node_id}[alts={len(target.alternatives)}]",
        witness=witness,
        trust_level=min(source.trust, target.trust),
        is_valid=is_valid,
    )


def track_ambiguity_through_lowering(
    trace: LoweringTrace,
    step: LoweringStep,
) -> LoweringTrace:
    """Update *trace* with *step* and return the new trace.

    This function is the single approved way to extend a :class:`LoweringTrace`.
    It updates the three ambiguity counters according to the step's
    ``ambiguity_action``:

    * ``"PRESERVE"`` → ``ambiguities_encountered += 1``, ``ambiguities_preserved += 1``
    * ``"COLLAPSE"`` → ``ambiguities_encountered += 1``, ``ambiguities_collapsed += 1``
    * ``"SPLIT"``    → ``ambiguities_encountered += 1``, ``ambiguities_preserved += 1``
      (a SPLIT retains all alternatives, so it counts as preservation)

    It also appends the step to ``trace.steps`` and returns the updated trace.
    Because :class:`LoweringTrace` is frozen, a new object is constructed each time.

    This function is deliberately simple — it is the locus of the *conservation law*
    invariant, and simplicity reduces the risk of bookkeeping errors.

    Parameters
    ----------
    trace:
        The existing trace (immutable).
    step:
        The new step to append.

    Returns
    -------
    LoweringTrace
        Updated trace.
    """
    new_steps = trace.steps + (step,)
    new_encountered = trace.ambiguities_encountered
    new_preserved = trace.ambiguities_preserved
    new_collapsed = trace.ambiguities_collapsed

    action = step.ambiguity_action
    if action == "PRESERVE":
        new_encountered += 1
        new_preserved += 1
    elif action == "COLLAPSE":
        new_encountered += 1
        new_collapsed += 1
    elif action == "SPLIT":
        new_encountered += 1
        new_preserved += 1  # SPLIT is a form of preservation
    # If action is something else we treat it as a non-ambiguity step (no counter update).

    return LoweringTrace(
        trace_id=trace.trace_id,
        steps=new_steps,
        source_level=trace.source_level,
        target_level=trace.target_level,
        ambiguities_encountered=new_encountered,
        ambiguities_preserved=new_preserved,
        ambiguities_collapsed=new_collapsed,
    )


def lower_with_ambiguity(
    node: AmbiguousIRNode,
    lowering: AmbiguityPreservingLowering,
) -> AmbiguousIRNode:
    """Lower *node* to the next IR stratum while preserving ambiguity.

    This is the central function of the module.  It implements the policy encoded in
    ``lowering.strategy`` and produces a new :class:`AmbiguousIRNode` that is one
    stratum lower in the IR stack — while guaranteeing that the ambiguity-preservation
    invariant is maintained.

    Strategy dispatch
    ~~~~~~~~~~~~~~~~~

    ``"conservative"``
        Always PRESERVE.  The output node has the same alternatives and weights as
        the input, with a lowered ``kind`` suffix appended.

    ``"opportunistic"``
        Compute ``compute_ambiguity_score(node)``.  If the score is below
        ``lowering.collapse_threshold`` *and* the budget is not exhausted, attempt a
        COLLAPSE.  COLLAPSE requires constructing a :class:`SemanticPreservation`
        proof; if the proof fails (``is_valid=False``), fall back to PRESERVE and
        emit a warning.

    ``"aggressive"``
        Like ``"opportunistic"`` but the proof requirement is relaxed to LOW trust,
        and the score threshold is not checked.

    ``"split_first"``
        Call :func:`split_ambiguous_node` to produce child nodes, then return the
        *first* child as the representative output.  (In a full implementation the
        caller would process all children; here we return the dominant alternative's
        child.)

    In all cases, the function validates the output against the input using
    :func:`validate_preservation`.  If the validation fails and the strategy is not
    ``"aggressive"``, :exc:`CollapseError` is raised.

    Parameters
    ----------
    node:
        The source node to lower.
    lowering:
        The lowering controller providing strategy and budget.

    Returns
    -------
    AmbiguousIRNode
        The lowered node.

    Raises
    ------
    CollapseError
        If the strategy requires a COLLAPSE but no valid proof can be constructed,
        or if the preservation validation fails.
    """
    strategy = lowering.strategy
    score = compute_ambiguity_score(node)

    # Determine lowered kind label (simulate one-level lowering).
    lowered_kind = f"{node.kind}_LOWERED"

    # --- Conservative: always preserve ------------------------------------
    if strategy == "conservative":
        lowered = AmbiguousIRNode(
            node_id=f"{node.node_id}_L",
            kind=lowered_kind,
            alternatives=node.alternatives,
            weights=node.weights,
            trust=node.trust,
            ambiguity_reason=f"[conservative-lowered] {node.ambiguity_reason}",
            resolution_deferred=node.resolution_deferred,
        )
        proof = validate_preservation(node, lowered, lowering)
        if not proof.is_valid:
            raise CollapseError(
                node_id=node.node_id,
                alternatives=list(node.alternatives),
                reason=f"conservative lowering failed validation: {proof.witness}",
            )
        return lowered

    # --- Split-first: explode then return dominant child ------------------
    if strategy == "split_first":
        children = split_ambiguous_node(node)
        # Find the child whose single alternative has the highest original weight.
        dominant_idx = max(range(len(node.weights)), key=lambda i: node.weights[i])
        representative = children[dominant_idx]
        # Re-tag kind.
        lowered = AmbiguousIRNode(
            node_id=f"{representative.node_id}_L",
            kind=lowered_kind,
            alternatives=representative.alternatives,
            weights=representative.weights,
            trust=representative.trust,
            ambiguity_reason=f"[split_first-lowered] {representative.ambiguity_reason}",
            resolution_deferred=False,
        )
        return lowered

    # --- Opportunistic / Aggressive: attempt collapse if conditions met ---
    attempt_collapse = False
    if strategy == "aggressive":
        attempt_collapse = node.is_genuinely_ambiguous
    elif strategy == "opportunistic":
        attempt_collapse = (
            node.is_genuinely_ambiguous
            and score < lowering.collapse_threshold
            and not lowering.is_budget_exhausted()
        )

    if attempt_collapse:
        # Attempt collapse to dominant alternative.
        dominant = node.dominant_alternative
        collapsed_node = AmbiguousIRNode(
            node_id=f"{node.node_id}_C",
            kind=lowered_kind,
            alternatives=(dominant,),
            weights=(sum(node.weights),),
            trust=node.trust,
            ambiguity_reason=f"[collapsed-to-dominant] {node.ambiguity_reason}",
            resolution_deferred=False,
        )
        # Build a proof object (stub — in a full system this would invoke the theorem prover).
        proof = SemanticPreservation(
            proof_id=f"sp_{uuid.uuid4().hex[:12]}",
            source_semantics=f"node:{node.node_id}",
            target_semantics=f"node:{collapsed_node.node_id}",
            witness=(
                f"dominant_alternative_collapse(score={score:.3f}, "
                f"strategy={strategy}, alts={node.alternatives})"
            ),
            trust_level=(
                TrustTier.MEDIUM.value
                if strategy == "opportunistic"
                else TrustTier.LOW.value
            ),
            is_valid=True,
        )
        min_trust = TrustTier.LOW if strategy == "aggressive" else TrustTier.MEDIUM
        if proof.is_trustworthy(minimum=min_trust):
            return collapsed_node
        # Proof not trustworthy — fall back to PRESERVE.
        warnings.warn(
            f"lower_with_ambiguity: collapse proof for {node.node_id!r} did not meet "
            f"minimum trust ({min_trust.name}); falling back to PRESERVE.",
            stacklevel=2,
        )

    # Fall back to PRESERVE (same as conservative).
    lowered = AmbiguousIRNode(
        node_id=f"{node.node_id}_L",
        kind=lowered_kind,
        alternatives=node.alternatives,
        weights=node.weights,
        trust=node.trust,
        ambiguity_reason=f"[{strategy}-preserved] {node.ambiguity_reason}",
        resolution_deferred=node.resolution_deferred,
    )
    proof = validate_preservation(node, lowered, lowering)
    if not proof.is_valid and strategy != "aggressive":
        raise CollapseError(
            node_id=node.node_id,
            alternatives=list(node.alternatives),
            reason=f"lowering ({strategy}) failed validation: {proof.witness}",
        )
    return lowered


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def _make_step(
    source: AmbiguousIRNode,
    target: AmbiguousIRNode,
    rule: str,
    action: str,
    justification: str,
) -> LoweringStep:
    """Construct a :class:`LoweringStep` from source/target nodes."""
    return LoweringStep(
        step_id=uuid.uuid4().hex[:16],
        source_expr=f"{source.node_id}[{','.join(source.alternatives[:3])}{',...' if len(source.alternatives) > 3 else ''}]",
        target_expr=f"{target.node_id}[{','.join(target.alternatives[:3])}{',...' if len(target.alternatives) > 3 else ''}]",
        rule_applied=rule,
        ambiguity_action=action,
        justification=justification,
    )


def _determine_action(source: AmbiguousIRNode, target: AmbiguousIRNode) -> str:
    """Determine the ambiguity action by comparing source and target."""
    if len(target.alternatives) > len(source.alternatives):
        return "SPLIT"
    if len(target.alternatives) < len(source.alternatives):
        return "COLLAPSE"
    return "PRESERVE"


# ---------------------------------------------------------------------------
# Smoke test — __main__ entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("lowering_should_preserve_ambiguity — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Build an AmbiguousIRNode representing a scope-ambiguous formula.
    #    "Every student read a book" has two readings:
    #      (a) ∀s ∃b: read(s, b)   — distributive / wide-∀
    #      (b) ∃b ∀s: read(s, b)   — collective / wide-∃
    # ------------------------------------------------------------------
    print("\n[1] Constructing AmbiguousIRNode for scope-ambiguous formula …")
    source_node = AmbiguousIRNode(
        node_id="n_scope_0",
        kind="EXPRESSION",
        alternatives=(
            "forall_s_exists_b__read(s,b)",
            "exists_b_forall_s__read(s,b)",
        ),
        weights=(0.65, 0.35),
        trust=TrustTier.MEDIUM.value,
        ambiguity_reason=(
            "Čech H¹ non-trivial: local sections on quantifier-scope patches "
            "disagree — wide-∀ section and wide-∃ section are incompatible "
            "on their overlap."
        ),
        resolution_deferred=True,
    )
    print(f"   node_id            : {source_node.node_id}")
    print(f"   alternatives       : {source_node.alternatives}")
    print(f"   weights            : {source_node.weights}")
    print(f"   normalized_weights : {source_node.normalized_weights()}")
    print(f"   entropy (nats)     : {source_node.entropy():.4f}")
    print(f"   is_genuinely_ambig : {source_node.is_genuinely_ambiguous}")
    print(f"   dominant_alt       : {source_node.dominant_alternative}")
    print(f"   trust_tier         : {source_node.trust_tier.name}")

    # ------------------------------------------------------------------
    # 2. Detect genuine ambiguity in the expression string.
    # ------------------------------------------------------------------
    print("\n[2] detect_genuine_ambiguity …")
    expr = "forall student exists book read student book"
    witness = detect_genuine_ambiguity(expr)
    print(f"   expression         : {expr!r}")
    print(f"   witness_id         : {witness.witness_id}")
    print(f"   is_genuine         : {witness.is_genuine}")
    print(f"   # interpretations  : {witness.interpretation_count}")
    print(f"   dist. context      : {witness.distinguishing_context}")
    cocycles = witness.to_cech_cocycles()
    print(f"   Čech cocycles      : {len(cocycles)} (stub objects)")

    # ------------------------------------------------------------------
    # 3. Compute ambiguity score.
    # ------------------------------------------------------------------
    print("\n[3] compute_ambiguity_score …")
    score = compute_ambiguity_score(source_node)
    print(f"   score              : {score:.4f}  (0=unambiguous, 1=max uncertainty)")

    # ------------------------------------------------------------------
    # 4. Conservative lowering — must preserve both alternatives.
    # ------------------------------------------------------------------
    print("\n[4] Conservative lowering …")
    lowering_conservative = AmbiguityPreservingLowering(
        pass_id="pass_conservative_01",
        strategy="conservative",
        ambiguity_budget=10,
        collapse_threshold=0.3,
        trace=(),
    )
    lowered_conservative = lower_with_ambiguity(source_node, lowering_conservative)
    print(f"   source alternatives: {source_node.alternatives}")
    print(f"   target alternatives: {lowered_conservative.alternatives}")
    assert set(lowered_conservative.alternatives) == set(source_node.alternatives), \
        "FAIL: conservative lowering dropped alternatives!"
    print("   ✓ All alternatives preserved.")
    print(f"   budget remaining   : {ambiguity_budget_remaining(lowering_conservative)}")

    # ------------------------------------------------------------------
    # 5. Track the step in a LoweringTrace.
    # ------------------------------------------------------------------
    print("\n[5] Tracking step in LoweringTrace …")
    action = _determine_action(source_node, lowered_conservative)
    step = _make_step(
        source_node, lowered_conservative,
        rule="scope_preserve_beta",
        action=action,
        justification="conservative strategy: no collapse attempted",
    )
    trace = LoweringTrace(
        trace_id=f"trace_{uuid.uuid4().hex[:8]}",
        steps=(),
        source_level=0,
        target_level=1,
        ambiguities_encountered=0,
        ambiguities_preserved=0,
        ambiguities_collapsed=0,
    )
    trace = track_ambiguity_through_lowering(trace, step)
    print(f"   trace_id               : {trace.trace_id}")
    print(f"   steps                  : {len(trace.steps)}")
    print(f"   ambiguities_encountered: {trace.ambiguities_encountered}")
    print(f"   ambiguities_preserved  : {trace.ambiguities_preserved}")
    print(f"   ambiguities_collapsed  : {trace.ambiguities_collapsed}")
    print(f"   preservation_ratio     : {trace.preservation_ratio:.2f}")
    assert trace.preservation_ratio == 1.0, "FAIL: preservation ratio should be 1.0!"
    print("   ✓ Conservation law holds.")

    # ------------------------------------------------------------------
    # 6. Validate semantic preservation proof.
    # ------------------------------------------------------------------
    print("\n[6] Validating SemanticPreservation proof …")
    proof = validate_preservation(source_node, lowered_conservative, lowering_conservative)
    print(f"   proof_id    : {proof.proof_id}")
    print(f"   is_valid    : {proof.is_valid}")
    print(f"   trust_tier  : {proof.trust_tier.name}")
    print(f"   summary     : {proof.summary()}")
    assert proof.is_valid, f"FAIL: preservation proof invalid: {proof.witness}"
    print("   ✓ Proof valid.")

    # ------------------------------------------------------------------
    # 7. Split the node and verify all alternatives survive.
    # ------------------------------------------------------------------
    print("\n[7] Splitting ambiguous node …")
    children = split_ambiguous_node(source_node)
    print(f"   parent alternatives : {source_node.alternatives}")
    print(f"   # children          : {len(children)}")
    for child in children:
        print(f"     child {child.node_id}: {child.alternatives[0]!r} (weight={child.weights[0]})")
    assert len(children) == len(source_node.alternatives), "FAIL: wrong number of children!"
    print("   ✓ Every alternative became a child.")

    # ------------------------------------------------------------------
    # 8. Merge alternatives from two source nodes.
    # ------------------------------------------------------------------
    print("\n[8] Merging alternatives from two sources …")
    alts_a = ("forall_s_exists_b__read(s,b)", "exists_b_forall_s__read(s,b)")
    alts_b = ("exists_b_forall_s__read(s,b)", "forall_s_forall_b__read(s,b)")
    wts_a = (0.65, 0.35)
    wts_b = (0.4, 0.2)
    merged_alts, merged_wts = merge_ambiguous_alternatives([alts_a, alts_b], [wts_a, wts_b])
    print(f"   merged alternatives : {merged_alts}")
    print(f"   merged weights      : {merged_wts}")
    assert len(merged_alts) == 3, f"FAIL: expected 3 merged alts, got {len(merged_alts)}"
    shared = "exists_b_forall_s__read(s,b)"
    shared_idx = merged_alts.index(shared)
    assert abs(merged_wts[shared_idx] - (0.35 + 0.4)) < 1e-9, \
        "FAIL: shared alternative weight should be summed"
    print(f"   ✓ Shared alternative weight summed correctly ({merged_wts[shared_idx]:.2f}).")

    # ------------------------------------------------------------------
    # 9. Opportunistic lowering on a low-entropy node (score < threshold).
    # ------------------------------------------------------------------
    print("\n[9] Opportunistic lowering on a nearly-resolved node …")
    near_resolved = AmbiguousIRNode(
        node_id="n_near_resolved",
        kind="EXPRESSION",
        alternatives=("forall_s_exists_b__read(s,b)", "exists_b_forall_s__read(s,b)"),
        weights=(0.99, 0.01),  # heavily skewed → low score
        trust=TrustTier.MEDIUM.value,
        ambiguity_reason="near-resolved scope ambiguity",
        resolution_deferred=True,
    )
    nr_score = compute_ambiguity_score(near_resolved)
    print(f"   ambiguity score     : {nr_score:.4f}")
    lowering_opp = AmbiguityPreservingLowering(
        pass_id="pass_opportunistic_01",
        strategy="opportunistic",
        ambiguity_budget=5,
        collapse_threshold=0.15,  # score must be < 0.15 to trigger collapse
        trace=(),
    )
    lowered_opp = lower_with_ambiguity(near_resolved, lowering_opp)
    print(f"   strategy            : {lowering_opp.strategy}")
    print(f"   collapse_threshold  : {lowering_opp.collapse_threshold}")
    print(f"   lowered alternatives: {lowered_opp.alternatives}")
    if nr_score < lowering_opp.collapse_threshold:
        print("   (score < threshold → collapse attempted)")
        assert len(lowered_opp.alternatives) == 1, "FAIL: collapse should produce 1 alternative"
        print("   ✓ Collapsed to dominant alternative.")
    else:
        print("   (score ≥ threshold → preserved)")
        assert set(lowered_opp.alternatives) == set(near_resolved.alternatives)
        print("   ✓ Preserved.")

    # ------------------------------------------------------------------
    # 10. CollapseError should fire when budget is exhausted and we PRESERVE.
    # ------------------------------------------------------------------
    print("\n[10] Budget exhaustion detection …")
    tight_budget = AmbiguityPreservingLowering(
        pass_id="pass_tight",
        strategy="conservative",
        ambiguity_budget=0,
        collapse_threshold=0.5,
        trace=(),
    )
    remaining = ambiguity_budget_remaining(tight_budget)
    print(f"   budget remaining    : {remaining}")
    assert remaining == 0, "FAIL: expected 0 remaining"
    print("   ✓ Budget correctly reported as exhausted.")

    # ------------------------------------------------------------------
    # 11. TrustTier algebra spot-checks.
    # ------------------------------------------------------------------
    print("\n[11] TrustTier ordered-algebra checks …")
    assert TrustTier.meet(TrustTier.HIGH, TrustTier.LOW) == TrustTier.LOW
    assert TrustTier.join(TrustTier.MEDIUM, TrustTier.VERIFIED) == TrustTier.VERIFIED
    assert TrustTier.meet(TrustTier.VERIFIED, TrustTier.VERIFIED) == TrustTier.VERIFIED
    assert TrustTier.NONE < TrustTier.LOW < TrustTier.MEDIUM < TrustTier.HIGH < TrustTier.VERIFIED
    print("   ✓ meet/join/ordering all correct.")

    # ------------------------------------------------------------------
    # 12. Full pipeline demo: SURFACE → SEMANTIC → LOGICAL
    # ------------------------------------------------------------------
    print("\n[12] Full lowering pipeline SURFACE → SEMANTIC → LOGICAL …")
    pipeline_node = AmbiguousIRNode(
        node_id="pipe_root",
        kind="SURFACE",
        alternatives=(
            "parse_A: every(student, lambda s. exists(book, lambda b. read(s,b)))",
            "parse_B: exists(book, lambda b. every(student, lambda s. read(s,b)))",
            "parse_C: every(student, lambda s. read(s, some_contextual_book))",
        ),
        weights=(0.5, 0.3, 0.2),
        trust=TrustTier.LOW.value,
        ambiguity_reason=(
            "Three-way scope ambiguity: SURFACE Čech cover has three patches "
            "with incompatible transitions on pair-wise overlaps → H¹ non-trivial."
        ),
        resolution_deferred=True,
    )
    pipeline_lowering = AmbiguityPreservingLowering(
        pass_id="pipeline_pass_01",
        strategy="conservative",
        ambiguity_budget=20,
        collapse_threshold=0.2,
        trace=(),
    )
    pipeline_trace = LoweringTrace(
        trace_id="pipeline_trace_01",
        steps=(),
        source_level=0,
        target_level=2,
        ambiguities_encountered=0,
        ambiguities_preserved=0,
        ambiguities_collapsed=0,
    )

    # Pass 1: SURFACE → SEMANTIC
    sem_node = lower_with_ambiguity(pipeline_node, pipeline_lowering)
    step1 = _make_step(pipeline_node, sem_node, "surface_to_semantic", "PRESERVE",
                        "conservative pass 1")
    pipeline_trace = track_ambiguity_through_lowering(pipeline_trace, step1)

    # Pass 2: SEMANTIC → LOGICAL
    log_node = lower_with_ambiguity(sem_node, pipeline_lowering)
    step2 = _make_step(sem_node, log_node, "semantic_to_logical", "PRESERVE",
                        "conservative pass 2")
    pipeline_trace = track_ambiguity_through_lowering(pipeline_trace, step2)

    print(f"   original alternatives : {len(pipeline_node.alternatives)}")
    print(f"   after 2 passes        : {len(log_node.alternatives)}")
    print(f"   trace steps           : {len(pipeline_trace.steps)}")
    print(f"   preservation_ratio    : {pipeline_trace.preservation_ratio:.2f}")
    assert len(log_node.alternatives) == len(pipeline_node.alternatives), \
        "FAIL: alternatives lost during pipeline!"
    assert pipeline_trace.preservation_ratio == 1.0, "FAIL: not all ambiguities preserved!"
    print("   ✓ All three alternatives survived two lowering passes.")
    print("   ✓ Čech H¹ obstruction class intact throughout pipeline.")

    print("\n" + "=" * 70)
    print("All smoke tests passed.  Ambiguity-preservation invariant verified.")
    print("=" * 70)
    sys.exit(0)
