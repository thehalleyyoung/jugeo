"""Integration layer connecting the live_mutation package to other JuGeo subsystems.

Bridges dynamic section operations (exec injection, monkey patching, hot reloads)
to the geometry supports subsystem, the judgment trust subsystem, the evidence
channels subsystem, and the fleet orchestration subsystem. In sheaf-theoretic
terms, this module ensures that every mutation event leaves a consistent trail
across all semantic-verification layers of the framework.

Theory alignment: Ch23 of theory2.tex.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:
    SupportRegion = SupportSet = SupportTracker = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import LocalJudgment, JudgmentStatus, TrustTier
except ImportError:
    LocalJudgment = JudgmentStatus = TrustTier = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRecord, ChannelRouter
except ImportError:
    EvidenceChannel = EvidenceRecord = ChannelRouter = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.fleet import Fleet, FleetBid, FleetMember
except ImportError:
    Fleet = FleetBid = FleetMember = None  # type: ignore[assignment,misc]

try:
    from jugeo.python_runtime.live_mutation.models import (
        MutationKind,
        InvalidationScope,
        ReloadStatus,
        DynamicSection,
        ExecContext,
        EvalResult,
        MonkeyPatchRecord,
        HotReloadEvent,
    )
except ImportError:
    MutationKind = InvalidationScope = ReloadStatus = None  # type: ignore[assignment]
    DynamicSection = ExecContext = EvalResult = MonkeyPatchRecord = HotReloadEvent = None  # type: ignore[assignment]


@dataclass
class SupportBridge:
    """Bridges dynamic sections to the geometry supports subsystem.

    When a section is injected via exec, a corresponding SupportRegion is
    synthesised and indexed here. When a patch invalidates sections, the
    support bridge propagates the invalidation to the support tracker, removing
    stale SupportRegion mappings and enqueuing sections for re-synthesis.

    This maintains consistency between the live_mutation view and the geometry
    layer's view of what sections are supported at which coordinates.
    """

    _section_to_support: dict[str, dict] = field(default_factory=dict)  # section_id -> support record
    _support_to_section: dict[str, str] = field(default_factory=dict)  # support_key -> section_id
    _sync_log: list[dict] = field(default_factory=list)
    _invalidation_queue: list[str] = field(default_factory=list)

    def section_to_support(
        self,
        section_id: str,
        support_coordinate: str,
        patch_keys: list[str],
        labels: list[str] | None = None,
    ) -> dict:
        """Create and index a support record for a dynamic section.

        Generates a unique support_key and builds a support record dict that
        captures the section_id, geometric coordinate, associated patch_keys,
        optional labels, and creation timestamp. Both forward and reverse
        indexes are updated.

        Args:
            section_id: Unique identifier of the injected dynamic section.
            support_coordinate: String representation of the geometric coordinate.
            patch_keys: List of patch keys that constrain this support region.
            labels: Optional list of human-readable labels for the region.

        Returns:
            The support record dict.
        """
        support_key = f"sup-{uuid.uuid4().hex[:12]}"
        record: dict = {
            "support_key": support_key,
            "section_id": section_id,
            "coordinate": support_coordinate,
            "patch_keys": list(patch_keys),
            "labels": list(labels) if labels else [],
            "created_at": time.time(),
            "status": "ACTIVE",
        }
        self._section_to_support[section_id] = record
        self._support_to_section[support_key] = section_id
        self._sync_log.append(
            {
                "event": "SECTION_MAPPED",
                "section_id": section_id,
                "support_key": support_key,
                "timestamp": time.time(),
            }
        )
        return record

    def support_to_section(self, support_key: str) -> str | None:
        """Resolve a support_key to its owning section_id.

        Args:
            support_key: The support region key to look up.

        Returns:
            The section_id string if found, otherwise None.
        """
        return self._support_to_section.get(support_key)

    def sync_on_patch(
        self,
        patch_id: str,
        invalidated_section_ids: list[str],
    ) -> int:
        """Queue invalidated sections for re-synchronisation after a patch.

        Each section in *invalidated_section_ids* is added to the internal
        invalidation queue so that a subsequent flush pass can re-synthesise
        the corresponding SupportRegion objects. A sync event is written to
        the sync log.

        Args:
            patch_id: Identifier of the monkey-patch operation.
            invalidated_section_ids: Sections invalidated by the patch.

        Returns:
            The number of sections enqueued.
        """
        queued = 0
        for section_id in invalidated_section_ids:
            if section_id not in self._invalidation_queue:
                self._invalidation_queue.append(section_id)
                queued += 1
        self._sync_log.append(
            {
                "event": "PATCH_SYNC",
                "patch_id": patch_id,
                "queued_count": queued,
                "timestamp": time.time(),
            }
        )
        return queued

    def propagate_invalidation(self, section_id: str) -> dict:
        """Remove a section's support mapping and enqueue it for re-synthesis.

        Removes the section from both forward and reverse indexes, adds it to
        the invalidation queue, and appends a log record.

        Args:
            section_id: The section whose support record should be invalidated.

        Returns:
            The log record dict describing the invalidation event.
        """
        support_record = self._section_to_support.pop(section_id, None)
        if support_record:
            self._support_to_section.pop(support_record["support_key"], None)

        if section_id not in self._invalidation_queue:
            self._invalidation_queue.append(section_id)

        log_record: dict = {
            "event": "PROPAGATE_INVALIDATION",
            "section_id": section_id,
            "had_support": support_record is not None,
            "support_key": support_record.get("support_key") if support_record else None,
            "timestamp": time.time(),
        }
        self._sync_log.append(log_record)
        return log_record

    def check_support_coverage(self, section_ids: list[str]) -> dict:
        """Report which sections have active support records and which do not.

        Args:
            section_ids: List of section IDs to check coverage for.

        Returns:
            Dict with 'covered' list, 'uncovered' list, and 'coverage_ratio'.
        """
        covered = [sid for sid in section_ids if sid in self._section_to_support]
        uncovered = [sid for sid in section_ids if sid not in self._section_to_support]
        total = len(section_ids)
        ratio = len(covered) / total if total > 0 else 0.0
        return {
            "covered": covered,
            "uncovered": uncovered,
            "coverage_ratio": round(ratio, 4),
        }

    def flush_invalidation_queue(self) -> list[str]:
        """Return and clear the invalidation queue.

        Returns:
            The list of section IDs that were waiting for re-synthesis.
        """
        flushed = list(self._invalidation_queue)
        self._invalidation_queue.clear()
        return flushed

    def export_bridge_state(self) -> dict:
        """Export the full SupportBridge state as a serialisable dict.

        Returns:
            Dict with section_count, support_count, queue_length, sync_count,
            and a compact summary of the current section→support mappings.
        """
        mapping_summary = {
            sid: rec.get("support_key")
            for sid, rec in self._section_to_support.items()
        }
        return {
            "section_count": len(self._section_to_support),
            "support_count": len(self._support_to_section),
            "queue_length": len(self._invalidation_queue),
            "sync_count": len(self._sync_log),
            "mapping_summary": mapping_summary,
        }


@dataclass
class JudgmentBridge:
    """Bridges mutation events to the judgment trust subsystem.

    Monkey patches, exec injections, and hot reloads can all affect the trust
    level of existing judgments. This bridge translates mutation events into
    trust delta records and obligation updates, maintaining the integrity of
    the judgment algebra by ensuring that trust cannot silently remain high
    after a section's supporting code has been mutated.
    """

    _trust_deltas: list[dict] = field(default_factory=list)
    _obligation_updates: list[dict] = field(default_factory=list)
    _judgment_cache: dict[str, dict] = field(default_factory=dict)

    def patch_to_trust_delta(
        self,
        patch_id: str,
        target_module: str,
        target_attribute: str,
        affected_judgments: list[str] | None = None,
    ) -> dict:
        """Compute a trust delta triggered by a monkey patch.

        A monkey patch replaces a module attribute at runtime, which can
        silently break the contract assumed by judgments that depended on the
        original attribute value. This method records a trust downgrade to
        PROPOSAL tier for all affected judgments.

        Args:
            patch_id: Identifier of the monkey-patch operation.
            target_module: Fully-qualified module whose attribute is patched.
            target_attribute: Name of the attribute being replaced.
            affected_judgments: List of judgment IDs affected; defaults to [].

        Returns:
            The trust delta record dict.
        """
        delta: dict = {
            "delta_id": f"delta-{uuid.uuid4().hex[:8]}",
            "patch_id": patch_id,
            "target": f"{target_module}.{target_attribute}",
            "affected_judgments": list(affected_judgments) if affected_judgments else [],
            "trust_change": "DOWNGRADE_TO_PROPOSAL",
            "previous_tier": "UNKNOWN",
            "new_tier": "PROPOSAL",
            "computed_at": time.time(),
        }
        self._trust_deltas.append(delta)
        return delta

    def reload_to_obligation(
        self,
        event_id: str,
        module_name: str,
        sections_replaced: list[str],
    ) -> dict:
        """Create a verification obligation triggered by a hot reload.

        When sections are replaced by a hot reload, existing proofs or
        verifications that reference those sections are no longer valid.
        This method records an obligation to re-verify the replaced sections.

        Args:
            event_id: Identifier of the hot reload event.
            module_name: Fully-qualified module being reloaded.
            sections_replaced: Section IDs that were replaced.

        Returns:
            The obligation update record dict.
        """
        obligation: dict = {
            "obligation_id": f"obl-{uuid.uuid4().hex[:8]}",
            "event_id": event_id,
            "module_name": module_name,
            "sections_count": len(sections_replaced),
            "sections_replaced": list(sections_replaced),
            "obligation": "RE_VERIFY_REPLACED_SECTIONS",
            "status": "OPEN",
            "created_at": time.time(),
        }
        self._obligation_updates.append(obligation)
        return obligation

    def exec_to_judgment(
        self,
        section_id: str,
        defined_names: list[str],
        trust_level: str,
    ) -> dict:
        """Translate an exec injection into a minimal judgment record.

        Creates a placeholder judgment capturing the section, the names it
        defines, and its current trust level. The judgment starts in OPEN
        status and must be promoted by external corroboration.

        Args:
            section_id: Unique identifier of the injected section.
            defined_names: Top-level names introduced by the section.
            trust_level: Trust tier for the section.

        Returns:
            The judgment record dict stored in _judgment_cache.
        """
        judgment_id = f"jdg-{uuid.uuid4().hex[:8]}"
        record: dict = {
            "judgment_id": judgment_id,
            "section_id": section_id,
            "proposition": (
                f"Section {section_id} defines {len(defined_names)} symbol(s): "
                + ", ".join(defined_names[:5])
                + ("…" if len(defined_names) > 5 else "")
            ),
            "trust_level": trust_level,
            "status": "OPEN",
            "defined_names": list(defined_names),
            "created_at": time.time(),
        }
        self._judgment_cache[section_id] = record
        return record

    def sync_trust_after_reload(
        self,
        event_id: str,
        module_name: str,
    ) -> dict:
        """Create a trust sync record after a hot reload.

        Indicates that all judgments associated with *module_name* should be
        re-evaluated because the module's sections have changed.

        Args:
            event_id: Identifier of the hot reload event.
            module_name: Module whose judgments need re-evaluation.

        Returns:
            The sync record dict.
        """
        affected_judgments = [
            jdg["judgment_id"]
            for jdg in self._judgment_cache.values()
            if module_name in jdg.get("section_id", "")
        ]
        sync_record: dict = {
            "sync_id": f"sync-{uuid.uuid4().hex[:8]}",
            "event_id": event_id,
            "module_name": module_name,
            "affected_judgment_count": len(affected_judgments),
            "affected_judgments": affected_judgments,
            "action": "RE_EVALUATE_ALL_MODULE_JUDGMENTS",
            "synced_at": time.time(),
        }
        self._trust_deltas.append(sync_record)
        return sync_record

    def get_trust_deltas(self, patch_id: str | None = None) -> list[dict]:
        """Return trust delta records, optionally filtered by patch_id.

        Args:
            patch_id: If provided, only deltas with this patch_id are returned.

        Returns:
            List of matching trust delta dicts.
        """
        if patch_id is None:
            return list(self._trust_deltas)
        return [d for d in self._trust_deltas if d.get("patch_id") == patch_id]

    def obligation_summary(self) -> dict:
        """Return aggregate statistics about recorded obligations.

        Returns:
            Dict with total_obligations, open_count, unique modules_affected,
            and avg_sections_per_reload.
        """
        total = len(self._obligation_updates)
        open_count = sum(
            1 for o in self._obligation_updates if o.get("status") == "OPEN"
        )
        modules = {o.get("module_name") for o in self._obligation_updates if o.get("module_name")}
        sections_counts = [o.get("sections_count", 0) for o in self._obligation_updates]
        avg_sections = sum(sections_counts) / total if total else 0.0
        return {
            "total_obligations": total,
            "open_count": open_count,
            "modules_affected": sorted(modules),
            "avg_sections_per_reload": round(avg_sections, 2),
        }

    def export_bridge_state(self) -> dict:
        """Export the full JudgmentBridge state.

        Returns:
            Dict with trust_delta_count, obligation_count, judgment_cache_size,
            and last_sync_at timestamp.
        """
        sync_records = [d for d in self._trust_deltas if "synced_at" in d]
        last_sync = max((r["synced_at"] for r in sync_records), default=None)
        return {
            "trust_delta_count": len(self._trust_deltas),
            "obligation_count": len(self._obligation_updates),
            "judgment_cache_size": len(self._judgment_cache),
            "last_sync_at": last_sync,
        }


@dataclass
class ChannelBridge:
    """Bridges live mutation events to the evidence channels subsystem.

    Dynamic sections injected via exec are submitted as evidence through the
    copilot channel at proposal-tier trust. Patch events generate channel
    routing updates through the runtime channel. This bridge ensures that all
    mutation-derived evidence flows through the correct channels with
    appropriate trust ceilings, so no mutation can silently elevate its own
    trust.
    """

    _channel_records: list[dict] = field(default_factory=list)
    _routing_table: dict[str, str] = field(default_factory=dict)  # mutation_kind -> channel_name
    _submission_log: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise the default routing table."""
        self._routing_table.setdefault("EXEC_INJECTION", "COPILOT")
        self._routing_table.setdefault("EVAL_QUERY", "COPILOT")
        self._routing_table.setdefault("MONKEY_PATCH", "RUNTIME")
        self._routing_table.setdefault("HOT_RELOAD", "RUNTIME")

    def submit_exec_as_evidence(
        self,
        section_id: str,
        source_code: str,
        defined_names: list[str],
    ) -> dict:
        """Submit an exec injection as evidence via the copilot channel.

        Builds an evidence submission record capturing the section, source code
        hash, defined names, the routing channel (COPILOT), trust ceiling
        (PROPOSAL), and submission timestamp.

        Args:
            section_id: Unique identifier of the injected section.
            source_code: Raw Python source code.
            defined_names: Top-level names introduced by the code.

        Returns:
            The evidence submission record dict.
        """
        submission: dict = {
            "submission_id": f"sub-{uuid.uuid4().hex[:8]}",
            "section_id": section_id,
            "channel": "COPILOT",
            "trust_ceiling": "PROPOSAL",
            "evidence_type": "EXEC_INJECTION",
            "defined_names": list(defined_names),
            "source_hash": hashlib.sha256(source_code.encode()).hexdigest(),
            "submitted_at": time.time(),
        }
        self._channel_records.append(submission)
        self._submission_log.append(submission)
        return submission

    def submit_patch_event(
        self,
        patch_id: str,
        target: str,
    ) -> dict:
        """Submit a monkey-patch event as evidence via the runtime channel.

        Records that a patch was applied, routing the evidence through the
        RUNTIME channel with a CORROBORATED trust ceiling.

        Args:
            patch_id: Identifier of the patch operation.
            target: Fully-qualified target attribute path.

        Returns:
            The evidence record dict.
        """
        record: dict = {
            "submission_id": f"sub-patch-{uuid.uuid4().hex[:8]}",
            "patch_id": patch_id,
            "target": target,
            "channel": "RUNTIME",
            "trust_ceiling": "CORROBORATED",
            "evidence_type": "MONKEY_PATCH",
            "submitted_at": time.time(),
        }
        self._channel_records.append(record)
        self._submission_log.append(record)
        return record

    def route_mutation_event(self, mutation_kind: str) -> str:
        """Return the evidence channel name for a given mutation kind.

        Looks up *mutation_kind* in _routing_table. Falls back to 'COPILOT'
        for injection/eval kinds and 'RUNTIME' for all others.

        Args:
            mutation_kind: Mutation type string.

        Returns:
            Channel name string.
        """
        if mutation_kind in self._routing_table:
            return self._routing_table[mutation_kind]
        if mutation_kind in ("EXEC_INJECTION", "EVAL_QUERY"):
            return "COPILOT"
        return "RUNTIME"

    def register_route(self, mutation_kind: str, channel_name: str) -> None:
        """Register a custom routing rule overriding the default.

        Args:
            mutation_kind: Mutation type string to route.
            channel_name: Target channel name.
        """
        self._routing_table[mutation_kind] = channel_name

    def channel_stats(self) -> dict:
        """Return aggregate statistics for channel submissions.

        Returns:
            Dict with total_submissions, by_channel (channel → count), and
            by_mutation_kind (evidence_type → count).
        """
        by_channel: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for record in self._submission_log:
            ch = record.get("channel", "UNKNOWN")
            kind = record.get("evidence_type", "UNKNOWN")
            by_channel[ch] = by_channel.get(ch, 0) + 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "total_submissions": len(self._submission_log),
            "by_channel": by_channel,
            "by_mutation_kind": by_kind,
        }

    def export_bridge_state(self) -> dict:
        """Export the full ChannelBridge state.

        Returns:
            Dict with channel_record_count, routing_table, submission_count.
        """
        return {
            "channel_record_count": len(self._channel_records),
            "routing_table": dict(self._routing_table),
            "submission_count": len(self._submission_log),
        }


