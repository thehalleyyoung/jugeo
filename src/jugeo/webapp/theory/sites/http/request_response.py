"""HTTP request/response as adjoint functors on a Grothendieck site.

In sheaf-theoretic terms, HTTP models a *site* whose two key coordinates are
the client and the server.  A request is an arrow ``client → server`` (the
left adjoint functor that "asks"), and a response is the corresponding arrow
``server → client`` (the right adjoint that "answers").  Together they form a
single morphism — an HTTPExchange — which is a complete section of the
communication sheaf.

CORS, caching, and redirect behaviour all admit natural descriptions in this
language:

* **CORS** — the origin check decides whether the covering family
  ``{client, server}`` satisfies the descent condition; a failed preflight is
  an obstruction.
* **Caching** — a cached response is a locally-defined section that descends
  without contacting the server coordinate.
* **Redirects** — a 3xx response is a morphism that factors the exchange
  through an intermediate coordinate before the final server is reached.

References
----------
RFC 9110  — HTTP Semantics
RFC 9111  — HTTP Caching
Fetch Living Standard — CORS algorithm
"""

from __future__ import annotations

__all__ = [
    "HTTPStatusClass",
    "HTTPStatusCode",
    "HTTPHeader",
    "HTTPRequest",
    "HTTPResponse",
    "HTTPExchange",
]

from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
)
from jugeo.geometry.descent import LocalSection, DescentResult


# ---------------------------------------------------------------------------
# 1. HTTPStatusClass
# ---------------------------------------------------------------------------

class HTTPStatusClass(str, Enum):
    """Broad category of an HTTP status code.

    Each class corresponds to the leading digit of the three-digit code,
    following RFC 9110 §15.
    """

    INFORMATIONAL = "1xx"
    SUCCESS = "2xx"
    REDIRECTION = "3xx"
    CLIENT_ERROR = "4xx"
    SERVER_ERROR = "5xx"

    @staticmethod
    def from_code(code: int) -> HTTPStatusClass:
        """Return the status class for *code*.

        Parameters
        ----------
        code:
            An integer HTTP status code (100–599).

        Raises
        ------
        ValueError
            If *code* is outside the valid 100–599 range.
        """
        if 100 <= code <= 199:
            return HTTPStatusClass.INFORMATIONAL
        if 200 <= code <= 299:
            return HTTPStatusClass.SUCCESS
        if 300 <= code <= 399:
            return HTTPStatusClass.REDIRECTION
        if 400 <= code <= 499:
            return HTTPStatusClass.CLIENT_ERROR
        if 500 <= code <= 599:
            return HTTPStatusClass.SERVER_ERROR
        raise ValueError(f"Invalid HTTP status code: {code!r}")


# ---------------------------------------------------------------------------
# 2. HTTPStatusCode
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HTTPStatusCode:
    """A fully-described HTTP status code.

    Fields
    ------
    code:
        The three-digit integer code.
    phrase:
        The canonical reason phrase (e.g. ``"OK"``, ``"Not Found"``).
    status_class:
        The broad :class:`HTTPStatusClass` bucket.
    is_cacheable:
        Whether responses with this code *may* be stored by a shared cache
        when no explicit ``Cache-Control`` directive is present (RFC 9111 §3).
    allows_body:
        Whether a response with this code is permitted to carry a message
        body (RFC 9110 §6.4).  ``False`` for 204, 304, and 1xx codes.
    """

    code: int
    phrase: str
    status_class: HTTPStatusClass
    is_cacheable: bool
    allows_body: bool

    def __str__(self) -> str:
        return f"{self.code} {self.phrase}"

    def __repr__(self) -> str:
        return (
            f"HTTPStatusCode(code={self.code}, phrase={self.phrase!r}, "
            f"status_class={self.status_class!r}, "
            f"is_cacheable={self.is_cacheable}, allows_body={self.allows_body})"
        )

    @classmethod
    def standard_codes(cls) -> dict[int, HTTPStatusCode]:
        """Return a mapping of the most important standard HTTP status codes.

        The dictionary is keyed by the integer code.  Cacheability follows
        RFC 9111 §3; body rules follow RFC 9110 §6.4.
        """
        # (code, phrase, is_cacheable, allows_body)
        _specs: list[tuple[int, str, bool, bool]] = [
            # 1xx — Informational
            (100, "Continue",                        False, False),
            # 2xx — Success
            (200, "OK",                              True,  True),
            (201, "Created",                         False, True),
            (204, "No Content",                      False, False),
            (206, "Partial Content",                 True,  True),
            # 3xx — Redirection
            (301, "Moved Permanently",               True,  True),
            (302, "Found",                           False, True),
            (303, "See Other",                       False, True),
            (304, "Not Modified",                    True,  False),
            (307, "Temporary Redirect",              False, True),
            (308, "Permanent Redirect",              True,  True),
            # 4xx — Client Error
            (400, "Bad Request",                     False, True),
            (401, "Unauthorized",                    False, True),
            (403, "Forbidden",                       False, True),
            (404, "Not Found",                       True,  True),
            (405, "Method Not Allowed",              False, True),
            (409, "Conflict",                        False, True),
            (410, "Gone",                            True,  True),
            (412, "Precondition Failed",             False, True),
            (415, "Unsupported Media Type",          False, True),
            (422, "Unprocessable Content",           False, True),
            (429, "Too Many Requests",               False, True),
            # 5xx — Server Error
            (500, "Internal Server Error",           False, True),
            (501, "Not Implemented",                 True,  True),
            (502, "Bad Gateway",                     False, True),
            (503, "Service Unavailable",             False, True),
            (504, "Gateway Timeout",                 False, True),
        ]
        return {
            code: cls(
                code=code,
                phrase=phrase,
                status_class=HTTPStatusClass.from_code(code),
                is_cacheable=cacheable,
                allows_body=body,
            )
            for code, phrase, cacheable, body in _specs
        }


