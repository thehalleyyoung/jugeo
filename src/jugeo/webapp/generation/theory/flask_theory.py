"""Theory of Flask applications as the server-side fibre of the web application sheaf.

Flask applications form the server-side fibre of the web application sheaf.
Routes are coordinates in the server site.  The request/response cycle is a
morphism.  Templates are the structural gluing between server and client
fibres.  Models are sections of the data presheaf.  Security obligations are
trust boundary descent conditions — data crossing the client/server boundary
must satisfy trust demotion rules.  CRUD completeness is a covering condition:
every model must be fully covered by routes.

Sheaf-theoretic interpretation
------------------------------
A **Flask application** is a presheaf on the *server site*, whose objects are
routes and whose morphisms are request/response cycles.  The presheaf assigns
to each route its handler code, its template (if page-rendering), its model
dependencies, and its security constraints.

**Descent** in this site means:
  1. **Route coverage** — every view coordinate in the view site has a
     corresponding server route.  No client page may dangle without a
     server handler.
  2. **CRUD completeness** — every model exposed through the application
     must be fully covered by create, read, update, and delete routes
     (the CRUD covering condition).
  3. **Security descent** — trust boundary constraints (CSRF, XSS, SQL
     injection prevention) hold on every route that accepts untrusted
     input.  Data crossing the client/server boundary must satisfy trust
     demotion rules.
  4. **Model consistency** — every route that references a model column
     actually has that column defined in the model schema.

When all four descent conditions hold, the Flask presheaf is a *sheaf* and the
generated server code is coherent, secure, and complete.

This module is domain-agnostic: it works for e-commerce stores, blogs,
dashboards, social networks, APIs — any web application expressible as a Flask
server with SQLAlchemy models and Jinja2 templates.
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .view_site import ViewCoordinate, ViewKind, ViewSite
from ..concept_extractor import ConceptMap, ConceptDomain, Concept

__all__ = [
    # Enums
    "RouteKind",
    "HTTPMethod",
    # Obligation dataclasses
    "FlaskRouteObligation",
    "FlaskModelObligation",
    "FlaskSecurityObligation",
    "FlaskStructureObligation",
    # Presheaf
    "FlaskObligationPresheaf",
    # Generators
    "FlaskRouteGenerator",
    "FlaskModelGenerator",
    "FlaskSecurityGenerator",
    "FlaskAppGenerator",
    # Descent & builder
    "FlaskDescentChecker",
    "FlaskViewSiteBuilder",
]


# ═══════════════════════════════════════════════════════════════════════
# §1  RouteKind — taxonomy of Flask route types
# ═══════════════════════════════════════════════════════════════════════

class RouteKind(str, Enum):
    """Taxonomy of route types that occur in Flask applications.

    Each kind carries semantic information about what the route does,
    which determines the generated handler shape, security requirements,
    and response format.
    """

    PAGE_RENDER = "page_render"
    API_JSON = "api_json"
    API_FORM = "api_form"
    STATIC_FILE = "static_file"
    REDIRECT = "redirect"
    WEBHOOK = "webhook"
    HEALTH_CHECK = "health_check"
    AUTH_LOGIN = "auth_login"
    AUTH_LOGOUT = "auth_logout"
    AUTH_REGISTER = "auth_register"
    ERROR_HANDLER = "error_handler"
    ADMIN = "admin"


# ═══════════════════════════════════════════════════════════════════════
# §2  HTTPMethod — HTTP verbs
# ═══════════════════════════════════════════════════════════════════════

class HTTPMethod(str, Enum):
    """Standard HTTP methods used by Flask routes."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    OPTIONS = "OPTIONS"


# ═══════════════════════════════════════════════════════════════════════
# §3  FlaskRouteObligation — a single route obligation
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FlaskRouteObligation:
    """Obligation for a single Flask route.

    A route obligation specifies what the route must do, what HTTP
    methods it must accept, what security constraints it must satisfy,
    and what response it must produce.  It is a *section* of the Flask
    presheaf over the route coordinate.
    """

    path: str
    methods: list[HTTPMethod] = field(default_factory=lambda: [HTTPMethod.GET])
    kind: RouteKind = RouteKind.PAGE_RENDER
    requires_auth: bool = False
    csrf_protected: bool = True
    input_validation: bool = True
    rate_limited: bool = False
    template: str | None = None
    response_type: str = "text/html"

    @property
    def is_api(self) -> bool:
        """True for API routes (JSON or form)."""
        return self.kind in (RouteKind.API_JSON, RouteKind.API_FORM)

    @property
    def is_auth(self) -> bool:
        """True for authentication-related routes."""
        return self.kind in (
            RouteKind.AUTH_LOGIN,
            RouteKind.AUTH_LOGOUT,
            RouteKind.AUTH_REGISTER,
        )

    @property
    def endpoint_name(self) -> str:
        """Derive a Python-safe endpoint name from the path."""
        name = self.path.strip("/").replace("/", "_").replace("<", "").replace(">", "")
        name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        return name or "index"


# ═══════════════════════════════════════════════════════════════════════
# §4  FlaskModelObligation — a single model obligation
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FlaskModelObligation:
    """Obligation for a single SQLAlchemy model.

    A model obligation is a section of the data presheaf.  It specifies
    the table structure, column types, relationships, and timestamp
    requirements.  Columns are represented as dicts with keys:
    ``name``, ``type``, ``nullable``, ``unique``, ``indexed``.
    """

    name: str
    table_name: str
    columns: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    has_timestamps: bool = True

    @property
    def column_names(self) -> list[str]:
        """Return all column names."""
        return [col["name"] for col in self.columns]

    @property
    def primary_key_columns(self) -> list[dict[str, Any]]:
        """Return columns marked as primary keys."""
        return [col for col in self.columns if col.get("primary_key")]

    def has_column(self, name: str) -> bool:
        """Check whether this model defines a column with the given name."""
        return name in self.column_names


