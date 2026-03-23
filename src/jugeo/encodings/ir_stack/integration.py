r"""Integration layer for the IR stack with external systems.

This module implements the integration infrastructure described in
Chapter 32 of ``theory2.tex`` — *Internal Representations and the IR Stack*.
It provides session management, pipeline orchestration, normal form
services, ambiguity resolution, and a copilot bridge so that the IR stack
can be driven programmatically from solver dispatch loops, language-server
plugins, and interactive proof assistants.

Architecture
------------

The integration layer sits *above* the raw data models
(:mod:`jugeo.encodings.ir_stack.models`) and *below* the external callers
(Z3 session dispatch, copilot oracle, judgment-term evaluators).  Its
layered responsibilities are:

1. **Session management** — :class:`IRStackSession` wraps an
   :class:`IRStack` with checkpoint, rollback, and commit semantics so
   that callers can safely explore speculative lowerings.

2. **Pipeline execution** — :class:`LoweringPipelineRunner` iterates over
   sequences of :class:`LoweringPass` objects, records timing information,
   and produces structured reports that can be forwarded to evidence
   channels.

3. **Normal form service** — :class:`NormalFormService` provides a
   high-throughput caching layer over the normal-form computation
   primitives, ensuring that repeated calls for the same node are
   answered in O(1) cache-lookup time.

4. **Ambiguity resolution** — :class:`AmbiguityResolver` collects
   :class:`AmbiguityMark` objects from lowered layers and applies
   resolution strategies ranging from deterministic (first-candidate)
   to oracle-assisted (copilot-proposed).

5. **Copilot IR bridge** — :class:`CopilotIRAssist` exposes the IR stack
   state to the copilot oracle and records structured feedback so that
   the oracle can improve its suggestions over successive calls.

.. math::

   \mathcal{I}(\mathcal{S}) =
   \bigl\langle
     \sigma(\mathcal{S}),\;
     \pi^*(\mathcal{S}),\;
     \hat{N}(\mathcal{S}),\;
     \alpha(\mathcal{S}),\;
     \kappa(\mathcal{S})
   \bigr\rangle

where :math:`\sigma` is the session functor, :math:`\pi^*` is the composed
lowering pipeline, :math:`\hat{N}` is the cached normal-form service,
:math:`\alpha` is the ambiguity resolver, and :math:`\kappa` is the copilot
bridge.
"""

from __future__ import annotations

import collections
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Sequence, Tuple

try:
    from jugeo.encodings.ir_stack.models import (
        IRNode,
        IRLayer,
        IRStack,
        NormalForm,
        LoweringPass,
        AmbiguityMark,
        IRNodeKind,
        IRLayerKind,
        NormalFormKind,
        LoweringPassKind,
        AmbiguityKind,
    )
except ImportError:
    pass  # type: ignore[assignment]

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder  # type: ignore[import]
except ImportError:
    class Z3Session:  # type: ignore[no-redef]
        """Stub for Z3Session when solver is unavailable."""

    class Z3Formula:  # type: ignore[no-redef]
        """Stub for Z3Formula when solver is unavailable."""

    class Z3Encoder:  # type: ignore[no-redef]
        """Stub for Z3Encoder when solver is unavailable."""

try:
    from jugeo.solver.reconstruction import ModelReconstruction  # type: ignore[import]
except ImportError:
    class ModelReconstruction:  # type: ignore[no-redef]
        """Stub for ModelReconstruction when solver is unavailable."""

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm  # type: ignore[import]
except ImportError:
    class JudgmentTerm:  # type: ignore[no-redef]
        """Stub for JudgmentTerm when judgments package is unavailable."""

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel  # type: ignore[import]
except ImportError:
    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for TrustAlgebra when evidence package is unavailable."""

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for TrustLevel when evidence package is unavailable."""

logger = logging.getLogger(__name__)


# ===================================================================== #
# 1. IR stack session management                                         #
# ===================================================================== #


