"""
Integration helpers – convert parsed coordinates and references into the
JuGeo web-site representation (coordinates → web coordinates, references →
morphisms) and provide a high-level ``ParserPipeline``.
"""
from __future__ import annotations

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
from .project_scanner import scan_project, FlaskProjectScanner


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------

def coordinate_to_web_coordinate(parsed: ParsedCoordinate) -> dict:
    """Convert a ``ParsedCoordinate`` to a plain dict suitable for the web-site
    representation."""
    return {
        "id": parsed.id,
        "kind": parsed.kind.value,
        "name": parsed.name,
        "file_path": parsed.file_path,
        "line_number": parsed.line_number,
        "language": parsed.language.value,
        "metadata": parsed.metadata,
    }


def reference_to_morphism(ref: ParsedReference) -> dict:
    """Convert a ``ParsedReference`` to a morphism dict."""
    return {
        "source_id": ref.source_id,
        "target_name": ref.target_name,
        "morphism_type": ref.reference_type.value,
        "file_path": ref.file_path,
        "line_number": ref.line_number,
    }


def build_web_site_from_project(project_result: ProjectParseResult) -> dict:
    """Build a web-site dict from a ``ProjectParseResult``."""
    coordinates = [coordinate_to_web_coordinate(c) for c in project_result.all_coordinates]
    morphisms = [reference_to_morphism(r) for r in project_result.all_references]
    cross = [reference_to_morphism(r) for r in project_result.cross_language_refs]
    return {
        "coordinates": coordinates,
        "morphisms": morphisms,
        "cross_language_morphisms": cross,
        "error_count": len(project_result.errors),
        "file_count": len(project_result.files),
    }


# ---------------------------------------------------------------------------
# ParserPipeline
# ---------------------------------------------------------------------------

class ParserPipeline:
    """High-level orchestrator: scan a project directory and produce a
    web-site dict."""

    def __init__(self, root_dir: str) -> None:
        self._root_dir = root_dir
        self._project_result: ProjectParseResult | None = None
        self._site: dict | None = None

    def run(self) -> dict:
        """Execute the full pipeline and return the site dict."""
        self._project_result = scan_project(self._root_dir)
        self._site = build_web_site_from_project(self._project_result)
        return self._site

    def get_coordinates_by_kind(self, kind: CoordinateKind) -> list[dict]:
        """Return coordinates of a given kind (calls ``run`` if needed)."""
        if self._site is None:
            self.run()
        assert self._site is not None
        return [c for c in self._site["coordinates"] if c["kind"] == kind.value]

    def get_cross_language_references(self) -> list[dict]:
        """Return cross-language morphisms (calls ``run`` if needed)."""
        if self._site is None:
            self.run()
        assert self._site is not None
        return self._site["cross_language_morphisms"]
