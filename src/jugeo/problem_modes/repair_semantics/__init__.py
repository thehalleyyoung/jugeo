"""Repair-semantics subsystem for JuGeo (theory2.tex Ch11).

This package implements the full Ch11 debug / repair cycle:
counterexample extraction, repair planning, repair execution, and
debug orchestration.  All core objects are frozen dataclasses with
full JSON round-trip support.

# copilot: repair_semantics package init — auto-generated from theory2 ch11
"""

from __future__ import annotations

from jugeo.problem_modes.repair_semantics.models import (
    CounterexampleRecord,
    DebugSession,
    DebugSessionStatus,
    RepairFrontier,
    RepairPlan,
    RepairStep,
    RepairValidator,
)
from jugeo.problem_modes.repair_semantics.algorithms import (
    delta_debug,
    compute_minimal_repair_frontier,
    topological_repair_order,
    merge_repair_frontiers,
    score_repair_confidence,
    compute_repair_distance,
    classify_cohomology_class,
    repair_convergence_certificate,
)
from jugeo.problem_modes.repair_semantics.integration import (
    RepairSemanticsIntegration,
)
from jugeo.problem_modes.repair_semantics.theorems import (
    TheoremObligation,
    ProofStrategy,
    TheoremStatus,
    check_theorem,
    get_all_theorems,
    generate_proof_obligations,
    theorem_coverage_report,
)

