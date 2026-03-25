"""HTTP caching modelled as a presheaf over the URL site.

A cached response is a *section* — a locally-defined, consistent assignment of
data to an open set of URLs.  Cache invalidation is a *retraction* of that
section: the assignment is withdrawn, forcing the next request to consult the
origin server.

This module provides the building blocks needed to reason about that structure:
directives, parsed headers, freshness predicates, and a route-level policy
recommender.
"""
from __future__ import annotations

__all__ = [
    "CacheDirective",
    "CacheControl",
    "ETagPolicy",
    "CacheEntry",
    "RoutesCachePolicy",
]

import re
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# 1. Directives
# ---------------------------------------------------------------------------

class CacheDirective(str, Enum):
    """Known Cache-Control directive tokens."""

    NO_STORE = "no-store"
    NO_CACHE = "no-cache"
    PUBLIC = "public"
    PRIVATE = "private"
    MAX_AGE = "max-age"
    S_MAXAGE = "s-maxage"
    MUST_REVALIDATE = "must-revalidate"
    PROXY_REVALIDATE = "proxy-revalidate"
    IMMUTABLE = "immutable"
    STALE_WHILE_REVALIDATE = "stale-while-revalidate"
    STALE_IF_ERROR = "stale-if-error"


# ---------------------------------------------------------------------------
# 2. CacheControl
# ---------------------------------------------------------------------------

