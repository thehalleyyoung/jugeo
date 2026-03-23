"""Bug-detection subsystem for JuGeo (theory2.tex Ch11).

This package implements the bug-detection mode of the JuGeo benchmark system.
Bugs are treated as first-class cohomological obstructions — elements of the
Čech cohomology group H¹(U, D) over the judgment-sheaf site Γ — rather than
ephemeral error logs.

Package structure
-----------------
``models``
    Core frozen dataclasses: ``BugKind``, ``BugReport``,
    ``BugDetectionResult``, ``DetectionSession``.

``ast_bridge``
    Bridge from Python AST to JuGeo symbolic form: ``ASTCoordinate``,
    ``SymbolicNode``, ``ASTBridgeConfig``, ``PythonASTBridge``,
    ``bridge_python_file``.

``detector``
    Unified detection entry point: ``BugDetector``, module-level
    ``detect_bugs`` convenience function.

``integration``
    Ecosystem wiring: ``BugDetectionOrchestrator``,
    ``wire_to_evidence_pipeline``, ``wire_to_manifest``,
    ``cli_detect``, ``api_detect``.

Quick start
-----------
::

    from jugeo.problem_modes.bug_detection import detect_bugs

    result = detect_bugs("path/to/module.py", is_path=True)
    for bug in result.bugs:
        print(bug.kind.value, bug.coordinate, bug.description)

Theory basis
------------
See theory2.tex Ch11 for the full theoretical treatment of bug detection
as repair semantics and cohomological obstruction theory.
"""

from .models import (
    BugDetectionResult,
    BugKind,
    BugReport,
    DetectionSession,
    MANIFEST_SPEC_PROVENANCE as _MODELS_PROVENANCE,
)
from .ast_bridge import (
    ASTBridgeConfig,
    ASTCoordinate,
    PythonASTBridge,
    SymbolicNode,
    bridge_python_file,
)
from .detector import (
    BugDetector,
    detect_bugs,
)
from .integration import (
    BugDetectionOrchestrator,
    api_detect,
    cli_detect,
    wire_to_evidence_pipeline,
    wire_to_manifest,
)

