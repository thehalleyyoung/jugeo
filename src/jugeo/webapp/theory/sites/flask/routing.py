"""Flask URL Routing as a Grothendieck Site
==========================================

Flask's URL dispatcher is modelled as a *Grothendieck site* whose underlying
category has:

* **Objects (Coordinates)** — individual route endpoints and blueprint prefixes.
* **Morphisms** — inclusion maps that embed a route into a blueprint, or a
  blueprint into the full URL space.
* **Covering families** — collections of morphisms whose sources together
  "cover" the target; the set of all routes covers the root URL space, while
  the routes of a single blueprint cover its prefix coordinate.

URL parameters are *fiber data* attached to each route coordinate: path
parameters correspond to locally-defined fibers (varying per request), while
query and body parameters are sections over those fibers.

Blueprints are *sub-sites*: each blueprint induces its own :class:`Site` and a
canonical inclusion morphism into the ambient site.
"""

from __future__ import annotations

__all__ = [
    "HTTPMethod",
    "URLParameterKind",
    "URLParameter",
    "FlaskRoute",
    "FlaskBlueprint",
    "FlaskURLSite",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
)
from jugeo.geometry.descent import LocalSection


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CONVERTER_REGEX: dict[str, str] = {
    "int":    r"[0-9]+",
    "float":  r"[0-9]+(?:\.[0-9]+)?",
    "uuid":   r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    "path":   r".+",
    "string": r"[^/]+",
    "":       r"[^/]+",
}

# Concrete substitutes used when checking whether pattern A matches pattern B.
_CONVERTER_EXAMPLE: dict[str, str] = {
    "int":    "1",
    "float":  "1.0",
    "uuid":   "00000000-0000-0000-0000-000000000000",
    "path":   "a/b",
    "string": "value",
    "":       "value",
}


def _flask_pattern_to_regex(url_pattern: str) -> re.Pattern[str]:
    """Compile a Flask URL pattern such as ``/users/<int:user_id>`` to a regex.

    Each ``<converter:name>`` (or bare ``<name>``) token is replaced by the
    corresponding named-capture group regex fragment so that the resulting
    pattern can be used for URL matching.
    """
    parts = re.split(r"(<[^>]+>)", url_pattern)
    regex_fragments: list[str] = []
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            inner = part[1:-1]
            converter = inner.split(":", 1)[0] if ":" in inner else ""
            fragment = _CONVERTER_REGEX.get(converter, r"[^/]+")
            regex_fragments.append(fragment)
        else:
            regex_fragments.append(re.escape(part))
    return re.compile(r"^" + "".join(regex_fragments) + r"$")


def _concrete_url_from_pattern(url_pattern: str) -> str:
    """Replace every dynamic segment with a representative concrete value."""
    def _substitute(m: re.Match[str]) -> str:
        inner = m.group(1)
        converter = inner.split(":", 1)[0] if ":" in inner else ""
        return _CONVERTER_EXAMPLE.get(converter, "value")

    return re.sub(r"<([^>]+)>", _substitute, url_pattern)


def _patterns_may_conflict(p1: str, p2: str) -> bool:
    """Return *True* if two Flask URL patterns could match the same request path.

    The check is heuristic rather than exhaustive:

    1. Identical patterns always conflict.
    2. After collapsing dynamic segments to ``_DYN_``, structurally equal
       patterns conflict.
    3. A concrete URL derived from *p2* is tested against *p1*'s regex, and
       vice-versa.
    """
    if p1 == p2:
        return True

    _dyn = re.compile(r"<[^>]+>")
    if _dyn.sub("_DYN_", p1) == _dyn.sub("_DYN_", p2):
        return True

    try:
        if _flask_pattern_to_regex(p1).match(_concrete_url_from_pattern(p2)):
            return True
        if _flask_pattern_to_regex(p2).match(_concrete_url_from_pattern(p1)):
            return True
    except re.error:
        pass

    return False


# ---------------------------------------------------------------------------
# 1. HTTPMethod
# ---------------------------------------------------------------------------

