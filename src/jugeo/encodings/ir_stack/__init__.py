r"""Internal Representation (IR) Stack package for JuGeo.

Implements Chapter 32 of ``theory2.tex``: the full IR stack infrastructure
for semantic layers, normal forms, lowering passes, and ambiguity tracking.

.. math::

   \mathcal{S} = (\mathcal{L}_0, \mathcal{L}_1, \ldots, \mathcal{L}_n,
   \{\pi_{i \to j}\}_{i \le j})

where :math:`\mathcal{L}_k` are semantic layers and :math:`\pi_{i \to j}`
are the lowering morphisms between them.

Architecture
------------

The IR stack is a layered pipeline that transforms surface-level terms
into solver-ready encodings.  The pipeline has five standard stages:

1. **SURFACE** -- raw parsed terms with syntactic sugar intact.
2. **SEMANTIC** -- desugared terms with name resolution applied.
3. **LOGICAL** -- obligations extracted; refinement predicates inlined.
4. **SOLVER_READY** -- terms fully encoded for Z3 dispatch.
5. **CACHED** / **DELTA** -- incremental reuse of prior computations.

Each stage is represented by an :class:`IRLayer` which contains:

* ``nodes`` -- a ``dict[str, IRNode]`` of the layer's term nodes.
* ``bindings`` -- a ``dict[str, str]`` for the binding environment.
* ``constraints`` -- a ``list[dict]`` of accumulated constraints.

Lowering passes (:class:`LoweringPass`) transform one layer into the
next.  Ambiguity marks (:class:`AmbiguityMark`) travel with nodes
throughout lowering so that every unresolved syntactic choice remains
auditable at the solver level.

.. math::

   \pi_{k \to k+1}(\mathcal{L}_k) = \mathcal{L}_{k+1}
   \quad\text{such that}\quad
   \mathrm{marks}(\mathcal{L}_k) \subseteq \mathrm{marks}(\mathcal{L}_{k+1})

Public API
----------

Core data models (:mod:`.models`)
    :class:`~jugeo.encodings.ir_stack.models.IRNode`,
    :class:`~jugeo.encodings.ir_stack.models.IRLayer`,
    :class:`~jugeo.encodings.ir_stack.models.IRStack`,
    :class:`~jugeo.encodings.ir_stack.models.NormalForm`,
    :class:`~jugeo.encodings.ir_stack.models.LoweringPass`,
    :class:`~jugeo.encodings.ir_stack.models.AmbiguityMark`

    Enum types:
    :class:`~jugeo.encodings.ir_stack.models.IRNodeKind`,
    :class:`~jugeo.encodings.ir_stack.models.IRLayerKind`,
    :class:`~jugeo.encodings.ir_stack.models.NormalFormKind`,
    :class:`~jugeo.encodings.ir_stack.models.AmbiguityKind`,
    :class:`~jugeo.encodings.ir_stack.models.LoweringPassKind`

    Factory helpers:
    :func:`~jugeo.encodings.ir_stack.models.create_ir_node`,
    :func:`~jugeo.encodings.ir_stack.models.create_ir_stack`,
    :func:`~jugeo.encodings.ir_stack.models.create_ambiguity_mark`

Integration layer (:mod:`.integration`)
    :class:`~jugeo.encodings.ir_stack.integration.IRStackSession` --
    session lifecycle management (begin / checkpoint / rollback / commit).

    :class:`~jugeo.encodings.ir_stack.integration.LoweringPipelineRunner` --
    orchestrates pass execution with per-pass timing and retry logic.

    :class:`~jugeo.encodings.ir_stack.integration.NormalFormService` --
    high-throughput cached normal-form computation service.

    :class:`~jugeo.encodings.ir_stack.integration.AmbiguityResolver` --
    collects and resolves :class:`AmbiguityMark` objects from layers.

    :class:`~jugeo.encodings.ir_stack.integration.CopilotIRAssist` --
    copilot oracle bridge for IR structure suggestions.

    Factory functions:
    :func:`~jugeo.encodings.ir_stack.integration.create_session`,
    :func:`~jugeo.encodings.ir_stack.integration.create_pipeline_runner`,
    :func:`~jugeo.encodings.ir_stack.integration.create_normal_form_service`,
    :func:`~jugeo.encodings.ir_stack.integration.create_ambiguity_resolver`

Formal theorems (:mod:`.theorems`)
    :class:`~jugeo.encodings.ir_stack.theorems.TheoremStatement` -- base
    dataclass for all IR stack theorems.

    :class:`~jugeo.encodings.ir_stack.theorems.AmbiguityPreservationTheorem`
    (Theorem 32.1) -- ambiguity marks are a superset after lowering.

    :class:`~jugeo.encodings.ir_stack.theorems.NormalFormConfluenceTheorem`
    (Theorem 32.2) -- the reduction system is Church-Rosser.

    :class:`~jugeo.encodings.ir_stack.theorems.StackDepthMonotonicityTheorem`
    (Theorem 32.3) -- stack depth never decreases under lowering.

    :class:`~jugeo.encodings.ir_stack.theorems.LoweringFaithfulnessTheorem`
    (Theorem 32.4) -- semantic interpretation is preserved by lowering.

    :class:`~jugeo.encodings.ir_stack.theorems.CacheCorrectnessTheorem`
    (Theorem 32.5) -- cache hit implies alpha-equivalence with fresh
    computation.

    Module-level singleton instances:
    :data:`~jugeo.encodings.ir_stack.theorems.AMBIGUITY_PRESERVATION`,
    :data:`~jugeo.encodings.ir_stack.theorems.CONFLUENCE`,
    :data:`~jugeo.encodings.ir_stack.theorems.DEPTH_MONOTONICITY`,
    :data:`~jugeo.encodings.ir_stack.theorems.LOWERING_FAITHFULNESS`,
    :data:`~jugeo.encodings.ir_stack.theorems.CACHE_CORRECTNESS`

Package-level utilities (this module)
    :func:`quick_run_pipeline` -- one-shot pipeline helper.
    :func:`describe_stack` -- human-readable stack summary string.
    :func:`stack_health_check` -- validate stack invariants, return report.
    :func:`package_info` -- return a dict of package metadata.

Quick-start example
-------------------

.. code-block:: python

    from jugeo.encodings.ir_stack import (
        create_ir_node, create_ir_stack, IRNodeKind,
        create_session, create_pipeline_runner,
        quick_run_pipeline, AMBIGUITY_PRESERVATION,
    )

    # Build a surface stack, run default pipeline, get summary.
    stack = create_ir_stack()
    node = create_ir_node(IRNodeKind.EXPRESSION, {"value": "x + 1"})
    summary = quick_run_pipeline(stack, passes=[], metadata={"query": "x + 1 > 0"})
    print(summary)

    # Inspect a theorem.
    print(AMBIGUITY_PRESERVATION.formal_statement())

Theory reference
----------------

Theorem 32.1  Ambiguity Preservation

   :math:`\forall \pi,\, \forall \mathcal{L}:\quad
   \mathrm{marks}(\mathcal{L}) \subseteq \mathrm{marks}(\pi(\mathcal{L}))`

Theorem 32.2  Normal Form Confluence

   :math:`\forall n,\, n \twoheadrightarrow^* n_1,\,
   n \twoheadrightarrow^* n_2:\quad
   \exists n_3:\; n_1 \twoheadrightarrow^* n_3 \land
   n_2 \twoheadrightarrow^* n_3`

Theorem 32.3  Stack Depth Monotonicity

   :math:`\forall \pi,\, \forall \mathcal{S}:\quad
   \mathrm{depth}(\pi(\mathcal{S})) \ge \mathrm{depth}(\mathcal{S})`

Theorem 32.4  Lowering Faithfulness

   :math:`\forall \pi,\, \forall n:\quad
   \llbracket \pi(n) \rrbracket = \llbracket n \rrbracket`

Theorem 32.5  Cache Correctness

   :math:`\mathrm{cache}[n, k] = v \implies v \equiv_\alpha N_k(n)`

Design decisions
----------------

**Why try/except import guards?**
    All jugeo imports are wrapped in ``try/except ImportError`` so that
    individual subpackages can be imported in isolation during testing or
    when the full package tree is not installed.  Public names are still
    listed in ``__all__`` so that IDEs can provide accurate completion.

**Why dataclasses for theorems?**
    Using ``@dataclass`` gives theorem objects free ``__repr__``, field
    defaults, and ``field(default_factory=...)`` without metaclass magic.
    The composite pattern (``_base: TheoremStatement``) allows theorem
    objects to extend the base without Python inheritance, keeping the
    design flat and serialisable.

**Why a module-level session registry?**
    The ``_SESSION_REGISTRY`` dict allows different parts of the jugeo
    runtime (solver dispatch, evidence channels, copilot oracle) to
    reference the same session by ID without passing session objects
    through deep call stacks.

**Why ambiguity preservation is a hard invariant?**
    Silently dropping an ambiguity mark is a soundness error: it means
    the solver-ready layer no longer carries the provenance of an
    unresolved syntactic choice.

Version history
---------------

1.0.0
    Initial implementation.  Adds :mod:`.integration` and :mod:`.theorems`
    alongside the existing :mod:`.models` module.  Exports all public
    names through this ``__init__``.  Adds package-level utilities
    :func:`quick_run_pipeline`, :func:`describe_stack`,
    :func:`stack_health_check`, and :func:`package_info`.
"""
from __future__ import annotations

