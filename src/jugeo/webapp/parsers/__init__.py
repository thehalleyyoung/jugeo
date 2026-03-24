"""
JuGeo webapp parsers — language-specific parsers for Flask web applications.

Provides coordinate extraction and cross-language reference resolution
for Python/Flask, Jinja2, JavaScript, CSS, HTML, and SQL.
"""
from __future__ import annotations

from .models import (
    CoordinateKind,
    ReferenceType,
    ErrorSeverity,
    Language,
    ParsedCoordinate,
    ParsedReference,
    ParseError,
    ParseResult,
    ProjectParseResult,
)
from .flask_loader import (
    FlaskRouteExtractor,
    extract_flask_coordinates,
    extract_render_template_kwargs,
)
from .jinja2_analyzer import (
    Jinja2TemplateParser,
    extract_template_coordinates,
    extract_template_variables,
    extract_template_blocks,
)
from .javascript_parser import (
    JavaScriptParser,
    extract_js_coordinates,
    extract_dom_references,
    extract_class_references,
    extract_fetch_urls,
)
from .css_analyzer import (
    CSSParser,
    CSSSpecificityCalculator,
    CSSCascadeAnalyzer,
    extract_css_coordinates,
    extract_css_classes,
    extract_css_ids,
    compute_specificity,
)
from .html_parser import (
    HTMLStructureParser,
    extract_html_coordinates,
    extract_html_ids,
    extract_html_classes,
    extract_form_actions,
)
from .sql_schema_loader import (
    SQLSchemaParser,
    extract_sql_coordinates,
    extract_tables,
    extract_foreign_keys,
)
from .project_scanner import (
    FlaskProjectScanner,
    scan_project,
    detect_flask_structure,
    resolve_cross_references,
)
from .integration import (
    coordinate_to_web_coordinate,
    reference_to_morphism,
    build_web_site_from_project,
    ParserPipeline,
)

__all__ = [
    # enums
    "CoordinateKind",
    "ReferenceType",
    "ErrorSeverity",
    "Language",
    # data models
    "ParsedCoordinate",
    "ParsedReference",
    "ParseError",
    "ParseResult",
    "ProjectParseResult",
    # flask
    "FlaskRouteExtractor",
    "extract_flask_coordinates",
    "extract_render_template_kwargs",
    # jinja2
    "Jinja2TemplateParser",
    "extract_template_coordinates",
    "extract_template_variables",
    "extract_template_blocks",
    # javascript
    "JavaScriptParser",
    "extract_js_coordinates",
    "extract_dom_references",
    "extract_class_references",
    "extract_fetch_urls",
    # css
    "CSSParser",
    "CSSSpecificityCalculator",
    "CSSCascadeAnalyzer",
    "extract_css_coordinates",
    "extract_css_classes",
    "extract_css_ids",
    "compute_specificity",
    # html
    "HTMLStructureParser",
    "extract_html_coordinates",
    "extract_html_ids",
    "extract_html_classes",
    "extract_form_actions",
    # sql
    "SQLSchemaParser",
    "extract_sql_coordinates",
    "extract_tables",
    "extract_foreign_keys",
    # project scanner
    "FlaskProjectScanner",
    "scan_project",
    "detect_flask_structure",
    "resolve_cross_references",
    # integration
    "coordinate_to_web_coordinate",
    "reference_to_morphism",
    "build_web_site_from_project",
    "ParserPipeline",
]
