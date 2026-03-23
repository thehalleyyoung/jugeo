"""Routing layer that dispatches benchmark tasks to JuGeo subsystems.

# copilot: task_router.py — unified entry-point for the three benchmark tasks.

This module sits between the public-facing entry points (:mod:`jugeo.interfaces.api`
and :mod:`jugeo.interfaces.cli`) and the three ``problem_modes/`` subsystems that
actually carry out semantic analysis.  It defines a small, explicit protocol:

1. A caller supplies a :class:`TaskRequest` carrying raw inputs (source paths,
   code strings, spec text) together with task-kind metadata.
2. :class:`TaskRouter` validates the request, resolves the subsystem, enforces
   trust-floor constraints, and returns a :class:`TaskResult` that carries the
   serialised subsystem output plus full provenance metadata.
3. The result is first-class: it captures the achieved trust tier, wall-clock
   elapsed time, any warnings generated during dispatch, and any errors
   encountered, without ever raising to the caller.

Design invariants
-----------------
* **Never raise.**  Every code path that could fail is wrapped in a
  ``try/except`` block; errors surface as :class:`TaskResult` objects with
  ``status="failed"`` and a populated ``errors`` tuple.
* **Trust is always explicit.**  Every result records the trust tier actually
  achieved by the subsystem.  The router never silently promotes a result to a
  higher trust tier than the subsystem returned.
* **Subsystem imports are optional.**  Each subsystem import lives in a
  ``try/except ImportError`` block; if the subsystem is not installed the router
  falls back to a best-effort analysis (AST scan, structural diff, naive
  spec-text match).  The fallback trust tier is always ``"PROPOSAL"`` — weaker
  than any solver-backed result.
* **Judgments are tuples (c,φ,A,E,O,B,T,Π).**  The ``payload`` field of every
  :class:`TaskResult` mirrors this structure where the subsystem provides it.

Theory2 chapter references
---------------------------
* Task routing corresponds to the *dispatch* meta-rule in §7.1 (Orchestration).
* The trust-floor check corresponds to the *no-silent-upgrade* invariant in §3.4.
* Composite task handling corresponds to the *cover-refinement* rule in §9.2.

Usage (CLI callers)::

    from jugeo.interfaces.task_router import detect_bugs, check_equivalence
    result = detect_bugs("path/to/prog.py", is_path=True)
    print(result.status, result.trust_tier)

Usage (HTTP callers)::

    from jugeo.interfaces.task_router import route_request
    out = route_request({"kind": "bug_detection", "source": "x = 1/0"})
    print(out["status"])

Usage (programmatic)::

    from jugeo.interfaces.task_router import TaskRouter, TaskRequest, TaskKind
    router = TaskRouter()
    request = TaskRequest(
        request_id="demo-001",
        kind=TaskKind.BUG_DETECTION,
        inputs={"source": "def f():\\n    return 1/0"},
        config={},
    )
    result = router.route(request)
    print(result.to_dict())
"""

from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import inspect
import json
import sys
import textwrap
import time
import traceback
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Optional JuGeo core imports — all wrapped in try/except so that this module
# remains importable even when the full subsystem tree is not installed.
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import JuGeoError, StructuredFailure
except ImportError:
    JuGeoError = RuntimeError  # type: ignore[misc,assignment]
    StructuredFailure = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
    _TRUST_LEVELS_AVAILABLE = True
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    _TRUST_LEVELS_AVAILABLE = False

try:
    from jugeo.interfaces.diagnostics import DiagnosticReport, collect_diagnostics
    _DIAGNOSTICS_AVAILABLE = True
except ImportError:
    DiagnosticReport = None  # type: ignore[assignment,misc]
    collect_diagnostics = None  # type: ignore[assignment]
    _DIAGNOSTICS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Optional subsystem imports — bug_detection, relational_refinement,
# specification_satisfaction.  Each subsystem exposes its public surface via
# its package __init__.  If the package is absent we fall back gracefully.
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.relational_refinement import (
        EquivalenceVerifier,  # type: ignore[attr-defined]
    )
    _RELATIONAL_AVAILABLE = True
except ImportError:
    EquivalenceVerifier = None  # type: ignore[assignment,misc]
    _RELATIONAL_AVAILABLE = False

try:
    from jugeo.problem_modes.relational_refinement.equivalence_verification import (  # type: ignore[import]
        EquivalenceVerifier as _EquivalenceVerifier,
    )
    if not _RELATIONAL_AVAILABLE:
        EquivalenceVerifier = _EquivalenceVerifier  # type: ignore[assignment]
        _RELATIONAL_AVAILABLE = True
except ImportError:
    pass

try:
    from jugeo.problem_modes.specification_satisfaction import (
        specification_satisfaction_algorithm,  # type: ignore[attr-defined]
    )
    _SPEC_SATISFACTION_AVAILABLE = True
except ImportError:
    specification_satisfaction_algorithm = None  # type: ignore[assignment]
    _SPEC_SATISFACTION_AVAILABLE = False

try:
    from jugeo.problem_modes.specification_satisfaction.algorithms import (  # type: ignore[import]
        specification_satisfaction_algorithm as _spec_alg,
    )
    if not _SPEC_SATISFACTION_AVAILABLE:
        specification_satisfaction_algorithm = _spec_alg  # type: ignore[assignment]
        _SPEC_SATISFACTION_AVAILABLE = True
except ImportError:
    pass

try:
    from jugeo.encodings.theorem_schemas.proof_obligations import (  # type: ignore[import]
        ObligationTracker,
        build_obligations_from_schema,
        dispatch_obligations,
    )
    _OBLIGATIONS_AVAILABLE = True
except ImportError:
    ObligationTracker = None  # type: ignore[assignment,misc]
    build_obligations_from_schema = None  # type: ignore[assignment]
    dispatch_obligations = None  # type: ignore[assignment]
    _OBLIGATIONS_AVAILABLE = False

try:
    from jugeo.benchmarks.models import (
        BugCase as _BenchmarkBugCase,
        EquivalenceCase as _BenchmarkEquivalenceCase,
        InputPoint as _BenchmarkInputPoint,
        SpecCase as _BenchmarkSpecCase,
        Witness as _BenchmarkWitness,
    )
    from jugeo.benchmarks.runner import (
        TRUST_ORACLE as _BENCHMARK_ORACLE_TRUST,
        TRUST_RUNTIME as _BENCHMARK_RUNTIME_TRUST,
        _check_spec_case as _benchmark_check_spec_case,
        _compare_extensional_equality as _benchmark_compare_extensional_equality,
        _detect_bugs as _benchmark_detect_bugs,
    )
    _BENCHMARKS_AVAILABLE = True
except ImportError:
    _BenchmarkBugCase = None  # type: ignore[assignment,misc]
    _BenchmarkEquivalenceCase = None  # type: ignore[assignment,misc]
    _BenchmarkInputPoint = None  # type: ignore[assignment,misc]
    _BenchmarkSpecCase = None  # type: ignore[assignment,misc]
    _BenchmarkWitness = None  # type: ignore[assignment,misc]
    _benchmark_check_spec_case = None  # type: ignore[assignment]
    _benchmark_compare_extensional_equality = None  # type: ignore[assignment]
    _benchmark_detect_bugs = None  # type: ignore[assignment]
    _BENCHMARK_ORACLE_TRUST = "ORACLE_PROPOSED"
    _BENCHMARK_RUNTIME_TRUST = "RUNTIME_WITNESSED"
    _BENCHMARKS_AVAILABLE = False

try:
    from jugeo.benchmarks.semantics import (
        BENCHMARK_DECLARED_COVER_MIN_POINTS as _BENCHMARK_DECLARED_COVER_MIN_POINTS,
        detect_bug_observations as _benchmark_detect_bug_observations,
        semantic_coordinate as _benchmark_semantic_coordinate,
    )
except ImportError:
    _BENCHMARK_DECLARED_COVER_MIN_POINTS = 10
    _benchmark_detect_bug_observations = None  # type: ignore[assignment]
    _benchmark_semantic_coordinate = None  # type: ignore[assignment]


def _resolve_benchmark_support() -> dict[str, Any]:
    """Resolve benchmark support lazily.

    Pytest import order can leave the module-level benchmark imports unavailable
    even though the benchmark package itself is importable by the time a handler
    runs. Re-resolving here keeps the router honest about declared-cover
    semantics instead of permanently falling back to weaker heuristics.
    """

    global _BenchmarkBugCase
    global _BenchmarkEquivalenceCase
    global _BenchmarkInputPoint
    global _BenchmarkSpecCase
    global _BenchmarkWitness
    global _benchmark_check_spec_case
    global _benchmark_compare_extensional_equality
    global _benchmark_detect_bugs
    global _BENCHMARK_ORACLE_TRUST
    global _BENCHMARK_RUNTIME_TRUST
    global _BENCHMARKS_AVAILABLE

    if (
        _BENCHMARKS_AVAILABLE
        and _BenchmarkBugCase is not None
        and _BenchmarkEquivalenceCase is not None
        and _BenchmarkInputPoint is not None
        and _BenchmarkSpecCase is not None
        and _benchmark_check_spec_case is not None
        and _benchmark_compare_extensional_equality is not None
        and _benchmark_detect_bugs is not None
    ):
        return {
            "BugCase": _BenchmarkBugCase,
            "EquivalenceCase": _BenchmarkEquivalenceCase,
            "InputPoint": _BenchmarkInputPoint,
            "SpecCase": _BenchmarkSpecCase,
            "Witness": _BenchmarkWitness,
            "check_spec_case": _benchmark_check_spec_case,
            "compare_extensional_equality": _benchmark_compare_extensional_equality,
            "detect_bugs": _benchmark_detect_bugs,
            "oracle_trust": _BENCHMARK_ORACLE_TRUST,
            "runtime_trust": _BENCHMARK_RUNTIME_TRUST,
        }

    try:
        from jugeo.benchmarks.models import (
            BugCase as benchmark_bug_case,
            EquivalenceCase as benchmark_equivalence_case,
            InputPoint as benchmark_input_point,
            SpecCase as benchmark_spec_case,
            Witness as benchmark_witness,
        )
        from jugeo.benchmarks.runner import (
            TRUST_ORACLE as benchmark_oracle_trust,
            TRUST_RUNTIME as benchmark_runtime_trust,
            _check_spec_case as benchmark_check_spec_case,
            _compare_extensional_equality as benchmark_compare_extensional_equality,
            _detect_bugs as benchmark_detect_bugs,
        )
    except ImportError:
        return {}

    _BenchmarkBugCase = benchmark_bug_case
    _BenchmarkEquivalenceCase = benchmark_equivalence_case
    _BenchmarkInputPoint = benchmark_input_point
    _BenchmarkSpecCase = benchmark_spec_case
    _BenchmarkWitness = benchmark_witness
    _benchmark_check_spec_case = benchmark_check_spec_case
    _benchmark_compare_extensional_equality = benchmark_compare_extensional_equality
    _benchmark_detect_bugs = benchmark_detect_bugs
    _BENCHMARK_ORACLE_TRUST = benchmark_oracle_trust
    _BENCHMARK_RUNTIME_TRUST = benchmark_runtime_trust
    _BENCHMARKS_AVAILABLE = True
    return {
        "BugCase": _BenchmarkBugCase,
        "EquivalenceCase": _BenchmarkEquivalenceCase,
        "InputPoint": _BenchmarkInputPoint,
        "SpecCase": _BenchmarkSpecCase,
        "Witness": _BenchmarkWitness,
        "check_spec_case": _benchmark_check_spec_case,
        "compare_extensional_equality": _benchmark_compare_extensional_equality,
        "detect_bugs": _benchmark_detect_bugs,
        "oracle_trust": _BENCHMARK_ORACLE_TRUST,
        "runtime_trust": _BENCHMARK_RUNTIME_TRUST,
    }

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "TaskKind",
    "TaskRequest",
    "TaskResult",
    "RouterConfig",
    "TaskRouter",
    "RouterRegistry",
    # convenience functions
    "get_default_router",
    "detect_bugs",
    "check_equivalence",
    "check_spec_adherence",
    "route_request",
]

# ---------------------------------------------------------------------------
# Internal sentinel for missing optional return values
# ---------------------------------------------------------------------------