import logging as _logging
import time as _time
from typing import Any as _Any

__version__ = "1.0.0"
__author__ = "jugeo"

_logger = _logging.getLogger(__name__)

# ===================================================================== #
# Core models                                                            #
# ===================================================================== #

try:
    from jugeo.encodings.ir_stack.models import (
        IRNode,
        IRLayer,
        IRStack,
        NormalForm,
        LoweringPass,
        AmbiguityMark,
        IRNodeKind,
        IRLayerKind,
        NormalFormKind,
        AmbiguityKind,
        LoweringPassKind,
        create_ir_node,
        create_ir_stack,
        create_ambiguity_mark,
    )
except ImportError:
    pass

# ===================================================================== #
# Integration layer                                                      #
# ===================================================================== #

try:
    from jugeo.encodings.ir_stack.integration import (
        IRStackSession,
        LoweringPipelineRunner,
        NormalFormService,
        AmbiguityResolver,
        CopilotIRAssist,
        create_session,
        create_pipeline_runner,
        create_normal_form_service,
        create_ambiguity_resolver,
        get_session,
        _SESSION_REGISTRY,
    )
except ImportError:
    pass

# ===================================================================== #
# Formal theorems                                                        #
# ===================================================================== #

try:
    from jugeo.encodings.ir_stack.theorems import (
        VerificationStatus,
        TheoremStatement,
        AmbiguityPreservationTheorem,
        NormalFormConfluenceTheorem,
        StackDepthMonotonicityTheorem,
        LoweringFaithfulnessTheorem,
        CacheCorrectnessTheorem,
        TheoremRegistry,
        get_theorem_registry,
        list_theorems,
        verify_theorem,
        AMBIGUITY_PRESERVATION,
        CONFLUENCE,
        DEPTH_MONOTONICITY,
        LOWERING_FAITHFULNESS,
        CACHE_CORRECTNESS,
    )
