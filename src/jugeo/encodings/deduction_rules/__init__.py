"""JuGeo deduction rules package -- theory2.tex Chapter 33.

This package encodes the deduction rules, inference rule schemas, judgment
transition semantics, and structural / semantic meta-theorems described in
Chapter 33 of theory2.tex.  It provides:

- **models**: Core dataclasses for rules and rule-sets.
- **manifest**: Package-level manifest and capability declarations.
- **inference_rules**: Propositional and modal inference rules.
- **judgment_transitions**: Judgment transition system (small-step).
- **structural_rules**: Weakening, contraction, and exchange schemas.
- **semantic_rules**: Kripke-model semantic rules.
- **algorithms**: Algorithm implementations (saturation, normalisation, …).
- **integration**: Z3 and external-solver integration helpers.
- **theorems**: First-class theorem objects for the five Ch33 meta-theorems.

All submodule imports are guarded with ``try/except`` so the package degrades
gracefully when only a subset of JuGeo is installed.

Typical usage::

    from jugeo.encodings.deduction_rules import REGISTRY, get_manifest
    print(REGISTRY.copilot_status_report())
    print(get_manifest())
"""

from __future__ import annotations

__version__ = "0.1.0"
__author__ = "JuGeo Project"

# ---------------------------------------------------------------------------
# Submodule imports — each wrapped individually for resilience
# ---------------------------------------------------------------------------

# .models
try:
    from .models import (  # type: ignore[import]
        DeductionRule,
        RuleSet,
        RuleKind,
        RuleSchema,
        RuleMetadata,
    )
    _models_ok = True
except ImportError:
    _models_ok = False

    class DeductionRule:  # type: ignore[no-redef]
        """Stub DeductionRule."""

    class RuleSet:  # type: ignore[no-redef]
        """Stub RuleSet."""

    class RuleKind:  # type: ignore[no-redef]
        """Stub RuleKind."""

    class RuleSchema:  # type: ignore[no-redef]
        """Stub RuleSchema."""

    class RuleMetadata:  # type: ignore[no-redef]
        """Stub RuleMetadata."""

# .manifest
try:
    from .manifest import (  # type: ignore[import]
        MANIFEST,
        get_manifest as _manifest_get_manifest,
        ManifestEntry,
        PackageManifest,
    )
    _manifest_ok = True
except ImportError:
    _manifest_ok = False
    MANIFEST = None  # type: ignore[assignment]
    ManifestEntry = None  # type: ignore[assignment]
    PackageManifest = None  # type: ignore[assignment]

    def _manifest_get_manifest() -> dict:  # type: ignore[return]
        return {"package": "jugeo.encodings.deduction_rules", "version": __version__}

# .inference_rules
try:
    from .inference_rules import *  # type: ignore[import]  # noqa: F401,F403
    from .inference_rules import __all__ as _s01_all
    _s01_ok = True
except ImportError:
    _s01_ok = False
    _s01_all: list[str] = []

# .judgment_transitions
try:
    from .judgment_transitions import *  # type: ignore[import]  # noqa: F401,F403
    from .judgment_transitions import __all__ as _s02_all
    _s02_ok = True
except ImportError:
    _s02_ok = False
    _s02_all: list[str] = []

# .structural_rules
try:
    from .structural_rules import *  # type: ignore[import]  # noqa: F401,F403
    from .structural_rules import __all__ as _s03_all
    _s03_ok = True
except ImportError:
    _s03_ok = False
    _s03_all: list[str] = []

# .semantic_rules
try:
    from .semantic_rules import *  # type: ignore[import]  # noqa: F401,F403
    from .semantic_rules import __all__ as _s04_all
    _s04_ok = True
except ImportError:
    _s04_ok = False
    _s04_all: list[str] = []

# .algorithms
try:
    from .algorithms import *  # type: ignore[import]  # noqa: F401,F403
    from .algorithms import __all__ as _alg_all
    _algorithms_ok = True
except ImportError:
    _algorithms_ok = False
    _alg_all: list[str] = []

# .integration
try:
    from .integration import *  # type: ignore[import]  # noqa: F401,F403
    from .integration import __all__ as _int_all
    _integration_ok = True
except ImportError:
    _integration_ok = False
    _int_all: list[str] = []

# .theorems  (always attempt — this module is part of the same package)
try:
    from .theorems import (  # noqa: F401
        VerificationStatus,
        TheoremKind,
        ProofMethod,
        Theorem,
        CutEliminationTheorem,
        StructuralAdmissibilityTheorem,
        SemanticSoundnessTheorem,
        ConfluenceTheorem,
        CompletenessTheorem,
        TheoremRegistry,
        CUT_ELIMINATION,
        STRUCTURAL_ADMISSIBILITY,
        SEMANTIC_SOUNDNESS,
        CONFLUENCE,
        COMPLETENESS,
        REGISTRY,
    )
    _theorems_ok = True
