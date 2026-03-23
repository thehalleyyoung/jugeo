"""JuGeo concurrency_boundaries package — Ch24 of theory2.tex.

Overview
--------
The ``concurrency_boundaries`` package implements the four primary concurrency
boundary constructs introduced in Chapter 24 of ``preliminaries/theory2.tex``:

1. **Task-local context as scoped sections** (Ch24.1)
   Python's ``contextvars`` module provides task-local context propagation.
   In JuGeo terms, each active context variable snapshot corresponds to a
   *scoped section* in the presheaf of task-local bindings.  The
   :class:`~jugeo.python_runtime.concurrency_boundaries.models.TaskLocalSection`
   class models one such section: an immutable snapshot of binding names that
   are visible only within a specific task and its children.  Restriction maps
   in the presheaf correspond to the ``parent_section_id`` chain.

2. **Cancellation as obstruction injection** (Ch24.2)
   ``asyncio.CancelledError`` and ``concurrent.futures.CancelledError`` are,
   from the theory's perspective, *injected obstruction classes* in the Čech
   cohomology of the task graph.  The
   :class:`~jugeo.python_runtime.concurrency_boundaries.models.CancellationRecord`
   class captures the obstruction key, the reason, the affected sections, and
   the optional parent cancellation record that triggered propagation.

3. **Exception groups as multi-obstruction records** (Ch24.3)
   Python 3.11+ ``ExceptionGroup`` (PEP 654) encodes *multiple simultaneous*
   obstruction classes, none of which subsumes another.  The
   :class:`~jugeo.python_runtime.concurrency_boundaries.models.ExceptionGroupRecord`
   class is a mutable container for serialised exception dicts with a
   ``resolve()`` method that records the discharge strategy.

4. **Process boundaries as cover boundaries** (Ch24.4)
   IPC channels, network sockets, and shared-memory regions are *cover
   boundaries* in the Grothendieck topology of the deployment site.  The
   :class:`~jugeo.python_runtime.concurrency_boundaries.models.ProcessBoundary`
   class models one such boundary: which sections are permitted to cross
   (``allowed_section_ids``), which cover morphism it corresponds to
   (``cover_morphism_id``), and whether it is currently active.

Package structure
-----------------
The package is organised as follows:

* ``models`` — core frozen and mutable dataclasses for the four constructs plus
  the :class:`~models.ConcurrencyScope` hierarchy node.
* ``manifest`` — symbol registry, validation, version-aware tracking, and
  Ch24 theory alignment index.
* ``s01`` … ``s03`` — section-level implementation modules (one per Ch24
  section group).
* ``algorithms`` — algorithms operating on scopes, sections, and boundaries
  (e.g. cancellation propagation, obstruction composition).
* ``integration`` — integration with Python's asyncio, concurrent.futures, and
  multiprocessing layers.
* ``theorems`` — formal statement objects corresponding to Ch24 theorems T1–T5.

Theory alignment
----------------
Every public symbol is mapped to a specific Ch24 section via the
:class:`~manifest.TheoryAlignment` class.  The mapping is queryable at runtime
and validated by :class:`~manifest.ManifestValidator`.  Symbols whose
theory_ref fields are empty are accepted but trigger a validator warning.

Design principles
-----------------
* Frozen dataclasses use ``slots=True`` for memory efficiency.
* Mutable dataclasses use ``slots=False`` to preserve ``__dict__`` access.
* All enums inherit from ``(str, Enum)`` to support JSON serialisation.
* No third-party dependencies — only the Python standard library.
* Timestamps use ``time.time()``; IDs use ``uuid.uuid4().hex``.
* Every module ends with the ``# copilot: shared-core marker`` comment.

Usage example
-------------
::

    from jugeo.python_runtime.concurrency_boundaries import (
        make_task_section,
        make_scope,
        CancellationReason,
        ConcurrencyRole,
    )

    scope = make_scope(role=ConcurrencyRole.TASK)
    section = make_task_section(task_id="t1", task_name="my_task")
    scope.add_section(section)
    cancelled = scope.cancel_all(reason=CancellationReason.TIMEOUT)
    print(f"Cancelled {len(cancelled)} sections")

Version history
---------------
* ``0.1.0`` — initial implementation covering Ch24.1–Ch24.5.
"""

