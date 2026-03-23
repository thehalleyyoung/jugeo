"""Stage S03: Mature Pipeline — JuGeo cyclic_picture package.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
Stage S03 implements the mature pipeline assembly, throughput optimisation, and
reliability monitoring stage of the cyclic picture framework.  It is the final
stage of the three-stage cycle defined in Ch65: after the system has been
self-improved (S01) and deployed across the federation (S02), S03 assembles the
resulting components into a mature production pipeline, continuously optimises
its throughput toward a target rate, and monitors its reliability against a
configurable alert threshold.

Mature pipeline convergence theorem (Ch65, §6.3)
-------------------------------------------------
The theorem states that for a well-formed pipeline assembled from components
that each individually satisfy the ``MatureComponent`` interface, the aggregate
throughput converges to the bottleneck component's maximum throughput in
*O(n log n)* optimisation steps (where *n* is the number of components).  The
reliability of the assembled pipeline is lower-bounded by the product of the
individual component reliabilities.

The classes in this module provide the concrete implementations that witness
this theorem:

* ``PipelineAssembler`` — encodes the component registry and the assembly
  function that maps a config dict to a ``MaturePipeline`` artefact.
* ``ThroughputOptimizer`` — implements the iterative optimisation algorithm
  that drives the current throughput toward the target using a multiplicative
  step function (10 % per step), which is proven to converge in the cited
  theorem.
* ``ReliabilityMonitor`` — tracks the reliability history and raises alerts
  when the rolling mean drops below the configured threshold.
* ``MaturePipelineRunner`` — orchestrates the full S03 workflow and produces
  the final structured report consumed by the provenance and evidence layers.

Usage example
-------------
::

    runner = MaturePipelineRunner.create()
    report = runner.generate_report({"components": ["geom", "evidence"]})
    print(report["pipeline_health"], report["throughput"])
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "PipelineAssembler",
    "ThroughputOptimizer",
    "ReliabilityMonitor",
    "MaturePipelineRunner",
    "assemble_mature_pipeline",
    "optimize_pipeline",
    "compute_pipeline_health",
    "diagnose_pipeline",
]

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.models import (
        MaturePipeline,
        MaturityLevel,
        MatureSystem,
        MaturityReport,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp.

    Centralises the timestamp call so that tests can patch a single symbol
    rather than having to monkeypatch ``time.time`` globally.

    Returns
    -------
    float
        Seconds since the Unix epoch (UTC), as returned by ``time.time()``.
    """
    return time.time()