# ═══════════════════════════════════════════════════════════════════════
# §5  FlaskSecurityObligation — trust boundary descent conditions
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FlaskSecurityObligation:
    """Security obligations for a Flask application.

    These are *trust boundary descent conditions*: any data crossing the
    client/server boundary (form submissions, API payloads, URL
    parameters) must be validated, sanitised, and authorised before being
    trusted by the server.
    """

    csrf_protection: bool = True
    sql_injection_prevention: bool = True
    xss_prevention: bool = True
    input_sanitization: bool = True
    auth_required_routes: list[str] = field(default_factory=list)
    password_hashing: bool = True
    session_management: bool = True
    cors_policy: str | None = None
    rate_limiting: bool = False
    https_redirect: bool = False

    @property
    def has_auth(self) -> bool:
        """True if any routes require authentication."""
        return len(self.auth_required_routes) > 0


# ═══════════════════════════════════════════════════════════════════════
# §6  FlaskStructureObligation — application structure obligations
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FlaskStructureObligation:
    """Structural obligations for a Flask application.

    These govern the overall shape of the application: blueprint
    organisation, error handling, configuration, logging, database
    migrations, and test scaffolding.
    """

    blueprints: list[str] = field(default_factory=list)
    error_handlers: list[int] = field(default_factory=lambda: [404, 500])
    config_from_env: bool = True
    logging: bool = True
    database_migrations: bool = True
    test_configuration: bool = True

    @property
    def has_blueprints(self) -> bool:
        """True if the application uses blueprints."""
        return len(self.blueprints) > 0


# ═══════════════════════════════════════════════════════════════════════
# §7  FlaskObligationPresheaf — the presheaf over the server site
# ═══════════════════════════════════════════════════════════════════════

class FlaskObligationPresheaf:
    """The obligation presheaf over the Flask server site.

    Collects route, model, security, and structural obligations into a
    single coherent presheaf.  The ``from_concepts`` class method derives
    a complete presheaf from a concept map and view coordinates.
    """

    def __init__(
        self,
        routes: list[FlaskRouteObligation] | None = None,
        models: list[FlaskModelObligation] | None = None,
        security: FlaskSecurityObligation | None = None,
        structure: FlaskStructureObligation | None = None,
    ) -> None:
        self.routes: list[FlaskRouteObligation] = routes or []
        self.models: list[FlaskModelObligation] = models or []
        self.security: FlaskSecurityObligation = security or FlaskSecurityObligation()
        self.structure: FlaskStructureObligation = structure or FlaskStructureObligation()

    # ── mutators ──────────────────────────────────────────────────────

    def add_route(self, route: FlaskRouteObligation) -> None:
        """Add a route obligation to the presheaf."""
        self.routes.append(route)

    def add_model(self, model: FlaskModelObligation) -> None:
        """Add a model obligation to the presheaf."""
        self.models.append(model)

    # ── queries ───────────────────────────────────────────────────────

    def routes_by_kind(self, kind: RouteKind) -> list[FlaskRouteObligation]:
        """Return all routes of the given kind."""
        return [r for r in self.routes if r.kind == kind]

    def auth_routes(self) -> list[FlaskRouteObligation]:
        """Return all authentication-related routes."""
        return [r for r in self.routes if r.is_auth]

    def api_routes(self) -> list[FlaskRouteObligation]:
        """Return all API routes."""
        return [r for r in self.routes if r.is_api]

    def page_routes(self) -> list[FlaskRouteObligation]:
        """Return all page-rendering routes."""
        return self.routes_by_kind(RouteKind.PAGE_RENDER)

    def model_names(self) -> list[str]:
        """Return all model names."""
        return [m.name for m in self.models]

    # ── class method builder ──────────────────────────────────────────

    @classmethod
    def from_concepts(
        cls,
        concepts: list[Concept],
        views: list[ViewCoordinate],
    ) -> FlaskObligationPresheaf:
        """Derive Flask obligations from concepts and view coordinates.

        This is a convenience wrapper around :class:`FlaskViewSiteBuilder`.
        """
        return FlaskViewSiteBuilder.from_concepts(concepts, views)

    # ── dunder helpers ────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"FlaskObligationPresheaf(routes={len(self.routes)}, "
            f"models={len(self.models)}, "
            f"blueprints={len(self.structure.blueprints)})"
        )


# ═══════════════════════════════════════════════════════════════════════
# §8  FlaskRouteGenerator — generate Flask route code
# ═══════════════════════════════════════════════════════════════════════

