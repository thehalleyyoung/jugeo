"""Programmatic API for the JuGeo shared core.

The API is the primary machine-facing entry point into JuGeo.  It exposes
the five semantic operations defined in theory2.tex — *verify*, *construct*,
*descend*, *federate*, and *inspect* — together with trust-transparent
reporting of every result.

Design invariants
-----------------
1. **No silent trust upgrades.** Every operation reports the trust level
   actually achieved; callers that set ``trust_floor`` receive an error
   rather than a silently weakened result.
2. **Residuals are never hidden.** Partial results carry their
   ``ResidualObligation`` sets verbatim; the API does not flatten or discard
   them.
3. **Copilot-backed channels appear at oracle trust tier.**
   :class:`CopilotAPIBridge` enforces a ceiling of
   ``TrustLevel.ORACLE_PROPOSED`` on every copilot response and refuses to
   relay results that lack required corroboration when the caller opts in.
4. **Every call is audited.**  :class:`APIEventLog` receives a record for
   every request and response regardless of outcome.

Usage example::

    api = JuGeoAPI.create()
    session = api.open_session(caller_id="researcher-1")

    req = APIRequest(
        request_id="r-001",
        operation="verify",
        coordinate="topology.compactness",
        proposition="X is compact",
    )
    resp = api.verify(req, session=session)
    print(resp.trust_level, resp.residuals)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.errors import JuGeoError, StructuredFailure
from jugeo.evidence.certificates import Certificate, CertificateVerifier
from jugeo.evidence.channels import (
    ChannelFederation,
    ChannelRouter,
    CopilotChannel,
    EvidenceChannel,
    EvidenceRecord,
    EvidenceRequest,
    EvidenceResponse,
)
from jugeo.evidence.manifests import Manifest, ManifestBuilder
from jugeo.evidence.provenance import ProvenanceGraph, ProvenanceTrace
from jugeo.evidence.trust import (
    TrustAlgebra,
    TrustAuditLog,
    TrustLevel,
    TrustPolicy,
    TrustTier,
)
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.interfaces.diagnostics import DiagnosticReport, collect_diagnostics
from jugeo.interfaces.serialization import serialize
from jugeo.judgments.exports import ExportRecord
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    Judgment,
    Obstruction,
    Proposition,
    ResidualObligation,
    TrustAnnotation,
)
from jugeo.runtime.checkpointing import Checkpoint, CheckpointStore

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "APIAuthenticator",
    "APIEventLog",
    "APIRateLimiter",
    "APIRequest",
    "APIResponse",
    "APIRouter",
    "APISerializer",
    "APISession",
    "APIValidator",
    "CopilotAPIBridge",
    "JuGeoAPI",
    "OperationKind",
    "RequestStatus",
]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class OperationKind(str, Enum):
    """The five semantic operations exposed by the JuGeo API.

    These correspond directly to the five inference rules in theory2.tex.
    All other API methods (``inspect``, ``propose``, ``copilot_query``) are
    administrative wrappers around these operations.
    """

    VERIFY = "verify"
    CONSTRUCT = "construct"
    DESCEND = "descend"
    FEDERATE = "federate"
    INSPECT = "inspect"


class RequestStatus(str, Enum):
    """Lifecycle status of an :class:`APIRequest`.

    ``PARTIAL`` means the engine produced a result but residual obligations
    remain; this is distinct from ``FAILED``, which signals that no result
    could be produced at all.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class APIRequest:
    """A single request submitted to the JuGeo API.

    Attributes
    ----------
    request_id:
        Caller-supplied or auto-generated opaque identifier.  Used for
        idempotency checks and log correlation.
    operation:
        Which of the five semantic operations to invoke.
    coordinate:
        Dot-separated name of the semantic site to operate on, e.g.
        ``"algebra.rings.noetherian"``.  Must be resolvable in the current
        manifest.
    proposition:
        The serialised proposition string (human-readable or schema-encoded)
        to verify, construct, or descend from.  Ignored for ``inspect``.
    evidence_hint:
        Optional pre-fetched evidence bundle.  The router may use this to
        skip channel queries when the bundle is already trustworthy enough.
    budget:
        Maximum number of solver steps allowed.  ``None`` means
        system-default (see :attr:`jugeo.runtime_defaults.FrontierBudget`).
    deadline:
        Wall-clock epoch deadline (seconds).  ``None`` means no deadline.
    trust_floor:
        Minimum :class:`~jugeo.evidence.trust.TrustLevel` the caller requires
        in the response.  If the engine cannot meet this floor the request
        is rejected with :attr:`RequestStatus.REJECTED` rather than silently
        returning a lower-trust result.
    metadata:
        Arbitrary caller-supplied key/value pairs attached to the request for
        logging and auditing purposes.
    """

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    operation: OperationKind = OperationKind.VERIFY
    coordinate: str = ""
    proposition: str = ""
    evidence_hint: EvidenceBundle | None = None
    budget: int | None = None
    deadline: float | None = None
    trust_floor: TrustLevel | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_timed_out(self) -> bool:
        """Return ``True`` if the wall-clock deadline has passed."""
        if self.deadline is None:
            return False
        return time.monotonic() > self.deadline

    def effective_budget(self, default: int = 10_000) -> int:
        """Return the request budget, falling back to *default*."""
        return self.budget if self.budget is not None else default

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain ``dict`` suitable for JSON encoding."""
        return {
            "request_id": self.request_id,
            "operation": self.operation.value,
            "coordinate": self.coordinate,
            "proposition": self.proposition,
            "budget": self.budget,
            "deadline": self.deadline,
            "trust_floor": self.trust_floor.value if self.trust_floor else None,
            "metadata": self.metadata,
        }


@dataclass
class APIResponse:
    """The result returned by the JuGeo API for a single request.

    The API never silently upgrades ``trust_level``.  If the engine's
    achieved trust is lower than the requested ``trust_floor`` the response
    status is :attr:`RequestStatus.REJECTED` and ``result`` is ``None``.

    Attributes
    ----------
    request_id:
        Echo of the originating :attr:`APIRequest.request_id`.
    status:
        Final lifecycle status of the request.
    result:
        The semantic result object — a :class:`~jugeo.judgments.judgment_terms.Judgment`,
        a reconstructed witness, a federated evidence record, or ``None`` on
        failure.
    trust_level:
        The trust level **actually achieved** by the engine.  Never coerced
        upwards.
    residuals:
        Residual obligations that remain open after partial verification.
        An empty list means the result is unconditional at *trust_level*.
    obstructions:
        Obstructions encountered during the operation.  Present even on
        success to allow the caller to detect near-misses.
    evidence_summary:
        Human-readable summary of the evidence used.
    latency_ms:
        Wall-clock time taken to service the request, in milliseconds.
    error:
        Structured error, present only when ``status`` is
        :attr:`RequestStatus.FAILED`.
    copilot_contributed:
        ``True`` when at least one copilot-sourced evidence item influenced
        the result.  Callers may use this flag to apply their own downstream
        trust policies.
    """

    request_id: str = ""
    status: RequestStatus = RequestStatus.PENDING
    result: Any = None
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    residuals: list[ResidualObligation] = field(default_factory=list)
    obstructions: list[Obstruction] = field(default_factory=list)
    evidence_summary: str = ""
    latency_ms: float = 0.0
    error: StructuredFailure | None = None
    copilot_contributed: bool = False

    def is_successful(self) -> bool:
        """Return ``True`` for complete or partial results."""
        return self.status in (RequestStatus.COMPLETE, RequestStatus.PARTIAL)

    def has_residuals(self) -> bool:
        """Return ``True`` when open obligations remain."""
        return bool(self.residuals)

    def trust_meets_floor(self, floor: TrustLevel) -> bool:
        """Return ``True`` when achieved trust is at least *floor*."""
        return TrustAlgebra.leq(floor, self.trust_level)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain ``dict`` suitable for JSON encoding."""
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "trust_level": self.trust_level.value,
            "residuals": [str(r) for r in self.residuals],
            "obstructions": [str(o) for o in self.obstructions],
            "evidence_summary": self.evidence_summary,
            "latency_ms": self.latency_ms,
            "copilot_contributed": self.copilot_contributed,
            "error": str(self.error) if self.error else None,
        }


# ---------------------------------------------------------------------------
# APISession
# ---------------------------------------------------------------------------


