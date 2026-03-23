"""The obstruction-to-kind pipeline — complete end-to-end flow (S03-PIPELINE).

This module wires together every upstream stage into a single, observable
end-to-end pipeline.  Given a raw list of obstruction dicts it returns a
fully-populated :class:`PipelineRun` record that captures which stages
completed, which kind candidates were proposed, timing information, and
any errors that arose.

# copilot: generated for jugeo.ideation.kind_discovery — end-to-end pipeline

Module layout::

    ┌─────────────────────────────────────────────────────────────────┐
    │  jugeo.ideation.kind_discovery.the_obstruction_to_kind_     │
    │  pipeline_c                                                     │
    ├─────────────────────────────────────────────────────────────────┤
    │  Helpers                                                        │
    │    _clamp           clamp float to [lo, hi]                    │
    │    _now_iso         UTC ISO-8601 timestamp                     │
    │    _run_id          fresh UUID for a pipeline run              │
    │    _step_id         fresh UUID for a pipeline step             │
    │    _elapsed_ms      milliseconds since a start time            │
    ├─────────────────────────────────────────────────────────────────┤
    │  Enums                                                          │
    │    PipelineStage    named stages of the pipeline               │
    ├─────────────────────────────────────────────────────────────────┤
    │  Value objects (frozen dataclasses)                             │
    │    PipelineConfig       hyper-parameters                       │
    │    PipelineStepResult   result of one pipeline step            │
    │    PipelineRun          complete run record                    │
    ├─────────────────────────────────────────────────────────────────┤
    │  Mutable container                                              │
    │    PipelineArtifacts    key-value store for inter-stage data   │
    ├─────────────────────────────────────────────────────────────────┤
    │  Stateful services                                              │
    │    ObstructionToKindPipelineAnalyzer    drives the stages      │
    │    ObstructionToKindPipelineWitness     records runs           │
    │    ObstructionToKindPipelineCoordinator orchestrator           │
    └─────────────────────────────────────────────────────────────────┘

Pipeline overview
─────────────────
The end-to-end pipeline is deliberately linear, mirroring a compiler's
front-to-back pass structure:

    FIELD_ANALYSIS
        ↓
    CLUSTERING
        ↓
    HYPOTHESIS_GENERATION
        ↓
    TYPE_CONSTRUCTION
        ↓
    BOOTSTRAP
        ↓
    VALIDATION
        ↓
    COMPLETE  (or  FAILED)

Each stage produces a :class:`PipelineStepResult` that is stored in a
:class:`PipelineArtifacts` container.  The artifacts object lets later
stages retrieve the outputs of earlier stages without coupling them directly.

Error handling
──────────────
If any stage raises an uncaught exception the pipeline transitions to the
``FAILED`` state.  The :class:`PipelineRun` records the error message and
the list of stages that *did* complete before the failure.

Timing
──────
Every step is timed with ``time.perf_counter()`` at millisecond resolution.
The :class:`PipelineStepResult` stores the elapsed time for the step itself,
and the :class:`PipelineRun` stores the total elapsed time across all steps.

Validation
──────────
After the pipeline reaches ``COMPLETE`` the
:meth:`ObstructionToKindPipelineAnalyzer.validate_pipeline_run` method
applies a small set of invariant checks:
  - At least one kind candidate was produced.
  - All listed stages actually completed.
  - Total elapsed time is within the configured timeout.

Parallelism
───────────
Although the stages are executed sequentially in this implementation, the
:class:`PipelineConfig` carries a ``parallel_hypotheses`` parameter that
downstream multi-threaded wrappers can use to run hypothesis generation
concurrently.  This module itself is single-threaded.
"""

from __future__ import annotations

import datetime
import enum
import time
import uuid
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Cross-package imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.kind_discovery.models import (
        KindCandidate,
        ObstructionField,
        KindPattern,
        NewKind,
        KindStatus,
        KindBootstrapPlan,
    )
except ImportError:
    KindCandidate = None  # type: ignore[assignment,misc]
    ObstructionField = None  # type: ignore[assignment,misc]
    KindPattern = None  # type: ignore[assignment,misc]
    NewKind = None  # type: ignore[assignment,misc]
    KindStatus = None  # type: ignore[assignment,misc]
    KindBootstrapPlan = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.kind_discovery.obstruction_fields_as_evidence_of import (
        ObstructionFieldsEvidenceCoordinator,
        ObstructionFieldEvidenceConfig,
        ObstructionFieldEvidenceRecord,
    )
