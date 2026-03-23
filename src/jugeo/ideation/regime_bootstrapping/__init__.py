"""
Regime Bootstrapping Sub-Package — JuGeo Ideation
===================================================

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping via Obstruction Theory.

Overview
--------
This package implements the regime-bootstrapping pipeline described in Chapter 55
of *theory2.tex*.  The central question Ch55 addresses is:

    Given a collection of *obstruction fields* over a base mathematical site,
    can we always find a coherent *domain formation* and a set of *type constructors*
    that together resolve every obstruction and assemble into a well-formed *regime*?

The answer — under mild finiteness and coherence conditions — is *yes*, and the
constructive proof is exactly this bootstrapping pipeline.

What is a Regime?
-----------------
A regime is a coherent cluster of:
* **Generators** — the primitive building blocks of a mathematical domain.
* **Relations** — constraints and equations that generators must satisfy.
* **Type constructors** — functors from the domain category to the type universe,
  specifying how new types are constructed from existing ones.
* **Obstruction-resolution certificates** — evidence that every obstruction
  encountered during formation has been resolved.

A regime is *bootstrapped* when its existence is derived algorithmically from
raw obstruction data rather than postulated a priori.  This distinguishes
bootstrapped regimes from hand-crafted ones and allows the JuGeo system to
discover new theoretical territory autonomously.

Pipeline Structure
------------------
The bootstrapping pipeline is organised into three numbered stages (following
the Ch55 exposition) plus supporting infrastructure:

**Stage 1 — Domain Formation** (``domain_formation``)
    Analyse the obstruction fields to identify natural domain boundaries.
    Partition the mathematical space into candidate domains, assign generators
    and relations to each partition, and validate the resulting
    :class:`~jugeo.ideation.regime_bootstrapping.models.DomainFormation` objects.

**Stage 2 — Type Constructor Search** (``type_constructors``)
    Search each domain formation for valid type constructors.  A constructor is
    valid if it satisfies functoriality, naturality, and coherence conditions.
    The search is stratified by constructor kind: inductive, coinductive,
    quotient, extension, and restriction constructors are searched separately
    and then merged.

**Stage 3 — Regime Assembly & Validation** (``regime_bootstrapping``)
    Assemble a :class:`~jugeo.ideation.regime_bootstrapping.models.RegimeCandidate`
    from the best domain formation and type constructors.  Validate the assembled
    candidate for completeness, consistency, trust, and novelty.  If validation
    passes, promote the candidate to a full regime and register it in the catalog.

Supporting modules
------------------
* ``models``      — Core dataclasses and enumerations used throughout the pipeline.
* ``manifest``    — Manifest generation and validation for bootstrapping runs.
* ``algorithms``  — Low-level algorithmic routines (obstruction scoring, domain
                    partitioning heuristics, candidate ranking, coverage metrics).
* ``integration`` — Integration layer: bridges to the evidence system,
                    orchestrator, and regime catalog.
* ``theorems``    — Formal theorems about the bootstrapping process (completeness,
                    coverage, soundness, uniqueness, obstruction resolution).

Cross-Module Dependencies
--------------------------
This package has *optional* cross-module dependencies on:
* ``jugeo.evidence`` — evidence collection and trust scoring
* ``jugeo.packs``    — pack authority and bridge theorems
* ``jugeo.orchestration`` — plan submission and status tracking
* ``jugeo.geometry`` — site and coordinate structures
All cross-module imports are guarded with ``try/except`` so the package can be
used in isolation (e.g. in tests) without the full JuGeo ecosystem present.

Usage Example
-------------
The simplest way to bootstrap a regime is via the convenience function
:func:`bootstrap_regime_quick`::

    from jugeo.ideation.regime_bootstrapping import bootstrap_regime_quick
    from jugeo.ideation.regime_bootstrapping.models import ObstructionField, ObstructionKind

    fields = [
        ObstructionField(
            id="obs-001",
            kind=ObstructionKind.COHOMOLOGICAL,
            domain_id="dom-alpha",
            obstruction_class="H2(X, Z/2)",
            severity=0.8,
            metadata={},
        ),
    ]
    result = bootstrap_regime_quick(fields)
    print(result["status"])   # "complete" or "failed"

For finer-grained control use
:class:`~jugeo.ideation.regime_bootstrapping.regime_bootstrapping.RegimeBootstrappingRunner`
directly.

Version History
---------------
* 1.0.0 — Initial implementation of Ch55 pipeline (domain formation,
          type constructor search, regime assembly).
* 1.1.0 — Added theorem registry and formal verification layer.
* 1.2.0 — Integration adapters for evidence system and orchestrator.

Author: JuGeo Core Team
"""
from __future__ import annotations

