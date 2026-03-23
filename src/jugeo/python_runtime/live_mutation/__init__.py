"""Live-mutation package for JuGeo — dynamic section injection, monkey patching, and hot reload.

This package implements Chapter 23 of ``theory2.tex``: the sheaf-theoretic
semantics of *live code mutation*.  Running a string with :func:`exec` or
:func:`eval`, replacing an attribute with a monkey patch, and reloading a
module at runtime are all *section operations* — they insert, replace, or
query sections in the semantic sheaf at runtime coordinates.

Package structure
-----------------

models
    Canonical data models: :class:`DynamicSection`, :class:`ExecContext`,
    :class:`EvalResult`, :class:`MonkeyPatchRecord`, :class:`HotReloadEvent`,
    and the enums :class:`MutationKind`, :class:`InvalidationScope`,
    :class:`ReloadStatus`, :class:`TrustTier`.

manifest
    Package manifest, symbol registry, theory-alignment cross-references,
    and validation utilities for the live_mutation exports.  The module-level
    :data:`LIVE_MUTATION_MANIFEST` and :data:`DEFAULT_REGISTRY` objects are
    pre-populated with every key export across the package.

exec_eval_injection
    Ch23 §1 — exec/eval as dynamic section injection.  :class:`ExecInjector`
    performs namespace-tracked code execution; :class:`EvalQuerier` records
    expression evaluations; :class:`NamespaceTracker` maintains symbol
    provenance; :class:`DynamicTrustAssigner` maps code properties to trust
    tiers.

monkey_patching
    Ch23 §2 — monkey patching as section replacement with invalidation.
    :class:`MonkeyPatcher` applies and reverts attribute patches;
    :class:`InvalidationTrigger` computes and fires BFS invalidation cascades;
    :class:`PatchStack` provides ordered LIFO patch management;
    :class:`PatchAuditor` keeps a full audit trail.

hot_reload
    Ch23 §3 — hot reload as incremental descent.  :class:`HotReloadEngine`
    manages reload lifecycle; :class:`DescentPlanner` produces topological
    reload plans; :class:`ReloadRollback` checkpoints and reverses descent
    steps; :class:`ConsistencyChecker` verifies pairwise section overlap
    agreement.

algorithms
    Higher-level algorithms: :class:`LiveMutationTracker` (session-wide
    mutation log), :class:`InvalidationEngine` (cascade computation with
    cycle detection), :class:`HotReloadPlanner` (plan generation and cost
    estimation), :class:`DynamicSectionValidator` (quarantine and release).

integration
    Integration bridges to other JuGeo subsystems:
    :class:`SupportBridge` ↔ geometry supports,
    :class:`JudgmentBridge` ↔ judgment trust algebra,
    :class:`ChannelBridge` ↔ evidence channels,
    :class:`FleetBridge` ↔ fleet orchestration.
    :class:`LiveMutationIntegration` wires them together.

theorems
    Formal theorem statements and proof sketches for Ch23.  Eight canonical
    theorems covering exec injection, eval semantics, patch invalidation, patch
    stack ordering, hot reload descent, rollback, trust bounding, and cascade
    termination.  :data:`DEFAULT_LIBRARY` is pre-populated.

Theory alignment
----------------

All objects in this package align with Ch23 of ``preliminaries/theory2.tex``.
Dynamic sections carry proposal-tier trust by default and must be externally
corroborated before trust promotion.  Invalidation cascades are guaranteed to
terminate under the acyclicity assumption (Theorem Ch23.T8).

Usage example
-------------

.. code-block:: python

    from jugeo.python_runtime.live_mutation import (
        ExecInjector, EvalQuerier, DynamicTrustAssigner,
        LiveMutationTracker, DEFAULT_LIBRARY,
    )

    injector = ExecInjector()
    record   = injector.inject("x = 42", ctx_id, globals())
    tracker  = LiveMutationTracker()
    tracker.track_exec(record["section_id"], "x = 42", ["x"], ctx_id)

    thm = DEFAULT_LIBRARY.get("Ch23.T1")
    print(thm.statement)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# models — canonical data models
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.models import (
        MutationKind,
        InvalidationScope,
        ReloadStatus,
        TrustTier,
        ExecContext,
        DynamicSection,
        EvalResult,
        MonkeyPatchRecord,
        HotReloadEvent,
        new_section_id,
        new_context_id,
        new_patch_id,
        new_event_id,
        new_result_id,
    )
except ImportError:  # pragma: no cover - isolated import guard
    MutationKind = InvalidationScope = ReloadStatus = TrustTier = None  # type: ignore[assignment]
    ExecContext = DynamicSection = EvalResult = MonkeyPatchRecord = HotReloadEvent = None  # type: ignore[assignment]
    new_section_id = new_context_id = new_patch_id = new_event_id = new_result_id = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# manifest — package manifest and theory alignment
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.manifest import (
        VERSION,
        THEORY_CHAPTER,
        MUTATION_RISK_LEVELS,
        PACKAGE_NAME,
        MutationRiskLevel,
        MutationCategory,
        SymbolRecord,
        LiveMutationManifest,
        ManifestValidator,
        ManifestRegistry,
        TheoryAlignment,
        LIVE_MUTATION_MANIFEST,
        DEFAULT_REGISTRY,
    )
except ImportError:  # pragma: no cover
    VERSION = "0.1.0"
    THEORY_CHAPTER = 23
    MUTATION_RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    PACKAGE_NAME = "jugeo.python_runtime.live_mutation"
    MutationRiskLevel = MutationCategory = SymbolRecord = None  # type: ignore[assignment]
    LiveMutationManifest = ManifestValidator = ManifestRegistry = TheoryAlignment = None  # type: ignore[assignment]
    LIVE_MUTATION_MANIFEST = DEFAULT_REGISTRY = None

# ---------------------------------------------------------------------------
# exec_eval_injection — Ch23 §1
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.exec_eval_injection import (
        ExecInjector,
        EvalQuerier,
        NamespaceTracker,
        DynamicTrustAssigner,
        make_exec_injector,
        make_eval_querier,
        make_namespace_tracker,
        make_dynamic_trust_assigner,
    )
except ImportError:  # pragma: no cover
    ExecInjector = EvalQuerier = NamespaceTracker = DynamicTrustAssigner = None  # type: ignore[assignment]
    make_exec_injector = make_eval_querier = make_namespace_tracker = make_dynamic_trust_assigner = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# monkey_patching — Ch23 §2
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.monkey_patching import (
        MonkeyPatcher,
        InvalidationTrigger,
        PatchStack,
        PatchAuditor,
        make_monkey_patcher,
        make_invalidation_trigger,
        make_patch_stack,
        make_patch_auditor,
    )
except ImportError:  # pragma: no cover
    MonkeyPatcher = InvalidationTrigger = PatchStack = PatchAuditor = None  # type: ignore[assignment]
    make_monkey_patcher = make_invalidation_trigger = make_patch_stack = make_patch_auditor = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# hot_reload — Ch23 §3
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.hot_reload import (
        HotReloadEngine,
        DescentPlanner,
        ReloadRollback,
        ConsistencyChecker,
        make_hot_reload_engine,
        make_descent_planner,
        make_reload_rollback,
        make_consistency_checker,
    )
except ImportError:  # pragma: no cover
    HotReloadEngine = DescentPlanner = ReloadRollback = ConsistencyChecker = None  # type: ignore[assignment]
    make_hot_reload_engine = make_descent_planner = make_reload_rollback = make_consistency_checker = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# algorithms — session-wide algorithmic components
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.algorithms import (
        LiveMutationTracker,
        InvalidationEngine,
        HotReloadPlanner,
        DynamicSectionValidator,
    )
except ImportError:  # pragma: no cover
    LiveMutationTracker = InvalidationEngine = HotReloadPlanner = DynamicSectionValidator = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# integration — bridges to other JuGeo subsystems
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.integration import (
        SupportBridge,
        JudgmentBridge,
        ChannelBridge,
        FleetBridge,
        LiveMutationIntegration,
    )
except ImportError:  # pragma: no cover
    SupportBridge = JudgmentBridge = ChannelBridge = FleetBridge = LiveMutationIntegration = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# theorems — Ch23 formal theorem library
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.live_mutation.theorems import (
        TheoremStatus,
        ProofMethod,
        TheoremRecord,
        TheoremProver,
        TheoremLibrary,
        DEFAULT_LIBRARY,
        THEOREM_EXEC_SECTION_INJECTION,
        THEOREM_EVAL_QUERY_SEMANTICS,
        THEOREM_MONKEY_PATCH_INVALIDATION,
        THEOREM_PATCH_STACK_ORDERING,
        THEOREM_HOT_RELOAD_DESCENT,
        THEOREM_RELOAD_ROLLBACK,
        THEOREM_DYNAMIC_SECTION_TRUST,
        THEOREM_INVALIDATION_CASCADE,
    )
except ImportError:  # pragma: no cover
    TheoremStatus = ProofMethod = TheoremRecord = TheoremProver = TheoremLibrary = None  # type: ignore[assignment]
    DEFAULT_LIBRARY = None
    THEOREM_EXEC_SECTION_INJECTION = THEOREM_EVAL_QUERY_SEMANTICS = None
    THEOREM_MONKEY_PATCH_INVALIDATION = THEOREM_PATCH_STACK_ORDERING = None
    THEOREM_HOT_RELOAD_DESCENT = THEOREM_RELOAD_ROLLBACK = None
    THEOREM_DYNAMIC_SECTION_TRUST = THEOREM_INVALIDATION_CASCADE = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # ---- package metadata ----
    "VERSION",
    "THEORY_CHAPTER",
    "MUTATION_RISK_LEVELS",
    "PACKAGE_NAME",
    # ---- models ----
    "MutationKind",
    "InvalidationScope",
    "ReloadStatus",
    "TrustTier",
    "ExecContext",
    "DynamicSection",
    "EvalResult",
    "MonkeyPatchRecord",
    "HotReloadEvent",
    "new_section_id",
    "new_context_id",
    "new_patch_id",
    "new_event_id",
    "new_result_id",
    # ---- manifest ----
    "MutationRiskLevel",
    "MutationCategory",
    "SymbolRecord",
    "LiveMutationManifest",
    "ManifestValidator",
    "ManifestRegistry",
    "TheoryAlignment",
    "LIVE_MUTATION_MANIFEST",
    "DEFAULT_REGISTRY",
    # ---- §1 exec/eval injection ----
    "ExecInjector",
    "EvalQuerier",
    "NamespaceTracker",
    "DynamicTrustAssigner",
    "make_exec_injector",
    "make_eval_querier",
    "make_namespace_tracker",
    "make_dynamic_trust_assigner",
    # ---- §2 monkey patching ----
    "MonkeyPatcher",
    "InvalidationTrigger",
    "PatchStack",
    "PatchAuditor",
    "make_monkey_patcher",
    "make_invalidation_trigger",
    "make_patch_stack",
    "make_patch_auditor",
    # ---- §3 hot reload ----
    "HotReloadEngine",
    "DescentPlanner",
    "ReloadRollback",
    "ConsistencyChecker",
    "make_hot_reload_engine",
    "make_descent_planner",
    "make_reload_rollback",
    "make_consistency_checker",
    # ---- algorithms ----
    "LiveMutationTracker",
    "InvalidationEngine",
    "HotReloadPlanner",
    "DynamicSectionValidator",
    # ---- integration ----
    "SupportBridge",
    "JudgmentBridge",
    "ChannelBridge",
    "FleetBridge",
    "LiveMutationIntegration",
    # ---- theorems ----
    "TheoremStatus",
    "ProofMethod",
    "TheoremRecord",
    "TheoremProver",
    "TheoremLibrary",
    "DEFAULT_LIBRARY",
    "THEOREM_EXEC_SECTION_INJECTION",
    "THEOREM_EVAL_QUERY_SEMANTICS",
    "THEOREM_MONKEY_PATCH_INVALIDATION",
    "THEOREM_PATCH_STACK_ORDERING",
    "THEOREM_HOT_RELOAD_DESCENT",
    "THEOREM_RELOAD_ROLLBACK",
    "THEOREM_DYNAMIC_SECTION_TRUST",
    "THEOREM_INVALIDATION_CASCADE",
    # ---- cross-references ----
    "mutation_countermodel",
    "mutation_encoding",
    "mutation_memory",
]


# ---------------------------------------------------------------------------
# Cross-subsystem functions
# ---------------------------------------------------------------------------


def mutation_countermodel(mutation: object) -> object:
    """Generate a countermodel for a live mutation.

    Uses :mod:`jugeo.solver.countermodels` to produce a concrete
    counterexample demonstrating how the mutation can violate a section
    invariant.

    Parameters
    ----------
    mutation : object
        A mutation record (e.g. :class:`DynamicSection`,
        :class:`MonkeyPatchRecord`).

    Returns
    -------
    object
        A countermodel object, or *None* if the solver is unavailable.
    """
    try:
        from jugeo.solver.countermodels import build_countermodel
    except ImportError:
        return None

    kind = getattr(mutation, "kind", getattr(mutation, "mutation_kind", "unknown"))
    section_id = getattr(mutation, "section_id", "unknown")
    return build_countermodel(
        label=f"mutation_{kind}_{section_id}",
        constraints={"kind": str(kind), "section_id": str(section_id)},
    )


def mutation_encoding(mutation: object) -> object:
    """Encode a live mutation for Z3 constraint solving.

    Uses :mod:`jugeo.encodings.sequence_mutation_encodings` to produce a
    Z3-compatible encoding of the mutation's state transition.

    Parameters
    ----------
    mutation : object
        A mutation record.

    Returns
    -------
    object
        A Z3 encoding, or *None* if the encoding layer is unavailable.
    """
    try:
        from jugeo.encodings.sequence_mutation_encodings import encode_mutation
    except ImportError:
        return None

    kind = getattr(mutation, "kind", getattr(mutation, "mutation_kind", "unknown"))
    section_id = getattr(mutation, "section_id", "unknown")
    return encode_mutation(
        label=f"mutation_{section_id}",
        kind=str(kind),
        section_id=str(section_id),
    )


def mutation_memory(mutation: object) -> dict:
    """Record a live mutation in the runtime memory layer.

    Uses :mod:`jugeo.runtime.memory` to persist the mutation event for
    later replay and analysis.

    Parameters
    ----------
    mutation : object
        A mutation record.

    Returns
    -------
    dict
        A memory entry dict with keys ``"address"``, ``"kind"``, and
        ``"timestamp"``.
    """
    try:
        from jugeo.runtime.memory import store_event
    except ImportError:
        import time as _time

        return {
            "address": getattr(mutation, "section_id", "unknown"),
            "kind": str(getattr(mutation, "kind", "unknown")),
            "timestamp": _time.time(),
        }

    return store_event(
        address=getattr(mutation, "section_id", "unknown"),
        kind=str(getattr(mutation, "kind", getattr(mutation, "mutation_kind", "unknown"))),
        payload={"mutation": str(mutation)},
    )


# copilot: public API re-exports for jugeo.python_runtime.live_mutation (Ch23)


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import epoch_indexed_module_and_object_su
except Exception:
    pass
try:
    from . import exec_and_eval_as_bounded_or_residu
except Exception:
    pass
try:
    from . import exec_eval_injection
except Exception:
    pass
try:
    from . import hot_reload
except Exception:
    pass
try:
    from . import hot_reload_and_development_mode_se
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
    from . import monkey_patching
except Exception:
    pass
try:
    from . import monkey_patching_and_late_rebinding
except Exception:
    pass
try:
    from . import semantic_apertures_in_the_python_w
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
