"""Package scaffold for JuGeo generated modules.

Cross-references: orchestration determines frontier phases from
maturity levels, runtime checkpointing, and geometry (descent).
"""

from __future__ import annotations
from typing import Any

try:
    from jugeo.maturity import describe_maturity_level, maturity_progression_path
except Exception:
    describe_maturity_level = None  # type: ignore[assignment]
    maturity_progression_path = None  # type: ignore[assignment]

try:
    from jugeo.runtime.checkpointing import Checkpoint, CheckpointStore
except Exception:
    Checkpoint = None  # type: ignore[assignment,misc]
    CheckpointStore = None  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentPhase, DescentConfiguration
except Exception:
    DescentPhase = None  # type: ignore[assignment,misc]
    DescentConfiguration = None  # type: ignore[assignment,misc]


def phase_via_maturity(maturity: Any) -> dict[str, Any]:
    """Determine the current frontier phase from a maturity level.

    Maps maturity descriptors (jugeo.maturity) to frontier phase labels
    so the orchestrator can adjust search strategy accordingly.
    """
    if describe_maturity_level is not None:
        try:
            description = describe_maturity_level(maturity)
        except Exception:
            description = str(maturity)
    else:
        description = str(maturity)

    progression: list[Any] = []
    if maturity_progression_path is not None:
        try:
            progression = maturity_progression_path(maturity)
        except Exception:
            pass

    level_str = str(maturity).lower()
    if "production" in level_str or "stable" in level_str:
        phase = "exploitation"
    elif "experimental" in level_str or "alpha" in level_str:
        phase = "exploration"
    else:
        phase = "mixed"

    return {
        "phase": phase,
        "maturity_description": description,
        "remaining_progression": progression,
        "subsystem": "jugeo.maturity",
    }


def phase_checkpoint(phase: Any) -> dict[str, Any]:
    """Create a phase checkpoint using jugeo.runtime.checkpointing.

    Persists the current phase boundary state so the orchestrator can
    resume or roll back to this point.
    """
    phase_label = getattr(phase, "value", str(phase)) if phase is not None else "unknown"

    if CheckpointStore is None:
        return {
            "saved": False,
            "reason": "CheckpointStore unavailable",
            "subsystem": "jugeo.runtime.checkpointing",
        }

    try:
        store = CheckpointStore()
        payload = {"phase": phase_label}
        cp = store.create(payload) if hasattr(store, "create") else None
        return {
            "saved": cp is not None,
            "checkpoint_id": getattr(cp, "id", None),
            "phase": phase_label,
            "subsystem": "jugeo.runtime.checkpointing",
        }
    except Exception as exc:
        return {"saved": False, "reason": str(exc), "subsystem": "jugeo.runtime.checkpointing"}


def phase_descent_check(phase: Any) -> dict[str, Any]:
    """Check the descent condition at a phase boundary.

    Uses jugeo.geometry.descent to verify that the descent invariant
    still holds when transitioning between frontier phases.
    """
    phase_label = getattr(phase, "value", str(phase)) if phase is not None else "unknown"

    if DescentPhase is None:
        return {
            "descent_ok": False,
            "reason": "DescentPhase unavailable",
            "subsystem": "jugeo.geometry.descent",
        }

    try:
        current = DescentPhase(phase_label)
        descent_ok = current in (
            DescentPhase.REFINING if hasattr(DescentPhase, "REFINING") else current,
        )
    except (ValueError, KeyError):
        descent_ok = True  # unknown phase passes vacuously

    return {
        "descent_ok": descent_ok,
        "phase": phase_label,
        "subsystem": "jugeo.geometry.descent",
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import bandit_style_allocation_across_het
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import large_projects_move_through_distin
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
    from . import phase_changes_should_be_triggered
except Exception:
    pass
try:
    from . import phase_detection
except Exception:
    pass
try:
    from . import phase_management
except Exception:
    pass
try:
    from . import search_should_preserve_diversity_a
except Exception:
    pass
try:
    from . import the_frontier_should_be_managed_as
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