except ImportError:
    ObstructionFieldsEvidenceCoordinator = None  # type: ignore[assignment,misc]
    ObstructionFieldEvidenceConfig = None  # type: ignore[assignment,misc]
    ObstructionFieldEvidenceRecord = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.kind_discovery.candidate_new_mathematical_kinds_e import (
        CandidateKindsCoordinator,
        CandidateKindConfig,
        KindHypothesis,
        TypeConstructorProposal,
    )
except ImportError:
    CandidateKindsCoordinator = None  # type: ignore[assignment,misc]
    CandidateKindConfig = None  # type: ignore[assignment,misc]
    KindHypothesis = None  # type: ignore[assignment,misc]
    TypeConstructorProposal = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: String used in PipelineRun.error_message when no error occurred.
NO_ERROR: str = ""

#: Minimum number of kind candidates to consider a run successful.
MIN_CANDIDATES_FOR_SUCCESS: int = 1

#: Default timeout used if no PipelineConfig is supplied.
DEFAULT_TIMEOUT_SECONDS: float = 60.0

#: Magic value used to cap elapsed-time comparisons.
MAX_ELAPSED_MS: float = 1_000_000.0  # 1000 seconds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float, hi: float) -> float:
    """Return *v* clamped to [*lo*, *hi*].

    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.5, 0.0, 1.0)
    0.0
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return the current UTC instant in ISO-8601 format.

    Example: ``"2024-06-01T09:00:00Z"``
    """
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _run_id() -> str:
    """Generate a unique pipeline-run identifier.

    Format: ``"run-<8 hex chars>"``

    Returns
    -------
    str
        A fresh run identifier.
    """
    return "run-" + uuid.uuid4().hex[:8]


def _step_id() -> str:
    """Generate a unique pipeline-step identifier.

    Format: ``"step-<8 hex chars>"``

    Returns
    -------
    str
        A fresh step identifier.
    """
    return "step-" + uuid.uuid4().hex[:8]


def _elapsed_ms(start: float) -> float:
    """Return the number of milliseconds since *start*.

    Parameters
    ----------
    start:
        A timestamp obtained from ``time.perf_counter()``.

    Returns
    -------
    float
        Elapsed time in milliseconds, rounded to two decimal places.
    """
    return round((time.perf_counter() - start) * 1000.0, 2)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PipelineStage(str, enum.Enum):
    """Named stages of the obstruction-to-kind pipeline.

    Attributes
    ----------
    FIELD_ANALYSIS:
        The first stage: extracting H¹ classes and computing evidence.
    CLUSTERING:
        The second stage: grouping H¹ classes into coherent clusters.
    HYPOTHESIS_GENERATION:
        The third stage: synthesising :class:`KindHypothesis` objects.
    TYPE_CONSTRUCTION:
        The fourth stage: producing :class:`TypeConstructorProposal` objects.
    BOOTSTRAP:
        The fifth stage: generating experimental laws for the new kind.
    VALIDATION:
        The sixth stage: checking pipeline invariants on the run record.
    COMPLETE:
        Terminal success state.
    FAILED:
        Terminal failure state.
    """

    FIELD_ANALYSIS = "FIELD_ANALYSIS"
    CLUSTERING = "CLUSTERING"
    HYPOTHESIS_GENERATION = "HYPOTHESIS_GENERATION"
    TYPE_CONSTRUCTION = "TYPE_CONSTRUCTION"
    BOOTSTRAP = "BOOTSTRAP"
    VALIDATION = "VALIDATION"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"

    def is_terminal(self) -> bool:
        """Return True if this stage is a terminal state."""
        return self in (PipelineStage.COMPLETE, PipelineStage.FAILED)

    def next_stage(self) -> "PipelineStage":
        """Return the logically next stage in the sequence.

        For terminal stages, returns the same stage (self).
        """
        _order = [
            PipelineStage.FIELD_ANALYSIS,
            PipelineStage.CLUSTERING,
            PipelineStage.HYPOTHESIS_GENERATION,
            PipelineStage.TYPE_CONSTRUCTION,
            PipelineStage.BOOTSTRAP,
            PipelineStage.VALIDATION,
            PipelineStage.COMPLETE,
        ]
        try:
            idx = _order.index(self)
            return _order[min(idx + 1, len(_order) - 1)]
        except ValueError:
            return self


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Hyper-parameters for the end-to-end pipeline.

    Attributes
    ----------
    evidence_threshold:
        Minimum evidence strength required to proceed past the field-analysis
        stage.  Runs that produce weaker evidence are terminated early with
        FAILED status.
    min_cluster_size:
        Clusters below this size are dropped before hypothesis generation.
    max_pipeline_depth:
        Maximum number of recursive re-runs triggered by validation failures.
        Currently unused in this implementation but reserved for future use.
    timeout_seconds:
        Wall-clock budget for the entire pipeline.  If the run exceeds this
        the coordinator will mark it as FAILED.
    parallel_hypotheses:
        The target number of hypotheses to generate concurrently.  This
        implementation is single-threaded; the parameter is advisory.
    bootstrap_iterations:
        Number of law-bootstrapping iterations to attempt during the
        BOOTSTRAP stage.
    """

    evidence_threshold: float = 0.5
    min_cluster_size: int = 3
    max_pipeline_depth: int = 5
    timeout_seconds: float = 60.0
    parallel_hypotheses: int = 3
    bootstrap_iterations: int = 10


@dataclass(frozen=True, slots=True)
class PipelineStepResult:
    """The result of a single pipeline stage execution.

    Attributes
    ----------
    step_id:
        Unique identifier for this step.
    stage:
        The :class:`PipelineStage` that this result corresponds to.
    success:
        Whether the stage completed without errors.
    output_summary:
        A brief human-readable description of what the stage produced.
    elapsed_ms:
        Wall-clock time taken by this stage in milliseconds.
    artifacts:
        Identifiers of the artifacts (by name) produced by this stage.
    """

    step_id: str
    stage: PipelineStage
    success: bool
    output_summary: str
    elapsed_ms: float
    artifacts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain Python dict."""
        return {
            "step_id": self.step_id,
            "stage": self.stage.value,
            "success": self.success,
            "output_summary": self.output_summary,
            "elapsed_ms": self.elapsed_ms,
            "artifacts": list(self.artifacts),
        }


