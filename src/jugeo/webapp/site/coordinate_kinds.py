"""WebCoordinateKind enum — all coordinate kinds in the web application site."""
from __future__ import annotations
from enum import Enum


class WebCoordinateKind(str, Enum):
    # Python layer
    ROUTE_HANDLER = "route_handler"
    VIEW_FUNCTION = "view_function"
    MODEL_CLASS = "model_class"
    FORM_CLASS = "form_class"
    MIDDLEWARE = "middleware"
    BLUEPRINT = "blueprint"
    CONFIG_KEY = "config_key"
    ERROR_HANDLER = "error_handler"

    # Template layer
    TEMPLATE_FILE = "template_file"
    TEMPLATE_BLOCK = "template_block"
    TEMPLATE_VARIABLE = "template_variable"
    TEMPLATE_MACRO = "template_macro"
    TEMPLATE_FILTER = "template_filter"

    # JavaScript layer
    JS_MODULE = "js_module"
    JS_FUNCTION = "js_function"
    JS_EVENT_HANDLER = "js_event_handler"
    JS_FETCH_CALL = "js_fetch_call"
    JS_DOM_MANIPULATION = "js_dom_manipulation"
    JS_STATE_VARIABLE = "js_state_variable"

    # CSS layer
    CSS_STYLESHEET = "css_stylesheet"
    CSS_RULE = "css_rule"
    CSS_PROPERTY = "css_property"
    CSS_MEDIA_QUERY = "css_media_query"
    CSS_ANIMATION = "css_animation"

    # HTML/DOM layer
    HTML_ELEMENT = "html_element"
    HTML_ATTRIBUTE = "html_attribute"
    HTML_FORM = "html_form"
    HTML_LINK = "html_link"

    # Database layer
    DB_TABLE = "db_table"
    DB_COLUMN = "db_column"
    DB_CONSTRAINT = "db_constraint"
    DB_INDEX = "db_index"
    DB_MIGRATION = "db_migration"

    # HTTP/API layer
    API_ENDPOINT = "api_endpoint"
    API_REQUEST_SCHEMA = "api_request_schema"
    API_RESPONSE_SCHEMA = "api_response_schema"
    API_ERROR_CODE = "api_error_code"
    API_HEADER = "api_header"

    # Session/Auth layer
    SESSION_KEY = "session_key"
    AUTH_DECORATOR = "auth_decorator"
    PERMISSION_CHECK = "permission_check"

    def language_layer(self) -> str:
        """Return the language layer this coordinate kind belongs to."""
        _LAYER_MAP = {
            "python": {
                WebCoordinateKind.ROUTE_HANDLER, WebCoordinateKind.VIEW_FUNCTION,
                WebCoordinateKind.MODEL_CLASS, WebCoordinateKind.FORM_CLASS,
                WebCoordinateKind.MIDDLEWARE, WebCoordinateKind.BLUEPRINT,
                WebCoordinateKind.CONFIG_KEY, WebCoordinateKind.ERROR_HANDLER,
            },
            "template": {
                WebCoordinateKind.TEMPLATE_FILE, WebCoordinateKind.TEMPLATE_BLOCK,
                WebCoordinateKind.TEMPLATE_VARIABLE, WebCoordinateKind.TEMPLATE_MACRO,
                WebCoordinateKind.TEMPLATE_FILTER,
            },
            "javascript": {
                WebCoordinateKind.JS_MODULE, WebCoordinateKind.JS_FUNCTION,
                WebCoordinateKind.JS_EVENT_HANDLER, WebCoordinateKind.JS_FETCH_CALL,
                WebCoordinateKind.JS_DOM_MANIPULATION, WebCoordinateKind.JS_STATE_VARIABLE,
            },
            "css": {
                WebCoordinateKind.CSS_STYLESHEET, WebCoordinateKind.CSS_RULE,
                WebCoordinateKind.CSS_PROPERTY, WebCoordinateKind.CSS_MEDIA_QUERY,
                WebCoordinateKind.CSS_ANIMATION,
            },
            "html": {
                WebCoordinateKind.HTML_ELEMENT, WebCoordinateKind.HTML_ATTRIBUTE,
                WebCoordinateKind.HTML_FORM, WebCoordinateKind.HTML_LINK,
            },
            "database": {
                WebCoordinateKind.DB_TABLE, WebCoordinateKind.DB_COLUMN,
                WebCoordinateKind.DB_CONSTRAINT, WebCoordinateKind.DB_INDEX,
                WebCoordinateKind.DB_MIGRATION,
            },
            "http": {
                WebCoordinateKind.API_ENDPOINT, WebCoordinateKind.API_REQUEST_SCHEMA,
                WebCoordinateKind.API_RESPONSE_SCHEMA, WebCoordinateKind.API_ERROR_CODE,
                WebCoordinateKind.API_HEADER,
            },
            "auth": {
                WebCoordinateKind.SESSION_KEY, WebCoordinateKind.AUTH_DECORATOR,
                WebCoordinateKind.PERMISSION_CHECK,
            },
        }
        for layer, kinds in _LAYER_MAP.items():
            if self in kinds:
                return layer
        return "unknown"

    def is_server_side(self) -> bool:
        return self.language_layer() in {"python", "template", "database", "auth"}

    def is_client_side(self) -> bool:
        return self.language_layer() in {"javascript", "css", "html"}
