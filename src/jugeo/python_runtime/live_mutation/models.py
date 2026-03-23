"""Canonical data models for the live_mutation package.

These are the canonical data models for the ``live_mutation`` package,
representing the sheaf-theoretic objects described in Ch23 of theory2.tex.
Dynamic sections, exec contexts, eval results, monkey patch records, and
hot reload events are all first-class objects here.

In the sheaf-theoretic framework, a *dynamic section* is a locally-defined
element of the presheaf of Python namespaces: it lives over a *support
coordinate* (an open set in the topology of active module paths) and can be
glued, compared for consistency, or invalidated when its support shrinks.

    * ``ExecContext``      — the open set / stalk metadata for a running exec
    * ``DynamicSection``   — the actual section injected via exec
    * ``EvalResult``       — the result of a query (eval) on the stalk
    * ``MonkeyPatchRecord`` — a section *replacement* with invalidation bookkeeping
    * ``HotReloadEvent``   — an incremental descent event across a reload boundary

Trust tiers (``TrustTier``) and invalidation scopes (``InvalidationScope``)
track the sheaf-consistency status of each object.

Theory reference: Ch23, §23.1–§23.7 of theory2.tex.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MutationKind(Enum):
    """Taxonomy of dynamic mutation operations modelled as section operations.

    Each value corresponds to a distinct class of section operation in the
    sheaf of Python namespaces (Ch23 §23.2).
    """

    EXEC_INJECTION = "exec_injection"
    """Injection of a new section via ``exec``.  Creates a new stalk element."""

    EVAL_QUERY = "eval_query"
    """Read-only query on an existing stalk element via ``eval``."""

    MONKEY_PATCH = "monkey_patch"
    """In-place replacement of an attribute — section replacement with
    immediate invalidation of downstream dependents."""

    HOT_RELOAD = "hot_reload"
    """Incremental descent: re-load a module, replacing all sections defined
    by the previous version while preserving consistent ones."""

    DYNAMIC_SECTION = "dynamic_section"
    """A generic, runtime-constructed section not tied to a single mechanism."""

    ATTRIBUTE_OVERRIDE = "attribute_override"
    """Override of a single attribute without full monkey-patch bookkeeping."""


class InvalidationScope(Enum):
    """The scope over which a mutation invalidates existing sections.

    Corresponds to the open sets in the topology used by Ch23 §23.5.
    """

    LOCAL = "local"
    """Only the immediately enclosing namespace is invalidated."""

    MODULE = "module"
    """The entire module stalk is invalidated."""

    PACKAGE = "package"
    """All stalks within the enclosing package are invalidated."""

    GLOBAL = "global"
    """Global namespace invalidation — all sections across all modules."""

    CASCADING = "cascading"
    """Invalidation propagates transitively through the import graph."""


class ReloadStatus(Enum):
    """Status of a hot-reload event, tracking the descent lifecycle.

    Corresponds to the lifecycle of an incremental descent operation
    described in Ch23 §23.6.
    """

    PENDING = "pending"
    """Reload has been requested but not yet started."""

    IN_PROGRESS = "in_progress"
    """Reload is actively executing descent steps."""

    COMPLETED = "completed"
    """Reload finished successfully; all sections are consistent."""

    FAILED = "failed"
    """Reload encountered an unrecoverable error."""

    ROLLED_BACK = "rolled_back"
    """Reload was aborted and the previous sections were restored."""


class TrustTier(Enum):
    """Sheaf-theoretic trust classification for dynamically-created sections.

    Mirrors the verification ladder in Ch23 §23.3.  Sections begin as
    PROPOSAL and advance as they accumulate corroborating evidence.
    """

    PROPOSAL = "proposal"
    """Default for dynamic sections.  No verification has been performed."""

    CORROBORATED = "corroborated"
    """Section is consistent with at least one neighbouring stalk."""

    VERIFIED = "verified"
    """Section has passed formal consistency checks."""

    CERTIFIED = "certified"
    """Section has been certified by the top-level sheaf validator."""


# ---------------------------------------------------------------------------
# ExecContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecContext:
    """Metadata describing the open-set context in which an exec call executes.

    An ``ExecContext`` records the namespace snapshot *before* an exec
    injection, together with the support coordinate (open set identifier) and
    the trust level assigned to code running in this context.  It is
    intentionally immutable: any mutation creates a new context.

    Attributes:
        context_id: Unique identifier for this context (use ``new_context_id()``).
        global_namespace: Frozen snapshot of global name bindings.
        local_namespace: Frozen snapshot of local name bindings.
        support_coordinate: String key identifying the open set (e.g. module path).
        trust_level: Trust tier string (a ``TrustTier`` value name).
        created_at: POSIX timestamp of context creation.
        source_module: Fully-qualified name of the module that created this context.
    """

    context_id: str
    global_namespace: frozenset[str]
    local_namespace: frozenset[str]
    support_coordinate: str
    trust_level: str
    created_at: float
    source_module: str

    def has_global(self, name: str) -> bool:
        """Return True if *name* is in the global namespace.

        Args:
            name: The symbol name to look up.

        Returns:
            ``True`` when the name is present in ``global_namespace``.
        """
        return name in self.global_namespace

    def has_local(self, name: str) -> bool:
        """Return True if *name* is in the local namespace.

        Args:
            name: The symbol name to look up.

        Returns:
            ``True`` when the name is present in ``local_namespace``.
        """
        return name in self.local_namespace

    def namespace_size(self) -> int:
        """Return the total number of names across both namespaces.

        Counts are de-duplicated; a name present in both namespaces is
        counted only once.

        Returns:
            Integer count of unique names.
        """
        return len(self.global_namespace | self.local_namespace)

    def is_trusted(self) -> bool:
        """Return True if trust_level is VERIFIED or CERTIFIED.

        Returns:
            ``True`` for ``TrustTier.VERIFIED`` and ``TrustTier.CERTIFIED``.
        """
        return self.trust_level in (TrustTier.VERIFIED.value, TrustTier.CERTIFIED.value)

    def all_names(self) -> frozenset[str]:
        """Return the union of global and local namespace names.

        Returns:
            A new ``frozenset`` containing every name visible in this context.
        """
        return self.global_namespace | self.local_namespace

    def age_seconds(self, now: float | None = None) -> float:
        """Return seconds elapsed since creation.

        Args:
            now: Optional reference timestamp; defaults to ``time.time()``.

        Returns:
            Non-negative float representing elapsed seconds.
        """
        reference = now if now is not None else time.time()
        return max(0.0, reference - self.created_at)

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dictionary.

        Returns:
            A plain ``dict`` suitable for ``json.dumps``.
        """
        return {
            "context_id": self.context_id,
            "global_namespace": sorted(self.global_namespace),
            "local_namespace": sorted(self.local_namespace),
            "support_coordinate": self.support_coordinate,
            "trust_level": self.trust_level,
            "created_at": self.created_at,
            "source_module": self.source_module,
        }


