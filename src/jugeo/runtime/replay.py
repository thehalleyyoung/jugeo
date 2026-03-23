"""Replay, reuse, and support-local reopening for the JuGeo runtime.

This module turns the replay doctrine from ``preliminaries/theory2.tex`` into a
small, explicit Python API.  The governing ideas are repeated throughout the
source because they matter operationally, not rhetorically:

* replay is support-local rather than project-global,
* trust may be reused only through named policy routes,
* persistent semantic memory is only useful when its seals remain auditable,
* retained work must reopen when support, treaties, certificates, or declared
  dependency epochs change.

The implementation intentionally preserves a simple legacy surface for nearby
runtime modules and tests while providing a richer public API for future files
implied by the blueprint.

Public compatibility goals
--------------------------
``ReplayRecord`` and ``ReplayLedger`` are kept lightweight and positional for
existing callers.  Newer code can also use ``ReplaySeal``, ``ReplayEngine``,
``ReplayDecision``, and ``ReplayReport`` for more expressive replay planning.

Design notes
------------
* ``ReplaySeal`` is the auditable summary of what made a replayable artifact
  admissible at the time it was first discharged.
* ``ReplayLedger`` is the append-only memory of discharged work.
* ``ReplayEngine`` computes support-local reopening under changed supports,
  changed dependency epochs, and changed trust/treaty/certificate policies.
* ``seal_is_valid()`` is deliberately narrow and deterministic.  It answers the
  question "does this seal still satisfy the currently named invariants?" It
  does **not** hide reopening heuristics.
* ``replay_region()`` is the high-level support-local entry point mirroring the
  vocabulary used in theory2.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Final, Iterable, Mapping, Sequence, TypeAlias

from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.geometry.covers import Cover
from jugeo.geometry.supports import SupportRegion
from jugeo.runtime.cache import CacheEntry, SemanticCache
from jugeo.runtime.invalidation import InvalidationPlan, InvalidationReason, plan_invalidation
from jugeo.runtime.memory import MemoryNote, SemanticMemory
from jugeo.runtime_defaults import RuntimeDefaults, default_runtime_options, default_trust_policy

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
ReplayValidator: TypeAlias = Callable[["ReplayRecord"], bool | tuple[bool, Sequence[str]]]

_DEFAULT_WITNESS_SCHEMA: Final[str] = "runtime.replay.generic"
_REPLAY_MEMORY_TAG: Final[str] = "replay"


class ReplayStatus(str, Enum):
    """Replay outcome for one retained record."""

    REUSED = "reused"
    REVALIDATED = "revalidated"
    REOPENED = "reopened"
    INVALIDATED = "invalidated"


class ReplayTrigger(str, Enum):
    """Why replay evaluation was performed."""

    SUPPORT_CHANGE = "support-change"
    TRUST_CHANGE = "trust-change"
    DEPENDENCY_CHANGE = "dependency-change"
    MANUAL_REOPEN = "manual-reopen"
    INITIAL_REPLAY = "initial-replay"


@dataclass(frozen=True, slots=True)
class ReplayPolicy:
    """Explicit replay policy derived from runtime defaults.

    The policy is intentionally conservative: unsealed records are not reused,
    and trust reuse defaults to the proposal tier unless a stricter floor is
    named by the caller.
    """

    name: str = "balanced"
    trust_floor: TrustTier = TrustTier.PROPOSAL
    require_provenance: bool = True
    reopen_on_treaty_change: bool = True
    reopen_on_certificate_change: bool = True
    allow_unsealed_replay: bool = False
    remember_reports: bool = True
    max_records: int = 128

    @classmethod
    def from_defaults(cls, defaults: RuntimeDefaults) -> "ReplayPolicy":
        return cls(
            name=defaults.preset.value,
            trust_floor=_tier_from_label(default_trust_policy(defaults.preset).proposal_tier),
            require_provenance=True,
            reopen_on_treaty_change=True,
            reopen_on_certificate_change=True,
            allow_unsealed_replay=False,
            remember_reports=True,
            max_records=int(getattr(getattr(defaults, 'descent', None), 'max_depth', 128)),
        )


@dataclass(frozen=True, slots=True)
class ReplaySeal:
    """Auditable replay boundary for one retained result.

    A seal captures exactly which semantic boundaries must remain unchanged for
    replay to be sound: support identity, trust floor, provenance presence,
    dependency epochs, treaty fingerprint, certificate fingerprint, and a stable
    semantic digest for the retained payload.
    """

    record_key: str
    support_fingerprint: str
    trust_tier: str
    trust_scope: tuple[str, ...] = ()
    provenance_origin: str = ""
    provenance_steps: int = 0
    dependency_keys: tuple[str, ...] = ()
    dependency_epochs: tuple[tuple[str, int], ...] = ()
    witness_schema: str = _DEFAULT_WITNESS_SCHEMA
    policy_tag: str = "balanced"
    treaty_fingerprint: str = ""
    certificate_fingerprint: str = ""
    semantic_fingerprint: str = ""
    metadata_fingerprint: str = ""
    issued_at: float = field(default_factory=time.time)

    @classmethod
    def issue(
        cls,
        *,
        record_key: str,
        support: SupportRegion,
        trust: TrustProfile,
        provenance: ProvenanceTrace,
        dependency_keys: Iterable[str] = (),
        dependency_epochs: Mapping[str, int] | None = None,
        witness_schema: str = _DEFAULT_WITNESS_SCHEMA,
        policy_tag: str = "balanced",
        treaty_fingerprint: str = "",
        certificate_fingerprint: str = "",
        semantic_payload: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ReplaySeal":
        normalized_epochs = _normalize_epoch_pairs(dependency_epochs)
        semantic_digest = _stable_digest(semantic_payload)
        metadata_digest = _stable_digest(metadata or {})
        return cls(
            record_key=record_key,
            support_fingerprint=_support_fingerprint(support),
            trust_tier=trust.tier.label(),
            trust_scope=tuple(trust.support_scope),
            provenance_origin=provenance.origin,
            provenance_steps=len(provenance.steps),
            dependency_keys=_normalize_strings(dependency_keys),
            dependency_epochs=normalized_epochs,
            witness_schema=witness_schema.strip() or _DEFAULT_WITNESS_SCHEMA,
            policy_tag=policy_tag.strip() or "balanced",
            treaty_fingerprint=treaty_fingerprint.strip(),
            certificate_fingerprint=certificate_fingerprint.strip(),
            semantic_fingerprint=semantic_digest,
            metadata_fingerprint=metadata_digest,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "record_key": self.record_key,
            "support_fingerprint": self.support_fingerprint,
            "trust_tier": self.trust_tier,
            "trust_scope": list(self.trust_scope),
            "provenance_origin": self.provenance_origin,
            "provenance_steps": self.provenance_steps,
            "dependency_keys": list(self.dependency_keys),
            "dependency_epochs": {key: epoch for key, epoch in self.dependency_epochs},
            "witness_schema": self.witness_schema,
            "policy_tag": self.policy_tag,
            "treaty_fingerprint": self.treaty_fingerprint,
            "certificate_fingerprint": self.certificate_fingerprint,
            "semantic_fingerprint": self.semantic_fingerprint,
            "metadata_fingerprint": self.metadata_fingerprint,
            "issued_at": self.issued_at,
        }

    def trust_meets_floor(self, floor: TrustTier) -> bool:
        return _tier_from_label(self.trust_tier) >= floor

    def dependency_epoch_for(self, key: str) -> int | None:
        for dep_key, epoch in self.dependency_epochs:
            if dep_key == key:
                return epoch
        return None

    def supports(self, support: SupportRegion) -> bool:
        return self.support_fingerprint == _support_fingerprint(support)

    def summary(self) -> str:
        scope = ", ".join(self.trust_scope) if self.trust_scope else "unscoped"
        return (
            f"ReplaySeal(record={self.record_key}, trust={self.trust_tier}, "
            f"scope={scope}, deps={len(self.dependency_keys)})"
        )


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """One discharged unit of work retained for possible replay.

    The first four positional parameters intentionally match the historic test
    surface in this repository.
    """

    name: str
    support: SupportRegion
    trust: TrustProfile
    provenance: ProvenanceTrace
    seal: ReplaySeal | None = None
    payload: Any = None
    residuals: tuple[str, ...] = ()
    obstructions: tuple[str, ...] = ()
    dependency_keys: tuple[str, ...] = ()
    dependency_epochs: tuple[tuple[str, int], ...] = ()
    witness_schema: str = _DEFAULT_WITNESS_SCHEMA
    treaty_fingerprint: str = ""
    certificate_fingerprint: str = ""
    semantic_tags: tuple[str, ...] = ()
    epoch: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependency_keys", _normalize_strings(self.dependency_keys))
        object.__setattr__(self, "dependency_epochs", _normalize_epoch_pairs(dict(self.dependency_epochs)))
        object.__setattr__(self, "residuals", _normalize_strings(self.residuals))
        object.__setattr__(self, "obstructions", _normalize_strings(self.obstructions))
        object.__setattr__(self, "semantic_tags", _normalize_strings(self.semantic_tags))
        object.__setattr__(self, "witness_schema", self.witness_schema.strip() or _DEFAULT_WITNESS_SCHEMA)
        object.__setattr__(self, "treaty_fingerprint", self.treaty_fingerprint.strip())
        object.__setattr__(self, "certificate_fingerprint", self.certificate_fingerprint.strip())
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def stable_key(self) -> str:
        patch_part = ",".join(sorted(self.support.patch_keys)) or self.support.coordinate.key
        return f"{self.name}@{self.support.coordinate.key}:{patch_part}"

    def ensure_seal(
        self,
        *,
        policy_tag: str = "balanced",
        dependency_epochs: Mapping[str, int] | None = None,
    ) -> "ReplayRecord":
        if self.seal is not None:
            return self
        seal = ReplaySeal.issue(
            record_key=self.stable_key,
            support=self.support,
            trust=self.trust,
            provenance=self.provenance,
            dependency_keys=self.dependency_keys,
            dependency_epochs=dependency_epochs or dict(self.dependency_epochs),
            witness_schema=self.witness_schema,
            policy_tag=policy_tag,
            treaty_fingerprint=self.treaty_fingerprint,
            certificate_fingerprint=self.certificate_fingerprint,
            semantic_payload=self.payload,
            metadata=self.metadata,
        )
        return replace(self, seal=seal)

    def intersects(self, region: SupportRegion) -> bool:
        return self.support.intersects(region)

    def affects_patch(self, patch_key: str) -> bool:
        return patch_key in self.support.patch_keys

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "stable_key": self.stable_key,
            "support": _support_snapshot(self.support),
            "trust": self.trust.to_dict(),
            "provenance": self.provenance.to_dict(),
            "seal": self.seal.to_dict() if self.seal else None,
            "payload": _json_safe(self.payload),
            "residuals": list(self.residuals),
            "obstructions": list(self.obstructions),
            "dependency_keys": list(self.dependency_keys),
            "dependency_epochs": {key: epoch for key, epoch in self.dependency_epochs},
            "witness_schema": self.witness_schema,
            "treaty_fingerprint": self.treaty_fingerprint,
            "certificate_fingerprint": self.certificate_fingerprint,
            "semantic_tags": list(self.semantic_tags),
            "epoch": self.epoch,
            "metadata": _json_safe(self.metadata),
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        return (
            f"ReplayRecord(name={self.name}, support={sorted(self.support.patch_keys)}, "
            f"trust={self.trust.tier.label()}, residuals={len(self.residuals)})"
        )


@dataclass(slots=True)
class ReplayLedger:
    """Append-only ledger of replayable work."""

    records: list[ReplayRecord] = field(default_factory=list)
    default_policy_tag: str = "balanced"

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def append(self, record: ReplayRecord) -> None:
        self.records.append(record.ensure_seal(policy_tag=self.default_policy_tag))

    def extend(self, records: Iterable[ReplayRecord]) -> None:
        for record in records:
            self.append(record)

    def latest(self) -> ReplayRecord | None:
        if not self.records:
            return None
        return self.records[-1]

    def replay_from(self, index: int) -> tuple[ReplayRecord, ...]:
        return tuple(self.records[index:])

    def find_by_name(self, name: str) -> tuple[ReplayRecord, ...]:
        return tuple(record for record in self.records if record.name == name)

    def for_region(self, region: SupportRegion) -> tuple[ReplayRecord, ...]:
        return tuple(record for record in self.records if record.intersects(region))

    def for_patch(self, patch_key: str) -> tuple[ReplayRecord, ...]:
        return tuple(record for record in self.records if record.affects_patch(patch_key))

    def active_dependency_keys(self) -> tuple[str, ...]:
        keys: set[str] = set()
        for record in self.records:
            keys.update(record.dependency_keys)
        return tuple(sorted(keys))

    def can_replay_under(self, support: SupportRegion) -> bool:
        return any(
            record.seal is not None and seal_is_valid(record.seal, support=record.support)
            for record in self.for_region(support)
        )

    def summary(self) -> dict[str, object]:
        return {
            "count": len(self.records),
            "names": [record.name for record in self.records],
            "dependency_keys": list(self.active_dependency_keys()),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "default_policy_tag": self.default_policy_tag,
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class ReplayDecision:
    """Decision taken for a single replay record under a replay request."""

    record: ReplayRecord
    status: ReplayStatus
    trigger: ReplayTrigger
    reasons: tuple[str, ...] = ()
    affected_patches: tuple[str, ...] = ()
    invalidated_cache_keys: tuple[str, ...] = ()
    validator_used: bool = False
    seal_valid: bool = False

    @property
    def stable_key(self) -> str:
        return self.record.stable_key

    def to_dict(self) -> dict[str, object]:
        return {
            "record": self.record.to_dict(),
            "status": self.status.value,
            "trigger": self.trigger.value,
            "reasons": list(self.reasons),
            "affected_patches": list(self.affected_patches),
            "invalidated_cache_keys": list(self.invalidated_cache_keys),
            "validator_used": self.validator_used,
            "seal_valid": self.seal_valid,
        }

    def summary(self) -> str:
        reason = self.reasons[0] if self.reasons else "no-reason"
        return f"{self.status.value}:{self.record.name}:{reason}"


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Support-local replay report for a changed region."""

    changed_support: SupportRegion
    trigger: ReplayTrigger
    decisions: tuple[ReplayDecision, ...]
    invalidation_plan: InvalidationPlan | None = None
    considered_records: int = 0
    memory_note_keys: tuple[str, ...] = ()

    @property
    def reused_count(self) -> int:
        return sum(1 for item in self.decisions if item.status is ReplayStatus.REUSED)

    @property
    def revalidated_count(self) -> int:
        return sum(1 for item in self.decisions if item.status is ReplayStatus.REVALIDATED)

    @property
    def reopened_count(self) -> int:
        return sum(1 for item in self.decisions if item.status is ReplayStatus.REOPENED)

    @property
    def invalidated_count(self) -> int:
        return sum(1 for item in self.decisions if item.status is ReplayStatus.INVALIDATED)

    @property
    def reopened_patches(self) -> tuple[str, ...]:
        patches: set[str] = set(self.changed_support.patch_keys)
        if self.invalidation_plan is not None:
            patches.update(self.invalidation_plan.reopened_patches)
        for decision in self.decisions:
            patches.update(decision.affected_patches)
        return tuple(sorted(patches))

    @property
    def invalidated_cache_keys(self) -> tuple[str, ...]:
        keys: set[str] = set()
        if self.invalidation_plan is not None:
            keys.update(self.invalidation_plan.invalidated_keys)
        for decision in self.decisions:
            keys.update(decision.invalidated_cache_keys)
        return tuple(sorted(keys))

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_support": _support_snapshot(self.changed_support),
            "trigger": self.trigger.value,
            "considered_records": self.considered_records,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "invalidation_plan": _invalidation_snapshot(self.invalidation_plan),
            "memory_note_keys": list(self.memory_note_keys),
            "counts": {
                "reused": self.reused_count,
                "revalidated": self.revalidated_count,
                "reopened": self.reopened_count,
                "invalidated": self.invalidated_count,
            },
            "reopened_patches": list(self.reopened_patches),
            "invalidated_cache_keys": list(self.invalidated_cache_keys),
        }

    def summary(self) -> str:
        return (
            "ReplayReport("
            f"reused={self.reused_count}, "
            f"revalidated={self.revalidated_count}, "
            f"reopened={self.reopened_count}, "
            f"invalidated={self.invalidated_count}, "
            f"patches={list(self.reopened_patches)}"
            ")"
        )