except ImportError:
    pass

__all__ = [
    # ----------------------------------------------------------------- #
    # models                                                             #
    # ----------------------------------------------------------------- #
    "IRNode",
    "IRLayer",
    "IRStack",
    "NormalForm",
    "LoweringPass",
    "AmbiguityMark",
    "IRNodeKind",
    "IRLayerKind",
    "NormalFormKind",
    "AmbiguityKind",
    "LoweringPassKind",
    "create_ir_node",
    "create_ir_stack",
    "create_ambiguity_mark",
    # ----------------------------------------------------------------- #
    # integration                                                        #
    # ----------------------------------------------------------------- #
    "IRStackSession",
    "LoweringPipelineRunner",
    "NormalFormService",
    "AmbiguityResolver",
    "CopilotIRAssist",
    "create_session",
    "create_pipeline_runner",
    "create_normal_form_service",
    "create_ambiguity_resolver",
    "get_session",
    "_SESSION_REGISTRY",
    # ----------------------------------------------------------------- #
    # theorems                                                           #
    # ----------------------------------------------------------------- #
    "VerificationStatus",
    "TheoremStatement",
    "AmbiguityPreservationTheorem",
    "NormalFormConfluenceTheorem",
    "StackDepthMonotonicityTheorem",
    "LoweringFaithfulnessTheorem",
    "CacheCorrectnessTheorem",
    "TheoremRegistry",
    "get_theorem_registry",
    "list_theorems",
    "verify_theorem",
    "AMBIGUITY_PRESERVATION",
    "CONFLUENCE",
    "DEPTH_MONOTONICITY",
    "LOWERING_FAITHFULNESS",
    "CACHE_CORRECTNESS",
    # ----------------------------------------------------------------- #
    # package-level utilities                                            #
    # ----------------------------------------------------------------- #
    "quick_run_pipeline",
    "describe_stack",
    "stack_health_check",
    "package_info",
    # ----------------------------------------------------------------- #
    # package metadata                                                   #
    # ----------------------------------------------------------------- #
    "__version__",
    "__author__",
]