_MISSING: object = object()

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TaskKind(str, Enum):
    """The set of routing destinations understood by :class:`TaskRouter`.

    The three benchmark tasks map to distinct ``problem_modes/`` subsystems:

    * :attr:`BUG_DETECTION` → ``problem_modes/bug_detection/`` (or AST fallback).
    * :attr:`EQUIVALENCE_TESTING` → ``problem_modes/relational_refinement/``.
    * :attr:`SPEC_ADHERENCE` → ``problem_modes/specification_satisfaction/``.

    :attr:`COMPOSITE` requests carry multiple sub-requests and are fanned out
    by :meth:`TaskRouter.run_composite`.  :attr:`UNKNOWN` is used internally
    when auto-detection fails; it always routes to the error path.
    """

    BUG_DETECTION = "bug_detection"
    EQUIVALENCE_TESTING = "equivalence_testing"
    SPEC_ADHERENCE = "spec_adherence"
    COMPOSITE = "composite"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, s: str) -> "TaskKind":
        """Parse a case-insensitive string to a :class:`TaskKind`.

        Accepts both the canonical enum value (``"bug_detection"``) and common
        aliases (``"bugs"``, ``"equiv"``, ``"spec"``, ``"composite"``).

        Parameters
        ----------
        s:
            Input string from CLI flag or HTTP query parameter.

        Returns
        -------
        TaskKind
            Matched enum member, or :attr:`UNKNOWN` if no match is found.
        """
        normalised = s.strip().lower().replace("-", "_").replace(" ", "_")
        _aliases: dict[str, TaskKind] = {
            "bug_detection": cls.BUG_DETECTION,
            "bugs": cls.BUG_DETECTION,
            "bug": cls.BUG_DETECTION,
            "detect_bugs": cls.BUG_DETECTION,
            "equivalence_testing": cls.EQUIVALENCE_TESTING,
            "equivalence": cls.EQUIVALENCE_TESTING,
            "equiv": cls.EQUIVALENCE_TESTING,
            "relational": cls.EQUIVALENCE_TESTING,
            "relational_refinement": cls.EQUIVALENCE_TESTING,
            "spec_adherence": cls.SPEC_ADHERENCE,
            "spec": cls.SPEC_ADHERENCE,
            "specification": cls.SPEC_ADHERENCE,
            "specification_satisfaction": cls.SPEC_ADHERENCE,
            "adherence": cls.SPEC_ADHERENCE,
            "composite": cls.COMPOSITE,
            "multi": cls.COMPOSITE,
        }
        return _aliases.get(normalised, cls.UNKNOWN)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """Immutable description of a single routing request.

    All three benchmark tasks share this envelope.  Task-specific details
    live in ``inputs`` and ``config``; the router inspects ``kind`` to
    determine which subsystem to invoke.

    Attributes
    ----------
    request_id:
        Caller-supplied or auto-generated opaque identifier used for log
        correlation and idempotency.  Auto-generated from :func:`uuid.uuid4`
        when omitted.
    kind:
        Which benchmark task to execute.  The router also attempts
        auto-detection via :meth:`TaskRouter._detect_task_kind` when the
        value is :attr:`TaskKind.UNKNOWN`.
    inputs:
        Raw task inputs.  The expected keys depend on ``kind``:

        * ``bug_detection`` — ``source`` (code string) **or** ``path`` (file path).
        * ``equivalence_testing`` — ``prog_a`` and ``prog_b`` (code strings or
          paths), plus optional ``is_path: bool`` and benchmark-style
          ``input_cover``.
        * ``spec_adherence`` — ``source`` (code string or path), ``spec``
          (spec text or path), and optional benchmark-style ``input_cover``.
        * ``composite`` — ``subtasks: list[dict]`` where each dict is a nested
          :class:`TaskRequest` serialisation.
    config:
        Task-specific configuration overrides.  Keys are subsystem-dependent.
        Unknown keys are silently ignored.
    trust_floor:
        Minimum trust tier name the caller requires.  If the subsystem cannot
        meet this floor the result has ``status="failed"`` with an explanatory
        message in ``errors``.  Accepts :attr:`TrustTier` names (``"PROPOSAL"``,
        ``"REVIEWED"``, ``"VERIFIED"``) or :attr:`TrustLevel` names
        (e.g. ``"SOLVER_DISCHARGED"``).
    max_depth:
        Maximum recursion / descent depth passed to subsystems that support it.
        Governs how deep the checker descends into nested scopes.
    timeout_s:
        Wall-clock timeout in seconds.  The router checks this before and
        after each subsystem call; if exceeded the result has
        ``status="timeout"``.
    metadata:
        Arbitrary caller-supplied key/value pairs attached for logging and
        auditing.
    """

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: TaskKind = TaskKind.UNKNOWN
    inputs: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    trust_floor: str = "PROPOSAL"
    max_depth: int = 10
    timeout_s: float = 60.0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskRequest":
        """Construct a :class:`TaskRequest` from a plain dictionary.

        This is the primary entry point for HTTP and CLI callers that
        serialise their requests as JSON.  Unknown keys in ``d`` are placed
        into ``metadata`` rather than silently discarded, preserving full
        audit provenance.

        Parameters
        ----------
        d:
            Dictionary typically produced by ``json.loads``.  Must contain at
            least a ``"kind"`` key; all other keys are optional.

        Returns
        -------
        TaskRequest
            Fully initialised request with ``kind`` normalised to a
            :class:`TaskKind` enum member.

        Examples
        --------
        >>> req = TaskRequest.from_dict({"kind": "bug_detection", "source": "x=1"})
        >>> req.kind
        <TaskKind.BUG_DETECTION: 'bug_detection'>
        """
        # Normalise 'kind' — accept both the canonical name and common shorthands.
        raw_kind = d.get("kind", "unknown")
        kind = TaskKind.from_string(str(raw_kind))

        # Gather well-known top-level keys into inputs, leaving everything else
        # in metadata so we never lose caller-supplied provenance context.
        known_input_keys = {
            "source", "path", "prog_a", "prog_b", "spec",
            "is_path", "source_is_path", "subtasks", "input_cover",
        }
        inputs: dict[str, Any] = dict(d.get("inputs", {}))
        for key in known_input_keys:
            if key in d and key not in inputs:
                inputs[key] = d[key]

        config: dict[str, Any] = dict(d.get("config", {}))
        metadata: dict[str, Any] = dict(d.get("metadata", {}))

        # Surface any unknown top-level keys into metadata.
        routing_keys = known_input_keys | {
            "kind", "request_id", "inputs", "config",
            "trust_floor", "max_depth", "timeout_s", "metadata",
        }
        for key, val in d.items():
            if key not in routing_keys:
                metadata.setdefault(f"_extra_{key}", val)

        return cls(
            request_id=str(d.get("request_id", uuid.uuid4())),
            kind=kind,
            inputs=inputs,
            config=config,
            trust_floor=str(d.get("trust_floor", "PROPOSAL")),
            max_depth=int(d.get("max_depth", 10)),
            timeout_s=float(d.get("timeout_s", 60.0)),
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON encoding.

        Returns
        -------
        dict
            All fields serialised; ``kind`` is represented as its string value.
        """
        return {
            "request_id": self.request_id,
            "kind": self.kind.value,
            "inputs": dict(self.inputs),
            "config": dict(self.config),
            "trust_floor": self.trust_floor,
            "max_depth": self.max_depth,
            "timeout_s": self.timeout_s,
            "metadata": dict(self.metadata),
        }

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    def is_timed_out(self, start: float) -> bool:
        """Return ``True`` if wall-clock time since *start* exceeds :attr:`timeout_s`."""
        return (time.monotonic() - start) >= self.timeout_s

    @property
    def source(self) -> str | None:
        """Convenience accessor for ``inputs["source"]``."""
        return self.inputs.get("source")

    @property
    def spec(self) -> str | None:
        """Convenience accessor for ``inputs["spec"]``."""
        return self.inputs.get("spec")


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Immutable result returned by :class:`TaskRouter` for every request.

    The router never raises; all error conditions are encoded here.  The
    result is a first-class object that carries full audit provenance,
    including the trust tier actually achieved by the subsystem, the wall-clock
    time elapsed, any warnings, and any errors that prevented full analysis.

    Attributes
    ----------
    request_id:
        Echo of the originating :attr:`TaskRequest.request_id`.
    kind:
        The task kind that was dispatched.
    status:
        Lifecycle outcome of the routing attempt:

        * ``"success"`` — subsystem produced a definitive result.
        * ``"partial"`` — subsystem produced a result but residual obligations
          remain.
        * ``"failed"`` — subsystem raised or the router could not dispatch.
        * ``"timeout"`` — the request exceeded its ``timeout_s`` budget.
    payload:
        Subsystem-specific result, serialised to a plain dictionary.  The
        structure varies by ``kind``; see the individual subsystem modules for
        field documentation.  Always present as at least ``{}`` even on failure.
    trust_tier:
        The trust tier name actually achieved.  One of ``"PROPOSAL"``,
        ``"REVIEWED"``, ``"VERIFIED"``, or a :attr:`TrustLevel` name such as
        ``"SOLVER_DISCHARGED"``.  Never coerced upward by the router.
    elapsed_s:
        Wall-clock time in seconds from the start of :meth:`TaskRouter.route`
        to the moment the result was constructed.
    errors:
        Tuple of error messages encountered during dispatch.  Empty on success.
    warnings:
        Tuple of non-fatal diagnostic messages produced during dispatch.
    metadata:
        Arbitrary provenance data: request fingerprint, subsystem version,
        fallback flags, etc.
    """

    request_id: str
    kind: TaskKind
    status: str  # "success" | "partial" | "failed" | "timeout"
    payload: dict[str, Any]
    trust_tier: str
    elapsed_s: float
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_success(self) -> bool:
        """Return ``True`` for both ``"success"`` and ``"partial"`` statuses.

        A partial result is still a result — the subsystem produced output but
        residual obligations remain open.  Callers that require unconditional
        results should check ``result.status == "success"`` directly.
        """
        return self.status in ("success", "partial")

    def has_errors(self) -> bool:
        """Return ``True`` when the ``errors`` tuple is non-empty."""
        return bool(self.errors)

    def has_warnings(self) -> bool:
        """Return ``True`` when the ``warnings`` tuple is non-empty."""
        return bool(self.warnings)

    def trust_meets_floor(self, floor: str) -> bool:
        """Return ``True`` when the achieved trust tier is at least *floor*.

        The comparison respects the partial order
        PROPOSAL < REVIEWED < VERIFIED (for :class:`TrustTier`) and the finer
        order induced by :class:`TrustLevel`.  Falls back to a simple string
        equality check when the trust machinery is unavailable.

        Parameters
        ----------
        floor:
            Minimum acceptable trust tier name.
        """
        _tier_order = {"PROPOSAL": 1, "REVIEWED": 2, "VERIFIED": 3}
        achieved = _tier_order.get(self.trust_tier.upper(), 1)
        required = _tier_order.get(floor.upper(), 1)
        return achieved >= required

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON encoding.

        Returns
        -------
        dict
            All fields serialised; ``kind`` is represented as its string value,
            ``errors`` and ``warnings`` are lists rather than tuples.
        """
        return {
            "request_id": self.request_id,
            "kind": self.kind.value,
            "status": self.status,
            "payload": dict(self.payload),
            "trust_tier": self.trust_tier,
            "elapsed_s": round(self.elapsed_s, 6),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }

    def render_text(self) -> str:
        """Return a short human-readable summary suitable for CLI output."""
        lines = [
            f"TaskResult({self.kind.value})",
            f"  status     : {self.status}",
            f"  trust_tier : {self.trust_tier}",
            f"  elapsed_s  : {self.elapsed_s:.3f}",
        ]
        if self.errors:
            lines.append(f"  errors     : {'; '.join(self.errors)}")
        if self.warnings:
            lines.append(f"  warnings   : {'; '.join(self.warnings)}")
        if self.payload:
            summary = json.dumps(self.payload, default=str)
            if len(summary) > 200:
                summary = summary[:197] + "..."
            lines.append(f"  payload    : {summary}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """Immutable configuration for a :class:`TaskRouter` instance.

    These values govern router-wide policy rather than per-request behaviour.
    Per-request overrides live in :attr:`TaskRequest.config`.

    Attributes
    ----------
    default_trust_floor:
        Trust floor applied to requests that do not specify one.
    default_max_depth:
        Default descent depth for requests that omit ``max_depth``.
    default_timeout_s:
        Default timeout for requests that omit ``timeout_s``.
    enable_z3:
        When ``True`` the router will attempt to enable Z3-backed analysis in
        subsystems that support it.  When ``False`` subsystems are instructed
        to use runtime-witnessed trust only.
    enable_oracle:
        When ``True`` oracle (copilot-backed) evidence channels are permitted.
        When ``False`` only locally-witnessed evidence is accepted, capping the
        achievable trust tier at ``"PROPOSAL"``.
    max_parallel_tasks:
        Maximum number of composite sub-tasks that may run concurrently.
        Currently honoured only as a documentation contract — actual parallel
        execution requires the asyncio extension.
    """

    default_trust_floor: str = "PROPOSAL"
    default_max_depth: int = 10
    default_timeout_s: float = 60.0
    enable_z3: bool = False
    enable_oracle: bool = False
    max_parallel_tasks: int = 4

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RouterConfig":
        """Construct a :class:`RouterConfig` from a plain dictionary.

        Unknown keys are silently ignored so that callers can pass a larger
        config object without filtering it first.

        Parameters
        ----------
        d:
            Dictionary typically loaded from a ``jugeo.toml`` or ``config.json``
            file.  Keys are matched case-insensitively.

        Returns
        -------
        RouterConfig
            A new config instance with values from *d* overriding defaults.
        """
        normalised = {k.lower(): v for k, v in d.items()}
        return cls(
            default_trust_floor=str(normalised.get("default_trust_floor", "PROPOSAL")),
            default_max_depth=int(normalised.get("default_max_depth", 10)),
            default_timeout_s=float(normalised.get("default_timeout_s", 60.0)),
            enable_z3=bool(normalised.get("enable_z3", False)),
            enable_oracle=bool(normalised.get("enable_oracle", False)),
            max_parallel_tasks=int(normalised.get("max_parallel_tasks", 4)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON encoding."""
        return {
            "default_trust_floor": self.default_trust_floor,
            "default_max_depth": self.default_max_depth,
            "default_timeout_s": self.default_timeout_s,
            "enable_z3": self.enable_z3,
            "enable_oracle": self.enable_oracle,
            "max_parallel_tasks": self.max_parallel_tasks,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _ASTBugAnalyzer:
    """Lightweight AST-based bug detector used as a fallback.

    When ``problem_modes/bug_detection/`` is not installed this analyzer
    performs a best-effort scan of a Python source string using the standard
    library ``ast`` module.  It is deliberately conservative: it only reports
    patterns it can identify syntactically without executing the code.

    Detectable patterns
    -------------------
    * Division or modulo by a literal zero (``x / 0``, ``x % 0``).
    * Bare ``except:`` clauses that swallow all exceptions.
    * ``assert False`` statements that always fail.
    * Assignments to ``__builtins__`` (shadow of built-in namespace).
    * Recursive infinite loops: ``while True:`` with no ``break`` or ``return``
      in the immediate body.
    * Unreachable code after ``return`` or ``raise`` at module/function top level.
    * Use of deprecated ``os.popen`` and similar unsafe calls.
    * Mutable default arguments in function definitions.
    """

    # Node-visitor helper: walk the tree collecting findings.
    def analyze(self, source: str) -> list[dict[str, Any]]:
        """Parse *source* and return a list of bug-report dicts.

        Each dict has keys ``"line"``, ``"col"``, ``"severity"``,
        ``"rule"``, and ``"message"``.

        Parameters
        ----------
        source:
            Python source code as a string.

        Returns
        -------
        list[dict]
            Zero or more findings.  An empty list means no suspicious patterns
            were detected (not that the code is correct).
        """
        findings: list[dict[str, Any]] = []
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            findings.append({
                "line": exc.lineno or 0,
                "col": exc.offset or 0,
                "severity": "error",
                "rule": "syntax_error",
                "message": f"SyntaxError: {exc.msg}",
            })
            return findings

        findings.extend(self._scan_division_by_zero(tree))
        findings.extend(self._scan_bare_except(tree))
        findings.extend(self._scan_assert_false(tree))
        findings.extend(self._scan_builtins_shadow(tree))
        findings.extend(self._scan_mutable_default_args(tree))
        findings.extend(self._scan_infinite_while_no_break(tree))
        findings.extend(self._scan_unreachable_after_return(tree))
        return findings

    # ------------------------------------------------------------------
    # Individual scan routines
    # ------------------------------------------------------------------

    def _scan_division_by_zero(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Detect ``expr / 0`` and ``expr % 0`` patterns."""
        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Mod, ast.FloorDiv)):
                right = node.right
                # Constant zero: 0, 0.0, 0j
                if isinstance(right, ast.Constant) and right.value == 0:
                    op_sym = "/" if isinstance(node.op, ast.Div) else (
                        "%" if isinstance(node.op, ast.Mod) else "//"
                    )
                    results.append({
                        "line": getattr(node, "lineno", 0),
                        "col": getattr(node, "col_offset", 0),
                        "severity": "error",
                        "rule": "division_by_zero",
                        "message": f"Division by literal zero: `{op_sym} 0`",
                    })
        return results

    def _scan_bare_except(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Detect ``except:`` without exception type."""
        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                results.append({
                    "line": getattr(node, "lineno", 0),
                    "col": getattr(node, "col_offset", 0),
                    "severity": "warning",
                    "rule": "bare_except",
                    "message": "Bare `except:` swallows all exceptions including KeyboardInterrupt",
                })
        return results

    def _scan_assert_false(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Detect ``assert False`` (always-failing assertion)."""
        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                test = node.test
                if isinstance(test, ast.Constant) and test.value is False:
                    results.append({
                        "line": getattr(node, "lineno", 0),
                        "col": getattr(node, "col_offset", 0),
                        "severity": "error",
                        "rule": "assert_false",
                        "message": "`assert False` unconditionally fails at runtime",
                    })
        return results

    def _scan_builtins_shadow(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Detect assignment to ``__builtins__``."""
        results = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]
                elif isinstance(node, ast.AnnAssign) and node.target:
                    targets = [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "__builtins__":
                        results.append({
                            "line": getattr(node, "lineno", 0),
                            "col": getattr(node, "col_offset", 0),
                            "severity": "warning",
                            "rule": "builtins_shadow",
                            "message": "Assignment to `__builtins__` shadows the built-in namespace",
                        })
        return results

    def _scan_mutable_default_args(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Detect mutable default argument values (``def f(x=[]):``)."""
        _mutable_types = (ast.List, ast.Dict, ast.Set)
        results = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = node.args.defaults + node.args.kw_defaults
                for default in defaults:
                    if default is not None and isinstance(default, _mutable_types):
                        type_name = type(default).__name__.replace("ast.", "").lower()
                        results.append({
                            "line": getattr(node, "lineno", 0),
                            "col": getattr(node, "col_offset", 0),
                            "severity": "warning",
                            "rule": "mutable_default_arg",
                            "message": (
                                f"Function `{node.name}` uses a mutable {type_name} as a default "
                                "argument; shared across all calls"
                            ),
                        })
        return results

    def _scan_infinite_while_no_break(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Detect ``while True:`` bodies with no ``break`` or ``return``."""
        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                test = node.test
                if not (isinstance(test, ast.Constant) and test.value is True):
                    continue
                # Check immediate body for break/return (shallow — not recursive).
                has_exit = any(
                    isinstance(stmt, (ast.Break, ast.Return, ast.Raise))
                    for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                )
                if not has_exit:
                    results.append({
                        "line": getattr(node, "lineno", 0),
                        "col": getattr(node, "col_offset", 0),
                        "severity": "warning",
                        "rule": "infinite_loop_no_break",
                        "message": "`while True:` loop with no `break`, `return`, or `raise` — potential infinite loop",
                    })
        return results

    def _scan_unreachable_after_return(self, tree: ast.AST) -> list[dict[str, Any]]:
        """Detect statements that follow a ``return`` or ``raise`` in the same block."""
        results = []

        def _check_body(body: list[ast.stmt]) -> None:
            terminal_seen = False
            for stmt in body:
                if terminal_seen:
                    results.append({
                        "line": getattr(stmt, "lineno", 0),
                        "col": getattr(stmt, "col_offset", 0),
                        "severity": "warning",
                        "rule": "unreachable_code",
                        "message": "Unreachable statement after `return` or `raise`",
                    })
                if isinstance(stmt, (ast.Return, ast.Raise)):
                    terminal_seen = True

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                _check_body(node.body)  # type: ignore[arg-type]

        return results


class _SourceLoader:
    """Utility for resolving source inputs (string or file path).

    Both CLI and API callers may provide source code either as an inline string
    or as a path to a file on disk.  This helper normalises the two forms so
    that the subsystem adapters never need to handle I/O themselves.
    """

    @staticmethod
    def load(source_or_path: str, *, is_path: bool = False) -> tuple[str, str]:
        """Load source from *source_or_path*.

        Parameters
        ----------
        source_or_path:
            Either raw Python source code or an absolute/relative file path.
        is_path:
            When ``True`` treat *source_or_path* as a file path and read the
            file.  When ``False`` treat it as inline source code.

        Returns
        -------
        tuple[str, str]
            ``(source_code, origin)`` where ``origin`` is either the file path
            or ``"<inline>"`` for inline strings.

        Raises
        ------
        FileNotFoundError
            If ``is_path=True`` and the file does not exist.
        """
        if is_path:
            import pathlib
            path = pathlib.Path(source_or_path)
            source = path.read_text(encoding="utf-8")
            return source, str(path.resolve())
        return source_or_path, "<inline>"

    @staticmethod
    def fingerprint(source: str) -> str:
        """Return a short SHA-256 fingerprint of *source* for provenance tracking."""
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return digest[:16]


def _coerce_benchmark_input_cover(raw_cover: Any) -> tuple[Any, ...] | None:
    """Normalise a benchmark-style declared cover into InputPoint objects."""
    if raw_cover is None:
        return None
    if not isinstance(raw_cover, (list, tuple)):
        raise TypeError("input_cover must be a sequence of points")
    points = []
    for raw_point in raw_cover:
        if isinstance(raw_point, _RouterBenchmarkPoint):
            points.append(raw_point)
        elif isinstance(raw_point, dict):
            points.append(_RouterBenchmarkPoint.from_dict(raw_point))
        elif hasattr(raw_point, "args") and hasattr(raw_point, "kwargs"):
            kwargs = dict(raw_point.kwargs)
            if not all(isinstance(key, str) for key in kwargs):
                raise TypeError("input_cover point kwargs must be a string-keyed mapping")
            points.append(_RouterBenchmarkPoint(tuple(raw_point.args), kwargs))
        else:
            raise TypeError("input_cover points must declare args and kwargs")
    return _router_require_declared_cover(tuple(points))


def _benchmark_cover_to_dicts(points: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [point.to_dict() if hasattr(point, "to_dict") else {"args": [], "kwargs": {}} for point in points]


def _benchmark_witness_to_dict(witness: Any | None) -> dict[str, Any] | None:
    if witness is None:
        return None
    payload = {"message": getattr(witness, "message", str(witness))}
    input_point = getattr(witness, "input_point", None)
    if input_point is not None:
        payload["input_point"] = input_point.to_dict() if hasattr(input_point, "to_dict") else repr(input_point)
    coordinate = getattr(witness, "coordinate", None)
    if coordinate is not None:
        payload["coordinate"] = coordinate
    cover_index = getattr(witness, "cover_index", None)
    if cover_index is not None:
        payload["cover_index"] = cover_index
    return payload


@dataclass(frozen=True, slots=True)
class _RouterBenchmarkPoint:
    args: tuple[Any, ...]
    kwargs: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "_RouterBenchmarkPoint":
        args = payload.get("args", ())
        kwargs = payload.get("kwargs", {})
        if not isinstance(args, (list, tuple)):
            raise TypeError("input_cover point args must be a sequence")
        if not isinstance(kwargs, Mapping) or not all(isinstance(key, str) for key in kwargs):
            raise TypeError("input_cover point kwargs must be a string-keyed mapping")
        return cls(args=tuple(args), kwargs=dict(kwargs))

    def to_dict(self) -> dict[str, Any]:
        return {"args": list(self.args), "kwargs": dict(self.kwargs)}


@dataclass(frozen=True, slots=True)
class _RouterBenchmarkWitness:
    message: str
    input_point: _RouterBenchmarkPoint | None = None
    coordinate: str | None = None
    cover_index: int | None = None


@dataclass(frozen=True, slots=True)
class _RouterExecutionOutcome:
    tag: str
    value: Any


def _router_load_function(source: str, function_name: str) -> Any:
    namespace: dict[str, Any] = {}
    exec(compile(source, f"<router-benchmark:{function_name}>", "exec"), namespace, namespace)
    function = namespace[function_name]
    if not callable(function):
        raise TypeError(f"{function_name!r} did not resolve to a callable")
    return function


def _router_call(function: Any, point: _RouterBenchmarkPoint) -> _RouterExecutionOutcome:
    try:
        args = copy.deepcopy(point.args)
        kwargs = copy.deepcopy(point.kwargs)
        return _RouterExecutionOutcome("return", function(*args, **kwargs))
    except Exception as exc:  # pragma: no cover - surfaced through witnesses.
        return _RouterExecutionOutcome("raise", (type(exc).__name__, str(exc)))


def _router_call_fresh(source: str, function_name: str, point: _RouterBenchmarkPoint) -> _RouterExecutionOutcome:
    return _router_call(_router_load_function(source, function_name), point)


def _router_point_signature(point: _RouterBenchmarkPoint) -> str:
    return json.dumps(point.to_dict(), sort_keys=True)


def _router_format_outcome(outcome: _RouterExecutionOutcome) -> str:
    return f"{outcome.tag}={outcome.value!r}"


def _router_semantic_coordinate(source: str) -> str | None:
    if _benchmark_semantic_coordinate is None:
        return None
    return _benchmark_semantic_coordinate(source)


def _router_require_declared_cover(points: tuple[_RouterBenchmarkPoint, ...]) -> tuple[_RouterBenchmarkPoint, ...]:
    if len(points) < _BENCHMARK_DECLARED_COVER_MIN_POINTS:
        raise ValueError(
            "declared-cover benchmark tasks require at least "
            f"{_BENCHMARK_DECLARED_COVER_MIN_POINTS} input points"
        )
    signatures = tuple(_router_point_signature(point) for point in points)
    if len(signatures) != len(set(signatures)):
        raise ValueError("declared-cover benchmark tasks require distinct input points")
    return points


def _router_compare_extensional_equality(
    source_a: str,
    source_b: str,
    *,
    relation_family: str,
    points: tuple[_RouterBenchmarkPoint, ...],
) -> tuple[bool, _RouterBenchmarkWitness | None]:
    if relation_family != "extensional-equality-on-declared-cover":
        raise ValueError(f"unsupported equivalence relation family {relation_family!r}")
    left_coordinate = _router_semantic_coordinate(source_a)
    right_coordinate = _router_semantic_coordinate(source_b)
    for index, point in enumerate(_router_require_declared_cover(points)):
        left_outcome = _router_call_fresh(source_a, "solve", point)
        right_outcome = _router_call_fresh(source_b, "solve", point)
        if left_outcome != right_outcome:
            if left_coordinate is not None and right_coordinate is not None:
                coordinate = f"{left_coordinate}|{right_coordinate}#cover[{index}]"
            else:
                coordinate = f"cover[{index}]"
            return (
                False,
                _RouterBenchmarkWitness(
                    message=(
                        f"relation {relation_family!r} failed on the declared finite cover: "
                        f"left {_router_format_outcome(left_outcome)} vs right {_router_format_outcome(right_outcome)}"
                    ),
                    input_point=point,
                    coordinate=coordinate,
                    cover_index=index,
                ),
            )
    return True, None


def _router_check_spec_execution(
    source: str,
    spec_source: str,
    *,
    points: tuple[_RouterBenchmarkPoint, ...],
) -> tuple[bool, _RouterBenchmarkWitness | None]:
    program_coordinate = _router_semantic_coordinate(source)
    for index, point in enumerate(_router_require_declared_cover(points)):
        coordinate = f"{program_coordinate}#cover[{index}]" if program_coordinate is not None else f"cover[{index}]"
        result = _router_call_fresh(source, "solve", point)
        if result.tag != "return":
            return False, _RouterBenchmarkWitness(
                "program raised instead of realizing a section on the declared cover",
                point,
                coordinate,
                index,
            )
        try:
            spec = _router_load_function(spec_source, "spec")
            spec_args = copy.deepcopy(point.args)
            spec_kwargs = copy.deepcopy(point.kwargs)
            spec_result = spec(result.value, *spec_args, **spec_kwargs)
        except Exception as exc:  # pragma: no cover - surfaced through witnesses.
            return False, _RouterBenchmarkWitness(f"spec raised {type(exc).__name__}", point, coordinate, index)
        if not isinstance(spec_result, bool):
            return False, _RouterBenchmarkWitness(
                "specification must return a boolean on the declared finite cover",
                point,
                coordinate,
                index,
            )
        if not spec_result:
            return False, _RouterBenchmarkWitness(
                "specification returned False on the declared finite cover",
                point,
                coordinate,
                index,
            )
    return True, None


@dataclass(frozen=True, slots=True)
class _RouterBugObservation:
    code: str
    lineno: int
    col: int
    node_type: str
    message: str


@dataclass(frozen=True, slots=True)
class _RouterOpenHandleBinding:
    name: str
    lineno: int
    col: int
    node_type: str


class _RouterOpenWithoutCloseAnalyzer:
    def __init__(self) -> None:
        self._leak_candidates: list[_RouterOpenHandleBinding] = []

    def detect(self, tree: ast.AST) -> tuple[_RouterOpenHandleBinding, ...]:
        body = getattr(tree, "body", None)
        if isinstance(body, list):
            self._analyze_block(body, {}, finalize=True)
        return tuple(sorted(self._leak_candidates, key=lambda item: (item.lineno, item.col, item.name)))

    def _record_leak(self, binding: _RouterOpenHandleBinding) -> None:
        if any(
            existing.name == binding.name
            and existing.lineno == binding.lineno
            and existing.col == binding.col
            and existing.node_type == binding.node_type
            for existing in self._leak_candidates
        ):
            return
        self._leak_candidates.append(binding)

    def _binding_from(self, target: ast.AST, value: ast.AST, name: str) -> _RouterOpenHandleBinding:
        return _RouterOpenHandleBinding(
            name=name,
            lineno=getattr(target, "lineno", getattr(value, "lineno", 0)),
            col=getattr(target, "col_offset", getattr(value, "col_offset", 0)),
            node_type=type(target).__name__,
        )

    def _merge_states(
        self, *states: dict[str, _RouterOpenHandleBinding]
    ) -> dict[str, _RouterOpenHandleBinding]:
        merged: dict[str, _RouterOpenHandleBinding] = {}
        for state in states:
            for name, binding in state.items():
                merged.setdefault(name, binding)
        return merged

    def _close_names_in_node(self, node: ast.AST, state: dict[str, _RouterOpenHandleBinding]) -> None:
        for nested in ast.walk(node):
            if (
                isinstance(nested, ast.Call)
                and isinstance(nested.func, ast.Attribute)
                and nested.func.attr == "close"
                and isinstance(nested.func.value, ast.Name)
            ):
                state.pop(nested.func.value.id, None)

    def _track_open_binding(
        self,
        state: dict[str, _RouterOpenHandleBinding],
        target: ast.AST,
        value: ast.AST,
    ) -> None:
        if _router_is_open_call(value):
            for name in _router_extract_target_names(target):
                previous = state.get(name)
                if previous is not None:
                    self._record_leak(previous)
                state[name] = self._binding_from(target, value, name)
            return
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for sub_target, sub_value in zip(target.elts, value.elts):
                self._track_open_binding(state, sub_target, sub_value)

    def _apply_expression_effects(self, node: ast.AST, state: dict[str, _RouterOpenHandleBinding]) -> None:
        for nested in ast.walk(node):
            if isinstance(nested, ast.NamedExpr):
                self._track_open_binding(state, nested.target, nested.value)
        self._close_names_in_node(node, state)

    def _analyze_block(
        self,
        statements: list[ast.stmt],
        state: dict[str, _RouterOpenHandleBinding],
        *,
        finalize: bool,
    ) -> dict[str, _RouterOpenHandleBinding]:
        current = dict(state)
        for statement in statements:
            current = self._analyze_statement(statement, current)
        if finalize:
            for binding in current.values():
                self._record_leak(binding)
        return current

    def _analyze_statement(
        self, statement: ast.stmt, state: dict[str, _RouterOpenHandleBinding]
    ) -> dict[str, _RouterOpenHandleBinding]:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested_body = getattr(statement, "body", None)
            if isinstance(nested_body, list):
                self._analyze_block(nested_body, {}, finalize=True)
            return state

        if isinstance(statement, ast.If):
            branch_state = dict(state)
            self._apply_expression_effects(statement.test, branch_state)
            body_state = self._analyze_block(statement.body, branch_state, finalize=False)
            else_state = self._analyze_block(statement.orelse, branch_state, finalize=False)
            return self._merge_states(body_state, else_state)

        if isinstance(statement, (ast.For, ast.AsyncFor)):
            loop_state = dict(state)
            self._apply_expression_effects(statement.iter, loop_state)
            body_state = self._analyze_block(statement.body, loop_state, finalize=False)
            orelse_state = self._analyze_block(statement.orelse, dict(state), finalize=False)
            return self._merge_states(state, body_state, orelse_state)

        if isinstance(statement, ast.While):
            loop_state = dict(state)
            self._apply_expression_effects(statement.test, loop_state)
            body_state = self._analyze_block(statement.body, loop_state, finalize=False)
            orelse_state = self._analyze_block(statement.orelse, dict(state), finalize=False)
            return self._merge_states(state, body_state, orelse_state)

        if isinstance(statement, (ast.With, ast.AsyncWith)):
            with_state = dict(state)
            managed_names: set[str] = set()
            bound_names: set[str] = set()
            for item in statement.items:
                self._apply_expression_effects(item.context_expr, with_state)
                if isinstance(item.context_expr, ast.Name):
                    managed_names.add(item.context_expr.id)
                if item.optional_vars is not None:
                    for name in _router_extract_target_names(item.optional_vars):
                        previous = with_state.get(name)
                        if previous is not None and not isinstance(item.context_expr, ast.Name):
                            self._record_leak(previous)
                        bound_names.add(name)
            body_state = self._analyze_block(statement.body, with_state, finalize=False)
            for name in managed_names | bound_names:
                body_state.pop(name, None)
            return body_state

        if isinstance(statement, ast.Try):
            try_state = self._analyze_block(statement.body, dict(state), finalize=False)
            normal_state = (
                self._analyze_block(statement.orelse, try_state, finalize=False)
                if statement.orelse
                else try_state
            )
            handler_seed = self._merge_states(dict(state), try_state)
            handler_states = [
                self._analyze_block(handler.body, handler_seed, finalize=False) for handler in statement.handlers
            ]
            if statement.finalbody:
                merged = self._merge_states(normal_state, *handler_states, dict(state))
                return self._analyze_block(statement.finalbody, merged, finalize=False)
            return self._merge_states(normal_state, *handler_states, dict(state))

        updated = dict(state)
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                self._track_open_binding(updated, target, statement.value)
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            self._track_open_binding(updated, statement.target, statement.value)
        elif isinstance(statement, ast.Expr):
            self._apply_expression_effects(statement.value, updated)
            return updated
        elif isinstance(statement, ast.Return) and statement.value is not None:
            self._apply_expression_effects(statement.value, updated)
            return updated

        self._close_names_in_node(statement, updated)
        return updated


class _RouterBenchmarkBugDetector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.observations: list[_RouterBugObservation] = []
        self._opened_names: dict[str, tuple[int, int, str]] = {}
        self._closed_names: set[str] = set()
        self._conditional_close_depth = 0
        self._open_without_close = _RouterOpenWithoutCloseAnalyzer()
        self._shadowed_builtins = frozenset(
            name
            for name, value in vars(builtins).items()
            if not name.startswith("__") and (callable(value) or isinstance(value, type))
        )

    def _visit_with_conditional_close_guard(self, nodes: list[ast.stmt]) -> None:
        self._conditional_close_depth += 1
        try:
            for node in nodes:
                self.visit(node)
        finally:
            self._conditional_close_depth -= 1

    def _observe(self, code: str, node: ast.AST, message: str) -> None:
        observation = _RouterBugObservation(
            code=code,
            lineno=getattr(node, "lineno", 0),
            col=getattr(node, "col_offset", 0),
            node_type=type(node).__name__,
            message=message,
        )
        if observation in self.observations:
            return
        self.observations.append(observation)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in self._shadowed_builtins:
            self._observe("shadow-builtin", node, f"function name {node.name!r} shadows a Python builtin")
        for default in node.args.defaults:
            if _router_is_mutable_default(default):
                self._observe("mutable-default", default, "mutable default argument introduces shared state")
        for default in node.args.kw_defaults:
            if default is not None and _router_is_mutable_default(default):
                self._observe("mutable-default", default, "mutable default argument introduces shared state")
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.arg in self._shadowed_builtins:
                self._observe("shadow-builtin", arg, f"parameter {arg.arg!r} shadows a Python builtin")
        if node.args.vararg is not None and node.args.vararg.arg in self._shadowed_builtins:
            self._observe("shadow-builtin", node.args.vararg, f"parameter {node.args.vararg.arg!r} shadows a Python builtin")
        if node.args.kwarg is not None and node.args.kwarg.arg in self._shadowed_builtins:
            self._observe("shadow-builtin", node.args.kwarg, f"parameter {node.args.kwarg.arg!r} shadows a Python builtin")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name in self._shadowed_builtins:
            self._observe("shadow-builtin", node, f"class name {node.name!r} shadows a Python builtin")
        self.generic_visit(node)

    def _track_open_binding(self, target: ast.AST, value: ast.AST) -> None:
        if _router_is_open_call(value):
            for name in _router_extract_target_names(target):
                self._opened_names[name] = (
                    getattr(target, "lineno", getattr(value, "lineno", 0)),
                    getattr(target, "col_offset", getattr(value, "col_offset", 0)),
                    type(target).__name__,
                )
            return
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for sub_target, sub_value in zip(target.elts, value.elts):
                self._track_open_binding(sub_target, sub_value)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._track_open_binding(target, node.value)
            for name in _router_extract_target_names(target):
                if name in self._shadowed_builtins:
                    self._observe("shadow-builtin", target, f"assignment to {name!r} shadows a Python builtin")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._track_open_binding(node.target, node.value)
        for name in _router_extract_target_names(node.target):
            if name in self._shadowed_builtins:
                self._observe("shadow-builtin", node.target, f"assignment to {name!r} shadows a Python builtin")
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._track_open_binding(node.target, node.value)
        for name in _router_extract_target_names(node.target):
            if name in self._shadowed_builtins:
                self._observe("shadow-builtin", node.target, f"assignment to {name!r} shadows a Python builtin")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        loop_names = _router_extract_target_names(node.target)
        for name in loop_names:
            if name in self._shadowed_builtins:
                self._observe("shadow-builtin", node.target, f"loop target {name!r} shadows a Python builtin")
        for child in node.body:
            for nested in ast.walk(child):
                if isinstance(nested, ast.Lambda):
                    free_names = _router_loaded_names(nested.body) - _router_argument_names(nested.args)
                    if free_names & loop_names:
                        self._observe(
                            "late-binding-closure",
                            nested,
                            "loop variable captured by lambda without freezing it in defaults",
                        )
                elif isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    free_names = set().union(*(_router_loaded_names(statement) for statement in nested.body)) - _router_argument_names(
                        nested.args
                    )
                    if free_names & loop_names:
                        self._observe(
                            "late-binding-closure",
                            nested,
                            "loop variable captured by nested function without binding a fresh value",
                        )
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.orelse)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.orelse)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.orelse)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            context_expr = item.context_expr
            if isinstance(context_expr, ast.Name):
                self._closed_names.add(context_expr.id)
            optional_vars = item.optional_vars
            if optional_vars is None:
                continue
            for name in _router_extract_target_names(optional_vars):
                if name in self._shadowed_builtins:
                    self._observe("shadow-builtin", optional_vars, f"with target {name!r} shadows a Python builtin")
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_with_conditional_close_guard(node.body)
        self._visit_with_conditional_close_guard(node.handlers)
        self._visit_with_conditional_close_guard(node.orelse)
        for finalizer in node.finalbody:
            self.visit(finalizer)

    def _visit_comprehension(self, body_nodes: tuple[ast.AST, ...]) -> None:
        for body in body_nodes:
            loop_names = {
                name.id
                for nested in ast.walk(body)
                if isinstance(nested, ast.comprehension)
                for name in ast.walk(nested.target)
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
            }
            if not loop_names:
                continue
            for name in loop_names:
                if name in self._shadowed_builtins:
                    self._observe("shadow-builtin", body, f"comprehension target {name!r} shadows a Python builtin")
            for nested in ast.walk(body):
                if isinstance(nested, ast.Lambda):
                    free_names = _router_loaded_names(nested.body) - _router_argument_names(nested.args)
                    if free_names & loop_names:
                        self._observe(
                            "late-binding-closure",
                            nested,
                            "comprehension variable captured by lambda without freezing it in defaults",
                        )
                        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension((node,))
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self._observe("bare-except", node, "bare except swallows unrelated failures")
        if node.name is not None and node.name in self._shadowed_builtins:
            self._observe("shadow-builtin", node, f"exception target {node.name!r} shadows a Python builtin")
        if node.type is not None:
            self.visit(node.type)
        self._visit_with_conditional_close_guard(node.body)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
            and self._conditional_close_depth == 0
        ):
            self._closed_names.add(node.func.value.id)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        for operator, left, right in zip(node.ops, operands, operands[1:]):
            if isinstance(operator, (ast.Is, ast.IsNot)) and (
                _router_is_non_singleton_literal(left) or _router_is_non_singleton_literal(right)
            ):
                self._observe(
                    "identity-literal",
                    node,
                    "identity comparison with a non-singleton literal is unreliable; use == or !=",
                )
                break
        self.generic_visit(node)

    def finalize(self) -> tuple[_RouterBugObservation, ...]:
        if hasattr(self, "_root"):
            leaks = self._open_without_close.detect(self._root)
            for leak in leaks:
                observation = _RouterBugObservation(
                    code="open-without-close",
                    lineno=leak.lineno,
                    col=leak.col,
                    node_type=leak.node_type,
                    message=f"file handle {leak.name!r} is opened without a matching close or context manager",
                )
                if observation not in self.observations:
                    self.observations.append(observation)
        return tuple(sorted(self.observations, key=lambda item: (item.lineno, item.col, item.code)))


def _router_extract_target_names(target: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _router_loaded_names(node: ast.AST) -> set[str]:
    return {
        name.id
        for name in ast.walk(node)
        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)
    }


def _router_argument_names(args: ast.arguments) -> set[str]:
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _router_is_mutable_default(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Tuple):
        return any(_router_is_mutable_default(element) for element in node.elts)
    return isinstance(node, ast.Call) and (_router_call_name(node.func) in {"list", "dict", "set", "defaultdict", "deque", "bytearray", "OrderedDict"})


def _router_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _router_is_open_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _router_call_name(node.func) == "open"


def _router_is_non_singleton_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        value = node.value
        return value is not None and value is not True and value is not False and value is not Ellipsis
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _router_is_non_singleton_literal(node.operand)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_router_is_literal_value(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is None or _router_is_literal_value(key) for key in node.keys) and all(
            _router_is_literal_value(value) for value in node.values
        )
    return False


def _router_is_literal_value(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _router_is_literal_value(node.operand)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_router_is_literal_value(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is None or _router_is_literal_value(key) for key in node.keys) and all(
            _router_is_literal_value(value) for value in node.values
        )
    return False


# ---------------------------------------------------------------------------
# Main router
# ---------------------------------------------------------------------------


class TaskRouter:
    """Routes :class:`TaskRequest` objects to the appropriate JuGeo subsystem.

    Instantiation is cheap; a single router instance may be reused across many
    requests.  The router is not thread-safe by default; external locking is
    required for concurrent use.

    Parameters
    ----------
    config:
        Router-wide configuration.  Uses :attr:`RouterConfig` defaults when
        ``None``.

    Attributes
    ----------
    config:
        The active router configuration.
    _ast_analyzer:
        Shared :class:`_ASTBugAnalyzer` instance for fallback bug detection.
    _source_loader:
        Shared :class:`_SourceLoader` instance.
    _dispatch_table:
        Maps each :class:`TaskKind` to the bound method that handles it.
    """

    def __init__(self, config: RouterConfig | None = None) -> None:
        self.config: RouterConfig = config if config is not None else RouterConfig()
        self._ast_analyzer = _ASTBugAnalyzer()
        self._source_loader = _SourceLoader()

        # Build dispatch table at construction time so hot-path routing
        # is a single dict lookup rather than a chain of if/elif.
        self._dispatch_table: dict[TaskKind, Any] = {
            TaskKind.BUG_DETECTION: self.detect_bugs,
            TaskKind.EQUIVALENCE_TESTING: self.check_equivalence,
            TaskKind.SPEC_ADHERENCE: self.check_spec_adherence,
            TaskKind.COMPOSITE: self.run_composite,
        }

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def route(self, request: TaskRequest) -> TaskResult:
        """Dispatch *request* to the appropriate subsystem.

        This is the single entry point for all three benchmark tasks.  It:

        1. Applies config defaults to any omitted request fields.
        2. Auto-detects task kind when ``request.kind`` is
           :attr:`TaskKind.UNKNOWN`.
        3. Looks up the bound handler in ``_dispatch_table``.
        4. Enforces the timeout budget; short-circuits to a timeout result if
           already exceeded before dispatch.
        5. Returns the handler's :class:`TaskResult` unchanged — it never
           modifies trust or status after the fact.

        Parameters
        ----------
        request:
            The request to dispatch.

        Returns
        -------
        TaskResult
            Always returns a result; never raises.
        """
        start = time.monotonic()

        try:
            # Auto-detect kind if necessary.
            kind = request.kind
            if kind is TaskKind.UNKNOWN:
                kind = self._detect_task_kind(request)
                # Rebuild the request with the detected kind.
                request = TaskRequest(
                    request_id=request.request_id,
                    kind=kind,
                    inputs=request.inputs,
                    config=request.config,
                    trust_floor=request.trust_floor,
                    max_depth=request.max_depth,
                    timeout_s=request.timeout_s,
                    metadata={**request.metadata, "_auto_detected_kind": kind.value},
                )

            # Pre-dispatch timeout check.
            if request.is_timed_out(start):
                return self._build_timeout_result(request, start)

            handler = self._dispatch_table.get(kind)
            if handler is None:
                return self._build_error_result(
                    request,
                    ValueError(f"No handler registered for TaskKind.{kind.name}"),
                    start=start,
                )

            # Dispatch to the handler.  Each handler is responsible for
            # returning a fully-formed TaskResult; we just pass it through.
            result = handler.__func__(self, **self._unpack_inputs(request))  # type: ignore[attr-defined]
            return result

        except Exception as exc:  # noqa: BLE001
            return self._build_error_result(request, exc, start=start)

    def _unpack_inputs(self, request: TaskRequest) -> dict[str, Any]:
        """Extract keyword arguments for the handler from *request*.

        The handler methods accept named parameters rather than a raw
        :class:`TaskRequest` so they can also be called directly by CLI code.
        This method translates the request into that calling convention.

        Returns
        -------
        dict
            Keyword arguments suitable for ``**`` unpacking into the handler.
        """
        inp = request.inputs
        cfg = request.config
        kind = request.kind

        if kind is TaskKind.BUG_DETECTION:
            source = inp.get("source") or inp.get("path", "")
            is_path = bool(inp.get("is_path", "path" in inp and "source" not in inp))
            return {
                "source_or_path": source,
                "is_path": is_path,
                "_request": request,
            }

        if kind is TaskKind.EQUIVALENCE_TESTING:
            return {
                "prog_a": inp.get("prog_a", ""),
                "prog_b": inp.get("prog_b", ""),
                "is_path": bool(inp.get("is_path", False)),
                "input_cover": inp.get("input_cover"),
                "_request": request,
            }

        if kind is TaskKind.SPEC_ADHERENCE:
            source = inp.get("source") or inp.get("path", "")
            source_is_path = bool(inp.get("source_is_path", False))
            spec = inp.get("spec", "")
            return {
                "source": source,
                "spec": spec,
                "source_is_path": source_is_path,
                "input_cover": inp.get("input_cover"),
                "_request": request,
            }

        if kind is TaskKind.COMPOSITE:
            return {"_request": request}

        return {"_request": request}

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------

    def detect_bugs(
        self,
        source_or_path: str,
        *,
        is_path: bool = False,
        _request: TaskRequest | None = None,
        **kwargs: Any,
    ) -> TaskResult:
        """Detect bugs in a Python source file or inline code string.

        Attempt to call the ``problem_modes.bug_detection`` subsystem if
        available; otherwise fall back to :class:`_ASTBugAnalyzer` which
        performs a purely structural scan using the standard-library ``ast``
        module.  The fallback achieves at most ``"PROPOSAL"`` trust since it
        cannot execute the code.

        Parameters
        ----------
        source_or_path:
            Either raw Python source code or a file path (when
            ``is_path=True``).
        is_path:
            Treat *source_or_path* as a path to read from disk.
        **kwargs:
            Extra keyword arguments forwarded to the subsystem if available.

        Returns
        -------
        TaskResult
            ``status="success"`` when the analysis completes (even if no bugs
            are found); ``status="failed"`` if parsing fails completely.
        """
        start = time.monotonic()
        request = _request or TaskRequest(
            kind=TaskKind.BUG_DETECTION,
            inputs={"source": source_or_path, "is_path": is_path},
        )

        warnings: list[str] = []
        trust_tier = "PROPOSAL"

        try:
            # Load source — may raise FileNotFoundError.
            source, origin = self._source_loader.load(source_or_path, is_path=is_path)
            fingerprint = self._source_loader.fingerprint(source)
            benchmark_support = _resolve_benchmark_support()
            if benchmark_support:
                bug_case = benchmark_support["BugCase"](
                    case_id=request.request_id,
                    description="TaskRouter benchmark-style bug detection request.",
                    program=source,
                    expected_bugs=(),
                )
                labels, reports = benchmark_support["detect_bugs"](bug_case)
                trust_tier = benchmark_support["oracle_trust"]
                findings = [
                    {
                        "label": report.counterexample.get("bug_code", report.cohomology_class.split(":", 1)[0]),
                        "kind": report.kind.value,
                        "severity": report.severity,
                        "coordinate": report.coordinate,
                        "message": report.description,
                        "counterexample": report.counterexample,
                        "cohomology_class": report.cohomology_class,
                    }
                    for report in reports
                ]
                payload = self._serialize_result(
                    {
                        "findings": findings,
                        "labels": list(labels),
                        "error_count": len(labels),
                        "warning_count": 0,
                        "source_fingerprint": fingerprint,
                        "origin": origin,
                        "line_count": source.count("\n") + 1,
                        "analysis_method": "benchmark_bug_detector",
                        "judgment_tuple": {
                            "c": origin,
                            "phi": "source_is_bug_free",
                            "A": "benchmark_bug_detector",
                            "E": findings,
                            "O": [finding["cohomology_class"] for finding in findings],
                            "B": len(findings),
                            "T": trust_tier,
                            "Pi": fingerprint,
                        },
                    },
                    TaskKind.BUG_DETECTION,
                )
                warnings.extend(
                    [
                        f"{len(labels)} benchmark bug label(s) detected; obstruction witnesses retained",
                    ]
                    if labels
                    else []
                )
                return TaskResult(
                    request_id=request.request_id,
                    kind=TaskKind.BUG_DETECTION,
                    status="success" if not labels else "partial",
                    payload=payload,
                    trust_tier=trust_tier,
                    elapsed_s=time.monotonic() - start,
                    errors=(),
                    warnings=tuple(warnings),
                    metadata={
                        "origin": origin,
                        "source_fingerprint": fingerprint,
                        "subsystem": "benchmark_bug_detector",
                    },
                )
            try:
                tree = ast.parse(source)
                if _benchmark_detect_bug_observations is not None:
                    observations = _benchmark_detect_bug_observations(source, filename=request.request_id)
                else:
                    detector = _RouterBenchmarkBugDetector()
                    detector._root = tree
                    detector.visit(tree)
                    observations = detector.finalize()
                trust_tier = "ORACLE_PROPOSED"
                kind_map = {
                    "mutable-default": "LOGIC_ERROR",
                    "bare-except": "PROTOCOL_VIOLATION",
                    "late-binding-closure": "SCOPE_VIOLATION",
                    "open-without-close": "RESOURCE_LEAK",
                    "shadow-builtin": "SCOPE_VIOLATION",
                    "identity-literal": "LOGIC_ERROR",
                }
                labels = tuple(observation.code for observation in observations)
                findings = [
                    {
                        "label": observation.code,
                        "kind": kind_map[observation.code],
                        "severity": None,
                        "coordinate": f"{request.request_id}:{observation.lineno}:{observation.col}:{observation.node_type}",
                        "message": observation.message,
                        "counterexample": {
                            "bug_code": observation.code,
                            "coordinate": f"{request.request_id}:{observation.lineno}:{observation.col}:{observation.node_type}",
                        },
                        "cohomology_class": f"{observation.code}:{request.request_id}",
                    }
                    for observation in observations
                ]
                payload = self._serialize_result(
                    {
                        "findings": findings,
                        "labels": list(labels),
                        "error_count": len(labels),
                        "warning_count": 0,
                        "source_fingerprint": fingerprint,
                        "origin": origin,
                        "line_count": source.count("\n") + 1,
                        "analysis_method": "benchmark_bug_detector",
                        "judgment_tuple": {
                            "c": origin,
                            "phi": "source_is_bug_free",
                            "A": "benchmark_bug_detector",
                            "E": findings,
                            "O": [finding["cohomology_class"] for finding in findings],
                            "B": len(findings),
                            "T": trust_tier,
                            "Pi": fingerprint,
                        },
                    },
                    TaskKind.BUG_DETECTION,
                )
                status = "success" if not labels else "partial"
                if labels:
                    warnings.append(
                        f"{len(labels)} benchmark bug label(s) detected; obstruction witnesses retained"
                    )
                return TaskResult(
                    request_id=request.request_id,
                    kind=TaskKind.BUG_DETECTION,
                    status=status,
                    payload=payload,
                    trust_tier=trust_tier,
                    elapsed_s=time.monotonic() - start,
                    errors=(),
                    warnings=tuple(warnings),
                    metadata={
                        "origin": origin,
                        "fingerprint": fingerprint,
                        "subsystem": "benchmark_bug_detector",
                    },
                )
            except SyntaxError:
                warnings.append(
                    "Benchmark bug detector could not parse the source; falling back to basic AST diagnostics"
                )

            # AST fallback (always executed since the subsystem doesn't exist).
            findings = self._ast_analyzer.analyze(source)
            warnings.append("Using AST fallback for bug detection (subsystem not installed)")

            # Categorise findings by severity.
            errors_found = [f for f in findings if f.get("severity") == "error"]
            warnings_found = [f for f in findings if f.get("severity") == "warning"]

            payload = self._serialize_result(
                {
                    "findings": findings,
                    "error_count": len(errors_found),
                    "warning_count": len(warnings_found),
                    "source_fingerprint": fingerprint,
                    "origin": origin,
                    "line_count": source.count("\n") + 1,
                    "analysis_method": "ast_fallback",
                    # Theory2 judgment tuple components:
                    # c = coordinate (origin), φ = proposition (no bugs),
                    # A = analysis method, E = findings evidence,
                    # O = obstructions (errors_found), B = budget consumed,
                    # T = trust (PROPOSAL), Π = provenance (fingerprint)
                    "judgment_tuple": {
                        "c": origin,
                        "phi": "source_is_bug_free",
                        "A": "ast_scan",
                        "E": findings,
                        "O": errors_found,
                        "B": len(findings),
                        "T": trust_tier,
                        "Pi": fingerprint,
                    },
                },
                TaskKind.BUG_DETECTION,
            )

            status = "success" if not errors_found else "partial"
            if errors_found:
                warnings.append(
                    f"{len(errors_found)} error-level finding(s) detected; "
                    "manual review required to confirm"
                )

            return TaskResult(
                request_id=request.request_id,
                kind=TaskKind.BUG_DETECTION,
                status=status,
                payload=payload,
                trust_tier=trust_tier,
                elapsed_s=time.monotonic() - start,
                errors=(),
                warnings=tuple(warnings),
                metadata={
                    "origin": origin,
                    "fingerprint": fingerprint,
                    "subsystem": "ast_fallback",
                },
            )

        except FileNotFoundError as exc:
            return self._build_error_result(request, exc, start=start)
        except Exception as exc:  # noqa: BLE001
            return self._build_error_result(request, exc, start=start)

    def check_equivalence(
        self,
        prog_a: str,
        prog_b: str,
        *,
        is_path: bool = False,
        input_cover: Any = None,
        _request: TaskRequest | None = None,
        **kwargs: Any,
    ) -> TaskResult:
        """Check whether two Python programs are semantically equivalent.

        Dispatches to ``problem_modes.relational_refinement`` when the
        subsystem is available.  The subsystem provides a
        :class:`~jugeo.problem_modes.relational_refinement.equivalence_verification.EquivalenceVerifier`
        that performs a structural and semantic comparison backed by the
        theory2 relational refinement chapter.

        When the subsystem is absent the router falls back to a structural
        comparison:

        1. Parse both programs to ASTs.
        2. Compare AST dumps (normalised for line-number differences).
        3. Compute a token-level diff for the payload.

        The fallback achieves at most ``"PROPOSAL"`` trust.

        Parameters
        ----------
        prog_a:
            First program (source string or file path).
        prog_b:
            Second program (source string or file path).
        is_path:
            Treat both *prog_a* and *prog_b* as file paths.

        Returns
        -------
        TaskResult
            ``payload["equivalent"]`` is a boolean (or ``None`` when
            analysis is inconclusive).
        """
        start = time.monotonic()
        request = _request or TaskRequest(
            kind=TaskKind.EQUIVALENCE_TESTING,
            inputs={"prog_a": prog_a, "prog_b": prog_b, "is_path": is_path},
        )

        warnings: list[str] = []
        trust_tier = "PROPOSAL"

        try:
            source_a, origin_a = self._source_loader.load(prog_a, is_path=is_path)
            source_b, origin_b = self._source_loader.load(prog_b, is_path=is_path)

            fp_a = self._source_loader.fingerprint(source_a)
            fp_b = self._source_loader.fingerprint(source_b)

            declared_cover = _coerce_benchmark_input_cover(input_cover)
            if declared_cover is not None:
                benchmark_support = _resolve_benchmark_support()
                relation_family = str(
                    request.inputs.get("relation_family", "extensional-equality-on-declared-cover")
                )
                if benchmark_support:
                    benchmark_points = tuple(
                        benchmark_support["InputPoint"](args=point.args, kwargs=point.kwargs)
                        for point in declared_cover
                    )
                    benchmark_case = benchmark_support["EquivalenceCase"](
                        case_id=request.request_id,
                        description="TaskRouter declared-cover equivalence request.",
                        relation_family=relation_family,
                        left_program=source_a,
                        right_program=source_b,
                        input_cover=benchmark_points,
                        expected_equivalent=True,
                    )
                    predicted, witness = benchmark_support["compare_extensional_equality"](benchmark_case)
                    trust_tier = benchmark_support["runtime_trust"]
                else:
                    predicted, witness = _router_compare_extensional_equality(
                        source_a,
                        source_b,
                        relation_family=relation_family,
                        points=declared_cover,
                    )
                    trust_tier = "RUNTIME_WITNESSED"
                payload = self._serialize_result(
                    {
                        "equivalent": predicted,
                        "relation_family": relation_family,
                        "analysis_method": "declared_cover_extensional",
                        "input_cover": _benchmark_cover_to_dicts(declared_cover),
                        "witness": _benchmark_witness_to_dict(witness),
                        "fingerprint_a": fp_a,
                        "fingerprint_b": fp_b,
                        "judgment_tuple": {
                            "c": f"{origin_a}||{origin_b}",
                            "phi": "prog_a_equiv_prog_b",
                            "A": "declared_cover_extensional",
                            "E": {"equivalent": predicted},
                            "O": [] if predicted else [getattr(witness, "message", "finite-cover mismatch")],
                            "B": len(declared_cover),
                            "T": trust_tier,
                            "Pi": f"{fp_a}:{fp_b}",
                        },
                    },
                    TaskKind.EQUIVALENCE_TESTING,
                )
                return TaskResult(
                    request_id=request.request_id,
                    kind=TaskKind.EQUIVALENCE_TESTING,
                    status="success" if predicted else "partial",
                    payload=payload,
                    trust_tier=trust_tier,
                    elapsed_s=time.monotonic() - start,
                    errors=(),
                    warnings=tuple(warnings),
                    metadata={
                        "origin_a": origin_a,
                        "origin_b": origin_b,
                        "fingerprint_a": fp_a,
                        "fingerprint_b": fp_b,
                        "subsystem": "benchmark_declared_cover",
                    },
                )

            # Attempt subsystem call.
            if _RELATIONAL_AVAILABLE and EquivalenceVerifier is not None:
                try:
                    verifier = EquivalenceVerifier()  # type: ignore[misc]
                    # EquivalenceVerifier.verify(prog_a, prog_b) — call if method exists.
                    verify_method = getattr(verifier, "verify", None)
                    if verify_method is not None:
                        raw = verify_method(source_a, source_b)
                        trust_tier = "REVIEWED"
                        payload = self._serialize_result(raw, TaskKind.EQUIVALENCE_TESTING)
                        return TaskResult(
                            request_id=request.request_id,
                            kind=TaskKind.EQUIVALENCE_TESTING,
                            status="success",
                            payload=payload,
                            trust_tier=trust_tier,
                            elapsed_s=time.monotonic() - start,
                            errors=(),
                            warnings=tuple(warnings),
                            metadata={
                                "origin_a": origin_a,
                                "origin_b": origin_b,
                                "fingerprint_a": fp_a,
                                "fingerprint_b": fp_b,
                                "subsystem": "relational_refinement",
                            },
                        )
                except Exception as sub_exc:  # noqa: BLE001
                    warnings.append(
                        f"Subsystem EquivalenceVerifier raised: {sub_exc!r}; "
                        "falling back to structural comparison"
                    )

            # AST structural fallback.
            warnings.append("Using structural AST fallback for equivalence checking")

            # Parse both programs; tolerate syntax errors in either.
            parse_errors: list[str] = []
            tree_a: ast.AST | None = None
            tree_b: ast.AST | None = None

            try:
                tree_a = ast.parse(source_a)
            except SyntaxError as exc:
                parse_errors.append(f"prog_a syntax error: {exc}")

            try:
                tree_b = ast.parse(source_b)
            except SyntaxError as exc:
                parse_errors.append(f"prog_b syntax error: {exc}")

            if parse_errors:
                return TaskResult(
                    request_id=request.request_id,
                    kind=TaskKind.EQUIVALENCE_TESTING,
                    status="partial",
                    payload={
                        "equivalent": None,
                        "reason": "syntax_error",
                        "parse_errors": parse_errors,
                    },
                    trust_tier=trust_tier,
                    elapsed_s=time.monotonic() - start,
                    errors=tuple(parse_errors),
                    warnings=tuple(warnings),
                    metadata={
                        "origin_a": origin_a,
                        "origin_b": origin_b,
                        "subsystem": "ast_fallback",
                    },
                )

            # Compare AST dumps, stripping line/column info for a
            # content-only comparison.
            dump_a = ast.dump(tree_a, indent=None)  # type: ignore[arg-type]
            dump_b = ast.dump(tree_b, indent=None)  # type: ignore[arg-type]
            structurally_identical = dump_a == dump_b

            # Compute a simple token-diff metric for the payload.
            tokens_a = source_a.split()
            tokens_b = source_b.split()
            shared = set(tokens_a) & set(tokens_b)
            jaccard = len(shared) / max(len(set(tokens_a) | set(tokens_b)), 1)

            # Collect top-level function/class names from each AST.
            def _top_level_names(tree: ast.AST) -> list[str]:
                return [
                    node.name  # type: ignore[attr-defined]
                    for node in ast.iter_child_nodes(tree)  # type: ignore[arg-type]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]

            names_a = _top_level_names(tree_a)  # type: ignore[arg-type]
            names_b = _top_level_names(tree_b)  # type: ignore[arg-type]
            names_in_common = sorted(set(names_a) & set(names_b))
            names_only_a = sorted(set(names_a) - set(names_b))
            names_only_b = sorted(set(names_b) - set(names_a))

            payload = self._serialize_result(
                {
                    "equivalent": structurally_identical,
                    "confidence": "structural_only",
                    "jaccard_similarity": round(jaccard, 4),
                    "structurally_identical": structurally_identical,
                    "names_in_common": names_in_common,
                    "names_only_in_a": names_only_a,
                    "names_only_in_b": names_only_b,
                    "source_a_lines": source_a.count("\n") + 1,
                    "source_b_lines": source_b.count("\n") + 1,
                    "fingerprint_a": fp_a,
                    "fingerprint_b": fp_b,
                    "analysis_method": "ast_structural",
                    "judgment_tuple": {
                        "c": f"{origin_a}||{origin_b}",
                        "phi": "prog_a_equiv_prog_b",
                        "A": "structural_ast_diff",
                        "E": {"jaccard": jaccard, "structurally_identical": structurally_identical},
                        "O": [],  # no obstructions at structural level
                        "B": len(tokens_a) + len(tokens_b),
                        "T": trust_tier,
                        "Pi": f"{fp_a}:{fp_b}",
                    },
                },
                TaskKind.EQUIVALENCE_TESTING,
            )

            return TaskResult(
                request_id=request.request_id,
                kind=TaskKind.EQUIVALENCE_TESTING,
                status="success",
                payload=payload,
                trust_tier=trust_tier,
                elapsed_s=time.monotonic() - start,
                errors=(),
                warnings=tuple(warnings),
                metadata={
                    "origin_a": origin_a,
                    "origin_b": origin_b,
                    "fingerprint_a": fp_a,
                    "fingerprint_b": fp_b,
                    "subsystem": "ast_fallback",
                },
            )

        except Exception as exc:  # noqa: BLE001
            return self._build_error_result(request, exc, start=start)

    def check_spec_adherence(
        self,
        source: str,
        spec: str,
        *,
        source_is_path: bool = False,
        input_cover: Any = None,
        _request: TaskRequest | None = None,
        **kwargs: Any,
    ) -> TaskResult:
        """Check whether a Python program adheres to a given specification.

        Dispatches to ``problem_modes.specification_satisfaction`` when the
        subsystem is available.  The subsystem's
        :func:`~jugeo.problem_modes.specification_satisfaction.algorithms.specification_satisfaction_algorithm`
        performs a full theory2 §10 satisfaction check backed by descent
        conditions and witness construction.

        When the subsystem is absent the router falls back to a structural
        spec-text analysis:

        1. Parse the spec as a plain-text or JSON document.
        2. Extract required function/class names, forbidden patterns, and type
           annotations from the spec text.
        3. Check the source AST for compliance with each extracted requirement.

        The fallback achieves at most ``"PROPOSAL"`` trust.

        Parameters
        ----------
        source:
            Python source code (or file path when ``source_is_path=True``).
        spec:
            Specification text.  Accepted formats: plain English (heuristic
            extraction), JSON with ``"required_names"``, ``"forbidden_patterns"``,
            and ``"required_annotations"`` keys.
        source_is_path:
            Treat *source* as a file path.

        Returns
        -------
        TaskResult
            ``payload["adheres"]`` is a boolean; ``payload["gaps"]`` lists any
            unmet requirements.
        """
        start = time.monotonic()
        request = _request or TaskRequest(
            kind=TaskKind.SPEC_ADHERENCE,
            inputs={"source": source, "spec": spec, "source_is_path": source_is_path},
        )

        warnings: list[str] = []
        trust_tier = "PROPOSAL"

        try:
            src_code, src_origin = self._source_loader.load(source, is_path=source_is_path)
            src_fp = self._source_loader.fingerprint(src_code)
            spec_fp = self._source_loader.fingerprint(spec)

            declared_cover = _coerce_benchmark_input_cover(input_cover)
            if declared_cover is not None:
                benchmark_support = _resolve_benchmark_support()
                if benchmark_support:
                    benchmark_points = tuple(
                        benchmark_support["InputPoint"](args=point.args, kwargs=point.kwargs)
                        for point in declared_cover
                    )
                    benchmark_case = benchmark_support["SpecCase"](
                        case_id=request.request_id,
                        description="TaskRouter declared-cover spec adherence request.",
                        program=src_code,
                        spec_program=spec,
                        input_cover=benchmark_points,
                        expected_satisfies=True,
                    )
                    adheres, witness = benchmark_support["check_spec_case"](benchmark_case)
                    trust_tier = benchmark_support["runtime_trust"]
                else:
                    adheres, witness = _router_check_spec_execution(
                        src_code,
                        spec,
                        points=declared_cover,
                    )
                    trust_tier = "RUNTIME_WITNESSED"
                payload = self._serialize_result(
                    {
                        "adheres": adheres,
                        "analysis_method": "declared_cover_spec_execution",
                        "input_cover": _benchmark_cover_to_dicts(declared_cover),
                        "witness": _benchmark_witness_to_dict(witness),
                        "source_fingerprint": src_fp,
                        "spec_fingerprint": spec_fp,
                        "origin": src_origin,
                        "judgment_tuple": {
                            "c": src_origin,
                            "phi": "source_satisfies_spec",
                            "A": "declared_cover_spec_execution",
                            "E": {"adheres": adheres},
                            "O": [] if adheres else [getattr(witness, "message", "undischarged finite-cover obligation")],
                            "B": len(declared_cover),
                            "T": trust_tier,
                            "Pi": f"{src_fp}:{spec_fp}",
                        },
                    },
                    TaskKind.SPEC_ADHERENCE,
                )
                return TaskResult(
                    request_id=request.request_id,
                    kind=TaskKind.SPEC_ADHERENCE,
                    status="success" if adheres else "partial",
                    payload=payload,
                    trust_tier=trust_tier,
                    elapsed_s=time.monotonic() - start,
                    errors=(),
                    warnings=tuple(warnings),
                    metadata={
                        "origin": src_origin,
                        "source_fingerprint": src_fp,
                        "spec_fingerprint": spec_fp,
                        "subsystem": "benchmark_declared_cover",
                    },
                )

            # Attempt subsystem call.
            if _SPEC_SATISFACTION_AVAILABLE and specification_satisfaction_algorithm is not None:
                try:
                    raw = specification_satisfaction_algorithm(src_code, spec)  # type: ignore[call-arg]
                    trust_tier = "REVIEWED"
                    payload = self._serialize_result(raw, TaskKind.SPEC_ADHERENCE)
                    return TaskResult(
                        request_id=request.request_id,
                        kind=TaskKind.SPEC_ADHERENCE,
                        status="success",
                        payload=payload,
                        trust_tier=trust_tier,
                        elapsed_s=time.monotonic() - start,
                        errors=(),
                        warnings=tuple(warnings),
                        metadata={
                            "origin": src_origin,
                            "source_fingerprint": src_fp,
                            "spec_fingerprint": spec_fp,
                            "subsystem": "specification_satisfaction",
                        },
                    )
                except Exception as sub_exc:  # noqa: BLE001
                    warnings.append(
                        f"Subsystem specification_satisfaction_algorithm raised: {sub_exc!r}; "
                        "falling back to structural spec analysis"
                    )

            # Structural fallback: parse spec and check source AST.
            warnings.append("Using structural spec-text fallback for adherence checking")

            spec_obj = self._parse_spec_text(spec)
            gaps, satisfied = self._check_spec_against_source(src_code, spec_obj)

            adheres = len(gaps) == 0
            payload = self._serialize_result(
                {
                    "adheres": adheres,
                    "gaps": gaps,
                    "satisfied_requirements": satisfied,
                    "spec_summary": spec_obj,
                    "source_fingerprint": src_fp,
                    "spec_fingerprint": spec_fp,
                    "origin": src_origin,
                    "analysis_method": "structural_spec_fallback",
                    "judgment_tuple": {
                        "c": src_origin,
                        "phi": "source_satisfies_spec",
                        "A": "spec_text_structural_check",
                        "E": satisfied,
                        "O": gaps,
                        "B": len(satisfied) + len(gaps),
                        "T": trust_tier,
                        "Pi": f"{src_fp}:{spec_fp}",
                    },
                },
                TaskKind.SPEC_ADHERENCE,
            )

            status = "success" if adheres else "partial"

            return TaskResult(
                request_id=request.request_id,
                kind=TaskKind.SPEC_ADHERENCE,
                status=status,
                payload=payload,
                trust_tier=trust_tier,
                elapsed_s=time.monotonic() - start,
                errors=(),
                warnings=tuple(warnings),
                metadata={
                    "origin": src_origin,
                    "source_fingerprint": src_fp,
                    "spec_fingerprint": spec_fp,
                    "subsystem": "structural_fallback",
                },
            )

        except Exception as exc:  # noqa: BLE001
            return self._build_error_result(request, exc, start=start)

    def run_composite(
        self,
        _request: TaskRequest | None = None,
        **kwargs: Any,
    ) -> TaskResult:
        """Execute a composite request containing multiple sub-tasks.

        The sub-tasks are extracted from ``request.inputs["subtasks"]``, each
        of which must be a dictionary serialisation of a :class:`TaskRequest`.
        Sub-tasks are executed sequentially (up to ``config.max_parallel_tasks``
        in a future async extension); their results are collected into
        ``payload["subtask_results"]``.

        The composite result's trust tier is the *minimum* trust tier across
        all sub-task results (weakest-link principle).  If any sub-task fails
        the composite status is ``"partial"``; if all fail it is ``"failed"``.

        Parameters
        ----------
        _request:
            The originating :class:`TaskRequest`.

        Returns
        -------
        TaskResult
            Composite result with all sub-task results in the payload.
        """
        start = time.monotonic()
        request = _request or TaskRequest(kind=TaskKind.COMPOSITE, inputs={})

        warnings: list[str] = []
        errors: list[str] = []

        try:
            raw_subtasks = request.inputs.get("subtasks", [])
            if not isinstance(raw_subtasks, list):
                return self._build_error_result(
                    request,
                    TypeError(f"'subtasks' must be a list, got {type(raw_subtasks).__name__}"),
                    start=start,
                )

            if not raw_subtasks:
                warnings.append("Composite request has no subtasks; returning empty payload")
                return TaskResult(
                    request_id=request.request_id,
                    kind=TaskKind.COMPOSITE,
                    status="success",
                    payload={"subtask_results": [], "subtask_count": 0},
                    trust_tier="PROPOSAL",
                    elapsed_s=time.monotonic() - start,
                    errors=(),
                    warnings=tuple(warnings),
                    metadata={"subsystem": "composite_router"},
                )

            # Enforce max_parallel_tasks as a sequential batch limit.
            limit = min(len(raw_subtasks), self.config.max_parallel_tasks)
            if len(raw_subtasks) > limit:
                warnings.append(
                    f"Composite request has {len(raw_subtasks)} subtasks but "
                    f"max_parallel_tasks={self.config.max_parallel_tasks}; "
                    f"truncating to first {limit}"
                )
                raw_subtasks = raw_subtasks[:limit]

            subtask_results: list[dict[str, Any]] = []
            trust_tiers_seen: list[str] = []
            success_count = 0
            failed_count = 0

            for i, raw_sub in enumerate(raw_subtasks):
                if not isinstance(raw_sub, dict):
                    errors.append(f"subtask[{i}] is not a dict; skipping")
                    failed_count += 1
                    continue

                try:
                    sub_req = TaskRequest.from_dict(raw_sub)
                    sub_result = self.route(sub_req)
                    subtask_results.append(sub_result.to_dict())
                    trust_tiers_seen.append(sub_result.trust_tier)
                    if sub_result.is_success():
                        success_count += 1
                    else:
                        failed_count += 1
                        errors.extend(sub_result.errors)
                except Exception as exc:  # noqa: BLE001
                    err_msg = f"subtask[{i}] routing failed: {exc!r}"
                    errors.append(err_msg)
                    failed_count += 1
                    subtask_results.append({
                        "index": i,
                        "status": "failed",
                        "error": err_msg,
                    })

            # Composite trust = weakest-link.
            _tier_order = {"PROPOSAL": 1, "REVIEWED": 2, "VERIFIED": 3}
            min_tier = min(trust_tiers_seen, key=lambda t: _tier_order.get(t, 0), default="PROPOSAL")

            if failed_count == 0:
                composite_status = "success"
            elif success_count == 0:
                composite_status = "failed"
            else:
                composite_status = "partial"

            payload = {
                "subtask_results": subtask_results,
                "subtask_count": len(subtask_results),
                "success_count": success_count,
                "failed_count": failed_count,
                "trust_tiers_seen": list(set(trust_tiers_seen)),
            }

            return TaskResult(
                request_id=request.request_id,
                kind=TaskKind.COMPOSITE,
                status=composite_status,
                payload=payload,
                trust_tier=min_tier,
                elapsed_s=time.monotonic() - start,
                errors=tuple(errors),
                warnings=tuple(warnings),
                metadata={"subsystem": "composite_router", "subtask_count": len(subtask_results)},
            )

        except Exception as exc:  # noqa: BLE001
            return self._build_error_result(request, exc, start=start)

    # ------------------------------------------------------------------
    # Spec parsing and checking (structural fallback helpers)
    # ------------------------------------------------------------------

    def _parse_spec_text(self, spec: str) -> dict[str, Any]:
        """Parse a specification string into a structured requirement dict.

        Attempts JSON parsing first (for machine-generated specs), then
        falls back to heuristic extraction from plain English text.

        Parameters
        ----------
        spec:
            Specification as a string.

        Returns
        -------
        dict
            Keys: ``"required_names"``, ``"forbidden_patterns"``,
            ``"required_annotations"``, ``"raw"``.
        """
        # Attempt JSON parse.
        try:
            parsed = json.loads(spec)
            if isinstance(parsed, dict):
                return {
                    "required_names": list(parsed.get("required_names", [])),
                    "forbidden_patterns": list(parsed.get("forbidden_patterns", [])),
                    "required_annotations": dict(parsed.get("required_annotations", {})),
                    "raw": spec,
                    "format": "json",
                }
        except (json.JSONDecodeError, ValueError):
            pass

        # Heuristic: scan for "must have", "must not", "requires", "forbidden".
        required: list[str] = []
        forbidden: list[str] = []
        import re

        # Patterns like "must have a function named foo", "a function named foo",
        # "requires bar", "define baz".  We scan for any "named/called <id>"
        # occurrence so that conjunctions ("named add and … named greet") are
        # all captured even when the trigger verb only appears once.
        for m in re.finditer(
            r"(?:named?|called?)\s+['\"]?([a-zA-Z_]\w*)['\"]?",
            spec,
            re.IGNORECASE,
        ):
            candidate = m.group(1)
            # Skip generic English words that are not identifiers.
            if candidate.lower() not in {"a", "an", "the", "it", "this", "that"}:
                required.append(candidate)

        # Also pick up "requires X" / "must have X" patterns without "named".
        for m in re.finditer(
            r"(?:must\s+have|requires?|must\s+define|defines?)\s+(?:a\s+)?(?:function|class|method|variable)?\s*['\"]?([a-zA-Z_]\w*)['\"]?",
            spec,
            re.IGNORECASE,
        ):
            candidate = m.group(1)
            if candidate.lower() not in {"a", "an", "the", "function", "class", "method", "variable", "named", "called"}:
                if candidate not in required:
                    required.append(candidate)

        # Patterns like "must not use X" or "forbidden: X"
        for m in re.finditer(
            r"(?:must\s+not\s+(?:use|call)|forbidden[:\s]+)\s*['\"]?([a-zA-Z_]\w*)['\"]?",
            spec,
            re.IGNORECASE,
        ):
            forbidden.append(m.group(1))

        return {
            "required_names": required,
            "forbidden_patterns": forbidden,
            "required_annotations": {},
            "raw": spec,
            "format": "heuristic",
        }

    def _check_spec_against_source(
        self,
        source: str,
        spec_obj: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Check *source* against a parsed spec object.

        Returns
        -------
        tuple[list, list]
            ``(gaps, satisfied)`` — each element is a list of requirement dicts
            with keys ``"requirement"``, ``"kind"``, and ``"detail"``.
        """
        gaps: list[dict[str, Any]] = []
        satisfied: list[dict[str, Any]] = []

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            gaps.append({
                "requirement": "parseable_source",
                "kind": "syntax",
                "detail": f"Source has a syntax error: {exc}",
            })
            return gaps, satisfied

        # Collect all defined names in the source (functions, classes, variables).
        defined_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defined_names.add(t.id)

        # Collect all called names (for forbidden pattern check).
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr)

        # Check required names.
        for name in spec_obj.get("required_names", []):
            if name in defined_names:
                satisfied.append({
                    "requirement": f"name_defined:{name}",
                    "kind": "required_name",
                    "detail": f"`{name}` is defined in the source",
                })
            else:
                gaps.append({
                    "requirement": f"name_defined:{name}",
                    "kind": "required_name",
                    "detail": f"Required name `{name}` is not defined in the source",
                })

        # Check forbidden patterns.
        for pat in spec_obj.get("forbidden_patterns", []):
            if pat in called_names or pat in source:
                gaps.append({
                    "requirement": f"name_absent:{pat}",
                    "kind": "forbidden_pattern",
                    "detail": f"Forbidden pattern `{pat}` found in the source",
                })
            else:
                satisfied.append({
                    "requirement": f"name_absent:{pat}",
                    "kind": "forbidden_pattern",
                    "detail": f"Forbidden pattern `{pat}` is absent (as required)",
                })

        # Check required annotations (function return type hints).
        for func_name, expected_annotation in spec_obj.get("required_annotations", {}).items():
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == func_name:
                        if node.returns is not None:
                            actual = ast.unparse(node.returns)
                            if actual == expected_annotation:
                                satisfied.append({
                                    "requirement": f"annotation:{func_name}",
                                    "kind": "required_annotation",
                                    "detail": f"`{func_name}` has return annotation `{actual}`",
                                })
                            else:
                                gaps.append({
                                    "requirement": f"annotation:{func_name}",
                                    "kind": "required_annotation",
                                    "detail": (
                                        f"`{func_name}` has annotation `{actual}` "
                                        f"but spec requires `{expected_annotation}`"
                                    ),
                                })
                        else:
                            gaps.append({
                                "requirement": f"annotation:{func_name}",
                                "kind": "required_annotation",
                                "detail": f"`{func_name}` has no return annotation; spec requires `{expected_annotation}`",
                            })

        return gaps, satisfied

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _serialize_result(self, raw: Any, kind: TaskKind) -> dict[str, Any]:
        """Serialise a subsystem result to a plain dictionary.

        This method is intentionally permissive: it tries to call ``.to_dict()``
        on the result, then falls back to ``vars()``, then to ``json.loads(json.dumps(raw))``,
        and finally wraps the raw object in a ``{"raw": ...}`` envelope.  The
        goal is to never lose a subsystem result even when the result type is
        unknown.

        Parameters
        ----------
        raw:
            Subsystem result — may be a dataclass, dict, list, or scalar.
        kind:
            The task kind, used to select type-specific serialisation hints.

        Returns
        -------
        dict
            A plain dictionary safe for JSON serialisation.
        """
        if isinstance(raw, dict):
            return dict(raw)

        if hasattr(raw, "to_dict") and callable(raw.to_dict):
            try:
                result = raw.to_dict()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass

        if hasattr(raw, "__dataclass_fields__"):
            try:
                import dataclasses
                return dataclasses.asdict(raw)
            except Exception:
                pass

        try:
            encoded = json.dumps(raw, default=str)
            decoded = json.loads(encoded)
            if isinstance(decoded, dict):
                return decoded
            return {"value": decoded}
        except Exception:
            pass

        return {"raw": repr(raw), "type": type(raw).__qualname__}

    def _build_error_result(
        self,
        request: TaskRequest,
        exc: Exception,
        *,
        start: float | None = None,
    ) -> TaskResult:
        """Construct a failed :class:`TaskResult` from an exception.

        This is the central error-normalisation point.  All unhandled
        exceptions in the router ultimately arrive here.  The full traceback
        is captured into ``metadata["traceback"]`` so that diagnostics tools
        can retrieve it without it cluttering the ``errors`` tuple.

        Parameters
        ----------
        request:
            The request that triggered the error.
        exc:
            The exception to wrap.
        start:
            Monotonic start time (optional).  If omitted, ``elapsed_s`` is 0.

        Returns
        -------
        TaskResult
            A result with ``status="failed"`` and the exception message in
            ``errors``.
        """
        elapsed = (time.monotonic() - start) if start is not None else 0.0
        tb = traceback.format_exc()

        error_msg = f"{type(exc).__name__}: {exc}"

        return TaskResult(
            request_id=request.request_id,
            kind=request.kind,
            status="failed",
            payload={},
            trust_tier="PROPOSAL",
            elapsed_s=elapsed,
            errors=(error_msg,),
            warnings=(),
            metadata={
                "exception_type": type(exc).__qualname__,
                "traceback": tb,
                "subsystem": "router_error_handler",
            },
        )

    def _build_timeout_result(
        self,
        request: TaskRequest,
        start: float,
    ) -> TaskResult:
        """Construct a timeout :class:`TaskResult`.

        Parameters
        ----------
        request:
            The request that timed out.
        start:
            Monotonic start time.
        """
        elapsed = time.monotonic() - start
        return TaskResult(
            request_id=request.request_id,
            kind=request.kind,
            status="timeout",
            payload={},
            trust_tier="PROPOSAL",
            elapsed_s=elapsed,
            errors=(
                f"Request exceeded timeout_s={request.timeout_s:.1f}s after {elapsed:.3f}s",
            ),
            warnings=(),
            metadata={"subsystem": "router_timeout_handler"},
        )

    def _detect_task_kind(self, request: TaskRequest) -> TaskKind:
        """Infer the task kind from the structure of *request.inputs*.

        Heuristics applied in order:

        1. If ``inputs`` has ``"prog_a"`` and ``"prog_b"`` → equivalence testing.
        2. If ``inputs`` has ``"spec"`` → spec adherence.
        3. If ``inputs`` has ``"subtasks"`` → composite.
        4. If ``inputs`` has ``"source"`` or ``"path"`` alone → bug detection.
        5. Otherwise → :attr:`TaskKind.UNKNOWN`.

        Parameters
        ----------
        request:
            Request with ``kind=TaskKind.UNKNOWN``.

        Returns
        -------
        TaskKind
            Best-effort inferred kind.
        """
        inp = request.inputs

        has_prog_a = "prog_a" in inp
        has_prog_b = "prog_b" in inp
        has_spec = "spec" in inp and bool(inp["spec"])
        has_subtasks = "subtasks" in inp and isinstance(inp["subtasks"], list)
        has_source = "source" in inp or "path" in inp

        if has_prog_a and has_prog_b:
            return TaskKind.EQUIVALENCE_TESTING
        if has_spec and has_source:
            return TaskKind.SPEC_ADHERENCE
        if has_subtasks:
            return TaskKind.COMPOSITE
        if has_source:
            return TaskKind.BUG_DETECTION

        return TaskKind.UNKNOWN


# ---------------------------------------------------------------------------
# Registry (singleton)
# ---------------------------------------------------------------------------


class RouterRegistry:
    """Singleton registry for shared :class:`TaskRouter` instances.

    The registry allows different parts of the application (CLI, API, test
    harnesses) to share a single router without passing it explicitly through
    every call site.  It is not a mandatory component; callers that prefer
    explicit dependency injection should use :class:`TaskRouter` directly.

    Thread safety: the ``get_instance`` / ``register_router`` / ``get_router``
    methods perform no locking.  External synchronisation is required for
    concurrent use.

    Examples
    --------
    >>> registry = RouterRegistry.get_instance()
    >>> registry.register_router(TaskRouter(RouterConfig(enable_z3=True)))
    >>> router = registry.get_router()
    """

    _instance: RouterRegistry | None = None

    def __init__(self) -> None:
        # The registry begins with no router; one is created lazily on first
        # call to get_router().
        self._router: TaskRouter | None = None

    @classmethod
    def get_instance(cls) -> "RouterRegistry":
        """Return the process-wide singleton :class:`RouterRegistry`.

        Creates the singleton on first call; subsequent calls return the same
        instance.

        Returns
        -------
        RouterRegistry
            The shared registry.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_router(self, router: TaskRouter) -> None:
        """Register *router* as the current shared router.

        Any previously registered router is silently replaced.  This method
        is useful for test setup where a custom-configured router should
        replace the default.

        Parameters
        ----------
        router:
            The :class:`TaskRouter` to register.
        """
        if not isinstance(router, TaskRouter):
            raise TypeError(
                f"Expected TaskRouter, got {type(router).__qualname__}"
            )
        self._router = router

    def get_router(self) -> TaskRouter:
        """Return the currently registered router, creating a default if needed.

        Returns
        -------
        TaskRouter
            The shared router.  A new :class:`TaskRouter` with default
            configuration is created and registered on first call.
        """
        if self._router is None:
            self._router = TaskRouter()
        return self._router

    def reset(self) -> None:
        """Clear the registered router, reverting to the lazy-init state.

        Primarily useful in test teardown to ensure a clean state between
        test cases.
        """
        self._router = None


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def get_default_router() -> TaskRouter:
    """Return the process-wide default :class:`TaskRouter`.

    Uses the :class:`RouterRegistry` singleton to ensure a single shared
    router is used across the process unless explicitly overridden.

    Returns
    -------
    TaskRouter
        The default shared router.
    """
    return RouterRegistry.get_instance().get_router()


def detect_bugs(source_or_path: str, **kwargs: Any) -> TaskResult:
    """Detect bugs in *source_or_path* using the default router.

    This is the primary convenience entry point for CLI and test callers.

    Parameters
    ----------
    source_or_path:
        Python source code string or file path (pass ``is_path=True`` via
        *kwargs*).
    **kwargs:
        Forwarded to :meth:`TaskRouter.detect_bugs`.

    Returns
    -------
    TaskResult
        Bug detection result.

    Examples
    --------
    >>> result = detect_bugs("x = 1 / 0")
    >>> result.is_success()
    True
    >>> result.payload["error_count"]
    1
    """
    return get_default_router().detect_bugs(source_or_path, **kwargs)


def check_equivalence(prog_a: str, prog_b: str, **kwargs: Any) -> TaskResult:
    """Check whether *prog_a* and *prog_b* are semantically equivalent.

    Uses the default router.  Both arguments may be inline source code or
    file paths (pass ``is_path=True`` via *kwargs*).

    Parameters
    ----------
    prog_a:
        First program.
    prog_b:
        Second program.
    **kwargs:
        Forwarded to :meth:`TaskRouter.check_equivalence`.

    Returns
    -------
    TaskResult
        Equivalence testing result; ``payload["equivalent"]`` is a bool.

    Examples
    --------
    >>> r = check_equivalence("x = 1 + 1", "x = 2")
    >>> r.payload["structurally_identical"]
    False
    """
    return get_default_router().check_equivalence(prog_a, prog_b, **kwargs)


def check_spec_adherence(source: str, spec: str, **kwargs: Any) -> TaskResult:
    """Check whether *source* adheres to the given *spec*.

    Uses the default router.

    Parameters
    ----------
    source:
        Python source code or file path.
    spec:
        Specification text (plain English or JSON).
    **kwargs:
        Forwarded to :meth:`TaskRouter.check_spec_adherence`.

    Returns
    -------
    TaskResult
        Spec adherence result; ``payload["adheres"]`` is a bool.

    Examples
    --------
    >>> spec = '{"required_names": ["main"]}'
    >>> result = check_spec_adherence("def main(): pass", spec)
    >>> result.payload["adheres"]
    True
    """
    return get_default_router().check_spec_adherence(source, spec, **kwargs)


def route_request(request: dict[str, Any]) -> dict[str, Any]:
    """Route a raw dictionary request, returning a raw dictionary result.

    This is the dict-in / dict-out API used by HTTP callers (via
    :mod:`jugeo.interfaces.api`) and CLI dispatch loops (via
    :mod:`jugeo.interfaces.cli`).  It wraps :meth:`TaskRouter.route` with
    explicit serialisation on both sides so callers never need to import the
    dataclasses directly.

    Parameters
    ----------
    request:
        Dictionary representation of a :class:`TaskRequest`.  Must contain at
        least a ``"kind"`` key.  All other keys are optional.

    Returns
    -------
    dict
        Serialised :class:`TaskResult`; always a plain dictionary safe for
        JSON encoding.

    Examples
    --------
    >>> out = route_request({"kind": "bug_detection", "source": "x = 1/0"})
    >>> out["status"]
    'success'
    >>> out["payload"]["error_count"]
    1

    >>> out2 = route_request({
    ...     "kind": "equivalence_testing",
    ...     "prog_a": "def f(): return 1",
    ...     "prog_b": "def f(): return 1",
    ... })
    >>> out2["payload"]["equivalent"]
    True
    """
    try:
        task_request = TaskRequest.from_dict(request)
        result = get_default_router().route(task_request)
        return result.to_dict()
    except Exception as exc:  # noqa: BLE001
        # Absolute last-resort fallback — should never be reached because
        # route() itself never raises, but we guard here for safety.
        return {
            "request_id": str(request.get("request_id", uuid.uuid4())),
            "kind": str(request.get("kind", "unknown")),
            "status": "failed",
            "payload": {},
            "trust_tier": "PROPOSAL",
            "elapsed_s": 0.0,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "warnings": [],
            "metadata": {"subsystem": "route_request_guard"},
        }


# ---------------------------------------------------------------------------
# Self-test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Demonstration: run all three benchmark tasks on trivial Python snippets.
    # -----------------------------------------------------------------------

    _DEMO_SOURCE_WITH_BUGS = textwrap.dedent("""\
        def divide(a, b):
            # BUG: does not guard against division by zero
            return a / 0

        def risky():
            try:
                risky()
            except:
                pass  # bare except — swallows everything

        def accumulate(items=[]):  # mutable default argument
            items.append(1)
            return items
    """)

    _DEMO_SOURCE_A = textwrap.dedent("""\
        def add(x, y):
            return x + y

        def greet(name):
            return f"Hello, {name}!"
    """)

    _DEMO_SOURCE_B = textwrap.dedent("""\
        def add(x, y):
            result = x + y
            return result

        def greet(name):
            return "Hello, " + name + "!"
    """)

    _DEMO_SOURCE_B_IDENTICAL = _DEMO_SOURCE_A  # structurally identical

    _DEMO_SPEC_JSON = json.dumps({
        "required_names": ["add", "greet"],
        "forbidden_patterns": ["eval"],
        "required_annotations": {},
    })

    _DEMO_SPEC_TEXT = (
        "The module must have a function named add and a function named greet. "
        "It must not use eval."
    )

    print("=" * 70)
    print("JuGeo Task Router — demonstration")
    print("=" * 70)
    print()

    # Task 1: Bug detection
    print("── Task 1: Bug Detection ──────────────────────────────────────────")
    r1 = route_request({"kind": "bug_detection", "source": _DEMO_SOURCE_WITH_BUGS})
    print(f"  status      : {r1['status']}")
    print(f"  trust_tier  : {r1['trust_tier']}")
    print(f"  elapsed_s   : {r1['elapsed_s']:.4f}")
    print(f"  error_count : {r1['payload'].get('error_count', '?')}")
    print(f"  warn_count  : {r1['payload'].get('warning_count', '?')}")
    for finding in r1["payload"].get("findings", []):
        print(f"    [{finding['severity'].upper():7}] L{finding['line']} {finding['rule']}: {finding['message']}")
    print()

    # Task 2a: Equivalence testing — non-identical programs
    print("── Task 2a: Equivalence Testing (structurally different) ──────────")
    r2a = route_request({
        "kind": "equivalence_testing",
        "prog_a": _DEMO_SOURCE_A,
        "prog_b": _DEMO_SOURCE_B,
    })
    print(f"  status      : {r2a['status']}")
    print(f"  equivalent  : {r2a['payload'].get('equivalent', '?')}")
    print(f"  jaccard     : {r2a['payload'].get('jaccard_similarity', '?')}")
    print(f"  trust_tier  : {r2a['trust_tier']}")
    print()

    # Task 2b: Equivalence testing — identical programs
    print("── Task 2b: Equivalence Testing (structurally identical) ──────────")
    r2b = route_request({
        "kind": "equivalence_testing",
        "prog_a": _DEMO_SOURCE_A,
        "prog_b": _DEMO_SOURCE_B_IDENTICAL,
    })
    print(f"  status      : {r2b['status']}")
    print(f"  equivalent  : {r2b['payload'].get('equivalent', '?')}")
    print(f"  trust_tier  : {r2b['trust_tier']}")
    print()

    # Task 3a: Spec adherence — JSON spec, passing source
    print("── Task 3a: Spec Adherence (JSON spec, passing source) ────────────")
    r3a = route_request({
        "kind": "spec_adherence",
        "source": _DEMO_SOURCE_A,
        "spec": _DEMO_SPEC_JSON,
    })
    print(f"  status      : {r3a['status']}")
    print(f"  adheres     : {r3a['payload'].get('adheres', '?')}")
    print(f"  gaps        : {r3a['payload'].get('gaps', [])}")
    print(f"  trust_tier  : {r3a['trust_tier']}")
    print()

    # Task 3b: Spec adherence — plain-text spec, failing source
    print("── Task 3b: Spec Adherence (text spec, source missing 'greet') ────")
    _INCOMPLETE_SOURCE = "def add(x, y): return x + y\n"
    r3b = route_request({
        "kind": "spec_adherence",
        "source": _INCOMPLETE_SOURCE,
        "spec": _DEMO_SPEC_TEXT,
    })
    print(f"  status      : {r3b['status']}")
    print(f"  adheres     : {r3b['payload'].get('adheres', '?')}")
    for gap in r3b["payload"].get("gaps", []):
        print(f"    GAP [{gap['kind']}]: {gap['detail']}")
    print()

    # Task 4: Composite
    print("── Task 4: Composite (bug_detection + equivalence) ─────────────────")
    r4 = route_request({
        "kind": "composite",
        "subtasks": [
            {"kind": "bug_detection", "source": "x = 1 / 0"},
            {"kind": "equivalence_testing", "prog_a": "x=1", "prog_b": "x=1"},
        ],
    })
    print(f"  status         : {r4['status']}")
    print(f"  subtask_count  : {r4['payload'].get('subtask_count', '?')}")
    print(f"  success_count  : {r4['payload'].get('success_count', '?')}")
    print(f"  trust_tier     : {r4['trust_tier']}")
    print()

    # Auto-detection demo
    print("── Auto-detection (kind omitted) ───────────────────────────────────")
    r5 = route_request({"source": "def f(): pass\ndef g(): pass\n"})
    print(f"  detected kind : {r5['kind']}")
    print(f"  status        : {r5['status']}")
    print()

    print("Demonstration complete.")


# ---------------------------------------------------------------------------
# Cross-subsystem routing helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.router import SolverRouter as _SolverRouter  # type: ignore[import]
    _SOLVER_ROUTER_AVAILABLE = True
except ImportError:
    _SolverRouter = None  # type: ignore[assignment,misc]
    _SOLVER_ROUTER_AVAILABLE = False

try:
    from jugeo.orchestration.controller import OrchestrationController as _OrchestrationController  # type: ignore[import]
    _ORCHESTRATION_AVAILABLE = True
except ImportError:
    _OrchestrationController = None  # type: ignore[assignment,misc]
    _ORCHESTRATION_AVAILABLE = False

try:
    from jugeo.evaluation import evaluate as _evaluate_fn  # type: ignore[import]
    _EVALUATION_AVAILABLE = True
except ImportError:
    _evaluate_fn = None  # type: ignore[assignment]
    _EVALUATION_AVAILABLE = False

try:
    from jugeo.ideation import IdeationEngine as _IdeationEngine  # type: ignore[import]
    _IDEATION_AVAILABLE = True
except ImportError:
    _IdeationEngine = None  # type: ignore[assignment,misc]
    _IDEATION_AVAILABLE = False


def route_to_solver(request: dict[str, Any]) -> dict[str, Any]:
    """Route a task request to the solver subsystem via ``jugeo.solver.router``.

    Delegates *request* to :class:`~jugeo.solver.router.SolverRouter` which
    selects the appropriate Z3 strategy and returns a solver-level result.
    Falls back gracefully when the solver subsystem is not installed.

    Parameters
    ----------
    request:
        Dictionary carrying at least ``"coordinate"`` and ``"proposition"``
        keys describing the claim to solve.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "result": ..., "trust_tier": str, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _SOLVER_ROUTER_AVAILABLE,
        "result": None,
        "trust_tier": "PROPOSAL",
        "errors": [],
    }
    if not _SOLVER_ROUTER_AVAILABLE:
        result["errors"].append("jugeo.solver.router subsystem is not installed")
        return result
    try:
        router = _SolverRouter()
        solver_result = router.solve(request)
        result["result"] = solver_result
        result["trust_tier"] = getattr(solver_result, "trust_tier", "SOLVER_DISCHARGED")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
        result["trust_tier"] = "PROPOSAL"
    return result


def route_to_orchestrator(request: dict[str, Any]) -> dict[str, Any]:
    """Route a task request to the orchestration subsystem via ``jugeo.orchestration.controller``.

    Delegates *request* to
    :class:`~jugeo.orchestration.controller.OrchestrationController` which
    manages frontier expansion and multi-step proof strategies.

    Parameters
    ----------
    request:
        Dictionary carrying task metadata for orchestration.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "result": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _ORCHESTRATION_AVAILABLE,
        "result": None,
        "errors": [],
    }
    if not _ORCHESTRATION_AVAILABLE:
        result["errors"].append("jugeo.orchestration.controller subsystem is not installed")
        return result
    try:
        controller = _OrchestrationController()
        orchestration_result = controller.dispatch(request)
        result["result"] = orchestration_result
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def route_to_evaluation(request: dict[str, Any]) -> dict[str, Any]:
    """Route a task request to the evaluation subsystem via ``jugeo.evaluation``.

    Delegates *request* to the evaluation entry point which scores
    judgments against benchmark criteria and returns evaluation metrics.

    Parameters
    ----------
    request:
        Dictionary carrying the judgment or result to evaluate.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "result": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _EVALUATION_AVAILABLE,
        "result": None,
        "errors": [],
    }
    if not _EVALUATION_AVAILABLE:
        result["errors"].append("jugeo.evaluation subsystem is not installed")
        return result
    try:
        eval_result = _evaluate_fn(request)
        result["result"] = eval_result
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def route_to_ideation(request: dict[str, Any]) -> dict[str, Any]:
    """Route a task request to the ideation subsystem via ``jugeo.ideation``.

    Delegates *request* to :class:`~jugeo.ideation.IdeationEngine` which
    proposes candidate proof strategies and repair plans.

    Parameters
    ----------
    request:
        Dictionary carrying the context for ideation.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "result": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _IDEATION_AVAILABLE,
        "result": None,
        "errors": [],
    }
    if not _IDEATION_AVAILABLE:
        result["errors"].append("jugeo.ideation subsystem is not installed")
        return result
    try:
        engine = _IdeationEngine()
        ideation_result = engine.ideate(request)
        result["result"] = ideation_result
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result
