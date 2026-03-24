"""
Jinja2 template parser.

Uses regular expressions (no ``jinja2`` dependency) to extract template
blocks, macros, variables, filters, extends/includes, ``url_for`` calls,
static references, conditionals and embedded HTML elements.
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

_BLOCK_RE = re.compile(
    r"\{%[-\s]*block\s+(\w+)\s*[-]?%\}",
    re.DOTALL,
)
_ENDBLOCK_RE = re.compile(r"\{%[-\s]*endblock\s*[-]?%\}")

_MACRO_RE = re.compile(
    r"\{%[-\s]*macro\s+(\w+)\s*\(([^)]*)\)\s*[-]?%\}",
    re.DOTALL,
)
_ENDMACRO_RE = re.compile(r"\{%[-\s]*endmacro\s*[-]?%\}")

_VARIABLE_RE = re.compile(
    r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)",
)

_FILTER_RE = re.compile(
    r"\|\s*(\w+)",
)

_EXTENDS_RE = re.compile(
    r"""\{%[-\s]*extends\s+['"]([^'"]+)['"]\s*[-]?%\}""",
)

_INCLUDE_RE = re.compile(
    r"""\{%[-\s]*include\s+['"]([^'"]+)['"]\s*[-]?%\}""",
)

_URL_FOR_RE = re.compile(
    r"""\{\{[^}]*url_for\s*\(\s*['"]([^'"]+)['"]""",
)

_STATIC_REF_RE = re.compile(
    r"""url_for\s*\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]""",
)

_IF_RE = re.compile(
    r"\{%[-\s]*if\s+(.+?)\s*[-]?%\}",
)

_HTML_TAG_RE = re.compile(
    r"<(\w+)(\s[^>]*)?>",
    re.DOTALL,
)

_ATTR_ID_RE = re.compile(r"""id\s*=\s*['"]([^'"]+)['"]""")
_ATTR_CLASS_RE = re.compile(r"""class\s*=\s*['"]([^'"]+)['"]""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord_id(file_path: str, kind: str, name: str, line: int) -> str:
    raw = f"{file_path}::{kind}::{name}::{line}"
    return hashlib.md5(raw.encode()).hexdigest()


def _line_number_at(source: str, pos: int) -> int:
    """Return 1-based line number for character offset *pos*."""
    return source[:pos].count("\n") + 1


# ---------------------------------------------------------------------------
# Jinja2TemplateParser
# ---------------------------------------------------------------------------

class Jinja2TemplateParser:
    """Parse a Jinja2 template and extract coordinates + references."""

    def parse(self, source: str, file_path: str) -> ParseResult:
        t0 = time.monotonic()
        coords: list[ParsedCoordinate] = []
        refs: list[ParsedReference] = []
        errors: list[ParseError] = []

        try:
            coords.extend(self._extract_blocks(source, file_path))
            coords.extend(self._extract_macros(source, file_path))
            coords.extend(self._extract_variables(source, file_path))
            coords.extend(self._extract_filters(source, file_path))
            coords.extend(self._extract_conditionals(source, file_path))
            coords.extend(self._extract_html_elements(source, file_path))

            refs.extend(self._extract_extends(source, file_path))
            refs.extend(self._extract_includes(source, file_path))
            refs.extend(self._extract_url_for(source, file_path))
            refs.extend(self._extract_static_refs(source, file_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(ParseError(
                file_path=file_path,
                line_number=0,
                message=f"Template parse error: {exc}",
                severity=ErrorSeverity.ERROR,
            ))

        elapsed = (time.monotonic() - t0) * 1000
        return ParseResult(
            file_path=file_path,
            language=Language.JINJA2,
            coordinates=coords,
            references=refs,
            errors=errors,
            parse_time_ms=elapsed,
        )

    # -- coordinates ---------------------------------------------------------

    def _extract_blocks(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _BLOCK_RE.finditer(source):
            name = m.group(1)
            line = _line_number_at(source, m.start())
            # Attempt to find endblock
            end_match = _ENDBLOCK_RE.search(source, m.end())
            end_line = _line_number_at(source, end_match.start()) if end_match else line
            cid = _make_coord_id(file_path, CoordinateKind.TEMPLATE_BLOCK.value, name, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.TEMPLATE_BLOCK,
                name=name,
                file_path=file_path,
                line_number=line,
                end_line=end_line,
                language=Language.JINJA2,
            ))
        return coords

    def _extract_macros(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _MACRO_RE.finditer(source):
            name = m.group(1)
            args_str = m.group(2).strip()
            args = [a.strip() for a in args_str.split(",") if a.strip()] if args_str else []
            line = _line_number_at(source, m.start())
            end_match = _ENDMACRO_RE.search(source, m.end())
            end_line = _line_number_at(source, end_match.start()) if end_match else line
            cid = _make_coord_id(file_path, CoordinateKind.TEMPLATE_MACRO.value, name, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.TEMPLATE_MACRO,
                name=name,
                file_path=file_path,
                line_number=line,
                end_line=end_line,
                language=Language.JINJA2,
                metadata={"args": args},
            ))
        return coords

    def _extract_variables(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        seen: set[tuple[str, int]] = set()
        for m in _VARIABLE_RE.finditer(source):
            full_name = m.group(1)
            line = _line_number_at(source, m.start())
            key = (full_name, line)
            if key in seen:
                continue
            seen.add(key)
            # Skip url_for(...) – handled separately
            rest = source[m.end():m.end() + 30]
            if full_name == "url_for":
                continue
            cid = _make_coord_id(file_path, CoordinateKind.TEMPLATE_VARIABLE.value, full_name, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.TEMPLATE_VARIABLE,
                name=full_name,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.JINJA2,
            ))
        return coords

    def _extract_filters(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        # Only look inside {{ ... }} expression blocks
        for expr_match in re.finditer(r"\{\{(.*?)\}\}", source, re.DOTALL):
            expr = expr_match.group(1)
            expr_start = expr_match.start()
            for m in _FILTER_RE.finditer(expr):
                name = m.group(1)
                abs_pos = expr_start + m.start()
                line = _line_number_at(source, abs_pos)
                cid = _make_coord_id(file_path, CoordinateKind.TEMPLATE_FILTER.value, name, line)
                coords.append(ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.TEMPLATE_FILTER,
                    name=name,
                    file_path=file_path,
                    line_number=line,
                    end_line=line,
                    language=Language.JINJA2,
                ))
        return coords

    def _extract_conditionals(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _IF_RE.finditer(source):
            condition = m.group(1).strip()
            line = _line_number_at(source, m.start())
            cid = _make_coord_id(file_path, CoordinateKind.CONDITIONAL_RENDER.value, condition, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.CONDITIONAL_RENDER,
                name=condition,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.JINJA2,
                metadata={"condition": condition},
            ))
        return coords

    def _extract_html_elements(self, source: str, file_path: str) -> list[ParsedCoordinate]:
        coords: list[ParsedCoordinate] = []
        for m in _HTML_TAG_RE.finditer(source):
            tag = m.group(1).lower()
            attrs_str = m.group(2) or ""
            line = _line_number_at(source, m.start())
            elem_id = ""
            classes = ""
            id_m = _ATTR_ID_RE.search(attrs_str)
            if id_m:
                elem_id = id_m.group(1)
            cls_m = _ATTR_CLASS_RE.search(attrs_str)
            if cls_m:
                classes = cls_m.group(1)
            # Only record elements with id or class
            if not elem_id and not classes:
                continue
            name = elem_id or f"{tag}.{classes.split()[0]}" if classes else tag
            cid = _make_coord_id(file_path, CoordinateKind.HTML_ELEMENT.value, name, line)
            coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.HTML_ELEMENT,
                name=name,
                file_path=file_path,
                line_number=line,
                end_line=line,
                language=Language.JINJA2,
                metadata={"tag": tag, "id": elem_id, "classes": classes.split() if classes else []},
            ))
        return coords

    # -- references ----------------------------------------------------------

    def _extract_extends(self, source: str, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for m in _EXTENDS_RE.finditer(source):
            parent = m.group(1)
            line = _line_number_at(source, m.start())
            sid = _make_coord_id(file_path, "extends", parent, line)
            refs.append(ParsedReference(
                source_id=sid,
                target_name=parent,
                reference_type=ReferenceType.TEMPLATE_EXTENDS,
                file_path=file_path,
                line_number=line,
            ))
        return refs

    def _extract_includes(self, source: str, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for m in _INCLUDE_RE.finditer(source):
            partial = m.group(1)
            line = _line_number_at(source, m.start())
            sid = _make_coord_id(file_path, "includes", partial, line)
            refs.append(ParsedReference(
                source_id=sid,
                target_name=partial,
                reference_type=ReferenceType.TEMPLATE_INCLUDES,
                file_path=file_path,
                line_number=line,
            ))
        return refs

    def _extract_url_for(self, source: str, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for m in _URL_FOR_RE.finditer(source):
            endpoint = m.group(1)
            if endpoint == "static":
                continue  # handled by _extract_static_refs
            line = _line_number_at(source, m.start())
            sid = _make_coord_id(file_path, "url_for", endpoint, line)
            refs.append(ParsedReference(
                source_id=sid,
                target_name=endpoint,
                reference_type=ReferenceType.TEMPLATE_URL_FOR,
                file_path=file_path,
                line_number=line,
            ))
        return refs

    def _extract_static_refs(self, source: str, file_path: str) -> list[ParsedReference]:
        refs: list[ParsedReference] = []
        for m in _STATIC_REF_RE.finditer(source):
            filename = m.group(1)
            line = _line_number_at(source, m.start())
            sid = _make_coord_id(file_path, "static", filename, line)
            refs.append(ParsedReference(
                source_id=sid,
                target_name=filename,
                reference_type=ReferenceType.STATIC_REFERENCE,
                file_path=file_path,
                line_number=line,
            ))
        return refs


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def extract_template_coordinates(source: str, file_path: str) -> ParseResult:
    """Convenience wrapper around ``Jinja2TemplateParser.parse``."""
    return Jinja2TemplateParser().parse(source, file_path)


def extract_template_variables(source: str) -> set[str]:
    """Return the set of top-level variable names used in *source*.

    For ``{{ post.author.name }}``, the top-level name is ``post``.
    """
    names: set[str] = set()
    for m in _VARIABLE_RE.finditer(source):
        full = m.group(1)
        if full == "url_for":
            continue
        top = full.split(".")[0]
        names.add(top)
    return names


def extract_template_blocks(source: str) -> list[str]:
    """Return a list of block names found in *source*."""
    return [m.group(1) for m in _BLOCK_RE.finditer(source)]
