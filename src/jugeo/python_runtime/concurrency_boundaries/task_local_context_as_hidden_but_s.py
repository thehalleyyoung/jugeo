from __future__ import annotations

"""task_local_context_as_hidden_but_s — Task-Local Context as Hidden but Structured Input.

Theory reference: Ch24 §2

contextvars.ContextVar bindings form a hidden but structured input to judgment predicates.
Each task carries its own copy of the context, making those bindings invisible across
task boundaries while still influencing the outcome of lookups.  This module models
that hidden structure via the Coordinator-Analyzer-Witness pattern.
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

try:
    from jugeo.core.context import ContextVar as JugeoContextVar  # type: ignore
except Exception:  # pragma: no cover
    class JugeoContextVar:  # type: ignore
        """Inline stub for jugeo.core.context.ContextVar."""
        def __init__(self, name: str) -> None:
            self.name = name
        def get(self, default: object = None) -> object:
            return default

try:
    from jugeo.sheaf.section import SectionGerm  # type: ignore
except Exception:  # pragma: no cover
    class SectionGerm:  # type: ignore
        """Inline stub for jugeo.sheaf.section.SectionGerm."""
        def __init__(self, data: dict | None = None) -> None:
            self._data: dict = data or {}
        def to_dict(self) -> dict:
            return self._data

try:
    from jugeo.evidence.impact import ImpactRecord  # type: ignore
except Exception:  # pragma: no cover
    class ImpactRecord:  # type: ignore
        """Inline stub for jugeo.evidence.impact.ImpactRecord."""
        def __init__(self, impact: str) -> None:
            self.impact = impact
        def describe(self) -> str:
            return self.impact

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ContextVisibility(str, Enum):
    """Visibility classification for a context binding from an outside observer.

    Values indicate how much of a binding's existence and value is externally
    observable relative to the task that owns the binding.

    Example::

        vis = ContextVisibility.PARTIALLY_HIDDEN
        print(vis.value)  # "PARTIALLY_HIDDEN"
    """

    FULLY_VISIBLE = "FULLY_VISIBLE"
    PARTIALLY_HIDDEN = "PARTIALLY_HIDDEN"
    FULLY_HIDDEN = "FULLY_HIDDEN"
    INHERITED = "INHERITED"
    SHADOWED = "SHADOWED"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _new_binding_id() -> str:
    """Return a short unique binding identifier.

    Returns:
        12-character hex string from uuid4.
    """
    return uuid.uuid4().hex[:12]


def _new_record_id() -> str:
    """Return a short unique record identifier.

    Returns:
        12-character hex string from uuid4.
    """
    return uuid.uuid4().hex[:12]


def _fingerprint(data: object) -> str:
    """Produce a deterministic SHA-256 digest of *data* serialised as JSON.

    Args:
        data: Any JSON-serialisable value.

    Returns:
        64-character hex string.
    """
    raw = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _impact_label(hidden_count: int, visible_count: int) -> str:
    """Derive a qualitative judgment-impact label from hidden/visible key counts.

    Args:
        hidden_count: Number of hidden binding keys for the task.
        visible_count: Number of visible binding keys for the task.

    Returns:
        One of: ``"none"``, ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.
    """
    total = hidden_count + visible_count
    if total == 0:
        return "none"
    ratio = hidden_count / total
    if ratio == 0.0:
        return "none"
    if ratio < 0.25:
        return "low"
    if ratio < 0.5:
        return "medium"
    if ratio < 0.75:
        return "high"
    return "critical"


def _sanitise_var_name(name: str) -> str:
    """Ensure a variable name is a valid Python identifier fragment.

    Args:
        name: The variable name string to sanitise.

    Returns:
        The sanitised name with invalid characters replaced by underscores.
    """
    return re.sub(r"[^a-zA-Z0-9_]", "_", name.strip()) or "_unnamed"


# ---------------------------------------------------------------------------
# Frozen record dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ContextBinding:
    """An immutable snapshot of one contextvars.ContextVar binding within a task.

    Attributes:
        binding_id: Unique identifier for this binding record.
        var_name: The name of the ContextVar (sanitised).
        binding_key: The sheaf binding key this var maps to.
        task_id: Identifier of the task that owns this binding.
        value_type_name: __qualname__ of the bound value's type.
        is_hidden: True when the binding is not visible across task boundaries.
        created_at: Monotonic timestamp of record creation.
        parent_binding_id: Optional parent binding from which this was inherited.

    Example::

        cb = ContextBinding(
            binding_id="abc123",
            var_name="request_id",
            binding_key="ctx:request",
            task_id="task-001",
            value_type_name="str",
            is_hidden=True,
            created_at=time.monotonic(),
            parent_binding_id=None,
        )
    """

    binding_id: str
    var_name: str
    binding_key: str
    task_id: str
    value_type_name: str
    is_hidden: bool
    created_at: float
    parent_binding_id: str | None

    def to_dict(self) -> dict[str, object]:
        """Serialise this binding to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "binding_id": self.binding_id,
            "var_name": self.var_name,
            "binding_key": self.binding_key,
            "task_id": self.task_id,
            "value_type_name": self.value_type_name,
            "is_hidden": self.is_hidden,
            "created_at": self.created_at,
            "parent_binding_id": self.parent_binding_id,
        }

    def fingerprint(self) -> str:
        """Return a deterministic content fingerprint.

        Returns:
            64-character hex string.
        """
        return _fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class HiddenInputRecord:
    """An immutable record capturing the hidden input profile of one task.

    Attributes:
        record_id: Unique identifier.
        task_id: The task whose hidden inputs are recorded.
        hidden_keys: Frozenset of binding keys classified as hidden.
        visible_keys: Frozenset of binding keys classified as visible.
        judgment_impact: Qualitative label for how much the hidden keys affect
            predicate evaluation (one of none/low/medium/high/critical).
        detected_at: Monotonic timestamp of record creation.

    Example::

        hir = HiddenInputRecord(
            record_id="xyz789",
            task_id="task-001",
            hidden_keys=frozenset(["ctx:user"]),
            visible_keys=frozenset(["ctx:trace"]),
            judgment_impact="medium",
            detected_at=time.monotonic(),
        )
    """

    record_id: str
    task_id: str
    hidden_keys: frozenset[str]
    visible_keys: frozenset[str]
    judgment_impact: str
    detected_at: float

    def to_dict(self) -> dict[str, object]:
        """Serialise this record to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "record_id": self.record_id,
            "task_id": self.task_id,
            "hidden_keys": sorted(self.hidden_keys),
            "visible_keys": sorted(self.visible_keys),
            "judgment_impact": self.judgment_impact,
            "detected_at": self.detected_at,
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

@dataclass
class ContextBindingAnalyzer:
    """Analyzes context bindings for hidden structure within tasks.

    Maintains a registry of ContextBinding objects and provides methods
    for classifying visibility, detecting shadowing, computing impact, and
    tracing inheritance chains.

    Attributes:
        _bindings: All registered bindings keyed by binding_id.
        _task_index: Maps task_id → list of binding_ids for that task.
        _key_index: Maps binding_key → list of binding_ids with that key.
        _hidden_records: All HiddenInputRecords produced, keyed by record_id.

    Example::

        analyzer = ContextBindingAnalyzer()
        b = analyzer.register_binding("req_id", "ctx:req", "task-1", "str", True)
    """

    _bindings: dict[str, ContextBinding] = field(default_factory=dict)
    _task_index: dict[str, list[str]] = field(default_factory=dict)
    _key_index: dict[str, list[str]] = field(default_factory=dict)
    _hidden_records: dict[str, HiddenInputRecord] = field(default_factory=dict)

    def register_binding(
        self,
        var_name: str,
        binding_key: str,
        task_id: str,
        value_type: str,
        is_hidden: bool,
        parent_id: str | None = None,
    ) -> ContextBinding:
        """Register a new context binding for a task.

        Args:
            var_name: The ContextVar's name.
            binding_key: The sheaf binding key.
            task_id: The owning task identifier.
            value_type: The bound value's type name.
            is_hidden: Whether this binding is task-local (hidden).
            parent_id: Optional parent binding_id (for inherited bindings).

        Returns:
            The newly created ContextBinding.

        Raises:
            ValueError: If *binding_key* or *task_id* are empty.

        Example::

            b = analyzer.register_binding("user", "ctx:user", "t1", "str", True)
        """
        if not binding_key:
            raise ValueError("binding_key must not be empty")
        if not task_id:
            raise ValueError("task_id must not be empty")

        safe_name = _sanitise_var_name(var_name)
        binding = ContextBinding(
            binding_id=_new_binding_id(),
            var_name=safe_name,
            binding_key=binding_key,
            task_id=task_id,
            value_type_name=value_type or "object",
            is_hidden=is_hidden,
            created_at=time.monotonic(),
            parent_binding_id=parent_id,
        )
        self._bindings[binding.binding_id] = binding
        self._task_index.setdefault(task_id, []).append(binding.binding_id)
        self._key_index.setdefault(binding_key, []).append(binding.binding_id)
        _log.debug(
            "Registered binding %s (%s) for task %s; hidden=%s",
            safe_name, binding_key, task_id, is_hidden,
        )
        return binding

    def classify_visibility(self, binding: ContextBinding) -> ContextVisibility:
        """Classify the visibility of a single ContextBinding.

        Applies the following rules in order:
        1. If the binding has a parent and the parent is hidden → INHERITED.
        2. If the binding key appears in more than one task → SHADOWED when another
           task's binding hides the same key.
        3. If is_hidden is True → FULLY_HIDDEN.
        4. If the binding key also appears with is_hidden=True in any other binding
           for the same task → PARTIALLY_HIDDEN.
        5. Otherwise → FULLY_VISIBLE.

        Args:
            binding: The binding to classify.

        Returns:
            A ContextVisibility value.

        Example::

            vis = analyzer.classify_visibility(b)
        """
        # Rule 1: inheritance
        if binding.parent_binding_id is not None:
            parent = self._bindings.get(binding.parent_binding_id)
            if parent is not None and parent.is_hidden:
                return ContextVisibility.INHERITED

        # Rule 2: shadowing — same key, different task, hidden
        same_key_ids = self._key_index.get(binding.binding_key, [])
        for bid in same_key_ids:
            other = self._bindings.get(bid)
            if other is None or other.binding_id == binding.binding_id:
                continue
            if other.task_id != binding.task_id and other.is_hidden:
                return ContextVisibility.SHADOWED

        # Rule 3: directly hidden
        if binding.is_hidden:
            return ContextVisibility.FULLY_HIDDEN

        # Rule 4: partially hidden (mixed visibility in same task)
        task_bindings = [
            self._bindings[bid]
            for bid in self._task_index.get(binding.task_id, [])
            if bid in self._bindings
        ]
        hidden_in_task = any(b.is_hidden for b in task_bindings)
        if hidden_in_task:
            return ContextVisibility.PARTIALLY_HIDDEN

        return ContextVisibility.FULLY_VISIBLE

    def find_hidden_inputs(self, task_id: str) -> list[ContextBinding]:
        """Return all hidden bindings registered for *task_id*.

        Args:
            task_id: The task whose hidden bindings are sought.

        Returns:
            List of ContextBinding objects with is_hidden=True for the task.

        Example::

            hidden = analyzer.find_hidden_inputs("task-001")
        """
        ids = self._task_index.get(task_id, [])
        return [
            self._bindings[bid]
            for bid in ids
            if bid in self._bindings and self._bindings[bid].is_hidden
        ]

    def compute_judgment_impact(self, task_id: str) -> str:
        """Compute the qualitative judgment impact for a task's hidden inputs.

        Args:
            task_id: The task to assess.

        Returns:
            One of ``"none"``, ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.

        Example::

            impact = analyzer.compute_judgment_impact("task-001")
        """
        ids = self._task_index.get(task_id, [])
        bindings = [self._bindings[bid] for bid in ids if bid in self._bindings]
        hidden_count = sum(1 for b in bindings if b.is_hidden)
        visible_count = sum(1 for b in bindings if not b.is_hidden)
        label = _impact_label(hidden_count, visible_count)

        # Produce and store a HiddenInputRecord for this assessment.
        hidden_keys = frozenset(b.binding_key for b in bindings if b.is_hidden)
        visible_keys = frozenset(b.binding_key for b in bindings if not b.is_hidden)
        rec = HiddenInputRecord(
            record_id=_new_record_id(),
            task_id=task_id,
            hidden_keys=hidden_keys,
            visible_keys=visible_keys,
            judgment_impact=label,
            detected_at=time.monotonic(),
        )
        self._hidden_records[rec.record_id] = rec
        return label

    def binding_inheritance_chain(self, binding_id: str) -> list[ContextBinding]:
        """Walk the parent chain from *binding_id* to its root binding.

        Args:
            binding_id: The starting binding.

        Returns:
            Ordered list starting at *binding_id* and ending at the root.
            If the binding does not exist, returns an empty list.

        Raises:
            RuntimeError: If a cycle is detected in parent pointers.

        Example::

            chain = analyzer.binding_inheritance_chain("abc123")
        """
        chain: list[ContextBinding] = []
        visited: set[str] = set()
        current_id: str | None = binding_id
        while current_id is not None:
            if current_id in visited:
                raise RuntimeError(
                    f"Cycle detected in binding inheritance chain at {current_id!r}"
                )
            binding = self._bindings.get(current_id)
            if binding is None:
                break
            visited.add(current_id)
            chain.append(binding)
            current_id = binding.parent_binding_id
        return chain

    def detect_shadowing(self, task_id: str) -> list[dict[str, object]]:
        """Find all shadowing situations for bindings in *task_id*.

        A shadowing situation occurs when a task overrides a key that another
        task has already registered under the same binding_key.

        Args:
            task_id: The task to inspect for shadowing.

        Returns:
            List of dicts with keys: ``binding_id``, ``binding_key``,
            ``shadowed_by``, ``other_task_id``.

        Example::

            shadows = analyzer.detect_shadowing("task-001")
        """
        ids = self._task_index.get(task_id, [])
        results: list[dict[str, object]] = []
        for bid in ids:
            binding = self._bindings.get(bid)
            if binding is None:
                continue
            same_key = self._key_index.get(binding.binding_key, [])
            for other_bid in same_key:
                other = self._bindings.get(other_bid)
                if other is None or other.task_id == task_id:
                    continue
                results.append({
                    "binding_id": binding.binding_id,
                    "binding_key": binding.binding_key,
                    "shadowed_by": other_bid,
                    "other_task_id": other.task_id,
                })
        return results

    def export_bindings(self) -> list[dict[str, object]]:
        """Export all registered bindings as plain dicts.

        Returns:
            List of serialised ContextBinding dicts.
        """
        return [b.to_dict() for b in self._bindings.values()]

    def stats(self) -> dict[str, object]:
        """Return summary statistics for the analyzer.

        Returns:
            Dict with keys: ``total_bindings``, ``hidden_bindings``,
            ``visible_bindings``, ``tasks``, ``binding_keys``,
            ``hidden_input_records``.
        """
        all_b = list(self._bindings.values())
        hidden = sum(1 for b in all_b if b.is_hidden)
        return {
            "total_bindings": len(all_b),
            "hidden_bindings": hidden,
            "visible_bindings": len(all_b) - hidden,
            "tasks": list(self._task_index.keys()),
            "binding_keys": list(self._key_index.keys()),
            "hidden_input_records": len(self._hidden_records),
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

@dataclass
class HiddenContextWitness:
    """Witnesses hidden context inputs and their impacts on judgments.

    Stores observations about hidden bindings, flags problematic inputs, and
    generates exportable evidence bundles.

    Attributes:
        _observations: All observation dicts keyed by obs_id.
        _task_obs_index: Maps task_id → list of obs_ids.
        _flags: Flagged binding_ids → reason strings.
        _impact_obs: Impact observation dicts keyed by obs_id.
        _obs_counter: Monotonic observation sequence counter.

    Example::

        witness = HiddenContextWitness()
        obs_id = witness.observe_binding(binding)
    """

    _observations: dict[str, dict[str, object]] = field(default_factory=dict)
    _task_obs_index: dict[str, list[str]] = field(default_factory=dict)
    _flags: dict[str, str] = field(default_factory=dict)
    _impact_obs: dict[str, dict[str, object]] = field(default_factory=dict)
    _obs_counter: int = field(default=0)

    def observe_binding(self, binding: ContextBinding) -> str:
        """Record an observation of a ContextBinding.

        Args:
            binding: The ContextBinding to observe.

        Returns:
            The obs_id for this witness entry.

        Example::

            oid = witness.observe_binding(b)
        """
        obs_id = f"obs_{_new_record_id()}"
        self._obs_counter += 1
        obs: dict[str, object] = {
            "obs_id": obs_id,
            "binding_id": binding.binding_id,
            "var_name": binding.var_name,
            "binding_key": binding.binding_key,
            "task_id": binding.task_id,
            "is_hidden": binding.is_hidden,
            "value_type_name": binding.value_type_name,
            "seq": self._obs_counter,
            "observed_at": time.monotonic(),
        }
        self._observations[obs_id] = obs
        self._task_obs_index.setdefault(binding.task_id, []).append(obs_id)
        _log.debug("HiddenContextWitness: observed binding %s → obs %s", binding.binding_id, obs_id)
        return obs_id

    def flag_hidden_input(self, binding_id: str, reason: str) -> bool:
        """Flag a binding_id as a problematic hidden input with a reason.

        Args:
            binding_id: The binding to flag.
            reason: Explanation of why this binding is flagged.

        Returns:
            True if the flag was newly added; False if already flagged.

        Example::

            was_new = witness.flag_hidden_input("abc123", "undeclared dependency")
        """
        if binding_id in self._flags:
            _log.debug("Binding %s already flagged; updating reason", binding_id)
            self._flags[binding_id] = reason
            return False
        self._flags[binding_id] = reason
        _log.warning("Flagged hidden input binding %s: %s", binding_id, reason)
        return True

    def observe_judgment_impact(self, record: HiddenInputRecord) -> str:
        """Record an observation of a HiddenInputRecord's judgment impact.

        Args:
            record: The HiddenInputRecord to observe.

        Returns:
            The obs_id for this impact observation.

        Example::

            oid = witness.observe_judgment_impact(hir)
        """
        obs_id = f"imp_{_new_record_id()}"
        self._obs_counter += 1
        obs: dict[str, object] = {
            "obs_id": obs_id,
            "record_id": record.record_id,
            "task_id": record.task_id,
            "judgment_impact": record.judgment_impact,
            "hidden_key_count": len(record.hidden_keys),
            "visible_key_count": len(record.visible_keys),
            "seq": self._obs_counter,
            "observed_at": time.monotonic(),
        }
        self._impact_obs[obs_id] = obs
        return obs_id

    def get_hidden_observations(self, task_id: str) -> list[dict[str, object]]:
        """Return all witness observations for hidden bindings in *task_id*.

        Args:
            task_id: The task to query.

        Returns:
            List of observation dicts where ``is_hidden`` is True.

        Example::

            obs = witness.get_hidden_observations("task-001")
        """
        ids = self._task_obs_index.get(task_id, [])
        return [
            self._observations[oid]
            for oid in ids
            if oid in self._observations and self._observations[oid].get("is_hidden")
        ]

    def visibility_summary(self) -> dict[str, int]:
        """Summarise observation counts by is_hidden value.

        Returns:
            Dict with keys ``"hidden"`` and ``"visible"``.
        """
        hidden = sum(1 for o in self._observations.values() if o.get("is_hidden"))
        return {"hidden": hidden, "visible": len(self._observations) - hidden}

    def impact_report(self) -> dict[str, int]:
        """Summarise impact observations by judgment_impact label.

        Returns:
            Dict mapping impact label → count.
        """
        report: dict[str, int] = {}
        for obs in self._impact_obs.values():
            label = str(obs.get("judgment_impact", "none"))
            report[label] = report.get(label, 0) + 1
        return report

    def generate_certificate(self) -> dict[str, object]:
        """Generate a witness certificate for all hidden context evidence.

        Returns:
            Dict with ``cert_id``, ``total_observations``, ``flagged_bindings``,
            ``impact_report``, ``visibility_summary``, ``issued_at``,
            ``fingerprint``.

        Example::

            cert = witness.generate_certificate()
        """
        payload: dict[str, object] = {
            "cert_id": f"cert_{_new_record_id()}",
            "total_observations": len(self._observations),
            "flagged_bindings": len(self._flags),
            "impact_report": self.impact_report(),
            "visibility_summary": self.visibility_summary(),
            "issued_at": time.monotonic(),
        }
        payload["fingerprint"] = _fingerprint(payload)
        return payload


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

@dataclass
class TaskLocalContextHiddenCoordinator:
    """Orchestrates context-binding registration, analysis, and witnessing.

    Combines a ContextBindingAnalyzer and a HiddenContextWitness to provide a
    single entry point for the full workflow around task-local hidden inputs.

    Attributes:
        _analyzer: The underlying ContextBindingAnalyzer.
        _witness: The underlying HiddenContextWitness.
        _session_id: Unique identifier for this coordinator session.

    Example::

        coord = TaskLocalContextHiddenCoordinator()
        coord.bind_context_var("user", "ctx:user", "task-1", "str", True)
    """

    _analyzer: ContextBindingAnalyzer = field(default_factory=ContextBindingAnalyzer)
    _witness: HiddenContextWitness = field(default_factory=HiddenContextWitness)
    _session_id: str = field(default_factory=lambda: _new_record_id())

    def bind_context_var(
        self,
        var_name: str,
        binding_key: str,
        task_id: str,
        value_type: str,
        is_hidden: bool,
        parent_id: str | None = None,
    ) -> dict[str, object]:
        """Register a context var binding and witness it.

        Args:
            var_name: Name of the ContextVar.
            binding_key: Sheaf binding key.
            task_id: Owning task identifier.
            value_type: Type name of the bound value.
            is_hidden: Whether the binding is task-local.
            parent_id: Optional parent binding_id.

        Returns:
            Dict with ``binding_id``, ``var_name``, ``is_hidden``,
            ``observation_id``.

        Example::

            r = coord.bind_context_var("req_id", "ctx:req", "t1", "str", False)
        """
        binding = self._analyzer.register_binding(
            var_name, binding_key, task_id, value_type, is_hidden, parent_id
        )
        obs_id = self._witness.observe_binding(binding)
        if is_hidden:
            self._witness.flag_hidden_input(binding.binding_id, "task-local hidden input")
        return {
            "binding_id": binding.binding_id,
            "var_name": binding.var_name,
            "is_hidden": binding.is_hidden,
            "observation_id": obs_id,
        }

    def assess_hidden_inputs(self, task_id: str) -> dict[str, object]:
        """Assess the hidden input profile of a task and record witness impact.

        Args:
            task_id: The task to assess.

        Returns:
            Dict with ``task_id``, ``judgment_impact``, ``hidden_bindings``,
            ``visible_bindings``, ``impact_obs_id``.

        Example::

            assessment = coord.assess_hidden_inputs("task-001")
        """
        hidden = self._analyzer.find_hidden_inputs(task_id)
        impact = self._analyzer.compute_judgment_impact(task_id)
        # Fetch the latest HiddenInputRecord produced.
        latest_record = max(
            self._analyzer._hidden_records.values(),
            key=lambda r: r.detected_at,
            default=None,
        )
        impact_obs_id = ""
        if latest_record is not None and latest_record.task_id == task_id:
            impact_obs_id = self._witness.observe_judgment_impact(latest_record)

        all_ids = self._analyzer._task_index.get(task_id, [])
        visible_count = sum(
            1 for bid in all_ids
            if bid in self._analyzer._bindings and not self._analyzer._bindings[bid].is_hidden
        )
        return {
            "task_id": task_id,
            "judgment_impact": impact,
            "hidden_bindings": len(hidden),
            "visible_bindings": visible_count,
            "impact_obs_id": impact_obs_id,
        }

    def inherited_context(self, task_id: str, parent_id: str) -> dict[str, object]:
        """Report all bindings in *task_id* that were inherited from *parent_id*.

        Args:
            task_id: The child task.
            parent_id: The parent binding_id to trace from.

        Returns:
            Dict with ``chain_length``, ``chain_binding_ids``,
            ``first_hidden_at_depth``.

        Example::

            info = coord.inherited_context("task-002", "parent-binding-id")
        """
        chain = self._analyzer.binding_inheritance_chain(parent_id)
        first_hidden: int | None = None
        for depth, b in enumerate(chain):
            if b.is_hidden:
                first_hidden = depth
                break
        return {
            "chain_length": len(chain),
            "chain_binding_ids": [b.binding_id for b in chain],
            "first_hidden_at_depth": first_hidden,
        }

    def full_context_report(self, task_id: str) -> dict[str, object]:
        """Produce a comprehensive context report for a single task.

        Args:
            task_id: The task to report on.

        Returns:
            Dict with ``task_id``, ``bindings``, ``hidden_assessment``,
            ``shadowing``, ``visibility_classifications``.

        Example::

            report = coord.full_context_report("task-001")
        """
        ids = self._analyzer._task_index.get(task_id, [])
        bindings = [
            self._analyzer._bindings[bid].to_dict()
            for bid in ids
            if bid in self._analyzer._bindings
        ]
        visibility: dict[str, str] = {}
        for bid in ids:
            b = self._analyzer._bindings.get(bid)
            if b is not None:
                visibility[bid] = self._analyzer.classify_visibility(b).value

        return {
            "task_id": task_id,
            "bindings": bindings,
            "hidden_assessment": self.assess_hidden_inputs(task_id),
            "shadowing": self._analyzer.detect_shadowing(task_id),
            "visibility_classifications": visibility,
        }

    def full_report(self) -> dict[str, object]:
        """Produce a comprehensive report for the entire coordinator session.

        Returns:
            Dict with ``session_id``, ``analyzer_stats``, ``witness_certificate``,
            ``impact_report``, ``all_bindings``.

        Example::

            report = coord.full_report()
        """
        return {
            "session_id": self._session_id,
            "analyzer_stats": self._analyzer.stats(),
            "witness_certificate": self._witness.generate_certificate(),
            "impact_report": self._witness.impact_report(),
            "all_bindings": self._analyzer.export_bindings(),
        }

    def reset(self) -> None:
        """Clear all state in the coordinator, analyzer, and witness.

        Example::

            coord.reset()
        """
        self._analyzer = ContextBindingAnalyzer()
        self._witness = HiddenContextWitness()
        self._session_id = _new_record_id()
        _log.info("TaskLocalContextHiddenCoordinator reset; new session=%s", self._session_id)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def make_coordinator() -> TaskLocalContextHiddenCoordinator:
    """Convenience factory that returns a ready-to-use coordinator instance.

    Returns:
        A freshly constructed TaskLocalContextHiddenCoordinator.
    """
    return TaskLocalContextHiddenCoordinator()


def visibility_names() -> list[str]:
    """Return the names of all ContextVisibility values.

    Returns:
        Sorted list of visibility name strings.
    """
    return sorted(v.value for v in ContextVisibility)


def build_hidden_input_record(
    task_id: str,
    hidden_keys: frozenset[str],
    visible_keys: frozenset[str],
) -> HiddenInputRecord:
    """Construct a HiddenInputRecord from key sets.

    Args:
        task_id: The owning task.
        hidden_keys: Frozenset of hidden binding keys.
        visible_keys: Frozenset of visible binding keys.

    Returns:
        A new HiddenInputRecord with computed judgment_impact.

    Example::

        hir = build_hidden_input_record(
            "task-1", frozenset(["ctx:user"]), frozenset(["ctx:trace"])
        )
    """
    impact = _impact_label(len(hidden_keys), len(visible_keys))
    return HiddenInputRecord(
        record_id=_new_record_id(),
        task_id=task_id,
        hidden_keys=hidden_keys,
        visible_keys=visible_keys,
        judgment_impact=impact,
        detected_at=time.monotonic(),
    )


def describe_visibility(vis: ContextVisibility) -> str:
    """Return a one-sentence description of a ContextVisibility value.

    Args:
        vis: The visibility value to describe.

    Returns:
        A plain-English sentence.
    """
    descriptions = {
        ContextVisibility.FULLY_VISIBLE: "The binding is observable by all concurrency units with access to the site.",
        ContextVisibility.PARTIALLY_HIDDEN: "The binding is visible in the current scope but some sibling bindings in the same task are hidden.",
        ContextVisibility.FULLY_HIDDEN: "The binding exists only within the owning task's context copy and is not visible externally.",
        ContextVisibility.INHERITED: "The binding was copied from a parent task context and retains the parent's visibility class.",
        ContextVisibility.SHADOWED: "Another task has a hidden binding under the same key, making this binding's value non-authoritative.",
    }
    return descriptions.get(vis, "Unknown visibility classification.")


__all__ = [
    "ContextVisibility",
    "ContextBinding",
    "HiddenInputRecord",
    "ContextBindingAnalyzer",
    "HiddenContextWitness",
    "TaskLocalContextHiddenCoordinator",
    "make_coordinator",
    "visibility_names",
    "build_hidden_input_record",
    "describe_visibility",
]

# copilot: s02 — task-local context as hidden but structured input; Ch24 §2
