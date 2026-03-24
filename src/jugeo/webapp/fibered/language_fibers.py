"""Concrete language-fiber descriptors.

Each class is a lightweight descriptor for one language fiber in the
web-application fibered category.  Class attributes enumerate the
coordinate kinds, internal morphisms, and descent conditions specific
to that language.

Usage::

    from jugeo.webapp.fibered.language_fibers import PythonFiber

    fiber = PythonFiber.get_fiber()
    assert fiber.name == "python"
"""
from __future__ import annotations

from .models import LanguageFiber


# ---------------------------------------------------------------------------
# PythonFiber
# ---------------------------------------------------------------------------

class PythonFiber:
    """Descriptor for the Python (server-side) fiber."""

    coordinate_kinds: list[str] = [
        "ROUTE_HANDLER",
        "VIEW_FUNCTION",
        "MODEL_CLASS",
        "FORM_CLASS",
        "MIDDLEWARE",
        "BLUEPRINT",
        "CONFIG_KEY",
        "ERROR_HANDLER",
    ]

    internal_morphisms: list[str] = [
        "function_call",
        "class_inheritance",
        "import_dependency",
        "decorator_application",
        "module_reference",
    ]

    internal_descent_conditions: list[str] = [
        "type_consistency",
        "import_resolution",
        "route_uniqueness",
        "model_field_types",
    ]

    INSTANCE: LanguageFiber = LanguageFiber(
        name="python",
        coordinate_kinds=list(coordinate_kinds),
        morphism_kinds=list(internal_morphisms),
        internal_topology={"type": "grothendieck", "coverage": "open_cover"},
        description="Server-side Python code (Flask / Django / FastAPI).",
    )

    @classmethod
    def get_fiber(cls) -> LanguageFiber:
        """Return the singleton ``LanguageFiber`` for Python."""
        return cls.INSTANCE


# ---------------------------------------------------------------------------
# JavaScriptFiber
# ---------------------------------------------------------------------------

class JavaScriptFiber:
    """Descriptor for the JavaScript (client-side) fiber."""

    coordinate_kinds: list[str] = [
        "JS_MODULE",
        "JS_FUNCTION",
        "JS_EVENT_HANDLER",
        "JS_FETCH_CALL",
        "JS_DOM_MANIPULATION",
        "JS_STATE_VARIABLE",
    ]

    internal_morphisms: list[str] = [
        "function_call",
        "event_binding",
        "fetch_request",
        "dom_query",
        "module_import",
    ]

    internal_descent_conditions: list[str] = [
        "dom_id_existence",
        "fetch_endpoint_existence",
        "event_handler_binding",
    ]

    INSTANCE: LanguageFiber = LanguageFiber(
        name="javascript",
        coordinate_kinds=list(coordinate_kinds),
        morphism_kinds=list(internal_morphisms),
        internal_topology={"type": "grothendieck", "coverage": "open_cover"},
        description="Client-side JavaScript / TypeScript code.",
    )

    @classmethod
    def get_fiber(cls) -> LanguageFiber:
        """Return the singleton ``LanguageFiber`` for JavaScript."""
        return cls.INSTANCE


# ---------------------------------------------------------------------------
# CSSFiber
# ---------------------------------------------------------------------------

class CSSFiber:
    """Descriptor for the CSS fiber."""

    coordinate_kinds: list[str] = [
        "CSS_STYLESHEET",
        "CSS_RULE",
        "CSS_PROPERTY",
        "CSS_MEDIA_QUERY",
        "CSS_ANIMATION",
    ]

    internal_morphisms: list[str] = [
        "rule_cascade",
        "selector_specificity",
        "media_query_application",
        "animation_reference",
    ]

    internal_descent_conditions: list[str] = [
        "selector_validity",
        "property_validity",
        "class_existence",
        "animation_keyframe_completeness",
    ]

    INSTANCE: LanguageFiber = LanguageFiber(
        name="css",
        coordinate_kinds=list(coordinate_kinds),
        morphism_kinds=list(internal_morphisms),
        internal_topology={"type": "grothendieck", "coverage": "cascade"},
        description="CSS stylesheets and rules.",
    )

    @classmethod
    def get_fiber(cls) -> LanguageFiber:
        """Return the singleton ``LanguageFiber`` for CSS."""
        return cls.INSTANCE