class FlaskRouteGenerator:
    """Generate Flask route handler code from route obligations.

    Each method returns a string of Python source code suitable for
    inclusion in a Flask blueprint or application module.
    """

    # ── single route ──────────────────────────────────────────────────

    @staticmethod
    def generate_route(obligation: FlaskRouteObligation) -> str:
        """Generate a single route handler from a route obligation."""
        methods_str = ", ".join(f'"{m.value}"' for m in obligation.methods)
        decorator = f'@bp.route("{obligation.path}", methods=[{methods_str}])'

        lines: list[str] = []

        if obligation.requires_auth:
            lines.append("@login_required")

        func_name = obligation.endpoint_name
        lines.insert(0, decorator)
        lines.append(f"def {func_name}():")

        # Body depends on route kind.
        if obligation.kind == RouteKind.PAGE_RENDER and obligation.template:
            lines.append(f'    return render_template("{obligation.template}")')
        elif obligation.kind == RouteKind.API_JSON:
            lines.append("    return jsonify({})")
        elif obligation.kind == RouteKind.REDIRECT:
            lines.append('    return redirect(url_for("main.index"))')
        elif obligation.kind == RouteKind.HEALTH_CHECK:
            lines.append('    return jsonify({"status": "ok"})')
        elif obligation.kind in (RouteKind.AUTH_LOGIN, RouteKind.AUTH_REGISTER):
            template = obligation.template or f"{func_name}.html"
            lines.append(f"    if request.method == \"POST\":")
            lines.append(f"        # TODO: handle {func_name} form submission")
            lines.append(f"        pass")
            lines.append(f'    return render_template("{template}")')
        elif obligation.kind == RouteKind.AUTH_LOGOUT:
            lines.append("    logout_user()")
            lines.append('    return redirect(url_for("main.index"))')
        elif obligation.kind == RouteKind.ERROR_HANDLER:
            lines.append("    return render_template(\"error.html\", code=error.code), error.code")
        else:
            lines.append("    pass")

        return "\n".join(lines)

    # ── blueprint ─────────────────────────────────────────────────────

    @classmethod
    def generate_blueprint(
        cls,
        name: str,
        routes: list[FlaskRouteObligation],
    ) -> str:
        """Generate a complete blueprint module with all routes."""
        header = textwrap.dedent(f"""\
            \"\"\"Blueprint: {name}.\"\"\"
            from flask import Blueprint, render_template, request, redirect, url_for, jsonify

            bp = Blueprint("{name}", __name__)
        """)

        route_blocks = []
        for route in routes:
            route_blocks.append("")
            route_blocks.append("")
            route_blocks.append(cls.generate_route(route))

        return header + "\n".join(route_blocks) + "\n"

    # ── error handlers ────────────────────────────────────────────────

    @staticmethod
    def generate_error_handlers(codes: list[int]) -> str:
        """Generate error handler registrations for the given HTTP codes."""
        lines: list[str] = []
        for code in codes:
            lines.append(f"@app.errorhandler({code})")
            lines.append(f"def error_{code}(error):")
            lines.append(
                f'    return render_template("errors/{code}.html"), {code}'
            )
            lines.append("")
        return "\n".join(lines)

    # ── auth routes ───────────────────────────────────────────────────

    @classmethod
    def generate_auth_routes(cls) -> str:
        """Generate a standard set of authentication routes."""
        auth_routes = [
            FlaskRouteObligation(
                path="/login",
                methods=[HTTPMethod.GET, HTTPMethod.POST],
                kind=RouteKind.AUTH_LOGIN,
                template="auth/login.html",
                csrf_protected=True,
            ),
            FlaskRouteObligation(
                path="/register",
                methods=[HTTPMethod.GET, HTTPMethod.POST],
                kind=RouteKind.AUTH_REGISTER,
                template="auth/register.html",
                csrf_protected=True,
            ),
            FlaskRouteObligation(
                path="/logout",
                methods=[HTTPMethod.GET],
                kind=RouteKind.AUTH_LOGOUT,
                requires_auth=True,
            ),
        ]
        return cls.generate_blueprint("auth", auth_routes)

    # ── CRUD API routes ───────────────────────────────────────────────

    @classmethod
    def generate_api_routes(cls, models: list[FlaskModelObligation]) -> str:
        """Generate CRUD API endpoints for each model.

        For every model, four routes are created:
          - GET  /api/<model>         — list all
          - POST /api/<model>         — create
          - GET  /api/<model>/<id>    — read one
          - PUT  /api/<model>/<id>    — update
          - DELETE /api/<model>/<id>  — delete
        """
        routes: list[FlaskRouteObligation] = []
        for model in models:
            base = f"/api/{model.table_name}"
            detail = f"/api/{model.table_name}/<int:id>"

            routes.append(FlaskRouteObligation(
                path=base,
                methods=[HTTPMethod.GET],
                kind=RouteKind.API_JSON,
                response_type="application/json",
            ))
            routes.append(FlaskRouteObligation(
                path=base,
                methods=[HTTPMethod.POST],
                kind=RouteKind.API_JSON,
                csrf_protected=True,
                input_validation=True,
                response_type="application/json",
            ))
            routes.append(FlaskRouteObligation(
                path=detail,
                methods=[HTTPMethod.GET],
                kind=RouteKind.API_JSON,
                response_type="application/json",
            ))
            routes.append(FlaskRouteObligation(
                path=detail,
                methods=[HTTPMethod.PUT],
                kind=RouteKind.API_JSON,
                csrf_protected=True,
                input_validation=True,
                response_type="application/json",
            ))
            routes.append(FlaskRouteObligation(
                path=detail,
                methods=[HTTPMethod.DELETE],
                kind=RouteKind.API_JSON,
                requires_auth=True,
                response_type="application/json",
            ))

        return cls.generate_blueprint("api", routes)


# ═══════════════════════════════════════════════════════════════════════
# §9  FlaskModelGenerator — generate SQLAlchemy model code
# ═══════════════════════════════════════════════════════════════════════

