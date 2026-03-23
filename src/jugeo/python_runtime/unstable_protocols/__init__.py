"""Unstable protocols package for JuGeo's sheaf-theoretic semantic verification.

This package implements Chapter 22 theory: protocols as behavioral sections
that may become stale, proxy objects as transport-restricted sections,
stability monitoring, and delegation chains as morphisms between protocol
sections.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §1 Protocol sections – behavioral sections over semantic coordinates
* §2 Proxy delegation – transport restrictions as cover conditions
* §3 Unstable surfaces – boundaries where support is actively retracting
* §4 Stability monitors – drift detection between declared/observed behavior
* §5 Delegation morphisms – morphisms between protocol sections

Submodules
----------
* manifest – Package manifest, version, theory alignment
* models – Core data models (ProtocolSection, ProxyRecord, etc.)
* protocol_sections – Protocol section theory and operations
* proxy_delegation – Proxy and delegation theory
* unstable_surfaces – Unstable surface tracking
* algorithms – Core algorithms for analysis and validation
* integration – Integration layer for other jugeo subsystems
* theorems – Formal theorem statements and proofs for Ch22
"""
from __future__ import annotations

from jugeo.python_runtime.unstable_protocols.models import (
    StabilityLevel,
    ProxyRestriction,
    DelegationKind,
    ProtocolSection,
    ProxyRecord,
    DelegationChain,
    UnstableInterface,
    StabilityMonitor,
)
from jugeo.python_runtime.unstable_protocols.manifest import (
    UnstableProtocolsManifest,
    SymbolRecord,
    ManifestValidator,
    ManifestRegistry,
    TheoryAlignment,
    PACKAGE_VERSION,
    THEORY_CHAPTER,
)
from jugeo.python_runtime.unstable_protocols.protocol_sections import (
    ProtocolSectionManager,
    ProtocolDescentEngine,
    ProtocolGluer,
    StalenessDetector,
)
from jugeo.python_runtime.unstable_protocols.proxy_delegation import (
    ProxyManager,
    DelegationMorphism,
    DelegationChainBuilder,
    ProxyValidator,
)
from jugeo.python_runtime.unstable_protocols.unstable_surfaces import (
    SurfaceTracker,
    RetractionEventLog,
    ObstructionInjector,
    SurfaceStabilizer,
)
from jugeo.python_runtime.unstable_protocols.algorithms import (
    ProtocolAnalyzer,
    StabilityChecker,
    DelegationTracker,
    ProxyValidator as AlgorithmProxyValidator,
)
from jugeo.python_runtime.unstable_protocols.integration import (
    UnstableProtocolIntegration,
    SupportBridge,
    JudgmentBridge,
    FleetBridge,
)
from jugeo.python_runtime.unstable_protocols.theorems import (
    TheoremRecord,
    TheoremProver,
    TheoremLibrary,
    THEOREM_PROTOCOL_SECTION_STALENESS,
    THEOREM_PROXY_TRANSPORT_RESTRICTION,
    THEOREM_DELEGATION_MORPHISM,
    THEOREM_SURFACE_RETRACTION,
    THEOREM_STABILITY_MONITOR,
    THEOREM_PROXY_EXPIRY,
    THEOREM_DELEGATION_CYCLE_OBSTRUCTION,
    THEOREM_SUPPORT_COVERAGE,
)

