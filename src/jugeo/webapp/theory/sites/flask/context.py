"""Flask Request Context as a Presheaf
======================================

Flask's runtime context (``request``, ``g``, ``session``) is modelled as a
*presheaf* over the site of request coordinates.  Each piece of context is a
local section that exists only for a bounded scope:

* **``request``** — a :class:`RequestContextSection` that is pushed onto the
  context stack for the duration of a single HTTP request and then discarded.
  It is a local section over the route coordinate.

* **``g``** — a :class:`GSection` pushed with application context startup;
  it lives for the lifetime of the application context (typically one request
  when using the standard request context, but potentially longer in CLI/test
  contexts).  It is a section over the whole request coordinate.

* **``session``** — a :class:`SessionSection` that *persists* across requests
  via a signed cookie.  It is a persistent section whose stalk survives the
  teardown of any individual request context.

The :class:`FlaskContextStack` gathers all three into a single object
available inside a view function, analogous to the global section produced by
descent over the cover ``{request, session, g}``.
"""

from __future__ import annotations

__all__ = [
    "FlaskContextKind",
    "RequestContextSection",
    "SessionSection",
    "GSection",
    "FlaskContextStack",
    "BeforeRequestChecker",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.descent import (
    LocalSection,
    DescentResult,
    GlobalSection,
    DescentObstruction,
)


# ---------------------------------------------------------------------------
# 1.  FlaskContextKind
# ---------------------------------------------------------------------------

class FlaskContextKind(str, Enum):
    """Enumeration of Flask execution context kinds.

    The request context is pushed once per HTTP request and torn down on
    response; the application context is pushed with app startup (or the first
    request) and can span multiple requests in some configurations.

    request context pushes with each request; application context with app startup.
    """

    REQUEST = "request"
    APPLICATION = "application"
    SESSION = "session"
    TEST = "test"
    CLI = "cli"


# ---------------------------------------------------------------------------
# 2.  RequestContextSection
# ---------------------------------------------------------------------------

@dataclass
class RequestContextSection:
    """Models Flask's ``request`` proxy as a local section.

    Flask pushes a new :class:`RequestContextSection` onto the context stack
    for every HTTP request.  The section lives only for the duration of that
    request and is torn down (popped) when the response is finalised — making
    it a genuinely *local* section in the presheaf sense.
    """

    request_id: str
    method: str
    path: str
    args: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] = field(default_factory=dict)
    json_data: dict | None = None
    headers: dict[str, str] = field(default_factory=dict)
    remote_addr: str = ""
    cookies: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Conversion to geometry layer
    # ------------------------------------------------------------------

    def to_local_section(self, route_coord: str) -> LocalSection:
        """Embed this request section as a :class:`LocalSection` at *route_coord*.

        The ``judgment_data`` dict carries a flattened snapshot of the request
        so that downstream descent machinery can reason about it without holding
        a reference to the live ``RequestContextSection``.
        """
        return LocalSection(
            coordinate=route_coord,
            judgment_data={
                "request_id": self.request_id,
                "method": self.method,
                "path": self.path,
                "remote_addr": self.remote_addr,
                "is_json": self.is_json(),
                "is_xhr": self.is_xhr(),
                "has_json_data": self.json_data is not None,
                "arg_keys": list(self.args.keys()),
                "form_keys": list(self.form.keys()),
                "cookie_keys": list(self.cookies.keys()),
            },
            evidence_bundle=(f"request:{self.request_id}",),
            trust_level=1.0,
            provenance=("flask.request_context",),
            is_partial=False,
            residual_obligations=[],
        )

    # ------------------------------------------------------------------
    # Content-type helpers
    # ------------------------------------------------------------------

    def is_json(self) -> bool:
        """Return ``True`` when the ``Content-Type`` header signals JSON."""
        content_type = self.headers.get("Content-Type", "")
        return "application/json" in content_type

    def is_xhr(self) -> bool:
        """Return ``True`` when the request was issued via XMLHttpRequest.

        Relies on the conventional ``X-Requested-With: XMLHttpRequest`` header
        set by most JavaScript HTTP clients.
        """
        return (
            self.headers.get("X-Requested-With", "").strip().lower()
            == "xmlhttprequest"
        )

    # ------------------------------------------------------------------
    # Parameter accessors
    # ------------------------------------------------------------------

    def get_arg(self, name: str, default: str = "") -> str:
        """Return the query-string argument *name*, or *default* if absent."""
        return self.args.get(name, default)

    def get_form(self, name: str, default: str = "") -> str:
        """Return the POST form field *name*, or *default* if absent."""
        return self.form.get(name, default)