@dataclass
class FleetBridge:
    """Bridges live mutation operations to fleet orchestration for distributed reloads.

    When a hot reload is needed across multiple fleet members, this bridge
    coordinates the reload sequence: it registers the request with the fleet,
    collects bids from candidate members, accepts the optimal bid, and
    aggregates results once members complete their reload steps.

    Theory alignment: Ch23 §3.3 (distributed descent) of theory2.tex.
    """

    _fleet_reload_requests: list[dict] = field(default_factory=list)
    _bid_log: list[dict] = field(default_factory=list)
    _distributed_results: list[dict] = field(default_factory=list)
    fleet_id: str = field(default_factory=lambda: f"fleet-{uuid.uuid4().hex[:8]}")

    def register_reload_with_fleet(
        self,
        event_id: str,
        module_name: str,
        sections: list[str],
    ) -> dict:
        """Register a hot reload request with the fleet.

        Creates a pending reload request that fleet members can bid on.

        Args:
            event_id: Identifier of the originating hot reload event.
            module_name: Fully-qualified module to reload.
            sections: Section IDs to be replaced.

        Returns:
            The reload request record dict.
        """
        request: dict = {
            "request_id": f"req-{uuid.uuid4().hex[:8]}",
            "event_id": event_id,
            "fleet_id": self.fleet_id,
            "module_name": module_name,
            "sections": list(sections),
            "section_count": len(sections),
            "registered_at": time.time(),
            "status": "PENDING",
        }
        self._fleet_reload_requests.append(request)
        return request

    def bid_on_reload(
        self,
        request_id: str,
        member_id: str,
        cost_estimate: float,
    ) -> dict:
        """Submit a bid from a fleet member for a reload request.

        A lower cost_estimate indicates the member is better positioned to
        handle the reload efficiently.

        Args:
            request_id: Identifier of the reload request.
            member_id: Identifier of the bidding fleet member.
            cost_estimate: Member's estimated cost/latency for the reload.

        Returns:
            The bid record dict.
        """
        bid: dict = {
            "bid_id": f"bid-{uuid.uuid4().hex[:8]}",
            "request_id": request_id,
            "member_id": member_id,
            "cost_estimate": cost_estimate,
            "submitted_at": time.time(),
            "accepted": False,
        }
        self._bid_log.append(bid)
        return bid

    def coordinate_distributed_patch(
        self,
        patch_id: str,
        target_members: list[str],
        attribute: str,
    ) -> dict:
        """Coordinate a distributed monkey patch across fleet members.

        Creates a coordination record that tracks the patch propagation across
        the specified members.

        Args:
            patch_id: Identifier of the monkey-patch operation.
            target_members: List of fleet member IDs that should apply the patch.
            attribute: Fully-qualified attribute path being patched.

        Returns:
            The coordination record dict.
        """
        coordination: dict = {
            "coordination_id": f"coord-{uuid.uuid4().hex[:8]}",
            "patch_id": patch_id,
            "fleet_id": self.fleet_id,
            "target_members": list(target_members),
            "member_count": len(target_members),
            "attribute": attribute,
            "coordination_status": "COORDINATING",
            "started_at": time.time(),
        }
        self._fleet_reload_requests.append(coordination)
        return coordination

    def receive_fleet_reload_result(
        self,
        request_id: str,
        member_id: str,
        success: bool,
        error: str | None = None,
    ) -> dict:
        """Record the outcome of a reload attempt by a fleet member.

        Args:
            request_id: The reload request the member was executing.
            member_id: The fleet member reporting its result.
            success: Whether the member's reload succeeded.
            error: Optional error message if the reload failed.

        Returns:
            The result record dict.
        """
        result: dict = {
            "result_id": f"res-{uuid.uuid4().hex[:8]}",
            "request_id": request_id,
            "member_id": member_id,
            "success": success,
            "error": error,
            "received_at": time.time(),
        }
        self._distributed_results.append(result)
        return result

    def accept_best_bid(self, request_id: str) -> dict | None:
        """Select and accept the lowest-cost bid for a reload request.

        Scans all bids for *request_id*, finds the one with the minimum
        cost_estimate, marks it as accepted in-place, and returns it.

        Args:
            request_id: The reload request to find the best bid for.

        Returns:
            The accepted bid dict, or None if no bids exist.
        """
        candidates = [b for b in self._bid_log if b.get("request_id") == request_id]
        if not candidates:
            return None
        best = min(candidates, key=lambda b: b.get("cost_estimate", float("inf")))
        best["accepted"] = True
        best["accepted_at"] = time.time()
        return best

    def fleet_stats(self) -> dict:
        """Return aggregate fleet bridge statistics.

        Returns:
            Dict with total_requests, total_bids, accepted_bids,
            distributed_results count, and avg_bid_cost.
        """
        total_bids = len(self._bid_log)
        accepted = sum(1 for b in self._bid_log if b.get("accepted"))
        costs = [b.get("cost_estimate", 0.0) for b in self._bid_log]
        avg_cost = sum(costs) / total_bids if total_bids else 0.0
        return {
            "total_requests": len(self._fleet_reload_requests),
            "total_bids": total_bids,
            "accepted_bids": accepted,
            "distributed_results": len(self._distributed_results),
            "avg_bid_cost": round(avg_cost, 4),
        }

    def export_bridge_state(self) -> dict:
        """Export the full FleetBridge state.

        Returns:
            Dict with fleet_id, request_count, bid_count, result_count.
        """
        return {
            "fleet_id": self.fleet_id,
            "request_count": len(self._fleet_reload_requests),
            "bid_count": len(self._bid_log),
            "result_count": len(self._distributed_results),
        }


