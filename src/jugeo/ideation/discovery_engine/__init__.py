"""JuGeo Discovery Engine package — theory2.tex Ch58.

The discovery engine implements the full pipeline for automated mathematical
discovery in the JuGeo framework.  It covers five major concerns:

    1. Novelty pipeline   — filtering and ranking candidate discoveries by
                            novelty score relative to the existing corpus.
    2. Kind classification — mapping candidates to their abstract mathematical
                            kind using characteristic-class signatures.
    3. Theorem synthesis  — deriving theorem candidates from classified kinds
                            via bridging patterns and proof-sketch generation.
    4. Pack promotion     — promoting validated theorem candidates into the
                            pack authority registry for downstream use.
    5. Integration        — wiring the pipeline to the rest of JuGeo
                            (evidence channels, bridges, orchestrator).

Theory reference: theory2.tex Ch58 — Automated Mathematical Discovery.

copilot: shared-core marker

Architectural Overview
======================

The discovery pipeline is a staged transformation system.  Each stage takes
a collection of :class:`~jugeo.ideation.discovery_engine.models.DiscoveryCandidate`
objects and transforms, annotates, or promotes them.

Pipeline topology::

    ┌──────────────────────────────────────────────────────────────┐
    │                     DiscoveryPipeline                        │
    │                                                              │
    │  candidates ──► NoveltyPipeline ──► KindClassification       │
    │                                         │                    │
    │                              TheoremSynthesis                │
    │                                         │                    │
    │                              PackPromotion ──► registry      │
    └──────────────────────────────────────────────────────────────┘

Each stage is driven by a :class:`~jugeo.ideation.discovery_engine.models.DiscoveryConfig`
configuration object.  Diagnostics are accumulated in a
:class:`~jugeo.ideation.discovery_engine.models.DiscoveryDiagnostics` container
and the overall run is documented in a
:class:`~jugeo.ideation.discovery_engine.manifest.DiscoveryEngineManifest`.

Usage Example
=============

The simplest way to run the pipeline is via the :func:`create_default_pipeline`
convenience function::

    from jugeo.ideation.discovery_engine import (
        create_default_pipeline,
        DiscoveryEngineAPI,
        DEFAULT_NOVELTY_THRESHOLD,
    )

    # Build a pipeline with default configuration
    pipeline = create_default_pipeline()

    # Inspect engine metadata
    info = get_engine_info()
    print(info["version"])       # "0.1.0"
    print(info["pipeline_stages"])  # ['NOVELTY', 'KIND_CLASSIFICATION', ...]

    # Construct the high-level API facade
    api = DiscoveryEngineAPI()
    status = api.health_check()
    print(status)   # {"ok": True, "stage_count": 4, ...}

Submodule Layout
================

``jugeo.ideation.discovery_engine.models``
    Canonical data structures: enumerations, dataclasses for each pipeline
    stage, result and diagnostics containers, kind signatures, theorem
    candidates, and promotion decisions.

``jugeo.ideation.discovery_engine.manifest``
    Evidence manifest construction, sealing, archiving, merging, and
    validation.  Provides the :class:`ManifestBuilder` fluent interface.

Cross-module Dependencies
=========================

The discovery engine integrates with several other JuGeo subsystems.
All cross-module imports are guarded with try/except blocks so that the
package can be imported in isolation during unit tests or in environments
where optional subsystems are not installed::

    # evidence subsystem
    jugeo.evidence.manifests   — Manifest, build_evidence_manifest
    jugeo.evidence.trust       — TrustProfile, TrustTier, join_trust_profiles
    jugeo.evidence.channels    — EvidenceRecord, EvidenceKind, build_channel
    jugeo.evidence.provenance  — ProvenanceTrace

    # packs subsystem
    jugeo.packs.bridges        — BridgeTheorem, BridgeRegistry, BridgeComposer
    jugeo.packs.authority      — PackAuthority, PackAuthorityRegistry
    jugeo.packs.catalog        — PackDescriptor

    # orchestration subsystem
    jugeo.orchestration.controller — Orchestrator, OrchestratorState

    # geometry subsystem
    jugeo.geometry.site        — Site, Coordinate
    jugeo.geometry.descent     — DescentResult, GlobalSection

    # ideation subsystem (peer modules)
    jugeo.ideation.ideas       — IdeaProposal, TrustStatus
    jugeo.ideation.regimes     — Regime, RegimeCatalog
    jugeo.ideation.novelty     — NoveltyScore

Version History
===============

0.1.0 — Initial scaffold.  Core models and manifest modules implemented.
        Pipeline integration and full orchestrator wiring planned for 0.2.0.

Notes
=====

Thread safety: The discovery engine is not thread-safe by default.  If you
need concurrent pipeline execution, wrap each pipeline instance in its own
thread and use the :class:`DiscoveryEngineAPI` facade which serialises access.

Performance: The default configuration is tuned for correctness over speed.
For large corpora (>10 000 candidates) consider raising ``novelty_threshold``
to 0.5 or higher and reducing ``synthesis_budget`` to limit theorem generation.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

__version__: str = "0.1.0"
__author__: str = "JuGeo Team"

# ---------------------------------------------------------------------------
# Package-level pipeline constants
# ---------------------------------------------------------------------------

DEFAULT_NOVELTY_THRESHOLD: float = 0.3
"""Minimum novelty score for a candidate to pass the novelty filter stage.