class HTTPMethod(str, Enum):
    """Standard HTTP verbs understood by Flask."""

    GET     = "GET"
    POST    = "POST"
    PUT     = "PUT"
    PATCH   = "PATCH"
    DELETE  = "DELETE"
    HEAD    = "HEAD"
    OPTIONS = "OPTIONS"

    def is_safe(self) -> bool:
        """Return *True* for methods that must not alter server state.

        Safe methods (GET, HEAD, OPTIONS) carry no side-effects by convention
        and may be cached or prefetched freely.
        """
        return self in {HTTPMethod.GET, HTTPMethod.HEAD, HTTPMethod.OPTIONS}

    def is_idempotent(self) -> bool:
        """Return *True* for methods whose repeated application is equivalent.

        Idempotent methods (GET, HEAD, PUT, DELETE, OPTIONS) produce the same
        server state when called once or many times with the same arguments.
        POST and PATCH are explicitly excluded because they may accumulate
        effects on each invocation.
        """
        return self in {
            HTTPMethod.GET,
            HTTPMethod.HEAD,
            HTTPMethod.PUT,
            HTTPMethod.DELETE,
            HTTPMethod.OPTIONS,
        }


# ---------------------------------------------------------------------------
# 2. URLParameterKind
# ---------------------------------------------------------------------------

class URLParameterKind(str, Enum):
    """Taxonomy of Flask request-parameter sources and types.

    Path parameters (``PATH_*``) are embedded directly in the URL segment and
    correspond to converters declared in the route pattern.  Query parameters
    (``QUERY_*``) arrive after the ``?`` separator.  Body parameters
    (``BODY_*``) are extracted from the request body.
    """

    PATH_STRING  = "path_string"
    PATH_INT     = "path_int"
    PATH_FLOAT   = "path_float"
    PATH_UUID    = "path_uuid"
    PATH_PATH    = "path_path"

    QUERY_STRING = "query_string"
    QUERY_INT    = "query_int"
    QUERY_BOOL   = "query_bool"
    QUERY_LIST   = "query_list"

    BODY_JSON    = "body_json"
    BODY_FORM    = "body_form"
    BODY_FILE    = "body_file"

    def is_path(self) -> bool:
        """Return *True* for parameter kinds embedded in the URL path."""
        return self.value.startswith("path_")

    def is_query(self) -> bool:
        """Return *True* for parameters supplied as query-string entries."""
        return self.value.startswith("query_")

    def is_body(self) -> bool:
        """Return *True* for parameters carried in the request body."""
        return self.value.startswith("body_")


# ---------------------------------------------------------------------------
# 3. URLParameter
# ---------------------------------------------------------------------------

@dataclass
class URLParameter:
    """A single named parameter attached to a Flask route.

    Parameters act as *fiber data*: each concrete request picks a point in the
    URL space (the base) and lifts it to a fiber value determined by the
    parameter value supplied at runtime.

    Attributes
    ----------
    name:
        The parameter name as it appears in Flask (e.g. ``user_id``).
    kind:
        Where the parameter value comes from and what type it carries.
    required:
        Whether the caller must supply a value.  Path parameters are always
        required; query and body parameters may be optional.
    default:
        Fallback value when *required* is ``False`` and no value is supplied.
    validation_pattern:
        An optional regex that parameter values must satisfy, used to express
        domain constraints beyond the bare type.
    """

    name:               str
    kind:               URLParameterKind
    required:           bool       = True
    default:            Any | None = None
    validation_pattern: str | None = None


# ---------------------------------------------------------------------------
# 4. FlaskRoute
# ---------------------------------------------------------------------------