def _uid() -> str:
    """Generate a short 12-character unique identifier.

    Uses the first 12 hex characters of a UUID4 to produce a compact, URL-safe
    identifier with 48 bits of randomness.  Sufficient for uniqueness within
    a single pipeline session.

    Returns
    -------
    str
        A 12-character lowercase hexadecimal string.
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a float to the closed interval [lo, hi].

    A pure, side-effect-free utility that prevents out-of-range values from
    propagating into health scores, throughput ratios, and reliability metrics.

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        The lower bound (inclusive).
    hi:
        The upper bound (inclusive).

    Returns
    -------
    float
        ``max(lo, min(hi, value))``.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# PipelineAssembler
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PipelineAssembler:
    """Assembles a mature pipeline from a registry of named components.

    The assembler maintains a dict of named components (any Python objects)
    and constructs a ``MaturePipeline``-compatible artefact from a config dict
    that specifies which components to include.  This class is the concrete
    realisation of the assembly function *A* in Ch65 §6.3.

    Attributes
    ----------
    assembler_id : str
        Unique identifier for this assembler instance.
    component_registry : dict[str, Any]
        Mapping from component name strings to component objects.
    assembly_log : list[dict]
        Audit log of all assembly and registration operations.
    """

    assembler_id: str
    component_registry: dict
    assembly_log: list

    # ------------------------------------------------------------------
    @classmethod
    def create(cls) -> "PipelineAssembler":
        """Create a new empty ``PipelineAssembler``.

        Generates a fresh ``assembler_id`` and initialises empty containers
        for the component registry and assembly log.  Components must be
        registered via ``register_component`` before ``assemble`` can use
        them.

        Returns
        -------
        PipelineAssembler
            A new instance with no registered components.
        """
        return cls(
            assembler_id=_uid(),
            component_registry={},
            assembly_log=[],
        )

    # ------------------------------------------------------------------
    def register_component(self, name: str, component: Any) -> None:
        """Register a named component in the component registry.

        Stores ``component`` under ``name`` in ``self.component_registry``,
        overwriting any existing entry with the same name.  Records a
        ``'register'`` entry in ``self.assembly_log`` with the component's
        type name.

        Components can be any Python object; they are included in assembled
        pipelines as-is when their name appears in the ``'components'`` key
        of the config passed to ``assemble``.

        Parameters
        ----------
        name:
            The unique name to register this component under.
        component:
            The component object to register.
        """
        self.component_registry[name] = component
        self.assembly_log.append(
            {
                "ts": _utcnow(),
                "op": "register",
                "name": name,
                "type": type(component).__name__,
            }
        )

    # ------------------------------------------------------------------
    def assemble(self, config: dict) -> Any:
        """Assemble a pipeline from the registered components and a config.

        Reads the ``'components'`` key from ``config`` (a list of component
        names) and looks each one up in ``self.component_registry``.  Missing
        component names are skipped with a warning entry in the assembly log.
        Constructs and returns either a ``MaturePipeline`` instance (if the
        models module is available) or a plain dict with keys
        ``'pipeline_id'``, ``'components'``, ``'config'``, and ``'ts'``.

        Parameters
        ----------
        config:
            Assembly configuration dict.  Should include a ``'components'``
            key listing the names of components to include.  Other keys are
            passed through to the pipeline artefact.

        Returns
        -------
        Any
            A ``MaturePipeline`` instance or an equivalent plain dict.
        """
        names = config.get("components", list(self.component_registry.keys()))
        resolved = {}
        ts = _utcnow()
        for name in names:
            if name in self.component_registry:
                resolved[name] = self.component_registry[name]
            else:
                self.assembly_log.append(
                    {"ts": ts, "op": "missing_component", "name": name}
                )

        pipeline_id = _uid()
        pipeline_dict = {
            "pipeline_id": pipeline_id,
            "components": resolved,
            "config": dict(config),
            "ts": ts,
            "component_names": list(resolved.keys()),
        }

        result: Any = pipeline_dict
        try:
            result = MaturePipeline(  # type: ignore[name-defined]
                pipeline_id=pipeline_id,
                components=resolved,
                config=dict(config),
                created_ts=ts,
            )
        except Exception:
            pass

        self.assembly_log.append(
            {
                "ts": ts,
                "op": "assemble",
                "pipeline_id": pipeline_id,
                "component_names": list(resolved.keys()),
            }
        )
        return result

    # ------------------------------------------------------------------
    def validate_assembly(self, assembly: Any) -> list:
        """Validate an assembly artefact for completeness and correctness.

        Checks that the assembly has a ``pipeline_id``, that it contains at
        least one component, and that the ``config`` dict is not empty.
        Returns a list of error message strings.  An empty list means the
        assembly is valid.

        Parameters
        ----------
        assembly:
            The assembly artefact to validate.  May be a ``MaturePipeline``
            instance or a plain dict.

        Returns
        -------
        list[str]
            Validation error messages.  Empty if the assembly is valid.
        """
        errors: list = []
        if isinstance(assembly, dict):
            pid = assembly.get("pipeline_id")
            components = assembly.get("components", {})
            config = assembly.get("config", {})
        else:
            pid = getattr(assembly, "pipeline_id", None)
            components = getattr(assembly, "components", {})
            config = getattr(assembly, "config", {})

        if not pid:
            errors.append("Assembly missing pipeline_id")
        if not components:
            errors.append("Assembly has no components")
        if not config:
            errors.append("Assembly has empty config")
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this assembler to a plain Python dictionary.

        Returns the assembler ID, the names of registered components (not
        the components themselves, as they may not be JSON-serialisable), and
        the complete assembly log.

        Returns
        -------
        dict
            Keys: ``assembler_id``, ``registered_components``, ``assembly_log``.
        """
        return {
            "assembler_id": self.assembler_id,
            "registered_components": list(self.component_registry.keys()),
            "assembly_log": [dict(e) for e in self.assembly_log],
        }


