"""
The Fetch API and network behavior as a functor between client and server sites.

The browser's ``fetch()`` API is not merely an HTTP helper — it is a *functor*
between two sites:

* The **client site** — the browser execution environment, whose coordinates
  are request origins, cache states, and connection pools.
* The **server site** — the remote resource hierarchy, whose coordinates are
  URL namespaces and content-addressed responses.

Each HTTP request is a **morphism** from a client coordinate to a server
coordinate.  The functor structure ensures that composing requests (e.g.
redirect chains) respects the covering topology.

**JSON serialisation** is a *fiber functor*: it lives over the identity on
URL coordinates but transforms the fiber of body representations from raw
bytes to structured objects.

**REST patterns** (GET/POST/PUT/DELETE) are the *standard morphisms* of this
category — they obey the universal properties one expects (idempotency of GET,
creation semantics of POST, etc.).

**WebSocket** is an *ongoing (persistent) morphism* — not a single arrow but a
co-span of continuous arrows kept open by the channel state machine.

This module provides:

1. :class:`FetchState` — lifecycle FSM for a single fetch.
2. :class:`CacheStrategy` — cache behaviour variants.
3. :class:`FetchRequest` — a request as a morphism with site metadata.
4. :class:`FetchResponse` — a response with parsed geometry.
5. :class:`RequestDeduplicator` — colimit-style sharing of in-flight requests.
6. :class:`RetryPolicy` — exponential-backoff retry envelope.
7. :class:`WebSocketChannel` — persistent bidirectional channel.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import Coordinate, CoveringFamily
from jugeo.geometry.descent import LocalSection, DescentResult, DescentObstruction

__all__ = [
    # Enumerations
    "FetchState",
    "CacheStrategy",
    # Core data types
    "FetchRequest",
    "FetchResponse",
    # Infrastructure
    "RequestDeduplicator",
    "RetryPolicy",
    "WebSocketChannel",
]

# ---------------------------------------------------------------------------
# 1. FetchState
# ---------------------------------------------------------------------------

class FetchState(str, Enum):
    """Lifecycle states of a single fetch operation.

    The transitions form a DAG rooted at ``IDLE``::

        IDLE → PENDING → FULFILLED
                       → REJECTED
                       → ABORTED
    """

    IDLE = "idle"
    PENDING = "pending"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    ABORTED = "aborted"


# ---------------------------------------------------------------------------
# 2. CacheStrategy
# ---------------------------------------------------------------------------

class CacheStrategy(str, Enum):
    """Cache behaviour variants for a fetch request.

    These correspond to the standard ``cache`` option of the Fetch API plus
    common service-worker strategies.
    """

    NO_STORE = "no-store"
    NO_CACHE = "no-cache"
    FORCE_CACHE = "force-cache"
    CACHE_FIRST = "cache-first"
    NETWORK_FIRST = "network-first"
    STALE_WHILE_REVALIDATE = "stale-while-revalidate"

    def allows_stale_response(self) -> bool:
        """Return ``True`` if the strategy may serve a stale cached response.

        ``CACHE_FIRST`` serves from cache without any freshness check.
        ``STALE_WHILE_REVALIDATE`` serves stale immediately while refreshing
        in the background.
        """
        return self in (CacheStrategy.CACHE_FIRST, CacheStrategy.STALE_WHILE_REVALIDATE)


# ---------------------------------------------------------------------------
# 3. FetchRequest
# ---------------------------------------------------------------------------

_SIMPLE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "POST"})
_SIMPLE_HEADERS: frozenset[str] = frozenset(
    {"accept", "accept-language", "content-language", "content-type"}
)
_SIMPLE_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "text/plain",
    }
)


@dataclass
class FetchRequest:
    """A network request modelled as a morphism in the fetch functor.

    The morphism goes from a client-site coordinate (the requesting origin)
    to a server-site coordinate (the target URL namespace).

    Parameters
    ----------
    request_id:
        Unique identifier for this request instance.
    url:
        Target URL string.
    method:
        HTTP verb; defaults to ``"GET"``.
    headers:
        Request headers as a plain mapping.
    body:
        Optional serialised request body.
    cache_strategy:
        How the request interacts with the browser cache.
    timeout_ms:
        Optional request timeout in milliseconds.
    credentials:
        Credential inclusion mode: ``"omit"``, ``"same-origin"``, or
        ``"include"``.
    """

    request_id: str
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    cache_strategy: CacheStrategy = CacheStrategy.NO_CACHE
    timeout_ms: float | None = None
    credentials: str = "same-origin"

    # ------------------------------------------------------------------
    # CORS helpers
    # ------------------------------------------------------------------

    def is_cors(self) -> bool:
        """Return ``True`` if the request requires CORS handling.

        A request is considered cross-origin (and therefore subject to CORS)
        when its URL carries an explicit scheme (``http``/``https``) — i.e. it
        is *not* a same-origin relative path.
        """
        lower = self.url.lower()
        return lower.startswith("http://") or lower.startswith("https://")

    def needs_preflight(self) -> bool:
        """Return ``True`` if the request will trigger a CORS preflight.

        A preflight is required when the request is cross-origin *and* uses a
        non-simple method or includes non-simple headers (or a non-simple
        ``Content-Type`` value for POST).
        """
        if not self.is_cors():
            return False
        if self.method.upper() not in _SIMPLE_METHODS:
            return True
        lower_headers = {k.lower(): v for k, v in self.headers.items()}
        for hdr_name, hdr_val in lower_headers.items():
            if hdr_name not in _SIMPLE_HEADERS:
                return True
            if hdr_name == "content-type":
                mime = hdr_val.split(";")[0].strip().lower()
                if mime not in _SIMPLE_CONTENT_TYPES:
                    return True
        return False

    # ------------------------------------------------------------------
    # Site geometry
    # ------------------------------------------------------------------

    def to_local_section(self, component_coord: str) -> LocalSection:
        """Represent this request as a local section over *component_coord*.

        The section encodes the morphism data (method, URL, headers, body)
        as ``judgment_data`` and tags its provenance as originating from the
        fetch functor.
        """
        return LocalSection(
            coordinate=component_coord,
            judgment_data={
                "request_id": self.request_id,
                "url": self.url,
                "method": self.method,
                "headers": dict(self.headers),
                "body": self.body,
                "cache_strategy": self.cache_strategy.value,
                "credentials": self.credentials,
                "is_cors": self.is_cors(),
                "needs_preflight": self.needs_preflight(),
            },
            provenance=("fetch_theory", "FetchRequest"),
        )


# ---------------------------------------------------------------------------
# 4. FetchResponse
# ---------------------------------------------------------------------------

@dataclass
class FetchResponse:
    """A network response modelled as the image of a fetch morphism.

    Parameters
    ----------
    request_id:
        Identifier matching the originating :class:`FetchRequest`.
    status:
        HTTP status code.
    headers:
        Response headers as a plain mapping.
    body_text:
        Optional decoded response body string.
    cache_hit:
        Whether the response was served from cache.
    latency_ms:
        Round-trip time in milliseconds, if measured.
    """

    request_id: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body_text: str | None = None
    cache_hit: bool = False
    latency_ms: float | None = None

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def is_ok(self) -> bool:
        """Return ``True`` for 2xx responses."""
        return 200 <= self.status <= 299

    def json_body(self) -> dict[str, Any] | None:
        """Parse the response body as JSON.

        Returns ``None`` if :attr:`body_text` is absent or not valid JSON.
        This is the *fiber functor* layer: it maps the raw-bytes fiber to the
        structured-objects fiber over the same URL coordinate.
        """
        if self.body_text is None:
            return None
        try:
            result = json.loads(self.body_text)
            return result if isinstance(result, dict) else {"__value__": result}
        except (json.JSONDecodeError, ValueError):
            return None

    def etag(self) -> str | None:
        """Return the ``ETag`` header value, or ``None`` if absent."""
        lower = {k.lower(): v for k, v in self.headers.items()}
        return lower.get("etag")

    def cache_control(self) -> dict[str, str]:
        """Parse the ``Cache-Control`` header into a directive mapping.

        Directives without a value are mapped to the empty string, e.g.::

            "no-cache, max-age=3600"
            → {"no-cache": "", "max-age": "3600"}
        """
        lower = {k.lower(): v for k, v in self.headers.items()}
        raw = lower.get("cache-control", "")
        directives: dict[str, str] = {}
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                name, _, value = part.partition("=")
                directives[name.strip().lower()] = value.strip()
            else:
                directives[part.lower()] = ""
        return directives


# ---------------------------------------------------------------------------
# 5. RequestDeduplicator
# ---------------------------------------------------------------------------

class RequestDeduplicator:
    """Colimit-based deduplication of in-flight GET requests.

    When two components independently issue the same GET request, only one
    network round-trip is needed.  The deduplicator acts as the *colimit
    construction*: it recognises when two morphisms share the same source and
    target and coalesces them.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, FetchRequest] = {}
        self._key_for_id: dict[str, str] = {}

    @staticmethod
    def cache_key(req: FetchRequest) -> str:
        """Compute a stable cache key from method, URL, and body.

        Two requests with the same key are semantically identical and safe
        to deduplicate.
        """
        raw = f"{req.method.upper()}:{req.url}:{req.body or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def should_deduplicate(self, req: FetchRequest) -> bool:
        """Return ``True`` if *req* is a GET with an identical request in flight."""
        if req.method.upper() != "GET":
            return False
        return self.cache_key(req) in self._in_flight

    def register(self, req: FetchRequest) -> None:
        """Mark *req* as in-flight."""
        key = self.cache_key(req)
        self._in_flight[key] = req
        self._key_for_id[req.request_id] = key

    def complete(self, req_id: str) -> None:
        """Remove the request identified by *req_id* from the in-flight set."""
        key = self._key_for_id.pop(req_id, None)
        if key is not None:
            self._in_flight.pop(key, None)

    def get_in_flight(self, key: str) -> FetchRequest | None:
        """Return the in-flight request for *key*, or ``None``."""
        return self._in_flight.get(key)


