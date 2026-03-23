"""Protocol sections theory for JuGeo unstable protocols (Ch22 §1).

Protocols as behavioral sections over semantic coordinates, descent,
gluing, and staleness detection.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §1  Protocol sections – behavioral sections over semantic coordinates
      A protocol section :math:`P(U)` assigns to each open set *U* (semantic
      coordinate) a collection of method names promised by the protocol.
      Restriction maps π_V^U : P(U) → P(V) for V ⊆ U correspond to
      :meth:`ProtocolDescentEngine.restrict`.
* §1  Sheaf gluing axiom – if sections agree on overlapping sub-coordinates,
      they can be assembled into a global section via :class:`ProtocolGluer`.
* §1  Staleness – a section is stale when the observed behaviour diverges
      from the declared interface beyond a configurable threshold.

This module is the backbone of the package: every other module depends on
the types and engine defined here.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Local model imports (always available inside the package)
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection,
        StabilityLevel,
        ProxyRecord,
        ProxyRestriction,
        DelegationChain,
        DelegationKind,
        UnstableInterface,
    )
except ImportError:  # pragma: no cover
    class ProtocolSection:  # type: ignore[no-redef]
        pass
    class StabilityLevel:  # type: ignore[no-redef]
        pass
    class ProxyRecord:  # type: ignore[no-redef]
        pass
    class ProxyRestriction:  # type: ignore[no-redef]
        pass
    class DelegationChain:  # type: ignore[no-redef]
        pass
    class DelegationKind:  # type: ignore[no-redef]
        pass
    class UnstableInterface:  # type: ignore[no-redef]
        pass

# ---------------------------------------------------------------------------
# Cross-package stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:  # pragma: no cover
    class SupportRegion:  # type: ignore[no-redef]
        pass
    class SupportSet:  # type: ignore[no-redef]
        pass
    class SupportTracker:  # type: ignore[no-redef]
        pass

try:
    from jugeo.judgments.judgment_terms import LocalJudgment, JudgmentStatus, TrustTier
except ImportError:  # pragma: no cover
    class LocalJudgment:  # type: ignore[no-redef]
        pass
    class JudgmentStatus:  # type: ignore[no-redef]
        pass
    class TrustTier:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRecord, ChannelRouter
except ImportError:  # pragma: no cover
    class EvidenceChannel:  # type: ignore[no-redef]
        pass
    class EvidenceRecord:  # type: ignore[no-redef]
        pass
    class ChannelRouter:  # type: ignore[no-redef]
        pass

try:
    from jugeo.orchestration.fleet import Fleet, FleetBid, FleetMember
except ImportError:  # pragma: no cover
    class Fleet:  # type: ignore[no-redef]
        pass
    class FleetBid:  # type: ignore[no-redef]
        pass
    class FleetMember:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# ProtocolSectionManager
# ---------------------------------------------------------------------------


@dataclass
class ProtocolSectionManager:
    """Manages a registry of protocol sections, providing CRUD, query, and lifecycle operations.

    The manager is the primary entry point for long-running services that
    need to track many protocol sections across multiple semantic coordinates.
    It enforces a :attr:`max_sections` cap to prevent unbounded memory growth,
    and exposes snapshot import/export so the registry state can be persisted
    across process restarts.

    Parameters
    ----------
    sections:
        Mapping from section_id to :class:`ProtocolSection`.
    max_sections:
        Hard cap on the number of simultaneously tracked sections.
    created_at:
        Unix timestamp of manager creation.
    """

    sections: dict[str, ProtocolSection] = field(default_factory=dict)
    max_sections: int = 10_000
    created_at: float = field(default_factory=time.time)

    def register(self, section: ProtocolSection) -> None:
        """Validate and store a protocol section.

        Parameters
        ----------
        section:
            The section to register.

        Raises
        ------
        ValueError
            If the registry is at capacity or the section_id is empty.
        TypeError
            If ``section`` is not a :class:`ProtocolSection` instance.
        """
        if not isinstance(section, ProtocolSection):
            raise TypeError(f"Expected ProtocolSection, got {type(section).__name__}")
        if not section.section_id:
            raise ValueError("section.section_id must not be empty")
        if section.section_id not in self.sections and len(self.sections) >= self.max_sections:
            raise ValueError(
                f"Registry is at capacity ({self.max_sections}). "
                "Purge collapsed sections before adding new ones."
            )
        self.sections[section.section_id] = section

    def unregister(self, section_id: str) -> bool:
        """Remove a section by ID.

        Parameters
        ----------
        section_id:
            The ID of the section to remove.

        Returns
        -------
        bool
            ``True`` if found and removed; ``False`` if not present.
        """
        if section_id in self.sections:
            del self.sections[section_id]
            return True
        return False

    def get(self, section_id: str) -> ProtocolSection | None:
        """Retrieve a section by ID, returning None if not found.

        Parameters
        ----------
        section_id:
            The section to look up.
        """
        return self.sections.get(section_id)

    def list_by_stability(self, level: StabilityLevel) -> list[ProtocolSection]:
        """Return all sections at a given :class:`StabilityLevel`.

        Parameters
        ----------
        level:
            Filter criterion.
        """
        return [s for s in self.sections.values() if s.stability_level == level]

    def list_by_coordinate(self, coordinate: str) -> list[ProtocolSection]:
        """Return all sections at a given semantic coordinate.

        Parameters
        ----------
        coordinate:
            Exact coordinate string to match.
        """
        return [s for s in self.sections.values() if s.coordinate == coordinate]

    def count(self) -> int:
        """Return the total number of registered sections."""
        return len(self.sections)

    def purge_collapsed(self) -> int:
        """Remove all sections in the COLLAPSED stability level.

        Returns
        -------
        int
            Number of sections removed.
        """
        collapsed_ids = [
            sid
            for sid, s in self.sections.items()
            if s.stability_level == StabilityLevel.COLLAPSED
        ]
        for sid in collapsed_ids:
            del self.sections[sid]
        return len(collapsed_ids)

    def bulk_update_stability(
        self, section_ids: list[str], level: StabilityLevel
    ) -> int:
        """Replace the stability level of multiple sections atomically.

        Because :class:`ProtocolSection` is frozen, each update produces a new
        instance via ``dataclasses.replace()``.

        Parameters
        ----------
        section_ids:
            List of section IDs to update.
        level:
            New :class:`StabilityLevel` to apply.

        Returns
        -------
        int
            Number of sections actually updated (those that existed).
        """
        from dataclasses import replace as dc_replace

        updated = 0
        for sid in section_ids:
            section = self.sections.get(sid)
            if section is not None:
                self.sections[sid] = dc_replace(section, stability_level=level)
                updated += 1
        return updated

    def export_snapshot(self) -> dict[str, Any]:
        """Return a fully serialisable snapshot of the registry state.

        Returns
        -------
        dict[str, Any]
            Contains ``created_at``, ``max_sections``, ``count``, and
            ``sections`` (mapping of section_id → to_dict()).
        """
        return {
            "created_at": self.created_at,
            "max_sections": self.max_sections,
            "count": self.count(),
            "sections": {sid: s.to_dict() for sid, s in self.sections.items()},
        }

    def import_snapshot(self, data: dict[str, Any]) -> None:
        """Replace registry contents with data from a previously exported snapshot.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`export_snapshot`.
        """
        self.sections.clear()
        for sid, s_data in data.get("sections", {}).items():
            self.sections[sid] = ProtocolSection.from_dict(s_data)
        self.max_sections = int(data.get("max_sections", self.max_sections))


# ---------------------------------------------------------------------------
# ProtocolDescentEngine
# ---------------------------------------------------------------------------


@dataclass
class ProtocolDescentEngine:
    """Implements protocol descent: restriction of a section to a sub-coordinate.

    In sheaf theory, the restriction map π_V^U projects a section over a
    large open set *U* down to a section over a smaller sub-open set *V ⊆ U*.
    Here the projection discards any declared methods that fall outside the
    ``allowed_methods`` set supplied for the sub-coordinate.

    The engine also logs every descent operation so that the full provenance
    chain from parent to child is always recoverable.

    Parameters
    ----------
    registered_restrictions:
        Mapping from sub-coordinate name to a list of allowed method names.
    descent_log:
        Ordered list of descent event records.
    """

    registered_restrictions: dict[str, list[str]] = field(default_factory=dict)
    descent_log: list[dict[str, Any]] = field(default_factory=list)

    def restrict(
        self,
        section: ProtocolSection,
        sub_coordinate: str,
        allowed_methods: set[str],
    ) -> ProtocolSection:
        """Restrict ``section`` to ``sub_coordinate`` by intersecting with ``allowed_methods``.

        Parameters
        ----------
        section:
            The parent :class:`ProtocolSection` to restrict.
        sub_coordinate:
            The sub-coordinate to restrict to.
        allowed_methods:
            The set of method names permitted in the sub-coordinate.

        Returns
        -------
        ProtocolSection
            A new section whose declared_methods and observed_methods are
            the intersections of the parent's with ``allowed_methods``.
        """
        from dataclasses import replace as dc_replace

        new_declared = tuple(
            m for m in section.declared_methods if m in allowed_methods
        )
        new_observed = tuple(
            m for m in section.observed_methods if m in allowed_methods
        )
        now = time.time()
        child = dc_replace(
            section,
            section_id=str(uuid.uuid4()),
            coordinate=sub_coordinate,
            declared_methods=new_declared,
            observed_methods=new_observed,
            created_at=now,
            last_verified=now,
            provenance=section.provenance + (section.section_id,),
        )
        score = self.compute_restriction_score(section, child)
        self.log_descent(section.section_id, child.section_id, score)
        self.registered_restrictions.setdefault(sub_coordinate, list(allowed_methods))
        return child

    def multi_restrict(
        self,
        section: ProtocolSection,
        sub_coordinates: list[str],
        method_map: dict[str, set[str]],
    ) -> list[ProtocolSection]:
        """Restrict ``section`` to multiple sub-coordinates simultaneously.

        Parameters
        ----------
        section:
            Parent section.
        sub_coordinates:
            List of sub-coordinate names.
        method_map:
            Mapping from sub-coordinate name to allowed method set.

        Returns
        -------
        list[ProtocolSection]
            One restricted child section per entry in ``sub_coordinates``.
        """
        results: list[ProtocolSection] = []
        for coord in sub_coordinates:
            allowed = method_map.get(coord, set())
            results.append(self.restrict(section, coord, allowed))
        return results

    def compute_restriction_score(
        self, parent: ProtocolSection, child: ProtocolSection
    ) -> float:
        """Compute how much information was retained during descent.

        Returns the ratio of child declared methods to parent declared methods.
        A score of 1.0 means nothing was lost; 0.0 means everything was dropped.

        Parameters
        ----------
        parent:
            The parent section before restriction.
        child:
            The child section produced by restriction.
        """
        n_parent = len(parent.declared_methods)
        if n_parent == 0:
            return 1.0
        n_child = len(child.declared_methods)
        return n_child / n_parent

    def validate_restriction(
        self, parent: ProtocolSection, child: ProtocolSection
    ) -> bool:
        """Return True when the child's declared methods are a subset of the parent's.

        Parameters
        ----------
        parent:
            The parent section.
        child:
            The supposedly restricted child.
        """
        parent_set = set(parent.declared_methods)
        child_set = set(child.declared_methods)
        return child_set <= parent_set

    def log_descent(self, parent_id: str, child_id: str, score: float) -> None:
        """Append a descent event record to :attr:`descent_log`.

        Parameters
        ----------
        parent_id:
            Section ID of the parent.
        child_id:
            Section ID of the produced child.
        score:
            Retention score from :meth:`compute_restriction_score`.
        """
        self.descent_log.append(
            {
                "parent_id": parent_id,
                "child_id": child_id,
                "score": score,
                "timestamp": time.time(),
            }
        )

    def descent_history(self, section_id: str) -> list[dict[str, Any]]:
        """Return all descent log entries where ``section_id`` is parent or child.

        Parameters
        ----------
        section_id:
            Section to filter by.
        """
        return [
            e
            for e in self.descent_log
            if e["parent_id"] == section_id or e["child_id"] == section_id
        ]

    def clear_log(self) -> None:
        """Clear the entire descent log."""
        self.descent_log.clear()

    def export_log(self) -> list[dict[str, Any]]:
        """Return a copy of the descent log."""
        return list(self.descent_log)


# ---------------------------------------------------------------------------
# ProtocolGluer
# ---------------------------------------------------------------------------


@dataclass
class ProtocolGluer:
    """Implements the sheaf gluing axiom for protocol sections.

    Given a collection of local sections over a cover of a semantic coordinate,
    the gluer verifies that they agree on method names wherever their
    sub-coordinates overlap, then assembles a single global section whose
    declared methods are the union of all local sections' declared methods.

    Agreement is checked by comparing each pair of sections' *observed* method
    sets restricted to methods that appear in both: if the observed intersection
    differs from the declared intersection, the gluing condition is violated.

    Parameters
    ----------
    gluing_log:
        Ordered list of successful gluing event records.
    conflict_log:
        Ordered list of conflict detection records.
    """

    gluing_log: list[dict[str, Any]] = field(default_factory=list)
    conflict_log: list[dict[str, Any]] = field(default_factory=list)

    def can_glue(self, sections: list[ProtocolSection]) -> bool:
        """Check pairwise overlap consistency for the given sections.

        Two sections are considered consistent if the set of methods they both
        declare is also present in both sections' observed sets.  A section
        with an empty observed set always conflicts with any other section
        that has overlapping declared methods.

        Parameters
        ----------
        sections:
            List of local sections to check.
        """
        conflicts = self.find_conflicts(sections)
        return len(conflicts) == 0

    def glue(
        self,
        sections: list[ProtocolSection],
        global_coordinate: str,
    ) -> ProtocolSection | None:
        """Assemble a global section from local sections if they can be glued.

        The global section's:
        * ``declared_methods`` = union of all locals' declared methods.
        * ``observed_methods`` = union of all locals' observed methods.
        * ``stability_level``  = maximum severity level among locals.
        * ``support_keys``     = union of all locals' support_keys.

        Parameters
        ----------
        sections:
            Local sections forming the cover.
        global_coordinate:
            The semantic coordinate for the assembled global section.

        Returns
        -------
        ProtocolSection | None
            The global section, or ``None`` if gluing is not possible.
        """
        if not sections:
            return None
        if not self.can_glue(sections):
            self.conflict_log.append(
                {
                    "event": "glue_failed",
                    "coordinate": global_coordinate,
                    "section_count": len(sections),
                    "timestamp": time.time(),
                }
            )
            return None

        declared_union: set[str] = set()
        observed_union: set[str] = set()
        support_union: frozenset[str] = frozenset()
        provenance: list[str] = []
        severity_scores: list[float] = []

        for s in sections:
            declared_union.update(s.declared_methods)
            observed_union.update(s.observed_methods)
            support_union = support_union | s.support_keys
            provenance.append(s.section_id)
            severity_scores.append(s.stability_level.severity_score())

        max_severity = max(severity_scores)
        stability = StabilityLevel.from_score(max_severity)
        now = time.time()
        min_created = min(s.created_at for s in sections)
        global_section = ProtocolSection(
            section_id=str(uuid.uuid4()),
            coordinate=global_coordinate,
            declared_methods=tuple(sorted(declared_union)),
            observed_methods=tuple(sorted(observed_union)),
            stability_level=stability,
            support_keys=support_union,
            created_at=min_created,
            last_verified=now,
            provenance=tuple(provenance),
        )
        self.gluing_log.append(
            {
                "global_id": global_section.section_id,
                "coordinate": global_coordinate,
                "component_ids": provenance,
                "score": self.gluing_score(sections),
                "timestamp": now,
            }
        )
        return global_section

    def find_conflicts(
        self, sections: list[ProtocolSection]
    ) -> list[dict[str, Any]]:
        """List all method-level conflicts between pairs of sections.

        A conflict exists when a method appears in both sections' declared sets
        but the observed status disagrees (present in one but not the other).

        Parameters
        ----------
        sections:
            List of local sections to check pairwise.

        Returns
        -------
        list[dict[str, Any]]
            Each entry has ``method``, ``section_a``, ``section_b``,
            ``in_observed_a``, ``in_observed_b``.
        """
        conflicts: list[dict[str, Any]] = []
        for i in range(len(sections)):
            for j in range(i + 1, len(sections)):
                sa, sb = sections[i], sections[j]
                shared_declared = set(sa.declared_methods) & set(sb.declared_methods)
                for method in shared_declared:
                    in_a = method in sa.observed_methods
                    in_b = method in sb.observed_methods
                    if in_a != in_b:
                        conflicts.append(
                            {
                                "method": method,
                                "section_a": sa.section_id,
                                "section_b": sb.section_id,
                                "in_observed_a": in_a,
                                "in_observed_b": in_b,
                                "timestamp": time.time(),
                            }
                        )
        return conflicts

    def resolve_conflict(
        self,
        method: str,
        sections: list[ProtocolSection],
        strategy: str = "majority",
    ) -> str | None:
        """Decide whether a conflicting method should be retained.

        Parameters
        ----------
        method:
            The method name under dispute.
        sections:
            All sections that contain the method.
        strategy:
            ``"majority"`` – retain if more than half the sections observe it.
            ``"any"``      – retain if any section observes it.
            ``"all"``      – retain only if every section observes it.

        Returns
        -------
        str | None
            The method name if it should be retained, else ``None``.
        """
        relevant = [s for s in sections if method in s.declared_methods]
        if not relevant:
            return None
        observed_count = sum(1 for s in relevant if method in s.observed_methods)
        total = len(relevant)
        if strategy == "majority":
            return method if observed_count > total / 2 else None
        if strategy == "any":
            return method if observed_count > 0 else None
        if strategy == "all":
            return method if observed_count == total else None
        return None

    def gluing_score(self, sections: list[ProtocolSection]) -> float:
        """Compute a 0.0–1.0 agreement score for the given sections.

        The score is the fraction of shared declared methods for which all
        sections agree on the observed status (both present or both absent).
        Returns 1.0 for empty or single-section inputs.

        Parameters
        ----------
        sections:
            Sections to evaluate.
        """
        if len(sections) <= 1:
            return 1.0
        all_declared: set[str] = set()
        for s in sections:
            all_declared.update(s.declared_methods)
        if not all_declared:
            return 1.0

        agree_count = 0
        total_checked = 0
        for method in all_declared:
            relevant = [s for s in sections if method in s.declared_methods]
            if len(relevant) < 2:
                continue
            observed_statuses = [method in s.observed_methods for s in relevant]
            # all agree = all True or all False
            if len(set(observed_statuses)) == 1:
                agree_count += 1
            total_checked += 1

        if total_checked == 0:
            return 1.0
        return agree_count / total_checked

    def export_gluing_log(self) -> list[dict[str, Any]]:
        """Return a copy of the gluing event log."""
        return list(self.gluing_log)

    def export_conflict_log(self) -> list[dict[str, Any]]:
        """Return a copy of the conflict detection log."""
        return list(self.conflict_log)

    def clear_logs(self) -> None:
        """Clear both the gluing and conflict logs."""
        self.gluing_log.clear()
        self.conflict_log.clear()


# ---------------------------------------------------------------------------
# StalenessDetector
# ---------------------------------------------------------------------------


@dataclass
class StalenessDetector:
    """Detects stale protocol sections by comparing declared vs observed methods.

    A section is considered stale when either:
    * It has not been verified within :attr:`threshold_seconds` seconds, *or*
    * Its Jaccard-based drift score exceeds :attr:`drift_threshold`.

    The detector also emits structured alert records and can produce a
    remediation suggestion list for each stale section.

    Parameters
    ----------
    threshold_seconds:
        Maximum allowed verification lag before a section is stale.
    drift_threshold:
        Maximum allowed drift score (0.0–1.0) before a section is stale.
    alerts:
        Accumulated alert records.
    checked_count:
        Total number of individual section checks performed.
    """

    threshold_seconds: float = 300.0
    drift_threshold: float = 0.3
    alerts: list[dict[str, Any]] = field(default_factory=list)
    checked_count: int = 0

    def is_stale(self, section: ProtocolSection) -> bool:
        """Return True when the section is stale by any criterion.

        Parameters
        ----------
        section:
            The section to evaluate.
        """
        self.checked_count += 1
        if section.is_stale(self.threshold_seconds):
            return True
        if self.drift_score(section) > self.drift_threshold:
            return True
        return False

    def drift_score(self, section: ProtocolSection) -> float:
        """Compute the Jaccard drift score for ``section``.

        Parameters
        ----------
        section:
            The section to evaluate.
        """
        return section.drift_score()

    def batch_check(
        self, sections: list[ProtocolSection]
    ) -> dict[str, bool]:
        """Check all sections in ``sections`` for staleness.

        Parameters
        ----------
        sections:
            List of sections to check.

        Returns
        -------
        dict[str, bool]
            Mapping from section_id to staleness flag.
        """
        return {s.section_id: self.is_stale(s) for s in sections}

    def rank_by_staleness(
        self, sections: list[ProtocolSection]
    ) -> list[tuple[ProtocolSection, float]]:
        """Rank sections from most to least stale.

        The staleness score is a weighted combination of normalised
        verification lag and drift score:
        ``0.4 * (lag / threshold) + 0.6 * (drift / drift_threshold)``
        clamped to [0, 1].

        Parameters
        ----------
        sections:
            List to rank.
        """
        ranked: list[tuple[ProtocolSection, float]] = []
        for s in sections:
            lag_norm = min(1.0, s.verification_lag() / self.threshold_seconds)
            drift_norm = min(1.0, self.drift_score(s) / max(self.drift_threshold, 1e-9))
            score = 0.4 * lag_norm + 0.6 * drift_norm
            ranked.append((s, score))
        ranked.sort(key=lambda t: t[1], reverse=True)
        return ranked

    def emit_alert(self, section: ProtocolSection, reason: str) -> dict[str, Any]:
        """Create and store an alert record for a stale section.

        Parameters
        ----------
        section:
            The stale section.
        reason:
            Short description of the staleness cause.

        Returns
        -------
        dict[str, Any]
            The alert record.
        """
        alert = {
            "alert_id": str(uuid.uuid4()),
            "section_id": section.section_id,
            "coordinate": section.coordinate,
            "stability_level": section.stability_level.value,
            "drift_score": self.drift_score(section),
            "verification_lag": section.verification_lag(),
            "reason": reason,
            "timestamp": time.time(),
        }
        self.alerts.append(alert)
        return alert

    def clear_alerts(self) -> None:
        """Remove all accumulated alerts."""
        self.alerts.clear()

    def alert_count(self) -> int:
        """Return the number of accumulated alerts."""
        return len(self.alerts)

    def staleness_report(
        self, sections: list[ProtocolSection]
    ) -> dict[str, Any]:
        """Generate a structured staleness report for a collection of sections.

        Parameters
        ----------
        sections:
            Sections to analyse.

        Returns
        -------
        dict[str, Any]
            Contains ``total``, ``stale_count``, ``stale_ids``,
            ``mean_drift``, ``max_drift``, ``mean_lag``, ``max_lag``.
        """
        if not sections:
            return {
                "total": 0,
                "stale_count": 0,
                "stale_ids": [],
                "mean_drift": 0.0,
                "max_drift": 0.0,
                "mean_lag": 0.0,
                "max_lag": 0.0,
            }
        stale_ids = [s.section_id for s in sections if self.is_stale(s)]
        drifts = [self.drift_score(s) for s in sections]
        lags = [s.verification_lag() for s in sections]
        return {
            "total": len(sections),
            "stale_count": len(stale_ids),
            "stale_ids": stale_ids,
            "mean_drift": sum(drifts) / len(drifts),
            "max_drift": max(drifts),
            "mean_lag": sum(lags) / len(lags),
            "max_lag": max(lags),
        }

    def suggest_remediation(self, section: ProtocolSection) -> list[str]:
        """Return a prioritised list of remediation suggestions for a stale section.

        Parameters
        ----------
        section:
            The stale section to diagnose.
        """
        suggestions: list[str] = []
        lag = section.verification_lag()
        drift = self.drift_score(section)
        missing = section.missing_methods()
        excess = section.excess_methods()

        if lag > self.threshold_seconds * 2:
            suggestions.append(
                f"Re-verify section immediately: last verified {lag:.0f}s ago "
                f"(threshold={self.threshold_seconds:.0f}s)."
            )
        elif lag > self.threshold_seconds:
            suggestions.append(
                f"Schedule re-verification soon: last verified {lag:.0f}s ago."
            )

        if drift > self.drift_threshold:
            suggestions.append(
                f"Drift score {drift:.3f} exceeds threshold {self.drift_threshold:.3f}. "
                "Reconcile declared and observed method sets."
            )

        if missing:
            suggestions.append(
                f"Implement or expose missing methods: {sorted(missing)}"
            )

        if excess:
            suggestions.append(
                f"Remove or document excess observed methods: {sorted(excess)}"
            )

        if section.stability_level in (
            StabilityLevel.RETRACTING,
            StabilityLevel.COLLAPSED,
        ):
            suggestions.append(
                f"Section is in {section.stability_level.value} state. "
                "Consider initiating a graceful shutdown or rollback."
            )

        if not suggestions:
            suggestions.append("No immediate remediation required.")
        return suggestions


# ---------------------------------------------------------------------------

__all__ = [
    "ProtocolSectionManager",
    "ProtocolDescentEngine",
    "ProtocolGluer",
    "StalenessDetector",
]

# copilot: protocol_sections.py – manager, descent engine, gluer, and staleness detector for protocol sections (Ch22 §1)
