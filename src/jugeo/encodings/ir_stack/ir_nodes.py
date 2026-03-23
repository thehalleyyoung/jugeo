r"""IR node taxonomy, payload encoding, and tree operations for the JuGeo IR stack.

This module implements Chapter 32 §1 of ``theory2.tex`` — *IR Node Taxonomy and
Implementation* — providing the concrete Python classes that populate and
manipulate :class:`~jugeo.encodings.ir_stack.models.IRNode` trees inside the
IR stack pipeline.

The node taxonomy organises every IR operation into one of the seven
:class:`~jugeo.encodings.ir_stack.models.IRNodeKind` categories and provides a
registry of per-kind metadata so that lowering passes and the copilot oracle can
dispatch efficiently.

Node type hierarchy
-------------------

.. math::

   \mathrm{IRNode} = \bigl(\mathrm{id},\; \kappa,\; \phi,\; \mathcal{C},\; \mu\bigr)

where :math:`\kappa \in \mathtt{IRNodeKind}` is the node kind,
:math:`\phi` is the kind-specific *payload* dictionary, :math:`\mathcal{C}` is
the ordered list of child nodes, and :math:`\mu` is an optional
:class:`~jugeo.encodings.ir_stack.models.AmbiguityMark`.

The kind hierarchy is a DAG :math:`(K, \preceq)` where
:math:`\kappa_1 \preceq \kappa_2` means "kind :math:`\kappa_1` is a subkind of
:math:`\kappa_2`":

.. math::

   \mathtt{EXPRESSION} \preceq \mathtt{EXPRESSION} \\
   \mathtt{QUANTIFIER} \preceq \mathtt{EXPRESSION} \\
   \mathtt{OBLIGATION} \preceq \mathtt{STATEMENT}

A *terminal* kind is one with no strict subkinds:

.. math::

   \mathrm{terminal}(\kappa) \iff \nexists\, \kappa' : \kappa' \prec \kappa

Ambiguity propagation satisfies the *monotone coverage* property: for any tree
rooted at :math:`r` and any propagation event :math:`(\mathrm{mark}, r)`,

.. math::

   |\mathrm{ambiguous}(r)_{\mathrm{after}}| \;\geq\; |\mathrm{ambiguous}(r)_{\mathrm{before}}|

Substitution :math:`[x \mapsto t]` is the standard capture-avoiding
substitution lifted to IR trees:

.. math::

   [x \mapsto t](r) = r' \;\text{where every free occurrence of } x
   \;\text{in } r \;\text{is replaced by } t

Architecture overview
~~~~~~~~~~~~~~~~~~~~~

* :class:`IRNodeKindRegistry` — kind metadata and hierarchy registry.
* :class:`NodePayload` — typed, versioned payload container.
* :class:`AmbiguityPropagator` — mark propagation and resolution.
* :class:`NodeSubstituter` — capture-avoiding variable substitution.
* :class:`IRTreeWalker` — DFS/BFS traversal and search utilities.
* :class:`CopilotNodeSuggestor` — oracle-assisted node kind and payload suggestion.

Theory alignment
~~~~~~~~~~~~~~~~

* §32.1 — Node taxonomy and kind hierarchy
* §32.2 — Payload encoding and schema validation
* §32.3 — Ambiguity mark propagation
* §32.4 — Variable substitution and alpha-renaming
* §32.5 — Tree traversal algorithms
* §32.6 — Copilot oracle integration for node suggestion
"""

from __future__ import annotations

import collections
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generator, Iterator, List, Optional, Sequence, Tuple

try:
    from jugeo.encodings.ir_stack.models import (  # type: ignore[import]
        IRNode,
        IRLayer,
        IRStack,
        IRNodeKind,
        IRLayerKind,
        AmbiguityMark,
        AmbiguityKind,
    )
except Exception:  # pragma: no cover
    pass  # stubs handled in models.py

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm  # type: ignore[import]
except Exception:  # pragma: no cover
    class JudgmentTerm:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel  # type: ignore[import]
except Exception:  # pragma: no cover
    class TrustAlgebra:  # type: ignore[no-redef]
        pass
    class TrustLevel:  # type: ignore[no-redef]
        pass


# ===================================================================== #
# 1. Node taxonomy — IRNodeKindRegistry                                  #
# ===================================================================== #