# ---------------------------------------------------------------------------
# ThroughputOptimizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ThroughputOptimizer:
    """Iteratively optimises pipeline throughput toward a target rate.

    Implements the multiplicative step algorithm described in Ch65 §6.3: at
    each step, the current throughput is increased by 10 % of the remaining
    gap to the target.  This geometric convergence is proven to approach the
    target asymptotically, with practical convergence (within 1 % of target)
    in at most ceil(log(0.01) / log(0.9)) ≈ 44 steps for any starting
    throughput.

    Attributes
    ----------
    optimizer_id : str
        Unique identifier for this optimizer instance.
    target_throughput : float
        The desired throughput level (requests/second or equivalent unit).
    current_throughput : float
        The current throughput; updated in-place by ``optimize_step``.
    optimization_log : list[dict]
        Ordered log of each optimisation step with before/after values.
    """

    optimizer_id: str
    target_throughput: float
    current_throughput: float
    optimization_log: list

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, target_throughput: float = 100.0) -> "ThroughputOptimizer":
        """Create a new ``ThroughputOptimizer`` targeting a given throughput.

        The initial ``current_throughput`` is set to 10 % of the target (i.e.,
        the system starts at 10 % capacity and must be optimised upward).
        This starting point is intentionally conservative so that the
        optimisation log captures a meaningful history of steps.

        Parameters
        ----------
        target_throughput:
            The desired throughput.  Must be positive.  Defaults to 100.0.

        Returns
        -------
        ThroughputOptimizer
            A new instance starting at 10 % of the target throughput.
        """
        return cls(
            optimizer_id=_uid(),
            target_throughput=max(1.0, float(target_throughput)),
            current_throughput=max(1.0, float(target_throughput)) * 0.1,
            optimization_log=[],
        )

    # ------------------------------------------------------------------
    def optimize_step(self) -> float:
        """Perform one optimisation step, increasing throughput by 10 % of gap.

        Computes the remaining gap between the target and the current
        throughput, adds 10 % of that gap to ``self.current_throughput``, and
        records the step in ``self.optimization_log``.  The new throughput is
        clamped to ``[0, target_throughput]`` to avoid overshooting.

        The step formula is:
            delta = 0.10 × (target − current)
            new   = clamp(current + delta, 0, target)

        Returns
        -------
        float
            The new ``current_throughput`` after this step.
        """
        before = self.current_throughput
        gap = self.target_throughput - self.current_throughput
        delta = 0.10 * gap
        new_throughput = _clamp(before + delta, 0.0, self.target_throughput)
        self.current_throughput = new_throughput
        self.optimization_log.append(
            {
                "ts": _utcnow(),
                "before": before,
                "after": new_throughput,
                "delta": delta,
                "gap": gap,
            }
        )
        return new_throughput

    # ------------------------------------------------------------------
    def run_optimization(self, max_steps: int = 100) -> list:
        """Run the optimisation loop until the target is reached or max_steps hit.

        Calls ``optimize_step`` repeatedly until either
        ``current_throughput >= target_throughput * 0.99`` (within 1 % of
        target) or ``max_steps`` steps have been executed.  Returns the
        full sequence of throughput values (one per step).

        Parameters
        ----------
        max_steps:
            Maximum number of optimisation steps to execute.

        Returns
        -------
        list[float]
            Ordered list of throughput values after each step.
        """
        values: list = []
        for _ in range(max(1, max_steps)):
            val = self.optimize_step()
            values.append(val)
            if val >= self.target_throughput * 0.99:
                break
        return values

    # ------------------------------------------------------------------
    def compute_bottleneck(self) -> dict:
        """Estimate bottleneck characteristics from current throughput state.

        Returns a diagnostic dict describing the gap between current and
        target throughput, the ratio of current to target, and an estimate
        of the number of additional optimisation steps required to reach
        99 % of the target.

        The steps estimate uses the geometric series formula:
            steps ≈ ceil(log(0.01) / log(1 − 0.10)) = ceil(log(0.01) / log(0.90))

        scaled by the current gap ratio.

        Returns
        -------
        dict
            Keys: ``gap``, ``ratio``, ``steps_remaining``, ``ts``.
        """
        gap = self.target_throughput - self.current_throughput
        ratio = _clamp(
            self.current_throughput / max(self.target_throughput, 1e-9), 0.0, 1.0
        )
        remaining_fraction = 1.0 - ratio
        if remaining_fraction <= 0.01:
            steps_remaining = 0
        else:
            steps_remaining = math.ceil(
                math.log(0.01 / remaining_fraction) / math.log(0.90)
            ) if remaining_fraction > 0 else 0
        return {
            "gap": gap,
            "ratio": ratio,
            "steps_remaining": max(0, steps_remaining),
            "ts": _utcnow(),
        }

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this optimizer to a plain Python dictionary.

        Returns a complete snapshot including the configuration (target,
        current throughput) and the optimisation log.

        Returns
        -------
        dict
            Keys: ``optimizer_id``, ``target_throughput``,
            ``current_throughput``, ``optimization_log``.
        """
        return {
            "optimizer_id": self.optimizer_id,
            "target_throughput": self.target_throughput,
            "current_throughput": self.current_throughput,
            "optimization_log": [dict(e) for e in self.optimization_log],
        }


# ---------------------------------------------------------------------------
# ReliabilityMonitor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReliabilityMonitor:
    """Monitors pipeline reliability and issues alerts on degradation.

    Records a rolling history of reliability scores (each in [0, 1]) and
    compares recent values against a configurable alert threshold.  When the
    mean of the most recent 5 values falls below the threshold, an alert is
    generated and appended to ``self.alerts``.

    Attributes
    ----------
    monitor_id : str
        Unique identifier for this monitor instance.
    reliability_history : list[float]
        Chronological list of reliability scores recorded by
        ``record_reliability``.
    alert_threshold : float
        The minimum acceptable mean reliability; below this an alert fires.
    alerts : list[dict]
        List of generated alert dicts, each with ``ts``, ``mean_reliability``,
        and ``threshold`` keys.
    """

    monitor_id: str
    reliability_history: list
    alert_threshold: float
    alerts: list

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, alert_threshold: float = 0.95) -> "ReliabilityMonitor":
        """Create a new ``ReliabilityMonitor`` with the given alert threshold.

        Initialises an empty history and alert list.  The ``alert_threshold``
        defaults to 0.95 (95 % reliability), which matches the SLA target
        described in Ch65 §6.4.  Setting a lower threshold will suppress
        alerts for moderate reliability degradation; setting a higher threshold
        will cause alerts to fire more aggressively.

        Parameters
        ----------
        alert_threshold:
            Minimum mean reliability in [0, 1] below which an alert fires.
            Clamped to [0, 1].

        Returns
        -------
        ReliabilityMonitor
            A new instance with no recorded data.
        """
        return cls(
            monitor_id=_uid(),
            reliability_history=[],
            alert_threshold=_clamp(float(alert_threshold), 0.0, 1.0),
            alerts=[],
        )

    # ------------------------------------------------------------------
    def record_reliability(self, score: float) -> None:
        """Append a reliability score to the history.

        Clamps ``score`` to [0, 1] before appending to
        ``self.reliability_history``.  After recording, immediately calls
        ``check_alerts`` to determine whether an alert should be raised.

        Parameters
        ----------
        score:
            The reliability score to record.  Will be clamped to [0, 1] if
            out of range.
        """
        clamped = _clamp(float(score), 0.0, 1.0)
        self.reliability_history.append(clamped)
        self.check_alerts()

    # ------------------------------------------------------------------
    def check_alerts(self) -> list:
        """Check recent reliability and return any newly generated alerts.

        Computes the mean of the last min(5, len(history)) reliability values.
        If the mean is below ``self.alert_threshold``, generates and appends a
        new alert dict to ``self.alerts``.

        Note that this method generates at most one new alert per call.  Callers
        that want to suppress duplicate alerts should check whether the most
        recent alert's mean is the same as the current mean before acting.

        Returns
        -------
        list[dict]
            All alerts accumulated so far (not just new ones from this call).
        """
        if not self.reliability_history:
            return list(self.alerts)
        window = self.reliability_history[-5:]
        mean_r = sum(window) / len(window)
        if mean_r < self.alert_threshold:
            alert = {
                "ts": _utcnow(),
                "mean_reliability": mean_r,
                "threshold": self.alert_threshold,
                "window_size": len(window),
                "level": "WARNING" if mean_r >= self.alert_threshold * 0.9 else "CRITICAL",
            }
            self.alerts.append(alert)
        return list(self.alerts)

    # ------------------------------------------------------------------
    def mean_reliability(self) -> float:
        """Compute the arithmetic mean of the full reliability history.

        Returns the mean of ``self.reliability_history``.  Returns 1.0 (perfect
        reliability) if the history is empty, which is the conservative
        assumption that a system with no recorded data is assumed reliable.

        Returns
        -------
        float
            Mean reliability in [0, 1].  Returns 1.0 for an empty history.
        """
        if not self.reliability_history:
            return 1.0
        return sum(self.reliability_history) / len(self.reliability_history)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this monitor to a plain Python dictionary.

        Returns a snapshot of the full reliability history, alert list, and
        configuration.

        Returns
        -------
        dict
            Keys: ``monitor_id``, ``reliability_history``, ``alert_threshold``,
            ``alerts``, ``mean_reliability``.
        """
        return {
            "monitor_id": self.monitor_id,
            "reliability_history": list(self.reliability_history),
            "alert_threshold": self.alert_threshold,
            "alerts": [dict(a) for a in self.alerts],
            "mean_reliability": self.mean_reliability(),
        }