# ---------------------------------------------------------------------------
# 6. RetryPolicy
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    """Exponential-backoff retry envelope for transient HTTP failures.

    The retry envelope wraps a sequence of fetch attempts.  Each attempt is
    separated by a delay computed as::

        delay = min(initial_delay_ms × backoff_factor^attempt, max_delay_ms)

    Parameters
    ----------
    max_attempts:
        Maximum number of total attempts (including the first).
    initial_delay_ms:
        Base delay before the first retry, in milliseconds.
    backoff_factor:
        Multiplicative growth factor applied per attempt.
    max_delay_ms:
        Hard ceiling on the inter-attempt delay.
    retryable_statuses:
        HTTP status codes that warrant a retry.
    """

    max_attempts: int = 3
    initial_delay_ms: float = 100.0
    backoff_factor: float = 2.0
    max_delay_ms: float = 10_000.0
    retryable_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 429, 500, 502, 503, 504})
    )

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the delay in milliseconds before *attempt* (0-indexed).

        ``attempt=0`` corresponds to the very first retry (after the initial
        failed request).
        """
        delay = self.initial_delay_ms * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay_ms)

    def should_retry(self, status: int, attempt: int) -> bool:
        """Return ``True`` if another retry is warranted.

        Parameters
        ----------
        status:
            The HTTP status code returned by the most recent attempt.
        attempt:
            The number of attempts already made (1-indexed: ``1`` means the
            initial request has been sent once).
        """
        return status in self.retryable_statuses and attempt < self.max_attempts


# ---------------------------------------------------------------------------
# 7. WebSocketChannel
# ---------------------------------------------------------------------------

@dataclass
class WebSocketChannel:
    """A persistent bidirectional channel modelled as an ongoing morphism.

    In the fetch functor, a single HTTP request is a *point morphism* — it
    fires once and returns.  A WebSocket is different: it is an *ongoing
    morphism*, a co-span of continuous arrows that remains open for the
    lifetime of the connection.

    The channel state machine mirrors the browser's ``WebSocket.readyState``::

        connecting → open → closing → closed

    Parameters
    ----------
    channel_id:
        Unique identifier for this channel.
    url:
        WebSocket endpoint URL (``ws://`` or ``wss://``).
    state:
        Current readyState string.
    send_buffer:
        Messages queued for transmission.
    received_messages:
        Messages delivered by the remote end.
    """

    channel_id: str
    url: str
    state: str = "connecting"
    send_buffer: list[str] = field(default_factory=list)
    received_messages: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Message passing
    # ------------------------------------------------------------------

    def send(self, message: str) -> None:
        """Enqueue *message* for transmission to the remote end."""
        self.send_buffer.append(message)

    def receive(self, message: str) -> None:
        """Record *message* as received from the remote end."""
        self.received_messages.append(message)

    def is_open(self) -> bool:
        """Return ``True`` when the channel is in the ``open`` state."""
        return self.state == "open"

    # ------------------------------------------------------------------
    # Site geometry
    # ------------------------------------------------------------------

    def to_local_section(self, coord: str) -> LocalSection:
        """Represent the channel's current state as a local section.

        The section encodes the ongoing morphism: its identity, endpoint,
        readyState, and pending message counts, allowing the descent engine
        to reason about channel coherence across components.
        """
        return LocalSection(
            coordinate=coord,
            judgment_data={
                "channel_id": self.channel_id,
                "url": self.url,
                "state": self.state,
                "pending_sends": len(self.send_buffer),
                "received_count": len(self.received_messages),
                "is_open": self.is_open(),
            },
            provenance=("fetch_theory", "WebSocketChannel"),
            is_partial=not self.is_open(),
        )