# ---------------------------------------------------------------------------
# 3.  SessionSection
# ---------------------------------------------------------------------------

@dataclass
class SessionSection:
    """Models Flask's ``session`` proxy as a persistent section.

    Unlike the request context, the session is *not* torn down between
    requests.  It is serialised into a signed cookie and re-hydrated on the
    next request, making it a persistent section in the presheaf sense — one
    whose stalk survives the teardown of any individual request context and is
    carried forward across the full sequence of requests from a client.
    """

    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    is_new: bool = False
    permanent: bool = False
    modified: bool = False

    # ------------------------------------------------------------------
    # dict-like interface
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the session value for *key*, or *default* if absent."""
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key* and mark the session as modified."""
        self.data[key] = value
        self.modified = True

    def pop(self, key: str) -> Any | None:
        """Remove *key* from the session and return its value, or ``None``."""
        value = self.data.pop(key, None)
        if value is not None:
            self.modified = True
        return value

    # ------------------------------------------------------------------
    # Conversion to geometry layer
    # ------------------------------------------------------------------

    def to_local_section(self, coord: str) -> LocalSection:
        """Embed this session as a :class:`LocalSection` at *coord*.

        The ``judgment_data`` carries non-sensitive metadata (keys only, not
        values) so that descent machinery can reason about session shape
        without leaking secrets.
        """
        return LocalSection(
            coordinate=coord,
            judgment_data={
                "session_id": self.session_id,
                "is_new": self.is_new,
                "permanent": self.permanent,
                "modified": self.modified,
                "is_authenticated": self.is_authenticated(),
                "data_keys": list(self.data.keys()),
            },
            evidence_bundle=(f"session:{self.session_id}",),
            trust_level=1.0,
            provenance=("flask.session_context",),
            is_partial=False,
            residual_obligations=[],
        )

    # ------------------------------------------------------------------
    # Auth helper
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        """Return ``True`` when ``user_id`` is present in the session data."""
        return "user_id" in self.data


# ---------------------------------------------------------------------------
# 4.  GSection
# ---------------------------------------------------------------------------