from __future__ import annotations

import importlib
import time
import warnings

# ══════════════════════════════════════════════════════════════════════════════
# Package-level constants
# ══════════════════════════════════════════════════════════════════════════════

__version__: str = "0.1.0"
__theory_chapter__: str = "Ch24"
__author__: str = "copilot"

# The canonical dotted package name used in fully-qualified symbol paths.
_PACKAGE_NAME: str = "jugeo.python_runtime.concurrency_boundaries"

# Ordered list of submodule names that make up the package.
_SUBMODULES: list[str] = [
    "manifest",
    "models",
    "s01",
    "s02",
    "s03",
    "algorithms",
    "integration",
    "theorems",
]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from manifest
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.concurrency_boundaries.manifest import (
        VERSION,
        THEORY_CHAPTER,
        BOUNDARY_KINDS,
        CONCURRENCY_ROLES,
        SymbolRecord,
        ConcurrencyBoundariesManifest,
        ManifestValidator,
        ManifestRegistry,
        TheoryAlignment,
        build_default_manifest,
    )
except ImportError as _manifest_err:
    warnings.warn(
        f"concurrency_boundaries: could not import from manifest submodule "
        f"({_manifest_err}).  Manifest symbols will be unavailable.",
        ImportWarning,
        stacklevel=2,
    )
    VERSION = "0.1.0"
    THEORY_CHAPTER = "Ch24"
    BOUNDARY_KINDS = []
    CONCURRENCY_ROLES = []
    SymbolRecord = None  # type: ignore[assignment,misc]
    ConcurrencyBoundariesManifest = None  # type: ignore[assignment,misc]
    ManifestValidator = None  # type: ignore[assignment,misc]
    ManifestRegistry = None  # type: ignore[assignment,misc]
    TheoryAlignment = None  # type: ignore[assignment,misc]
    build_default_manifest = None  # type: ignore[assignment]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from models
# ══════════════════════════════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.concurrency_boundaries.models import (
        ConcurrencyRole,
        CancellationReason,
        BoundaryKind,
        ScopeStatus,
        TaskLocalSection,
        CancellationRecord,
        ExceptionGroupRecord,
        ProcessBoundary,
        ConcurrencyScope,
        make_task_section,
        make_cancellation_record,
        make_process_boundary,
        make_scope,
    )
except ImportError as _models_err:
    warnings.warn(
        f"concurrency_boundaries: could not import from models submodule "
        f"({_models_err}).  Core model symbols will be unavailable.",
        ImportWarning,
        stacklevel=2,
    )
    ConcurrencyRole = None  # type: ignore[assignment,misc]
    CancellationReason = None  # type: ignore[assignment,misc]
    BoundaryKind = None  # type: ignore[assignment,misc]
    ScopeStatus = None  # type: ignore[assignment,misc]
    TaskLocalSection = None  # type: ignore[assignment,misc]
    CancellationRecord = None  # type: ignore[assignment,misc]
    ExceptionGroupRecord = None  # type: ignore[assignment,misc]
    ProcessBoundary = None  # type: ignore[assignment,misc]
    ConcurrencyScope = None  # type: ignore[assignment,misc]
    make_task_section = None  # type: ignore[assignment]
    make_cancellation_record = None  # type: ignore[assignment]
    make_process_boundary = None  # type: ignore[assignment]
    make_scope = None  # type: ignore[assignment]

# ══════════════════════════════════════════════════════════════════════════════
# Imports from s01 (stubbed — module not yet generated)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import jugeo.python_runtime.concurrency_boundaries.s01 as _s01  # type: ignore[import]
    from jugeo.python_runtime.concurrency_boundaries.s01 import *  # type: ignore[import]  # noqa: F401,F403