@dataclass
class IRNodeKindRegistry:
    """Registry that stores metadata and parent-child relationships for IR node kinds.

    The registry is the authoritative source for the kind hierarchy described
    in §32.1 of ``theory2.tex``.  Lowering passes and the copilot oracle query
    it to determine how a node should be processed without inspecting its
    payload directly.

    Attributes
    ----------
    _kind_metadata:
        Mapping from kind value string to an arbitrary metadata dictionary.
    _kind_parents:
        Mapping from kind value string to the list of parent kind value strings
        in the DAG.
    _registered_at:
        Unix timestamp for when each kind was registered.
    """

    _kind_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    _kind_parents: dict[str, list[str]] = field(default_factory=dict)
    _registered_at: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def register_kind(
        self,
        kind: "IRNodeKind",
        metadata: dict[str, Any],
        parent_kinds: list["IRNodeKind"] | None = None,
    ) -> None:
        """Register *kind* with its metadata and optional parent kinds.

        If *kind* is already registered, its metadata is merged (incoming
        wins on key conflicts) and new parents are appended.

        :param kind: The :class:`IRNodeKind` to register.
        :param metadata: Arbitrary metadata dictionary for this kind.
        :param parent_kinds: Optional list of parent :class:`IRNodeKind`
            values in the DAG.
        """
        import time as _time
        key = kind.value
        existing = self._kind_metadata.get(key, {})
        existing.update(metadata)
        self._kind_metadata[key] = existing
        if key not in self._registered_at:
            self._registered_at[key] = _time.time()
        if parent_kinds:
            existing_parents = self._kind_parents.get(key, [])
            for parent in parent_kinds:
                parent_val = parent.value
                if parent_val not in existing_parents:
                    existing_parents.append(parent_val)
            self._kind_parents[key] = existing_parents
        else:
            self._kind_parents.setdefault(key, [])

    def get_metadata(self, kind: "IRNodeKind") -> dict[str, Any]:
        """Return the metadata dictionary for *kind*.

        Returns an empty dictionary when *kind* has not been registered.

        :param kind: The :class:`IRNodeKind` whose metadata is requested.
        :returns: The metadata dictionary (may be empty).
        """
        return dict(self._kind_metadata.get(kind.value, {}))

    def is_subkind(self, kind: "IRNodeKind", parent: "IRNodeKind") -> bool:
        """Return ``True`` if *kind* is a subkind (descendant) of *parent*.

        Performs a BFS over the parent edges.  A kind is considered a
        subkind of itself (reflexive).

        :param kind: The :class:`IRNodeKind` to test.
        :param parent: The :class:`IRNodeKind` to test against.
        :returns: ``True`` if *kind* == *parent* or *parent* is reachable
            via parent edges from *kind*.
        """
        if kind.value == parent.value:
            return True
        queue: collections.deque[str] = collections.deque(
            self._kind_parents.get(kind.value, [])
        )
        visited: set[str] = {kind.value}
        while queue:
            current = queue.popleft()
            if current == parent.value:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(self._kind_parents.get(current, []))
        return False

    def list_terminal_kinds(self) -> list["IRNodeKind"]:
        """Return kinds that have no registered children in the hierarchy.

        A terminal kind is one that no other registered kind declares as a
        parent.

        :returns: A list of :class:`IRNodeKind` objects with no children.
        """
        all_children: set[str] = set()
        for parents in self._kind_parents.values():
            all_children.update(parents)
        terminal_keys = [
            key for key in self._kind_metadata
            if key not in all_children
        ]
        result: list[IRNodeKind] = []
        for key in terminal_keys:
            try:
                result.append(IRNodeKind(key))
            except (ValueError, NameError):
                pass
        return result

    def taxonomy_tree(self) -> dict[str, list[str]]:
        """Return the full parent-child tree as a plain dictionary.

        Keys are kind value strings; values are lists of child kind value
        strings (i.e., the tree is inverted from the internal parent
        representation).

        :returns: A ``{parent_kind_value: [child_kind_value, ...]}`` dict.
        """
        tree: dict[str, list[str]] = {k: [] for k in self._kind_metadata}
        for child_key, parents in self._kind_parents.items():
            for parent_key in parents:
                if parent_key in tree:
                    if child_key not in tree[parent_key]:
                        tree[parent_key].append(child_key)
        return tree

    def validate_node(self, node: "IRNode") -> list[str]:
        """Validate *node* against the rules registered for its kind.

        Checks performed:

        * The node kind must have been registered.
        * If the kind metadata includes a ``"required_payload_keys"`` entry,
          all listed keys must appear in ``node.payload``.
        * The ``node_id`` must be non-empty.

        :param node: The :class:`IRNode` to validate.
        :returns: A list of error strings; empty if valid.
        """
        errors: list[str] = []
        kind_val = node.node_kind.value
        if kind_val not in self._kind_metadata:
            errors.append(
                f"Node {node.node_id!r} has unregistered kind {kind_val!r}."
            )
            return errors
        if not node.node_id:
            errors.append("IRNode.node_id must not be empty.")
        meta = self._kind_metadata[kind_val]
        required_keys: list[str] = meta.get("required_payload_keys", [])
        for req_key in required_keys:
            if req_key not in node.payload:
                errors.append(
                    f"Node {node.node_id!r} (kind={kind_val!r}) is missing "
                    f"required payload key {req_key!r}."
                )
        return errors


# ===================================================================== #
# 2. Payload encoding — NodePayload                                      #
# ===================================================================== #