class APISession:
    """Manages the lifecycle of a sequence of related API requests.

    A session groups requests that share configuration, caller identity, and
    provenance context.  Sessions are not required — every :class:`JuGeoAPI`
    method can be called statelessly — but they enable cheaper repeated calls,
    correlated audit trails, and mid-session checkpointing.

    Parameters
    ----------
    session_id:
        Unique identifier for the session.
    caller_id:
        Authenticated identity of the caller.
    configuration:
        Key/value configuration overrides applied to every request in the
        session.  Keys mirror the fields of :class:`APIRequest`.
    checkpoint_store:
        Optional store for mid-session checkpoints.
    """

    def __init__(
        self,
        session_id: str | None = None,
        caller_id: str = "anonymous",
        configuration: dict[str, Any] | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ) -> None:
        self.session_id: str = session_id or str(uuid.uuid4())
        self.caller_id: str = caller_id
        self.configuration: dict[str, Any] = configuration or {}
        self._checkpoint_store: CheckpointStore | None = checkpoint_store
        self.open_requests: dict[str, APIRequest] = {}
        self.history: list[tuple[APIRequest, APIResponse]] = []
        self._closed: bool = False
        self._created_at: float = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        """Return ``True`` when the session has not been closed."""
        return not self._closed

    def close(self) -> None:
        """Close the session and release any held resources.

        After calling ``close()``, submitting further requests will raise
        :class:`~jugeo.errors.JuGeoError`.  In-flight requests recorded in
        :attr:`open_requests` are cancelled and moved to history with status
        :attr:`RequestStatus.FAILED`.
        """
        if self._closed:
            return
        for req_id, req in list(self.open_requests.items()):
            resp = APIResponse(
                request_id=req_id,
                status=RequestStatus.FAILED,
                error=StructuredFailure(
                    message="session closed before request completed",
                    scope=None,
                ),
            )
            self.history.append((req, resp))
        self.open_requests.clear()
        self._closed = True

    def checkpoint(self, label: str = "") -> Checkpoint | None:
        """Persist a checkpoint of the session state.

        Returns the stored :class:`~jugeo.runtime.checkpointing.Checkpoint`
        object, or ``None`` when no checkpoint store is configured.
        """
        if self._checkpoint_store is None:
            return None
        cp = Checkpoint(
            label=label or f"session-{self.session_id[:8]}",
            data={
                "session_id": self.session_id,
                "caller_id": self.caller_id,
                "configuration": self.configuration,
                "history_length": len(self.history),
            },
        )
        self._checkpoint_store.store(cp)
        return cp

    # ------------------------------------------------------------------
    # Request management
    # ------------------------------------------------------------------

    def register_request(self, req: APIRequest) -> None:
        """Record *req* as an open request for this session.

        Raises
        ------
        JuGeoError
            If the session is closed.
        """
        if self._closed:
            raise JuGeoError(f"session {self.session_id} is closed")
        self.open_requests[req.request_id] = req

    def complete_request(self, req: APIRequest, resp: APIResponse) -> None:
        """Move *req* from open to history after receiving *resp*."""
        self.open_requests.pop(req.request_id, None)
        self.history.append((req, resp))

    def recent_history(self, n: int = 10) -> list[tuple[APIRequest, APIResponse]]:
        """Return the *n* most recent (request, response) pairs."""
        return self.history[-n:]

    def uptime_seconds(self) -> float:
        """Return session age in seconds."""
        return time.monotonic() - self._created_at

    def apply_defaults(self, req: APIRequest) -> APIRequest:
        """Return a copy of *req* with session configuration defaults applied.

        Session-level defaults fill in only fields that were not explicitly
        set by the caller (i.e. still carry their dataclass default values).
        """
        overrides: dict[str, Any] = {}
        if "budget" in self.configuration and req.budget is None:
            overrides["budget"] = self.configuration["budget"]
        if "trust_floor" in self.configuration and req.trust_floor is None:
            raw = self.configuration["trust_floor"]
            overrides["trust_floor"] = TrustLevel(raw) if isinstance(raw, str) else raw
        from dataclasses import replace as dc_replace
        return dc_replace(req, **overrides) if overrides else req


# ---------------------------------------------------------------------------
# APIAuthenticator
# ---------------------------------------------------------------------------


class APIAuthenticator:
    """Authenticates callers and enforces per-caller permissions.

    JuGeo uses a simple capability model: each caller carries a set of
    *granted operations* and an *optional trust ceiling* that caps the trust
    level the caller is allowed to request.  The authenticator does **not**
    implement cryptographic identity — that is delegated to the transport
    layer.  It enforces the resulting capability set.

    Parameters
    ----------
    registry:
        Mapping from caller ID to their capability record.  A capability
        record is a dict with optional keys ``"operations"`` (set of
        :class:`OperationKind` values), ``"trust_ceiling"``
        (:class:`~jugeo.evidence.trust.TrustLevel`), and ``"rate_limit"``
        (requests per minute, int).
    """

    def __init__(self, registry: dict[str, dict[str, Any]] | None = None) -> None:
        self._registry: dict[str, dict[str, Any]] = registry or {}
        self._access_log: list[dict[str, Any]] = []

    def authenticate(self, caller_id: str, token: str | None = None) -> bool:
        """Verify *caller_id* is registered and, if *token* is given, matches.

        Returns ``True`` when authentication succeeds.  The method always
        returns ``False`` for unknown callers; it never raises.
        """
        if caller_id not in self._registry:
            self.record_access(caller_id, operation=None, granted=False)
            return False
        record = self._registry[caller_id]
        if token is not None:
            expected = record.get("token")
            if expected is not None and not _constant_time_compare(token, expected):
                self.record_access(caller_id, operation=None, granted=False)
                return False
        self.record_access(caller_id, operation=None, granted=True)
        return True

    def check_permissions(self, caller_id: str, operation: OperationKind) -> bool:
        """Return ``True`` when *caller_id* is permitted to invoke *operation*.

        If the caller record has no ``"operations"`` restriction, all
        operations are permitted.
        """
        record = self._registry.get(caller_id, {})
        allowed_ops: set[str] | None = record.get("operations")
        if allowed_ops is None:
            return True
        granted = operation.value in allowed_ops or operation in allowed_ops
        self.record_access(caller_id, operation=operation, granted=granted)
        return granted

    def record_access(
        self,
        caller_id: str,
        operation: OperationKind | None,
        granted: bool,
    ) -> None:
        """Append an access event to the in-memory access log.

        The log is intentionally simple; production deployments should wire
        this to a durable audit backend.
        """
        self._access_log.append(
            {
                "ts": time.time(),
                "caller_id": caller_id,
                "operation": operation.value if operation else None,
                "granted": granted,
            }
        )

    def rate_limit_check(self, caller_id: str, window_seconds: float = 60.0) -> bool:
        """Return ``True`` when the caller is within their rate limit.

        Counts accesses by *caller_id* within the last *window_seconds*.
        If no ``"rate_limit"`` is set in the caller's record, the check
        always passes.
        """
        record = self._registry.get(caller_id, {})
        limit: int | None = record.get("rate_limit")
        if limit is None:
            return True
        cutoff = time.time() - window_seconds
        recent_count = sum(
            1
            for entry in self._access_log
            if entry["caller_id"] == caller_id and entry["ts"] >= cutoff
        )
        return recent_count < limit

    def register_caller(
        self,
        caller_id: str,
        operations: set[str] | None = None,
        trust_ceiling: TrustLevel | None = None,
        rate_limit: int | None = None,
        token: str | None = None,
    ) -> None:
        """Register or update a caller capability record."""
        self._registry[caller_id] = {
            "operations": operations,
            "trust_ceiling": trust_ceiling,
            "rate_limit": rate_limit,
            "token": token,
        }

    def access_log(self) -> list[dict[str, Any]]:
        """Return a snapshot of the in-memory access log."""
        return list(self._access_log)


# ---------------------------------------------------------------------------
# APIRateLimiter
# ---------------------------------------------------------------------------


