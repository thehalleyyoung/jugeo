from __future__ import annotations

"""s03 — exec and eval as Bounded or Residual Events (Ch23 §3).

exec/eval punch holes in the type-safe judgment sheaf — "semantic apertures."
A bounded event leaves no residual namespace mutations; a residual event
introduces new bindings that persist beyond the exec/eval call frame.
This module classifies, witnesses, and reports on exec/eval events under
JuGeo's sheaf-theoretic semantics.

Under sheaf semantics, every exec/eval call is modeled as a local section
that either restricts cleanly to the ambient namespace (bounded) or bleeds
new bindings into the restriction map (residual).  The coordinator type
ExecEvalBoundedResidualCoordinator acts as the global section assembler,
collecting local witnesses from ResidualEventWitness and classification
verdicts from ExecBoundednessAnalyzer into a single coherent report.
"""

import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.sheaf import JudgmentSheaf  # type: ignore
except ImportError:
    class JudgmentSheaf:  # type: ignore
        """Inline stub for JudgmentSheaf.

        The real JudgmentSheaf lives in jugeo.sheaf and represents the
        presheaf of typing judgments over the open-cover of the program's
        namespace lattice.  This stub is used when jugeo is not installed.
        """

        def __init__(self) -> None:
            self._sections: dict[str, Any] = {}

try:
    from jugeo.runtime import RuntimeContext  # type: ignore
except ImportError:
    class RuntimeContext:  # type: ignore
        """Inline stub for RuntimeContext.

        The real RuntimeContext tracks interpreter state including the active
        namespace stack, live bindings, and pending judgment obligations.
        This stub is used when jugeo is not installed.
        """

        def __init__(self) -> None:
            self._frame_stack: list[dict[str, Any]] = []

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _new_event_id() -> str:
    """Generate a unique event identifier prefixed with ``ev_``.

    Returns:
        A string of the form ``ev_<10 hex chars>``, e.g. ``ev_3f9a12b4c8``.

    Example:
        >>> eid = _new_event_id()
        >>> eid.startswith("ev_")
        True
        >>> len(eid)
        13
    """
    return "ev_" + uuid.uuid4().hex[:10]


def _new_obs_id() -> str:
    """Generate a unique observation identifier prefixed with ``ob_``.

    Returns:
        A string of the form ``ob_<10 hex chars>``, e.g. ``ob_a1b2c3d4e5``.

    Example:
        >>> oid = _new_obs_id()
        >>> oid.startswith("ob_")
        True
        >>> len(oid)
        13
    """
    return "ob_" + uuid.uuid4().hex[:10]


def _hash_code(code: str) -> str:
    """Compute a short SHA-256 fingerprint of the given source code string.

    The hash serves as a stable identity for a code fragment across multiple
    exec/eval invocations so that repeated patterns can be detected without
    storing the raw source text in every event record.

    Args:
        code: The Python source code string to hash.  May be a complete
            module, a single expression, or an arbitrary snippet.

    Returns:
        The first 16 hex characters of the SHA-256 digest of the UTF-8
        encoding of *code*.  This gives 64 bits of collision resistance,
        which is sufficient for event de-duplication purposes.

    Example:
        >>> _hash_code("x = 1")
        '5e0a97d4e3...'  # illustrative — actual value will differ
        >>> _hash_code("") != _hash_code("x = 1")
        True
    """
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return digest[:16]