@dataclass
class NodePayload:
    """Typed, versioned container for the data stored inside an IR node.

    :class:`NodePayload` wraps the raw ``payload`` dictionary of an
    :class:`IRNode` with schema enforcement, merging semantics, and
    content-addressed hashing.

    Attributes
    ----------
    payload_id:
        Unique identifier for this payload instance.
    kind:
        The :class:`IRNodeKind` that owns this payload.
    data:
        The actual payload data as a plain dictionary.
    schema_version:
        Integer schema version for forwards/backwards compatibility.
    encoding:
        Serialization encoding used, currently always ``"json"``.
    """

    kind: "IRNodeKind"
    payload_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    data: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    encoding: str = "json"

    # ------------------------------------------------------------------
    @classmethod
    def encode(
        cls,
        kind: "IRNodeKind",
        raw_data: dict[str, Any],
    ) -> "NodePayload":
        """Create a new :class:`NodePayload` from *raw_data* for the given *kind*.

        The raw data is shallow-copied so that mutations to the original do
        not affect the payload.

        :param kind: The :class:`IRNodeKind` this payload belongs to.
        :param raw_data: The source data dictionary to encode.
        :returns: A new :class:`NodePayload` instance.
        """
        return cls(
            payload_id=str(uuid.uuid4()),
            kind=kind,
            data=dict(raw_data),
            schema_version=1,
            encoding="json",
        )

    def decode(self) -> dict[str, Any]:
        """Return the decoded payload data with kind-specific defaults filled in.

        Missing keys are populated with canonical defaults for the kind:

        * EXPRESSION — ``{"expr": "", "type_hint": None}``
        * STATEMENT  — ``{"stmt": "", "effects": []}``
        * TYPE_TERM  — ``{"repr": "", "bindings": {}}``
        * OBLIGATION — ``{"goal": "", "context": {}, "discharged": False}``
        * ANNOTATION — ``{"label": "", "source_ref": ""}``
        * BINDING    — ``{"var": "", "value": None}``
        * QUANTIFIER — ``{"binder": "forall", "bound_vars": []}``

        :returns: A dictionary combining stored data and per-kind defaults.
        """
        _defaults: dict[str, dict[str, Any]] = {
            "expression": {"expr": "", "type_hint": None},
            "statement": {"stmt": "", "effects": []},
            "type_term": {"repr": "", "bindings": {}},
            "obligation": {"goal": "", "context": {}, "discharged": False},
            "annotation": {"label": "", "source_ref": ""},
            "binding": {"var": "", "value": None},
            "quantifier": {"binder": "forall", "bound_vars": []},
        }
        result: dict[str, Any] = dict(
            _defaults.get(self.kind.value, {})
        )
        result.update(self.data)
        return result

    def validate(self) -> list[str]:
        """Return a list of validation errors for this payload.

        Checks:

        * ``payload_id`` must be non-empty.
        * ``data`` must be a dictionary.
        * ``schema_version`` must be a positive integer.

        :returns: A list of error strings; empty if valid.
        """
        errors: list[str] = []
        if not self.payload_id:
            errors.append("NodePayload.payload_id must not be empty.")
        if not isinstance(self.data, dict):
            errors.append("NodePayload.data must be a dictionary.")
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            errors.append(
                "NodePayload.schema_version must be a positive integer."
            )
        return errors

    def merge(self, other: "NodePayload") -> "NodePayload":
        """Return a new payload combining *self* and *other*.

        *other* wins on key conflicts.  The result has a fresh
        ``payload_id`` and uses the higher of the two schema versions.

        :param other: The :class:`NodePayload` whose data takes precedence.
        :returns: A new merged :class:`NodePayload`.
        """
        merged_data: dict[str, Any] = dict(self.data)
        merged_data.update(other.data)
        return NodePayload(
            payload_id=str(uuid.uuid4()),
            kind=other.kind,
            data=merged_data,
            schema_version=max(self.schema_version, other.schema_version),
            encoding=other.encoding,
        )

    def to_bytes(self) -> bytes:
        """Return a deterministic byte encoding of this payload's data.

        Uses ``json.dumps`` with sorted keys to ensure reproducibility.

        :returns: UTF-8-encoded JSON bytes.
        """
        return json.dumps(self.data, sort_keys=True, default=str).encode("utf-8")

    def hash(self) -> str:
        """Return the SHA-256 hex digest of ``to_bytes()``.

        :returns: A 64-character lowercase hex string.
        """
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def matches_schema(self, schema: dict[str, Any]) -> bool:
        """Check whether this payload's data conforms to a simple schema.

        The *schema* is a dictionary mapping required key names to their
        expected Python types (as type objects).  The payload matches the
        schema if every key in *schema* is present in ``self.data`` and
        ``isinstance(self.data[key], schema[key])`` holds.

        :param schema: A ``{key: type}`` dictionary defining requirements.
        :returns: ``True`` if all required keys are present and typed correctly.
        """
        for key, expected_type in schema.items():
            if key not in self.data:
                return False
            if not isinstance(self.data[key], expected_type):
                return False
        return True


# ===================================================================== #
# 3. Ambiguity mark propagation — AmbiguityPropagator                   #
# ===================================================================== #