# ---------------------------------------------------------------------------
# 3. HTTPHeader
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HTTPHeader:
    """Metadata descriptor for a single HTTP header field.

    Header *names* are stored in lowercase following RFC 9110 §5.1 and the
    Fetch standard (which normalises names to ASCII lowercase).

    Fields
    ------
    name:
        Lowercase header field name (e.g. ``"content-type"``).
    value:
        A representative or default value; may be empty.
    is_request_header:
        True when this field is meaningful on a request message.
    is_response_header:
        True when this field is meaningful on a response message.
    is_cacheable_directive:
        True when the field participates in the RFC 9111 caching model
        (e.g. ``cache-control``, ``etag``, ``last-modified``).
    """

    name: str
    value: str
    is_request_header: bool
    is_response_header: bool
    is_cacheable_directive: bool

    @classmethod
    def standard_headers(cls) -> list[HTTPHeader]:
        """Return descriptors for the most important standard header fields.

        Each tuple is:
        ``(name, sample_value, is_request, is_response, is_cache_directive)``
        """
        # (name, sample_value, is_request, is_response, is_cache)
        _specs: list[tuple[str, str, bool, bool, bool]] = [
            # Representation / negotiation
            ("content-type",              "application/json",  True,  True,  False),
            ("content-length",            "0",                 True,  True,  False),
            ("content-encoding",          "gzip",              False, True,  False),
            ("accept",                    "*/*",               True,  False, False),
            ("accept-encoding",           "gzip, deflate, br", True,  False, False),
            ("accept-language",           "en-US,en;q=0.9",    True,  False, False),
            # Auth
            ("authorization",             "Bearer <token>",    True,  False, False),
            ("www-authenticate",          "Bearer realm=api",  False, True,  False),
            # Caching
            ("cache-control",             "max-age=3600",      True,  True,  True),
            ("etag",                      '"abc123"',          False, True,  True),
            ("last-modified",             "Wed, 21 Oct 2015 07:28:00 GMT",
                                                               False, True,  True),
            ("if-none-match",             '"abc123"',          True,  False, True),
            ("if-modified-since",         "Wed, 21 Oct 2015 07:28:00 GMT",
                                                               True,  False, True),
            ("expires",                   "Thu, 01 Dec 2099 16:00:00 GMT",
                                                               False, True,  True),
            ("vary",                      "Accept-Encoding",   False, True,  True),
            # Redirects / location
            ("location",                  "https://example.com/new",
                                                               False, True,  False),
            # Cookies
            ("set-cookie",                "sid=abc; HttpOnly; Secure",
                                                               False, True,  False),
            ("cookie",                    "sid=abc",           True,  False, False),
            # CORS
            ("origin",                    "https://example.com",
                                                               True,  False, False),
            ("access-control-allow-origin",  "*",             False, True,  False),
            ("access-control-allow-methods", "GET, POST",     False, True,  False),
            ("access-control-allow-headers", "Content-Type",  False, True,  False),
            ("access-control-allow-credentials", "true",      False, True,  False),
            ("access-control-max-age",    "86400",             False, True,  False),
            ("access-control-request-method",  "POST",        True,  False, False),
            ("access-control-request-headers", "Content-Type",True,  False, False),
            # Misc
            ("host",                      "example.com",       True,  False, False),
            ("user-agent",                "Mozilla/5.0",       True,  False, False),
            ("referer",                   "https://example.com/",
                                                               True,  False, False),
            ("x-request-id",              "",                  True,  True,  False),
        ]
        return [
            cls(
                name=name,
                value=value,
                is_request_header=req,
                is_response_header=resp,
                is_cacheable_directive=cache,
            )
            for name, value, req, resp, cache in _specs
        ]