@dataclass
class IRStackSession:
    """Manages a single IR processing session with full lifecycle control.

    An :class:`IRStackSession` wraps an :class:`IRStack` and adds the
    operational bookkeeping needed for safe speculative lowering:
    checkpoint/rollback, history tracking, commit finalisation, and
    serialisable snapshots for evidence export.

    Sessions are created through the module-level :func:`create_session`
    factory which also registers them in :data:`_SESSION_REGISTRY`.

    .. note::

        A session must be explicitly started with :meth:`begin` before
        layers are added.  Calling :meth:`add_layer` on an inactive session
        raises :class:`RuntimeError`.

    Attributes:
        session_id: Stable UUID string identifying this session.
        stack: The :class:`IRStack` managed by this session.
        created_at: Unix timestamp at object construction time.
        _history: Ordered list of operation records.
        _checkpoints: Maps checkpoint label to a deep-cloned stack snapshot.
        is_active: Whether the session has been started but not committed.
        metadata: Arbitrary key-value annotations.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    stack: Any = field(default_factory=lambda: None)  # IRStack
    created_at: float = field(default_factory=time.time)
    _history: list[dict[str, Any]] = field(default_factory=list)
    _checkpoints: dict[str, Any] = field(default_factory=dict)  # str -> IRStack
    is_active: bool = field(default=False)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def begin(self) -> None:
        """Mark the session as active and record the start timestamp.

        Sets :attr:`is_active` to ``True`` and records ``"started_at"``
        and ``"session_id"`` entries in :attr:`metadata`.  Re-starting an
        already active session is a no-op; the original start time is
        preserved.

        :raises RuntimeError: Never — this method is always safe to call.
        """
        if self.is_active:
            logger.debug(
                "IRStackSession.begin called on already-active session %s; ignored.",
                self.session_id,
            )
            return
        self.is_active = True
        self.metadata["started_at"] = time.time()
        self.metadata["session_id"] = self.session_id
        self._history.append(
            {
                "event": "session_begin",
                "session_id": self.session_id,
                "timestamp": self.metadata["started_at"],
            }
        )
        logger.info("IRStackSession %s started.", self.session_id)

    def commit(self) -> dict[str, Any]:
        """Finalise the session and compute a summary.

        Marks the session as inactive, records the commit timestamp, and
        returns a structured summary dictionary that can be forwarded to
        evidence channels or stored in the session registry.

        :returns: A dictionary with ``"session_id"``, ``"layer_count"``,
            ``"history_length"``, ``"checkpoint_count"``, ``"duration_s"``,
            and ``"committed_at"`` keys.
        """
        committed_at = time.time()
        self.is_active = False
        self.metadata["committed_at"] = committed_at
        started_at = self.metadata.get("started_at", self.created_at)
        duration_s = committed_at - started_at

        layer_count = 0
        if self.stack is not None and hasattr(self.stack, "layers"):
            layer_count = len(self.stack.layers)

        summary: dict[str, Any] = {
            "session_id": self.session_id,
            "layer_count": layer_count,
            "history_length": len(self._history),
            "checkpoint_count": len(self._checkpoints),
            "duration_s": round(duration_s, 6),
            "committed_at": committed_at,
        }
        self._history.append(
            {
                "event": "session_commit",
                "session_id": self.session_id,
                "timestamp": committed_at,
                "summary": summary,
            }
        )
        logger.info(
            "IRStackSession %s committed after %.3fs with %d layers.",
            self.session_id,
            duration_s,
            layer_count,
        )
        return summary

    def rollback(self, checkpoint_id: str) -> bool:
        """Restore the stack from a previously created checkpoint.

        If *checkpoint_id* is found in :attr:`_checkpoints`, the current
        :attr:`stack` is replaced with the stored snapshot and a rollback
        event is appended to the history.

        :param checkpoint_id: Label returned by :meth:`checkpoint`.
        :returns: ``True`` if the checkpoint existed and was restored,
            ``False`` if no such checkpoint was found.
        """
        if checkpoint_id not in self._checkpoints:
            logger.warning(
                "Rollback failed: checkpoint %r not found in session %s.",
                checkpoint_id,
                self.session_id,
            )
            return False

        saved_stack = self._checkpoints[checkpoint_id]
        if hasattr(saved_stack, "clone"):
            self.stack = saved_stack.clone()
        else:
            self.stack = saved_stack

        self._history.append(
            {
                "event": "session_rollback",
                "session_id": self.session_id,
                "checkpoint_id": checkpoint_id,
                "timestamp": time.time(),
            }
        )
        logger.info(
            "IRStackSession %s rolled back to checkpoint %r.",
            self.session_id,
            checkpoint_id,
        )
        return True

    def checkpoint(self, label: str) -> str:
        """Save the current stack state under *label* and return the ID.

        The checkpoint is stored as a clone of :attr:`stack` so that
        subsequent mutations do not affect it.  If the stack does not
        implement ``.clone()``, a shallow reference is stored as a
        fallback.

        :param label: Human-readable name for this checkpoint.
        :returns: The checkpoint ID (which equals *label* for convenience).
        """
        checkpoint_id = label
        if self.stack is not None and hasattr(self.stack, "clone"):
            self._checkpoints[checkpoint_id] = self.stack.clone()
        else:
            self._checkpoints[checkpoint_id] = self.stack
        self._history.append(
            {
                "event": "session_checkpoint",
                "session_id": self.session_id,
                "checkpoint_id": checkpoint_id,
                "timestamp": time.time(),
            }
        )
        logger.debug(
            "IRStackSession %s: checkpoint %r saved.",
            self.session_id,
            checkpoint_id,
        )
        return checkpoint_id

    def add_layer(self, layer: Any) -> None:  # layer: IRLayer
        """Push *layer* onto the managed stack and record it in history.

        :param layer: An :class:`IRLayer` to append to the stack.
        :raises RuntimeError: If the session is not active (i.e.,
            :meth:`begin` has not been called or :meth:`commit` has already
            been called).
        """
        if not self.is_active:
            raise RuntimeError(
                f"Cannot add layer to inactive session {self.session_id!r}. "
                "Call begin() first."
            )
        if self.stack is None:
            raise RuntimeError(
                f"Session {self.session_id!r} has no attached stack. "
                "Set session.stack before calling add_layer()."
            )
        if hasattr(self.stack, "push"):
            self.stack.push(layer)
        else:
            if not hasattr(self.stack, "layers"):
                self.stack.layers = []
            self.stack.layers.append(layer)

        layer_id = getattr(layer, "layer_id", "<unknown>")
        layer_kind_val = getattr(
            getattr(layer, "layer_kind", None), "value", str(getattr(layer, "layer_kind", "unknown"))
        )
        self._history.append(
            {
                "event": "layer_added",
                "session_id": self.session_id,
                "layer_id": layer_id,
                "layer_kind": layer_kind_val,
                "timestamp": time.time(),
            }
        )

    def get_history(self) -> list[dict[str, Any]]:
        """Return the full ordered list of session history events.

        Each entry is a dictionary with at minimum ``"event"``,
        ``"session_id"``, and ``"timestamp"`` keys.

        :returns: A copy of the internal history list.
        """
        return list(self._history)

    def statistics(self) -> dict[str, Any]:
        """Return operational statistics for this session.

        Computes layer count, history length, checkpoint count, elapsed
        duration, and a breakdown of event types from the history.

        :returns: A dictionary of statistics keyed by metric name.
        """
        now = time.time()
        started_at = self.metadata.get("started_at", self.created_at)
        elapsed = now - started_at

        layer_count = 0
        if self.stack is not None and hasattr(self.stack, "layers"):
            layer_count = len(self.stack.layers)

        event_counts: dict[str, int] = collections.Counter(
            e.get("event", "unknown") for e in self._history
        )
        return {
            "session_id": self.session_id,
            "is_active": self.is_active,
            "layer_count": layer_count,
            "history_length": len(self._history),
            "checkpoint_count": len(self._checkpoints),
            "elapsed_s": round(elapsed, 6),
            "event_breakdown": dict(event_counts),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable snapshot of this session.

        The returned dictionary includes the session ID, creation time,
        activation status, metadata, history, and a list of checkpoint
        IDs (without the full stack contents to keep output small).

        :returns: A JSON-serialisable snapshot dictionary.
        """
        stack_dict: Any = None
        if self.stack is not None and hasattr(self.stack, "to_dict"):
            try:
                stack_dict = self.stack.to_dict()
            except Exception:  # pragma: no cover
                stack_dict = {"stack_id": getattr(self.stack, "stack_id", None)}

        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "is_active": self.is_active,
            "metadata": self.metadata,
            "history_length": len(self._history),
            "history": list(self._history),
            "checkpoint_ids": list(self._checkpoints.keys()),
            "stack_summary": stack_dict,
        }


# ===================================================================== #
# 2. Lowering pipeline runner                                            #
# ===================================================================== #


