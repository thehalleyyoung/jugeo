"""
JavaScript parser.

Uses regular expressions (no Node.js / esprima dependency) to extract
function declarations, fetch/axios calls, DOM manipulations, event
handlers, class manipulations, style mutations, imports and state
variables.
"""
from __future__ import annotations

import hashlib
import re
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
# Regex patterns
# ---------------------------------------------------------------------------

# function declarations: function name(...) {
_FUNC_DECL_RE = re.compile(
    r"\bfunction\s+(\w+)\s*\(",
)

# arrow / expression assignments: const name = (...) => | const name = function
_ARROW_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:\([^)]*\)|[a-zA-Z_]\w*)\s*=>",
)
_FUNC_EXPR_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*=\s*function\s*\(",
)

# async variants
_ASYNC_FUNC_RE = re.compile(
    r"\basync\s+function\s+(\w+)\s*\(",
)
_ASYNC_ARROW_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*=\s*async\s*(?:\([^)]*\)|[a-zA-Z_]\w*)\s*=>",
)

# fetch / axios
_FETCH_RE = re.compile(
    r"""\bfetch\s*\(\s*['"`]([^'"`]+)['"`]""",
)
_AXIOS_RE = re.compile(
    r"""\baxios\s*\.\s*(?:get|post|put|patch|delete|head|options|request)\s*\(\s*['"`]([^'"`]+)['"`]""",
)

# DOM manipulation
_GET_ELEM_RE = re.compile(
    r"""\b(?:document\.)?getElementById\s*\(\s*['"`]([^'"`]+)['"`]\s*\)""",
)
_QS_RE = re.compile(
    r"""\b(?:document\.)?querySelector\s*\(\s*['"`]([^'"`]+)['"`]\s*\)""",
)
_QSA_RE = re.compile(
    r"""\b(?:document\.)?querySelectorAll\s*\(\s*['"`]([^'"`]+)['"`]\s*\)""",
)

# event handlers
_ADD_EVENT_RE = re.compile(
    r"""\.addEventListener\s*\(\s*['"`](\w+)['"`]""",
)
_ON_EVENT_RE = re.compile(
    r"""\b(\w+)\s*\.\s*on(\w+)\s*=""",
)

# classList
_CLASSLIST_RE = re.compile(
    r"""\.classList\s*\.\s*(add|remove|toggle|contains)\s*\(\s*['"`]([^'"`]+)['"`]""",
)

# style mutations
_STYLE_RE = re.compile(
    r"""\.style\s*\.\s*(\w+)\s*=""",
)

# ES module imports
_IMPORT_RE = re.compile(
    r"""import\s+(?:\{[^}]*\}|[\w*]+(?:\s+as\s+\w+)?|\*\s+as\s+\w+)\s+from\s+['"]([^'"]+)['"]""",
)
# CommonJS require
_REQUIRE_RE = re.compile(
    r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)

