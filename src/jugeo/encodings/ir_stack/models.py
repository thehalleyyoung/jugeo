r"""Internal representation (IR) stack data models for JuGeo.

This module defines the data structures underlying the IR stack described in
Chapter 32 of ``theory2.tex`` — *Internal Representations and the IR Stack*.
The IR stack is a layered pipeline through which surface-level terms are
progressively lowered into solver-ready encodings, with ambiguity marks
preserved throughout so that copilot oracle proposals are always traceable
back to their source syntactic positions.

Architecture overview
---------------------

The IR stack consists of an ordered sequence of :class:`IRLayer` objects.
Each layer corresponds to a distinct semantic level:

* **SURFACE** — raw parsed term structure with all syntactic sugar intact.
* **SEMANTIC** — desugared terms with name resolution applied.
* **LOGICAL** — obligations extracted, refinement predicates inlined.
* **SOLVER_READY** — terms fully encoded and ready for Z3 dispatch.
* **CACHED** — previously computed results reused across queries.
* **DELTA** — incremental changes relative to a prior layer.

Lowering passes (:class:`LoweringPass`) transform one layer kind into
another.  Ambiguity marks (:class:`AmbiguityMark`) travel with nodes as
they are lowered, ensuring that every unresolved syntactic choice remains
auditable at the solver-ready level.

.. math::

   \mathcal{S} = \bigl(\mathcal{L}_0, \mathcal{L}_1, \ldots, \mathcal{L}_n\bigr)

where each layer :math:`\mathcal{L}_k` is a triple

.. math::

   \mathcal{L}_k = \bigl(N_k,\; B_k,\; C_k\bigr)

with :math:`N_k` a dictionary of :class:`IRNode` objects,
:math:`B_k` a binding environment, and :math:`C_k` a list of
constraints.  A lowering pass :math:`\pi_{k \to k+1}` satisfies

.. math::

   \pi_{k \to k+1}(\mathcal{L}_k) = \mathcal{L}_{k+1}

such that if :math:`\mathcal{L}_k` contains an ambiguity mark
:math:`\mu` then :math:`\mathcal{L}_{k+1}` also contains :math:`\mu`
(ambiguity preservation).  The fully flattened stack is:

.. math::

   \bigoplus_{k=0}^{n} \mathcal{L}_k

where :math:`\oplus` is the layer merge operator (right-biased union on
bindings, union on nodes and constraints).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.encodings.ir_stack import _registry as _ir_registry  # type: ignore[import]
except Exception:  # pragma: no cover
    _ir_registry = None  # type: ignore[assignment]

try:
    from jugeo.kernel.trust import TrustLevel as _TrustLevel  # type: ignore[import]
except Exception:  # pragma: no cover
    _TrustLevel = None  # type: ignore[assignment]

try:
    from jugeo.evidence.channel import EvidenceChannel as _EvidenceChannel  # type: ignore[import]
except Exception:  # pragma: no cover
    _EvidenceChannel = None  # type: ignore[assignment]


# ===================================================================== #
# 1. Enumerations                                                        #
# ===================================================================== #


class IRNodeKind(str, Enum):
    """Semantic classification of a single IR node.

    Node kinds form a coarse taxonomy that lets lowering passes dispatch
    efficiently without inspecting payload contents.
    """

    EXPRESSION = "expression"
    STATEMENT = "statement"
    TYPE_TERM = "type_term"
    OBLIGATION = "obligation"
    ANNOTATION = "annotation"
    BINDING = "binding"
    QUANTIFIER = "quantifier"

    # ------------------------------------------------------------------
    def is_terminal(self) -> bool:
        """Return ``True`` if this kind cannot have semantic children.

        Annotations and bindings are structurally leaf-like in that they
        reference other nodes by ID rather than embedding them.
        """
        return self in (IRNodeKind.ANNOTATION, IRNodeKind.BINDING)

    def display_label(self) -> str:
        """Return a short human-readable label for UI and logging use."""
        _labels: dict[str, str] = {
            "expression": "Expr",
            "statement": "Stmt",
            "type_term": "Type",
            "obligation": "Obl",
            "annotation": "Ann",
            "binding": "Bind",
            "quantifier": "Quant",
        }
        return _labels.get(self.value, self.value.capitalize())

    def can_be_quantified(self) -> bool:
        """Return ``True`` if this kind may appear under a quantifier node."""
        return self in (
            IRNodeKind.EXPRESSION,
            IRNodeKind.TYPE_TERM,
            IRNodeKind.OBLIGATION,
        )

    def solver_priority(self) -> int:
        """Return a numeric priority for solver dispatch ordering.

        Lower values are processed first.  Obligations are highest priority
        because failing to encode them would silently lose proof goals.
        """
        _priority: dict[str, int] = {
            "obligation": 0,
            "type_term": 1,
            "expression": 2,
            "quantifier": 3,
            "statement": 4,
            "binding": 5,
            "annotation": 6,
        }
        return _priority.get(self.value, 99)


class IRLayerKind(str, Enum):
    """Identifies the semantic level of an :class:`IRLayer`.

    Layers are ordered from least processed (SURFACE) to most processed
    (SOLVER_READY / CACHED / DELTA).  The integer-valued ``depth_hint``
    method expresses the canonical ordering.
    """

    SURFACE = "surface"
    SEMANTIC = "semantic"
    LOGICAL = "logical"
    SOLVER_READY = "solver_ready"
    CACHED = "cached"
    DELTA = "delta"

    # ------------------------------------------------------------------
    def depth_hint(self) -> int:
        """Return a canonical depth for this layer kind.

        Used by :meth:`IRStack.merge_stacks` to interleave layers from
        different stacks in the correct order.
        """
        _depths: dict[str, int] = {
            "surface": 0,
            "semantic": 1,
            "logical": 2,
            "solver_ready": 3,
            "cached": 4,
            "delta": 5,
        }
        return _depths.get(self.value, 99)

    def is_solver_facing(self) -> bool:
        """Return ``True`` if this layer is consumed directly by a solver."""
        return self in (IRLayerKind.SOLVER_READY, IRLayerKind.CACHED)

    def display_label(self) -> str:
        """Return a short human-readable label for UI and logging."""
        _labels: dict[str, str] = {
            "surface": "Surf",
            "semantic": "Sem",
            "logical": "Log",
            "solver_ready": "Slvr",
            "cached": "Cach",
            "delta": "Δ",
        }
        return _labels.get(self.value, self.value.upper())

    def precedes(self, other: IRLayerKind) -> bool:
        """Return ``True`` if this layer kind is lowered before *other*."""
        return self.depth_hint() < other.depth_hint()


class NormalFormKind(str, Enum):
    """Identifies the kind of normal form stored in a :class:`NormalForm`.

    Beta and eta normal forms are the primary targets for obligation
    extraction; head-normal form is used during type unification.
    """

    HEAD_NORMAL = "head_normal"
    FULL_NORMAL = "full_normal"
    WEAK_HEAD = "weak_head"
    BETA_NORMAL = "beta_normal"
    ETA_NORMAL = "eta_normal"

    # ------------------------------------------------------------------
    def is_complete(self) -> bool:
        """Return ``True`` if this form fully reduces all redexes.

        HEAD_NORMAL and WEAK_HEAD may still contain un-reduced subterms.
        """
        return self in (NormalFormKind.FULL_NORMAL, NormalFormKind.BETA_NORMAL)

    def display_label(self) -> str:
        """Return a short label suitable for proof trees and UI."""
        _labels: dict[str, str] = {
            "head_normal": "HNF",
            "full_normal": "NF",
            "weak_head": "WHN",
            "beta_normal": "βNF",
            "eta_normal": "ηNF",
        }
        return _labels.get(self.value, self.value)

    def reduction_order(self) -> int:
        """Return the numeric cost of reaching this normal form.

        Higher values require more reduction work.  Used to pick the
        cheapest normal form when multiple forms are acceptable.
        """
        _order: dict[str, int] = {
            "weak_head": 0,
            "head_normal": 1,
            "beta_normal": 2,
            "eta_normal": 3,
            "full_normal": 4,
        }
        return _order.get(self.value, 99)


class AmbiguityKind(str, Enum):
    """Categorises the origin of an ambiguity in the IR.

    Structural ambiguities arise from parse-level choices (e.g., operator
    precedence, function vs. application).  Semantic ambiguities arise
    from scope or overload resolution.
    """

    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    RESOLUTION_PENDING = "resolution_pending"
    DEFINITIONAL = "definitional"
    OVERLOADED = "overloaded"

    # ------------------------------------------------------------------
    def is_resolvable_by_type(self) -> bool:
        """Return ``True`` if type information alone can resolve this kind."""
        return self in (AmbiguityKind.OVERLOADED, AmbiguityKind.SEMANTIC)

    def severity_order(self) -> int:
        """Return a numeric severity (higher = more problematic).

        ``RESOLUTION_PENDING`` has the highest severity because it blocks
        further lowering.
        """
        _severity: dict[str, int] = {
            "structural": 1,
            "semantic": 2,
            "definitional": 3,
            "overloaded": 4,
            "resolution_pending": 5,
        }
        return _severity.get(self.value, 0)

    def display_label(self) -> str:
        """Return a short label for diagnostics and proof traces."""
        _labels: dict[str, str] = {
            "structural": "Struct",
            "semantic": "Sem",
            "resolution_pending": "Pend",
            "definitional": "Def",
            "overloaded": "Over",
        }
        return _labels.get(self.value, self.value.capitalize())


class LoweringPassKind(str, Enum):
    """Identifies the role of a :class:`LoweringPass`.

    Passes are executed in the order defined by their ``pass_order``
    method, from DESUGARING to Z3_ENCODING.
    """

    DESUGARING = "desugaring"
    TYPE_ERASURE = "type_erasure"
    OBLIGATION_EXTRACTION = "obligation_extraction"
    CONSTRAINT_NORMALIZATION = "constraint_normalization"
    Z3_ENCODING = "z3_encoding"

    # ------------------------------------------------------------------
    def pass_order(self) -> int:
        """Return the canonical execution order for this pass kind."""
        _order: dict[str, int] = {
            "desugaring": 0,
            "type_erasure": 1,
            "obligation_extraction": 2,
            "constraint_normalization": 3,
            "z3_encoding": 4,
        }
        return _order.get(self.value, 99)

    def display_label(self) -> str:
        """Return a short label used in lowering pipeline diagnostics."""
        _labels: dict[str, str] = {
            "desugaring": "Desugar",
            "type_erasure": "Erase",
            "obligation_extraction": "ExtObl",
            "constraint_normalization": "NormCons",
            "z3_encoding": "Z3Enc",
        }
        return _labels.get(self.value, self.value)

    def produces_obligations(self) -> bool:
        """Return ``True`` if this pass kind may generate new obligations."""
        return self in (
            LoweringPassKind.OBLIGATION_EXTRACTION,
            LoweringPassKind.CONSTRAINT_NORMALIZATION,
        )


# ===================================================================== #
# 2. AmbiguityMark                                                       #
# ===================================================================== #


@dataclass
class AmbiguityMark:
    """Tracks unresolved syntactic or semantic ambiguities in the IR.

    An :class:`AmbiguityMark` is attached to one or more :class:`IRNode`
    objects and travels with them through the lowering pipeline.  When all
    ``ambiguous_nodes`` have been resolved the mark is considered closed.

    .. note::

        # copilot: AmbiguityMark resolution candidates are suggested by the copilot oracle

    Resolution candidates are keyed by ``node_id`` and list the possible
    desugared forms that the oracle considers viable.  The final choice is
    recorded by :meth:`resolve`.
    """

    mark_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ambiguous_nodes: list[str] = field(default_factory=list)
    resolution_candidates: dict[str, list[str]] = field(default_factory=dict)
    mark_kind: AmbiguityKind = field(default=AmbiguityKind.STRUCTURAL)
    confidence: float = field(default=0.5)

    # ------------------------------------------------------------------
    def add_ambiguous(self, node_id: str) -> None:
        """Register *node_id* as an ambiguous node.

        If *node_id* is already tracked this is a no-op so callers need not
        check for duplicates.
        """
        if node_id not in self.ambiguous_nodes:
            self.ambiguous_nodes.append(node_id)

    def resolve(self, node_id: str, chosen_candidate: str) -> bool:
        """Record a resolution choice for *node_id*.

        Removes *node_id* from ``ambiguous_nodes`` and records the chosen
        candidate under ``resolution_candidates``.  Returns ``True`` when
        the last ambiguous node has been resolved (i.e., the mark is now
        fully closed).

        :param node_id: ID of the node whose ambiguity is being resolved.
        :param chosen_candidate: The candidate form that was selected.
        :returns: ``True`` if this was the last outstanding ambiguity.
        """
        if node_id in self.ambiguous_nodes:
            self.ambiguous_nodes.remove(node_id)
        existing = self.resolution_candidates.get(node_id, [])
        if chosen_candidate not in existing:
            existing.append(chosen_candidate)
        self.resolution_candidates[node_id] = existing
        return len(self.ambiguous_nodes) == 0

    def is_resolved(self) -> bool:
        """Return ``True`` if there are no remaining unresolved nodes."""
        return len(self.ambiguous_nodes) == 0

    def candidates_for(self, node_id: str) -> list[str]:
        """Return the resolution candidates for *node_id*.

        Returns an empty list when *node_id* has no recorded candidates.
        """
        return self.resolution_candidates.get(node_id, [])

    def broadcast(self, target_nodes: list[str]) -> None:
        """Add all nodes in *target_nodes* to the ambiguous set.

        This is used by :meth:`IRNode.mark_ambiguous` when propagating a
        mark to descendant nodes that share the same ambiguity context.

        :param target_nodes: List of node IDs to mark as ambiguous.
        """
        for node_id in target_nodes:
            self.add_ambiguous(node_id)

    def prune_candidates(self, min_confidence: float) -> int:
        """Remove low-confidence candidates from all nodes.

        Uses a heuristic: candidates at index *k* in a list of length *n*
        are assigned a score of ``confidence * (n - k) / n``.  Candidates
        whose score falls below *min_confidence* are removed.

        :param min_confidence: Threshold below which candidates are pruned.
        :returns: Total number of candidates removed.
        """
        pruned_count = 0
        for node_id, candidates in list(self.resolution_candidates.items()):
            n = len(candidates)
            if n == 0:
                continue
            surviving: list[str] = []
            for k, candidate in enumerate(candidates):
                score = self.confidence * (n - k) / n
                if score >= min_confidence:
                    surviving.append(candidate)
                else:
                    pruned_count += 1
            self.resolution_candidates[node_id] = surviving
        return pruned_count

    def merge_marks(self, other: AmbiguityMark) -> AmbiguityMark:
        """Return a new :class:`AmbiguityMark` combining *self* and *other*.

        The resulting mark uses the lower confidence of the two inputs,
        takes the union of ambiguous nodes, and merges resolution candidate
        lists (other takes precedence for shared node IDs).

        :param other: Another :class:`AmbiguityMark` to combine with.
        :returns: A fresh :class:`AmbiguityMark` containing both marks' data.
        """
        combined_nodes: list[str] = list(
            dict.fromkeys(self.ambiguous_nodes + other.ambiguous_nodes)
        )
        combined_candidates: dict[str, list[str]] = {}
        for node_id in set(list(self.resolution_candidates.keys()) + list(other.resolution_candidates.keys())):
            self_cands = self.resolution_candidates.get(node_id, [])
            other_cands = other.resolution_candidates.get(node_id, [])
            merged: list[str] = list(dict.fromkeys(self_cands + other_cands))
            combined_candidates[node_id] = merged
        merged_kind = (
            other.mark_kind
            if other.mark_kind.severity_order() > self.mark_kind.severity_order()
            else self.mark_kind
        )
        return AmbiguityMark(
            mark_id=str(uuid.uuid4()),
            ambiguous_nodes=combined_nodes,
            resolution_candidates=combined_candidates,
            mark_kind=merged_kind,
            confidence=min(self.confidence, other.confidence),
        )


# ===================================================================== #
# 3. IRNode                                                              #
# ===================================================================== #


@dataclass
class IRNode:
    """A single node in the internal representation tree.

    :class:`IRNode` is the fundamental unit of the IR stack.  Nodes are
    mutable so that lowering passes can annotate them in-place, but each
    node carries a stable ``node_id`` so that identity is preserved across
    transformations.

    The ``payload`` field stores node-kind-specific data as a plain
    dictionary so that the IR remains serializable without requiring
    custom codec registration.

    The ``trust_level`` field shadows the trust algebra from
    ``evidence/trust.py`` at the IR level: ``0`` is untrusted (surface
    input), and higher values reflect increasing verification depth.
    """

    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    node_kind: IRNodeKind = field(default=IRNodeKind.EXPRESSION)
    payload: dict[str, Any] = field(default_factory=dict)
    children: list[IRNode] = field(default_factory=list)
    ambiguity_mark: AmbiguityMark | None = field(default=None)
    trust_level: int = field(default=0)
    source_ref: str = field(default="")

    # ------------------------------------------------------------------
    def _ancestor_ids(self) -> set[str]:
        """Return all node IDs reachable by following ``children`` edges.

        Used internally by :meth:`add_child` to detect cycles before they
        are introduced.
        """
        visited: set[str] = set()
        stack: list[IRNode] = list(self.children)
        while stack:
            current = stack.pop()
            if current.node_id in visited:
                continue
            visited.add(current.node_id)
            stack.extend(current.children)
        return visited

    def add_child(self, child: IRNode) -> None:
        """Append *child* to this node's children list.

        Raises :class:`ValueError` if *child* is the same node as *self*
        or if adding *child* would introduce a cycle (i.e., *self* is
        already reachable from *child*).

        :param child: The :class:`IRNode` to attach as a child.
        :raises ValueError: When the addition would create a cycle.
        """
        if child.node_id == self.node_id:
            raise ValueError(
                f"Cannot add node {self.node_id!r} as its own child."
            )
        reachable_from_child = child._ancestor_ids()
        if self.node_id in reachable_from_child:
            raise ValueError(
                f"Adding child {child.node_id!r} would create a cycle "
                f"through node {self.node_id!r}."
            )
        self.children.append(child)

    def remove_child(self, child_id: str) -> bool:
        """Remove the first child whose ``node_id`` matches *child_id*.

        :param child_id: The ID of the child node to remove.
        :returns: ``True`` if a matching child was found and removed,
            ``False`` otherwise.
        """
        original_length = len(self.children)
        self.children = [c for c in self.children if c.node_id != child_id]
        return len(self.children) < original_length

    def mark_ambiguous(self, mark: AmbiguityMark) -> None:
        """Attach *mark* to this node and propagate to QUANTIFIER children.

        For nodes of kind :attr:`IRNodeKind.QUANTIFIER`, the mark is also
        broadcast to all immediate children so that the entire quantifier
        scope is recorded as ambiguous.

        :param mark: The :class:`AmbiguityMark` to attach.
        """
        self.ambiguity_mark = mark
        mark.add_ambiguous(self.node_id)
        if self.node_kind == IRNodeKind.QUANTIFIER:
            child_ids = [c.node_id for c in self.children]
            mark.broadcast(child_ids)
            for child in self.children:
                child.ambiguity_mark = mark

    def clear_ambiguity(self) -> None:
        """Recursively clear ambiguity marks from this node and all children.

        After calling this method, ``ambiguity_mark`` will be ``None`` for
        this node and every descendant.
        """
        self.ambiguity_mark = None
        for child in self.children:
            child.clear_ambiguity()

    def to_dict(self) -> dict[str, Any]:
        """Serialise this node and all descendants to a plain dictionary.

        The returned value is JSON-serializable.  :class:`AmbiguityMark`
        objects are included inline.

        :returns: A recursively serialised representation of this node.
        """
        mark_dict: dict[str, Any] | None = None
        if self.ambiguity_mark is not None:
            m = self.ambiguity_mark
            mark_dict = {
                "mark_id": m.mark_id,
                "ambiguous_nodes": list(m.ambiguous_nodes),
                "resolution_candidates": dict(m.resolution_candidates),
                "mark_kind": m.mark_kind.value,
                "confidence": m.confidence,
            }
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind.value,
            "payload": dict(self.payload),
            "children": [c.to_dict() for c in self.children],
            "ambiguity_mark": mark_dict,
            "trust_level": self.trust_level,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IRNode:
        """Deserialise an :class:`IRNode` from a plain dictionary.

        Recursively deserialises all children.  If the payload contains an
        ``ambiguity_mark`` key its value is reconstituted as an
        :class:`AmbiguityMark`.

        :param data: Dictionary as produced by :meth:`to_dict`.
        :returns: A fully reconstructed :class:`IRNode`.
        """
        mark: AmbiguityMark | None = None
        raw_mark = data.get("ambiguity_mark")
        if raw_mark is not None:
            mark = AmbiguityMark(
                mark_id=raw_mark.get("mark_id", str(uuid.uuid4())),
                ambiguous_nodes=list(raw_mark.get("ambiguous_nodes", [])),
                resolution_candidates=dict(raw_mark.get("resolution_candidates", {})),
                mark_kind=AmbiguityKind(raw_mark.get("mark_kind", AmbiguityKind.STRUCTURAL.value)),
                confidence=float(raw_mark.get("confidence", 0.5)),
            )
        children: list[IRNode] = [
            cls.from_dict(child_data) for child_data in data.get("children", [])
        ]
        return cls(
            node_id=data.get("node_id", str(uuid.uuid4())),
            node_kind=IRNodeKind(data.get("node_kind", IRNodeKind.EXPRESSION.value)),
            payload=dict(data.get("payload", {})),
            children=children,
            ambiguity_mark=mark,
            trust_level=int(data.get("trust_level", 0)),
            source_ref=str(data.get("source_ref", "")),
        )

    def hash_content(self) -> str:
        """Return a SHA-256 fingerprint of this node's logical content.

        The hash covers ``node_kind``, ``payload``, and the sorted list of
        child hashes so that structurally identical trees produce the same
        fingerprint regardless of insertion order.

        :returns: Hex-encoded SHA-256 digest string.
        """
        child_hashes = sorted(child.hash_content() for child in self.children)
        canonical_payload = json.dumps(
            {"node_kind": self.node_kind.value, "payload": self.payload, "children": child_hashes},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    def matches(self, pattern: dict[str, Any]) -> bool:
        """Return ``True`` if this node matches *pattern*.

        A match requires:

        * If ``node_kind`` key is present in *pattern*, it must equal
          this node's ``node_kind`` (accepts both string and
          :class:`IRNodeKind` values).
        * For every other key in *pattern*, the corresponding key must be
          present in this node's ``payload`` with an equal value.

        :param pattern: A dictionary describing the match criteria.
        :returns: ``True`` if all pattern criteria are satisfied.
        """
        if "node_kind" in pattern:
            expected_kind = pattern["node_kind"]
            if isinstance(expected_kind, IRNodeKind):
                if self.node_kind != expected_kind:
                    return False
            else:
                if self.node_kind.value != str(expected_kind):
                    return False
        for key, value in pattern.items():
            if key == "node_kind":
                continue
            if self.payload.get(key) != value:
                return False
        return True

    def substitute(self, var_name: str, replacement: IRNode) -> IRNode:
        """Return a deep copy of this node with *var_name* replaced.

        All occurrences of ``var_name`` in the ``payload`` (as string
        values) are replaced with the ``node_id`` of *replacement*.  Child
        nodes are recursively substituted.  The original node is not
        modified.

        :param var_name: The variable name (string) to replace in payload.
        :param replacement: The :class:`IRNode` whose ``node_id`` replaces
            occurrences of *var_name*.
        :returns: A new :class:`IRNode` with substitutions applied.
        """
        new_payload: dict[str, Any] = {}
        for k, v in self.payload.items():
            if isinstance(v, str) and v == var_name:
                new_payload[k] = replacement.node_id
            else:
                new_payload[k] = v
        new_children: list[IRNode] = [
            child.substitute(var_name, replacement) for child in self.children
        ]
        return IRNode(
            node_id=str(uuid.uuid4()),
            node_kind=self.node_kind,
            payload=new_payload,
            children=new_children,
            ambiguity_mark=self.ambiguity_mark,
            trust_level=self.trust_level,
            source_ref=self.source_ref,
        )


# ===================================================================== #
# 4. IRLayer                                                             #
# ===================================================================== #


@dataclass
class IRLayer:
    """A single semantic stratum in the IR stack.

    An :class:`IRLayer` bundles a collection of :class:`IRNode` objects
    (keyed by ``node_id``), a lexical binding environment, and a list of
    constraint dictionaries.  Layers are created by lowering passes and
    consumed by the solver pipeline.

    The ``layer_depth`` field records this layer's position in its
    containing :class:`IRStack` and is managed by
    :meth:`IRStack.push`.
    """

    layer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    layer_kind: IRLayerKind = field(default=IRLayerKind.SURFACE)
    nodes: dict[str, IRNode] = field(default_factory=dict)
    bindings: dict[str, Any] = field(default_factory=dict)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    layer_depth: int = field(default=0)

    # ------------------------------------------------------------------
    def add_node(self, node: IRNode) -> None:
        """Insert *node* into this layer's node dictionary.

        If a node with the same ``node_id`` already exists it is
        silently replaced.

        :param node: The :class:`IRNode` to register.
        """
        self.nodes[node.node_id] = node

    def bind(self, name: str, value: Any) -> None:
        """Extend the binding environment with a name–value pair.

        :param name: The variable or symbol name to bind.
        :param value: The value to associate with *name*.
        """
        self.bindings[name] = value

    def lookup(self, name: str) -> Any | None:
        """Look up *name* in the binding environment.

        :param name: The name to look up.
        :returns: The associated value, or ``None`` if *name* is unbound.
        """
        return self.bindings.get(name)

    def add_constraint(self, constraint: dict[str, Any]) -> None:
        """Append *constraint* to the constraint list.

        :param constraint: A dictionary describing the constraint.  Callers
            are responsible for using a consistent schema.
        """
        self.constraints.append(constraint)

    def to_normal_form(self) -> IRLayer:
        """Return a copy of this layer with nodes and constraints sorted.

        Nodes are ordered by their ``hash_content()`` fingerprint and
        constraints are sorted by their string representation.  This
        provides a canonical form useful for cache keying and diffing.

        :returns: A new :class:`IRLayer` with deterministic ordering.
        """
        sorted_nodes: dict[str, IRNode] = dict(
            sorted(self.nodes.items(), key=lambda kv: kv[1].hash_content())
        )
        sorted_constraints: list[dict[str, Any]] = sorted(
            self.constraints, key=lambda c: json.dumps(c, sort_keys=True, default=str)
        )
        return IRLayer(
            layer_id=self.layer_id,
            layer_kind=self.layer_kind,
            nodes=sorted_nodes,
            bindings=dict(self.bindings),
            constraints=sorted_constraints,
            layer_depth=self.layer_depth,
        )

    def diff(self, other: IRLayer) -> dict[str, Any]:
        """Compute the structural difference between *self* and *other*.

        Returns a dictionary with the following keys:

        * ``added_nodes`` — node IDs present in *other* but not *self*.
        * ``removed_nodes`` — node IDs present in *self* but not *other*.
        * ``changed_nodes`` — node IDs present in both but with different
          ``hash_content`` values.
        * ``added_bindings`` — binding names added in *other*.
        * ``removed_bindings`` — binding names removed in *other*.

        :param other: The :class:`IRLayer` to diff against.
        :returns: A dictionary describing the structural changes.
        """
        self_ids = set(self.nodes.keys())
        other_ids = set(other.nodes.keys())
        added_nodes = list(other_ids - self_ids)
        removed_nodes = list(self_ids - other_ids)
        changed_nodes: list[str] = [
            nid
            for nid in self_ids & other_ids
            if self.nodes[nid].hash_content() != other.nodes[nid].hash_content()
        ]
        self_bindings = set(self.bindings.keys())
        other_bindings = set(other.bindings.keys())
        added_bindings = list(other_bindings - self_bindings)
        removed_bindings = list(self_bindings - other_bindings)
        return {
            "added_nodes": added_nodes,
            "removed_nodes": removed_nodes,
            "changed_nodes": changed_nodes,
            "added_bindings": added_bindings,
            "removed_bindings": removed_bindings,
        }

    def merge(self, other: IRLayer) -> IRLayer:
        """Merge *self* and *other* into a new :class:`IRLayer`.

        The merge is right-biased: when both layers define the same node ID
        or binding name, *other*'s value wins.  Constraints are
        concatenated (with duplicates preserved — callers may deduplicate
        if needed).

        :param other: The layer whose values take precedence in conflicts.
        :returns: A new merged :class:`IRLayer`.
        """
        merged_nodes: dict[str, IRNode] = {**self.nodes, **other.nodes}
        merged_bindings: dict[str, Any] = {**self.bindings, **other.bindings}
        merged_constraints: list[dict[str, Any]] = list(self.constraints) + list(other.constraints)
        return IRLayer(
            layer_id=str(uuid.uuid4()),
            layer_kind=other.layer_kind,
            nodes=merged_nodes,
            bindings=merged_bindings,
            constraints=merged_constraints,
            layer_depth=max(self.layer_depth, other.layer_depth),
        )

    def clone(self) -> IRLayer:
        """Return a deep copy of this layer.

        Uses a to-dict / from-dict round-trip to guarantee independence
        from the original.  All nodes, bindings, and constraints are fully
        copied.

        :returns: A new :class:`IRLayer` instance with the same content.
        """
        raw: dict[str, Any] = {
            "layer_id": str(uuid.uuid4()),
            "layer_kind": self.layer_kind.value,
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "bindings": json.loads(json.dumps(self.bindings, default=str)),
            "constraints": json.loads(json.dumps(self.constraints, default=str)),
            "layer_depth": self.layer_depth,
        }
        cloned_nodes: dict[str, IRNode] = {
            nid: IRNode.from_dict(nd) for nid, nd in raw["nodes"].items()
        }
        return IRLayer(
            layer_id=raw["layer_id"],
            layer_kind=IRLayerKind(raw["layer_kind"]),
            nodes=cloned_nodes,
            bindings=raw["bindings"],
            constraints=raw["constraints"],
            layer_depth=raw["layer_depth"],
        )


# ===================================================================== #
# 5. IRStack                                                             #
# ===================================================================== #


@dataclass
class IRStack:
    """An ordered stack of :class:`IRLayer` objects.

    The :class:`IRStack` is the central data structure in the JuGeo
    lowering pipeline.  Layers are pushed in order from surface (bottom)
    to solver-ready (top).  The stack supports flattening (merging all
    layers), projection (extracting a layer by kind), and serialization.

    ``creation_time`` is recorded as a Unix timestamp float so that
    stacks can be ordered and cached by age.
    """

    layers: list[IRLayer] = field(default_factory=list)
    stack_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    creation_time: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def push(self, layer: IRLayer) -> None:
        """Push *layer* onto the top of the stack.

        Sets ``layer.layer_depth`` to its index in the stack after
        appending so that depth values are always consistent.

        :param layer: The :class:`IRLayer` to push.
        """
        self.layers.append(layer)
        layer.layer_depth = len(self.layers) - 1

    def pop(self) -> IRLayer | None:
        """Remove and return the topmost layer.

        :returns: The topmost :class:`IRLayer`, or ``None`` if the stack
            is empty.
        """
        if not self.layers:
            return None
        return self.layers.pop()

    def peek(self) -> IRLayer | None:
        """Return the topmost layer without removing it.

        :returns: The topmost :class:`IRLayer`, or ``None`` if the stack
            is empty.
        """
        if not self.layers:
            return None
        return self.layers[-1]

    def depth(self) -> int:
        """Return the number of layers currently on the stack.

        :returns: Integer depth of the stack.
        """
        return len(self.layers)

    def flatten(self) -> IRLayer:
        """Merge all layers bottom-up into a single :class:`IRLayer`.

        Layers are merged in order from index ``0`` (bottom) upward using
        :meth:`IRLayer.merge`, which is right-biased — later layers win
        on conflicts.

        :returns: A single merged :class:`IRLayer` representing the entire
            stack, or an empty ``IRLayer`` if the stack is empty.
        """
        if not self.layers:
            return IRLayer()
        result = self.layers[0].clone()
        for layer in self.layers[1:]:
            result = result.merge(layer)
        return result

    def project_layer(self, layer_kind: IRLayerKind) -> IRLayer | None:
        """Find and return the first layer matching *layer_kind*.

        Layers are searched from bottom (index 0) to top.

        :param layer_kind: The :class:`IRLayerKind` to search for.
        :returns: The first matching :class:`IRLayer`, or ``None`` if no
            layer of that kind is present.
        """
        for layer in self.layers:
            if layer.layer_kind == layer_kind:
                return layer
        return None

    def merge_stacks(self, other: IRStack) -> IRStack:
        """Create a new stack by interleaving layers from *self* and *other*.

        Layers from both stacks are combined and sorted by
        ``layer_depth``.  When two layers share the same depth, *other*'s
        layer is placed after *self*'s so that it wins on subsequent
        flatten operations.

        :param other: The :class:`IRStack` to interleave with.
        :returns: A new :class:`IRStack` with all layers from both inputs.
        """
        all_layers = list(self.layers) + list(other.layers)
        all_layers.sort(key=lambda l: (l.layer_depth, l.layer_id))
        new_stack = IRStack(
            stack_id=str(uuid.uuid4()),
            creation_time=time.time(),
            metadata={**self.metadata, **other.metadata},
        )
        for layer in all_layers:
            new_stack.push(layer.clone())
        return new_stack

    def validate(self) -> list[str]:
        """Validate the stack's structural invariants.

        Checks performed:

        * ``layer_depth`` values are non-decreasing and match index positions.
        * No two layers share the same ``layer_id``.

        :returns: A list of human-readable error strings.  An empty list
            means the stack is valid.
        """
        errors: list[str] = []
        seen_ids: set[str] = set()
        for idx, layer in enumerate(self.layers):
            if layer.layer_id in seen_ids:
                errors.append(
                    f"Duplicate layer_id {layer.layer_id!r} at index {idx}."
                )
            seen_ids.add(layer.layer_id)
            if layer.layer_depth != idx:
                errors.append(
                    f"Layer at index {idx} has layer_depth={layer.layer_depth}, "
                    f"expected {idx}."
                )
        return errors

    def serialize(self) -> dict[str, Any]:
        """Serialise the entire stack to a JSON-serializable dictionary.

        :returns: A dictionary suitable for ``json.dumps``.
        """
        return {
            "stack_id": self.stack_id,
            "creation_time": self.creation_time,
            "metadata": self.metadata,
            "layers": [
                {
                    "layer_id": layer.layer_id,
                    "layer_kind": layer.layer_kind.value,
                    "layer_depth": layer.layer_depth,
                    "nodes": {nid: node.to_dict() for nid, node in layer.nodes.items()},
                    "bindings": layer.bindings,
                    "constraints": layer.constraints,
                }
                for layer in self.layers
            ],
        }


# ===================================================================== #
# 6. NormalForm                                                          #
# ===================================================================== #


@dataclass
class NormalForm:
    """Stores a term's canonical form together with its reduction history.

    :class:`NormalForm` records the original term, the fully reduced
    canonical form, the sequence of reduction steps taken, and the kind of
    normal form achieved.  Reduction steps are dictionaries with at minimum
    the keys ``rule`` (string) and ``position`` (list of ints describing a
    path into the term tree).

    This class is used by the obligation extraction pass to ensure that
    type constraints are presented to the solver in their most reduced form,
    minimising the chance of triggering incomplete decision procedures.
    """

    form_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    canonical: dict[str, Any] = field(default_factory=dict)
    original: dict[str, Any] = field(default_factory=dict)
    reduction_steps: list[dict[str, Any]] = field(default_factory=list)
    normal_form_kind: NormalFormKind = field(default=NormalFormKind.HEAD_NORMAL)

    # ------------------------------------------------------------------
    def is_normal(self) -> bool:
        """Return ``True`` if the term is already in normal form.

        A term is normal if either no reduction steps have been recorded or
        the canonical form is structurally equal to the original.

        :returns: ``True`` when no reductions are necessary.
        """
        if not self.reduction_steps:
            return True
        return json.dumps(self.canonical, sort_keys=True, default=str) == json.dumps(
            self.original, sort_keys=True, default=str
        )

    def reduce_step(self) -> bool:
        """Perform one synthetic beta/eta reduction step.

        Looks for the first ``lambda``-keyed entry in ``canonical`` and
        performs a simple inlining: if ``canonical`` contains a ``"lambda"``
        key paired with an ``"arg"`` key, the body is inlined with the
        argument substituted for the bound variable.  Records the step in
        ``reduction_steps``.

        :returns: ``True`` if a reduction was performed, ``False`` if the
            term is already in head-normal form with respect to this heuristic.
        """
        if "lambda" not in self.canonical or "arg" not in self.canonical:
            return False
        bound_var = self.canonical.get("lambda", "x")
        body = self.canonical.get("body", {})
        arg = self.canonical.get("arg")
        step: dict[str, Any] = {
            "rule": "beta",
            "position": [],
            "bound_var": bound_var,
            "arg": arg,
        }
        # Perform shallow substitution: replace occurrences of bound_var in body
        new_body: dict[str, Any] = {}
        for k, v in body.items():
            if v == bound_var:
                new_body[k] = arg
            else:
                new_body[k] = v
        self.reduction_steps.append(step)
        self.canonical = new_body
        return True

    def reduce_fully(self, max_steps: int = 1000) -> int:
        """Reduce to normal form by calling :meth:`reduce_step` repeatedly.

        Stops when no further reduction is possible or *max_steps* has been
        reached, whichever comes first.

        :param max_steps: Upper bound on the number of reduction steps.
        :returns: The total number of steps actually performed.
        """
        steps_taken = 0
        for _ in range(max_steps):
            if not self.reduce_step():
                break
            steps_taken += 1
        return steps_taken

    def compare(self, other: NormalForm) -> int:
        """Compare the canonical forms of *self* and *other*.

        :returns: ``-1`` if *self* < *other*, ``0`` if equal, ``1`` if
            *self* > *other* (lexicographic comparison of JSON serialization).
        """
        self_str = json.dumps(self.canonical, sort_keys=True, default=str)
        other_str = json.dumps(other.canonical, sort_keys=True, default=str)
        if self_str < other_str:
            return -1
        if self_str > other_str:
            return 1
        return 0

    def hash_canonical(self) -> str:
        """Return a SHA-256 hex digest of the canonical form.

        :returns: A hex-encoded SHA-256 string for use as a cache key.
        """
        serialized = json.dumps(self.canonical, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def reconstruct_original(self) -> dict[str, Any]:
        """Replay reduction steps in reverse to reconstruct the original term.

        Each step in ``reduction_steps`` must contain ``"bound_var"`` and
        ``"arg"`` keys to allow the inverse substitution.  Steps that
        lack these keys are skipped with a warning comment in the result.

        :returns: An approximation of the original term dictionary.
        """
        reconstructed = dict(self.canonical)
        for step in reversed(self.reduction_steps):
            rule = step.get("rule", "")
            if rule == "beta":
                bound_var = step.get("bound_var")
                arg = step.get("arg")
                if bound_var is not None and arg is not None:
                    wrapped: dict[str, Any] = {
                        "lambda": bound_var,
                        "arg": arg,
                        "body": reconstructed,
                    }
                    reconstructed = wrapped
        return reconstructed

    def validate_reduction(self) -> bool:
        """Check that all recorded reduction steps have the required keys.

        A valid reduction step must contain at minimum the keys ``"rule"``
        and ``"position"``.

        :returns: ``True`` if every step is well-formed, ``False`` otherwise.
        """
        required_keys = {"rule", "position"}
        return all(required_keys.issubset(step.keys()) for step in self.reduction_steps)


# ===================================================================== #
# 7. LoweringPass                                                        #
# ===================================================================== #


@dataclass
class LoweringPass:
    """Describes a single transformation from one IR layer kind to another.

    :class:`LoweringPass` is the unit of work in the lowering pipeline.
    Each pass declares its input and output layer kinds, carries a list of
    transformation descriptors (plain dictionaries), and optionally
    preserves ambiguity marks.

    Transformations are represented as dictionaries with at minimum a
    ``"kind"`` key.  The :meth:`apply` method interprets these descriptors
    and mutates a copy of the input layer.  Callers can compose passes with
    :meth:`compose_with` to build multi-step pipelines.
    """

    pass_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pass_name: str = field(default="unnamed_pass")
    input_layer: IRLayerKind = field(default=IRLayerKind.SURFACE)
    output_layer: IRLayerKind = field(default=IRLayerKind.SEMANTIC)
    transformations: list[dict[str, Any]] = field(default_factory=list)
    ambiguity_preserved: bool = field(default=True)

    # ------------------------------------------------------------------
    def apply(self, layer: IRLayer) -> IRLayer:
        """Apply this pass to *layer* and return the transformed layer.

        For each transformation descriptor in ``transformations``, the pass
        dispatches on the ``"kind"`` key:

        * ``"rename_binding"`` — renames a binding from ``"from"`` to
          ``"to"`` in the layer's binding environment.
        * ``"add_constraint"`` — appends the ``"constraint"`` value to the
          layer's constraint list.
        * ``"elevate_trust"`` — increments ``trust_level`` of all nodes by
          ``"delta"`` (default 1).
        * Any other kind is recorded in the result layer's metadata.

        :param layer: The :class:`IRLayer` to transform.
        :returns: A new :class:`IRLayer` with transformations applied.
        """
        result = layer.clone()
        result.layer_kind = self.output_layer
        result.layer_id = str(uuid.uuid4())
        applied_kinds: list[str] = []
        for transformation in self.transformations:
            kind = transformation.get("kind", "")
            if kind == "rename_binding":
                old_name = transformation.get("from", "")
                new_name = transformation.get("to", "")
                if old_name in result.bindings:
                    result.bindings[new_name] = result.bindings.pop(old_name)
            elif kind == "add_constraint":
                constraint = transformation.get("constraint", {})
                result.add_constraint(dict(constraint))
            elif kind == "elevate_trust":
                delta = int(transformation.get("delta", 1))
                for node in result.nodes.values():
                    node.trust_level += delta
            else:
                applied_kinds.append(kind)
        if applied_kinds:
            result.bindings.setdefault("_applied_unknown_kinds", applied_kinds)
        return result

    def verify_ambiguity_preservation(self, before: IRLayer, after: IRLayer) -> bool:
        """Check that the pass did not silently drop ambiguity marks.

        Counts the number of nodes with a non-``None`` ``ambiguity_mark``
        in both layers.  The after-count must be >= the before-count.

        :param before: The layer before the pass was applied.
        :param after: The layer after the pass was applied.
        :returns: ``True`` if ambiguity marks were preserved or increased.
        """
        before_count = sum(
            1 for node in before.nodes.values() if node.ambiguity_mark is not None
        )
        after_count = sum(
            1 for node in after.nodes.values() if node.ambiguity_mark is not None
        )
        return after_count >= before_count

    def rollback(self, after_layer: IRLayer, original_layer: IRLayer) -> IRLayer:
        """Return a copy of *original_layer* annotated with rollback metadata.

        The rollback copy has a fresh ``layer_id`` and its bindings include
        a ``"_rolled_back_from"`` entry pointing at ``after_layer.layer_id``
        so that audit logs can trace the decision.

        :param after_layer: The layer that is being discarded.
        :param original_layer: The layer to restore.
        :returns: A clone of *original_layer* with rollback metadata.
        """
        restored = original_layer.clone()
        restored.bindings["_rolled_back_from"] = after_layer.layer_id
        restored.bindings["_rollback_pass"] = self.pass_name
        return restored

    def compose_with(self, other: LoweringPass) -> LoweringPass:
        """Return a new :class:`LoweringPass` composed of *self* then *other*.

        The composed pass inherits *self*'s ``input_layer`` and *other*'s
        ``output_layer``.  Transformations are concatenated in order.
        Ambiguity is preserved only if both passes preserve it.

        :param other: The pass to run after *self*.
        :returns: A new :class:`LoweringPass` representing the composition.
        """
        return LoweringPass(
            pass_id=str(uuid.uuid4()),
            pass_name=f"{self.pass_name}>>{other.pass_name}",
            input_layer=self.input_layer,
            output_layer=other.output_layer,
            transformations=list(self.transformations) + list(other.transformations),
            ambiguity_preserved=self.ambiguity_preserved and other.ambiguity_preserved,
        )

    def explain(self) -> str:
        """Return a human-readable description of this pass's transformations.

        :returns: A multi-line string summarising each transformation.
        """
        lines: list[str] = [
            f"Pass '{self.pass_name}'",
            f"  Input layer : {self.input_layer.display_label()}",
            f"  Output layer: {self.output_layer.display_label()}",
            f"  Ambiguity preserved: {self.ambiguity_preserved}",
            f"  Transformations ({len(self.transformations)}):",
        ]
        for idx, t in enumerate(self.transformations):
            kind = t.get("kind", "<unknown>")
            rest = {k: v for k, v in t.items() if k != "kind"}
            lines.append(f"    [{idx}] {kind} {rest}")
        return "\n".join(lines)

    def statistics(self) -> dict[str, Any]:
        """Return a statistics dictionary for monitoring and profiling.

        :returns: A dictionary with ``pass_name``, ``transformation_count``,
            ``input_layer``, ``output_layer``, and ``ambiguity_preserved``.
        """
        return {
            "pass_name": self.pass_name,
            "transformation_count": len(self.transformations),
            "input_layer": self.input_layer.value,
            "output_layer": self.output_layer.value,
            "ambiguity_preserved": self.ambiguity_preserved,
        }

    def to_proof_step(self) -> dict[str, Any]:
        """Return a proof-step representation of this pass.

        The proof step conforms to the JuGeo evidence schema: it has a
        ``"kind"`` of ``"lowering_pass"``, a ``"claim"`` summarising the
        transformation, and ``"justification"`` listing the individual
        transformation descriptors.

        :returns: A dictionary representing this pass as a proof step.
        """
        return {
            "kind": "lowering_pass",
            "pass_id": self.pass_id,
            "pass_name": self.pass_name,
            "claim": (
                f"Layer {self.input_layer.display_label()} is lowered to "
                f"{self.output_layer.display_label()} by pass '{self.pass_name}'."
            ),
            "justification": list(self.transformations),
            "ambiguity_preserved": self.ambiguity_preserved,
        }


# ===================================================================== #
# 8. Module-level helper functions                                       #
# ===================================================================== #


def create_ir_node(kind: IRNodeKind, payload: dict[str, Any]) -> IRNode:
    """Create a fresh :class:`IRNode` with the given kind and payload.

    This is the preferred factory for simple node construction; it assigns
    a new ``node_id`` and leaves all other fields at their defaults.

    :param kind: The :class:`IRNodeKind` for the new node.
    :param payload: Initial payload dictionary for the node.
    :returns: A new :class:`IRNode` instance.
    """
    return IRNode(
        node_id=str(uuid.uuid4()),
        node_kind=kind,
        payload=dict(payload),
    )


def create_ir_stack() -> IRStack:
    """Create and return a new empty :class:`IRStack`.

    The returned stack has a fresh ``stack_id`` and ``creation_time`` but
    no layers.  Use :meth:`IRStack.push` to add layers.

    :returns: A new empty :class:`IRStack`.
    """
    return IRStack(
        stack_id=str(uuid.uuid4()),
        creation_time=time.time(),
        metadata={},
    )


def create_ambiguity_mark(
    kind: AmbiguityKind = AmbiguityKind.STRUCTURAL,
) -> AmbiguityMark:
    """Create a fresh :class:`AmbiguityMark` of the given kind.

    The returned mark has no ambiguous nodes and default confidence of
    ``0.5``.  Use :meth:`AmbiguityMark.add_ambiguous` to register nodes.

    :param kind: The :class:`AmbiguityKind` for the new mark.
    :returns: A new :class:`AmbiguityMark` with no recorded ambiguities.
    """
    return AmbiguityMark(
        mark_id=str(uuid.uuid4()),
        ambiguous_nodes=[],
        resolution_candidates={},
        mark_kind=kind,
        confidence=0.5,
    )
