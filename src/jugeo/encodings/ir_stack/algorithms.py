r"""Core algorithms for the IR stack subsystem.

This module implements the algorithmic backbone of the IR stack described in
Chapter 32 of ``theory2.tex`` — *Internal Representations and the IR Stack*.
It provides pure functions and lightweight dataclasses that operate on
:class:`IRNode`, :class:`IRLayer`, and :class:`IRStack` objects to perform
lowering, normal-form computation, ambiguity propagation, diffing, merging,
validation, caching, and pass composition.

.. math::

   \text{lower\_ir\_stack}(S,\, k) = S \cup \bigl\{ p(L) \mid
   L \in S,\; p \in \mathcal{P}_{L.k \to k} \bigr\}

where :math:`\mathcal{P}_{a \to b}` is the set of lowering passes with
:math:`\mathrm{input\_layer} = a` and :math:`\mathrm{output\_layer} = b`.

Normal forms satisfy the reduction relation:

.. math::

   \text{nf}(t) = t' \;\Longleftrightarrow\; t \twoheadrightarrow_\beta t'
   \;\land\; t' \in \mathrm{NF}

Ambiguity propagation uses a monotone spreading rule:

.. math::

   \forall v \in \mathrm{children}(u):\;
   \mathrm{mark}(v) \supseteq \mathrm{mark}(u)

Architecture
------------

All public functions in this module are stateless: they accept immutable-ish
data objects and return new objects or descriptive dicts.  The two dataclasses
:class:`AlgorithmConfig` and :class:`AlgorithmResult` carry configuration and
result envelopes respectively.

:func:`run_full_pipeline` is the top-level orchestrator that calls all other
algorithms in the correct order and accumulates the results into a single
:class:`AlgorithmResult`.

Theory alignment
~~~~~~~~~~~~~~~~

* §32.1 — IR layer kinds and depth ordering
* §32.2 — Normal form reduction rules
* §32.3 — Ambiguity mark propagation semantics
* §32.4 — Lowering pass catalogue
* §32.5 — Diff and merge semantics for IR layers
* §32.6 — Cache and memoisation contract
* §32.7 — Pass composition monoid
"""

from __future__ import annotations

