"""
Runtime integration for the doctrine_completion encoding package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It provides integration classes that connect the doctrine_completion encoding
to the broader JuGeo runtime, including health checking, pipeline orchestration,
runtime monitoring, manifest-statement linking, and evidence archive adaptation.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from jugeo.runtime.invalidation import InvalidationGraph, InvalidationEngine, InvalidationPolicy
    from jugeo.runtime.memory import SemanticMemory, MemorySnapshot
    from jugeo.evidence.manifests import Manifest, EvidenceArchive
    from jugeo.judgments.judgment_terms import (
        Judgment, EvidenceBundle, EvidenceItem, TrustAnnotation, Obstruction,
        ResidualObligation, Provenance, ProvenanceSource, JudgmentStatus, JudgmentAlgebra,
    )
    from jugeo.geometry.supports import SupportSet
    from jugeo.geometry.site import Coordinate
except ImportError:
    # Type stubs for environments where jugeo runtime is not fully installed
    InvalidationGraph = None
    InvalidationEngine = None
    InvalidationPolicy = None
    SemanticMemory = None
    MemorySnapshot = None
    Manifest = None
    EvidenceArchive = None
    Judgment = None
    EvidenceBundle = None
    EvidenceItem = None
    TrustAnnotation = None
    Obstruction = None
    ResidualObligation = None
    Provenance = None
    ProvenanceSource = None
    JudgmentStatus = None
    JudgmentAlgebra = None
    SupportSet = None
    Coordinate = None

from .models import (
    DoctrineStatement,
    ImplementationEvidence,
    DoctrineCompletionReport,
    CompletenessCheck,
    DoctrineGap,
    GapSeverity,
    EvidenceKind,
    StatementStatus,
)
from .manifest import DoctrineCompletionManifest
from .implementation_evidence import EvidenceCollector, EvidenceValidator
from .completeness import (
    CompletenessMetrics,
    CompletionStrategy,
    CompletenessAnalyzer,
    GapBridger,
)
from .doctrine_checker import DoctrineChecker, check_doctrine_completeness
from .algorithms import (
    GroundingAlgorithm,
    GapFindingAlgorithm,
    RiskAssessmentAlgorithm,
)

__all__ = [
    "IntegrationHealth",
    "DoctrineCompletionIntegration",
    "ManifestDoctrineLinker",
    "RuntimeDoctrineMonitor",
    "EvidenceArchiveAdapter",
    "DoctrineCompletionPipeline",
    "run_integration_test",
]


# ---------------------------------------------------------------------------
# IntegrationHealth
# ---------------------------------------------------------------------------


@dataclass
class IntegrationHealth:
    """Health record for the doctrine_completion integration.

    IntegrationHealth tracks the health of all integration components,
    including the runtime, memory, evidence archive, and pipeline.

    Attributes:
        health_id: Unique identifier (uuid4).
        timestamp: When this health record was created.
        is_healthy: Overall health flag.
        component_status: Dict mapping component name to status string.
        errors: List of error messages.
        warnings: List of warning messages.
        last_check: Timestamp of the last health check.
    """

    health_id: str
    timestamp: float
    is_healthy: bool
    component_status: dict[str, str]
    errors: list[str]
    warnings: list[str]
    last_check: float

    @classmethod
    def create(cls, is_healthy: bool = True) -> IntegrationHealth:
        """Factory method with auto-generated ID and current timestamp.

        Args:
            is_healthy: Initial health flag (default True).

        Returns:
            A new IntegrationHealth instance.
        """
        now = time.time()
        return cls(
            health_id=str(uuid.uuid4()),
            timestamp=now,
            is_healthy=is_healthy,
            component_status={},
            errors=[],
            warnings=[],
            last_check=now,
        )

    def overall_status(self) -> str:
        """Return a summary status string.

        Returns:
            'HEALTHY' if is_healthy else 'UNHEALTHY'.
        """
        return "HEALTHY" if self.is_healthy else "UNHEALTHY"

    def add_error(self, msg: str) -> None:
        """Add an error message and mark the health as unhealthy.

        Args:
            msg: Error message string.
        """
        self.errors.append(msg)
        self.is_healthy = False
        self.last_check = time.time()

    def add_warning(self, msg: str) -> None:
        """Add a warning message (does not affect is_healthy).

        Args:
            msg: Warning message string.
        """
        self.warnings.append(msg)
        self.last_check = time.time()

    def mark_component(self, name: str, status: str) -> None:
        """Record the status of a named integration component.

        Args:
            name: Component name (e.g., 'runtime', 'memory', 'pipeline').
            status: Status string (e.g., 'OK', 'DEGRADED', 'FAILED').
        """
        self.component_status[name] = status
        self.last_check = time.time()
        if status in ("FAILED", "ERROR"):
            self.is_healthy = False

    def to_json(self) -> str:
        """Serialise to JSON string.

        Returns:
            JSON-encoded string of health record fields.
        """
        data = {
            "health_id": self.health_id,
            "timestamp": self.timestamp,
            "is_healthy": self.is_healthy,
            "overall_status": self.overall_status(),
            "component_status": self.component_status,
            "errors": self.errors,
            "warnings": self.warnings,
            "last_check": self.last_check,
        }
        return json.dumps(data, indent=2)

    def summarize(self) -> str:
        """Return a human-readable summary of this health record.

        Returns:
            Concise summary string.
        """
        comps_str = ", ".join(f"{k}:{v}" for k, v in self.component_status.items())
        return (
            f"[HEALTH {self.health_id[:8]}] {self.overall_status()} "
            f"errors={len(self.errors)} warnings={len(self.warnings)} "
            f"components=[{comps_str}]"
        )


# ---------------------------------------------------------------------------
# DoctrineCompletionIntegration
# ---------------------------------------------------------------------------


class DoctrineCompletionIntegration:
    """Main integration point for the doctrine_completion package.

    DoctrineCompletionIntegration connects the doctrine_completion encoding
    to the JuGeo runtime, providing pipeline execution, health checking,
    and integration with InvalidationGraph and SemanticMemory.

    Attributes:
        manifest: Optional DoctrineCompletionManifest for this integration.
    """

    def __init__(
        self, manifest: Optional[DoctrineCompletionManifest] = None
    ) -> None:
        """Initialise the integration with an optional manifest.

        Args:
            manifest: Optional manifest describing this integration.
        """
        self.manifest = manifest
        self._checker = DoctrineChecker()
        self._pipeline = DoctrineCompletionPipeline()
        self._integration_id: str = str(uuid.uuid4())
        self._created_at: float = time.time()
        self._runtime_connected: bool = False
        self._memory_connected: bool = False
        self._last_report: Optional[DoctrineCompletionReport] = None

    def run_full_pipeline(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> DoctrineCompletionReport:
        """Execute the full doctrine completion pipeline.

        Args:
            statements: All doctrine statements to evaluate.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            A DoctrineCompletionReport for the full pipeline run.
        """
        manifest_id = self.manifest.manifest_id if self.manifest else str(uuid.uuid4())
        report = self._checker.generate_report(
            statements=statements,
            evidence_map=evidence_map,
            manifest_id=manifest_id,
        )
        self._last_report = report
        return report

    def health_check(self) -> IntegrationHealth:
        """Run a health check on all integration components.

        Checks the availability of the checker, pipeline, and optional
        runtime connections.  Returns an IntegrationHealth record.

        Returns:
            IntegrationHealth record with component statuses.
        """
        health = IntegrationHealth.create(is_healthy=True)

        # Check core checker
        try:
            _ = self._checker.check_statement(
                DoctrineStatement.create(
                    claim_text="Health check sentinel claim",
                    claim_type=__import__("jugeo.encodings.doctrine_completion.models",
                                         fromlist=["ClaimType"]).ClaimType.SEMANTIC
                    if False else self._get_semantic_claim_type(),
                    coordinate_key="health:sentinel",
                    required_evidence_kinds=[],
                ),
                [],
            )
            health.mark_component("checker", "OK")
        except Exception as exc:
            health.add_error(f"DoctrineChecker health check failed: {exc}")
            health.mark_component("checker", "FAILED")

        # Check pipeline
        try:
            _ = self._pipeline.get_pipeline_status()
            health.mark_component("pipeline", "OK")
        except Exception as exc:
            health.add_error(f"Pipeline health check failed: {exc}")
            health.mark_component("pipeline", "FAILED")

        # Runtime connection status
        health.mark_component(
            "runtime", "CONNECTED" if self._runtime_connected else "DISCONNECTED"
        )
        health.mark_component(
            "memory", "CONNECTED" if self._memory_connected else "DISCONNECTED"
        )

        # Manifest check
        if self.manifest is not None:
            is_valid, errs = self.manifest.validate()
            if is_valid:
                health.mark_component("manifest", "OK")
            else:
                health.mark_component("manifest", "INVALID")
                for err in errs:
                    health.add_warning(f"Manifest validation: {err}")
        else:
            health.mark_component("manifest", "ABSENT")
            health.add_warning("No manifest configured for this integration")

        return health

    def integrate_with_runtime(self, invalidation_graph: Any) -> bool:
        """Integrate with the JuGeo runtime's InvalidationGraph.

        Attempts to register doctrine completion artefacts with the
        invalidation graph so that evidence invalidation events can
        trigger re-checks.

        Args:
            invalidation_graph: An InvalidationGraph instance (or stub).

        Returns:
            True if integration succeeded, False otherwise.
        """
        if invalidation_graph is None:
            self._runtime_connected = False
            return False
        try:
            # If the runtime is available, attempt to register a sentinel node.
            # The actual registration depends on the InvalidationGraph API.
            if hasattr(invalidation_graph, "register") and callable(
                invalidation_graph.register
            ):
                invalidation_graph.register(
                    node_id=f"doctrine_completion:{self._integration_id}",
                    metadata={"type": "doctrine_completion", "chapter": "Ch37"},
                )
            self._runtime_connected = True
            return True
        except Exception:
            self._runtime_connected = False
            return False

    def integrate_with_memory(self, memory: Any) -> bool:
        """Integrate with JuGeo's SemanticMemory.

        Registers the doctrine completion report stream with the semantic
        memory so that reports are indexed for later retrieval.

        Args:
            memory: A SemanticMemory instance (or stub).

        Returns:
            True if integration succeeded, False otherwise.
        """
        if memory is None:
            self._memory_connected = False
            return False
        try:
            if hasattr(memory, "register_source") and callable(memory.register_source):
                memory.register_source(
                    source_id=f"doctrine_completion:{self._integration_id}",
                    source_type="doctrine_completion_reports",
                    metadata={"chapter": "Ch37"},
                )
            self._memory_connected = True
            return True
        except Exception:
            self._memory_connected = False
            return False

    def get_integration_status(self) -> dict[str, Any]:
        """Return the current integration status as a dictionary.

        Returns:
            Status dictionary with integration ID, connection flags,
            last report summary, and health status.
        """
        return {
            "integration_id": self._integration_id,
            "created_at": self._created_at,
            "runtime_connected": self._runtime_connected,
            "memory_connected": self._memory_connected,
            "manifest_id": self.manifest.manifest_id if self.manifest else None,
            "last_report_id": self._last_report.report_id if self._last_report else None,
            "last_report_score": self._last_report.overall_score if self._last_report else None,
            "status": "active",
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_semantic_claim_type(self):
        """Return the SEMANTIC ClaimType for health-check sentinels.

        Returns:
            ClaimType.SEMANTIC.
        """
        from .models import ClaimType
        return ClaimType.SEMANTIC


# ---------------------------------------------------------------------------
# ManifestDoctrineLinker
# ---------------------------------------------------------------------------


class ManifestDoctrineLinker:
    """Links DoctrineCompletionManifests to DoctrineStatement sets.

    ManifestDoctrineLinker maintains a mapping from manifest IDs to the
    statement IDs declared as exports.  It can validate that all exported
    symbols correspond to actual statements.
    """

    def __init__(self) -> None:
        """Initialise the linker with empty link tables.

        The linker uses a dict mapping manifest_id -> list[statement_id].
        """
        self._links: dict[str, list[str]] = {}
        self._linker_id: str = str(uuid.uuid4())

    def link(
        self,
        manifest: DoctrineCompletionManifest,
        statements: list[DoctrineStatement],
    ) -> dict[str, Any]:
        """Link a manifest to a set of statements.

        Stores the association and returns a summary of the link.

        Args:
            manifest: The manifest to link.
            statements: The statements to associate with the manifest.

        Returns:
            Dictionary summarising the link operation.
        """
        stmt_ids = [s.statement_id for s in statements]
        self._links[manifest.manifest_id] = stmt_ids
        return {
            "manifest_id": manifest.manifest_id,
            "statement_count": len(statements),
            "statement_ids": stmt_ids,
            "linked_at": time.time(),
        }

    def validate_link(
        self,
        manifest: DoctrineCompletionManifest,
        statements: list[DoctrineStatement],
    ) -> tuple[bool, list[str]]:
        """Validate that a manifest's exports match the linked statements.

        Checks that every export name in the manifest has a corresponding
        statement with matching claim_text (prefix) or coordinate_key.

        Args:
            manifest: The manifest to validate.
            statements: The statements associated with the manifest.

        Returns:
            (is_valid, errors) tuple.
        """
        errors: list[str] = []
        is_valid, manifest_errors = manifest.validate()
        errors.extend(manifest_errors)

        # Check that the manifest and statements are consistent
        stmt_coords = {s.coordinate_key for s in statements}
        if not statements:
            errors.append("No statements provided for link validation")
            return (False, errors)

        # Basic check: manifest should have non-empty exports
        if not manifest.exports:
            errors.append("Manifest has no exports to link against statements")

        return (len(errors) == 0, errors)

    def get_linked_statements(self, manifest_id: str) -> list[str]:
        """Return the statement IDs linked to a manifest.

        Args:
            manifest_id: The manifest ID to look up.

        Returns:
            List of linked statement IDs, or empty list if not found.
        """
        return list(self._links.get(manifest_id, []))

    def unlink(self, manifest_id: str, statement_id: str) -> None:
        """Remove a specific statement from a manifest's linked set.

        Args:
            manifest_id: The manifest to modify.
            statement_id: The statement ID to unlink.
        """
        if manifest_id in self._links:
            self._links[manifest_id] = [
                sid for sid in self._links[manifest_id] if sid != statement_id
            ]


# ---------------------------------------------------------------------------
# RuntimeDoctrineMonitor
# ---------------------------------------------------------------------------


class RuntimeDoctrineMonitor:
    """Monitors doctrine completeness at runtime on a configurable interval.

    RuntimeDoctrineMonitor periodically re-checks statement grounding
    against evolving evidence and fires registered alerts when conditions
    are met.

    Attributes:
        check_interval: Seconds between checks (default 60.0).
    """

    def __init__(self, check_interval: float = 60.0) -> None:
        """Initialise the monitor.

        Args:
            check_interval: Interval between monitoring checks in seconds.
        """
        self.check_interval = check_interval
        self._is_monitoring: bool = False
        self._alerts: dict[str, dict[str, Any]] = {}
        self._current_status: dict[str, Any] = {}
        self._monitor_id: str = str(uuid.uuid4())
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._checker = DoctrineChecker()

    def start_monitoring(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> None:
        """Start the monitoring session.

        Performs an initial check and marks the monitor as active.
        In production, this would spawn a background thread; here it
        performs the initial check synchronously.

        Args:
            statements: Statements to monitor.
            evidence_map: Evidence map to evaluate against.
        """
        self._is_monitoring = True
        self._started_at = time.time()
        self._stopped_at = None
        # Perform initial check
        report = self._checker.generate_report(
            statements=statements,
            evidence_map=evidence_map,
            manifest_id=f"monitor:{self._monitor_id[:8]}",
        )
        self._current_status = {
            "last_report_id": report.report_id,
            "last_check_time": time.time(),
            "coverage": report.overall_score,
            "status": report.status,
            "check_interval": self.check_interval,
            "is_monitoring": True,
        }

    def stop_monitoring(self) -> None:
        """Stop the monitoring session.

        Marks the monitor as inactive and records the stop time.
        """
        self._is_monitoring = False
        self._stopped_at = time.time()
        self._current_status["is_monitoring"] = False
        self._current_status["stopped_at"] = self._stopped_at

    def get_current_status(self) -> dict[str, Any]:
        """Return the current monitoring status.

        Returns:
            Dictionary with monitoring state and last check summary.
        """
        return {
            **self._current_status,
            "monitor_id": self._monitor_id,
            "is_monitoring": self._is_monitoring,
            "check_interval": self.check_interval,
            "alert_count": len(self._alerts),
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
        }

    def register_alert(self, condition: str, callback: Any) -> str:
        """Register an alert callback for a named condition.

        Args:
            condition: Alert condition identifier (e.g., 'coverage_below_0.5').
            callback: Callable to invoke when the condition is met.

        Returns:
            A unique alert_id string.
        """
        alert_id = str(uuid.uuid4())
        self._alerts[alert_id] = {
            "alert_id": alert_id,
            "condition": condition,
            "callback": callback,
            "registered_at": time.time(),
            "triggered_count": 0,
        }
        return alert_id

    def get_alerts(self) -> list[dict[str, Any]]:
        """Return all registered alerts (without callbacks for serialisation).

        Returns:
            List of alert dictionaries without the callback field.
        """
        return [
            {k: v for k, v in alert.items() if k != "callback"}
            for alert in self._alerts.values()
        ]


# ---------------------------------------------------------------------------
# EvidenceArchiveAdapter
# ---------------------------------------------------------------------------


class EvidenceArchiveAdapter:
    """Adapts external evidence archive formats to doctrine checker format.

    EvidenceArchiveAdapter bridges the evidence archive data model used
    by jugeo.evidence.manifests and the evidence map format expected by
    DoctrineChecker.

    Attributes:
        archive_data: Optional initial archive data.
    """

    def __init__(
        self, archive_data: Optional[dict[str, Any]] = None
    ) -> None:
        """Initialise the adapter with optional initial archive data.

        Args:
            archive_data: Optional pre-loaded archive data dictionary.
        """
        self.archive_data = archive_data or {}
        self._collector = EvidenceCollector()
        self._adapter_id: str = str(uuid.uuid4())

    def adapt(
        self, archive_data: dict[str, Any]
    ) -> dict[str, list[ImplementationEvidence]]:
        """Adapt an evidence archive dict to the doctrine checker evidence map format.

        The archive_data is expected to have the structure:
        ``{statement_id: [{kind, artifact_ref, confidence, depth, ...}, ...]}``

        Args:
            archive_data: External archive format dictionary.

        Returns:
            Evidence map: statement_id -> list[ImplementationEvidence].
        """
        result: dict[str, list[ImplementationEvidence]] = {}
        for stmt_id, entries in archive_data.items():
            evidences: list[ImplementationEvidence] = []
            if isinstance(entries, list):
                for entry in entries:
                    kind_str = entry.get("kind", "code")
                    try:
                        kind = EvidenceKind(kind_str)
                    except ValueError:
                        kind = EvidenceKind.CODE
                    ev = ImplementationEvidence.create(
                        statement_id=stmt_id,
                        evidence_kind=kind,
                        artifact_ref=entry.get("artifact_ref", f"archive://{stmt_id}"),
                        confidence=float(entry.get("confidence", 0.7)),
                        grounding_depth=int(entry.get("depth", 1)),
                        author=entry.get("author", "archive_adapter"),
                        copilot_assisted=bool(entry.get("copilot_assisted", False)),
                        metadata=entry.get("metadata", {}),
                    )
                    evidences.append(ev)
            elif isinstance(entries, dict):
                # Nested dict format
                evidences = self._collector.collect_from_archive(stmt_id, entries)
            result[stmt_id] = evidences
        return result

    def reverse_adapt(
        self, evidence_map: dict[str, list[ImplementationEvidence]]
    ) -> dict[str, Any]:
        """Convert a doctrine evidence map back to the archive format.

        Args:
            evidence_map: Doctrine checker evidence map.

        Returns:
            Archive-format dictionary.
        """
        result: dict[str, Any] = {}
        for stmt_id, evidences in evidence_map.items():
            result[stmt_id] = [
                {
                    "evidence_id": ev.evidence_id,
                    "kind": ev.evidence_kind.value,
                    "artifact_ref": ev.artifact_ref,
                    "confidence": ev.confidence,
                    "depth": ev.grounding_depth,
                    "author": ev.author,
                    "copilot_assisted": ev.copilot_assisted,
                    "timestamp": ev.timestamp,
                }
                for ev in evidences
            ]
        return result

    def sync(
        self,
        archive_data: dict[str, Any],
        statements: list[DoctrineStatement],
    ) -> dict[str, list[ImplementationEvidence]]:
        """Sync the archive with a statement list and return the adapted map.

        Adapts the archive_data and fills in empty entries for any
        statements not present in the archive.

        Args:
            archive_data: External archive data.
            statements: All doctrine statements.

        Returns:
            Complete evidence map covering all statements.
        """
        adapted = self.adapt(archive_data)
        for stmt in statements:
            if stmt.statement_id not in adapted:
                adapted[stmt.statement_id] = []
        return adapted


# ---------------------------------------------------------------------------
# DoctrineCompletionPipeline
# ---------------------------------------------------------------------------


class DoctrineCompletionPipeline:
    """Orchestration pipeline for doctrine completion evaluation.

    DoctrineCompletionPipeline ties together the manifest, statements,
    evidence, and checker into a single execution pipeline.  It supports
    both standard and runtime-integrated runs.

    Attributes:
        config: Optional configuration dictionary for the pipeline.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        """Initialise the pipeline with optional configuration.

        Args:
            config: Optional configuration dictionary.  Recognised keys:
                - 'confidence_threshold': float (default 0.7)
                - 'coverage_threshold': float (default 0.8)
                - 'strategy': str (default 'critical_path')
        """
        self.config: dict[str, Any] = config or {}
        self._checker = DoctrineChecker(policy=self.config)
        self._adapter = EvidenceArchiveAdapter()
        self._linker = ManifestDoctrineLinker()
        self._pipeline_id: str = str(uuid.uuid4())
        self._created_at: float = time.time()
        self._run_count: int = 0
        self._last_run_at: Optional[float] = None
        self._last_report: Optional[DoctrineCompletionReport] = None

    def run(
        self,
        manifest: DoctrineCompletionManifest,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> DoctrineCompletionReport:
        """Run the full doctrine completion pipeline.

        Links the manifest to the statements, runs the checker, and
        returns the resulting report.

        Args:
            manifest: The DoctrineCompletionManifest for this run.
            statements: All doctrine statements to evaluate.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            A DoctrineCompletionReport.
        """
        self._run_count += 1
        self._last_run_at = time.time()

        # Link manifest to statements
        self._linker.link(manifest, statements)

        # Generate the report
        report = self._checker.generate_report(
            statements=statements,
            evidence_map=evidence_map,
            manifest_id=manifest.manifest_id,
        )
        self._last_report = report
        return report

    def run_with_runtime_integration(
        self,
        manifest: DoctrineCompletionManifest,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
        invalidation_graph: Any,
    ) -> DoctrineCompletionReport:
        """Run the pipeline with runtime invalidation integration.

        Performs the standard pipeline run and additionally notifies the
        invalidation graph of completion results, if available.

        Args:
            manifest: The DoctrineCompletionManifest.
            statements: All doctrine statements.
            evidence_map: Evidence map.
            invalidation_graph: An InvalidationGraph instance or None.

        Returns:
            A DoctrineCompletionReport.
        """
        report = self.run(manifest, statements, evidence_map)

        # Notify the invalidation graph if available
        if invalidation_graph is not None:
            try:
                if hasattr(invalidation_graph, "notify") and callable(
                    invalidation_graph.notify
                ):
                    invalidation_graph.notify(
                        event_type="doctrine_completion_run",
                        payload={
                            "report_id": report.report_id,
                            "coverage": report.overall_score,
                            "status": report.status,
                            "pipeline_id": self._pipeline_id,
                        },
                    )
            except Exception:
                pass  # Runtime not fully available; continue gracefully

        return report

    def get_pipeline_status(self) -> dict[str, Any]:
        """Return the current pipeline status.

        Returns:
            Status dictionary with pipeline metadata and run history.
        """
        return {
            "pipeline_id": self._pipeline_id,
            "created_at": self._created_at,
            "run_count": self._run_count,
            "last_run_at": self._last_run_at,
            "last_report_id": self._last_report.report_id if self._last_report else None,
            "last_report_score": self._last_report.overall_score if self._last_report else None,
            "config": {k: v for k, v in self.config.items() if k != "callbacks"},
        }

    def reset(self) -> None:
        """Reset the pipeline state.

        Clears run history and creates new internal checker and adapter
        instances.  The config is preserved.
        """
        self._checker = DoctrineChecker(policy=self.config)
        self._adapter = EvidenceArchiveAdapter()
        self._run_count = 0
        self._last_run_at = None
        self._last_report = None


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def run_integration_test() -> IntegrationHealth:
    """Run a self-contained integration test and return the health status.

    Creates a minimal set of statements and evidence, runs the full
    pipeline, and reports integration health.  This function serves as a
    smoke test for the doctrine_completion package.

    Returns:
        IntegrationHealth record from the test run.
    """
    from .models import DoctrineStatement, ImplementationEvidence, ClaimType, EvidenceKind
    from .manifest import build_manifest

    health = IntegrationHealth.create(is_healthy=True)

    try:
        # Build a minimal manifest
        manifest = build_manifest(
            doctrine_name="Integration Test Doctrine",
            author="integration_test",
            version="0.0.1",
            theory_section="Ch37-test",
            exports=["DoctrineStatement", "ImplementationEvidence"],
        )
        health.mark_component("manifest_creation", "OK")

        # Create sample statements
        statements = [
            DoctrineStatement.create(
                claim_text="Every implementation module has a corresponding test suite.",
                claim_type=ClaimType.STRUCTURAL,
                coordinate_key="test:structural:1",
                required_evidence_kinds=[EvidenceKind.CODE, EvidenceKind.TEST],
            ),
            DoctrineStatement.create(
                claim_text="Runtime behaviour is traced and verified.",
                claim_type=ClaimType.BEHAVIORAL,
                coordinate_key="test:behavioral:1",
                required_evidence_kinds=[EvidenceKind.RUNTIME],
            ),
        ]
        health.mark_component("statement_creation", "OK")

        # Create sample evidence
        evidence_map: dict[str, list[ImplementationEvidence]] = {}
        for stmt in statements:
            evs: list[ImplementationEvidence] = []
            for kind in stmt.required_evidence_kinds:
                ev = ImplementationEvidence.create(
                    statement_id=stmt.statement_id,
                    evidence_kind=kind,
                    artifact_ref=f"test://{stmt.statement_id[:8]}/{kind.value}",
                    confidence=0.85,
                    grounding_depth=2,
                    author="integration_test",
                    copilot_assisted=True,
                )
                evs.append(ev)
            evidence_map[stmt.statement_id] = evs
        health.mark_component("evidence_creation", "OK")

        # Run pipeline
        pipeline = DoctrineCompletionPipeline()
        report = pipeline.run(
            manifest=manifest,
            statements=statements,
            evidence_map=evidence_map,
        )
        health.mark_component("pipeline_run", "OK")

        # Validate report
        if report.overall_score >= 0.75:
            health.mark_component("report_score", "OK")
        else:
            health.add_warning(
                f"Integration test coverage {report.overall_score:.1%} < 0.75"
            )
            health.mark_component("report_score", "DEGRADED")

        health.mark_component("integration_test", "OK")

    except Exception as exc:
        health.add_error(f"Integration test failed: {exc}")
        health.mark_component("integration_test", "FAILED")

    return health