# top-level state variables
_STATE_VAR_RE = re.compile(
    r"^(?:const|let|var)\s+(\w+)\s*=",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord_id(file_path: str, kind: str, name: str, line: int) -> str:
    raw = f"{file_path}::{kind}::{name}::{line}"
    return hashlib.md5(raw.encode()).hexdigest()


def _line_number_at(source: str, pos: int) -> int:
    return source[:pos].count("\n") + 1


# ---------------------------------------------------------------------------
# JavaScriptParser
# ---------------------------------------------------------------------------

class JavaScriptParser:
    """Parse a JavaScript source file and extract coordinates + references."""

    def parse(self, source: str, file_path: str) -> ParseResult:
        t0 = time.monotonic()
        coords: list[ParsedCoordinate] = []
        refs: list[ParsedReference] = []
        errors: list[ParseError] = []

        try:
            coords.extend(self._extract_functions(source, file_path))
            coords.extend(self._extract_fetch_calls(source, file_path))
            coords.extend(self._extract_dom_manipulations(source, file_path))
            coords.extend(self._extract_event_handlers(source, file_path))
            coords.extend(self._extract_state_variables(source, file_path))

            refs.extend(self._extract_class_manipulations(source, file_path))
            refs.extend(self._extract_style_mutations(source, file_path))
            refs.extend(self._extract_imports(source, file_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(ParseError(
                file_path=file_path,
                line_number=0,
                message=f"JS parse error: {exc}",
                severity=ErrorSeverity.ERROR,
            ))

        elapsed = (time.monotonic() - t0) * 1000
        return ParseResult(
            file_path=file_path,
            language=Language.JAVASCRIPT,
            coordinates=coords,
            references=refs,
            errors=errors,
            parse_time_ms=elapsed,
        )

    # -- functions -----------------------------------------------------------

    def _extract_functions(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        seen: set[tuple[str, int]] = set()

        patterns = [
            _FUNC_DECL_RE,
            _ARROW_RE,
            _FUNC_EXPR_RE,
            _ASYNC_FUNC_RE,
            _ASYNC_ARROW_RE,
        ]
        for pat in patterns:
            for m in pat.finditer(source):
                name = m.group(1)
                line = _line_number_at(source, m.start())
                key = (name, line)
                if key in seen:
                    continue
                seen.add(key)
                cid = _make_coord_id(file_path, CoordinateKind.JS_FUNCTION.value, name, line)
                coords.append(ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.JS_FUNCTION,
                    name=name,
                    file_path=file_path,
                    line_number=line,
                    end_line=line,
                    language=Language.JAVASCRIPT,
                ))
        return coords

    # -- fetch / axios -------------------------------------------------------

    def _extract_fetch_calls(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for pat in (_FETCH_RE, _AXIOS_RE):
            for m in pat.finditer(source):
                url = m.group(1)
                line = _line_number_at(source, m.start())
                cid = _make_coord_id(file_path, CoordinateKind.JS_FETCH_CALL.value, url, line)
                coords.append(ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.JS_FETCH_CALL,
                    name=url,
                    file_path=file_path,
                    line_number=line,
                    end_line=line,
                    language=Language.JAVASCRIPT,
                    metadata={"url": url},
                ))
        return coords

    # -- DOM manipulations ---------------------------------------------------

    def _extract_dom_manipulations(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for pat, method in [
            (_GET_ELEM_RE, "getElementById"),
            (_QS_RE, "querySelector"),
            (_QSA_RE, "querySelectorAll"),
        ]:
            for m in pat.finditer(source):
                selector = m.group(1)
                line = _line_number_at(source, m.start())
                cid = _make_coord_id(file_path, CoordinateKind.JS_DOM_MANIPULATION.value, selector, line)
                coords.append(ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.JS_DOM_MANIPULATION,
                    name=selector,
                    file_path=file_path,
                    line_number=line,
                    end_line=line,
                    language=Language.JAVASCRIPT,
                    metadata={"method": method, "selector": selector},
                ))
        return coords

    # -- event handlers ------------------------------------------------------

    def _extract_event_handlers(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _ADD_EVENT_RE.finditer(source):
            event = m.group(1)
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.JS_EVENT_HANDLER.value, event, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.JS_EVENT_HANDLER,
                name=event,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.JAVASCRIPT,
                metadata={"event": event, "method": "addEventListener"},
            ))
        for m in _ON_EVENT_RE.finditer(source):
            event = m.group(2).lower()
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.JS_EVENT_HANDLER.value, event, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.JS_EVENT_HANDLER,
                name=event,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.JAVASCRIPT,
                metadata={"event": event, "method": "on_property"},
            ))
        return coords

    # -- class manipulations (references) ------------------------------------

    def _extract_class_manipulations(self, source: str, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for m in _CLASSLIST_RE.finditer(source):
            method = m.group(1)
            cls_name = m.group(2)
            line = _line_number_at(source, m.start())
            sid = _make_coord_id(file_path, "classList", cls_name, line)
            refs.append(ParsedReference(
                source_id=sid,
                target_name=cls_name,
                reference_type=ReferenceType.CLASS_MANIPULATION,
                file_path=file_path,
                line_number=line,
                metadata={"method": method},
            ))
        return refs

    # -- style mutations (references) ----------------------------------------

    def _extract_style_mutations(self, source: str, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for m in _STYLE_RE.finditer(source):
            prop = m.group(1)
            line = _line_number_at(source, m.start())
            sid = _make_coord_id(file_path, "style", prop, line)
            refs.append(ParsedReference(
                source_id=sid,
                target_name=prop,
                reference_type=ReferenceType.STYLE_MUTATION,
                file_path=file_path,
                line_number=line,
            ))
        return refs

    # -- imports (references) ------------------------------------------------

    def _extract_imports(self, source: str, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        seen: set[tuple[str, int]] = set()
        for pat in (_IMPORT_RE, _REQUIRE_RE):
            for m in pat.finditer(source):
                module = m.group(1)
                line = _line_number_at(source, m.start())
                key = (module, line)
                if key in seen:
                    continue
                seen.add(key)
                sid = _make_coord_id(file_path, "import", module, line)
                refs.append(ParsedReference(
                    source_id=sid,
                    target_name=module,
                    reference_type=ReferenceType.MODULE_IMPORT,
                    file_path=file_path,
                    line_number=line,
                ))
        return refs

    # -- state variables -----------------------------------------------------

    def _extract_state_variables(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _STATE_VAR_RE.finditer(source):
            name = m.group(1)
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.JS_STATE_VARIABLE.value, name, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.JS_STATE_VARIABLE,
                name=name,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.JAVASCRIPT,
            ))
        return coords


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def extract_js_coordinates(source: str, file_path: str) -> ParseResult:
    """Convenience wrapper around ``JavaScriptParser.parse``."""
    return JavaScriptParser().parse(source, file_path)


def extract_dom_references(source: str) -> set[str]:
    """Return all ``getElementById`` / ``querySelector`` argument strings."""
    refs: set[str] = set()
    for pat in (_GET_ELEM_RE, _QS_RE, _QSA_RE):
        for m in pat.finditer(source):
            refs.add(m.group(1))
    return refs


def extract_class_references(source: str) -> set[str]:
    """Return all class names used in ``classList.add/toggle/…``."""
    return {m.group(2) for m in _CLASSLIST_RE.finditer(source)}


def extract_fetch_urls(source: str) -> list[str]:
    """Return all URL strings from ``fetch(…)`` and ``axios.*(…)``."""
    urls: list[str] = []
    for pat in (_FETCH_RE, _AXIOS_RE):
        for m in pat.finditer(source):
            urls.append(m.group(1))
    return urls