@dataclass
class AmbiguityPropagator:
    """Propagates :class:`AmbiguityMark` objects through an IR node tree.

    The propagator implements the mark-propagation semantics from §32.3
    of ``theory2.tex``.  It records every propagation event in a log so
    that the copilot oracle can audit resolution decisions.

    Attributes
    ----------
    propagation_rules:
        Mapping from :class:`IRNodeKind` value strings to a propagation
        strategy string: ``"children"`` (propagate to direct children),
        ``"all"`` (DFS to all descendants), or ``"none"`` (do not
        propagate beyond this node).
    _propagation_log:
        Ordered list of propagation event records for auditing.
    """

    propagation_rules: dict[str, str] = field(default_factory=dict)
    _propagation_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    def propagate(self, root: "IRNode", mark: "AmbiguityMark") -> int:
        """Apply *mark* to all nodes in the subtree rooted at *root*.

        The propagation strategy for each node is determined by
        ``propagation_rules``.  Nodes not listed in the rules default to
        the ``"children"`` strategy.  The DFS visits every node once.

        :param root: The :class:`IRNode` at which propagation begins.
        :param mark: The :class:`AmbiguityMark` to attach to affected nodes.
        :returns: The number of nodes that received the mark.
        """
        affected = 0
        stack: list[IRNode] = [root]
        visited: set[str] = set()
        while stack:
            node = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            strategy = self.propagation_rules.get(node.node_kind.value, "children")
            if strategy != "none":
                node.ambiguity_mark = mark
                mark.add_ambiguous(node.node_id)
                affected += 1
                self._propagation_log.append(
                    {
                        "event": "mark_applied",
                        "node_id": node.node_id,
                        "mark_id": mark.mark_id,
                        "strategy": strategy,
                    }
                )
            if strategy == "all":
                stack.extend(node.children)
            elif strategy == "children":
                stack.extend(node.children)
        return affected

    def collect_ambiguous(self, root: "IRNode") -> list["IRNode"]:
        """Return all nodes in the subtree rooted at *root* that carry a mark.

        Performs a DFS and collects every node where ``ambiguity_mark`` is
        not ``None``.

        :param root: The :class:`IRNode` to search from.
        :returns: A list of nodes (including *root*) with ambiguity marks.
        """
        result: list[IRNode] = []
        stack: list[IRNode] = [root]
        visited: set[str] = set()
        while stack:
            node = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            if node.ambiguity_mark is not None:
                result.append(node)
            stack.extend(node.children)
        return result

    def resolve_upward(self, root: "IRNode", node_id: str) -> bool:
        """Mark *node_id* as resolved and check if the parent can also be resolved.

        Finds the node with ``node_id`` by DFS, resolves it (clears the
        mark), then checks whether *root* has any remaining ambiguous
        descendants.

        :param root: The subtree root to search within.
        :param node_id: The ID of the node to resolve.
        :returns: ``True`` if *root* (or its whole subtree) is now fully
            resolved after this operation.
        """
        target: IRNode | None = None
        stack: list[IRNode] = [root]
        visited: set[str] = set()
        while stack:
            node = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            if node.node_id == node_id:
                target = node
                break
            stack.extend(node.children)

        if target is not None and target.ambiguity_mark is not None:
            mark = target.ambiguity_mark
            mark.resolve(node_id, "resolved_upward")
            if mark.is_resolved():
                target.ambiguity_mark = None
            self._propagation_log.append(
                {"event": "resolved_upward", "node_id": node_id}
            )

        remaining = self.collect_ambiguous(root)
        return len(remaining) == 0

    def ambiguity_depth(self, root: "IRNode") -> int:
        """Return the maximum depth at which ambiguous nodes appear.

        Depth is measured from *root* (depth 0).  Returns -1 if no
        ambiguous nodes exist in the subtree.

        :param root: The :class:`IRNode` to measure from.
        :returns: Maximum ambiguity depth, or -1 if none.
        """
        max_depth = -1
        stack: list[tuple[IRNode, int]] = [(root, 0)]
        visited: set[str] = set()
        while stack:
            node, depth = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            if node.ambiguity_mark is not None:
                if depth > max_depth:
                    max_depth = depth
            for child in node.children:
                stack.append((child, depth + 1))
        return max_depth

    def generate_resolution_candidates(
        self,
        node: "IRNode",
        context: dict[str, Any],
    ) -> list[str]:
        """Generate plausible resolution candidate strings for *node*.

        Candidates are generated heuristically based on the node kind and
        payload contents.  The *context* dictionary may supply additional
        hints such as ``"expected_type"`` or ``"scope"``.

        :param node: The :class:`IRNode` whose ambiguity needs candidates.
        :param context: A context dictionary with optional resolution hints.
        :returns: A list of candidate strings (may be empty).
        """
        candidates: list[str] = []
        kind_val = node.node_kind.value
        payload = node.payload

        if kind_val == "expression":
            expr = payload.get("expr", "")
            if expr:
                candidates.append(expr)
                candidates.append(f"({expr})")
                type_hint = context.get("expected_type", "")
                if type_hint:
                    candidates.append(f"({expr} : {type_hint})")
        elif kind_val == "type_term":
            repr_str = payload.get("repr", "")
            if repr_str:
                candidates.append(repr_str)
                candidates.append(f"Refined({repr_str})")
                scope = context.get("scope", "")
                if scope:
                    candidates.append(f"{repr_str} in {scope}")
        elif kind_val == "obligation":
            goal = payload.get("goal", "")
            if goal:
                candidates.append(goal)
                candidates.append(f"Provable({goal})")
        elif kind_val == "quantifier":
            binder = payload.get("binder", "forall")
            bound = payload.get("bound_vars", [])
            if bound:
                bound_str = ", ".join(str(v) for v in bound)
                candidates.append(f"{binder} {bound_str}. _")
        else:
            if payload:
                candidates.append(json.dumps(payload, sort_keys=True))

        return candidates

    def prune_resolved(self, root: "IRNode") -> int:
        """Remove fully-resolved ambiguity marks from the subtree.

        A mark is considered fully resolved when ``is_resolved()`` returns
        ``True``.  The node's ``ambiguity_mark`` field is set to ``None``
        for each such node.

        :param root: The :class:`IRNode` subtree to prune.
        :returns: The number of marks that were removed.
        """
        pruned = 0
        stack: list[IRNode] = [root]
        visited: set[str] = set()
        while stack:
            node = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            if (
                node.ambiguity_mark is not None
                and node.ambiguity_mark.is_resolved()
            ):
                node.ambiguity_mark = None
                pruned += 1
                self._propagation_log.append(
                    {"event": "mark_pruned", "node_id": node.node_id}
                )
            stack.extend(node.children)
        return pruned


# ===================================================================== #
# 4. Node substitution — NodeSubstituter                                 #
# ===================================================================== #