class ReplayEngine:
    """Primary replay coordinator for support-local reopening.

    The engine owns three persistence surfaces:

    * ``ReplayLedger`` for retained replay candidates,
    * ``SemanticCache`` for fast invalidation of cached derived values,
    * ``SemanticMemory`` for durable semantic notes about replay decisions.

    The engine never silently upgrades trust.  Replay reuse is only allowed when
    the seal remains valid under the currently named trust floor and policy.
    """

    def __init__(
        self,
        *,
        cache: SemanticCache | None = None,
        memory: SemanticMemory | None = None,
        ledger: ReplayLedger | None = None,
        defaults: RuntimeDefaults | None = None,
        policy: ReplayPolicy | None = None,
    ) -> None:
        self.defaults = defaults or default_runtime_options()
        self.policy = policy or ReplayPolicy.from_defaults(self.defaults)
        self.cache = cache or SemanticCache()
        self.memory = memory or SemanticMemory()
        self.ledger = ledger or ReplayLedger(default_policy_tag=self.policy.name)

    def capture(
        self,
        name: str,
        support: SupportRegion,
        trust: TrustProfile,
        provenance: ProvenanceTrace,
        *,
        payload: Any = None,
        residuals: Iterable[str] = (),
        obstructions: Iterable[str] = (),
        dependency_keys: Iterable[str] = (),
        dependency_epochs: Mapping[str, int] | None = None,
        witness_schema: str = _DEFAULT_WITNESS_SCHEMA,
        treaty_fingerprint: str = "",
        certificate_fingerprint: str = "",
        semantic_tags: Iterable[str] = (),
        epoch: int = 0,
        metadata: Mapping[str, Any] | None = None,
        cache_key: str | None = None,
        remember: bool = True,
    ) -> ReplayRecord:
        record = ReplayRecord(
            name=name,
            support=support,
            trust=trust,
            provenance=provenance,
            payload=payload,
            residuals=tuple(residuals),
            obstructions=tuple(obstructions),
            dependency_keys=tuple(dependency_keys),
            dependency_epochs=_normalize_epoch_pairs(dependency_epochs),
            witness_schema=witness_schema,
            treaty_fingerprint=treaty_fingerprint,
            certificate_fingerprint=certificate_fingerprint,
            semantic_tags=tuple(semantic_tags),
            epoch=epoch,
            metadata=metadata or {},
        ).ensure_seal(
            policy_tag=self.policy.name,
            dependency_epochs=dependency_epochs,
        )
        self.ledger.append(record)
        if cache_key:
            self.cache.put(CacheEntry(cache_key, payload, support, trust, provenance))
        if remember:
            note = MemoryNote(
                key=self._memory_key("capture", record.name),
                value={
                    "event": "capture",
                    "record": record.to_dict(),
                },
                tags=(_REPLAY_MEMORY_TAG, "capture", record.name),
                provenance=provenance,
            )
            self.memory.remember(note)
        return record

    def replay(
        self,
        support: SupportRegion,
        *,
        cover: Cover | None = None,
        trigger: ReplayTrigger = ReplayTrigger.SUPPORT_CHANGE,
        changed_dependency_keys: Iterable[str] = (),
        dependency_epochs: Mapping[str, int] | None = None,
        trust_floor: TrustTier | None = None,
        treaty_fingerprint: str | None = None,
        certificate_fingerprint: str | None = None,
        validator: ReplayValidator | None = None,
    ) -> ReplayReport:
        floor = trust_floor or self.policy.trust_floor
        changed_dependencies = set(_normalize_strings(changed_dependency_keys))
        invalidation_plan = self._compute_invalidation_plan(support, cover, trigger)
        reopened_patch_set = set(support.patch_keys)
        invalidated_cache_keys: tuple[str, ...] = ()
        if invalidation_plan is not None:
            reopened_patch_set.update(invalidation_plan.reopened_patches)
            invalidated_cache_keys = invalidation_plan.invalidated_keys

        retained = self._retained_records()
        decisions = tuple(
            self._evaluate_record(
                record,
                trigger=trigger,
                reopened_patch_set=reopened_patch_set,
                invalidated_cache_keys=invalidated_cache_keys,
                changed_dependencies=changed_dependencies,
                dependency_epochs=dependency_epochs,
                trust_floor=floor,
                treaty_fingerprint=treaty_fingerprint,
                certificate_fingerprint=certificate_fingerprint,
                validator=validator,
            )
            for record in retained
        )
        report = ReplayReport(
            changed_support=support,
            trigger=trigger,
            decisions=decisions,
            invalidation_plan=invalidation_plan,
            considered_records=len(retained),
        )
        if self.policy.remember_reports:
            note_key = self._memory_key("report", trigger.value)
            self.memory.remember(
                MemoryNote(
                    key=note_key,
                    value=report.to_dict(),
                    tags=(_REPLAY_MEMORY_TAG, "report", trigger.value),
                    provenance=ProvenanceTrace(f"replay:{trigger.value}"),
                )
            )
            report = replace(report, memory_note_keys=(note_key,))
        return report

    def replay_region(
        self,
        support: SupportRegion,
        *,
        cover: Cover | None = None,
        trigger: ReplayTrigger = ReplayTrigger.SUPPORT_CHANGE,
        changed_dependency_keys: Iterable[str] = (),
        dependency_epochs: Mapping[str, int] | None = None,
        trust_floor: TrustTier | None = None,
        treaty_fingerprint: str | None = None,
        certificate_fingerprint: str | None = None,
        validator: ReplayValidator | None = None,
    ) -> ReplayReport:
        return self.replay(
            support,
            cover=cover,
            trigger=trigger,
            changed_dependency_keys=changed_dependency_keys,
            dependency_epochs=dependency_epochs,
            trust_floor=trust_floor,
            treaty_fingerprint=treaty_fingerprint,
            certificate_fingerprint=certificate_fingerprint,
            validator=validator,
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "policy": {
                "name": self.policy.name,
                "trust_floor": self.policy.trust_floor.label(),
                "remember_reports": self.policy.remember_reports,
                "max_records": self.policy.max_records,
            },
            "defaults": _defaults_snapshot(self.defaults),
            "ledger": self.ledger.to_dict(),
            "cache_keys": sorted(self.cache.entries),
            "memory_keys": _memory_note_keys(self.memory),
        }

    def _retained_records(self) -> tuple[ReplayRecord, ...]:
        if self.policy.max_records <= 0:
            return tuple(self.ledger.records)
        return tuple(self.ledger.records[-self.policy.max_records :])

    def _compute_invalidation_plan(
        self,
        support: SupportRegion,
        cover: Cover | None,
        trigger: ReplayTrigger,
    ) -> InvalidationPlan | None:
        if cover is None:
            return None
        reason = {
            ReplayTrigger.SUPPORT_CHANGE: InvalidationReason.SUPPORT_CHANGE,
            ReplayTrigger.TRUST_CHANGE: InvalidationReason.TRUST_CHANGE,
            ReplayTrigger.DEPENDENCY_CHANGE: InvalidationReason.REPLAY_CONFLICT,
            ReplayTrigger.MANUAL_REOPEN: InvalidationReason.REPLAY_CONFLICT,
            ReplayTrigger.INITIAL_REPLAY: InvalidationReason.REPLAY_CONFLICT,
        }[trigger]
        return plan_invalidation(self.cache, support, cover, reason=reason)

    def _evaluate_record(
        self,
        record: ReplayRecord,
        *,
        trigger: ReplayTrigger,
        reopened_patch_set: set[str],
        invalidated_cache_keys: tuple[str, ...],
        changed_dependencies: set[str],
        dependency_epochs: Mapping[str, int] | None,
        trust_floor: TrustTier,
        treaty_fingerprint: str | None,
        certificate_fingerprint: str | None,
        validator: ReplayValidator | None,
    ) -> ReplayDecision:
        affected_patches = tuple(sorted(reopened_patch_set & set(record.support.patch_keys)))
        dependency_changed = bool(changed_dependencies & set(record.dependency_keys))
        seal_valid, seal_reasons = _evaluate_seal(
            record.seal,
            support=record.support,
            trust_floor=trust_floor,
            dependency_epochs=dependency_epochs,
            policy_tag=self.policy.name,
            treaty_fingerprint=treaty_fingerprint,
            certificate_fingerprint=certificate_fingerprint,
            require_provenance=self.policy.require_provenance,
            allow_unsealed=self.policy.allow_unsealed_replay,
        )
        impacted = bool(affected_patches) or dependency_changed
        if dependency_changed:
            seal_reasons = seal_reasons + ("declared-dependency-changed",)

        if not impacted and seal_valid:
            return ReplayDecision(
                record=record,
                status=ReplayStatus.REUSED,
                trigger=trigger,
                reasons=("outside-replay-region",),
                affected_patches=affected_patches,
                invalidated_cache_keys=(),
                validator_used=False,
                seal_valid=True,
            )

        if impacted and seal_valid and validator is not None:
            validator_ok, validator_reasons = _apply_validator(validator, record)
            return ReplayDecision(
                record=record,
                status=ReplayStatus.REVALIDATED if validator_ok else ReplayStatus.INVALIDATED,
                trigger=trigger,
                reasons=validator_reasons
                or (("revalidated-under-original-witness-schema",) if validator_ok else ("validator-rejected-record",)),
                affected_patches=affected_patches,
                invalidated_cache_keys=invalidated_cache_keys if affected_patches else (),
                validator_used=True,
                seal_valid=validator_ok,
            )

        if impacted and seal_valid:
            reasons = ["affected-by-support-local-reopening"]
            if dependency_changed:
                reasons.append("declared-dependency-changed")
            if affected_patches:
                reasons.append("within-reopened-star")
            return ReplayDecision(
                record=record,
                status=ReplayStatus.REOPENED,
                trigger=trigger,
                reasons=tuple(reasons),
                affected_patches=affected_patches,
                invalidated_cache_keys=invalidated_cache_keys if affected_patches else (),
                validator_used=False,
                seal_valid=True,
            )

        return ReplayDecision(
            record=record,
            status=ReplayStatus.INVALIDATED,
            trigger=trigger,
            reasons=seal_reasons or ("seal-invalid",),
            affected_patches=affected_patches,
            invalidated_cache_keys=invalidated_cache_keys if affected_patches else (),
            validator_used=False,
            seal_valid=False,
        )

    def _memory_key(self, kind: str, suffix: str) -> str:
        timestamp = time.time_ns()
        return f"replay:{kind}:{suffix}:{timestamp}"

    # -- cross-subsystem integration -----------------------------------------

    def judgment_replay(
        self,
        judgment: Any,
        support: SupportRegion,
        trust: TrustProfile,
        provenance: ProvenanceTrace,
        *,
        trigger: ReplayTrigger = ReplayTrigger.INITIAL_REPLAY,
    ) -> ReplayReport:
        """Replay the construction of a judgment term.

        Uses ``jugeo.judgments.judgment_terms.Judgment`` to derive the
        replay key and support fingerprint from the judgment's
        coordinate and proposition, then delegates to :meth:`replay`.

        Parameters
        ----------
        judgment:
            A ``Judgment`` from ``jugeo.judgments.judgment_terms``.
        support:
            The support region in which the judgment was originally
            discharged.
        trust:
            Trust profile for the replay context.
        provenance:
            Provenance trace of the original discharge.
        trigger:
            Replay trigger kind.

        Returns
        -------
        ReplayReport
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment as JT
        except ImportError:  # pragma: no cover
            JT = None  # type: ignore[assignment,misc]

        name = "judgment-replay"
        if JT is not None and isinstance(judgment, JT):
            coord_str = (
                ".".join(judgment.coordinate.components)
                if hasattr(judgment.coordinate, "components")
                else str(judgment.coordinate)
            )
            name = f"judgment-replay:{coord_str}"

        # Capture the judgment as a replayable record first
        self.capture(
            name=name,
            support=support,
            trust=trust,
            provenance=provenance,
            payload=judgment,
            semantic_tags=("judgment",),
        )
        return self.replay(support, trigger=trigger)

    def solver_replay(
        self,
        session_snapshot: Any,
        support: SupportRegion,
        trust: TrustProfile,
        provenance: ProvenanceTrace,
        *,
        trigger: ReplayTrigger = ReplayTrigger.INITIAL_REPLAY,
    ) -> ReplayReport:
        """Replay solver queries from a Z3 session snapshot.

        Uses ``jugeo.solver.z3_session.Z3Session`` to reconstruct the
        solver state and replay the query sequence under the current
        support region.

        Parameters
        ----------
        session_snapshot:
            A ``Z3Session`` from ``jugeo.solver.z3_session``, or a dict
            representing a serialized session state.
        support:
            Support region for the replay.
        trust:
            Trust profile.
        provenance:
            Provenance trace.
        trigger:
            Replay trigger kind.

        Returns
        -------
        ReplayReport
        """
        try:
            from jugeo.solver.z3_session import Z3Session
        except ImportError:  # pragma: no cover
            Z3Session = None  # type: ignore[assignment,misc]

        payload: Any = session_snapshot
        name = "solver-replay"
        if Z3Session is not None and isinstance(session_snapshot, Z3Session):
            name = f"solver-replay:{getattr(session_snapshot, 'session_id', 'unknown')}"
            if hasattr(session_snapshot, "to_dict"):
                payload = session_snapshot.to_dict()

        self.capture(
            name=name,
            support=support,
            trust=trust,
            provenance=provenance,
            payload=payload,
            semantic_tags=("solver",),
        )
        return self.replay(support, trigger=trigger)

    def provenance_replay(
        self,
        support: SupportRegion,
        *,
        trigger: ReplayTrigger = ReplayTrigger.INITIAL_REPLAY,
    ) -> dict[str, Any]:
        """Trace replay provenance for records in a support region.

        Uses ``jugeo.evidence.provenance.ProvenanceTrace`` to build a
        provenance-annotated summary of every replay decision in the
        specified region.

        Parameters
        ----------
        support:
            The support region to trace.
        trigger:
            Replay trigger kind.

        Returns
        -------
        dict[str, Any]
            A summary dict with ``"decisions"`` and ``"provenance_chains"``
            keys.
        """
        try:
            from jugeo.evidence.provenance import ProvenanceTrace as PT
        except ImportError:  # pragma: no cover
            PT = None  # type: ignore[assignment,misc]

        report = self.replay(support, trigger=trigger)
        provenance_chains: list[dict[str, Any]] = []
        for decision in report.decisions:
            record = decision.record
            chain: dict[str, Any] = {
                "record_key": record.stable_key,
                "status": decision.status.value,
                "trigger": decision.trigger.value,
            }
            if PT is not None and isinstance(record.provenance, PT):
                chain["origin"] = record.provenance.origin
                chain["steps"] = len(record.provenance.steps)
                chain["trace"] = record.provenance.to_dict()
            else:
                chain["origin"] = str(getattr(record.provenance, "origin", "unknown"))
                chain["steps"] = 0
            provenance_chains.append(chain)

        return {
            "report_summary": report.summary(),
            "decisions": [d.to_dict() for d in report.decisions],
            "provenance_chains": provenance_chains,
        }


def seal_is_valid(
    seal: ReplaySeal | None,
    *,
    support: SupportRegion | None = None,
    trust_floor: TrustTier | None = None,
    dependency_epochs: Mapping[str, int] | None = None,
    policy_tag: str | None = None,
    treaty_fingerprint: str | None = None,
    certificate_fingerprint: str | None = None,
    require_provenance: bool = True,
) -> bool:
    valid, _ = _evaluate_seal(
        seal,
        support=support,
        trust_floor=trust_floor,
        dependency_epochs=dependency_epochs,
        policy_tag=policy_tag,
        treaty_fingerprint=treaty_fingerprint,
        certificate_fingerprint=certificate_fingerprint,
        require_provenance=require_provenance,
        allow_unsealed=False,
    )
    return valid


def replay_region(
    engine: ReplayEngine,
    support: SupportRegion,
    *,
    cover: Cover | None = None,
    trigger: ReplayTrigger = ReplayTrigger.SUPPORT_CHANGE,
    changed_dependency_keys: Iterable[str] = (),
    dependency_epochs: Mapping[str, int] | None = None,
    trust_floor: TrustTier | None = None,
    treaty_fingerprint: str | None = None,
    certificate_fingerprint: str | None = None,
    validator: ReplayValidator | None = None,
) -> ReplayReport:
    return engine.replay_region(
        support,
        cover=cover,
        trigger=trigger,
        changed_dependency_keys=changed_dependency_keys,
        dependency_epochs=dependency_epochs,
        trust_floor=trust_floor,
        treaty_fingerprint=treaty_fingerprint,
        certificate_fingerprint=certificate_fingerprint,
        validator=validator,
    )


def _evaluate_seal(
    seal: ReplaySeal | None,
    *,
    support: SupportRegion | None,
    trust_floor: TrustTier | None,
    dependency_epochs: Mapping[str, int] | None,
    policy_tag: str | None,
    treaty_fingerprint: str | None,
    certificate_fingerprint: str | None,
    require_provenance: bool,
    allow_unsealed: bool,
) -> tuple[bool, tuple[str, ...]]:
    if seal is None:
        if allow_unsealed:
            return True, ()
        return False, ("record-has-no-replay-seal",)

    reasons: list[str] = []
    if support is not None and not seal.supports(support):
        reasons.append("support-fingerprint-mismatch")
    if trust_floor is not None and not seal.trust_meets_floor(trust_floor):
        reasons.append("trust-floor-not-met")
    if require_provenance and not seal.provenance_origin.strip():
        reasons.append("missing-provenance-origin")
    if require_provenance and seal.provenance_steps < 0:
        reasons.append("invalid-provenance-step-count")
    if policy_tag is not None and seal.policy_tag != policy_tag:
        reasons.append("policy-tag-mismatch")
    if treaty_fingerprint is not None and seal.treaty_fingerprint != treaty_fingerprint:
        reasons.append("treaty-fingerprint-mismatch")
    if certificate_fingerprint is not None and seal.certificate_fingerprint != certificate_fingerprint:
        reasons.append("certificate-fingerprint-mismatch")
    if dependency_epochs is not None:
        for key, expected_epoch in seal.dependency_epochs:
            actual_epoch = dependency_epochs.get(key)
            if actual_epoch is None:
                reasons.append(f"missing-dependency-epoch:{key}")
                continue
            if actual_epoch != expected_epoch:
                reasons.append(f"dependency-epoch-mismatch:{key}")
    return (not reasons), tuple(reasons)


def _apply_validator(
    validator: ReplayValidator,
    record: ReplayRecord,
) -> tuple[bool, tuple[str, ...]]:
    outcome = validator(record)
    if isinstance(outcome, tuple):
        valid, reasons = outcome
        normalized = _normalize_strings(reasons)
        return bool(valid), normalized
    return bool(outcome), (("validator-accepted-record",) if outcome else ("validator-rejected-record",))


def _tier_from_label(label: str) -> TrustTier:
    normalized = label.strip().lower()
    for tier in TrustTier.ordered():
        if tier.label() == normalized:
            return tier
    raise ValueError(f"Unknown trust tier label: {label!r}")


def _normalize_strings(values: Iterable[str]) -> tuple[str, ...]:
    normalized = {str(value).strip() for value in values if str(value).strip()}
    return tuple(sorted(normalized))


def _normalize_epoch_pairs(values: Mapping[str, int] | Iterable[tuple[str, int]] | None) -> tuple[tuple[str, int], ...]:
    if values is None:
        return ()
    if isinstance(values, Mapping):
        items = values.items()
    else:
        items = values
    return tuple(sorted((str(key), int(epoch)) for key, epoch in items))


def _support_snapshot(support: SupportRegion) -> dict[str, object]:
    return {
        "coordinate": support.coordinate.key,
        "patch_keys": list(sorted(support.patch_keys)),
        "labels": list(sorted(support.labels)),
        "provenance": list(support.provenance),
    }


def _invalidation_snapshot(plan: InvalidationPlan | None) -> dict[str, object] | None:
    if plan is None:
        return None
    return {
        "reason": plan.reason.value,
        "invalidated_keys": list(plan.invalidated_keys),
        "reopened_patches": list(plan.reopened_patches),
    }


def _defaults_snapshot(defaults: RuntimeDefaults) -> dict[str, object]:
    if hasattr(defaults, 'as_dict'):
        payload = defaults.as_dict()
        if isinstance(payload, dict):
            return payload
    if hasattr(defaults, 'get_all'):
        payload = defaults.get_all()
        if isinstance(payload, dict):
            return payload
    return {'preset': getattr(getattr(defaults, 'preset', None), 'value', str(getattr(defaults, 'preset', 'unknown')))}


def _memory_note_keys(memory: SemanticMemory) -> list[str]:
    if hasattr(memory, 'notes'):
        notes = getattr(memory, 'notes')
        if isinstance(notes, Mapping):
            return sorted(str(key) for key in notes)
    internal = getattr(memory, '_notes', None)
    if isinstance(internal, Mapping):
        return sorted(str(key) for key in internal)
    return []


def _support_fingerprint(support: SupportRegion) -> str:
    return _stable_digest(_support_snapshot(support))


def _stable_digest(payload: Any) -> str:
    material = _stable_json(payload)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _stable_json(payload: Any) -> str:
    return json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"))


def _json_safe(payload: Any) -> JsonValue:
    if payload is None or isinstance(payload, (bool, int, float, str)):
        return payload
    if isinstance(payload, Enum):
        return payload.value
    if isinstance(payload, TrustProfile):
        return payload.to_dict()  # type: ignore[return-value]
    if isinstance(payload, ProvenanceTrace):
        return payload.to_dict()  # type: ignore[return-value]
    if isinstance(payload, SupportRegion):
        return _support_snapshot(payload)  # type: ignore[return-value]
    if isinstance(payload, Mapping):
        return {str(key): _json_safe(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_json_safe(value) for value in payload]
    if isinstance(payload, (set, frozenset)):
        return [_json_safe(value) for value in sorted(payload, key=str)]
    if hasattr(payload, "to_dict"):
        return _json_safe(payload.to_dict())
    return str(payload)


__all__ = [
    "ReplayStatus",
    "ReplayTrigger",
    "ReplayPolicy",
    "ReplaySeal",
    "ReplayRecord",
    "ReplayLedger",
    "ReplayDecision",
    "ReplayReport",
    "ReplayEngine",
    "replay_region",
    "seal_is_valid",
]

# copilot: shared-runtime replay surface for future orchestration and agent reuse.