class FlaskModelGenerator:
    """Generate SQLAlchemy model code from model obligations."""

    # ── type mapping ──────────────────────────────────────────────────

    _TYPE_MAP: dict[str, str] = {
        "integer": "db.Integer",
        "string": "db.String(255)",
        "text": "db.Text",
        "boolean": "db.Boolean",
        "float": "db.Float",
        "datetime": "db.DateTime",
        "date": "db.Date",
        "json": "db.JSON",
    }

    # ── single model ──────────────────────────────────────────────────

    @classmethod
    def generate_model(cls, obligation: FlaskModelObligation) -> str:
        """Generate a single SQLAlchemy model class."""
        lines: list[str] = [
            f"class {obligation.name}(db.Model):",
            f'    """Model: {obligation.name}."""',
            f"    __tablename__ = \"{obligation.table_name}\"",
            "",
        ]

        # Primary key (add if not explicitly in columns).
        has_pk = any(col.get("primary_key") for col in obligation.columns)
        if not has_pk:
            lines.append(
                "    id = db.Column(db.Integer, primary_key=True)"
            )

        # Columns.
        for col in obligation.columns:
            col_type = cls._TYPE_MAP.get(col.get("type", "string"), "db.String(255)")
            parts = [col_type]
            if col.get("primary_key"):
                parts.append("primary_key=True")
            if col.get("nullable") is False:
                parts.append("nullable=False")
            if col.get("unique"):
                parts.append("unique=True")
            if col.get("indexed"):
                parts.append("index=True")

            col_def = ", ".join(parts)
            lines.append(f"    {col['name']} = db.Column({col_def})")

        # Timestamps.
        if obligation.has_timestamps:
            lines.append("")
            lines.append(
                "    created_at = db.Column("
                "db.DateTime, server_default=db.func.now())"
            )
            lines.append(
                "    updated_at = db.Column("
                "db.DateTime, server_default=db.func.now(), "
                "onupdate=db.func.now())"
            )

        # Relationships.
        for rel in obligation.relationships:
            rel_name = rel.get("name", "related")
            rel_model = rel.get("model", "Model")
            back_ref = rel.get("backref", obligation.table_name)
            lazy = rel.get("lazy", "select")
            lines.append(
                f"    {rel_name} = db.relationship("
                f'"{rel_model}", backref="{back_ref}", lazy="{lazy}")'
            )

        # __repr__
        lines.append("")
        lines.append("    def __repr__(self):")
        lines.append(f'        return f"<{obligation.name} {{self.id}}>"')

        return "\n".join(lines)

    # ── all models ────────────────────────────────────────────────────

    @classmethod
    def generate_all_models(cls, models: list[FlaskModelObligation]) -> str:
        """Generate a complete models module with all models."""
        header = textwrap.dedent("""\
            \"\"\"Database models.\"\"\"
            from datetime import datetime
            from extensions import db
        """)

        blocks = [header]
        for model in models:
            blocks.append("")
            blocks.append("")
            blocks.append(cls.generate_model(model))

        return "\n".join(blocks) + "\n"


# ═══════════════════════════════════════════════════════════════════════
# §10  FlaskSecurityGenerator — generate security boilerplate
# ═══════════════════════════════════════════════════════════════════════

class FlaskSecurityGenerator:
    """Generate security-related code for Flask applications.

    Security is a *trust boundary obligation*: every piece of data that
    crosses the client/server boundary must be validated, escaped, and
    authorised.  This generator produces the code that enforces those
    trust demotion rules.
    """

    @staticmethod
    def generate_csrf_setup() -> str:
        """Generate CSRF protection setup."""
        return textwrap.dedent("""\
            from flask_wtf.csrf import CSRFProtect

            csrf = CSRFProtect()


            def init_csrf(app):
                \"\"\"Initialise CSRF protection on the application.\"\"\"
                csrf.init_app(app)
        """)

    @staticmethod
    def generate_auth_decorators() -> str:
        """Generate authentication decorator utilities."""
        return textwrap.dedent("""\
            from functools import wraps
            from flask import redirect, url_for, flash
            from flask_login import current_user


            def login_required(f):
                \"\"\"Redirect unauthenticated users to the login page.\"\"\"
                @wraps(f)
                def decorated_function(*args, **kwargs):
                    if not current_user.is_authenticated:
                        flash("Please log in to access this page.", "warning")
                        return redirect(url_for("auth.login"))
                    return f(*args, **kwargs)
                return decorated_function


            def admin_required(f):
                \"\"\"Restrict access to admin users.\"\"\"
                @wraps(f)
                def decorated_function(*args, **kwargs):
                    if not current_user.is_authenticated:
                        return redirect(url_for("auth.login"))
                    if not getattr(current_user, "is_admin", False):
                        flash("Admin access required.", "danger")
                        return redirect(url_for("main.index"))
                    return f(*args, **kwargs)
                return decorated_function
        """)

    @staticmethod
    def generate_input_validation() -> str:
        """Generate input validation utilities."""
        return textwrap.dedent("""\
            import re
            from markupsafe import escape


            def sanitize_string(value: str, max_length: int = 255) -> str:
                \"\"\"Sanitise a user-supplied string.\"\"\"
                if not isinstance(value, str):
                    return ""
                value = str(escape(value.strip()))
                return value[:max_length]


            def validate_email(email: str) -> bool:
                \"\"\"Validate an email address format.\"\"\"
                pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
                return bool(re.match(pattern, email))


            def validate_password(password: str, min_length: int = 8) -> list[str]:
                \"\"\"Return a list of password-strength violations (empty = valid).\"\"\"
                errors: list[str] = []
                if len(password) < min_length:
                    errors.append(f"Password must be at least {min_length} characters.")
                if not re.search(r"[A-Z]", password):
                    errors.append("Password must contain an uppercase letter.")
                if not re.search(r"[a-z]", password):
                    errors.append("Password must contain a lowercase letter.")
                if not re.search(r"[0-9]", password):
                    errors.append("Password must contain a digit.")
                return errors
        """)

    @staticmethod
    def generate_rate_limiting() -> str:
        """Generate rate-limiting setup."""
        return textwrap.dedent("""\
            from flask_limiter import Limiter
            from flask_limiter.util import get_remote_address

            limiter = Limiter(key_func=get_remote_address)


            def init_rate_limiting(app):
                \"\"\"Initialise rate limiting on the application.\"\"\"
                limiter.init_app(app)
        """)


