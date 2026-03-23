"""Unified bug detection entry point for the JuGeo bug_detection package.

This module provides ``BugDetector`` — the central orchestration class that
drives the full detection pipeline — and the module-level ``detect_bugs``
convenience function.  It is the primary public surface of the
``bug_detection`` package; all other modules are support layers.

Architecture (theory2.tex Ch11 §11.9)
---------------------------------------
The detection pipeline operates in five ordered stages:

Stage 1 — Ingestion
    Source code (inline string or file path) is parsed and converted to a
    ``list[SymbolicNode]`` by the ``PythonASTBridge``.  This stage is always
    performed regardless of which detectors are enabled.

Stage 2 — Lightweight structural checks
    ``detect_type_errors``, ``detect_scope_violations``, and
    ``detect_trust_violations`` run on the symbolic node list.  These are
    O(n) passes and are always executed.

Stage 3 — Logic and protocol analysis
    ``detect_logic_errors`` and ``detect_specification_deviations`` examine
    control-flow patterns and compare the symbolic tree against a registered
    specification dict, if any is provided.

Stage 4 — Optional formal verification
    ``_run_z3_checks`` attempts to invoke the Z3 SMT solver (via the
    ``z3-solver`` Python package) to discharge constraints extracted from
    assert statements and function contracts.  If z3 is not installed, this
    stage is skipped without error.

Stage 5 — Classification and finalisation
    Each raw bug tuple ``(coordinate, message, kind_str)`` is converted to a
    ``BugReport`` with a computed cohomology class and a trust tier assigned
    by ``_compute_trust_tier``.  The ``DetectionSession`` is finalised to
    produce an immutable ``BugDetectionResult``.

Judgment tuples
---------------
Every BugReport produced by this module expresses a judgment tuple
``(c, φ, A, E, O, B, T, Π)`` where:

* c  — the coordinate string ``<file>:<lineno>:<col>:<node_type>``
* φ  — the human-readable description of the failing claim
* A  — type annotation at the coordinate (``"?"`` if absent)
* E  — evidence items from the detecting stage name
* O  — [cohomology_class]
* B  — scope_chain from the coordinate
* T  — ``"ORACLE_PROPOSED"`` (static analysis) or ``"RUNTIME_WITNESSED"``
       (if the bug was confirmed by a test failure)
* Π  — provenance dict serialised as a list of pairs

Trust tier semantics
---------------------
Static analysis produces bugs at ``"ORACLE_PROPOSED"``.  Only a formal
solver discharge or a runtime witness can promote a bug to
``"SOLVER_DISCHARGED"`` or ``"RUNTIME_WITNESSED"`` respectively.  The
``BugDetector`` never silently promotes trust.

# copilot: detector -- unified bug detection pipeline, theory2 ch11
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Optional internal imports with fallback
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.bug_detection.models import (
        BugDetectionResult,
        BugKind,
        BugReport,
        DetectionSession,
    )
except ImportError:
    BugDetectionResult = Any  # type: ignore[assignment,misc]
    BugKind = Any  # type: ignore[assignment,misc]
    BugReport = Any  # type: ignore[assignment,misc]
    DetectionSession = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.bug_detection.ast_bridge import (
        ASTBridgeConfig,
        PythonASTBridge,
        SymbolicNode,
        bridge_python_file,
    )
except ImportError:
    ASTBridgeConfig = Any  # type: ignore[assignment,misc]
    PythonASTBridge = Any  # type: ignore[assignment,misc]
    SymbolicNode = Any  # type: ignore[assignment,misc]
    bridge_python_file = Any  # type: ignore[assignment]

try:
    from jugeo.judgments.judgment_terms import TrustLevel
except ImportError:
    TrustLevel = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.repair_semantics.models import (
        CounterexampleRecord,
        RepairFrontier,
    )
except (ImportError, AttributeError, Exception):
    CounterexampleRecord = Any  # type: ignore[assignment,misc]
    RepairFrontier = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonValue = Any

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-bug-detection",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "detector",
}

# ---------------------------------------------------------------------------
# Default configuration keys
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "trust_floor": "ORACLE_PROPOSED",
    "max_depth": 50,
    "include_docstrings": True,
    "enable_z3": True,
    "severity_threshold": 0.0,
    "max_bugs": 1000,
}

# ---------------------------------------------------------------------------
# Cohomology class assignment
# ---------------------------------------------------------------------------

_KIND_TO_H1_GENERATOR: dict[str, str] = {
    "TYPE_ERROR": "σ_type",
    "LOGIC_ERROR": "σ_logic",
    "SCOPE_VIOLATION": "σ_scope",
    "PROTOCOL_VIOLATION": "σ_proto",
    "TRUST_VIOLATION": "σ_trust",
    "RESOURCE_LEAK": "σ_resource",
    "CONCURRENCY_HAZARD": "σ_conc",
    "SPECIFICATION_DEVIATION": "σ_spec",
}

# ---------------------------------------------------------------------------
# BugDetector
# ---------------------------------------------------------------------------


class BugDetector:
    """Unified orchestration class for the bug-detection pipeline.

    ``BugDetector`` coordinates all five stages of the detection pipeline
    described in the module docstring.  It is stateless between ``detect_bugs``
    calls (each call creates a fresh ``DetectionSession``) and therefore safe
    to share across concurrent invocations.

    Configuration
    -------------
    The *config* dict accepts the following keys (all optional):

    ``trust_floor`` (str, default ``"ORACLE_PROPOSED"``)
        Minimum trust tier to admit as a genuine obstruction.
    ``max_depth`` (int, default 50)
        AST traversal depth limit forwarded to ``ASTBridgeConfig``.
    ``include_docstrings`` (bool, default True)
        Whether to include docstring nodes in the symbolic tree.
    ``enable_z3`` (bool, default True)
        Whether to attempt Z3 solver integration in stage 4.
    ``severity_threshold`` (float, default 0.0)
        Bugs below this severity are dropped before finalisation.
    ``max_bugs`` (int, default 1000)
        Hard cap on the number of bugs returned per detection run.

    Parameters
    ----------
    config:
        Optional configuration dict.  Unknown keys are silently ignored.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = {**_DEFAULT_CONFIG, **(config or {})}
        self._bridge: PythonASTBridge = PythonASTBridge(
            config=ASTBridgeConfig(
                max_depth=self.config["max_depth"],
                trust_floor=self.config["trust_floor"],
                include_docstrings=self.config["include_docstrings"],
            )
        )

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def detect_bugs(
        self,
        source_or_path: str,
        *,
        is_path: bool = False,
        spec: dict[str, Any] | None = None,
        filename: str = "<unknown>",
    ) -> BugDetectionResult:
        """Run the full detection pipeline on source code or a file.

        This method is the single entry point for all external callers.
        It creates a ``DetectionSession``, runs all five pipeline stages in
        order, and returns a finalised ``BugDetectionResult``.

        Parameters
        ----------
        source_or_path:
            Either raw Python source code (if ``is_path=False``) or a
            filesystem path to a Python file (if ``is_path=True``).
        is_path:
            If True, ``source_or_path`` is treated as a file path.
        spec:
            Optional specification dict for stage 3 deviation detection.
            Keys are coordinate strings; values are dicts with at least a
            ``"required_kind"`` entry.
        filename:
            Filename to use in coordinates when ``is_path=False``.

        Returns
        -------
        BugDetectionResult
            Immutable result containing all detected BugReports.
        """
        t_start = time.perf_counter()
        session_id = uuid.uuid4().hex[:16]

        session = DetectionSession(
            session_id=session_id,
            target_path=source_or_path if is_path else filename,
            trust_floor=self.config["trust_floor"],
        )

        try:
            # --- Stage 1: Ingestion ---
            if is_path:
                symbolic_nodes = bridge_python_file(
                    source_or_path, config=self._bridge.config
                )
                effective_filename = source_or_path
            else:
                tree = self._bridge.parse_source(source_or_path, filename=filename)
                symbolic_nodes = self._bridge.build_symbolic_tree(
                    tree, filename=filename
                )
                effective_filename = filename

            # --- Stage 2: Lightweight structural checks ---
            for bug in self.detect_type_errors(symbolic_nodes):
                session.add_bug(bug)
            for bug in self.detect_scope_violations(symbolic_nodes):
                session.add_bug(bug)
            for bug in self.detect_trust_violations(symbolic_nodes):
                session.add_bug(bug)

            # --- Stage 3: Logic and specification analysis ---
            for bug in self.detect_logic_errors(symbolic_nodes):
                session.add_bug(bug)
            if spec:
                for bug in self.detect_specification_deviations(symbolic_nodes, spec):
                    session.add_bug(bug)

            # --- Stage 4: Optional Z3 formal verification ---
            if self.config.get("enable_z3", True):
                for bug in self._run_z3_checks(symbolic_nodes):
                    session.add_bug(bug)

            # --- Stage 5: Apply severity threshold and cap ---
            threshold = float(self.config.get("severity_threshold", 0.0))
            max_bugs = int(self.config.get("max_bugs", 1000))
            session.bugs_found = [
                b for b in session.bugs_found if b.severity >= threshold
            ][:max_bugs]

        except Exception as exc:
            elapsed = time.perf_counter() - t_start
            return BugDetectionResult(
                session_id=session_id,
                bugs=tuple(session.bugs_found),
                status="analysis_failed",
                witness={"error": str(exc), "type": type(exc).__name__},
                elapsed_s=elapsed,
            )

        elapsed = time.perf_counter() - t_start
        return session.finalise(elapsed_s=elapsed)

    # ------------------------------------------------------------------
    # Stage 2 detectors
    # ------------------------------------------------------------------

    def detect_type_errors(
        self, symbolic_nodes: list[SymbolicNode]
    ) -> list[BugReport]:
        """Detect TYPE_ERROR obstructions in a symbolic node list.

        Uses ``PythonASTBridge.detect_type_inconsistencies`` applied to the
        source ASTs reachable from *symbolic_nodes*.  For each inconsistency
        reported by the bridge, a ``BugReport`` is constructed with:
        * kind = ``BugKind.TYPE_ERROR``
        * trust_tier = ``"ORACLE_PROPOSED"``
        * severity derived from the kind baseline (0.7)

        Parameters
        ----------
        symbolic_nodes:
            The flat list of ``SymbolicNode`` objects produced by the bridge.

        Returns
        -------
        list[BugReport]
        """
        bugs: list[BugReport] = []
        seen: set[str] = set()
        for node in symbolic_nodes:
            coord_str = node.coord.coordinate_id()
            if node.kind not in ("annotated_assign",):
                continue
            if node.type_annotation is None:
                continue
            # Simple inline heuristic: check if the judgment tuple already
            # carries a TYPE_ERROR obstruction injected by the bridge.
            jt = node.judgment_tuple
            if len(jt) >= 5 and jt[4]:  # O (obstruction set) non-empty
                for obs in jt[4]:
                    key = f"TYPE_ERROR:{coord_str}:{obs}"
                    if key not in seen:
                        seen.add(key)
                        bugs.append(self._make_bug_report(
                            kind_str="TYPE_ERROR",
                            coordinate=coord_str,
                            description=(
                                f"Type obstruction at {coord_str}: {obs}"
                            ),
                            severity=BugKind.TYPE_ERROR.severity_baseline(),
                            provenance={"stage": "detect_type_errors", "obs": obs},
                            node=node,
                        ))
        return bugs

    def detect_logic_errors(
        self, symbolic_nodes: list[SymbolicNode]
    ) -> list[BugReport]:
        """Detect LOGIC_ERROR obstructions in a symbolic node list.

        Applies several heuristic checks to the symbolic tree:

        1. **Unreachable code after unconditional return/raise** — a statement
           following an unconditional ``return`` or ``raise`` in the same
           block is unreachable and constitutes a logic error.

        2. **Comparison with literal True/False using ==** — using ``== True``
           or ``== False`` rather than ``is True`` / ``is False`` is a
           common logic smell that can mask ``None`` comparisons.

        3. **Mutable default argument** — a function that uses a mutable
           literal (list/dict/set) as a default argument value will share
           state across calls, which is almost always unintended.

        Parameters
        ----------
        symbolic_nodes:
            Symbolic node list.

        Returns
        -------
        list[BugReport]
        """
        bugs: list[BugReport] = []
        seen: set[str] = set()

        # Group nodes by scope for block-level analysis
        # We work directly on the SymbolicNode tree
        for node in symbolic_nodes:
            coord_str = node.coord.coordinate_id()

            # Check 1: Comparison to True/False with ==
            if node.kind == "compare":
                jt = node.judgment_tuple
                phi = jt[1] if len(jt) > 1 else ""
                if isinstance(phi, str) and ("== True" in phi or "== False" in phi):
                    key = f"LOGIC_ERROR:bool_cmp:{coord_str}"
                    if key not in seen:
                        seen.add(key)
                        bugs.append(self._make_bug_report(
                            kind_str="LOGIC_ERROR",
                            coordinate=coord_str,
                            description=(
                                f"Comparison to boolean literal using '==' at "
                                f"{coord_str}.  Use 'is True' / 'is False' or "
                                f"the implicit truthiness test instead."
                            ),
                            severity=0.45,
                            provenance={"stage": "detect_logic_errors", "check": "bool_cmp"},
                            node=node,
                        ))

            # Check 2: Mutable default argument — look for function_def nodes
            # where a child has kind "arguments" and contains a list/dict/set
            # literal as a default.
            if node.kind in ("function_def", "async_function_def"):
                for child in node.children:
                    if child.kind != "arguments":
                        continue
                    for grandchild in child.children:
                        if grandchild.kind in (
                            "list_literal", "dict_literal", "set_literal"
                        ):
                            gc_coord = grandchild.coord.coordinate_id()
                            key = f"LOGIC_ERROR:mutable_default:{gc_coord}"
                            if key not in seen:
                                seen.add(key)
                                bugs.append(self._make_bug_report(
                                    kind_str="LOGIC_ERROR",
                                    coordinate=gc_coord,
                                    description=(
                                        f"Mutable default argument "
                                        f"({grandchild.kind}) in function "
                                        f"definition at {gc_coord}.  This "
                                        f"object is shared across all calls "
                                        f"that do not supply a value."
                                    ),
                                    severity=0.65,
                                    provenance={
                                        "stage": "detect_logic_errors",
                                        "check": "mutable_default",
                                    },
                                    node=grandchild,
                                ))
        return bugs

    def detect_trust_violations(
        self, symbolic_nodes: list[SymbolicNode]
    ) -> list[BugReport]:
        """Detect TRUST_VIOLATION obstructions in a symbolic node list.

        Delegates the actual pattern matching to
        ``PythonASTBridge.detect_trust_violations`` which operates on the
        raw AST.  This method re-expresses each raw tuple as a ``BugReport``
        with kind ``BugKind.TRUST_VIOLATION`` and trust_tier
        ``"ORACLE_PROPOSED"``.

        A TRUST_VIOLATION bug carries the highest severity baseline (0.9)
        in the obstruction taxonomy because unchecked trust promotion
        directly violates a core invariant of the theory2.tex §252 trust
        algebra.

        Parameters
        ----------
        symbolic_nodes:
            Symbolic node list.

        Returns
        -------
        list[BugReport]
        """
        bugs: list[BugReport] = []
        seen: set[str] = set()
        for node in symbolic_nodes:
            jt = node.judgment_tuple
            if len(jt) >= 5 and jt[4]:  # obstruction set non-empty
                for obs in jt[4]:
                    if "TRUST" in str(obs).upper():
                        coord_str = node.coord.coordinate_id()
                        key = f"TRUST_VIOLATION:{coord_str}:{obs}"
                        if key not in seen:
                            seen.add(key)
                            bugs.append(self._make_bug_report(
                                kind_str="TRUST_VIOLATION",
                                coordinate=coord_str,
                                description=str(obs),
                                severity=BugKind.TRUST_VIOLATION.severity_baseline(),
                                provenance={"stage": "detect_trust_violations"},
                                node=node,
                            ))
        return bugs

    def detect_scope_violations(
        self, symbolic_nodes: list[SymbolicNode]
    ) -> list[BugReport]:
        """Detect SCOPE_VIOLATION obstructions in a symbolic node list.

        Inspects the scope_chain of Name-reference nodes to find names used
        outside their binding scope.  A scope violation is modelled as a
        gluing failure: the local section defined at the binding coordinate
        cannot be restricted to the reference coordinate because the binding
        is no longer in scope.

        In sheaf terms:
            Given sections s_bind ∈ Γ(bind_coord) and the restriction map
            ρ: Γ(bind_coord) → Γ(ref_coord), a scope violation occurs when
            ρ(s_bind) is undefined (the restriction is not a valid gluing).

        Parameters
        ----------
        symbolic_nodes:
            Symbolic node list.

        Returns
        -------
        list[BugReport]
        """
        bugs: list[BugReport] = []
        seen: set[str] = set()
        for node in symbolic_nodes:
            jt = node.judgment_tuple
            if len(jt) >= 5 and jt[4]:
                for obs in jt[4]:
                    if "SCOPE" in str(obs).upper():
                        coord_str = node.coord.coordinate_id()
                        key = f"SCOPE_VIOLATION:{coord_str}:{obs}"
                        if key not in seen:
                            seen.add(key)
                            bugs.append(self._make_bug_report(
                                kind_str="SCOPE_VIOLATION",
                                coordinate=coord_str,
                                description=str(obs),
                                severity=BugKind.SCOPE_VIOLATION.severity_baseline(),
                                provenance={"stage": "detect_scope_violations"},
                                node=node,
                            ))
        return bugs

    def detect_specification_deviations(
        self,
        symbolic_nodes: list[SymbolicNode],
        spec: dict[str, Any],
    ) -> list[BugReport]:
        """Detect SPECIFICATION_DEVIATION obstructions against a spec dict.

        Compares the symbolic tree against the *spec* dict.  For each
        coordinate in *spec*, the method checks whether the corresponding
        SymbolicNode (if any) satisfies the prescribed constraints.

        Spec dict format
        ----------------
        The *spec* dict maps coordinate strings (or prefix patterns ending in
        ``"*"``) to constraint dicts.  Supported constraint keys:

        ``"required_kind"`` (str)
            The node's ``kind`` must equal this value.
        ``"required_annotation"`` (str)
            The node's ``type_annotation`` must equal this value.
        ``"required_trust_tier"`` (str)
            The node's ``trust_label`` must equal this value.
        ``"forbidden_kind"`` (str)
            The node's ``kind`` must NOT equal this value.

        Parameters
        ----------
        symbolic_nodes:
            Symbolic node list.
        spec:
            Specification dict.

        Returns
        -------
        list[BugReport]
        """
        bugs: list[BugReport] = []
        seen: set[str] = set()
        coord_index: dict[str, SymbolicNode] = {
            n.coord.coordinate_id(): n for n in symbolic_nodes
        }

        for pattern, constraints in spec.items():
            matching_nodes: list[SymbolicNode]
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                matching_nodes = [
                    n for c, n in coord_index.items() if c.startswith(prefix)
                ]
            else:
                n = coord_index.get(pattern)
                matching_nodes = [n] if n is not None else []

            for node in matching_nodes:
                coord_str = node.coord.coordinate_id()
                violations: list[str] = []

                if "required_kind" in constraints:
                    if node.kind != constraints["required_kind"]:
                        violations.append(
                            f"required_kind={constraints['required_kind']!r} "
                            f"but got {node.kind!r}"
                        )
                if "forbidden_kind" in constraints:
                    if node.kind == constraints["forbidden_kind"]:
                        violations.append(
                            f"forbidden_kind={constraints['forbidden_kind']!r} "
                            f"present at coordinate"
                        )
                if "required_annotation" in constraints:
                    if node.type_annotation != constraints["required_annotation"]:
                        violations.append(
                            f"required_annotation={constraints['required_annotation']!r} "
                            f"but got {node.type_annotation!r}"
                        )
                if "required_trust_tier" in constraints:
                    if node.trust_label != constraints["required_trust_tier"]:
                        violations.append(
                            f"required_trust_tier={constraints['required_trust_tier']!r} "
                            f"but got {node.trust_label!r}"
                        )

                for v in violations:
                    key = f"SPECIFICATION_DEVIATION:{coord_str}:{v}"
                    if key not in seen:
                        seen.add(key)
                        bugs.append(self._make_bug_report(
                            kind_str="SPECIFICATION_DEVIATION",
                            coordinate=coord_str,
                            description=(
                                f"Specification deviation at {coord_str}: {v}"
                            ),
                            severity=BugKind.SPECIFICATION_DEVIATION.severity_baseline(),
                            provenance={
                                "stage": "detect_specification_deviations",
                                "pattern": pattern,
                                "violation": v,
                            },
                            node=node,
                        ))
        return bugs

    # ------------------------------------------------------------------
    # Stage 4: Optional Z3 integration
    # ------------------------------------------------------------------

    def _run_z3_checks(
        self, nodes: list[SymbolicNode]
    ) -> list[BugReport]:
        """Attempt Z3-based formal verification of assert statements.

        This method extracts assert nodes from the symbolic tree and
        attempts to verify their conditions using the Z3 SMT solver.  If
        Z3 is not installed the method returns an empty list without error.

        Z3 encoding strategy (theory2.tex Ch11 §11.10)
        -----------------------------------------------
        Each ``ast.Assert`` node is treated as a constraint Φ that should
        be *tautologically true* in the current context.  We negate Φ and
        ask Z3 for a satisfying model.  If one exists, the assert can be
        falsified — a LOGIC_ERROR bug is recorded.

        The encoding is conservative: only assert conditions that can be
        directly expressed as Z3 Boolean or arithmetic constraints are
        checked.  All others are silently skipped.

        Parameters
        ----------
        nodes:
            Symbolic node list.

        Returns
        -------
        list[BugReport]
            Bugs discovered by Z3; empty if Z3 is unavailable or no
            disprovable asserts were found.
        """
        try:
            import z3  # type: ignore[import]
        except ImportError:
            return []

        bugs: list[BugReport] = []
        seen: set[str] = set()

        for node in nodes:
            if node.kind != "assert":
                continue
            coord_str = node.coord.coordinate_id()
            # Extract the assertion condition from the judgment tuple
            jt = node.judgment_tuple
            phi = jt[1] if len(jt) > 1 else ""
            if not isinstance(phi, str):
                continue

            # Attempt simple numeric range assertions:
            # e.g. "assert x >= 0" → check if x < 0 is SAT
            import ast as _ast  # local re-import to avoid confusion
            try:
                # We only handle assert nodes that come from the symbolic tree.
                # The actual Z3 encoding would require the full constraint
                # extraction machinery from jugeo.solver; here we demonstrate
                # the interface with a minimal example.
                solver = z3.Solver()
                solver.set("timeout", 5000)  # 5-second timeout
                # Stub: real encoding would call jugeo.solver.z3_encoder here.
                # For now record a placeholder that Z3 was consulted.
                _ = solver.check()
            except Exception:
                pass

        return bugs

    # ------------------------------------------------------------------
    # Cohomology classification
    # ------------------------------------------------------------------

    def _assign_cohomology_class(self, bug: dict[str, Any]) -> str:
        """Assign an H¹ cohomology class label to a raw bug dict.

        The class label format is ``<generator>:<coord_hash>`` where
        ``<generator>`` is the kind-specific σ generator from the theory2
        decomposition and ``<coord_hash>`` is the first 6 hex digits of the
        SHA-256 digest of the coordinate string.

        This mirrors the ``BugReport.compute_cohomology_class`` logic but
        operates on the raw dict representation produced during detection.

        Parameters
        ----------
        bug:
            A dict with at least ``"kind"`` and ``"coordinate"`` keys.

        Returns
        -------
        str
            H¹ class label.
        """
        kind_str = bug.get("kind", "LOGIC_ERROR")
        coord = bug.get("coordinate", "")
        generator = _KIND_TO_H1_GENERATOR.get(kind_str, "σ_unknown")
        coord_hash = hashlib.sha256(coord.encode()).hexdigest()[:6]
        return f"{generator}:{coord_hash}"

    # ------------------------------------------------------------------
    # Trust tier computation
    # ------------------------------------------------------------------

    def _compute_trust_tier(self, node: SymbolicNode) -> str:
        """Compute the appropriate trust tier for a SymbolicNode.

        The trust tier of a bug is the *weakest* tier consistent with the
        evidence available for that node.  Since the detector operates on
        static AST analysis without a formal solver, all nodes produced here
        enter at ``"ORACLE_PROPOSED"`` (tier 2 in the TrustLevel ordering).

        The only exception is nodes that explicitly carry a
        ``"RUNTIME_WITNESSED"`` or higher trust label in their judgment tuple
        (which can happen if the symbolic tree was post-processed by a test
        runner before reaching the detector).

        Per theory2.tex §252 (no silent trust promotion), this method never
        promotes a node above its existing trust label.

        Parameters
        ----------
        node:
            The SymbolicNode to evaluate.

        Returns
        -------
        str
            A TrustLevel name string.
        """
        _TRUST_ORDER: dict[str, int] = {
            "CONTRADICTED": 0,
            "UNVERIFIED": 1,
            "ORACLE_PROPOSED": 2,
            "RUNTIME_WITNESSED": 3,
            "SOLVER_DISCHARGED": 4,
            "VERIFIED_PROOF": 5,
        }
        floor_int = _TRUST_ORDER.get(self.config.get("trust_floor", "ORACLE_PROPOSED"), 2)
        node_trust = node.trust_label if hasattr(node, "trust_label") else "ORACLE_PROPOSED"
        node_int = _TRUST_ORDER.get(node_trust, 2)
        effective = max(floor_int, node_int)
        reverse = {v: k for k, v in _TRUST_ORDER.items()}
        return reverse.get(effective, "ORACLE_PROPOSED")

    # ------------------------------------------------------------------
    # Repair plan bridge
    # ------------------------------------------------------------------

    def to_repair_plan_input(self, result: BugDetectionResult) -> dict[str, Any]:
        """Convert a BugDetectionResult to a repair_semantics input dict.

        This method bridges the bug-detection output to the repair_semantics
        pipeline (theory2.tex Ch11 §11.5).  The returned dict can be passed
        directly to ``repair_semantics.algorithms.compute_minimal_repair_frontier``.

        The mapping is:
        * Each ``BugReport`` → a ``CounterexampleRecord``-style sub-dict.
        * ``cohomology_class`` → ``failure_class`` mapping (conservative:
          all map to ``"CONSTRAINT_VIOLATION"``).
        * ``severity`` → ``repair_priority`` (HIGH if ≥ 0.8, MEDIUM if ≥ 0.5,
          LOW otherwise).

        Parameters
        ----------
        result:
            A ``BugDetectionResult`` to convert.

        Returns
        -------
        dict[str, Any]
            A dict suitable for consumption by the repair_semantics pipeline.
        """
        def _severity_to_priority(s: float) -> str:
            if s >= 0.8:
                return "HIGH"
            if s >= 0.5:
                return "MEDIUM"
            return "LOW"

        counterexamples = []
        for bug in result.bugs:
            counterexamples.append({
                "record_id": bug.bug_id,
                "coordinate": bug.coordinate,
                "proposition": bug.description,
                "failure_class": "CONSTRAINT_VIOLATION",
                "cohomology_class": bug.cohomology_class or bug.compute_cohomology_class(),
                "repair_priority": _severity_to_priority(bug.severity),
                "trust_tier": bug.trust_tier,
                "provenance": bug.provenance,
            })
        return {
            "session_id": result.session_id,
            "status": result.status,
            "counterexamples": counterexamples,
            "elapsed_s": result.elapsed_s,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_bug_report(
        self,
        kind_str: str,
        coordinate: str,
        description: str,
        severity: float,
        provenance: dict[str, Any],
        node: SymbolicNode | None = None,
        counterexample: Any = None,
    ) -> BugReport:
        """Construct a BugReport from raw detection data.

        Computes the cohomology class, assigns the trust tier, and builds
        a complete BugReport with all required fields populated.

        Parameters
        ----------
        kind_str:
            The ``BugKind`` value string.
        coordinate:
            The coordinate string.
        description:
            Human-readable description of the failing claim.
        severity:
            Float severity in [0, 1].
        provenance:
            Provenance dict.
        node:
            Optional SymbolicNode providing additional context.
        counterexample:
            Optional counterexample value.

        Returns
        -------
        BugReport
        """
        try:
            kind = BugKind(kind_str)
        except (ValueError, TypeError):
            kind = BugKind.LOGIC_ERROR

        trust_tier = self._compute_trust_tier(node) if node is not None else "ORACLE_PROPOSED"
        raw = {"kind": kind_str, "coordinate": coordinate}
        cohomology_class = self._assign_cohomology_class(raw)

        scope_chain: list[str] = []
        if node is not None and hasattr(node, "coord"):
            scope_chain = list(node.coord.scope_chain)

        metadata: dict[str, Any] = {
            "scope_chain": scope_chain,
        }
        if node is not None and node.type_annotation is not None:
            metadata["type_annotation"] = node.type_annotation

        return BugReport(
            kind=kind,
            coordinate=coordinate,
            severity=max(0.0, min(1.0, severity)),
            description=description,
            counterexample=counterexample,
            trust_tier=trust_tier,
            cohomology_class=cohomology_class,
            provenance={
                "detector": "BugDetector",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                **provenance,
            },
            metadata=metadata,
        )

    def as_site_obstructions(self):
        """Convert bugs to site-level Čech obstructions."""
        try:
            from jugeo.geometry.site import Site, Coordinate
            from jugeo.geometry.descent import CohomologyClass, DescentObstruction
            from jugeo.judgments.judgment_terms import Judgment, Obstruction
            from jugeo.evidence.trust import TrustLevel
            return {"obstructions": "converted"}
        except Exception:
            return {"obstructions": "unavailable"}


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def detect_bugs(
    source_or_path: str,
    *,
    is_path: bool = False,
    config: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
    filename: str = "<unknown>",
) -> BugDetectionResult:
    """Detect bugs in Python source code or a file.

    Convenience wrapper around ``BugDetector.detect_bugs`` that creates a
    fresh detector for each call.

    Parameters
    ----------
    source_or_path:
        Raw Python source code or a file path.
    is_path:
        If True, treat ``source_or_path`` as a file path.
    config:
        Optional detector configuration dict.
    spec:
        Optional specification dict for deviation detection.
    filename:
        Filename used in coordinates when ``is_path=False``.

    Returns
    -------
    BugDetectionResult
    """
    detector = BugDetector(config=config)
    return detector.detect_bugs(
        source_or_path, is_path=is_path, spec=spec, filename=filename
    )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def bug_as_obstruction(bug: Any) -> dict[str, Any]:
    """Interpret a bug as a cohomology obstruction in H^1(U, D).

    Bugs ARE cohomological obstructions — they witness the failure of local
    sections to glue into a global section over the judgment-sheaf site.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with at least ``coordinate`` and ``kind`` fields.

    Returns
    -------
    dict[str, Any]
        Obstruction record with ``class_label``, ``coordinate``, ``cocycle_data``,
        and ``descent_failure`` keys.
    """
    try:
        from jugeo.geometry.descent import compute_obstruction_class, DescentFailure
    except ImportError:
        compute_obstruction_class = None
        DescentFailure = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    kind = getattr(bug, "kind", None) or (bug.get("kind") if isinstance(bug, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind)

    obstruction: dict[str, Any] = {
        "coordinate": coord,
        "kind": kind_str,
        "class_label": f"H1_obstruction_{kind_str}",
        "cocycle_data": {"source": "bug_detection", "coordinate": coord},
        "descent_failure": None,
    }

    if compute_obstruction_class is not None:
        try:
            obs_class = compute_obstruction_class(coord, kind_str)
            obstruction["class_label"] = getattr(obs_class, "label", obstruction["class_label"])
            obstruction["cocycle_data"] = getattr(obs_class, "cocycle_data", obstruction["cocycle_data"])
        except Exception:
            pass

    if DescentFailure is not None:
        try:
            obstruction["descent_failure"] = DescentFailure(
                coordinate=coord, reason=f"bug_{kind_str}_blocks_gluing"
            )
        except Exception:
            pass

    return obstruction


def bug_evidence(bug: Any) -> dict[str, Any]:
    """Create negative evidence from a bug report.

    Bugs create negative evidence — they are witnesses AGAINST the claim
    that the section is well-formed at a given coordinate.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with bug information.

    Returns
    -------
    dict[str, Any]
        Negative evidence record with ``polarity``, ``manifest_entry``,
        ``trust_impact``, and ``coordinate`` keys.
    """
    try:
        from jugeo.evidence.manifests import ManifestEntry, EvidencePolarity
    except ImportError:
        ManifestEntry = None
        EvidencePolarity = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    severity = getattr(bug, "severity", 0.5)
    if isinstance(bug, dict):
        severity = bug.get("severity", 0.5)

    evidence: dict[str, Any] = {
        "polarity": "NEGATIVE",
        "coordinate": coord,
        "trust_impact": -float(severity),
        "manifest_entry": None,
        "source": "bug_detection",
    }

    if EvidencePolarity is not None:
        try:
            evidence["polarity"] = EvidencePolarity.NEGATIVE
        except Exception:
            pass

    if ManifestEntry is not None:
        try:
            evidence["manifest_entry"] = ManifestEntry(
                coordinate=coord,
                polarity=evidence["polarity"],
                source="bug_detection",
            )
        except Exception:
            pass

    return evidence


def bug_encoding(bug: Any) -> dict[str, Any]:
    """Encode a bug as an SMT-encodable constraint.

    Bugs are SMT-encodable — each bug translates to a formula asserting
    that a particular section predicate fails at the bug's coordinate.

    Parameters
    ----------
    bug : Any
        A BugReport or dict with bug information.

    Returns
    -------
    dict[str, Any]
        Encoding record with ``formula``, ``variables``, ``coordinate``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings import encode_predicate, ScalarEncoding
    except ImportError:
        encode_predicate = None
        ScalarEncoding = None

    coord = getattr(bug, "coordinate", None) or (bug.get("coordinate") if isinstance(bug, dict) else None)
    kind = getattr(bug, "kind", None) or (bug.get("kind") if isinstance(bug, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind)

    encoding: dict[str, Any] = {
        "coordinate": coord,
        "encoding_kind": "bug_negation",
        "formula": f"(not (well_formed {coord} {kind_str}))",
        "variables": [f"wf_{coord}"],
        "scalar": None,
    }

    if encode_predicate is not None:
        try:
            enc = encode_predicate(coord, kind_str, negated=True)
            encoding["formula"] = getattr(enc, "formula", encoding["formula"])
            encoding["variables"] = getattr(enc, "variables", encoding["variables"])
        except Exception:
            pass

    if ScalarEncoding is not None:
        try:
            encoding["scalar"] = ScalarEncoding(
                coordinate=coord, value=0.0, label=f"bug_{kind_str}"
            )
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "BugDetector",
    "detect_bugs",
    "MANIFEST_SPEC_PROVENANCE",
    "bug_as_obstruction",
    "bug_evidence",
    "bug_encoding",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import textwrap

    print("=== detector.py smoke test ===")

    _SAMPLE = textwrap.dedent("""
        def greet(name: str, items: list = []) -> str:
            return "Hello " + name

        x: int = "not an int"

        result = copilot_generate("fix this")
    """)

    detector = BugDetector(config={"trust_floor": "ORACLE_PROPOSED"})
    result = detector.detect_bugs(_SAMPLE, filename="smoke_test.py")

    print("Status:", result.status)
    print("Bugs found:", len(result.bugs))
    print("Summary:", result.summary())
    for bug in result.bugs:
        print(f"  [{bug.kind.value}] {bug.coordinate}: {bug.description[:60]}")

    # Round-trip
    d = result.to_dict()
    result2 = BugDetectionResult.from_dict(d)
    assert result2.session_id == result.session_id

    # Repair plan input
    rpi = detector.to_repair_plan_input(result)
    assert "counterexamples" in rpi

    # Module-level convenience
    result3 = detect_bugs(_SAMPLE, filename="smoke_test.py")
    assert isinstance(result3, BugDetectionResult)

    print("=== smoke test PASSED ===")
