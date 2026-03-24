"""Obligation presheaf — shared typed quality enforcement for all generators.

From the JG perspective, an *obligation* is a stalk of the obligation
presheaf O over the application site.  Each stalk specifies a lower
bound on a measurable quality metric.  The generator checks descent of
the produced output against this presheaf: if any obligation is unmet,
the enricher repairs the obstruction.

This module provides:
  - The abstract obligation types (shared by HTML-only and Flask generators)
  - The checker protocol (measures a spec against obligations)
  - The enricher protocol (repairs unmet obligations)
  - Preset obligation levels (minimal, standard, stunning)
  - A unified ``GenerationTarget`` enum for choosing Flask vs HTML-only

Both generators import from here rather than defining their own types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeVar


# ══════════════════════════════════════════════════════════════════════
# Generation target — Flask or HTML-only
# ══════════════════════════════════════════════════════════════════════

class GenerationTarget(str, Enum):
    FLASK = "flask"
    HTML_ONLY = "html_only"


# ══════════════════════════════════════════════════════════════════════
# Unified obligation kinds — superset covering both Flask and HTML apps
# ══════════════════════════════════════════════════════════════════════

class ObligationKind(str, Enum):
    """All measurable quality dimensions, shared across generators."""

    # ── Visual / CSS ──
    ANIMATION_COUNT = "animation_count"
    COLOR_RICHNESS = "color_richness"
    RESPONSIVE_BREAKPOINTS = "responsive_breakpoints"
    TYPOGRAPHY_QUALITY = "typography_quality"
    VISUAL_HIERARCHY = "visual_hierarchy"
    CSS_LINE_COUNT = "css_line_count"

    # ── Interactivity / JS ──
    INTERACTIVITY_SCORE = "interactivity_score"
    JAVASCRIPT_FEATURES = "javascript_features"
    JS_LINE_COUNT = "js_line_count"

    # ── Content / Structure ──
    CONTENT_DENSITY = "content_density"
    COMPONENT_VARIETY = "component_variety"
    NAVIGATION_DEPTH = "navigation_depth"
    HTML_LINE_COUNT = "html_line_count"
    TOTAL_LINE_COUNT = "total_line_count"

    # ── Scale / Comprehensiveness (JG: structural completeness across fibers) ──
    FEATURE_SYSTEM_COUNT = "feature_system_count"
    MODULE_COUNT = "module_count"
    ALGORITHM_VARIETY = "algorithm_variety"
    INTERACTION_PATTERN_COUNT = "interaction_pattern_count"

    # ── Flask-specific ──
    ROUTE_COUNT = "route_count"
    MODEL_COUNT = "model_count"
    TEMPLATE_COUNT = "template_count"
    STATIC_FILE_COUNT = "static_file_count"
    API_ENDPOINT_COUNT = "api_endpoint_count"
    FORM_COUNT = "form_count"
    AUTH_PRESENT = "auth_present"
    ERROR_HANDLING = "error_handling"
    CRUD_COMPLETENESS = "crud_completeness"
    DATABASE_SCHEMA_DEPTH = "database_schema_depth"
    BLUEPRINT_COUNT = "blueprint_count"
    OVERALL_FILE_COUNT = "overall_file_count"


# ══════════════════════════════════════════════════════════════════════
# Obligation / Result / Report — the core value types
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Obligation:
    """A single measurable quality obligation (stalk of the presheaf)."""
    kind: ObligationKind
    minimum: float
    description: str = ""
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "minimum": self.minimum,
            "description": self.description,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Obligation":
        return cls(
            kind=ObligationKind(data["kind"]),
            minimum=data["minimum"],
            description=data.get("description", ""),
            weight=data.get("weight", 1.0),
        )


@dataclass
class ObligationResult:
    """Result of checking a single obligation."""
    obligation: Obligation
    actual: float
    met: bool
    deficit: float = 0.0

    def to_dict(self) -> dict:
        return {
            "kind": self.obligation.kind.value,
            "minimum": self.obligation.minimum,
            "actual": self.actual,
            "met": self.met,
            "deficit": self.deficit,
        }


@dataclass
class ObligationReport:
    """Full obligation audit for a generated app."""
    results: list[ObligationResult]
    all_met: bool
    enrichment_rounds: int = 0

    def to_dict(self) -> dict:
        return {
            "results": [r.to_dict() for r in self.results],
            "all_met": self.all_met,
            "enrichment_rounds": self.enrichment_rounds,
        }

    @property
    def unmet(self) -> list[ObligationResult]:
        return [r for r in self.results if not r.met]


# ══════════════════════════════════════════════════════════════════════
# Checker / Enricher protocols
# ══════════════════════════════════════════════════════════════════════

SpecT = TypeVar("SpecT")


class ObligationChecker(Protocol[SpecT]):
    """Protocol: measures a spec against obligations."""

    def check(self, spec: SpecT, obligations: list[Obligation]) -> ObligationReport: ...


class SpecEnricher(Protocol[SpecT]):
    """Protocol: enriches a spec to meet unmet obligations."""

    def enrich(self, spec: SpecT, unmet: list[ObligationResult]) -> SpecT: ...


# ══════════════════════════════════════════════════════════════════════
# Obligation enforcement loop (shared by both generators)
# ══════════════════════════════════════════════════════════════════════

def enforce_obligations(
    spec: SpecT,
    obligations: list[Obligation],
    checker: Any,  # ObligationChecker[SpecT]
    enricher: Any,  # SpecEnricher[SpecT]
    max_rounds: int = 5,
) -> tuple[SpecT, ObligationReport]:
    """Run the check→enrich loop until all obligations pass.

    Returns the (possibly enriched) spec and the final report.
    """
    rounds = 0
    report = checker.check(spec, obligations)
    while not report.all_met and rounds < max_rounds:
        spec = enricher.enrich(spec, report.unmet)
        rounds += 1
        report = checker.check(spec, obligations)
    report.enrichment_rounds = rounds
    return spec, report


# ══════════════════════════════════════════════════════════════════════
# Preset obligation sets
# ══════════════════════════════════════════════════════════════════════

def _html_minimal() -> list[Obligation]:
    return [
        Obligation(ObligationKind.COMPONENT_VARIETY, 2, "At least 2 distinct component types"),
        Obligation(ObligationKind.HTML_LINE_COUNT, 30, "At least 30 lines of HTML"),
        Obligation(ObligationKind.CSS_LINE_COUNT, 50, "At least 50 lines of CSS"),
    ]


def _html_standard() -> list[Obligation]:
    return [
        Obligation(ObligationKind.ANIMATION_COUNT, 3, "At least 3 CSS animations"),
        Obligation(ObligationKind.INTERACTIVITY_SCORE, 4, "At least 4 interactive features"),
        Obligation(ObligationKind.CONTENT_DENSITY, 8, "At least 8 content sections"),
        Obligation(ObligationKind.COLOR_RICHNESS, 5, "At least 5 distinct colors"),
        Obligation(ObligationKind.COMPONENT_VARIETY, 5, "At least 5 component types"),
        Obligation(ObligationKind.RESPONSIVE_BREAKPOINTS, 2, "At least 2 breakpoints"),
        Obligation(ObligationKind.NAVIGATION_DEPTH, 3, "At least 3 nav targets"),
        Obligation(ObligationKind.JAVASCRIPT_FEATURES, 4, "At least 4 JS features"),
        Obligation(ObligationKind.CSS_LINE_COUNT, 150, "150+ lines CSS"),
        Obligation(ObligationKind.JS_LINE_COUNT, 80, "80+ lines JS"),
        Obligation(ObligationKind.HTML_LINE_COUNT, 80, "80+ lines HTML"),
    ]


def _html_stunning() -> list[Obligation]:
    return [
        Obligation(ObligationKind.ANIMATION_COUNT, 8,
                   "Rich animations: particles, scroll reveals, hovers, transitions, shimmer, gradients, lift, glow", 2.0),
        Obligation(ObligationKind.INTERACTIVITY_SCORE, 10,
                   "Deep interactivity: SPA routing, localStorage, modals, toasts, tabs, accordion, validation, canvas, keyboard, drag", 2.0),
        Obligation(ObligationKind.CONTENT_DENSITY, 15,
                   "Dense content: hero, features, stats, code examples, demos, tables, visualizations", 2.0),
        Obligation(ObligationKind.COLOR_RICHNESS, 8, "Rich palette: primary, accent, success, warning, danger, gradients"),
        Obligation(ObligationKind.COMPONENT_VARIETY, 10, "Diverse components: navbar, hero, cards, tables, charts, modals, tabs, accordion, toasts, code"),
        Obligation(ObligationKind.RESPONSIVE_BREAKPOINTS, 3, "Full responsive: mobile, tablet, desktop"),
        Obligation(ObligationKind.NAVIGATION_DEPTH, 5, "At least 5 navigation targets"),
        Obligation(ObligationKind.JAVASCRIPT_FEATURES, 8, "Advanced JS: router, store, canvas, observer, modals, toasts, keyboard, validation"),
        Obligation(ObligationKind.TYPOGRAPHY_QUALITY, 3, "Good typography: sizes, weights, line-heights"),
        Obligation(ObligationKind.VISUAL_HIERARCHY, 4, "Clear hierarchy: hero > sections > cards > body"),
        Obligation(ObligationKind.CSS_LINE_COUNT, 250, "250+ lines CSS"),
        Obligation(ObligationKind.JS_LINE_COUNT, 200, "200+ lines JS"),
        Obligation(ObligationKind.HTML_LINE_COUNT, 150, "150+ lines HTML"),
        Obligation(ObligationKind.TOTAL_LINE_COUNT, 800, "800+ total lines", 1.5),
    ]


def _flask_minimal() -> list[Obligation]:
    return [
        Obligation(ObligationKind.ROUTE_COUNT, 2, "At least 2 routes"),
        Obligation(ObligationKind.TEMPLATE_COUNT, 2, "At least 2 templates"),
        Obligation(ObligationKind.OVERALL_FILE_COUNT, 5, "At least 5 files"),
    ]


def _flask_standard() -> list[Obligation]:
    return [
        Obligation(ObligationKind.ROUTE_COUNT, 6, "6+ routes (index + CRUD)"),
        Obligation(ObligationKind.MODEL_COUNT, 1, "At least 1 model"),
        Obligation(ObligationKind.TEMPLATE_COUNT, 5, "5+ templates"),
        Obligation(ObligationKind.STATIC_FILE_COUNT, 1, "Custom CSS"),
        Obligation(ObligationKind.NAVIGATION_DEPTH, 3, "3+ navigable pages"),
        Obligation(ObligationKind.ERROR_HANDLING, 2, "404 + 500 errors"),
        Obligation(ObligationKind.OVERALL_FILE_COUNT, 10, "10+ files"),
    ]


def _flask_stunning() -> list[Obligation]:
    return [
        Obligation(ObligationKind.ROUTE_COUNT, 10,
                   "Rich routing: index, dashboard, CRUD, API, search, about, settings", 2.0),
        Obligation(ObligationKind.MODEL_COUNT, 3,
                   "Multiple related models with FKs and indexes", 2.0),
        Obligation(ObligationKind.TEMPLATE_COUNT, 10,
                   "Comprehensive templates: base, pages, forms, errors", 2.0),
        Obligation(ObligationKind.STATIC_FILE_COUNT, 3, "Rich statics: CSS, JS, charts"),
        Obligation(ObligationKind.API_ENDPOINT_COUNT, 3, "JSON API endpoints"),
        Obligation(ObligationKind.FORM_COUNT, 2, "2+ forms"),
        Obligation(ObligationKind.NAVIGATION_DEPTH, 5, "5+ navigable pages"),
        Obligation(ObligationKind.CRUD_COMPLETENESS, 5, "Full CRUD per model"),
        Obligation(ObligationKind.DATABASE_SCHEMA_DEPTH, 5, "5+ columns per model"),
        Obligation(ObligationKind.ERROR_HANDLING, 2, "Error templates"),
        Obligation(ObligationKind.OVERALL_FILE_COUNT, 16, "16+ files total", 1.5),
        # Flask apps also get the visual obligations for their CSS/JS/templates
        Obligation(ObligationKind.CSS_LINE_COUNT, 100, "100+ lines app CSS"),
        Obligation(ObligationKind.JS_LINE_COUNT, 80, "80+ lines app JS"),
    ]


def _html_production() -> list[Obligation]:
    """Production-scale obligations for 20K+ LOC HTML apps.

    From the JG perspective, production scale is not just "more lines" —
    it is structural completeness of the application sheaf.  The obligation
    presheaf at this level measures coverage across *all* fibers: algorithmic
    depth, feature variety, interaction richness, visual sophistication.
    A bare line-count obligation would be the counting measure; instead we
    require the *spectral* measure — completeness across distinct dimensions.
    """
    return [
        # ── Scale: the counting measure ──
        Obligation(ObligationKind.TOTAL_LINE_COUNT, 20000,
                   "Production scale: 20K+ total lines", 3.0),
        Obligation(ObligationKind.JS_LINE_COUNT, 14000,
                   "Substantial JS codebase: engines, algorithms, UI", 2.5),
        Obligation(ObligationKind.CSS_LINE_COUNT, 3500,
                   "Rich design system with full component styles", 2.0),
        Obligation(ObligationKind.HTML_LINE_COUNT, 1500,
                   "Dense structured markup with multiple views", 1.5),
        # ── Spectral: completeness across fibers ──
        Obligation(ObligationKind.FEATURE_SYSTEM_COUNT, 12,
                   "12+ distinct feature systems (engine, renderer, AI, audio, etc.)", 2.5),
        Obligation(ObligationKind.MODULE_COUNT, 18,
                   "18+ code modules / namespaces", 2.0),
        Obligation(ObligationKind.ALGORITHM_VARIETY, 8,
                   "8+ distinct algorithms (noise, cellular, fractal, pathfinding, etc.)", 2.5),
        Obligation(ObligationKind.INTERACTION_PATTERN_COUNT, 8,
                   "8+ interaction patterns (click, drag, keyboard, touch, scroll, resize, contextmenu, wheel)", 2.0),
        # ── Visual quality (inherited from stunning) ──
        Obligation(ObligationKind.ANIMATION_COUNT, 15,
                   "Rich animations throughout", 1.5),
        Obligation(ObligationKind.INTERACTIVITY_SCORE, 14,
                   "Deep interactivity across all features", 2.0),
        Obligation(ObligationKind.COMPONENT_VARIETY, 10,
                   "Diverse component palette", 1.5),
        Obligation(ObligationKind.RESPONSIVE_BREAKPOINTS, 4,
                   "Full responsive: mobile, tablet, desktop, wide", 1.0),
        Obligation(ObligationKind.NAVIGATION_DEPTH, 6,
                   "6+ navigation targets", 1.0),
        Obligation(ObligationKind.COLOR_RICHNESS, 12,
                   "Rich palette with semantic colors and gradients", 1.0),
    ]


def _flask_production() -> list[Obligation]:
    """Production-scale Flask obligations — the full fiber sheaf (20K+)."""
    return [
        Obligation(ObligationKind.TOTAL_LINE_COUNT, 20000,
                   "Production scale: 20K+ total lines", 3.0),
        Obligation(ObligationKind.ROUTE_COUNT, 30,
                   "30+ routes covering CRUD, API, pages, auth", 2.0),
        Obligation(ObligationKind.MODEL_COUNT, 6,
                   "6+ data models with relationships", 2.0),
        Obligation(ObligationKind.TEMPLATE_COUNT, 20,
                   "20+ templates: layouts, pages, forms, partials, errors", 2.0),
        Obligation(ObligationKind.STATIC_FILE_COUNT, 10,
                   "10+ static files: CSS, JS modules, charts, forms", 1.5),
        Obligation(ObligationKind.API_ENDPOINT_COUNT, 12,
                   "12+ JSON API endpoints", 2.0),
        Obligation(ObligationKind.CSS_LINE_COUNT, 3500,
                   "Rich design system", 2.0),
        Obligation(ObligationKind.JS_LINE_COUNT, 14000,
                   "Substantial client-side codebase", 2.5),
        Obligation(ObligationKind.FEATURE_SYSTEM_COUNT, 12,
                   "12+ distinct feature systems", 2.5),
        Obligation(ObligationKind.MODULE_COUNT, 18,
                   "18+ code modules", 2.0),
        Obligation(ObligationKind.ALGORITHM_VARIETY, 8,
                   "8+ distinct algorithms", 2.5),
        Obligation(ObligationKind.CRUD_COMPLETENESS, 18,
                   "Full CRUD across all models", 2.0),
        Obligation(ObligationKind.DATABASE_SCHEMA_DEPTH, 7,
                   "Deep schema: 7+ columns per model", 1.5),
        Obligation(ObligationKind.NAVIGATION_DEPTH, 10,
                   "10+ navigable pages", 1.5),
        Obligation(ObligationKind.OVERALL_FILE_COUNT, 40,
                   "40+ files total", 1.5),
    ]


OBLIGATION_PRESETS: dict[str, dict[str, list[Obligation]]] = {
    "minimal": {
        "html_only": _html_minimal(),
        "flask": _flask_minimal(),
    },
    "standard": {
        "html_only": _html_standard(),
        "flask": _flask_standard(),
    },
    "stunning": {
        "html_only": _html_stunning(),
        "flask": _flask_stunning(),
    },
    "production": {
        "html_only": _html_production(),
        "flask": _flask_production(),
    },
}


def get_obligations(
    level: str = "stunning",
    target: GenerationTarget | str = GenerationTarget.HTML_ONLY,
) -> list[Obligation]:
    """Get obligation list for a given level and target."""
    if isinstance(target, str):
        target_key = target
    else:
        target_key = target.value
    presets = OBLIGATION_PRESETS.get(level, OBLIGATION_PRESETS["stunning"])
    return presets.get(target_key, presets.get("html_only", []))


def resolve_obligations(
    obligations: str | list[Obligation] | None,
    target: GenerationTarget | str = GenerationTarget.HTML_ONLY,
    default: str = "stunning",
) -> list[Obligation]:
    """Resolve obligations from a preset name, explicit list, or None."""
    if obligations is None:
        return get_obligations(default, target)
    if isinstance(obligations, str):
        return get_obligations(obligations, target)
    return obligations
