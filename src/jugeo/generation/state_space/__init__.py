"""Package scaffold for JuGeo generated modules."""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Cross-subsystem state-space helpers
# ---------------------------------------------------------------------------


def state_from_memory(memory: Any) -> dict[str, Any]:
    """Reconstruct a state-space snapshot from runtime memory.

    Uses :mod:`jugeo.runtime.memory` to deserialise the persisted
    memory layout into a state-space representation that the
    generation pipeline can resume from.
    """
    try:
        from jugeo.runtime.memory import load_state  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        load_state = None

    if load_state is not None:
        state = load_state(memory)
    else:
        state = getattr(memory, "state", {})

    return {
        "memory": memory,
        "state": state,
        "source": "jugeo.runtime.memory",
    }


def state_judgment(state: Any) -> dict[str, Any]:
    """Derive judgment terms for every entry in a state space.

    Uses :mod:`jugeo.judgments.judgment_terms` to map each state
    entry to its corresponding formal judgment, enabling downstream
    verification steps.
    """
    try:
        from jugeo.judgments.judgment_terms import derive_judgment  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        derive_judgment = None

    entries = getattr(state, "entries", [state])
    judgments = []
    for entry in entries:
        if derive_judgment is not None:
            judgments.append(derive_judgment(entry))
        else:
            judgments.append({"term": str(entry), "valid": None})

    return {
        "state": state,
        "judgments": judgments,
        "source": "jugeo.judgments.judgment_terms",
    }


def state_encoding(state: Any) -> dict[str, Any]:
    """Encode a state-space snapshot for serialisation or transport.

    Uses :mod:`jugeo.encodings` to produce a compact, round-trippable
    encoding of *state* suitable for caching, logging, or network
    transfer.
    """
    try:
        from jugeo.encodings import encode  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        encode = None

    if encode is not None:
        encoded = encode(state)
    else:
        encoded = {"raw": str(state)}

    return {
        "state": state,
        "encoded": encoded,
        "source": "jugeo.encodings",
    }


__all__ = [
    "state_from_memory",
    "state_judgment",
    "state_encoding",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import backtracking
except Exception:
    pass
try:
    from . import convergence_detection
except Exception:
    pass
try:
    from . import frontier_management
except Exception:
    pass
try:
    from . import generation_as_section_construction
except Exception:
    pass
try:
    from . import generation_moves_as_dependent_tran
except Exception:
    pass
try:
    from . import implementation_consequences
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
    from . import pruning
except Exception:
    pass
try:
    from . import search_strategies
except Exception:
    pass
try:
    from . import state_merging
except Exception:
    pass
try:
    from . import state_representation
except Exception:
    pass
try:
    from . import state_serialization
except Exception:
    pass
try:
    from . import the_core_state_space_for_generatio
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