@dataclass
class CacheControl:
    """Parsed representation of a Cache-Control header.

    ``directives`` maps each directive token to either:
    - ``True``  — the directive is present without a value (e.g. "public")
    - ``str``   — the directive carries a value (e.g. "max-age=3600" → "3600")
    """

    directives: dict[str, str | bool] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, header_value: str) -> CacheControl:
        """Parse a raw Cache-Control header value into a :class:`CacheControl`.

        Example::

            CacheControl.parse("max-age=3600, public, immutable")
        """
        directives: dict[str, str | bool] = {}
        for token in re.split(r",\s*", header_value.strip()):
            token = token.strip()
            if not token:
                continue
            if "=" in token:
                key, _, value = token.partition("=")
                directives[key.strip().lower()] = value.strip().strip('"')
            else:
                directives[token.lower()] = True
        return cls(directives=directives)

    @classmethod
    def no_store(cls) -> CacheControl:
        """Nothing may be stored anywhere — not even by the browser."""
        return cls(directives={CacheDirective.NO_STORE: True})

    @classmethod
    def public_immutable(cls, max_age_s: int = 31_536_000) -> CacheControl:
        """Suitable for content-hashed static assets that never change."""
        return cls(
            directives={
                CacheDirective.PUBLIC: True,
                CacheDirective.MAX_AGE: str(max_age_s),
                CacheDirective.IMMUTABLE: True,
            }
        )

    @classmethod
    def private_no_cache(cls) -> CacheControl:
        """For authenticated or personalised pages: store in browser only,
        always revalidate before use."""
        return cls(
            directives={
                CacheDirective.PRIVATE: True,
                CacheDirective.NO_CACHE: True,
            }
        )

    @classmethod
    def api_default(cls) -> CacheControl:
        """For API endpoints: force revalidation on every request."""
        return cls(
            directives={
                CacheDirective.NO_CACHE: True,
                CacheDirective.MUST_REVALIDATE: True,
            }
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def max_age_seconds(self) -> int | None:
        """Return the ``max-age`` value in seconds, or ``None`` if absent."""
        value = self.directives.get(CacheDirective.MAX_AGE)
        if value is None or value is True:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def is_storable(self) -> bool:
        """Return ``True`` unless ``no-store`` is present."""
        return CacheDirective.NO_STORE not in self.directives

    def requires_revalidation(self) -> bool:
        """Return ``True`` when either ``no-cache`` or ``must-revalidate`` is set."""
        return (
            CacheDirective.NO_CACHE in self.directives
            or CacheDirective.MUST_REVALIDATE in self.directives
        )

    def to_header(self) -> str:
        """Serialise back to a valid Cache-Control header value."""
        parts: list[str] = []
        for key, value in self.directives.items():
            token = key.value if isinstance(key, CacheDirective) else str(key)
            if value is True:
                parts.append(token)
            else:
                parts.append(f"{token}={value}")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"CacheControl({self.to_header()!r})"


# ---------------------------------------------------------------------------
# 3. ETagPolicy
# ---------------------------------------------------------------------------

_WEAK_PREFIX = re.compile(r"^W/", re.IGNORECASE)


@dataclass
class ETagPolicy:
    """Describes how ETags are generated and compared for a resource.

    Attributes:
        etag_kind: Either ``"strong"`` or ``"weak"``.
    """

    etag_kind: str = "strong"

    def generate_etag(self, content: str) -> str:
        """Generate an ETag for *content*.

        Uses Python's built-in :func:`hash` — intentionally simple and
        deterministic within a single process run.  Prefixes weak ETags with
        ``W/``.
        """
        raw = str(hash(content))
        if self.etag_kind == "weak":
            return f'W/"{raw}"'
        return f'"{raw}"'

    def matches(self, etag: str, if_none_match: str) -> bool:
        """Return ``True`` when *etag* satisfies the *If-None-Match* value.

        Handles:
        - The ``*`` wildcard (matches any stored ETag).
        - Weak ``W/`` prefix — weak comparison strips the prefix before
          comparing the opaque tag value.
        - A comma-separated list of ETags in *if_none_match*.
        """
        if if_none_match.strip() == "*":
            return True

        def _normalise(e: str) -> str:
            return _WEAK_PREFIX.sub("", e.strip()).strip('"')

        normalised_etag = _normalise(etag)
        for candidate in if_none_match.split(","):
            if _normalise(candidate) == normalised_etag:
                return True
        return False


# ---------------------------------------------------------------------------
# 4. CacheEntry
# ---------------------------------------------------------------------------

@dataclass
class CacheEntry:
    """A single cached HTTP response — a *section* of the URL presheaf.

    Attributes:
        url:              The full URL that was cached.
        method:           HTTP method (typically ``"GET"``).
        status:           HTTP status code of the cached response.
        cache_control:    Parsed Cache-Control directives.
        etag:             ETag header value, if present.
        last_modified:    Last-Modified header value, if present.
        stored_at_s:      Unix timestamp (seconds) when the entry was stored.
        body_size_bytes:  Size of the response body in bytes.
    """

    url: str
    method: str
    status: int
    cache_control: CacheControl
    etag: str | None = None
    last_modified: str | None = None
    stored_at_s: float = 0.0
    body_size_bytes: int = 0

    # ------------------------------------------------------------------
    # Freshness
    # ------------------------------------------------------------------

    def is_fresh(self, current_time_s: float) -> bool:
        """Return ``True`` if the entry has not yet exceeded its ``max-age``.

        An entry with no ``max-age`` directive (or a non-storable one) is
        considered immediately stale.
        """
        if not self.cache_control.is_storable():
            return False
        max_age = self.cache_control.max_age_seconds()
        if max_age is None:
            return False
        return (self.stored_at_s + max_age) > current_time_s

    def is_stale(self, current_time_s: float) -> bool:
        """Return ``True`` when the entry is no longer fresh."""
        return not self.is_fresh(current_time_s)

    def can_serve_stale(self) -> bool:
        """Return ``True`` when the entry declares a stale-serving directive.

        ``stale-while-revalidate`` allows a stale response to be served while
        a background revalidation is in flight.  ``stale-if-error`` allows it
        when the origin returns an error.
        """
        return (
            CacheDirective.STALE_WHILE_REVALIDATE in self.cache_control.directives
            or CacheDirective.STALE_IF_ERROR in self.cache_control.directives
        )


# ---------------------------------------------------------------------------
# 5. RoutesCachePolicy
# ---------------------------------------------------------------------------

class RoutesCachePolicy:
    """Route-level cache policy advisor.

    Maps URL patterns and request properties to recommended
    :class:`CacheControl` configurations, and audits a route table for
    inconsistencies.
    """

    # ------------------------------------------------------------------
    # Policy recommendation
    # ------------------------------------------------------------------

    def recommend_policy(
        self,
        path: str,
        method: str,
        requires_auth: bool,
    ) -> CacheControl:
        """Return the recommended :class:`CacheControl` for a route.

        Decision tree (first match wins):

        1. Mutating methods (POST/PUT/PATCH/DELETE) → :meth:`CacheControl.no_store`.
        2. ``/static/*`` path prefix → :meth:`CacheControl.public_immutable`.
        3. GET + authenticated → :meth:`CacheControl.private_no_cache`.
        4. GET ``/api/*`` path prefix → :meth:`CacheControl.api_default`.
        5. GET (HTML pages, assumed personalised) → :meth:`CacheControl.private_no_cache`.
        """
        upper_method = method.upper()

        if upper_method in {"POST", "PUT", "PATCH", "DELETE"}:
            return CacheControl.no_store()

        if path.startswith("/static/"):
            return CacheControl.public_immutable()

        if requires_auth:
            return CacheControl.private_no_cache()

        if path.startswith("/api/"):
            return CacheControl.api_default()

        # Default for GET HTML pages — treat as personalised.
        return CacheControl.private_no_cache()

    # ------------------------------------------------------------------
    # Consistency audit
    # ------------------------------------------------------------------

    def check_consistency(
        self,
        routes: list[dict],
    ) -> list[tuple[str, str, str]]:
        """Audit a route table for caching anti-patterns.

        Parameters:
            routes: Each entry is a ``dict`` with keys:
                - ``path`` (str)
                - ``method`` (str)
                - ``requires_auth`` (bool)
                - ``cache_control_header`` (str) — the header value declared
                  on the route, e.g. ``"public, max-age=3600"``.

        Returns:
            A list of ``(path, issue, recommendation)`` triples for every
            inconsistency detected.  An empty list means no issues were found.

        Checks:
        - POST/PUT/PATCH/DELETE route with a ``public`` cache directive.
        - Authenticated route with a ``public`` cache directive.
        - ``/static/*`` route with ``no-store`` (prevents all caching —
          wasteful for immutable assets).
        """
        issues: list[tuple[str, str, str]] = []

        for route in routes:
            path: str = route.get("path", "")
            method: str = route.get("method", "GET").upper()
            requires_auth: bool = bool(route.get("requires_auth", False))
            cc_header: str = route.get("cache_control_header", "")

            if not cc_header:
                continue

            cc = CacheControl.parse(cc_header)
            is_public = CacheDirective.PUBLIC in cc.directives

            # Anti-pattern 1: mutating method with public cache.
            if method in {"POST", "PUT", "PATCH", "DELETE"} and is_public:
                issues.append((
                    path,
                    f"{method} route declares public cache — mutating responses "
                    "must never be cached publicly",
                    CacheControl.no_store().to_header(),
                ))

            # Anti-pattern 2: authenticated route with public cache.
            if requires_auth and is_public:
                issues.append((
                    path,
                    "Authenticated route declares public cache — user-specific "
                    "responses must not be stored in shared caches",
                    CacheControl.private_no_cache().to_header(),
                ))

            # Anti-pattern 3: static asset with no-store.
            if path.startswith("/static/") and not cc.is_storable():
                issues.append((
                    path,
                    "Static asset declares no-store — immutable assets should "
                    "be cached aggressively to avoid redundant transfers",
                    CacheControl.public_immutable().to_header(),
                ))

        return issues
