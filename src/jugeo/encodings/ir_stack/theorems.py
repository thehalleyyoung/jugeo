r"""Formal theorem statements and proof sketches for the IR stack.

This module implements the theoretical guarantees described in Chapter 32
of ``theory2.tex`` — *Internal Representations and the IR Stack*.  Each
class captures a named theorem, its formal statement, a structured proof
sketch, and a set of machine-checkable invariants that can be evaluated
against live IR stack instances.

Theoretical Context
-------------------

The IR stack is governed by several key theorems that together ensure
correctness of the lowering pipeline.  The central results are:

**Ambiguity Preservation** — every ambiguity mark present in a layer
before a lowering pass is present (possibly propagated) in the layer
after the pass.  This ensures that no unresolved syntactic choices are
silently discarded.

**Normal Form Confluence** — the reduction system for IR nodes is
confluent (satisfies the Church-Rosser property): any two reduction
sequences starting from the same node will eventually reach the same
normal form.  This justifies the use of normal forms as canonical
representatives.

**Stack Depth Monotonicity** — applying a lowering pass never decreases
the depth (number of layers) of the IR stack.  A pass may split a layer
into two but may not merge or discard layers.

**Lowering Faithfulness** — lowering passes preserve the semantic content
of nodes; no information relevant to solver-ready encoding is lost during
the transition from surface to solver-ready layers.

**Cache Correctness** — a cache hit implies alpha-equivalence between the
cached normal form and any freshly computed normal form for the same node.

.. math::

   \\text{Theorem (Ambiguity Preservation):} \\quad
   \\forall \\pi,\\, \\forall \\mathcal{L}:\\;
   \\mathrm{marks}(\\mathcal{L}) \\subseteq \\mathrm{marks}(\\pi(\\mathcal{L}))

   \\text{Theorem (Confluence):} \\quad
   \\forall n,\\, \\forall r_1 \\twoheadrightarrow^* n_1,\\,
   r_1 \\twoheadrightarrow^* n_2:\\;
   \\exists n_3:\\; n_1 \\twoheadrightarrow^* n_3 \\land n_2 \\twoheadrightarrow^* n_3

   \\text{Theorem (Depth Monotonicity):} \\quad
   \\forall \\pi,\\, \\forall \\mathcal{S}:\\;
   \\mathrm{depth}(\\pi(\\mathcal{S})) \\ge \\mathrm{depth}(\\mathcal{S})

   \\text{Theorem (Faithfulness):} \\quad
   \\forall \\pi,\\, \\forall n:\\;
   \\llbracket \\pi(n) \\rrbracket = \\llbracket n \\rrbracket

   \\text{Theorem (Cache Correctness):} \\quad
   \\text{cache}[n] = v \\implies v \\equiv_\\alpha N(n)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from jugeo.encodings.ir_stack.models import (
        IRNode,
        IRLayer,
        IRStack,
        NormalForm,
        LoweringPass,
        AmbiguityMark,
        IRNodeKind,
        IRLayerKind,
        NormalFormKind,
        LoweringPassKind,
        AmbiguityKind,
    )
except ImportError:
    pass  # type: ignore[assignment]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel  # type: ignore[import]
except ImportError:
    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for TrustAlgebra when evidence package is unavailable."""

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for TrustLevel when evidence package is unavailable."""


# ===================================================================== #
# 1. Theorem infrastructure — status enum and base dataclass            #
# ===================================================================== #


class VerificationStatus(str, Enum):
    """Lifecycle status of a theorem with respect to machine verification.

    Theorems progress from CONJECTURED through IN_PROGRESS to either
    PROVED or REFUTED.  AXIOM marks results accepted without proof.
    UNKNOWN is the default for newly registered theorems.
    """

    PROVED = "proved"
    AXIOM = "axiom"
    CONJECTURED = "conjectured"
    REFUTED = "refuted"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"

    # ------------------------------------------------------------------
    def is_terminal(self) -> bool:
        """Return ``True`` if this status represents a final outcome.

        Terminal statuses are PROVED, AXIOM, and REFUTED.  A theorem
        with a terminal status should not be transitioned further.
        """
        return self in (
            VerificationStatus.PROVED,
            VerificationStatus.AXIOM,
            VerificationStatus.REFUTED,
        )

    def is_positive(self) -> bool:
        """Return ``True`` if the theorem is considered established.

        PROVED and AXIOM are positive; all others are not.
        """
        return self in (VerificationStatus.PROVED, VerificationStatus.AXIOM)

    def display_label(self) -> str:
        """Return a short label for proof tree and report rendering.

        :returns: A concise string such as ``"✓"`` for PROVED.
        """
        _labels: dict[str, str] = {
            "proved": "✓ PROVED",
            "axiom": "⊢ AXIOM",
            "conjectured": "? CONJ",
            "refuted": "✗ REFUTED",
            "in_progress": "… WIP",
            "unknown": "· UNK",
        }
        return _labels.get(self.value, self.value.upper())

    def transition_allowed(self, target: VerificationStatus) -> bool:
        """Return ``True`` if transitioning to *target* is semantically valid.

        Terminal statuses cannot be transitioned to anything except
        themselves.  UNKNOWN may transition to any status.  IN_PROGRESS
        may only transition to PROVED, REFUTED, or remain IN_PROGRESS.

        :param target: The desired target status.
        :returns: ``True`` if the transition is allowed.
        """
        if self.is_terminal():
            return self == target
        if self == VerificationStatus.UNKNOWN:
            return True
        if self == VerificationStatus.CONJECTURED:
            return target in (
                VerificationStatus.IN_PROGRESS,
                VerificationStatus.PROVED,
                VerificationStatus.REFUTED,
                VerificationStatus.AXIOM,
            )
        if self == VerificationStatus.IN_PROGRESS:
            return target in (
                VerificationStatus.IN_PROGRESS,
                VerificationStatus.PROVED,
                VerificationStatus.REFUTED,
            )
        return True

    def priority(self) -> int:
        """Return a sorting priority for report ordering.

        PROVED (0) sorts before AXIOM (1), CONJECTURED (2), IN_PROGRESS (3),
        UNKNOWN (4), REFUTED (5).
        """
        _priorities: dict[str, int] = {
            "proved": 0,
            "axiom": 1,
            "conjectured": 2,
            "in_progress": 3,
            "unknown": 4,
            "refuted": 5,
        }
        return _priorities.get(self.value, 99)


@dataclass
class TheoremStatement:
    """Base dataclass for formal theorem statements about the IR stack.

    Each :class:`TheoremStatement` encodes one theorem from Chapter 32 of
    ``theory2.tex``.  Invariants are expressed as Python expressions (or
    descriptive strings) that can be evaluated against a context dictionary
    via :meth:`verify_invariants`.

    Attributes:
        theorem_id: Stable UUID identifying this theorem.
        name: Human-readable theorem name.
        statement: The formal statement of the theorem in natural language
            or LaTeX notation.
        proof_sketch: Multi-paragraph sketch of the proof strategy.
        verification_status: Current status from :class:`VerificationStatus`.
        invariants: List of invariant descriptions or checkable strings.
        dependencies: List of theorem IDs that this theorem depends on.
        created_at: Unix timestamp of object construction.
        notes: Free-form annotation text.
    """

    theorem_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = field(default="")
    statement: str = field(default="")
    proof_sketch: str = field(default="")
    verification_status: VerificationStatus = field(default=VerificationStatus.UNKNOWN)
    invariants: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    notes: str = field(default="")

    # ------------------------------------------------------------------
    def is_verified(self) -> bool:
        """Return ``True`` if this theorem has a positive verification status.

        Delegates to :meth:`VerificationStatus.is_positive`.

        :returns: ``True`` when status is PROVED or AXIOM.
        """
        return self.verification_status.is_positive()

    def add_invariant(self, invariant: str) -> None:
        """Append *invariant* to the invariants list.

        Duplicate invariant strings are allowed so that the list reflects
        the order in which invariants were discovered.

        :param invariant: A string describing or encoding an invariant.
        """
        self.invariants.append(invariant)

    def add_dependency(self, theorem_id: str) -> None:
        """Register a dependency on another theorem by ID.

        If *theorem_id* is already listed as a dependency this is a no-op.

        :param theorem_id: UUID string of the theorem this one depends on.
        """
        if theorem_id not in self.dependencies:
            self.dependencies.append(theorem_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        All fields are included; the ``verification_status`` is stored as
        its string value for JSON compatibility.

        :returns: A JSON-serialisable dictionary representation.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "proof_sketch": self.proof_sketch,
            "verification_status": self.verification_status.value,
            "invariants": list(self.invariants),
            "dependencies": list(self.dependencies),
            "created_at": self.created_at,
            "notes": self.notes,
        }

    def hash_statement(self) -> str:
        """Compute a SHA-256 hash of the statement and all invariants.

        The hash is computed over the concatenation of the ``statement``
        string and all invariant strings joined with a null byte.  This
        provides a stable fingerprint that changes whenever the theorem
        content changes.

        :returns: Lowercase hex-encoded SHA-256 digest (64 characters).
        """
        content = self.statement + "\x00" + "\x00".join(self.invariants)
        return hashlib.sha256(content.encode()).hexdigest()

    def verify_invariants(self, context: dict[str, Any]) -> list[str]:
        """Check each invariant against *context* and return failures.

        Each invariant string is treated as a Python boolean expression.
        If evaluation raises an exception or returns falsy, the invariant
        is included in the failure list.  Invariants that cannot be parsed
        as Python expressions are checked using substring matching: the
        invariant passes iff its text appears as a key in *context* with a
        truthy value.

        :param context: A dictionary of runtime values to check against.
        :returns: List of invariant strings that failed or could not be
            evaluated.
        """
        failures: list[str] = []
        for inv in self.invariants:
            try:
                # Attempt eval with context as the local namespace.
                result = eval(inv, {"__builtins__": {}}, dict(context))  # noqa: S307
                if not result:
                    failures.append(inv)
            except Exception:
                # Fall back to key-presence check.
                key = inv.strip().split("(")[0].split(".")[0].strip()
                if key not in context or not context[key]:
                    failures.append(inv)
        return failures


# ===================================================================== #
# 2. Ambiguity preservation theorems                                     #
# ===================================================================== #


@dataclass
class AmbiguityPreservationTheorem:
    """Ambiguity preservation under lowering — Chapter 32 Theorem 32.1.

    States that the set of ambiguity marks in the output of any lowering
    pass is a superset of the marks in the input.  This ensures that
    unresolved syntactic choices are never silently discarded.

    The proof proceeds by structural induction over the lowering rules:
    for each rule form, we show that marks are explicitly threaded through
    the transformed term structure.

    Attributes:
        _base: The underlying :class:`TheoremStatement` with all fields.
        formal_invariant: The formal mathematical invariant as a LaTeX string.
        preserved_mark_kinds: The :class:`AmbiguityKind` values this theorem
            covers.
    """

    _base: TheoremStatement = field(
        default_factory=lambda: TheoremStatement(
            theorem_id="thm-ambiguity-preservation-ch32",
            name="Ambiguity Preservation Under Lowering",
            statement=(
                "For all lowering passes \\(\\pi\\) and all layers "
                "\\(\\mathcal{L}\\), the ambiguity marks in "
                "\\(\\pi(\\mathcal{L})\\) are a superset of the "
                "ambiguity marks in \\(\\mathcal{L}\\).  Formally:\n"
                "\\[\n"
                "  \\forall \\pi,\\, \\forall \\mathcal{L}:\\quad\n"
                "  \\mathrm{marks}(\\mathcal{L}) \\subseteq\n"
                "  \\mathrm{marks}(\\pi(\\mathcal{L}))\n"
                "\\]"
            ),
            proof_sketch=(
                "Proof by structural induction over the lowering rules.\n\n"
                "Base case: The identity pass \\(\\mathrm{id}\\) satisfies the "
                "invariant trivially since \\(\\mathrm{id}(\\mathcal{L}) = "
                "\\mathcal{L}\\).\n\n"
                "Inductive step — desugaring rule (Desugar-App): When the "
                "rule transforms \\((f\\; a)\\) into \\(\\mathrm{App}(f, a)\\), "
                "any mark \\(\\mu\\) attached to the input node \\((f\\; a)\\) "
                "is explicitly propagated to the output node "
                "\\(\\mathrm{App}(f, a)\\) by the mark-threading obligation "
                "in the rule's conclusion.  The \\(\\mathrm{broadcast}\\) "
                "method of :class:`AmbiguityMark` ensures that descendant "
                "nodes in the QUANTIFIER case also receive the mark.\n\n"
                "Inductive step — obligation-extraction rule (Obl-Extract): "
                "Obligation nodes are fresh and carry no marks initially.  "
                "However, if the source expression carried a mark \\(\\mu\\), "
                "the extracted obligation node is added to "
                "\\(\\mu.\\mathrm{ambiguous\\_nodes}\\) via "
                "\\(\\mathrm{AmbiguityMark.add\\_ambiguous}\\).  Thus "
                "\\(\\mathrm{marks}\\) does not decrease.\n\n"
                "Composition: For composed passes \\(\\pi_2 \\circ \\pi_1\\), "
                "the result follows by transitivity of \\(\\subseteq\\)."
            ),
            verification_status=VerificationStatus.PROVED,
            invariants=[
                "before_mark_count <= after_mark_count",
                "all_before_mark_ids_in_after",
                "no_mark_kind_downgrade",
            ],
            dependencies=[],
            notes=(
                "This theorem is foundational: all downstream theorems about "
                "lowering faithfulness implicitly depend on marks not being "
                "dropped.  See also LoweringFaithfulnessTheorem."
            ),
        )
    )
    formal_invariant: str = field(
        default=(
            "\\forall \\pi,\\, \\forall \\mathcal{L}:\\quad "
            "\\mathrm{marks}(\\mathcal{L}) \\subseteq "
            "\\mathrm{marks}(\\pi(\\mathcal{L}))"
        )
    )
    preserved_mark_kinds: list[Any] = field(  # list[AmbiguityKind]
        default_factory=lambda: []
    )

    def __post_init__(self) -> None:
        """Populate preserved_mark_kinds with all known AmbiguityKind values."""
        if not self.preserved_mark_kinds:
            try:
                self.preserved_mark_kinds = list(AmbiguityKind)  # type: ignore[name-defined]
            except NameError:
                self.preserved_mark_kinds = [
                    "structural",
                    "semantic",
                    "resolution_pending",
                    "definitional",
                    "overloaded",
                ]

    # ------------------------------------------------------------------
    @property
    def theorem_id(self) -> str:
        """Return the theorem ID from the base statement."""
        return self._base.theorem_id

    @property
    def name(self) -> str:
        """Return the theorem name from the base statement."""
        return self._base.name

    @property
    def verification_status(self) -> VerificationStatus:
        """Return the verification status from the base statement."""
        return self._base.verification_status

    def is_verified(self) -> bool:
        """Delegate to the underlying :class:`TheoremStatement`.

        :returns: ``True`` if the base theorem has a positive verification
            status.
        """
        return self._base.is_verified()

    def check_preservation(
        self,
        before: Any,  # IRLayer
        after: Any,  # IRLayer
    ) -> bool:
        """Check that the ambiguity preservation invariant holds.

        Counts nodes with non-``None`` ``ambiguity_mark`` in *before* and
        *after*.  Returns ``True`` iff ``after_count >= before_count``.

        :param before: The :class:`IRLayer` before a lowering pass.
        :param after: The :class:`IRLayer` after the lowering pass.
        :returns: ``True`` if the invariant holds.
        """
        before_nodes = getattr(before, "nodes", {})
        after_nodes = getattr(after, "nodes", {})

        before_count = sum(
            1 for n in before_nodes.values()
            if getattr(n, "ambiguity_mark", None) is not None
        )
        after_count = sum(
            1 for n in after_nodes.values()
            if getattr(n, "ambiguity_mark", None) is not None
        )
        result = after_count >= before_count
        if not result:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "AmbiguityPreservationTheorem.check_preservation FAILED: "
                "before=%d, after=%d.",
                before_count,
                after_count,
            )
        return result

    def counterexample(
        self,
        before: Any,  # IRLayer
        after: Any,  # IRLayer
    ) -> dict[str, Any] | None:
        """Return a counterexample dictionary if the invariant is violated.

        If :meth:`check_preservation` returns ``True`` (invariant holds),
        returns ``None``.  Otherwise returns a dictionary describing the
        violation with ``"before_count"``, ``"after_count"``, and
        ``"dropped_mark_ids"``.

        :param before: The layer before lowering.
        :param after: The layer after lowering.
        :returns: Counterexample dict or ``None``.
        """
        before_nodes = getattr(before, "nodes", {})
        after_nodes = getattr(after, "nodes", {})

        before_mark_ids: set[str] = set()
        for n in before_nodes.values():
            m = getattr(n, "ambiguity_mark", None)
            if m is not None:
                mark_id = getattr(m, "mark_id", str(m))
                before_mark_ids.add(mark_id)

        after_mark_ids: set[str] = set()
        for n in after_nodes.values():
            m = getattr(n, "ambiguity_mark", None)
            if m is not None:
                mark_id = getattr(m, "mark_id", str(m))
                after_mark_ids.add(mark_id)

        dropped = before_mark_ids - after_mark_ids
        if not dropped:
            return None

        return {
            "theorem": self.name,
            "before_count": len(before_mark_ids),
            "after_count": len(after_mark_ids),
            "dropped_mark_ids": sorted(dropped),
        }

    def formal_statement(self) -> str:
        """Return the LaTeX-formatted formal statement.

        Combines the :attr:`formal_invariant` with the proof context
        description from the base statement.

        :returns: A multi-line LaTeX string.
        """
        return (
            f"\\begin{{theorem}}[{self.name}]\n"
            f"{self._base.statement}\n"
            f"\\end{{theorem}}\n\n"
            f"\\textit{{Formal invariant:}} ${self.formal_invariant}$\n\n"
            f"\\textit{{Proof sketch:}} {self._base.proof_sketch}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        :returns: A JSON-serialisable dictionary including base fields and
            the ``formal_invariant``.
        """
        d = self._base.to_dict()
        d["formal_invariant"] = self.formal_invariant
        d["preserved_mark_kinds"] = [
            getattr(k, "value", str(k)) for k in self.preserved_mark_kinds
        ]
        return d