# ---------------------------------------------------------------------------
# DynamicSection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DynamicSection:
    """A section of the sheaf of Python namespaces injected at runtime.

    A ``DynamicSection`` represents the result of executing a code string in
    a specific ``ExecContext``.  It records the source, the set of names it
    defines, its support (via ``exec_context_id`` and ``support_keys``), and
    the trust tier assigned to the injected symbols.

    ``compiled_code`` is excluded from ``__eq__``, ``__hash__``, and
    ``__repr__`` because code objects are not reliably comparable or
    serialisable.

    Attributes:
        section_id: Unique identifier (use ``new_section_id()``).
        source_code: The Python source string that was executed.
        compiled_code: Optional pre-compiled code object; excluded from
            equality and hashing.
        namespace: Names defined (or mutated) by executing ``source_code``.
        exec_context_id: ID of the ``ExecContext`` used during injection.
        created_at: POSIX timestamp of injection.
        support_keys: Open-set keys over which this section is valid.
        mutation_kind: Which ``MutationKind`` produced this section.
        trust_level: Trust tier string.
    """

    section_id: str
    source_code: str
    compiled_code: Any = field(default=None, compare=False, hash=False, repr=False)
    namespace: frozenset[str] = field(default_factory=frozenset)
    exec_context_id: str = ""
    created_at: float = field(default_factory=time.time)
    support_keys: frozenset[str] = field(default_factory=frozenset)
    mutation_kind: MutationKind = MutationKind.EXEC_INJECTION
    trust_level: str = TrustTier.PROPOSAL.value

    def is_valid(self) -> bool:
        """Return True if the section has a non-empty namespace and source_code.

        A section is considered structurally valid when it both has source
        code to execute and has defined at least one symbol.

        Returns:
            ``True`` when both ``source_code`` and ``namespace`` are non-empty.
        """
        return bool(self.source_code) and bool(self.namespace)

    def has_symbol(self, name: str) -> bool:
        """Return True if *name* was injected by this section.

        Args:
            name: Symbol name to check.

        Returns:
            ``True`` when the name appears in ``namespace``.
        """
        return name in self.namespace

    def symbols_defined(self) -> list[str]:
        """Return sorted list of symbols defined by this section.

        Returns:
            A sorted ``list`` of symbol name strings.
        """
        return sorted(self.namespace)

    def age_seconds(self, now: float | None = None) -> float:
        """Return seconds since creation.

        Args:
            now: Optional reference timestamp; defaults to ``time.time()``.

        Returns:
            Non-negative float.
        """
        reference = now if now is not None else time.time()
        return max(0.0, reference - self.created_at)

    def code_lines(self) -> int:
        """Return the number of lines in source_code.

        Returns:
            Integer line count.  An empty string returns 0.
        """
        if not self.source_code:
            return 0
        return len(self.source_code.splitlines())

    def is_trusted(self) -> bool:
        """Return True if trust_level is VERIFIED or CERTIFIED.

        Returns:
            ``True`` for ``TrustTier.VERIFIED`` and ``TrustTier.CERTIFIED``.
        """
        return self.trust_level in (TrustTier.VERIFIED.value, TrustTier.CERTIFIED.value)

    def fingerprint(self) -> str:
        """Return a short SHA-256 hex digest of the source_code.

        The digest covers the UTF-8 encoding of ``source_code`` and is
        truncated to 16 hex characters for readability.

        Returns:
            A 16-character lowercase hex string.
        """
        raw = self.source_code.encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict, omitting compiled_code.

        Returns:
            A plain ``dict`` suitable for ``json.dumps``.
        """
        return {
            "section_id": self.section_id,
            "source_code": self.source_code,
            "namespace": sorted(self.namespace),
            "exec_context_id": self.exec_context_id,
            "created_at": self.created_at,
            "support_keys": sorted(self.support_keys),
            "mutation_kind": self.mutation_kind.value,
            "trust_level": self.trust_level,
            "fingerprint": self.fingerprint(),
            "code_lines": self.code_lines(),
        }


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalResult:
    """The result of a read-only eval query on a section stalk.

    Corresponds to the *query* operation in Ch23 §23.4 — evaluating an
    expression against an existing stalk without mutating it.

    Attributes:
        result_id: Unique identifier (use ``new_result_id()``).
        expression: The Python expression string that was evaluated.
        result_repr: ``repr()`` of the return value, or empty string on error.
        result_type: ``type(result).__qualname__``, or empty string on error.
        support_keys: Open-set keys providing the evaluation context.
        trust_level: Trust tier string of the context used.
        evaluated_at: POSIX timestamp of evaluation.
        context_id: ID of the ``ExecContext`` used during evaluation.
        error: Error message string if evaluation raised an exception, else ``None``.
    """

    result_id: str
    expression: str
    result_repr: str
    result_type: str
    support_keys: frozenset[str]
    trust_level: str
    evaluated_at: float
    context_id: str
    error: str | None = None

    def is_error(self) -> bool:
        """Return True if an error occurred during evaluation.

        Returns:
            ``True`` when ``error`` is not ``None``.
        """
        return self.error is not None

    def is_trusted(self) -> bool:
        """Return True if trust_level is VERIFIED or CERTIFIED.

        Returns:
            ``True`` for ``TrustTier.VERIFIED`` and ``TrustTier.CERTIFIED``.
        """
        return self.trust_level in (TrustTier.VERIFIED.value, TrustTier.CERTIFIED.value)

    def has_support(self, key: str) -> bool:
        """Return True if *key* is in support_keys.

        Args:
            key: The open-set key to test.

        Returns:
            ``True`` when the key is present in ``support_keys``.
        """
        return key in self.support_keys

    def expression_length(self) -> int:
        """Return the character length of the expression.

        Returns:
            Integer character count of ``expression``.
        """
        return len(self.expression)

    def age_seconds(self, now: float | None = None) -> float:
        """Return seconds since evaluation.

        Args:
            now: Optional reference timestamp; defaults to ``time.time()``.

        Returns:
            Non-negative float.
        """
        reference = now if now is not None else time.time()
        return max(0.0, reference - self.evaluated_at)

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict.

        Returns:
            A plain ``dict`` suitable for ``json.dumps``.
        """
        return {
            "result_id": self.result_id,
            "expression": self.expression,
            "result_repr": self.result_repr,
            "result_type": self.result_type,
            "support_keys": sorted(self.support_keys),
            "trust_level": self.trust_level,
            "evaluated_at": self.evaluated_at,
            "context_id": self.context_id,
            "error": self.error,
            "is_error": self.is_error(),
            "expression_length": self.expression_length(),
        }