@dataclass
class FlaskRoute:
    """A single Flask route modelled as a coordinate in the URL site.

    Each route occupies a unique *coordinate* in the Grothendieck site whose
    underlying space is the application's URL structure.  The route's
    parameters constitute the *fiber* above that coordinate: a request matched
    to this route supplies values for each parameter, giving a concrete section
    of the parameter bundle.

    Attributes
    ----------
    endpoint_name:
        Flask's internal name for the endpoint (the function name or the name
        passed to ``app.add_url_rule``).
    url_pattern:
        The URL rule string with optional converters, e.g.
        ``"/users/<int:user_id>/posts/<slug>"``.
    methods:
        HTTP verbs accepted by this route.
    blueprint_name:
        Name of the owning :class:`FlaskBlueprint`, or ``None`` for routes
        registered directly on the application.
    parameters:
        Ordered list of :class:`URLParameter` objects documenting every
        parameter the view function reads.
    requires_auth:
        Whether the route requires an authenticated session.
    rate_limited:
        Whether the route is subject to a rate-limiting policy.
    """

    endpoint_name:  str
    url_pattern:    str
    methods:        list[HTTPMethod]
    blueprint_name: str | None         = None
    parameters:     list[URLParameter] = field(default_factory=list)
    requires_auth:  bool               = False
    rate_limited:   bool               = False

    # ------------------------------------------------------------------
    # Geometric projection
    # ------------------------------------------------------------------

    def to_coordinate(self) -> Coordinate:
        """Represent this route as a :class:`Coordinate` in the URL site.

        The endpoint name becomes the coordinate's *name* component, and the
        kind is set to ``FUNCTION`` because a route ultimately maps to a
        callable view function.
        """
        return Coordinate(
            name=self.endpoint_name,
            kind=CoordinateKind.FUNCTION,
            metadata={
                "url_pattern":    self.url_pattern,
                "methods":        [m.value for m in self.methods],
                "blueprint_name": self.blueprint_name,
                "requires_auth":  self.requires_auth,
                "rate_limited":   self.rate_limited,
            },
        )

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def full_path(self, blueprint_prefix: str = "") -> str:
        """Return the absolute URL pattern, prepending *blueprint_prefix*.

        >>> r = FlaskRoute("index", "/", [HTTPMethod.GET], None, [])
        >>> r.full_path("/api/v1")
        '/api/v1/'
        """
        if not blueprint_prefix:
            return self.url_pattern
        return blueprint_prefix.rstrip("/") + "/" + self.url_pattern.lstrip("/")

    # ------------------------------------------------------------------
    # Parameter projections
    # ------------------------------------------------------------------

    def path_parameters(self) -> list[URLParameter]:
        """Return only the parameters sourced from URL path segments."""
        return [p for p in self.parameters if p.kind.is_path()]

    def query_parameters(self) -> list[URLParameter]:
        """Return only the parameters sourced from the query string."""
        return [p for p in self.parameters if p.kind.is_query()]

    def body_parameters(self) -> list[URLParameter]:
        """Return only the parameters sourced from the request body."""
        return [p for p in self.parameters if p.kind.is_body()]


# ---------------------------------------------------------------------------
# 5. FlaskBlueprint
# ---------------------------------------------------------------------------

@dataclass
class FlaskBlueprint:
    """A Flask Blueprint modelled as a *sub-site* of the URL site.

    A blueprint groups related routes under a shared URL prefix and an
    isolated namespace.  Geometrically it induces a sub-category of the URL
    site together with a canonical inclusion functor into the ambient site.

    Attributes
    ----------
    name:
        The blueprint's registration name (must be unique within the app).
    url_prefix:
        The common URL prefix prepended to every route in the blueprint.
    routes:
        Routes belonging to this blueprint.
    """

    name:       str
    url_prefix: str
    routes:     list[FlaskRoute] = field(default_factory=list)

    def to_sub_site(self) -> Site:
        """Build a :class:`Site` whose coordinates are the blueprint's routes.

        A *blueprint prefix* coordinate is added as the base object; each
        route coordinate is then connected to it via an inclusion morphism,
        reflecting the fact that blueprint routes are specialisations of the
        common URL prefix.
        """
        sub_site = Site(label=f"blueprint:{self.name}")

        # Root object: the blueprint prefix itself
        prefix_coord = Coordinate(
            name=f"blueprint:{self.name}",
            kind=CoordinateKind.REGION,
            metadata={"url_prefix": self.url_prefix},
        )
        sub_site.add_coordinate(prefix_coord)

        for route in self.routes:
            route_coord = route.to_coordinate()
            sub_site.add_coordinate(route_coord)
            sub_site.add_morphism(
                Morphism(
                    source=route_coord,
                    target=prefix_coord,
                    kind=MorphismKind.INCLUSION,
                    label=f"{route.endpoint_name}→{self.name}",
                )
            )

        return sub_site


# ---------------------------------------------------------------------------
# 6. FlaskURLSite
# ---------------------------------------------------------------------------