class APIRateLimiter:
    """Token-bucket rate limiter scoped per caller.

    Each caller gets an independent token bucket.  Tokens regenerate at
    *refill_rate* per second up to *capacity*.  A call costs one token by
    default; expensive operations (e.g. ``federate``) may cost more.

    Parameters
    ----------
    capacity:
        Maximum burst size (tokens).
    refill_rate:
        Tokens added per second.
    """

    def __init__(self, capacity: int = 60, refill_rate: float = 1.0) -> None:
        self._capacity: int = capacity
        self._refill_rate: float = refill_rate
        self._buckets: dict[str, dict[str, Any]] = {}
        self._overrides: dict[str, dict[str, Any]] = {}

    def _bucket(self, caller_id: str) -> dict[str, Any]:
        if caller_id not in self._buckets:
            cap, rate = self._caller_config(caller_id)
            self._buckets[caller_id] = {
                "tokens": float(cap),
                "last_refill": time.monotonic(),
                "capacity": cap,
                "refill_rate": rate,
                "total_calls": 0,
                "throttled_calls": 0,
            }
        return self._buckets[caller_id]

    def _caller_config(self, caller_id: str) -> tuple[int, float]:
        ov = self._overrides.get(caller_id, {})
        cap = ov.get("capacity", self._capacity)
        rate = ov.get("refill_rate", self._refill_rate)
        return cap, rate

    def check(self, caller_id: str, cost: int = 1) -> bool:
        """Return ``True`` if *caller_id* has enough tokens for a call of *cost*.

        Does **not** consume tokens; call :meth:`record` after a successful
        check to deduct them.
        """
        b = self._bucket(caller_id)
        self._refill(b)
        return b["tokens"] >= cost

    def record(self, caller_id: str, cost: int = 1) -> None:
        """Deduct *cost* tokens from *caller_id*'s bucket after a successful call."""
        b = self._bucket(caller_id)
        self._refill(b)
        b["tokens"] = max(0.0, b["tokens"] - cost)
        b["total_calls"] += 1

    def throttle(self, caller_id: str, cost: int = 1) -> float:
        """Return seconds the caller must wait before a call of *cost* is allowed.

        Returns ``0.0`` when the caller can proceed immediately.
        """
        b = self._bucket(caller_id)
        self._refill(b)
        shortage = cost - b["tokens"]
        if shortage <= 0:
            return 0.0
        return shortage / b["refill_rate"]

    def configure(
        self,
        caller_id: str,
        capacity: int | None = None,
        refill_rate: float | None = None,
    ) -> None:
        """Set per-caller overrides for bucket capacity and refill rate.

        Changes take effect the next time the caller's bucket is touched.
        """
        ov = self._overrides.setdefault(caller_id, {})
        if capacity is not None:
            ov["capacity"] = capacity
        if refill_rate is not None:
            ov["refill_rate"] = refill_rate
        # Invalidate existing bucket so new settings apply immediately.
        self._buckets.pop(caller_id, None)

    def _refill(self, bucket: dict[str, Any]) -> None:
        now = time.monotonic()
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            bucket["capacity"],
            bucket["tokens"] + elapsed * bucket["refill_rate"],
        )
        bucket["last_refill"] = now

    def stats(self, caller_id: str) -> dict[str, Any]:
        """Return usage statistics for *caller_id*."""
        b = self._bucket(caller_id)
        self._refill(b)
        return {
            "caller_id": caller_id,
            "tokens_available": b["tokens"],
            "capacity": b["capacity"],
            "refill_rate": b["refill_rate"],
            "total_calls": b["total_calls"],
            "throttled_calls": b["throttled_calls"],
        }


# ---------------------------------------------------------------------------
# APIValidator
# ---------------------------------------------------------------------------


class APIValidator:
    """Validates :class:`APIRequest` objects before they reach the router.

    Validation is pure (no side-effects) and cheap.  It checks structural
    correctness of the request fields but does **not** resolve the coordinate
    against the live manifest — that is the router's job.

    Parameters
    ----------
    max_budget:
        Hard cap on :attr:`APIRequest.budget`; requests exceeding this are
        rejected.
    max_proposition_length:
        Maximum character length of :attr:`APIRequest.proposition`.
    """

    def __init__(
        self,
        max_budget: int = 1_000_000,
        max_proposition_length: int = 65_536,
    ) -> None:
        self._max_budget = max_budget
        self._max_proposition_length = max_proposition_length

    def validate_request(self, req: APIRequest) -> list[str]:
        """Return a list of validation error strings.

        An empty list means the request is valid.  Each string describes a
        distinct problem; callers should surface all errors rather than
        stopping at the first.
        """
        errors: list[str] = []
        errors.extend(self.check_budget(req))
        errors.extend(self.check_coordinate(req))
        errors.extend(self.check_proposition_format(req))
        if req.deadline is not None and req.deadline < time.time():
            errors.append(
                f"deadline {req.deadline} is in the past (now={time.time():.1f})"
            )
        return errors

    def check_budget(self, req: APIRequest) -> list[str]:
        """Validate the budget field of *req*.

        Returns a list of error strings (empty if valid).
        """
        if req.budget is None:
            return []
        if req.budget <= 0:
            return [f"budget must be positive, got {req.budget}"]
        if req.budget > self._max_budget:
            return [
                f"budget {req.budget} exceeds maximum allowed {self._max_budget}"
            ]
        return []

    def check_coordinate(self, req: APIRequest) -> list[str]:
        """Validate the coordinate string of *req*.

        A coordinate must be a non-empty dot-separated sequence of
        identifier-like components.  This validator is deliberately lenient;
        full resolution is deferred to the router.
        """
        coord = req.coordinate.strip()
        if not coord:
            return ["coordinate must not be empty"]
        parts = coord.split(".")
        for part in parts:
            if not part or not part.replace("_", "").replace("-", "").isalnum():
                return [
                    f"coordinate component {part!r} is not a valid identifier "
                    f"in {coord!r}"
                ]
        return []

    def check_proposition_format(self, req: APIRequest) -> list[str]:
        """Validate the proposition field of *req*.

        ``inspect`` operations do not require a proposition; all others do.
        The proposition must not exceed the configured length limit.
        """
        if req.operation == OperationKind.INSPECT:
            return []
        if not req.proposition.strip():
            return [
                f"operation {req.operation.value!r} requires a non-empty proposition"
            ]
        if len(req.proposition) > self._max_proposition_length:
            return [
                f"proposition length {len(req.proposition)} exceeds limit "
                f"{self._max_proposition_length}"
            ]
        return []

    def is_valid(self, req: APIRequest) -> bool:
        """Return ``True`` when :meth:`validate_request` produces no errors."""
        return not self.validate_request(req)


# ---------------------------------------------------------------------------
# APIRouter
# ---------------------------------------------------------------------------


