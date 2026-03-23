"""Diagnostic projections for JuGeo interfaces.

This module exposes the internal semantic state of a JuGeo instance to
operators and external tooling.  It faithfully reports:

* **Verified** claims -- with the evidence trail, trust level, and support
  scope that justify the claim according to ``theory2.tex``.
* **Residual** obligations -- open proof obligations with priority, deadline,
  and the blocking reason preventing discharge.
* **Failed / Obstructed** items -- persistent cohomology-class obstructions
  with the violated condition and repair hints.
* **Trust distribution** -- the breakdown of claims by trust level, channel,
  and coordinate region over time.

Copilot-backed evidence channels are exposed at ``COPILOT_SUGGESTED``
(oracle-tier visibility, solver-tier promotion barrier).  The module never
silently promotes copilot evidence -- every copilot contribution is surfaced
in the channel summary with its explicit trust ceiling annotation.

See ``theory2.tex`` S252 (trust algebra) and S354 (semantic state projections).
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

from jugeo.evidence.channels import (
    ChannelMonitor,
    CopilotChannel,
    EvidenceChannel,
)
from jugeo.evidence.manifests import (
    ManifestStatistics,
    ObligationPriority,
    ObstructionKind,
)
from jugeo.evidence.trust import TrustLevel
from jugeo.generation.backpressure import BackpressureSignal
from jugeo.kernel.health import HealthIndicator, HealthSnapshot
from jugeo.orchestration.frontier import FrontierState

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now() -> float:
    """Return the current UTC timestamp as a UNIX float."""
    return time.time()


def _fresh_id(prefix: str = "diag") -> str:
    """Generate a short deterministic-prefix UUID."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _safe_mean(values: Sequence[float]) -> float:
    """Return the mean of *values*, or 0.0 if the sequence is empty."""
    if not values:
        return 0.0
    return statistics.mean(values)


def _safe_stdev(values: Sequence[float]) -> float:
    """Return the population standard deviation, or 0.0 for < 2 samples."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


# ---------------------------------------------------------------------------
# DiagnosticLevel  (preserved for backward compatibility)
# ---------------------------------------------------------------------------


class DiagnosticLevel(str, Enum):
    """Coarse severity classification for a diagnostic message.

    Values correspond to logging levels familiar to operators:
    ``INFO`` for routine observations, ``WARNING`` for soft anomalies that do
    not yet require intervention, and ``ERROR`` for hard failures that need
    immediate attention.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ---------------------------------------------------------------------------
# DiagnosticMessage  (preserved for backward compatibility)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiagnosticMessage:
    """A single human-readable diagnostic entry with a severity level.

    Instances are immutable and hashable so that they can be stored in frozen
    containers (e.g., the ``DiagnosticReport.messages`` tuple).

    Attributes:
        level: Severity of this message (INFO / WARNING / ERROR).
        message: Human-readable description of the observation.
    """

    level: DiagnosticLevel
    message: str


# ===========================================================================
# 1.  DiagnosticReport
# ===========================================================================