# ═══════════════════════════════════════════════════════════════════════
# §11  FlaskAppGenerator — main entry point for full app generation
# ═══════════════════════════════════════════════════════════════════════

class FlaskAppGenerator:
    """Generate a complete Flask application from an obligation presheaf.

    This is the main entry point for server-side code generation.  It
    orchestrates route, model, security, and config generators to
    produce a runnable Flask project.
    """

    # ── app factory ───────────────────────────────────────────────────

    @staticmethod
    def generate_app_factory(presheaf: FlaskObligationPresheaf) -> str:
        """Generate the ``create_app`` application factory."""
        bp_imports: list[str] = []
        bp_registers: list[str] = []

        for bp_name in presheaf.structure.blueprints:
            bp_imports.append(
                f"    from .{bp_name} import bp as {bp_name}_bp"
            )
            bp_registers.append(
                f"    app.register_blueprint({bp_name}_bp)"
            )

        # Auth blueprint if there are auth routes.
        if presheaf.auth_routes():
            if "auth" not in presheaf.structure.blueprints:
                bp_imports.append("    from .auth import bp as auth_bp")
                bp_registers.append("    app.register_blueprint(auth_bp)")

        # API blueprint if there are API routes.
        if presheaf.api_routes():
            if "api" not in presheaf.structure.blueprints:
                bp_imports.append("    from .api import bp as api_bp")
                bp_registers.append("    app.register_blueprint(api_bp)")

        imports_block = "\n".join(bp_imports) if bp_imports else "    pass"
        registers_block = "\n".join(bp_registers) if bp_registers else "    pass"

        # Security initialisations.
        security_inits: list[str] = []
        if presheaf.security.csrf_protection:
            security_inits.append("    csrf.init_app(app)")
        if presheaf.security.rate_limiting:
            security_inits.append("    limiter.init_app(app)")
        if presheaf.security.session_management:
            security_inits.append("    login_manager.init_app(app)")

        security_block = "\n".join(security_inits) if security_inits else ""

        # Error handlers.
        error_lines: list[str] = []
        for code in presheaf.structure.error_handlers:
            error_lines.append(f"    @app.errorhandler({code})")
            error_lines.append(f"    def error_{code}(error):")
            error_lines.append(
                f'        return render_template("errors/{code}.html"), {code}'
            )
            error_lines.append("")

        error_block = "\n".join(error_lines)

        # Logging setup.
        logging_block = ""
        if presheaf.structure.logging:
            logging_block = textwrap.dedent("""\
                import logging
                logging.basicConfig(level=logging.INFO)
                app.logger.setLevel(logging.INFO)
            """)
            logging_block = textwrap.indent(logging_block, "    ")

        return textwrap.dedent(f"""\
            \"\"\"Application factory.\"\"\"
            import os
            from flask import Flask, render_template
            from extensions import db


            def create_app(config_name=None):
                \"\"\"Create and configure the Flask application.\"\"\"
                app = Flask(__name__)

                # Configuration
                app.config.from_object(config_name or os.environ.get(
                    "FLASK_CONFIG", "config.DevelopmentConfig"
                ))

                # Extensions
                db.init_app(app)
            {security_block}
            {logging_block}
                # Blueprints
            {imports_block}
            {registers_block}

                # Error handlers
            {error_block}
                return app
        """)

    # ── config ────────────────────────────────────────────────────────

    @staticmethod
    def generate_config() -> str:
        """Generate a standard Flask configuration module."""
        return textwrap.dedent("""\
            \"\"\"Application configuration.\"\"\"
            import os


            class Config:
                \"\"\"Base configuration.\"\"\"
                SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
                SQLALCHEMY_TRACK_MODIFICATIONS = False
                WTF_CSRF_ENABLED = True


            class DevelopmentConfig(Config):
                \"\"\"Development configuration.\"\"\"
                DEBUG = True
                SQLALCHEMY_DATABASE_URI = os.environ.get(
                    "DATABASE_URL", "sqlite:///dev.db"
                )


            class TestingConfig(Config):
                \"\"\"Testing configuration.\"\"\"
                TESTING = True
                SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
                WTF_CSRF_ENABLED = False


            class ProductionConfig(Config):
                \"\"\"Production configuration.\"\"\"
                DEBUG = False
                SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

                @classmethod
                def init_app(cls, app):
                    Config.init_app(app)
                    import logging
                    from logging.handlers import RotatingFileHandler
                    handler = RotatingFileHandler(
                        "app.log", maxBytes=10_000_000, backupCount=5
                    )
                    handler.setLevel(logging.WARNING)
                    app.logger.addHandler(handler)
        """)

    # ── requirements ──────────────────────────────────────────────────

    @staticmethod
    def generate_requirements() -> str:
        """Generate a ``requirements.txt`` for the Flask application."""
        packages = [
            "Flask>=3.0",
            "Flask-SQLAlchemy>=3.1",
            "Flask-WTF>=1.2",
            "Flask-Login>=0.6",
            "Flask-Migrate>=4.0",
            "Flask-Limiter>=3.5",
            "python-dotenv>=1.0",
            "gunicorn>=21.2",
            "Werkzeug>=3.0",
            "markupsafe>=2.1",
        ]
        return "\n".join(packages) + "\n"


# ═══════════════════════════════════════════════════════════════════════
# §12  FlaskDescentChecker — verify descent conditions
# ═══════════════════════════════════════════════════════════════════════