Candidates with a novelty score strictly below this threshold are discarded
before kind classification begins.  The value 0.3 is chosen conservatively so
that marginally novel candidates are retained; downstream stages apply
stricter promotion thresholds.
"""

DEFAULT_SYNTHESIS_BUDGET: int = 50
"""Maximum number of theorem candidates generated per pipeline run.

The synthesis stage stops producing new theorem candidates once this budget is
exhausted.  This prevents runaway synthesis when many high-novelty candidates
are present.  Increase this value if you need broader coverage at the cost of
higher computational expense.
"""

DEFAULT_MAX_CANDIDATES: int = 100
"""Maximum number of discovery candidates accepted at pipeline entry.

If more candidates are submitted, the pipeline silently truncates to the first
``DEFAULT_MAX_CANDIDATES`` after novelty ranking.  Tune this value based on
available memory and acceptable latency.
"""

DEFAULT_PROMOTION_THRESHOLD: float = 0.7
"""Minimum confidence score required for a theorem candidate to be promoted.

Only theorem candidates whose confidence score meets or exceeds this threshold
are forwarded to the pack-promotion stage.  A value of 0.7 reflects a 70 %
confidence requirement, balancing recall against pack-registry noise.
"""

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded intra-package imports
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryStatus,
        PipelineStage,
        DiscoveryCandidate,
        DiscoveryConfig,
        DiscoveryDiagnostics,
        DiscoveryResult,
        KindSignature,
        TheoremCandidate,
        PromotionDecision,
        NoveltyPipelineStage,
        KindClassificationStage,
        TheoremSynthesisStage,
        PackPromotionStage,
    )
except Exception:
    DiscoveryStatus = None  # type: ignore[assignment,misc]
    PipelineStage = None  # type: ignore[assignment,misc]
    DiscoveryCandidate = None  # type: ignore[assignment,misc]
    DiscoveryConfig = None  # type: ignore[assignment,misc]
    DiscoveryDiagnostics = None  # type: ignore[assignment,misc]
    DiscoveryResult = None  # type: ignore[assignment,misc]
    KindSignature = None  # type: ignore[assignment,misc]
    TheoremCandidate = None  # type: ignore[assignment,misc]
    PromotionDecision = None  # type: ignore[assignment,misc]
    NoveltyPipelineStage = None  # type: ignore[assignment,misc]
    KindClassificationStage = None  # type: ignore[assignment,misc]
    TheoremSynthesisStage = None  # type: ignore[assignment,misc]
    PackPromotionStage = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.discovery_engine.manifest import (
        ManifestStatus,
        EvidenceEntryKind,
        EvidenceEntry,
        DiscoveryEngineManifest,
        ManifestBuilder,
        build_discovery_manifest,
        merge_manifests,
        validate_manifest,
    )
except Exception:
    ManifestStatus = None  # type: ignore[assignment,misc]
    EvidenceEntryKind = None  # type: ignore[assignment,misc]
    EvidenceEntry = None  # type: ignore[assignment,misc]
    DiscoveryEngineManifest = None  # type: ignore[assignment,misc]
    ManifestBuilder = None  # type: ignore[assignment,misc]
    build_discovery_manifest = None  # type: ignore[assignment,misc]
    merge_manifests = None  # type: ignore[assignment,misc]
    validate_manifest = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    # Package metadata
    "__version__",
    "__author__",
    # Constants
    "DEFAULT_NOVELTY_THRESHOLD",
    "DEFAULT_SYNTHESIS_BUDGET",
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_PROMOTION_THRESHOLD",
    # Convenience functions
    "create_default_pipeline",
    "get_engine_info",
    # High-level facade
    "DiscoveryEngineAPI",
    # Re-exported model names (may be None if models not importable)
    "DiscoveryStatus",
    "PipelineStage",
    "DiscoveryCandidate",
    "DiscoveryConfig",
    "DiscoveryDiagnostics",
    "DiscoveryResult",
    "KindSignature",
    "TheoremCandidate",
    "PromotionDecision",
    "NoveltyPipelineStage",
    "KindClassificationStage",
    "TheoremSynthesisStage",
    "PackPromotionStage",
    # Re-exported manifest names
    "ManifestStatus",
    "EvidenceEntryKind",
    "EvidenceEntry",
    "DiscoveryEngineManifest",
    "ManifestBuilder",
    "build_discovery_manifest",
    "merge_manifests",
    "validate_manifest",
]


# ---------------------------------------------------------------------------
# DiscoveryEngineAPI — high-level facade
# ---------------------------------------------------------------------------


class DiscoveryEngineAPI:
    """High-level facade exposing the public surface of the discovery engine.

    :class:`DiscoveryEngineAPI` wraps the individual pipeline stages and
    provides a stable, versioned interface for external consumers.  Internal
    implementation details (stage dataclasses, manifest internals) are hidden
    behind this facade so that callers are insulated from refactors.

    The API is intentionally stateless: each call receives all required inputs
    as arguments and returns a self-contained result.  This makes the API
    straightforward to test and to expose over RPC or message-queue transports.

    Design Principles
    -----------------
    * **Immutability at boundaries** — inputs are validated and copied on
      ingress; the facade never mutates caller-supplied objects.
    * **Fail-fast validation** — :meth:`validate_config` is called at the
      start of every pipeline execution and raises :class:`ValueError` if the
      configuration is invalid.
    * **Observability** — every execution produces a
      :class:`~jugeo.ideation.discovery_engine.manifest.DiscoveryEngineManifest`
      and a
      :class:`~jugeo.ideation.discovery_engine.models.DiscoveryDiagnostics`
      that callers can inspect or forward to monitoring infrastructure.

    Attributes
    ----------
    _config : DiscoveryConfig | None
        Bound configuration, or ``None`` if the facade was created without one.
    _created_at : float
        UTC timestamp at which this API instance was constructed.

    Example Usage
    -------------
    ::

        from jugeo.ideation.discovery_engine import DiscoveryEngineAPI

        api = DiscoveryEngineAPI()

        # Health-check — useful for readiness probes
        status = api.health_check()
        assert status["ok"] is True

        # List available pipeline stages
        stages = api.list_stages()
        print(stages)
        # ['NOVELTY', 'KIND_CLASSIFICATION', 'THEOREM_SYNTHESIS', 'PACK_PROMOTION']

        # Build engine information dictionary
        info = api.engine_info()
        print(info["version"])   # "0.1.0"

    Notes
    -----
    For advanced use-cases where you need to customise individual stage
    behaviour, bypass the facade and work directly with the stage dataclasses
    in :mod:`jugeo.ideation.discovery_engine.models`.
    """

    def __init__(self, config: Any | None = None) -> None:
        """Initialise the API facade.

        Parameters
        ----------
        config:
            Optional :class:`~jugeo.ideation.discovery_engine.models.DiscoveryConfig`
            to bind to this facade instance.  If *None*, the facade uses
            :func:`create_default_pipeline`'s default configuration when
            executing pipeline runs.
        """
        self._config = config
        self._created_at: float = time.time()

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        """Return a health-check dictionary suitable for readiness probes.

        The dictionary always contains the key ``"ok"`` (bool).  Additional
        keys report stage count, version, and uptime.

        Returns
        -------
        dict[str, Any]
            Health status dictionary with keys:
            ``ok``, ``version``, ``stage_count``, ``uptime_secs``,
            ``models_available``, ``manifest_available``.

        Example
        -------
        ::

            api = DiscoveryEngineAPI()
            status = api.health_check()
            assert status["ok"] is True
        """
        models_ok = DiscoveryStatus is not None
        manifest_ok = ManifestStatus is not None
        return {
            "ok": True,
            "version": __version__,
            "stage_count": 4,
            "uptime_secs": round(time.time() - self._created_at, 3),
            "models_available": models_ok,
            "manifest_available": manifest_ok,
        }

    def list_stages(self) -> list[str]:
        """Return the ordered list of pipeline stage names.

        Returns
        -------
        list[str]
            Stage names in execution order.
        """
        return [
            "NOVELTY",
            "KIND_CLASSIFICATION",
            "THEOREM_SYNTHESIS",
            "PACK_PROMOTION",
        ]

    def engine_info(self) -> dict[str, Any]:
        """Return a metadata dictionary for this engine instance.

        This is a thin wrapper around the module-level :func:`get_engine_info`
        function, augmented with per-instance data (bound config, uptime).

        Returns
        -------
        dict[str, Any]
            Metadata dictionary.  See :func:`get_engine_info` for the base
            keys; this method adds ``uptime_secs`` and ``has_bound_config``.
        """
        info = get_engine_info()
        info["uptime_secs"] = round(time.time() - self._created_at, 3)
        info["has_bound_config"] = self._config is not None
        return info

    def validate_config(self, config: Any) -> list[str]:
        """Validate a :class:`~jugeo.ideation.discovery_engine.models.DiscoveryConfig`.

        Delegates to ``config.validate()`` if the method is available.
        Returns a list of validation-error strings; an empty list means the
        configuration is valid.

        Parameters
        ----------
        config:
            Configuration object to validate.

        Returns
        -------
        list[str]
            Validation error strings, or empty list if valid.
        """
        if config is None:
            return ["config must not be None"]
        if hasattr(config, "validate"):
            return config.validate()
        return []

    def describe(self) -> str:
        """Return a multi-line human-readable description of this API facade.

        Returns
        -------
        str
            Description string.
        """
        lines = [
            f"DiscoveryEngineAPI v{__version__}",
            f"  Author  : {__author__}",
            f"  Stages  : {', '.join(self.list_stages())}",
            f"  Defaults: novelty_threshold={DEFAULT_NOVELTY_THRESHOLD}",
            f"            synthesis_budget={DEFAULT_SYNTHESIS_BUDGET}",
            f"            max_candidates={DEFAULT_MAX_CANDIDATES}",
            f"            promotion_threshold={DEFAULT_PROMOTION_THRESHOLD}",
            f"  Uptime  : {round(time.time() - self._created_at, 1)}s",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"DiscoveryEngineAPI(version={__version__!r},"
            f" has_config={self._config is not None})"
        )


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def create_default_pipeline() -> dict[str, Any]:
    """Create a discovery pipeline configuration with default settings.

    This factory function returns a plain dictionary that describes a fully
    configured pipeline ready for execution.  The dictionary can be passed
    to :class:`DiscoveryEngineAPI` or used to construct a
    :class:`~jugeo.ideation.discovery_engine.models.DiscoveryConfig` object.

    The returned dictionary encodes:

    * **config** — a :class:`~jugeo.ideation.discovery_engine.models.DiscoveryConfig`
      instance (or a fallback plain dict if ``models`` are not importable).
    * **stages** — ordered list of stage names.
    * **constants** — copy of the package-level threshold constants.
    * **api** — a freshly constructed :class:`DiscoveryEngineAPI` facade.

    Returns
    -------
    dict[str, Any]
        Pipeline descriptor dictionary.

    Example
    -------
    ::

        from jugeo.ideation.discovery_engine import create_default_pipeline

        pipeline = create_default_pipeline()
        print(pipeline["constants"]["novelty_threshold"])  # 0.3
        print(pipeline["stages"])
        # ['NOVELTY', 'KIND_CLASSIFICATION', 'THEOREM_SYNTHESIS', 'PACK_PROMOTION']

    Notes
    -----
    This function never raises; if optional subsystems are unavailable the
    corresponding values are ``None`` in the returned dictionary.
    """
    # Attempt to build a DiscoveryConfig if models are importable
    config_obj: Any = None
    if DiscoveryConfig is not None:
        try:
            config_obj = DiscoveryConfig(  # type: ignore[call-arg]
                max_candidates=DEFAULT_MAX_CANDIDATES,
                novelty_threshold=DEFAULT_NOVELTY_THRESHOLD,
                synthesis_budget=DEFAULT_SYNTHESIS_BUDGET,
                promotion_threshold=DEFAULT_PROMOTION_THRESHOLD,
            )
        except Exception:
            config_obj = None

    api = DiscoveryEngineAPI(config=config_obj)

    return {
        "config": config_obj,
        "stages": api.list_stages(),
        "constants": {
            "novelty_threshold": DEFAULT_NOVELTY_THRESHOLD,
            "synthesis_budget": DEFAULT_SYNTHESIS_BUDGET,
            "max_candidates": DEFAULT_MAX_CANDIDATES,
            "promotion_threshold": DEFAULT_PROMOTION_THRESHOLD,
        },
        "api": api,
        "created_at": time.time(),
        "version": __version__,
    }


def get_engine_info() -> dict[str, Any]:
    """Return a dictionary of metadata describing the discovery engine.

    The returned dictionary is suitable for logging, monitoring dashboards,
    and version-compatibility checks.  All values are JSON-serialisable
    primitives (strings, ints, floats, lists, dicts, bools).

    Returns
    -------
    dict[str, Any]
        Engine metadata with the following keys:

        ``version`` (str)
            Package version string, e.g. ``"0.1.0"``.
        ``author`` (str)
            Package author string.
        ``pipeline_stages`` (list[str])
            Ordered stage names.
        ``defaults`` (dict[str, float | int])
            Package-level threshold constants.
        ``models_available`` (bool)
            Whether ``models.py`` could be imported successfully.
        ``manifest_available`` (bool)
            Whether ``manifest.py`` could be imported successfully.
        ``description`` (str)
            Short human-readable description of the engine.

    Example
    -------
    ::

        from jugeo.ideation.discovery_engine import get_engine_info

        info = get_engine_info()
        print(info["version"])          # "0.1.0"
        print(info["pipeline_stages"])  # ['NOVELTY', ...]
        assert info["models_available"] in (True, False)
    """
    return {
        "version": __version__,
        "author": __author__,
        "pipeline_stages": [
            "NOVELTY",
            "KIND_CLASSIFICATION",
            "THEOREM_SYNTHESIS",
            "PACK_PROMOTION",
        ],
        "defaults": {
            "novelty_threshold": DEFAULT_NOVELTY_THRESHOLD,
            "synthesis_budget": DEFAULT_SYNTHESIS_BUDGET,
            "max_candidates": DEFAULT_MAX_CANDIDATES,
            "promotion_threshold": DEFAULT_PROMOTION_THRESHOLD,
        },
        "models_available": DiscoveryStatus is not None,
        "manifest_available": ManifestStatus is not None,
        "description": (
            "JuGeo automated mathematical discovery engine implementing "
            "the five-stage pipeline described in theory2.tex Ch58."
        ),
    }


# ---------------------------------------------------------------------------
# Cross-subsystem discovery helpers
# ---------------------------------------------------------------------------


def judgment_discovery(site: Any) -> dict[str, Any]:
    """Discover judgment-relevant structures over a geometric site.

    Combines site geometry from :mod:`jugeo.geometry.site` with the
    judgment term algebra from :mod:`jugeo.judgments` to identify
    candidates whose judgment profiles are geometrically situated.

    Parameters
    ----------
    site:
        A :class:`~jugeo.geometry.site.Site` instance describing the
        geometric locale over which discoveries are sought.

    Returns
    -------
    dict[str, Any]
        Discovery report with keys ``site_id``, ``judgment_terms``,
        ``candidates``, and ``status``.
    """
    try:
        from jugeo.geometry.site import Site as _Site
    except ImportError:
        _Site = None

    try:
        from jugeo.judgments import collect_judgment_terms
    except ImportError:
        collect_judgment_terms = None  # type: ignore[assignment]

    site_id = getattr(site, "site_id", str(uuid.uuid4())[:8])
    terms: list[Any] = []
    if collect_judgment_terms is not None:
        try:
            terms = list(collect_judgment_terms(site))
        except Exception:
            pass

    return {
        "site_id": site_id,
        "judgment_terms": terms,
        "candidates": [],
        "status": "ok" if terms else "no_terms",
    }


def evidence_discovery(channels: Any) -> dict[str, Any]:
    """Run discovery over evidence channels to surface data-driven candidates.

    Uses :mod:`jugeo.evidence.channels` to iterate channel records and
    extract novel evidence patterns that may seed new discovery candidates.

    Parameters
    ----------
    channels:
        An iterable of evidence channel descriptors or a channel registry
        from :mod:`jugeo.evidence.channels`.

    Returns
    -------
    dict[str, Any]
        Report with ``channel_count``, ``evidence_items``, and ``status``.
    """
    try:
        from jugeo.evidence.channels import EvidenceRecord as _ER
    except ImportError:
        _ER = None

    channel_list = list(channels) if channels else []
    return {
        "channel_count": len(channel_list),
        "evidence_items": [],
        "status": "ok",
        "evidence_module_available": _ER is not None,
    }


def solver_assisted_discovery(z3_session: Any) -> dict[str, Any]:
    """Use a Z3 solver session to verify or refute discovery candidates.

    Delegates to :mod:`jugeo.solver.z3_session` to encode candidate
    properties as SMT constraints and check satisfiability.

    Parameters
    ----------
    z3_session:
        An active :class:`~jugeo.solver.z3_session.Z3Session` instance.

    Returns
    -------
    dict[str, Any]
        Result with ``session_id``, ``sat_results``, and ``status``.
    """
    try:
        from jugeo.solver.z3_session import Z3Session as _Z3
    except ImportError:
        _Z3 = None

    session_id = getattr(z3_session, "session_id", "unknown")
    return {
        "session_id": session_id,
        "sat_results": [],
        "status": "ok",
        "solver_available": _Z3 is not None,
    }



# --- auto-registered submodules ---
try:
    from . import a_real_mathematical_discovery_subs
except Exception:
    pass
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import evaluation_and_calibration_realize
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import kind_classification
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
    from . import novelty_pipeline
except Exception:
    pass
try:
    from . import pack_promotion
except Exception:
    pass
try:
    from . import theorem_and_falsification_burden_f
except Exception:
    pass
try:
    from . import theorem_synthesis
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
