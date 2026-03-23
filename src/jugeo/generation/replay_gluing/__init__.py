"""Package scaffold for JuGeo generated modules."""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Cross-subsystem replay / gluing helpers
# ---------------------------------------------------------------------------


def replay_with_cache(record: Any) -> dict[str, Any]:
    """Replay a construction record using the runtime cache.

    Combines :mod:`jugeo.runtime.cache` (for memoised intermediate
    results) with :mod:`jugeo.runtime.replay` (for deterministic
    re-execution) to efficiently reproduce a previous construction.
    """
    try:
        from jugeo.runtime.cache import get_cached  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_cached = None

    try:
        from jugeo.runtime.replay import replay_record  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        replay_record = None

    cached = get_cached(record) if get_cached is not None else None
    if cached is not None:
        return {
            "record": record,
            "result": cached,
            "cache_hit": True,
            "source": "jugeo.runtime.cache",
        }

    if replay_record is not None:
        result = replay_record(record)
    else:
        result = {"replayed": False, "reason": "replay module unavailable"}

    return {
        "record": record,
        "result": result,
        "cache_hit": False,
        "source": "jugeo.runtime.replay",
    }


def glue_via_descent(sections: Any) -> dict[str, Any]:
    """Glue local sections together using descent data.

    Uses :mod:`jugeo.geometry.descent` to compute the descent
    morphisms needed to glue overlapping *sections* into a
    coherent global section.
    """
    try:
        from jugeo.geometry.descent import compute_descent  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        compute_descent = None

    if compute_descent is not None:
        descent = compute_descent(sections)
    else:
        descent = {"glued": False, "reason": "descent module unavailable"}

    return {
        "sections": sections,
        "descent": descent,
        "source": "jugeo.geometry.descent",
    }


__all__ = [
    "replay_with_cache",
    "glue_via_descent",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import convergence_verification
except Exception:
    pass
try:
    from . import cumulative_generation_memory_assem
except Exception:
    pass
try:
    from . import final_assembly
except Exception:
    pass
try:
    from . import global_gluing_under_replay_integra
except Exception:
    pass
try:
    from . import implementation_path_for_cumulative
except Exception:
    pass
try:
    from . import incremental_replay
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
    from . import replay_planning
except Exception:
    pass
try:
    from . import theorem_and_falsification_burden_f
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