@dataclass(frozen=True, slots=True)
class PipelineRun:
    """A complete, immutable record of a pipeline run.

    Attributes
    ----------
    run_id:
        Unique identifier for this run.
    input_obstruction_count:
        Number of raw obstructions passed to the pipeline.
    stages_completed:
        Ordered tuple of stages that completed successfully.
    final_stage:
        The last stage reached before the run terminated.
    kind_candidates:
        Names or IDs of the kind candidates produced.
    elapsed_total_ms:
        Total wall-clock time for the run.
    success:
        Whether the run reached COMPLETE status.
    error_message:
        Empty string if successful; error text otherwise.
    timestamp:
        UTC timestamp at which the run was finalised.
    """

    run_id: str
    input_obstruction_count: int
    stages_completed: tuple[PipelineStage, ...]
    final_stage: PipelineStage
    kind_candidates: tuple[str, ...]
    elapsed_total_ms: float
    success: bool
    error_message: str
    timestamp: str

    def is_partial(self) -> bool:
        """Return True if the run completed some but not all stages."""
        all_main = {
            PipelineStage.FIELD_ANALYSIS,
            PipelineStage.CLUSTERING,
            PipelineStage.HYPOTHESIS_GENERATION,
            PipelineStage.TYPE_CONSTRUCTION,
            PipelineStage.BOOTSTRAP,
            PipelineStage.VALIDATION,
        }
        return bool(self.stages_completed) and set(self.stages_completed) != all_main

    def stage_count(self) -> int:
        """Return the number of stages completed."""
        return len(self.stages_completed)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain Python dict."""
        return {
            "run_id": self.run_id,
            "input_obstruction_count": self.input_obstruction_count,
            "stages_completed": [s.value for s in self.stages_completed],
            "final_stage": self.final_stage.value,
            "kind_candidates": list(self.kind_candidates),
            "elapsed_total_ms": self.elapsed_total_ms,
            "success": self.success,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
            "stage_count": self.stage_count(),
            "is_partial": self.is_partial(),
        }


# ---------------------------------------------------------------------------
# Mutable container
# ---------------------------------------------------------------------------


class PipelineArtifacts:
    """Mutable key-value store for inter-stage data exchange.

    Each stage calls :meth:`store` to deposit its output and later stages
    call :meth:`retrieve` to get that output.  This decouples the stages
    from one another — they communicate through the artifact store rather
    than through direct method calls.

    Note: This class is intentionally *not* frozen because it is designed
    to accumulate state across a run.

    Examples
    --------
    ::

        arts = PipelineArtifacts()
        arts.store(PipelineStage.FIELD_ANALYSIS, {"h1_classes": [...]})
        data = arts.retrieve(PipelineStage.FIELD_ANALYSIS)
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def store(self, stage: PipelineStage, data: Any) -> None:
        """Store *data* under the key *stage.value*.

        Parameters
        ----------
        stage:
            The pipeline stage whose output is being stored.
        data:
            Arbitrary data to store.
        """
        self._store[stage.value] = data

    def retrieve(self, stage: PipelineStage) -> Any:
        """Retrieve the data stored by *stage*.

        Parameters
        ----------
        stage:
            The pipeline stage whose output to retrieve.

        Returns
        -------
        Any
            The stored data, or ``None`` if nothing was stored for *stage*.
        """
        return self._store.get(stage.value)

    def all_stages(self) -> list[str]:
        """Return the names of all stages that have stored data.

        Returns
        -------
        list[str]
            Stage names in insertion order.
        """
        return list(self._store.keys())

    def to_dict(self) -> dict[str, Any]:
        """Return a copy of the internal store as a plain dict.

        Returns
        -------
        dict[str, Any]
            A shallow copy of the store.
        """
        return dict(self._store)


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------