# ===================================================================== #
# Package-level utility functions                                        #
# ===================================================================== #


def quick_run_pipeline(
    stack: _Any,
    passes: list[_Any],
    metadata: dict[str, _Any] | None = None,
) -> dict[str, _Any]:
    """Run *passes* over *stack* in a fresh session and return the summary.

    This is the primary convenience entry point for callers that want to
    run the full lowering pipeline without manually managing session
    lifecycle.  It:

    1. Creates a new :class:`~.integration.IRStackSession` via
       :func:`~.integration.create_session`.
    2. Attaches *stack* to the session and calls
       :meth:`~.integration.IRStackSession.begin`.
    3. Creates a :class:`~.integration.LoweringPipelineRunner` and calls
       :meth:`~.integration.LoweringPipelineRunner.run_with_session`.
    4. Commits the session and returns a merged summary dict containing
       both the session summary and the pipeline run report.

    :param stack: An :class:`~.models.IRStack` to process.
    :param passes: Ordered list of :class:`~.models.LoweringPass` objects.
    :param metadata: Optional metadata forwarded to the session.
    :returns: A merged summary dictionary with keys from both the session
        commit and the pipeline run report.
    """
    try:
        session = create_session(metadata=metadata)  # type: ignore[name-defined]
    except NameError:
        _logger.error("quick_run_pipeline: integration module not available.")
        return {"status": "error", "reason": "integration module not imported"}

    session.stack = stack
    session.begin()

    try:
        runner = create_pipeline_runner()  # type: ignore[name-defined]
        run_report = runner.run_with_session(session, passes)
    except Exception as exc:
        _logger.error("quick_run_pipeline: pipeline run failed: %s", exc)
        run_report = {"status": "error", "reason": str(exc)}

    commit_summary = session.commit()
    merged: dict[str, _Any] = {**commit_summary, "run_report": run_report}
    _logger.info(
        "quick_run_pipeline: completed session %s with %d passes.",
        commit_summary.get("session_id", "?"),
        run_report.get("passes_applied", 0) if isinstance(run_report, dict) else 0,
    )
    return merged


def describe_stack(stack: _Any) -> str:
    """Return a human-readable multi-line description of *stack*.

    Produces a textual summary of the stack's layer sequence, including
    each layer's kind, node count, and ambiguity mark count.

    :param stack: An :class:`~.models.IRStack` to describe.
    :returns: A formatted string suitable for logging or REPL display.
    """
    stack_id = getattr(stack, "stack_id", "<unknown>")
    layers = getattr(stack, "layers", [])
    lines: list[str] = [
        f"IRStack {stack_id}",
        f"  depth: {len(layers)} layer(s)",
    ]
    for idx, layer in enumerate(layers):
        layer_id = getattr(layer, "layer_id", f"L{idx}")
        layer_kind = getattr(getattr(layer, "layer_kind", None), "value",
                             str(getattr(layer, "layer_kind", "?")))
        nodes = getattr(layer, "nodes", {})
        node_count = len(nodes)
        ambig_count = sum(
            1 for n in nodes.values()
            if getattr(n, "ambiguity_mark", None) is not None
        )
        constraint_count = len(getattr(layer, "constraints", []))
        lines.append(
            f"  [{idx}] {layer_kind:12s}  id={layer_id[:8]}..."
            f"  nodes={node_count}  ambig={ambig_count}  constraints={constraint_count}"
        )
    if not layers:
        lines.append("  (empty — no layers pushed)")
    return "\n".join(lines)