@dataclass
class NodeSubstituter:
    """Performs capture-avoiding variable substitution in IR node trees.

    Implements the substitution operation :math:`[x \\mapsto t]` described
    in §32.4.  When ``capture_avoiding`` is ``True`` the substituter
    alpha-renames bound variables that would be captured by the replacement
    node.

    Attributes
    ----------
    substitution_map:
        Mapping from variable name string to the replacement :class:`IRNode`.
    _substitution_count:
        Running total of individual substitution applications made.
    capture_avoiding:
        When ``True``, bound variables are renamed to avoid capture.
    """

    substitution_map: dict[str, "IRNode"] = field(default_factory=dict)
    _substitution_count: int = field(default=0)
    capture_avoiding: bool = field(default=True)

    # ------------------------------------------------------------------
    def add_substitution(self, var_name: str, replacement: "IRNode") -> None:
        """Register a substitution mapping *var_name* to *replacement*.

        :param var_name: The variable name to substitute.
        :param replacement: The :class:`IRNode` to insert in its place.
        """
        self.substitution_map[var_name] = replacement

    def apply(self, root: "IRNode") -> "IRNode":
        """Return a deep copy of *root* with all substitutions applied.

        The original tree is not modified.  Each substitution in
        ``substitution_map`` is applied in one pass over the copied tree.

        :param root: The :class:`IRNode` tree to substitute into.
        :returns: A new :class:`IRNode` tree with all substitutions applied.
        """
        import copy as _copy
        cloned = _copy.deepcopy(root)
        for var_name, replacement in self.substitution_map.items():
            self._apply_to_node(cloned, var_name, _copy.deepcopy(replacement))
        return cloned

    def _apply_to_node(
        self,
        node: "IRNode",
        var_name: str,
        replacement: "IRNode",
    ) -> None:
        """In-place substitution of *var_name* with *replacement* in *node*.

        Mutates *node* and all descendants.  Binding nodes short-circuit
        substitution to respect scope.

        :param node: The node to mutate.
        :param var_name: The variable name to replace.
        :param replacement: The node to insert.
        """
        if node.node_kind.value == "binding":
            bound_var = node.payload.get("var", "")
            if bound_var == var_name:
                return
        node.payload = self.apply_to_payload(node.payload, var_name, replacement.node_id)
        new_children: list[IRNode] = []
        for child in node.children:
            if (
                child.node_kind.value == "expression"
                and child.payload.get("expr", "") == var_name
            ):
                self._substitution_count += 1
                new_children.append(replacement)
            else:
                self._apply_to_node(child, var_name, replacement)
                new_children.append(child)
        node.children = new_children

    def apply_to_payload(
        self,
        payload: dict[str, Any],
        var_name: str,
        replacement_repr: str,
    ) -> dict[str, Any]:
        """Return a new payload with string occurrences of *var_name* replaced.

        String values in the payload dict that are equal to *var_name* or
        that contain the pattern ``"var_name"`` as a substring are updated
        to use *replacement_repr*.

        :param payload: The payload dictionary to process.
        :param var_name: The variable name to replace in string values.
        :param replacement_repr: The string to insert in place of *var_name*.
        :returns: A new dictionary with replacements applied.
        """
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                result[key] = value.replace(var_name, replacement_repr)
            elif isinstance(value, list):
                new_list: list[Any] = []
                for item in value:
                    if isinstance(item, str):
                        new_list.append(item.replace(var_name, replacement_repr))
                    else:
                        new_list.append(item)
                result[key] = new_list
            else:
                result[key] = value
        return result

    def is_free_variable(self, node: "IRNode", var_name: str) -> bool:
        """Return ``True`` if *var_name* appears free in *node*'s subtree.

        A variable is free at *node* if it appears in the payload or any
        descendant payload and is not bound by an enclosing BINDING node.

        :param node: The subtree root.
        :param var_name: The variable name to test.
        :returns: ``True`` if *var_name* appears free.
        """
        stack: list[tuple[IRNode, bool]] = [(node, False)]
        visited: set[str] = set()
        while stack:
            current, is_bound = stack.pop()
            if current.node_id in visited:
                continue
            visited.add(current.node_id)
            if current.node_kind.value == "binding":
                bound_var = current.payload.get("var", "")
                if bound_var == var_name:
                    is_bound = True
            if not is_bound:
                for val in current.payload.values():
                    if isinstance(val, str) and var_name in val:
                        return True
            for child in current.children:
                stack.append((child, is_bound))
        return False

    def rename_bound(
        self,
        root: "IRNode",
        old_name: str,
        new_name: str,
    ) -> "IRNode":
        """Return a copy of *root* with all bound occurrences of *old_name* renamed.

        Only renames within the scope of BINDING nodes that bind *old_name*.
        Free occurrences of *old_name* are left untouched.

        :param root: The :class:`IRNode` tree to rename within.
        :param old_name: The current bound variable name.
        :param new_name: The replacement variable name.
        :returns: A new :class:`IRNode` tree with the renaming applied.
        """
        import copy as _copy
        cloned = _copy.deepcopy(root)
        self._rename_in_subtree(cloned, old_name, new_name, in_scope=False)
        return cloned

    def _rename_in_subtree(
        self,
        node: "IRNode",
        old_name: str,
        new_name: str,
        in_scope: bool,
    ) -> None:
        """Mutate *node* in-place, renaming *old_name* to *new_name* when in scope.

        :param node: The node to process.
        :param old_name: The variable to rename.
        :param new_name: The new variable name.
        :param in_scope: Whether we are currently inside a binding scope for
            *old_name*.
        """
        if node.node_kind.value == "binding":
            bound_var = node.payload.get("var", "")
            if bound_var == old_name:
                in_scope = True
                node.payload = self.apply_to_payload(node.payload, old_name, new_name)
        if in_scope:
            node.payload = self.apply_to_payload(node.payload, old_name, new_name)
        for child in node.children:
            self._rename_in_subtree(child, old_name, new_name, in_scope)

    def count_occurrences(self, root: "IRNode", var_name: str) -> int:
        """Count how many times *var_name* appears in the payload of *root*'s tree.

        Counts string occurrences across all payload string values using
        substring matching.

        :param root: The subtree to search.
        :param var_name: The variable name to count.
        :returns: Total number of string occurrences.
        """
        count = 0
        stack: list[IRNode] = [root]
        visited: set[str] = set()
        while stack:
            node = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            for val in node.payload.values():
                if isinstance(val, str):
                    count += val.count(var_name)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, str):
                            count += item.count(var_name)
            stack.extend(node.children)
        return count

    def statistics(self) -> dict[str, Any]:
        """Return a dictionary summarising this substituter's activity.

        :returns: A dictionary with keys ``"total_substitutions"``,
            ``"registered_variables"``, and ``"capture_avoiding"``.
        """
        return {
            "total_substitutions": self._substitution_count,
            "registered_variables": list(self.substitution_map.keys()),
            "capture_avoiding": self.capture_avoiding,
        }