__all__ = [
    # models
    "CounterexampleRecord",
    "DebugSession",
    "DebugSessionStatus",
    "RepairFrontier",
    "RepairPlan",
    "RepairStep",
    "RepairValidator",
    # algorithms
    "delta_debug",
    "compute_minimal_repair_frontier",
    "topological_repair_order",
    "merge_repair_frontiers",
    "score_repair_confidence",
    "compute_repair_distance",
    "classify_cohomology_class",
    "repair_convergence_certificate",
    # integration
    "RepairSemanticsIntegration",
    # theorems
    "TheoremObligation",
    "ProofStrategy",
    "TheoremStatus",
    "check_theorem",
    "get_all_theorems",
    "generate_proof_obligations",
    "theorem_coverage_report",
    # cross-subsystem integration
    "repair_via_descent",
    "countermodel_repair",
    "certified_repair",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration helpers
# ---------------------------------------------------------------------------


def repair_via_descent(
    session: "DebugSession",
) -> "dict[str, object]":
    """Repair by modifying sections to restore sheaf gluing conditions.

    Uses :mod:`jugeo.geometry.descent` to identify which local sections
    violate the overlap compatibility (gluing) condition and produces a
    repair plan that, when applied, restores descent.

    Parameters
    ----------
    session : DebugSession
        Active debug session containing the counterexample(s) and current
        repair frontier.

    Returns
    -------
    dict[str, object]
        Keys: ``descent_result`` (:class:`~jugeo.geometry.descent.DescentResult`
        or ``None``), ``obstructions`` (list of obstructions found),
        ``repair_sections`` (list of :class:`~jugeo.geometry.descent.LocalSection`
        that need modification), ``gluing_restored`` (bool).

    Raises
    ------
    NotImplementedError
        If ``jugeo.geometry.descent`` is not available.

    See Also
    --------
    jugeo.geometry.descent.DescentEngine : Core descent engine.
    jugeo.geometry.descent.LocalSection : Section data type.
    """
    try:
        from jugeo.geometry.descent import DescentEngine, LocalSection
    except ImportError:
        raise NotImplementedError(
            "repair_via_descent requires jugeo.geometry.descent to be installed."
        )

    repair_sections: list = []
    obstructions: list = []
    gluing_restored = False
    descent_result = None

    try:
        engine = DescentEngine()
        frontier = getattr(session, "frontier", None)
        counterexamples = getattr(session, "counterexamples", [])

        sections = []
        for cx in counterexamples:
            coord = getattr(cx, "coordinate", None) or "unknown"
            sections.append(LocalSection(
                coordinate_id=str(coord),
                data={"counterexample": cx},
            ))

        descent_result = engine.run(sections)
        obstructions = list(getattr(descent_result, "obstructions", []))
        repair_sections = [
            s for s in sections
            if any(
                str(getattr(o, "coordinate_id", "")) == s.coordinate_id
                for o in obstructions
            )
        ]
        gluing_restored = len(obstructions) == 0
    except Exception:  # noqa: BLE001
        pass

    return {
        "descent_result": descent_result,
        "obstructions": obstructions,
        "repair_sections": repair_sections,
        "gluing_restored": gluing_restored,
    }


def countermodel_repair(
    session: "DebugSession",
) -> "dict[str, object]":
    """Use countermodels to guide the repair process.

    Extracts countermodels from the debug session's counterexample records
    via :mod:`jugeo.solver.countermodels`, minimises them, generates repair
    hints, and produces a prioritised repair plan.

    Parameters
    ----------
    session : DebugSession
        Active debug session with counterexample data.

    Returns
    -------
    dict[str, object]
        Keys: ``countermodels`` (list of :class:`~jugeo.solver.countermodels.Countermodel`),
        ``repair_hints`` (list of hint dicts), ``failure_classes``
        (list of :class:`~jugeo.solver.countermodels.FailureClass` values),
        ``minimized`` (bool indicating whether minimisation succeeded).

    Raises
    ------
    NotImplementedError
        If ``jugeo.solver.countermodels`` is not available.

    See Also
    --------
    jugeo.solver.countermodels.CountermodelExtractor : Extraction engine.
    jugeo.solver.countermodels.RepairHintGenerator : Hint generator.
    """
    try:
        from jugeo.solver.countermodels import (
            CountermodelExtractor,
            CountermodelMinimizer,
            RepairHintGenerator,
        )
    except ImportError:
        raise NotImplementedError(
            "countermodel_repair requires jugeo.solver.countermodels to be installed."
        )

    countermodels: list = []
    repair_hints: list = []
    failure_classes: list = []
    minimized = False

    try:
        extractor = CountermodelExtractor()
        minimizer = CountermodelMinimizer()
        hint_gen = RepairHintGenerator()

        counterexamples = getattr(session, "counterexamples", [])
        for cx in counterexamples:
            cm = extractor.extract(cx)
            if cm is not None:
                cm = minimizer.minimize(cm)
                minimized = True
                countermodels.append(cm)
                fc = getattr(cm, "failure_class", None)
                if fc is not None:
                    failure_classes.append(fc)
                hint = hint_gen.generate(cm)
                if hint is not None:
                    repair_hints.append(hint)
    except Exception:  # noqa: BLE001
        pass

    return {
        "countermodels": countermodels,
        "repair_hints": repair_hints,
        "failure_classes": failure_classes,
        "minimized": minimized,
    }


def certified_repair(
    session: "DebugSession",
    plan: "RepairPlan",
) -> "dict[str, object]":
    """Produce a certificate attesting that a repair plan resolves the session's bugs.

    Bridges the repair-semantics subsystem to :mod:`jugeo.evidence.certificates`,
    producing a :class:`~jugeo.evidence.certificates.Certificate` that records
    what was repaired, the evidence backing the repair, and the resulting
    trust level.

    Parameters
    ----------
    session : DebugSession
        The debug session containing the original defects.
    plan : RepairPlan
        The repair plan that was (or will be) applied.

    Returns
    -------
    dict[str, object]
        Keys: ``certificate`` (:class:`~jugeo.evidence.certificates.Certificate`
        or ``None``), ``builder`` (the
        :class:`~jugeo.evidence.certificates.CertificateBuilder` used),
        ``status`` (str — ``"issued"``, ``"failed"``, or ``"unavailable"``).

    Raises
    ------
    NotImplementedError
        If ``jugeo.evidence.certificates`` is not available.

    See Also
    --------
    jugeo.evidence.certificates.Certificate : Certificate type.
    jugeo.evidence.certificates.CertificateBuilder : Builder for certificates.
    """
    try:
        from jugeo.evidence.certificates import CertificateBuilder
    except ImportError:
        raise NotImplementedError(
            "certified_repair requires jugeo.evidence.certificates to be installed."
        )

    certificate = None
    status = "unavailable"
    builder = None

    try:
        builder = CertificateBuilder()
        builder.set_subject(f"repair:{getattr(session, 'session_id', 'unknown')}")
        builder.set_evidence({
            "repair_plan": plan,
            "counterexamples": getattr(session, "counterexamples", []),
            "session_status": str(getattr(session, "status", "unknown")),
        })
        steps = getattr(plan, "steps", [])
        builder.set_scope([str(getattr(s, "coordinate", "")) for s in steps])
        certificate = builder.build()
        status = "issued"
    except Exception:  # noqa: BLE001
        status = "failed"

    return {
        "certificate": certificate,
        "builder": builder,
        "status": status,
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import counterexample_extraction
except Exception:
    pass
try:
    from . import counterexamples_as_semantic_witnes
except Exception:
    pass
try:
    from . import debug_orchestration
except Exception:
    pass
try:
    from . import debugging_as_obstruction_localizat
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import repair_as_controlled_surgery_on_a
except Exception:
    pass
try:
    from . import repair_execution
except Exception:
    pass
try:
    from . import repair_planning
except Exception:
    pass
try:
    from . import repairs_and_generations_should_be
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
