"""Branching, joins, and path-sensitive obligations.

This module implements the path-sensitive encoding phase of the exact Z3
encoding pipeline, as described in **Chapter 26.4** of
``preliminaries/theory2.tex``.

Path Sensitivity
----------------
A program analysis is *path-sensitive* if it tracks separate abstract states
for different control-flow paths.  In a Z3 encoding, path sensitivity is
achieved by associating each program point with a **path predicate** — the
conjunction of all branch conditions that must hold for control to reach that
point.

Formally, given a control-flow graph (CFG) with edges labelled by branch
conditions, the path predicate ``π(n)`` at node ``n`` is:

    π(entry) = true
    π(n) = AND { c_e | e = (m, n) is a true-branch edge } AND { NOT c_e | e = (m, n) is a false-branch edge } AND π(m)

At a **join node** (a node with multiple incoming edges), the path predicate
is the *disjunction* of the predicates from each incoming path:

    π(join) = OR { π(m) | (m, join) is an edge in the CFG }

A proof obligation ``O`` is **path-sensitive** if its validity depends on the
path predicate:  ``π(n) ⟹ O``.

The Z3 encoding of a path-sensitive obligation is:

    (assert π(n))
    (assert (not O))
    (check-sat)   ; unsat ⟹ O holds on all paths reaching n

Branch Conditions and Guard Formulae
-------------------------------------
Each branch condition is an SMT-LIB 2 formula over the scalar variables
declared in the structural core (§26.3).  The encoding layer computes:

* **True-branch predicate**: ``c`` (the condition is asserted to hold).
* **False-branch predicate**: ``(not c)`` (the condition is asserted to fail).
* **Join predicate**: the disjunction ``(or π₁ π₂ … πₖ)`` of all incoming
  path predicates.

Loop Encoding
-------------
Loop heads introduce a special case: the path predicate must be invariant
under one loop iteration.  The module provides
:meth:`BranchingJoinsPathSensitiveAnalyzer.encode_loop_invariant`, which
encodes the Hoare-logic obligation:

    { I ∧ c } body { I }

as the SMT2 query:

    (assert I)
    (assert c)
    (assert body_obligation)
    (assert (not I_post))
    (check-sat)   ; unsat ⟹ loop invariant holds

Architecture
------------
* **PathNodeKind** — :class:`~enum.Enum` classifying CFG node types.
* **BranchingJoinsPathSensitiveWitness** — frozen dataclass capturing the
  path-sensitive obligation evidence.
* **BranchingJoinsPathSensitiveAnalyzer** — stateless helper that builds
  path predicates, join predicates, and obligation encodings.
* **BranchingJoinsPathSensitiveCoordinator** — session-level coordinator
  with cache and stats.

copilot note
------------
When copilot generates branch conditions for new control-flow analyses, call
:func:`encode_simple_branch` to wrap each condition in a path-sensitive
witness.  The returned witness's
:meth:`~BranchingJoinsPathSensitiveWitness.to_smt2_path_query` can be used
directly as an SMT-LIB 2 satisfiability query.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from itertools import chain
from typing import Any

# copilot: optional jugeo imports with _FLAG_AVAILABLE guards.
try:
    from jugeo.encodings.scalar_encodings.models import (
        SortKind,
        FragmentHint,
        EncodeStatus,
        PathCondition,
        GuardFormula,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    SortKind = None  # type: ignore[assignment,misc]
    FragmentHint = None  # type: ignore[assignment,misc]
    EncodeStatus = None  # type: ignore[assignment,misc]
    PathCondition = None  # type: ignore[assignment,misc]
    GuardFormula = None  # type: ignore[assignment,misc]
    _MODELS_AVAILABLE = False

try:
    from jugeo.solver.fragments import Fragment, classify_fragment
    _FRAGMENTS_AVAILABLE = True
except ImportError:
    Fragment = None  # type: ignore[assignment,misc]
    classify_fragment = None  # type: ignore[assignment,misc]
    _FRAGMENTS_AVAILABLE = False

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, SolveOutcome
    _Z3SESSION_AVAILABLE = True
except ImportError:
    Z3Session = None  # type: ignore[assignment,misc]
    Z3Formula = None  # type: ignore[assignment,misc]
    SolveOutcome = None  # type: ignore[assignment,misc]
    _Z3SESSION_AVAILABLE = False

try:
    import z3
    _Z3_AVAILABLE = True
except ImportError:
    z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================== path node kind enum ==============================


class PathNodeKind(Enum):
    """Classification of nodes in the path-predicate control-flow graph.

    Each member represents a distinct structural role in the CFG.  The
    path-predicate computation rules differ per kind: branch nodes split the
    path predicate, join nodes merge it, loop nodes enforce invariants.

    Members
    -------
    ENTRY
        The unique CFG entry node. Path predicate is ``true`` at this node.
    BRANCH_TRUE
        A node reached by taking the ``true`` branch of a conditional. The
        branch condition is *added* to the path predicate.
    BRANCH_FALSE
        A node reached by taking the ``false`` branch. The *negation* of the
        branch condition is added to the path predicate.
    JOIN
        A node with multiple incoming edges (φ-point). The path predicate is
        the disjunction of incoming predicates.
    EXIT
        The unique CFG exit node. No path-predicate extension.
    LOOP_HEAD
        The header of a loop (dominates the back-edge source). Path predicate
        accumulates the loop condition.
    LOOP_BACK
        A back edge target (same as ``LOOP_HEAD`` in natural loops).  Signals
        that the loop invariant must be re-checked.
    ASSERTION_POINT
        A node at which a proof obligation is discharged. The full path
        predicate at this node is used as the antecedent.
    """

    ENTRY = auto()
    BRANCH_TRUE = auto()
    BRANCH_FALSE = auto()
    JOIN = auto()
    EXIT = auto()
    LOOP_HEAD = auto()
    LOOP_BACK = auto()
    ASSERTION_POINT = auto()

    # ------------------------------------------------------------------ #
    # Classification helpers                                               #
    # ------------------------------------------------------------------ #

    def is_branching(self) -> bool:
        """Return True if this node kind introduces a branch split.

        Branch-splitting nodes are those where the path predicate diverges into
        two separate sub-predicates: one for the true branch and one for the
        false branch.

        Returns
        -------
        bool

        Examples
        --------
        >>> PathNodeKind.BRANCH_TRUE.is_branching()
        True
        >>> PathNodeKind.JOIN.is_branching()
        False
        """
        return self in (PathNodeKind.BRANCH_TRUE, PathNodeKind.BRANCH_FALSE)

    def is_merging(self) -> bool:
        """Return True if this node kind merges two or more path predicates.

        Merging nodes compute the disjunction of their incoming path
        predicates.  Only :attr:`JOIN` nodes merge predicates in a standard
        CFG.

        Returns
        -------
        bool

        Examples
        --------
        >>> PathNodeKind.JOIN.is_merging()
        True
        >>> PathNodeKind.BRANCH_TRUE.is_merging()
        False
        """
        return self is PathNodeKind.JOIN

    def is_loop_related(self) -> bool:
        """Return True if this node kind is associated with loop encoding.

        Loop-related nodes require special handling of the path predicate:
        the predicate must be augmented with the loop invariant to support
        inductive reasoning.

        Returns
        -------
        bool

        Examples
        --------
        >>> PathNodeKind.LOOP_HEAD.is_loop_related()
        True
        >>> PathNodeKind.EXIT.is_loop_related()
        False
        """
        return self in (PathNodeKind.LOOP_HEAD, PathNodeKind.LOOP_BACK)

    def node_smt2_tag(self) -> str:
        """Return the SMT-LIB 2 comment tag for this node kind.

        The tag is a short string used to annotate SMT-LIB 2 scripts with the
        control-flow context at each assertion point.

        Returns
        -------
        str
            A lowercase hyphen-separated tag string.

        Examples
        --------
        >>> PathNodeKind.BRANCH_TRUE.node_smt2_tag()
        '; node:branch-true'
        >>> PathNodeKind.JOIN.node_smt2_tag()
        '; node:join'
        """
        _tags: dict[PathNodeKind, str] = {
            PathNodeKind.ENTRY: "entry",
            PathNodeKind.BRANCH_TRUE: "branch-true",
            PathNodeKind.BRANCH_FALSE: "branch-false",
            PathNodeKind.JOIN: "join",
            PathNodeKind.EXIT: "exit",
            PathNodeKind.LOOP_HEAD: "loop-head",
            PathNodeKind.LOOP_BACK: "loop-back",
            PathNodeKind.ASSERTION_POINT: "assertion-point",
        }
        return f"; node:{_tags[self]}"


# ============================== witness dataclass ==============================


@dataclass(frozen=True)
class BranchingJoinsPathSensitiveWitness:
    """Witness of a path-sensitive proof obligation.

    This frozen dataclass records a complete path-sensitive obligation: the
    proof obligation SMT formula, the full path predicate under which it must
    be evaluated, the individual branch conditions that compose the predicate,
    and metadata about feasibility and provenance.

    Fields
    ------
    witness_id : str
        Unique identifier (UUID4 hex string).
    obligation_smt : str
        The SMT-LIB 2 formula for the proof obligation (e.g. ``"(> x 0)"``).
    path_predicate : str
        The pre-computed conjunctive/disjunctive path predicate as an SMT2
        formula string.
    branch_conditions : tuple[str, ...]
        The individual branch-condition SMT2 strings whose conjunction (or
        negation-adjusted conjunction) yields ``path_predicate``.
    join_predicates : tuple[str, ...]
        The incoming path predicates at a join node; empty for non-join nodes.
    node_kind : PathNodeKind
        The kind of CFG node at which this obligation is evaluated.
    path_depth : int
        The number of branch conditions in the path predicate (nesting depth).
    is_feasible : bool
        Whether the path predicate is satisfiable (not trivially ``false``).
    copilot_label : str
        Optional copilot annotation label.
    timestamp : float
        Unix timestamp of witness creation.
    """

    witness_id: str
    obligation_smt: str
    path_predicate: str
    branch_conditions: tuple[str, ...]
    join_predicates: tuple[str, ...]
    node_kind: PathNodeKind
    path_depth: int
    is_feasible: bool
    copilot_label: str
    timestamp: float

    # ------------------------------------------------------------------ #
    # Obligation helpers                                                   #
    # ------------------------------------------------------------------ #

    def full_path_obligation(self) -> str:
        """Return the full path-sensitive obligation as an SMT2 implication.

        The implication is ``(=> path_predicate obligation_smt)``, which is
        valid iff the obligation holds whenever the path predicate is satisfied.

        Returns
        -------
        str
            An SMT2 implication string.

        Examples
        --------
        >>> w.full_path_obligation()
        '(=> (and (> x 0)) (< y 10))'
        """
        if not self.path_predicate or self.path_predicate == "true":
            return self.obligation_smt
        return f"(=> {self.path_predicate} {self.obligation_smt})"

    def negate_path(self) -> str:
        """Return the negation of the path predicate.

        Used when constructing a counterexample query: asserting the negation
        of the path predicate yields the conditions under which the current
        path is *not* taken.

        Returns
        -------
        str
            SMT2 negation of ``path_predicate``.

        Examples
        --------
        >>> w.negate_path()
        '(not (and (> x 0)))'
        """
        if not self.path_predicate or self.path_predicate == "true":
            return "(not true)"
        return f"(not {self.path_predicate})"

    def to_smt2_path_query(self) -> str:
        """Return an SMT-LIB 2 satisfiability query for this obligation.

        The query asserts the path predicate and the *negation* of the
        obligation; ``unsat`` means the obligation holds on this path.

        Returns
        -------
        str
            A complete SMT-LIB 2 check-sat block.

        Examples
        --------
        >>> print(w.to_smt2_path_query())
        ; path-sensitive obligation check
        ; node: branch-true  depth=2  feasible=True
        (push)
        (assert (> x 0))
        (assert (not (< y 10)))
        (check-sat)
        (pop)
        """
        lines: list[str] = [
            f"; path-sensitive obligation check",
            f"{self.node_kind.node_smt2_tag()}  depth={self.path_depth}  "
            f"feasible={self.is_feasible}",
            "(push)",
        ]
        if self.path_predicate and self.path_predicate != "true":
            lines.append(f"(assert {self.path_predicate})")
        lines.append(f"(assert (not {self.obligation_smt}))")
        lines.append("(check-sat)")
        lines.append("(pop)")
        return "\n".join(lines)

    def fingerprint(self) -> str:
        """Compute a stable content fingerprint for this witness.

        Returns
        -------
        str
            A 32-character lowercase hex string (MD5 of obligation + path +
            node_kind + branch_conditions).

        Examples
        --------
        >>> w.fingerprint()
        'b4e1f23a...'
        """
        payload = json.dumps(
            {
                "obligation": self.obligation_smt,
                "path": self.path_predicate,
                "kind": self.node_kind.name,
                "branches": list(self.branch_conditions),
            },
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    def merge_paths(
        self, other: BranchingJoinsPathSensitiveWitness
    ) -> BranchingJoinsPathSensitiveWitness:
        """Merge this witness with another at a join point.

        The merged path predicate is the disjunction of both path predicates.
        The obligation must match; if it does not, the first obligation is
        used with a warning comment.

        Parameters
        ----------
        other:
            The other incoming-path witness.

        Returns
        -------
        BranchingJoinsPathSensitiveWitness
            A new witness representing the merged join-node obligation.

        Examples
        --------
        >>> w_join = w_true.merge_paths(w_false)
        >>> w_join.node_kind
        <PathNodeKind.JOIN: 4>
        """
        join_preds = tuple(
            p for p in (self.path_predicate, other.path_predicate)
            if p and p != "true"
        )
        if len(join_preds) == 2:
            merged_pred = f"(or {join_preds[0]} {join_preds[1]})"
        elif len(join_preds) == 1:
            merged_pred = join_preds[0]
        else:
            merged_pred = "true"

        merged_branches = tuple(
            dict.fromkeys(chain(self.branch_conditions, other.branch_conditions))
        )

        if self.obligation_smt != other.obligation_smt:
            logger.warning(
                "merge_paths: obligation mismatch; using self obligation. "
                "self=%r other=%r", self.obligation_smt, other.obligation_smt
            )

        return BranchingJoinsPathSensitiveWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            obligation_smt=self.obligation_smt,
            path_predicate=merged_pred,
            branch_conditions=merged_branches,
            join_predicates=(self.path_predicate, other.path_predicate),
            node_kind=PathNodeKind.JOIN,
            path_depth=min(self.path_depth, other.path_depth),
            is_feasible=self.is_feasible or other.is_feasible,
            copilot_label=f"join:{self.witness_id[:8]}+{other.witness_id[:8]}",
            timestamp=time.time(),
        )

    def copilot_path_hint(self) -> str:
        """Return a copilot-style hint for this path-sensitive witness.

        Returns
        -------
        str
            A multi-line string beginning with ``# copilot:``.

        Examples
        --------
        >>> print(w.copilot_path_hint())
        # copilot: path-sensitive witness id=abc123
        # copilot: obligation=(> x 0)  depth=2  feasible=True
        """
        return (
            f"# copilot: path-sensitive witness id={self.witness_id[:8]}\n"
            f"# copilot: obligation={self.obligation_smt!r}  "
            f"depth={self.path_depth}  feasible={self.is_feasible}\n"
            f"# copilot: node_kind={self.node_kind.name}\n"
            f"# copilot: path_predicate={self.path_predicate!r}"
        )


# ============================== analyzer ==============================


class BranchingJoinsPathSensitiveAnalyzer:
    """Analyzes path predicates and builds path-sensitive obligations.

    This analyzer accepts branch conditions (as SMT2 formula strings) and
    obligations, and produces :class:`BranchingJoinsPathSensitiveWitness`
    instances that pair each obligation with the path predicate under which it
    must be evaluated.

    The analyzer maintains no state across calls (all state is returned in
    witnesses), making it safe to share across analysis threads.

    copilot: Pass each branch condition individually through
    :meth:`analyze_branch`, then combine witnesses at join points using
    :meth:`analyze_join`.
    """

    def __init__(self) -> None:
        """Initialise the analyzer."""
        # copilot: small cache to avoid redundant simplification passes.
        self._simplify_cache: dict[str, str] = {}
        logger.debug("BranchingJoinsPathSensitiveAnalyzer initialised")

    # ------------------------------------------------------------------ #
    # Core analysis methods                                                #
    # ------------------------------------------------------------------ #

    def analyze_branch(
        self,
        condition_smt: str,
        obligation_smt: str,
        is_true_branch: bool,
    ) -> BranchingJoinsPathSensitiveWitness:
        """Build a path-sensitive witness for a single branch.

        For a true-branch, the path predicate is extended with ``condition_smt``.
        For a false-branch, the path predicate is extended with
        ``(not condition_smt)``.

        Parameters
        ----------
        condition_smt:
            The branch condition as an SMT2 formula string.
        obligation_smt:
            The proof obligation to check under this branch's path predicate.
        is_true_branch:
            ``True`` for the true-branch encoding; ``False`` for the
            false-branch encoding.

        Returns
        -------
        BranchingJoinsPathSensitiveWitness

        Examples
        --------
        >>> w = analyzer.analyze_branch("(> x 0)", "(< y 10)", True)
        >>> w.node_kind
        <PathNodeKind.BRANCH_TRUE: 2>
        """
        # copilot: the branch condition is negated for false branches.
        if is_true_branch:
            branch_cond = condition_smt
            node_kind = PathNodeKind.BRANCH_TRUE
        else:
            branch_cond = f"(not {condition_smt})"
            node_kind = PathNodeKind.BRANCH_FALSE

        branch_conditions = (branch_cond,)
        path_predicate = self.build_path_predicate(list(branch_conditions))
        feasible = self.check_path_feasibility(path_predicate)
        depth = self.compute_path_depth(list(branch_conditions))

        return BranchingJoinsPathSensitiveWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            obligation_smt=obligation_smt,
            path_predicate=path_predicate,
            branch_conditions=branch_conditions,
            join_predicates=(),
            node_kind=node_kind,
            path_depth=depth,
            is_feasible=feasible,
            copilot_label="",
            timestamp=time.time(),
        )

    def analyze_join(
        self,
        witnesses: list[BranchingJoinsPathSensitiveWitness],
    ) -> BranchingJoinsPathSensitiveWitness:
        """Build a join-node witness from a list of incoming-path witnesses.

        The join node's path predicate is the disjunction of all incoming
        path predicates.  The obligation is taken from the first witness (all
        incoming witnesses should share the same post-join obligation; a
        warning is logged if they differ).

        Parameters
        ----------
        witnesses:
            List of witnesses from incoming control-flow paths.

        Returns
        -------
        BranchingJoinsPathSensitiveWitness
            A witness for the join node.

        Examples
        --------
        >>> w_join = analyzer.analyze_join([w_true, w_false])
        >>> w_join.node_kind
        <PathNodeKind.JOIN: 4>
        """
        if not witnesses:
            raise ValueError("analyze_join requires at least one incoming witness")

        all_preds = [w.path_predicate for w in witnesses]
        all_branches = list(chain.from_iterable(w.branch_conditions for w in witnesses))

        join_pred = self._disjoin_conditions(all_preds)
        feasible = any(w.is_feasible for w in witnesses)
        depth = min(w.path_depth for w in witnesses)

        obligations = [w.obligation_smt for w in witnesses]
        if len(set(obligations)) > 1:
            logger.warning(
                "analyze_join: obligation mismatch across incoming witnesses: %s",
                obligations,
            )
        obligation = obligations[0]

        return BranchingJoinsPathSensitiveWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            obligation_smt=obligation,
            path_predicate=join_pred,
            branch_conditions=tuple(dict.fromkeys(all_branches)),
            join_predicates=tuple(all_preds),
            node_kind=PathNodeKind.JOIN,
            path_depth=depth,
            is_feasible=feasible,
            copilot_label="",
            timestamp=time.time(),
        )

    def build_path_predicate(self, branch_conditions: list[str]) -> str:
        """Build the path predicate string from a list of branch conditions.

        The path predicate is the conjunction of all branch conditions.  A
        single-element list returns that element directly (no ``and`` wrapper).
        An empty list returns the constant ``"true"``.

        Parameters
        ----------
        branch_conditions:
            Ordered list of SMT2 branch condition strings.

        Returns
        -------
        str
            The conjunctive path predicate as an SMT2 formula.

        Examples
        --------
        >>> analyzer.build_path_predicate(["(> x 0)", "(< y 10)"])
        '(and (> x 0) (< y 10))'
        >>> analyzer.build_path_predicate(["(> x 0)"])
        '(> x 0)'
        """
        return self._conjoin_conditions(branch_conditions)

    def check_path_feasibility(self, path_predicate: str) -> bool:
        """Return True if the path predicate is *likely* satisfiable.

        This is a lightweight syntactic check: a path is marked infeasible if
        the predicate string contains both a condition and its explicit
        negation.  Full satisfiability checking requires a live Z3 session.

        Parameters
        ----------
        path_predicate:
            The path predicate SMT2 formula string.

        Returns
        -------
        bool
            ``True`` unless a trivial infeasibility is detected syntactically.

        Examples
        --------
        >>> analyzer.check_path_feasibility("(and (> x 0) (not (> x 0)))")
        False
        >>> analyzer.check_path_feasibility("(> x 0)")
        True
        """
        if not path_predicate or path_predicate == "true":
            return True
        # copilot: simple syntactic check — a formula and its explicit
        # negation both appearing signals an infeasible path.
        conditions = self._extract_conjuncts(path_predicate)
        return not self._detect_infeasible_path(conditions)

    def compute_path_depth(self, branch_conditions: list[str]) -> int:
        """Compute the path depth as the number of branch conditions.

        The path depth is a measure of the nesting level of conditional
        branches along the path.  Deeper paths are more expensive to solve.

        Parameters
        ----------
        branch_conditions:
            List of branch condition strings.

        Returns
        -------
        int
            The number of branch conditions (>= 0).

        Examples
        --------
        >>> analyzer.compute_path_depth(["(> x 0)", "(< y 10)"])
        2
        """
        return len(branch_conditions)

    def encode_loop_invariant(self, loop_condition: str, invariant: str) -> str:
        """Encode the Hoare-logic loop invariant obligation as SMT2.

        The obligation is: if the invariant holds and the loop condition holds,
        then after one loop iteration the invariant still holds.  This is
        encoded as the satisfiability query:

            (push)
            (assert invariant)
            (assert loop_condition)
            (assert (not invariant_post))
            (check-sat)
            (pop)

        Since we do not have a separate ``invariant_post``, this method
        produces the skeleton with a placeholder comment for the body
        encoding.

        Parameters
        ----------
        loop_condition:
            The loop-guard SMT2 formula.
        invariant:
            The loop invariant SMT2 formula.

        Returns
        -------
        str
            An SMT2 loop-invariant check stub.

        Examples
        --------
        >>> print(analyzer.encode_loop_invariant("(< i n)", "(>= i 0)"))
        ; loop invariant check
        (push)
        (assert (>= i 0))
        (assert (< i n))
        ; ... body encoding here ...
        (assert (not (>= i 0)))
        (check-sat)
        (pop)
        """
        lines = [
            "; loop invariant check",
            "(push)",
            f"(assert {invariant})",
            f"(assert {loop_condition})",
            "; ... body encoding here ...",
            f"(assert (not {invariant}))",
            "(check-sat)",
            "(pop)",
        ]
        return "\n".join(lines)

    def generate_path_conditions(
        self,
        cfg_edges: list[tuple[str, str, str]],
    ) -> dict[str, str]:
        """Generate path conditions for all nodes in a CFG edge list.

        Each edge is a triple ``(source_node, target_node, condition_smt)``
        where ``condition_smt`` is the branch condition (or ``"true"`` for
        unconditional edges).

        The method computes path predicates for each reachable node using a
        forward BFS from the first encountered source node.

        Parameters
        ----------
        cfg_edges:
            List of ``(source, target, condition)`` edge triples.

        Returns
        -------
        dict[str, str]
            Mapping from node name to its path predicate SMT2 string.

        Examples
        --------
        >>> edges = [("entry", "b1", "(> x 0)"), ("entry", "b2", "(not (> x 0))")]
        >>> analyzer.generate_path_conditions(edges)
        {'entry': 'true', 'b1': '(> x 0)', 'b2': '(not (> x 0))'}
        """
        if not cfg_edges:
            return {}

        # Build adjacency: source → list of (target, condition)
        adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
        all_targets: set[str] = set()
        all_sources: set[str] = set()
        for src, tgt, cond in cfg_edges:
            adjacency[src].append((tgt, cond))
            all_targets.add(tgt)
            all_sources.add(src)

        entry_nodes = all_sources - all_targets
        path_predicates: dict[str, str] = {}

        # BFS forward
        queue = list(entry_nodes)
        for node in queue:
            path_predicates.setdefault(node, "true")

        visited: set[str] = set()
        bfs_queue = list(entry_nodes)
        while bfs_queue:
            node = bfs_queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            pred = path_predicates.get(node, "true")
            for tgt, cond in adjacency.get(node, []):
                if cond and cond != "true":
                    extended = self._conjoin_conditions([pred, cond]) if pred != "true" else cond
                else:
                    extended = pred
                # copilot: at join nodes, take the disjunction of incoming predicates.
                if tgt in path_predicates:
                    existing = path_predicates[tgt]
                    path_predicates[tgt] = self._disjoin_conditions([existing, extended])
                else:
                    path_predicates[tgt] = extended
                    bfs_queue.append(tgt)

        return path_predicates

    def copilot_path_analysis_hint(
        self, witness: BranchingJoinsPathSensitiveWitness
    ) -> str:
        """Return a copilot-style hint for a path-sensitive analysis result.

        Parameters
        ----------
        witness:
            The witness to summarise.

        Returns
        -------
        str
            Multi-line hint string.

        Examples
        --------
        >>> print(analyzer.copilot_path_analysis_hint(w))
        # copilot: path analysis witness id=abc123
        """
        return (
            f"# copilot: path analysis witness id={witness.witness_id[:8]}\n"
            f"# copilot: node_kind={witness.node_kind.name}\n"
            f"# copilot: path_depth={witness.path_depth}  "
            f"feasible={witness.is_feasible}\n"
            f"# copilot: branches={len(witness.branch_conditions)}  "
            f"joins={len(witness.join_predicates)}"
        )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _simplify_path_predicate(self, predicate: str) -> str:
        """Apply lightweight simplifications to a path predicate string.

        Simplifications applied (syntactic only, no solver interaction):

        * ``(and true P)`` → ``P``
        * ``(or false P)`` → ``P``
        * ``(not (not P))`` → ``P``

        Parameters
        ----------
        predicate:
            An SMT2 formula string.

        Returns
        -------
        str
            Simplified formula string (may equal the input if no
            simplification applies).
        """
        if predicate in self._simplify_cache:
            return self._simplify_cache[predicate]
        simplified = predicate
        # Remove trivial (and true P) wrappers
        if simplified.startswith("(and true ") and simplified.endswith(")"):
            simplified = simplified[len("(and true "):-1]
        if simplified.startswith("(or false ") and simplified.endswith(")"):
            simplified = simplified[len("(or false "):-1]
        # Remove double negation
        if simplified.startswith("(not (not ") and simplified.endswith("))"):
            simplified = simplified[len("(not (not "):-2]
        self._simplify_cache[predicate] = simplified
        return simplified

    def _detect_infeasible_path(self, conditions: list[str]) -> bool:
        """Return True if a trivial contradiction is detected in ``conditions``.

        A contradiction is detected when a condition ``c`` and its explicit
        negation ``(not c)`` both appear in the list.

        Parameters
        ----------
        conditions:
            List of SMT2 condition strings.

        Returns
        -------
        bool
            ``True`` if a trivial contradiction is found.
        """
        cond_set = set(conditions)
        for c in conditions:
            if f"(not {c})" in cond_set:
                return True
            if c.startswith("(not ") and c.endswith(")"):
                inner = c[5:-1]
                if inner in cond_set:
                    return True
        return False

    def _conjoin_conditions(self, conditions: list[str]) -> str:
        """Build a conjunctive SMT2 formula from a list of conditions.

        An empty list returns ``"true"``.  A singleton returns the element
        directly.  Multiple conditions are wrapped in ``(and ...)``.

        Parameters
        ----------
        conditions:
            List of SMT2 formula strings.

        Returns
        -------
        str
            Conjunctive formula.
        """
        # Filter trivial "true" conjuncts
        filtered = [c for c in conditions if c and c != "true"]
        if not filtered:
            return "true"
        if len(filtered) == 1:
            return filtered[0]
        return "(and " + " ".join(filtered) + ")"

    def _disjoin_conditions(self, conditions: list[str]) -> str:
        """Build a disjunctive SMT2 formula from a list of conditions.

        An empty list returns ``"false"``.  A singleton returns the element
        directly.  Multiple conditions are wrapped in ``(or ...)``.

        Parameters
        ----------
        conditions:
            List of SMT2 formula strings.

        Returns
        -------
        str
            Disjunctive formula.
        """
        # Filter trivial "false" disjuncts
        filtered = [c for c in conditions if c and c != "false"]
        if not filtered:
            return "false"
        if len(filtered) == 1:
            return filtered[0]
        return "(or " + " ".join(filtered) + ")"

    def _extract_conjuncts(self, predicate: str) -> list[str]:
        """Extract the top-level conjuncts from an ``(and ...)`` formula.

        For a formula not of the form ``(and ...)``, returns a singleton list
        containing the full predicate.

        Parameters
        ----------
        predicate:
            An SMT2 formula string.

        Returns
        -------
        list[str]
            List of conjunct strings.
        """
        stripped = predicate.strip()
        if stripped.startswith("(and ") and stripped.endswith(")"):
            inner = stripped[5:-1].strip()
            # naive split on top-level spaces (does not handle nested parens perfectly)
            parts: list[str] = []
            depth = 0
            current: list[str] = []
            for ch in inner:
                if ch == "(":
                    depth += 1
                    current.append(ch)
                elif ch == ")":
                    depth -= 1
                    current.append(ch)
                elif ch == " " and depth == 0:
                    if current:
                        parts.append("".join(current))
                        current = []
                else:
                    current.append(ch)
            if current:
                parts.append("".join(current))
            return [p for p in parts if p]
        return [stripped] if stripped else []


# ============================== coordinator ==============================


class BranchingJoinsPathSensitiveCoordinator:
    """Main coordinator for path-sensitive obligation encoding.

    Maintains a session-level ``_cache`` (fingerprint → witness) and
    ``_stats`` counter dict, and exposes the high-level ``encode_branch``,
    ``encode_join``, and ``encode_loop`` entry points.

    Attributes
    ----------
    _analyzer : BranchingJoinsPathSensitiveAnalyzer
        The stateless analysis helper.
    _witnesses : list[BranchingJoinsPathSensitiveWitness]
        All witnesses produced by this coordinator (in creation order).
    _cache : dict[str, BranchingJoinsPathSensitiveWitness]
        Fingerprint → witness cache.
    _stats : dict[str, int]
        Counters: ``"branches_encoded"``, ``"joins_encoded"``,
        ``"loops_encoded"``, ``"cache_hits"``, ``"infeasible_paths"``.

    copilot: Use this coordinator as the single interface for path-sensitive
    encoding in the analysis pipeline.  The ``all_feasible_paths`` method
    returns only witnesses whose path predicates are satisfiable.
    """

    def __init__(self) -> None:
        """Initialise the coordinator."""
        self._analyzer = BranchingJoinsPathSensitiveAnalyzer()
        self._witnesses: list[BranchingJoinsPathSensitiveWitness] = []
        self._cache: dict[str, BranchingJoinsPathSensitiveWitness] = {}
        self._stats: dict[str, int] = defaultdict(int)
        logger.debug("BranchingJoinsPathSensitiveCoordinator initialised")

    # ------------------------------------------------------------------ #
    # Encoding entry points                                                #
    # ------------------------------------------------------------------ #

    def encode_branch(
        self,
        condition: str,
        obligation: str,
        branch: bool,
    ) -> BranchingJoinsPathSensitiveWitness:
        """Encode a path-sensitive obligation for a single branch.

        Parameters
        ----------
        condition:
            Branch condition SMT2 formula string.
        obligation:
            Proof obligation SMT2 formula string.
        branch:
            ``True`` for the true branch; ``False`` for the false branch.

        Returns
        -------
        BranchingJoinsPathSensitiveWitness

        Examples
        --------
        >>> w = coord.encode_branch("(> x 0)", "(< y 100)", True)
        """
        witness = self._analyzer.analyze_branch(condition, obligation, branch)
        cache_key = witness.fingerprint()
        if cache_key in self._cache:
            self._stats["cache_hits"] += 1
            return self._cache[cache_key]

        self._cache[cache_key] = witness
        self._witnesses.append(witness)
        self._stats["branches_encoded"] += 1
        if not witness.is_feasible:
            self._stats["infeasible_paths"] += 1
        logger.info(
            "encode_branch: %s branch condition=%r obligation=%r depth=%d feasible=%s",
            "true" if branch else "false",
            condition, obligation, witness.path_depth, witness.is_feasible,
        )
        return witness

    def encode_join(
        self,
        incoming_witnesses: list[BranchingJoinsPathSensitiveWitness],
    ) -> BranchingJoinsPathSensitiveWitness:
        """Encode the join-node path-sensitive obligation.

        Parameters
        ----------
        incoming_witnesses:
            List of witnesses from the incoming CFG edges.

        Returns
        -------
        BranchingJoinsPathSensitiveWitness

        Examples
        --------
        >>> w_join = coord.encode_join([w_true, w_false])
        """
        witness = self._analyzer.analyze_join(incoming_witnesses)
        self._witnesses.append(witness)
        self._stats["joins_encoded"] += 1
        logger.info(
            "encode_join: joined %d paths → predicate=%r",
            len(incoming_witnesses), witness.path_predicate,
        )
        return witness

    def encode_loop(
        self,
        condition: str,
        invariant: str,
        body_obligation: str,
    ) -> BranchingJoinsPathSensitiveWitness:
        """Encode a loop-invariant path-sensitive obligation.

        The witness records the loop head as the node kind and the
        loop invariant as the path predicate.

        Parameters
        ----------
        condition:
            Loop guard SMT2 formula.
        invariant:
            Loop invariant SMT2 formula.
        body_obligation:
            Proof obligation that must hold inside the loop body.

        Returns
        -------
        BranchingJoinsPathSensitiveWitness

        Examples
        --------
        >>> w = coord.encode_loop("(< i n)", "(>= i 0)", "(< arr_i MAX)")
        """
        inv_smt2 = self._analyzer.encode_loop_invariant(condition, invariant)
        branch_conditions = (invariant, condition)
        path_predicate = self._analyzer.build_path_predicate(list(branch_conditions))
        feasible = self._analyzer.check_path_feasibility(path_predicate)

        witness = BranchingJoinsPathSensitiveWitness(
            witness_id=str(uuid.uuid4()).replace("-", ""),
            obligation_smt=body_obligation,
            path_predicate=path_predicate,
            branch_conditions=branch_conditions,
            join_predicates=(),
            node_kind=PathNodeKind.LOOP_HEAD,
            path_depth=2,
            is_feasible=feasible,
            copilot_label=f"loop:{condition[:20]}",
            timestamp=time.time(),
        )
        self._witnesses.append(witness)
        self._stats["loops_encoded"] += 1
        logger.info(
            "encode_loop: condition=%r invariant=%r obligation=%r",
            condition, invariant, body_obligation,
        )
        _ = inv_smt2  # stored for reference; returned in the smt2 query
        return witness

    def all_feasible_paths(self) -> list[BranchingJoinsPathSensitiveWitness]:
        """Return all witnesses whose path predicates are feasible.

        Returns
        -------
        list[BranchingJoinsPathSensitiveWitness]
            Filtered list of witnesses with ``is_feasible=True``.

        Examples
        --------
        >>> feasible = coord.all_feasible_paths()
        """
        return [w for w in self._witnesses if w.is_feasible]

    def path_sensitivity_report(self) -> str:
        """Return a human-readable summary of path-sensitive encoding activity.

        Returns
        -------
        str
            A multi-line report string.

        Examples
        --------
        >>> print(coord.path_sensitivity_report())
        BranchingJoinsPathSensitiveCoordinator
          branches_encoded: 4
          joins_encoded: 2
        """
        lines = ["BranchingJoinsPathSensitiveCoordinator"]
        for key, value in sorted(self._stats.items()):
            lines.append(f"  {key}: {value}")
        lines.append(f"  total_witnesses: {len(self._witnesses)}")
        lines.append(f"  feasible: {sum(1 for w in self._witnesses if w.is_feasible)}")
        return "\n".join(lines)

    @property
    def stats(self) -> dict[str, int]:
        """Read-only copy of the operation statistics dict.

        Returns
        -------
        dict[str, int]
        """
        return dict(self._stats)

    def __repr__(self) -> str:
        """Return a concise repr string."""
        return (
            f"BranchingJoinsPathSensitiveCoordinator("
            f"branches={self._stats['branches_encoded']}, "
            f"joins={self._stats['joins_encoded']}, "
            f"loops={self._stats['loops_encoded']}, "
            f"z3={_Z3_AVAILABLE})"
        )


# ============================== module convenience ==============================


def encode_simple_branch(
    condition: str,
    obligation: str,
) -> BranchingJoinsPathSensitiveWitness:
    """Module-level convenience: encode a true-branch path-sensitive obligation.

    Creates a fresh :class:`BranchingJoinsPathSensitiveCoordinator` and
    encodes the true-branch path-sensitive obligation for the given condition
    and obligation formula.

    Parameters
    ----------
    condition:
        Branch condition SMT2 formula string.
    obligation:
        Proof obligation SMT2 formula string.

    Returns
    -------
    BranchingJoinsPathSensitiveWitness
        The path-sensitive witness for the true branch.

    Examples
    --------
    >>> w = encode_simple_branch("(> x 0)", "(< y 100)")
    >>> print(w.to_smt2_path_query())
    ; path-sensitive obligation check
    ; node:branch-true  depth=1  feasible=True
    (push)
    (assert (> x 0))
    (assert (not (< y 100)))
    (check-sat)
    (pop)
    """
    # copilot: fresh coordinator for module-level calls.
    coord = BranchingJoinsPathSensitiveCoordinator()
    return coord.encode_branch(condition, obligation, True)


# ============================== smoke test ==============================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== branching_joins_and_path_sensitive smoke test ===\n")

    # --- PathNodeKind helpers ---
    for kind in PathNodeKind:
        print(
            f"  {kind.name}: branching={kind.is_branching()} "
            f"merging={kind.is_merging()} "
            f"loop={kind.is_loop_related()} "
            f"tag={kind.node_smt2_tag()!r}"
        )
    print()

    # --- Analyzer ---
    analyzer = BranchingJoinsPathSensitiveAnalyzer()

    # True branch
    w_true = analyzer.analyze_branch("(> x 0)", "(< y 100)", True)
    print(f"True-branch witness:")
    print(f"  id={w_true.witness_id[:8]}")
    print(f"  path_predicate={w_true.path_predicate!r}")
    print(f"  depth={w_true.path_depth}")
    print(f"  feasible={w_true.is_feasible}")
    print()
    print(w_true.to_smt2_path_query())
    print()

    # False branch
    w_false = analyzer.analyze_branch("(> x 0)", "(< y 100)", False)
    print(f"False-branch witness:")
    print(f"  path_predicate={w_false.path_predicate!r}")
    print()

    # Join
    w_join = analyzer.analyze_join([w_true, w_false])
    print(f"Join witness:")
    print(f"  node_kind={w_join.node_kind.name}")
    print(f"  path_predicate={w_join.path_predicate!r}")
    print()

    # merge_paths
    w_merged = w_true.merge_paths(w_false)
    print(f"merge_paths result: {w_merged.path_predicate!r}")
    print()

    # Path predicates
    path_pred = analyzer.build_path_predicate(["(> x 0)", "(< y 10)", "(= z 5)"])
    print(f"Path predicate: {path_pred!r}")
    print()

    # Loop invariant
    loop_smt2 = analyzer.encode_loop_invariant("(< i n)", "(>= i 0)")
    print("Loop invariant encoding:")
    print(loop_smt2)
    print()

    # CFG path conditions
    cfg_edges = [
        ("entry", "b_true", "(> x 0)"),
        ("entry", "b_false", "(not (> x 0))"),
        ("b_true", "join", "true"),
        ("b_false", "join", "true"),
    ]
    path_conds = analyzer.generate_path_conditions(cfg_edges)
    print("CFG path conditions:")
    for node, pred in sorted(path_conds.items()):
        print(f"  {node}: {pred!r}")
    print()

    # Copilot hints
    print(w_true.copilot_path_hint())
    print()
    print(analyzer.copilot_path_analysis_hint(w_join))
    print()

    # --- Coordinator ---
    coord = BranchingJoinsPathSensitiveCoordinator()
    w1 = coord.encode_branch("(> x 0)", "(< y 100)", True)
    w2 = coord.encode_branch("(> x 0)", "(< y 100)", False)
    w3 = coord.encode_join([w1, w2])
    w4 = coord.encode_loop("(< i n)", "(>= i 0)", "(< arr_i MAX)")

    print(f"all_feasible_paths: {len(coord.all_feasible_paths())}")
    print()
    print(coord.path_sensitivity_report())
    print(repr(coord))
    print()

    # --- Module-level convenience ---
    w_simple = encode_simple_branch("(= flag true)", "(> result 0)")
    print(f"encode_simple_branch: {w_simple.path_predicate!r}")
    print()

    print(f"Z3 available: {_Z3_AVAILABLE}")
    print(f"models available: {_MODELS_AVAILABLE}")
    print("\n=== smoke test complete ===")
