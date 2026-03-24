"""CrossLanguageMorphismKind enum — all morphism kinds in the web application site."""
from __future__ import annotations
from enum import Enum


class CrossLanguageMorphismKind(str, Enum):
    # Python ↔ Template
    CONTEXT_PROVISION = "context_provision"
    TEMPLATE_RENDERING = "template_rendering"
    URL_GENERATION = "url_generation"
    FORM_BINDING = "form_binding"

    # Python ↔ Database
    ORM_MAPPING = "orm_mapping"
    QUERY_EXECUTION = "query_execution"
    MIGRATION_DELTA = "migration_delta"
    CONSTRAINT_ENCODING = "constraint_encoding"

    # Python ↔ JavaScript (via API)
    API_CONTRACT = "api_contract"
    ERROR_PROPAGATION = "error_propagation"
    AUTH_STATE_SYNC = "auth_state_sync"
    WEBSOCKET_CHANNEL = "websocket_channel"

    # JavaScript ↔ HTML/DOM
    DOM_SELECTION = "dom_selection"
    EVENT_BINDING = "event_binding"
    CONTENT_INJECTION = "content_injection"
    CLASS_MANIPULATION = "class_manipulation"

    # JavaScript ↔ CSS
    STYLE_MUTATION = "style_mutation"
    CLASS_REFERENCE = "class_reference"
    ANIMATION_TRIGGER = "animation_trigger"
    MEDIA_QUERY_JS = "media_query_js"

    # HTML ↔ CSS
    SELECTOR_MATCH = "selector_match"
    SPECIFICITY_CASCADE = "specificity_cascade"
    LAYOUT_CONSTRAINT = "layout_constraint"

    # Template ↔ HTML/CSS/JS
    TEMPLATE_EMISSION = "template_emission"
    STATIC_REFERENCE = "static_reference"
    CONDITIONAL_RENDER = "conditional_render"

    def source_layer(self) -> str:
        return _MORPHISM_LAYERS[self][0]

    def target_layer(self) -> str:
        return _MORPHISM_LAYERS[self][1]

    def crosses_trust_boundary(self) -> bool:
        _SERVER_SIDE = {"python", "template", "database", "auth"}
        _CLIENT_SIDE = {"javascript", "css", "html"}
        sl = self.source_layer()
        tl = self.target_layer()
        return (sl in _SERVER_SIDE and tl in _CLIENT_SIDE) or \
               (sl in _CLIENT_SIDE and tl in _SERVER_SIDE)


_MORPHISM_LAYERS: dict[CrossLanguageMorphismKind, tuple[str, str]] = {
    CrossLanguageMorphismKind.CONTEXT_PROVISION: ("python", "template"),
    CrossLanguageMorphismKind.TEMPLATE_RENDERING: ("template", "python"),
    CrossLanguageMorphismKind.URL_GENERATION: ("template", "python"),
    CrossLanguageMorphismKind.FORM_BINDING: ("python", "html"),
    CrossLanguageMorphismKind.ORM_MAPPING: ("python", "database"),
    CrossLanguageMorphismKind.QUERY_EXECUTION: ("python", "database"),
    CrossLanguageMorphismKind.MIGRATION_DELTA: ("database", "python"),
    CrossLanguageMorphismKind.CONSTRAINT_ENCODING: ("python", "database"),
    CrossLanguageMorphismKind.API_CONTRACT: ("python", "javascript"),
    CrossLanguageMorphismKind.ERROR_PROPAGATION: ("python", "javascript"),
    CrossLanguageMorphismKind.AUTH_STATE_SYNC: ("python", "javascript"),
    CrossLanguageMorphismKind.WEBSOCKET_CHANNEL: ("python", "javascript"),
    CrossLanguageMorphismKind.DOM_SELECTION: ("javascript", "html"),
    CrossLanguageMorphismKind.EVENT_BINDING: ("javascript", "html"),
    CrossLanguageMorphismKind.CONTENT_INJECTION: ("javascript", "html"),
    CrossLanguageMorphismKind.CLASS_MANIPULATION: ("javascript", "css"),
    CrossLanguageMorphismKind.STYLE_MUTATION: ("javascript", "css"),
    CrossLanguageMorphismKind.CLASS_REFERENCE: ("javascript", "css"),
    CrossLanguageMorphismKind.ANIMATION_TRIGGER: ("javascript", "css"),
    CrossLanguageMorphismKind.MEDIA_QUERY_JS: ("javascript", "css"),
    CrossLanguageMorphismKind.SELECTOR_MATCH: ("css", "html"),
    CrossLanguageMorphismKind.SPECIFICITY_CASCADE: ("css", "html"),
    CrossLanguageMorphismKind.LAYOUT_CONSTRAINT: ("css", "html"),
    CrossLanguageMorphismKind.TEMPLATE_EMISSION: ("template", "html"),
    CrossLanguageMorphismKind.STATIC_REFERENCE: ("template", "http"),
    CrossLanguageMorphismKind.CONDITIONAL_RENDER: ("template", "html"),
}