@dataclass
class GSection:
    """Models Flask's ``g`` object as an application-context section.

    ``g`` is a namespace that exists for the duration of the *application
    context* — typically one request, but potentially longer in CLI or testing
    configurations.  It is a section over the whole request coordinate: unlike
    the request or session, ``g`` is not tied to a particular client but to
    the current execution thread/coroutine.

    Common per-request resources (database connections, current user) are
    stored here under well-known keys captured as class-level constants.
    """

    DB_KEY: str = field(default="db", init=False, repr=False, compare=False)
    USER_KEY: str = field(default="current_user", init=False, repr=False, compare=False)

    values: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Namespace interface
    # ------------------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key* in the application context namespace."""
        self.values[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value stored under *key*, or *default* if absent."""
        return self.values.get(key, default)

    def has(self, key: str) -> bool:
        """Return ``True`` when *key* is present in the namespace."""
        return key in self.values


# ---------------------------------------------------------------------------
# 5.  FlaskContextStack
# ---------------------------------------------------------------------------

@dataclass
class FlaskContextStack:
    """The complete context available to a Flask request handler.

    A :class:`FlaskContextStack` bundles the three context layers — request,
    session, and ``g`` — together with the static application configuration,
    mirroring the set of sections that descent must glue into a coherent global
    section before a view function can safely execute.
    """

    request: RequestContextSection
    session: SessionSection
    g: GSection
    app_config: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Auth shortcut
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        """Return ``True`` when the current session contains a ``user_id``."""
        return self.session.is_authenticated()

    # ------------------------------------------------------------------
    # Descent integration
    # ------------------------------------------------------------------

    def check_required_context(
        self, required_keys: list[str]
    ) -> DescentResult:
        """Check that all *required_keys* are available in ``g`` or ``session``.

        Returns a successful :class:`DescentResult` wrapping a
        :class:`GlobalSection` when every key is present, or a failing result
        wrapping a :class:`DescentObstruction` when at least one key is
        missing — the missing keys form the obstruction.
        """
        missing = [
            k
            for k in required_keys
            if not self.g.has(k) and k not in self.session.data
        ]

        coord = self.request.path

        if not missing:
            section = GlobalSection(
                coordinate=coord,
                merged_judgment={
                    "required_keys": required_keys,
                    "all_present": True,
                    "request_id": self.request.request_id,
                },
                constituent_sections=(
                    f"request:{self.request.request_id}",
                    f"session:{self.session.session_id}",
                    "g",
                ),
            )
            return DescentResult.success(section)

        obstruction = DescentObstruction(
            coordinate=coord,
            partial_section={
                "required_keys": required_keys,
                "missing_keys": missing,
                "request_id": self.request.request_id,
            },
        )
        return DescentResult.failure(obstruction)

    def to_local_sections(self, route_coord: str) -> list[LocalSection]:
        """Return local sections for request, session, and ``g`` at *route_coord*.

        Produces three :class:`LocalSection` objects — one per context layer —
        suitable for feeding into the descent engine when reasoning about the
        full context cover.
        """
        g_section = LocalSection(
            coordinate=route_coord,
            judgment_data={
                "context_kind": FlaskContextKind.APPLICATION.value,
                "g_keys": list(self.g.values.keys()),
                "has_db": self.g.has(self.g.DB_KEY),
                "has_user": self.g.has(self.g.USER_KEY),
            },
            evidence_bundle=("flask.g",),
            trust_level=1.0,
            provenance=("flask.application_context",),
            is_partial=False,
            residual_obligations=[],
        )
        return [
            self.request.to_local_section(route_coord),
            self.session.to_local_section(route_coord),
            g_section,
        ]


# ---------------------------------------------------------------------------
# 6.  BeforeRequestChecker
# ---------------------------------------------------------------------------

class BeforeRequestChecker:
    """Utilities for implementing Flask ``@app.before_request`` logic.

    All methods are stateless and operate purely on the data passed in,
    making them easy to unit-test without a running Flask application.
    """

    # ------------------------------------------------------------------
    # Auth gate
    # ------------------------------------------------------------------

    def check_auth_required(
        self,
        route_path: str,
        auth_required_paths: list[str],
        context: FlaskContextStack,
    ) -> bool:
        """Return ``True`` when the request should be allowed to proceed.

        A request proceeds when either:

        * The requested *route_path* is **not** in *auth_required_paths*, or
        * The user **is** authenticated (session contains ``user_id``).

        Returns ``False`` only when the route requires authentication *and*
        the session has no ``user_id``.
        """
        route_requires_auth = any(
            route_path == protected or route_path.startswith(protected)
            for protected in auth_required_paths
        )
        if not route_requires_auth:
            return True
        return context.is_authenticated()

    # ------------------------------------------------------------------
    # CSRF gate
    # ------------------------------------------------------------------

    def check_csrf(
        self,
        context: FlaskContextStack,
        csrf_token_name: str = "csrf_token",
    ) -> bool:
        """Return ``True`` when the CSRF check passes.

        Safe methods (GET, HEAD, OPTIONS) are always allowed.  For all other
        methods the check passes when either:

        * The form data contains a field named *csrf_token_name*, or
        * The ``X-CSRFToken`` request header is present and non-empty.
        """
        safe_methods = {"GET", "HEAD", "OPTIONS"}
        if context.request.method.upper() in safe_methods:
            return True

        form_token = context.request.get_form(csrf_token_name)
        if form_token:
            return True

        header_token = context.request.headers.get("X-CSRFToken", "").strip()
        return bool(header_token)

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    def build_before_request_code(
        self,
        auth_routes: list[str],
        csrf_enabled: bool,
    ) -> str:
        """Generate the Python source for a ``@app.before_request`` function.

        The generated code is a self-contained string that can be written to a
        file or exec'd into an existing Flask application.  It references only
        Flask's standard ``request``, ``session``, and ``abort`` symbols.

        *auth_routes* is the list of path prefixes that require authentication.
        *csrf_enabled* controls whether the CSRF block is included.
        """
        auth_routes_repr = repr(auth_routes)

        csrf_block = ""
        if csrf_enabled:
            csrf_block = """\

    # CSRF check for state-mutating methods
    _safe_methods = {"GET", "HEAD", "OPTIONS"}
    if request.method.upper() not in _safe_methods:
        _csrf_ok = (
            request.form.get("csrf_token")
            or request.headers.get("X-CSRFToken", "").strip()
        )
        if not _csrf_ok:
            abort(403)
"""

        code = f"""\
@app.before_request
def before_request() -> None:
    \"\"\"Guard all requests with authentication and CSRF checks.\"\"\"
    _auth_required_paths = {auth_routes_repr}

    # Authentication check
    _route_needs_auth = any(
        request.path == p or request.path.startswith(p)
        for p in _auth_required_paths
    )
    if _route_needs_auth and "user_id" not in session:
        abort(401)
{csrf_block}"""

        return code