def _classify_code_complexity(code: str) -> int:
    """Estimate the cyclomatic-like complexity score of a Python code snippet.

    The score is a simple integer derived from structural features:
    - 1 point per non-empty, non-comment line
    - 2 points per ``for`` or ``while`` loop
    - 1 point per ``if`` or ``elif`` branch
    - 1 point per ``try`` block
    - 1 point per function or class definition
    - A floor of 1 is always returned.

    This heuristic is intentionally lightweight; it is used only to inform
    the boundedness classifier, not to replace a full static analysis.

    Args:
        code: The Python source code string to analyse.

    Returns:
        A positive integer complexity score.  Higher values indicate more
        complex code with greater potential for residual side-effects.

    Example:
        >>> _classify_code_complexity("x = 1")
        1
        >>> _classify_code_complexity("for i in range(10):\\n    if i > 5:\\n        x = i")
        6
    """
    lines = code.splitlines()
    score = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        score += 1  # every substantive line contributes 1
        if re.match(r"^\s*(for|while)\b", line):
            score += 2
        if re.match(r"^\s*(if|elif)\b", line):
            score += 1
        if re.match(r"^\s*try\b", line):
            score += 1
        if re.match(r"^\s*(def|class)\b", line):
            score += 1
    return max(1, score)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EventBoundedness(str, Enum):
    """Classification of an exec/eval event's effect on the ambient namespace.

    Under JuGeo's sheaf semantics, a *bounded* event is one whose local
    section restricts to the identity on the ambient namespace — it leaves
    no new bindings behind.  A *residual* event introduces at least one new
    binding that persists in the caller's namespace after the exec/eval call
    returns.  *Partially bounded* events fall between these extremes.

    Values:
        BOUNDED: The event introduced no new namespace bindings.
        RESIDUAL: The event introduced one or more new namespace bindings
            that persist beyond the call frame.
        PARTIALLY_BOUNDED: The event introduced a small number of new
            bindings (1–5), suggesting intentional but limited leakage.
        UNKNOWN: Classification could not be determined, typically because
            namespace snapshots were unavailable.
    """

    BOUNDED = "bounded"
    RESIDUAL = "residual"
    PARTIALLY_BOUNDED = "partially_bounded"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Frozen dataclasses (value types)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecEvent:
    """An immutable record of a single ``exec()`` call and its namespace effect.

    ExecEvent captures the full context of an exec invocation: the code
    fingerprint, before/after namespace snapshots, timing, and the
    boundedness verdict.  Because it is frozen, it can be stored in sets,
    used as dict keys, and safely shared across threads.

    Attributes:
        event_id: Unique identifier generated by ``_new_event_id()``.
        code_hash: SHA-256 fingerprint (16 hex chars) of the executed code.
        namespace_keys_before: Frozenset of names present in the target
            namespace *before* exec was called.
        namespace_keys_after: Frozenset of names present in the target
            namespace *after* exec returned.
        is_bounded: True iff no new keys were introduced (residual_keys is
            empty).
        residual_keys: Frozenset of names introduced by this exec call.
        exec_at: Unix timestamp (float) when exec was initiated.
        completed_at: Unix timestamp when exec completed, or None if the
            event has not yet been finalised.
        status: String status tag, e.g. ``"completed"``, ``"error"``,
            ``"pending"``.
        boundedness: EventBoundedness classification for this event.
    """

    event_id: str
    code_hash: str
    namespace_keys_before: frozenset[str]
    namespace_keys_after: frozenset[str]
    is_bounded: bool
    residual_keys: frozenset[str]
    exec_at: float
    completed_at: float | None
    status: str
    boundedness: EventBoundedness

    def duration(self) -> float:
        """Return elapsed wall-clock seconds between exec_at and completed_at.

        Returns:
            The duration in seconds, or 0.0 if the event has not completed.

        Example:
            >>> ev.duration()
            0.0023
        """
        if self.completed_at is None:
            return 0.0
        return self.completed_at - self.exec_at

    def new_key_count(self) -> int:
        """Return the number of new namespace keys introduced by this event.

        Returns:
            Integer count of residual_keys.

        Example:
            >>> ev.new_key_count()
            3
        """
        return len(self.residual_keys)

    def label(self) -> str:
        """Return a concise human-readable label for this event.

        Returns:
            A string of the form ``exec[<event_id>](<boundedness>)``.

        Example:
            >>> ev.label()
            'exec[ev_3f9a12b4c8](bounded)'
        """
        return f"exec[{self.event_id}]({self.boundedness.value})"

    def to_dict(self) -> dict[str, Any]:
        """Serialise this event to a JSON-compatible dictionary.

        Returns:
            A dict containing all fields of this ExecEvent, with frozensets
            converted to sorted lists and the boundedness enum to its string
            value.

        Example:
            >>> d = ev.to_dict()
            >>> d["event_id"]
            'ev_3f9a12b4c8'
        """
        return {
            "event_id": self.event_id,
            "code_hash": self.code_hash,
            "namespace_keys_before": sorted(self.namespace_keys_before),
            "namespace_keys_after": sorted(self.namespace_keys_after),
            "is_bounded": self.is_bounded,
            "residual_keys": sorted(self.residual_keys),
            "exec_at": self.exec_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "boundedness": self.boundedness.value,
            "duration": self.duration(),
            "new_key_count": self.new_key_count(),
            "label": self.label(),
        }

    def is_complete(self) -> bool:
        """Return True if this event has a completed_at timestamp and ``"completed"`` status.

        Returns:
            Boolean indicating completion.

        Example:
            >>> ev.is_complete()
            True
        """
        return self.completed_at is not None and self.status == "completed"


@dataclass(frozen=True, slots=True)
class EvalEvent:
    """An immutable record of a single ``eval()`` call and its observed effects.

    EvalEvent captures the expression fingerprint, the Python type name of
    the returned value, any detected side-effect keys, timing, and the
    boundedness verdict.  Like ExecEvent, it is frozen for safe sharing.

    Attributes:
        event_id: Unique identifier generated by ``_new_event_id()``.
        expression_hash: SHA-256 fingerprint (16 hex chars) of the evaluated
            expression string.
        result_type: The ``type(result).__name__`` of the value returned by
            eval, e.g. ``"int"``, ``"str"``, ``"NoneType"``.
        is_bounded: True iff the expression is judged to have no side effects.
        side_effects: Frozenset of namespace key names that were observed to
            change as a side effect of evaluation (may be empty).
        eval_at: Unix timestamp (float) when eval was initiated.
        boundedness: EventBoundedness classification for this event.
        complexity_score: Integer complexity score from
            ``_classify_code_complexity``.
    """

    event_id: str
    expression_hash: str
    result_type: str
    is_bounded: bool
    side_effects: frozenset[str]
    eval_at: float
    boundedness: EventBoundedness
    complexity_score: int

    def label(self) -> str:
        """Return a concise human-readable label for this eval event.

        Returns:
            A string of the form ``eval[<event_id>]→<result_type>``.

        Example:
            >>> ev.label()
            'eval[ev_a1b2c3d4e5]→int'
        """
        return f"eval[{self.event_id}]→{self.result_type}"

    def has_side_effects(self) -> bool:
        """Return True iff any side-effect keys were recorded for this event.

        Returns:
            Boolean.

        Example:
            >>> ev.has_side_effects()
            False
        """
        return len(self.side_effects) > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise this event to a JSON-compatible dictionary.

        Returns:
            A dict containing all fields, with frozensets as sorted lists.

        Example:
            >>> ev.to_dict()["result_type"]
            'int'
        """
        return {
            "event_id": self.event_id,
            "expression_hash": self.expression_hash,
            "result_type": self.result_type,
            "is_bounded": self.is_bounded,
            "side_effects": sorted(self.side_effects),
            "eval_at": self.eval_at,
            "boundedness": self.boundedness.value,
            "complexity_score": self.complexity_score,
            "label": self.label(),
            "has_side_effects": self.has_side_effects(),
            "age": self.age(),
        }

    def age(self) -> float:
        """Return the number of seconds since this eval event was recorded.

        Returns:
            Float seconds elapsed since eval_at.

        Example:
            >>> ev.age()
            4.217
        """
        return time.time() - self.eval_at


@dataclass(frozen=True, slots=True)
class BoundednessClassification:
    """An immutable record of a boundedness classification decision.

    Captures the verdict, confidence level, and rationale string produced
    by the classifier for a specific exec or eval event.

    Attributes:
        class_id: Unique identifier for this classification record.
        event_id: The event_id of the ExecEvent or EvalEvent being classified.
        boundedness: The EventBoundedness verdict.
        confidence: A float in [0.0, 1.0] expressing classifier confidence.
        rationale: A human-readable string explaining the verdict.
        classified_at: Unix timestamp when the classification was produced.
    """

    class_id: str
    event_id: str
    boundedness: EventBoundedness
    confidence: float
    rationale: str
    classified_at: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise this classification to a JSON-compatible dictionary.

        Returns:
            A dict with all fields, enum converted to string value.

        Example:
            >>> cls.to_dict()["confidence"]
            0.95
        """
        return {
            "class_id": self.class_id,
            "event_id": self.event_id,
            "boundedness": self.boundedness.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "classified_at": self.classified_at,
            "is_high_confidence": self.is_high_confidence(),
        }

    def is_high_confidence(self) -> bool:
        """Return True iff confidence is at or above the 0.8 threshold.

        Returns:
            Boolean.

        Example:
            >>> cls.is_high_confidence()
            True
        """
        return self.confidence >= 0.8


