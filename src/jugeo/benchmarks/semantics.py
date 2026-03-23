from __future__ import annotations

import ast
import builtins
import copy
import json
import warnings
from dataclasses import dataclass
from typing import Any, Callable

from .models import InputPoint

BENCHMARK_DECLARED_COVER_MIN_POINTS = 10


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    tag: str
    value: Any


def load_namespace(source: str, label: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        exec(compile(source, f"<benchmark:{label}>", "exec"), namespace, namespace)
    return namespace


def load_function(source: str, function_name: str) -> Callable[..., Any]:
    namespace = load_namespace(source, function_name)
    value = namespace[function_name]
    if not callable(value):
        raise TypeError(f"{function_name!r} did not resolve to a callable")
    return value


def count_required_positional_parameters(function: Callable[..., Any]) -> int | None:
    code = getattr(function, "__code__", None)
    if code is None:
        return None
    positional = code.co_argcount + code.co_posonlyargcount
    defaults = getattr(function, "__defaults__", None) or ()
    return max(0, positional - len(defaults))


def semantic_coordinate(source: str) -> str | None:
    namespace = load_namespace(source, "semantic-coordinate")
    coordinate_candidates = []
    for name, value in namespace.items():
        if "_coordinate" not in name or not callable(value):
            continue
        required = count_required_positional_parameters(value)
        if required == 0:
            coordinate_candidates.append((name, value))
    if len(coordinate_candidates) != 1:
        return None
    coordinate = coordinate_candidates[0][1]()
    if not isinstance(coordinate, str):
        raise TypeError("semantic coordinate helper must return a string")
    return coordinate


def call(function: Callable[..., Any], point: InputPoint) -> ExecutionOutcome:
    try:
        args = copy.deepcopy(point.args)
        kwargs = copy.deepcopy(point.kwargs)
        return ExecutionOutcome("return", function(*args, **kwargs))
    except Exception as exc:  # pragma: no cover - surfaced through callers.
        return ExecutionOutcome("raise", (type(exc).__name__, str(exc)))


def call_fresh(source: str, function_name: str, point: InputPoint) -> ExecutionOutcome:
    return call(load_function(source, function_name), point)


def point_signature(point: InputPoint) -> str:
    return json.dumps(point.to_dict(), sort_keys=True)


def require_declared_cover(
    points: tuple[InputPoint, ...], *, case_id: str, category: str
) -> tuple[InputPoint, ...]:
    if len(points) < BENCHMARK_DECLARED_COVER_MIN_POINTS:
        raise ValueError(
            f"{category} case {case_id!r} must declare a finite cover with at least "
            f"{BENCHMARK_DECLARED_COVER_MIN_POINTS} points"
        )
    signatures = tuple(point_signature(point) for point in points)
    if len(signatures) != len(set(signatures)):
        raise ValueError(f"{category} case {case_id!r} must use distinct points in its declared finite cover")
    return points


def format_outcome(outcome: ExecutionOutcome) -> str:
    return f"{outcome.tag}={outcome.value!r}"


@dataclass(frozen=True, slots=True)
class BugObservation:
    code: str
    lineno: int
    col: int
    node_type: str
    message: str


_SHADOWED_BUILTINS = frozenset(
    {
        name
        for name, value in vars(builtins).items()
        if not name.startswith("__") and (callable(value) or isinstance(value, type))
    }
)


def _extract_target_names(target: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _loaded_names(node: ast.AST) -> set[str]:
    return {
        name.id
        for name in ast.walk(node)
        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)
    }


def _argument_names(args: ast.arguments) -> set[str]:
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_mutable_default(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Tuple):
        return any(_is_mutable_default(element) for element in node.elts)
    return (
        isinstance(node, ast.Call)
        and (_call_name(node.func) in {"list", "dict", "set", "defaultdict", "deque", "bytearray", "OrderedDict"})
    )


def _is_open_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_name(node.func) == "open"


@dataclass(frozen=True, slots=True)
class _OpenHandleBinding:
    name: str
    lineno: int
    col: int
    node_type: str


class _OpenWithoutCloseAnalyzer:
    def __init__(self) -> None:
        self._leak_candidates: list[_OpenHandleBinding] = []

    def detect(self, tree: ast.AST) -> tuple[_OpenHandleBinding, ...]:
        body = getattr(tree, "body", None)
        if isinstance(body, list):
            self._analyze_block(body, {}, finalize=True)
        return tuple(sorted(self._leak_candidates, key=lambda item: (item.lineno, item.col, item.name)))

    def _record_leak(self, binding: _OpenHandleBinding) -> None:
        if any(
            existing.name == binding.name
            and existing.lineno == binding.lineno
            and existing.col == binding.col
            and existing.node_type == binding.node_type
            for existing in self._leak_candidates
        ):
            return
        self._leak_candidates.append(binding)

    def _binding_from(self, target: ast.AST, value: ast.AST, name: str) -> _OpenHandleBinding:
        return _OpenHandleBinding(
            name=name,
            lineno=getattr(target, "lineno", getattr(value, "lineno", 0)),
            col=getattr(target, "col_offset", getattr(value, "col_offset", 0)),
            node_type=type(target).__name__,
        )

    def _merge_states(
        self, *states: dict[str, _OpenHandleBinding]
    ) -> dict[str, _OpenHandleBinding]:
        merged: dict[str, _OpenHandleBinding] = {}
        for state in states:
            for name, binding in state.items():
                merged.setdefault(name, binding)
        return merged

    def _close_names_in_node(self, node: ast.AST, state: dict[str, _OpenHandleBinding]) -> None:
        for nested in ast.walk(node):
            if (
                isinstance(nested, ast.Call)
                and isinstance(nested.func, ast.Attribute)
                and nested.func.attr == "close"
                and isinstance(nested.func.value, ast.Name)
            ):
                state.pop(nested.func.value.id, None)

    def _track_open_binding(
        self,
        state: dict[str, _OpenHandleBinding],
        target: ast.AST,
        value: ast.AST,
    ) -> None:
        if _is_open_call(value):
            for name in _extract_target_names(target):
                previous = state.get(name)
                if previous is not None:
                    self._record_leak(previous)
                state[name] = self._binding_from(target, value, name)
            return
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for sub_target, sub_value in zip(target.elts, value.elts):
                self._track_open_binding(state, sub_target, sub_value)

    def _apply_expression_effects(self, node: ast.AST, state: dict[str, _OpenHandleBinding]) -> None:
        for nested in ast.walk(node):
            if isinstance(nested, ast.NamedExpr):
                self._track_open_binding(state, nested.target, nested.value)
        self._close_names_in_node(node, state)

    def _analyze_block(
        self,
        statements: list[ast.stmt],
        state: dict[str, _OpenHandleBinding],
        *,
        finalize: bool,
    ) -> dict[str, _OpenHandleBinding]:
        current = dict(state)
        for statement in statements:
            current = self._analyze_statement(statement, current)
        if finalize:
            for binding in current.values():
                self._record_leak(binding)
        return current

    def _analyze_statement(
        self, statement: ast.stmt, state: dict[str, _OpenHandleBinding]
    ) -> dict[str, _OpenHandleBinding]:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested_body = getattr(statement, "body", None)
            if isinstance(nested_body, list):
                self._analyze_block(nested_body, {}, finalize=True)
            return state

        if isinstance(statement, ast.If):
            branch_state = dict(state)
            self._apply_expression_effects(statement.test, branch_state)
            body_state = self._analyze_block(statement.body, branch_state, finalize=False)
            else_state = self._analyze_block(statement.orelse, branch_state, finalize=False)
            return self._merge_states(body_state, else_state)

        if isinstance(statement, (ast.For, ast.AsyncFor)):
            loop_state = dict(state)
            self._apply_expression_effects(statement.iter, loop_state)
            body_state = self._analyze_block(statement.body, loop_state, finalize=False)
            orelse_state = self._analyze_block(statement.orelse, dict(state), finalize=False)
            return self._merge_states(state, body_state, orelse_state)

        if isinstance(statement, ast.While):
            loop_state = dict(state)
            self._apply_expression_effects(statement.test, loop_state)
            body_state = self._analyze_block(statement.body, loop_state, finalize=False)
            orelse_state = self._analyze_block(statement.orelse, dict(state), finalize=False)
            return self._merge_states(state, body_state, orelse_state)

        if isinstance(statement, (ast.With, ast.AsyncWith)):
            with_state = dict(state)
            managed_names: set[str] = set()
            bound_names: set[str] = set()
            for item in statement.items:
                self._apply_expression_effects(item.context_expr, with_state)
                if isinstance(item.context_expr, ast.Name):
                    managed_names.add(item.context_expr.id)
                if item.optional_vars is not None:
                    for name in _extract_target_names(item.optional_vars):
                        previous = with_state.get(name)
                        if previous is not None and not isinstance(item.context_expr, ast.Name):
                            self._record_leak(previous)
                        bound_names.add(name)
            body_state = self._analyze_block(statement.body, with_state, finalize=False)
            for name in managed_names | bound_names:
                body_state.pop(name, None)
            return body_state

        if isinstance(statement, ast.Try):
            try_state = self._analyze_block(statement.body, dict(state), finalize=False)
            normal_state = (
                self._analyze_block(statement.orelse, try_state, finalize=False)
                if statement.orelse
                else try_state
            )
            handler_seed = self._merge_states(dict(state), try_state)
            handler_states = [
                self._analyze_block(handler.body, handler_seed, finalize=False) for handler in statement.handlers
            ]
            if statement.finalbody:
                merged = self._merge_states(normal_state, *handler_states, dict(state))
                return self._analyze_block(statement.finalbody, merged, finalize=False)
            return self._merge_states(normal_state, *handler_states, dict(state))

        updated = dict(state)
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                self._track_open_binding(updated, target, statement.value)
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            self._track_open_binding(updated, statement.target, statement.value)
        elif isinstance(statement, ast.Expr):
            self._apply_expression_effects(statement.value, updated)
            return updated
        elif isinstance(statement, ast.Return) and statement.value is not None:
            self._apply_expression_effects(statement.value, updated)
            return updated

        self._close_names_in_node(statement, updated)
        return updated


def _is_literal_value(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_literal_value(node.operand)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal_value(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is None or _is_literal_value(key) for key in node.keys) and all(
            _is_literal_value(value) for value in node.values
        )
    return False


def _is_non_singleton_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        value = node.value
        return value is not None and value is not True and value is not False and value is not Ellipsis
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_non_singleton_literal(node.operand)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal_value(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is None or _is_literal_value(key) for key in node.keys) and all(
            _is_literal_value(value) for value in node.values
        )
    return False


class BugDetector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.observations: list[BugObservation] = []
        self._opened_names: dict[str, tuple[int, int, str]] = {}
        self._closed_names: set[str] = set()
        self._conditional_close_depth = 0
        self._open_without_close = _OpenWithoutCloseAnalyzer()

    def _visit_with_conditional_close_guard(self, nodes: list[ast.stmt]) -> None:
        self._conditional_close_depth += 1
        try:
            for node in nodes:
                self.visit(node)
        finally:
            self._conditional_close_depth -= 1

    def observe(self, code: str, node: ast.AST, message: str) -> None:
        observation = BugObservation(
            code=code,
            lineno=getattr(node, "lineno", 0),
            col=getattr(node, "col_offset", 0),
            node_type=type(node).__name__,
            message=message,
        )
        if observation in self.observations:
            return
        self.observations.append(observation)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in _SHADOWED_BUILTINS:
            self.observe("shadow-builtin", node, f"function name {node.name!r} shadows a Python builtin")
        for default in node.args.defaults:
            if _is_mutable_default(default):
                self.observe("mutable-default", default, "mutable default argument introduces shared state")
        for default in node.args.kw_defaults:
            if default is not None and _is_mutable_default(default):
                self.observe("mutable-default", default, "mutable default argument introduces shared state")
        for arg in node.args.args:
            if arg.arg in _SHADOWED_BUILTINS:
                self.observe("shadow-builtin", arg, f"parameter {arg.arg!r} shadows a Python builtin")
        for arg in node.args.posonlyargs:
            if arg.arg in _SHADOWED_BUILTINS:
                self.observe("shadow-builtin", arg, f"parameter {arg.arg!r} shadows a Python builtin")
        for arg in node.args.kwonlyargs:
            if arg.arg in _SHADOWED_BUILTINS:
                self.observe("shadow-builtin", arg, f"parameter {arg.arg!r} shadows a Python builtin")
        if node.args.vararg is not None and node.args.vararg.arg in _SHADOWED_BUILTINS:
            self.observe("shadow-builtin", node.args.vararg, f"parameter {node.args.vararg.arg!r} shadows a Python builtin")
        if node.args.kwarg is not None and node.args.kwarg.arg in _SHADOWED_BUILTINS:
            self.observe("shadow-builtin", node.args.kwarg, f"parameter {node.args.kwarg.arg!r} shadows a Python builtin")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name in _SHADOWED_BUILTINS:
            self.observe("shadow-builtin", node, f"class name {node.name!r} shadows a Python builtin")
        self.generic_visit(node)

    def _track_open_binding(self, target: ast.AST, value: ast.AST) -> None:
        if _is_open_call(value):
            for name in _extract_target_names(target):
                self._opened_names[name] = (
                    getattr(target, "lineno", getattr(value, "lineno", 0)),
                    getattr(target, "col_offset", getattr(value, "col_offset", 0)),
                    type(target).__name__,
                )
            return
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for sub_target, sub_value in zip(target.elts, value.elts):
                self._track_open_binding(sub_target, sub_value)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._track_open_binding(target, node.value)
            for name in _extract_target_names(target):
                if name in _SHADOWED_BUILTINS:
                    self.observe("shadow-builtin", target, f"assignment to {name!r} shadows a Python builtin")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._track_open_binding(node.target, node.value)
        for name in _extract_target_names(node.target):
            if name in _SHADOWED_BUILTINS:
                self.observe("shadow-builtin", node.target, f"assignment to {name!r} shadows a Python builtin")
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._track_open_binding(node.target, node.value)
        for name in _extract_target_names(node.target):
            if name in _SHADOWED_BUILTINS:
                self.observe("shadow-builtin", node.target, f"assignment to {name!r} shadows a Python builtin")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        loop_names = _extract_target_names(node.target)
        for name in loop_names:
            if name in _SHADOWED_BUILTINS:
                self.observe("shadow-builtin", node.target, f"loop target {name!r} shadows a Python builtin")
        for child in node.body:
            for nested in ast.walk(child):
                if isinstance(nested, ast.Lambda):
                    free_names = _loaded_names(nested.body) - _argument_names(nested.args)
                    if free_names & loop_names:
                        self.observe(
                            "late-binding-closure",
                            nested,
                            "loop variable captured by lambda without freezing it in defaults",
                        )
                elif isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    free_names = set().union(*(_loaded_names(statement) for statement in nested.body)) - _argument_names(
                        nested.args
                    )
                    if free_names & loop_names:
                        self.observe(
                            "late-binding-closure",
                            nested,
                            "loop variable captured by nested function without binding a fresh value",
                        )
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.orelse)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.orelse)

    visit_AsyncWhile = visit_While

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.orelse)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            context_expr = item.context_expr
            if isinstance(context_expr, ast.Name):
                self._closed_names.add(context_expr.id)
            optional_vars = item.optional_vars
            if optional_vars is None:
                continue
            for name in _extract_target_names(optional_vars):
                if name in _SHADOWED_BUILTINS:
                    self.observe("shadow-builtin", optional_vars, f"with target {name!r} shadows a Python builtin")
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.handlers)
        self._visit_with_conditional_close_guard(node.orelse)
        for finalizer in node.finalbody:
            self.visit(finalizer)

    def _visit_comprehension(self, body_nodes: tuple[ast.AST, ...]) -> None:
        for body in body_nodes:
            loop_names = {
                name.id
                for nested in ast.walk(body)
                if isinstance(nested, ast.comprehension)
                for name in ast.walk(nested.target)
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
            }
            if not loop_names:
                continue
            for name in loop_names:
                if name in _SHADOWED_BUILTINS:
                    self.observe("shadow-builtin", body, f"comprehension target {name!r} shadows a Python builtin")
            for nested in ast.walk(body):
                if isinstance(nested, ast.Lambda):
                    free_names = _loaded_names(nested.body) - _argument_names(nested.args)
                    if free_names & loop_names:
                        self.observe(
                            "late-binding-closure",
                            nested,
                            "comprehension variable captured by lambda without freezing it in defaults",
                        )
                        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.observe("bare-except", node, "bare except swallows unrelated failures")
        if node.name is not None and node.name in _SHADOWED_BUILTINS:
            self.observe("shadow-builtin", node, f"exception target {node.name!r} shadows a Python builtin")
        if node.type is not None:
            self.visit(node.type)
        self._visit_with_conditional_close_guard(node.body)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
            and self._conditional_close_depth == 0
        ):
            self._closed_names.add(node.func.value.id)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for operator, left, right in zip(node.ops, operands, operands[1:]):
            if isinstance(operator, (ast.Is, ast.IsNot)) and (
                _is_non_singleton_literal(left) or _is_non_singleton_literal(right)
            ):
                self.observe(
                    "identity-literal",
                    node,
                    "identity comparison with a non-singleton literal is unreliable; use == or !=",
                )
                break
        self.generic_visit(node)

    def finalize(self) -> tuple[BugObservation, ...]:
        if hasattr(self, "_root"):
            leaks = self._open_without_close.detect(self._root)
            for leak in leaks:
                observation = BugObservation(
                    code="open-without-close",
                    lineno=leak.lineno,
                    col=leak.col,
                    node_type=leak.node_type,
                    message=f"file handle {leak.name!r} is opened without a matching close or context manager",
                )
                if observation not in self.observations:
                    self.observations.append(observation)
        return tuple(sorted(self.observations, key=lambda item: (item.lineno, item.col, item.code)))