except ImportError:
    _theorems_ok = False
    # Minimal stubs so the package remains importable.
    VerificationStatus = None  # type: ignore[assignment]
    TheoremKind = None  # type: ignore[assignment]
    ProofMethod = None  # type: ignore[assignment]
    Theorem = None  # type: ignore[assignment]
    CutEliminationTheorem = None  # type: ignore[assignment]
    StructuralAdmissibilityTheorem = None  # type: ignore[assignment]
    SemanticSoundnessTheorem = None  # type: ignore[assignment]
    ConfluenceTheorem = None  # type: ignore[assignment]
    CompletenessTheorem = None  # type: ignore[assignment]
    TheoremRegistry = None  # type: ignore[assignment]
    CUT_ELIMINATION = None  # type: ignore[assignment]
    STRUCTURAL_ADMISSIBILITY = None  # type: ignore[assignment]
    SEMANTIC_SOUNDNESS = None  # type: ignore[assignment]
    CONFLUENCE = None  # type: ignore[assignment]
    COMPLETENESS = None  # type: ignore[assignment]
    REGISTRY = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def get_manifest() -> dict:
    """Return the package manifest as a plain dictionary.

    When the manifest submodule is available the full machine-readable
    manifest is returned.  Otherwise a minimal dict is synthesised from
    ``__version__`` and the load-status of each submodule.
    """
    base = _manifest_get_manifest()
    base.setdefault("version", __version__)
    base["submodule_status"] = {
        "models": _models_ok,
        "manifest": _manifest_ok,
        "inference_rules": _s01_ok,
        "judgment_transitions": _s02_ok,
        "structural_rules": _s03_ok,
        "semantic_rules": _s04_ok,
        "algorithms": _algorithms_ok,
        "integration": _integration_ok,
        "theorems": _theorems_ok,
    }
    if _theorems_ok and REGISTRY is not None:
        base["theorem_count"] = len(REGISTRY)
        base["verified_theorems"] = [
            t.theorem_id for t in REGISTRY.all_verified()
        ]
    return base


def create_session(
    *,
    enable_z3: bool = True,
    enable_reconstruction: bool = True,
) -> dict:
    """Create and return a deduction session configuration dictionary.

    The returned dict can be passed to solver helpers and algorithm
    functions throughout the package as a lightweight session handle.

    Parameters
    ----------
    enable_z3:
        Attempt to instantiate a Z3 session.  Set to ``False`` in
        environments where Z3 is not installed.
    enable_reconstruction:
        Include a model-reconstruction stub in the session.
    """
    import uuid as _uuid
    import time as _time

    session: dict = {
        "session_id": str(_uuid.uuid4()),
        "created_at": _time.time(),
        "package_version": __version__,
        "registry": REGISTRY,
        "z3_session": None,
        "reconstruction": None,
        "flags": {
            "enable_z3": enable_z3,
            "enable_reconstruction": enable_reconstruction,
        },
    }

    if enable_z3:
        try:
            from jugeo.solver.z3_session import Z3Session  # type: ignore[import]
            session["z3_session"] = Z3Session()
        except ImportError:
            session["z3_session"] = None

    if enable_reconstruction:
        try:
            from jugeo.solver.reconstruction import ModelReconstruction  # type: ignore[import]
            session["reconstruction"] = ModelReconstruction()
        except ImportError:
            session["reconstruction"] = None

    return session


def copilot_assist(query: str = "") -> str:
    """Return a natural-language description of this package for Copilot.

    When *query* is non-empty it is used to filter the output to the most
    relevant subset of theorems and submodules.

    Examples::

        >>> print(copilot_assist())
        >>> print(copilot_assist("cut elimination"))
    """
    # copilot natural language entry point for the deduction_rules package
    lines = [
        "# jugeo.encodings.deduction_rules — Copilot overview",
        "",
        f"Package version: {__version__}",
        "",
        "This package encodes the JuGeo deduction rule system from theory2.tex "
        "Chapter 33.  It provides inference rules, judgment transitions, "
        "structural and semantic rules, and five major meta-theorems.",
        "",
    ]

    if _theorems_ok and REGISTRY is not None:
        lines.append(REGISTRY.copilot_status_report())
        lines.append("")
        if query:
            q = query.lower()
            lines.append(f"## Theorems matching '{query}'")
            for thm in REGISTRY:
                if q in thm.name.lower() or q in thm.statement.lower():
                    lines.append("")
                    lines.append(thm.copilot_explain())
    else:
        lines.append(
            "⚠️  The `theorems` submodule could not be imported.  "
            "Run `pip install jugeo[full]` for complete functionality."
        )

    lines += [
        "",
        "## Submodule load status",
    ]
    manifest = get_manifest()
    for mod, ok in manifest.get("submodule_status", {}).items():
        icon = "✅" if ok else "❌"
        lines.append(f"  {icon} {mod}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Package metadata
    "__version__",
    "__author__",
    # Convenience functions
    "get_manifest",
    "create_session",
    "copilot_assist",
    # From .models
    "DeductionRule",
    "RuleSet",
    "RuleKind",
    "RuleSchema",
    "RuleMetadata",
    # From .manifest
    "MANIFEST",
    "ManifestEntry",
    "PackageManifest",
    # From .theorems — enumerations
    "VerificationStatus",
    "TheoremKind",
    "ProofMethod",
    # From .theorems — dataclasses
    "Theorem",
    "CutEliminationTheorem",
    "StructuralAdmissibilityTheorem",
    "SemanticSoundnessTheorem",
    "ConfluenceTheorem",
    "CompletenessTheorem",
    "TheoremRegistry",
    # From .theorems — canonical instances
    "CUT_ELIMINATION",
    "STRUCTURAL_ADMISSIBILITY",
    "SEMANTIC_SOUNDNESS",
    "CONFLUENCE",
    "COMPLETENESS",
    "REGISTRY",
    # Dynamic re-exports from wildcard imports
    *_s01_all,
    *_s02_all,
    *_s03_all,
    *_s04_all,
    *_alg_all,
    *_int_all,
]