@dataclass
class LoweringPipelineRunner:
    """Runs lowering pipelines with monitoring and structured logging.

    The runner keeps a reference to a current :class:`IRStackSession` when
    called via :meth:`run_with_session` and maintains per-pass timing
    statistics across multiple invocations.  All execution results are
    appended to :attr:`_run_log` so that the full history can be exported
    for evidence channels.

    .. math::

       \\pi^*(\\mathcal{L}) = \\pi_n \\circ \\cdots \\circ \\pi_1(\\mathcal{L})

    where each :math:`\\pi_k` is a :class:`LoweringPass` applied in order.

    Attributes:
        runner_id: Stable UUID string for this runner instance.
        _current_session: The session currently being driven, or ``None``.
        _run_log: Cumulative log of all pass executions.
        enable_monitoring: When ``True`` timing and error data are captured.
        _pass_timings: Maps pass identifier to list of observed durations.
    """

    runner_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _current_session: Any = field(default=None)  # IRStackSession | None
    _run_log: list[dict[str, Any]] = field(default_factory=list)
    enable_monitoring: bool = field(default=True)
    _pass_timings: dict[str, list[float]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def run(
        self,
        stack: Any,  # IRStack
        passes: list[Any],  # list[LoweringPass]
    ) -> tuple[Any, dict[str, Any]]:
        """Run *passes* sequentially over the layers of *stack*.

        Each pass is applied to the *top* layer of *stack*.  If the stack
        has no layers, an empty run report is returned immediately.  The
        result stack is a clone of *stack* with the top layer replaced by
        the transformed layer.

        :param stack: The :class:`IRStack` to process.
        :param passes: Ordered sequence of :class:`LoweringPass` objects.
        :returns: A tuple ``(result_stack, run_report)`` where
            *run_report* contains per-pass timings and status flags.
        """
        run_id = str(uuid.uuid4())
        started = time.time()
        pass_results: list[dict[str, Any]] = []

        if not hasattr(stack, "layers") or not stack.layers:
            report: dict[str, Any] = {
                "run_id": run_id,
                "pass_count": len(passes),
                "passes_applied": 0,
                "total_elapsed_s": 0.0,
                "status": "skipped_empty_stack",
                "pass_results": [],
            }
            self._run_log.append(report)
            return stack, report

        # Work on a clone so the original is not mutated.
        if hasattr(stack, "clone"):
            result_stack = stack.clone()
        else:
            result_stack = stack

        for lp in passes:
            if not hasattr(result_stack, "layers") or not result_stack.layers:
                break
            top_layer = result_stack.layers[-1]
            transformed_layer, elapsed = self.run_pass(lp, top_layer)
            result_stack.layers[-1] = transformed_layer

            pass_name = getattr(lp, "pass_name", str(lp))
            pass_id = getattr(lp, "pass_id", pass_name)
            pass_results.append(
                {
                    "pass_id": pass_id,
                    "pass_name": pass_name,
                    "elapsed_s": round(elapsed, 6),
                    "status": "ok",
                }
            )
            logger.debug(
                "Runner %s: pass %r completed in %.4fs.",
                self.runner_id,
                pass_name,
                elapsed,
            )

        total_elapsed = time.time() - started
        run_report: dict[str, Any] = {
            "run_id": run_id,
            "runner_id": self.runner_id,
            "pass_count": len(passes),
            "passes_applied": len(pass_results),
            "total_elapsed_s": round(total_elapsed, 6),
            "status": "ok",
            "pass_results": pass_results,
        }
        self._run_log.append(run_report)
        return result_stack, run_report

    def run_pass(
        self,
        lp: Any,  # LoweringPass
        layer: Any,  # IRLayer
    ) -> tuple[Any, float]:  # (IRLayer, float)
        """Run a single lowering pass on *layer* and return timing.

        Calls :meth:`LoweringPass.apply` if available, otherwise records
        the pass as a no-op transformation.

        :param lp: The :class:`LoweringPass` to apply.
        :param layer: The :class:`IRLayer` to transform.
        :returns: ``(transformed_layer, elapsed_seconds)`` tuple.
        """
        pass_name = getattr(lp, "pass_name", str(lp))
        pass_id = getattr(lp, "pass_id", pass_name)
        t0 = time.perf_counter()

        try:
            if hasattr(lp, "apply"):
                transformed = lp.apply(layer)
            else:
                if hasattr(layer, "clone"):
                    transformed = layer.clone()
                else:
                    transformed = layer
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            logger.error(
                "Runner %s: pass %r raised %s: %s",
                self.runner_id,
                pass_name,
                type(exc).__name__,
                exc,
            )
            if self.enable_monitoring:
                self._pass_timings.setdefault(pass_id, []).append(elapsed)
            raise

        elapsed = time.perf_counter() - t0
        if self.enable_monitoring:
            self._pass_timings.setdefault(pass_id, []).append(elapsed)

        return transformed, elapsed

    def run_with_session(
        self,
        session: IRStackSession,
        passes: list[Any],  # list[LoweringPass]
    ) -> dict[str, Any]:
        """Run *passes* within an active session context.

        The runner binds to *session* for the duration of this call.
        After execution the session's layer list is updated with the
        result and a pipeline-run event is appended to the session history.

        :param session: An active :class:`IRStackSession`.
        :param passes: Ordered list of :class:`LoweringPass` objects.
        :returns: The run report dictionary produced by :meth:`run`.
        :raises RuntimeError: If *session* is not active.
        """
        if not session.is_active:
            raise RuntimeError(
                f"Cannot run pipeline in inactive session {session.session_id!r}."
            )
        self._current_session = session
        try:
            result_stack, run_report = self.run(session.stack, passes)
            session.stack = result_stack
            session._history.append(
                {
                    "event": "pipeline_run",
                    "session_id": session.session_id,
                    "runner_id": self.runner_id,
                    "run_id": run_report.get("run_id"),
                    "passes_applied": run_report.get("passes_applied", 0),
                    "timestamp": time.time(),
                }
            )
        finally:
            self._current_session = None

        return run_report

    def retry_failed_pass(
        self,
        lp: Any,  # LoweringPass
        layer: Any,  # IRLayer
        max_retries: int = 3,
    ) -> Any:  # IRLayer | None
        """Retry *lp* on *layer* up to *max_retries* times.

        Each attempt is separated by an exponential backoff delay (0.05 s
        * 2^attempt).  Returns the transformed layer on the first success
        or ``None`` if all attempts fail.

        :param lp: The :class:`LoweringPass` to retry.
        :param layer: The :class:`IRLayer` to transform.
        :param max_retries: Maximum number of attempts (default 3).
        :returns: The transformed layer or ``None`` on exhausted retries.
        """
        pass_name = getattr(lp, "pass_name", str(lp))
        for attempt in range(max_retries):
            try:
                transformed, elapsed = self.run_pass(lp, layer)
                logger.info(
                    "Runner %s: pass %r succeeded on attempt %d.",
                    self.runner_id,
                    pass_name,
                    attempt + 1,
                )
                return transformed
            except Exception as exc:
                delay = 0.05 * (2 ** attempt)
                logger.warning(
                    "Runner %s: pass %r attempt %d failed (%s); retrying in %.2fs.",
                    self.runner_id,
                    pass_name,
                    attempt + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
        logger.error(
            "Runner %s: pass %r exhausted %d retries.",
            self.runner_id,
            pass_name,
            max_retries,
        )
        return None

    def average_pass_time(self, pass_id: str) -> float:
        """Return the mean observed duration for *pass_id*.

        If no timing data has been recorded for *pass_id* returns ``0.0``.

        :param pass_id: Identifier string matching a pass's ``pass_id`` or
            ``pass_name`` attribute.
        :returns: Mean duration in seconds.
        """
        timings = self._pass_timings.get(pass_id, [])
        if not timings:
            return 0.0
        return sum(timings) / len(timings)

    def generate_report(self) -> dict[str, Any]:
        """Generate a full execution report with timings and status.

        Aggregates all entries in :attr:`_run_log` and :attr:`_pass_timings`
        into a single dictionary suitable for evidence export.

        :returns: A comprehensive report dictionary.
        """
        total_passes = sum(
            entry.get("passes_applied", 0) for entry in self._run_log
        )
        total_runs = len(self._run_log)
        all_elapsed = [
            entry.get("total_elapsed_s", 0.0) for entry in self._run_log
        ]
        avg_elapsed = sum(all_elapsed) / len(all_elapsed) if all_elapsed else 0.0

        pass_summaries: list[dict[str, Any]] = []
        for pass_id, timings in self._pass_timings.items():
            pass_summaries.append(
                {
                    "pass_id": pass_id,
                    "run_count": len(timings),
                    "mean_elapsed_s": round(sum(timings) / len(timings), 6),
                    "min_elapsed_s": round(min(timings), 6),
                    "max_elapsed_s": round(max(timings), 6),
                }
            )

        return {
            "runner_id": self.runner_id,
            "total_runs": total_runs,
            "total_passes_applied": total_passes,
            "avg_run_elapsed_s": round(avg_elapsed, 6),
            "pass_summaries": pass_summaries,
            "run_log": list(self._run_log),
        }


# ===================================================================== #
# 3. Normal form service                                                 #
# ===================================================================== #


@dataclass
class NormalFormService:
    """Service for computing and caching normal forms.

    The service wraps the :class:`NormalForm` computation logic from
    :mod:`jugeo.encodings.ir_stack.models` with a bounded LRU-style cache
    so that repeated requests for the same node return immediately.  Cache
    entries are keyed by a compound hash of ``node_id`` and ``kind``.

    .. math::

       \\hat{N}(n, k) =
       \\begin{cases}
         \\text{cache}[n, k] & \\text{if } (n, k) \\in \\text{cache} \\\\
         N(n, k)             & \\text{otherwise}
       \\end{cases}

    where :math:`N(n, k)` is the raw computation and the result is stored
    before being returned.

    Attributes:
        service_id: Stable UUID string for this service instance.
        _cache: Maps ``(node_id, kind)`` compound key to :class:`NormalForm`.
        _cache_hits: Cumulative count of cache hits.
        _cache_misses: Cumulative count of cache misses.
        default_kind: :class:`NormalFormKind` used when ``kind`` is ``None``.
        max_cache_size: Maximum number of entries before eviction.
    """

    service_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _cache: dict[str, Any] = field(default_factory=dict)  # str -> NormalForm
    _cache_hits: int = field(default=0)
    _cache_misses: int = field(default=0)
    default_kind: Any = field(default=None)  # NormalFormKind | None
    max_cache_size: int = field(default=10_000)

    # ------------------------------------------------------------------
    def _cache_key(self, node_id: str, kind: Any) -> str:
        """Compute the cache key for a *node_id* + *kind* pair.

        :param node_id: The :class:`IRNode` identifier.
        :param kind: The :class:`NormalFormKind` value.
        :returns: A compound string key.
        """
        kind_val = getattr(kind, "value", str(kind))
        raw = f"{node_id}::{kind_val}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _effective_kind(self, kind: Any) -> Any:
        """Return *kind* if non-``None``, otherwise :attr:`default_kind`.

        :param kind: Caller-supplied kind, may be ``None``.
        :returns: The effective :class:`NormalFormKind`.
        """
        if kind is not None:
            return kind
        if self.default_kind is not None:
            return self.default_kind
        # Fall back to a sensible default string when enum is unavailable.
        try:
            return NormalFormKind.BETA_NORMAL
        except NameError:
            return "beta_normal"

    def _evict_if_needed(self) -> None:
        """Remove the oldest quarter of cache entries when over capacity.

        Uses insertion-order of the dict (guaranteed in Python 3.7+) to
        identify the oldest entries.
        """
        if len(self._cache) >= self.max_cache_size:
            evict_count = max(1, self.max_cache_size // 4)
            keys_to_evict = list(self._cache.keys())[:evict_count]
            for k in keys_to_evict:
                del self._cache[k]
            logger.debug(
                "NormalFormService %s evicted %d cache entries.",
                self.service_id,
                evict_count,
            )

    def compute(self, node: Any, kind: Any = None) -> Any:  # -> NormalForm
        """Compute and cache the normal form of *node* under *kind*.

        If the result is already cached the cached value is returned
        immediately and :attr:`_cache_hits` is incremented.  Otherwise the
        normal form is computed, cached, and :attr:`_cache_misses` is
        incremented.

        :param node: An :class:`IRNode` to reduce.
        :param kind: The :class:`NormalFormKind` to compute.  Defaults to
            :attr:`default_kind`.
        :returns: The computed :class:`NormalForm`.
        """
        eff_kind = self._effective_kind(kind)
        node_id = getattr(node, "node_id", str(node))
        cache_key = self._cache_key(node_id, eff_kind)

        if cache_key in self._cache:
            self._cache_hits += 1
            return self._cache[cache_key]

        self._cache_misses += 1
        # Attempt to use the node's own to_dict as the normal-form payload.
        try:
            payload: dict[str, Any] = node.to_dict() if hasattr(node, "to_dict") else {"node_id": node_id}
        except Exception:
            payload = {"node_id": node_id}

        try:
            nf_kind_val = getattr(eff_kind, "value", str(eff_kind))
            normal_form = NormalForm(  # type: ignore[call-arg]
                form_id=str(uuid.uuid4()),
                source_node_id=node_id,
                normal_form_kind=eff_kind,
                canonical_payload=payload,
                reduction_steps=0,
                is_ground=not bool(getattr(node, "children", [])),
            )
        except NameError:
            # NormalForm not imported — return a plain dict as fallback.
            normal_form = {  # type: ignore[assignment]
                "form_id": str(uuid.uuid4()),
                "source_node_id": node_id,
                "normal_form_kind": getattr(eff_kind, "value", str(eff_kind)),
                "canonical_payload": payload,
            }

        self._evict_if_needed()
        self._cache[cache_key] = normal_form
        return normal_form

    def batch_compute(
        self,
        nodes: list[Any],  # list[IRNode]
        kind: Any = None,  # NormalFormKind | None
    ) -> dict[str, Any]:  # dict[str, NormalForm]
        """Compute normal forms for all nodes in *nodes*.

        Results are collected into a mapping from ``node_id`` to
        :class:`NormalForm`.  Already-cached entries are served from
        the cache; remaining entries are computed in order.

        :param nodes: List of :class:`IRNode` objects to reduce.
        :param kind: Normal form kind to compute for all nodes.
        :returns: Mapping from ``node_id`` to :class:`NormalForm`.
        """
        result: dict[str, Any] = {}
        for node in nodes:
            node_id = getattr(node, "node_id", str(node))
            nf = self.compute(node, kind)
            result[node_id] = nf
        return result

    def invalidate(self, node_id: str) -> bool:
        """Remove all cache entries for *node_id*.

        Scans the entire cache and removes entries whose key encodes
        *node_id*.  Returns ``True`` if at least one entry was removed.

        :param node_id: The node identifier whose cache entries should be
            invalidated.
        :returns: ``True`` if any entries were removed.
        """
        # Collect keys that encode this node_id.
        keys_to_remove: list[str] = []
        for kind_val in [
            "beta_normal",
            "eta_normal",
            "full_normal",
            "head_normal",
            "weak_head",
        ]:
            raw = f"{node_id}::{kind_val}"
            key = hashlib.sha256(raw.encode()).hexdigest()[:32]
            if key in self._cache:
                keys_to_remove.append(key)

        for k in keys_to_remove:
            del self._cache[k]
        return bool(keys_to_remove)

    def warm_cache(
        self,
        layer: Any,  # IRLayer
        kind: Any = None,  # NormalFormKind | None
    ) -> int:
        """Pre-compute normal forms for all nodes in *layer*.

        Iterates over ``layer.nodes`` (expected to be a ``dict[str, IRNode]``)
        and calls :meth:`compute` for each.  Returns the number of nodes
        processed.

        :param layer: An :class:`IRLayer` whose nodes should be warmed.
        :param kind: Normal form kind to compute.
        :returns: Number of nodes for which normal forms were computed.
        """
        nodes_dict = getattr(layer, "nodes", {})
        count = 0
        for node_id, node in nodes_dict.items():
            self.compute(node, kind)
            count += 1
        logger.debug(
            "NormalFormService %s: warmed cache for %d nodes in layer.",
            self.service_id,
            count,
        )
        return count

    def compare_nodes(
        self,
        node1: Any,  # IRNode
        node2: Any,  # IRNode
    ) -> int:
        """Compare *node1* and *node2* via their normal forms.

        Computes the BETA_NORMAL form of both nodes, then compares their
        canonical payload JSON strings lexicographically.

        :param node1: First :class:`IRNode`.
        :param node2: Second :class:`IRNode`.
        :returns: ``-1`` if node1 < node2, ``0`` if equal, ``1`` if
            node1 > node2.
        """
        try:
            nf_kind = NormalFormKind.BETA_NORMAL  # type: ignore[name-defined]
        except NameError:
            nf_kind = "beta_normal"

        nf1 = self.compute(node1, nf_kind)
        nf2 = self.compute(node2, nf_kind)

        def _payload_str(nf: Any) -> str:
            if hasattr(nf, "canonical_payload"):
                return json.dumps(nf.canonical_payload, sort_keys=True)
            if isinstance(nf, dict):
                return json.dumps(nf.get("canonical_payload", {}), sort_keys=True)
            return str(nf)

        s1 = _payload_str(nf1)
        s2 = _payload_str(nf2)
        if s1 < s2:
            return -1
        if s1 > s2:
            return 1
        return 0

    def cache_statistics(self) -> dict[str, Any]:
        """Return statistics about cache utilisation.

        :returns: Dictionary with ``"hits"``, ``"misses"``, ``"size"``,
            ``"max_size"``, and ``"hit_rate"`` keys.
        """
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        return {
            "service_id": self.service_id,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "total_lookups": total,
            "hit_rate": round(hit_rate, 4),
            "size": len(self._cache),
            "max_size": self.max_cache_size,
        }

    def flush_cache(self) -> int:
        """Clear all entries from the cache.

        :returns: The number of entries that were cleared.
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(
            "NormalFormService %s: cache flushed (%d entries removed).",
            self.service_id,
            count,
        )
        return count


# ===================================================================== #
# 4. Ambiguity resolution                                                #
# ===================================================================== #


@dataclass
class AmbiguityResolver:
    """Resolves ambiguity marks in IR layers.

    Collects :class:`AmbiguityMark` objects from lowered layers and applies
    configurable resolution strategies.  When :attr:`auto_resolve` is
    ``True``, every call to :meth:`register_mark` also triggers an attempt
    to resolve the mark using the ``"first_candidate"`` strategy.

    Attributes:
        resolver_id: Stable UUID string for this resolver instance.
        _resolution_log: Ordered list of resolution records.
        _pending_resolutions: Maps ``mark_id`` to unresolved marks.
        auto_resolve: When ``True`` marks are resolved immediately on
            registration.
    """

    resolver_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _resolution_log: list[dict[str, Any]] = field(default_factory=list)
    _pending_resolutions: dict[str, Any] = field(default_factory=dict)  # mark_id -> AmbiguityMark
    auto_resolve: bool = field(default=False)

    # ------------------------------------------------------------------
    def register_mark(self, mark: Any) -> None:  # mark: AmbiguityMark
        """Add *mark* to the pending resolution queue.

        If :attr:`auto_resolve` is ``True``, immediately attempts to
        resolve the mark using the ``"first_candidate"`` strategy.

        :param mark: An :class:`AmbiguityMark` to register.
        """
        mark_id = getattr(mark, "mark_id", str(mark))
        if mark_id in self._pending_resolutions:
            logger.debug(
                "AmbiguityResolver %s: mark %r already registered; skipping.",
                self.resolver_id,
                mark_id,
            )
            return
        self._pending_resolutions[mark_id] = mark
        logger.debug(
            "AmbiguityResolver %s: registered mark %r.",
            self.resolver_id,
            mark_id,
        )
        if self.auto_resolve:
            ambiguous_nodes = getattr(mark, "ambiguous_nodes", [])
            for node_id in list(ambiguous_nodes):
                candidates = getattr(mark, "resolution_candidates", {}).get(node_id, [])
                if candidates:
                    self.resolve_mark(mark_id, node_id, candidates[0])

    def resolve_mark(
        self,
        mark_id: str,
        node_id: str,
        candidate: str,
    ) -> bool:
        """Resolve the ambiguity for *node_id* within mark *mark_id*.

        Calls :meth:`AmbiguityMark.resolve` on the mark and, if the mark
        becomes fully resolved, removes it from the pending queue.

        :param mark_id: Identifier of the :class:`AmbiguityMark`.
        :param node_id: The node whose ambiguity is being resolved.
        :param candidate: The chosen resolution candidate string.
        :returns: ``True`` if the operation succeeded (mark existed),
            ``False`` if the mark was not found.
        """
        mark = self._pending_resolutions.get(mark_id)
        if mark is None:
            logger.warning(
                "AmbiguityResolver %s: mark %r not found.",
                self.resolver_id,
                mark_id,
            )
            return False

        fully_resolved = False
        if hasattr(mark, "resolve"):
            fully_resolved = mark.resolve(node_id, candidate)
        else:
            ambiguous = getattr(mark, "ambiguous_nodes", [])
            if node_id in ambiguous:
                ambiguous.remove(node_id)
            fully_resolved = len(ambiguous) == 0

        record: dict[str, Any] = {
            "event": "resolution",
            "resolver_id": self.resolver_id,
            "mark_id": mark_id,
            "node_id": node_id,
            "candidate": candidate,
            "fully_resolved": fully_resolved,
            "timestamp": time.time(),
        }
        self._resolution_log.append(record)

        if fully_resolved:
            del self._pending_resolutions[mark_id]
            logger.info(
                "AmbiguityResolver %s: mark %r fully resolved.",
                self.resolver_id,
                mark_id,
            )
        return True

    def auto_resolve_marks(
        self,
        layer: Any,  # IRLayer
        strategy: str = "first_candidate",
    ) -> int:
        """Automatically resolve all marks attached to nodes in *layer*.

        Iterates over ``layer.nodes`` and inspects ``node.ambiguity_mark``
        for each node.  Applies *strategy* to pick a candidate:

        * ``"first_candidate"`` — choose the first entry in the candidates list.
        * ``"last_candidate"`` — choose the last entry.
        * ``"highest_trust"`` — choose the candidate whose index corresponds
          to the node's ``trust_level`` (clamped to list length).

        :param layer: The :class:`IRLayer` to scan.
        :param strategy: Resolution strategy identifier.
        :returns: Number of ambiguity nodes successfully resolved.
        """
        resolved_count = 0
        nodes_dict = getattr(layer, "nodes", {})
        for node_id, node in nodes_dict.items():
            mark = getattr(node, "ambiguity_mark", None)
            if mark is None:
                continue
            mark_id = getattr(mark, "mark_id", str(mark))
            # Register if not yet known.
            if mark_id not in self._pending_resolutions:
                self._pending_resolutions[mark_id] = mark

            ambiguous_nodes = getattr(mark, "ambiguous_nodes", [])
            if node_id not in ambiguous_nodes:
                continue

            candidates = getattr(mark, "resolution_candidates", {}).get(node_id, [])
            if not candidates:
                continue

            if strategy == "last_candidate":
                chosen = candidates[-1]
            elif strategy == "highest_trust":
                trust = getattr(node, "trust_level", 0)
                idx = min(trust, len(candidates) - 1)
                chosen = candidates[idx]
            else:
                # Default: first_candidate
                chosen = candidates[0]

            if self.resolve_mark(mark_id, node_id, chosen):
                resolved_count += 1

        return resolved_count

    def pending_count(self) -> int:
        """Return the number of marks with outstanding ambiguities.

        :returns: Length of :attr:`_pending_resolutions`.
        """
        return len(self._pending_resolutions)

    def resolution_summary(self) -> dict[str, Any]:
        """Return a structured summary of all resolutions performed.

        :returns: Dictionary with ``"resolved_count"``, ``"pending_count"``,
            ``"resolver_id"``, and a ``"by_strategy"`` breakdown derived
            from the resolution log.
        """
        resolved_events = [
            e for e in self._resolution_log if e.get("event") == "resolution"
        ]
        fully_resolved = [e for e in resolved_events if e.get("fully_resolved")]
        return {
            "resolver_id": self.resolver_id,
            "resolved_count": len(resolved_events),
            "fully_resolved_marks": len(fully_resolved),
            "pending_count": self.pending_count(),
            "total_log_entries": len(self._resolution_log),
        }

    def export_resolutions(self) -> list[dict[str, Any]]:
        """Return all resolution records from the log.

        :returns: A copy of :attr:`_resolution_log`.
        """
        return list(self._resolution_log)


# ===================================================================== #
# 5. Copilot IR bridge                                                   #
# ===================================================================== #


@dataclass
class CopilotIRAssist:
    """Copilot bridge for IR stack operations.

    Exposes the IR stack state to the copilot oracle and records structured
    feedback so that suggestion quality can be measured and improved over
    successive calls.  All suggestion methods are annotated with
    ``# copilot`` comments as the canonical extension points.

    Attributes:
        assist_id: Stable UUID string for this assistant instance.
        session_id: ID of the parent :class:`IRStackSession`.
        _suggestions: Ordered list of suggestion records.
        _feedback: Ordered list of feedback records.
        confidence_threshold: Minimum confidence for a suggestion to be
            returned; lower-confidence suggestions are suppressed.
        model_hint: Hint string forwarded to the copilot model selector.
    """

    assist_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = field(default="")
    _suggestions: list[dict[str, Any]] = field(default_factory=list)
    _feedback: list[dict[str, Any]] = field(default_factory=list)
    confidence_threshold: float = field(default=0.6)
    model_hint: str = field(default="default")

    # ------------------------------------------------------------------
    def suggest_ir_structure(self, context: dict[str, Any]) -> Any:  # -> IRNode
        """Suggest an IR node structure for the given context.

        # copilot: This method is the primary extension point for the
        # copilot oracle to propose IR node structures.  The context dict
        # should include "node_kind", "payload_hints", and optionally
        # "parent_node_id" and "layer_kind".

        Constructs a heuristic :class:`IRNode` based on context keys and
        records the suggestion for feedback tracking.

        :param context: Dictionary describing the desired node properties.
        :returns: A new :class:`IRNode` with kind and payload derived from
            context.
        """
        suggestion_id = str(uuid.uuid4())
        kind_hint = context.get("node_kind", "expression")
        payload_hints: dict[str, Any] = context.get("payload_hints", {})
        confidence: float = float(context.get("confidence", 0.8))

        try:
            nk = IRNodeKind(kind_hint)  # type: ignore[name-defined]
        except (NameError, ValueError):
            nk = kind_hint  # type: ignore[assignment]

        try:
            node = IRNode(  # type: ignore[call-arg]
                node_id=str(uuid.uuid4()),
                node_kind=nk,
                payload={
                    "source": "copilot_suggestion",
                    "suggestion_id": suggestion_id,
                    **payload_hints,
                },
            )
        except NameError:
            node = {  # type: ignore[assignment]
                "node_id": str(uuid.uuid4()),
                "node_kind": kind_hint,
                "payload": {
                    "source": "copilot_suggestion",
                    "suggestion_id": suggestion_id,
                    **payload_hints,
                },
            }

        record: dict[str, Any] = {
            "suggestion_id": suggestion_id,
            "type": "ir_structure",
            "confidence": confidence,
            "context_keys": list(context.keys()),
            "timestamp": time.time(),
        }
        self._suggestions.append(record)
        logger.debug(
            "CopilotIRAssist %s: ir_structure suggestion %s (conf=%.2f).",
            self.assist_id,
            suggestion_id,
            confidence,
        )
        return node

    def suggest_lowering_strategy(
        self,
        stack: Any,  # IRStack
    ) -> list[str]:
        """Suggest a lowering pass ordering for *stack*.

        # copilot: Analyses the current stack depth and top-layer kind to
        # recommend a pass ordering.  The recommendation is a list of
        # LoweringPassKind value strings in execution order.

        Uses a heuristic based on the top layer's ``layer_kind`` to select
        an appropriate default pass sequence.

        :param stack: The :class:`IRStack` to analyse.
        :returns: Ordered list of :class:`LoweringPassKind` value strings.
        """
        suggestion_id = str(uuid.uuid4())
        layers = getattr(stack, "layers", [])
        top_kind_val = "surface"
        if layers:
            top_layer = layers[-1]
            top_kind = getattr(top_layer, "layer_kind", None)
            top_kind_val = getattr(top_kind, "value", str(top_kind))

        _strategy_map: dict[str, list[str]] = {
            "surface": [
                "desugaring",
                "type_erasure",
                "obligation_extraction",
                "constraint_normalization",
                "z3_encoding",
            ],
            "semantic": [
                "obligation_extraction",
                "constraint_normalization",
                "z3_encoding",
            ],
            "logical": [
                "constraint_normalization",
                "z3_encoding",
            ],
            "solver_ready": ["z3_encoding"],
            "cached": [],
            "delta": ["desugaring", "constraint_normalization"],
        }
        strategy = _strategy_map.get(
            top_kind_val,
            ["desugaring", "obligation_extraction", "z3_encoding"],
        )
        record: dict[str, Any] = {
            "suggestion_id": suggestion_id,
            "type": "lowering_strategy",
            "top_kind": top_kind_val,
            "strategy": strategy,
            "timestamp": time.time(),
        }
        self._suggestions.append(record)
        return list(strategy)

    def explain_ambiguity(self, mark: Any) -> str:  # mark: AmbiguityMark
        """Generate a human-readable explanation of *mark*.

        # copilot: Summarises the ambiguity mark's kind, affected nodes,
        # and available candidates for display in IDE hover documentation
        # or proof assistant UIs.

        :param mark: The :class:`AmbiguityMark` to explain.
        :returns: A multi-line explanation string.
        """
        suggestion_id = str(uuid.uuid4())
        mark_id = getattr(mark, "mark_id", "<unknown>")
        mark_kind = getattr(getattr(mark, "mark_kind", None), "value", str(getattr(mark, "mark_kind", "unknown")))
        ambiguous_nodes: list[str] = list(getattr(mark, "ambiguous_nodes", []))
        candidates: dict[str, list[str]] = dict(getattr(mark, "resolution_candidates", {}))
        confidence: float = float(getattr(mark, "confidence", 0.0))

        lines: list[str] = [
            f"Ambiguity mark {mark_id!r} (kind={mark_kind}, confidence={confidence:.2f})",
            f"  Unresolved nodes ({len(ambiguous_nodes)}):",
        ]
        for node_id in ambiguous_nodes[:10]:
            node_candidates = candidates.get(node_id, [])
            lines.append(f"    - {node_id}: {node_candidates}")
        if len(ambiguous_nodes) > 10:
            lines.append(f"    ... and {len(ambiguous_nodes) - 10} more.")

        explanation = "\n".join(lines)
        self._suggestions.append(
            {
                "suggestion_id": suggestion_id,
                "type": "explain_ambiguity",
                "mark_id": mark_id,
                "timestamp": time.time(),
            }
        )
        return explanation

    def propose_resolution(
        self,
        mark: Any,  # AmbiguityMark
        context: dict[str, Any],
    ) -> str | None:
        """Propose a resolution candidate for the first unresolved node.

        # copilot: Uses the context dict to select the most suitable
        # resolution candidate.  Context keys "preferred_kind" and
        # "trust_budget" are used for heuristic selection; fall back to
        # the first available candidate if no preference is found.

        :param mark: The :class:`AmbiguityMark` to resolve.
        :param context: Dictionary with optional "preferred_kind" and
            "trust_budget" keys.
        :returns: A candidate string if one can be identified, else ``None``.
        """
        suggestion_id = str(uuid.uuid4())
        ambiguous_nodes: list[str] = list(getattr(mark, "ambiguous_nodes", []))
        candidates_map: dict[str, list[str]] = dict(
            getattr(mark, "resolution_candidates", {})
        )
        preferred_kind: str = context.get("preferred_kind", "")

        chosen: str | None = None
        for node_id in ambiguous_nodes:
            node_candidates = candidates_map.get(node_id, [])
            if not node_candidates:
                continue
            # Prefer candidates that contain the preferred_kind hint.
            if preferred_kind:
                for c in node_candidates:
                    if preferred_kind in c:
                        chosen = c
                        break
            if chosen is None and node_candidates:
                chosen = node_candidates[0]
            if chosen is not None:
                break

        self._suggestions.append(
            {
                "suggestion_id": suggestion_id,
                "type": "propose_resolution",
                "mark_id": getattr(mark, "mark_id", "<unknown>"),
                "proposed_candidate": chosen,
                "confidence": 0.75 if chosen else 0.0,
                "timestamp": time.time(),
            }
        )
        return chosen

    def record_feedback(
        self,
        suggestion_id: str,
        accepted: bool,
        correction: dict[str, Any] | None = None,
    ) -> None:
        """Record user feedback for a previous suggestion.

        Locates the suggestion with *suggestion_id* in :attr:`_suggestions`
        and appends a feedback record to :attr:`_feedback`.

        :param suggestion_id: The ID of the suggestion being rated.
        :param accepted: Whether the suggestion was accepted by the user.
        :param correction: Optional dictionary with corrected values.
        """
        matching = [
            s for s in self._suggestions if s.get("suggestion_id") == suggestion_id
        ]
        original = matching[-1] if matching else {}
        feedback_record: dict[str, Any] = {
            "feedback_id": str(uuid.uuid4()),
            "suggestion_id": suggestion_id,
            "suggestion_type": original.get("type", "unknown"),
            "accepted": accepted,
            "correction": correction,
            "timestamp": time.time(),
        }
        self._feedback.append(feedback_record)
        logger.debug(
            "CopilotIRAssist %s: feedback for %s — accepted=%s.",
            self.assist_id,
            suggestion_id,
            accepted,
        )

    def learning_summary(self) -> dict[str, Any]:
        """Return acceptance rates and feedback patterns.

        Groups feedback by suggestion type and computes per-type and
        overall acceptance rates.

        :returns: A summary dictionary keyed by suggestion type.
        """
        by_type: dict[str, dict[str, int]] = {}
        for fb in self._feedback:
            stype = fb.get("suggestion_type", "unknown")
            entry = by_type.setdefault(stype, {"accepted": 0, "rejected": 0})
            if fb.get("accepted"):
                entry["accepted"] += 1
            else:
                entry["rejected"] += 1

        summary_by_type: dict[str, Any] = {}
        total_accepted = 0
        total_rejected = 0
        for stype, counts in by_type.items():
            acc = counts["accepted"]
            rej = counts["rejected"]
            total_accepted += acc
            total_rejected += rej
            total = acc + rej
            summary_by_type[stype] = {
                "accepted": acc,
                "rejected": rej,
                "acceptance_rate": round(acc / total, 4) if total > 0 else 0.0,
            }

        total_feedback = total_accepted + total_rejected
        overall_rate = total_accepted / total_feedback if total_feedback > 0 else 0.0
        return {
            "assist_id": self.assist_id,
            "session_id": self.session_id,
            "total_suggestions": len(self._suggestions),
            "total_feedback": total_feedback,
            "overall_acceptance_rate": round(overall_rate, 4),
            "by_type": summary_by_type,
        }

    def integration_status(self) -> dict[str, Any]:
        """Check connectivity and return a status dictionary.

        Verifies that the underlying copilot infrastructure is reachable
        and returns a structured status report.  In the current
        implementation this is a self-assessment based on accumulated
        state; external connectivity checks would be added when a live
        copilot endpoint is configured.

        :returns: Dictionary with ``"assist_id"``, ``"session_id"``,
            ``"suggestions_count"``, ``"feedback_count"``,
            ``"confidence_threshold"``, ``"model_hint"``, and
            ``"status"`` keys.
        """
        return {
            "assist_id": self.assist_id,
            "session_id": self.session_id,
            "suggestions_count": len(self._suggestions),
            "feedback_count": len(self._feedback),
            "confidence_threshold": self.confidence_threshold,
            "model_hint": self.model_hint,
            "status": "operational",
        }


# ===================================================================== #
# Module-level session registry and factory functions                    #
# ===================================================================== #

_SESSION_REGISTRY: dict[str, IRStackSession] = {}
"""Global registry mapping ``session_id`` to :class:`IRStackSession`.

Sessions are added by :func:`create_session` and looked up by
:func:`get_session`.  Callers are responsible for removing sessions that
are no longer needed; the registry does not perform automatic expiration.
"""


def create_session(metadata: dict[str, Any] | None = None) -> IRStackSession:
    """Create a new :class:`IRStackSession` and register it globally.

    Constructs a fresh session with a new UUID, wraps a new
    :class:`IRStack`, and adds it to :data:`_SESSION_REGISTRY`.

    :param metadata: Optional initial metadata dictionary.
    :returns: A newly created :class:`IRStackSession`.
    """
    try:
        stack = IRStack(  # type: ignore[call-arg]
            stack_id=str(uuid.uuid4()),
            creation_time=time.time(),
            metadata={},
        )
    except NameError:
        stack = None  # type: ignore[assignment]

    session = IRStackSession(
        session_id=str(uuid.uuid4()),
        stack=stack,
        created_at=time.time(),
        metadata=dict(metadata) if metadata else {},
    )
    _SESSION_REGISTRY[session.session_id] = session
    logger.debug("create_session: registered session %s.", session.session_id)
    return session


def get_session(session_id: str) -> IRStackSession | None:
    """Look up a previously created session by its ID.

    :param session_id: The UUID string of the session to retrieve.
    :returns: The :class:`IRStackSession` if found, else ``None``.
    """
    return _SESSION_REGISTRY.get(session_id)


def create_pipeline_runner(enable_monitoring: bool = True) -> LoweringPipelineRunner:
    """Create a new :class:`LoweringPipelineRunner`.

    :param enable_monitoring: When ``True`` timing data is captured for
        each pass (default ``True``).
    :returns: A freshly created :class:`LoweringPipelineRunner`.
    """
    return LoweringPipelineRunner(
        runner_id=str(uuid.uuid4()),
        enable_monitoring=enable_monitoring,
    )


def create_normal_form_service(max_cache_size: int = 10_000) -> NormalFormService:
    """Create a new :class:`NormalFormService` with the given cache limit.

    :param max_cache_size: Maximum number of cache entries before eviction
        (default 10 000).
    :returns: A freshly created :class:`NormalFormService`.
    """
    try:
        default_kind = NormalFormKind.BETA_NORMAL  # type: ignore[name-defined]
    except NameError:
        default_kind = None

    return NormalFormService(
        service_id=str(uuid.uuid4()),
        default_kind=default_kind,
        max_cache_size=max_cache_size,
    )


def create_ambiguity_resolver(auto_resolve: bool = False) -> AmbiguityResolver:
    """Create a new :class:`AmbiguityResolver`.

    :param auto_resolve: When ``True`` marks are resolved immediately upon
        registration using the ``"first_candidate"`` strategy (default
        ``False``).
    :returns: A freshly created :class:`AmbiguityResolver`.
    """
    return AmbiguityResolver(
        resolver_id=str(uuid.uuid4()),
        auto_resolve=auto_resolve,
    )
