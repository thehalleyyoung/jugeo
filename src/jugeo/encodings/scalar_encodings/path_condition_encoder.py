r"""PathConditionEncoder — encodes branching/join path conditions.

This module implements **Chapter 26.5** path-condition encoding for the JuGeo
SMT-based verification pipeline.  Path conditions arise whenever the solver
must reason about which branches of a control-flow graph are reachable under a
given set of hypotheses.

Mathematical basis
------------------
A *path condition* for a control-flow path ``π = b₀ → b₁ → … → bₙ`` is the
conjunction of the branch guards encountered along that path::

    PC(π) = guard(b₀) ∧ guard(b₁) ∧ … ∧ guard(bₙ)

A *join condition* is the disjunction of the path conditions for all paths
leading to a join node::

    JC(join) = ⋁ { PC(π) | π is a path to join }

The encoding preserves *feasibility*: if ``PC(π)`` is satisfiable then the
corresponding SMT2 formula produced by this module is also satisfiable.  It
also preserves *join soundness*: ``JC(join)`` is satisfiable iff at least one
path to the join node is feasible.

Architecture
------------
The module is structured as a four-layer pipeline:

1. **BranchNode** — an immutable value object representing a single branch
   point in a control-flow graph, carrying the SMT2 guard formula, depth, and
   a reference to the parent node.

2. **PathTree** — a mutable tree that accumulates :class:`BranchNode` objects
   and provides structural queries (root-to-leaf paths, leaf enumeration,
   infeasibility pruning).

3. **JoinConditionSynthesizer** — stateless (except for a synthesis log)
   utility that takes lists of :class:`PathCondition` objects and combines
   them into join conditions, applying weakening and redundancy elimination.

4. **PathConditionEncoder** — the public entry point.  It drives the
   synthesizer, maintains an encoding cache and statistical counters, and
   exposes high-level helpers for branch, join, sequential, and loop-invariant
   encoding.

Key invariants
--------------
* **Feasibility preservation**: ``PathConditionEncoder.feasibility_check``
  returns ``False`` only when the path condition contains a literal ``false``
  guard or a known-unsatisfiable consequent.
* **Join soundness**: ``JoinConditionSynthesizer.synthesize_join`` produces a
  disjunction over all supplied path conditions, so the result is satisfiable
  iff at least one input is satisfiable.
* **Cycle safety**: :meth:`BranchNode.path_to_root` raises :class:`ValueError`
  if a cycle is detected in the parent-id chain (limit: 1000 steps).

copilot: This module is a primary target for LLM-assisted path-condition
synthesis.  When Copilot suggests a guard formula it should be wrapped by
:func:`encode_simple_branch` before being passed to the rest of the pipeline.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterator

from jugeo.geometry.supports import SupportRegion, SupportSet
from jugeo.solver.fragments import Fragment, LogicalFragment, SolverFragment, classify_fragment
from jugeo.solver.z3_session import (
    Z3Formula,
    Z3QueryBuilder,
    Z3Result,
    Z3Session,
    SolveOutcome,
    SolverResult,
    Z3Encoder,
    Z3Decoder,
)
from jugeo.encodings.scalar_encodings.models import (
    SortKind,
    FragmentHint,
    PathCondition,
    GuardFormula,
    EncodingContext,
    EncodingResult,
    make_context_id,
)

logger = logging.getLogger(__name__)

# ============================== constants ==============================

_CYCLE_GUARD = 1_000
"""Maximum parent-chain steps before BranchNode.path_to_root raises ValueError."""

_FALSE_LITERALS: frozenset[str] = frozenset({"false", "False", "(= true false)", "(not true)"})
"""SMT2 expressions recognised as unconditionally infeasible guards."""

_INT_ARITH_PATTERN = re.compile(
    r"\b(div|mod|rem|\+|-|\*|<=|>=|<|>|=)\b.*\bInt\b"
    r"|\b[0-9]+\b.*\b(div|mod|\+|\*)",
    re.IGNORECASE,
)
_REAL_ARITH_PATTERN = re.compile(r"\bReal\b|\b[0-9]+\.[0-9]+\b", re.IGNORECASE)
_BITVEC_PATTERN = re.compile(r"\bBitVec\b|bv[0-9]+|#x[0-9a-fA-F]+|#b[01]+", re.IGNORECASE)


# ============================== BranchNode ==============================


@dataclass(frozen=True)
class BranchNode:
    """Immutable node in a control-flow branch tree (§26.5.1).

    Each :class:`BranchNode` represents a single decision point in the
    program's control-flow graph.  The ``guard_smt`` field holds the SMT2
    expression that must hold for the outgoing edge to be taken.

    Parameters
    ----------
    node_id:
        Globally unique identifier for this node (typically a UUID hex slug).
    label:
        Human-readable name, e.g. ``"if_then_42"`` or ``"loop_header"``.
    guard_smt:
        SMT-LIB 2 boolean expression that guards this branch.
    parent_id:
        ``node_id`` of the parent :class:`BranchNode`, or ``None`` for the
        root.
    depth:
        Nesting depth from the root (root is depth 0).

    copilot: BranchNode is immutable; create new nodes rather than mutating.
    """

    node_id: str
    label: str
    guard_smt: str
    parent_id: str | None
    depth: int

    # ------------------------------------------------------------------ queries

    def is_root(self) -> bool:
        """Return ``True`` iff this node has no parent (is the tree root)."""
        return self.parent_id is None

    def is_leaf(self, children: list[BranchNode]) -> bool:
        """Return ``True`` iff no node in *children* claims this node as parent.

        Parameters
        ----------
        children:
            The full list of nodes in the tree that could be children of any
            node.  The method performs a linear scan, so callers that call this
            in a loop should precompute a children map instead.
        """
        return not any(c.parent_id == self.node_id for c in children)

    def path_to_root(self, nodes_map: dict[str, BranchNode]) -> list[BranchNode]:
        """Return the path from this node up to the root (inclusive).

        The returned list begins at ``self`` and ends at the root.  If the
        parent-id chain forms a cycle the method raises :class:`ValueError`
        after at most :data:`_CYCLE_GUARD` steps.

        Parameters
        ----------
        nodes_map:
            Dictionary mapping ``node_id`` to :class:`BranchNode` for every
            node in the tree.

        Raises
        ------
        ValueError
            If a cycle is detected in the parent-id chain.
        """
        path: list[BranchNode] = [self]
        current = self
        for _ in range(_CYCLE_GUARD):
            if current.parent_id is None:
                break
            parent = nodes_map.get(current.parent_id)
            if parent is None:
                logger.warning(
                    "path_to_root: dangling parent_id %r on node %r — stopping",
                    current.parent_id,
                    current.node_id,
                )
                break
            path.append(parent)
            current = parent
        else:
            raise ValueError(
                f"Cycle detected in BranchNode parent chain starting at {self.node_id!r}; "
                f"exceeded {_CYCLE_GUARD} steps."
            )
        return path

    def depth_label(self) -> str:
        """Return a coarse depth label for diagnostic messages.

        Returns ``"root"`` for depth 0, ``"leaf"`` for depth > 5, and
        ``"d{depth}"`` otherwise.
        """
        if self.depth == 0:
            return "root"
        if self.depth > 5:
            return "leaf"
        return f"d{self.depth}"

    def guard_complexity(self) -> int:
        """Return a proxy for the syntactic complexity of the guard formula.

        The metric is simply the count of ``(`` characters in the SMT2 string,
        which correlates with the nesting depth of the s-expression.
        """
        return self.guard_smt.count("(")


# ============================== PathTree ==============================


class PathTree:
    """Mutable control-flow branch tree for path-condition analysis (§26.5.2).

    A :class:`PathTree` accumulates :class:`BranchNode` objects and maintains
    parent→children edges so that structural queries — paths, leaves, Mermaid
    diagrams — are efficient.

    Typical usage::

        tree = PathTree()
        root = BranchNode("n0", "entry", "true", None, 0)
        tree.add_node(root)
        left  = BranchNode("n1", "left",  "(< x 0)", "n0", 1)
        right = BranchNode("n2", "right", "(>= x 0)", "n0", 1)
        tree.add_node(left)
        tree.add_node(right)
        paths = tree.join_paths(["n1", "n2"])
    """

    def __init__(self) -> None:
        self._nodes: dict[str, BranchNode] = {}
        self._children: dict[str, list[str]] = {}  # parent_id -> [child_id, ...]
        self._root_id: str | None = None

    # ------------------------------------------------------------------ mutation

    def add_node(self, node: BranchNode) -> None:
        """Insert *node* into the tree, updating internal indices.

        If *node* is a root (``parent_id is None``) and no root has been
        registered yet, it becomes the tree root.  Subsequent roots log a
        warning and are inserted as disconnected nodes.
        """
        if node.node_id in self._nodes:
            logger.debug("add_node: node %r already present — skipping", node.node_id)
            return

        self._nodes[node.node_id] = node

        # Ensure every node has a children-list entry.
        if node.node_id not in self._children:
            self._children[node.node_id] = []

        if node.parent_id is not None:
            if node.parent_id not in self._children:
                self._children[node.parent_id] = []
            self._children[node.parent_id].append(node.node_id)
        else:
            if self._root_id is None:
                self._root_id = node.node_id
            else:
                logger.warning(
                    "add_node: second root node %r registered (existing root: %r)",
                    node.node_id,
                    self._root_id,
                )

    def add_child(self, parent_id: str, child: BranchNode) -> None:
        """Add *child* as a direct child of *parent_id*.

        The child's ``parent_id`` field is replaced with *parent_id* so that
        the internal bookkeeping is always consistent, even if the caller
        supplied a node with a different (or absent) parent reference.

        Parameters
        ----------
        parent_id:
            The ``node_id`` of the existing parent node.
        child:
            The new child node.  Its ``parent_id`` will be overwritten.

        Raises
        ------
        KeyError
            If *parent_id* is not already in the tree.
        """
        if parent_id not in self._nodes:
            raise KeyError(
                f"add_child: parent {parent_id!r} not found in tree — add the parent first."
            )
        # Rebuild the frozen dataclass with the correct parent_id.
        adjusted = BranchNode(
            node_id=child.node_id,
            label=child.label,
            guard_smt=child.guard_smt,
            parent_id=parent_id,
            depth=self._nodes[parent_id].depth + 1,
        )
        self.add_node(adjusted)

    # ------------------------------------------------------------------ queries

    def get_path(self, leaf_id: str) -> list[BranchNode]:
        """Return the path from the root to *leaf_id* (inclusive).

        If *leaf_id* is not in the tree, returns an empty list.  The path is
        obtained by calling :meth:`BranchNode.path_to_root` on the leaf node
        and then reversing the result.
        """
        node = self._nodes.get(leaf_id)
        if node is None:
            logger.debug("get_path: node %r not found", leaf_id)
            return []
        try:
            reversed_path = node.path_to_root(self._nodes)
        except ValueError:
            logger.error("get_path: cycle detected for node %r — returning empty path", leaf_id)
            return []
        return list(reversed(reversed_path))

    def all_leaves(self) -> list[BranchNode]:
        """Return every node that has no registered children.

        A node is a leaf iff its entry in :attr:`_children` is an empty list.
        """
        return [
            node
            for node_id, node in self._nodes.items()
            if not self._children.get(node_id)
        ]

    def join_paths(self, leaf_ids: list[str]) -> PathCondition:
        """Compute the join path condition for a set of leaf nodes.

        For each leaf, the path condition is the conjunction of all guard SMT
        expressions along the root-to-leaf path.  The join condition is then
        the disjunction of those conjunctions, producing a :class:`PathCondition`
        with ``is_join=True``.

        Parameters
        ----------
        leaf_ids:
            Identifiers of the leaves whose paths should be joined.

        Returns
        -------
        PathCondition
            A path condition whose ``consequent`` is an SMT2 ``(or ...)``
            expression.
        """
        per_path_conjuncts: list[str] = []
        max_depth = 0
        all_antecedents: list[str] = []

        for leaf_id in leaf_ids:
            path = self.get_path(leaf_id)
            if not path:
                continue
            guards = [n.guard_smt for n in path if n.guard_smt not in ("true", "True")]
            if guards:
                if len(guards) == 1:
                    per_path_conjuncts.append(guards[0])
                else:
                    per_path_conjuncts.append(f"(and {' '.join(guards)})")
            else:
                per_path_conjuncts.append("true")
            all_antecedents.extend(guards)
            max_depth = max(max_depth, path[-1].depth if path else 0)

        if not per_path_conjuncts:
            consequent = "false"
        elif len(per_path_conjuncts) == 1:
            consequent = per_path_conjuncts[0]
        else:
            consequent = f"(or {' '.join(per_path_conjuncts)})"

        # Deduplicate antecedents preserving order.
        seen: set[str] = set()
        unique_antecedents: list[str] = []
        for ant in all_antecedents:
            if ant not in seen:
                seen.add(ant)
                unique_antecedents.append(ant)

        return PathCondition(
            condition_id=f"join_{uuid.uuid4().hex[:8]}",
            branch_label=f"join({','.join(leaf_ids[:3])}{',...' if len(leaf_ids) > 3 else ''})",
            antecedents=tuple(unique_antecedents),
            consequent=consequent,
            depth=max_depth,
            is_join=True,
            fragment=FragmentHint.QF_BOOL,
        )

    def prune_infeasible(self, session_hint: str = "") -> int:
        """Remove nodes whose guard is trivially ``false`` and return the count.

        A node is pruned if its ``guard_smt`` value is in
        :data:`_FALSE_LITERALS`.  The method also removes all descendants of
        pruned nodes, since their paths are unreachable.

        Parameters
        ----------
        session_hint:
            Optional label included in log messages for correlation.

        Returns
        -------
        int
            Number of nodes removed.
        """
        tag = f"[{session_hint}] " if session_hint else ""

        # Identify directly infeasible nodes.
        infeasible_ids: set[str] = {
            nid for nid, node in self._nodes.items() if node.guard_smt in _FALSE_LITERALS
        }

        # Transitively include descendants of infeasible nodes.
        to_visit = list(infeasible_ids)
        while to_visit:
            current = to_visit.pop()
            for child_id in list(self._children.get(current, [])):
                if child_id not in infeasible_ids:
                    infeasible_ids.add(child_id)
                    to_visit.append(child_id)

        for nid in infeasible_ids:
            logger.debug("%sprune_infeasible: removing node %r", tag, nid)
            self._nodes.pop(nid, None)
            self._children.pop(nid, None)

        # Remove references to pruned nodes from parent children lists.
        for parent_id, child_list in self._children.items():
            self._children[parent_id] = [c for c in child_list if c not in infeasible_ids]

        if self._root_id in infeasible_ids:
            self._root_id = None

        count = len(infeasible_ids)
        if count:
            logger.info("%sprune_infeasible: pruned %d node(s)", tag, count)
        return count

    def to_mermaid(self) -> str:
        """Return a Mermaid ``graph TD`` diagram of the tree.

        Each node is represented as ``NodeId[label (depth_label)]`` and edges
        are ``ParentId --> ChildId``.  The diagram is suitable for embedding in
        Markdown documentation.
        """
        lines: list[str] = ["graph TD"]
        for node_id, node in sorted(self._nodes.items()):
            safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", node_id)
            safe_label = node.label.replace('"', "'")
            lines.append(f'  {safe_id}["{safe_label} ({node.depth_label()})"]')
        for parent_id, child_ids in sorted(self._children.items()):
            safe_parent = re.sub(r"[^a-zA-Z0-9_]", "_", parent_id)
            for child_id in sorted(child_ids):
                safe_child = re.sub(r"[^a-zA-Z0-9_]", "_", child_id)
                lines.append(f"  {safe_parent} --> {safe_child}")
        return "\n".join(lines)

    def node_count(self) -> int:
        """Return the total number of nodes in the tree."""
        return len(self._nodes)


# ============================== JoinConditionSynthesizer ==============================


class JoinConditionSynthesizer:
    """Synthesize join conditions by combining multiple :class:`PathCondition` objects.

    This class implements the join-condition synthesis described in §26.5.3.
    It is intentionally stateless aside from the :attr:`_synthesis_log`, which
    accumulates diagnostic messages for later inspection.

    copilot: The synthesizer is the most semantically rich component in this
    module.  LLM-assisted simplification of ``(or ...)`` expressions should
    hook into :meth:`synthesize_join` via the ``_synthesis_log`` channel.
    """

    def __init__(self) -> None:
        self._synthesis_log: list[str] = []

    # ------------------------------------------------------------------ core operations

    def synthesize_join(self, conditions: list[PathCondition]) -> PathCondition:
        """Combine multiple path conditions into a single join condition.

        The join condition's antecedents are the union (deduped) of all input
        antecedents.  Its consequent is the SMT2 disjunction of all input
        consequents.  The fragment is determined by the most general
        ``can_merge_with`` reduction.

        Parameters
        ----------
        conditions:
            Non-empty list of :class:`PathCondition` objects to join.

        Returns
        -------
        PathCondition
            A new path condition with ``is_join=True``.
        """
        if not conditions:
            msg = "synthesize_join called with empty conditions list"
            self._synthesis_log.append(msg)
            logger.warning(msg)
            return PathCondition(
                condition_id=f"join_{uuid.uuid4().hex[:8]}",
                branch_label="empty_join",
                antecedents=(),
                consequent="false",
                depth=0,
                is_join=True,
                fragment=FragmentHint.QF_BOOL,
            )

        # Collect all antecedents (deduped, order-preserving).
        seen_ants: set[str] = set()
        merged_antecedents: list[str] = []
        for cond in conditions:
            for ant in cond.antecedents:
                if ant not in seen_ants:
                    seen_ants.add(ant)
                    merged_antecedents.append(ant)

        # Build disjunction of consequents.
        consequents = [c.consequent for c in conditions if c.consequent not in ("false",)]
        if not consequents:
            disjunction = "false"
        elif len(consequents) == 1:
            disjunction = consequents[0]
        else:
            disjunction = f"(or {' '.join(consequents)})"

        # Reduce fragment hints via can_merge_with.
        merged_fragment = conditions[0].fragment
        for cond in conditions[1:]:
            merged_fragment = merged_fragment.can_merge_with(cond.fragment)

        max_depth = max(c.depth for c in conditions)
        join_id = f"join_{uuid.uuid4().hex[:8]}"

        self._synthesis_log.append(
            f"synthesize_join: id={join_id} inputs={len(conditions)} "
            f"depth={max_depth} fragment={merged_fragment.name}"
        )
        logger.debug(self._synthesis_log[-1])

        return PathCondition(
            condition_id=join_id,
            branch_label=f"join({len(conditions)}_paths)",
            antecedents=tuple(merged_antecedents),
            consequent=disjunction,
            depth=max_depth,
            is_join=True,
            fragment=merged_fragment,
        )

    def weaken_to_join(self, conds: list[PathCondition]) -> PathCondition:
        """Synthesize a weakened join by hoisting shared antecedents.

        Antecedents that appear in *every* input condition are promoted to the
        consequent (they are always true at the join point).  The remaining
        antecedents form the new, smaller antecedent set.  This produces a
        logically weaker (more permissive) condition that is easier to
        discharge.

        Parameters
        ----------
        conds:
            List of :class:`PathCondition` objects to weaken and join.
        """
        if not conds:
            return self.synthesize_join(conds)

        # Find antecedents shared by ALL conditions.
        common = set(conds[0].antecedents)
        for cond in conds[1:]:
            common &= set(cond.antecedents)

        # Remove common antecedents from each condition, then join.
        stripped: list[PathCondition] = []
        for cond in conds:
            new_ants = tuple(a for a in cond.antecedents if a not in common)
            stripped.append(
                PathCondition(
                    condition_id=cond.condition_id,
                    branch_label=cond.branch_label,
                    antecedents=new_ants,
                    consequent=cond.consequent,
                    depth=cond.depth,
                    is_join=cond.is_join,
                    fragment=cond.fragment,
                )
            )

        base_join = self.synthesize_join(stripped)

        # Incorporate the common antecedents into the consequent if any.
        if common:
            common_conj = (
                next(iter(common))
                if len(common) == 1
                else f"(and {' '.join(sorted(common))})"
            )
            new_consequent = (
                f"(and {common_conj} {base_join.consequent})"
                if base_join.consequent != "false"
                else "false"
            )
        else:
            new_consequent = base_join.consequent

        self._synthesis_log.append(
            f"weaken_to_join: hoisted {len(common)} shared antecedent(s)"
        )

        return PathCondition(
            condition_id=base_join.condition_id,
            branch_label=f"weak_{base_join.branch_label}",
            antecedents=base_join.antecedents,
            consequent=new_consequent,
            depth=base_join.depth,
            is_join=True,
            fragment=base_join.fragment,
        )

    def strengthen_branch(self, branch: PathCondition, extra: str) -> PathCondition:
        """Return a new :class:`PathCondition` with *extra* added to antecedents.

        If *extra* is already present in the antecedent tuple the original
        condition is returned unchanged.  This method never produces duplicate
        antecedents.

        Parameters
        ----------
        branch:
            The condition to strengthen.
        extra:
            An SMT2 formula string to add as an additional antecedent.
        """
        if extra in branch.antecedents:
            logger.debug(
                "strengthen_branch: %r already in antecedents of %r — no-op",
                extra,
                branch.condition_id,
            )
            return branch

        new_ants = branch.antecedents + (extra,)
        return PathCondition(
            condition_id=branch.condition_id,
            branch_label=branch.branch_label,
            antecedents=new_ants,
            consequent=branch.consequent,
            depth=branch.depth,
            is_join=branch.is_join,
            fragment=branch.fragment,
        )

    def detect_overlap(self, c1: PathCondition, c2: PathCondition) -> bool:
        """Return ``True`` iff *c1* and *c2* share at least one antecedent.

        Overlap indicates that the two path conditions have a non-trivial
        common prefix, which may allow join weakening.
        """
        return bool(set(c1.antecedents) & set(c2.antecedents))

    def minimize_join(self, conditions: list[PathCondition]) -> list[PathCondition]:
        """Remove redundant conditions from *conditions*.

        Two conditions are redundant with respect to each other iff they have
        the same ``consequent``.  In that case only the one with the smaller
        antecedent tuple is kept.  Tautological conditions
        (``is_tautology() == True``) are always removed.

        Parameters
        ----------
        conditions:
            List of :class:`PathCondition` objects to minimise.

        Returns
        -------
        list[PathCondition]
            Deduplicated list (original order preserved for first occurrence).
        """
        seen_consequents: dict[str, PathCondition] = {}
        kept: list[PathCondition] = []

        for cond in conditions:
            if cond.is_tautology():
                self._synthesis_log.append(
                    f"minimize_join: dropping tautology {cond.condition_id!r}"
                )
                continue
            existing = seen_consequents.get(cond.consequent)
            if existing is None:
                seen_consequents[cond.consequent] = cond
                kept.append(cond)
            elif len(cond.antecedents) < len(existing.antecedents):
                # Replace with the simpler (fewer antecedents) version.
                idx = kept.index(existing)
                kept[idx] = cond
                seen_consequents[cond.consequent] = cond
                self._synthesis_log.append(
                    f"minimize_join: replacing {existing.condition_id!r} with "
                    f"{cond.condition_id!r} (fewer antecedents)"
                )

        return kept

    def copilot_join_hint(self, conditions: list[PathCondition]) -> str:
        """Generate a human-readable join-strategy hint for Copilot.

        The hint considers: number of conditions, pairwise overlap, depth
        distribution, and fragment heterogeneity.  It returns multi-sentence
        advice that can be surfaced in IDE tooltips or log messages.

        Parameters
        ----------
        conditions:
            The list of :class:`PathCondition` objects about to be joined.

        copilot: This method exists specifically to provide Copilot with
        structured context about the join being synthesised.
        """
        n = len(conditions)
        if n == 0:
            return "No conditions supplied; join will produce 'false'."
        if n == 1:
            return (
                "Only one condition — no join needed; "
                "consider using encode_branch directly."
            )

        depths = [c.depth for c in conditions]
        min_depth, max_depth = min(depths), max(depths)
        depth_skew = max_depth - min_depth

        # Count overlapping pairs.
        overlap_count = sum(
            1
            for i in range(n)
            for j in range(i + 1, n)
            if self.detect_overlap(conditions[i], conditions[j])
        )
        total_pairs = n * (n - 1) // 2
        overlap_ratio = overlap_count / total_pairs if total_pairs else 0.0

        # Check fragment heterogeneity.
        fragments = {c.fragment for c in conditions}
        heterogeneous = len(fragments) > 1

        parts: list[str] = [
            f"Joining {n} path conditions (depths {min_depth}–{max_depth})."
        ]

        if overlap_ratio > 0.5:
            parts.append(
                f"High antecedent overlap ({overlap_ratio:.0%} of pairs) — "
                "consider weaken_to_join to hoist shared guards."
            )
        else:
            parts.append(
                "Low antecedent overlap — synthesize_join will produce a wide disjunction."
            )

        if depth_skew > 3:
            parts.append(
                f"Depth skew is {depth_skew}; the shallowest path may subsume deeper ones — "
                "run minimize_join first."
            )

        if heterogeneous:
            names = ", ".join(f.name for f in fragments)
            parts.append(
                f"Conditions span multiple fragments ({names}); "
                "the merged fragment will be MIXED — ensure the solver supports combination."
            )
        else:
            parts.append(
                f"All conditions target fragment {next(iter(fragments)).name} — "
                "single-theory discharge expected."
            )

        return "  ".join(parts)


# ============================== PathConditionEncoder ==============================


class PathConditionEncoder:
    """High-level encoder for branching and join path conditions (§26.5).

    :class:`PathConditionEncoder` is the primary entry point for consumers of
    this module.  It delegates join synthesis to a
    :class:`JoinConditionSynthesizer`, caches previously encoded path
    conditions by their consequent, and maintains a counters dictionary for
    diagnostic purposes.

    Usage::

        enc = PathConditionEncoder()
        branch = enc.encode_branch("(< x 10)", antecedents=["(>= x 0)"])
        join   = enc.encode_join([branch, other_branch])

    copilot: PathConditionEncoder is the public API surface for path-condition
    encoding.  When adding new encoding strategies prefer adding them here
    rather than in the synthesizer.
    """

    def __init__(self) -> None:
        self._synthesizer: JoinConditionSynthesizer = JoinConditionSynthesizer()
        self._encoding_cache: dict[str, PathCondition] = {}
        self._stats: dict[str, int] = {
            "branches_encoded": 0,
            "joins_encoded": 0,
            "infeasible_pruned": 0,
            "cache_hits": 0,
            "sequential_composed": 0,
            "loop_invariants_encoded": 0,
        }

    # ------------------------------------------------------------------ fragment detection

    @staticmethod
    def _detect_fragment(guard_smt: str) -> FragmentHint:
        """Infer the most specific :class:`FragmentHint` from a raw SMT2 string.

        Uses lightweight regex heuristics:

        * Bit-vector patterns (``BitVec``, ``#x…``, ``bv…``) → ``QF_BV``
        * Real arithmetic or decimal literals → ``QF_LRA``
        * Integer arithmetic operators with digit patterns → ``QF_LIA``
        * Otherwise → ``QF_BOOL``
        """
        if _BITVEC_PATTERN.search(guard_smt):
            return FragmentHint.QF_BV
        if _REAL_ARITH_PATTERN.search(guard_smt):
            return FragmentHint.QF_LRA
        if _INT_ARITH_PATTERN.search(guard_smt):
            return FragmentHint.QF_LIA
        return FragmentHint.QF_BOOL

    # ------------------------------------------------------------------ encoding API

    def encode_branch(self, guard: str, antecedents: list[str]) -> PathCondition:
        """Encode a single branch guard as a :class:`PathCondition`.

        The fragment is auto-detected from the guard's SMT2 syntax.  Encoding
        results are cached by ``(guard, tuple(antecedents))`` so repeated calls
        with the same arguments are free.

        Parameters
        ----------
        guard:
            SMT-LIB 2 boolean expression for the branch guard.
        antecedents:
            Ordered list of SMT2 expressions already known to hold at this
            branch point (typically the guards on the path from the program
            entry to this branch).

        Returns
        -------
        PathCondition
            A fresh (or cached) path condition with ``is_join=False``.
        """
        cache_key = f"{guard}::{tuple(antecedents)!r}"
        if cache_key in self._encoding_cache:
            self._stats["cache_hits"] += 1
            logger.debug("encode_branch: cache hit for guard %r", guard[:40])
            return self._encoding_cache[cache_key]

        fragment = self._detect_fragment(guard)
        condition_id = f"branch_{uuid.uuid4().hex[:8]}"
        depth = len(antecedents)

        pc = PathCondition(
            condition_id=condition_id,
            branch_label="branch",
            antecedents=tuple(antecedents),
            consequent=guard,
            depth=depth,
            is_join=False,
            fragment=fragment,
        )
        self._encoding_cache[cache_key] = pc
        self._stats["branches_encoded"] += 1
        logger.debug(
            "encode_branch: id=%s depth=%d fragment=%s guard=%.40s",
            condition_id,
            depth,
            fragment.name,
            guard,
        )
        return pc

    def encode_join(self, branch_conditions: list[PathCondition]) -> PathCondition:
        """Encode a join point by delegating to the synthesizer.

        Parameters
        ----------
        branch_conditions:
            The individual path conditions flowing into the join node.

        Returns
        -------
        PathCondition
            A new join condition with ``is_join=True``.
        """
        result = self._synthesizer.synthesize_join(branch_conditions)
        self._stats["joins_encoded"] += 1
        logger.info(
            "encode_join: synthesised id=%s from %d branch(es)",
            result.condition_id,
            len(branch_conditions),
        )
        return result

    def encode_sequential(self, conditions: list[PathCondition]) -> PathCondition:
        """Compose a list of conditions sequentially (path extension).

        Sequential composition accumulates all antecedents from every input
        condition and uses the *last* condition's consequent as the final
        assertion.  This models a straight-line segment of a control-flow path
        where each step produces a new fact.

        Parameters
        ----------
        conditions:
            Ordered list of :class:`PathCondition` objects along a single
            straight-line path.  Must be non-empty.

        Returns
        -------
        PathCondition
            A single condition representing the composed path.

        Raises
        ------
        ValueError
            If *conditions* is empty.
        """
        if not conditions:
            raise ValueError("encode_sequential requires at least one condition.")

        # Collect all antecedents in order, deduplicating.
        seen: set[str] = set()
        all_ants: list[str] = []
        for cond in conditions:
            for ant in cond.antecedents:
                if ant not in seen:
                    seen.add(ant)
                    all_ants.append(ant)

        last = conditions[-1]
        composed_id = f"seq_{uuid.uuid4().hex[:8]}"

        # Promote intermediate consequents to antecedents.
        for cond in conditions[:-1]:
            if cond.consequent not in seen and cond.consequent not in ("true", "false"):
                seen.add(cond.consequent)
                all_ants.append(cond.consequent)

        merged_fragment = conditions[0].fragment
        for cond in conditions[1:]:
            merged_fragment = merged_fragment.can_merge_with(cond.fragment)

        result = PathCondition(
            condition_id=composed_id,
            branch_label=f"seq({len(conditions)}_steps)",
            antecedents=tuple(all_ants),
            consequent=last.consequent,
            depth=last.depth,
            is_join=False,
            fragment=merged_fragment,
        )
        self._stats["sequential_composed"] += 1
        logger.debug(
            "encode_sequential: id=%s steps=%d antecedents=%d",
            composed_id,
            len(conditions),
            len(all_ants),
        )
        return result

    def encode_loop_invariant(self, guard: str, body_condition: PathCondition) -> PathCondition:
        """Encode a loop-invariant path condition.

        The loop invariant must hold both when the loop is entered (before the
        body executes) and after the body executes.  The encoding captures this
        as:

        * antecedents: the loop guard plus the body's consequent (the
          post-body fact)
        * consequent: ``(and <guard> <body_consequent>)`` — the conjunction
          that must remain true across iterations.

        Parameters
        ----------
        guard:
            SMT2 boolean expression for the loop guard (the condition that
            keeps the loop running).
        body_condition:
            :class:`PathCondition` representing the effect of one loop-body
            iteration.

        Returns
        -------
        PathCondition
            A path condition with ``branch_label="loop_invariant"``.
        """
        inv_id = f"inv_{uuid.uuid4().hex[:8]}"
        fragment = self._detect_fragment(guard).can_merge_with(body_condition.fragment)

        antecedents = list(body_condition.antecedents)
        # Add guard and body consequent as antecedents (deduped).
        for extra in (guard, body_condition.consequent):
            if extra not in antecedents and extra not in ("true", "false"):
                antecedents.append(extra)

        consequent = f"(and {guard} {body_condition.consequent})"

        result = PathCondition(
            condition_id=inv_id,
            branch_label="loop_invariant",
            antecedents=tuple(antecedents),
            consequent=consequent,
            depth=body_condition.depth,
            is_join=False,
            fragment=fragment,
        )
        self._stats["loop_invariants_encoded"] += 1
        logger.debug("encode_loop_invariant: id=%s fragment=%s", inv_id, fragment.name)
        return result

    # ------------------------------------------------------------------ analysis helpers

    def feasibility_check(self, path_condition: PathCondition) -> bool:
        """Perform a heuristic feasibility check on *path_condition*.

        This check is deliberately over-approximate (returns ``True`` when
        uncertain) so that it never incorrectly eliminates feasible paths.
        It returns ``False`` only when a definite infeasibility witness is
        found:

        * Any antecedent is in :data:`_FALSE_LITERALS`.
        * The ``consequent`` is in :data:`_FALSE_LITERALS`.
        * The literal string ``"unsat"`` appears in the consequent.

        For sound satisfiability checking use a full SMT solver query instead.
        """
        for ant in path_condition.antecedents:
            if ant.strip() in _FALSE_LITERALS:
                logger.debug(
                    "feasibility_check: infeasible — antecedent %r is false", ant
                )
                return False
        if path_condition.consequent.strip() in _FALSE_LITERALS:
            logger.debug("feasibility_check: infeasible — consequent is false")
            return False
        if "unsat" in path_condition.consequent:
            logger.debug(
                "feasibility_check: infeasible — 'unsat' literal in consequent"
            )
            return False
        return True

    def eliminate_dead_paths(self, tree: PathTree) -> int:
        """Remove provably infeasible paths from *tree* and return the count.

        In addition to calling :meth:`PathTree.prune_infeasible` (which removes
        nodes with a literal ``false`` guard), this method checks each leaf's
        root-to-leaf path for contradictory guard pairs.  A pair of guards is
        considered contradictory if one is the syntactic negation of the other
        in the simple form ``(not <expr>)`` vs ``<expr>``.

        Parameters
        ----------
        tree:
            The :class:`PathTree` to inspect and prune.

        Returns
        -------
        int
            Total number of nodes eliminated (from both pruning passes).
        """
        pruned = tree.prune_infeasible()
        self._stats["infeasible_pruned"] += pruned

        # Second pass: look for contradictory guards along individual paths.
        contradiction_count = 0
        for leaf in tree.all_leaves():
            path = tree.get_path(leaf.node_id)
            if not path:
                continue
            guards_seen: set[str] = set()
            contradicted = False
            for node in path:
                g = node.guard_smt.strip()
                # Check simple negation patterns: (not X) vs X.
                if g.startswith("(not ") and g.endswith(")"):
                    inner = g[5:-1].strip()
                    if inner in guards_seen:
                        contradicted = True
                        break
                else:
                    negated = f"(not {g})"
                    if negated in guards_seen:
                        contradicted = True
                        break
                guards_seen.add(g)

            if contradicted:
                logger.debug(
                    "eliminate_dead_paths: contradictory path ending at %r",
                    leaf.node_id,
                )
                # Mark the leaf's guard as false so a subsequent prune removes it.
                adjusted = BranchNode(
                    node_id=leaf.node_id,
                    label=leaf.label,
                    guard_smt="false",
                    parent_id=leaf.parent_id,
                    depth=leaf.depth,
                )
                tree._nodes[leaf.node_id] = adjusted
                contradiction_count += 1

        if contradiction_count:
            extra = tree.prune_infeasible(session_hint="contradiction-pass")
            self._stats["infeasible_pruned"] += extra

        return pruned + contradiction_count

    def emit_all_path_assertions(self, tree: PathTree) -> str:
        """Emit an SMT2 script asserting all feasible leaf path conditions.

        For each leaf in *tree* the method builds a path condition via the
        path from root to that leaf.  Infeasible paths (as determined by
        :meth:`feasibility_check`) are skipped.  The script ends with
        ``(check-sat)``.

        Parameters
        ----------
        tree:
            A :class:`PathTree` whose leaves define the assertion targets.

        Returns
        -------
        str
            A self-contained SMT-LIB 2 script ready for passing to a solver.
        """
        leaves = tree.all_leaves()
        smtlines: list[str] = [
            "; JuGeo path-condition assertions — auto-generated by PathConditionEncoder",
            f"; nodes={tree.node_count()} leaves={len(leaves)}",
            "(set-logic QF_BOOL)",
            "",
        ]
        emitted = 0
        for leaf in leaves:
            path = tree.get_path(leaf.node_id)
            if not path:
                continue
            guards = [n.guard_smt for n in path if n.guard_smt not in ("true", "True")]
            if not guards:
                continue
            # Build antecedents from all guards except the last.
            antecedents = guards[:-1]
            consequent = guards[-1]
            pc = PathCondition(
                condition_id=f"assert_{leaf.node_id}",
                branch_label=leaf.label,
                antecedents=tuple(antecedents),
                consequent=consequent,
                depth=leaf.depth,
                is_join=False,
                fragment=self._detect_fragment(consequent),
            )
            if not self.feasibility_check(pc):
                smtlines.append(f"; skipped infeasible path to {leaf.node_id}")
                continue
            smtlines.append(f"; path to leaf {leaf.node_id!r} (depth {leaf.depth})")
            smtlines.append(pc.to_smt2())
            smtlines.append("")
            emitted += 1

        smtlines.append(f"; {emitted} path assertion(s) emitted")
        smtlines.append("(check-sat)")
        return "\n".join(smtlines)

    def copilot_path_summary(self, tree: PathTree) -> str:
        """Return a human-readable summary of *tree* for Copilot display.

        The summary includes:

        * Total node count and leaf count.
        * An estimate of feasible paths (leaves minus those with ``false`` guards).
        * Whether any potential loop structures were detected (cycles in labels).
        * A note that the Mermaid diagram is available via ``tree.to_mermaid()``.

        copilot: This method is intended for surfacing tree diagnostics in
        Copilot chat context windows.
        """
        node_count = tree.node_count()
        leaves = tree.all_leaves()
        leaf_count = len(leaves)
        infeasible_leaves = sum(
            1 for lf in leaves if lf.guard_smt in _FALSE_LITERALS
        )
        feasible_estimate = leaf_count - infeasible_leaves

        # Heuristic loop detection: look for labels containing 'loop' or 'back'.
        loop_labels = [
            n.label
            for n in tree._nodes.values()
            if re.search(r"\b(loop|back|while|for|repeat)\b", n.label, re.IGNORECASE)
        ]
        loop_note = (
            f"  Detected {len(loop_labels)} potential loop node(s): "
            + ", ".join(loop_labels[:5])
            + ("..." if len(loop_labels) > 5 else "")
            if loop_labels
            else "  No loop structures detected."
        )

        lines = [
            "=== PathTree Summary ===",
            f"  Total nodes     : {node_count}",
            f"  Leaf nodes      : {leaf_count}",
            f"  Feasible paths  : ~{feasible_estimate}  "
            f"({infeasible_leaves} trivially infeasible)",
            loop_note,
            "  Mermaid diagram : call tree.to_mermaid() to render.",
            f"  Encoder stats   : {self._stats}",
            "========================",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ statistics

    def stats(self) -> dict[str, int]:
        """Return a copy of the internal statistics counters."""
        return dict(self._stats)


# ============================== module-level helpers ==============================


def encode_simple_branch(guard: str) -> PathCondition:
    """Convenience function: encode a single guard with no prior antecedents.

    This is the simplest possible encoding — a branch with an empty
    antecedent set.  Useful for quick one-off encoding in test code and
    Copilot-suggested guards.

    Parameters
    ----------
    guard:
        An SMT-LIB 2 boolean expression representing the branch condition.

    Returns
    -------
    PathCondition
        A new path condition with ``depth=0``, ``is_join=False``, and an
        auto-detected ``fragment``.

    Example
    -------
    >>> pc = encode_simple_branch("(< x 10)")
    >>> pc.depth
    0
    >>> pc.is_join
    False
    """
    encoder = PathConditionEncoder()
    return encoder.encode_branch(guard, antecedents=[])