# ---------------------------------------------------------------------------
# Simple-header set used by CORS "simple request" logic
# ---------------------------------------------------------------------------

_CORS_SIMPLE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "POST"})

_CORS_SIMPLE_REQUEST_HEADERS: frozenset[str] = frozenset({
    "accept",
    "accept-language",
    "content-language",
    "content-type",
    "range",
})

_CORS_SIMPLE_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
})


# ---------------------------------------------------------------------------
# 4. HTTPRequest
# ---------------------------------------------------------------------------

@dataclass
class HTTPRequest:
    """An HTTP request modelled as an arrow ``client → server``.

    In the site-theoretic picture this is the *left adjoint* functor: it
    carries the client's local section (the query intent) to the server
    coordinate, where the server functor (the right adjoint) produces a
    response.

    Fields
    ------
    request_id:
        Opaque correlation identifier (UUIDs, trace IDs, etc.).
    method:
        Uppercase HTTP method verb (``"GET"``, ``"POST"``, …).
    url:
        The fully-qualified request URL.
    headers:
        Lowercase-keyed mapping of header field names to values.
    body:
        Optional raw request body bytes.
    origin:
        The ``Origin`` header value when present (used for CORS logic).
    """

    request_id: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    origin: str | None = None

    # -- site-theoretic helpers ----------------------------------------------

    def to_local_section(self, client_coord: str) -> LocalSection:
        """Lift this request to a :class:`~jugeo.geometry.descent.LocalSection`.

        The judgment data encodes the full request so that a descent engine
        can check whether two parallel requests (e.g. from different clients)
        agree on their shared headers at the server coordinate.

        Parameters
        ----------
        client_coord:
            The string coordinate label for the issuing client
            (e.g. ``"client.browser.tab-1"``).
        """
        return LocalSection(
            coordinate=client_coord,
            judgment_data={
                "request_id": self.request_id,
                "method": self.method,
                "url": self.url,
                "headers": dict(self.headers),
                "has_body": self.body is not None,
                "origin": self.origin,
            },
            evidence_bundle=(f"http_request:{self.request_id}",),
            trust_level=1.0,
            provenance=("HTTPRequest.to_local_section",),
        )

    # -- CORS helpers --------------------------------------------------------

    def is_cors_request(self) -> bool:
        """Return ``True`` when this request carries a cross-origin ``Origin`` header.

        A request is cross-origin when the ``Origin`` header is present *and*
        its value differs from the host implied by the request URL (Fetch §3.1).
        """
        effective_origin = (self.origin or self.headers.get("origin", "")).strip()
        if not effective_origin:
            return False
        try:
            request_parsed = urlparse(self.url)
            origin_parsed = urlparse(effective_origin)
        except Exception:
            return False

        request_host = (
            f"{request_parsed.scheme}://{request_parsed.netloc}"
            if request_parsed.netloc
            else ""
        )
        return effective_origin.rstrip("/") != request_host.rstrip("/")

    def is_simple_request(self) -> bool:
        """Return ``True`` when this qualifies as a CORS *simple request*.

        A request is simple (Fetch §3.2.2) when:

        1. The method is ``GET``, ``HEAD``, or ``POST``.
        2. No headers beyond the "CORS-safelisted request-headers" are set.
        3. If ``Content-Type`` is present it is one of the three allowed MIME
           types (``application/x-www-form-urlencoded``, ``multipart/form-data``,
           ``text/plain``).
        """
        if self.method.upper() not in _CORS_SIMPLE_METHODS:
            return False

        for header_name in self.headers:
            name_lower = header_name.lower()
            if name_lower not in _CORS_SIMPLE_REQUEST_HEADERS:
                return False

        content_type = self.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and content_type not in _CORS_SIMPLE_CONTENT_TYPES:
            return False

        return True


# ---------------------------------------------------------------------------
# 5. HTTPResponse
# ---------------------------------------------------------------------------