__all__ = [
    # models
    "BugKind",
    "BugReport",
    "BugDetectionResult",
    "DetectionSession",
    # ast_bridge
    "ASTCoordinate",
    "SymbolicNode",
    "ASTBridgeConfig",
    "PythonASTBridge",
    "bridge_python_file",
    # detector
    "BugDetector",
    "detect_bugs",
    # integration
    "BugDetectionOrchestrator",
    "wire_to_evidence_pipeline",
    "wire_to_manifest",
    "cli_detect",
    "api_detect",
    # cross-subsystem integration
    "bugs_as_obstructions",
    "bug_evidence",
    "solver_confirmed_bugs",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration helpers
# ---------------------------------------------------------------------------


def bugs_as_obstructions(
    result: "BugDetectionResult",
) -> "list[dict[str, object]]":
    """Convert detected bugs into Čech cohomology obstructions.

    Each bug in *result* is mapped to a :class:`~jugeo.geometry.descent.DescentObstruction`
    via the descent engine, representing the bug as a failure of local sections
    to glue into a global section of the judgment sheaf.

    Parameters
    ----------
    result : BugDetectionResult
        Detection result containing zero or more :class:`BugReport` entries.

    Returns
    -------
    list[dict[str, object]]
        One dict per bug with keys ``bug`` (the :class:`BugReport`),
        ``obstruction`` (the :class:`DescentObstruction` or ``None``),
        and ``cohomology_class`` (the :class:`CohomologyClass` or ``None``).

    Raises
    ------
    NotImplementedError
        If ``jugeo.geometry.descent`` is not available.

    See Also
    --------
    jugeo.geometry.descent.DescentObstruction : The obstruction type.
    jugeo.geometry.descent.CohomologyClass : Čech cohomology representative.
    """
    try:
        from jugeo.geometry.descent import (
            CohomologyClass,
            DescentObstruction,
            LocalSection,
        )
    except ImportError:
        raise NotImplementedError(
            "bugs_as_obstructions requires jugeo.geometry.descent to be installed."
        )

    obstructions: list[dict[str, object]] = []
    for bug in result.bugs:
        coord_id = getattr(bug, "coordinate", None) or "unknown"
        try:
            section = LocalSection(
                coordinate_id=str(coord_id),
                data={"bug_kind": bug.kind.value, "description": bug.description},
            )
            obstruction = DescentObstruction(
                coordinate_id=str(coord_id),
                reason=f"Bug detected: {bug.kind.value}",
                failed_sections=[section],
            )
            cohomology = CohomologyClass(
                representative=obstruction,
                degree=1,
            )
        except Exception:  # noqa: BLE001
            obstruction = None  # type: ignore[assignment]
            cohomology = None  # type: ignore[assignment]
        obstructions.append({
            "bug": bug,
            "obstruction": obstruction,
            "cohomology_class": cohomology,
        })
    return obstructions


def bug_evidence(
    result: "BugDetectionResult",
) -> "list[dict[str, object]]":
    """Create evidence manifest entries for each detected bug.

    Bridges the bug-detection subsystem to the evidence infrastructure by
    converting each :class:`BugReport` into data suitable for ingestion by
    :class:`~jugeo.evidence.manifests.EvidenceManifest`.

    Parameters
    ----------
    result : BugDetectionResult
        Detection result containing zero or more :class:`BugReport` entries.

    Returns
    -------
    list[dict[str, object]]
        One dict per bug with keys ``bug`` (the report), ``evidence_entry``
        (dict suitable for manifest ingestion), and ``manifest_builder``
        (a :class:`~jugeo.evidence.manifests.ManifestBuilder` or ``None``).

    Raises
    ------
    NotImplementedError
        If ``jugeo.evidence.manifests`` is not available.

    See Also
    --------
    jugeo.evidence.manifests.EvidenceManifest : Target manifest type.
    jugeo.evidence.manifests.ManifestBuilder : Builder for manifests.
    """
    try:
        from jugeo.evidence.manifests import ManifestBuilder
    except ImportError:
        raise NotImplementedError(
            "bug_evidence requires jugeo.evidence.manifests to be installed."
        )

    entries: list[dict[str, object]] = []
    for bug in result.bugs:
        coord_id = getattr(bug, "coordinate", None) or "unknown"
        evidence_entry = {
            "coordinate": str(coord_id),
            "kind": "bug_detection",
            "bug_kind": bug.kind.value,
            "description": bug.description,
            "trust_level": "TOOL_VERIFIED",
        }
        try:
            builder = ManifestBuilder()
        except Exception:  # noqa: BLE001
            builder = None
        entries.append({
            "bug": bug,
            "evidence_entry": evidence_entry,
            "manifest_builder": builder,
        })
    return entries


def solver_confirmed_bugs(
    result: "BugDetectionResult",
    *,
    timeout_ms: int = 5000,
) -> "list[dict[str, object]]":
    """Use SMT solving to confirm or refute each detected bug.

    For each bug, encodes the bug condition as a Z3 formula and checks
    satisfiability.  A SAT result confirms the bug is reachable; UNSAT
    indicates the bug may be a false positive.

    Parameters
    ----------
    result : BugDetectionResult
        Detection result containing zero or more :class:`BugReport` entries.
    timeout_ms : int, optional
        Per-bug Z3 solver timeout in milliseconds (default: 5000).

    Returns
    -------
    list[dict[str, object]]
        One dict per bug with keys ``bug`` (the report), ``confirmed``
        (bool or ``None`` if solver timed out), ``solver_outcome``
        (the :class:`~jugeo.solver.z3_session.SolveOutcome` value), and
        ``session`` (the :class:`~jugeo.solver.z3_session.Z3Session`
        instance or ``None``).

    Raises
    ------
    NotImplementedError
        If ``jugeo.solver.z3_session`` is not available.

    See Also
    --------
    jugeo.solver.z3_session.Z3Session : Z3 session manager.
    jugeo.solver.z3_session.Z3Encoder : Formula encoder.
    """
    try:
        from jugeo.solver.z3_session import Z3Session, Z3Encoder, SolveOutcome
    except ImportError:
        raise NotImplementedError(
            "solver_confirmed_bugs requires jugeo.solver.z3_session to be installed."
        )

    confirmations: list[dict[str, object]] = []
    for bug in result.bugs:
        confirmed = None
        outcome = None
        session = None
        try:
            session = Z3Session(timeout_ms=timeout_ms)
            encoder = Z3Encoder()
            formula = encoder.encode({
                "kind": "bug_reachability",
                "bug_kind": bug.kind.value,
                "coordinate": str(getattr(bug, "coordinate", "unknown")),
                "description": bug.description,
            })
            solver_result = session.check(formula)
            outcome = solver_result.outcome
            if outcome == SolveOutcome.SAT:
                confirmed = True
            elif outcome == SolveOutcome.UNSAT:
                confirmed = False
            # UNKNOWN / TIMEOUT → confirmed stays None
        except Exception:  # noqa: BLE001
            pass
        confirmations.append({
            "bug": bug,
            "confirmed": confirmed,
            "solver_outcome": outcome,
            "session": session,
        })
    return confirmations