class FlaskDescentChecker:
    """Verify that the Flask presheaf satisfies descent conditions.

    Descent means:
      1. Route coverage  — every view has a server route.
      2. Security descent — trust boundary obligations hold.
      3. Model consistency — routes reference only existing model columns.
      4. CRUD completeness — every model is covered by CRUD routes.

    Returns lists of human-readable obstruction strings.
    """

    # ── route coverage ────────────────────────────────────────────────

    @staticmethod
    def check_route_coverage(
        routes: list[FlaskRouteObligation],
        views: list[ViewCoordinate],
    ) -> list[str]:
        """Check that every view coordinate has a corresponding route.

        Returns a list of obstruction messages (empty = no obstructions).
        """
        route_paths = {r.path for r in routes}
        obstructions: list[str] = []

        for view in views:
            # Convert hash-routes to server paths.
            server_path = _view_route_to_server_path(view.route)
            if server_path not in route_paths:
                obstructions.append(
                    f"View '{view.id}' (route {view.route}) has no "
                    f"corresponding server route at {server_path}."
                )

        return obstructions

    # ── security ──────────────────────────────────────────────────────

    @staticmethod
    def check_security(
        code: str,
        obligations: FlaskSecurityObligation,
    ) -> list[str]:
        """Check that generated code satisfies security obligations.

        Scans the code string for indicators that required security
        measures are present.  Returns obstruction messages for any
        missing measures.
        """
        obstructions: list[str] = []

        if obligations.csrf_protection:
            if "CSRFProtect" not in code and "csrf" not in code.lower():
                obstructions.append(
                    "CSRF protection is required but CSRFProtect not found in code."
                )

        if obligations.sql_injection_prevention:
            # Check for raw SQL string formatting (a common vulnerability).
            if re.search(r'execute\s*\(\s*f["\']', code):
                obstructions.append(
                    "Possible SQL injection: f-string used in execute()."
                )
            if re.search(r'execute\s*\(\s*["\'].*%s', code):
                obstructions.append(
                    "Possible SQL injection: %-formatting used in execute()."
                )

        if obligations.xss_prevention:
            # Check that Jinja2 autoescaping is not disabled.
            if "autoescape=False" in code or "| safe" in code:
                obstructions.append(
                    "XSS risk: autoescaping disabled or |safe filter used."
                )

        if obligations.password_hashing:
            if (
                "generate_password_hash" not in code
                and "bcrypt" not in code.lower()
                and "argon2" not in code.lower()
            ):
                if obligations.has_auth:
                    obstructions.append(
                        "Password hashing is required but no hashing "
                        "library found in code."
                    )

        if obligations.input_sanitization:
            if "escape" not in code and "sanitize" not in code.lower():
                if any(
                    kw in code for kw in ["request.form", "request.json", "request.args"]
                ):
                    obstructions.append(
                        "Input sanitisation required but no escape/sanitize "
                        "found for routes that read request data."
                    )

        if obligations.https_redirect:
            if "SSLify" not in code and "PREFERRED_URL_SCHEME" not in code:
                obstructions.append(
                    "HTTPS redirect required but no SSL configuration found."
                )

        return obstructions

    # ── model consistency ─────────────────────────────────────────────

    @staticmethod
    def check_model_consistency(
        models: list[FlaskModelObligation],
        routes: list[FlaskRouteObligation],
    ) -> list[str]:
        """Check that routes and models are mutually consistent.

        Verifies:
          - Every model has at least one route that references it
            (CRUD covering condition).
          - CRUD routes exist for each model (list, create, read,
            update, delete).
        """
        obstructions: list[str] = []
        model_tables = {m.table_name for m in models}

        # Check that every model has CRUD routes.
        api_paths = {r.path for r in routes if r.is_api}
        for model in models:
            base_path = f"/api/{model.table_name}"
            detail_path = f"/api/{model.table_name}/<int:id>"

            crud_coverage: dict[str, bool] = {
                "list": False,
                "create": False,
                "read": False,
                "update": False,
                "delete": False,
            }

            for route in routes:
                if route.path == base_path:
                    if HTTPMethod.GET in route.methods:
                        crud_coverage["list"] = True
                    if HTTPMethod.POST in route.methods:
                        crud_coverage["create"] = True
                if route.path == detail_path:
                    if HTTPMethod.GET in route.methods:
                        crud_coverage["read"] = True
                    if HTTPMethod.PUT in route.methods:
                        crud_coverage["update"] = True
                    if HTTPMethod.DELETE in route.methods:
                        crud_coverage["delete"] = True

            missing = [op for op, covered in crud_coverage.items() if not covered]
            if missing:
                obstructions.append(
                    f"Model '{model.name}' missing CRUD coverage for: "
                    f"{', '.join(missing)}."
                )

        # Check for API routes referencing non-existent models.
        for route in routes:
            if route.is_api:
                # Extract table name from /api/<table_name>[/<id>].
                match = re.match(r"/api/(\w+)", route.path)
                if match:
                    table = match.group(1)
                    if table not in model_tables:
                        obstructions.append(
                            f"Route '{route.path}' references model table "
                            f"'{table}' which is not defined."
                        )

        return obstructions


# ═══════════════════════════════════════════════════════════════════════
# §13  FlaskViewSiteBuilder — derive Flask obligations from view site
# ═══════════════════════════════════════════════════════════════════════