@dataclass(frozen=True, slots=True)
class ResidualObservation:
    """An immutable witness record produced when an event is observed.

    ResidualEventWitness creates one ResidualObservation per exec/eval event
    it is asked to observe.  The observation may later be flagged as residual
    via ``flag_residual``.

    Attributes:
        obs_id: Unique observation identifier generated by ``_new_obs_id()``.
        event_id: The event_id of the event being observed.
        event_type: Either ``"exec"`` or ``"eval"``.
        observed_at: Unix timestamp when the observation was created.
        flagged: True iff this observation has been flagged as residual.
        flag_reason: The human-readable reason for flagging, or None.
    """

    obs_id: str
    event_id: str
    event_type: str
    observed_at: float
    flagged: bool
    flag_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        """Serialise this observation to a JSON-compatible dictionary.

        Returns:
            A dict with all fields.

        Example:
            >>> obs.to_dict()["flagged"]
            False
        """
        return {
            "obs_id": self.obs_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "observed_at": self.observed_at,
            "flagged": self.flagged,
            "flag_reason": self.flag_reason,
            "age": self.age(),
        }

    def age(self) -> float:
        """Return seconds elapsed since this observation was recorded.

        Returns:
            Float seconds.

        Example:
            >>> obs.age()
            1.004
        """
        return time.time() - self.observed_at


# ---------------------------------------------------------------------------
# Mutable manager classes
# ---------------------------------------------------------------------------