@dataclass
class HTTPResponse:
    """An HTTP response modelled as an arrow ``server → client``.

    This is the *right adjoint* functor: it takes the server's local section
    (the computed result) back to the client coordinate, completing the
    adjunction.

    Fields
    ------
    request_id:
        Must match the originating :class:`HTTPRequest` ``request_id``.
    status:
        The :class:`HTTPStatusCode` describing the outcome.
    headers:
        Lowercase-keyed mapping of response header field names to values.
    body:
        Optional raw response body bytes.
    """

    request_id: str
    status: HTTPStatusCode
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None

    # -- site-theoretic helpers ----------------------------------------------

    def to_local_section(self, server_coord: str) -> LocalSection:
        """Lift this response to a :class:`~jugeo.geometry.descent.LocalSection`.

        The judgment data encodes the full response so that a descent engine
        can verify that a cached section is compatible with the fresh one.

        Parameters
        ----------
        server_coord:
            The string coordinate label for the responding server
            (e.g. ``"server.api.v1"``).
        """
        return LocalSection(
            coordinate=server_coord,
            judgment_data={
                "request_id": self.request_id,
                "status_code": self.status.code,
                "status_phrase": self.status.phrase,
                "headers": dict(self.headers),
                "has_body": self.body is not None,
                "is_cacheable": self.status.is_cacheable,
            },
            evidence_bundle=(f"http_response:{self.request_id}:{self.status.code}",),
            trust_level=1.0,
            provenance=("HTTPResponse.to_local_section",),
        )

    # -- caching helpers -----------------------------------------------------

    def cache_key(self) -> str | None:
        """Return a cache key string, or ``None`` when the response is not cacheable.

        A response is cacheable when:

        1. The status code is marked ``is_cacheable``, *and*
        2. ``Cache-Control`` does not contain ``no-store``.

        The cache key is the request URL embedded in the ``content-location``
        header (if present), otherwise derived from ``request_id``.  Callers
        that maintain a URL-keyed cache should typically build the key from the
        request URL directly; this method is intentionally conservative.
        """
        if not self.status.is_cacheable:
            return None

        cc = self.headers.get("cache-control", "")
        if "no-store" in cc:
            return None

        location = self.headers.get("content-location")
        if location:
            return f"url:{location}"

        return f"request_id:{self.request_id}:{self.status.code}"

    # -- CORS helpers --------------------------------------------------------

    def is_cors_allowed(self, request: HTTPRequest) -> bool:
        """Return ``True`` when this response grants CORS access to *request*.

        Checks the ``Access-Control-Allow-Origin`` response header against the
        request's ``Origin`` following the Fetch standard §3.2.5:

        * A wildcard ``*`` permits any origin, *but only for requests without
          credentials*.
        * An explicit origin must match exactly (scheme + host + port).
        """
        acao = self.headers.get("access-control-allow-origin", "").strip()
        if not acao:
            return False

        request_origin = (
            request.origin or request.headers.get("origin", "")
        ).strip()

        if acao == "*":
            # Wildcard is only valid for credentialless requests
            has_credentials = (
                "cookie" in request.headers
                or "authorization" in request.headers
                or request.headers.get("access-control-allow-credentials", "").lower() == "true"
            )
            return not has_credentials

        return acao.rstrip("/") == request_origin.rstrip("/")


# ---------------------------------------------------------------------------
# 6. HTTPExchange
# ---------------------------------------------------------------------------

@dataclass
class HTTPExchange:
    """A complete HTTP exchange: request + optional response.

    The exchange is the *composition* of the two adjoint morphisms — the
    whole round-trip ``client → server → client`` that constitutes a single
    unit of communication over the HTTP site.

    A pending exchange (no response yet) represents an open morphism that
    has not yet been completed.

    Fields
    ------
    request:
        The outgoing :class:`HTTPRequest`.
    response:
        The incoming :class:`HTTPResponse`, or ``None`` if not yet received.
    latency_ms:
        Round-trip time in milliseconds, measured by the client.
    cached:
        ``True`` when the response was served from a local cache without
        contacting the server coordinate.
    """

    request: HTTPRequest
    response: HTTPResponse | None = None
    latency_ms: float | None = None
    cached: bool = False

    # -- outcome predicates --------------------------------------------------

    def is_successful(self) -> bool:
        """Return ``True`` when the response carries a 2xx status code."""
        if self.response is None:
            return False
        return self.response.status.status_class is HTTPStatusClass.SUCCESS

    def is_redirect(self) -> bool:
        """Return ``True`` when the response carries a 3xx status code."""
        if self.response is None:
            return False
        return self.response.status.status_class is HTTPStatusClass.REDIRECTION

    def follow_redirect(self) -> str | None:
        """Return the ``Location`` header value when this is a redirect.

        Returns ``None`` when the exchange is not a redirect or when the
        ``Location`` header is absent.  Callers are responsible for resolving
        relative URLs against the original request URL.
        """
        if not self.is_redirect():
            return None
        return self.response.headers.get("location") or None  # type: ignore[union-attr]
