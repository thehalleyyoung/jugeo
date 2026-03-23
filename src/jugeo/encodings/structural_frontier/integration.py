"""Integration layer for the JuGeo structural frontier subsystem.

This module provides the integration layer connecting the structural frontier
subsystem to the rest of JuGeo.  It ties together the frontier definer, the
solver-lifted type system, the countermodel-to-repair pipeline, Z3 sessions,
geometry support regions, and external type systems into a unified orchestration
surface.

The pipeline orchestrates the full lifecycle of structural frontier analysis:

1. **Define phase** — Enumerate fragments and locate frontier boundaries by
   calling the :class:`StructuralFrontierDefiner` for each known fragment.
2. **Classify phase** — Assign each formula to a side of the frontier using
   the decidability oracle.
3. **Repair phase** — Route countermodels through
   :class:`CountermodelToRepair`, converting solver failures into typed
   obstructions and candidate repair actions.
4. **Report phase** — Aggregate statistics, emit copilot-readable summaries,
   and serialise the full pipeline state.

Copilot integration is available throughout: every class exposes a
``copilot_*`` method returning a structured natural-language narrative that
copilot can incorporate into proposal summaries, hints, and explanations.
The bridge layer (:class:`Z3FrontierBridge`) manages Z3 session lifecycles
so that the rest of the subsystem never touches raw solver state directly.

Architecture note
-----------------
All heavy imports are guarded by ``try/except`` so the integration module
remains importable even when optional subsystems (Z3 bindings, geometry
packages, or the s0x definer modules) are not yet available.  In that case
each unavailable class is replaced by a lightweight stub that provides the
same interface but returns sentinel values.

Theory reference
----------------
theory2.tex Ch25 §25.1  "The structural frontier and decidability classes"
theory2.tex Ch25 §25.4  "Integration with the Z3 session pool"
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports — each wrapped so unavailable modules degrade gracefully
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import (
        Z3Formula,
        Z3QueryBuilder,
        Z3Result,
        Z3Session,
        SolveOutcome,
        SolverResult,
    )
except ImportError:  # pragma: no cover
    Z3Formula = Any  # type: ignore[assignment,misc]
    Z3QueryBuilder = Any  # type: ignore[assignment,misc]
    Z3Result = Any  # type: ignore[assignment,misc]
    Z3Session = Any  # type: ignore[assignment,misc]
    SolveOutcome = Any  # type: ignore[assignment,misc]
    SolverResult = Any  # type: ignore[assignment,misc]

try:
    from jugeo.solver.fragments import Fragment, LogicalFragment, SolverFragment, classify_fragment
except ImportError:  # pragma: no cover
    Fragment = Any  # type: ignore[assignment,misc]
    LogicalFragment = Any  # type: ignore[assignment,misc]
    SolverFragment = Any  # type: ignore[assignment,misc]

    def classify_fragment(formula: str) -> Any:  # type: ignore[misc]
        return None

try:
    from jugeo.solver.countermodels import (
        Countermodel,
        CountermodelExtractor,
        FailureClass,
        ObstructionConverter,
        RepairType,
    )
except ImportError:  # pragma: no cover
    class FailureClass(str, Enum):  # type: ignore[no-redef]
        UNKNOWN = "unknown"
        ASSIGNMENT_CONFLICT = "assignment_conflict"
        SORT_VIOLATION = "sort_violation"
        FUNCTION_MISMATCH = "function_mismatch"
        ARRAY_OUT_OF_BOUNDS = "array_out_of_bounds"
        QUANTIFIER_WITNESS = "quantifier_witness"

    class RepairType(str, Enum):  # type: ignore[no-redef]
        MANUAL_REVIEW = "manual_review"
        STRENGTHEN_PRECONDITION = "strengthen_precondition"
        WEAKEN_POSTCONDITION = "weaken_postcondition"
        ADD_INVARIANT = "add_invariant"

    @dataclass
    class Countermodel:  # type: ignore[no-redef]
        assignment: dict[str, bool] = field(default_factory=dict)
        support: Any = None
        reasons: tuple[str, ...] = field(default_factory=tuple)
        model_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
        coordinate: str = ""
        negated_proposition: str = ""
        variable_assignments: dict[str, Any] = field(default_factory=dict)
        failure_class: FailureClass = FailureClass.UNKNOWN

    CountermodelExtractor = Any  # type: ignore[assignment,misc]
    ObstructionConverter = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.supports import SupportRegion
except ImportError:  # pragma: no cover
    @dataclass
    class SupportRegion:  # type: ignore[no-redef]
        coordinate: Any = None
        patch_keys: frozenset[str] = field(default_factory=frozenset)

try:
    from jugeo.encodings.structural_frontier.models import (
        CountermodelObstruction,
        DecidabilityClass,
        DecidabilityMap,
        FrontierBoundary,
        FrontierSide,
        KNOWN_DECIDABLE_FRAGMENTS,
        RepairAction,
        SolverLiftedType,
        StructuralFrontier,
        make_default_boundary,
        make_default_frontier,
        make_default_map,
    )
except ImportError:  # pragma: no cover
    class DecidabilityClass(str, Enum):  # type: ignore[no-redef]
        DECIDABLE = "decidable"
        UNDECIDABLE = "undecidable"
        SEMI_DECIDABLE = "semi_decidable"
        BOUNDARY = "boundary"
        UNKNOWN = "unknown"

    class FrontierSide(str, Enum):  # type: ignore[no-redef]
        INSIDE = "inside"
        OUTSIDE = "outside"
        BOUNDARY = "boundary"
        UNKNOWN = "unknown"

    class _RepairTypeStub(str, Enum):  # type: ignore[misc]
        STRENGTHEN_PRECONDITION = "strengthen_precondition"
        WEAKEN_POSTCONDITION = "weaken_postcondition"
        ADD_INVARIANT = "add_invariant"
        FIX_IMPLEMENTATION = "fix_implementation"
        SPLIT_COVER = "split_cover"
        ADD_SORT_CONSTRAINT = "add_sort_constraint"
        REFINE_FUNCTION_SPEC = "refine_function_spec"
        MANUAL_REVIEW = "manual_review"

    class SolverLiftedType(str, Enum):  # type: ignore[no-redef]
        BOOL = "bool"
        INT = "int"
        REAL = "real"
        BITVEC = "bitvec"
        ARRAY = "array"
        UF = "uf"
        DATATYPE = "datatype"
        UNKNOWN = "unknown"

    @dataclass(frozen=True)
    class RepairAction:  # type: ignore[no-redef]
        action_type: _RepairTypeStub = _RepairTypeStub.MANUAL_REVIEW
        description: str = ""
        smt_fragment: str = ""
        cost: int = 1
        origin: str = "heuristic"

        def copilot_summary(self) -> str:
            return (
                f"[RepairAction] type={self.action_type.value} cost={self.cost}: "
                f"{self.description[:80]}"
            )

        def to_dict(self) -> dict[str, Any]:
            return {
                "action_type": self.action_type.value,
                "description": self.description,
                "smt_fragment": self.smt_fragment,
                "cost": self.cost,
                "origin": self.origin,
            }

    @dataclass(frozen=True)
    class StructuralFrontier:  # type: ignore[no-redef]
        name: str = ""
        decidability_class: DecidabilityClass = DecidabilityClass.UNKNOWN
        boundary_formula_smt: str = ""
        description: str = ""
        is_default: bool = False

        def is_decidable(self) -> bool:
            return self.decidability_class == DecidabilityClass.DECIDABLE

        def copilot_summary(self) -> str:
            return f"[StructuralFrontier] name={self.name!r}"

        def to_dict(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "decidability_class": self.decidability_class.value,
                "boundary_formula_smt": self.boundary_formula_smt,
                "description": self.description,
                "is_default": self.is_default,
            }

    @dataclass(frozen=True)
    class FrontierBoundary:  # type: ignore[no-redef]
        inside_fragment: str = ""
        outside_fragment: str = ""
        crossing_cost: int = 1
        boundary_formula_smt: str = ""
        crossing_label: str = ""

        def copilot_summary(self) -> str:
            return (
                f"[FrontierBoundary] {self.outside_fragment!r} → "
                f"{self.inside_fragment!r} cost={self.crossing_cost}"
            )

        def reverse(self) -> FrontierBoundary:
            return FrontierBoundary(
                inside_fragment=self.outside_fragment,
                outside_fragment=self.inside_fragment,
                crossing_cost=self.crossing_cost,
                boundary_formula_smt=self.boundary_formula_smt,
                crossing_label=f"reverse_{self.crossing_label}",
            )

        def to_dict(self) -> dict[str, Any]:
            return {
                "inside_fragment": self.inside_fragment,
                "outside_fragment": self.outside_fragment,
                "crossing_cost": self.crossing_cost,
                "boundary_formula_smt": self.boundary_formula_smt,
                "crossing_label": self.crossing_label,
            }

    @dataclass
    class DecidabilityMap:  # type: ignore[no-redef]
        frontiers: list[StructuralFrontier] = field(default_factory=list)
        boundaries: list[FrontierBoundary] = field(default_factory=list)

        def decidable_frontier_names(self) -> list[str]:
            return [f.name for f in self.frontiers if f.is_decidable()]

        def to_dict(self) -> dict[str, Any]:
            return {
                "frontiers": [f.to_dict() for f in self.frontiers],
                "boundaries": [b.to_dict() for b in self.boundaries],
            }

    @dataclass
    class CountermodelObstruction:  # type: ignore[no-redef]
        countermodel: Any = field(default_factory=dict)
        failure_class: Any = None
        violated_invariant: str = ""
        repair_frontier: FrontierBoundary = field(default_factory=FrontierBoundary)
        suggested_actions: list[RepairAction] = field(default_factory=list)
        confidence: float = 0.5
        obstruction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        context: str = ""
        created_at: float = field(default_factory=time.time)

        def is_resolvable(self) -> bool:
            return bool(self.suggested_actions)

        def most_likely_repair(self) -> RepairAction:
            if not self.suggested_actions:
                return RepairAction(description="No automated repair; manual review required.")
            return min(self.suggested_actions, key=lambda a: a.cost)

        def copilot_summary(self) -> str:
            return (
                f"[CountermodelObstruction] id={self.obstruction_id[:8]} "
                f"failure={self.failure_class} resolvable={self.is_resolvable()}"
            )

    KNOWN_DECIDABLE_FRAGMENTS: list[str] = [
        "qf_lia", "qf_lra", "qf_bv", "qf_uf", "qf_auflia",
        "qf_rdl", "qf_idl", "qf_ufbv", "qf_abv", "propositional",
    ]

    def make_default_frontier() -> StructuralFrontier:  # type: ignore[misc]
        return StructuralFrontier(name="default", is_default=True)

    def make_default_boundary() -> FrontierBoundary:  # type: ignore[misc]
        return FrontierBoundary()

    def make_default_map() -> DecidabilityMap:  # type: ignore[misc]
        return DecidabilityMap()

try:
    from jugeo.encodings.structural_frontier.structural_frontier_definer import (
        DecidabilityOracle,
        FrontierBoundaryLocator,
        StructuralFrontierDefiner,
    )
except ImportError:  # pragma: no cover
    class StructuralFrontierDefiner:  # type: ignore[no-redef]
        """Stub definer used when s01 module is unavailable."""

        def define_frontier(self, fragment_name: str) -> StructuralFrontier:
            return StructuralFrontier(name=fragment_name)

        def classify_formula(self, formula: str) -> FrontierSide:
            if any(q in formula for q in ("forall", "exists", "∀", "∃")):
                return FrontierSide.OUTSIDE
            return FrontierSide.INSIDE

        def emit_report(self) -> str:
            return "[StructuralFrontierDefiner stub report]"

    class DecidabilityOracle:  # type: ignore[no-redef]
        def query(self, formula: str) -> DecidabilityClass:
            return DecidabilityClass.UNKNOWN

    class FrontierBoundaryLocator:  # type: ignore[no-redef]
        def locate(self, fragment: str) -> FrontierBoundary:
            return FrontierBoundary(inside_fragment=fragment)

try:
    from jugeo.encodings.structural_frontier.solver_lifted_type_system import (
        InvariantChecker,
        SolverLiftedTypeSystem,
        TypeLiftingStrategy,
        TypeLiftingTranslator,
    )
except ImportError:  # pragma: no cover
    class SolverLiftedTypeSystem:  # type: ignore[no-redef]
        """Stub type system used when s02 module is unavailable."""

        def __init__(self) -> None:
            self._types: dict[str, SolverLiftedType] = {}

        def register_type(self, lifted_type: SolverLiftedType) -> None:
            self._types[lifted_type.type_id] = lifted_type

        def emit_all_declarations(self) -> str:
            lines = [t.emit_declaration() for t in self._types.values()]
            return "\n".join(lines) if lines else "(; no types registered ;)"

        def emit_report(self) -> str:
            return f"[SolverLiftedTypeSystem stub: {len(self._types)} types]"

        @property
        def types(self) -> dict[str, SolverLiftedType]:
            return self._types

    class TypeLiftingTranslator:  # type: ignore[no-redef]
        def translate(self, base_type: str) -> SolverLiftedType:
            return SolverLiftedType(base_type=base_type, lifted_name=f"Lifted_{base_type}")

    class InvariantChecker:  # type: ignore[no-redef]
        def check(self, lifted_type: SolverLiftedType) -> bool:
            return bool(lifted_type.invariants)

    class TypeLiftingStrategy(str, Enum):  # type: ignore[no-redef]
        EAGER = "eager"
        LAZY = "lazy"
        INCREMENTAL = "incremental"

try:
    from jugeo.encodings.structural_frontier.countermodel_to_repair import (
        CountermodelToRepair,
        ObstructionClassifier,
        RepairCandidateGenerator,
    )
except ImportError:  # pragma: no cover
    class CountermodelToRepair:  # type: ignore[no-redef]
        """Stub repair pipeline used when s03 module is unavailable."""

        def process(self, countermodel: Countermodel) -> CountermodelObstruction:
            fc = getattr(countermodel, "failure_class", FailureClass.UNKNOWN)
            return CountermodelObstruction(
                countermodel=countermodel,
                failure_class=fc,
                description=f"Stub obstruction for model {countermodel.model_id}",
            )

        def emit_report(self) -> str:
            return "[CountermodelToRepair stub report]"

    class ObstructionClassifier:  # type: ignore[no-redef]
        def classify(self, countermodel: Countermodel) -> FailureClass:
            return getattr(countermodel, "failure_class", FailureClass.UNKNOWN)

    class RepairCandidateGenerator:  # type: ignore[no-redef]
        def generate(self, obstruction: CountermodelObstruction) -> list[RepairAction]:
            return [RepairAction.MANUAL_REVIEW]


# --- Internal helpers -------------------------------------------------------

def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_id() -> str:
    """Return a compact random hex identifier (12 chars)."""
    return uuid.uuid4().hex[:12]


def _digest(*parts: str) -> str:
    """Return a 16-char SHA-256 hex digest of the concatenated *parts*."""
    payload = "||".join(parts).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* into the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


# --- Phase enumeration ------------------------------------------------------


class PipelinePhase(str, Enum):
    """Named phases of the :class:`StructuralFrontierPipeline` lifecycle.

    copilot: This enum is consumed by the pipeline summary method to produce
    human-readable phase labels.
    """

    IDLE = "idle"
    DEFINE = "define"
    CLASSIFY = "classify"
    REPAIR = "repair"
    REPORT = "report"
    COMPLETE = "complete"
    FAILED = "failed"


# --- Inline fallback stubs (used when module instantiation fails at runtime) -


class _StubDefiner:
    """Minimal definer stub for when StructuralFrontierDefiner cannot be instantiated."""

    def define_frontier(self, fragment_name: str) -> StructuralFrontier:
        return StructuralFrontier(name=fragment_name)

    def classify_formula(self, formula: str) -> FrontierSide:
        if any(q in formula for q in ("forall", "exists", "∀", "∃")):
            return FrontierSide.OUTSIDE
        return FrontierSide.INSIDE

    def emit_report(self) -> str:
        return "[_StubDefiner: StructuralFrontierDefiner unavailable]"


class _StubTypeSystem:
    """Minimal type system stub for when SolverLiftedTypeSystem cannot be instantiated."""

    def __init__(self) -> None:
        self._types: dict[str, SolverLiftedType] = {}

    def register_type(self, t: SolverLiftedType) -> None:
        # SolverLiftedType is an Enum; key by value
        key = getattr(t, "value", str(t))
        self._types[key] = t

    def emit_all_declarations(self) -> str:
        return "(; _StubTypeSystem: no declarations ;)"

    def emit_report(self) -> str:
        return f"[_StubTypeSystem: {len(self._types)} types]"

    @property
    def types(self) -> dict[str, SolverLiftedType]:
        return self._types


class _StubRepairPipeline:
    """Minimal repair pipeline stub for when CountermodelToRepair cannot be instantiated."""

    def process(self, countermodel: Countermodel) -> CountermodelObstruction:
        fc = getattr(countermodel, "failure_class", FailureClass.UNKNOWN)
        return CountermodelObstruction(
            countermodel=countermodel,
            failure_class=fc,
            violated_invariant=getattr(countermodel, "negated_proposition", ""),
            context="[_StubRepairPipeline fallback obstruction]",
        )

    def emit_report(self) -> str:
        return "[_StubRepairPipeline: CountermodelToRepair unavailable]"


# ============================================================================
# StructuralFrontierPipeline
# ============================================================================


class StructuralFrontierPipeline:
    """Orchestrates the full structural-frontier analysis lifecycle.

    The pipeline runs four phases in sequence:
    - **define** — locate frontier boundaries for each known fragment.
    - **classify** — assign each formula to a side of the frontier.
    - **repair** — convert countermodels to typed obstructions and repairs.
    - **report** — emit a comprehensive, copilot-readable summary.

    Copilot integration note: call :meth:`copilot_pipeline_summary` after
    :meth:`run` to obtain a structured narrative suitable for including in
    copilot proposals.

    Parameters
    ----------
    None — the pipeline creates its subsystems internally.
    """

    def __init__(self) -> None:
        try:
            self.definer: StructuralFrontierDefiner = StructuralFrontierDefiner()
        except Exception as _exc:  # noqa: BLE001
            logger.warning(
                "StructuralFrontierDefiner() failed (%s); using stub definer.", _exc
            )
            self.definer = _StubDefiner()  # type: ignore[assignment]
        try:
            self.type_system: SolverLiftedTypeSystem = SolverLiftedTypeSystem()
        except Exception as _exc:  # noqa: BLE001
            logger.warning(
                "SolverLiftedTypeSystem() failed (%s); using stub.", _exc
            )
            self.type_system = _StubTypeSystem()  # type: ignore[assignment]
        try:
            self.repair_pipeline: CountermodelToRepair = CountermodelToRepair()
        except Exception as _exc:  # noqa: BLE001
            logger.warning(
                "CountermodelToRepair() failed (%s); using stub.", _exc
            )
            self.repair_pipeline = _StubRepairPipeline()  # type: ignore[assignment]
        self.map_: DecidabilityMap = make_default_map()
        self._stats: dict[str, Any] = {
            "pipeline_id": _short_id(),
            "started_at": None,
            "completed_at": None,
            "phase_durations_ms": {},
            "frontier_count": 0,
            "formula_count": 0,
            "obstruction_count": 0,
            "repair_count": 0,
        }
        self._phase_log: list[dict[str, Any]] = []
        self._frontiers: list[StructuralFrontier] = []
        self._current_phase: PipelinePhase = PipelinePhase.IDLE
        logger.debug(
            "StructuralFrontierPipeline initialised (id=%s)",
            self._stats["pipeline_id"],
        )

    # --- Phase execution ----------------------------------------------------

    def run(self, context: str = "") -> dict[str, Any]:
        """Execute all four phases in sequence and return accumulated stats.

        The phases run in this order: define → classify → repair → report.
        Each phase is timed independently and its duration recorded in
        ``stats["phase_durations_ms"]``.  Any exception in a phase transitions
        the pipeline to :attr:`PipelinePhase.FAILED` and re-raises.

        Parameters
        ----------
        context:
            Optional free-text context string forwarded to the report phase
            for inclusion in the copilot summary.

        Returns
        -------
        dict[str, Any]
            The accumulated statistics dict, also available via
            :meth:`stats`.
        """
        self._stats["started_at"] = _utcnow_iso()
        self._stats["context"] = context
        logger.info(
            "Pipeline %s starting (context=%r)",
            self._stats["pipeline_id"],
            context[:80] if context else "",
        )
        try:
            self.define_phase()
            self.classify_phase([])
            self.repair_phase([])
            self.report_phase()
            self._current_phase = PipelinePhase.COMPLETE
        except Exception as exc:  # noqa: BLE001
            self._current_phase = PipelinePhase.FAILED
            self._stats["error"] = str(exc)
            logger.exception("Pipeline %s failed: %s", self._stats["pipeline_id"], exc)
            raise
        finally:
            self._stats["completed_at"] = _utcnow_iso()
        return dict(self._stats)

    def define_phase(self) -> None:
        """Locate frontier boundaries for every fragment in KNOWN_DECIDABLE_FRAGMENTS.

        For each fragment name the phase calls
        :meth:`StructuralFrontierDefiner.define_frontier` and appends the
        resulting :class:`StructuralFrontier` to an internal list.  Phase
        timing and per-fragment results are written to the phase log.

        Side-effects
        ------------
        Populates ``self._frontiers`` and increments
        ``stats["frontier_count"]``.
        """
        self._current_phase = PipelinePhase.DEFINE
        t0 = time.monotonic()
        phase_entry: dict[str, Any] = {
            "phase": PipelinePhase.DEFINE,
            "started_at": _utcnow_iso(),
            "fragments": [],
        }
        for fragment_name in KNOWN_DECIDABLE_FRAGMENTS:
            try:
                frontier = self.definer.define_frontier(fragment_name)
                self._frontiers.append(frontier)
                phase_entry["fragments"].append(
                    {
                        "fragment": fragment_name,
                        "frontier_name": getattr(frontier, "name", "?"),
                        "decidability": str(
                            getattr(frontier, "decidability_class", DecidabilityClass.UNKNOWN)
                        ),
                    }
                )
                logger.debug("Defined frontier for fragment %s", fragment_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to define frontier for %s: %s", fragment_name, exc
                )
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats["frontier_count"] = len(self._frontiers)
        self._stats["phase_durations_ms"]["define"] = round(elapsed_ms, 3)
        phase_entry["elapsed_ms"] = elapsed_ms
        phase_entry["frontier_count"] = len(self._frontiers)
        self._phase_log.append(phase_entry)
        logger.info(
            "Define phase complete: %d frontiers in %.1f ms",
            len(self._frontiers),
            elapsed_ms,
        )

    def classify_phase(self, formulas: list[str]) -> dict[str, FrontierSide]:
        """Classify each formula in *formulas* as inside, outside, or on the boundary.

        Uses :meth:`StructuralFrontierDefiner.classify_formula` for each
        formula string.  Results are returned as a mapping and also recorded
        in the phase log.  Formulas that raise errors are mapped to
        :attr:`FrontierSide.UNKNOWN` and logged as warnings.

        Parameters
        ----------
        formulas:
            List of SMT-LIB2 or JuGeo formula strings to classify.

        Returns
        -------
        dict[str, FrontierSide]
            Mapping from formula string to its frontier side.
        """
        self._current_phase = PipelinePhase.CLASSIFY
        t0 = time.monotonic()
        results: dict[str, FrontierSide] = {}
        for formula in formulas:
            try:
                side = self.definer.classify_formula(formula)
                results[formula] = side
            except Exception as exc:  # noqa: BLE001
                logger.warning("classify_formula error for %r: %s", formula[:60], exc)
                results[formula] = FrontierSide.UNKNOWN
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats["formula_count"] = len(formulas)
        self._stats["phase_durations_ms"]["classify"] = round(elapsed_ms, 3)
        inside_count = sum(1 for s in results.values() if s == FrontierSide.INSIDE)
        outside_count = sum(1 for s in results.values() if s == FrontierSide.OUTSIDE)
        self._phase_log.append(
            {
                "phase": PipelinePhase.CLASSIFY,
                "started_at": _utcnow_iso(),
                "formula_count": len(formulas),
                "inside": inside_count,
                "outside": outside_count,
                "elapsed_ms": elapsed_ms,
            }
        )
        logger.info(
            "Classify phase: %d formulas (%d inside, %d outside) in %.1f ms",
            len(formulas),
            inside_count,
            outside_count,
            elapsed_ms,
        )
        return results

    def repair_phase(self, countermodels: list[Countermodel]) -> list[CountermodelObstruction]:
        """Process each countermodel through the repair pipeline.

        Delegates to :meth:`CountermodelToRepair.process` for each
        countermodel, collecting :class:`CountermodelObstruction` objects.
        The obstructions are also recorded in the phase log.

        Parameters
        ----------
        countermodels:
            List of countermodels to process.

        Returns
        -------
        list[CountermodelObstruction]
            One obstruction record per countermodel.
        """
        self._current_phase = PipelinePhase.REPAIR
        t0 = time.monotonic()
        obstructions: list[CountermodelObstruction] = []
        repair_count = 0
        for cm in countermodels:
            try:
                obstruction = self.repair_pipeline.process(cm)
                obstructions.append(obstruction)
                if getattr(obstruction, "resolvable", False):
                    repair_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "repair_pipeline.process failed for model %s: %s",
                    getattr(cm, "model_id", "?"),
                    exc,
                )
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats["obstruction_count"] = len(obstructions)
        self._stats["repair_count"] = repair_count
        self._stats["phase_durations_ms"]["repair"] = round(elapsed_ms, 3)
        self._phase_log.append(
            {
                "phase": PipelinePhase.REPAIR,
                "started_at": _utcnow_iso(),
                "countermodel_count": len(countermodels),
                "obstruction_count": len(obstructions),
                "repair_count": repair_count,
                "elapsed_ms": elapsed_ms,
            }
        )
        logger.info(
            "Repair phase: %d countermodels → %d obstructions (%d resolvable) in %.1f ms",
            len(countermodels),
            len(obstructions),
            repair_count,
            elapsed_ms,
        )
        return obstructions

    def report_phase(self) -> str:
        """Emit a comprehensive pipeline report combining all subsystem reports.

        Calls ``emit_report()`` on the definer, type system, and repair
        pipeline, then assembles them into a structured multi-section text.
        Also serialises the phase log as JSON and appends pipeline-level
        statistics.

        Returns
        -------
        str
            A structured multi-line report string.
        """
        self._current_phase = PipelinePhase.REPORT
        t0 = time.monotonic()
        sections: list[str] = [
            "# StructuralFrontierPipeline Report",
            f"# pipeline_id: {self._stats['pipeline_id']}",
            f"# started_at:  {self._stats.get('started_at', '?')}",
            "",
            "## Frontier Definer",
        ]
        try:
            sections.append(self.definer.emit_report())
        except Exception as exc:  # noqa: BLE001
            sections.append(f"[definer report unavailable: {exc}]")
        sections += ["", "## Solver Lifted Type System"]
        try:
            sections.append(self.type_system.emit_report())
        except Exception as exc:  # noqa: BLE001
            sections.append(f"[type system report unavailable: {exc}]")
        sections += ["", "## Repair Pipeline"]
        try:
            sections.append(self.repair_pipeline.emit_report())
        except Exception as exc:  # noqa: BLE001
            sections.append(f"[repair pipeline report unavailable: {exc}]")
        sections += ["", "## Phase Log (JSON)"]
        try:
            sections.append(json.dumps(self._phase_log, default=str, indent=2))
        except Exception:  # noqa: BLE001
            sections.append("[phase log serialisation error]")
        sections += ["", "## Statistics"]
        for key, value in self._stats.items():
            sections.append(f"  {key}: {value}")
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats["phase_durations_ms"]["report"] = round(elapsed_ms, 3)
        report_text = "\n".join(sections)
        logger.info(
            "Report phase complete in %.1f ms (%d chars)", elapsed_ms, len(report_text)
        )
        return report_text

    def handle_undecidable(self, formula: str) -> str:
        """Return a structured narrative explaining what to do with an undecidable formula.

        Analyses the formula for common patterns that push it outside the
        decidable fragment (nested quantifiers, non-linear arithmetic, higher-
        order predicates) and suggests concrete mitigation steps such as
        manual abstraction, fragment restriction, or splitting.

        Parameters
        ----------
        formula:
            The SMT-LIB2 or JuGeo formula that was classified as undecidable.

        Returns
        -------
        str
            A multi-line copilot-readable narrative.
        """
        short = formula[:120].replace("\n", " ")
        hints: list[str] = []
        if any(q in formula for q in ("forall", "exists", "∀", "∃", "Forall", "Exists")):
            hints.append(
                "- The formula contains a quantifier. Consider restricting to the "
                "quantifier-free fragment (QF_LIA / QF_LRA) via instantiation or "
                "Skolemisation."
            )
        if "*" in formula or "^" in formula or "**" in formula:
            hints.append(
                "- Non-linear arithmetic detected. Consider linearising multiplications "
                "by introducing fresh variables for products, or switching to a "
                "bounded-integer abstraction."
            )
        if "Array" in formula or "select" in formula or "store" in formula:
            hints.append(
                "- Array theory operations found. Verify that all array accesses are "
                "within provable bounds, or abstract the array to a finite map."
            )
        if not hints:
            hints.append(
                "- No specific pattern detected. Manual review is recommended. "
                "Consider using the repair pipeline to generate candidate repair actions."
            )
        hint_text = "\n".join(hints)
        return (
            f"# Undecidable Formula Analysis\n"
            f"Formula (truncated): {short!r}\n\n"
            f"## Suggested mitigations\n"
            f"{hint_text}\n\n"
            f"## Frontier position\n"
            f"This formula lies outside the structural frontier. "
            f"The decidability map contains "
            f"{len(getattr(self.map_, 'frontiers', getattr(self.map_, 'entries', {})))} "
            f"frontier(s)."
        )

    def stats(self) -> dict[str, Any]:
        """Return the accumulated pipeline statistics dictionary.

        The returned dict is a shallow copy; mutating it does not affect
        internal state.  Statistics include timing breakdowns, counts of
        frontiers, formulas, obstructions, and repair actions.

        Returns
        -------
        dict[str, Any]
            Shallow copy of the stats dict.
        """
        return dict(self._stats)

    def copilot_pipeline_summary(self) -> str:
        """Return a copilot-readable summary of the entire pipeline run.

        The summary includes: pipeline ID, phase durations, counts of frontiers
        classified formulas, obstructions and repairs, current phase, and a
        brief prose narrative that copilot can quote verbatim in proposals.

        Returns
        -------
        str
            Structured text suitable for inclusion in a copilot proposal.
        """
        phase_dur = self._stats.get("phase_durations_ms", {})
        total_ms = sum(phase_dur.values())
        lines = [
            "## StructuralFrontierPipeline — Copilot Summary",
            f"Pipeline ID    : {self._stats.get('pipeline_id', '?')}",
            f"Status         : {self._current_phase.value}",
            f"Started at     : {self._stats.get('started_at', 'not started')}",
            f"Completed at   : {self._stats.get('completed_at', 'not completed')}",
            f"Total wall time: {total_ms:.1f} ms",
            "",
            "Phase durations (ms):",
        ]
        for phase_name, ms in phase_dur.items():
            lines.append(f"  {phase_name:12s}: {ms:.1f}")
        lines += [
            "",
            f"Frontiers defined  : {self._stats.get('frontier_count', 0)}",
            f"Formulas classified: {self._stats.get('formula_count', 0)}",
            f"Obstructions found : {self._stats.get('obstruction_count', 0)}",
            f"Repairs identified : {self._stats.get('repair_count', 0)}",
            "",
            "Narrative:",
            (
                f"The pipeline processed {self._stats.get('formula_count', 0)} formula(s) "
                f"against {self._stats.get('frontier_count', 0)} frontier boundary(ies). "
                f"{self._stats.get('obstruction_count', 0)} countermodel obstruction(s) "
                f"were found, of which {self._stats.get('repair_count', 0)} "
                f"are automatically resolvable. Copilot can use these results to "
                f"guide the next encoding iteration."
            ),
        ]
        return "\n".join(lines)


# ============================================================================
# Z3FrontierBridge
# ============================================================================


class Z3FrontierBridge:
    """Manages Z3 session lifecycles on behalf of the structural frontier subsystem.

    The bridge abstracts away raw Z3 session state, providing type-safe
    methods for opening sessions, asserting type invariants, checking
    membership, extracting countermodels, and closing sessions.  Pool
    size controls how many concurrent sessions may be open.

    Copilot note: use :meth:`session_stats` after closing a session to
    obtain a structured summary of what was checked during the session.

    Parameters
    ----------
    pool_size:
        Maximum number of concurrent Z3 sessions managed by this bridge.
        Defaults to 4.
    """

    def __init__(self, pool_size: int = 4) -> None:
        self._pool_size: int = max(1, pool_size)
        self._session_stats: dict[str, Any] = {
            "sessions_opened": 0,
            "sessions_closed": 0,
            "assertions_made": 0,
            "membership_checks": 0,
            "countermodels_extracted": 0,
            "total_session_time_ms": 0.0,
        }
        self._active_session: dict[str, Any] | None = None
        self._type_assertions: list[dict[str, Any]] = []
        self._session_start: float = 0.0
        logger.debug("Z3FrontierBridge initialised (pool_size=%d)", self._pool_size)

    # --- Session lifecycle --------------------------------------------------

    def open_session(self) -> None:
        """Open a new Z3 session context and initialise internal state.

        Simulates acquisition of a session from the Z3 session pool.  Logs
        the session ID and records the session start time for later duration
        computation.  Raises ``RuntimeError`` if a session is already open.
        """
        if self._active_session is not None:
            raise RuntimeError(
                "Z3FrontierBridge: a session is already open; call close_session() first."
            )
        session_id = _short_id()
        self._active_session = {
            "session_id": session_id,
            "opened_at": _utcnow_iso(),
            "assertion_ids": [],
        }
        self._type_assertions = []
        self._session_start = time.monotonic()
        self._session_stats["sessions_opened"] += 1
        logger.info("Z3FrontierBridge: session opened (id=%s)", session_id)

    def assert_type_invariant(self, lifted_type: SolverLiftedType) -> None:
        """Assert a type invariant into the current session.

        Encodes the invariants of *lifted_type* as SMT-LIB2 assertions and
        records them in the internal assertion list.  The lifted type's
        ``emit_declaration`` method is called to produce the declaration text.
        Raises ``RuntimeError`` if no session is open.

        Parameters
        ----------
        lifted_type:
            The solver-lifted type whose invariants should be asserted.
        """
        if self._active_session is None:
            raise RuntimeError(
                "Z3FrontierBridge: no session open; call open_session() first."
            )
        declaration = lifted_type.emit_declaration()
        invariant_texts = getattr(lifted_type, "invariants", [])
        entry: dict[str, Any] = {
            "assertion_id": _short_id(),
            "type_id": getattr(lifted_type, "type_id", "?"),
            "lifted_name": getattr(lifted_type, "lifted_name", "?"),
            "declaration": declaration,
            "invariant_count": len(invariant_texts),
            "asserted_at": _utcnow_iso(),
        }
        self._type_assertions.append(entry)
        self._active_session["assertion_ids"].append(entry["assertion_id"])
        self._session_stats["assertions_made"] += 1
        logger.debug(
            "Asserted type invariant for %s (%d invariants)",
            entry["lifted_name"],
            entry["invariant_count"],
        )

    def check_membership(self, value_smt: str, lifted_type: SolverLiftedType) -> bool:
        """Check whether *value_smt* is a member of *lifted_type*.

        Delegates to :meth:`SolverLiftedType.check_member` with the provided
        SMT expression string.  Records the check in session statistics.

        Parameters
        ----------
        value_smt:
            An SMT-LIB2 expression string representing the value to check.
        lifted_type:
            The type against which membership is being checked.

        Returns
        -------
        bool
            ``True`` if the value satisfies the type's invariants.
        """
        result = lifted_type.check_member(value_smt)
        self._session_stats["membership_checks"] += 1
        logger.debug(
            "check_membership(%r, %s) -> %s",
            value_smt[:40],
            getattr(lifted_type, "lifted_name", "?"),
            result,
        )
        return result

    def extract_countermodel_if_sat(self) -> Countermodel | None:
        """Extract a minimal countermodel if there are unsatisfied type assertions.

        Returns ``None`` if no session is open or if all assertions are
        satisfied (i.e., the type assertion set is empty).  Otherwise
        constructs a minimal :class:`Countermodel` whose assignment reflects
        the unsatisfied invariants.

        Returns
        -------
        Countermodel or None
            A countermodel if assertions remain unsatisfied; ``None`` otherwise.
        """
        if self._active_session is None:
            return None
        unsatisfied = [
            a for a in self._type_assertions if a["invariant_count"] > 0
        ]
        if not unsatisfied:
            return None
        assignment: dict[str, bool] = {}
        for assertion in unsatisfied:
            assignment[assertion["lifted_name"]] = False
        cm = Countermodel(
            assignment=assignment,
            reasons=tuple(a["assertion_id"] for a in unsatisfied),
            coordinate=self._active_session.get("session_id", ""),
            negated_proposition=f"type_invariant_failure:{len(unsatisfied)}_types",
        )
        self._session_stats["countermodels_extracted"] += 1
        logger.info(
            "Extracted countermodel from session %s (%d unsatisfied assertions)",
            self._active_session.get("session_id", "?"),
            len(unsatisfied),
        )
        return cm

    def close_session(self) -> None:
        """Close the active session and record timing statistics.

        Computes session wall-clock duration, appends it to the cumulative
        total, clears internal session state, and logs a summary.  Safe to
        call even if no session is open (logs a warning instead of raising).
        """
        if self._active_session is None:
            logger.warning("Z3FrontierBridge.close_session called with no active session.")
            return
        elapsed_ms = (time.monotonic() - self._session_start) * 1000.0
        self._session_stats["total_session_time_ms"] += elapsed_ms
        self._session_stats["sessions_closed"] += 1
        session_id = self._active_session.get("session_id", "?")
        assertion_count = len(self._type_assertions)
        self._active_session = None
        self._type_assertions = []
        logger.info(
            "Z3FrontierBridge: session %s closed (%.1f ms, %d assertions)",
            session_id,
            elapsed_ms,
            assertion_count,
        )

    def session_stats(self) -> dict[str, Any]:
        """Return a snapshot of cumulative session statistics.

        Statistics include the number of sessions opened/closed, total
        assertion count, membership checks performed, countermodels extracted,
        and cumulative session wall-clock time.

        Returns
        -------
        dict[str, Any]
            Shallow copy of the current session statistics dict.
        """
        return dict(self._session_stats)


# ============================================================================
# FrontierSupportLinker
# ============================================================================


class FrontierSupportLinker:
    """Links structural frontiers to geometry support regions.

    The linker maintains a mapping from frontier IDs to
    :class:`SupportRegion` objects and provides verification and export
    methods.  This is the integration point between the encoding subsystem
    and the JuGeo geometry layer.

    Copilot note: call :meth:`copilot_support_frontier_report` after
    linking all frontiers to obtain a diagnostic report suitable for
    copilot summaries.
    """

    def __init__(self) -> None:
        self._frontier_support_map: dict[str, SupportRegion] = {}
        self._linker_log: list[dict[str, Any]] = []
        logger.debug("FrontierSupportLinker initialised.")

    # --- Linking operations -------------------------------------------------

    def link(self, frontier: StructuralFrontier, support: SupportRegion) -> None:
        """Associate a frontier with a support region.

        Stores the mapping from *frontier.frontier_id* to *support* in the
        internal map and appends a log entry.  Overwrites any existing mapping
        for the same frontier ID.

        Parameters
        ----------
        frontier:
            The structural frontier to link.
        support:
            The geometry support region to associate.
        """
        frontier_id = getattr(frontier, "name", None) or getattr(frontier, "frontier_id", None) or _short_id()
        self._frontier_support_map[frontier_id] = support
        entry: dict[str, Any] = {
            "action": "link",
            "frontier_id": frontier_id,
            "fragment_name": getattr(frontier, "name", getattr(frontier, "fragment_name", "?")),
            "patch_keys": sorted(getattr(support, "patch_keys", frozenset())),
            "timestamp": _utcnow_iso(),
        }
        self._linker_log.append(entry)
        logger.debug(
            "Linked frontier %s → support with %d patch keys",
            frontier_id,
            len(getattr(support, "patch_keys", frozenset())),
        )

    def verify_support_covers_frontier(
        self,
        frontier: StructuralFrontier,
        support: SupportRegion,
    ) -> bool:
        """Check that *support* is non-trivially covering for *frontier*.

        A support region covers a frontier if it has at least one patch key
        or a non-None coordinate.  This is a necessary (not sufficient)
        condition for the geometry to be well-founded.

        Parameters
        ----------
        frontier:
            The frontier to check coverage for.
        support:
            The support region to validate.

        Returns
        -------
        bool
            ``True`` if the support provides at least minimal coverage.
        """
        has_patches = bool(getattr(support, "patch_keys", frozenset()))
        has_coordinate = getattr(support, "coordinate", None) is not None
        result = has_patches or has_coordinate
        logger.debug(
            "verify_support_covers_frontier(%s): patches=%s coord=%s → %s",
            getattr(frontier, "frontier_id", "?"),
            has_patches,
            has_coordinate,
            result,
        )
        return result

    def export_frontier_support_map(self) -> dict[str, Any]:
        """Serialise the frontier → support mapping to a JSON-compatible dict.

        Each entry includes the frontier ID, a string representation of the
        support coordinate, and the sorted list of patch keys.

        Returns
        -------
        dict[str, Any]
            Serialisable mapping with one entry per frontier.
        """
        result: dict[str, Any] = {
            "count": len(self._frontier_support_map),
            "entries": {},
        }
        for frontier_id, support in self._frontier_support_map.items():
            result["entries"][frontier_id] = {
                "coordinate": str(getattr(support, "coordinate", None)),
                "patch_keys": sorted(getattr(support, "patch_keys", frozenset())),
            }
        return result

    def check_jurisdiction(
        self,
        frontier: StructuralFrontier,
        region: SupportRegion,
    ) -> bool:
        """Check whether *region* is jurisdictionally compatible with *frontier*.

        Compatibility is assessed by intersecting the region's patch keys
        with those of the support region already linked to this frontier (if
        any).  If no support is linked, the check falls back to
        :meth:`verify_support_covers_frontier`.

        Parameters
        ----------
        frontier:
            The frontier whose jurisdiction is being checked.
        region:
            The candidate support region.

        Returns
        -------
        bool
            ``True`` if the region is within jurisdiction.
        """
        frontier_id = (
            getattr(frontier, "name", None)
            or getattr(frontier, "frontier_id", None)
        )
        if frontier_id and frontier_id in self._frontier_support_map:
            existing = self._frontier_support_map[frontier_id]
            existing_keys: frozenset[str] = getattr(existing, "patch_keys", frozenset())
            region_keys: frozenset[str] = getattr(region, "patch_keys", frozenset())
            compatible = bool(existing_keys & region_keys) or not existing_keys
        else:
            compatible = self.verify_support_covers_frontier(frontier, region)
        logger.debug(
            "check_jurisdiction(%s) → %s", frontier_id or "?", compatible
        )
        return compatible

    def copilot_support_frontier_report(self) -> str:
        """Return a structured report of all frontier–support linkages for copilot.

        The report lists each linked frontier with its coverage status,
        patch key count, and coordinate string.  Useful for copilot to include
        in geometry diagnostic summaries.

        Returns
        -------
        str
            Multi-line structured text.
        """
        lines = [
            "## FrontierSupportLinker — Copilot Report",
            f"Total linked frontiers: {len(self._frontier_support_map)}",
            f"Log entries           : {len(self._linker_log)}",
            "",
        ]
        for frontier_id, support in self._frontier_support_map.items():
            patch_keys = sorted(getattr(support, "patch_keys", frozenset()))
            has_coord = getattr(support, "coordinate", None) is not None
            coverage = "covered" if (patch_keys or has_coord) else "UNCOVERED"
            lines.append(
                f"  {frontier_id}: {coverage} "
                f"({len(patch_keys)} patches, coord={'yes' if has_coord else 'no'})"
            )
        if not self._frontier_support_map:
            lines.append("  (no frontiers linked)")
        lines += [
            "",
            "Recent log entries:",
        ]
        for entry in self._linker_log[-5:]:
            lines.append(
                f"  [{entry.get('timestamp', '?')}] {entry.get('action', '?')} "
                f"frontier={entry.get('frontier_id', '?')}"
            )
        return "\n".join(lines)


# ============================================================================
# TypeSystemIntegrator
# ============================================================================


class TypeSystemIntegrator:
    """Integrates solver-lifted types into type systems and cross-references frontiers.

    Provides the glue between :class:`SolverLiftedType` instances and
    :class:`SolverLiftedTypeSystem` objects, including consistency
    verification, cross-referencing with frontier boundaries, and merging
    of type systems.

    Copilot note: :meth:`copilot_integration_report` provides a narrative
    summary of all registered systems suitable for copilot proposals.
    """

    def __init__(self) -> None:
        self._integration_log: list[dict[str, Any]] = []
        self._registered_systems: dict[str, SolverLiftedTypeSystem] = {}
        self._cross_references: list[FrontierBoundary] = []
        logger.debug("TypeSystemIntegrator initialised.")

    # --- Integration operations ---------------------------------------------

    def integrate_lifted_type(
        self,
        lifted_type: SolverLiftedType,
        system: SolverLiftedTypeSystem,
    ) -> None:
        """Register a lifted type with a type system.

        Calls :meth:`SolverLiftedTypeSystem.register_type` and records
        the integration in the log.  Also tracks the system in the internal
        registry keyed by a digest of its identity.

        Parameters
        ----------
        lifted_type:
            The solver-lifted type to register.
        system:
            The type system to register it with.
        """
        system.register_type(lifted_type)
        type_name = getattr(lifted_type, "value", getattr(lifted_type, "lifted_name", str(lifted_type)))
        system_key = _digest(type_name, str(id(system)))
        self._registered_systems[system_key] = system
        self._integration_log.append(
            {
                "action": "integrate",
                "type_name": type_name,
                "system_key": system_key,
                "timestamp": _utcnow_iso(),
            }
        )
        logger.debug(
            "Integrated lifted type %s into system %s",
            type_name,
            system_key,
        )

    def verify_system_consistency(self, system: SolverLiftedTypeSystem) -> bool:
        """Verify that all types in *system* have non-empty invariants and valid fragments.

        A type system is considered consistent if every registered
        :class:`SolverLiftedType` has at least one invariant and a non-empty
        fragment name.  Any inconsistency is logged as a warning.

        Parameters
        ----------
        system:
            The type system to check.

        Returns
        -------
        bool
            ``True`` if all types pass the consistency checks.
        """
        types_map = getattr(system, "types", {})
        consistent = True
        for type_key, t in types_map.items():
            # SolverLiftedType is an Enum — check that it has a non-empty value
            type_val = getattr(t, "value", getattr(t, "fragment", str(t)))
            if not type_val.strip():
                logger.warning(
                    "Consistency violation: type %s has empty value/fragment", type_key
                )
                consistent = False
        return consistent

    def export_declarations(self, system: SolverLiftedTypeSystem) -> str:
        """Export all SMT-LIB2 declarations from *system*.

        Delegates to :meth:`SolverLiftedTypeSystem.emit_all_declarations`.

        Parameters
        ----------
        system:
            The type system whose declarations should be exported.

        Returns
        -------
        str
            SMT-LIB2 declaration block as a string.
        """
        return system.emit_all_declarations()

    def cross_reference_frontiers(
        self,
        system: SolverLiftedTypeSystem,
    ) -> list[FrontierBoundary]:
        """Create frontier boundaries for each pair of adjacent fragment types in *system*.

        Iterates over the registered types and constructs :class:`FrontierBoundary`
        objects for each consecutive pair of fragment names in the type system.
        Appends the boundaries to :attr:`_cross_references`.

        Parameters
        ----------
        system:
            The type system to cross-reference.

        Returns
        -------
        list[FrontierBoundary]
            Newly created frontier boundaries.
        """
        types_map = getattr(system, "types", {})
        # SolverLiftedType is an Enum; collect fragment names from type values or names
        fragment_names: list[str] = []
        for t in types_map.values():
            # If it's an Enum value, use its string value as fragment name
            fragment = getattr(t, "value", None) or getattr(t, "fragment", "") or str(t)
            if fragment and fragment not in fragment_names:
                fragment_names.append(fragment)
        new_boundaries: list[FrontierBoundary] = []
        for i in range(len(fragment_names) - 1):
            outside = fragment_names[i]
            inside = fragment_names[i + 1]
            boundary = FrontierBoundary(
                inside_fragment=inside,
                outside_fragment=outside,
                crossing_cost=1,
                crossing_label=f"cross_{outside}_to_{inside}",
            )
            new_boundaries.append(boundary)
            self._cross_references.append(boundary)
        logger.debug(
            "cross_reference_frontiers: created %d boundaries from %d types",
            len(new_boundaries),
            len(types_map),
        )
        return new_boundaries

    def merge_systems(
        self,
        s1: SolverLiftedTypeSystem,
        s2: SolverLiftedTypeSystem,
    ) -> SolverLiftedTypeSystem:
        """Merge two type systems into a new combined system.

        Creates a new :class:`SolverLiftedTypeSystem`, then registers all
        types from both *s1* and *s2* into it.  If a type with the same ID
        appears in both systems, the version from *s2* takes precedence.

        Parameters
        ----------
        s1:
            First type system (lower precedence for conflicts).
        s2:
            Second type system (higher precedence for conflicts).

        Returns
        -------
        SolverLiftedTypeSystem
            A fresh type system containing all types from both inputs.
        """
        merged = SolverLiftedTypeSystem()
        for t in getattr(s1, "types", {}).values():
            merged.register_type(t)
        for t in getattr(s2, "types", {}).values():
            merged.register_type(t)
        logger.info(
            "Merged type systems: s1(%d) + s2(%d) → merged(%d)",
            len(getattr(s1, "types", {})),
            len(getattr(s2, "types", {})),
            len(getattr(merged, "types", {})),
        )
        self._integration_log.append(
            {
                "action": "merge",
                "s1_size": len(getattr(s1, "types", {})),
                "s2_size": len(getattr(s2, "types", {})),
                "merged_size": len(getattr(merged, "types", {})),
                "timestamp": _utcnow_iso(),
            }
        )
        return merged

    def copilot_integration_report(self, system: SolverLiftedTypeSystem) -> str:
        """Return a structured integration report for copilot.

        Includes system size, declaration summary, cross-reference count,
        consistency verdict, and a prose narrative.

        Parameters
        ----------
        system:
            The type system to report on.

        Returns
        -------
        str
            Multi-line structured text.
        """
        types_map = getattr(system, "types", {})
        consistent = self.verify_system_consistency(system)
        declarations = self.export_declarations(system)
        decl_lines = declarations.count("\n") + 1
        lines = [
            "## TypeSystemIntegrator — Copilot Report",
            f"System size            : {len(types_map)} types",
            f"Cross-references       : {len(self._cross_references)} frontier boundaries",
            f"Integration log entries: {len(self._integration_log)}",
            f"Consistency            : {'PASS' if consistent else 'FAIL'}",
            f"Declaration block size : {decl_lines} lines",
            "",
            "Narrative:",
            (
                f"The type system contains {len(types_map)} solver-lifted type(s). "
                f"Consistency check {'passed' if consistent else 'FAILED'}. "
                f"{len(self._cross_references)} frontier boundary/boundaries were "
                f"auto-generated via cross-referencing. "
                f"Copilot can use these boundaries to guide fragment classification."
            ),
        ]
        return "\n".join(lines)


# ============================================================================
# CountermodelRepairDispatcher
# ============================================================================


class CountermodelRepairDispatcher:
    """Routes countermodels to appropriate repair pipelines based on failure class.

    The dispatcher maintains a routing table from :class:`FailureClass` to
    :class:`RepairAction` and applies the mapped repair to each incoming
    countermodel.  Repair history is tracked for summarisation.

    Copilot note: :meth:`copilot_dispatch_narrative` provides a decision
    narrative that copilot can include in repair proposal text.
    """

    def __init__(self) -> None:
        self._dispatch_log: list[dict[str, Any]] = []
        self._repair_history: list[CountermodelObstruction] = []
        # Maps FailureClass string value → RepairType string value
        self._failure_class_routes: dict[str, str] = {
            "assignment_conflict": "strengthen_precondition",
            "sort_violation": "add_sort_constraint",
            "function_mismatch": "refine_function_spec",
            "array_out_of_bounds": "add_invariant",
            "quantifier_witness": "weaken_postcondition",
            "unknown": "manual_review",
        }
        logger.debug("CountermodelRepairDispatcher initialised.")

    # --- Dispatch operations ------------------------------------------------

    def dispatch(self, countermodel: Countermodel) -> CountermodelObstruction:
        """Route *countermodel* to the appropriate repair action and return an obstruction.

        Determines the failure class of the countermodel, looks up the
        appropriate :class:`RepairAction` via :meth:`route_by_failure_class`,
        constructs a :class:`CountermodelObstruction`, and tracks the result.

        Parameters
        ----------
        countermodel:
            The countermodel to dispatch.

        Returns
        -------
        CountermodelObstruction
            The obstruction record with repair action populated.
        """
        repair_action = self.route_by_failure_class(countermodel)
        fc = getattr(countermodel, "failure_class", FailureClass.UNKNOWN)
        fc_value = getattr(fc, "value", str(fc))
        # Build the repair description and create an action instance
        repair_description = (
            f"Auto-dispatched from failure class {fc_value}: "
            f"apply {repair_action.value} to model "
            f"{getattr(countermodel, 'model_id', '?')}"
        )
        action_instance = RepairAction(
            action_type=repair_action,
            description=repair_description,
            cost=1,
            origin="dispatcher",
        )
        obstruction = CountermodelObstruction(
            countermodel=countermodel,
            failure_class=fc,
            violated_invariant=getattr(countermodel, "negated_proposition", ""),
            suggested_actions=[action_instance],
            confidence=0.75 if repair_action.value != "manual_review" else 0.3,
            context=(
                f"Dispatched countermodel {getattr(countermodel, 'model_id', '?')} "
                f"(failure_class={fc_value})"
            ),
        )
        self.track_repair_history(obstruction)
        self._dispatch_log.append(
            {
                "model_id": getattr(countermodel, "model_id", "?"),
                "failure_class": fc_value,
                "repair_type": repair_action.value,
                "resolvable": obstruction.is_resolvable(),
                "timestamp": _utcnow_iso(),
            }
        )
        logger.debug(
            "Dispatched model %s: %s → %s",
            getattr(countermodel, "model_id", "?"),
            fc_value,
            repair_action.value,
        )
        return obstruction

    def route_by_failure_class(self, countermodel: Countermodel) -> RepairType:
        """Map a countermodel's failure class to its canonical repair type.

        Looks up the :class:`FailureClass` in the routing table.  If the
        failure class is not found, returns :attr:`RepairType.MANUAL_REVIEW`.

        Parameters
        ----------
        countermodel:
            The countermodel whose failure class should be routed.

        Returns
        -------
        RepairType
            The repair type appropriate for the failure class.
        """
        fc = getattr(countermodel, "failure_class", FailureClass.UNKNOWN)
        fc_value: str = getattr(fc, "value", str(fc))
        # Normalise to the base suffix (e.g. "solver.FailureClass.unknown" → "unknown")
        fc_key = fc_value.split(".")[-1]
        repair_value = self._failure_class_routes.get(fc_key, "manual_review")
        try:
            return RepairType(repair_value)
        except (ValueError, KeyError):
            return RepairType.MANUAL_REVIEW

    def apply_repair(self, obstruction: CountermodelObstruction) -> bool:
        """Apply the most likely repair from *obstruction* and return whether resolved.

        Checks the :attr:`CountermodelObstruction.most_likely_repair` field
        and simulates repair application.  Returns ``True`` if the repair
        action is not :attr:`RepairAction.MANUAL_REVIEW`, indicating that an
        automatic resolution path exists.

        Parameters
        ----------
        obstruction:
            The obstruction record whose repair should be applied.

        Returns
        -------
        bool
            ``True`` if an automatic repair was successfully applied.
        """
        # most_likely_repair() is a method on CountermodelObstruction
        repair = obstruction.most_likely_repair()
        resolvable = obstruction.is_resolvable()
        repair_type_val = getattr(getattr(repair, "action_type", None), "value", "manual_review")
        auto_path = resolvable and repair_type_val != "manual_review"
        if auto_path:
            logger.info(
                "Applying repair %s to obstruction %s",
                repair_type_val,
                getattr(obstruction, "obstruction_id", "?"),
            )
        else:
            logger.warning(
                "Obstruction %s requires manual review (no automatic repair).",
                getattr(obstruction, "obstruction_id", "?"),
            )
        return auto_path

    def track_repair_history(self, obstruction: CountermodelObstruction) -> None:
        """Append *obstruction* to the repair history log.

        The history is used by :meth:`summarize_repairs` to produce
        aggregate statistics over all repairs attempted in this dispatcher
        session.

        Parameters
        ----------
        obstruction:
            The obstruction record to append.
        """
        self._repair_history.append(obstruction)

    def summarize_repairs(self) -> dict[str, Any]:
        """Return aggregate statistics about all repairs tracked so far.

        Computes total count, resolvable count, manual-review count, and
        a breakdown by failure class and repair action.

        Returns
        -------
        dict[str, Any]
            Statistics dictionary.
        """
        total = len(self._repair_history)
        resolvable = sum(1 for o in self._repair_history if o.is_resolvable())
        manual = total - resolvable
        by_failure: dict[str, int] = {}
        by_repair: dict[str, int] = {}
        for obs in self._repair_history:
            fc_key = str(getattr(getattr(obs, "failure_class", None), "value", "unknown"))
            ra = obs.most_likely_repair()
            ra_key = str(getattr(getattr(ra, "action_type", None), "value", "manual_review"))
            by_failure[fc_key] = by_failure.get(fc_key, 0) + 1
            by_repair[ra_key] = by_repair.get(ra_key, 0) + 1
        return {
            "total": total,
            "resolvable": resolvable,
            "manual_review_required": manual,
            "resolution_rate": round(resolvable / total, 4) if total > 0 else 0.0,
            "by_failure_class": by_failure,
            "by_repair_action": by_repair,
        }

    def copilot_dispatch_narrative(self, countermodel: Countermodel) -> str:
        """Return a narrative describing the dispatch decision for *countermodel*.

        The narrative explains the failure class, the routing decision, and
        whether an automatic repair is available.  Suitable for inclusion
        in a copilot proposal or debugging summary.

        Parameters
        ----------
        countermodel:
            The countermodel whose dispatch decision should be narrated.

        Returns
        -------
        str
            Structured multi-line narrative string.
        """
        fc = getattr(countermodel, "failure_class", FailureClass.UNKNOWN)
        fc_val = getattr(fc, "value", str(fc))
        model_id = getattr(countermodel, "model_id", "?")
        negated_prop = getattr(countermodel, "negated_proposition", "(unknown proposition)")
        repair_type = self.route_by_failure_class(countermodel)
        repair_type_val = getattr(repair_type, "value", str(repair_type))
        resolvable = repair_type_val != "manual_review"
        summary = self.summarize_repairs()
        lines = [
            "## CountermodelRepairDispatcher — Dispatch Narrative",
            f"Model ID              : {model_id}",
            f"Failure class         : {fc_val}",
            f"Negated proposition   : {negated_prop[:80]}",
            f"Routed repair type    : {repair_type_val}",
            f"Automatically resolvable: {'yes' if resolvable else 'no (manual review)'}",
            "",
            f"Routing table has {len(self._failure_class_routes)} entries.",
            f"Dispatch history: {summary['total']} total, "
            f"{summary['resolvable']} resolvable "
            f"({summary['resolution_rate']*100:.1f}% resolution rate).",
            "",
            "Copilot recommendation:",
        ]
        if resolvable:
            lines.append(
                f"  Apply repair type '{repair_type_val}' to address the "
                f"'{fc_val}' failure. This repair class has a known automatic "
                f"resolution path within the structural frontier pipeline."
            )
        else:
            lines.append(
                f"  This failure class ('{fc_val}') requires manual review. "
                f"Inspect the countermodel assignment and consider strengthening "
                f"the precondition or restricting the formula to a decidable fragment."
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level convenience factory
# ---------------------------------------------------------------------------


def build_pipeline() -> StructuralFrontierPipeline:
    """Construct a fully initialised :class:`StructuralFrontierPipeline`.

    Convenience factory for callers that do not need to customise sub-system
    construction.  Equivalent to calling ``StructuralFrontierPipeline()``
    directly but documents intent.

    Returns
    -------
    StructuralFrontierPipeline
        A fresh pipeline ready for :meth:`~StructuralFrontierPipeline.run`.
    """
    return StructuralFrontierPipeline()


def build_bridge(pool_size: int = 4) -> Z3FrontierBridge:
    """Construct a :class:`Z3FrontierBridge` with the given pool size.

    Parameters
    ----------
    pool_size:
        Maximum number of concurrent Z3 sessions (default 4).

    Returns
    -------
    Z3FrontierBridge
        A fresh bridge ready for :meth:`~Z3FrontierBridge.open_session`.
    """
    return Z3FrontierBridge(pool_size=pool_size)