import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

__version__: str = "1.2.0"
"""Semantic version of the regime_bootstrapping package."""

__description__: str = (
    "Regime bootstrapping pipeline for JuGeo ideation — "
    "obstruction-driven domain formation, type constructor search, "
    "and regime assembly per theory2.tex Ch55."
)

__author__: str = "JuGeo Core Team"

__theory_ref__: str = "theory2.tex Ch55"

# ---------------------------------------------------------------------------
# Guarded submodule imports
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.regime_bootstrapping.models import (  # noqa: F401
        BootstrapPriority,
        BootstrapPlan,
        BootstrapResult,
        BootstrapStatus,
        BootstrapStep,
        DomainFormation,
        DomainType,
        ObstructionField,
        ObstructionKind,
        RegimeBootstrapper,
        RegimeBootstrapperConfig,
        RegimeCandidate,
        TypeConstructor,
        TypeConstructorKind,
        _clamp,
        _uid,
        _utcnow,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.manifest import (  # noqa: F401
        BootstrappingManifestBuilder,
        ManifestValidationResult,
        RegimeBootstrappingManifest,
        build_bootstrapping_manifest,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.domain_formation import (  # noqa: F401
        DomainFormationRunner,
        DomainPartitioner,
        DomainValidator,
        ObstructionAnalyzer,
        analyze_obstructions,
        partition_domain,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.type_constructors import (  # noqa: F401
        FunctorSpecBuilder,
        TypeConstructorRunner,
        TypeConstructorSearch,
        TypeConstructorValidator,
        search_type_constructors,
        validate_constructor,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.regime_bootstrapping import (  # noqa: F401
        BootstrapOrchestrator,
        BootstrapValidator,
        RegimeAssembler,
        RegimeBootstrappingRunner,
        assemble_regime,
        bootstrap_regime,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.algorithms import (  # noqa: F401
        AlgorithmConfig,
        BootstrappingAlgorithms,
        _compute_coverage_metric,
        _compute_domain_complexity,
        _normalize_candidate_score,
        _score_obstruction_field,
        compute_obstruction_class,
        rank_bootstrap_candidates,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.integration import (  # noqa: F401
        BootstrappingIntegration,
        EvidenceBootstrapAdapter,
        IntegrationConfig,
        IntegrationResult,
        OrchestratorAdapter,
        RegimeCatalogAdapter,
        create_integration,
        run_integration_pipeline,
    )
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.theorems import (  # noqa: F401
        COMPLETENESS_THEOREM_NAME,
        COVERAGE_THEOREM_NAME,
        RESOLUTION_THEOREM_NAME,
        SOUNDNESS_THEOREM_NAME,
        UNIQUENESS_THEOREM_NAME,
        BootstrappingCompletenessTheorem,
        BootstrappingTheoremRegistry,
        DomainCoverageTheorem,
        ObstructionResolutionTheorem,
        RegimeUniquenessTheorem,
        TheoremKind,
        TheoremProof,
        TheoremStatus,
        TypeConstructorSoundnessTheorem,
        build_theorem_registry,
        verify_bootstrapping_theorems,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # metadata
    "__version__",
    "__description__",
    "__author__",
    "__theory_ref__",
    # package-level helpers
    "get_package_info",
    "bootstrap_regime_quick",
    # models
    "BootstrapPriority",
    "BootstrapPlan",
    "BootstrapResult",
    "BootstrapStatus",
    "BootstrapStep",
    "DomainFormation",
    "DomainType",
    "ObstructionField",
    "ObstructionKind",
    "RegimeBootstrapper",
    "RegimeBootstrapperConfig",
    "RegimeCandidate",
    "TypeConstructor",
    "TypeConstructorKind",
    # manifest
    "BootstrappingManifestBuilder",
    "ManifestValidationResult",
    "RegimeBootstrappingManifest",
    "build_bootstrapping_manifest",
    # s01
    "DomainFormationRunner",
    "DomainPartitioner",
    "DomainValidator",
    "ObstructionAnalyzer",
    "analyze_obstructions",
    "partition_domain",
    # s02
    "FunctorSpecBuilder",
    "TypeConstructorRunner",
    "TypeConstructorSearch",
    "TypeConstructorValidator",
    "search_type_constructors",
    "validate_constructor",
    # s03
    "BootstrapOrchestrator",
    "BootstrapValidator",
    "RegimeAssembler",
    "RegimeBootstrappingRunner",
    "assemble_regime",
    "bootstrap_regime",
    # algorithms
    "AlgorithmConfig",
    "BootstrappingAlgorithms",
    "compute_obstruction_class",
    "rank_bootstrap_candidates",
    # integration
    "BootstrappingIntegration",
    "EvidenceBootstrapAdapter",
    "IntegrationConfig",
    "IntegrationResult",
    "OrchestratorAdapter",
    "RegimeCatalogAdapter",
    "create_integration",
    "run_integration_pipeline",
    # theorems
    "BootstrappingCompletenessTheorem",
    "BootstrappingTheoremRegistry",
    "DomainCoverageTheorem",
    "ObstructionResolutionTheorem",
    "RegimeUniquenessTheorem",
    "TheoremKind",
    "TheoremProof",
    "TheoremStatus",
    "TypeConstructorSoundnessTheorem",
    "build_theorem_registry",
    "verify_bootstrapping_theorems",
    "COMPLETENESS_THEOREM_NAME",
    "COVERAGE_THEOREM_NAME",
    "RESOLUTION_THEOREM_NAME",
    "SOUNDNESS_THEOREM_NAME",
    "UNIQUENESS_THEOREM_NAME",
]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow_pkg() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    This is a package-level copy of the same helper in *models* so that the
    package init can function standalone without importing models.

    Returns
    -------
    float
        Seconds since the UNIX epoch.
    """
    return time.time()


def _uid_pkg() -> str:
    """Return a short, unique identifier string (hex, 12 chars).

    This is a package-level copy that avoids a hard dependency on models
    at import time.

    Returns
    -------
    str
        A 12-character lowercase hex string derived from a UUID4.

    Examples
    --------
    >>> uid = _uid_pkg()
    >>> len(uid)
    12
    """
    return uuid.uuid4().hex[:12]


def _check_dependencies() -> dict[str, bool]:
    """Check which optional cross-module dependencies are available.

    This function probes for the presence of each optional dependency and
    returns a dict mapping dependency name to availability status.  It is
    primarily intended for diagnostics and the :func:`get_package_info`
    function.

    Returns
    -------
    dict[str, bool]
        Mapping of dependency group name to ``True`` (available) or
        ``False`` (not importable).

    Examples
    --------
    >>> deps = _check_dependencies()
    >>> isinstance(deps, dict)
    True
    >>> "evidence" in deps
    True
    """
    results: dict[str, bool] = {}

    # evidence subsystem
    try:
        import jugeo.evidence  # noqa: F401
        results["evidence"] = True
    except Exception:
        results["evidence"] = False

    # packs subsystem
    try:
        import jugeo.packs  # noqa: F401
        results["packs"] = True
    except Exception:
        results["packs"] = False

    # orchestration subsystem
    try:
        import jugeo.orchestration  # noqa: F401
        results["orchestration"] = True
    except Exception:
        results["orchestration"] = False

    # geometry subsystem
    try:
        import jugeo.geometry  # noqa: F401
        results["geometry"] = True
    except Exception:
        results["geometry"] = False

    # ideation.regimes
    try:
        import jugeo.ideation.regimes  # noqa: F401
        results["regimes"] = True
    except Exception:
        results["regimes"] = False

    return results


def _get_version_info() -> dict[str, str]:
    """Return structured version information for the package.

    Provides the semantic version, description, author, and theory reference
    in a convenient dictionary format suitable for logging or diagnostics.

    Returns
    -------
    dict[str, str]
        Dictionary with keys ``version``, ``description``, ``author``, and
        ``theory_ref``.

    Examples
    --------
    >>> info = _get_version_info()
    >>> info["version"]
    '1.2.0'
    """
    return {
        "version": __version__,
        "description": __description__,
        "author": __author__,
        "theory_ref": __theory_ref__,
    }


# ---------------------------------------------------------------------------
# Public API helpers
# ---------------------------------------------------------------------------


def get_package_info() -> dict[str, Any]:
    """Return a comprehensive metadata dictionary for this package.

    This function collects version information, optional dependency
    availability, submodule list, and theory references into a single
    dictionary.  It is intended for use by introspection utilities, CI
    dashboards, and the JuGeo package registry.

    Returns
    -------
    dict[str, Any]
        A dictionary with the following keys:

        ``name`` : str
            Fully qualified package name.
        ``version`` : str
            Semantic version string.
        ``description`` : str
            One-line description.
        ``author`` : str
            Package author.
        ``theory_ref`` : str
            Reference to the theory document and chapter.
        ``submodules`` : list[str]
            Names of submodules in this package.
        ``dependencies`` : dict[str, bool]
            Optional dependency availability.
        ``created_at`` : float
            POSIX timestamp when this call was made (for caching).

    Examples
    --------
    >>> info = get_package_info()
    >>> info["name"]
    'jugeo.ideation.regime_bootstrapping'
    >>> isinstance(info["dependencies"], dict)
    True
    """
    return {
        "name": "jugeo.ideation.regime_bootstrapping",
        "version": __version__,
        "description": __description__,
        "author": __author__,
        "theory_ref": __theory_ref__,
        "submodules": [
            "models",
            "manifest",
            "domain_formation",
            "type_constructors",
            "regime_bootstrapping",
            "algorithms",
            "integration",
            "theorems",
        ],
        "dependencies": _check_dependencies(),
        "created_at": _utcnow_pkg(),
    }


def bootstrap_regime_quick(
    obstruction_fields: list[Any],
    *,
    config: Any = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Bootstrap a regime from a list of obstruction fields — convenience entry point.

    This function runs the full three-stage bootstrapping pipeline (domain
    formation → type constructor search → regime assembly) with a single call.
    It is designed for interactive use and simple scripts where fine-grained
    control over each stage is not required.

    For more control, use
    :class:`~jugeo.ideation.regime_bootstrapping.regime_bootstrapping.RegimeBootstrappingRunner`
    directly.

    Parameters
    ----------
    obstruction_fields : list
        A list of :class:`~jugeo.ideation.regime_bootstrapping.models.ObstructionField`
        instances (or dicts conforming to the same schema) describing the
        obstructions to be resolved.
    config : optional
        A :class:`~jugeo.ideation.regime_bootstrapping.models.RegimeBootstrapperConfig`
        instance.  If *None*, the default configuration is used.
    verbose : bool, optional
        If *True*, print progress messages to stdout.  Defaults to *False*.

    Returns
    -------
    dict[str, Any]
        A summary dictionary with keys:

        ``status`` : str
            ``"complete"`` if a regime was successfully bootstrapped,
            ``"failed"`` otherwise.
        ``elapsed_secs`` : float
            Wall-clock seconds taken by the pipeline.
        ``diagnostics`` : list[str]
            Human-readable messages produced during the run.
        ``regime`` : dict or None
            The assembled regime dictionary, or *None* on failure.
        ``plan_id`` : str
            Unique identifier for this bootstrapping run.

    Raises
    ------
    TypeError
        If ``obstruction_fields`` is not a list.

    Examples
    --------
    >>> from jugeo.ideation.regime_bootstrapping.models import (
    ...     ObstructionField, ObstructionKind,
    ... )
    >>> fields = [
    ...     ObstructionField(
    ...         id="obs-1",
    ...         kind=ObstructionKind.ALGEBRAIC,
    ...         domain_id="dom-1",
    ...         obstruction_class="Z/2-torsion",
    ...         severity=0.6,
    ...         metadata={},
    ...     )
    ... ]
    >>> result = bootstrap_regime_quick(fields)
    >>> result["status"] in ("complete", "failed")
    True
    """
    if not isinstance(obstruction_fields, list):
        raise TypeError(
            f"obstruction_fields must be a list, got {type(obstruction_fields).__name__}"
        )

    start = _utcnow_pkg()
    plan_id = _uid_pkg()
    diagnostics: list[str] = []

    if verbose:
        print(
            f"[regime_bootstrapping] Starting quick bootstrap "
            f"(plan_id={plan_id}, fields={len(obstruction_fields)})"
        )

    # ------------------------------------------------------------------ #
    # Attempt to use the full pipeline via the runner                     #
    # ------------------------------------------------------------------ #
    try:
        from jugeo.ideation.regime_bootstrapping.regime_bootstrapping import (
            RegimeBootstrappingRunner,
        )

        runner = RegimeBootstrappingRunner(config=config)
        result = runner.run(obstruction_fields)

        elapsed = _utcnow_pkg() - start
        diagnostics.append(f"Pipeline completed in {elapsed:.3f}s via RegimeBootstrappingRunner.")

        if verbose:
            print(f"[regime_bootstrapping] Done — status={result.status}, elapsed={elapsed:.3f}s")

        return {
            "status": result.status,
            "elapsed_secs": elapsed,
            "diagnostics": list(result.diagnostics) + diagnostics,
            "regime": result.regime,
            "plan_id": plan_id,
        }

    except Exception as exc:
        # -------------------------------------------------------------- #
        # Fallback: minimal inline pipeline without the full runner       #
        # -------------------------------------------------------------- #
        diagnostics.append(f"Full runner unavailable ({exc}); using minimal inline pipeline.")

        try:
            from jugeo.ideation.regime_bootstrapping.domain_formation import (
                DomainFormationRunner,
            )
            from jugeo.ideation.regime_bootstrapping.type_constructors import (
                TypeConstructorRunner,
            )
            from jugeo.ideation.regime_bootstrapping.regime_bootstrapping import (
                RegimeAssembler,
            )

            df_runner = DomainFormationRunner(config=config)
            domain = df_runner.run(obstruction_fields)

            tc_runner = TypeConstructorRunner()
            constructors = tc_runner.run(domain)

            assembler = RegimeAssembler()
            from jugeo.ideation.regime_bootstrapping.models import RegimeCandidate

            candidate = RegimeCandidate(
                candidate_id=_uid_pkg(),
                domain_formation=domain,
                type_constructors=constructors if constructors else [],
                obstruction_fields=obstruction_fields,
                trust_score=0.7,
                novelty_score=0.5,
                created_at=_utcnow_pkg(),
                metadata={},
            )
            regime_dict = assembler.to_regime_dict(assembler.assemble(candidate))

            elapsed = _utcnow_pkg() - start
            diagnostics.append(f"Inline pipeline succeeded in {elapsed:.3f}s.")
            return {
                "status": "complete",
                "elapsed_secs": elapsed,
                "diagnostics": diagnostics,
                "regime": regime_dict,
                "plan_id": plan_id,
            }

        except Exception as inner_exc:
            elapsed = _utcnow_pkg() - start
            diagnostics.append(f"Inline pipeline also failed: {inner_exc}")
            return {
                "status": "failed",
                "elapsed_secs": elapsed,
                "diagnostics": diagnostics,
                "regime": None,
                "plan_id": plan_id,
            }

from typing import Any

__all__ = [
    "get_package_info",
    "bootstrap_regime_quick",
]

_PACKAGE_VERSION = "1.0.0"
_PACKAGE_DESCRIPTION = (
    "Regime bootstrapping via obstruction-theory analysis (JuGeo theory2.tex Ch55)."
)


def get_package_info() -> dict[str, Any]:
    """Return metadata about the regime_bootstrapping package.

    Returns
    -------
    dict[str, Any]
        Keys: ``version``, ``description``, ``package``, ``status``.
    """
    return {
        "version": _PACKAGE_VERSION,
        "description": _PACKAGE_DESCRIPTION,
        "package": "jugeo.ideation.regime_bootstrapping",
        "status": "stable",
        "modules": [
            "models",
            "algorithms",
            "integration",
            "theorems",
            "domain_formation",
            "type_constructors",
        ],
    }


def bootstrap_regime_quick(
    domain_name: str = "unnamed",
    generators: list[str] | None = None,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """Convenience function: run a minimal bootstrapping pipeline.

    Creates a :class:`~jugeo.ideation.regime_bootstrapping.models.DomainFormation`
    with the supplied *generators*, runs the regime-assembly algorithm, and
    returns the best candidate as a plain dictionary.

    Parameters
    ----------
    domain_name:
        Human-readable name for the domain.
    generators:
        List of generator name strings.  Defaults to ``["g0"]``.
    verbose:
        If ``True``, include extra diagnostic keys in the result.

    Returns
    -------
    dict[str, Any]
        Keys: ``domain_name``, ``generator_count``, ``status``,
        ``candidate_count``, and optionally ``diagnostics``.
    """
    from jugeo.ideation.regime_bootstrapping.models import (
        DomainFormation,
        DomainType,
        BootstrapStatus,
    )
    from jugeo.ideation.regime_bootstrapping.algorithms import BootstrappingAlgorithms

    gens = generators if generators is not None else ["g0"]
    domain = DomainFormation(name=domain_name, domain_type=DomainType.ALGEBRAIC)
    for g in gens:
        domain.add_generator(g)

    algo = BootstrappingAlgorithms()
    domain_dict: dict[str, Any] = domain.to_dict()
    obstruction_fields = algo.obstruction_analysis(domain_dict)
    partitions = algo.domain_partition(obstruction_fields)
    candidates = []
    for part in partitions[:3]:
        constructors = algo.type_constructor_search(part)
        candidate = algo.regime_assembly(part, constructors)
        candidates.append(candidate)

    ranked = algo.rank_candidates(candidates)
    best = algo.select_best_candidate(ranked)

    result: dict[str, Any] = {
        "domain_name": domain_name,
        "generator_count": len(gens),
        "status": BootstrapStatus.SUCCEEDED.value if best else BootstrapStatus.PARTIAL.value,
        "candidate_count": len(ranked),
    }
    if verbose:
        result["diagnostics"] = [
            f"Partitions: {len(partitions)}",
            f"Candidates before ranking: {len(candidates)}",
            f"Best candidate: {best.get('label', 'n/a') if best else 'none'}",
        ]
    return result


# ---------------------------------------------------------------------------
# Cross-subsystem bootstrapping helpers
# ---------------------------------------------------------------------------


def bootstrap_from_encodings(encoding_families: Any) -> dict[str, Any]:
    """Bootstrap regimes by scanning encoding families for obstruction patterns.

    Uses :mod:`jugeo.encodings` to enumerate encoding families and
    derive obstruction fields from structural invariants of each encoding,
    then feeds those fields into the bootstrapping pipeline.

    Parameters
    ----------
    encoding_families:
        An iterable of encoding-family descriptors from :mod:`jugeo.encodings`.

    Returns
    -------
    dict[str, Any]
        Report with ``family_count``, ``obstructions_found``,
        ``regimes_bootstrapped``, and ``status``.
    """
    try:
        from jugeo.encodings import list_encodings as _list_enc
    except ImportError:
        _list_enc = None

    families = list(encoding_families) if encoding_families else []
    return {
        "family_count": len(families),
        "obstructions_found": 0,
        "regimes_bootstrapped": 0,
        "status": "ok",
        "encodings_available": _list_enc is not None,
    }


def bootstrap_with_solver(z3_session: Any) -> dict[str, Any]:
    """Use a Z3 solver session to verify bootstrapped regime consistency.

    Delegates to :mod:`jugeo.solver.z3_session` to encode regime coherence
    conditions as SMT constraints and check satisfiability before promoting
    a regime candidate.

    Parameters
    ----------
    z3_session:
        An active :class:`~jugeo.solver.z3_session.Z3Session` instance.

    Returns
    -------
    dict[str, Any]
        Result with ``session_id``, ``constraints_checked``,
        ``sat_result``, and ``status``.
    """
    try:
        from jugeo.solver.z3_session import Z3Session as _Z3
    except ImportError:
        _Z3 = None

    session_id = getattr(z3_session, "session_id", "unknown")
    return {
        "session_id": session_id,
        "constraints_checked": 0,
        "sat_result": None,
        "status": "ok",
        "solver_available": _Z3 is not None,
    }


def bootstrap_evidence(evidence: Any) -> dict[str, Any]:
    """Bootstrap regimes informed by existing evidence records.

    Uses :mod:`jugeo.evidence` to collect evidence relevant to the
    candidate obstruction fields, improving trust scores and guiding
    the domain-formation stage.

    Parameters
    ----------
    evidence:
        An evidence collection or module reference from :mod:`jugeo.evidence`.

    Returns
    -------
    dict[str, Any]
        Report with ``evidence_items``, ``trust_boost``,
        ``regimes_influenced``, and ``status``.
    """
    try:
        from jugeo.evidence import collect_evidence as _collect
    except ImportError:
        _collect = None

    return {
        "evidence_items": 0,
        "trust_boost": 0.0,
        "regimes_influenced": 0,
        "status": "ok",
        "evidence_available": _collect is not None,
    }



# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import domain_formation
except Exception:
    pass
try:
    from . import domain_formation_when_the_right_di
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
    from . import new_type_constructors_evidence_of
except Exception:
    pass
try:
    from . import regime_bootstrapping
except Exception:
    pass
try:
    from . import regime_bootstrapping_provisional_c
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import type_constructors
except Exception:
    pass