@dataclass
class DiagnosticReport:
    """Snapshot of the full semantic state of a JuGeo instance.

    ``DiagnosticReport`` is the *root* projection object produced by
    :class:`DiagnosticsEngine`.  Every field is populated from the live
    manifest and kernel health monitors at report-generation time.

    The report captures what IS verified (with evidence), what is RESIDUAL
    (with open obligations), and what has FAILED (with obstruction classes).
    Trust distribution is broken down per channel so that copilot-backed
    channels remain visible at their oracle-tier trust ceiling rather than
    being silently folded into a solver-tier aggregate.

    Attributes:
        report_id: Stable UUID for this snapshot (``diag-<12 hex chars>``).
        timestamp: UNIX UTC float at generation time.
        scope: Textual description of the coordinate region covered.
        verified_count: Number of claims with at least one discharged
            obligation and no active obstructions.
        residual_count: Number of open residual obligations.
        obstruction_count: Number of active (unresolved) obstructions.
        trust_distribution: Mapping from TrustLevel name to integer count
            of claims at that trust tier.
        evidence_channel_summary: Mapping from channel name to a dict with
            keys ``queries``, ``successes``, ``failures``,
            ``latency_ms_mean``, ``trust_ceiling``.
        health_indicators: Tuple of HealthIndicator instances from all
            active health checks.
        messages: Legacy tuple of DiagnosticMessage entries for backward
            compatibility with callers that consumed the original
            ``collect_diagnostics`` return value.
        frontier_summary: Free-text summary of the current orchestration
            frontier, including phase and item counts.
        backpressure_level: Integer backpressure level (0 = none,
            4 = critical).
        generation_duration_ms: Wall-clock time in milliseconds spent
            generating this report.
    """

    report_id: str = field(default_factory=lambda: _fresh_id("diag"))
    timestamp: float = field(default_factory=_utc_now)
    scope: str = "global"
    verified_count: int = 0
    residual_count: int = 0
    obstruction_count: int = 0
    trust_distribution: dict[str, int] = field(default_factory=dict)
    evidence_channel_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    health_indicators: tuple[HealthIndicator, ...] = field(default_factory=tuple)
    messages: tuple[DiagnosticMessage, ...] = field(default_factory=tuple)
    frontier_summary: str = ""
    backpressure_level: int = 0
    generation_duration_ms: float = 0.0

    def __post_init__(self) -> None:
        """Preserve the legacy ``DiagnosticReport((msg1, msg2, ...))`` constructor.

        Older callers passed the message tuple positionally before this dataclass
        grew additional explicit fields. Keep that surface working by
        reinterpret­ing a non-string first positional value as ``messages``.
        """

        if isinstance(self.report_id, str):
            return

        if self.messages:
            raise TypeError("legacy DiagnosticReport positional messages cannot be combined with explicit messages")

        raw_messages = self.report_id
        if isinstance(raw_messages, DiagnosticMessage):
            message_items = (raw_messages,)
        elif isinstance(raw_messages, Sequence) and not isinstance(raw_messages, (str, bytes, bytearray)):
            message_items = tuple(self._coerce_message(item) for item in raw_messages)
        else:
            raise TypeError("DiagnosticReport positional argument must be a DiagnosticMessage or a sequence of them")

        self.report_id = _fresh_id("diag")
        self.messages = message_items

    @staticmethod
    def _coerce_message(value: Any) -> DiagnosticMessage:
        if isinstance(value, DiagnosticMessage):
            return value
        if isinstance(value, Mapping):
            return DiagnosticMessage(
                level=DiagnosticLevel(str(value.get("level", DiagnosticLevel.INFO.value))),
                message=str(value.get("message", "")),
            )
        raise TypeError("DiagnosticReport messages must be DiagnosticMessage instances or mappings")

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def total_claims(self) -> int:
        """Return the sum of verified, residual, and obstructed claims."""
        return self.verified_count + self.residual_count + self.obstruction_count

    @property
    def health_status(self) -> str:
        """Return the worst health status across all health indicators.

        Returns ``'unknown'`` when no indicators are present.
        """
        if not self.health_indicators:
            return "unknown"
        rank: dict[str, int] = {
            "healthy": 0, "recovering": 1, "degraded": 2,
            "unhealthy": 3, "unknown": 4,
        }
        worst = max(
            self.health_indicators,
            key=lambda h: rank.get(str(h.status.value), 4),
        )
        return str(worst.status.value)

    @property
    def top_trust_level(self) -> str:
        """Return the name of the highest trust level with at least one claim."""
        order = [
            "mechanically_verified", "solver_discharged", "runtime_witnessed",
            "human_attested", "oracle_proposed", "copilot_suggested",
            "unverified", "contradicted",
        ]
        for level in order:
            if self.trust_distribution.get(level, 0) > 0:
                return level
        return "none"

    @property
    def has_critical_obstructions(self) -> bool:
        """Return ``True`` if any active obstruction is recorded."""
        return self.obstruction_count > 0

    @property
    def copilot_channel_active(self) -> bool:
        """Return ``True`` if the copilot evidence channel contributed queries."""
        info = self.evidence_channel_summary.get(EvidenceChannel.COPILOT.value, {})
        return int(info.get("queries", 0)) > 0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation of this report.

        Health indicators are serialised as plain dicts; enum values are
        coerced to their string representations.
        """
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "scope": self.scope,
            "verified_count": self.verified_count,
            "residual_count": self.residual_count,
            "obstruction_count": self.obstruction_count,
            "trust_distribution": dict(self.trust_distribution),
            "evidence_channel_summary": {
                k: dict(v) for k, v in self.evidence_channel_summary.items()
            },
            "health_indicators": [
                {
                    "dimension": str(hi.dimension.value),
                    "status": str(hi.status.value),
                    "message": hi.message,
                    "value": hi.value,
                    "timestamp": hi.timestamp,
                }
                for hi in self.health_indicators
            ],
            "messages": [
                {"level": m.level.value, "message": m.message}
                for m in self.messages
            ],
            "frontier_summary": self.frontier_summary,
            "backpressure_level": self.backpressure_level,
            "generation_duration_ms": self.generation_duration_ms,
        }


# ===========================================================================
# 2.  DiagnosticsEngine
# ===========================================================================


class DiagnosticsEngine:
    """Central engine for generating and exporting JuGeo diagnostic reports.

    ``DiagnosticsEngine`` aggregates health snapshots, frontier state,
    backpressure signals, and manifest statistics into coherent
    :class:`DiagnosticReport` objects.  Supports quick summaries (single-line),
    full reports (all fields), and delta reports (diff between two snapshots).

    Copilot-backed channels are **always** surfaced at their declared trust
    ceiling (``COPILOT_SUGGESTED``) rather than being promoted.  The engine
    never suppresses copilot evidence from the channel summary -- doing so
    would violate the *no silent promotion* invariant in ``theory2.tex`` S252.

    Args:
        manifest_stats: Current statistics snapshot from the live manifest.
        health_snapshots: Tuple of HealthSnapshot objects from all registered
            health checks.
        frontier_state: Current orchestration frontier state.
        backpressure_signal: Current backpressure signal from the integration
            pipeline.
        channel_monitors: Mapping from channel name to ChannelMonitor instances.
        copilot_channel: Optional live CopilotChannel; if present its stats
            are included in the channel summary.
        scope: Textual identifier for the coordinate region this engine covers.
    """

    def __init__(
        self,
        manifest_stats: ManifestStatistics,
        health_snapshots: tuple[HealthSnapshot, ...],
        frontier_state: FrontierState,
        backpressure_signal: BackpressureSignal,
        *,
        channel_monitors: Mapping[str, ChannelMonitor] | None = None,
        copilot_channel: CopilotChannel | None = None,
        scope: str = "global",
    ) -> None:
        self._stats = manifest_stats
        self._health = health_snapshots
        self._frontier = frontier_state
        self._signal = backpressure_signal
        self._monitors: Mapping[str, ChannelMonitor] = channel_monitors or {}
        self._copilot_channel = copilot_channel
        self._scope = scope
        self._history: list[DiagnosticReport] = []

    # ------------------------------------------------------------------
    # Core report generation
    # ------------------------------------------------------------------

    def generate_report(self) -> DiagnosticReport:
        """Generate a complete DiagnosticReport from current state.

        Pulls all subsystem diagnostics, computes trust distribution, channel
        summaries, and health indicators, then assembles a single snapshot.

        Returns:
            A fully populated DiagnosticReport instance.
        """
        t0 = _utc_now()
        messages = self._collect_messages()
        trust_dist = self._compute_trust_distribution()
        channel_summary = self._compute_channel_summary()
        health_indicators = self._collect_health_indicators()
        frontier_summary = self._compute_frontier_summary()
        bp_level = int(self._signal.level) if hasattr(self._signal, "level") else 0
        verified = max(0, self._stats.judgment_count - self._stats.pending_obligations)
        report = DiagnosticReport(
            scope=self._scope,
            verified_count=verified,
            residual_count=self._stats.pending_obligations,
            obstruction_count=self._stats.active_obstructions,
            trust_distribution=trust_dist,
            evidence_channel_summary=channel_summary,
            health_indicators=tuple(health_indicators),
            messages=tuple(messages),
            frontier_summary=frontier_summary,
            backpressure_level=bp_level,
            generation_duration_ms=(_utc_now() - t0) * 1000.0,
        )
        self._history.append(report)
        return report

    def quick_summary(self) -> str:
        """Return a single-line human-readable summary of the current state.

        Suitable for CLI status lines, log messages, and monitoring dashboards.
        Includes scope, verified / residual / obstruction counts, and worst
        health status.

        Returns:
            A single-line string.
        """
        r = self.generate_report()
        bp = f" | bp={r.backpressure_level}" if r.backpressure_level > 0 else ""
        return (
            f"[{r.scope}] v{r.verified_count} verified"
            f" | !{r.residual_count} residual"
            f" | x{r.obstruction_count} obstruction"
            f" | health: {r.health_status}{bp}"
        )

    def full_report(self) -> DiagnosticReport:
        """Generate and return the full diagnostic report.

        Alias for :meth:`generate_report` that makes intent explicit when
        the caller distinguishes between quick and full mode.

        Returns:
            A fully populated DiagnosticReport.
        """
        return self.generate_report()

    def delta_report(self, previous: DiagnosticReport) -> dict[str, Any]:
        """Compute the delta between *previous* and the current state.

        Highlights changes in verified count, residual count, obstruction
        count, trust distribution, and health status since *previous*.

        Args:
            previous: An older DiagnosticReport to compare against.

        Returns:
            Dict with keys verified_delta, residual_delta, obstruction_delta,
            trust_delta, health_changed, elapsed_seconds, scope_match,
            previous_report_id, current_report_id.
        """
        current = self.generate_report()
        trust_delta: dict[str, int] = {}
        for lvl in set(current.trust_distribution) | set(previous.trust_distribution):
            delta = (
                current.trust_distribution.get(lvl, 0)
                - previous.trust_distribution.get(lvl, 0)
            )
            if delta != 0:
                trust_delta[lvl] = delta
        return {
            "verified_delta": current.verified_count - previous.verified_count,
            "residual_delta": current.residual_count - previous.residual_count,
            "obstruction_delta": current.obstruction_count - previous.obstruction_count,
            "trust_delta": trust_delta,
            "health_changed": current.health_status != previous.health_status,
            "elapsed_seconds": current.timestamp - previous.timestamp,
            "scope_match": current.scope == previous.scope,
            "previous_report_id": previous.report_id,
            "current_report_id": current.report_id,
        }

    def export_json(self, *, indent: int = 2) -> str:
        """Export the current diagnostic report as a JSON string.

        Args:
            indent: JSON indentation level (default 2).

        Returns:
            A JSON string representation of the DiagnosticReport.
        """
        return DiagnosticSerializer().to_json(self.generate_report(), indent=indent)

    def export_text(self) -> str:
        """Export the current diagnostic report as plain human-readable text.

        The text format is suitable for terminal output or log files.

        Returns:
            A multi-line string with section headers and indented details.
        """
        return DiagnosticExporter(self.generate_report()).to_text()

    def copilot_explain_report(self) -> str:
        """Return a copilot-oriented narrative explanation of the current report.

        Produces a structured explanation intended for consumption by a large
        language model or a human operator reviewing copilot-assisted work.
        It explicitly calls out:

        * Which claims were backed by copilot evidence (at COPILOT_SUGGESTED
          trust, never silently promoted).
        * Which residual obligations remain open and why.
        * Which obstructions carry non-trivial cohomology classes.
        * Recommended next steps for the operator or the copilot session.

        Returns:
            A multi-paragraph explanation string.
        """
        report = self.generate_report()
        lines: list[str] = [
            f"=== JuGeo Diagnostic Explanation (scope: {report.scope}) ===",
            "",
            f"Report generated at timestamp {report.timestamp:.3f}.",
            f"Total claims tracked: {report.total_claims}.",
            "",
            "--- VERIFIED ---",
            f"  {report.verified_count} claim(s) have been discharged with evidence.",
            f"  Top trust level in use: {report.top_trust_level}.",
        ]
        copilot_count = report.trust_distribution.get("copilot_suggested", 0)
        if copilot_count > 0:
            lines += [
                "",
                f"  WARNING: Copilot evidence channel contributed {copilot_count} claim(s).",
                "  These claims carry the COPILOT_SUGGESTED trust ceiling (oracle-tier",
                "  visibility, solver-tier promotion barrier).  They appear in the",
                "  evidence channel summary under the 'copilot' key.  Promotion to a",
                "  higher trust tier requires explicit human or solver corroboration.",
            ]
        lines += ["", "--- RESIDUAL OBLIGATIONS ---"]
        if report.residual_count == 0:
            lines.append("  No open residual obligations.")
        else:
            lines += [
                f"  {report.residual_count} obligation(s) remain open.",
                "  Each open obligation represents a proof requirement not yet",
                "  discharged by any evidence channel.  Consult the ResidualView",
                "  for per-obligation priority and deadline information.",
            ]
        lines += ["", "--- OBSTRUCTIONS ---"]
        if report.obstruction_count == 0:
            lines.append("  No active obstructions.")
        else:
            lines += [
                f"  {report.obstruction_count} obstruction(s) are active.",
                "  Each obstruction is a non-trivial cohomology class that prevents",
                "  global section descent.  Consult the ObstructionView for violated",
                "  conditions and repair hints.",
            ]
        lines += [
            "",
            "--- HEALTH ---",
            f"  Overall health status: {report.health_status}.",
            f"  Backpressure level: {report.backpressure_level} / 4.",
            "",
            "--- RECOMMENDED NEXT STEPS ---",
        ]
        steps: list[str] = []
        if report.obstruction_count > 0:
            steps.append("Resolve active obstructions before advancing the frontier.")
        if report.residual_count > 0:
            steps.append("Discharge high-priority residual obligations via solver or runtime.")
        if copilot_count > 0:
            steps.append("Corroborate copilot-suggested claims with solver or runtime evidence.")
        if steps:
            for i, s in enumerate(steps, 1):
                lines.append(f"  {i}. {s}")
        else:
            lines.append("  No action required.")
        return "\n".join(lines)

    def history(self) -> tuple[DiagnosticReport, ...]:
        """Return all reports generated by this engine in chronological order."""
        return tuple(self._history)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_messages(self) -> list[DiagnosticMessage]:
        msgs: list[DiagnosticMessage] = [
            DiagnosticMessage(
                DiagnosticLevel.INFO, f"health snapshots: {len(self._health)}"
            ),
        ]
        if self._frontier.items:
            msgs.append(DiagnosticMessage(
                DiagnosticLevel.INFO,
                f"frontier items: {len(self._frontier.items)}",
            ))
        if self._signal.reasons:
            msgs.append(DiagnosticMessage(
                DiagnosticLevel.WARNING, ", ".join(self._signal.reasons)
            ))
        if self._stats.active_obstructions > 0:
            msgs.append(DiagnosticMessage(
                DiagnosticLevel.ERROR,
                f"{self._stats.active_obstructions} active obstruction(s) prevent descent",
            ))
        return msgs

    def _compute_trust_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {lvl.value: 0 for lvl in TrustLevel}
        raw = self._stats.trust_distribution
        if isinstance(raw, dict):
            for key, val in raw.items():
                k = key.value if isinstance(key, Enum) else str(key)
                dist[k] = int(val)
        return dist

    def _compute_channel_summary(self) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for ch_name, monitor in self._monitors.items():
            ch_summary = monitor.summary() if hasattr(monitor, "summary") else {}
            summary[str(ch_name)] = {
                "queries": ch_summary.get("total_requests", 0),
                "successes": ch_summary.get("total_successes", 0),
                "failures": ch_summary.get("total_failures", 0),
                "latency_ms_mean": ch_summary.get("mean_latency_ms", 0.0),
                "trust_ceiling": ch_summary.get("trust_ceiling", "unknown"),
            }
        if self._copilot_channel is not None:
            ceiling = self._copilot_channel.TRUST_CEILING
            ceiling_str = ceiling.value if isinstance(ceiling, Enum) else str(ceiling)
            ch_key = EvidenceChannel.COPILOT.value
            if ch_key not in summary:
                summary[ch_key] = {
                    "queries": 0,
                    "successes": 0,
                    "failures": 0,
                    "latency_ms_mean": 0.0,
                    "trust_ceiling": ceiling_str,
                }
            else:
                summary[ch_key]["trust_ceiling"] = ceiling_str
        return summary

    def _collect_health_indicators(self) -> list[HealthIndicator]:
        indicators: list[HealthIndicator] = []
        for snap in self._health:
            details = getattr(snap, "details", None)
            if isinstance(details, (list, tuple)):
                for item in details:
                    if isinstance(item, HealthIndicator):
                        indicators.append(item)
        return indicators

    def _compute_frontier_summary(self) -> str:
        items = getattr(self._frontier, "items", None)
        if items is not None:
            return f"frontier items: {len(items)}"
        return "frontier: unknown"


# ===========================================================================
# 3.  VerificationStatusView
# ===========================================================================


@dataclass
class VerifiedItem:
    """A single verified claim with its evidence trail.

    Attributes:
        claim_id: Unique identifier for this claim.
        description: Human-readable description of what was verified.
        trust_level: The trust level at which the claim was discharged.
        support_scope: Tuple of coordinate path segments forming the
            evidence support.
        evidence_channels: Tuple of channel names that contributed evidence.
        discharge_timestamp: UNIX UTC float when the obligation was
            discharged.
        is_copilot_backed: Whether any copilot evidence channel contributed.
    """

    claim_id: str
    description: str
    trust_level: str
    support_scope: tuple[str, ...]
    evidence_channels: tuple[str, ...]
    discharge_timestamp: float
    is_copilot_backed: bool = False


class VerificationStatusView:
    """View of all verified claims in the current JuGeo manifest.

    Provides iteration, filtering, and summary methods over the set of
    claims that have been positively discharged (i.e., at least one
    obligation was met and no active obstructions remain).

    Copilot-backed claims are explicitly tagged and never surfaced at a
    trust level above their declared ceiling.

    See ``theory2.tex`` S354 for the definition of a *settled* judgment.

    Args:
        items: Sequence of VerifiedItem objects.
    """

    def __init__(self, items: Sequence[VerifiedItem] = ()) -> None:
        self._items: tuple[VerifiedItem, ...] = tuple(items)

    def all_items(self) -> tuple[VerifiedItem, ...]:
        """Return all verified items in insertion order."""
        return self._items

    def count(self) -> int:
        """Return the total number of verified items."""
        return len(self._items)

    def by_trust_level(self, level: str) -> tuple[VerifiedItem, ...]:
        """Return all items at the given trust level string.

        Args:
            level: Trust level string, e.g. ``'solver_discharged'``.
        """
        return tuple(it for it in self._items if it.trust_level == level)

    def copilot_backed_items(self) -> tuple[VerifiedItem, ...]:
        """Return items verified with at least one copilot-channel contribution.

        These items are always at the COPILOT_SUGGESTED trust ceiling or below.
        The caller must not assume solver-tier trust without further
        corroboration.
        """
        return tuple(it for it in self._items if it.is_copilot_backed)

    def by_channel(self, channel: str) -> tuple[VerifiedItem, ...]:
        """Return all items that involved a specific evidence channel.

        Args:
            channel: Channel name, e.g. ``'solver'`` or ``'copilot'``.
        """
        return tuple(it for it in self._items if channel in it.evidence_channels)

    def trust_distribution(self) -> dict[str, int]:
        """Return a mapping from trust level name to item count."""
        dist: dict[str, int] = defaultdict(int)
        for it in self._items:
            dist[it.trust_level] += 1
        return dict(dist)

    def support_scope_union(self) -> frozenset[str]:
        """Return the union of all support scope components across items."""
        result: set[str] = set()
        for it in self._items:
            result.update(it.support_scope)
        return frozenset(result)

    def most_recent(self, n: int = 10) -> tuple[VerifiedItem, ...]:
        """Return the *n* most recently discharged items.

        Args:
            n: Maximum number of items to return.

        Returns:
            Tuple of at most *n* items, most recent first.
        """
        return tuple(
            sorted(self._items, key=lambda it: it.discharge_timestamp, reverse=True)[:n]
        )

    def iter_items(self) -> Iterator[VerifiedItem]:
        """Yield verified items in insertion order."""
        yield from self._items

    def summary(self) -> str:
        """Return a short human-readable summary of the verification view.

        Returns:
            String like ``"42 verified (solver: 30, copilot: 5, other: 7)"``.
        """
        dist = self.trust_distribution()
        solver_n = dist.get("solver_discharged", 0)
        copilot_n = dist.get("copilot_suggested", 0)
        other_n = self.count() - solver_n - copilot_n
        return (
            f"{self.count()} verified"
            f" (solver: {solver_n}, copilot: {copilot_n}, other: {other_n})"
        )


# ===========================================================================
# 4.  ResidualView
# ===========================================================================


@dataclass
class ResidualEntry:
    """A single open residual obligation with metadata.

    Attributes:
        obligation_id: Unique identifier from the obligation store.
        description: Human-readable description of the proof obligation.
        priority: Priority level (1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL).
        deadline: Optional UNIX UTC float deadline for discharge.
        blocking_reason: Human-readable reason the obligation is not yet
            discharged (e.g., missing channel, budget exhausted).
        coordinate_path: Dot-separated coordinate path for the claim.
        depends_on: Tuple of obligation IDs this entry depends on.
        age_seconds: Seconds since this obligation was created.
    """

    obligation_id: str
    description: str
    priority: int
    deadline: float | None
    blocking_reason: str
    coordinate_path: str
    depends_on: tuple[str, ...]
    age_seconds: float = 0.0


class ResidualView:
    """View of all open residual obligations in the current JuGeo manifest.

    Exposes obligations that have not yet been discharged, together with
    their priority, deadline, blocking reason, and dependency graph.

    See ``theory2.tex`` for the residual obligation presheaf O.

    Args:
        entries: Sequence of ResidualEntry objects.
    """

    def __init__(self, entries: Sequence[ResidualEntry] = ()) -> None:
        self._entries: tuple[ResidualEntry, ...] = tuple(entries)

    def all_entries(self) -> tuple[ResidualEntry, ...]:
        """Return all residual obligation entries."""
        return self._entries

    def count(self) -> int:
        """Return the total number of open obligations."""
        return len(self._entries)

    def by_priority(self, priority: int) -> tuple[ResidualEntry, ...]:
        """Return all entries with the given priority level.

        Args:
            priority: Integer priority (1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL).
        """
        return tuple(e for e in self._entries if e.priority == priority)

    def critical(self) -> tuple[ResidualEntry, ...]:
        """Return all CRITICAL-priority obligations (priority == 4)."""
        return self.by_priority(ObligationPriority.CRITICAL)

    def overdue(self, now: float | None = None) -> tuple[ResidualEntry, ...]:
        """Return obligations whose deadline has passed.

        Args:
            now: Reference UNIX UTC timestamp; defaults to current time.
        """
        ref = now if now is not None else _utc_now()
        return tuple(
            e for e in self._entries if e.deadline is not None and e.deadline < ref
        )

    def blocking_reasons_summary(self) -> dict[str, int]:
        """Return a mapping from blocking reason text to occurrence count."""
        counts: dict[str, int] = defaultdict(int)
        for e in self._entries:
            counts[e.blocking_reason] += 1
        return dict(counts)

    def dependency_roots(self) -> tuple[ResidualEntry, ...]:
        """Return obligations that no other obligation depends on.

        These are the leaves of the dependency DAG -- the obligations that
        can be attempted first without needing to unblock others.
        """
        all_ids = {e.obligation_id for e in self._entries}
        depended_on = {
            dep for e in self._entries for dep in e.depends_on if dep in all_ids
        }
        return tuple(e for e in self._entries if e.obligation_id not in depended_on)

    def sorted_by_priority(self) -> tuple[ResidualEntry, ...]:
        """Return entries sorted by priority descending (CRITICAL first)."""
        return tuple(sorted(self._entries, key=lambda e: e.priority, reverse=True))

    def iter_entries(self) -> Iterator[ResidualEntry]:
        """Yield all residual entries in insertion order."""
        yield from self._entries

    def summary(self) -> str:
        """Return a short human-readable summary of open obligations.

        Returns:
            String like ``"7 residual (3 critical, 2 high, 2 overdue)"``.
        """
        critical_n = len(self.by_priority(4))
        high_n = len(self.by_priority(3))
        medium_n = len(self.by_priority(2))
        overdue_n = len(self.overdue())
        parts: list[str] = []
        if critical_n:
            parts.append(f"{critical_n} critical")
        if high_n:
            parts.append(f"{high_n} high")
        if medium_n:
            parts.append(f"{medium_n} medium")
        if overdue_n:
            parts.append(f"{overdue_n} overdue")
        detail = ", ".join(parts) if parts else "none critical"
        return f"{self.count()} residual ({detail})"


# ===========================================================================
# 5.  ObstructionView
# ===========================================================================


@dataclass
class ObstructionEntry:
    """A single active obstruction with cohomology metadata.

    Attributes:
        obstruction_id: Unique identifier from the obstruction store.
        coordinate_path: Dot-separated coordinate path at which the
            obstruction was detected.
        violated_condition: Human-readable description of the violated
            condition (e.g., "Cech cocycle d-sigma != 0 at U_i n U_j").
        cohomology_class: String representation of the H1 class.
        kind: ObstructionKind value string.
        repair_hints: Tuple of actionable repair suggestions.
        detected_timestamp: UNIX UTC float when first detected.
        is_transient: Whether the obstruction may resolve on retry.
    """

    obstruction_id: str
    coordinate_path: str
    violated_condition: str
    cohomology_class: str
    kind: str
    repair_hints: tuple[str, ...]
    detected_timestamp: float
    is_transient: bool = False


class ObstructionView:
    """View of all active (unresolved) obstructions in the current manifest.

    Obstructions represent persistent cohomology classes (H1 elements) that
    block global section descent.  They are never silently erased -- this
    view exposes the full record including the violated condition, cohomology
    class, and structured repair hints.

    See ``theory2.tex`` for the first-class treatment of descent obstructions.

    Args:
        entries: Sequence of ObstructionEntry objects.
    """

    def __init__(self, entries: Sequence[ObstructionEntry] = ()) -> None:
        self._entries: tuple[ObstructionEntry, ...] = tuple(entries)

    def all_entries(self) -> tuple[ObstructionEntry, ...]:
        """Return all obstruction entries."""
        return self._entries

    def count(self) -> int:
        """Return the number of active obstructions."""
        return len(self._entries)

    def by_kind(self, kind: str) -> tuple[ObstructionEntry, ...]:
        """Return obstructions of a specific kind.

        Args:
            kind: Kind string, e.g. ``'descent_obstruction'``.
        """
        return tuple(e for e in self._entries if e.kind == kind)

    def transient(self) -> tuple[ObstructionEntry, ...]:
        """Return obstructions that may resolve on retry."""
        return tuple(e for e in self._entries if e.is_transient)

    def persistent(self) -> tuple[ObstructionEntry, ...]:
        """Return definitively non-transient obstructions.

        These require explicit repair action (e.g., adding missing evidence,
        correcting a cover, or reclassifying a claim).
        """
        return tuple(e for e in self._entries if not e.is_transient)

    def coordinates(self) -> tuple[str, ...]:
        """Return the distinct coordinate paths of all active obstructions."""
        seen: dict[str, None] = {}
        for e in self._entries:
            seen[e.coordinate_path] = None
        return tuple(seen)

    def cohomology_classes(self) -> tuple[str, ...]:
        """Return all distinct non-trivial cohomology class strings."""
        seen: dict[str, None] = {}
        for e in self._entries:
            if e.cohomology_class and e.cohomology_class not in ("trivial", "0", ""):
                seen[e.cohomology_class] = None
        return tuple(seen)

    def repair_hints_union(self) -> tuple[str, ...]:
        """Return the de-duplicated union of all repair hints."""
        seen: dict[str, None] = {}
        for e in self._entries:
            for hint in e.repair_hints:
                seen[hint] = None
        return tuple(seen)

    def kind_distribution(self) -> dict[str, int]:
        """Return a mapping from obstruction kind string to count."""
        dist: dict[str, int] = defaultdict(int)
        for e in self._entries:
            dist[e.kind] += 1
        return dict(dist)

    def iter_entries(self) -> Iterator[ObstructionEntry]:
        """Yield all obstruction entries in insertion order."""
        yield from self._entries

    def summary(self) -> str:
        """Return a short human-readable summary of active obstructions."""
        if not self._entries:
            return "0 obstructions"
        kinds = self.kind_distribution()
        kind_str = ", ".join(f"{k}: {v}" for k, v in kinds.items())
        return (
            f"{self.count()} obstruction(s) [{kind_str}]"
            f" -- {len(self.persistent())} persistent,"
            f" {len(self.transient())} transient"
        )


# ===========================================================================
# 6.  TrustDistributionView
# ===========================================================================


@dataclass
class TrustDistributionSnapshot:
    """A point-in-time snapshot of the trust distribution.

    Attributes:
        timestamp: UNIX UTC float of this snapshot.
        distribution: Mapping from trust level name to claim count.
        channel_breakdown: Mapping from channel name to per-trust-level
            counts.
        coordinate_path: Coordinate scope for this snapshot.
    """

    timestamp: float
    distribution: dict[str, int]
    channel_breakdown: dict[str, dict[str, int]]
    coordinate_path: str = "global"


class TrustDistributionView:
    """View of the trust distribution across all claims.

    Breaks down trust levels by count, channel, coordinate, and over time.
    Copilot-backed channels are always shown separately at their declared
    COPILOT_SUGGESTED ceiling -- never aggregated into higher tiers.

    See ``theory2.tex`` S252 for the trust algebra lattice.

    Args:
        snapshots: Sequence of TrustDistributionSnapshot objects.
    """

    def __init__(
        self, snapshots: Sequence[TrustDistributionSnapshot] = ()
    ) -> None:
        self._snapshots: tuple[TrustDistributionSnapshot, ...] = tuple(snapshots)

    def latest(self) -> TrustDistributionSnapshot | None:
        """Return the most recent snapshot, or ``None`` if empty."""
        if not self._snapshots:
            return None
        return max(self._snapshots, key=lambda s: s.timestamp)

    def overall_distribution(self) -> dict[str, int]:
        """Return the trust distribution from the most recent snapshot.

        Returns:
            Dict mapping trust-level name to claim count.
        """
        snap = self.latest()
        return dict(snap.distribution) if snap else {}

    def copilot_fraction(self) -> float:
        """Return the fraction of claims that are copilot-suggested.

        Returns:
            Float in [0.0, 1.0].  Returns 0.0 if there are no claims.
        """
        dist = self.overall_distribution()
        total = sum(dist.values())
        if total == 0:
            return 0.0
        return dist.get("copilot_suggested", 0) / total

    def mechanically_verified_fraction(self) -> float:
        """Return the fraction of claims that are mechanically verified."""
        dist = self.overall_distribution()
        total = sum(dist.values())
        if total == 0:
            return 0.0
        return dist.get("mechanically_verified", 0) / total

    def trend(self) -> dict[str, list[tuple[float, int]]]:
        """Return a time-series of counts per trust level.

        Returns:
            Dict mapping trust-level name to list of (timestamp, count)
            tuples, sorted ascending by timestamp.
        """
        series: dict[str, list[tuple[float, int]]] = defaultdict(list)
        for snap in sorted(self._snapshots, key=lambda s: s.timestamp):
            for lvl, cnt in snap.distribution.items():
                series[lvl].append((snap.timestamp, cnt))
        return dict(series)

    def channel_breakdown(self) -> dict[str, dict[str, int]]:
        """Return the channel breakdown from the most recent snapshot."""
        snap = self.latest()
        if snap is None:
            return {}
        return {ch: dict(bd) for ch, bd in snap.channel_breakdown.items()}

    def per_coordinate(self) -> dict[str, dict[str, int]]:
        """Return per-coordinate trust distributions from all snapshots.

        Returns:
            Dict mapping coordinate path to distribution dict.
        """
        result: dict[str, dict[str, int]] = {}
        for snap in self._snapshots:
            if snap.coordinate_path not in result:
                result[snap.coordinate_path] = dict(snap.distribution)
        return result

    def add_snapshot(self, snapshot: TrustDistributionSnapshot) -> None:
        """Append a new snapshot to this view.

        Args:
            snapshot: The TrustDistributionSnapshot to add.
        """
        self._snapshots = self._snapshots + (snapshot,)

    def snapshot_count(self) -> int:
        """Return the number of snapshots held by this view."""
        return len(self._snapshots)

    def summary_line(self) -> str:
        """Return a one-line summary of the current trust distribution."""
        dist = self.overall_distribution()
        if not dist:
            return "no trust data"
        total = sum(dist.values())
        top = max(dist, key=lambda k: dist[k])
        copilot_pct = self.copilot_fraction() * 100.0
        return f"total={total}, top={top}({dist[top]}), copilot={copilot_pct:.1f}%"


# ===========================================================================
# 7.  EvidenceChannelView
# ===========================================================================


@dataclass
class ChannelStats:
    """Per-channel statistics for the evidence channel view.

    Attributes:
        channel_name: Canonical channel name string.
        queries: Total number of evidence requests sent.
        successes: Number of requests that returned evidence.
        failures: Number of requests that resulted in error or timeout.
        latency_ms_samples: Recorded per-request latency values (ms).
        trust_ceiling: The trust level ceiling enforced by this channel.
        is_copilot_backed: Whether this channel routes through a copilot
            model.
        last_activity_timestamp: UNIX UTC float of the last request.
    """

    channel_name: str
    queries: int = 0
    successes: int = 0
    failures: int = 0
    latency_ms_samples: tuple[float, ...] = field(default_factory=tuple)
    trust_ceiling: str = "unverified"
    is_copilot_backed: bool = False
    last_activity_timestamp: float = 0.0


class EvidenceChannelView:
    """View of per-channel evidence statistics.

    Aggregates query counts, success/failure rates, latency distributions,
    and trust ceilings across all registered evidence channels.

    The copilot channel is always reported separately with its trust ceiling
    explicitly annotated -- it is never folded into an aggregate that would
    obscure its COPILOT_SUGGESTED ceiling.

    See ``theory2.tex`` S252 for channel jurisdiction rules.

    Args:
        channel_stats: Mapping from channel name to ChannelStats.
    """

    def __init__(
        self, channel_stats: Mapping[str, ChannelStats] | None = None
    ) -> None:
        self._stats: dict[str, ChannelStats] = dict(channel_stats or {})

    def all_channels(self) -> tuple[str, ...]:
        """Return all registered channel names."""
        return tuple(self._stats)

    def stats_for(self, channel_name: str) -> ChannelStats | None:
        """Return stats for a specific channel, or None if not found."""
        return self._stats.get(channel_name)

    def total_queries(self) -> int:
        """Return the total query count across all channels."""
        return sum(s.queries for s in self._stats.values())

    def total_failures(self) -> int:
        """Return the total failure count across all channels."""
        return sum(s.failures for s in self._stats.values())

    def overall_success_rate(self) -> float:
        """Return the overall success rate across all channels.

        Returns:
            Float in [0.0, 1.0].  Returns 0.0 if no queries were made.
        """
        total = self.total_queries()
        if total == 0:
            return 0.0
        return sum(s.successes for s in self._stats.values()) / total

    def copilot_stats(self) -> ChannelStats | None:
        """Return the ChannelStats for the copilot channel, if present."""
        return self._stats.get(EvidenceChannel.COPILOT.value)

    def latency_summary(self) -> dict[str, dict[str, float]]:
        """Return mean and stdev latency for every channel.

        Returns:
            Dict mapping channel name to {'mean_ms': ..., 'stdev_ms': ...}.
        """
        result: dict[str, dict[str, float]] = {}
        for name, stats in self._stats.items():
            samples = list(stats.latency_ms_samples)
            result[name] = {
                "mean_ms": _safe_mean(samples),
                "stdev_ms": _safe_stdev(samples),
            }
        return result

    def channels_above_failure_rate(
        self, threshold: float = 0.1
    ) -> tuple[str, ...]:
        """Return channels whose failure rate exceeds threshold.

        Args:
            threshold: Failure-rate threshold in [0.0, 1.0].
        """
        result = []
        for name, stats in self._stats.items():
            if stats.queries > 0 and (stats.failures / stats.queries) > threshold:
                result.append(name)
        return tuple(result)

    def trust_ceiling_map(self) -> dict[str, str]:
        """Return a mapping from channel name to trust ceiling string."""
        return {name: s.trust_ceiling for name, s in self._stats.items()}

    def register(self, stats: ChannelStats) -> None:
        """Register or replace stats for a channel.

        Args:
            stats: ChannelStats instance to register.
        """
        self._stats[stats.channel_name] = stats

    def summary(self) -> str:
        """Return a multi-line summary of all channel stats."""
        lines = [f"Evidence channels ({len(self._stats)} registered):"]
        for name, stats in sorted(self._stats.items()):
            sr = (stats.successes / stats.queries * 100) if stats.queries else 0.0
            mean_lat = _safe_mean(list(stats.latency_ms_samples))
            copilot_tag = " [copilot]" if stats.is_copilot_backed else ""
            lines.append(
                f"  {name}{copilot_tag}: {stats.queries}q,"
                f" {sr:.1f}% ok, {mean_lat:.1f}ms,"
                f" ceiling={stats.trust_ceiling}"
            )
        return "\n".join(lines)


# ===========================================================================
# 8.  DiagnosticFilter
# ===========================================================================


@dataclass
class FilterCriteria:
    """Criteria for narrowing a diagnostic query.

    Attributes:
        coordinate_prefix: If set, only items whose coordinate starts with
            this prefix are included.
        trust_levels: If non-empty, only items at one of these trust levels.
        channels: If non-empty, only items involving one of these channels.
        since_timestamp: If set, only items after this UNIX UTC float.
        until_timestamp: If set, only items before this UNIX UTC float.
        statuses: If non-empty, only items with one of these status strings.
        include_copilot: Whether to include copilot-backed items.
    """

    coordinate_prefix: str = ""
    trust_levels: tuple[str, ...] = field(default_factory=tuple)
    channels: tuple[str, ...] = field(default_factory=tuple)
    since_timestamp: float | None = None
    until_timestamp: float | None = None
    statuses: tuple[str, ...] = field(default_factory=tuple)
    include_copilot: bool = True


class DiagnosticFilter:
    """Applies FilterCriteria to diagnostic views.

    Provides methods to filter VerificationStatusView, ResidualView,
    ObstructionView, and raw report sequences according to coordinate prefix,
    trust level, channel, time range, and status constraints.

    Copilot-backed items may be excluded globally by setting
    ``criteria.include_copilot = False``, which is useful for audits that
    must assess the non-copilot portion of a claim set independently.

    Args:
        criteria: The FilterCriteria to apply.
    """

    def __init__(self, criteria: FilterCriteria | None = None) -> None:
        self._criteria = criteria or FilterCriteria()

    @property
    def criteria(self) -> FilterCriteria:
        """Return the current filter criteria."""
        return self._criteria

    def with_coordinate_prefix(self, prefix: str) -> DiagnosticFilter:
        """Return a new filter restricted to the given coordinate prefix.

        Args:
            prefix: Coordinate path prefix, e.g. ``'jugeo.solver'``.
        """
        from dataclasses import replace
        return DiagnosticFilter(replace(self._criteria, coordinate_prefix=prefix))

    def with_trust_levels(self, *levels: str) -> DiagnosticFilter:
        """Return a new filter restricted to the given trust levels."""
        from dataclasses import replace
        return DiagnosticFilter(replace(self._criteria, trust_levels=tuple(levels)))

    def with_channels(self, *channels: str) -> DiagnosticFilter:
        """Return a new filter restricted to the given channel names."""
        from dataclasses import replace
        return DiagnosticFilter(replace(self._criteria, channels=tuple(channels)))

    def with_time_range(
        self,
        since: float | None = None,
        until: float | None = None,
    ) -> DiagnosticFilter:
        """Return a new filter restricted to a time range.

        Args:
            since: Inclusive lower bound (UNIX UTC float).
            until: Inclusive upper bound (UNIX UTC float).
        """
        from dataclasses import replace
        return DiagnosticFilter(
            replace(self._criteria, since_timestamp=since, until_timestamp=until)
        )

    def exclude_copilot(self) -> DiagnosticFilter:
        """Return a new filter that excludes all copilot-backed items."""
        from dataclasses import replace
        return DiagnosticFilter(replace(self._criteria, include_copilot=False))

    def filter_verified(
        self, view: VerificationStatusView
    ) -> VerificationStatusView:
        """Apply this filter to a VerificationStatusView.

        Args:
            view: The verification view to filter.

        Returns:
            A new VerificationStatusView with only matching items.
        """
        return VerificationStatusView(
            [it for it in view.all_items() if self._matches_verified(it)]
        )

    def filter_residuals(self, view: ResidualView) -> ResidualView:
        """Apply this filter to a ResidualView.

        Args:
            view: The residual view to filter.

        Returns:
            A new ResidualView with only matching entries.
        """
        return ResidualView(
            [e for e in view.all_entries() if self._matches_residual(e)]
        )

    def filter_obstructions(self, view: ObstructionView) -> ObstructionView:
        """Apply this filter to an ObstructionView.

        Args:
            view: The obstruction view to filter.

        Returns:
            A new ObstructionView with only matching entries.
        """
        return ObstructionView(
            [e for e in view.all_entries() if self._matches_obstruction(e)]
        )

    def filter_reports(
        self, reports: Sequence[DiagnosticReport]
    ) -> tuple[DiagnosticReport, ...]:
        """Apply time-range filtering to a sequence of reports.

        Args:
            reports: Sequence of DiagnosticReport objects.

        Returns:
            Tuple containing only reports within the configured time range.
        """
        c = self._criteria
        result = []
        for r in reports:
            if c.since_timestamp is not None and r.timestamp < c.since_timestamp:
                continue
            if c.until_timestamp is not None and r.timestamp > c.until_timestamp:
                continue
            result.append(r)
        return tuple(result)

    # ------------------------------------------------------------------
    # Internal predicates
    # ------------------------------------------------------------------

    def _matches_verified(self, item: VerifiedItem) -> bool:
        c = self._criteria
        if c.coordinate_prefix:
            coord = ".".join(item.support_scope)
            if not coord.startswith(c.coordinate_prefix):
                return False
        if c.trust_levels and item.trust_level not in c.trust_levels:
            return False
        if c.channels and not any(ch in item.evidence_channels for ch in c.channels):
            return False
        if not c.include_copilot and item.is_copilot_backed:
            return False
        if c.since_timestamp is not None and item.discharge_timestamp < c.since_timestamp:
            return False
        if c.until_timestamp is not None and item.discharge_timestamp > c.until_timestamp:
            return False
        return True

    def _matches_residual(self, entry: ResidualEntry) -> bool:
        c = self._criteria
        if c.coordinate_prefix and not entry.coordinate_path.startswith(
            c.coordinate_prefix
        ):
            return False
        if c.since_timestamp is not None and c.until_timestamp is not None:
            created = _utc_now() - entry.age_seconds
            if created < c.since_timestamp or created > c.until_timestamp:
                return False
        return True

    def _matches_obstruction(self, entry: ObstructionEntry) -> bool:
        c = self._criteria
        if c.coordinate_prefix and not entry.coordinate_path.startswith(
            c.coordinate_prefix
        ):
            return False
        if c.since_timestamp is not None and entry.detected_timestamp < c.since_timestamp:
            return False
        if c.until_timestamp is not None and entry.detected_timestamp > c.until_timestamp:
            return False
        return True


# ===========================================================================
# 9.  DiagnosticExporter
# ===========================================================================


class DiagnosticExporter:
    """Exports a DiagnosticReport to multiple human-readable formats.

    Supports plain text (suitable for CLI and log files), JSON, Markdown,
    and HTML.  All formats faithfully represent the trust distribution,
    evidence channel summary, and any active obstructions.  Copilot-backed
    channels are always annotated as such in the output.

    Args:
        report: The DiagnosticReport to export.
    """

    def __init__(self, report: DiagnosticReport) -> None:
        self._report = report

    def to_text(self) -> str:
        """Export the report as plain human-readable text.

        Returns:
            Multi-line string with section headers and indented detail lines.
        """
        r = self._report
        lines: list[str] = [
            "=" * 60,
            f"JuGeo Diagnostic Report  [{r.report_id}]",
            f"Scope:      {r.scope}",
            f"Timestamp:  {r.timestamp:.3f}",
            f"Generated:  {r.generation_duration_ms:.1f} ms",
            "=" * 60,
            "",
            "SUMMARY",
            f"  Verified:      {r.verified_count}",
            f"  Residual:      {r.residual_count}",
            f"  Obstructions:  {r.obstruction_count}",
            f"  Health:        {r.health_status}",
            f"  Backpressure:  {r.backpressure_level}",
            "",
            "TRUST DISTRIBUTION",
        ]
        for lvl, cnt in sorted(r.trust_distribution.items()):
            flag = "  [copilot ceiling]" if lvl == "copilot_suggested" else ""
            lines.append(f"  {lvl:<32} {cnt:>6}{flag}")
        lines += ["", "EVIDENCE CHANNELS"]
        for ch_name, ch_info in sorted(r.evidence_channel_summary.items()):
            copilot_flag = " [copilot]" if ch_name == EvidenceChannel.COPILOT.value else ""
            lines.append(f"  {ch_name}{copilot_flag}")
            lines.append(
                f"    queries={ch_info.get('queries', 0)}"
                f"  successes={ch_info.get('successes', 0)}"
                f"  failures={ch_info.get('failures', 0)}"
                f"  latency={ch_info.get('latency_ms_mean', 0.0):.1f}ms"
                f"  ceiling={ch_info.get('trust_ceiling', '?')}"
            )
        lines += ["", "HEALTH INDICATORS"]
        if r.health_indicators:
            for hi in r.health_indicators:
                lines.append(f"  [{hi.status.value}] {hi.dimension.value}: {hi.message}")
        else:
            lines.append("  (none)")
        lines += ["", "MESSAGES"]
        for msg in r.messages:
            lines.append(f"  [{msg.level.value.upper()}] {msg.message}")
        lines += ["", "=" * 60]
        return "\n".join(lines)

    def to_json(self, *, indent: int = 2) -> str:
        """Export the report as a JSON string.

        Args:
            indent: JSON indentation level.

        Returns:
            JSON string representation.
        """
        return json.dumps(self._report.as_dict(), indent=indent, default=str)

    def to_markdown(self) -> str:
        """Export the report as GitHub-flavoured Markdown.

        Returns:
            A Markdown string suitable for issue comments or documentation.
        """
        r = self._report
        lines: list[str] = [
            "## JuGeo Diagnostic Report",
            "",
            f"**Report ID:** `{r.report_id}`  ",
            f"**Scope:** `{r.scope}`  ",
            f"**Timestamp:** {r.timestamp:.3f}  ",
            "",
            "### Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Verified | {r.verified_count} |",
            f"| Residual | {r.residual_count} |",
            f"| Obstructions | {r.obstruction_count} |",
            f"| Health | `{r.health_status}` |",
            f"| Backpressure | {r.backpressure_level} |",
            "",
            "### Trust Distribution",
            "",
            "| Level | Count | Notes |",
            "|-------|-------|-------|",
        ]
        for lvl, cnt in sorted(r.trust_distribution.items()):
            note = (
                "WARNING copilot ceiling -- never silently promoted"
                if lvl == "copilot_suggested"
                else ""
            )
            lines.append(f"| `{lvl}` | {cnt} | {note} |")
        lines += [
            "",
            "### Evidence Channels",
            "",
            "| Channel | Queries | Successes | Failures | Mean Latency | Trust Ceiling |",
            "|---------|---------|-----------|----------|--------------|---------------|",
        ]
        for ch_name, ch_info in sorted(r.evidence_channel_summary.items()):
            copilot_tag = " [copilot]" if ch_name == EvidenceChannel.COPILOT.value else ""
            lines.append(
                f"| `{ch_name}`{copilot_tag}"
                f" | {ch_info.get('queries', 0)}"
                f" | {ch_info.get('successes', 0)}"
                f" | {ch_info.get('failures', 0)}"
                f" | {ch_info.get('latency_ms_mean', 0.0):.1f} ms"
                f" | `{ch_info.get('trust_ceiling', '?')}` |"
            )
        return "\n".join(lines)

    def to_html(self) -> str:
        """Export the report as a self-contained HTML fragment.

        The fragment does not depend on external CSS and is suitable for
        embedding in web dashboards.

        Returns:
            HTML string fragment (no <html> or <body> wrapper).
        """
        r = self._report

        def esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        rows_trust = "".join(
            f"<tr><td>{esc(lvl)}</td><td>{cnt}</td>"
            f"<td>{'WARNING copilot ceiling' if lvl == 'copilot_suggested' else ''}</td></tr>"
            for lvl, cnt in sorted(r.trust_distribution.items())
        )
        rows_channel = "".join(
            f"<tr><td>{esc(ch_name)}"
            f"{'&nbsp;[copilot]' if ch_name == 'copilot' else ''}</td>"
            f"<td>{ch_info.get('queries', 0)}</td>"
            f"<td>{ch_info.get('successes', 0)}</td>"
            f"<td>{ch_info.get('failures', 0)}</td>"
            f"<td>{ch_info.get('latency_ms_mean', 0.0):.1f}&nbsp;ms</td>"
            f"<td>{esc(str(ch_info.get('trust_ceiling', '?')))}</td></tr>"
            for ch_name, ch_info in sorted(r.evidence_channel_summary.items())
        )
        return (
            '<div class="jugeo-report">'
            "<h2>JuGeo Diagnostic Report</h2>"
            f"<p><b>ID:</b> {esc(r.report_id)}&nbsp;"
            f"<b>Scope:</b> {esc(r.scope)}&nbsp;"
            f"<b>Health:</b> {esc(r.health_status)}</p>"
            f"<p>Verified: <b>{r.verified_count}</b>&nbsp;"
            f"Residual: <b>{r.residual_count}</b>&nbsp;"
            f"Obstructions: <b>{r.obstruction_count}</b></p>"
            "<h3>Trust Distribution</h3>"
            '<table border="1" cellpadding="4">'
            "<tr><th>Level</th><th>Count</th><th>Notes</th></tr>"
            f"{rows_trust}</table>"
            "<h3>Evidence Channels</h3>"
            '<table border="1" cellpadding="4">'
            "<tr><th>Channel</th><th>Queries</th><th>Successes</th>"
            "<th>Failures</th><th>Latency</th><th>Trust Ceiling</th></tr>"
            f"{rows_channel}</table>"
            "</div>"
        )


# ===========================================================================
# 10.  DiagnosticHistory
# ===========================================================================


class DiagnosticHistory:
    """Historical store and trend analyser for DiagnosticReport snapshots.

    Accumulates reports over time and provides trend analysis, regression
    detection, and historical queries.  Designed to be updated at regular
    intervals (e.g., once per orchestration epoch).

    Regression detection flags any snapshot where the obstruction count
    increases relative to the previous one, or where the health status
    degrades.

    Args:
        max_history: Maximum number of reports to retain (FIFO eviction).
    """

    _HEALTH_RANK: dict[str, int] = {
        "healthy": 0, "recovering": 1, "degraded": 2,
        "unhealthy": 3, "unknown": 4,
    }

    def __init__(self, max_history: int = 500) -> None:
        self._reports: list[DiagnosticReport] = []
        self._max_history = max_history

    def record(self, report: DiagnosticReport) -> None:
        """Append *report* to the history (evicts oldest on overflow).

        Args:
            report: The DiagnosticReport to record.
        """
        self._reports.append(report)
        if len(self._reports) > self._max_history:
            self._reports.pop(0)

    def all_reports(self) -> tuple[DiagnosticReport, ...]:
        """Return all stored reports in chronological order."""
        return tuple(self._reports)

    def count(self) -> int:
        """Return the number of stored reports."""
        return len(self._reports)

    def latest(self) -> DiagnosticReport | None:
        """Return the most recent report, or None if history is empty."""
        return self._reports[-1] if self._reports else None

    def reports_since(self, since: float) -> tuple[DiagnosticReport, ...]:
        """Return all reports generated after *since* (exclusive).

        Args:
            since: UNIX UTC float lower bound.
        """
        return tuple(r for r in self._reports if r.timestamp > since)

    def trend_verified(self) -> list[tuple[float, int]]:
        """Return the time series of verified counts.

        Returns:
            List of (timestamp, verified_count) tuples.
        """
        return [(r.timestamp, r.verified_count) for r in self._reports]

    def trend_obstructions(self) -> list[tuple[float, int]]:
        """Return the time series of obstruction counts.

        Returns:
            List of (timestamp, obstruction_count) tuples.
        """
        return [(r.timestamp, r.obstruction_count) for r in self._reports]

    def detect_regressions(self) -> list[tuple[DiagnosticReport, str]]:
        """Detect regressions between consecutive reports.

        A regression is detected when:

        * The obstruction count increases (new obstructions appeared).
        * The health status degrades.

        Returns:
            List of (report, reason_string) tuples where each entry
            identifies the report that introduced the regression.
        """
        regressions: list[tuple[DiagnosticReport, str]] = []
        for prev, curr in zip(self._reports, self._reports[1:]):
            reasons: list[str] = []
            if curr.obstruction_count > prev.obstruction_count:
                delta = curr.obstruction_count - prev.obstruction_count
                reasons.append(f"+{delta} new obstruction(s)")
            prev_rank = self._HEALTH_RANK.get(prev.health_status, 4)
            curr_rank = self._HEALTH_RANK.get(curr.health_status, 4)
            if curr_rank > prev_rank:
                reasons.append(
                    f"health degraded: {prev.health_status} to {curr.health_status}"
                )
            if reasons:
                regressions.append((curr, "; ".join(reasons)))
        return regressions

    def trust_trend_for_level(self, level: str) -> list[tuple[float, int]]:
        """Return the time series of a specific trust level count.

        Args:
            level: Trust level name string, e.g. ``'copilot_suggested'``.

        Returns:
            List of (timestamp, count) tuples.
        """
        return [
            (r.timestamp, r.trust_distribution.get(level, 0))
            for r in self._reports
        ]

    def copilot_trend(self) -> list[tuple[float, int]]:
        """Return the time series of copilot-suggested claim counts.

        Returns:
            List of (timestamp, copilot_count) tuples.
        """
        return self.trust_trend_for_level("copilot_suggested")

    def summary_statistics(self) -> dict[str, Any]:
        """Compute summary statistics over all stored reports.

        Returns:
            Dict with keys count, verified_mean, verified_stdev,
            obstruction_mean, residual_mean, regression_count,
            copilot_mean.
        """
        if not self._reports:
            return {"count": 0}
        verified_vals = [float(r.verified_count) for r in self._reports]
        obstruction_vals = [float(r.obstruction_count) for r in self._reports]
        residual_vals = [float(r.residual_count) for r in self._reports]
        copilot_vals = [
            float(r.trust_distribution.get("copilot_suggested", 0))
            for r in self._reports
        ]
        return {
            "count": len(self._reports),
            "verified_mean": _safe_mean(verified_vals),
            "verified_stdev": _safe_stdev(verified_vals),
            "obstruction_mean": _safe_mean(obstruction_vals),
            "residual_mean": _safe_mean(residual_vals),
            "regression_count": len(self.detect_regressions()),
            "copilot_mean": _safe_mean(copilot_vals),
        }


# ===========================================================================
# 11.  DiagnosticSerializer
# ===========================================================================


class DiagnosticSerializer:
    """Serializes and deserializes DiagnosticReport objects.

    All serialization is deterministic and round-trip safe for the standard
    fields of DiagnosticReport.  Complex nested objects (e.g.,
    HealthIndicator) are flattened to plain dicts.

    Copilot-backed channel data is always preserved in serialized output --
    stripping it would violate the auditability requirement described in
    ``theory2.tex`` S354.
    """

    def to_json(self, report: DiagnosticReport, *, indent: int = 2) -> str:
        """Serialize *report* to a JSON string.

        Args:
            report: The DiagnosticReport to serialize.
            indent: JSON indentation level.

        Returns:
            JSON string.
        """
        return json.dumps(report.as_dict(), indent=indent, default=str)

    def to_dict(self, report: DiagnosticReport) -> dict[str, Any]:
        """Serialize *report* to a plain Python dict.

        Args:
            report: The DiagnosticReport to serialize.

        Returns:
            A JSON-serializable dict.
        """
        return report.as_dict()

    def reports_to_json(
        self, reports: Sequence[DiagnosticReport], *, indent: int = 2
    ) -> str:
        """Serialize a sequence of reports to a JSON array.

        Args:
            reports: Sequence of DiagnosticReport objects.
            indent: JSON indentation level.

        Returns:
            JSON array string.
        """
        return json.dumps([r.as_dict() for r in reports], indent=indent, default=str)

    def from_dict(self, data: dict[str, Any]) -> DiagnosticReport:
        """Reconstruct a DiagnosticReport from a plain dict.

        Only the scalar fields and trust distribution / channel summary dicts
        are reconstructed; nested objects such as health indicators are not
        fully reinstantiated.

        Args:
            data: Dict as produced by to_dict or to_json.

        Returns:
            A DiagnosticReport with all scalar fields populated.

        Raises:
            KeyError: If required fields are missing from *data*.
            TypeError: If a field value has an unexpected type.
        """
        messages = tuple(
            DiagnosticMessage(
                level=DiagnosticLevel(m["level"]),
                message=str(m["message"]),
            )
            for m in data.get("messages", [])
        )
        return DiagnosticReport(
            report_id=str(data.get("report_id", _fresh_id("diag"))),
            timestamp=float(data.get("timestamp", 0.0)),
            scope=str(data.get("scope", "global")),
            verified_count=int(data.get("verified_count", 0)),
            residual_count=int(data.get("residual_count", 0)),
            obstruction_count=int(data.get("obstruction_count", 0)),
            trust_distribution={
                str(k): int(v)
                for k, v in data.get("trust_distribution", {}).items()
            },
            evidence_channel_summary={
                str(ch): dict(info)
                for ch, info in data.get("evidence_channel_summary", {}).items()
            },
            health_indicators=(),
            messages=messages,
            frontier_summary=str(data.get("frontier_summary", "")),
            backpressure_level=int(data.get("backpressure_level", 0)),
            generation_duration_ms=float(data.get("generation_duration_ms", 0.0)),
        )

    def from_json(self, payload: str) -> DiagnosticReport:
        """Reconstruct a DiagnosticReport from a JSON string.

        Args:
            payload: JSON string as produced by to_json.

        Returns:
            A DiagnosticReport.
        """
        return self.from_dict(json.loads(payload))

    def round_trip(self, report: DiagnosticReport) -> DiagnosticReport:
        """Serialize and immediately deserialize *report*.

        Useful in tests to verify that a report survives a serialization
        round trip without loss of scalar fields.

        Args:
            report: The DiagnosticReport to round-trip.

        Returns:
            A newly reconstructed DiagnosticReport.
        """
        return self.from_json(self.to_json(report))

    def validate(self, data: dict[str, Any]) -> list[str]:
        """Validate a raw dict against the expected report schema.

        Returns a list of error strings -- an empty list means the dict is
        valid.

        Args:
            data: Dict to validate.

        Returns:
            List of error description strings (empty if valid).
        """
        errors: list[str] = []
        for key in ("report_id", "scope", "frontier_summary"):
            if key not in data:
                errors.append(f"missing required field: {key}")
            elif not isinstance(data[key], str):
                errors.append(f"field {key!r} must be a string")
        for key in (
            "verified_count", "residual_count",
            "obstruction_count", "backpressure_level",
        ):
            if key not in data:
                errors.append(f"missing required field: {key}")
            elif not isinstance(data[key], int):
                errors.append(f"field {key!r} must be an int")
        for key in ("timestamp", "generation_duration_ms"):
            if key not in data:
                errors.append(f"missing required field: {key}")
            elif not isinstance(data[key], (int, float)):
                errors.append(f"field {key!r} must be numeric")
        if "trust_distribution" in data and not isinstance(
            data["trust_distribution"], dict
        ):
            errors.append("field 'trust_distribution' must be a dict")
        if "evidence_channel_summary" in data and not isinstance(
            data["evidence_channel_summary"], dict
        ):
            errors.append("field 'evidence_channel_summary' must be a dict")
        return errors


# ===========================================================================
# Legacy public API  (backward-compatible with callers of the original file)
# ===========================================================================


def collect_diagnostics(
    health: tuple[HealthSnapshot, ...],
    frontier: FrontierState,
    signal: BackpressureSignal,
) -> DiagnosticReport:
    """Assemble a DiagnosticReport from subsystem snapshots.

    This is the legacy entry point preserved for backward compatibility with
    callers that imported the original ``collect_diagnostics`` function.  New
    callers should use DiagnosticsEngine directly for richer output.

    Args:
        health: Tuple of HealthSnapshot objects.
        frontier: Current FrontierState.
        signal: Current BackpressureSignal from the integration pipeline.

    Returns:
        A populated DiagnosticReport.
    """
    messages: list[DiagnosticMessage] = [
        DiagnosticMessage(DiagnosticLevel.INFO, f"health snapshots: {len(health)}"),
    ]
    if frontier.items:
        messages.append(DiagnosticMessage(
            DiagnosticLevel.INFO,
            f"frontier items: {len(frontier.items)}",
        ))
    if signal.reasons:
        messages.append(
            DiagnosticMessage(DiagnosticLevel.WARNING, ", ".join(signal.reasons))
        )
    bp_level = int(signal.level) if hasattr(signal, "level") else 0
    return DiagnosticReport(
        scope="global",
        residual_count=0,
        obstruction_count=0,
        backpressure_level=bp_level,
        messages=tuple(messages),
    )


# ===========================================================================
# __all__
# ===========================================================================

__all__ = [
    # Legacy
    "DiagnosticLevel",
    "DiagnosticMessage",
    "collect_diagnostics",
    # Core types
    "DiagnosticReport",
    "DiagnosticsEngine",
    # Views
    "VerifiedItem",
    "VerificationStatusView",
    "ResidualEntry",
    "ResidualView",
    "ObstructionEntry",
    "ObstructionView",
    "TrustDistributionSnapshot",
    "TrustDistributionView",
    "ChannelStats",
    "EvidenceChannelView",
    # Filtering
    "FilterCriteria",
    "DiagnosticFilter",
    # Export / serialization / history
    "DiagnosticExporter",
    "DiagnosticHistory",
    "DiagnosticSerializer",
    # Cross-subsystem diagnostics
    "geometry_diagnostics",
    "evidence_diagnostics",
    "solver_diagnostics",
]

# ---------------------------------------------------------------------------
# Cross-subsystem diagnostic projections
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry import site as _geo_site, covers as _geo_covers, descent as _geo_descent  # type: ignore[import]
    _GEOMETRY_DIAG_AVAILABLE = True
except ImportError:
    _geo_site = None  # type: ignore[assignment]
    _geo_covers = None  # type: ignore[assignment]
    _geo_descent = None  # type: ignore[assignment]
    _GEOMETRY_DIAG_AVAILABLE = False

try:
    from jugeo.evidence import trust as _ev_trust, certificates as _ev_certs, provenance as _ev_prov  # type: ignore[import]
    _EVIDENCE_DIAG_AVAILABLE = True
except ImportError:
    _ev_trust = None  # type: ignore[assignment]
    _ev_certs = None  # type: ignore[assignment]
    _ev_prov = None  # type: ignore[assignment]
    _EVIDENCE_DIAG_AVAILABLE = False

try:
    from jugeo.solver import session as _solver_session  # type: ignore[import]
    _SOLVER_DIAG_AVAILABLE = True
except ImportError:
    _solver_session = None  # type: ignore[assignment]
    _SOLVER_DIAG_AVAILABLE = False


def geometry_diagnostics() -> dict[str, Any]:
    """Collect health diagnostics for the geometry subsystem.

    Queries ``jugeo.geometry`` for site topology health, cover completeness,
    and descent progress.  Returns a dictionary safe for inclusion in a
    :class:`DiagnosticReport`.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "site": ..., "covers": ..., "descent": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _GEOMETRY_DIAG_AVAILABLE,
        "site": None,
        "covers": None,
        "descent": None,
        "errors": [],
    }
    if not _GEOMETRY_DIAG_AVAILABLE:
        result["errors"].append("jugeo.geometry subsystem is not installed")
        return result
    try:
        if hasattr(_geo_site, "health_summary"):
            result["site"] = _geo_site.health_summary()
        if hasattr(_geo_covers, "health_summary"):
            result["covers"] = _geo_covers.health_summary()
        if hasattr(_geo_descent, "health_summary"):
            result["descent"] = _geo_descent.health_summary()
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def evidence_diagnostics() -> dict[str, Any]:
    """Collect health diagnostics for the evidence subsystem.

    Queries ``jugeo.evidence`` for trust algebra consistency, certificate
    validity, and provenance graph health.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "trust": ..., "certificates": ..., "provenance": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _EVIDENCE_DIAG_AVAILABLE,
        "trust": None,
        "certificates": None,
        "provenance": None,
        "errors": [],
    }
    if not _EVIDENCE_DIAG_AVAILABLE:
        result["errors"].append("jugeo.evidence subsystem is not installed")
        return result
    try:
        if hasattr(_ev_trust, "health_summary"):
            result["trust"] = _ev_trust.health_summary()
        if hasattr(_ev_certs, "health_summary"):
            result["certificates"] = _ev_certs.health_summary()
        if hasattr(_ev_prov, "health_summary"):
            result["provenance"] = _ev_prov.health_summary()
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def solver_diagnostics() -> dict[str, Any]:
    """Collect health diagnostics for the solver subsystem.

    Queries ``jugeo.solver`` for Z3 session health including active session
    count, timeout statistics, and memory usage.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "session_health": ..., "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _SOLVER_DIAG_AVAILABLE,
        "session_health": None,
        "errors": [],
    }
    if not _SOLVER_DIAG_AVAILABLE:
        result["errors"].append("jugeo.solver subsystem is not installed")
        return result
    try:
        if hasattr(_solver_session, "health_summary"):
            result["session_health"] = _solver_session.health_summary()
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


# copilot: shared-core marker for future LLM orchestration.
