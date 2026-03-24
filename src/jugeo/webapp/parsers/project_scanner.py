"""
Flask project scanner.

Walks a directory tree, dispatches the appropriate language parser for each
file and assembles a ``ProjectParseResult`` with cross-language references.
"""
from __future__ import annotations

import os
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
    ProjectParseResult,
    ReferenceType,
)
from .flask_loader import extract_flask_coordinates
from .jinja2_analyzer import extract_template_coordinates
from .javascript_parser import extract_js_coordinates
from .css_analyzer import extract_css_coordinates
from .html_parser import extract_html_coordinates
from .sql_schema_loader import extract_sql_coordinates

# ---------------------------------------------------------------------------
# File-extension → language mapping
# ---------------------------------------------------------------------------

_EXT_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".html": Language.HTML,
    ".htm": Language.HTML,
    ".jinja": Language.JINJA2,
    ".jinja2": Language.JINJA2,
    ".j2": Language.JINJA2,
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".css": Language.CSS,
    ".sql": Language.SQL,
}

_SKIP_DIRS = {
    "__pycache__", ".git", "node_modules", ".tox", ".mypy_cache",
    ".pytest_cache", "venv", ".venv", "env", ".env", "dist", "build",
    "egg-info",
}


def _language_for(file_path: str, source: str | None = None) -> Language:
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    lang = _EXT_MAP.get(ext, Language.UNKNOWN)
    # .html files inside a templates/ directory are treated as Jinja2
    if lang == Language.HTML:
        norm = file_path.replace("\\", "/")
        if "/templates/" in norm or norm.startswith("templates/"):
            lang = Language.JINJA2
    return lang


def _parse_file(file_path: str, source: str, lang: Language) -> ParseResult:
    if lang == Language.PYTHON:
        return extract_flask_coordinates(source, file_path)
    if lang == Language.JINJA2:
        return extract_template_coordinates(source, file_path)
    if lang == Language.JAVASCRIPT:
        return extract_js_coordinates(source, file_path)
    if lang == Language.CSS:
        return extract_css_coordinates(source, file_path)
    if lang == Language.HTML:
        return extract_html_coordinates(source, file_path)
    if lang == Language.SQL:
        return extract_sql_coordinates(source, file_path)
    # Unknown – return empty
    return ParseResult(file_path=file_path, language=lang)


# ---------------------------------------------------------------------------
# FlaskProjectScanner
# ---------------------------------------------------------------------------

class FlaskProjectScanner:
    """Walk a project directory, parse all recognised files and collect
    cross-language references."""

    def scan(self, root_dir: str) -> ProjectParseResult:
        results: list[ParseResult] = []

        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, root_dir)
                lang = _language_for(rel)
                if lang == Language.UNKNOWN:
                    continue
                try:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        source = fh.read()
                except OSError:
                    continue
                # For Python files, only parse Flask-related files unless small
                if lang == Language.PYTHON and not self._is_flask_file(rel, source):
                    continue
                result = _parse_file(rel, source, lang)
                results.append(result)

        all_coords: list[ParsedCoordinate] = []
        all_refs: list[ParsedReference] = []
        all_errors: list[ParseError] = []
        for r in results:
            all_coords.extend(r.coordinates)
            all_refs.extend(r.references)
            all_errors.extend(r.errors)

        cross = self._collect_cross_refs(results)

        return ProjectParseResult(
            files=results,
            all_coordinates=all_coords,
            all_references=all_refs,
            cross_language_refs=cross,
            errors=all_errors,
        )

    @staticmethod
    def _is_flask_file(file_path: str, source: str) -> bool:
        """Return ``True`` if the Python source looks Flask-related."""
        indicators = [
            "from flask",
            "import flask",
            "Flask(",
            "Blueprint(",
            "app.route",
            "render_template",
            "db.Model",
            "FlaskForm",
            "@app.",
        ]
        for indicator in indicators:
            if indicator in source:
                return True
        return False

    @staticmethod
    def _collect_cross_refs(results: list[ParseResult]) -> list[ParsedReference]:
        """Identify references that cross language boundaries."""
        cross: list[ParsedReference] = []
        # Build language index per file
        lang_map: dict[str, Language] = {r.file_path: r.language for r in results}

        cross_types = {
            ReferenceType.RENDERS_TEMPLATE,
            ReferenceType.STATIC_REFERENCE,
            ReferenceType.TEMPLATE_URL_FOR,
            ReferenceType.TEMPLATE_EXTENDS,
            ReferenceType.TEMPLATE_INCLUDES,
            ReferenceType.HTML_SCRIPT,
            ReferenceType.HTML_STYLESHEET,
        }
        for r in results:
            for ref in r.references:
                if ref.reference_type in cross_types:
                    cross.append(ref)
        return cross


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def scan_project(root_dir: str) -> ProjectParseResult:
    """Convenience wrapper around ``FlaskProjectScanner.scan``."""
    return FlaskProjectScanner().scan(root_dir)


def detect_flask_structure(root_dir: str) -> dict:
    """Detect the Flask project layout and return key paths."""
    structure: dict = {
        "app_file": None,
        "template_dir": None,
        "static_dir": None,
        "model_files": [],
        "blueprint_files": [],
    }

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root_dir)

        # Template / static dirs
        if os.path.basename(dirpath) == "templates":
            structure["template_dir"] = rel_dir
        if os.path.basename(dirpath) == "static":
            structure["static_dir"] = rel_dir

        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root_dir)
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
            except OSError:
                continue

            if "Flask(__name__)" in source or "create_app" in source:
                structure["app_file"] = rel
            if "db.Model" in source or "class.*Model" in source:
                structure["model_files"].append(rel)
            if "Blueprint(" in source:
                structure["blueprint_files"].append(rel)

    return structure


def resolve_cross_references(results: list[ParseResult]) -> list[ParsedReference]:
    """Match cross-language references to their targets.

    * ``RENDERS_TEMPLATE`` → match target_name against HTML/Jinja2 file paths
    * ``DOM_ACCESS`` → match selector against HTML element ids
    * ``CLASS_MANIPULATION`` → match class name against CSS rule selectors
    """
    resolved: list[ParsedReference] = []

    # Build lookup structures
    html_files: set[str] = set()
    html_ids: set[str] = set()
    css_classes: set[str] = set()

    for r in results:
        if r.language in (Language.HTML, Language.JINJA2):
            html_files.add(os.path.basename(r.file_path))
            for coord in r.coordinates:
                if coord.kind == CoordinateKind.HTML_ELEMENT:
                    eid = coord.metadata.get("id", "")
                    if eid:
                        html_ids.add(eid)
        if r.language == Language.CSS:
            for coord in r.coordinates:
                if coord.kind == CoordinateKind.CSS_RULE:
                    import re as _re
                    for m in _re.finditer(r"\.[\w-]+", coord.name):
                        css_classes.add(m.group(0)[1:])

    for r in results:
        for ref in r.references:
            if ref.reference_type == ReferenceType.RENDERS_TEMPLATE:
                tpl_base = os.path.basename(ref.target_name)
                if tpl_base in html_files:
                    resolved.append(ref)
            elif ref.reference_type == ReferenceType.DOM_ACCESS:
                if ref.target_name in html_ids:
                    resolved.append(ref)
            elif ref.reference_type == ReferenceType.CLASS_MANIPULATION:
                if ref.target_name in css_classes:
                    resolved.append(ref)

    return resolved