@dataclass
class ExecBoundednessAnalyzer:
    """Analyses exec and eval events for boundedness and residual namespace effects.

    ExecBoundednessAnalyzer is the primary classification engine.  It accepts
    raw exec/eval call information, constructs ExecEvent and EvalEvent
    records, stores them internally, and exposes statistical summaries.

    Under JuGeo's sheaf semantics, the analyzer acts as the *local section
    evaluator*: for each exec/eval aperture it determines whether the local
    section (the exec'd code's effect on the namespace) can be extended to a
    global section (bounded) or introduces an irreconcilable residual.

    Attributes:
        _exec_events: Accumulated ExecEvent records.
        _eval_events: Accumulated EvalEvent records.
        _classifications: BoundednessClassification records produced for
            exec events.
        _residual_keys_by_event: Mapping from event_id to the frozenset of
            residual keys observed for that event.
    """

    _exec_events: list[ExecEvent] = field(default_factory=list)
    _eval_events: list[EvalEvent] = field(default_factory=list)
    _classifications: list[BoundednessClassification] = field(default_factory=list)
    _residual_keys_by_event: dict[str, frozenset[str]] = field(default_factory=dict)

    def analyze_exec(
        self,
        code: str,
        before_keys: set[str],
        after_keys: set[str],
    ) -> ExecEvent:
        """Analyse a completed exec() call and produce an ExecEvent record.

        Computes the code hash, derives the residual key set, determines
        boundedness, constructs an ExecEvent, stores it internally, and
        returns it.

        Args:
            code: The Python source code string that was executed.
            before_keys: The set of names present in the target namespace
                immediately *before* exec was called.
            after_keys: The set of names present in the target namespace
                immediately *after* exec returned.

        Returns:
            A fully populated ExecEvent with status ``"completed"``.

        Example:
            >>> analyzer = ExecBoundednessAnalyzer()
            >>> ev = analyzer.analyze_exec("x = 1", set(), {"x"})
            >>> ev.boundedness
            <EventBoundedness.PARTIALLY_BOUNDED: 'partially_bounded'>
        """
        now = time.time()
        code_hash = _hash_code(code)
        frozen_before = frozenset(before_keys)
        frozen_after = frozenset(after_keys)
        residual_keys = frozen_after - frozen_before
        is_bounded = len(residual_keys) == 0
        boundedness = self.classify_boundedness_raw(residual_keys)

        event = ExecEvent(
            event_id=_new_event_id(),
            code_hash=code_hash,
            namespace_keys_before=frozen_before,
            namespace_keys_after=frozen_after,
            is_bounded=is_bounded,
            residual_keys=residual_keys,
            exec_at=now,
            completed_at=time.time(),
            status="completed",
            boundedness=boundedness,
        )
        self._exec_events.append(event)
        self._residual_keys_by_event[event.event_id] = residual_keys

        _log.debug(
            "analyze_exec: event_id=%s code_hash=%s residual_keys=%r boundedness=%s",
            event.event_id,
            code_hash,
            sorted(residual_keys),
            boundedness.value,
        )
        return event

    def analyze_eval(self, expression: str, result_type: str) -> EvalEvent:
        """Analyse a completed eval() call and produce an EvalEvent record.

        Uses expression complexity and result type heuristics to assign a
        boundedness classification.  Pure-value result types (int, str, float,
        bool, NoneType) with low complexity scores are classified as BOUNDED.
        Complex expressions or mutable result types are classified as
        PARTIALLY_BOUNDED or RESIDUAL.

        Args:
            expression: The Python expression string that was evaluated.
            result_type: The ``type(result).__name__`` of the value returned
                by the eval call, e.g. ``"int"``, ``"NoneType"``, ``"list"``.

        Returns:
            A fully populated EvalEvent.

        Example:
            >>> ev = analyzer.analyze_eval("1 + 1", "int")
            >>> ev.boundedness
            <EventBoundedness.BOUNDED: 'bounded'>
        """
        now = time.time()
        expr_hash = _hash_code(expression)
        complexity = _classify_code_complexity(expression)

        pure_value_types = {"int", "float", "str", "bool", "NoneType", "bytes", "complex"}
        mutable_types = {"list", "dict", "set", "bytearray", "object"}

        if result_type in pure_value_types and complexity <= 3:
            boundedness = EventBoundedness.BOUNDED
            is_bounded = True
            side_effects: frozenset[str] = frozenset()
        elif result_type in mutable_types or complexity > 10:
            boundedness = EventBoundedness.RESIDUAL
            is_bounded = False
            side_effects = frozenset({"__implicit_mutation__"})
        elif complexity > 5:
            boundedness = EventBoundedness.PARTIALLY_BOUNDED
            is_bounded = False
            side_effects = frozenset()
        else:
            boundedness = EventBoundedness.BOUNDED
            is_bounded = True
            side_effects = frozenset()

        event = EvalEvent(
            event_id=_new_event_id(),
            expression_hash=expr_hash,
            result_type=result_type,
            is_bounded=is_bounded,
            side_effects=side_effects,
            eval_at=now,
            boundedness=boundedness,
            complexity_score=complexity,
        )
        self._eval_events.append(event)

        _log.debug(
            "analyze_eval: event_id=%s expr_hash=%s result_type=%s complexity=%d boundedness=%s",
            event.event_id,
            expr_hash,
            result_type,
            complexity,
            boundedness.value,
        )
        return event

    def classify_boundedness(self, exec_event: ExecEvent) -> EventBoundedness:
        """Classify the boundedness of an already-constructed ExecEvent.

        Delegates to the internal raw classifier using the event's residual
        key set.  This method is provided for post-hoc re-classification.

        Args:
            exec_event: The ExecEvent to classify.

        Returns:
            An EventBoundedness value.

        Example:
            >>> analyzer.classify_boundedness(ev)
            <EventBoundedness.BOUNDED: 'bounded'>
        """
        return self.classify_boundedness_raw(exec_event.residual_keys)

    def classify_boundedness_raw(self, residual_keys: frozenset[str]) -> EventBoundedness:
        """Classify boundedness directly from a residual key frozenset.

        Rules:
        - Empty residual_keys → BOUNDED
        - 1–5 residual keys → PARTIALLY_BOUNDED
        - >5 residual keys → RESIDUAL
        - (The UNKNOWN branch is unreachable via this path but kept for
          completeness; callers that cannot obtain namespace snapshots should
          pass it in directly.)

        Args:
            residual_keys: The frozenset of new namespace keys.

        Returns:
            An EventBoundedness value.
        """
        n = len(residual_keys)
        if n == 0:
            return EventBoundedness.BOUNDED
        if 1 <= n <= 5:
            return EventBoundedness.PARTIALLY_BOUNDED
        return EventBoundedness.RESIDUAL

    def compute_residual_keys(self, exec_event: ExecEvent) -> frozenset[str]:
        """Recompute the residual key set from an ExecEvent's namespace snapshots.

        This is a pure re-derivation and does not mutate internal state.  It
        is useful for verification after the fact.

        Args:
            exec_event: The ExecEvent whose residual keys should be recomputed.

        Returns:
            A frozenset of names that are in namespace_keys_after but not in
            namespace_keys_before.

        Example:
            >>> analyzer.compute_residual_keys(ev)
            frozenset({'x', 'y'})
        """
        return exec_event.namespace_keys_after - exec_event.namespace_keys_before

    def boundedness_ratio(self, events: list[ExecEvent]) -> float:
        """Compute the fraction of events that are classified as BOUNDED.

        Args:
            events: A list of ExecEvent objects to analyse.

        Returns:
            A float in [0.0, 1.0] representing the proportion of BOUNDED
            events.  Returns 0.0 if the list is empty.

        Example:
            >>> analyzer.boundedness_ratio(events)
            0.75
        """
        if not events:
            _log.debug("boundedness_ratio: empty event list, returning 0.0")
            return 0.0
        bounded_count = sum(
            1 for ev in events if ev.boundedness == EventBoundedness.BOUNDED
        )
        ratio = bounded_count / len(events)
        _log.debug(
            "boundedness_ratio: %d/%d = %.4f", bounded_count, len(events), ratio
        )
        return ratio

    def find_unbounded_events(self, events: list[ExecEvent]) -> list[ExecEvent]:
        """Return all events classified as RESIDUAL or PARTIALLY_BOUNDED.

        Args:
            events: A list of ExecEvent objects to filter.

        Returns:
            A list of ExecEvent objects whose boundedness is RESIDUAL or
            PARTIALLY_BOUNDED, in the order they appear in *events*.

        Example:
            >>> unbounded = analyzer.find_unbounded_events(all_events)
        """
        result = [
            ev
            for ev in events
            if ev.boundedness in (
                EventBoundedness.RESIDUAL,
                EventBoundedness.PARTIALLY_BOUNDED,
            )
        ]
        _log.debug("find_unbounded_events: found %d unbounded events", len(result))
        return result

    def summarize_side_effects(self, events: list[EvalEvent]) -> dict[str, Any]:
        """Summarise side-effect observations across a collection of EvalEvents.

        Counts events that reported at least one side-effect key, collects the
        union of all side-effect keys, and identifies the most frequently
        appearing side-effect key.

        Args:
            events: A list of EvalEvent objects.

        Returns:
            A dict with keys:
            - ``"total_events"``: int — total number of events examined.
            - ``"events_with_side_effects"``: int — count of events with
              at least one side-effect key.
            - ``"unique_side_effect_keys"``: list[str] — sorted union of
              all side-effect keys across all events.
            - ``"most_common_key"``: str | None — the key that appeared in
              the most events, or None if no side effects were observed.

        Example:
            >>> analyzer.summarize_side_effects(eval_events)
            {'total_events': 10, 'events_with_side_effects': 3, ...}
        """
        total = len(events)
        events_with_fx = [ev for ev in events if ev.has_side_effects()]
        all_keys: dict[str, int] = {}
        for ev in events_with_fx:
            for key in ev.side_effects:
                all_keys[key] = all_keys.get(key, 0) + 1

        most_common: str | None = None
        if all_keys:
            most_common = max(all_keys, key=lambda k: all_keys[k])

        summary = {
            "total_events": total,
            "events_with_side_effects": len(events_with_fx),
            "unique_side_effect_keys": sorted(all_keys.keys()),
            "most_common_key": most_common,
        }
        _log.debug("summarize_side_effects: %r", summary)
        return summary

    def export_events(self) -> list[dict[str, Any]]:
        """Serialise all recorded exec and eval events to a list of dicts.

        Returns:
            A list of JSON-compatible dictionaries.  Exec events appear first,
            followed by eval events.  Each dict includes a ``"kind"`` field
            set to ``"exec"`` or ``"eval"``.

        Example:
            >>> exported = analyzer.export_events()
            >>> exported[0]["kind"]
            'exec'
        """
        result: list[dict[str, Any]] = []
        for ev in self._exec_events:
            d = ev.to_dict()
            d["kind"] = "exec"
            result.append(d)
        for ev in self._eval_events:
            d = ev.to_dict()
            d["kind"] = "eval"
            result.append(d)
        _log.debug("export_events: exported %d total events", len(result))
        return result

    def stats(self) -> dict[str, Any]:
        """Return comprehensive statistics over all recorded events.

        Returns:
            A dict with keys:
            - ``"total_exec_events"``: int
            - ``"total_eval_events"``: int
            - ``"exec_boundedness_breakdown"``: dict[str, int] counting by
              EventBoundedness value
            - ``"eval_boundedness_breakdown"``: dict[str, int]
            - ``"bounded_exec_ratio"``: float
            - ``"average_residual_keys"``: float — mean residual key count
              across exec events
            - ``"max_residual_keys"``: int — maximum residual key count
            - ``"eval_with_side_effects"``: int

        Example:
            >>> analyzer.stats()["total_exec_events"]
            42
        """
        exec_breakdown: dict[str, int] = {b.value: 0 for b in EventBoundedness}
        for ev in self._exec_events:
            exec_breakdown[ev.boundedness.value] += 1

        eval_breakdown: dict[str, int] = {b.value: 0 for b in EventBoundedness}
        for ev in self._eval_events:
            eval_breakdown[ev.boundedness.value] += 1

        residual_counts = [len(ev.residual_keys) for ev in self._exec_events]
        avg_residual = (
            sum(residual_counts) / len(residual_counts) if residual_counts else 0.0
        )
        max_residual = max(residual_counts, default=0)

        eval_with_fx = sum(1 for ev in self._eval_events if ev.has_side_effects())

        result = {
            "total_exec_events": len(self._exec_events),
            "total_eval_events": len(self._eval_events),
            "exec_boundedness_breakdown": exec_breakdown,
            "eval_boundedness_breakdown": eval_breakdown,
            "bounded_exec_ratio": self.boundedness_ratio(self._exec_events),
            "average_residual_keys": round(avg_residual, 4),
            "max_residual_keys": max_residual,
            "eval_with_side_effects": eval_with_fx,
        }
        _log.debug("stats: %r", result)
        return result

    def most_residual_event(self) -> ExecEvent | None:
        """Return the ExecEvent with the greatest number of residual keys.

        If multiple events share the maximum residual key count, returns the
        first one encountered (earliest recorded).

        Returns:
            The ExecEvent with the most residual keys, or None if no exec
            events have been recorded.

        Example:
            >>> analyzer.most_residual_event().new_key_count()
            7
        """
        if not self._exec_events:
            _log.debug("most_residual_event: no exec events recorded")
            return None
        worst = max(self._exec_events, key=lambda ev: len(ev.residual_keys))
        _log.debug(
            "most_residual_event: %s with %d residual keys",
            worst.event_id,
            len(worst.residual_keys),
        )
        return worst

    def boundedness_histogram(self) -> dict[str, int]:
        """Return a histogram of exec event counts by EventBoundedness value.

        Returns:
            A dict mapping each EventBoundedness value string to the count
            of exec events with that classification.  All four values are
            present even if their count is 0.

        Example:
            >>> analyzer.boundedness_histogram()
            {'bounded': 10, 'residual': 2, 'partially_bounded': 5, 'unknown': 0}
        """
        histogram: dict[str, int] = {b.value: 0 for b in EventBoundedness}
        for ev in self._exec_events:
            histogram[ev.boundedness.value] += 1
        _log.debug("boundedness_histogram: %r", histogram)
        return histogram

    def detect_repeated_residuals(self) -> list[str]:
        """Identify namespace keys that appear as residuals in more than one event.

        A key that repeatedly escapes bounded classification is a strong
        signal of a persistent side-effect pattern worth investigating.

        Returns:
            A sorted list of key strings that appear as residual in two or
            more distinct exec events.

        Example:
            >>> analyzer.detect_repeated_residuals()
            ['_helper', 'result']
        """
        key_counts: dict[str, int] = {}
        for keys in self._residual_keys_by_event.values():
            for k in keys:
                key_counts[k] = key_counts.get(k, 0) + 1
        repeated = sorted(k for k, cnt in key_counts.items() if cnt >= 2)
        _log.debug("detect_repeated_residuals: %r", repeated)
        return repeated


