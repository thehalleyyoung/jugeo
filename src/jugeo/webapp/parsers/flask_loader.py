"""
Flask / Python source-file parser.

Uses the *ast* module to extract route handlers, models, forms, blueprints,
middleware, error handlers and cross-language references such as
``render_template`` and ``url_for`` calls.
"""
from __future__ import annotations

import ast
import hashlib
import time
from dataclasses import dataclass, field

from .models import (
    CoordinateKind,
    ErrorSeverity,
    Language,
    ParsedCoordinate,
    ParsedReference,
    ParseError,
    ParseResult,
    ReferenceType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord_id(file_path: str, kind: str, name: str, line: int) -> str:
    """Return a deterministic, unique coordinate ID."""
    raw = f"{file_path}::{kind}::{name}::{line}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_decorator_names(node: ast.AST) -> list[str]:
    """Return a list of human-readable decorator strings for a node."""
    names: list[str] = []
    for dec in getattr(node, "decorator_list", []):
        names.append(_decorator_to_str(dec))
    return names


def _decorator_to_str(dec: ast.AST) -> str:
    """Convert a decorator AST node to a dotted string."""
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return f"{_decorator_to_str(dec.value)}.{dec.attr}"
    if isinstance(dec, ast.Call):
        return _decorator_to_str(dec.func)
    return ""


def _const_value(node: ast.AST):
    """Extract a constant value from a Constant / Str / Num node."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _list_of_strings(node: ast.AST) -> list[str]:
    """Extract a list of string constants from an ast.List node."""
    if not isinstance(node, ast.List):
        return []
    out: list[str] = []
    for elt in node.elts:
        v = _const_value(elt)
        if isinstance(v, str):
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# FlaskRouteExtractor
# ---------------------------------------------------------------------------

class FlaskRouteExtractor:
    """Extract coordinates and references from a Flask Python source file."""

    def extract(self, source: str, file_path: str) -> ParseResult:
        """Main entry point – parse *source* and return a ``ParseResult``."""
        t0 = time.monotonic()
        coords: list[ParsedCoordinate] = []
        refs: list[ParsedReference] = []
        errors: list[ParseError] = []

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as exc:
            errors.append(ParseError(
                file_path=file_path,
                line_number=exc.lineno or 0,
                message=f"SyntaxError: {exc.msg}",
                severity=ErrorSeverity.ERROR,
            ))
            elapsed = (time.monotonic() - t0) * 1000
            return ParseResult(
                file_path=file_path,
                language=Language.PYTHON,
                coordinates=coords,
                references=refs,
                errors=errors,
                parse_time_ms=elapsed,
            )

        try:
            coords.extend(self._extract_routes(tree, file_path))
            coords.extend(self._extract_models(tree, file_path))
            coords.extend(self._extract_forms(tree, file_path))
            coords.extend(self._extract_blueprints(tree, file_path))
            coords.extend(self._extract_middleware(tree, file_path))
            coords.extend(self._extract_error_handlers(tree, file_path))
            refs.extend(self._extract_render_template_refs(tree, file_path))
            refs.extend(self._extract_url_for_refs(tree, file_path))
            refs.extend(self._extract_config_refs(tree, file_path))
            refs.extend(self._extract_session_refs(tree, file_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(ParseError(
                file_path=file_path,
                line_number=0,
                message=f"Unexpected extraction error: {exc}",
                severity=ErrorSeverity.ERROR,
            ))

        elapsed = (time.monotonic() - t0) * 1000
        return ParseResult(
            file_path=file_path,
            language=Language.PYTHON,
            coordinates=coords,
            references=refs,
            errors=errors,
            parse_time_ms=elapsed,
        )

    # -- route extraction ----------------------------------------------------

    def _extract_routes(self, tree: ast.Module, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                dec_str = _decorator_to_str(dec)
                if not dec_str.endswith(".route") and dec_str != "route":
                    continue
                path_str = ""
                methods: list[str] = ["GET"]
                if isinstance(dec, ast.Call) and dec.args:
                    v = _const_value(dec.args[0])
                    if isinstance(v, str):
                        path_str = v
                    for kw in dec.keywords:
                        if kw.arg == "methods":
                            methods = _list_of_strings(kw.value) or methods
                end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
                cid = _make_coord_id(file_path, CoordinateKind.ROUTE_HANDLER.value, node.name, node.lineno)
                coords.append(ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.ROUTE_HANDLER,
                    name=node.name,
                    file_path=file_path,
                    line_number=node.lineno,
                    end_line=end_line,
                    language=Language.PYTHON,
                    metadata={"path": path_str, "methods": methods},
                ))
        return coords

    # -- model extraction ----------------------------------------------------

    def _extract_models(self, tree: ast.Module, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not self._inherits_from(node, ("db.Model", "Model", "Base")):
                continue
            columns = self._extract_column_names(node)
            end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
            cid = _make_coord_id(file_path, CoordinateKind.MODEL_CLASS.value, node.name, node.lineno)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.MODEL_CLASS,
                name=node.name,
                file_path=file_path,
                line_number=node.lineno,
                end_line=end_line,
                language=Language.PYTHON,
                metadata={"columns": columns},
            ))
        return coords

    @staticmethod
    def _inherits_from(node: ast.ClassDef, names: tuple[str, ...]) -> bool:
        for base in node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = _decorator_to_str(base)
            if base_name in names:
                return True
        return False

    @staticmethod
    def _extract_column_names(cls_node: ast.ClassDef) -> list[str]:
        cols: list[str] = []
        for stmt in cls_node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(stmt.value, ast.Call):
                    func_str = _decorator_to_str(stmt.value.func) if hasattr(stmt.value, "func") else ""
                    if "Column" in func_str:
                        cols.append(target.id)
        return cols

    # -- form extraction -----------------------------------------------------

    def _extract_forms(self, tree: ast.Module, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not self._inherits_from(node, ("FlaskForm", "Form")):
                continue
            fields = self._extract_form_field_names(node)
            end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
            cid = _make_coord_id(file_path, CoordinateKind.FORM_CLASS.value, node.name, node.lineno)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.FORM_CLASS,
                name=node.name,
                file_path=file_path,
                line_number=node.lineno,
                end_line=end_line,
                language=Language.PYTHON,
                metadata={"fields": fields},
            ))
        return coords

    @staticmethod
    def _extract_form_field_names(cls_node: ast.ClassDef) -> list[str]:
        fields: list[str] = []
        for stmt in cls_node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        fields.append(target.id)
        return fields

    # -- blueprint extraction ------------------------------------------------

    def _extract_blueprints(self, tree: ast.Module, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            func_str = _decorator_to_str(node.value.func) if hasattr(node.value, "func") else ""
            if func_str != "Blueprint":
                continue
            bp_name = ""
            if node.value.args:
                v = _const_value(node.value.args[0])
                if isinstance(v, str):
                    bp_name = v
            var_name = ""
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_name = target.id
                    break
            name = bp_name or var_name
            cid = _make_coord_id(file_path, CoordinateKind.BLUEPRINT.value, name, node.lineno)
            end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.BLUEPRINT,
                name=name,
                file_path=file_path,
                line_number=node.lineno,
                end_line=end_line,
                language=Language.PYTHON,
                metadata={"variable": var_name},
            ))
        return coords

    # -- middleware extraction ------------------------------------------------

    def _extract_middleware(self, tree: ast.Module, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                dec_str = _decorator_to_str(dec)
                if dec_str in ("app.before_request", "app.after_request",
                               "app.teardown_request", "app.before_first_request"):
                    end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
                    cid = _make_coord_id(file_path, CoordinateKind.MIDDLEWARE.value, node.name, node.lineno)
                    coords.append(ParsedCoordinate(
                        id=cid,
                        kind=CoordinateKind.MIDDLEWARE,
                        name=node.name,
                        file_path=file_path,
                        line_number=node.lineno,
                        end_line=end_line,
                        language=Language.PYTHON,
                        metadata={"hook": dec_str},
                    ))
        return coords

    # -- error handler extraction --------------------------------------------

    def _extract_error_handlers(self, tree: ast.Module, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                dec_str = _decorator_to_str(dec)
                if dec_str != "app.errorhandler":
                    continue
                code: int | str = 0
                if isinstance(dec, ast.Call) and dec.args:
                    v = _const_value(dec.args[0])
                    if v is not None:
                        code = v
                end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
                cid = _make_coord_id(file_path, CoordinateKind.ERROR_HANDLER.value, node.name, node.lineno)
                coords.append(ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.ERROR_HANDLER,
                    name=node.name,
                    file_path=file_path,
                    line_number=node.lineno,
                    end_line=end_line,
                    language=Language.PYTHON,
                    metadata={"error_code": code},
                ))
        return coords

    # -- cross-language references -------------------------------------------

    def _extract_render_template_refs(self, tree: ast.Module, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_str = ""
            if isinstance(node.func, ast.Name):
                func_str = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_str = node.func.attr
            if func_str != "render_template":
                continue
            if not node.args:
                continue
            tpl = _const_value(node.args[0])
            if not isinstance(tpl, str):
                continue
            # Find enclosing function for source_id
            source_id = _make_coord_id(file_path, "render_template", tpl, node.lineno)
            kwargs_dict: dict[str, str] = {}
            for kw in node.keywords:
                if kw.arg is not None:
                    kwargs_dict[kw.arg] = ast.dump(kw.value)
            refs.append(ParsedReference(
                source_id=source_id,
                target_name=tpl,
                reference_type=ReferenceType.RENDERS_TEMPLATE,
                file_path=file_path,
                line_number=node.lineno,
                metadata={"kwargs": list(kwargs_dict.keys())},
            ))
        return refs

    def _extract_url_for_refs(self, tree: ast.Module, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_str = ""
            if isinstance(node.func, ast.Name):
                func_str = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_str = node.func.attr
            if func_str != "url_for":
                continue
            if not node.args:
                continue
            endpoint = _const_value(node.args[0])
            if not isinstance(endpoint, str):
                continue
            source_id = _make_coord_id(file_path, "url_for", endpoint, node.lineno)
            refs.append(ParsedReference(
                source_id=source_id,
                target_name=endpoint,
                reference_type=ReferenceType.URL_FOR,
                file_path=file_path,
                line_number=node.lineno,
            ))
        return refs

    def _extract_config_refs(self, tree: ast.Module, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            val = node.value
            if not (isinstance(val, ast.Attribute) and val.attr == "config"):
                continue
            key = ""
            if isinstance(node.slice, ast.Constant):
                key = str(node.slice.value)
            elif isinstance(node.slice, ast.Index):  # Python 3.8 compat
                inner = getattr(node.slice, "value", None)
                if isinstance(inner, ast.Constant):
                    key = str(inner.value)
            if not key:
                continue
            source_id = _make_coord_id(file_path, "config", key, node.lineno)
            refs.append(ParsedReference(
                source_id=source_id,
                target_name=key,
                reference_type=ReferenceType.CONFIG_ACCESS,
                file_path=file_path,
                line_number=node.lineno,
            ))
        return refs

    def _extract_session_refs(self, tree: ast.Module, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            val = node.value
            if not (isinstance(val, ast.Name) and val.id == "session"):
                continue
            key = ""
            if isinstance(node.slice, ast.Constant):
                key = str(node.slice.value)
            elif isinstance(node.slice, ast.Index):
                inner = getattr(node.slice, "value", None)
                if isinstance(inner, ast.Constant):
                    key = str(inner.value)
            if not key:
                continue
            source_id = _make_coord_id(file_path, "session", key, node.lineno)
            refs.append(ParsedReference(
                source_id=source_id,
                target_name=key,
                reference_type=ReferenceType.SESSION_ACCESS,
                file_path=file_path,
                line_number=node.lineno,
            ))
        return refs


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def extract_flask_coordinates(source: str, file_path: str) -> ParseResult:
    """Convenience wrapper around ``FlaskRouteExtractor.extract``."""
    return FlaskRouteExtractor().extract(source, file_path)


def extract_render_template_kwargs(source: str) -> list[dict]:
    """Return a list of dicts with *template_name* and *kwargs* for each
    ``render_template(...)`` call found in *source*.
    """
    results: list[dict] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return results

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_str = ""
        if isinstance(node.func, ast.Name):
            func_str = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_str = node.func.attr
        if func_str != "render_template":
            continue
        if not node.args:
            continue
        tpl = _const_value(node.args[0])
        if not isinstance(tpl, str):
            continue
        kwargs = [kw.arg for kw in node.keywords if kw.arg is not None]
        results.append({"template_name": tpl, "kwargs": kwargs})
    return results