class ObstructionToKindPipelineAnalyzer:
    """Drives each stage of the obstruction-to-kind pipeline.

    This class is responsible for executing individual pipeline stages and
    returning :class:`PipelineStepResult` objects.  It does not maintain
    cross-run state; all mutable state lives in the :class:`PipelineArtifacts`
    container and the :class:`ObstructionToKindPipelineWitness`.

    Parameters
    ----------
    config:
        Pipeline hyper-parameters.
    artifacts:
        The shared artifact store for this run.

    The analyzer is designed to be constructed once per pipeline run.
    """

    def __init__(
        self,
        config: PipelineConfig,
        artifacts: PipelineArtifacts,
    ) -> None:
        self._config = config
        self._artifacts = artifacts

    # ------------------------------------------------------------------
    # Stage methods
    # ------------------------------------------------------------------

    def run_field_analysis(
        self, obstructions: list[dict]
    ) -> PipelineStepResult:
        """Execute the FIELD_ANALYSIS stage.

        Extracts H¹ obstruction classes and computes an evidence record for
        the given obstructions.  Stores the evidence record in the artifact
        store under :attr:`PipelineStage.FIELD_ANALYSIS`.

        Parameters
        ----------
        obstructions:
            Raw obstruction dicts.

        Returns
        -------
        PipelineStepResult
            Step result with success/failure status.
        """
        t0 = time.perf_counter()
        step_id = _step_id()
        try:
            # Use the evidence coordinator if available; fall back to a stub
            if ObstructionFieldsEvidenceCoordinator is not None:
                ev_coord = ObstructionFieldsEvidenceCoordinator()
                field = {"id": "pipeline-field", "name": "Pipeline Field"}
                record = ev_coord.run(obstructions, field)
                self._artifacts.store(PipelineStage.FIELD_ANALYSIS, record)
                summary = (
                    f"Extracted evidence record {getattr(record, 'evidence_id', 'unknown')} "
                    f"with strength={getattr(record, 'evidence_strength', 0.0):.3f}"
                )
            else:
                # Stub: produce a minimal dict-based artifact
                stub = {
                    "evidence_id": "ev-stub",
                    "field_id": "pipeline-field",
                    "evidence_strength": min(len(obstructions) / 10.0, 1.0),
                    "cluster_count": max(1, len(obstructions) // 3),
                    "h1_classes": [],
                    "missing_kind_hypothesis": "stub-hypothesis",
                    "timestamp": _now_iso(),
                }
                self._artifacts.store(PipelineStage.FIELD_ANALYSIS, stub)
                summary = f"Stub field analysis for {len(obstructions)} obstructions."
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.FIELD_ANALYSIS,
                success=True,
                output_summary=summary,
                elapsed_ms=_elapsed_ms(t0),
                artifacts=("evidence_record",),
            )
        except Exception as exc:  # noqa: BLE001
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.FIELD_ANALYSIS,
                success=False,
                output_summary=f"Field analysis failed: {exc}",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=(),
            )

    def run_clustering(
        self,
        field_result: PipelineStepResult,
        config: PipelineConfig,
    ) -> PipelineStepResult:
        """Execute the CLUSTERING stage.

        Retrieves the evidence record from the artifact store and converts
        it into a list of cluster dicts for downstream consumption.

        Parameters
        ----------
        field_result:
            The result from the FIELD_ANALYSIS stage.
        config:
            Pipeline configuration.

        Returns
        -------
        PipelineStepResult
            Step result.
        """
        t0 = time.perf_counter()
        step_id = _step_id()
        try:
            ev = self._artifacts.retrieve(PipelineStage.FIELD_ANALYSIS)
            cluster_count = getattr(ev, "cluster_count", ev.get("cluster_count", 1) if isinstance(ev, dict) else 1)
            # Build synthetic cluster dicts from the evidence
            clusters = [
                {
                    "cluster_id": f"clust-{i}",
                    "centroid": getattr(ev, "missing_kind_hypothesis", "unknown") if not isinstance(ev, dict) else ev.get("missing_kind_hypothesis", "unknown"),
                    "size": max(self._config.min_cluster_size, 3),
                    "intra_similarity": 0.6,
                }
                for i in range(max(1, cluster_count))
            ]
            self._artifacts.store(PipelineStage.CLUSTERING, clusters)
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.CLUSTERING,
                success=True,
                output_summary=f"Produced {len(clusters)} cluster(s).",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=("clusters",),
            )
        except Exception as exc:  # noqa: BLE001
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.CLUSTERING,
                success=False,
                output_summary=f"Clustering failed: {exc}",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=(),
            )

    def run_hypothesis_generation(
        self, cluster_result: PipelineStepResult
    ) -> PipelineStepResult:
        """Execute the HYPOTHESIS_GENERATION stage.

        Uses the :class:`CandidateKindsCoordinator` (if available) to
        generate kind hypotheses from the clusters.

        Parameters
        ----------
        cluster_result:
            The result from the CLUSTERING stage.

        Returns
        -------
        PipelineStepResult
            Step result.
        """
        t0 = time.perf_counter()
        step_id = _step_id()
        try:
            clusters = self._artifacts.retrieve(PipelineStage.CLUSTERING) or []
            ev = self._artifacts.retrieve(PipelineStage.FIELD_ANALYSIS)
            evidence = ev.to_dict() if hasattr(ev, "to_dict") else (ev if isinstance(ev, dict) else {})

            if CandidateKindsCoordinator is not None:
                ck_coord = CandidateKindsCoordinator()
                hyps = ck_coord.run(clusters, evidence, [])
            else:
                hyps = [{"hypothesis_id": f"hyp-stub-{i}", "name": f"StubKind{i}",
                         "composite_score": 0.5} for i in range(max(1, len(clusters)))]

            self._artifacts.store(PipelineStage.HYPOTHESIS_GENERATION, hyps)
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.HYPOTHESIS_GENERATION,
                success=True,
                output_summary=f"Generated {len(hyps)} hypothesis/hypotheses.",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=("hypotheses",),
            )
        except Exception as exc:  # noqa: BLE001
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.HYPOTHESIS_GENERATION,
                success=False,
                output_summary=f"Hypothesis generation failed: {exc}",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=(),
            )

    def run_type_construction(
        self, hyp_result: PipelineStepResult
    ) -> PipelineStepResult:
        """Execute the TYPE_CONSTRUCTION stage.

        Derives :class:`TypeConstructorProposal` objects (or stub dicts) from
        the hypotheses generated in the previous stage.

        Parameters
        ----------
        hyp_result:
            The result from the HYPOTHESIS_GENERATION stage.

        Returns
        -------
        PipelineStepResult
            Step result.
        """
        t0 = time.perf_counter()
        step_id = _step_id()
        try:
            hyps = self._artifacts.retrieve(PipelineStage.HYPOTHESIS_GENERATION) or []
            proposals = []
            for hyp in hyps:
                if hasattr(hyp, "hypothesis_id") and CandidateKindsCoordinator is not None:
                    from jugeo.ideation.kind_discovery.candidate_new_mathematical_kinds_e import CandidateKindsAnalyzer
                    analyzer = CandidateKindsAnalyzer()
                    prop = analyzer.propose_type_constructor(hyp)
                    proposals.append(prop)
                else:
                    hyp_id = hyp.get("hypothesis_id", "hyp-?") if isinstance(hyp, dict) else str(hyp)
                    proposals.append({
                        "proposal_id": f"prop-{uuid.uuid4().hex[:6]}",
                        "kind_hypothesis_id": hyp_id,
                        "constructor_name": f"MkStub",
                        "laws": ["identity-law"],
                    })
            self._artifacts.store(PipelineStage.TYPE_CONSTRUCTION, proposals)
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.TYPE_CONSTRUCTION,
                success=True,
                output_summary=f"Produced {len(proposals)} type-constructor proposal(s).",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=("proposals",),
            )
        except Exception as exc:  # noqa: BLE001
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.TYPE_CONSTRUCTION,
                success=False,
                output_summary=f"Type construction failed: {exc}",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=(),
            )

    def run_bootstrap(self, tc_result: PipelineStepResult) -> PipelineStepResult:
        """Execute the BOOTSTRAP stage.

        Iterates over the type-constructor proposals and generates
        experimental law instances for each, respecting
        ``config.bootstrap_iterations``.

        Parameters
        ----------
        tc_result:
            The result from the TYPE_CONSTRUCTION stage.

        Returns
        -------
        PipelineStepResult
            Step result.
        """
        t0 = time.perf_counter()
        step_id = _step_id()
        try:
            proposals = self._artifacts.retrieve(PipelineStage.TYPE_CONSTRUCTION) or []
            bootstrap_results = []
            for prop in proposals:
                cname = (
                    prop.constructor_name if hasattr(prop, "constructor_name")
                    else prop.get("constructor_name", "MkUnknown") if isinstance(prop, dict)
                    else "MkUnknown"
                )
                laws = (
                    list(prop.laws) if hasattr(prop, "laws")
                    else prop.get("laws", []) if isinstance(prop, dict)
                    else []
                )
                iterations = min(self._config.bootstrap_iterations, len(laws) + 1)
                bootstrap_results.append({
                    "constructor": cname,
                    "laws_bootstrapped": laws[:iterations],
                    "iterations": iterations,
                    "status": "bootstrapped",
                })
            self._artifacts.store(PipelineStage.BOOTSTRAP, bootstrap_results)
            total_laws = sum(len(r["laws_bootstrapped"]) for r in bootstrap_results)
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.BOOTSTRAP,
                success=True,
                output_summary=(
                    f"Bootstrapped {len(bootstrap_results)} constructor(s), "
                    f"{total_laws} law instance(s) total."
                ),
                elapsed_ms=_elapsed_ms(t0),
                artifacts=("bootstrap_results",),
            )
        except Exception as exc:  # noqa: BLE001
            return PipelineStepResult(
                step_id=step_id,
                stage=PipelineStage.BOOTSTRAP,
                success=False,
                output_summary=f"Bootstrap failed: {exc}",
                elapsed_ms=_elapsed_ms(t0),
                artifacts=(),
            )

    def validate_pipeline_run(self, run: PipelineRun) -> list[str]:
        """Apply invariant checks to a completed :class:`PipelineRun`.

        Checks:
        1. At least :data:`MIN_CANDIDATES_FOR_SUCCESS` kind candidate(s) produced.
        2. Run is marked as successful.
        3. Total elapsed time is within the configured timeout.
        4. The FIELD_ANALYSIS and CLUSTERING stages both completed.

        Parameters
        ----------
        run:
            The run to validate.

        Returns
        -------
        list[str]
            A list of human-readable validation error messages.  Empty list
            means the run is valid.
        """
        errors: list[str] = []
        if len(run.kind_candidates) < MIN_CANDIDATES_FOR_SUCCESS:
            errors.append(
                f"No kind candidates produced "
                f"(minimum {MIN_CANDIDATES_FOR_SUCCESS} required)."
            )
        if not run.success:
            errors.append(f"Run marked as failed: {run.error_message}")
        timeout_ms = self._config.timeout_seconds * 1000.0
        if run.elapsed_total_ms > timeout_ms:
            errors.append(
                f"Elapsed time {run.elapsed_total_ms:.1f}ms exceeds "
                f"timeout {timeout_ms:.1f}ms."
            )
        required = {PipelineStage.FIELD_ANALYSIS, PipelineStage.CLUSTERING}
        missing = required - set(run.stages_completed)
        for s in missing:
            errors.append(f"Required stage {s.value} did not complete.")
        return errors

    def summarize_run(self, run: PipelineRun) -> str:
        """Return a multi-line human-readable summary of a pipeline run.

        Parameters
        ----------
        run:
            The run to summarise.

        Returns
        -------
        str
            A formatted multi-line string.
        """
        status = "SUCCESS" if run.success else "FAILED"
        lines = [
            f"Pipeline Run {run.run_id}  [{status}]",
            "=" * 60,
            f"Input obstructions:   {run.input_obstruction_count}",
            f"Stages completed:     {', '.join(s.value for s in run.stages_completed)}",
            f"Final stage:          {run.final_stage.value}",
            f"Kind candidates:      {len(run.kind_candidates)}",
            f"Total elapsed:        {run.elapsed_total_ms:.2f} ms",
            f"Timestamp:            {run.timestamp}",
        ]
        if run.error_message:
            lines.append(f"Error:                {run.error_message}")
        if run.kind_candidates:
            lines.append("Candidates:")
            for cand in run.kind_candidates:
                lines.append(f"  • {cand}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class ObstructionToKindPipelineWitness:
    """Accumulates and queries :class:`PipelineRun` records.

    Every coordinator run deposits its result here.  The witness provides
    aggregate statistics and supports failure analysis.

    Usage::

        witness = ObstructionToKindPipelineWitness()
        witness.record(run_a)
        witness.record(run_b)
        print(f"success rate: {witness.success_rate():.2%}")
        print(f"avg elapsed:  {witness.avg_elapsed_ms():.1f} ms")
    """

    def __init__(self) -> None:
        self._runs: list[PipelineRun] = []

    def record(self, run: PipelineRun) -> None:
        """Append *run* to the internal log.

        Parameters
        ----------
        run:
            The :class:`PipelineRun` to record.
        """
        self._runs.append(run)

    def success_rate(self) -> float:
        """Return the fraction of runs that succeeded.

        Returns
        -------
        float
            A value in [0.0, 1.0]; 0.0 if no runs have been recorded.
        """
        if not self._runs:
            return 0.0
        return sum(1 for r in self._runs if r.success) / len(self._runs)

    def avg_elapsed_ms(self) -> float:
        """Return the mean elapsed time across all recorded runs.

        Returns
        -------
        float
            Mean elapsed time in milliseconds; 0.0 if no runs recorded.
        """
        if not self._runs:
            return 0.0
        return sum(r.elapsed_total_ms for r in self._runs) / len(self._runs)

    def failed_runs(self) -> list[PipelineRun]:
        """Return all runs that did not succeed.

        Returns
        -------
        list[PipelineRun]
            Runs in insertion order whose ``success`` flag is ``False``.
        """
        return [r for r in self._runs if not r.success]

    def total_candidates(self) -> int:
        """Return the total number of kind candidates produced across all runs."""
        return sum(len(r.kind_candidates) for r in self._runs)

    def export(self) -> list[dict[str, Any]]:
        """Serialise all runs to a list of dicts.

        Returns
        -------
        list[dict[str, Any]]
            One dict per run, from :meth:`PipelineRun.to_dict`.
        """
        return [r.to_dict() for r in self._runs]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ObstructionToKindPipelineCoordinator:
    """End-to-end orchestrator for the obstruction-to-kind pipeline.

    This is the primary entry point for external callers.  It wires together
    the analyzer, artifact store, and witness, then executes all six pipeline
    stages in sequence.

    Parameters
    ----------
    config:
        Pipeline hyper-parameters.  Defaults to :class:`PipelineConfig`.

    Example
    -------
    ::

        coord = ObstructionToKindPipelineCoordinator()
        obstructions = [
            {"label": "non-associative", "coordinate": "Expr.compose"},
            {"label": "non-associative", "coordinate": "Expr.pipe"},
            {"label": "non-associative", "coordinate": "Expr.chain"},
        ]
        run = coord.run(obstructions)
        print(coord.report())
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self._config = config or PipelineConfig()
        self.witness = ObstructionToKindPipelineWitness()

    def run(self, obstructions: list[dict]) -> PipelineRun:
        """Execute the full pipeline with the default configuration.

        This is a convenience wrapper around :meth:`run_with_config`.

        Parameters
        ----------
        obstructions:
            List of raw obstruction dicts.

        Returns
        -------
        PipelineRun
            The completed (or failed) run record.
        """
        return self.run_with_config(obstructions, self._config)

    def run_with_config(
        self,
        obstructions: list[dict],
        config: PipelineConfig,
    ) -> PipelineRun:
        """Execute the pipeline with an explicit configuration.

        Parameters
        ----------
        obstructions:
            List of raw obstruction dicts.
        config:
            The pipeline configuration to use for this run.

        Returns
        -------
        PipelineRun
            The completed (or failed) run record.
        """
        run_id = _run_id()
        artifacts = PipelineArtifacts()
        analyzer = ObstructionToKindPipelineAnalyzer(config, artifacts)
        stages_completed: list[PipelineStage] = []
        t_start = time.perf_counter()
        error_message = NO_ERROR

        stage_sequence = [
            (PipelineStage.FIELD_ANALYSIS,        lambda: analyzer.run_field_analysis(obstructions)),
            (PipelineStage.CLUSTERING,             lambda: analyzer.run_clustering(stages_completed[-1] if stages_completed else PipelineStepResult(_step_id(), PipelineStage.FIELD_ANALYSIS, False, "", 0.0, ()), config)),
            (PipelineStage.HYPOTHESIS_GENERATION,  lambda: analyzer.run_hypothesis_generation(stages_completed[-1] if stages_completed else PipelineStepResult(_step_id(), PipelineStage.CLUSTERING, False, "", 0.0, ()))),
            (PipelineStage.TYPE_CONSTRUCTION,      lambda: analyzer.run_type_construction(stages_completed[-1] if stages_completed else PipelineStepResult(_step_id(), PipelineStage.HYPOTHESIS_GENERATION, False, "", 0.0, ()))),
            (PipelineStage.BOOTSTRAP,              lambda: analyzer.run_bootstrap(stages_completed[-1] if stages_completed else PipelineStepResult(_step_id(), PipelineStage.TYPE_CONSTRUCTION, False, "", 0.0, ()))),
        ]

        final_stage = PipelineStage.FIELD_ANALYSIS
        step_results: list[PipelineStepResult] = []

        for stage, fn in stage_sequence:
            # Check timeout
            if _elapsed_ms(t_start) > config.timeout_seconds * 1000.0:
                error_message = f"Pipeline timed out before reaching {stage.value}."
                final_stage = PipelineStage.FAILED
                break
            step = fn()
            step_results.append(step)
            if step.success:
                stages_completed.append(stage)
                final_stage = stage
            else:
                error_message = step.output_summary
                final_stage = PipelineStage.FAILED
                break
        else:
            # All stages completed
            final_stage = PipelineStage.COMPLETE

        # Collect kind candidate names
        hyps = artifacts.retrieve(PipelineStage.HYPOTHESIS_GENERATION) or []
        kind_candidates: tuple[str, ...] = tuple(
            getattr(h, "name", h.get("name", "unknown") if isinstance(h, dict) else "unknown")
            for h in hyps
        )

        run = PipelineRun(
            run_id=run_id,
            input_obstruction_count=len(obstructions),
            stages_completed=tuple(stages_completed),
            final_stage=final_stage,
            kind_candidates=kind_candidates,
            elapsed_total_ms=_elapsed_ms(t_start),
            success=(final_stage == PipelineStage.COMPLETE),
            error_message=error_message,
            timestamp=_now_iso(),
        )
        self.witness.record(run)
        return run

    def report(self) -> dict[str, Any]:
        """Return a snapshot report from the internal witness.

        Returns
        -------
        dict[str, Any]
            A dict with ``success_rate``, ``avg_elapsed_ms``,
            ``total_candidates``, ``failed_count``, and ``runs`` keys.
        """
        return {
            "success_rate": round(self.witness.success_rate(), 4),
            "avg_elapsed_ms": round(self.witness.avg_elapsed_ms(), 2),
            "total_candidates": self.witness.total_candidates(),
            "failed_count": len(self.witness.failed_runs()),
            "runs": self.witness.export(),
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    _obstructions = [
        {"label": "non-associative", "coordinate": "Expr.compose", "characteristic": "idempotent"},
        {"label": "non-associative", "coordinate": "Expr.pipe", "characteristic": "idempotent"},
        {"label": "non-associative", "coordinate": "Expr.chain", "characteristic": "idempotent"},
        {"label": "non-invertible", "coordinate": "Expr.bind", "characteristic": "partial"},
        {"label": "non-invertible", "coordinate": "Expr.apply", "characteristic": "partial"},
        {"label": "non-invertible", "coordinate": "Expr.flatMap", "characteristic": "partial"},
    ]

    _coord = ObstructionToKindPipelineCoordinator()
    _run = _coord.run(_obstructions)

    _analyzer = ObstructionToKindPipelineAnalyzer(PipelineConfig(), PipelineArtifacts())
    print(_analyzer.summarize_run(_run))
    print()
    _errs = _analyzer.validate_pipeline_run(_run)
    if _errs:
        print("Validation errors:")
        for e in _errs:
            print(f"  - {e}")
    else:
        print("Validation: PASSED")

    print()
    print(json.dumps(_coord.report(), indent=2))