@dataclass
class ResidualEventWitness:
    """Records and flags residual observations for exec and eval events.

    ResidualEventWitness acts as the *witness object* in the JuGeo sheaf
    framework: it records the fact of each exec/eval call, tracks which
    observations have been flagged as residual, and can emit a signed
    certificate summarising the witnessing session.

    Attributes:
        _observations: All ResidualObservation records, in creation order.
        _flagged: Mapping from event_id to the flag reason string for all
            events that have been flagged.
        _timeline: A deque of lightweight timeline entries (dicts) in
            chronological order.
    """

    _observations: list[ResidualObservation] = field(default_factory=list)
    _flagged: dict[str, str] = field(default_factory=dict)
    _timeline: deque = field(default_factory=deque)

    def observe_exec(self, event: ExecEvent) -> str:
        """Record a ResidualObservation for an ExecEvent.

        Creates a new ResidualObservation with ``event_type="exec"``, appends
        it to the internal list, adds a timeline entry, and logs the event.

        Args:
            event: The ExecEvent to observe.

        Returns:
            The obs_id string of the newly created observation.

        Example:
            >>> obs_id = witness.observe_exec(ev)
            >>> obs_id.startswith("ob_")
            True
        """
        obs_id = _new_obs_id()
        obs = ResidualObservation(
            obs_id=obs_id,
            event_id=event.event_id,
            event_type="exec",
            observed_at=time.time(),
            flagged=False,
            flag_reason=None,
        )
        self._observations.append(obs)
        self._timeline.append(
            {
                "obs_id": obs_id,
                "event_id": event.event_id,
                "event_type": "exec",
                "observed_at": obs.observed_at,
                "boundedness": event.boundedness.value,
            }
        )
        _log.debug(
            "observe_exec: obs_id=%s event_id=%s boundedness=%s",
            obs_id,
            event.event_id,
            event.boundedness.value,
        )
        return obs_id

    def observe_eval(self, event: EvalEvent) -> str:
        """Record a ResidualObservation for an EvalEvent.

        Creates a new ResidualObservation with ``event_type="eval"``, appends
        it to the internal list, adds a timeline entry, and logs the event.

        Args:
            event: The EvalEvent to observe.

        Returns:
            The obs_id string of the newly created observation.

        Example:
            >>> obs_id = witness.observe_eval(ev)
            >>> obs_id.startswith("ob_")
            True
        """
        obs_id = _new_obs_id()
        obs = ResidualObservation(
            obs_id=obs_id,
            event_id=event.event_id,
            event_type="eval",
            observed_at=time.time(),
            flagged=False,
            flag_reason=None,
        )
        self._observations.append(obs)
        self._timeline.append(
            {
                "obs_id": obs_id,
                "event_id": event.event_id,
                "event_type": "eval",
                "observed_at": obs.observed_at,
                "boundedness": event.boundedness.value,
            }
        )
        _log.debug(
            "observe_eval: obs_id=%s event_id=%s boundedness=%s",
            obs_id,
            event.event_id,
            event.boundedness.value,
        )
        return obs_id

    def flag_residual(self, event_id: str, reason: str) -> bool:
        """Flag the observation for a given event_id as residual.

        Because ResidualObservation is frozen, flagging is implemented by
        creating a replacement observation with ``flagged=True`` and the given
        reason, then substituting it in ``_observations`` in place of the
        original.

        Args:
            event_id: The event_id of the event whose observation should be
                flagged.
            reason: A human-readable string describing why the event is being
                flagged as residual.

        Returns:
            True if a matching observation was found and flagged, False if no
            observation with the given event_id exists.

        Example:
            >>> witness.flag_residual("ev_3f9a12b4c8", "introduced global `x`")
            True
        """
        self._flagged[event_id] = reason
        found = False
        for i, obs in enumerate(self._observations):
            if obs.event_id == event_id:
                new_obs = ResidualObservation(
                    obs_id=obs.obs_id,
                    event_id=obs.event_id,
                    event_type=obs.event_type,
                    observed_at=obs.observed_at,
                    flagged=True,
                    flag_reason=reason,
                )
                self._observations[i] = new_obs
                found = True
                _log.debug(
                    "flag_residual: flagged obs_id=%s event_id=%s reason=%r",
                    obs.obs_id,
                    event_id,
                    reason,
                )
                break
        if not found:
            _log.debug("flag_residual: no observation found for event_id=%s", event_id)
        return found

    def get_residual_observations(self) -> list[dict[str, Any]]:
        """Return serialised observations that have been flagged as residual.

        Returns:
            A list of dicts produced by ``ResidualObservation.to_dict()`` for
            each observation with ``flagged=True``.

        Example:
            >>> residuals = witness.get_residual_observations()
            >>> all(r["flagged"] for r in residuals)
            True
        """
        result = [obs.to_dict() for obs in self._observations if obs.flagged]
        _log.debug("get_residual_observations: %d flagged observations", len(result))
        return result

    def timeline(self) -> list[dict[str, Any]]:
        """Return all timeline entries sorted by observed_at timestamp.

        Returns:
            A list of timeline entry dicts in ascending time order.

        Example:
            >>> entries = witness.timeline()
            >>> entries[0]["event_type"]
            'exec'
        """
        sorted_tl = sorted(self._timeline, key=lambda e: e["observed_at"])
        _log.debug("timeline: %d entries", len(sorted_tl))
        return sorted_tl

    def residual_report(self) -> dict[str, Any]:
        """Generate a summary report of residual observations.

        Returns:
            A dict with keys:
            - ``"total_observed"``: int — total observations recorded.
            - ``"flagged_count"``: int — count of flagged observations.
            - ``"timeline_span_seconds"``: float — elapsed time from the
              earliest to the latest observation, or 0.0 if fewer than 2.
            - ``"top_residual_event_ids"``: list[str] — event_ids of the
              most recent flagged observations (up to 5).

        Example:
            >>> report = witness.residual_report()
            >>> report["total_observed"]
            12
        """
        total = len(self._observations)
        flagged = [obs for obs in self._observations if obs.flagged]

        if len(self._observations) >= 2:
            times = [obs.observed_at for obs in self._observations]
            span = max(times) - min(times)
        else:
            span = 0.0

        top_residual_ids = [
            obs.event_id for obs in sorted(flagged, key=lambda o: o.observed_at, reverse=True)
        ][:5]

        report = {
            "total_observed": total,
            "flagged_count": len(flagged),
            "timeline_span_seconds": round(span, 6),
            "top_residual_event_ids": top_residual_ids,
        }
        _log.debug("residual_report: %r", report)
        return report

    def generate_certificate(self) -> dict[str, Any]:
        """Generate a signed witness certificate summarising this session.

        The certificate contains a hash of all observation IDs (providing
        tamper evidence), a timestamp, a stats summary, and a ``"seal"`` field
        that encodes the certificate's own hash for self-verification.

        Returns:
            A dict with keys:
            - ``"certificate_id"``: str — a unique cert identifier.
            - ``"issued_at"``: float — Unix timestamp.
            - ``"observation_count"``: int
            - ``"flagged_count"``: int
            - ``"obs_id_hash"``: str — SHA-256 of all obs_ids concatenated.
            - ``"stats"``: dict — from residual_report()
            - ``"seal"``: str — SHA-256 of the serialised certificate body.

        Example:
            >>> cert = witness.generate_certificate()
            >>> len(cert["seal"])
            16
        """
        obs_ids_concat = "".join(obs.obs_id for obs in self._observations)
        obs_id_hash = hashlib.sha256(obs_ids_concat.encode("utf-8")).hexdigest()[:16]
        now = time.time()
        cert_id = "cert_" + uuid.uuid4().hex[:10]
        stats = self.residual_report()

        body = {
            "certificate_id": cert_id,
            "issued_at": now,
            "observation_count": len(self._observations),
            "flagged_count": len(self._flagged),
            "obs_id_hash": obs_id_hash,
            "stats": stats,
        }
        body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
        seal = hashlib.sha256(body_bytes).hexdigest()[:16]
        body["seal"] = seal

        _log.debug(
            "generate_certificate: cert_id=%s obs_id_hash=%s seal=%s",
            cert_id,
            obs_id_hash,
            seal,
        )
        return body

    def unflagged_observations(self) -> list[ResidualObservation]:
        """Return all observations that have not been flagged.

        Returns:
            A list of ResidualObservation objects with ``flagged=False``.

        Example:
            >>> unflagged = witness.unflagged_observations()
            >>> all(not o.flagged for o in unflagged)
            True
        """
        result = [obs for obs in self._observations if not obs.flagged]
        _log.debug("unflagged_observations: %d unflagged", len(result))
        return result

    def observation_count(self) -> int:
        """Return the total number of observations recorded.

        Returns:
            Integer count.

        Example:
            >>> witness.observation_count()
            7
        """
        return len(self._observations)

    def flagged_count(self) -> int:
        """Return the number of events that have been flagged as residual.

        Returns:
            Integer count of entries in ``_flagged``.

        Example:
            >>> witness.flagged_count()
            2
        """
        return len(self._flagged)