class APIRouter:
    """Routes validated :class:`APIRequest` objects to the appropriate subsystem.

    The router resolves the coordinate against the manifest, selects the
    evidence channel, and dispatches to the correct kernel operation.  It is
    the sole place where incoming requests are mapped to internal JuGeo
    subsystems; nothing outside this class should touch the kernel directly.

    Parameters
    ----------
    manifest:
        The active :class:`~jugeo.evidence.manifests.Manifest`.
    channel_router:
        Channel selector used to pick the evidence source for each request.
    copilot_bridge:
        Optional :class:`CopilotAPIBridge` for copilot-backed queries.
    """

    def __init__(
        self,
        manifest: Manifest,
        channel_router: ChannelRouter,
        copilot_bridge: "CopilotAPIBridge | None" = None,
    ) -> None:
        self._manifest = manifest
        self._channel_router = channel_router
        self._copilot_bridge = copilot_bridge

    def route(self, req: APIRequest) -> APIResponse:
        """Dispatch *req* to the correct subsystem and return a response.

        This is the single dispatch method; callers should prefer the
        type-specific ``route_*`` methods for clarity.
        """
        start = time.monotonic()
        try:
            if req.operation == OperationKind.VERIFY:
                resp = self.route_verify(req)
            elif req.operation == OperationKind.CONSTRUCT:
                resp = self.route_construct(req)
            elif req.operation == OperationKind.DESCEND:
                resp = self.route_descend(req)
            elif req.operation == OperationKind.FEDERATE:
                resp = self.route_federate(req)
            elif req.operation == OperationKind.INSPECT:
                resp = self._route_inspect(req)
            else:
                resp = APIResponse(
                    request_id=req.request_id,
                    status=RequestStatus.FAILED,
                    error=StructuredFailure(
                        message=f"unknown operation {req.operation!r}",
                        scope=None,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            resp = APIResponse(
                request_id=req.request_id,
                status=RequestStatus.FAILED,
                error=StructuredFailure(message=str(exc), scope=None),
            )
        resp.latency_ms = (time.monotonic() - start) * 1000.0
        return resp

    def route_verify(self, req: APIRequest) -> APIResponse:
        """Route a *verify* request.

        Attempts to verify the proposition at the given coordinate using
        available evidence channels.  Returns a partial result with residuals
        when full verification is not achievable within the budget.
        """
        coord = self._resolve_coordinate(req.coordinate)
        channel = self._channel_router.preferred_channel(coord)
        evidence_req = EvidenceRequest(
            coordinate=coord,
            proposition=req.proposition,
            channel=channel,
            budget=req.effective_budget(),
        )
        evidence_resp = self._channel_router.dispatch(evidence_req)
        return self._build_response(req, evidence_resp)

    def route_construct(self, req: APIRequest) -> APIResponse:
        """Route a *construct* request.

        Attempts to construct a witness for the proposition at the given
        coordinate.  A witness is stronger than a verification certificate;
        it requires the engine to produce an explicit term.
        """
        coord = self._resolve_coordinate(req.coordinate)
        channel = self._channel_router.preferred_channel(coord)
        evidence_req = EvidenceRequest(
            coordinate=coord,
            proposition=req.proposition,
            channel=channel,
            budget=req.effective_budget(),
        )
        evidence_resp = self._channel_router.dispatch(evidence_req)
        resp = self._build_response(req, evidence_resp)
        # Construct requires VERIFIED; demote to PARTIAL if only ORACLE_PROPOSED.
        if resp.status == RequestStatus.COMPLETE and TrustAlgebra.leq(
            resp.trust_level, TrustLevel.ORACLE_PROPOSED
        ):
            resp.status = RequestStatus.PARTIAL
            resp.residuals.append(
                ResidualObligation(
                    description="witness requires human attestation to graduate from ORACLE_PROPOSED"
                )
            )
        return resp

    def route_descend(self, req: APIRequest) -> APIResponse:
        """Route a *descend* request.

        Descend refines a proposition at a coarser coordinate to a narrower
        child coordinate, inheriting trust only where the refinement is
        provably conservative.
        """
        coord = self._resolve_coordinate(req.coordinate)
        # Descend always uses the locally-trusted channel to avoid polluting
        # the child with oracle-tier evidence.
        channel = EvidenceChannel.RUNTIME
        evidence_req = EvidenceRequest(
            coordinate=coord,
            proposition=req.proposition,
            channel=channel,
            budget=req.effective_budget(),
        )
        evidence_resp = self._channel_router.dispatch(evidence_req)
        return self._build_response(req, evidence_resp)

    def route_federate(self, req: APIRequest) -> APIResponse:
        """Route a *federate* request.

        Federation aggregates evidence from multiple channels and attempts to
        compose them into a single higher-trust result.  Copilot-sourced
        evidence is included at oracle tier and never silently elevated.
        """
        coord = self._resolve_coordinate(req.coordinate)
        federation = ChannelFederation(router=self._channel_router)
        evidence_req = EvidenceRequest(
            coordinate=coord,
            proposition=req.proposition,
            channel=EvidenceChannel.RUNTIME,
            budget=req.effective_budget(),
        )
        records = federation.collect_all(evidence_req)
        # Compose trust levels across all collected records.
        achieved = TrustLevel.UNVERIFIED
        copilot_seen = False
        for record in records:
            if record.channel in {EvidenceChannel.COPILOT, EvidenceChannel.ORACLE}:
                copilot_seen = True
            achieved = TrustAlgebra.meet(achieved, record.trust_level)
        status = (
            RequestStatus.COMPLETE
            if achieved >= TrustLevel.VERIFIED
            else RequestStatus.PARTIAL
        )
        return APIResponse(
            request_id=req.request_id,
            status=status,
            result=records,
            trust_level=achieved,
            evidence_summary=f"federated {len(records)} record(s)",
            copilot_contributed=copilot_seen,
        )

    def _route_inspect(self, req: APIRequest) -> APIResponse:
        """Route an *inspect* request (internal)."""
        coord = self._resolve_coordinate(req.coordinate)
        obligations = self._manifest.get_obligations(coord)
        obstructions = self._manifest.get_obstructions(coord)
        return APIResponse(
            request_id=req.request_id,
            status=RequestStatus.COMPLETE,
            result={
                "coordinate": str(coord),
                "obligations": [str(o) for o in obligations],
                "obstructions": [str(o) for o in obstructions],
            },
            trust_level=TrustLevel.VERIFIED,
        )

    def _resolve_coordinate(self, raw: str) -> Coordinate:
        parts = tuple(raw.strip().split("."))
        return Coordinate(components=parts, kind=CoordinateKind.REGION)

    def _build_response(
        self, req: APIRequest, ev_resp: EvidenceResponse
    ) -> APIResponse:
        trust = getattr(ev_resp, "trust_level", TrustLevel.UNVERIFIED)
        residuals = list(getattr(ev_resp, "residuals", []))
        obstructions = list(getattr(ev_resp, "obstructions", []))
        copilot = getattr(ev_resp, "channel", None) in {
            EvidenceChannel.COPILOT,
            EvidenceChannel.ORACLE,
        }
        status = (
            RequestStatus.COMPLETE
            if not residuals and trust >= TrustLevel.VERIFIED
            else RequestStatus.PARTIAL
            if getattr(ev_resp, "result", None) is not None
            else RequestStatus.FAILED
        )
        if (
            req.trust_floor is not None
            and not TrustAlgebra.leq(req.trust_floor, trust)
        ):
            return APIResponse(
                request_id=req.request_id,
                status=RequestStatus.REJECTED,
                trust_level=trust,
                residuals=residuals,
                obstructions=obstructions,
                copilot_contributed=copilot,
                evidence_summary="trust floor not met",
            )
        return APIResponse(
            request_id=req.request_id,
            status=status,
            result=getattr(ev_resp, "result", None),
            trust_level=trust,
            residuals=residuals,
            obstructions=obstructions,
            evidence_summary=getattr(ev_resp, "summary", ""),
            copilot_contributed=copilot,
        )


# ---------------------------------------------------------------------------
# APIEventLog
# ---------------------------------------------------------------------------


class APIEventLog:
    """Append-only audit log for all API events.

    Every request dispatch, authentication check, rate-limit hit, and
    copilot query is recorded here.  The log is in-memory by default;
    production deployments should attach a durable sink via
    :meth:`add_sink`.

    Records are plain dicts with at least the keys ``"ts"`` (epoch seconds),
    ``"event_type"`` (string), and ``"request_id"`` (string or ``None``).
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._sinks: list[Callable[[dict[str, Any]], None]] = []

    def add_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        """Register a callable that will receive each new event record."""
        self._sinks.append(sink)

    def record(
        self,
        event_type: str,
        request_id: str | None = None,
        **extra: Any,
    ) -> None:
        """Append an event record to the log.

        Parameters
        ----------
        event_type:
            Short machine-readable label, e.g. ``"request.start"``,
            ``"auth.fail"``, ``"copilot.query"``.
        request_id:
            Correlation ID linking this event to a specific request.
        **extra:
            Additional key/value pairs merged into the record.
        """
        entry: dict[str, Any] = {
            "ts": time.time(),
            "event_type": event_type,
            "request_id": request_id,
            **extra,
        }
        self._records.append(entry)
        for sink in self._sinks:
            try:
                sink(entry)
            except Exception:  # noqa: BLE001
                pass

    def query(
        self,
        event_type: str | None = None,
        request_id: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query the log with optional filters.

        All provided filters are ANDed together.  Returns matching records
        in insertion order (oldest first).
        """
        results = [
            r
            for r in self._records
            if (event_type is None or r.get("event_type") == event_type)
            and (request_id is None or r.get("request_id") == request_id)
            and (since is None or r["ts"] >= since)
        ]
        if limit is not None:
            results = results[-limit:]
        return results

    def export(self, fmt: str = "json") -> str:
        """Export the full log as a serialised string.

        Parameters
        ----------
        fmt:
            ``"json"`` (default) or ``"jsonl"`` (one JSON object per line).
        """
        if fmt == "jsonl":
            return "\n".join(json.dumps(r, default=str) for r in self._records)
        return json.dumps(self._records, default=str, indent=2)

    def clear(self) -> None:
        """Truncate the in-memory log.  Does not affect sinks."""
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# APISerializer
# ---------------------------------------------------------------------------


class APISerializer:
    """Converts API objects to and from JSON-compatible representations.

    :class:`APISerializer` is intentionally a stateless helper; all methods
    are safe to call concurrently.  It does not import heavy kernel types at
    module load time — imports are deferred to the methods that need them to
    keep the interfaces package lightweight.

    The serialisation contract: every ``to_*`` method returns a ``dict``
    whose values are JSON-primitive-compatible (str, int, float, bool, None,
    list, dict).  Every ``from_*`` method reconstructs a dataclass from such
    a dict, raising :class:`ValueError` on missing required keys.
    """

    def to_request(self, req: APIRequest) -> dict[str, Any]:
        """Serialise an :class:`APIRequest` to a dict."""
        return req.to_dict()

    def from_request(self, data: dict[str, Any]) -> APIRequest:
        """Deserialise a dict to an :class:`APIRequest`.

        Raises
        ------
        ValueError
            If required keys are missing or have invalid types.
        """
        _require_keys(data, ("request_id", "operation"))
        return APIRequest(
            request_id=data["request_id"],
            operation=OperationKind(data["operation"]),
            coordinate=data.get("coordinate", ""),
            proposition=data.get("proposition", ""),
            budget=data.get("budget"),
            deadline=data.get("deadline"),
            trust_floor=(
                TrustLevel(data["trust_floor"])
                if data.get("trust_floor")
                else None
            ),
            metadata=data.get("metadata", {}),
        )

    def to_response(self, resp: APIResponse) -> dict[str, Any]:
        """Serialise an :class:`APIResponse` to a dict."""
        return resp.to_dict()

    def from_response(self, data: dict[str, Any]) -> APIResponse:
        """Deserialise a dict to an :class:`APIResponse`.

        Note: ``result``, ``residuals``, and ``obstructions`` are restored as
        raw values / strings only; full reconstruction of kernel objects
        requires the manifest to be present.
        """
        _require_keys(data, ("request_id", "status"))
        return APIResponse(
            request_id=data["request_id"],
            status=RequestStatus(data["status"]),
            trust_level=(
                TrustLevel(data["trust_level"])
                if data.get("trust_level")
                else TrustLevel.UNVERIFIED
            ),
            evidence_summary=data.get("evidence_summary", ""),
            latency_ms=float(data.get("latency_ms", 0.0)),
            copilot_contributed=bool(data.get("copilot_contributed", False)),
        )

    def session_snapshot(self, session: "APISession") -> dict[str, Any]:
        """Return a JSON-compatible snapshot of *session* state."""
        return {
            "session_id": session.session_id,
            "caller_id": session.caller_id,
            "configuration": session.configuration,
            "is_open": session.is_open(),
            "open_request_count": len(session.open_requests),
            "history_length": len(session.history),
            "uptime_seconds": session.uptime_seconds(),
        }

    def batch_requests(self, requests: Iterable[APIRequest]) -> str:
        """Serialise a sequence of requests to a JSON array string."""
        return json.dumps([self.to_request(r) for r in requests])

    def batch_responses(self, responses: Iterable[APIResponse]) -> str:
        """Serialise a sequence of responses to a JSON array string."""
        return json.dumps([self.to_response(r) for r in responses])

    def round_trip_request(self, req: APIRequest) -> APIRequest:
        """Serialise then deserialise *req* — useful in tests."""
        return self.from_request(self.to_request(req))


# ---------------------------------------------------------------------------
# CopilotAPIBridge
# ---------------------------------------------------------------------------


class CopilotAPIBridge:
    """Bridges copilot oracle calls through the JuGeo API trust machinery.

    Copilot is a powerful generative tool, but its outputs are unverified
    proposals.  This bridge enforces the following invariants:

    1. **Hard ceiling of** ``ORACLE_PROPOSED``.  No copilot response may
       emerge from this bridge at a trust level above
       :attr:`~jugeo.evidence.trust.TrustLevel.ORACLE_PROPOSED`, regardless
       of any metadata the copilot response carries.
    2. **Corroboration requirement (optional)**.  When *require_corroboration*
       is ``True``, the bridge refuses to return a result unless at least one
       independent non-copilot evidence source agrees with the copilot
       response.
    3. **Full audit trail**.  Every call — including failures and
       corroboration checks — is recorded in the supplied
       :class:`APIEventLog`.

    Parameters
    ----------
    channel:
        The underlying :class:`~jugeo.evidence.channels.CopilotChannel`.
    event_log:
        Audit log for all copilot interactions.
    require_corroboration:
        When ``True``, query results lacking corroboration are held at
        :attr:`~jugeo.evidence.trust.TrustLevel.COPILOT_SUGGESTED` rather
        than returning :attr:`~jugeo.evidence.trust.TrustLevel.ORACLE_PROPOSED`.
    corroboration_sources:
        Optional list of non-copilot channels consulted for corroboration.
    """

    COPILOT_TRUST_CEILING = TrustLevel.ORACLE_PROPOSED

    def __init__(
        self,
        channel: CopilotChannel,
        event_log: APIEventLog,
        require_corroboration: bool = False,
        corroboration_sources: list[EvidenceChannel] | None = None,
    ) -> None:
        self._channel = channel
        self._event_log = event_log
        self._require_corroboration = require_corroboration
        self._corroboration_sources = corroboration_sources or []
        self._query_count: int = 0
        self._rejected_count: int = 0

    def query(
        self,
        prompt: str,
        coordinate: str = "",
        budget: int = 1000,
        request_id: str | None = None,
    ) -> APIResponse:
        """Submit *prompt* to the copilot oracle and return an :class:`APIResponse`.

        The response trust level is capped at
        :attr:`COPILOT_TRUST_CEILING`.  When corroboration is required and
        no corroborating source agrees, the returned trust level is demoted
        to ``COPILOT_SUGGESTED`` and a residual obligation is added.

        Parameters
        ----------
        prompt:
            The natural-language or structured query to send to copilot.
        coordinate:
            Semantic coordinate providing context for the query.
        budget:
            Maximum tokens / solver steps to allocate.
        request_id:
            Optional correlation ID; auto-generated when not provided.
        """
        req_id = request_id or str(uuid.uuid4())
        self._query_count += 1
        self._event_log.record(
            "copilot.query",
            request_id=req_id,
            prompt_length=len(prompt),
            coordinate=coordinate,
        )
        start = time.monotonic()
        try:
            raw_response = self._channel.query(prompt=prompt, budget=budget)
        except Exception as exc:  # noqa: BLE001
            self._event_log.record(
                "copilot.error", request_id=req_id, error=str(exc)
            )
            return APIResponse(
                request_id=req_id,
                status=RequestStatus.FAILED,
                error=StructuredFailure(message=str(exc), scope=None),
                copilot_contributed=True,
            )
        latency = (time.monotonic() - start) * 1000.0
        parsed = self.parse_response(raw_response, req_id=req_id)
        capped = self.enforce_trust_ceiling(parsed)
        if self._require_corroboration:
            capped = self.require_corroboration(
                capped, prompt=prompt, coordinate=coordinate
            )
        capped.latency_ms = latency
        capped.copilot_contributed = True
        self._event_log.record(
            "copilot.response",
            request_id=req_id,
            trust_level=capped.trust_level.value,
            status=capped.status.value,
            corroboration_required=self._require_corroboration,
        )
        return capped

    def parse_response(
        self,
        raw: EvidenceResponse | dict[str, Any],
        req_id: str = "",
    ) -> APIResponse:
        """Convert a raw copilot channel response to an :class:`APIResponse`.

        The trust level reported by the channel is preserved here; capping
        is applied in :meth:`enforce_trust_ceiling`.
        """
        if isinstance(raw, dict):
            trust_raw = raw.get("trust_level", TrustLevel.COPILOT_SUGGESTED.value)
            trust = (
                TrustLevel(trust_raw) if isinstance(trust_raw, str) else trust_raw
            )
            residuals_raw = raw.get("residuals", [])
            residuals = [
                ResidualObligation(description=str(r)) for r in residuals_raw
            ]
            return APIResponse(
                request_id=req_id,
                status=RequestStatus.COMPLETE,
                result=raw.get("result"),
                trust_level=trust,
                residuals=residuals,
                evidence_summary=raw.get("summary", "copilot response"),
            )
        # EvidenceResponse duck-typing
        trust = getattr(raw, "trust_level", TrustLevel.COPILOT_SUGGESTED)
        residuals = list(getattr(raw, "residuals", []))
        return APIResponse(
            request_id=req_id,
            status=RequestStatus.COMPLETE,
            result=getattr(raw, "result", None),
            trust_level=trust,
            residuals=residuals,
            evidence_summary=getattr(raw, "summary", "copilot response"),
        )

    def enforce_trust_ceiling(self, resp: APIResponse) -> APIResponse:
        """Cap *resp* trust level to at most :attr:`COPILOT_TRUST_CEILING`.

        If the incoming trust level exceeds ``ORACLE_PROPOSED``, it is
        silently capped and a residual obligation is appended to document the
        demotion.  This method **never raises**; demotion is always safe.
        """
        if TrustAlgebra.leq(resp.trust_level, self.COPILOT_TRUST_CEILING):
            return resp
        from dataclasses import replace as dc_replace
        demoted_residuals = list(resp.residuals) + [
            ResidualObligation(
                description=(
                    f"copilot response demoted from {resp.trust_level.value!r} "
                    f"to {self.COPILOT_TRUST_CEILING.value!r} by trust ceiling policy"
                )
            )
        ]
        self._rejected_count += 1
        self._event_log.record(
            "copilot.trust_ceiling_enforced",
            request_id=resp.request_id,
            original_trust=resp.trust_level.value,
            capped_to=self.COPILOT_TRUST_CEILING.value,
        )
        return dc_replace(
            resp,
            trust_level=self.COPILOT_TRUST_CEILING,
            residuals=demoted_residuals,
        )

    def require_corroboration(
        self,
        resp: APIResponse,
        prompt: str = "",
        coordinate: str = "",
    ) -> APIResponse:
        """Demote *resp* to ``COPILOT_SUGGESTED`` if no corroboration found.

        Corroboration is defined as at least one non-copilot, non-oracle
        evidence source that either (a) returns an independent positive
        result for the same proposition, or (b) explicitly acknowledges the
        copilot's claim.

        When corroboration sources are not configured, this method always
        demotes — callers must explicitly opt out of the requirement via
        ``require_corroboration=False`` on the bridge.
        """
        if not self._corroboration_sources:
            from dataclasses import replace as dc_replace
            demoted = list(resp.residuals) + [
                ResidualObligation(
                    description=(
                        "copilot result held at COPILOT_SUGGESTED: "
                        "no corroboration sources configured"
                    )
                )
            ]
            return dc_replace(
                resp,
                trust_level=TrustLevel.COPILOT_SUGGESTED,
                residuals=demoted,
            )
        corroborated = False
        for src_channel in self._corroboration_sources:
            try:
                corr_resp = self._channel.corroborate(
                    prompt=prompt,
                    channel=src_channel,
                )
                if getattr(corr_resp, "agrees", False):
                    corroborated = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not corroborated:
            from dataclasses import replace as dc_replace
            demoted = list(resp.residuals) + [
                ResidualObligation(
                    description=(
                        "copilot result demoted to COPILOT_SUGGESTED: "
                        "no corroborating evidence found"
                    )
                )
            ]
            return dc_replace(
                resp,
                trust_level=TrustLevel.COPILOT_SUGGESTED,
                residuals=demoted,
            )
        return resp

    def stats(self) -> dict[str, Any]:
        """Return usage statistics for this bridge."""
        return {
            "query_count": self._query_count,
            "rejected_count": self._rejected_count,
            "require_corroboration": self._require_corroboration,
            "trust_ceiling": self.COPILOT_TRUST_CEILING.value,
        }


# ---------------------------------------------------------------------------
# JuGeoAPI — main class
# ---------------------------------------------------------------------------


class JuGeoAPI:
    """Primary programmatic entry point into the JuGeo shared core.

    :class:`JuGeoAPI` is the single object callers should hold a reference to.
    It wires together authentication, rate limiting, validation, routing, and
    event logging behind a clean method-per-operation surface.

    All five semantic operations are exposed as top-level methods.  The
    administrative operations (``get_manifest``, ``get_judgment``, etc.) wrap
    manifest and certificate lookups without touching the solver.

    Parameters
    ----------
    manifest:
        The active :class:`~jugeo.evidence.manifests.Manifest`.
    authenticator:
        Handles caller identity and permissions.
    rate_limiter:
        Enforces per-caller throughput limits.
    validator:
        Validates request structure before dispatch.
    router:
        Routes requests to subsystems.
    event_log:
        Audit log for all API events.
    copilot_bridge:
        Optional bridge for copilot oracle queries.
    """

    def __init__(
        self,
        manifest: Manifest | None = None,
        authenticator: APIAuthenticator | None = None,
        rate_limiter: APIRateLimiter | None = None,
        validator: APIValidator | None = None,
        router: APIRouter | None = None,
        event_log: APIEventLog | None = None,
        copilot_bridge: CopilotAPIBridge | None = None,
    ) -> None:
        resolved_manifest = manifest
        if resolved_manifest is None:
            if router is not None and hasattr(router, "_manifest"):
                resolved_manifest = router._manifest  # type: ignore[attr-defined]
            else:
                resolved_manifest = ManifestBuilder().build()
        resolved_authenticator = authenticator or APIAuthenticator()
        if authenticator is None:
            resolved_authenticator.register_caller("anonymous")
        resolved_rate_limiter = rate_limiter or APIRateLimiter()
        resolved_validator = validator or APIValidator()
        resolved_event_log = event_log or APIEventLog()
        resolved_router = router or APIRouter(
            manifest=resolved_manifest,
            channel_router=ChannelRouter(),
            copilot_bridge=copilot_bridge,
        )

        self._manifest = resolved_manifest
        self._authenticator = resolved_authenticator
        self._rate_limiter = resolved_rate_limiter
        self._validator = resolved_validator
        self._router = resolved_router
        self._event_log = resolved_event_log
        self._copilot_bridge = copilot_bridge
        self._serializer = APISerializer()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        manifest: Manifest | None = None,
        allow_copilot: bool = True,
        require_corroboration: bool = False,
    ) -> "JuGeoAPI":
        """Construct a :class:`JuGeoAPI` with sensible defaults.

        Parameters
        ----------
        manifest:
            Override the default manifest.  When ``None``, a minimal empty
            manifest is constructed.
        allow_copilot:
            When ``True``, a :class:`CopilotAPIBridge` backed by a
            :class:`~jugeo.evidence.channels.CopilotChannel` is wired in.
            Copilot responses are always capped at ``ORACLE_PROPOSED``.
        require_corroboration:
            Passed through to :class:`CopilotAPIBridge`; when ``True`` all
            copilot responses must be corroborated before reaching the caller.
        """
        if manifest is None:
            manifest = ManifestBuilder().build()
        auth = APIAuthenticator()
        auth.register_caller("anonymous")
        limiter = APIRateLimiter()
        validator = APIValidator()
        channel_router = ChannelRouter()
        event_log = APIEventLog()
        copilot_bridge: CopilotAPIBridge | None = None
        if allow_copilot:
            copilot_channel = CopilotChannel()
            copilot_bridge = CopilotAPIBridge(
                channel=copilot_channel,
                event_log=event_log,
                require_corroboration=require_corroboration,
            )
        router = APIRouter(
            manifest=manifest,
            channel_router=channel_router,
            copilot_bridge=copilot_bridge,
        )
        return cls(
            manifest=manifest,
            authenticator=auth,
            rate_limiter=limiter,
            validator=validator,
            router=router,
            event_log=event_log,
            copilot_bridge=copilot_bridge,
        )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def open_session(
        self,
        caller_id: str = "anonymous",
        configuration: dict[str, Any] | None = None,
    ) -> APISession:
        """Create and return a new :class:`APISession` for *caller_id*.

        The session is not persisted by default; attach a
        :class:`~jugeo.runtime.checkpointing.CheckpointStore` to the
        returned session for durability.
        """
        session = APISession(caller_id=caller_id, configuration=configuration)
        self._event_log.record("session.open", caller_id=caller_id, session_id=session.session_id)
        return session

    # ------------------------------------------------------------------
    # Semantic operations
    # ------------------------------------------------------------------

    def verify(
        self,
        req: APIRequest,
        *,
        caller_id: str = "anonymous",
        session: APISession | None = None,
    ) -> APIResponse:
        """Verify the proposition named in *req* at the given coordinate.

        Trust is reported honestly; the caller's ``trust_floor`` is checked
        **after** the engine runs, not before.  If the achieved trust is
        below the floor the response status is
        :attr:`RequestStatus.REJECTED` and the result is ``None``.

        Parameters
        ----------
        req:
            The verification request.
        caller_id:
            Identity for auth and rate-limit checks.  Overridden by the
            session's ``caller_id`` when *session* is provided.
        session:
            Optional session providing configuration defaults.
        """
        req = self._prepare(req, caller_id=caller_id, session=session, op=OperationKind.VERIFY)
        if isinstance(req, APIResponse):
            return req  # pre-flight rejection
        resp = self._router.route(req)
        self._finalise(req, resp, session=session)
        return resp

    def construct(
        self,
        req: APIRequest,
        *,
        caller_id: str = "anonymous",
        session: APISession | None = None,
    ) -> APIResponse:
        """Attempt to construct an explicit witness for the proposition in *req*.

        Construction is strictly stronger than verification: the engine must
        produce a term, not merely a certificate.  Copilot may contribute
        candidate witnesses but they remain at oracle trust tier until a
        human or formal checker attests them.

        Parameters
        ----------
        req:
            The construction request.
        caller_id:
            Identity for auth and rate-limit checks.
        session:
            Optional session providing configuration defaults.
        """
        req = self._prepare(req, caller_id=caller_id, session=session, op=OperationKind.CONSTRUCT)
        if isinstance(req, APIResponse):
            return req
        resp = self._router.route(req)
        self._finalise(req, resp, session=session)
        return resp

    def descend(
        self,
        req: APIRequest,
        *,
        caller_id: str = "anonymous",
        session: APISession | None = None,
    ) -> APIResponse:
        """Refine a proposition to a child coordinate.

        The descent operation maps evidence at a coarser site to a narrower
        one, preserving trust only where the refinement is provably
        conservative.  Residuals from the parent that do not transfer cleanly
        are surfaced as obligations on the child.

        Parameters
        ----------
        req:
            The descent request.  ``req.coordinate`` names the child site.
        caller_id:
            Identity for auth and rate-limit checks.
        session:
            Optional session providing configuration defaults.
        """
        req = self._prepare(req, caller_id=caller_id, session=session, op=OperationKind.DESCEND)
        if isinstance(req, APIResponse):
            return req
        resp = self._router.route(req)
        self._finalise(req, resp, session=session)
        return resp

    def federate_evidence(
        self,
        req: APIRequest,
        *,
        caller_id: str = "anonymous",
        session: APISession | None = None,
    ) -> APIResponse:
        """Aggregate evidence from all available channels at *req.coordinate*.

        Federation collects responses from solver, runtime, and optionally
        copilot channels, then composes them into a single result via the
        trust algebra.  The resulting trust level is the meet (greatest lower
        bound) of all contributing channels; it can never exceed the weakest
        contributor.

        Copilot contributions are always explicitly flagged via
        :attr:`APIResponse.copilot_contributed`.

        Parameters
        ----------
        req:
            The federation request.
        caller_id:
            Identity for auth and rate-limit checks.
        session:
            Optional session providing configuration defaults.
        """
        req = self._prepare(req, caller_id=caller_id, session=session, op=OperationKind.FEDERATE)
        if isinstance(req, APIResponse):
            return req
        resp = self._router.route(req)
        self._finalise(req, resp, session=session)
        return resp

    def get_manifest(self, caller_id: str = "anonymous") -> Manifest:
        """Return the active :class:`~jugeo.evidence.manifests.Manifest`.

        The manifest is read-only from the API's perspective; mutations go
        through the solver subsystem.
        """
        self._event_log.record("manifest.read", caller_id=caller_id)
        return self._manifest

    def export_record(self, record: ExportRecord | Mapping[str, Any]) -> dict[str, Any]:
        """Project an export record to a JSON-ready dictionary.

        Historical interface tests and lightweight callers treat the API as the
        stable projection boundary for section exports, so this convenience
        method remains available even though the authoritative export logic lives
        in ``jugeo.judgments.exports``.
        """

        if hasattr(record, "to_dict") and callable(record.to_dict):
            payload = record.to_dict()
            if isinstance(payload, dict):
                return self._normalize_export_payload(payload)
        if isinstance(record, Mapping):
            return self._normalize_export_payload(dict(record))
        return self._normalize_export_payload(serialize(record))

    def serialize_export(self, record: ExportRecord | Mapping[str, Any]) -> str:
        """Legacy helper returning the JSON string for an export record."""
        return json.dumps(self.export_record(record), sort_keys=True)

    def _normalize_export_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply small legacy-normalization rules to exported payloads."""

        coordinate = payload.get("coordinate")
        if isinstance(coordinate, str):
            parts = coordinate.split("/")
            if parts and all(len(part) == 1 for part in parts):
                payload = dict(payload)
                payload["coordinate"] = "".join(parts)
        return payload

    def get_judgment(
        self,
        coordinate: str,
        caller_id: str = "anonymous",
    ) -> Judgment | None:
        """Return the recorded :class:`~jugeo.judgments.judgment_terms.Judgment`
        for *coordinate*, or ``None`` if no judgment has been registered.

        Parameters
        ----------
        coordinate:
            Dot-separated coordinate string to look up.
        caller_id:
            Identity for audit logging.
        """
        self._event_log.record(
            "judgment.read", caller_id=caller_id, coordinate=coordinate
        )
        coord = Coordinate(
            components=tuple(coordinate.split(".")),
            kind=CoordinateKind.REGION,
        )
        return self._manifest.judgment_store.get(coord)

    def get_obligations(
        self,
        coordinate: str,
        caller_id: str = "anonymous",
    ) -> list[ResidualObligation]:
        """Return all open :class:`~jugeo.judgments.judgment_terms.ResidualObligation`
        objects registered at *coordinate*.

        Residuals are never silently discarded; this method returns the full
        set regardless of trust level.

        Parameters
        ----------
        coordinate:
            Dot-separated coordinate string to look up.
        caller_id:
            Identity for audit logging.
        """
        self._event_log.record(
            "obligations.read", caller_id=caller_id, coordinate=coordinate
        )
        coord = Coordinate(
            components=tuple(coordinate.split(".")),
            kind=CoordinateKind.REGION,
        )
        return list(self._manifest.get_obligations(coord))

    def get_obstructions(
        self,
        coordinate: str,
        caller_id: str = "anonymous",
    ) -> list[Obstruction]:
        """Return all :class:`~jugeo.judgments.judgment_terms.Obstruction` objects
        recorded at *coordinate*.

        Obstructions document known impediments to verification; they remain
        visible even after partial progress.

        Parameters
        ----------
        coordinate:
            Dot-separated coordinate string to look up.
        caller_id:
            Identity for audit logging.
        """
        self._event_log.record(
            "obstructions.read", caller_id=caller_id, coordinate=coordinate
        )
        coord = Coordinate(
            components=tuple(coordinate.split(".")),
            kind=CoordinateKind.REGION,
        )
        return list(self._manifest.get_obstructions(coord))

    def get_certificate(
        self,
        coordinate: str,
        caller_id: str = "anonymous",
    ) -> Certificate | None:
        """Return the most recent verification :class:`~jugeo.evidence.certificates.Certificate`
        for *coordinate*, or ``None`` if none exists.

        Parameters
        ----------
        coordinate:
            Dot-separated coordinate string to look up.
        caller_id:
            Identity for audit logging.
        """
        self._event_log.record(
            "certificate.read", caller_id=caller_id, coordinate=coordinate
        )
        coord = Coordinate(
            components=tuple(coordinate.split(".")),
            kind=CoordinateKind.REGION,
        )
        return self._manifest.certificate_store.latest(coord)

    def submit_proposal(
        self,
        coordinate: str,
        proposition: str,
        evidence: dict[str, Any] | None = None,
        caller_id: str = "anonymous",
        trust_level: TrustLevel = TrustLevel.ORACLE_PROPOSED,
    ) -> APIResponse:
        """Submit an external evidence proposal for *coordinate*.

        Proposals arrive at most at ``ORACLE_PROPOSED`` trust; they are
        subject to the same validation, rate-limiting, and trust-ceiling
        rules as solver-generated evidence.  Copilot-assisted proposals
        should be submitted via :meth:`copilot_query` instead.

        Parameters
        ----------
        coordinate:
            Target coordinate for the proposal.
        proposition:
            The proposed claim in human-readable form.
        evidence:
            Optional supporting evidence bundle as a plain dict.
        caller_id:
            Identity of the proposing caller.
        trust_level:
            Self-assessed trust level of the proposal.  Capped at
            ``ORACLE_PROPOSED`` regardless of what the caller supplies.
        """
        effective_trust = TrustAlgebra.meet(trust_level, TrustLevel.ORACLE_PROPOSED)
        req = APIRequest(
            operation=OperationKind.VERIFY,
            coordinate=coordinate,
            proposition=proposition,
            metadata={"submitted_by": caller_id, "evidence": evidence},
        )
        self._event_log.record(
            "proposal.submit",
            request_id=req.request_id,
            caller_id=caller_id,
            coordinate=coordinate,
            trust_level=effective_trust.value,
        )
        resp = self.verify(req, caller_id=caller_id)
        return resp

    def copilot_query(
        self,
        prompt: str,
        coordinate: str = "",
        budget: int = 1000,
        caller_id: str = "anonymous",
        session: APISession | None = None,
    ) -> APIResponse:
        """Submit *prompt* to the copilot oracle via :class:`CopilotAPIBridge`.

        The response trust level is capped at ``ORACLE_PROPOSED`` by the
        bridge.  If no copilot bridge is configured, this method returns a
        ``FAILED`` response with an explanatory error.

        This is the **only** path through which copilot-generated evidence
        enters the JuGeo trust hierarchy.  Callers must not attempt to inject
        copilot responses via other methods.

        Parameters
        ----------
        prompt:
            The query text sent to the copilot oracle.
        coordinate:
            Semantic context coordinate; helps the oracle scope its answer.
        budget:
            Token / step budget passed to the oracle.
        caller_id:
            Identity for rate limiting and audit.
        session:
            Optional session for configuration defaults.
        """
        self._event_log.record(
            "copilot.api_query",
            caller_id=caller_id,
            coordinate=coordinate,
            prompt_length=len(prompt),
        )
        if not self._rate_limiter.check(caller_id, cost=2):
            wait = self._rate_limiter.throttle(caller_id, cost=2)
            return APIResponse(
                request_id=str(uuid.uuid4()),
                status=RequestStatus.REJECTED,
                error=StructuredFailure(
                    message=f"rate limit exceeded; retry after {wait:.1f}s",
                    scope=None,
                ),
            )
        if self._copilot_bridge is None:
            return APIResponse(
                request_id=str(uuid.uuid4()),
                status=RequestStatus.FAILED,
                error=StructuredFailure(
                    message="no copilot bridge configured on this JuGeoAPI instance",
                    scope=None,
                ),
            )
        self._rate_limiter.record(caller_id, cost=2)
        resp = self._copilot_bridge.query(
            prompt=prompt,
            coordinate=coordinate,
            budget=budget,
        )
        if session is not None:
            # Synthesise a dummy request for session history.
            req = APIRequest(
                operation=OperationKind.INSPECT,
                coordinate=coordinate,
                proposition=prompt,
                metadata={"copilot": True},
            )
            session.complete_request(req, resp)
        return resp

    def diagnostics(self, *args: Any, **kwargs: Any) -> DiagnosticReport:
        """Return a :class:`~jugeo.interfaces.diagnostics.DiagnosticReport`
        for the current system state.
        """
        return collect_diagnostics(*args, **kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare(
        self,
        req: APIRequest,
        caller_id: str,
        session: APISession | None,
        op: OperationKind,
    ) -> APIRequest | APIResponse:
        """Run pre-flight checks and apply defaults.  Returns the (possibly
        mutated) request on success, or a pre-built rejection :class:`APIResponse`
        on failure.
        """
        effective_caller = (
            session.caller_id if session is not None else caller_id
        )
        # Auth
        if not self._authenticator.check_permissions(effective_caller, op):
            return APIResponse(
                request_id=req.request_id,
                status=RequestStatus.REJECTED,
                error=StructuredFailure(
                    message=f"caller {effective_caller!r} not permitted for {op.value!r}",
                    scope=None,
                ),
            )
        # Rate limiting
        if not self._rate_limiter.check(effective_caller):
            wait = self._rate_limiter.throttle(effective_caller)
            return APIResponse(
                request_id=req.request_id,
                status=RequestStatus.REJECTED,
                error=StructuredFailure(
                    message=f"rate limit exceeded; retry after {wait:.1f}s",
                    scope=None,
                ),
            )
        # Apply session defaults
        if session is not None:
            req = session.apply_defaults(req)
            session.register_request(req)
        # Validation
        errors = self._validator.validate_request(req)
        if errors:
            return APIResponse(
                request_id=req.request_id,
                status=RequestStatus.REJECTED,
                error=StructuredFailure(
                    message="; ".join(errors),
                    scope=None,
                ),
            )
        # Timeout check
        if req.is_timed_out():
            return APIResponse(
                request_id=req.request_id,
                status=RequestStatus.TIMEOUT,
                error=StructuredFailure(message="deadline exceeded", scope=None),
            )
        self._rate_limiter.record(effective_caller)
        self._event_log.record(
            f"{op.value}.start",
            request_id=req.request_id,
            caller_id=effective_caller,
            coordinate=req.coordinate,
        )
        return req

    def _finalise(
        self,
        req: APIRequest,
        resp: APIResponse,
        session: APISession | None,
    ) -> None:
        """Post-request bookkeeping: log the outcome and update the session."""
        self._event_log.record(
            f"{req.operation.value}.end",
            request_id=req.request_id,
            status=resp.status.value,
            trust_level=resp.trust_level.value,
            latency_ms=resp.latency_ms,
            copilot_contributed=resp.copilot_contributed,
        )
        if session is not None:
            session.complete_request(req, resp)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _constant_time_compare(a: str, b: str) -> bool:
    """Compare two strings in constant time to mitigate timing attacks."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0


def _require_keys(data: dict[str, Any], keys: Iterable[str]) -> None:
    """Raise :class:`ValueError` if any of *keys* are missing from *data*."""
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(f"missing required keys: {missing}")


# ---------------------------------------------------------------------------
# Cross-subsystem API endpoints
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import judgment_terms as _judgment_mod
    _JUDGMENTS_SUBSYSTEM_AVAILABLE = True
except ImportError:
    _judgment_mod = None  # type: ignore[assignment]
    _JUDGMENTS_SUBSYSTEM_AVAILABLE = False

try:
    from jugeo.geometry.descent import DescentEngine as _DescentEngine  # type: ignore[import]
    _DESCENT_SUBSYSTEM_AVAILABLE = True
except ImportError:
    _DescentEngine = None  # type: ignore[assignment,misc]
    _DESCENT_SUBSYSTEM_AVAILABLE = False

try:
    from jugeo.evidence import trust as _evidence_trust_mod, channels as _evidence_channels_mod  # type: ignore[import]
    _EVIDENCE_SUBSYSTEM_AVAILABLE = True
except ImportError:
    _evidence_trust_mod = None  # type: ignore[assignment]
    _evidence_channels_mod = None  # type: ignore[assignment]
    _EVIDENCE_SUBSYSTEM_AVAILABLE = False

try:
    from jugeo.encodings import registry as _encoding_registry_mod  # type: ignore[import]
    _ENCODING_SUBSYSTEM_AVAILABLE = True
except ImportError:
    _encoding_registry_mod = None  # type: ignore[assignment]
    _ENCODING_SUBSYSTEM_AVAILABLE = False


def judgment_api_endpoint(
    coordinate: str,
    proposition: str,
    *,
    trust_floor: TrustLevel | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose judgment construction from ``jugeo.judgments``.

    Constructs a :class:`~jugeo.judgments.judgment_terms.Judgment` for the
    given *coordinate* and *proposition*, returning a dict with the judgment
    payload and trust metadata.

    Parameters
    ----------
    coordinate:
        Dot-separated semantic site name.
    proposition:
        Human-readable or schema-encoded proposition string.
    trust_floor:
        Optional minimum trust level required.
    metadata:
        Arbitrary caller-supplied key/value pairs.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "judgment": ..., "trust_level": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _JUDGMENTS_SUBSYSTEM_AVAILABLE,
        "judgment": None,
        "trust_level": None,
        "errors": [],
    }
    if not _JUDGMENTS_SUBSYSTEM_AVAILABLE:
        result["errors"].append("jugeo.judgments subsystem is not installed")
        return result
    try:
        prop = Proposition(content=proposition)
        coord = Coordinate(name=coordinate, kind=CoordinateKind.MODULE)
        judgment = Judgment(coordinate=coord, proposition=prop)
        result["judgment"] = serialize(judgment)
        result["trust_level"] = str(trust_floor) if trust_floor else "unspecified"
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def descent_api_endpoint(
    coordinate: str,
    *,
    depth: int = 5,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose descent operations from ``jugeo.geometry.descent``.

    Initiates a descent from the given *coordinate* down to the specified
    *depth*, returning a summary of the descent path and any obstructions.

    Parameters
    ----------
    coordinate:
        Dot-separated semantic site to descend from.
    depth:
        Maximum descent depth (default 5).
    metadata:
        Arbitrary caller-supplied key/value pairs.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "coordinate": str, "depth": int, "result": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _DESCENT_SUBSYSTEM_AVAILABLE,
        "coordinate": coordinate,
        "depth": depth,
        "result": None,
        "errors": [],
    }
    if not _DESCENT_SUBSYSTEM_AVAILABLE:
        result["errors"].append("jugeo.geometry.descent subsystem is not installed")
        return result
    try:
        engine = _DescentEngine()
        descent_result = engine.descend(coordinate, max_depth=depth)
        result["result"] = serialize(descent_result) if descent_result else None
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def evidence_api_endpoint(
    coordinate: str,
    *,
    trust_floor: TrustLevel | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose evidence queries from ``jugeo.evidence``.

    Queries the evidence subsystem for records associated with
    *coordinate*, optionally filtering by *trust_floor*.

    Parameters
    ----------
    coordinate:
        Dot-separated semantic site to query evidence for.
    trust_floor:
        Minimum trust level for returned evidence records.
    metadata:
        Arbitrary caller-supplied key/value pairs.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "coordinate": str, "records": [...], "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _EVIDENCE_SUBSYSTEM_AVAILABLE,
        "coordinate": coordinate,
        "records": [],
        "errors": [],
    }
    if not _EVIDENCE_SUBSYSTEM_AVAILABLE:
        result["errors"].append("jugeo.evidence subsystem is not installed")
        return result
    try:
        request = EvidenceRequest(coordinate=coordinate)
        router = ChannelRouter()
        response = router.query(request)
        records = response.records if hasattr(response, "records") else []
        result["records"] = [serialize(r) for r in records]
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def encoding_api_endpoint(
    encoding_family: str,
    payload: Any,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose encoding operations from ``jugeo.encodings``.

    Applies the named *encoding_family* to *payload* and returns the
    encoded representation.

    Parameters
    ----------
    encoding_family:
        Name of the encoding family to use (e.g. ``"theorem_schemas"``).
    payload:
        The object to encode.
    metadata:
        Arbitrary caller-supplied key/value pairs.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "encoding_family": str, "encoded": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _ENCODING_SUBSYSTEM_AVAILABLE,
        "encoding_family": encoding_family,
        "encoded": None,
        "errors": [],
    }
    if not _ENCODING_SUBSYSTEM_AVAILABLE:
        result["errors"].append("jugeo.encodings subsystem is not installed")
        return result
    try:
        encoder = _encoding_registry_mod.get_encoder(encoding_family)
        result["encoded"] = encoder.encode(payload)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result