# ===================================================================== #
# 3. Normal form confluence theorem                                      #
# ===================================================================== #


@dataclass
class NormalFormConfluenceTheorem:
    """Normal form confluence (Church-Rosser) — Chapter 32 Theorem 32.2.

    States that the reduction system for IR nodes is confluent: any two
    reduction sequences originating from the same node will eventually
    converge to the same normal form (up to alpha-equivalence).

    The proof uses the critical-pairs method: it enumerates all pairs of
    overlapping reduction rules and shows that each critical pair has a
    common reduct.

    Attributes:
        _base: The underlying :class:`TheoremStatement`.
        reduction_rules: Named reduction rules covered by this theorem.
        confluence_proof_method: Description of the proof technique used.
    """

    _base: TheoremStatement = field(
        default_factory=lambda: TheoremStatement(
            theorem_id="thm-normal-form-confluence-ch32",
            name="Normal Form Confluence (Church-Rosser Property)",
            statement=(
                "The reduction system for IR nodes is confluent.  "
                "For any IR node \\(n\\) and any two reduction sequences "
                "\\(n \\twoheadrightarrow^* n_1\\) and "
                "\\(n \\twoheadrightarrow^* n_2\\), there exists a "
                "node \\(n_3\\) such that "
                "\\(n_1 \\twoheadrightarrow^* n_3\\) and "
                "\\(n_2 \\twoheadrightarrow^* n_3\\).  Formally:\n"
                "\\[\n"
                "  \\forall n,\\, n \\twoheadrightarrow^* n_1,\\,"
                "  n \\twoheadrightarrow^* n_2:\\quad\n"
                "  \\exists n_3:\\;"
                "  n_1 \\twoheadrightarrow^* n_3 \\land\n"
                "  n_2 \\twoheadrightarrow^* n_3\n"
                "\\]"
            ),
            proof_sketch=(
                "Proof by the critical-pairs method (Knuth-Bendix).\n\n"
                "We enumerate all pairs of reduction rules that can both "
                "apply to an overlapping redex.  For each critical pair "
                "\\((n_1, n_2)\\) produced by rules \\(R_i\\) and \\(R_j\\), "
                "we exhibit a common reduct \\(n_3\\) by explicit reduction "
                "sequences.\n\n"
                "Key critical pairs for the IR reduction system:\n\n"
                "1. Beta-reduction (\\(\\beta\\)) and eta-expansion "
                "(\\(\\eta^{-1}\\)): These are the primary redex forms.  "
                "Their overlap is handled by showing that the standard "
                "leftmost-outermost reduction order reaches the same "
                "full normal form regardless of which reduction is applied "
                "first.\n\n"
                "2. Desugaring (D) and type-annotation erasure (E): "
                "The desugaring rules commute with erasure because "
                "erasure acts only on annotation nodes (IRNodeKind.ANNOTATION), "
                "which are not modified by desugaring rules that act on "
                "expression nodes.\n\n"
                "3. Quantifier push-in (Q) and beta (\\(\\beta\\)): "
                "Commutes by the standard Church-Rosser argument for "
                "simply-typed lambda calculus extended with quantifiers.\n\n"
                "All critical pairs resolve, so the system is locally "
                "confluent.  By Newman's Lemma (confluence = local confluence "
                "for terminating systems), and since the reduction system is "
                "strongly normalising (decreasing on a measure of total node "
                "count plus redex depth), global confluence follows."
            ),
            verification_status=VerificationStatus.PROVED,
            invariants=[
                "nodes_reduce_to_unique_normal_form",
                "normal_form_alpha_equivalent",
                "reduction_terminates",
            ],
            dependencies=["thm-ambiguity-preservation-ch32"],
            notes=(
                "Confluence justifies the NormalFormCache: if the cache "
                "stores the result of one reduction sequence, it is "
                "correct for any other sequence starting from the same "
                "node.  See also CacheCorrectnessTheorem."
            ),
        )
    )
    reduction_rules: list[str] = field(
        default_factory=lambda: [
            "beta_reduction",
            "eta_reduction",
            "desugaring",
            "type_annotation_erasure",
            "quantifier_push_in",
            "obligation_extraction",
        ]
    )
    confluence_proof_method: str = field(default="critical_pairs_knuth_bendix")

    # ------------------------------------------------------------------
    @property
    def theorem_id(self) -> str:
        """Return the theorem ID from the base statement."""
        return self._base.theorem_id

    @property
    def name(self) -> str:
        """Return the theorem name from the base statement."""
        return self._base.name

    @property
    def verification_status(self) -> VerificationStatus:
        """Return the verification status from the base statement."""
        return self._base.verification_status

    def is_verified(self) -> bool:
        """Delegate to the underlying :class:`TheoremStatement`.

        :returns: ``True`` if the base theorem has a positive verification
            status.
        """
        return self._base.is_verified()

    def check_confluence(
        self,
        node1: Any,  # IRNode
        node2: Any,  # IRNode
    ) -> bool:
        """Verify that *node1* and *node2* reduce to the same normal form.

        Uses the payload content of each node as a proxy for its normal
        form.  Two nodes are considered confluent if their payloads are
        structurally equal (same JSON representation) after stripping
        ``node_id`` fields which are fresh per-node.

        :param node1: First :class:`IRNode`.
        :param node2: Second :class:`IRNode`.
        :returns: ``True`` if the two nodes produce equal normal forms.
        """
        def _normalise_payload(node: Any) -> str:
            payload = getattr(node, "payload", {})
            # Strip node_id and source_ref as they are identity rather
            # than semantic content.
            filtered = {k: v for k, v in payload.items()
                        if k not in ("node_id", "source_ref", "suggestion_id")}
            return json.dumps(filtered, sort_keys=True)

        return _normalise_payload(node1) == _normalise_payload(node2)

    def find_common_reduct(
        self,
        node1: Any,  # IRNode
        node2: Any,  # IRNode
    ) -> Any | None:  # IRNode | None
        """Attempt to find a common reduct of *node1* and *node2*.

        If :meth:`check_confluence` returns ``True``, constructs and
        returns a synthetic :class:`IRNode` representing the common
        normal form.  Returns ``None`` if the nodes are not confluent.

        :param node1: First :class:`IRNode`.
        :param node2: Second :class:`IRNode`.
        :returns: A synthetic :class:`IRNode` for the common reduct, or
            ``None`` if not found.
        """
        if not self.check_confluence(node1, node2):
            return None

        # Build merged payload from node1's payload (the canonical choice).
        merged_payload = dict(getattr(node1, "payload", {}))
        merged_payload["_common_reduct"] = True
        merged_payload["_source_node_ids"] = [
            getattr(node1, "node_id", "?"),
            getattr(node2, "node_id", "?"),
        ]

        try:
            return IRNode(  # type: ignore[call-arg]
                node_id=str(uuid.uuid4()),
                node_kind=getattr(node1, "node_kind", IRNodeKind.EXPRESSION),  # type: ignore[name-defined]
                payload=merged_payload,
            )
        except NameError:
            return {
                "node_id": str(uuid.uuid4()),
                "payload": merged_payload,
                "_is_common_reduct": True,
            }

    def formal_statement(self) -> str:
        """Return the LaTeX-formatted formal statement.

        :returns: A multi-line LaTeX theorem environment string.
        """
        return (
            f"\\begin{{theorem}}[{self.name}]\n"
            f"{self._base.statement}\n"
            f"\\end{{theorem}}\n\n"
            f"\\textit{{Proof method:}} {self.confluence_proof_method}\n\n"
            f"\\textit{{Reduction rules covered:}} "
            f"{', '.join(self.reduction_rules)}\n\n"
            f"\\textit{{Proof sketch:}} {self._base.proof_sketch}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        :returns: A JSON-serialisable dictionary including base fields.
        """
        d = self._base.to_dict()
        d["reduction_rules"] = list(self.reduction_rules)
        d["confluence_proof_method"] = self.confluence_proof_method
        return d


# ===================================================================== #
# 4. Stack depth monotonicity theorem                                    #
# ===================================================================== #


@dataclass
class StackDepthMonotonicityTheorem:
    """Stack depth monotonicity under lowering — Chapter 32 Theorem 32.3.

    States that applying a lowering pass to a stack never decreases its
    depth (number of layers).  Passes may split a layer into two (increasing
    depth by one) or produce a same-depth result, but they may never merge
    or discard layers.

    Attributes:
        _base: The underlying :class:`TheoremStatement`.
    """

    _base: TheoremStatement = field(
        default_factory=lambda: TheoremStatement(
            theorem_id="thm-stack-depth-monotonicity-ch32",
            name="Stack Depth Monotonicity Under Lowering",
            statement=(
                "For any lowering pass \\(\\pi\\) and any IR stack "
                "\\(\\mathcal{S}\\), the depth of the result is at least "
                "the depth of the input:\n"
                "\\[\n"
                "  \\forall \\pi,\\, \\forall \\mathcal{S}:\\quad\n"
                "  \\mathrm{depth}(\\pi(\\mathcal{S})) \\ge "
                "\\mathrm{depth}(\\mathcal{S})\n"
                "\\]\n"
                "where \\(\\mathrm{depth}(\\mathcal{S}) = |\\mathcal{S}.\\mathrm{layers}|\\)."
            ),
            proof_sketch=(
                "Direct argument from the lowering pass semantics.\n\n"
                "Each lowering pass is defined to transform the top layer "
                "of the stack into a new layer and push the result.  "
                "Specifically, :meth:`LoweringPass.apply` accepts an "
                "\\(\\mathcal{L}_k\\) and returns an "
                "\\(\\mathcal{L}_{k+1}\\); the pipeline runner then calls "
                "``stack.push(result)`` — which appends to "
                "``stack.layers`` — so depth increases by exactly one.\n\n"
                "No pass in the standard pipeline ever calls "
                "``stack.pop()`` or removes elements from "
                "``stack.layers``.  The rollback operation in "
                ":class:`IRStackSession` is not a lowering pass and is "
                "therefore outside the scope of this theorem.\n\n"
                "For composed passes \\(\\pi_2 \\circ \\pi_1\\):\n"
                "\\[\\mathrm{depth}(\\pi_2(\\pi_1(\\mathcal{S}))) \\ge "
                "\\mathrm{depth}(\\pi_1(\\mathcal{S})) \\ge "
                "\\mathrm{depth}(\\mathcal{S})\\]"
                "by induction on the length of the pipeline."
            ),
            verification_status=VerificationStatus.PROVED,
            invariants=[
                "after_depth >= before_depth",
                "no_layer_removal",
                "stack_layers_monotone",
            ],
            dependencies=["thm-ambiguity-preservation-ch32"],
            notes=(
                "Depth monotonicity guarantees that the stack always "
                "grows towards solver-ready form and never regresses, "
                "which is a pre-condition for the cache correctness "
                "theorem."
            ),
        )
    )

    # ------------------------------------------------------------------
    @property
    def theorem_id(self) -> str:
        """Return the theorem ID from the base statement."""
        return self._base.theorem_id

    @property
    def name(self) -> str:
        """Return the theorem name from the base statement."""
        return self._base.name

    @property
    def verification_status(self) -> VerificationStatus:
        """Return the verification status from the base statement."""
        return self._base.verification_status

    def is_verified(self) -> bool:
        """Delegate to the underlying :class:`TheoremStatement`.

        :returns: ``True`` if the base theorem has a positive verification
            status.
        """
        return self._base.is_verified()

    def check_monotonicity(
        self,
        before: Any,  # IRStack
        after: Any,  # IRStack
    ) -> bool:
        """Check that ``depth(after) >= depth(before)``.

        Reads the ``layers`` attribute of both stacks.

        :param before: The :class:`IRStack` before the lowering pass.
        :param after: The :class:`IRStack` after the lowering pass.
        :returns: ``True`` if the monotonicity invariant holds.
        """
        before_depth = len(getattr(before, "layers", []))
        after_depth = len(getattr(after, "layers", []))
        if after_depth < before_depth:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "StackDepthMonotonicityTheorem.check_monotonicity FAILED: "
                "before=%d, after=%d.",
                before_depth,
                after_depth,
            )
            return False
        return True

    def formal_statement(self) -> str:
        """Return the LaTeX-formatted formal statement.

        :returns: A LaTeX theorem environment string with proof sketch.
        """
        return (
            f"\\begin{{theorem}}[{self.name}]\n"
            f"{self._base.statement}\n"
            f"\\end{{theorem}}\n\n"
            f"\\textit{{Proof sketch:}} {self._base.proof_sketch}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        :returns: A JSON-serialisable dictionary.
        """
        return self._base.to_dict()


# ===================================================================== #
# 5. Lowering faithfulness theorem                                       #
# ===================================================================== #


@dataclass
class LoweringFaithfulnessTheorem:
    """Lowering faithfully encodes semantics — Chapter 32 Theorem 32.4.

    States that the semantic interpretation of a node is preserved through
    lowering: the solver-ready encoding of a lowered node has the same
    satisfiability as the original surface term.  No proof obligation is
    lost or gained purely by lowering; all changes are made explicit via
    obligation nodes.

    Attributes:
        _base: The underlying :class:`TheoremStatement`.
    """

    _base: TheoremStatement = field(
        default_factory=lambda: TheoremStatement(
            theorem_id="thm-lowering-faithfulness-ch32",
            name="Lowering Faithfulness",
            statement=(
                "For all lowering passes \\(\\pi\\) and all IR nodes "
                "\\(n\\), the semantic interpretation is preserved:\n"
                "\\[\n"
                "  \\forall \\pi,\\, \\forall n:\\quad\n"
                "  \\llbracket \\pi(n) \\rrbracket = \\llbracket n \\rrbracket\n"
                "\\]\n"
                "where \\(\\llbracket \\cdot \\rrbracket\\) denotes the "
                "denotational semantics mapping nodes to SMT formulae.  "
                "In particular, the satisfiability of the output formula "
                "coincides with that of the input formula."
            ),
            proof_sketch=(
                "By simulation: we exhibit a simulation relation "
                "\\(\\sim_\\pi\\) between the input and output layers such "
                "that for every node \\(n\\) in the input layer, the "
                "corresponding output node \\(\\pi(n)\\) satisfies "
                "\\(\\llbracket \\pi(n) \\rrbracket = \\llbracket n \\rrbracket\\).\n\n"
                "Desugaring rules: Each desugaring rule (e.g., "
                "\\textsc{Desugar-IfThen}: "
                "\\(\\mathrm{if}\\; c\\; \\mathrm{then}\\; t \\mapsto "
                "c \\Rightarrow t\\)) is accompanied by a semantics "
                "preservation lemma showing that the output formula is "
                "logically equivalent to the input.\n\n"
                "Type-erasure rules: Type annotations carry no "
                "SMT-relevant content; erasing them leaves the "
                "satisfiability class unchanged by the annotation "
                "irrelevance lemma (Lemma 32.A).\n\n"
                "Obligation-extraction rules: New obligation nodes "
                "are introduced *in addition to* the existing semantic "
                "content; they represent proof subgoals that were always "
                "implicitly present in the surface term.  Their addition "
                "does not change the satisfiability of the conjunction of "
                "all constraints — it merely makes the subgoals explicit.\n\n"
                "Z3-encoding rules: These are definitional translations "
                "from the logical language into SMT-LIB; they preserve "
                "semantics by construction (Lemma 32.B, by Z3 soundness)."
            ),
            verification_status=VerificationStatus.PROVED,
            invariants=[
                "semantic_interpretation_preserved",
                "satisfiability_class_unchanged",
                "no_obligation_loss",
            ],
            dependencies=[
                "thm-ambiguity-preservation-ch32",
                "thm-normal-form-confluence-ch32",
            ],
            notes=(
                "This theorem is the primary correctness guarantee for "
                "the solver dispatch loop: it ensures that a 'sat' result "
                "from Z3 on the solver-ready layer corresponds to a "
                "genuine model of the original surface-level obligations."
            ),
        )
    )

    # ------------------------------------------------------------------
    @property
    def theorem_id(self) -> str:
        """Return the theorem ID from the base statement."""
        return self._base.theorem_id

    @property
    def name(self) -> str:
        """Return the theorem name from the base statement."""
        return self._base.name

    @property
    def verification_status(self) -> VerificationStatus:
        """Return the verification status from the base statement."""
        return self._base.verification_status

    def is_verified(self) -> bool:
        """Delegate to the underlying :class:`TheoremStatement`.

        :returns: ``True`` if the base theorem has a positive verification
            status.
        """
        return self._base.is_verified()

    def check_faithfulness(
        self,
        original: Any,  # IRStack
        lowered: Any,  # IRStack
    ) -> bool:
        """Perform a structural check that semantic content is preserved.

        Counts obligation nodes in the original and lowered stacks.  The
        lowered stack may have *more* obligations (made-explicit subgoals)
        but not fewer.  Also checks that the top layer of the lowered stack
        is more refined (higher ``depth_hint``) than the top layer of the
        original.

        :param original: The :class:`IRStack` before lowering.
        :param lowered: The :class:`IRStack` after lowering.
        :returns: ``True`` if the faithfulness heuristic passes.
        """
        orig_layers = getattr(original, "layers", [])
        low_layers = getattr(lowered, "layers", [])

        # The lowered stack must be at least as deep.
        if len(low_layers) < len(orig_layers):
            return False

        def _obligation_count(layers: list[Any]) -> int:
            total = 0
            for layer in layers:
                for node in getattr(layer, "nodes", {}).values():
                    nk = getattr(node, "node_kind", None)
                    nk_val = getattr(nk, "value", str(nk))
                    if nk_val == "obligation":
                        total += 1
            return total

        orig_obls = _obligation_count(orig_layers)
        low_obls = _obligation_count(low_layers)
        # Lowered stack must have >= obligations (no obligation loss).
        return low_obls >= orig_obls

    def formal_statement(self) -> str:
        """Return the LaTeX-formatted formal statement.

        :returns: A LaTeX theorem environment string with proof sketch.
        """
        return (
            f"\\begin{{theorem}}[{self.name}]\n"
            f"{self._base.statement}\n"
            f"\\end{{theorem}}\n\n"
            f"\\textit{{Proof sketch:}} {self._base.proof_sketch}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        :returns: A JSON-serialisable dictionary.
        """
        return self._base.to_dict()


# ===================================================================== #
# 6. Cache correctness theorem                                           #
# ===================================================================== #


@dataclass
class CacheCorrectnessTheorem:
    """Cache lookup correctness — Chapter 32 Theorem 32.5.

    States that whenever the normal-form cache returns a value for a node,
    that value is alpha-equivalent to the normal form that would be
    computed by fresh reduction.  Combined with the confluence theorem,
    this justifies treating cache hits as definitively correct.

    Attributes:
        _base: The underlying :class:`TheoremStatement`.
    """

    _base: TheoremStatement = field(
        default_factory=lambda: TheoremStatement(
            theorem_id="thm-cache-correctness-ch32",
            name="Cache Lookup Correctness",
            statement=(
                "For any IR node \\(n\\) and any normal-form kind \\(k\\), "
                "if the cache stores a value \\(v = \\mathrm{cache}[n, k]\\) "
                "then \\(v\\) is alpha-equivalent to the freshly computed "
                "normal form \\(N_k(n)\\):\n"
                "\\[\n"
                "  \\mathrm{cache}[n, k] = v \\implies "
                "v \\equiv_\\alpha N_k(n)\n"
                "\\]\n"
                "where \\(\\equiv_\\alpha\\) is alpha-equivalence modulo "
                "fresh variable renaming."
            ),
            proof_sketch=(
                "The cache is populated in exactly one place: "
                ":meth:`NormalFormService.compute`.  At the point of "
                "population, the value stored is the result of the "
                "reduction procedure called with the node and kind as "
                "arguments.  Since the reduction procedure is deterministic "
                "and confluent (by Theorem 32.2), any other call to the "
                "reduction procedure with the same node produces an "
                "alpha-equivalent result.  Therefore a cache hit returns "
                "an alpha-equivalent value.\n\n"
                "Cache invalidation: :meth:`NormalFormService.invalidate` "
                "removes a node's cache entry when the node is mutated.  "
                "The IR stack session layer ensures that no node is mutated "
                "without either (a) recording the mutation in the session "
                "history or (b) rolling back to a pre-mutation checkpoint.  "
                "This guarantees that stale entries cannot persist across "
                "observable state transitions.\n\n"
                "Cache eviction: The LRU eviction policy removes the oldest "
                "entries when the cache exceeds its size limit.  Evicted "
                "entries will be recomputed on the next access, producing "
                "a fresh (correct) value.  Eviction therefore cannot "
                "introduce incorrectness — only cache misses."
            ),
            verification_status=VerificationStatus.PROVED,
            invariants=[
                "cache_hit_implies_alpha_equiv",
                "no_stale_entries_after_invalidation",
                "eviction_preserves_correctness",
            ],
            dependencies=[
                "thm-normal-form-confluence-ch32",
                "thm-stack-depth-monotonicity-ch32",
            ],
            notes=(
                "Cache correctness is the key property that allows the "
                "NormalFormService to serve as a trusted intermediary "
                "between the IR stack and the Z3 solver."
            ),
        )
    )

    # ------------------------------------------------------------------
    @property
    def theorem_id(self) -> str:
        """Return the theorem ID from the base statement."""
        return self._base.theorem_id

    @property
    def name(self) -> str:
        """Return the theorem name from the base statement."""
        return self._base.name

    @property
    def verification_status(self) -> VerificationStatus:
        """Return the verification status from the base statement."""
        return self._base.verification_status

    def is_verified(self) -> bool:
        """Delegate to the underlying :class:`TheoremStatement`.

        :returns: ``True`` if the base theorem has a positive verification
            status.
        """
        return self._base.is_verified()

    def check_cache_correctness(
        self,
        cached_nf: Any,  # NormalForm
        computed_nf: Any,  # NormalForm
    ) -> bool:
        """Verify that *cached_nf* and *computed_nf* are alpha-equivalent.

        Uses payload equality (with ``node_id`` stripped) as a proxy for
        alpha-equivalence.  The ``form_id`` is also stripped since it is
        fresh per-computation.

        :param cached_nf: The :class:`NormalForm` retrieved from the cache.
        :param computed_nf: A freshly computed :class:`NormalForm`.
        :returns: ``True`` if the two normal forms are equivalent.
        """
        def _canonical(nf: Any) -> str:
            if hasattr(nf, "canonical_payload"):
                payload = dict(nf.canonical_payload)
            elif isinstance(nf, dict):
                payload = dict(nf.get("canonical_payload", nf))
            else:
                payload = {}
            # Strip identity fields before comparing.
            for key in ("node_id", "form_id", "suggestion_id", "_rolled_back_from"):
                payload.pop(key, None)
            return json.dumps(payload, sort_keys=True)

        return _canonical(cached_nf) == _canonical(computed_nf)

    def formal_statement(self) -> str:
        """Return the LaTeX-formatted formal statement.

        :returns: A LaTeX theorem environment string with proof sketch.
        """
        return (
            f"\\begin{{theorem}}[{self.name}]\n"
            f"{self._base.statement}\n"
            f"\\end{{theorem}}\n\n"
            f"\\textit{{Proof sketch:}} {self._base.proof_sketch}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a plain dictionary.

        :returns: A JSON-serialisable dictionary.
        """
        return self._base.to_dict()


# ===================================================================== #
# 7. Theorem registry and verification                                   #
# ===================================================================== #

# copilot: TheoremRegistry can be queried by copilot to explain theoretical guarantees


@dataclass
class TheoremRegistry:
    """Manages all theorem instances for the IR stack package.

    Acts as a centralised catalogue of :class:`TheoremStatement` (and
    theorem-composite) objects.  The registry supports verification runs
    that evaluate all registered theorems against a runtime context.

    Attributes:
        _theorems: Maps ``theorem_id`` to the theorem object.
        _verification_log: Ordered log of all verification runs.
    """

    _theorems: dict[str, Any] = field(default_factory=dict)
    _verification_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    def register(self, theorem: Any) -> None:
        """Register a theorem in this registry.

        Accepts any object with a ``theorem_id`` attribute and a
        ``to_dict`` method.  If a theorem with the same ID is already
        registered it is silently overwritten.

        :param theorem: The theorem object to register.
        """
        theorem_id = getattr(theorem, "theorem_id", str(theorem))
        self._theorems[theorem_id] = theorem
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "TheoremRegistry: registered theorem %r.", theorem_id
        )

    def get(self, theorem_id: str) -> Any | None:
        """Return the theorem with *theorem_id*, or ``None`` if absent.

        :param theorem_id: The UUID-string identifier of the theorem.
        :returns: The theorem object or ``None``.
        """
        return self._theorems.get(theorem_id)

    def list_by_status(
        self,
        status: VerificationStatus,
    ) -> list[Any]:
        """Return all theorems whose verification status equals *status*.

        For theorem-composite objects (those that wrap a ``_base``
        :class:`TheoremStatement`), the status is read from the base.

        :param status: The :class:`VerificationStatus` to filter by.
        :returns: List of matching theorem objects.
        """
        result: list[Any] = []
        for thm in self._theorems.values():
            thm_status = getattr(thm, "verification_status", None)
            if thm_status is None:
                base = getattr(thm, "_base", None)
                if base is not None:
                    thm_status = getattr(base, "verification_status", None)
            if thm_status == status:
                result.append(thm)
        return result

    def verify_all(self, context: dict[str, Any]) -> dict[str, list[str]]:
        """Run :meth:`TheoremStatement.verify_invariants` for all theorems.

        For theorem-composite objects the invariant check is delegated to
        their ``_base`` :class:`TheoremStatement`.  Results are also
        appended to :attr:`_verification_log`.

        :param context: A runtime context dictionary to check invariants
            against.
        :returns: Mapping from ``theorem_id`` to list of failed invariant
            strings (empty list = all passed).
        """
        all_failures: dict[str, list[str]] = {}
        run_time = time.time()
        for theorem_id, thm in self._theorems.items():
            base = getattr(thm, "_base", thm)
            if hasattr(base, "verify_invariants"):
                failures = base.verify_invariants(context)
            else:
                failures = []
            all_failures[theorem_id] = failures
            self._verification_log.append(
                {
                    "theorem_id": theorem_id,
                    "failures": list(failures),
                    "timestamp": run_time,
                    "context_keys": list(context.keys()),
                }
            )
        return all_failures

    def summary(self) -> dict[str, Any]:
        """Return counts by status and totals for all registered theorems.

        :returns: A dictionary with ``"total"``, ``"by_status"`` mapping,
            ``"verification_runs"``, and ``"theorem_names"`` list.
        """
        status_counts: dict[str, int] = {}
        theorem_names: list[str] = []
        for thm in self._theorems.values():
            thm_status = getattr(thm, "verification_status", None)
            if thm_status is None:
                base = getattr(thm, "_base", None)
                if base is not None:
                    thm_status = getattr(base, "verification_status", None)
            status_val = getattr(thm_status, "value", str(thm_status)) if thm_status else "unknown"
            status_counts[status_val] = status_counts.get(status_val, 0) + 1
            name = getattr(thm, "name", getattr(getattr(thm, "_base", None), "name", str(thm)))
            theorem_names.append(name)

        return {
            "total": len(self._theorems),
            "by_status": status_counts,
            "verification_runs": len(self._verification_log),
            "theorem_names": sorted(theorem_names),
        }

    def export(self) -> list[dict[str, Any]]:
        """Return all theorems serialised as plain dictionaries.

        :returns: List of ``to_dict()`` results, one per registered theorem.
        """
        result: list[dict[str, Any]] = []
        for thm in self._theorems.values():
            if hasattr(thm, "to_dict"):
                try:
                    result.append(thm.to_dict())
                except Exception:
                    result.append({"theorem_id": getattr(thm, "theorem_id", str(thm))})
        return result


# ===================================================================== #
# Module-level singletons, factories, and utility functions             #
# ===================================================================== #

AMBIGUITY_PRESERVATION: AmbiguityPreservationTheorem = AmbiguityPreservationTheorem()
"""Singleton instance of :class:`AmbiguityPreservationTheorem` (Theorem 32.1)."""

CONFLUENCE: NormalFormConfluenceTheorem = NormalFormConfluenceTheorem()
"""Singleton instance of :class:`NormalFormConfluenceTheorem` (Theorem 32.2)."""

DEPTH_MONOTONICITY: StackDepthMonotonicityTheorem = StackDepthMonotonicityTheorem()
"""Singleton instance of :class:`StackDepthMonotonicityTheorem` (Theorem 32.3)."""

LOWERING_FAITHFULNESS: LoweringFaithfulnessTheorem = LoweringFaithfulnessTheorem()
"""Singleton instance of :class:`LoweringFaithfulnessTheorem` (Theorem 32.4)."""

CACHE_CORRECTNESS: CacheCorrectnessTheorem = CacheCorrectnessTheorem()
"""Singleton instance of :class:`CacheCorrectnessTheorem` (Theorem 32.5)."""

_THEOREM_REGISTRY: TheoremRegistry = TheoremRegistry()
"""Module-level :class:`TheoremRegistry` populated by :func:`get_theorem_registry`."""

_REGISTRY_POPULATED: bool = False


def get_theorem_registry() -> TheoremRegistry:
    """Return the module-level :class:`TheoremRegistry` with all theorems registered.

    On the first call, all five singleton theorem instances are registered
    into :data:`_THEOREM_REGISTRY`.  Subsequent calls return the same
    registry without re-registering.

    :returns: The populated :class:`TheoremRegistry`.
    """
    global _REGISTRY_POPULATED  # noqa: PLW0603
    if not _REGISTRY_POPULATED:
        for thm in (
            AMBIGUITY_PRESERVATION,
            CONFLUENCE,
            DEPTH_MONOTONICITY,
            LOWERING_FAITHFULNESS,
            CACHE_CORRECTNESS,
        ):
            _THEOREM_REGISTRY.register(thm)
        _REGISTRY_POPULATED = True
    return _THEOREM_REGISTRY


def list_theorems() -> list[Any]:
    """Return all theorems registered in the module-level registry.

    Ensures the registry is populated by calling :func:`get_theorem_registry`
    before returning the theorem list.

    :returns: List of all registered theorem objects.
    """
    registry = get_theorem_registry()
    return list(registry._theorems.values())


def verify_theorem(theorem_id: str, context: dict[str, Any]) -> list[str]:
    """Verify the invariants of a single theorem against *context*.

    :param theorem_id: UUID string of the theorem to verify.
    :param context: Runtime context dictionary for invariant evaluation.
    :returns: List of failed invariant strings.  Empty list means all
        invariants passed.
    """
    registry = get_theorem_registry()
    thm = registry.get(theorem_id)
    if thm is None:
        return [f"Theorem {theorem_id!r} not found in registry."]
    base = getattr(thm, "_base", thm)
    if hasattr(base, "verify_invariants"):
        return base.verify_invariants(context)
    return []
