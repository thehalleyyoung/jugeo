"""Package scaffold for JuGeo generated modules."""

# ---------------------------------------------------------------------------
# Unified top-level API re-exports
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import GeometricSite  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    GeometricSite = None

try:
    from jugeo.evidence.trust import TrustAlgebra  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    TrustAlgebra = None

try:
    from jugeo.judgments import construct_judgment, validate_judgment_form  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    construct_judgment = None
    validate_judgment_form = None

try:
    from jugeo.solver import solve  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    solve = None

try:
    from jugeo.easy import prove, bugs, equiv, ideate, carry, spec, research  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    prove = bugs = equiv = ideate = carry = spec = research = None

try:
    from jugeo.moves import MoveEngine, MoveKind, MoveStatus, SemanticMove  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    MoveEngine = MoveKind = MoveStatus = SemanticMove = None

__all__ = [
    "GeometricSite",
    "TrustAlgebra",
    "construct_judgment",
    "validate_judgment_form",
    "solve",
    # Easy API — one function per capability
    "prove",
    "bugs",
    "equiv",
    "spec",
    "ideate",
    "carry",
    "research",
    "MoveEngine",
    "MoveKind",
    "MoveStatus",
    "SemanticMove",
]


# --- auto-registered submodules ---
try:
    from . import __main__
except Exception:
    pass
try:
    from . import bootstrap
except Exception:
    pass
try:
    from . import errors
except Exception:
    pass
try:
    from . import package_manifest
except Exception:
    pass
try:
    from . import runtime_defaults
except Exception:
    pass
try:
    from . import benchmarks
except Exception:
    pass
try:
    from . import cli
except Exception:
    pass
try:
    from . import encodings
except Exception:
    pass
try:
    from . import evaluation
except Exception:
    pass
try:
    from . import evidence
except Exception:
    pass
try:
    from . import foundations
except Exception:
    pass
try:
    from . import generation
except Exception:
    pass
try:
    from . import geometry
except Exception:
    pass
try:
    from . import ideation
except Exception:
    pass
try:
    from . import interfaces
except Exception:
    pass
try:
    from . import judgments
except Exception:
    pass
try:
    from . import kernel
except Exception:
    pass
try:
    from . import maturity
except Exception:
    pass
try:
    from . import orchestration
except Exception:
    pass
try:
    from . import packs
except Exception:
    pass
try:
    from . import problem_modes
except Exception:
    pass
try:
    from . import python_runtime
except Exception:
    pass
try:
    from . import runtime
except Exception:
    pass
try:
    from . import solver
except Exception:
    pass
try:
    from . import thesis
except Exception:
    pass
