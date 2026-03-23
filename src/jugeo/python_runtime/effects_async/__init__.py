"""Effects and async runtime package for JuGeo python_runtime Ch18.

Implements theory2.tex Chapter 18: Exceptions, context managers,
iterators, generators, and async as typed judgment sections over a
semantic site.

Copilot-assisted generation governed by the JuGeo trust algebra.
"""

from jugeo.python_runtime.effects_async.models import (
    ExceptionSection,
    ContextScope,
    AsyncSection,
    GeneratorSection,
    CancellationRecord,
)
from jugeo.python_runtime.effects_async.manifest import MANIFEST

__all__ = [
    "ExceptionSection",
    "ContextScope",
    "AsyncSection",
    "GeneratorSection",
    "CancellationRecord",
    "MANIFEST",
    # cross-references
    "effect_judgment",
    "effect_encoding",
    "effect_orchestration",
]


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def effect_judgment(effect: object) -> tuple:
    """Create a judgment term for an async effect.

    Uses :mod:`jugeo.judgments.judgment_terms` to build a judgment term
    that captures the effect's kind (exception, context, async, generator)
    and its associated trust level.

    Parameters
    ----------
    effect : object
        An effect record (e.g. :class:`ExceptionSection`,
        :class:`AsyncSection`).

    Returns
    -------
    tuple
        A judgment term tuple.
    """
    try:
        from jugeo.judgments.judgment_terms import make_judgment_term
    except ImportError:
        return ("effect", type(effect).__name__, str(effect), None, False, False, (), 0)

    kind = type(effect).__name__
    coordinate = getattr(effect, "coordinate", getattr(effect, "name", "effect"))
    return make_judgment_term(
        coordinate=coordinate,
        kind=f"effect_{kind}",
        parameters=(),
        return_type=None,
        is_async=isinstance(effect, AsyncSection) if AsyncSection is not None else False,
        is_generator=isinstance(effect, GeneratorSection) if GeneratorSection is not None else False,
        decorators=(),
        trust_level=getattr(effect, "trust_level", 0),
    )


def effect_encoding(effect: object) -> object:
    """Encode an effect for Z3 constraint solving.

    Uses :mod:`jugeo.encodings` to produce a Z3-compatible encoding of
    the effect's control-flow constraints.

    Parameters
    ----------
    effect : object
        An effect record.

    Returns
    -------
    object
        A Z3 encoding, or *None* if the encoding layer is unavailable.
    """
    try:
        from jugeo.encodings import encode_value
    except ImportError:
        return None

    kind = type(effect).__name__
    return encode_value(
        label=f"effect_{kind}",
        value=getattr(effect, "name", str(effect)),
        domain="effect",
    )


def effect_orchestration(effects: object) -> dict:
    """Orchestrate checking of multiple async effects.

    Uses :mod:`jugeo.orchestration` to schedule and coordinate effect
    verification across the runtime boundary.

    Parameters
    ----------
    effects : object
        An iterable of effect records.

    Returns
    -------
    dict
        An orchestration plan dict with keys ``"tasks"`` and ``"status"``.
    """
    try:
        from jugeo.orchestration import schedule_tasks
    except ImportError:
        effect_list = list(effects) if hasattr(effects, "__iter__") else [effects]
        return {
            "tasks": [str(e) for e in effect_list],
            "status": "unscheduled",
        }

    effect_list = list(effects) if hasattr(effects, "__iter__") else [effects]
    tasks = [
        {
            "kind": "effect_check",
            "target": getattr(e, "name", str(e)),
            "effect_type": type(e).__name__,
        }
        for e in effect_list
    ]
    return schedule_tasks(tasks=tasks, domain="effects_async")


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import context_managers
except Exception:
    pass
try:
    from . import context_managers_temporal_obligati
except Exception:
    pass
try:
    from . import exceptions
except Exception:
    pass
try:
    from . import exceptions_as_alternate_semantic_p
except Exception:
    pass
try:
    from . import generators
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
    from . import theorems
except Exception:
    pass
try:
    from . import async_primitives
except Exception:
    pass
try:
    from . import async_and_task_semantics_suspended
except Exception:
    pass