@dataclass
class ExecEvalBoundedResidualCoordinator:
    """Top-level coordinator that wires together analysis and witnessing.

    ExecEvalBoundedResidualCoordinator is the façade for the entire s03
    subsystem.  Callers pass raw exec/eval call information to
    ``process_exec`` and ``process_eval``; the coordinator delegates to the
    embedded ``ExecBoundednessAnalyzer`` and ``ResidualEventWitness``,
    automatically flags non-bounded events, and exposes unified reporting.

    Attributes:
        analyzer: The ExecBoundednessAnalyzer instance.
        witness: The ResidualEventWitness instance.
        _session_id: A short hex string identifying this coordinator session.
        _created_at: Unix timestamp when this coordinator was instantiated.
    """

    analyzer: ExecBoundednessAnalyzer = field(default_factory=ExecBoundednessAnalyzer)
    witness: ResidualEventWitness = field(default_factory=ResidualEventWitness)
    _session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _created_at: float = field(default_factory=time.time)

    def process_exec(
        self,
        code: str,
        before_keys: set[str],
        after_keys: set[str],
    ) -> dict[str, Any]:
        """Process a completed exec() call end-to-end.

        Calls ``analyzer.analyze_exec``, passes the resulting ExecEvent to
        ``witness.observe_exec``, and if the event is not bounded automatically
        flags it with a reason derived from the residual key set.

        Args:
            code: The executed Python source code string.
            before_keys: Namespace keys before exec.
            after_keys: Namespace keys after exec.

        Returns:
            A summary dict with keys:
            - ``"event"``: dict — the serialised ExecEvent.
            - ``"obs_id"``: str — the observation ID from the witness.
            - ``"flagged"``: bool — whether the event was auto-flagged.
            - ``"session_id"``: str — this coordinator's session ID.

        Example:
            >>> coord = ExecEvalBoundedResidualCoordinator()
            >>> result = coord.process_exec("x = 1 + 2", set(), {"x"})
            >>> result["flagged"]
            True
        """
        event = self.analyzer.analyze_exec(code, before_keys, after_keys)
        obs_id = self.witness.observe_exec(event)
        flagged = False
        if not event.is_bounded:
            reason = (
                f"exec introduced {event.new_key_count()} residual key(s): "
                + ", ".join(sorted(event.residual_keys))
            )
            self.witness.flag_residual(event.event_id, reason)
            flagged = True

        _log.debug(
            "process_exec: event_id=%s obs_id=%s flagged=%s",
            event.event_id,
            obs_id,
            flagged,
        )
        return {
            "event": event.to_dict(),
            "obs_id": obs_id,
            "flagged": flagged,
            "session_id": self._session_id,
        }

    def process_eval(self, expression: str, result_type: str) -> dict[str, Any]:
        """Process a completed eval() call end-to-end.

        Calls ``analyzer.analyze_eval``, passes the resulting EvalEvent to
        ``witness.observe_eval``, and if the event has side effects
        automatically flags it.

        Args:
            expression: The evaluated Python expression string.
            result_type: The ``type(result).__name__`` string.

        Returns:
            A summary dict with keys:
            - ``"event"``: dict — the serialised EvalEvent.
            - ``"obs_id"``: str — the observation ID from the witness.
            - ``"flagged"``: bool — whether the event was auto-flagged.
            - ``"session_id"``: str — this coordinator's session ID.

        Example:
            >>> result = coord.process_eval("[x for x in range(5)]", "list")
            >>> result["flagged"]
            True
        """
        event = self.analyzer.analyze_eval(expression, result_type)
        obs_id = self.witness.observe_eval(event)
        flagged = False
        if event.has_side_effects():
            reason = (
                f"eval produced side effects: "
                + ", ".join(sorted(event.side_effects))
            )
            self.witness.flag_residual(event.event_id, reason)
            flagged = True

        _log.debug(
            "process_eval: event_id=%s obs_id=%s flagged=%s",
            event.event_id,
            obs_id,
            flagged,
        )
        return {
            "event": event.to_dict(),
            "obs_id": obs_id,
            "flagged": flagged,
            "session_id": self._session_id,
        }

    def bounded_fraction(self) -> float:
        """Return the fraction of exec events that are classified as BOUNDED.

        Delegates to ``analyzer.boundedness_ratio`` over all recorded exec
        events.

        Returns:
            A float in [0.0, 1.0].

        Example:
            >>> coord.bounded_fraction()
            0.6
        """
        return self.analyzer.boundedness_ratio(self.analyzer._exec_events)

    def residual_summary(self) -> dict[str, Any]:
        """Return a combined residual summary from the witness and analyzer.

        Returns:
            A dict combining:
            - ``"flagged_observations"``: list[dict] from the witness.
            - ``"repeated_residual_keys"``: list[str] from the analyzer.
            - ``"boundedness_histogram"``: dict[str, int] from the analyzer.
            - ``"session_id"``: str

        Example:
            >>> coord.residual_summary()["session_id"]
            'a3b4c5d6e7f8'
        """
        return {
            "flagged_observations": self.witness.get_residual_observations(),
            "repeated_residual_keys": self.analyzer.detect_repeated_residuals(),
            "boundedness_histogram": self.analyzer.boundedness_histogram(),
            "session_id": self._session_id,
        }

    def full_report(self) -> dict[str, Any]:
        """Generate a comprehensive report combining all subsystem outputs.

        Returns:
            A dict with keys:
            - ``"session_id"``: str
            - ``"session_age_seconds"``: float — elapsed time since creation.
            - ``"analyzer_stats"``: dict — from ``analyzer.stats()``.
            - ``"witness_report"``: dict — from ``witness.residual_report()``.
            - ``"witness_certificate"``: dict — from
              ``witness.generate_certificate()``.
            - ``"bounded_fraction"``: float
            - ``"residual_summary"``: dict — from ``residual_summary()``.
            - ``"exported_events"``: list[dict] — from
              ``analyzer.export_events()``.

        Example:
            >>> report = coord.full_report()
            >>> "analyzer_stats" in report
            True
        """
        report = {
            "session_id": self._session_id,
            "session_age_seconds": round(time.time() - self._created_at, 4),
            "analyzer_stats": self.analyzer.stats(),
            "witness_report": self.witness.residual_report(),
            "witness_certificate": self.witness.generate_certificate(),
            "bounded_fraction": self.bounded_fraction(),
            "residual_summary": self.residual_summary(),
            "exported_events": self.analyzer.export_events(),
        }
        _log.debug(
            "full_report: session_id=%s exec=%d eval=%d",
            self._session_id,
            self.exec_count(),
            self.eval_count(),
        )
        return report

    def reset(self) -> None:
        """Reinitialise the analyzer and witness, discarding all recorded data.

        The session_id and created_at fields are preserved (they identify this
        coordinator instance, not its data).  After reset, the coordinator
        behaves as if freshly constructed.

        Example:
            >>> coord.reset()
            >>> coord.exec_count()
            0
        """
        self.analyzer = ExecBoundednessAnalyzer()
        self.witness = ResidualEventWitness()
        _log.debug("reset: analyzer and witness reinitialised for session %s", self._session_id)

    def exec_count(self) -> int:
        """Return the total number of exec events recorded in the analyzer.

        Returns:
            Integer count.

        Example:
            >>> coord.exec_count()
            5
        """
        return len(self.analyzer._exec_events)

    def eval_count(self) -> int:
        """Return the total number of eval events recorded in the analyzer.

        Returns:
            Integer count.

        Example:
            >>> coord.eval_count()
            3
        """
        return len(self.analyzer._eval_events)


__all__ = [
    "EventBoundedness",
    "ExecEvent",
    "EvalEvent",
    "BoundednessClassification",
    "ExecBoundednessAnalyzer",
    "ResidualObservation",
    "ResidualEventWitness",
    "ExecEvalBoundedResidualCoordinator",
]

# copilot: s03 — exec and eval as Bounded or Residual Events (Ch23 §3)