# ---------------------------------------------------------------------------
# MonkeyPatchRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonkeyPatchRecord:
    """Record of a monkey-patch applied to a module attribute.

    In sheaf-theoretic terms a monkey patch is a *section replacement*: the
    old section is removed, a new section is spliced in, and all sections
    whose stalks depended on the old one are invalidated (Ch23 §23.5).

    Attributes:
        patch_id: Unique identifier (use ``new_patch_id()``).
        target_module: Fully-qualified module name being patched.
        target_attribute: Attribute name on the module being replaced.
        original_id: ``id()`` of the original object, for identity tracking.
        patch_hash: Short hash of the replacement value's repr.
        invalidated_section_ids: IDs of sections invalidated by this patch.
        created_at: POSIX timestamp when the record was created.
        applied_at: POSIX timestamp when the patch was applied.
        reverted_at: POSIX timestamp when the patch was reverted, or ``None``.
        scope: The ``InvalidationScope`` of this patch.
    """

    patch_id: str
    target_module: str
    target_attribute: str
    original_id: int
    patch_hash: str
    invalidated_section_ids: tuple[str, ...]
    created_at: float
    applied_at: float
    reverted_at: float | None = None
    scope: InvalidationScope = InvalidationScope.MODULE

    def is_active(self) -> bool:
        """Return True if the patch has been applied but not reverted.

        Returns:
            ``True`` when ``reverted_at`` is ``None``.
        """
        return self.reverted_at is None

    def is_reverted(self) -> bool:
        """Return True if reverted_at is set.

        Returns:
            ``True`` when the patch has been explicitly reverted.
        """
        return self.reverted_at is not None

    def duration_seconds(self) -> float:
        """Return seconds the patch has been active (or was active).

        Uses ``reverted_at`` as the end timestamp if set, otherwise uses the
        current time.

        Returns:
            Non-negative float duration in seconds.
        """
        end = self.reverted_at if self.reverted_at is not None else time.time()
        return max(0.0, end - self.applied_at)

    def affects_section(self, section_id: str) -> bool:
        """Return True if *section_id* is in invalidated_section_ids.

        Args:
            section_id: The section identifier to check.

        Returns:
            ``True`` when the section was invalidated by this patch.
        """
        return section_id in self.invalidated_section_ids

    def invalidation_count(self) -> int:
        """Return the number of sections this patch invalidated.

        Returns:
            Integer count of ``invalidated_section_ids``.
        """
        return len(self.invalidated_section_ids)

    def fully_qualified_target(self) -> str:
        """Return 'module.attribute' string.

        Returns:
            A dot-joined string of ``target_module`` and ``target_attribute``.
        """
        return f"{self.target_module}.{self.target_attribute}"

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict.

        Returns:
            A plain ``dict`` suitable for ``json.dumps``.
        """
        return {
            "patch_id": self.patch_id,
            "target_module": self.target_module,
            "target_attribute": self.target_attribute,
            "original_id": self.original_id,
            "patch_hash": self.patch_hash,
            "invalidated_section_ids": list(self.invalidated_section_ids),
            "created_at": self.created_at,
            "applied_at": self.applied_at,
            "reverted_at": self.reverted_at,
            "scope": self.scope.value,
            "is_active": self.is_active(),
            "invalidation_count": self.invalidation_count(),
            "fully_qualified_target": self.fully_qualified_target(),
        }


# ---------------------------------------------------------------------------
# HotReloadEvent
# ---------------------------------------------------------------------------


@dataclass
class HotReloadEvent:
    """A hot-reload event representing an incremental descent across a module boundary.

    Hot reload is modelled in Ch23 §23.6 as an *incremental descent*: the
    loader descends the import graph, replaces sections whose source has
    changed, and preserves sections that remain consistent.  Each descent
    step is recorded in ``descent_steps``.

    Attributes:
        event_id: Unique identifier (use ``new_event_id()``).
        module_name: Fully-qualified name of the module being reloaded.
        reload_status: Current ``ReloadStatus`` of this event.
        sections_replaced: IDs of sections replaced by this reload.
        sections_invalidated: IDs of sections invalidated (but not replaced).
        descent_steps: Ordered list of step records (dicts with at least
            ``"step_name"`` and ``"timestamp"`` keys).
        started_at: POSIX timestamp of reload start.
        completed_at: POSIX timestamp of completion (``None`` while running).
        error: Error message if the reload failed, else ``None``.
    """

    event_id: str
    module_name: str
    reload_status: ReloadStatus
    sections_replaced: list[str] = field(default_factory=list)
    sections_invalidated: list[str] = field(default_factory=list)
    descent_steps: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    error: str | None = None

    def is_complete(self) -> bool:
        """Return True if reload_status is COMPLETED.

        Returns:
            ``True`` when the reload finished successfully.
        """
        return self.reload_status is ReloadStatus.COMPLETED

    def is_failed(self) -> bool:
        """Return True if reload_status is FAILED.

        Returns:
            ``True`` when the reload encountered an unrecoverable error.
        """
        return self.reload_status is ReloadStatus.FAILED

    def duration_seconds(self) -> float:
        """Return elapsed seconds from started_at to completed_at (or now).

        If ``completed_at`` is ``None``, the current time is used so that
        in-progress events report live durations.

        Returns:
            Non-negative float duration in seconds.
        """
        end = self.completed_at if self.completed_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def total_sections_affected(self) -> int:
        """Return len(sections_replaced) + len(sections_invalidated).

        Returns:
            Combined integer count of affected sections.
        """
        return len(self.sections_replaced) + len(self.sections_invalidated)

    def add_step(self, step_name: str, details: dict | None = None) -> None:
        """Append a descent step record.

        Each step record contains at minimum ``"step_name"`` and
        ``"timestamp"`` keys.  Additional key-value pairs from ``details``
        are merged in.

        Args:
            step_name: Short name describing this descent step.
            details: Optional extra key-value pairs to include in the record.
        """
        record: dict = {
            "step_name": step_name,
            "timestamp": time.time(),
            "step_index": len(self.descent_steps),
        }
        if details:
            record.update(details)
        self.descent_steps.append(record)

    def mark_complete(self) -> None:
        """Set reload_status to COMPLETED and completed_at to now.

        Transitions the event out of IN_PROGRESS and records the finish time.
        Calling this on an already-completed event is idempotent (the status
        is simply set again and completed_at is updated to the new timestamp).
        """
        self.reload_status = ReloadStatus.COMPLETED
        self.completed_at = time.time()

    def mark_failed(self, error_msg: str) -> None:
        """Set reload_status to FAILED, error, and completed_at.

        Args:
            error_msg: Human-readable description of the failure.
        """
        self.reload_status = ReloadStatus.FAILED
        self.error = error_msg
        self.completed_at = time.time()

    def step_count(self) -> int:
        """Return number of descent steps taken.

        Returns:
            Integer count of entries in ``descent_steps``.
        """
        return len(self.descent_steps)

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict.

        Returns:
            A plain ``dict`` suitable for ``json.dumps``.
        """
        return {
            "event_id": self.event_id,
            "module_name": self.module_name,
            "reload_status": self.reload_status.value,
            "sections_replaced": list(self.sections_replaced),
            "sections_invalidated": list(self.sections_invalidated),
            "descent_steps": list(self.descent_steps),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "duration_seconds": self.duration_seconds(),
            "total_sections_affected": self.total_sections_affected(),
            "step_count": self.step_count(),
        }