# ---------------------------------------------------------------------------
# HTMLFiber
# ---------------------------------------------------------------------------

class HTMLFiber:
    """Descriptor for the HTML fiber."""

    coordinate_kinds: list[str] = [
        "HTML_ELEMENT",
        "HTML_ATTRIBUTE",
        "HTML_FORM",
        "HTML_LINK",
        "HTML_SCRIPT",
        "HTML_STYLE",
    ]

    internal_morphisms: list[str] = [
        "element_containment",
        "attribute_binding",
        "href_reference",
        "src_reference",
    ]

    internal_descent_conditions: list[str] = [
        "well_formedness",
        "id_uniqueness",
        "href_target_existence",
        "alt_text_presence",
    ]

    INSTANCE: LanguageFiber = LanguageFiber(
        name="html",
        coordinate_kinds=list(coordinate_kinds),
        morphism_kinds=list(internal_morphisms),
        internal_topology={"type": "grothendieck", "coverage": "dom_tree"},
        description="HTML document structure.",
    )

    @classmethod
    def get_fiber(cls) -> LanguageFiber:
        """Return the singleton ``LanguageFiber`` for HTML."""
        return cls.INSTANCE


# ---------------------------------------------------------------------------
# SQLFiber
# ---------------------------------------------------------------------------

class SQLFiber:
    """Descriptor for the SQL (database schema) fiber."""

    coordinate_kinds: list[str] = [
        "DB_TABLE",
        "DB_COLUMN",
        "DB_CONSTRAINT",
        "DB_INDEX",
        "DB_MIGRATION",
        "DB_VIEW",
    ]

    internal_morphisms: list[str] = [
        "foreign_key",
        "index_on_column",
        "constraint_on_column",
        "view_definition",
    ]

    internal_descent_conditions: list[str] = [
        "referential_integrity",
        "column_type_consistency",
        "constraint_satisfiability",
        "migration_completeness",
    ]

    INSTANCE: LanguageFiber = LanguageFiber(
        name="sql",
        coordinate_kinds=list(coordinate_kinds),
        morphism_kinds=list(internal_morphisms),
        internal_topology={"type": "grothendieck", "coverage": "schema"},
        description="SQL database schema and migrations.",
    )

    @classmethod
    def get_fiber(cls) -> LanguageFiber:
        """Return the singleton ``LanguageFiber`` for SQL."""
        return cls.INSTANCE


# ---------------------------------------------------------------------------
# TemplateFiber
# ---------------------------------------------------------------------------

class TemplateFiber:
    """Descriptor for the template (Jinja2 / Django) fiber."""

    coordinate_kinds: list[str] = [
        "TEMPLATE_FILE",
        "TEMPLATE_BLOCK",
        "TEMPLATE_VARIABLE",
        "TEMPLATE_MACRO",
        "TEMPLATE_FILTER",
        "TEMPLATE_INCLUDE",
    ]

    internal_morphisms: list[str] = [
        "block_inheritance",
        "macro_call",
        "include_reference",
        "filter_application",
        "variable_access",
    ]

    internal_descent_conditions: list[str] = [
        "variable_defined_in_context",
        "macro_parameter_types",
        "filter_applicability",
        "block_override_compatibility",
    ]

    INSTANCE: LanguageFiber = LanguageFiber(
        name="template",
        coordinate_kinds=list(coordinate_kinds),
        morphism_kinds=list(internal_morphisms),
        internal_topology={"type": "grothendieck", "coverage": "template_tree"},
        description="Jinja2 / Django template layer.",
    )

    @classmethod
    def get_fiber(cls) -> LanguageFiber:
        """Return the singleton ``LanguageFiber`` for templates."""
        return cls.INSTANCE
