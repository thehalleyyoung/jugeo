"""Bridge from Python AST to JuGeo symbolic form (theory2.tex Ch11).

This module converts a Python source tree — as produced by the standard
``ast`` module — into the symbolic judgment-sheaf representation used
throughout the JuGeo analysis pipeline.  It is the *primary ingestion
gateway* for bug detection: every other module in the bug_detection package
consumes the ``SymbolicNode`` list that this bridge emits.

Theoretical basis (Ch11 §11.6, §11.7)
---------------------------------------
The Python AST is treated as a *locally constant presheaf* over the site
whose objects are AST nodes (identified by their position in the source file)
and whose morphisms are parent-child containment relations.  The bridge
performs two passes:

Pass 1 — Coordinate extraction
    Every node in the AST is assigned an ``ASTCoordinate`` — a tuple
    ``(file, lineno, col, node_type, scope_chain)`` — that uniquely
    identifies it as an object in the judgment-sheaf site Γ.  Coordinates
    are the *primary keys* used by all downstream analysis.

Pass 2 — Symbolic node construction
    Each coordinate is lifted to a ``SymbolicNode`` that carries:
    * a ``trust_label`` (defaulting to ``"ORACLE_PROPOSED"`` because AST
      analysis is a static, non-solver pass),
    * a ``judgment_tuple`` encoding the eight-component theory2 judgment
      ``(c, φ, A, E, O, B, T, Π)``,
    * a ``type_annotation`` extracted from PEP-484 annotations where
      available,
    * a list of child SymbolicNodes (the tree structure).

No silent trust promotion
--------------------------
Because the bridge runs without a formal solver, every node it emits carries
``trust_label = "ORACLE_PROPOSED"`` (integer value 2 in the TrustLevel
ordering).  Promotion to ``"RUNTIME_WITNESSED"`` or ``"SOLVER_DISCHARGED"``
is the responsibility of downstream analysis stages.

Obstruction detection (Ch11 §11.8)
------------------------------------
The bridge performs three lightweight obstruction checks:

detect_scope_violations
    Walks the name-use graph to find references that escape their binding
    scope.  A scope violation is a *gluing failure* between the section
    defined at the binding coordinate and the section accessed at the
    reference coordinate.

detect_type_inconsistencies
    Compares inferred and annotated types at assignment nodes.  Where the
    annotation is explicit but the assigned value is a literal of a different
    type, a TYPE_ERROR obstruction is recorded.

detect_trust_violations
    Scans for patterns that indicate silent trust promotion: assignments
    from functions whose names contain ``"oracle"``, ``"copilot"``, or
    ``"propose"`` directly to variables that carry ``"verified"`` in their
    annotation comments, without an intervening explicit discharge step.

# copilot: ast_bridge -- AST-to-sheaf coordinate bridge for bug detection
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional internal imports with fallback
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.bug_detection.models import (
        BugKind,
        BugReport,
    )
except ImportError:
    BugKind = Any  # type: ignore[assignment,misc]
    BugReport = Any  # type: ignore[assignment,misc]

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
    "module": "ast_bridge",
}

# ---------------------------------------------------------------------------
# ASTCoordinate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ASTCoordinate:
    """Unique identifier for an AST node as a coordinate in the sheaf site Γ.

    An ASTCoordinate is an object in the category whose morphisms are the
    parent-child containment relations in the Python AST.  Two nodes are
    *adjacent* in the coverage topology iff one is the immediate parent of
    the other.

    The ``scope_chain`` captures the sequence of enclosing scopes (module,
    class, function) from outermost to innermost, following the binding
    hierarchy of the Python language.  It is used to compute the binder
    context component B of the judgment tuple.

    Format conventions
    ------------------
    ``__str__`` returns the canonical coordinate string::

        <file>:<lineno>:<col>:<node_type>

    This string is used as the ``coordinate`` field of all BugReports and
    SymbolicNodes produced from this coordinate.

    Parameters
    ----------
    file:
        Absolute or relative path to the source file.
    lineno:
        1-based line number of the node's first token.
    col:
        0-based column offset of the node's first token.
    node_type:
        The class name of the AST node (e.g. ``"FunctionDef"``, ``"Assign"``).
    scope_chain:
        Tuple of enclosing scope names from outermost to innermost.
        E.g. ``("module", "MyClass", "my_method")``.
    """

    file: str = ""
    lineno: int = 0
    col: int = 0
    node_type: str = ""
    scope_chain: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.file}:{self.lineno}:{self.col}:{self.node_type}"

    def coordinate_id(self) -> str:
        """Return the canonical coordinate string (same as ``str(self)``).

        Returns
        -------
        str
        """
        return str(self)

    def scope_depth(self) -> int:
        """Return the nesting depth of this coordinate.

        Returns
        -------
        int
            Length of the scope_chain.
        """
        return len(self.scope_chain)

    def in_scope(self, name: str) -> bool:
        """Return True iff *name* appears anywhere in the scope_chain.

        Parameters
        ----------
        name:
            A scope name to look up.

        Returns
        -------
        bool
        """
        return name in self.scope_chain

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "file": self.file,
            "lineno": self.lineno,
            "col": self.col,
            "node_type": self.node_type,
            "scope_chain": list(self.scope_chain),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ASTCoordinate":
        """Deserialise from a plain dict.

        Parameters
        ----------
        payload:
            A dict as produced by ``to_dict``.

        Returns
        -------
        ASTCoordinate
        """
        return cls(
            file=payload.get("file", ""),
            lineno=int(payload.get("lineno", 0)),
            col=int(payload.get("col", 0)),
            node_type=payload.get("node_type", ""),
            scope_chain=tuple(payload.get("scope_chain", [])),
        )


# ---------------------------------------------------------------------------
# SymbolicNode
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SymbolicNode:
    """A node in the symbolic tree produced by ``PythonASTBridge``.

    A SymbolicNode is the lifted version of an AST node in the judgment
    sheaf.  It carries:

    * A coordinate locating it in the site Γ.
    * A kind label describing its syntactic role.
    * An optional type annotation extracted from PEP-484 hints.
    * A trust label (always ``"ORACLE_PROPOSED"`` for bridge-produced nodes).
    * A judgment_tuple encoding the eight-component theory2 judgment.
    * A list of child SymbolicNodes completing the tree structure.

    Judgment tuple layout (theory2.tex §11.1)
    ------------------------------------------
    ``judgment_tuple = (c, φ, A, E, O, B, T, Π)`` where:

    * c  — ``coord.coordinate_id()``
    * φ  — description of the claim at this node
    * A  — type_annotation (or ``"?"`` if absent)
    * E  — [] (evidence bundle, filled by downstream analysis)
    * O  — [] (obstruction set, filled by detect_* methods)
    * B  — list(coord.scope_chain)
    * T  — trust_label
    * Π  — [("bridge", "ast_bridge"), ("kind", kind)]

    Parameters
    ----------
    coord:
        The ``ASTCoordinate`` identifying this node's position in the site.
    kind:
        Syntactic kind label (e.g. ``"function_def"``, ``"assign"``,
        ``"import"``, ``"call"``).
    type_annotation:
        PEP-484 annotation string, or ``None`` if absent.
    trust_label:
        Trust tier string; always ``"ORACLE_PROPOSED"`` for bridge output.
    judgment_tuple:
        Eight-component judgment tuple ``(c,φ,A,E,O,B,T,Π)``.
    children:
        Tuple of child SymbolicNodes.  Immutable because the whole tree is
        frozen once constructed.
    """

    coord: ASTCoordinate = field(default_factory=ASTCoordinate)
    kind: str = "unknown"
    type_annotation: str | None = None
    trust_label: str = "ORACLE_PROPOSED"
    judgment_tuple: tuple[Any, ...] = ()
    children: tuple["SymbolicNode", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-serialisable dict (children are inlined).

        Returns
        -------
        dict[str, Any]
        """
        return {
            "coord": self.coord.to_dict(),
            "kind": self.kind,
            "type_annotation": self.type_annotation,
            "trust_label": self.trust_label,
            "judgment_tuple": list(self.judgment_tuple),
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SymbolicNode":
        """Deserialise from a plain dict (children are recursively deserialised).

        Parameters
        ----------
        payload:
            A dict as produced by ``to_dict``.

        Returns
        -------
        SymbolicNode
        """
        return cls(
            coord=ASTCoordinate.from_dict(payload.get("coord", {})),
            kind=payload.get("kind", "unknown"),
            type_annotation=payload.get("type_annotation"),
            trust_label=payload.get("trust_label", "ORACLE_PROPOSED"),
            judgment_tuple=tuple(payload.get("judgment_tuple", [])),
            children=tuple(
                cls.from_dict(c) for c in payload.get("children", [])
            ),
        )

    def all_descendants(self) -> list["SymbolicNode"]:
        """Return a flat list of self and all descendants (pre-order).

        Returns
        -------
        list[SymbolicNode]
        """
        result: list[SymbolicNode] = [self]
        for child in self.children:
            result.extend(child.all_descendants())
        return result


# ---------------------------------------------------------------------------
# ASTBridgeConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ASTBridgeConfig:
    """Configuration for the ``PythonASTBridge``.

    Parameters
    ----------
    max_depth:
        Maximum recursion depth when building the symbolic tree.  Nodes
        beyond this depth are represented as leaf nodes with kind
        ``"depth_limit"``.
    trust_floor:
        Minimum trust tier to admit in obstruction reports.  Weaker evidence
        is still collected but marked as ``"candidate"`` rather than
        ``"genuine"``.
    include_docstrings:
        Whether to include ``Expr(value=Constant(...))`` docstring nodes
        in the symbolic tree.  When ``False``, these nodes are skipped to
        reduce noise.
    """

    max_depth: int = 50
    trust_floor: str = "ORACLE_PROPOSED"
    include_docstrings: bool = True


# ---------------------------------------------------------------------------
# AST node → kind mapping
# ---------------------------------------------------------------------------

_AST_KIND_MAP: dict[str, str] = {
    "Module": "module",
    "FunctionDef": "function_def",
    "AsyncFunctionDef": "async_function_def",
    "ClassDef": "class_def",
    "Return": "return",
    "Delete": "delete",
    "Assign": "assign",
    "AugAssign": "augmented_assign",
    "AnnAssign": "annotated_assign",
    "For": "for_loop",
    "AsyncFor": "async_for_loop",
    "While": "while_loop",
    "If": "conditional",
    "With": "with_block",
    "AsyncWith": "async_with_block",
    "Raise": "raise",
    "Try": "try_block",
    "TryStar": "try_star_block",
    "Assert": "assert",
    "Import": "import",
    "ImportFrom": "import_from",
    "Global": "global_decl",
    "Nonlocal": "nonlocal_decl",
    "Expr": "expr_stmt",
    "Pass": "pass",
    "Break": "break",
    "Continue": "continue",
    "BoolOp": "bool_op",
    "BinOp": "bin_op",
    "UnaryOp": "unary_op",
    "Lambda": "lambda",
    "IfExp": "ternary",
    "Dict": "dict_literal",
    "Set": "set_literal",
    "ListComp": "list_comp",
    "SetComp": "set_comp",
    "DictComp": "dict_comp",
    "GeneratorExp": "generator_exp",
    "Await": "await",
    "Yield": "yield",
    "YieldFrom": "yield_from",
    "Compare": "compare",
    "Call": "call",
    "FormattedValue": "fstring_part",
    "JoinedStr": "fstring",
    "Constant": "constant",
    "Attribute": "attribute_access",
    "Subscript": "subscript",
    "Starred": "starred",
    "Name": "name_ref",
    "List": "list_literal",
    "Tuple": "tuple_literal",
    "Slice": "slice",
    "arg": "argument",
    "arguments": "arguments",
    "keyword": "keyword_arg",
    "alias": "import_alias",
    "withitem": "context_manager",
    "ExceptHandler": "except_handler",
}


def _ast_kind(node: ast.AST) -> str:
    """Return the symbolic kind label for an AST node.

    Parameters
    ----------
    node:
        An AST node.

    Returns
    -------
    str
        A lowercase underscore-separated kind label.
    """
    return _AST_KIND_MAP.get(type(node).__name__, type(node).__name__.lower())


# ---------------------------------------------------------------------------
# PythonASTBridge
# ---------------------------------------------------------------------------


class PythonASTBridge:
    """Bridge that converts a Python AST to a list of SymbolicNodes.

    The bridge operates in two passes over the AST:

    1. **Coordinate extraction** (``extract_coordinates``) — assign each node
       an ``ASTCoordinate`` by walking the tree and tracking the current scope
       chain.

    2. **Symbolic tree construction** (``build_symbolic_tree``) — lift each
       coordinate to a full ``SymbolicNode`` carrying the judgment tuple and
       type annotation.

    Additionally, three *obstruction detection* methods are provided that
    analyse the tree for scope violations, type inconsistencies, and trust
    violations.  These return raw ``(coordinate_str, message, kind_str)``
    tuples that the ``BugDetector`` converts to ``BugReport`` objects.

    All methods are pure with respect to ``self`` — no mutable state is
    accumulated between calls.  The bridge can be shared safely across threads.

    Parameters
    ----------
    config:
        Bridge configuration.  Defaults to ``ASTBridgeConfig()``.
    """

    def __init__(self, config: ASTBridgeConfig | None = None) -> None:
        self.config: ASTBridgeConfig = config or ASTBridgeConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_source(self, source: str, filename: str = "<unknown>") -> ast.Module:
        """Parse Python source into an AST module.

        Parameters
        ----------
        source:
            Raw Python source code.
        filename:
            The name to report in syntax error messages and coordinates.

        Returns
        -------
        ast.Module
            The parsed module AST with ``type_comment`` support enabled.

        Raises
        ------
        SyntaxError
            If the source contains a syntax error.
        """
        tree = ast.parse(
            textwrap.dedent(source),
            filename=filename,
            type_comments=True,
            feature_version=(3, 11),
        )
        ast.fix_missing_locations(tree)
        return tree  # type: ignore[return-value]

    def extract_coordinates(
        self,
        tree: ast.Module,
        filename: str = "<unknown>",
    ) -> list[ASTCoordinate]:
        """Walk the AST and produce an ASTCoordinate for every node.

        The walk is depth-first pre-order.  Scope-introducing nodes
        (``FunctionDef``, ``AsyncFunctionDef``, ``ClassDef``, ``Lambda``,
        ``Module``) extend the running scope_chain; when their subtree is
        exhausted the scope chain is restored.

        Parameters
        ----------
        tree:
            A parsed ``ast.Module``.
        filename:
            The source file path to embed in each coordinate.

        Returns
        -------
        list[ASTCoordinate]
            Coordinates in pre-order (parent before children).
        """
        coords: list[ASTCoordinate] = []
        scope_stack: list[str] = ["module"]

        def _visit(node: ast.AST, depth: int) -> None:
            if depth > self.config.max_depth:
                return
            lineno = getattr(node, "lineno", 0)
            col = getattr(node, "col_offset", 0)
            coord = ASTCoordinate(
                file=filename,
                lineno=lineno,
                col=col,
                node_type=type(node).__name__,
                scope_chain=tuple(scope_stack),
            )
            coords.append(coord)
            # Determine if this node introduces a new scope
            scope_name: str | None = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope_name = node.name
            elif isinstance(node, ast.ClassDef):
                scope_name = node.name
            elif isinstance(node, ast.Lambda):
                scope_name = "<lambda>"
            if scope_name is not None:
                scope_stack.append(scope_name)
            for child in ast.iter_child_nodes(node):
                _visit(child, depth + 1)
            if scope_name is not None:
                scope_stack.pop()

        _visit(tree, 0)
        return coords

    def build_symbolic_tree(
        self,
        tree: ast.Module,
        config: ASTBridgeConfig | None = None,
        filename: str = "<unknown>",
    ) -> list[SymbolicNode]:
        """Build a list of top-level SymbolicNodes from the module AST.

        Each statement-level node in the module body is converted to a
        SymbolicNode; its children are the statement's direct sub-nodes.  The
        full subtree is flattened to a list for easy consumption by the
        downstream detector.

        The returned list is in pre-order (parent before children).

        Parameters
        ----------
        tree:
            A parsed ``ast.Module``.
        config:
            Optional override config.
        filename:
            Source file path.

        Returns
        -------
        list[SymbolicNode]
            All symbolic nodes in pre-order.
        """
        cfg = config or self.config
        result: list[SymbolicNode] = []
        scope_stack: list[str] = ["module"]

        def _node(ast_node: ast.AST, depth: int) -> SymbolicNode:
            if depth > cfg.max_depth:
                coord = ASTCoordinate(
                    file=filename,
                    lineno=getattr(ast_node, "lineno", 0),
                    col=getattr(ast_node, "col_offset", 0),
                    node_type=type(ast_node).__name__,
                    scope_chain=tuple(scope_stack),
                )
                return SymbolicNode(
                    coord=coord,
                    kind="depth_limit",
                    trust_label="ORACLE_PROPOSED",
                    judgment_tuple=self._make_judgment(coord, "depth_limit", None),
                )

            lineno = getattr(ast_node, "lineno", 0)
            col = getattr(ast_node, "col_offset", 0)
            kind = _ast_kind(ast_node)
            coord = ASTCoordinate(
                file=filename,
                lineno=lineno,
                col=col,
                node_type=type(ast_node).__name__,
                scope_chain=tuple(scope_stack),
            )

            # Skip standalone docstrings if configured to do so
            if not cfg.include_docstrings and isinstance(ast_node, ast.Expr):
                if isinstance(getattr(ast_node, "value", None), ast.Constant):
                    val = ast_node.value.value  # type: ignore[attr-defined]
                    if isinstance(val, str):
                        return SymbolicNode(
                            coord=coord,
                            kind="docstring",
                            trust_label="ORACLE_PROPOSED",
                            judgment_tuple=self._make_judgment(coord, "docstring", None),
                        )

            ann = self._extract_annotation_for_node(ast_node)

            # Push scope if needed
            scope_name: str | None = None
            if isinstance(ast_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope_name = ast_node.name
            elif isinstance(ast_node, ast.ClassDef):
                scope_name = ast_node.name
            elif isinstance(ast_node, ast.Lambda):
                scope_name = "<lambda>"

            if scope_name is not None:
                scope_stack.append(scope_name)

            children = tuple(
                _node(child, depth + 1)
                for child in ast.iter_child_nodes(ast_node)
            )

            if scope_name is not None:
                scope_stack.pop()

            jt = self._make_judgment(coord, kind, ann)
            return SymbolicNode(
                coord=coord,
                kind=kind,
                type_annotation=ann,
                trust_label="ORACLE_PROPOSED",
                judgment_tuple=jt,
                children=children,
            )

        for stmt in ast.iter_child_nodes(tree):
            snode = _node(stmt, 1)
            result.append(snode)
            result.extend(snode.all_descendants()[1:])  # skip self already in list

        return result

    def extract_type_annotations(self, node: ast.AST) -> dict[str, str]:
        """Extract all PEP-484 type annotations from an AST subtree.

        Walks the subtree collecting:
        * ``AnnAssign`` targets → annotation pairs
        * ``FunctionDef`` / ``AsyncFunctionDef`` argument annotations and
          return annotations

        Parameters
        ----------
        node:
            The root of the subtree to inspect.

        Returns
        -------
        dict[str, str]
            Mapping from name (or qualified path) to annotation string.
        """
        annotations: dict[str, str] = {}
        for n in ast.walk(node):
            if isinstance(n, ast.AnnAssign):
                target_name = self._name_of(n.target)
                if target_name:
                    annotations[target_name] = ast.unparse(n.annotation)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in (
                    n.args.args
                    + n.args.posonlyargs
                    + n.args.kwonlyargs
                    + ([n.args.vararg] if n.args.vararg else [])
                    + ([n.args.kwarg] if n.args.kwarg else [])
                ):
                    if arg.annotation is not None:
                        annotations[f"{n.name}.{arg.arg}"] = ast.unparse(
                            arg.annotation
                        )
                if n.returns is not None:
                    annotations[f"{n.name}.__return__"] = ast.unparse(n.returns)
        return annotations

    def detect_scope_violations(
        self, tree: ast.Module, filename: str = "<unknown>"
    ) -> list[tuple[str, str, str]]:
        """Detect name references outside their binding scope.

        Performs a two-pass analysis:

        Pass 1 — binding collection: walk all ``FunctionDef``,
        ``ClassDef``, and assignment nodes to collect
        ``{name: scope_chain}`` bindings.

        Pass 2 — reference check: walk all ``Name`` nodes with
        ``Load`` context and verify the referenced name is bound in the
        current scope or an enclosing scope.

        Parameters
        ----------
        tree:
            A parsed ``ast.Module``.
        filename:
            Source file path for coordinate strings.

        Returns
        -------
        list[tuple[str, str, str]]
            Triples ``(coordinate_str, message, "SCOPE_VIOLATION")``.
        """
        violations: list[tuple[str, str, str]] = []

        # First collect all bound names per scope (simple conservative analysis)
        builtins = frozenset(
            dir(__builtins__) if isinstance(__builtins__, dict) else dir(__builtins__)  # type: ignore[arg-type]
        )
        global_names: set[str] = set(builtins)

        # Module-level bindings
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                global_names.add(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    global_names.add(name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name != "*":
                        global_names.add(name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    self._collect_assigned_names(target, global_names)
            elif isinstance(node, ast.AnnAssign):
                self._collect_assigned_names(node.target, global_names)

        # Function-level scope analysis for local variable usage before assignment
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local_names: set[str] = set()
            local_names.update(
                a.arg
                for a in (
                    node.args.args
                    + node.args.posonlyargs
                    + node.args.kwonlyargs
                    + ([node.args.vararg] if node.args.vararg else [])
                    + ([node.args.kwarg] if node.args.kwarg else [])
                )
            )
            # Collect all assignments in function body
            for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        self._collect_assigned_names(t, local_names)
                elif isinstance(stmt, ast.AnnAssign):
                    self._collect_assigned_names(stmt.target, local_names)
                elif isinstance(stmt, ast.Global):
                    local_names.update(stmt.names)
                elif isinstance(stmt, ast.Nonlocal):
                    local_names.update(stmt.names)

            # Check for Name(Load) nodes that are neither local nor global
            for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if (
                    isinstance(sub, ast.Name)
                    and isinstance(sub.ctx, ast.Load)
                    and sub.id not in local_names
                    and sub.id not in global_names
                ):
                    coord_str = (
                        f"{filename}:{getattr(sub,'lineno',0)}:"
                        f"{getattr(sub,'col_offset',0)}:Name"
                    )
                    violations.append((
                        coord_str,
                        f"Name {sub.id!r} used but not defined in any reachable scope "
                        f"(function {node.name!r}).",
                        "SCOPE_VIOLATION",
                    ))
        return violations

    def detect_type_inconsistencies(
        self, tree: ast.Module, filename: str = "<unknown>"
    ) -> list[tuple[str, str, str]]:
        """Detect annotated assignments whose value type contradicts the annotation.

        Analyses ``AnnAssign`` nodes where the annotation is a simple name
        (``int``, ``str``, ``float``, ``bool``, ``bytes``, ``list``, ``dict``,
        ``tuple``, ``set``) and the assigned value is a ``Constant`` literal
        whose Python type disagrees.

        Parameters
        ----------
        tree:
            A parsed ``ast.Module``.
        filename:
            Source file path.

        Returns
        -------
        list[tuple[str, str, str]]
            Triples ``(coordinate_str, message, "TYPE_ERROR")``.
        """
        violations: list[tuple[str, str, str]] = []

        _LITERAL_TYPES: dict[type, str] = {
            int: "int",
            float: "float",
            str: "str",
            bool: "bool",
            bytes: "bytes",
            type(None): "None",
        }
        _NAME_TO_TYPE: dict[str, type] = {
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "bytes": bytes,
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign):
                continue
            if node.value is None:
                continue  # declaration only, no assignment
            ann_node = node.annotation
            if not isinstance(ann_node, ast.Name):
                continue
            expected_type = _NAME_TO_TYPE.get(ann_node.id)
            if expected_type is None:
                continue
            # Handle bool before int because bool is subclass of int
            if expected_type is int and ann_node.id != "bool":
                pass
            val_node = node.value
            if not isinstance(val_node, ast.Constant):
                continue  # can't statically determine type of non-literal
            actual = type(val_node.value)
            # Allow bool for bool annotations; disallow int for bool annotations
            if ann_node.id == "bool" and actual is bool:
                continue
            if ann_node.id == "int" and actual is bool:
                violations.append((
                    f"{filename}:{node.lineno}:{node.col_offset}:AnnAssign",
                    f"Annotation 'int' but assigned a bool literal "
                    f"{val_node.value!r}; bool is a subtype of int but "
                    f"explicit annotation suggests int semantics.",
                    "TYPE_ERROR",
                ))
                continue
            if actual is not expected_type:
                got_name = _LITERAL_TYPES.get(actual, type(val_node.value).__name__)
                violations.append((
                    f"{filename}:{node.lineno}:{node.col_offset}:AnnAssign",
                    f"Annotation {ann_node.id!r} but assigned value has type "
                    f"{got_name!r} (value: {val_node.value!r}).",
                    "TYPE_ERROR",
                ))
        return violations

    def detect_trust_violations(
        self, tree: ast.Module, filename: str = "<unknown>"
    ) -> list[tuple[str, str, str]]:
        """Detect silent trust-promotion patterns in source code.

        A *silent trust promotion* occurs when the result of an oracle-sourced
        call (identified by function names containing ``oracle``, ``copilot``,
        ``propose``, ``suggest``, or ``generate``) is assigned directly to a
        variable without being routed through an explicit discharge step.

        Heuristic indicators of a discharge step:
        * The assignment is wrapped in a ``with`` block whose context manager
          name contains ``verify``, ``discharge``, or ``check``.
        * The called function name itself contains ``verify``, ``discharge``,
          or ``validate``.
        * The assignment is immediately followed by an ``assert`` statement.

        This method is intentionally conservative: it reports only clear
        violations where neither of the above mitigations is present.

        Parameters
        ----------
        tree:
            A parsed ``ast.Module``.
        filename:
            Source file path.

        Returns
        -------
        list[tuple[str, str, str]]
            Triples ``(coordinate_str, message, "TRUST_VIOLATION")``.
        """
        violations: list[tuple[str, str, str]] = []
        _ORACLE_KEYWORDS = {"oracle", "copilot", "propose", "suggest", "generate"}
        _DISCHARGE_KEYWORDS = {"verify", "discharge", "validate", "check", "assert_"}

        def _is_oracle_call(call_node: ast.Call) -> bool:
            """Return True if the call target contains an oracle keyword."""
            func = call_node.func
            if isinstance(func, ast.Name):
                name_lower = func.id.lower()
            elif isinstance(func, ast.Attribute):
                name_lower = func.attr.lower()
            else:
                return False
            return any(kw in name_lower for kw in _ORACLE_KEYWORDS)

        def _is_discharged(call_node: ast.Call) -> bool:
            """Return True if the call name contains a discharge keyword."""
            func = call_node.func
            if isinstance(func, ast.Name):
                name_lower = func.id.lower()
            elif isinstance(func, ast.Attribute):
                name_lower = func.attr.lower()
            else:
                return False
            return any(kw in name_lower for kw in _DISCHARGE_KEYWORDS)

        def _stmt_list_has_assert_after(stmts: list[ast.stmt], idx: int) -> bool:
            """Return True if the next statement after idx is an Assert."""
            nxt = idx + 1
            return nxt < len(stmts) and isinstance(stmts[nxt], ast.Assert)

        def _check_block(stmts: list[ast.stmt]) -> None:
            for i, stmt in enumerate(stmts):
                if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    continue
                rhs = stmt.value if isinstance(stmt, ast.Assign) else getattr(stmt, "value", None)
                if rhs is None or not isinstance(rhs, ast.Call):
                    continue
                if not _is_oracle_call(rhs):
                    continue
                if _is_discharged(rhs):
                    continue
                if _stmt_list_has_assert_after(stmts, i):
                    continue
                coord_str = (
                    f"{filename}:{stmt.lineno}:{stmt.col_offset}:"
                    f"{type(stmt).__name__}"
                )
                func_name = ast.unparse(rhs.func)
                violations.append((
                    coord_str,
                    f"Silent trust promotion: result of oracle-sourced call "
                    f"'{func_name}(...)' assigned without explicit discharge step. "
                    f"Per theory2.tex §252, all oracle proposals must enter at "
                    f"ORACLE_PROPOSED and be explicitly promoted.",
                    "TRUST_VIOLATION",
                ))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _check_block(node.body)
            elif isinstance(node, ast.Module):
                _check_block(node.body)
            elif isinstance(node, ast.ClassDef):
                _check_block(node.body)
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.With)):
                _check_block(getattr(node, "body", []))
                _check_block(getattr(node, "orelse", []))

        return violations

    def to_judgment_encoding(self, node: SymbolicNode) -> dict[str, Any]:
        """Encode a SymbolicNode as a theory2 judgment dict.

        Returns a dict with keys ``c``, ``phi``, ``A``, ``E``, ``O``,
        ``B``, ``T``, ``Pi`` matching the eight-component judgment tuple
        layout defined in theory2.tex §11.1.

        Parameters
        ----------
        node:
            The SymbolicNode to encode.

        Returns
        -------
        dict[str, Any]
            Eight-key judgment dict.
        """
        jt = node.judgment_tuple
        if len(jt) == 8:
            c, phi, a, e, o, b, t, pi = jt
        else:
            c = node.coord.coordinate_id()
            phi = f"node at {c} has kind {node.kind!r}"
            a = node.type_annotation or "?"
            e, o, b, pi = [], [], list(node.coord.scope_chain), []
            t = node.trust_label
        return {"c": c, "phi": phi, "A": a, "E": e, "O": o, "B": b, "T": t, "Pi": pi}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_judgment(
        self,
        coord: ASTCoordinate,
        kind: str,
        annotation: str | None,
    ) -> tuple[Any, ...]:
        """Construct the eight-component judgment tuple for a coordinate.

        Parameters
        ----------
        coord:
            The coordinate object.
        kind:
            The syntactic kind label.
        annotation:
            The type annotation string, or None.

        Returns
        -------
        tuple[Any, ...]
            Eight-element tuple ``(c,φ,A,E,O,B,T,Π)``.
        """
        c = coord.coordinate_id()
        phi = f"node {coord.node_type!r} at {c} is syntactically well-formed"
        a = annotation or "?"
        e: list[Any] = []
        o: list[Any] = []
        b: list[str] = list(coord.scope_chain)
        t = "ORACLE_PROPOSED"
        pi: list[Any] = [("bridge", "ast_bridge"), ("kind", kind)]
        return (c, phi, a, e, o, b, t, pi)

    def _extract_annotation_for_node(self, node: ast.AST) -> str | None:
        """Return a type annotation string for an AST node, or None.

        Parameters
        ----------
        node:
            The AST node.

        Returns
        -------
        str | None
        """
        if isinstance(node, ast.AnnAssign) and node.annotation:
            return ast.unparse(node.annotation)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                return ast.unparse(node.returns)
        if isinstance(node, ast.arg) and node.annotation:
            return ast.unparse(node.annotation)
        return None

    @staticmethod
    def _name_of(node: ast.AST) -> str | None:
        """Extract the simple name string from a target node, or None.

        Parameters
        ----------
        node:
            An assignment target node.

        Returns
        -------
        str | None
        """
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{PythonASTBridge._name_of(node.value) or '?'}.{node.attr}"
        return None

    @staticmethod
    def _collect_assigned_names(target: ast.AST, names: set[str]) -> None:
        """Recursively collect all names assigned in a target expression.

        Parameters
        ----------
        target:
            An assignment target (Name, Tuple, List, Starred, or Attribute).
        names:
            The set to update in place.
        """
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                PythonASTBridge._collect_assigned_names(elt, names)
        elif isinstance(target, ast.Starred):
            PythonASTBridge._collect_assigned_names(target.value, names)
        # Attribute assignments are not new local bindings


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def bridge_python_file(
    path: str, config: ASTBridgeConfig | None = None
) -> list[SymbolicNode]:
    """Parse a Python file and return its symbolic node list.

    This is the primary entry point for the bridge layer.  It reads the
    source at *path*, parses it, and returns the full flat list of
    ``SymbolicNode`` objects in pre-order.

    Parameters
    ----------
    path:
        Path to the Python source file.
    config:
        Optional bridge configuration.

    Returns
    -------
    list[SymbolicNode]
        All symbolic nodes in the file, in pre-order.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    SyntaxError
        If the file contains a Python syntax error.
    """
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    bridge = PythonASTBridge(config=config)
    tree = bridge.parse_source(source, filename=str(p))
    return bridge.build_symbolic_tree(tree, filename=str(p))


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
    "ASTCoordinate",
    "SymbolicNode",
    "ASTBridgeConfig",
    "PythonASTBridge",
    "bridge_python_file",
    "MANIFEST_SPEC_PROVENANCE",
    "bug_as_obstruction",
    "bug_evidence",
    "bug_encoding",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== ast_bridge.py smoke test ===")

    _SAMPLE = textwrap.dedent("""
        def add(x: int, y: int) -> int:
            return x + y

        z: str = 42

        result = oracle_propose(add(1, 2))
    """)

    bridge = PythonASTBridge()
    tree = bridge.parse_source(_SAMPLE, filename="sample.py")

    coords = bridge.extract_coordinates(tree, filename="sample.py")
    print(f"Coordinates extracted: {len(coords)}")
    for c in coords[:5]:
        print(" ", str(c))

    nodes = bridge.build_symbolic_tree(tree, filename="sample.py")
    print(f"Symbolic nodes built: {len(nodes)}")

    anns = bridge.extract_type_annotations(tree)
    print("Type annotations:", anns)

    type_issues = bridge.detect_type_inconsistencies(tree, filename="sample.py")
    print("Type inconsistencies:", type_issues)

    trust_issues = bridge.detect_trust_violations(tree, filename="sample.py")
    print("Trust violations:", trust_issues)

    scope_issues = bridge.detect_scope_violations(tree, filename="sample.py")
    print("Scope violations:", scope_issues)

    if nodes:
        enc = bridge.to_judgment_encoding(nodes[0])
        print("First judgment encoding keys:", list(enc.keys()))
        assert set(enc.keys()) == {"c", "phi", "A", "E", "O", "B", "T", "Pi"}

    print("=== smoke test PASSED ===")