# ---------------------------------------------------------------------------
# ID generator helpers
# ---------------------------------------------------------------------------


def new_section_id() -> str:
    """Generate a UUID4-based section identifier with a ``sec-`` prefix.

    Returns:
        A string of the form ``"sec-<uuid4>"``.
    """
    return f"sec-{uuid.uuid4()}"


def new_context_id() -> str:
    """Generate a UUID4-based context identifier with a ``ctx-`` prefix.

    Returns:
        A string of the form ``"ctx-<uuid4>"``.
    """
    return f"ctx-{uuid.uuid4()}"


def new_patch_id() -> str:
    """Generate a UUID4-based patch identifier with a ``patch-`` prefix.

    Returns:
        A string of the form ``"patch-<uuid4>"``.
    """
    return f"patch-{uuid.uuid4()}"


def new_event_id() -> str:
    """Generate a UUID4-based event identifier with an ``evt-`` prefix.

    Returns:
        A string of the form ``"evt-<uuid4>"``.
    """
    return f"evt-{uuid.uuid4()}"


def new_result_id() -> str:
    """Generate a UUID4-based result identifier with a ``res-`` prefix.

    Returns:
        A string of the form ``"res-<uuid4>"``.
    """
    return f"res-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "MutationKind",
    "InvalidationScope",
    "ReloadStatus",
    "TrustTier",
    # Data models
    "ExecContext",
    "DynamicSection",
    "EvalResult",
    "MonkeyPatchRecord",
    "HotReloadEvent",
    # ID generators
    "new_section_id",
    "new_context_id",
    "new_patch_id",
    "new_event_id",
    "new_result_id",
]

# copilot: canonical data models for live_mutation Ch23