@dataclass
class LiveMutationIntegration:
    """Main integration class connecting all live_mutation bridges to JuGeo.

    Provides a single entry point for initialising the integration, running
    health checks, and exporting the full integration state. Delegates to the
    four bridge classes — SupportBridge, JudgmentBridge, ChannelBridge, and
    FleetBridge — while maintaining a unified integration log.
    """

    support_bridge: SupportBridge = field(default_factory=SupportBridge)
    judgment_bridge: JudgmentBridge = field(default_factory=JudgmentBridge)
    channel_bridge: ChannelBridge = field(default_factory=ChannelBridge)
    fleet_bridge: FleetBridge = field(default_factory=FleetBridge)
    _integration_log: list[dict] = field(default_factory=list)
    _initialized_at: float = field(default_factory=time.time)

    def _log(self, event: str, data: dict) -> None:
        """Append an event to the integration log."""
        self._integration_log.append(
            {"event": event, "data": data, "timestamp": time.time()}
        )

    def integrate_with_supports(
        self,
        section_id: str,
        coordinate: str,
        patch_keys: list[str],
    ) -> dict:
        """Register a section with the geometry supports subsystem.

        Delegates to SupportBridge.section_to_support and logs the event.

        Args:
            section_id: Unique identifier of the section.
            coordinate: Geometric coordinate string for the support region.
            patch_keys: Patch keys that constrain the support.

        Returns:
            The support record dict.
        """
        result = self.support_bridge.section_to_support(section_id, coordinate, patch_keys)
        self._log("SUPPORT_INTEGRATION", {"section_id": section_id, "support_key": result.get("support_key")})
        return result

    def integrate_with_judgments(
        self,
        section_id: str,
        defined_names: list[str],
        trust_level: str,
    ) -> dict:
        """Translate an exec injection into a judgment record.

        Delegates to JudgmentBridge.exec_to_judgment and logs the event.

        Args:
            section_id: Unique identifier of the section.
            defined_names: Top-level names introduced by the section.
            trust_level: Trust tier for the section.

        Returns:
            The judgment record dict.
        """
        result = self.judgment_bridge.exec_to_judgment(section_id, defined_names, trust_level)
        self._log("JUDGMENT_INTEGRATION", {"section_id": section_id, "judgment_id": result.get("judgment_id")})
        return result

    def integrate_with_channels(
        self,
        section_id: str,
        source_code: str,
        defined_names: list[str],
    ) -> dict:
        """Submit an exec injection as evidence via the channel subsystem.

        Delegates to ChannelBridge.submit_exec_as_evidence and logs the event.

        Args:
            section_id: Unique identifier of the section.
            source_code: Raw Python source code.
            defined_names: Top-level names introduced.

        Returns:
            The submission record dict.
        """
        result = self.channel_bridge.submit_exec_as_evidence(section_id, source_code, defined_names)
        self._log("CHANNEL_INTEGRATION", {"section_id": section_id, "submission_id": result.get("submission_id")})
        return result

    def integrate_with_fleet(
        self,
        event_id: str,
        module_name: str,
        sections: list[str],
    ) -> dict:
        """Register a hot reload request with the fleet orchestration system.

        Delegates to FleetBridge.register_reload_with_fleet and logs the event.

        Args:
            event_id: Identifier of the originating hot reload event.
            module_name: Module being reloaded.
            sections: Section IDs to replace.

        Returns:
            The fleet reload request dict.
        """
        result = self.fleet_bridge.register_reload_with_fleet(event_id, module_name, sections)
        self._log("FLEET_INTEGRATION", {"event_id": event_id, "request_id": result.get("request_id")})
        return result

    def full_integration_check(self) -> dict:
        """Run all bridge export methods and return the combined state.

        Returns:
            Dict with state snapshots from all four bridges.
        """
        return {
            "support_bridge": self.support_bridge.export_bridge_state(),
            "judgment_bridge": self.judgment_bridge.export_bridge_state(),
            "channel_bridge": self.channel_bridge.export_bridge_state(),
            "fleet_bridge": self.fleet_bridge.export_bridge_state(),
            "checked_at": time.time(),
        }

    def export_integration_state(self) -> dict:
        """Export the complete integration state including all bridge states and log.

        Returns:
            Dict with all bridge states, log entries, and metadata.
        """
        return {
            "initialized_at": self._initialized_at,
            "uptime_seconds": round(time.time() - self._initialized_at, 3),
            "log_count": len(self._integration_log),
            "integration_log": list(self._integration_log),
            **self.full_integration_check(),
        }

    def reload_integration(self) -> bool:
        """Reset all bridge logs and integration log.

        Clears the integration log and calls each bridge's internal list
        resets. Returns True to indicate success.

        Returns:
            True unconditionally.
        """
        self._integration_log.clear()
        self.support_bridge._sync_log.clear()
        self.support_bridge._invalidation_queue.clear()
        self.judgment_bridge._trust_deltas.clear()
        self.judgment_bridge._obligation_updates.clear()
        self.channel_bridge._submission_log.clear()
        self.channel_bridge._channel_records.clear()
        self.fleet_bridge._bid_log.clear()
        self.fleet_bridge._distributed_results.clear()
        return True

    def health_check(self) -> dict:
        """Return a health status dict for the integration layer.

        Checks that each bridge is reachable and that the integration log is
        not excessively large. Returns is_healthy=True if all checks pass.

        Returns:
            Dict with is_healthy, bridge_statuses, uptime_seconds, log_count.
        """
        bridge_statuses: dict[str, str] = {}
        all_healthy = True

        for bridge_name, bridge in [
            ("support_bridge", self.support_bridge),
            ("judgment_bridge", self.judgment_bridge),
            ("channel_bridge", self.channel_bridge),
            ("fleet_bridge", self.fleet_bridge),
        ]:
            try:
                bridge.export_bridge_state()
                bridge_statuses[bridge_name] = "OK"
            except Exception as exc:  # noqa: BLE001
                bridge_statuses[bridge_name] = f"ERROR: {exc}"
                all_healthy = False

        log_count = len(self._integration_log)
        if log_count > 10_000:
            all_healthy = False
            bridge_statuses["integration_log"] = f"WARN: log has {log_count} entries"

        return {
            "is_healthy": all_healthy,
            "bridge_statuses": bridge_statuses,
            "uptime_seconds": round(time.time() - self._initialized_at, 3),
            "log_count": log_count,
        }


__all__ = [
    "SupportBridge",
    "JudgmentBridge",
    "ChannelBridge",
    "FleetBridge",
    "LiveMutationIntegration",
]

# copilot: integration layer for live_mutation Ch23