def stack_health_check(stack: _Any) -> dict[str, _Any]:
    """Validate the structural invariants of *stack* and return a report.

    Checks the following invariants:

    * All layers have non-empty ``layer_id`` strings.
    * Layer kinds form a non-decreasing sequence by ``depth_hint``.
    * No node appears in more than one layer (node IDs are unique
      across the stack).
    * Every node's ``children`` list references only node IDs that exist
      within the same layer.

    :param stack: An :class:`~.models.IRStack` to inspect.
    :returns: A dictionary with ``"healthy"`` (bool), ``"warnings"``
        (list[str]), ``"errors"`` (list[str]), ``"layer_count"``,
        and ``"node_count"`` keys.
    """
    layers = getattr(stack, "layers", [])
    warnings: list[str] = []
    errors: list[str] = []

    # Check layer_id presence.
    for idx, layer in enumerate(layers):
        lid = getattr(layer, "layer_id", "")
        if not lid:
            errors.append(f"Layer[{idx}] has empty layer_id.")

    # Check depth_hint monotonicity.
    prev_depth = -1
    for idx, layer in enumerate(layers):
        lk = getattr(layer, "layer_kind", None)
        depth = getattr(lk, "depth_hint", lambda: idx)()
        if depth < prev_depth:
            warnings.append(
                f"Layer[{idx}] kind depth_hint={depth} is less than "
                f"previous depth_hint={prev_depth} (non-monotone ordering)."
            )
        prev_depth = depth

    # Check node ID uniqueness across layers.
    seen_node_ids: dict[str, int] = {}
    total_nodes = 0
    for layer_idx, layer in enumerate(layers):
        nodes = getattr(layer, "nodes", {})
        total_nodes += len(nodes)
        for node_id in nodes:
            if node_id in seen_node_ids:
                warnings.append(
                    f"Node ID {node_id!r} appears in both layer[{seen_node_ids[node_id]}]"
                    f" and layer[{layer_idx}]."
                )
            else:
                seen_node_ids[node_id] = layer_idx

    # Check that child references resolve within the same layer.
    for layer_idx, layer in enumerate(layers):
        nodes = getattr(layer, "nodes", {})
        layer_node_ids = set(nodes.keys())
        for node_id, node in nodes.items():
            children = getattr(node, "children", [])
            for child in children:
                child_id = getattr(child, "node_id", str(child))
                if child_id not in layer_node_ids:
                    warnings.append(
                        f"Node {node_id!r} in layer[{layer_idx}] has child "
                        f"{child_id!r} not present in the same layer."
                    )

    healthy = not bool(errors)
    return {
        "healthy": healthy,
        "warnings": warnings,
        "errors": errors,
        "layer_count": len(layers),
        "node_count": total_nodes,
        "stack_id": getattr(stack, "stack_id", "<unknown>"),
    }


def package_info() -> dict[str, _Any]:
    """Return a dictionary of metadata about this package.

    Includes version, author, module availability flags, and counts of
    active sessions and registered theorems.

    :returns: A metadata dictionary keyed by metric name.
    """
    # Check which sub-modules are available.
    modules_available: dict[str, bool] = {}
    for mod_name in ("models", "integration", "theorems"):
        full_name = f"jugeo.encodings.ir_stack.{mod_name}"
        try:
            import importlib as _il
            _il.import_module(full_name)
            modules_available[mod_name] = True
        except ImportError:
            modules_available[mod_name] = False

    # Count active sessions.
    active_sessions = 0
    try:
        active_sessions = len(_SESSION_REGISTRY)  # type: ignore[name-defined]
    except NameError:
        pass

    # Count registered theorems.
    theorem_count = 0
    try:
        registry = get_theorem_registry()  # type: ignore[name-defined]
        theorem_count = len(registry._theorems)
    except (NameError, AttributeError):
        pass

    return {
        "package": "jugeo.encodings.ir_stack",
        "version": __version__,
        "author": __author__,
        "modules_available": modules_available,
        "active_sessions": active_sessions,
        "registered_theorems": theorem_count,
        "theory_chapter": "Ch32",
        "query_time": _time.time(),
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import an_implementation_ready_theory_nee
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import ir_layers
except Exception:
    pass
try:
    from . import ir_nodes
except Exception:
    pass
try:
    from . import lowering
except Exception:
    pass
try:
    from . import lowering_should_preserve_ambiguity
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
    from . import normal_forms
except Exception:
    pass
try:
    from . import normal_forms_where_comparison_cach
except Exception:
    pass
try:
    from . import the_theory_wants_a_small_number_of
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