#: Coordinate representing the root of the entire URL space.
_URL_ROOT = Coordinate(name="url_space", kind=CoordinateKind.REGION)


class FlaskURLSite:
    """The full URL space of a Flask application as a Grothendieck site.

    The site has:

    * A root coordinate ``url_space`` representing the application as a whole.
    * One coordinate per registered :class:`FlaskRoute`.
    * One coordinate per registered :class:`FlaskBlueprint` prefix.
    * Inclusion morphisms from each route / blueprint prefix up to
      ``url_space``, and from blueprint routes up to their blueprint prefix.
    * Covering families certifying that routes cover the URL space.
    """

    def __init__(self) -> None:
        self._site = Site(label="flask_url_site")
        self._routes: dict[str, FlaskRoute] = {}
        self._blueprints: dict[str, FlaskBlueprint] = {}

        # Seed the site with the root coordinate.
        self._site.add_coordinate(_URL_ROOT)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_route(self, route: FlaskRoute) -> None:
        """Register *route* in the URL site.

        The route's coordinate is added to the underlying :class:`Site` and an
        inclusion morphism is created from the route coordinate to
        ``url_space``.
        """
        coord = route.to_coordinate()
        self._site.add_coordinate(coord)
        self._site.add_morphism(
            Morphism(
                source=coord,
                target=_URL_ROOT,
                kind=MorphismKind.INCLUSION,
                label=f"{route.endpoint_name}→url_space",
            )
        )
        self._routes[route.endpoint_name] = route

    def add_blueprint(self, bp: FlaskBlueprint) -> None:
        """Register a blueprint and wire its routes into the URL site.

        Steps performed:

        1. A *blueprint prefix* coordinate is added to the ambient site.
        2. An inclusion morphism connects the prefix coordinate to
           ``url_space``.
        3. Every route in the blueprint is added via :meth:`add_route`, then
           an additional inclusion morphism connects each route coordinate to
           the blueprint prefix, capturing the sub-site structure.
        """
        prefix_coord = Coordinate(
            name=f"blueprint:{bp.name}",
            kind=CoordinateKind.REGION,
            metadata={"url_prefix": bp.url_prefix},
        )
        self._site.add_coordinate(prefix_coord)
        self._site.add_morphism(
            Morphism(
                source=prefix_coord,
                target=_URL_ROOT,
                kind=MorphismKind.INCLUSION,
                label=f"blueprint:{bp.name}→url_space",
            )
        )

        for route in bp.routes:
            # Stamp each route with the owning blueprint name if not already set.
            if route.blueprint_name is None:
                route.blueprint_name = bp.name

            self.add_route(route)

            route_coord = route.to_coordinate()
            self._site.add_morphism(
                Morphism(
                    source=route_coord,
                    target=prefix_coord,
                    kind=MorphismKind.INCLUSION,
                    label=f"{route.endpoint_name}→blueprint:{bp.name}",
                )
            )

        self._blueprints[bp.name] = bp

    # ------------------------------------------------------------------
    # Covering families
    # ------------------------------------------------------------------

    def url_covering(self) -> CoveringFamily:
        """Return a covering family that covers the full URL space.

        Every registered route contributes an inclusion morphism into
        ``url_space``; together they form a *covering sieve* in the
        Grothendieck topology, asserting that any request URL is handled by
        at least one route.
        """
        members: list[Morphism] = [
            Morphism(
                source=route.to_coordinate(),
                target=_URL_ROOT,
                kind=MorphismKind.INCLUSION,
                label=f"{name}→url_space",
            )
            for name, route in self._routes.items()
        ]
        return CoveringFamily(
            base=_URL_ROOT,
            members=members,
            label="full_url_covering",
        )

    def blueprint_covering(self, bp_name: str) -> CoveringFamily:
        """Return a covering family for the routes within *bp_name*.

        The base of the covering family is the blueprint-prefix coordinate;
        members are the inclusion morphisms from each route coordinate to that
        prefix, certifying that the blueprint's routes jointly cover its
        portion of the URL space.

        Raises
        ------
        KeyError
            If *bp_name* is not a registered blueprint.
        """
        if bp_name not in self._blueprints:
            raise KeyError(f"Unknown blueprint: {bp_name!r}")

        bp = self._blueprints[bp_name]
        prefix_coord = Coordinate(
            name=f"blueprint:{bp.name}",
            kind=CoordinateKind.REGION,
            metadata={"url_prefix": bp.url_prefix},
        )
        members: list[Morphism] = [
            Morphism(
                source=route.to_coordinate(),
                target=prefix_coord,
                kind=MorphismKind.INCLUSION,
                label=f"{route.endpoint_name}→blueprint:{bp_name}",
            )
            for route in bp.routes
        ]
        return CoveringFamily(
            base=prefix_coord,
            members=members,
            label=f"blueprint_covering:{bp_name}",
        )

    # ------------------------------------------------------------------
    # URL matching
    # ------------------------------------------------------------------

    def match_url(self, url: str, method: str) -> FlaskRoute | None:
        """Find the first registered route that matches *url* and *method*.

        Matching uses the same converter-aware regex derived from each route's
        URL pattern.  Static routes (no dynamic segments) are tried before
        dynamic routes so that exact matches take priority, mirroring Flask's
        own dispatcher heuristic.

        Returns ``None`` if no route matches.
        """
        http_method = HTTPMethod(method.upper()) if method.upper() in HTTPMethod._value2member_map_ else None  # noqa: SLF001
        if http_method is None:
            return None

        # Sort: routes without dynamic segments first (higher priority).
        candidates = sorted(
            self._routes.values(),
            key=lambda r: (1 if "<" in r.url_pattern else 0),
        )
        for route in candidates:
            if http_method not in route.methods:
                continue
            try:
                if _flask_pattern_to_regex(route.url_pattern).match(url):
                    return route
            except re.error:
                continue
        return None

    # ------------------------------------------------------------------
    # Static analysis
    # ------------------------------------------------------------------

    def detect_conflicts(self) -> list[tuple[str, str]]:
        """Return pairs of endpoint names whose URL patterns may conflict.

        Two routes *conflict* when their HTTP method sets overlap and their URL
        patterns could both match the same request path.  This is a common
        source of subtle bugs where one route unintentionally shadows another.

        The check is heuristic (see :func:`_patterns_may_conflict`); it may
        produce false positives for routes with mutually exclusive parameter
        constraints.
        """
        conflicts: list[tuple[str, str]] = []
        routes = list(self._routes.values())

        for i, r1 in enumerate(routes):
            methods1 = set(r1.methods)
            for r2 in routes[i + 1:]:
                if not methods1.intersection(r2.methods):
                    continue
                if _patterns_may_conflict(r1.url_pattern, r2.url_pattern):
                    conflicts.append((r1.endpoint_name, r2.endpoint_name))

        return conflicts

    def orphaned_parameters(
        self,
        route_name: str,
        template_vars: list[str],
    ) -> list[str]:
        """Return parameter names defined on *route_name* but absent from *template_vars*.

        A parameter is *orphaned* when the route declares it (via
        :attr:`FlaskRoute.parameters`) but the associated template never
        references it.  Orphaned parameters may indicate dead code, a naming
        mismatch, or a parameter that should have been removed.

        Parameters
        ----------
        route_name:
            The :attr:`~FlaskRoute.endpoint_name` of the route to inspect.
        template_vars:
            Variable names that appear in the Jinja2 template rendered by the
            view (typically obtained by static analysis of the template source).

        Raises
        ------
        KeyError
            If *route_name* is not a registered route.
        """
        if route_name not in self._routes:
            raise KeyError(f"Unknown route: {route_name!r}")

        route = self._routes[route_name]
        used = set(template_vars)
        return [p.name for p in route.parameters if p.name not in used]

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def site(self) -> Site:
        """The underlying :class:`Site` object."""
        return self._site

    @property
    def routes(self) -> dict[str, FlaskRoute]:
        """Registered routes keyed by endpoint name."""
        return dict(self._routes)

    @property
    def blueprints(self) -> dict[str, FlaskBlueprint]:
        """Registered blueprints keyed by blueprint name."""
        return dict(self._blueprints)

    def __repr__(self) -> str:
        return (
            f"FlaskURLSite("
            f"routes={len(self._routes)}, "
            f"blueprints={len(self._blueprints)})"
        )