# ===================================================================== #
# 5. Tree traversal — IRTreeWalker                                       #
# ===================================================================== #


@dataclass
class IRTreeWalker:
    """Traversal and search utilities for IR node trees.

    :class:`IRTreeWalker` provides DFS, BFS, and targeted search operations
    over :class:`IRNode` trees.  It records visit counts for profiling and
    enforces a configurable depth limit to guard against pathological inputs.

    Attributes
    ----------
    _visit_count:
        Total number of nodes visited across all traversal calls.
    _visit_log:
        List of node IDs visited, in visit order.
    max_depth:
        Maximum traversal depth (inclusive).  Nodes beyond this depth are
        not visited.
    """

    _visit_count: int = field(default=0)
    _visit_log: list[str] = field(default_factory=list)
    max_depth: int = field(default=1000)

    # ------------------------------------------------------------------
    def depth_first(
        self, root: "IRNode"
    ) -> Generator["IRNode", None, None]:
        """Yield all nodes in the subtree rooted at *root* in DFS order.

        Uses an explicit stack to avoid Python recursion limits.  Nodes are
        yielded in pre-order (parent before children).

        :param root: The :class:`IRNode` to start from.
        :yields: :class:`IRNode` objects in DFS pre-order.
        """
        stack: list[tuple[IRNode, int]] = [(root, 0)]
        visited: set[str] = set()
        while stack:
            node, depth = stack.pop()
            if node.node_id in visited or depth > self.max_depth:
                continue
            visited.add(node.node_id)
            self._visit_count += 1
            self._visit_log.append(node.node_id)
            yield node
            for child in reversed(node.children):
                stack.append((child, depth + 1))

    def breadth_first(
        self, root: "IRNode"
    ) -> Generator["IRNode", None, None]:
        """Yield all nodes in the subtree rooted at *root* in BFS order.

        Uses a deque for O(1) appends and pops.  Nodes are yielded level
        by level, shallowest first.

        :param root: The :class:`IRNode` to start from.
        :yields: :class:`IRNode` objects in BFS order.
        """
        queue: collections.deque[tuple[IRNode, int]] = collections.deque(
            [(root, 0)]
        )
        visited: set[str] = set()
        while queue:
            node, depth = queue.popleft()
            if node.node_id in visited or depth > self.max_depth:
                continue
            visited.add(node.node_id)
            self._visit_count += 1
            self._visit_log.append(node.node_id)
            yield node
            for child in node.children:
                queue.append((child, depth + 1))

    def find_by_kind(
        self,
        root: "IRNode",
        kind: "IRNodeKind",
    ) -> list["IRNode"]:
        """Return all nodes of *kind* in the subtree rooted at *root*.

        :param root: The subtree to search.
        :param kind: The :class:`IRNodeKind` to match.
        :returns: A list of matching :class:`IRNode` objects.
        """
        return [
            node for node in self.depth_first(root)
            if node.node_kind == kind
        ]

    def find_by_id(
        self,
        root: "IRNode",
        node_id: str,
    ) -> "IRNode | None":
        """Search the subtree for a node with the given *node_id*.

        :param root: The subtree to search.
        :param node_id: The node ID to locate.
        :returns: The first matching :class:`IRNode`, or ``None``.
        """
        for node in self.depth_first(root):
            if node.node_id == node_id:
                return node
        return None

    def height(self, root: "IRNode") -> int:
        """Return the height of the subtree rooted at *root*.

        Height is defined as the maximum depth from *root* to any leaf
        node, where a leaf has no children.  A single-node tree has
        height 0.

        :param root: The root of the subtree.
        :returns: The tree height (>= 0).
        """
        if not root.children:
            return 0
        stack: list[tuple[IRNode, int]] = [(root, 0)]
        visited: set[str] = set()
        max_h = 0
        while stack:
            node, depth = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            if depth > max_h:
                max_h = depth
            for child in node.children:
                stack.append((child, depth + 1))
        return max_h

    def size(self, root: "IRNode") -> int:
        """Return the total number of nodes in the subtree rooted at *root*.

        :param root: The root of the subtree.
        :returns: Total node count (>= 1).
        """
        return sum(1 for _ in self.depth_first(root))

    def path_to(
        self,
        root: "IRNode",
        target_id: str,
    ) -> list[str] | None:
        """Return the list of node IDs from *root* to the node with *target_id*.

        Returns ``None`` if *target_id* is not found in the subtree.

        :param root: The subtree root.
        :param target_id: The node ID to locate.
        :returns: A list of node IDs ``[root.node_id, ..., target_id]``, or
            ``None`` if the target is not in the subtree.
        """
        stack: list[tuple[IRNode, list[str]]] = [(root, [root.node_id])]
        visited: set[str] = set()
        while stack:
            node, path = stack.pop()
            if node.node_id in visited:
                continue
            visited.add(node.node_id)
            if node.node_id == target_id:
                return path
            for child in node.children:
                if child.node_id not in visited:
                    stack.append((child, path + [child.node_id]))
        return None