def detect_bug_observations(source: str, *, filename: str = "<benchmark-bug>") -> tuple[BugObservation, ...]:
    tree = ast.parse(source, filename=filename)
    detector = BugDetector()
    detector._root = tree
    detector.visit(tree)
    return detector.finalize()


def detect_bug_labels(source: str, *, filename: str = "<benchmark-bug>") -> tuple[str, ...]:
    return tuple(observation.code for observation in detect_bug_observations(source, filename=filename))


# ---------------------------------------------------------------------------
# Unified judgment-geometric semantic checks
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import construct_judgment as _construct_judgment  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _construct_judgment = None

try:
    from jugeo.geometry import site as _geometry_site  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _geometry_site = None

try:
    from jugeo.encodings import encode_program as _encode_program  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _encode_program = None

try:
    from jugeo.solver import solve as _solve  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _solve = None


def semantic_judgment_check(program: str) -> dict[str, object]:
    """Check whether *program* admits a well-formed judgment at a geometric site.

    Combines ``jugeo.judgments`` (judgment construction) with
    ``jugeo.geometry`` (site semantics) to verify that the program's
    judgment descriptor is valid relative to its geometric context.
    """
    result: dict[str, object] = {"program": program, "valid": False}
    if _construct_judgment is None or _geometry_site is None:
        result["error"] = "jugeo.judgments or jugeo.geometry not available"
        return result
    try:
        judgment = _construct_judgment(program)
        site = _geometry_site.for_judgment(judgment)
        result["valid"] = site is not None
        result["judgment"] = judgment
        result["site"] = site
    except Exception as exc:
        result["error"] = str(exc)
    return result


def semantic_encoding_check(program: str) -> dict[str, object]:
    """Check whether *program* can be encoded and solved.

    Combines ``jugeo.encodings`` (program encoding) with
    ``jugeo.solver`` (constraint solving) to verify that the encoded
    form is satisfiable.
    """
    result: dict[str, object] = {"program": program, "satisfiable": False}
    if _encode_program is None or _solve is None:
        result["error"] = "jugeo.encodings or jugeo.solver not available"
        return result
    try:
        encoded = _encode_program(program)
        solution = _solve(encoded)
        result["satisfiable"] = solution is not None
        result["encoded"] = encoded
        result["solution"] = solution
    except Exception as exc:
        result["error"] = str(exc)
    return result