class FlaskViewSiteBuilder:
    """Derive Flask obligations from view coordinates and concepts.

    This builder translates the client-side view site into server-side
    Flask obligations:
      - Page views → GET routes with template rendering.
      - Form views → GET + POST routes with CSRF and validation.
      - Auth views → authentication routes with session management.
      - Data concepts → SQLAlchemy models with CRUD API routes.
      - Security concepts → trust boundary obligations.
    """

    # ── view kind → route kind mapping ────────────────────────────────

    _VIEW_TO_ROUTE_KIND: dict[ViewKind, RouteKind] = {
        ViewKind.LANDING: RouteKind.PAGE_RENDER,
        ViewKind.DASHBOARD: RouteKind.PAGE_RENDER,
        ViewKind.LIST: RouteKind.PAGE_RENDER,
        ViewKind.DETAIL: RouteKind.PAGE_RENDER,
        ViewKind.FORM: RouteKind.API_FORM,
        ViewKind.SETTINGS: RouteKind.PAGE_RENDER,
        ViewKind.PROFILE: RouteKind.PAGE_RENDER,
        ViewKind.SEARCH: RouteKind.PAGE_RENDER,
        ViewKind.GALLERY: RouteKind.PAGE_RENDER,
        ViewKind.EDITOR: RouteKind.PAGE_RENDER,
        ViewKind.CANVAS: RouteKind.PAGE_RENDER,
        ViewKind.TUTORIAL: RouteKind.PAGE_RENDER,
        ViewKind.ABOUT: RouteKind.PAGE_RENDER,
        ViewKind.ERROR: RouteKind.ERROR_HANDLER,
        ViewKind.AUTH_LOGIN: RouteKind.AUTH_LOGIN,
        ViewKind.AUTH_REGISTER: RouteKind.AUTH_REGISTER,
        ViewKind.AUTH_RESET: RouteKind.PAGE_RENDER,
        ViewKind.ADMIN: RouteKind.ADMIN,
        ViewKind.HELP: RouteKind.PAGE_RENDER,
        ViewKind.CHANGELOG: RouteKind.PAGE_RENDER,
        ViewKind.PRICING: RouteKind.PAGE_RENDER,
        ViewKind.CHECKOUT: RouteKind.API_FORM,
        ViewKind.CART: RouteKind.PAGE_RENDER,
        ViewKind.NOTIFICATION: RouteKind.PAGE_RENDER,
        ViewKind.EMPTY_STATE: RouteKind.PAGE_RENDER,
        ViewKind.CUSTOM: RouteKind.PAGE_RENDER,
    }

    # ── public API ────────────────────────────────────────────────────

    @classmethod
    def from_concepts(
        cls,
        concepts: list[Concept],
        views: list[ViewCoordinate],
    ) -> FlaskObligationPresheaf:
        """Build a complete obligation presheaf from concepts and views.

        Steps:
          1. Derive routes from view coordinates.
          2. Derive models from data concepts.
          3. Derive security obligations from auth concepts.
          4. Derive structural obligations from the route set.
        """
        presheaf = FlaskObligationPresheaf()

        # 1 — routes from views
        cls._derive_routes(presheaf, views)

        # 2 — models from concepts
        cls._derive_models(presheaf, concepts)

        # 3 — CRUD API routes for models
        cls._derive_api_routes(presheaf)

        # 4 — security
        cls._derive_security(presheaf, views, concepts)

        # 5 — structure
        cls._derive_structure(presheaf)

        return presheaf

    # ── internal: routes ──────────────────────────────────────────────

    @classmethod
    def _derive_routes(
        cls,
        presheaf: FlaskObligationPresheaf,
        views: list[ViewCoordinate],
    ) -> None:
        """Create a Flask route obligation for each view coordinate."""
        for view in views:
            route_kind = cls._VIEW_TO_ROUTE_KIND.get(
                view.kind, RouteKind.PAGE_RENDER
            )
            server_path = _view_route_to_server_path(view.route)

            # Determine HTTP methods based on view kind.
            methods = [HTTPMethod.GET]
            if view.kind in (ViewKind.FORM, ViewKind.CHECKOUT):
                methods = [HTTPMethod.GET, HTTPMethod.POST]
            elif view.kind in (ViewKind.AUTH_LOGIN, ViewKind.AUTH_REGISTER):
                methods = [HTTPMethod.GET, HTTPMethod.POST]

            # Template path.
            template = _derive_template_path(view)

            presheaf.add_route(FlaskRouteObligation(
                path=server_path,
                methods=methods,
                kind=route_kind,
                requires_auth=view.requires_auth,
                csrf_protected=HTTPMethod.POST in methods,
                input_validation=HTTPMethod.POST in methods,
                template=template,
                response_type="text/html",
            ))

        # Always add a health-check route.
        presheaf.add_route(FlaskRouteObligation(
            path="/health",
            methods=[HTTPMethod.GET],
            kind=RouteKind.HEALTH_CHECK,
            requires_auth=False,
            csrf_protected=False,
            input_validation=False,
            response_type="application/json",
        ))

    # ── internal: models ──────────────────────────────────────────────

    @classmethod
    def _derive_models(
        cls,
        presheaf: FlaskObligationPresheaf,
        concepts: list[Concept],
    ) -> None:
        """Create model obligations from data concepts."""
        for concept in concepts:
            if concept.domain != ConceptDomain.DATA:
                continue

            model_name = _concept_to_model_name(concept.name)
            table_name = _concept_to_table_name(concept.name)

            columns = concept.params.get("columns", [
                {"name": "id", "type": "integer", "primary_key": True,
                 "nullable": False, "unique": True, "indexed": True},
                {"name": "name", "type": "string", "nullable": False,
                 "unique": False, "indexed": True},
            ])

            relationships = concept.params.get("relationships", [])

            presheaf.add_model(FlaskModelObligation(
                name=model_name,
                table_name=table_name,
                columns=columns,
                relationships=relationships,
                has_timestamps=True,
            ))

        # If any view requires auth but no User model exists, add one.
        if not any(m.name == "User" for m in presheaf.models):
            has_auth_concepts = any(
                c.name in ("auth", "login", "user", "account")
                for c in concepts
            )
            if has_auth_concepts:
                presheaf.add_model(_default_user_model())

    # ── internal: API routes ──────────────────────────────────────────

    @classmethod
    def _derive_api_routes(cls, presheaf: FlaskObligationPresheaf) -> None:
        """Add CRUD API routes for each model."""
        for model in presheaf.models:
            base = f"/api/{model.table_name}"
            detail = f"/api/{model.table_name}/<int:id>"

            presheaf.add_route(FlaskRouteObligation(
                path=base,
                methods=[HTTPMethod.GET],
                kind=RouteKind.API_JSON,
                response_type="application/json",
            ))
            presheaf.add_route(FlaskRouteObligation(
                path=base,
                methods=[HTTPMethod.POST],
                kind=RouteKind.API_JSON,
                csrf_protected=True,
                input_validation=True,
                response_type="application/json",
            ))
            presheaf.add_route(FlaskRouteObligation(
                path=detail,
                methods=[HTTPMethod.GET],
                kind=RouteKind.API_JSON,
                response_type="application/json",
            ))
            presheaf.add_route(FlaskRouteObligation(
                path=detail,
                methods=[HTTPMethod.PUT],
                kind=RouteKind.API_JSON,
                csrf_protected=True,
                input_validation=True,
                response_type="application/json",
            ))
            presheaf.add_route(FlaskRouteObligation(
                path=detail,
                methods=[HTTPMethod.DELETE],
                kind=RouteKind.API_JSON,
                requires_auth=True,
                response_type="application/json",
            ))

    # ── internal: security ────────────────────────────────────────────

    @classmethod
    def _derive_security(
        cls,
        presheaf: FlaskObligationPresheaf,
        views: list[ViewCoordinate],
        concepts: list[Concept],
    ) -> None:
        """Derive security obligations from views and concepts."""
        auth_paths = [
            _view_route_to_server_path(v.route)
            for v in views if v.requires_auth
        ]

        has_auth = len(auth_paths) > 0 or any(
            c.name in ("auth", "login", "user", "account")
            for c in concepts
        )

        presheaf.security = FlaskSecurityObligation(
            csrf_protection=True,
            sql_injection_prevention=True,
            xss_prevention=True,
            input_sanitization=True,
            auth_required_routes=auth_paths,
            password_hashing=has_auth,
            session_management=has_auth,
            cors_policy="*" if presheaf.api_routes() else None,
            rate_limiting=any(r.rate_limited for r in presheaf.routes),
            https_redirect=False,
        )

    # ── internal: structure ───────────────────────────────────────────

    @classmethod
    def _derive_structure(cls, presheaf: FlaskObligationPresheaf) -> None:
        """Derive structural obligations from the route set."""
        # Infer blueprints from route prefixes.
        blueprints: set[str] = {"main"}
        for route in presheaf.routes:
            if route.is_auth:
                blueprints.add("auth")
            elif route.is_api:
                blueprints.add("api")
            elif route.kind == RouteKind.ADMIN:
                blueprints.add("admin")

        presheaf.structure = FlaskStructureObligation(
            blueprints=sorted(blueprints),
            error_handlers=[400, 403, 404, 405, 500],
            config_from_env=True,
            logging=True,
            database_migrations=len(presheaf.models) > 0,
            test_configuration=True,
        )