# ===================================================================== #
# 6. Copilot-assisted node suggestion — CopilotNodeSuggestor             #
# ===================================================================== #


@dataclass
class CopilotNodeSuggestor:
    """Oracle-assisted suggestion of IR node kinds and payloads.

    :class:`CopilotNodeSuggestor` implements §32.6 of ``theory2.tex``:
    the copilot oracle may propose an :class:`IRNodeKind` and a default
    payload for a partially-specified context.  Suggestions and feedback
    are logged so that acceptance-rate statistics can be computed.

    Attributes
    ----------
    suggestion_log:
        List of suggestion event dictionaries.
    _session_id:
        Unique identifier for this suggestion session (for correlation).
    confidence_threshold:
        Minimum confidence required for a suggestion to be surfaced.
    """

    suggestion_log: list[dict[str, Any]] = field(default_factory=list)
    _session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    confidence_threshold: float = field(default=0.5)

    # ------------------------------------------------------------------
    def suggest_node_kind(self, context: dict[str, Any]) -> "IRNodeKind":
        """Suggest the most appropriate :class:`IRNodeKind` for a context.

        # copilot: suggest_node_kind uses context to pick the best IR node kind

        The heuristic inspects contextual keys to choose a kind:

        * ``"goal"`` or ``"provable"`` → OBLIGATION
        * ``"binder"`` or ``"forall"`` / ``"exists"`` → QUANTIFIER
        * ``"type"`` or ``"repr"`` → TYPE_TERM
        * ``"stmt"`` or ``"effect"`` → STATEMENT
        * ``"label"`` or ``"ref"`` → ANNOTATION
        * ``"var"`` and ``"value"`` present → BINDING
        * default → EXPRESSION

        :param context: A dictionary of contextual hints.
        :returns: A suggested :class:`IRNodeKind`.
        """
        kind: IRNodeKind
        if "goal" in context or "provable" in context:
            kind = IRNodeKind.OBLIGATION
        elif "binder" in context or context.get("quantifier"):
            kind = IRNodeKind.QUANTIFIER
        elif "type" in context or "repr" in context:
            kind = IRNodeKind.TYPE_TERM
        elif "stmt" in context or "effect" in context:
            kind = IRNodeKind.STATEMENT
        elif "label" in context or "ref" in context:
            kind = IRNodeKind.ANNOTATION
        elif "var" in context and "value" in context:
            kind = IRNodeKind.BINDING
        else:
            kind = IRNodeKind.EXPRESSION
        import time as _time
        self.suggestion_log.append(
            {
                "event": "kind_suggested",
                "kind": kind.value,
                "context_keys": list(context.keys()),
                "session_id": self._session_id,
                "timestamp": _time.time(),
            }
        )
        return kind

    def suggest_payload(
        self,
        kind: "IRNodeKind",
        partial_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Fill in missing payload fields for *kind* using reasonable defaults.

        # copilot: suggest_payload fills in missing fields for the given node kind

        The returned payload includes all keys from *partial_payload* plus
        any kind-specific defaults for missing keys.

        :param kind: The :class:`IRNodeKind` whose schema defines defaults.
        :param partial_payload: A partially-filled payload dictionary.
        :returns: A complete payload dictionary with defaults applied.
        """
        _defaults: dict[str, dict[str, Any]] = {
            "expression": {"expr": "", "type_hint": None, "source": "unknown"},
            "statement": {"stmt": "", "effects": [], "is_pure": True},
            "type_term": {"repr": "", "bindings": {}, "variance": "invariant"},
            "obligation": {
                "goal": "",
                "context": {},
                "discharged": False,
                "solver_hint": None,
            },
            "annotation": {
                "label": "",
                "source_ref": "",
                "is_user_facing": True,
            },
            "binding": {"var": "", "value": None, "is_recursive": False},
            "quantifier": {
                "binder": "forall",
                "bound_vars": [],
                "domain": None,
            },
        }
        defaults = dict(_defaults.get(kind.value, {}))
        defaults.update(partial_payload)
        import time as _time
        self.suggestion_log.append(
            {
                "event": "payload_suggested",
                "kind": kind.value,
                "filled_keys": [
                    k for k in defaults if k not in partial_payload
                ],
                "session_id": self._session_id,
                "timestamp": _time.time(),
            }
        )
        return defaults

    def explain_choice(self, kind: "IRNodeKind") -> str:
        """Return a human-readable explanation of why *kind* was suggested.

        :param kind: The :class:`IRNodeKind` to explain.
        :returns: A one-paragraph explanation string.
        """
        _explanations: dict[str, str] = {
            "expression": (
                "EXPRESSION was chosen because the context did not match any "
                "more specific kind.  Expressions represent general computations "
                "and are the fallback category in the IR node taxonomy."
            ),
            "statement": (
                "STATEMENT was chosen because the context indicated an effectful "
                "operation (e.g., an 'effect' or 'stmt' key).  Statements may "
                "produce side-effects and are processed before pure expressions."
            ),
            "type_term": (
                "TYPE_TERM was chosen because the context included a 'type' or "
                "'repr' key, indicating that the node encodes a type-level "
                "construct rather than a value-level expression."
            ),
            "obligation": (
                "OBLIGATION was chosen because the context contained a 'goal' "
                "or 'provable' key, indicating a proof obligation that must be "
                "discharged by the solver pipeline."
            ),
            "annotation": (
                "ANNOTATION was chosen because the context contained a 'label' "
                "or 'ref' key.  Annotations are metadata nodes that attach "
                "source references and documentation to other IR nodes."
            ),
            "binding": (
                "BINDING was chosen because both 'var' and 'value' keys were "
                "present in the context, indicating a let-binding or function "
                "parameter declaration."
            ),
            "quantifier": (
                "QUANTIFIER was chosen because the context contained a 'binder' "
                "key or a 'quantifier' flag, indicating a forall/exists construct."
            ),
        }
        return _explanations.get(
            kind.value,
            f"No explanation available for kind {kind.value!r}.",
        )

    def record_feedback(self, node_id: str, accepted: bool) -> None:
        """Record whether the last suggestion for *node_id* was accepted.

        :param node_id: The ID of the node the suggestion was made for.
        :param accepted: ``True`` if the suggestion was used; ``False`` if
            it was rejected.
        """
        import time as _time
        self.suggestion_log.append(
            {
                "event": "feedback",
                "node_id": node_id,
                "accepted": accepted,
                "session_id": self._session_id,
                "timestamp": _time.time(),
            }
        )

    def statistics(self) -> dict[str, Any]:
        """Return a summary of suggestion activity and acceptance rates.

        :returns: A dictionary with counts for each event type and the
            overall acceptance rate.
        """
        total_suggestions = sum(
            1 for e in self.suggestion_log if e.get("event") in (
                "kind_suggested", "payload_suggested"
            )
        )
        feedbacks = [
            e for e in self.suggestion_log if e.get("event") == "feedback"
        ]
        accepted_count = sum(1 for e in feedbacks if e.get("accepted"))
        acceptance_rate = (
            accepted_count / len(feedbacks) if feedbacks else 0.0
        )
        kind_counts: dict[str, int] = collections.Counter(
            e["kind"]
            for e in self.suggestion_log
            if e.get("event") == "kind_suggested"
        )  # type: ignore[assignment]
        return {
            "session_id": self._session_id,
            "total_suggestions": total_suggestions,
            "total_feedbacks": len(feedbacks),
            "accepted_count": accepted_count,
            "acceptance_rate": round(acceptance_rate, 4),
            "kind_distribution": dict(kind_counts),
        }


# ===================================================================== #
# 7. Module-level helper functions                                       #
# ===================================================================== #


def build_expression_node(
    expr_str: str,
    trust_level: int = 0,
) -> "IRNode":
    """Create a new :class:`IRNode` of kind EXPRESSION.

    :param expr_str: The expression string to store in the payload.
    :param trust_level: Initial trust level for the node (default 0).
    :returns: A new :class:`IRNode` with kind EXPRESSION.
    """
    return IRNode(
        node_id=str(uuid.uuid4()),
        node_kind=IRNodeKind.EXPRESSION,
        payload={"expr": expr_str, "type_hint": None},
        children=[],
        trust_level=trust_level,
    )


def build_type_node(
    type_repr: str,
    bindings: dict[str, Any] | None = None,
) -> "IRNode":
    """Create a new :class:`IRNode` of kind TYPE_TERM.

    :param type_repr: The type representation string.
    :param bindings: Optional binding environment dictionary.
    :returns: A new :class:`IRNode` with kind TYPE_TERM.
    """
    return IRNode(
        node_id=str(uuid.uuid4()),
        node_kind=IRNodeKind.TYPE_TERM,
        payload={"repr": type_repr, "bindings": bindings or {}},
        children=[],
        trust_level=0,
    )


def build_obligation_node(obligation: dict[str, Any]) -> "IRNode":
    """Create a new :class:`IRNode` of kind OBLIGATION.

    :param obligation: A dictionary with at minimum a ``"goal"`` key.
    :returns: A new :class:`IRNode` with kind OBLIGATION.
    """
    payload: dict[str, Any] = {
        "goal": obligation.get("goal", ""),
        "context": obligation.get("context", {}),
        "discharged": obligation.get("discharged", False),
    }
    return IRNode(
        node_id=str(uuid.uuid4()),
        node_kind=IRNodeKind.OBLIGATION,
        payload=payload,
        children=[],
        trust_level=0,
    )


def walk_and_collect(
    root: "IRNode",
    predicate: Callable[["IRNode"], bool],
) -> list["IRNode"]:
    """Return all nodes in the subtree of *root* satisfying *predicate*.

    Uses a default :class:`IRTreeWalker` with DFS traversal order.

    :param root: The subtree root to walk.
    :param predicate: A callable that returns ``True`` for nodes to keep.
    :returns: A list of matching :class:`IRNode` objects.
    """
    walker = IRTreeWalker()
    return [node for node in walker.depth_first(root) if predicate(node)]


def substitute_in_tree(
    root: "IRNode",
    substitutions: dict[str, "IRNode"],
) -> "IRNode":
    """Apply a batch of substitutions to the subtree rooted at *root*.

    Convenience wrapper around :class:`NodeSubstituter`.

    :param root: The :class:`IRNode` tree to substitute into.
    :param substitutions: A ``{var_name: replacement_node}`` dictionary.
    :returns: A new :class:`IRNode` tree with all substitutions applied.
    """
    substituter = NodeSubstituter(capture_avoiding=True)
    for var_name, replacement in substitutions.items():
        substituter.add_substitution(var_name, replacement)
    return substituter.apply(root)