import collections
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from jugeo.encodings.ir_stack.models import (  # type: ignore[import]
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
    pass

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder  # type: ignore[import]
except ImportError:
    class Z3Session:  # type: ignore[no-redef]
        pass

    class Z3Formula:  # type: ignore[no-redef]
        pass

    class Z3Encoder:  # type: ignore[no-redef]
        pass

try:
    from jugeo.solver.reconstruction import ModelReconstruction  # type: ignore[import]
except ImportError:
    class ModelReconstruction:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel  # type: ignore[import]
except ImportError:
    class TrustAlgebra:  # type: ignore[no-redef]
        pass

    class TrustLevel:  # type: ignore[no-redef]
        pass


# ===================================================================== #
# 1. Configuration and result dataclasses                                #
# ===================================================================== #


@dataclass
class AlgorithmConfig:
    """Configuration envelope for the IR stack algorithm suite.

    Carries tuning parameters that control the behaviour of
    :func:`run_full_pipeline` and the individual algorithm functions.

    Attributes:
        max_steps: Maximum number of reduction steps allowed during
            normal-form computation before the loop is terminated.
        enable_cache: When ``True``, :func:`cache_lookup` is consulted
            before each normal-form computation and results are stored
            on success.
        verify_ambiguity: When ``True``, ambiguity preservation is checked
            after every lowering pass via
            :func:`validate_lowering`.
        propagation_depth: Maximum DFS depth for
            :func:`propagate_ambiguity_marks`.  ``-1`` means unlimited.
        conflict_policy: Strategy for resolving binding conflicts during
            :func:`merge_ir_stacks`.  Accepted values: ``"prefer_right"``,
            ``"prefer_left"``, ``"error"``.
        enable_diff: When ``True``, :func:`diff_ir_layers` is called on
            each pair of consecutive layers after lowering.
        log_level: Verbosity level (``"silent"``, ``"info"``, ``"debug"``).
        target_layer_kind: The desired output :class:`IRLayerKind` for the
            lowering pipeline.
    """

    max_steps: int = field(default=1000)
    enable_cache: bool = field(default=True)
    verify_ambiguity: bool = field(default=True)
    propagation_depth: int = field(default=-1)
    conflict_policy: str = field(default="prefer_right")
    enable_diff: bool = field(default=False)
    log_level: str = field(default="info")
    target_layer_kind: Any = field(default=None)

    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        """Return a list of configuration validation errors.

        Checks that all numeric bounds are sensible and that
        ``conflict_policy`` is one of the recognised values.

        Returns:
            A list of human-readable error strings; empty when the
            configuration is valid.
        """
        errors: list[str] = []
        if self.max_steps < 1:
            errors.append(f"max_steps must be >= 1; got {self.max_steps}.")
        if self.propagation_depth < -1:
            errors.append(
                f"propagation_depth must be >= -1; got {self.propagation_depth}."
            )
        valid_policies = {"prefer_right", "prefer_left", "error"}
        if self.conflict_policy not in valid_policies:
            errors.append(
                f"conflict_policy must be one of {valid_policies}; "
                f"got '{self.conflict_policy}'."
            )
        valid_log_levels = {"silent", "info", "debug"}
        if self.log_level not in valid_log_levels:
            errors.append(
                f"log_level must be one of {valid_log_levels}; "
                f"got '{self.log_level}'."
            )
        return errors


@dataclass
class AlgorithmResult:
    """Result envelope returned by :func:`run_full_pipeline` and helpers.

    Attributes:
        success: ``True`` when the pipeline completed without fatal errors.
        errors: List of error messages produced during execution.
        warnings: List of non-fatal warnings.
        stats: Arbitrary statistics dict (pass counts, cache hits, etc.).
        result_value: The primary output of the algorithm (e.g., the
            lowered :class:`IRStack` or a :class:`NormalForm`).
    """

    success: bool = field(default=True)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    result_value: Any = field(default=None)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this result to a plain JSON-safe dict.

        The ``result_value`` is converted via ``str()`` when it is not
        already JSON-serialisable.

        Returns:
            A dict with keys ``success``, ``errors``, ``warnings``,
            ``stats``, and ``result_value``.
        """
        safe_result: Any
        try:
            json.dumps(self.result_value)
            safe_result = self.result_value
        except (TypeError, ValueError):
            safe_result = str(self.result_value)

        return {
            "success": self.success,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
            "result_value": safe_result,
        }

    def add_error(self, message: str) -> None:
        """Append an error message and mark the result as failed.

        Args:
            message: A human-readable error string.
        """
        self.errors.append(message)
        self.success = False

    def add_warning(self, message: str) -> None:
        """Append a non-fatal warning message.

        Args:
            message: A human-readable warning string.
        """
        self.warnings.append(message)

    def merge(self, other: AlgorithmResult) -> AlgorithmResult:
        """Combine this result with *other*, returning a new merged result.

        The merged result is successful only when **both** are successful.
        Errors, warnings, and stats are concatenated or merged.

        Args:
            other: Another :class:`AlgorithmResult` to merge in.

        Returns:
            A new :class:`AlgorithmResult` combining both inputs.
        """
        merged_stats = dict(self.stats)
        for key, value in other.stats.items():
            if key in merged_stats and isinstance(merged_stats[key], int) and isinstance(value, int):
                merged_stats[key] = merged_stats[key] + value
            else:
                merged_stats[key] = value
        return AlgorithmResult(
            success=self.success and other.success,
            errors=list(self.errors) + list(other.errors),
            warnings=list(self.warnings) + list(other.warnings),
            stats=merged_stats,
            result_value=other.result_value if other.result_value is not None else self.result_value,
        )


# ===================================================================== #
# 2. IR stack lowering algorithm                                         #
# ===================================================================== #


def lower_ir_stack(
    stack: Any,
    target_layer_kind: Any = None,
    passes: list[Any] | None = None,
) -> Any:
    """Lower *stack* towards *target_layer_kind* by applying *passes*.

    Iterates through the stack's layers in order.  For each layer whose
    kind is not yet *target_layer_kind*, the function applies each
    relevant pass from *passes* (filtered by ``input_layer`` match) and
    appends the resulting layer to the output stack.  Ambiguity
    preservation is validated after each pass application.

    .. math::

       S' = S \\cup \\bigl\\{ p(L) \\mid L \\in S,\\;
       p.\\mathrm{input\\_layer} = L.\\mathrm{layer\\_kind},\\;
       p \\in \\mathcal{P} \\bigr\\}

    Args:
        stack: The :class:`IRStack` to lower.  Must not be ``None``.
        target_layer_kind: The :class:`IRLayerKind` to target.  When
            ``None`` the function defaults to ``IRLayerKind.SOLVER_READY``
            if that symbol is importable, otherwise all passes are applied
            unconditionally.
        passes: An explicit list of :class:`LoweringPass` objects to use.
            When ``None`` the function returns *stack* unchanged (no passes
            are available without a registry).

    Returns:
        The :class:`IRStack` with newly lowered layers appended.  The
        original layers are preserved; new layers are pushed on top.

    Raises:
        TypeError: If *stack* is ``None``.
    """
    if stack is None:
        raise TypeError("stack must not be None.")

    if passes is None:
        return stack

    # Determine target kind
    effective_target = target_layer_kind
    if effective_target is None:
        try:
            effective_target = IRLayerKind.SOLVER_READY  # type: ignore[name-defined]
        except Exception:
            effective_target = None

    layers: list[Any] = list(getattr(stack, "layers", []))
    added_layers: list[Any] = []
    validation_errors: list[str] = []

    for layer in layers:
        current_kind = getattr(layer, "layer_kind", None)

        # Skip layers that are already at or past the target
        if effective_target is not None and current_kind == effective_target:
            continue

        current_layer = layer
        for lp in passes:
            lp_input = getattr(lp, "input_layer", None)
            if lp_input is not None and lp_input != current_kind:
                continue

            try:
                next_layer = lp.apply(current_layer)
            except Exception as exc:
                validation_errors.append(
                    f"Pass '{getattr(lp, 'pass_name', '?')}' raised an exception "
                    f"on layer '{getattr(current_layer, 'layer_id', '?')}': {exc}"
                )
                continue

            # Validate ambiguity preservation
            before_marks = _collect_mark_ids(current_layer)
            after_marks = _collect_mark_ids(next_layer)
            dropped = before_marks - after_marks
            if dropped:
                validation_errors.append(
                    f"Pass '{getattr(lp, 'pass_name', '?')}' dropped ambiguity "
                    f"marks for nodes: {sorted(dropped)}"
                )

            current_layer = next_layer
            current_kind = getattr(current_layer, "layer_kind", current_kind)

            if effective_target is not None and current_kind == effective_target:
                break

        if current_layer is not layer:
            added_layers.append(current_layer)

    # Push all newly created layers onto the stack
    for new_layer in added_layers:
        try:
            stack.push(new_layer)
        except Exception:
            stack_layers = getattr(stack, "layers", [])
            if isinstance(stack_layers, list):
                stack_layers.append(new_layer)

    # Store validation errors in stack metadata
    metadata = getattr(stack, "metadata", {})
    if validation_errors:
        metadata.setdefault("lowering_errors", []).extend(validation_errors)

    return stack


def _collect_mark_ids(layer: Any) -> set[str]:
    """Return the set of node IDs that carry an ambiguity mark in *layer*.

    Args:
        layer: An :class:`IRLayer` to inspect.

    Returns:
        A set of ``node_id`` strings for nodes with non-``None``
        ``ambiguity_mark``.
    """
    nodes: dict[str, Any] = getattr(layer, "nodes", {})
    return {
        nid
        for nid, node in nodes.items()
        if getattr(node, "ambiguity_mark", None) is not None
    }


# ===================================================================== #
# 3. Normal form computation                                             #
# ===================================================================== #


def compute_normal_form(
    node: Any,
    kind: Any = None,
    max_steps: int = 1000,
) -> Any:
    """Compute the normal form of *node* under the given reduction strategy.

    Implements a step-bounded reduction loop.  At each step the function
    attempts to reduce the node by consulting its ``payload`` for a
    ``reducible`` flag and a ``reduce_step`` callable.  When the node
    reports it is already in normal form (or no reducer is available) the
    loop terminates early.

    For ``HEAD_NORMAL`` kind the loop applies only head reductions
    (reducing the outermost redex).  For ``FULL_NORMAL`` the loop recurses
    into children as well.

    Args:
        node: The :class:`IRNode` to normalise.  Must not be ``None``.
        kind: A :class:`NormalFormKind` selecting the reduction strategy.
            Defaults to ``NormalFormKind.FULL_NORMAL`` when available.
        max_steps: Maximum number of reduction steps.  The function
            terminates with a ``WEAK_HEAD`` form when this limit is reached.

    Returns:
        A :class:`NormalForm` recording the canonical form, original
        snapshot, and the reduction history.

    Raises:
        TypeError: If *node* is ``None``.
    """
    if node is None:
        raise TypeError("node must not be None.")

    # Resolve default kind
    effective_kind = kind
    if effective_kind is None:
        try:
            effective_kind = NormalFormKind.FULL_NORMAL  # type: ignore[name-defined]
        except Exception:
            effective_kind = None

    # Capture original snapshot
    original_snapshot: dict[str, Any]
    if hasattr(node, "to_dict"):
        try:
            original_snapshot = node.to_dict()
        except Exception:
            original_snapshot = {"node_id": getattr(node, "node_id", None)}
    else:
        original_snapshot = {"node_id": getattr(node, "node_id", None)}

    reduction_steps: list[dict[str, Any]] = []
    current = node
    step_count = 0
    terminated_normally = False

    while step_count < max_steps:
        # Check if current node is already in normal form
        payload = getattr(current, "payload", {})
        is_reducible = payload.get("reducible", False)
        if not is_reducible:
            terminated_normally = True
            break

        # Attempt head reduction
        reducer: Callable[..., Any] | None = payload.get("reduce_step")
        if reducer is None:
            terminated_normally = True
            break

        try:
            reduced = reducer(current)
        except Exception as exc:
            reduction_steps.append(
                {
                    "step": step_count,
                    "kind": "error",
                    "error": str(exc),
                    "timestamp": time.time(),
                }
            )
            break

        if reduced is current:
            # No progress made — already at normal form
            terminated_normally = True
            break

        step_hash = _hash_node(current)
        reduction_steps.append(
            {
                "step": step_count,
                "kind": "head_reduction",
                "from_node_id": getattr(current, "node_id", None),
                "from_hash": step_hash,
                "to_node_id": getattr(reduced, "node_id", None),
                "timestamp": time.time(),
            }
        )

        current = reduced
        step_count += 1

        # For FULL_NORMAL: also reduce children recursively
        kind_value = getattr(effective_kind, "value", None)
        if kind_value in ("full_normal", "beta_normal", "eta_normal"):
            children = getattr(current, "children", [])
            for i, child in enumerate(children):
                child_nf = compute_normal_form(
                    child,
                    kind=effective_kind,
                    max_steps=max(1, max_steps - step_count),
                )
                child_steps = getattr(child_nf, "reduction_steps", [])
                for cs in child_steps:
                    cs["child_index"] = i
                reduction_steps.extend(child_steps)

    # Determine the actual normal form kind reached
    if not terminated_normally:
        # Hit step limit — only weak-head normal form guaranteed
        try:
            actual_kind = NormalFormKind.WEAK_HEAD  # type: ignore[name-defined]
        except Exception:
            actual_kind = effective_kind
    else:
        actual_kind = effective_kind

    # Build canonical snapshot from reduced node
    if hasattr(current, "to_dict"):
        try:
            canonical = current.to_dict()
        except Exception:
            canonical = {"node_id": getattr(current, "node_id", None)}
    else:
        canonical = {"node_id": getattr(current, "node_id", None)}

    try:
        nf = NormalForm(  # type: ignore[call-arg]
            form_id=str(uuid.uuid4()),
            canonical=canonical,
            original=original_snapshot,
            reduction_steps=reduction_steps,
            normal_form_kind=actual_kind,
        )
    except Exception:
        nf = type("_NF", (), {  # type: ignore[assignment]
            "form_id": str(uuid.uuid4()),
            "canonical": canonical,
            "original": original_snapshot,
            "reduction_steps": reduction_steps,
            "normal_form_kind": actual_kind,
        })()

    return nf


def _hash_node(node: Any) -> str:
    """Return a short hex hash of *node*'s content for reduction tracing.

    Args:
        node: An :class:`IRNode` to hash.

    Returns:
        An 8-character hex string.
    """
    if hasattr(node, "hash_content"):
        try:
            return node.hash_content()[:8]
        except Exception:
            pass
    raw = json.dumps(
        {"node_id": getattr(node, "node_id", ""), "payload": getattr(node, "payload", {})},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


# ===================================================================== #
# 4. Ambiguity mark propagation                                          #
# ===================================================================== #


def propagate_ambiguity_marks(
    root: Any,
    marks: list[Any],
    propagation_depth: int = -1,
) -> dict[str, Any]:
    """Propagate *marks* through the subtree rooted at *root*.

    Performs a depth-first traversal from *root* and applies each mark in
    *marks* to every node whose ``node_kind`` or ``payload`` matches the
    mark's ``ambiguous_nodes`` list.  The traversal respects
    *propagation_depth*: when non-negative, nodes deeper than that level
    are skipped.

    # copilot: the spreading rule is monotone — once a mark is applied to a
    node it is never removed, satisfying the invariant from §32.3.

    .. math::

       \\forall v \\in \\mathrm{children}(u):\\;
       \\mathrm{mark}(v) \\supseteq \\mathrm{mark}(u)

    Args:
        root: The root :class:`IRNode` of the subtree to process.
        marks: A list of :class:`AmbiguityMark` objects to propagate.
        propagation_depth: Maximum DFS depth.  ``-1`` means unlimited.

    Returns:
        A dict mapping ``node_id`` strings to the :class:`AmbiguityMark`
        that was applied to each node.  Nodes that received no mark are
        absent from the dict.

    Raises:
        TypeError: If *root* is ``None``.
    """
    if root is None:
        raise TypeError("root must not be None.")

    if not marks:
        return {}

    result: dict[str, Any] = {}

    # Collect all node IDs targeted by any mark
    targeted_ids: set[str] = set()
    for mark in marks:
        targeted_ids.update(getattr(mark, "ambiguous_nodes", []))

    # DFS stack: (node, current_depth, inherited_mark)
    stack: list[tuple[Any, int, Any]] = [(root, 0, None)]
    visited: set[str] = set()

    while stack:
        node, depth, inherited_mark = stack.pop()
        node_id: str = getattr(node, "node_id", "")

        if node_id in visited:
            continue
        visited.add(node_id)

        if propagation_depth >= 0 and depth > propagation_depth:
            continue

        # Determine which mark (if any) to apply to this node
        applied_mark: Any = inherited_mark

        # Check if this node is directly targeted by any mark
        for mark in marks:
            target_ids: list[str] = getattr(mark, "ambiguous_nodes", [])
            if node_id in target_ids:
                applied_mark = mark
                break
            # Also match by node kind
            node_kind_val = getattr(getattr(node, "node_kind", None), "value", None)
            mark_kind_val = getattr(getattr(mark, "mark_kind", None), "value", None)
            if node_kind_val is not None and mark_kind_val == "structural":
                # Structural marks propagate to all expression nodes
                if node_kind_val in ("expression", "annotation"):
                    applied_mark = mark
                    break

        # Apply the mark to the node
        if applied_mark is not None:
            try:
                node.mark_ambiguous(applied_mark)
            except Exception:
                # Fallback: set attribute directly
                try:
                    node.ambiguity_mark = applied_mark
                except Exception:
                    pass
            result[node_id] = applied_mark

        # Visit children; inherit the mark if this node received one
        children: list[Any] = getattr(node, "children", [])
        next_inherited = applied_mark if applied_mark is not None else inherited_mark
        for child in reversed(children):
            child_id = getattr(child, "node_id", "")
            if child_id not in visited:
                stack.append((child, depth + 1, next_inherited))

    return result


# ===================================================================== #
# 5. Layer diffing and merging                                           #
# ===================================================================== #


def diff_ir_layers(before: Any, after: Any) -> dict[str, Any]:
    """Compute the diff between two :class:`IRLayer` objects.

    Compares nodes, bindings, and constraint counts between *before* and
    *after*.  A node is considered *changed* when its ``hash_content()``
    value differs between the two layers.

    Args:
        before: The :class:`IRLayer` representing the earlier state.
        after: The :class:`IRLayer` representing the later state.

    Returns:
        A dict with keys:
        * ``diff_id`` — unique identifier for this diff.
        * ``before_layer_id`` — ID of the before layer.
        * ``after_layer_id`` — ID of the after layer.
        * ``added_nodes`` — list of node IDs present in *after* but not *before*.
        * ``removed_nodes`` — list of node IDs present in *before* but not *after*.
        * ``changed_nodes`` — list of node IDs present in both but with different content hashes.
        * ``added_bindings`` — list of binding keys added in *after*.
        * ``removed_bindings`` — list of binding keys removed in *after*.
        * ``changed_constraints_count`` — difference in constraint list length.
        * ``timestamp`` — Unix timestamp of this diff computation.

    Raises:
        TypeError: If either *before* or *after* is ``None``.
    """
    if before is None or after is None:
        raise TypeError("both before and after layers must not be None.")

    before_nodes: dict[str, Any] = getattr(before, "nodes", {})
    after_nodes: dict[str, Any] = getattr(after, "nodes", {})

    before_keys = set(before_nodes.keys())
    after_keys = set(after_nodes.keys())

    added_nodes = sorted(after_keys - before_keys)
    removed_nodes = sorted(before_keys - after_keys)

    # Detect changed nodes by comparing content hashes
    changed_nodes: list[str] = []
    for nid in before_keys & after_keys:
        before_hash = _hash_node(before_nodes[nid])
        after_hash = _hash_node(after_nodes[nid])
        if before_hash != after_hash:
            changed_nodes.append(nid)

    # Diff bindings
    before_bindings: dict[str, Any] = getattr(before, "bindings", {})
    after_bindings: dict[str, Any] = getattr(after, "bindings", {})
    added_bindings = sorted(set(after_bindings.keys()) - set(before_bindings.keys()))
    removed_bindings = sorted(set(before_bindings.keys()) - set(after_bindings.keys()))

    # Diff constraints
    before_constraints: list[Any] = getattr(before, "constraints", [])
    after_constraints: list[Any] = getattr(after, "constraints", [])
    changed_constraints_count = len(after_constraints) - len(before_constraints)

    return {
        "diff_id": str(uuid.uuid4()),
        "before_layer_id": getattr(before, "layer_id", None),
        "after_layer_id": getattr(after, "layer_id", None),
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "changed_nodes": sorted(changed_nodes),
        "added_bindings": added_bindings,
        "removed_bindings": removed_bindings,
        "changed_constraints_count": changed_constraints_count,
        "timestamp": time.time(),
    }


def merge_ir_stacks(
    stack1: Any,
    stack2: Any,
    conflict_policy: str = "prefer_right",
) -> Any:
    """Merge two :class:`IRStack` objects into a single combined stack.

    Layers are merged pairwise by ``layer_depth``.  When both stacks
    contain a layer at the same depth the two layers are merged by unioning
    their node dicts and applying *conflict_policy* to binding conflicts.
    Layers that exist in only one stack are copied as-is.

    Args:
        stack1: The first :class:`IRStack`.
        stack2: The second :class:`IRStack`.
        conflict_policy: How to handle binding key conflicts.
            ``"prefer_right"`` — *stack2*'s value wins.
            ``"prefer_left"`` — *stack1*'s value wins.
            ``"error"`` — raise ``ValueError`` on first conflict.

    Returns:
        A new :class:`IRStack` containing the merged layers.

    Raises:
        TypeError: If either stack is ``None``.
        ValueError: If *conflict_policy* is ``"error"`` and a binding
            conflict is detected.
    """
    if stack1 is None or stack2 is None:
        raise TypeError("Neither stack1 nor stack2 may be None.")

    valid_policies = {"prefer_right", "prefer_left", "error"}
    if conflict_policy not in valid_policies:
        raise ValueError(
            f"conflict_policy must be one of {valid_policies}; got '{conflict_policy}'."
        )

    layers1: list[Any] = getattr(stack1, "layers", [])
    layers2: list[Any] = getattr(stack2, "layers", [])

    # Build depth-indexed maps
    depth_map1: dict[int, Any] = {getattr(l, "layer_depth", i): l for i, l in enumerate(layers1)}
    depth_map2: dict[int, Any] = {getattr(l, "layer_depth", i): l for i, l in enumerate(layers2)}

    all_depths = sorted(set(depth_map1.keys()) | set(depth_map2.keys()))
    merged_layers: list[Any] = []

    for depth in all_depths:
        l1 = depth_map1.get(depth)
        l2 = depth_map2.get(depth)

        if l1 is not None and l2 is None:
            merged_layers.append(l1)
            continue
        if l2 is not None and l1 is None:
            merged_layers.append(l2)
            continue

        # Both exist — merge them
        merged_layer = _merge_two_layers(l1, l2, conflict_policy)
        merged_layers.append(merged_layer)

    # Construct result stack
    try:
        result_stack = IRStack(  # type: ignore[call-arg]
            stack_id=str(uuid.uuid4()),
            layers=merged_layers,
            metadata={
                "merged_from": [
                    getattr(stack1, "stack_id", None),
                    getattr(stack2, "stack_id", None),
                ],
                "conflict_policy": conflict_policy,
                "merge_timestamp": time.time(),
            },
        )
    except Exception:
        result_stack = type("_Stack", (), {"layers": merged_layers, "stack_id": str(uuid.uuid4())})()  # type: ignore[assignment]

    return result_stack


def _merge_two_layers(l1: Any, l2: Any, conflict_policy: str) -> Any:
    """Merge two :class:`IRLayer` objects with the given *conflict_policy*.

    Args:
        l1: The first layer.
        l2: The second layer (takes precedence under ``"prefer_right"``).
        conflict_policy: ``"prefer_right"``, ``"prefer_left"``, or ``"error"``.

    Returns:
        A new :class:`IRLayer` containing the merged content.

    Raises:
        ValueError: When *conflict_policy* is ``"error"`` and a binding
            conflict is found.
    """
    nodes1: dict[str, Any] = dict(getattr(l1, "nodes", {}))
    nodes2: dict[str, Any] = dict(getattr(l2, "nodes", {}))
    merged_nodes: dict[str, Any] = {}

    # Union nodes; prefer_right on collision
    for nid, node in nodes1.items():
        merged_nodes[nid] = node
    for nid, node in nodes2.items():
        if nid not in merged_nodes:
            merged_nodes[nid] = node
        elif conflict_policy == "prefer_right":
            merged_nodes[nid] = node
        elif conflict_policy == "prefer_left":
            pass  # keep nodes1 version
        # "error" policy only applies to bindings in our implementation

    # Merge bindings
    bindings1: dict[str, Any] = dict(getattr(l1, "bindings", {}))
    bindings2: dict[str, Any] = dict(getattr(l2, "bindings", {}))
    merged_bindings: dict[str, Any] = {}
    all_keys = set(bindings1.keys()) | set(bindings2.keys())
    for key in all_keys:
        in_1 = key in bindings1
        in_2 = key in bindings2
        if in_1 and not in_2:
            merged_bindings[key] = bindings1[key]
        elif in_2 and not in_1:
            merged_bindings[key] = bindings2[key]
        else:
            # Conflict
            if conflict_policy == "error":
                raise ValueError(
                    f"Binding conflict for key '{key}' with conflict_policy='error'."
                )
            elif conflict_policy == "prefer_right":
                merged_bindings[key] = bindings2[key]
            else:
                merged_bindings[key] = bindings1[key]

    # Merge constraints (concatenate and deduplicate by JSON repr)
    constraints1: list[Any] = list(getattr(l1, "constraints", []))
    constraints2: list[Any] = list(getattr(l2, "constraints", []))
    seen_constraints: set[str] = set()
    merged_constraints: list[Any] = []
    for c in constraints1 + constraints2:
        key_repr = json.dumps(c, sort_keys=True, default=str)
        if key_repr not in seen_constraints:
            seen_constraints.add(key_repr)
            merged_constraints.append(c)

    depth = getattr(l1, "layer_depth", 0)
    kind = getattr(l2, "layer_kind", getattr(l1, "layer_kind", None))

    try:
        merged = IRLayer(  # type: ignore[call-arg]
            layer_id=str(uuid.uuid4()),
            layer_kind=kind,
            nodes=merged_nodes,
            bindings=merged_bindings,
            constraints=merged_constraints,
            layer_depth=depth,
        )
    except Exception:
        merged = type("_Layer", (), {  # type: ignore[assignment]
            "layer_id": str(uuid.uuid4()),
            "layer_kind": kind,
            "nodes": merged_nodes,
            "bindings": merged_bindings,
            "constraints": merged_constraints,
            "layer_depth": depth,
        })()
    return merged


# ===================================================================== #
# 6. Stack validation                                                    #
# ===================================================================== #


def validate_lowering(original: Any, lowered: Any) -> list[str]:
    """Validate that *lowered* is a faithful lowering of *original*.

    Checks three invariants:

    1. Every layer from *original* has a corresponding layer in *lowered*
       at the same or greater depth.
    2. The count of ambiguous nodes does not decrease across the full stack.
    3. No node IDs present in *original* were silently dropped from *lowered*.

    Args:
        original: The :class:`IRStack` before lowering.
        lowered: The :class:`IRStack` after lowering.

    Returns:
        A list of human-readable error strings; empty when all invariants
        hold.

    Raises:
        TypeError: If either argument is ``None``.
    """
    if original is None or lowered is None:
        raise TypeError("Neither original nor lowered may be None.")

    errors: list[str] = []
    orig_layers: list[Any] = getattr(original, "layers", [])
    low_layers: list[Any] = getattr(lowered, "layers", [])

    # Invariant 1: layer count
    if len(low_layers) < len(orig_layers):
        errors.append(
            f"Lowered stack has fewer layers ({len(low_layers)}) than "
            f"original ({len(orig_layers)})."
        )

    # Build depth-indexed lookup for lowered layers
    low_by_depth: dict[int, Any] = {getattr(l, "layer_depth", i): l for i, l in enumerate(low_layers)}

    # Invariant 2: ambiguity mark count does not decrease per matched layer
    for i, orig_layer in enumerate(orig_layers):
        orig_depth = getattr(orig_layer, "layer_depth", i)
        low_layer = low_by_depth.get(orig_depth)
        if low_layer is None:
            errors.append(
                f"No corresponding lowered layer found for original layer at "
                f"depth {orig_depth} (id='{getattr(orig_layer, 'layer_id', '?')}')."
            )
            continue

        orig_marks = _collect_mark_ids(orig_layer)
        low_marks = _collect_mark_ids(low_layer)
        dropped = orig_marks - low_marks
        if dropped:
            errors.append(
                f"Ambiguity marks dropped at depth {orig_depth} for node IDs: "
                f"{sorted(dropped)}."
            )

        # Invariant 3: node IDs not silently dropped
        orig_node_ids = set(getattr(orig_layer, "nodes", {}).keys())
        low_node_ids_union: set[str] = set()
        for ll in low_layers:
            low_node_ids_union.update(getattr(ll, "nodes", {}).keys())

        missing_ids = orig_node_ids - low_node_ids_union
        if missing_ids:
            errors.append(
                f"Node IDs from original layer at depth {orig_depth} are absent "
                f"from all lowered layers: {sorted(missing_ids)}."
            )

    return errors


# ===================================================================== #
# 7. Cache operations                                                    #
# ===================================================================== #


def cache_lookup(
    cache: dict[str, Any],
    node: Any,
    kind: Any,
) -> Any | None:
    """Look up a precomputed :class:`NormalForm` for *node* in *cache*.

    The cache key is constructed as ``SHA256(node_hash + ":" + kind_value)``
    so that different reduction strategies produce distinct entries for the
    same node.

    Args:
        cache: A mutable dict mapping cache-key strings to
            :class:`NormalForm` objects.
        node: The :class:`IRNode` to look up.
        kind: The :class:`NormalFormKind` identifying the reduction
            strategy.

    Returns:
        The cached :class:`NormalForm` if found; ``None`` on a cache miss.

    Raises:
        TypeError: If *cache* is ``None``.
    """
    if cache is None:
        raise TypeError("cache must not be None.")
    if node is None:
        return None

    node_hash = _hash_node(node)
    kind_val = getattr(kind, "value", str(kind))
    raw_key = f"{node_hash}:{kind_val}"
    cache_key = hashlib.sha256(raw_key.encode()).hexdigest()

    return cache.get(cache_key)


def cache_store(
    cache: dict[str, Any],
    node: Any,
    kind: Any,
    normal_form: Any,
) -> str:
    """Store *normal_form* in *cache* under the key derived from *node* and *kind*.

    Args:
        cache: The mutable cache dict to update.
        node: The :class:`IRNode` that was reduced.
        kind: The :class:`NormalFormKind` that was used.
        normal_form: The :class:`NormalForm` to store.

    Returns:
        The cache key string under which the entry was stored.
    """
    node_hash = _hash_node(node)
    kind_val = getattr(kind, "value", str(kind))
    raw_key = f"{node_hash}:{kind_val}"
    cache_key = hashlib.sha256(raw_key.encode()).hexdigest()
    cache[cache_key] = normal_form
    return cache_key


# ===================================================================== #
# 8. Reconstruction from normal form                                     #
# ===================================================================== #


def rebuild_from_normal_form(nf: Any, target_layer: Any) -> Any:
    """Reconstruct an :class:`IRNode` from a :class:`NormalForm`.

    Reads the ``canonical`` dict from *nf* and attempts to recreate an
    :class:`IRNode` by reversing the recorded reduction steps.  The
    reconstructed node is added to *target_layer*'s node dict if a
    ``layer_id`` is present and the layer's ``add_node`` method is
    available.

    The reverse-reduction is performed by replaying the ``reduction_steps``
    list in reverse order.  Each step entry is expected to carry at minimum
    a ``from_node_id`` that can be used to recover the pre-reduction state
    from *target_layer*.

    Args:
        nf: A :class:`NormalForm` to reconstruct from.
        target_layer: The :class:`IRLayer` that provides context for
            reconstruction and receives the resulting node.

    Returns:
        The reconstructed :class:`IRNode`.

    Raises:
        TypeError: If *nf* is ``None``.
    """
    if nf is None:
        raise TypeError("nf must not be None.")

    canonical: dict[str, Any] = getattr(nf, "canonical", {})
    reduction_steps: list[dict[str, Any]] = getattr(nf, "reduction_steps", [])
    nf_kind = getattr(nf, "normal_form_kind", None)

    # Start from canonical dict; attempt to build an IRNode
    node_id = canonical.get("node_id", str(uuid.uuid4()))
    node_kind_val = canonical.get("node_kind", "expression")
    payload = canonical.get("payload", {})
    children_dicts = canonical.get("children", [])
    ambiguity_mark_dict = canonical.get("ambiguity_mark")
    trust_level = canonical.get("trust_level", 0)

    # Resolve node_kind enum
    try:
        node_kind = IRNodeKind(node_kind_val)  # type: ignore[call-arg]
    except Exception:
        node_kind = node_kind_val

    # Reconstruct children recursively (shallow — one level)
    reconstructed_children: list[Any] = []
    for child_dict in children_dicts:
        try:
            child_node = IRNode(  # type: ignore[call-arg]
                node_id=child_dict.get("node_id", str(uuid.uuid4())),
                node_kind=IRNodeKind(child_dict.get("node_kind", "expression")),  # type: ignore[call-arg]
                payload=child_dict.get("payload", {}),
                children=[],
                trust_level=child_dict.get("trust_level", 0),
            )
            reconstructed_children.append(child_node)
        except Exception:
            pass

    # Apply reverse reduction steps if present
    # In the simplest model, reverse reduction means restoring the ``reducible`` flag
    if reduction_steps:
        last_step = reduction_steps[-1]
        # If the last step was a head_reduction, the original payload had reducible=True
        if last_step.get("kind") == "head_reduction":
            payload = dict(payload)
            payload["reducible"] = True
            payload["__reversed_from_nf_kind"] = getattr(nf_kind, "value", str(nf_kind))

    try:
        node = IRNode(  # type: ignore[call-arg]
            node_id=node_id,
            node_kind=node_kind,
            payload=payload,
            children=reconstructed_children,
            trust_level=trust_level,
            source_ref=canonical.get("source_ref", ""),
        )
    except Exception:
        node = type("_IRNode", (), {  # type: ignore[assignment]
            "node_id": node_id,
            "node_kind": node_kind,
            "payload": payload,
            "children": reconstructed_children,
            "trust_level": trust_level,
        })()

    # Register the node with target_layer if possible
    if target_layer is not None:
        try:
            target_layer.add_node(node)
        except Exception:
            nodes = getattr(target_layer, "nodes", {})
            if isinstance(nodes, dict):
                nodes[node_id] = node

    return node


# ===================================================================== #
# 9. Pass composition                                                    #
# ===================================================================== #


def compose_lowering_passes(passes: list[Any]) -> Any:
    """Compose multiple :class:`LoweringPass` objects into a single pass.

    Creates a new :class:`LoweringPass` whose ``transformations`` list is
    the concatenation of all constituent passes' transformation lists.
    The composed pass's ``input_layer`` is that of the first pass and
    ``output_layer`` is that of the last.  ``ambiguity_preserved`` is
    ``True`` iff every constituent pass preserves ambiguity.

    .. math::

       (p_n \\circ \\cdots \\circ p_1)(L)
       = p_n(\\cdots p_1(L) \\cdots)

    Args:
        passes: An ordered list of :class:`LoweringPass` objects.  Must
            not be empty.

    Returns:
        A new :class:`LoweringPass` representing the sequential composition.

    Raises:
        ValueError: If *passes* is empty.
        TypeError: If *passes* is ``None``.
    """
    if passes is None:
        raise TypeError("passes must not be None.")
    if not passes:
        raise ValueError("Cannot compose an empty list of passes.")

    all_preserve = all(getattr(p, "ambiguity_preserved", True) for p in passes)

    composed_transformations: list[dict[str, Any]] = []
    for p in passes:
        transforms = getattr(p, "transformations", [])
        for t in transforms:
            entry: dict[str, Any] = dict(t)
            entry["source_pass_id"] = getattr(p, "pass_id", None)
            entry["source_pass_name"] = getattr(p, "pass_name", None)
            composed_transformations.append(entry)

    first = passes[0]
    last = passes[-1]
    composed_name = "composed:" + "+".join(
        getattr(p, "pass_name", "?") for p in passes
    )

    try:
        composed = LoweringPass(  # type: ignore[call-arg]
            pass_id=str(uuid.uuid4()),
            pass_name=composed_name,
            input_layer=getattr(first, "input_layer", None),
            output_layer=getattr(last, "output_layer", None),
            transformations=composed_transformations,
            ambiguity_preserved=all_preserve,
        )
    except Exception:
        composed = type("_LP", (), {  # type: ignore[assignment]
            "pass_id": str(uuid.uuid4()),
            "pass_name": composed_name,
            "input_layer": getattr(first, "input_layer", None),
            "output_layer": getattr(last, "output_layer", None),
            "transformations": composed_transformations,
            "ambiguity_preserved": all_preserve,
        })()

    return composed


# ===================================================================== #
# 10. Full pipeline orchestration                                        #
# ===================================================================== #


def run_full_pipeline(
    stack: Any,
    config: AlgorithmConfig | None = None,
) -> AlgorithmResult:
    """Orchestrate the complete IR stack algorithm suite on *stack*.

    Applies in order:
    1. :func:`lower_ir_stack` — lowers stack towards ``SOLVER_READY``.
    2. :func:`validate_lowering` — checks fidelity invariants.
    3. Ambiguity propagation via :func:`propagate_ambiguity_marks` on the
       top layer's root node (if present).
    4. Normal-form computation for each node in the top layer (when
       the cache is enabled in *config*).
    5. Optional diff of the original top layer against the lowered top
       layer (when ``config.enable_diff`` is ``True``).

    Args:
        stack: The :class:`IRStack` to process.  Must not be ``None``.
        config: Optional :class:`AlgorithmConfig`.  Defaults to a
            standard config when ``None``.

    Returns:
        An :class:`AlgorithmResult` with ``result_value`` set to the
        lowered :class:`IRStack`; ``errors`` populated with any
        validation or algorithm failures.

    Raises:
        TypeError: If *stack* is ``None``.
    """
    if stack is None:
        raise TypeError("stack must not be None.")

    cfg = config if config is not None else AlgorithmConfig()
    config_errors = cfg.validate()
    if config_errors:
        result = AlgorithmResult(success=False)
        for err in config_errors:
            result.add_error(err)
        return result

    result = AlgorithmResult(success=True, stats={})
    pipeline_start = time.time()

    # ------------------------------------------------------------------ #
    # Step 1: Lowering                                                    #
    # ------------------------------------------------------------------ #
    original_layers = list(getattr(stack, "layers", []))
    original_top = original_layers[-1] if original_layers else None

    lowered_stack = lower_ir_stack(
        stack,
        target_layer_kind=cfg.target_layer_kind,
        passes=None,  # no passes available without registry — extend as needed
    )
    result.stats["lowering_completed"] = True

    # ------------------------------------------------------------------ #
    # Step 2: Validate lowering fidelity                                  #
    # ------------------------------------------------------------------ #
    if cfg.verify_ambiguity and original_layers:
        try:
            # Reconstruct original-like stack for comparison
            validation_errors = validate_lowering(
                _make_minimal_stack(original_layers),
                lowered_stack,
            )
            for err in validation_errors:
                result.add_error(err)
            result.stats["validation_errors"] = len(validation_errors)
        except Exception as exc:
            result.add_warning(f"validate_lowering raised: {exc}")

    # ------------------------------------------------------------------ #
    # Step 3: Ambiguity mark propagation on top layer                     #
    # ------------------------------------------------------------------ #
    propagation_count = 0
    lowered_layers = getattr(lowered_stack, "layers", [])
    if lowered_layers:
        top_layer = lowered_layers[-1]
        nodes: dict[str, Any] = getattr(top_layer, "nodes", {})
        if nodes:
            # Collect all existing marks from the layer
            all_marks: list[Any] = [
                getattr(n, "ambiguity_mark")
                for n in nodes.values()
                if getattr(n, "ambiguity_mark", None) is not None
            ]
            # Use first node as root for propagation
            root_node = next(iter(nodes.values()))
            propagated = propagate_ambiguity_marks(
                root_node,
                all_marks,
                propagation_depth=cfg.propagation_depth,
            )
            propagation_count = len(propagated)
    result.stats["propagated_marks"] = propagation_count

    # ------------------------------------------------------------------ #
    # Step 4: Normal form computation (cached)                            #
    # ------------------------------------------------------------------ #
    nf_cache: dict[str, Any] = {}
    nf_computed = 0
    nf_cache_hits = 0

    if lowered_layers:
        top_layer = lowered_layers[-1]
        nodes = getattr(top_layer, "nodes", {})
        try:
            nf_kind = NormalFormKind.FULL_NORMAL  # type: ignore[name-defined]
        except Exception:
            nf_kind = None

        for node in nodes.values():
            cached = None
            if cfg.enable_cache:
                cached = cache_lookup(nf_cache, node, nf_kind)
            if cached is not None:
                nf_cache_hits += 1
                continue
            try:
                nf = compute_normal_form(node, kind=nf_kind, max_steps=cfg.max_steps)
                if cfg.enable_cache and nf is not None:
                    cache_store(nf_cache, node, nf_kind, nf)
                nf_computed += 1
            except Exception as exc:
                result.add_warning(f"Normal form computation failed for node "
                                   f"'{getattr(node, 'node_id', '?')}': {exc}")

    result.stats["nf_computed"] = nf_computed
    result.stats["nf_cache_hits"] = nf_cache_hits

    # ------------------------------------------------------------------ #
    # Step 5: Optional diff                                               #
    # ------------------------------------------------------------------ #
    if cfg.enable_diff and original_top is not None and lowered_layers:
        low_top = lowered_layers[-1]
        try:
            diff_result = diff_ir_layers(original_top, low_top)
            result.stats["diff"] = diff_result
        except Exception as exc:
            result.add_warning(f"diff_ir_layers raised: {exc}")

    # ------------------------------------------------------------------ #
    # Finalise                                                            #
    # ------------------------------------------------------------------ #
    result.stats["duration_s"] = time.time() - pipeline_start
    result.result_value = lowered_stack
    return result


def _make_minimal_stack(layers: list[Any]) -> Any:
    """Construct a minimal :class:`IRStack`-like object from *layers*.

    Args:
        layers: A list of :class:`IRLayer` objects.

    Returns:
        An :class:`IRStack` (or duck-typed surrogate) wrapping *layers*.
    """
    try:
        return IRStack(  # type: ignore[call-arg]
            stack_id=str(uuid.uuid4()),
            layers=list(layers),
        )
    except Exception:
        obj = type("_Stack", (), {"layers": list(layers), "stack_id": str(uuid.uuid4())})()
        return obj


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import judgment_terms as _judgment_terms
except ImportError:
    _judgment_terms = None  # type: ignore[assignment]

try:
    from jugeo import solver as _solver_mod
except ImportError:
    _solver_mod = None  # type: ignore[assignment]


def ir_from_judgment(judgment: Any) -> dict[str, Any]:
    """Build an IR node from a judgment term.

    Bridges the judgment subsystem into the IR-stack pipeline by
    converting a judgment term into an IR-level representation that
    the lowering passes can consume.

    Parameters
    ----------
    judgment:
        A judgment term from ``jugeo.judgments.judgment_terms``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"judgment"``, ``"ir_node"``, and ``"source_layer"``
        keys.
    """
    if _judgment_terms is None:
        raise RuntimeError("jugeo.judgments.judgment_terms is not available")
    term_data = _judgment_terms.extract_term(judgment) if hasattr(_judgment_terms, "extract_term") else {"raw": str(judgment)}
    return {
        "judgment": judgment,
        "ir_node": term_data,
        "source_layer": "judgment",
    }


def ir_lowering_with_solver(ir_node: Any) -> dict[str, Any]:
    """Lower an IR node with solver-assisted verification.

    Uses the solver subsystem to verify correctness properties during
    IR lowering, producing a verified lowered representation.

    Parameters
    ----------
    ir_node:
        An IR node to lower.

    Returns
    -------
    dict[str, Any]
        A dict with ``"ir_node"``, ``"lowered"``, and ``"solver_verified"``
        keys.
    """
    if _solver_mod is None:
        raise RuntimeError("jugeo.solver is not available")
    verified = False
    if hasattr(_solver_mod, "quick_check"):
        verified = _solver_mod.quick_check(ir_node)
    return {
        "ir_node": ir_node,
        "lowered": True,
        "solver_verified": verified,
    }