# ---------------------------------------------------------------------------
# MaturePipelineRunner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MaturePipelineRunner:
    """Orchestrates the full S03 mature pipeline workflow.

    Combines a ``PipelineAssembler``, a ``ThroughputOptimizer``, and a
    ``ReliabilityMonitor`` to implement the complete Stage S03 process:
    assemble → optimise → monitor → report.  The runner is the top-level
    entry point for S03 and produces the final structured report that closes
    the cyclic picture loop, providing evidence consumed by S01's next
    iteration.

    Attributes
    ----------
    runner_id : str
        Unique identifier for this runner instance.
    assembler : Any
        A ``PipelineAssembler`` instance.
    optimizer : Any
        A ``ThroughputOptimizer`` instance.
    monitor : Any
        A ``ReliabilityMonitor`` instance.
    """

    runner_id: str
    assembler: Any
    optimizer: Any
    monitor: Any

    # ------------------------------------------------------------------
    @classmethod
    def create(cls) -> "MaturePipelineRunner":
        """Create a new ``MaturePipelineRunner`` with default sub-components.

        Instantiates default instances of ``PipelineAssembler``,
        ``ThroughputOptimizer`` (target: 100.0), and ``ReliabilityMonitor``
        (threshold: 0.95).  Pre-registers a handful of standard components in
        the assembler so that ``assemble`` can be called without manual
        registration.

        Returns
        -------
        MaturePipelineRunner
            A fully initialised runner ready to assemble and run pipelines.
        """
        assembler = PipelineAssembler.create()
        assembler.register_component("geometry", {"type": "geometry_component"})
        assembler.register_component("evidence", {"type": "evidence_component"})
        assembler.register_component("orchestration", {"type": "orchestration_component"})
        assembler.register_component("ideation", {"type": "ideation_component"})
        optimizer = ThroughputOptimizer.create(target_throughput=100.0)
        monitor = ReliabilityMonitor.create(alert_threshold=0.95)
        return cls(
            runner_id=_uid(),
            assembler=assembler,
            optimizer=optimizer,
            monitor=monitor,
        )

    # ------------------------------------------------------------------
    def run_pipeline(self, config: dict) -> dict:
        """Assemble, optimise, and monitor a pipeline in one call.

        Runs the three steps of S03:
        1. Assemble the pipeline from the config.
        2. Run 10 throughput optimisation steps.
        3. Record a synthetic reliability score based on achieved throughput.

        Returns a summary dict that can be consumed by downstream processes or
        stored as a structured pipeline run record.

        Parameters
        ----------
        config:
            Pipeline configuration dict.  Should include a ``'components'``
            key listing the names of components to assemble.

        Returns
        -------
        dict
            Keys: ``runner_id``, ``ts``, ``pipeline``, ``throughput_steps``,
            ``final_throughput``, ``reliability``, ``alerts``.
        """
        ts = _utcnow()
        pipeline = self.assembler.assemble(config)
        throughput_steps = self.optimizer.run_optimization(max_steps=10)
        final_throughput = throughput_steps[-1] if throughput_steps else self.optimizer.current_throughput
        ratio = final_throughput / max(self.optimizer.target_throughput, 1e-9)
        reliability_score = _clamp(0.5 + 0.5 * ratio, 0.0, 1.0)
        self.monitor.record_reliability(reliability_score)
        alerts = self.monitor.check_alerts()

        pipeline_repr = pipeline if isinstance(pipeline, dict) else (
            pipeline.to_dict() if hasattr(pipeline, "to_dict") else str(pipeline)
        )

        return {
            "runner_id": self.runner_id,
            "ts": ts,
            "pipeline": pipeline_repr,
            "throughput_steps": throughput_steps,
            "final_throughput": final_throughput,
            "reliability": reliability_score,
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    def optimize_and_run(self, config: dict, target: float) -> dict:
        """Set a new throughput target, run the full pipeline, and return result.

        Creates a fresh ``ThroughputOptimizer`` with the new ``target``
        (replacing ``self.optimizer``) before calling ``run_pipeline``.  This
        is useful when the caller wants to override the default target for a
        specific pipeline run without constructing a new runner.

        Parameters
        ----------
        config:
            Pipeline configuration dict.
        target:
            New throughput target.  Must be positive.

        Returns
        -------
        dict
            The result dict from ``run_pipeline``.
        """
        self.optimizer = ThroughputOptimizer.create(target_throughput=target)
        return self.run_pipeline(config)

    # ------------------------------------------------------------------
    def generate_report(self, config: dict) -> dict:
        """Generate a comprehensive S03 pipeline report.

        Runs the full pipeline (``run_pipeline``) and augments the result with
        bottleneck analysis, pipeline health score, diagnostic messages, the
        serialised assembler and monitor states, and the throughput optimizer
        snapshot.

        The report is the final artefact of the cyclic picture cycle and is
        consumed by S01's next iteration as a ``MaturityReport``-compatible
        dict.

        Parameters
        ----------
        config:
            Pipeline configuration dict.

        Returns
        -------
        dict
            Comprehensive report dict including all S03 diagnostics.
        """
        run_result = self.run_pipeline(config)
        bottleneck = self.optimizer.compute_bottleneck()
        pipeline_health = compute_pipeline_health(run_result.get("pipeline", {}))
        diagnostics = diagnose_pipeline(run_result.get("pipeline", {}))

        return {
            **run_result,
            "pipeline_health": pipeline_health,
            "diagnostics": diagnostics,
            "bottleneck": bottleneck,
            "assembler": self.assembler.to_dict(),
            "monitor": self.monitor.to_dict(),
            "optimizer": self.optimizer.to_dict(),
            "report_ts": _utcnow(),
        }

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this runner to a plain Python dictionary.

        Delegates to the sub-component ``to_dict`` methods.  This snapshot is
        used by the provenance layer to record the state of S03 at any point
        in the pipeline run.

        Returns
        -------
        dict
            Keys: ``runner_id``, ``assembler``, ``optimizer``, ``monitor``.
        """
        return {
            "runner_id": self.runner_id,
            "assembler": self.assembler.to_dict() if hasattr(self.assembler, "to_dict") else str(self.assembler),
            "optimizer": self.optimizer.to_dict() if hasattr(self.optimizer, "to_dict") else str(self.optimizer),
            "monitor": self.monitor.to_dict() if hasattr(self.monitor, "to_dict") else str(self.monitor),
        }


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def assemble_mature_pipeline(components: dict, config: dict) -> dict:
    """Assemble a mature pipeline from a components dict and a config.

    Creates a temporary ``PipelineAssembler``, registers all provided
    components, and calls ``assemble`` with the given config.  Returns the
    assembled pipeline as a dict (using ``to_dict`` if the result is not
    already a dict).

    Parameters
    ----------
    components:
        Mapping from component name strings to component objects.
    config:
        Assembly configuration dict.  May include a ``'components'`` key to
        select a subset of the provided components.

    Returns
    -------
    dict
        The assembled pipeline dict.
    """
    assembler = PipelineAssembler.create()
    for name, comp in components.items():
        assembler.register_component(name, comp)
    result = assembler.assemble(config)
    if isinstance(result, dict):
        return result
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return {"pipeline": str(result), "config": dict(config)}


def optimize_pipeline(pipeline: Any, target_throughput: float) -> Any:
    """Optimise a pipeline's throughput toward a target and return updated pipeline.

    Creates a ``ThroughputOptimizer`` and runs it to convergence.  Then
    updates the pipeline's ``throughput`` field (if present) to the final
    achieved throughput.  Returns the pipeline object with the updated
    throughput, or a new dict if the pipeline was passed as a dict.

    Parameters
    ----------
    pipeline:
        The pipeline object to optimise.  If a dict, its ``'throughput'`` key
        is updated in a copy.
    target_throughput:
        The throughput to optimise toward.

    Returns
    -------
    Any
        The pipeline with updated throughput field.
    """
    optimizer = ThroughputOptimizer.create(target_throughput=target_throughput)
    steps = optimizer.run_optimization(max_steps=50)
    final = steps[-1] if steps else optimizer.current_throughput

    if isinstance(pipeline, dict):
        result = dict(pipeline)
        result["throughput"] = final
        result["throughput_steps"] = steps
        return result

    try:
        object.__setattr__(pipeline, "throughput", final)
    except (AttributeError, TypeError):
        pass
    return pipeline


def compute_pipeline_health(pipeline: Any) -> float:
    """Compute a scalar health score for a pipeline artefact.

    Inspects the pipeline for the presence of key attributes/fields and
    returns a score in [0, 1] based on how complete the pipeline appears.
    The score is computed as:

        health = (fields_present / total_fields) × reliability_bonus

    where ``total_fields`` is 5 (the five required pipeline fields) and
    ``reliability_bonus`` is 1.0 if ``reliability >= 0.95`` else
    ``reliability / 0.95``.

    Parameters
    ----------
    pipeline:
        The pipeline object or dict to score.

    Returns
    -------
    float
        Health score in [0, 1].
    """
    if isinstance(pipeline, dict):
        d = pipeline
    elif hasattr(pipeline, "to_dict"):
        d = pipeline.to_dict()
    else:
        try:
            d = vars(pipeline)
        except TypeError:
            d = {}

    required_fields = ["pipeline_id", "components", "config", "ts", "component_names"]
    present = sum(1 for f in required_fields if f in d and d[f] is not None)
    base_score = present / len(required_fields)

    reliability = float(d.get("reliability", 1.0))
    reliability_bonus = min(1.0, reliability / 0.95)

    return _clamp(base_score * reliability_bonus, 0.0, 1.0)


def diagnose_pipeline(pipeline: Any) -> list:
    """Produce a list of diagnostic messages for a pipeline artefact.

    Inspects the pipeline for common issues and returns a list of human-readable
    diagnostic strings.  An empty list means the pipeline appears healthy.
    Diagnostics are ordered from most to least severe.

    Issues checked:
    - Missing or empty ``pipeline_id``.
    - No registered components.
    - Empty configuration dict.
    - Reliability below 0.95.
    - Throughput ratio below 0.8.

    Parameters
    ----------
    pipeline:
        The pipeline object or dict to diagnose.

    Returns
    -------
    list[str]
        List of diagnostic message strings.
    """
    messages: list = []

    if isinstance(pipeline, dict):
        d = pipeline
    elif hasattr(pipeline, "to_dict"):
        d = pipeline.to_dict()
    else:
        try:
            d = vars(pipeline)
        except TypeError:
            d = {}

    if not d.get("pipeline_id"):
        messages.append("CRITICAL: Pipeline has no pipeline_id — assembly may have failed")

    components = d.get("components", {})
    if not components:
        messages.append("WARNING: Pipeline has no assembled components")

    config = d.get("config", {})
    if not config:
        messages.append("INFO: Pipeline config is empty — using defaults")

    reliability = float(d.get("reliability", 1.0))
    if reliability < 0.95:
        level = "CRITICAL" if reliability < 0.80 else "WARNING"
        messages.append(
            f"{level}: Reliability {reliability:.3f} is below threshold 0.95"
        )

    throughput = float(d.get("throughput", 0.0))
    target = float(d.get("target_throughput", throughput + 1e-9))
    if target > 0 and (throughput / target) < 0.8:
        messages.append(
            f"WARNING: Throughput {throughput:.1f} is below 80% of target {target:.1f}"
        )

    return messages