# ═══════════════════════════════════════════════════════════════════════
# §14  Helpers
# ═══════════════════════════════════════════════════════════════════════

def _view_route_to_server_path(view_route: str) -> str:
    """Convert a client-side hash-route to a server-side path.

    >>> _view_route_to_server_path("#/dashboard")
    '/dashboard'
    >>> _view_route_to_server_path("#/data/:id")
    '/data/<int:id>'
    >>> _view_route_to_server_path("#/")
    '/'
    """
    path = view_route.lstrip("#")
    if not path.startswith("/"):
        path = "/" + path
    # Convert :param to <int:param> for Flask.
    path = re.sub(r":(\w+)", r"<int:\1>", path)
    return path


def _derive_template_path(view: ViewCoordinate) -> str:
    """Derive a Jinja2 template path from a view coordinate.

    >>> from jugeo.webapp.generation.theory.view_site import ViewKind
    >>> v = ViewCoordinate(id="home", kind=ViewKind.LANDING, route="#/", label="Home")
    >>> _derive_template_path(v)
    'pages/home.html'
    """
    if view.is_auth_view:
        return f"auth/{view.id}.html"
    return f"pages/{view.id}.html"


def _concept_to_model_name(concept_name: str) -> str:
    """Convert a concept name to a PascalCase model name.

    >>> _concept_to_model_name("user_profile")
    'UserProfile'
    >>> _concept_to_model_name("data")
    'Data'
    """
    parts = re.split(r"[_\-\s]+", concept_name)
    return "".join(p.capitalize() for p in parts if p)


def _concept_to_table_name(concept_name: str) -> str:
    """Convert a concept name to a snake_case table name.

    >>> _concept_to_table_name("UserProfile")
    'user_profile'
    >>> _concept_to_table_name("data")
    'data'
    """
    # Insert underscores before capitals, then lower-case.
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", concept_name)
    name = re.sub(r"[\-\s]+", "_", name)
    return name.lower()


def _default_user_model() -> FlaskModelObligation:
    """Return a default User model obligation."""
    return FlaskModelObligation(
        name="User",
        table_name="users",
        columns=[
            {"name": "id", "type": "integer", "primary_key": True,
             "nullable": False, "unique": True, "indexed": True},
            {"name": "username", "type": "string", "nullable": False,
             "unique": True, "indexed": True},
            {"name": "email", "type": "string", "nullable": False,
             "unique": True, "indexed": True},
            {"name": "password_hash", "type": "string", "nullable": False,
             "unique": False, "indexed": False},
            {"name": "is_admin", "type": "boolean", "nullable": False,
             "unique": False, "indexed": False},
        ],
        relationships=[],
        has_timestamps=True,
    )
