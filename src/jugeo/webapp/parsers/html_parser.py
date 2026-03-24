"""
HTML structure parser.

Uses :mod:`html.parser` from the standard library to extract elements,
forms, links, script/stylesheet references and id/class attributes.
"""
from __future__ import annotations

import hashlib
import html.parser
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
# Helpers
# ---------------------------------------------------------------------------

def _make_coord_id(file_path: str, kind: str, name: str, line: int) -> str:
    raw = f"{file_path}::{kind}::{name}::{line}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# HTMLStructureParser
# ---------------------------------------------------------------------------

class HTMLStructureParser(html.parser.HTMLParser):
    """Feed HTML source and collect coordinates + references."""

    def __init__(self, file_path: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self._file_path = file_path
        self._coords: list[ParsedCoordinate] = []
        self._refs: list[ParsedReference] = []
        self._errors: list[ParseError] = []

        # Form tracking state
        self._in_form = False
        self._current_form_action = ""
        self._current_form_method = "get"
        self._current_form_line = 0
        self._current_form_fields: list[str] = []

    # -- public api ----------------------------------------------------------

    def get_coordinates(self) -> list[ParsedCoordinate]:
        return list(self._coords)

    def get_references(self) -> list[ParsedReference]:
        return list(self._refs)

    def get_errors(self) -> list[ParseError]:
        return list(self._errors)

    # -- html.parser overrides -----------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict: dict[str, str] = {}
        for k, v in attrs:
            attr_dict[k.lower()] = v or ""

        line = self.getpos()[0]

        # Elements with id or class
        elem_id = attr_dict.get("id", "")
        classes_str = attr_dict.get("class", "")
        classes = classes_str.split() if classes_str else []

        if elem_id or classes:
            name = elem_id or f"{tag}.{classes[0]}"
            cid = _make_coord_id(self._file_path, CoordinateKind.HTML_ELEMENT.value, name, line)
            self._coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.HTML_ELEMENT,
                name=name,
                file_path=self._file_path,
                line_number=line,
                end_line=line,
                language=Language.HTML,
                metadata={"tag": tag, "id": elem_id, "classes": classes},
            ))

        # Forms
        if tag == "form":
            self._in_form = True
            self._current_form_action = attr_dict.get("action", "")
            self._current_form_method = attr_dict.get("method", "get").lower()
            self._current_form_line = line
            self._current_form_fields = []
            cid = _make_coord_id(self._file_path, CoordinateKind.HTML_FORM.value,
                                 self._current_form_action or "form", line)
            self._coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.HTML_FORM,
                name=self._current_form_action or "form",
                file_path=self._file_path,
                line_number=line,
                end_line=line,
                language=Language.HTML,
                metadata={
                    "action": self._current_form_action,
                    "method": self._current_form_method,
                },
            ))

        # Input / select / textarea inside form
        if self._in_form and tag in ("input", "select", "textarea"):
            field_name = attr_dict.get("name", "")
            if field_name:
                self._current_form_fields.append(field_name)

        # Links (<a href="...">)
        if tag == "a" and "href" in attr_dict:
            href = attr_dict["href"]
            cid = _make_coord_id(self._file_path, CoordinateKind.HTML_LINK.value, href, line)
            self._coords.append(ParsedCoordinate(
                id=cid,
                kind=CoordinateKind.HTML_LINK,
                name=href,
                file_path=self._file_path,
                line_number=line,
                end_line=line,
                language=Language.HTML,
                metadata={"href": href},
            ))
            sid = _make_coord_id(self._file_path, "href", href, line)
            self._refs.append(ParsedReference(
                source_id=sid,
                target_name=href,
                reference_type=ReferenceType.HTML_HREF,
                file_path=self._file_path,
                line_number=line,
            ))

        # Script references
        if tag == "script" and "src" in attr_dict:
            src = attr_dict["src"]
            sid = _make_coord_id(self._file_path, "script", src, line)
            self._refs.append(ParsedReference(
                source_id=sid,
                target_name=src,
                reference_type=ReferenceType.HTML_SCRIPT,
                file_path=self._file_path,
                line_number=line,
            ))

        # Stylesheet references
        if tag == "link" and attr_dict.get("rel", "") == "stylesheet":
            href = attr_dict.get("href", "")
            if href:
                sid = _make_coord_id(self._file_path, "stylesheet", href, line)
                self._refs.append(ParsedReference(
                    source_id=sid,
                    target_name=href,
                    reference_type=ReferenceType.HTML_STYLESHEET,
                    file_path=self._file_path,
                    line_number=line,
                ))

        # img (recorded as HTML_ELEMENT if it has id/class; always record src)
        if tag == "img" and "src" in attr_dict:
            src = attr_dict["src"]
            if not elem_id and not classes:
                name = f"img:{src}"
                cid = _make_coord_id(self._file_path, CoordinateKind.HTML_ELEMENT.value, name, line)
                self._coords.append(ParsedCoordinate(
                    id=cid,
                    kind=CoordinateKind.HTML_ELEMENT,
                    name=name,
                    file_path=self._file_path,
                    line_number=line,
                    end_line=line,
                    language=Language.HTML,
                    metadata={"tag": "img", "src": src, "id": "", "classes": []},
                ))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_form:
            # Update last form coord with field names
            for coord in reversed(self._coords):
                if coord.kind == CoordinateKind.HTML_FORM:
                    coord.metadata["fields"] = list(self._current_form_fields)
                    break
            self._in_form = False

    def error(self, message: str) -> None:
        self._errors.append(ParseError(
            file_path=self._file_path,
            line_number=self.getpos()[0],
            message=message,
            severity=ErrorSeverity.WARNING,
        ))


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def extract_html_coordinates(source: str, file_path: str) -> ParseResult:
    """Parse *source* as HTML and return a ``ParseResult``."""
    t0 = time.monotonic()
    parser = HTMLStructureParser(file_path)
    errors: list[ParseError] = []
    try:
        parser.feed(source)
    except Exception as exc:  # noqa: BLE001
        errors.append(ParseError(
            file_path=file_path,
            line_number=0,
            message=f"HTML parse error: {exc}",
            severity=ErrorSeverity.ERROR,
        ))

    elapsed = (time.monotonic() - t0) * 1000
    return ParseResult(
        file_path=file_path,
        language=Language.HTML,
        coordinates=parser.get_coordinates(),
        references=parser.get_references(),
        errors=errors + parser.get_errors(),
        parse_time_ms=elapsed,
    )


def extract_html_ids(source: str) -> set[str]:
    """Return the set of ``id`` attribute values in *source*."""
    return set(re.findall(r"""id\s*=\s*['"]([^'"]+)['"]""", source))


def extract_html_classes(source: str) -> set[str]:
    """Return the set of class names used in *source* (space-separated values split)."""
    classes: set[str] = set()
    for m in re.finditer(r"""class\s*=\s*['"]([^'"]+)['"]""", source):
        for cls in m.group(1).split():
            classes.add(cls)
    return classes


def extract_form_actions(source: str) -> list[dict]:
    """Return a list of dicts with *action*, *method*, *fields* for each ``<form>``."""
    parser = HTMLStructureParser("")
    try:
        parser.feed(source)
    except Exception:  # noqa: BLE001
        pass

    forms: list[dict] = []
    for coord in parser.get_coordinates():
        if coord.kind == CoordinateKind.HTML_FORM:
            forms.append({
                "action": coord.metadata.get("action", ""),
                "method": coord.metadata.get("method", "get"),
                "fields": coord.metadata.get("fields", []),
            })
    return forms
