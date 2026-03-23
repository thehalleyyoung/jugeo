r"""Lowering passes that preserve ambiguity marks across IR strata.

This module implements the lowering-pass framework described in Chapter 32 §4
of ``theory2.tex`` — *Internal Representations and the IR Stack*.  A
*lowering pass* is a monotone transformation from one :class:`IRLayer` kind
to another that is required to **preserve** every ambiguity mark present in
its input:

.. math::

   \forall p \in \text{LoweringPass},\; \forall L \in \text{IRLayer}:\;
   \text{ambiguity}(p(L)) \supseteq \text{ambiguity}(L)

This invariant ensures that no unresolved ambiguity is silently dropped
during the pipeline from ``SURFACE`` through ``SEMANTIC`` and ``LOGICAL`` to
``SOLVER_READY``.  Violations of the invariant are recorded as
:class:`AmbiguityPreservationViolation` entries, collected by the
:class:`AmbiguityPreservationChecker`, and surfaced in the execution report
produced by :class:`LoweringPipeline`.

Architecture
------------

The central objects in this module are:

* :class:`LoweringPassRegistry` — owns the catalogue of available passes and
  resolves dependency order via a topological sort.
* :class:`AmbiguityPreservationChecker` — verifies the invariant above after
  each pass application.
* :class:`PassComposer` — assembles a sequence of individual passes into a
  single composite pass, with optional redundancy elimination.
* :class:`LoweringPipeline` — orchestrates the full ``SURFACE → SOLVER_READY``
  pipeline, supporting checkpoints and rollback.
* :class:`CopilotLoweringHint` — records copilot-assisted ordering and
  disambiguation suggestions.
* :class:`StandardLoweringPasses` — factory for the five canonical passes
  described in §32.4.

Module-level convenience helpers (:func:`lower_layer`, :func:`lower_stack`,
:func:`verify_lowering_fidelity`, :func:`create_standard_pipeline`) are
provided for single-call use.

Theory alignment
~~~~~~~~~~~~~~~~

* §32.1 — IR layer kinds and depth ordering
* §32.4 — Standard lowering pass catalogue
* §32.5 — Ambiguity preservation proof obligations
* §32.6 — Copilot-assisted lowering hints
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
        LoweringPass,
        LoweringPassKind,
        IRNodeKind,
        IRLayerKind,
        AmbiguityMark,
        AmbiguityKind,
        NormalForm,
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
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel  # type: ignore[import]
except ImportError:
    class TrustAlgebra:  # type: ignore[no-redef]
        pass

    class TrustLevel:  # type: ignore[no-redef]
        pass


# ===================================================================== #
# 1. Lowering pass framework                                             #
# ===================================================================== #


class LoweringPassRegistry:
    """Registry of all available lowering passes for the IR stack pipeline.

    The registry maintains an ordered catalogue of :class:`LoweringPass`
    objects, tracks inter-pass dependencies, and can resolve a topological
    execution order so that downstream passes only run after all of their
    prerequisites have completed.

    Attributes:
        _passes: Mapping from ``pass_id`` to :class:`LoweringPass`.
        _pass_order: Insertion-ordered list of ``pass_id`` values.
        _pass_dependencies: Maps each ``pass_id`` to the list of
            ``pass_id`` values it depends on.
    """

    def __init__(self) -> None:
        self._passes: dict[str, Any] = {}
        self._pass_order: list[str] = []
        self._pass_dependencies: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def register(self, lp: Any) -> None:
        """Register a :class:`LoweringPass` by its ``pass_id``.

        If a pass with the same ``pass_id`` already exists it is
        silently replaced.  The dependency list is initialised to an
        empty list when the pass is registered for the first time.

        Args:
            lp: The :class:`LoweringPass` to register.
        """
        pid = lp.pass_id
        if pid not in self._passes:
            self._pass_order.append(pid)
            self._pass_dependencies.setdefault(pid, [])
        self._passes[pid] = lp

    def get(self, pass_id: str) -> Any | None:
        """Return the :class:`LoweringPass` with the given ``pass_id``.

        Args:
            pass_id: The unique identifier of the desired pass.

        Returns:
            The matching :class:`LoweringPass`, or ``None`` if not found.
        """
        return self._passes.get(pass_id)

    def add_dependency(self, pass_id: str, depends_on: str) -> None:
        """Declare that *pass_id* must run after *depends_on*.

        Args:
            pass_id: The pass that has a dependency.
            depends_on: The pass that must complete first.
        """
        self._pass_dependencies.setdefault(pass_id, [])
        if depends_on not in self._pass_dependencies[pass_id]:
            self._pass_dependencies[pass_id].append(depends_on)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def list_passes(self, kind: Any | None = None) -> list[Any]:
        """Return all registered passes, optionally filtered by kind.

        Args:
            kind: An optional :class:`LoweringPassKind` to filter on.
                When ``None`` all registered passes are returned.

        Returns:
            A list of :class:`LoweringPass` objects in registration order.
        """
        passes = [self._passes[pid] for pid in self._pass_order if pid in self._passes]
        if kind is None:
            return passes
        return [p for p in passes if getattr(p, "pass_kind", None) == kind]

    def get_pipeline(self, input_kind: Any, output_kind: Any) -> list[Any]:
        """Find the minimal sequence of passes transforming *input_kind* to *output_kind*.

        The method performs a BFS over the registered passes using the
        ``input_layer`` / ``output_layer`` attributes as edges in a
        directed graph.  It returns the shortest discovered path from
        *input_kind* to *output_kind*, or an empty list when no path
        exists.

        Args:
            input_kind: Starting :class:`IRLayerKind`.
            output_kind: Target :class:`IRLayerKind`.

        Returns:
            An ordered list of :class:`LoweringPass` objects forming the
            pipeline, or an empty list if no path was found.
        """
        if input_kind == output_kind:
            return []

        # Build adjacency: source_kind -> list of (dest_kind, pass)
        graph: dict[Any, list[tuple[Any, Any]]] = collections.defaultdict(list)
        for lp in self._passes.values():
            src = getattr(lp, "input_layer", None)
            dst = getattr(lp, "output_layer", None)
            if src is not None and dst is not None:
                graph[src].append((dst, lp))

        # BFS
        visited: set[Any] = set()
        queue: collections.deque[tuple[Any, list[Any]]] = collections.deque()
        queue.append((input_kind, []))

        while queue:
            current_kind, path_so_far = queue.popleft()
            if current_kind in visited:
                continue
            visited.add(current_kind)
            for next_kind, lp in graph.get(current_kind, []):
                new_path = path_so_far + [lp]
                if next_kind == output_kind:
                    return new_path
                if next_kind not in visited:
                    queue.append((next_kind, new_path))

        return []

    def validate_dependencies(self) -> list[str]:
        """Return error messages for any missing dependency declarations.

        Checks that every pass_id listed as a dependency of another pass
        is itself registered.

        Returns:
            A list of human-readable error strings, empty when all
            dependencies are satisfied.
        """
        errors: list[str] = []
        for pid, deps in self._pass_dependencies.items():
            for dep in deps:
                if dep not in self._passes:
                    errors.append(
                        f"Pass '{pid}' declares dependency on '{dep}' which is not registered."
                    )
        return errors

    def topological_order(self) -> list[str]:
        """Return pass IDs in a valid topological execution order.

        Uses Kahn's algorithm.  Passes with no unresolved dependencies
        are placed first.  If a cycle is detected the remaining IDs are
        appended in registration order and a ``RuntimeError`` is **not**
        raised — callers should call :meth:`validate_dependencies` first.

        Returns:
            An ordered list of ``pass_id`` strings.
        """
        in_degree: dict[str, int] = {pid: 0 for pid in self._pass_order}
        for pid, deps in self._pass_dependencies.items():
            for _ in deps:
                in_degree[pid] = in_degree.get(pid, 0) + 1

        queue: collections.deque[str] = collections.deque(
            pid for pid in self._pass_order if in_degree.get(pid, 0) == 0
        )
        result: list[str] = []
        while queue:
            pid = queue.popleft()
            result.append(pid)
            for other_pid, deps in self._pass_dependencies.items():
                if pid in deps:
                    in_degree[other_pid] -= 1
                    if in_degree[other_pid] == 0:
                        queue.append(other_pid)

        # Append any remaining (cycle / unvisited) in original order
        remaining = [pid for pid in self._pass_order if pid not in result]
        result.extend(remaining)
        return result

    def statistics(self) -> dict[str, Any]:
        """Return summary statistics about the registered passes.

        Returns:
            A dict with keys:
            * ``total`` — total number of registered passes.
            * ``by_kind`` — mapping of kind name to count.
            * ``ambiguity_preserving`` — count of passes with
              ``ambiguity_preserved == True``.
            * ``non_preserving`` — count of passes that do not preserve.
            * ``dependency_edges`` — total number of declared dependencies.
        """
        by_kind: dict[str, int] = collections.defaultdict(int)
        preserving = 0
        non_preserving = 0
        for lp in self._passes.values():
            kind_val = getattr(getattr(lp, "pass_kind", None), "value", "unknown")
            by_kind[kind_val] += 1
            if getattr(lp, "ambiguity_preserved", True):
                preserving += 1
            else:
                non_preserving += 1

        dep_edges = sum(len(v) for v in self._pass_dependencies.values())
        return {
            "total": len(self._passes),
            "by_kind": dict(by_kind),
            "ambiguity_preserving": preserving,
            "non_preserving": non_preserving,
            "dependency_edges": dep_edges,
        }


# ===================================================================== #
# 2. Ambiguity preservation invariant                                    #
# ===================================================================== #


class AmbiguityPreservationChecker:
    """Verifies that lowering passes preserve all ambiguity marks.

    For each pass application the checker compares the set of ambiguity
    marks present *before* the pass with those present *after*.  Any mark
    that was present before but is absent after constitutes a *violation*
    of the invariant described in the module docstring.

    Attributes:
        checker_id: Unique identifier for this checker instance.
        violations: Accumulated list of violation records.
        _check_log: Internal log of every check performed, including
            passing checks.
    """

    def __init__(self) -> None:
        self.checker_id: str = str(uuid.uuid4())
        self.violations: list[dict[str, Any]] = []
        self._check_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Core invariant checks
    # ------------------------------------------------------------------

    def check(self, before: Any, after: Any, pass_name: str) -> bool:
        """Compare ambiguity marks before and after a lowering pass.

        The check succeeds when every mark present in *before* is also
        present in *after* (additional marks in *after* are allowed).
        A failed check appends an entry to :attr:`violations`.

        Args:
            before: The :class:`IRLayer` before the pass was applied.
            after: The :class:`IRLayer` after the pass was applied.
            pass_name: Human-readable name of the pass (for reporting).

        Returns:
            ``True`` when the invariant holds; ``False`` otherwise.
        """
        before_marks = self.collect_ambiguity_marks(before)
        after_marks = self.collect_ambiguity_marks(after)

        dropped = set(before_marks.keys()) - set(after_marks.keys())
        passed = len(dropped) == 0

        entry: dict[str, Any] = {
            "check_id": str(uuid.uuid4()),
            "pass_name": pass_name,
            "timestamp": time.time(),
            "before_layer_id": getattr(before, "layer_id", None),
            "after_layer_id": getattr(after, "layer_id", None),
            "before_count": len(before_marks),
            "after_count": len(after_marks),
            "dropped_node_ids": list(dropped),
            "passed": passed,
        }
        self._check_log.append(entry)

        if not passed:
            violation: dict[str, Any] = {
                "violation_id": str(uuid.uuid4()),
                "pass_name": pass_name,
                "dropped_marks": {nid: before_marks[nid] for nid in dropped},
                "timestamp": entry["timestamp"],
                "before_layer_id": entry["before_layer_id"],
                "after_layer_id": entry["after_layer_id"],
            }
            self.violations.append(violation)

        return passed

    def count_ambiguous_nodes(self, layer: Any) -> int:
        """Count the number of nodes in *layer* that carry an ambiguity mark.

        A node is considered ambiguous when its ``ambiguity_mark`` attribute
        is not ``None``.

        Args:
            layer: An :class:`IRLayer` to inspect.

        Returns:
            Integer count of nodes with a non-``None`` ambiguity mark.
        """
        nodes: dict[str, Any] = getattr(layer, "nodes", {})
        return sum(
            1 for node in nodes.values() if getattr(node, "ambiguity_mark", None) is not None
        )

    def collect_ambiguity_marks(self, layer: Any) -> dict[str, Any]:
        """Build a mapping of node_id to :class:`AmbiguityMark` for *layer*.

        Only nodes that carry a non-``None`` ``ambiguity_mark`` are
        included.

        Args:
            layer: An :class:`IRLayer` to inspect.

        Returns:
            A dict mapping ``node_id`` strings to :class:`AmbiguityMark`
            objects.
        """
        nodes: dict[str, Any] = getattr(layer, "nodes", {})
        result: dict[str, Any] = {}
        for node_id, node in nodes.items():
            mark = getattr(node, "ambiguity_mark", None)
            if mark is not None:
                result[node_id] = mark
        return result

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------

    def report_violations(self) -> list[dict[str, Any]]:
        """Return all recorded violation records.

        Returns:
            A list of dicts, each describing one invariant violation.
        """
        return list(self.violations)

    def clear_violations(self) -> None:
        """Reset the violations list and check log to empty.

        Use this between independent pipeline runs to avoid
        cross-contaminating violation records.
        """
        self.violations = []
        self._check_log = []

    def generate_invariant_proof(self, before: Any, after: Any) -> dict[str, Any]:
        """Construct a proof certificate that the invariant holds.

        The proof records the exact mark sets before and after and
        confirms that the after-set is a superset of the before-set.
        The certificate includes a SHA-256 hash of the serialised mark
        sets for tamper-evidence.

        Args:
            before: The :class:`IRLayer` before lowering.
            after: The :class:`IRLayer` after lowering.

        Returns:
            A dict with keys ``before_marks``, ``after_marks``,
            ``is_superset``, ``dropped``, ``added``, and
            ``certificate_hash``.
        """
        before_marks = self.collect_ambiguity_marks(before)
        after_marks = self.collect_ambiguity_marks(after)
        before_keys = set(before_marks.keys())
        after_keys = set(after_marks.keys())
        dropped = before_keys - after_keys
        added = after_keys - before_keys
        is_superset = len(dropped) == 0

        payload = json.dumps(
            {
                "before_node_ids": sorted(before_keys),
                "after_node_ids": sorted(after_keys),
                "dropped": sorted(dropped),
                "added": sorted(added),
            },
            sort_keys=True,
        )
        certificate_hash = hashlib.sha256(payload.encode()).hexdigest()

        return {
            "proof_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "before_marks": list(before_keys),
            "after_marks": list(after_keys),
            "is_superset": is_superset,
            "dropped": list(dropped),
            "added": list(added),
            "certificate_hash": certificate_hash,
        }

    def is_invariant_satisfied(self) -> bool:
        """Return ``True`` when no violations have been recorded.

        Returns:
            ``True`` if :attr:`violations` is empty; ``False`` otherwise.
        """
        return len(self.violations) == 0


# ===================================================================== #
# 3. Pass composition                                                    #
# ===================================================================== #


class PassComposer:
    """Assembles multiple lowering passes into a single pipeline.

    A :class:`PassComposer` accumulates passes via :meth:`add_pass` and
    can either produce a single composite :class:`LoweringPass` via
    :meth:`compose` or apply the pipeline to an :class:`IRLayer`
    incrementally via :meth:`apply_pipeline`.

    Attributes:
        composer_id: Unique identifier for this composer instance.
        passes: Ordered list of passes currently in the pipeline.
        _composition_log: Records each composition and application event.
        verify_after_each: When ``True``, an :class:`AmbiguityPreservationChecker`
            is invoked after each step during :meth:`apply_pipeline`.
    """

    def __init__(self, verify_after_each: bool = True) -> None:
        self.composer_id: str = str(uuid.uuid4())
        self.passes: list[Any] = []
        self._composition_log: list[dict[str, Any]] = []
        self.verify_after_each: bool = verify_after_each

    # ------------------------------------------------------------------
    # Pipeline construction
    # ------------------------------------------------------------------

    def add_pass(self, lp: Any) -> None:
        """Append *lp* to the end of the current pipeline.

        Args:
            lp: A :class:`LoweringPass` to add.
        """
        self.passes.append(lp)
        self._composition_log.append(
            {
                "event": "add_pass",
                "pass_id": getattr(lp, "pass_id", None),
                "pass_name": getattr(lp, "pass_name", None),
                "timestamp": time.time(),
            }
        )

    def compose(self, passes: list[Any]) -> Any:
        """Create a single composite :class:`LoweringPass` from *passes*.

        The composed pass preserves ambiguity when **all** constituent
        passes preserve it.  The ``transformations`` list is the
        concatenation of each constituent's transformations.

        Args:
            passes: An ordered list of :class:`LoweringPass` objects to
                compose.

        Returns:
            A new :class:`LoweringPass` representing the sequential
            composition of all supplied passes.

        Raises:
            ValueError: If *passes* is empty.
        """
        if not passes:
            raise ValueError("Cannot compose an empty list of passes.")

        try:
            composed_transformations: list[dict[str, Any]] = []
            for p in passes:
                composed_transformations.extend(getattr(p, "transformations", []))

            all_preserve = all(getattr(p, "ambiguity_preserved", True) for p in passes)
            first = passes[0]
            last = passes[-1]

            composed = LoweringPass(  # type: ignore[call-arg]
                pass_id=str(uuid.uuid4()),
                pass_name="composed:" + "+".join(
                    getattr(p, "pass_name", "?") for p in passes
                ),
                input_layer=getattr(first, "input_layer", None),
                output_layer=getattr(last, "output_layer", None),
                transformations=composed_transformations,
                ambiguity_preserved=all_preserve,
            )
        except Exception:
            # Fallback: return first pass unchanged when model not available
            composed = passes[0]

        self._composition_log.append(
            {
                "event": "compose",
                "composed_pass_id": getattr(composed, "pass_id", None),
                "constituent_ids": [getattr(p, "pass_id", None) for p in passes],
                "timestamp": time.time(),
            }
        )
        return composed

    # ------------------------------------------------------------------
    # Pipeline application
    # ------------------------------------------------------------------

    def apply_pipeline(self, layer: Any) -> tuple[Any, list[dict[str, Any]]]:
        """Apply all passes in :attr:`passes` sequentially to *layer*.

        When :attr:`verify_after_each` is ``True`` an
        :class:`AmbiguityPreservationChecker` is consulted at each step
        and a warning is appended to the log on violation.

        Args:
            layer: The starting :class:`IRLayer`.

        Returns:
            A two-tuple ``(result_layer, log_entries)`` where
            *result_layer* is the transformed layer and *log_entries*
            records each step.
        """
        checker = AmbiguityPreservationChecker()
        log_entries: list[dict[str, Any]] = []
        current = layer

        for lp in self.passes:
            step_start = time.time()
            prev = current

            # Apply the pass (real apply when model is available)
            try:
                current = lp.apply(current)
            except Exception as exc:
                current = prev
                log_entries.append(
                    {
                        "event": "pass_error",
                        "pass_name": getattr(lp, "pass_name", None),
                        "error": str(exc),
                        "timestamp": time.time(),
                    }
                )
                continue

            duration = time.time() - step_start

            violated = False
            if self.verify_after_each:
                ok = checker.check(prev, current, getattr(lp, "pass_name", "unknown"))
                violated = not ok

            log_entries.append(
                {
                    "event": "pass_applied",
                    "pass_name": getattr(lp, "pass_name", None),
                    "pass_id": getattr(lp, "pass_id", None),
                    "duration_s": duration,
                    "invariant_violated": violated,
                    "timestamp": time.time(),
                }
            )

        return current, log_entries

    # ------------------------------------------------------------------
    # Pipeline analysis
    # ------------------------------------------------------------------

    def validate_pipeline(self) -> list[str]:
        """Check that consecutive passes have compatible layer kinds.

        For each adjacent pair ``(passes[i], passes[i+1])`` the method
        verifies that ``passes[i].output_layer == passes[i+1].input_layer``.

        Returns:
            A list of human-readable error strings for each incompatibility
            found; empty when the pipeline is valid.
        """
        errors: list[str] = []
        for i in range(len(self.passes) - 1):
            current_out = getattr(self.passes[i], "output_layer", None)
            next_in = getattr(self.passes[i + 1], "input_layer", None)
            if current_out is not None and next_in is not None and current_out != next_in:
                errors.append(
                    f"Layer kind mismatch between pass {i} "
                    f"('{getattr(self.passes[i], 'pass_name', '?')}' outputs "
                    f"'{getattr(current_out, 'value', current_out)}') and pass {i + 1} "
                    f"('{getattr(self.passes[i + 1], 'pass_name', '?')}' expects "
                    f"'{getattr(next_in, 'value', next_in)}')."
                )
        return errors

    def pipeline_summary(self) -> str:
        """Return a human-readable description of the current pipeline.

        Returns:
            A multi-line string listing each pass in order with its input
            and output layer kinds.
        """
        lines: list[str] = [f"Pipeline [{self.composer_id[:8]}] — {len(self.passes)} pass(es):"]
        for i, lp in enumerate(self.passes):
            name = getattr(lp, "pass_name", "?")
            src = getattr(getattr(lp, "input_layer", None), "value", "?")
            dst = getattr(getattr(lp, "output_layer", None), "value", "?")
            preserved = getattr(lp, "ambiguity_preserved", True)
            lines.append(
                f"  [{i}] {name}  ({src} → {dst})  "
                f"[ambiguity_preserved={preserved}]"
            )
        return "\n".join(lines)

    def optimize_pipeline(self) -> list[Any]:
        """Remove redundant passes to produce a minimal pipeline.

        Two consecutive passes are considered *redundant* when they share
        the same ``pass_kind`` and the second pass is a pure identity
        transformation (``transformations`` list is empty).

        Returns:
            A new list of :class:`LoweringPass` objects with redundant
            entries removed.  The original :attr:`passes` list is not
            mutated.
        """
        if not self.passes:
            return []

        optimized: list[Any] = [self.passes[0]]
        for lp in self.passes[1:]:
            prev = optimized[-1]
            prev_kind = getattr(prev, "pass_kind", None)
            curr_kind = getattr(lp, "pass_kind", None)
            curr_transforms = getattr(lp, "transformations", None)
            if (
                prev_kind is not None
                and prev_kind == curr_kind
                and isinstance(curr_transforms, list)
                and len(curr_transforms) == 0
            ):
                # Skip this redundant pass
                self._composition_log.append(
                    {
                        "event": "optimized_out",
                        "pass_name": getattr(lp, "pass_name", None),
                        "reason": "redundant consecutive same-kind pass with empty transformations",
                        "timestamp": time.time(),
                    }
                )
                continue
            optimized.append(lp)

        return optimized


# ===================================================================== #
# 4. Rollback mechanism                                                  #
# ===================================================================== #


@dataclass
class _StackCheckpoint:
    """Internal snapshot of an :class:`IRStack` at a point in time.

    Attributes:
        checkpoint_id: Unique identifier for this checkpoint.
        stack_id: The ``stack_id`` of the original stack.
        layer_snapshots: Serialised JSON of each layer in the stack.
        timestamp: Unix timestamp when the checkpoint was created.
        metadata: Caller-supplied context (e.g., pass name, pipeline step).
    """

    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    stack_id: str = field(default="")
    layer_snapshots: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


def _serialise_layer(layer: Any) -> dict[str, Any]:
    """Return a JSON-safe dict snapshot of *layer*.

    Uses ``to_dict()`` when the method is available, otherwise constructs
    a minimal snapshot from the layer's ``__dict__``.
    """
    if hasattr(layer, "to_dict"):
        try:
            return layer.to_dict()
        except Exception:
            pass
    raw = getattr(layer, "__dict__", {})
    try:
        return json.loads(json.dumps(raw, default=str))
    except Exception:
        return {"layer_id": getattr(layer, "layer_id", None)}


# ===================================================================== #
# 5. Lowering from high-level IR to Z3-ready IR                         #
# ===================================================================== #


class LoweringPipeline:
    """Full pipeline for lowering an :class:`IRStack` from SURFACE to SOLVER_READY.

    :class:`LoweringPipeline` orchestrates the ordered application of all
    registered passes to an :class:`IRStack`, adds each newly lowered
    :class:`IRLayer` to the result stack, and validates the ambiguity
    preservation invariant throughout.  Checkpoints can be created before
    each major step to support rollback when a pass fails.

    Attributes:
        pipeline_id: Unique identifier for this pipeline run context.
        registry: The :class:`LoweringPassRegistry` supplying available passes.
        preservation_checker: The checker used to validate invariants.
        _execution_log: Accumulated log of all pipeline events.
        enable_rollback: When ``True``, checkpoints are created before each
            pass application so that :meth:`rollback` can undo bad passes.
    """

    def __init__(
        self,
        registry: LoweringPassRegistry,
        preservation_checker: AmbiguityPreservationChecker | None = None,
        enable_rollback: bool = True,
    ) -> None:
        self.pipeline_id: str = str(uuid.uuid4())
        self.registry: LoweringPassRegistry = registry
        self.preservation_checker: AmbiguityPreservationChecker = (
            preservation_checker or AmbiguityPreservationChecker()
        )
        self._execution_log: list[dict[str, Any]] = []
        self.enable_rollback: bool = enable_rollback

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def execute(self, stack: Any) -> Any:
        """Run the full pipeline on *stack*, adding lowered layers.

        The method resolves the topological pass order from the registry,
        applies each pass to the top layer of *stack*, and pushes the
        resulting layer onto the stack.  Checkpoints are created before
        each pass when :attr:`enable_rollback` is ``True``.

        Args:
            stack: The :class:`IRStack` to lower.

        Returns:
            The modified :class:`IRStack` with new lowered layers appended.
        """
        ordered_ids = self.registry.topological_order()
        ordered_passes = [
            self.registry.get(pid)
            for pid in ordered_ids
            if self.registry.get(pid) is not None
        ]

        self._execution_log.append(
            {
                "event": "pipeline_start",
                "pipeline_id": self.pipeline_id,
                "stack_id": getattr(stack, "stack_id", None),
                "pass_count": len(ordered_passes),
                "timestamp": time.time(),
            }
        )

        checkpoint: dict[str, Any] | None = None
        for lp in ordered_passes:
            if self.enable_rollback:
                checkpoint = self.create_checkpoint(stack)

            # Determine the input layer
            layers = getattr(stack, "layers", [])
            if not layers:
                self._execution_log.append(
                    {"event": "no_layers", "pass_name": getattr(lp, "pass_name", None)}
                )
                continue

            input_layer = layers[-1]
            try:
                output_layer = self.execute_pass(lp, input_layer)
            except Exception as exc:
                self._execution_log.append(
                    {
                        "event": "pass_exception",
                        "pass_name": getattr(lp, "pass_name", None),
                        "error": str(exc),
                        "timestamp": time.time(),
                    }
                )
                if self.enable_rollback and checkpoint is not None:
                    stack = self.rollback(stack, checkpoint)
                continue

            # Push the lowered layer onto the stack
            try:
                stack.push(output_layer)
            except Exception:
                layers_list = getattr(stack, "layers", [])
                layers_list.append(output_layer)

        self._execution_log.append(
            {
                "event": "pipeline_complete",
                "pipeline_id": self.pipeline_id,
                "violations": len(self.preservation_checker.violations),
                "timestamp": time.time(),
            }
        )
        return stack

    def execute_pass(self, lp: Any, layer: Any) -> Any:
        """Apply a single lowering pass to *layer* with logging and verification.

        Args:
            lp: The :class:`LoweringPass` to apply.
            layer: The :class:`IRLayer` to transform.

        Returns:
            The transformed :class:`IRLayer`.
        """
        step_start = time.time()
        try:
            result_layer = lp.apply(layer)
        except Exception as exc:
            self._execution_log.append(
                {
                    "event": "apply_error",
                    "pass_name": getattr(lp, "pass_name", None),
                    "error": str(exc),
                    "timestamp": time.time(),
                }
            )
            raise

        duration = time.time() - step_start
        ok = self.preservation_checker.check(
            layer, result_layer, getattr(lp, "pass_name", "unknown")
        )

        self._execution_log.append(
            {
                "event": "pass_applied",
                "pass_name": getattr(lp, "pass_name", None),
                "pass_id": getattr(lp, "pass_id", None),
                "duration_s": duration,
                "invariant_ok": ok,
                "timestamp": time.time(),
            }
        )
        return result_layer

    # ------------------------------------------------------------------
    # Checkpoint and rollback
    # ------------------------------------------------------------------

    def create_checkpoint(self, stack: Any) -> dict[str, Any]:
        """Capture the current state of *stack* as a checkpoint dict.

        Args:
            stack: The :class:`IRStack` to snapshot.

        Returns:
            A dict that can later be passed to :meth:`rollback`.
        """
        layers = getattr(stack, "layers", [])
        snapshots = [_serialise_layer(layer) for layer in layers]
        cp = _StackCheckpoint(
            stack_id=getattr(stack, "stack_id", ""),
            layer_snapshots=snapshots,
            metadata={
                "pipeline_id": self.pipeline_id,
                "log_length_at_checkpoint": len(self._execution_log),
            },
        )
        return {
            "checkpoint_id": cp.checkpoint_id,
            "stack_id": cp.stack_id,
            "layer_count": len(snapshots),
            "layer_snapshots": cp.layer_snapshots,
            "timestamp": cp.timestamp,
            "metadata": cp.metadata,
        }

    def rollback(self, stack: Any, checkpoint: dict[str, Any]) -> Any:
        """Restore *stack* to the state captured in *checkpoint*.

        The method truncates the stack's layer list to the number of layers
        that were present when the checkpoint was created.

        Args:
            stack: The :class:`IRStack` to roll back (mutated in place).
            checkpoint: A dict previously returned by :meth:`create_checkpoint`.

        Returns:
            The rolled-back :class:`IRStack`.
        """
        target_count = checkpoint.get("layer_count", 0)
        layers = getattr(stack, "layers", [])
        while len(layers) > target_count:
            layers.pop()

        self._execution_log.append(
            {
                "event": "rollback",
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "restored_layer_count": len(layers),
                "timestamp": time.time(),
            }
        )
        return stack

    # ------------------------------------------------------------------
    # Verification and reporting
    # ------------------------------------------------------------------

    def verify_result(self, original: Any, lowered: Any) -> list[str]:
        """Validate that lowering was faithful to the original stack.

        Checks that:
        1. The lowered stack has at least as many layers as the original.
        2. All ambiguity marks from the original top layer are present in
           the lowered top layer.
        3. No node IDs from the original layers were silently dropped.

        Args:
            original: The :class:`IRStack` before lowering.
            lowered: The :class:`IRStack` after lowering.

        Returns:
            A list of error strings, empty when the result is valid.
        """
        errors: list[str] = []

        orig_layers = getattr(original, "layers", [])
        low_layers = getattr(lowered, "layers", [])

        if len(low_layers) < len(orig_layers):
            errors.append(
                f"Lowered stack has fewer layers ({len(low_layers)}) than "
                f"original ({len(orig_layers)})."
            )

        if orig_layers and low_layers:
            orig_top = orig_layers[-1]
            low_top = low_layers[-1]
            orig_marks = self.preservation_checker.collect_ambiguity_marks(orig_top)
            low_marks = self.preservation_checker.collect_ambiguity_marks(low_top)
            dropped = set(orig_marks.keys()) - set(low_marks.keys())
            for nid in dropped:
                errors.append(
                    f"Ambiguity mark for node '{nid}' was dropped during lowering."
                )

        # Check no original node IDs were silently dropped
        for orig_layer in orig_layers:
            orig_node_ids = set(getattr(orig_layer, "nodes", {}).keys())
            if not orig_node_ids:
                continue
            found_in_lowered = False
            for low_layer in low_layers:
                low_node_ids = set(getattr(low_layer, "nodes", {}).keys())
                if orig_node_ids.issubset(low_node_ids):
                    found_in_lowered = True
                    break
            if not found_in_lowered:
                errors.append(
                    f"Layer '{getattr(orig_layer, 'layer_id', '?')}': not all original "
                    f"node IDs are present in any lowered layer."
                )

        return errors

    def execution_report(self) -> dict[str, Any]:
        """Produce a full summary report of the pipeline execution.

        Returns:
            A dict with keys:
            * ``pipeline_id`` — this pipeline's ID.
            * ``total_events`` — number of log entries.
            * ``passes_applied`` — count of successful pass applications.
            * ``pass_errors`` — count of errors during application.
            * ``rollbacks`` — count of rollback events.
            * ``invariant_violations`` — violation count from checker.
            * ``log`` — the full execution log.
        """
        passes_applied = sum(
            1 for e in self._execution_log if e.get("event") == "pass_applied"
        )
        pass_errors = sum(
            1 for e in self._execution_log if e.get("event") in ("pass_error", "apply_error", "pass_exception")
        )
        rollbacks = sum(
            1 for e in self._execution_log if e.get("event") == "rollback"
        )
        return {
            "pipeline_id": self.pipeline_id,
            "total_events": len(self._execution_log),
            "passes_applied": passes_applied,
            "pass_errors": pass_errors,
            "rollbacks": rollbacks,
            "invariant_violations": len(self.preservation_checker.violations),
            "log": list(self._execution_log),
        }


# ===================================================================== #
# 6. Copilot-assisted lowering hints                                     #
# ===================================================================== #


class CopilotLoweringHint:
    """Copilot-assisted hints for optimising the lowering pipeline.

    This class records suggestions produced by the copilot oracle regarding
    pass ordering and disambiguation and tracks how often those suggestions
    are accepted by the system.

    Attributes:
        hint_id: Unique identifier for this hint session.
        session_id: External session context (e.g., a Copilot session ID).
        hints: Accumulated list of all generated hints.
        _accepted_hints: Count of hints marked as accepted.
        _rejected_hints: Count of hints marked as rejected.
        confidence_threshold: Minimum confidence score for a hint to be
            included in suggestions.
    """

    def __init__(
        self,
        session_id: str | None = None,
        confidence_threshold: float = 0.7,
    ) -> None:
        self.hint_id: str = str(uuid.uuid4())
        self.session_id: str = session_id or str(uuid.uuid4())
        self.hints: list[dict[str, Any]] = []
        self._accepted_hints: int = 0
        self._rejected_hints: int = 0
        self.confidence_threshold: float = confidence_threshold

    # ------------------------------------------------------------------
    # Suggestion generation  # copilot
    # ------------------------------------------------------------------

    def suggest_pass_order(self, stack: Any) -> list[str]:
        """Suggest an optimal pass ordering for *stack*.

        # copilot: analyses the layer kinds present in the stack and
        recommends an ordering that minimises ambiguity violations.

        The suggestion is based on the depth ordering of the layers:
        passes targeting shallower layer kinds (SURFACE, SEMANTIC) are
        recommended before deeper ones (LOGICAL, SOLVER_READY).

        Args:
            stack: The :class:`IRStack` to analyse.

        Returns:
            A list of pass name strings in the suggested execution order.
        """
        layers = getattr(stack, "layers", [])
        kind_depths: dict[str, int] = {}
        for layer in layers:
            lk = getattr(layer, "layer_kind", None)
            if lk is not None:
                kind_val = getattr(lk, "value", str(lk))
                depth = getattr(lk, "depth_hint", lambda: 0)()
                kind_depths[kind_val] = depth

        # Map layer kinds to standard pass names
        _kind_to_pass: dict[str, str] = {
            "surface": "desugar",
            "semantic": "erase_types",
            "logical": "extract_obligations",
            "solver_ready": "normalize_constraints",
        }
        order: list[str] = []
        for kind_val, _ in sorted(kind_depths.items(), key=lambda kv: kv[1]):
            pass_name = _kind_to_pass.get(kind_val)
            if pass_name and pass_name not in order:
                order.append(pass_name)

        # Always include Z3 encoding at the end
        if "encode_for_z3" not in order:
            order.append("encode_for_z3")

        hint_entry: dict[str, Any] = {
            "hint_id": str(uuid.uuid4()),
            "kind": "pass_order",
            "suggestion": order,
            "confidence": 0.85,
            "timestamp": time.time(),
        }
        self.hints.append(hint_entry)
        return order

    def suggest_disambiguation(self, mark: Any, context: dict[str, Any]) -> list[str]:
        """Suggest resolution candidates for an :class:`AmbiguityMark`.

        # copilot: inspects the mark's kind and context to produce an
        ordered list of candidate resolution strategies.

        Args:
            mark: An :class:`AmbiguityMark` whose ambiguity needs resolution.
            context: A dict providing surrounding context (e.g., parent
                node kind, current layer kind, trust level).

        Returns:
            A list of candidate resolution strings in priority order.
        """
        candidates: list[str] = []
        mark_kind = getattr(getattr(mark, "mark_kind", None), "value", "structural")
        confidence = getattr(mark, "confidence", 0.5)

        if mark_kind == "structural":
            candidates = ["expand_sugar", "beta_reduce", "eta_expand"]
        elif mark_kind == "semantic":
            candidates = ["type_unify", "overload_resolve", "coerce"]
        elif mark_kind == "resolution_pending":
            candidates = ["defer_to_solver", "oracle_consult", "human_review"]
        elif mark_kind == "definitional":
            candidates = ["unfold_definition", "inline_let", "delta_reduce"]
        elif mark_kind == "overloaded":
            candidates = ["select_instance", "monomorphise", "type_direct"]
        else:
            candidates = ["inspect_manually"]

        # Prune low-confidence existing candidates
        existing = getattr(mark, "resolution_candidates", {})
        for node_id, node_candidates in existing.items():
            for c in node_candidates:
                if c not in candidates:
                    candidates.append(c)

        # Filter by context trust level if available
        trust = context.get("trust_level", 1)
        if trust < 1 and "oracle_consult" in candidates:
            candidates.remove("oracle_consult")

        # Only return if above threshold
        if confidence >= self.confidence_threshold:
            hint_entry: dict[str, Any] = {
                "hint_id": str(uuid.uuid4()),
                "kind": "disambiguation",
                "mark_kind": mark_kind,
                "candidates": candidates,
                "confidence": confidence,
                "timestamp": time.time(),
            }
            self.hints.append(hint_entry)

        return candidates

    def explain_lowering(self, lp: Any) -> str:
        """Produce a natural-language explanation of a lowering pass.

        # copilot: translates the machine-readable pass description into
        prose suitable for developer documentation and audit logs.

        Args:
            lp: A :class:`LoweringPass` to explain.

        Returns:
            A multi-sentence string describing what the pass does.
        """
        name = getattr(lp, "pass_name", "unnamed")
        src = getattr(getattr(lp, "input_layer", None), "value", "unknown")
        dst = getattr(getattr(lp, "output_layer", None), "value", "unknown")
        n_transforms = len(getattr(lp, "transformations", []))
        preserves = getattr(lp, "ambiguity_preserved", True)

        explanation = (
            f"Pass '{name}' transforms IR layers from the '{src}' stratum to "
            f"the '{dst}' stratum.  It applies {n_transforms} transformation "
            f"rule(s) to each node in the input layer.  "
        )
        if preserves:
            explanation += (
                "This pass is certified to preserve all ambiguity marks: every "
                "mark present in the input layer will be present in the output."
            )
        else:
            explanation += (
                "WARNING: This pass does NOT guarantee preservation of ambiguity "
                "marks.  Callers should invoke AmbiguityPreservationChecker after "
                "application."
            )

        hint_entry: dict[str, Any] = {
            "hint_id": str(uuid.uuid4()),
            "kind": "explanation",
            "pass_name": name,
            "text": explanation,
            "timestamp": time.time(),
        }
        self.hints.append(hint_entry)
        return explanation

    # ------------------------------------------------------------------
    # Outcome tracking
    # ------------------------------------------------------------------

    def record_outcome(self, hint_id: str, accepted: bool) -> None:
        """Record whether a suggestion with *hint_id* was accepted.

        Args:
            hint_id: The ``hint_id`` key from a hint dict in :attr:`hints`.
            accepted: ``True`` when the hint was acted upon.
        """
        if accepted:
            self._accepted_hints += 1
        else:
            self._rejected_hints += 1

        for hint in self.hints:
            if hint.get("hint_id") == hint_id:
                hint["outcome"] = "accepted" if accepted else "rejected"
                hint["outcome_recorded_at"] = time.time()
                break

    def statistics(self) -> dict[str, Any]:
        """Return acceptance rate and summary statistics.

        Returns:
            A dict with keys ``total_hints``, ``accepted``, ``rejected``,
            ``acceptance_rate``, and ``by_kind``.
        """
        total = self._accepted_hints + self._rejected_hints
        rate = (self._accepted_hints / total) if total > 0 else 0.0
        by_kind: dict[str, int] = collections.defaultdict(int)
        for hint in self.hints:
            by_kind[hint.get("kind", "unknown")] += 1
        return {
            "total_hints": len(self.hints),
            "accepted": self._accepted_hints,
            "rejected": self._rejected_hints,
            "acceptance_rate": rate,
            "by_kind": dict(by_kind),
        }


# ===================================================================== #
# 7. Standard lowering passes                                            #
# ===================================================================== #


class StandardLoweringPasses:
    """Factory for the five canonical lowering passes described in §32.4.

    Each class method constructs a :class:`LoweringPass` pre-configured for
    one of the standard transformations.  The transformations list for each
    pass carries a single descriptive dict that downstream encoders can
    interpret.
    """

    # ------------------------------------------------------------------
    @classmethod
    def desugar(cls) -> Any:
        """Create a desugaring pass from SURFACE to SEMANTIC.

        Desugaring expands syntactic sugar in the surface IR into its
        explicit semantic equivalents.  All ambiguity marks are preserved
        because desugaring is a purely structural expansion.

        Returns:
            A :class:`LoweringPass` configured for desugaring.
        """
        try:
            return LoweringPass(  # type: ignore[call-arg]
                pass_id=str(uuid.uuid4()),
                pass_name="desugar",
                input_layer=IRLayerKind.SURFACE,  # type: ignore[name-defined]
                output_layer=IRLayerKind.SEMANTIC,  # type: ignore[name-defined]
                transformations=[
                    {
                        "rule": "expand_let_bindings",
                        "scope": "all_nodes",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "expand_pattern_match",
                        "scope": "statement_nodes",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "expand_do_notation",
                        "scope": "expression_nodes",
                        "preserves_ambiguity": True,
                    },
                ],
                ambiguity_preserved=True,
            )
        except Exception:
            return None

    @classmethod
    def erase_types(cls) -> Any:
        """Create a type-erasure pass from SEMANTIC to LOGICAL.

        Type erasure removes type annotations and type-level terms from
        the semantic layer, producing a purely logical representation.
        Ambiguity marks on typed nodes are migrated to the erased nodes.

        Returns:
            A :class:`LoweringPass` configured for type erasure.
        """
        try:
            return LoweringPass(  # type: ignore[call-arg]
                pass_id=str(uuid.uuid4()),
                pass_name="erase_types",
                input_layer=IRLayerKind.SEMANTIC,  # type: ignore[name-defined]
                output_layer=IRLayerKind.LOGICAL,  # type: ignore[name-defined]
                transformations=[
                    {
                        "rule": "remove_type_annotations",
                        "scope": "type_term_nodes",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "migrate_ambiguity_marks",
                        "scope": "all_nodes",
                        "preserves_ambiguity": True,
                    },
                ],
                ambiguity_preserved=True,
            )
        except Exception:
            return None

    @classmethod
    def extract_obligations(cls) -> Any:
        """Create an obligation-extraction pass from LOGICAL to LOGICAL.

        This pass scans the logical IR for quantifier and constraint nodes
        and lifts them into the explicit ``constraints`` list of the layer,
        where they become first-class proof obligations.

        Returns:
            A :class:`LoweringPass` configured for obligation extraction.
        """
        try:
            return LoweringPass(  # type: ignore[call-arg]
                pass_id=str(uuid.uuid4()),
                pass_name="extract_obligations",
                input_layer=IRLayerKind.LOGICAL,  # type: ignore[name-defined]
                output_layer=IRLayerKind.LOGICAL,  # type: ignore[name-defined]
                transformations=[
                    {
                        "rule": "lift_quantifiers_to_constraints",
                        "scope": "quantifier_nodes",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "lift_obligation_nodes",
                        "scope": "obligation_nodes",
                        "preserves_ambiguity": True,
                    },
                ],
                ambiguity_preserved=True,
            )
        except Exception:
            return None

    @classmethod
    def normalize_constraints(cls) -> Any:
        """Create a constraint-normalisation pass from LOGICAL to SOLVER_READY.

        Normalisation rewrites constraints into a canonical form accepted
        by the Z3 solver interface, including flattening nested conjunctions
        and sorting quantifier prefixes.

        Returns:
            A :class:`LoweringPass` configured for constraint normalisation.
        """
        try:
            return LoweringPass(  # type: ignore[call-arg]
                pass_id=str(uuid.uuid4()),
                pass_name="normalize_constraints",
                input_layer=IRLayerKind.LOGICAL,  # type: ignore[name-defined]
                output_layer=IRLayerKind.SOLVER_READY,  # type: ignore[name-defined]
                transformations=[
                    {
                        "rule": "flatten_conjunctions",
                        "scope": "constraint_list",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "sort_quantifier_prefix",
                        "scope": "quantifier_nodes",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "skolemise_existentials",
                        "scope": "quantifier_nodes",
                        "preserves_ambiguity": True,
                    },
                ],
                ambiguity_preserved=True,
            )
        except Exception:
            return None

    @classmethod
    def encode_for_z3(cls) -> Any:
        """Create a Z3-encoding pass from SOLVER_READY to SOLVER_READY.

        This pass translates solver-ready constraints into Z3-compatible
        formula representations, ready for direct consumption by the
        :class:`Z3Session`.

        Returns:
            A :class:`LoweringPass` configured for Z3 encoding.
        """
        try:
            return LoweringPass(  # type: ignore[call-arg]
                pass_id=str(uuid.uuid4()),
                pass_name="encode_for_z3",
                input_layer=IRLayerKind.SOLVER_READY,  # type: ignore[name-defined]
                output_layer=IRLayerKind.SOLVER_READY,  # type: ignore[name-defined]
                transformations=[
                    {
                        "rule": "emit_z3_bool_sort",
                        "scope": "boolean_nodes",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "emit_z3_int_sort",
                        "scope": "integer_nodes",
                        "preserves_ambiguity": True,
                    },
                    {
                        "rule": "emit_z3_quantifier",
                        "scope": "quantifier_nodes",
                        "preserves_ambiguity": True,
                    },
                ],
                ambiguity_preserved=True,
            )
        except Exception:
            return None


# ===================================================================== #
# 8. Module-level helpers                                                #
# ===================================================================== #


def lower_layer(layer: Any, target_kind: Any, registry: LoweringPassRegistry) -> Any:
    """Lower a single :class:`IRLayer` to *target_kind* using *registry*.

    Resolves the shortest pipeline from the layer's current kind to
    *target_kind* and applies each pass in order.  Returns the original
    layer unchanged when no path is found.

    Args:
        layer: The :class:`IRLayer` to lower.
        target_kind: The desired :class:`IRLayerKind` for the output.
        registry: A :class:`LoweringPassRegistry` to query for passes.

    Returns:
        The lowered :class:`IRLayer`, or the original layer when no
        applicable passes are found.
    """
    current_kind = getattr(layer, "layer_kind", None)
    if current_kind is None or current_kind == target_kind:
        return layer

    pipeline = registry.get_pipeline(current_kind, target_kind)
    if not pipeline:
        return layer

    checker = AmbiguityPreservationChecker()
    current = layer
    for lp in pipeline:
        try:
            result = lp.apply(current)
            checker.check(current, result, getattr(lp, "pass_name", "?"))
            current = result
        except Exception:
            # Abort on error; return what we have so far
            break

    return current


def lower_stack(stack: Any) -> Any:
    """Lower all layers in *stack* to ``SOLVER_READY`` using standard passes.

    Builds a :class:`LoweringPassRegistry` from :class:`StandardLoweringPasses`,
    constructs a :class:`LoweringPipeline`, and runs it against *stack*.

    Args:
        stack: The :class:`IRStack` to lower.

    Returns:
        The lowered :class:`IRStack`.
    """
    pipeline = create_standard_pipeline()
    return pipeline.execute(stack)


def verify_lowering_fidelity(original: Any, lowered: Any) -> bool:
    """Check whether *lowered* is a faithful lowering of *original*.

    Creates a temporary :class:`LoweringPipeline` solely for its
    :meth:`~LoweringPipeline.verify_result` helper and returns ``True``
    when no errors are detected.

    Args:
        original: The :class:`IRStack` before lowering.
        lowered: The :class:`IRStack` after lowering.

    Returns:
        ``True`` when no fidelity errors are found; ``False`` otherwise.
    """
    registry = LoweringPassRegistry()
    pipeline = LoweringPipeline(registry=registry, enable_rollback=False)
    errors = pipeline.verify_result(original, lowered)
    return len(errors) == 0


def create_standard_pipeline() -> LoweringPipeline:
    """Build and return a fully configured standard lowering pipeline.

    Registers the five canonical passes from :class:`StandardLoweringPasses`
    in dependency order, wraps them in a :class:`LoweringPipeline`, and
    returns it ready for use.

    Returns:
        A :class:`LoweringPipeline` pre-loaded with all standard passes.
    """
    registry = LoweringPassRegistry()

    factory_methods = [
        StandardLoweringPasses.desugar,
        StandardLoweringPasses.erase_types,
        StandardLoweringPasses.extract_obligations,
        StandardLoweringPasses.normalize_constraints,
        StandardLoweringPasses.encode_for_z3,
    ]

    pass_ids: list[str] = []
    for factory in factory_methods:
        lp = factory()
        if lp is not None:
            registry.register(lp)
            pass_ids.append(lp.pass_id)

    # Declare linear dependency chain so topological_order produces the
    # correct sequence: desugar → erase_types → extract_obligations →
    # normalize_constraints → encode_for_z3
    for i in range(1, len(pass_ids)):
        registry.add_dependency(pass_ids[i], pass_ids[i - 1])

    checker = AmbiguityPreservationChecker()
    pipeline = LoweringPipeline(
        registry=registry,
        preservation_checker=checker,
        enable_rollback=True,
    )
    return pipeline