__all__ = [
    # models
    "StabilityLevel", "ProxyRestriction", "DelegationKind",
    "ProtocolSection", "ProxyRecord", "DelegationChain",
    "UnstableInterface", "StabilityMonitor",
    # manifest
    "UnstableProtocolsManifest", "SymbolRecord", "ManifestValidator",
    "ManifestRegistry", "TheoryAlignment", "PACKAGE_VERSION", "THEORY_CHAPTER",
    # s01
    "ProtocolSectionManager", "ProtocolDescentEngine", "ProtocolGluer", "StalenessDetector",
    # s02
    "ProxyManager", "DelegationMorphism", "DelegationChainBuilder", "ProxyValidator",
    # s03
    "SurfaceTracker", "RetractionEventLog", "ObstructionInjector", "SurfaceStabilizer",
    # algorithms
    "ProtocolAnalyzer", "StabilityChecker", "DelegationTracker", "AlgorithmProxyValidator",
    # integration
    "UnstableProtocolIntegration", "SupportBridge", "JudgmentBridge", "FleetBridge",
    # theorems
    "TheoremRecord", "TheoremProver", "TheoremLibrary",
    "THEOREM_PROTOCOL_SECTION_STALENESS", "THEOREM_PROXY_TRANSPORT_RESTRICTION",
    "THEOREM_DELEGATION_MORPHISM", "THEOREM_SURFACE_RETRACTION",
    "THEOREM_STABILITY_MONITOR", "THEOREM_PROXY_EXPIRY",
    "THEOREM_DELEGATION_CYCLE_OBSTRUCTION", "THEOREM_SUPPORT_COVERAGE",
    # cross-references
    "protocol_judgment", "protocol_trust", "protocol_encoding",
]


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def protocol_judgment(protocol: object) -> tuple:
    """Create a judgment term for an unstable protocol.

    Uses :mod:`jugeo.judgments.judgment_terms` to build a judgment term
    encoding the protocol's stability level, delegation kind, and
    behavioral obligations.

    Parameters
    ----------
    protocol : object
        A protocol record (e.g. :class:`ProtocolSection`,
        :class:`UnstableInterface`).

    Returns
    -------
    tuple
        A judgment term tuple.
    """
    try:
        from jugeo.judgments.judgment_terms import make_judgment_term
    except ImportError:
        return ("protocol", type(protocol).__name__, str(protocol), None, False, False, (), 0)

    kind = type(protocol).__name__
    coordinate = getattr(protocol, "name", getattr(protocol, "section_id", str(protocol)))
    return make_judgment_term(
        coordinate=coordinate,
        kind=f"protocol_{kind}",
        parameters=(),
        return_type=None,
        is_async=False,
        is_generator=False,
        decorators=(),
        trust_level=getattr(protocol, "trust_level", 0),
    )


def protocol_trust(protocol: object) -> object:
    """Assign a trust level to an unstable protocol.

    Uses :mod:`jugeo.evidence.trust` to compute a trust level based on
    the protocol's stability level and staleness metrics.

    Parameters
    ----------
    protocol : object
        A protocol record.

    Returns
    -------
    object
        A trust-level object or integer.
    """
    try:
        from jugeo.evidence.trust import assign_trust
    except ImportError:
        return 0

    stability = getattr(protocol, "stability_level", getattr(protocol, "stability", "unknown"))
    kind = type(protocol).__name__
    return assign_trust(
        entity="protocol",
        kind=kind,
        origin=str(stability),
    )


def protocol_encoding(protocol: object) -> object:
    """Encode an unstable protocol for Z3 constraint solving.

    Uses :mod:`jugeo.encodings` to produce a Z3-compatible encoding of
    the protocol's behavioral constraints and stability conditions.

    Parameters
    ----------
    protocol : object
        A protocol record.

    Returns
    -------
    object
        A Z3 encoding, or *None* if the encoding layer is unavailable.
    """
    try:
        from jugeo.encodings import encode_value
    except ImportError:
        return None

    kind = type(protocol).__name__
    name = getattr(protocol, "name", getattr(protocol, "section_id", "protocol"))
    return encode_value(
        label=f"protocol_{kind}_{name}",
        value=name,
        domain="protocol",
    )


# copilot: package init for unstable_protocols, re-exports all public symbols


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import delegation_chains
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
    from . import protocol_obligations
except Exception:
    pass
try:
    from . import protocol_sections
except Exception:
    pass
try:
    from . import proxy_delegation
except Exception:
    pass
try:
    from . import stable_versus_unstable_surface_are
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import unstable_surfaces
except Exception:
    pass
try:
    from . import why_this_matters_for_repair
except Exception:
    pass