except ImportError:
    warnings.warn(
        "concurrency_boundaries: s01 submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Imports from s02 (stubbed — module not yet generated)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import jugeo.python_runtime.concurrency_boundaries.s02 as _s02  # type: ignore[import]
    from jugeo.python_runtime.concurrency_boundaries.s02 import *  # type: ignore[import]  # noqa: F401,F403
except ImportError:
    warnings.warn(
        "concurrency_boundaries: s02 submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Imports from s03 (stubbed — module not yet generated)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import jugeo.python_runtime.concurrency_boundaries.s03 as _s03  # type: ignore[import]
    from jugeo.python_runtime.concurrency_boundaries.s03 import *  # type: ignore[import]  # noqa: F401,F403
except ImportError:
    warnings.warn(
        "concurrency_boundaries: s03 submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Imports from algorithms (stubbed — module not yet generated)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import jugeo.python_runtime.concurrency_boundaries.algorithms as _algorithms  # type: ignore[import]
    from jugeo.python_runtime.concurrency_boundaries.algorithms import *  # type: ignore[import]  # noqa: F401,F403
except ImportError:
    warnings.warn(
        "concurrency_boundaries: algorithms submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Imports from integration (stubbed — module not yet generated)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import jugeo.python_runtime.concurrency_boundaries.integration as _integration  # type: ignore[import]
    from jugeo.python_runtime.concurrency_boundaries.integration import *  # type: ignore[import]  # noqa: F401,F403
except ImportError:
    warnings.warn(
        "concurrency_boundaries: integration submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Imports from theorems (stubbed — module not yet generated)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import jugeo.python_runtime.concurrency_boundaries.theorems as _theorems  # type: ignore[import]
    from jugeo.python_runtime.concurrency_boundaries.theorems import *  # type: ignore[import]  # noqa: F401,F403
except ImportError:
    warnings.warn(
        "concurrency_boundaries: theorems submodule not yet available.",
        ImportWarning,
        stacklevel=2,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Package metadata helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_package_info() -> dict[str, object]:
    """Return a dictionary of package metadata.

    The returned dict is suitable for logging, introspection tools, and
    health-check endpoints.  It includes version, theory chapter, author,
    submodule list, and a snapshot of the available symbol counts per module.

    Returns:
        Dict with keys:

        * ``"name"`` — fully qualified package name.
        * ``"version"`` — semantic version string.
        * ``"theory_chapter"`` — theory chapter (``"Ch24"``).
        * ``"author"`` — package author identifier.
        * ``"submodules"`` — list of expected submodule names.
        * ``"boundary_kinds"`` — list of supported boundary kind strings.
        * ``"concurrency_roles"`` — list of supported role strings.
        * ``"generated_at"`` — Unix timestamp of this call.
    """
    return {
        "name": _PACKAGE_NAME,
        "version": __version__,
        "theory_chapter": __theory_chapter__,
        "author": __author__,
        "submodules": list(_SUBMODULES),
        "boundary_kinds": list(BOUNDARY_KINDS) if BOUNDARY_KINDS else [],
        "concurrency_roles": list(CONCURRENCY_ROLES) if CONCURRENCY_ROLES else [],
        "generated_at": time.time(),
    }


def list_exports() -> dict[str, list[str]]:
    """Return all exported symbol names grouped by submodule.

    Symbols from submodules that failed to import are omitted from the result.
    The grouping is derived from the default manifest when available, and from
    static knowledge of the ``models`` and ``manifest`` modules otherwise.

    Returns:
        Dict mapping module name strings to sorted lists of exported symbol
        names.  Example::

            {
                "manifest": ["ConcurrencyBoundariesManifest", ...],
                "models": ["BoundaryKind", ...],
            }
    """
    groups: dict[str, list[str]] = {
        "manifest": [
            "VERSION",
            "THEORY_CHAPTER",
            "BOUNDARY_KINDS",
            "CONCURRENCY_ROLES",
            "SymbolRecord",
            "ConcurrencyBoundariesManifest",
            "ManifestValidator",
            "ManifestRegistry",
            "TheoryAlignment",
            "build_default_manifest",
        ],
        "models": [
            "CancellationReason",
            "BoundaryKind",
            "CancellationRecord",
            "ConcurrencyRole",
            "ConcurrencyScope",
            "ExceptionGroupRecord",
            "ProcessBoundary",
            "ScopeStatus",
            "TaskLocalSection",
            "make_cancellation_record",
            "make_process_boundary",
            "make_scope",
            "make_task_section",
        ],
        "s01": [],
        "s02": [],
        "s03": [],
        "algorithms": [],
        "integration": [],
        "theorems": [],
    }
    # Enrich from the live manifest if available.
    if build_default_manifest is not None:
        try:
            manifest = build_default_manifest()
            for module_name in manifest.modules_present():
                symbols = [
                    r.name for r in manifest.symbols_by_module(module_name)
                ]
                groups[module_name] = sorted(symbols)
        except Exception:  # noqa: BLE001
            pass
    return groups


def health_check() -> dict[str, object]:
    """Import all package submodules and return a status report.

    Each submodule is attempted via ``importlib.import_module``.  The result
    records which modules loaded successfully and which raised ImportError.

    Returns:
        Dict with keys:

        * ``"status"`` — ``"healthy"`` if all core modules loaded, else
          ``"degraded"``.
        * ``"modules"`` — dict mapping module names to ``"ok"`` or an error
          string.
        * ``"core_ok"`` — bool, True if both ``manifest`` and ``models``
          loaded.
        * ``"checked_at"`` — Unix timestamp of this call.
        * ``"version"`` — current package version.
    """
    base = _PACKAGE_NAME
    module_status: dict[str, str] = {}
    for mod_name in _SUBMODULES:
        full_name = f"{base}.{mod_name}"
        try:
            importlib.import_module(full_name)
            module_status[mod_name] = "ok"
        except ImportError as exc:
            module_status[mod_name] = f"ImportError: {exc}"
        except Exception as exc:  # noqa: BLE001
            module_status[mod_name] = f"Error: {exc}"

    core_ok = (
        module_status.get("manifest") == "ok"
        and module_status.get("models") == "ok"
    )
    all_ok = all(v == "ok" for v in module_status.values())
    status = "healthy" if all_ok else ("degraded" if core_ok else "unhealthy")

    return {
        "status": status,
        "modules": module_status,
        "core_ok": core_ok,
        "checked_at": time.time(),
        "version": __version__,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ConcurrencyBoundariesPackage — package façade class
# ══════════════════════════════════════════════════════════════════════════════

class ConcurrencyBoundariesPackage:
    """Façade class providing class-method access to package metadata.

    This class groups all package-level introspection capabilities under a
    single namespace so that callers who prefer OO-style access can write::

        info = ConcurrencyBoundariesPackage.version()
        chapter = ConcurrencyBoundariesPackage.theory_chapter()
        ConcurrencyBoundariesPackage.validate()

    All methods are class methods; no instance needs to be created.
    """

    # ------------------------------------------------------------------
    # Static accessors
    # ------------------------------------------------------------------

    @classmethod
    def version(cls) -> str:
        """Return the current package version string.

        Returns:
            The semantic version string, e.g. ``"0.1.0"``.
        """
        return __version__

    @classmethod
    def theory_chapter(cls) -> str:
        """Return the theory chapter identifier this package implements.

        Returns:
            ``"Ch24"``.
        """
        return __theory_chapter__

    @classmethod
    def author(cls) -> str:
        """Return the package author identifier.

        Returns:
            ``"copilot"``.
        """
        return __author__

    # ------------------------------------------------------------------
    # Export and module listings
    # ------------------------------------------------------------------

    @classmethod
    def exports(cls) -> dict[str, list[str]]:
        """Return all exported symbol names grouped by submodule.

        Delegates to :func:`list_exports`.

        Returns:
            Dict mapping module name to sorted list of symbol name strings.
        """
        return list_exports()

    @classmethod
    def modules(cls) -> list[str]:
        """Return the ordered list of expected submodule names.

        Returns:
            List of module name strings.
        """
        return list(_SUBMODULES)

    @classmethod
    def flat_exports(cls) -> list[str]:
        """Return a flat sorted list of all exported symbol names.

        Returns:
            Sorted list of all symbol names across all modules.
        """
        names: list[str] = []
        for syms in list_exports().values():
            names.extend(syms)
        return sorted(set(names))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @classmethod
    def validate(cls) -> dict[str, object]:
        """Run full package validation and return a structured report.

        Validation stages:

        1. Build the default manifest (checks manifest module is importable).
        2. Run :class:`~manifest.ManifestValidator` to check symbol completeness
           and theory cross-references.
        3. Run :func:`health_check` to verify all submodules are importable.

        Returns:
            Dict with keys:

            * ``"manifest_valid"`` (bool) — manifest validation passed.
            * ``"manifest_report"`` (str) — human-readable validator output.
            * ``"health"`` (dict) — result of :func:`health_check`.
            * ``"overall_valid"`` (bool) — True only if all stages pass.
            * ``"validated_at"`` (float) — Unix timestamp.

        Raises:
            RuntimeError: If the manifest module is unavailable and validation
                cannot proceed.
        """
        if build_default_manifest is None or ManifestValidator is None:
            raise RuntimeError(
                "Cannot validate: manifest submodule failed to import."
            )
        try:
            manifest_obj = build_default_manifest()
            validator = ManifestValidator(manifest_obj)
            report_str = validator.generate_report()
            manifest_valid = "OVERALL: PASS" in report_str
        except Exception as exc:  # noqa: BLE001
            report_str = f"Validation error: {exc}"
            manifest_valid = False

        health = health_check()
        overall = manifest_valid and health.get("core_ok", False)

        return {
            "manifest_valid": manifest_valid,
            "manifest_report": report_str,
            "health": health,
            "overall_valid": overall,
            "validated_at": time.time(),
        }

    @classmethod
    def info(cls) -> dict[str, object]:
        """Return comprehensive package metadata.

        Combines :func:`get_package_info` with export listings.

        Returns:
            Dict merging get_package_info() output with an ``"exports"`` key.
        """
        result = get_package_info()
        result["exports"] = list_exports()
        return result

    @classmethod
    def summary(cls) -> str:
        """Return a one-line human-readable package summary.

        Returns:
            A formatted summary string.
        """
        return (
            f"ConcurrencyBoundariesPackage v{__version__} "
            f"(theory: {__theory_chapter__}, author: {__author__}) — "
            f"implements Ch24 concurrency boundary constructs for JuGeo."
        )

    def __repr__(cls) -> str:
        return f"<ConcurrencyBoundariesPackage v{__version__}>"


# ══════════════════════════════════════════════════════════════════════════════
# Convenience re-exports — top-level shortcuts
# ══════════════════════════════════════════════════════════════════════════════
# The following names are the most commonly used symbols.  They are explicitly
# listed here so that ``from concurrency_boundaries import X`` works without
# needing to know the submodule.

# (All already imported above from models and manifest via try/except blocks.)


# ══════════════════════════════════════════════════════════════════════════════
# __all__
# ══════════════════════════════════════════════════════════════════════════════

__all__: list[str] = [
    # Package constants
    "__version__",
    "__theory_chapter__",
    "__author__",
    # Package-level helpers
    "get_package_info",
    "list_exports",
    "health_check",
    # Façade class
    "ConcurrencyBoundariesPackage",
    # ── from manifest ──────────────────────────────────────────────────
    "VERSION",
    "THEORY_CHAPTER",
    "BOUNDARY_KINDS",
    "CONCURRENCY_ROLES",
    "SymbolRecord",
    "ConcurrencyBoundariesManifest",
    "ManifestValidator",
    "ManifestRegistry",
    "TheoryAlignment",
    "build_default_manifest",
    # ── from models ────────────────────────────────────────────────────
    "ConcurrencyRole",
    "CancellationReason",
    "BoundaryKind",
    "ScopeStatus",
    "TaskLocalSection",
    "CancellationRecord",
    "ExceptionGroupRecord",
    "ProcessBoundary",
    "ConcurrencyScope",
    "make_task_section",
    "make_cancellation_record",
    "make_process_boundary",
    "make_scope",
    # ── cross-references ──────────────────────────────────────────────
    "concurrency_judgment",
    "concurrency_evidence",
    "concurrency_encoding",
]


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def concurrency_judgment(boundary: object) -> tuple:
    """Create a judgment term for a concurrency boundary.

    Uses :mod:`jugeo.judgments.judgment_terms` to build a judgment term
    encoding the boundary's kind, scope, and cancellation status.

    Parameters
    ----------
    boundary : object
        A concurrency boundary record (e.g. :class:`TaskLocalSection`,
        :class:`CancellationRecord`, :class:`ProcessBoundary`).

    Returns
    -------
    tuple
        A judgment term tuple.
    """
    try:
        from jugeo.judgments.judgment_terms import make_judgment_term
    except ImportError:
        return ("concurrency", type(boundary).__name__, str(boundary), None, False, False, (), 0)

    kind = type(boundary).__name__
    coordinate = getattr(boundary, "section_id", getattr(boundary, "boundary_id", str(boundary)))
    return make_judgment_term(
        coordinate=coordinate,
        kind=f"concurrency_{kind}",
        parameters=(),
        return_type=None,
        is_async=True,
        is_generator=False,
        decorators=(),
        trust_level=getattr(boundary, "trust_level", 0),
    )


def concurrency_evidence(boundary: object) -> dict:
    """Collect evidence from concurrency boundary analysis.

    Uses :mod:`jugeo.evidence.channels` to record boundary analysis
    results as evidence for trust aggregation.

    Parameters
    ----------
    boundary : object
        A concurrency boundary record.

    Returns
    -------
    dict
        An evidence record dict.
    """
    try:
        from jugeo.evidence.channels import record_evidence
    except ImportError:
        return {
            "channel": "concurrency_boundaries",
            "source": "python_runtime",
            "payload": {"boundary": str(boundary)},
        }

    kind = type(boundary).__name__
    return record_evidence(
        channel="concurrency_boundaries",
        source="python_runtime.concurrency_boundaries",
        payload={
            "kind": kind,
            "boundary_id": getattr(boundary, "section_id", getattr(boundary, "boundary_id", "unknown")),
            "active": getattr(boundary, "is_active", True),
        },
    )


def concurrency_encoding(boundary: object) -> object:
    """Encode a concurrency boundary for Z3 constraint solving.

    Uses :mod:`jugeo.encodings` to produce a Z3-compatible encoding of
    the boundary's reachability and isolation constraints.

    Parameters
    ----------
    boundary : object
        A concurrency boundary record.

    Returns
    -------
    object
        A Z3 encoding, or *None* if the encoding layer is unavailable.
    """
    try:
        from jugeo.encodings import encode_value
    except ImportError:
        return None

    kind = type(boundary).__name__
    boundary_id = getattr(boundary, "section_id", getattr(boundary, "boundary_id", "boundary"))
    return encode_value(
        label=f"concurrency_{kind}_{boundary_id}",
        value=boundary_id,
        domain="concurrency",
    )


# copilot: shared-core marker for future LLM orchestration.


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import cancellation_and_exception_group_s
except Exception:
    pass
try:
    from . import cancellation_obstructions
except Exception:
    pass
try:
    from . import concurrency_in_python_is_not_one_p
except Exception:
    pass
try:
    from . import exception_groups_process_boundaries
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
    from . import process_boundaries_and_replicated
except Exception:
    pass
try:
    from . import replicated_state_obstructions
except Exception:
    pass
try:
    from . import task_local_context
except Exception:
    pass
try:
    from . import task_local_context_as_hidden_but_s
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
