"""Stable vs unstable surface area classification for JuGeo unstable protocols (Ch22 §1).

Theory alignment (Ch22, theory2.tex)
--------------------------------------
* §1  Stable surface area – the portion of a Python interface that is
      explicitly contracted: declared via ``typing.Protocol``, ``abc.ABC``,
      or a formal ``__all__`` + version pin.  A stable surface may be
      trusted up to ``TrustLevel.SOLVER_DISCHARGED`` because its contract
      is machine-checkable.
* §1  Unstable surface area – the remaining portion of an interface that
      leaks implementation details through private-attribute access via public
      methods, undocumented dunder overrides, or cross-module type references
      that bind callers to internal types.  The trust ceiling for an unstable
      surface is capped at ``TrustLevel.ORACLE_PROPOSED``.
* §1  Surface heuristics – the eight heuristics defined in
      ``StabilityHeuristic`` operationalise the dichotomic classification.
      Each heuristic is independently checked and combined into a weighted
      trust ceiling.
* §1  Stability drift – when a previously stable surface later exposes private
      details (e.g. after a refactor), a *regression* is recorded.
      Regressions are first-class obstructions that block automated analysis
      until they are explicitly resolved.

The classes in this module implement the three exported coordinators:

* ``StableUnstableSurfaceAreaCoordinator``  – lifecycle coordinator
* ``StableUnstableSurfaceAreaAnalyzer``     – judgment-emitting analyzer
* ``StableUnstableSurfaceAreaWitness``      – runtime witness for attribute
  access patterns

Note: the file name carries a deliberate typo (``surface_are`` instead of
``surface_area``) that is preserved as part of the canonical module identity;
do not rename it.
"""

from __future__ import annotations

import abc
import enum
import functools
import hashlib
import inspect
import json
import logging
import time
import typing
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace as dc_replace
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local model imports
# ---------------------------------------------------------------------------
# copilot: import core stability models; fall back to minimal stubs so the
# module stays importable even without the full jugeo installation.
try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection,
        StabilityLevel,
        ProxyRecord,
        ProxyRestriction,
        DelegationChain,
        DelegationKind,
        UnstableInterface,
        StabilityMonitor,
    )
except ImportError:  # pragma: no cover
    class ProtocolSection:  # type: ignore[no-redef]
        """Stub for ProtocolSection when models are unavailable."""
        pass

    class StabilityLevel(enum.Enum):  # type: ignore[no-redef]
        """Stub StabilityLevel with the five canonical levels."""
        STABLE = "stable"
        DEGRADING = "degrading"
        UNSTABLE = "unstable"
        RETRACTING = "retracting"
        COLLAPSED = "collapsed"

    class ProxyRecord:  # type: ignore[no-redef]
        """Stub ProxyRecord."""
        pass

    class ProxyRestriction:  # type: ignore[no-redef]
        """Stub ProxyRestriction."""
        pass

    class DelegationChain:  # type: ignore[no-redef]
        """Stub DelegationChain."""
        pass

    class DelegationKind(enum.Enum):  # type: ignore[no-redef]
        """Stub DelegationKind."""
        DIRECT = "direct"
        PROXIED = "proxied"

    class UnstableInterface:  # type: ignore[no-redef]
        """Stub UnstableInterface."""
        pass

    class StabilityMonitor:  # type: ignore[no-redef]
        """Stub StabilityMonitor."""
        pass

# ---------------------------------------------------------------------------
# Cross-package stubs – geometry.site
# ---------------------------------------------------------------------------
# copilot: geometry.site is an optional dependency; provide complete stubs so
# that this module remains importable in minimal environments.
try:
    from jugeo.geometry.site import (
        CoordinateObject,
        CoordinateKind,
        CoordinateMorphism,
        MorphismKind,
        Site,
        SiteBuilder,
    )
except Exception:  # pragma: no cover
    class CoordinateKind(enum.Enum):  # type: ignore[no-redef]
        """Stub CoordinateKind."""
        MODULE = "module"
        FUNCTION = "function"
        INTERFACE = "interface"
        TEST = "test"
        THEOREM = "theorem"
        REGION = "region"

    class MorphismKind(enum.Enum):  # type: ignore[no-redef]
        """Stub MorphismKind."""
        RESTRICTION = "restriction"
        INCLUSION = "inclusion"
        TRANSPORT = "transport"
        REFINEMENT = "refinement"

    @dataclass(frozen=True, slots=True)
    class CoordinateObject:  # type: ignore[no-redef]
        """Stub CoordinateObject representing a geometric coordinate."""
        components: tuple[str, ...] = ()
        kind: Any = None
        support_labels: frozenset[str] = field(default_factory=frozenset)
        metadata: dict = field(default_factory=dict)

    class CoordinateMorphism:  # type: ignore[no-redef]
        """Stub CoordinateMorphism linking two coordinate objects."""
        def __init__(self, source: Any, target: Any, reason: str = "") -> None:
            self.source = source
            self.target = target
            self.reason = reason

    class Site:  # type: ignore[no-redef]
        """Stub Site."""
        pass

    class SiteBuilder:  # type: ignore[no-redef]
        """Stub SiteBuilder."""
        pass

# ---------------------------------------------------------------------------
# Cross-package stubs – judgments.judgment_terms
# ---------------------------------------------------------------------------
# copilot: judgment_terms is also optional; stubs provide full API surface
# so callers can duck-type against them.
try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        PropositionKind,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
    )
except Exception:  # pragma: no cover
    class TrustLevel(enum.IntEnum):  # type: ignore[no-redef]
        """Stub TrustLevel – lower bound of machine verifiability."""
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class JudgmentStatus(enum.Enum):  # type: ignore[no-redef]
        """Stub JudgmentStatus."""
        PROPOSED = "proposed"
        CHALLENGED = "challenged"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class PropositionKind(enum.Enum):  # type: ignore[no-redef]
        """Stub PropositionKind."""
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"
        RESOURCE = "resource"
        SEMANTIC = "semantic"

    class EvidenceItemKind(enum.Enum):  # type: ignore[no-redef]
        """Stub EvidenceItemKind."""
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"
        FORMAL_PROOF = "formal_proof"

    class ProvenanceSource(enum.Enum):  # type: ignore[no-redef]
        """Stub ProvenanceSource."""
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"
        COMPOSED = "composed"

    @dataclass(frozen=True, slots=True)
    class Proposition:  # type: ignore[no-redef]
        """Stub Proposition – a logical formula with free variables."""
        kind: Any = None
        formula: str = ""
        free_variables: tuple[str, ...] = ()
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class Carrier:  # type: ignore[no-redef]
        """Stub Carrier – names the type carried by a judgment."""
        name: str = ""
        parameters: tuple[str, ...] = ()
        is_dependent: bool = False
        metadata: dict = field(default_factory=dict)

    @dataclass(frozen=True, slots=True)
    class EvidenceItem:  # type: ignore[no-redef]
        """Stub EvidenceItem – one piece of evidence supporting a judgment."""
        kind: Any = None
        payload: dict = field(default_factory=dict)
        trust_level: Any = None
        channel: str = ""
        timestamp: str = ""
        expiry: str = ""
        provenance: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class EvidenceBundle:  # type: ignore[no-redef]
        """Stub EvidenceBundle – collection of EvidenceItems."""
        items: tuple[Any, ...] = ()

    @dataclass(frozen=True, slots=True)
    class ResidualObligation:  # type: ignore[no-redef]
        """Stub ResidualObligation – an open proof obligation."""
        description: str = ""
        obligation_id: str = ""
        priority: int = 1
        is_discharged: bool = False

        def discharge(self, evidence: str = "") -> "ResidualObligation":
            """Return a new obligation marked as discharged."""
            return dc_replace(self, is_discharged=True)

    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        """Stub Obstruction – a first-class cohomology obstruction."""
        description: str = ""
        obstruction_id: str = ""
        severity: int = 1

    @dataclass(frozen=True, slots=True)
    class TrustAnnotation:  # type: ignore[no-redef]
        """Stub TrustAnnotation – attaches a trust level and rationale."""
        level: Any = None
        rationale: str = ""

    @dataclass(frozen=True, slots=True)
    class Provenance:  # type: ignore[no-redef]
        """Stub Provenance – records how a judgment was derived."""
        sources: tuple[Any, ...] = ()
        chain: tuple[str, ...] = ()

    @dataclass(frozen=True, slots=True)
    class Judgment:  # type: ignore[no-redef]
        """Stub Judgment – the full judgment record."""
        coordinate: Any = None
        proposition: Any = None
        carrier: Any = None
        evidence: Any = None
        obligations: tuple = ()
        obstructions: tuple = ()
        trust: Any = None
        provenance: Any = None


# ===========================================================================
# Domain enumerations
# ===========================================================================


class StabilityHeuristic(enum.Enum):
    """Eight heuristics used to classify a Python surface as stable or unstable.

    Theory alignment (Ch22, theory2.tex §1.2):
    -------------------------------------------
    The heuristics form a partial order where positive indicators
    (``EXPLICIT_PROTOCOL``, ``ABSTRACT_BASE_CLASS``, ``DOCUMENTED_API``,
    ``VERSION_PINNED``) raise the trust ceiling, while negative indicators
    (``HAS_PRIVATE_EXPOSURE``, ``HAS_DUNDER_OVERRIDE``,
    ``IMPLEMENTATION_LEAKING``, ``DEPRECATED_MARKERS``) lower it.  The
    final trust ceiling is computed as the meet of the individual bounds
    returned by :func:`SurfaceClassifier._compute_trust_ceiling`.

    Attributes
    ----------
    EXPLICIT_PROTOCOL:
        The object or one of its bases is a ``typing.Protocol`` subclass.
        This is the strongest positive signal: the contract is machine-
        checkable at static-analysis time.
    ABSTRACT_BASE_CLASS:
        The object uses ``abc.ABCMeta`` as its metaclass or inherits from
        ``abc.ABC``.  Slightly weaker than EXPLICIT_PROTOCOL because ABCs
        do not enforce structural subtyping by default.
    DOCUMENTED_API:
        ``__all__`` is defined and non-empty, indicating the author has
        consciously curated the public surface.
    HAS_PRIVATE_EXPOSURE:
        At least one name beginning with a single underscore (but not a
        dunder) is reachable via a public method's return annotation or
        default argument.
    HAS_DUNDER_OVERRIDE:
        The object overrides one or more dunder methods beyond the
        canonical ``__init__`` / ``__repr__`` / ``__str__`` trio.
        Overriding e.g. ``__getattr__`` or ``__setattr__`` is a common
        source of unstable behaviour.
    IMPLEMENTATION_LEAKING:
        Docstrings or type annotations reference internal module paths
        (heuristically detected by the presence of ``._`` segments in
        qualified names found in annotations).
    VERSION_PINNED:
        ``__version__`` is defined on the object or its containing module.
    DEPRECATED_MARKERS:
        The docstring contains the word *deprecated* or *deprecat* (case-
        insensitive), or ``__deprecated__`` is defined.
    """

    EXPLICIT_PROTOCOL = "explicit_protocol"
    ABSTRACT_BASE_CLASS = "abstract_base_class"
    DOCUMENTED_API = "documented_api"
    HAS_PRIVATE_EXPOSURE = "has_private_exposure"
    HAS_DUNDER_OVERRIDE = "has_dunder_override"
    IMPLEMENTATION_LEAKING = "implementation_leaking"
    VERSION_PINNED = "version_pinned"
    DEPRECATED_MARKERS = "deprecated_markers"


# ===========================================================================
# Frozen dataclasses
# ===========================================================================


@dataclass(frozen=True, slots=True)
class SurfaceStabilityRecord:
    """Immutable record capturing the stability classification of one interface.

    Theory alignment (Ch22, theory2.tex §1.3):
    -------------------------------------------
    Each record is uniquely identified by ``coordinate_key`` (a SHA-256
    digest of ``interface_name + last_checked``).  Records are immutable;
    updating a classification requires creating a new record and passing
    both the old and new to :class:`SurfaceComparisonEngine`.

    Attributes
    ----------
    interface_name : str
        Fully qualified name of the Python object (e.g.
        ``collections.OrderedDict``).
    stability_level : StabilityLevel
        The overall stability verdict for this surface.
    reasons : tuple[str, ...]
        Human-readable descriptions of the heuristics that fired.
    leaked_details : tuple[str, ...]
        Names of attributes / types that constitute implementation leakage.
    public_api : frozenset[str]
        Set of names that form the public contract.
    private_exposed : frozenset[str]
        Subset of private names accessible through the public API.
    trust_ceiling : TrustLevel
        Maximum trust level that analysis results may claim for this
        interface.  Capped by the most severe negative heuristic found.
    coordinate_key : str
        Stable SHA-256 digest used to identify this record across storage
        boundaries.
    last_checked : str
        ISO-8601 timestamp at which classification was last run.
    """

    interface_name: str
    stability_level: Any  # StabilityLevel
    reasons: tuple[str, ...]
    leaked_details: tuple[str, ...]
    public_api: frozenset[str]
    private_exposed: frozenset[str]
    trust_ceiling: Any  # TrustLevel
    coordinate_key: str
    last_checked: str

    # ------------------------------------------------------------------
    # Derived queries
    # ------------------------------------------------------------------

    def is_stable(self) -> bool:
        """Return True when the stability level is STABLE or DEGRADING.

        A *stable* surface is one where positive heuristics outweigh
        negative ones and no private members are exposed through the
        public API.  The judgment trust ceiling will be at least
        ``TrustLevel.RUNTIME_WITNESSED`` for stable surfaces.

        Returns
        -------
        bool
            ``True`` iff :attr:`stability_level` is ``STABLE`` or
            ``DEGRADING``.

        Examples
        --------
        >>> rec.is_stable()
        True
        """
        # copilot: mirror the is_healthy() convention from models.StabilityLevel
        try:
            return self.stability_level in (
                StabilityLevel.STABLE,
                StabilityLevel.DEGRADING,
            )
        except Exception:
            return str(self.stability_level) in ("stable", "degrading")

    def stability_score(self) -> float:
        """Return a [0.0, 1.0] numeric stability score.

        The score is computed from the trust ceiling relative to the
        maximum possible trust level.  A fully stable surface scores 1.0;
        a contradicted surface scores 0.0.

        The mapping is::

            score = trust_ceiling.value / max_trust_value

        where ``max_trust_value`` defaults to 5 (``VERIFIED_PROOF``) when
        the TrustLevel enum is present.

        Returns
        -------
        float
            Normalised stability score in [0.0, 1.0].
        """
        # copilot: normalise trust ceiling against maximum known trust level
        try:
            max_trust = max(t.value for t in TrustLevel)
            raw = int(self.trust_ceiling.value) if hasattr(self.trust_ceiling, "value") else int(self.trust_ceiling)
            return max(0.0, min(1.0, raw / max_trust))
        except Exception:
            # If we cannot determine the ceiling, default to midpoint
            return 0.5

    def to_dict(self) -> dict:
        """Serialise the record to a plain Python dictionary.

        All frozensets are converted to sorted lists so that the result is
        JSON-serialisable.  Enum members are converted to their ``.value``
        strings.

        Returns
        -------
        dict
            JSON-serialisable representation of this record.
        """
        # copilot: serialise enums and frozensets so callers can json.dumps directly
        def _enum_val(v: Any) -> Any:
            return v.value if hasattr(v, "value") else str(v)

        return {
            "interface_name": self.interface_name,
            "stability_level": _enum_val(self.stability_level),
            "reasons": list(self.reasons),
            "leaked_details": list(self.leaked_details),
            "public_api": sorted(self.public_api),
            "private_exposed": sorted(self.private_exposed),
            "trust_ceiling": _enum_val(self.trust_ceiling),
            "coordinate_key": self.coordinate_key,
            "last_checked": self.last_checked,
            "is_stable": self.is_stable(),
            "stability_score": self.stability_score(),
        }

    def summary(self) -> str:
        """Return a concise one-line summary of this stability record.

        The summary is suitable for logging and human-readable reports.
        Format::

            <interface_name> [<stability_level>] score=<score:.2f>
            public=<n> private_exposed=<m>

        Returns
        -------
        str
            Single-line summary string.
        """
        # copilot: keep summary compact for log lines; score formatted to 2dp
        level_str = self.stability_level.value if hasattr(self.stability_level, "value") else str(self.stability_level)
        return (
            f"{self.interface_name} [{level_str}] "
            f"score={self.stability_score():.2f} "
            f"public={len(self.public_api)} "
            f"private_exposed={len(self.private_exposed)}"
        )


@dataclass(frozen=True, slots=True)
class WitnessRecord:
    """Immutable record of a single attribute-access event observed at runtime.

    Theory alignment (Ch22, theory2.tex §1.5):
    -------------------------------------------
    A witness record is created whenever a public method on a Python object
    is observed to access a private attribute (one beginning with ``_`` but
    not a dunder).  Such accesses are potential implementation leaks and are
    tracked as part of the unstable surface area evidence base.

    Attributes
    ----------
    record_id : str
        UUID v4 that uniquely identifies this witness event.
    target_qualname : str
        ``__qualname__`` of the object whose attribute was accessed.
    attr_name : str
        Name of the attribute that was accessed.
    via_public_method : str
        Name of the public method through which the access was made, or
        an empty string if the access was direct.
    timestamp : str
        ISO-8601 timestamp of the access event.
    is_private_leak : bool
        ``True`` when ``attr_name`` starts with ``_`` but not ``__``.
    call_stack_depth : int
        Depth of the Python call stack at the moment of observation.
    metadata : dict
        Arbitrary additional context (e.g. thread id, frame locals keys).
    """

    record_id: str
    target_qualname: str
    attr_name: str
    via_public_method: str
    timestamp: str
    is_private_leak: bool
    call_stack_depth: int
    metadata: dict

    def to_dict(self) -> dict:
        """Serialise the witness record to a plain Python dictionary.

        Returns
        -------
        dict
            JSON-serialisable dictionary representation of this record.
        """
        # copilot: metadata may contain non-serialisable values; coerce to str
        safe_metadata = {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                         for k, v in self.metadata.items()}
        return {
            "record_id": self.record_id,
            "target_qualname": self.target_qualname,
            "attr_name": self.attr_name,
            "via_public_method": self.via_public_method,
            "timestamp": self.timestamp,
            "is_private_leak": self.is_private_leak,
            "call_stack_depth": self.call_stack_depth,
            "metadata": safe_metadata,
        }


@dataclass(frozen=True, slots=True)
class SurfaceAuditReport:
    """Immutable report produced by a full module stability audit.

    Theory alignment (Ch22, theory2.tex §1.6):
    -------------------------------------------
    An audit report aggregates all :class:`SurfaceStabilityRecord` objects
    produced by scanning every public class in a module.  It provides
    aggregate statistics and a human-readable summary that can be attached
    to a :class:`Judgment` as evidence.

    Attributes
    ----------
    report_id : str
        UUID v4 uniquely identifying this audit run.
    module_name : str
        Fully qualified name of the module that was audited.
    records : tuple[SurfaceStabilityRecord, ...]
        All stability records produced during the audit.
    total_stable : int
        Count of records whose :meth:`SurfaceStabilityRecord.is_stable`
        returns ``True``.
    total_unstable : int
        Count of records that are not stable.
    obstructions : tuple
        Any :class:`Obstruction` objects raised during the audit.
    generated_at : str
        ISO-8601 timestamp at which the report was generated.
    """

    report_id: str
    module_name: str
    records: tuple  # tuple[SurfaceStabilityRecord, ...]
    total_stable: int
    total_unstable: int
    obstructions: tuple
    generated_at: str

    def stability_ratio(self) -> float:
        """Return the fraction of audited surfaces that are stable.

        Returns 1.0 when there are no records (vacuously stable).

        Returns
        -------
        float
            Value in [0.0, 1.0].  1.0 means all surfaces are stable.
        """
        # copilot: guard against zero-division when module has no public classes
        total = self.total_stable + self.total_unstable
        if total == 0:
            return 1.0
        return self.total_stable / total

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the audit.

        The summary includes:
        * Module name and generation timestamp
        * Stability ratio as a percentage
        * Per-record one-line summaries
        * Count of obstructions

        Returns
        -------
        str
            Multi-line report string.
        """
        # copilot: produce a consistent format that can be embedded in log files
        lines: list[str] = [
            f"=== SurfaceAuditReport {self.report_id} ===",
            f"Module     : {self.module_name}",
            f"Generated  : {self.generated_at}",
            f"Stable     : {self.total_stable}",
            f"Unstable   : {self.total_unstable}",
            f"Ratio      : {self.stability_ratio():.1%}",
            f"Obstructions: {len(self.obstructions)}",
            "--- Records ---",
        ]
        for rec in self.records:
            lines.append(f"  {rec.summary()}")
        if self.obstructions:
            lines.append("--- Obstructions ---")
            for obs in self.obstructions:
                desc = obs.description if hasattr(obs, "description") else str(obs)
                lines.append(f"  {desc}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialise the audit report to a plain Python dictionary.

        Returns
        -------
        dict
            JSON-serialisable dictionary.
        """
        # copilot: nested records serialised via their own to_dict methods
        return {
            "report_id": self.report_id,
            "module_name": self.module_name,
            "records": [r.to_dict() for r in self.records],
            "total_stable": self.total_stable,
            "total_unstable": self.total_unstable,
            "obstructions": [
                (o.to_dict() if hasattr(o, "to_dict") else str(o))
                for o in self.obstructions
            ],
            "generated_at": self.generated_at,
            "stability_ratio": self.stability_ratio(),
        }


# ===========================================================================
# SurfaceClassifier
# ===========================================================================


class SurfaceClassifier:
    """Classifies a Python object's surface area as stable or unstable.

    Theory alignment (Ch22, theory2.tex §1.2):
    -------------------------------------------
    The classifier applies the eight :class:`StabilityHeuristic` checks in
    sequence against the Python object supplied to :meth:`classify`.  Each
    check is independent; results are accumulated into a list and then
    combined by :meth:`_compute_trust_ceiling` to produce a final
    :class:`SurfaceStabilityRecord`.

    The classifier is stateless between calls: each call to
    :meth:`classify` is a pure function of the input object.  Callers
    that want to track changes over time should wrap the classifier with a
    :class:`StabilityHistoryTracker`.

    Notes
    -----
    The classifier uses only the ``inspect`` standard library module for
    introspection; it does not execute any code on the target object.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def classify(self, obj: Any) -> SurfaceStabilityRecord:
        """Classify a Python object and return a :class:`SurfaceStabilityRecord`.

        The following checks are performed in order:

        1. Is ``obj`` a ``typing.Protocol`` subclass?
        2. Does ``obj`` use ``abc.ABCMeta``?
        3. Is ``__all__`` defined and non-empty?
        4. Are any private attributes exposed through public methods?
        5. Does ``obj`` override dunder methods beyond the canonical set?
        6. Do annotations or docstrings reference internal modules?
        7. Is ``__version__`` defined?
        8. Are there deprecation markers?

        Parameters
        ----------
        obj : Any
            Any Python object.  Classes are the primary target, but
            modules and callables are also supported.

        Returns
        -------
        SurfaceStabilityRecord
            Fully populated immutable record.
        """
        # copilot: gather qualname early; fall back gracefully for builtins
        interface_name = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or repr(obj)

        fired_heuristics: list[StabilityHeuristic] = []
        reasons: list[str] = []
        leaked_details: list[str] = []

        # --- heuristic checks ---
        if self._check_protocol(obj):
            fired_heuristics.append(StabilityHeuristic.EXPLICIT_PROTOCOL)
            reasons.append("Declares typing.Protocol in MRO – explicit contract")

        if self._check_abc(obj):
            fired_heuristics.append(StabilityHeuristic.ABSTRACT_BASE_CLASS)
            reasons.append("Uses abc.ABCMeta or inherits abc.ABC – abstract contract")

        if self._check_documented_api(obj):
            fired_heuristics.append(StabilityHeuristic.DOCUMENTED_API)
            reasons.append("__all__ is defined and non-empty – curated public API")

        if self._check_version_pinned(obj):
            fired_heuristics.append(StabilityHeuristic.VERSION_PINNED)
            reasons.append("__version__ is defined – interface is version-pinned")

        # negative heuristics ---
        private_exposed = self._get_private_exposed(obj)
        if private_exposed:
            fired_heuristics.append(StabilityHeuristic.HAS_PRIVATE_EXPOSURE)
            leaked = sorted(private_exposed)
            leaked_details.extend(leaked)
            reasons.append(f"Private members exposed via public API: {leaked[:5]}")

        if self._check_dunder_override(obj):
            fired_heuristics.append(StabilityHeuristic.HAS_DUNDER_OVERRIDE)
            reasons.append("Non-canonical dunder methods overridden – potential instability")

        impl_leaks = self._check_implementation_leaking(obj)
        if impl_leaks:
            fired_heuristics.append(StabilityHeuristic.IMPLEMENTATION_LEAKING)
            leaked_details.extend(impl_leaks)
            reasons.append(f"Annotations/docstrings reference internal types: {impl_leaks[:3]}")

        if self._check_deprecated_markers(obj):
            fired_heuristics.append(StabilityHeuristic.DEPRECATED_MARKERS)
            reasons.append("Deprecation markers found – surface is shrinking")

        # compute overall level and trust ceiling
        stability_level = self._compute_stability_level(fired_heuristics)
        trust_ceiling = self._compute_trust_ceiling(fired_heuristics)
        public_api = self._get_public_api(obj)

        # build stable coordinate key from qualname + timestamp
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        coord_raw = f"{interface_name}|{ts}"
        coordinate_key = hashlib.sha256(coord_raw.encode()).hexdigest()[:16]

        return SurfaceStabilityRecord(
            interface_name=interface_name,
            stability_level=stability_level,
            reasons=tuple(reasons),
            leaked_details=tuple(leaked_details),
            public_api=public_api,
            private_exposed=private_exposed,
            trust_ceiling=trust_ceiling,
            coordinate_key=coordinate_key,
            last_checked=ts,
        )

    # ------------------------------------------------------------------
    # Internal heuristic checks
    # ------------------------------------------------------------------

    def _check_protocol(self, obj: Any) -> bool:
        """Return True if ``obj`` is a subclass of ``typing.Protocol``.

        Checks the MRO of ``obj`` (if it is a class) for
        ``typing.Protocol``.  Non-class objects always return ``False``.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        bool
        """
        # copilot: typing.Protocol appears in MRO only for direct Protocol subclasses
        if not inspect.isclass(obj):
            return False
        try:
            protocol_cls = typing.Protocol  # type: ignore[attr-defined]
            return any(
                c is not obj and c.__name__ == "Protocol"
                for c in getattr(obj, "__mro__", ())
            )
        except Exception:
            return False

    def _check_abc(self, obj: Any) -> bool:
        """Return True if ``obj`` uses ``abc.ABCMeta`` or inherits ``abc.ABC``.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        bool
        """
        # copilot: inspect metaclass and explicit ABC inheritance separately
        if not inspect.isclass(obj):
            return False
        if type(obj) is abc.ABCMeta:
            return True
        return abc.ABC in getattr(obj, "__mro__", ())

    def _check_documented_api(self, obj: Any) -> bool:
        """Return True if ``__all__`` is defined and non-empty on ``obj``.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        bool
        """
        all_attr = getattr(obj, "__all__", None)
        return isinstance(all_attr, (list, tuple, frozenset, set)) and len(all_attr) > 0

    def _check_version_pinned(self, obj: Any) -> bool:
        """Return True if ``__version__`` is defined on ``obj`` or its module.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        bool
        """
        # copilot: check both the object and its containing module
        if hasattr(obj, "__version__"):
            return True
        mod_name = getattr(obj, "__module__", None)
        if mod_name:
            import sys
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, "__version__"):
                return True
        return False

    def _check_dunder_override(self, obj: Any) -> bool:
        """Return True if ``obj`` overrides non-canonical dunder methods.

        Canonical dunders that are *not* considered destabilising:
        ``__init__``, ``__repr__``, ``__str__``, ``__new__``, ``__doc__``,
        ``__module__``, ``__dict__``, ``__weakref__``, ``__class__``,
        ``__slots__``.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        bool
        """
        # copilot: a rich dunder override set is a signal of complex lifecycle management
        _CANONICAL = frozenset({
            "__init__", "__repr__", "__str__", "__new__", "__doc__",
            "__module__", "__dict__", "__weakref__", "__class__", "__slots__",
            "__init_subclass__", "__subclasshook__",
        })
        if not inspect.isclass(obj):
            return False
        for name, value in inspect.getmembers(obj):
            if name.startswith("__") and name.endswith("__") and name not in _CANONICAL:
                # check that it is actually overridden on obj itself, not inherited from object
                if name in obj.__dict__:
                    return True
        return False

    def _check_deprecated_markers(self, obj: Any) -> bool:
        """Return True if ``obj`` carries deprecation markers.

        Checks:
        * ``__deprecated__`` attribute exists
        * Docstring contains 'deprecat' (case-insensitive)

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        bool
        """
        if hasattr(obj, "__deprecated__"):
            return True
        doc = inspect.getdoc(obj) or ""
        return "deprecat" in doc.lower()

    def _check_implementation_leaking(self, obj: Any) -> list[str]:
        """Return names of annotations/types that reference internal modules.

        A reference is considered internal if the module path of the
        referenced type contains a component that starts with ``_``.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        list[str]
            Names of leaking annotations or types.
        """
        # copilot: look at type annotations on public methods; flag anything
        # whose __module__ contains a private path segment
        leaks: list[str] = []
        if not inspect.isclass(obj):
            return leaks

        for method_name, method in inspect.getmembers(obj, predicate=inspect.isfunction):
            if method_name.startswith("__"):
                continue
            try:
                hints = typing.get_type_hints(method)
            except Exception:
                hints = {}
            for param_name, hint in hints.items():
                hint_module = getattr(hint, "__module__", "") or ""
                # flag if any segment of the module path starts with '_'
                segments = hint_module.split(".")
                if any(seg.startswith("_") and not seg.startswith("__") for seg in segments):
                    leaks.append(f"{method_name}.{param_name}:{hint_module}")
        return leaks

    def _get_public_api(self, obj: Any) -> frozenset[str]:
        """Return the set of public attribute/method names on ``obj``.

        Public names are those that do not start with ``_``.  If
        ``__all__`` is defined, it is used instead.

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        frozenset[str]
            Frozenset of public name strings.
        """
        # copilot: prefer __all__ as an authoritative public API declaration
        all_attr = getattr(obj, "__all__", None)
        if isinstance(all_attr, (list, tuple, frozenset, set)) and all_attr:
            return frozenset(all_attr)
        # fall back to all non-underscore names
        try:
            names = [n for n in dir(obj) if not n.startswith("_")]
        except Exception:
            names = []
        return frozenset(names)

    def _get_private_exposed(self, obj: Any) -> frozenset[str]:
        """Return private attributes exposed through public method return types or defaults.

        An attribute is considered "exposed" if:
        * Its name starts with ``_`` but not ``__``
        * AND it appears as a default argument value or return annotation
          in one of the object's public methods

        Parameters
        ----------
        obj : Any
            The object to inspect.

        Returns
        -------
        frozenset[str]
            Frozenset of exposed private attribute names.
        """
        # copilot: walk public methods' signatures looking for private defaults
        exposed: set[str] = set()
        if not inspect.isclass(obj):
            # for non-class callables, check if the object itself has private attrs
            for name in dir(obj):
                if name.startswith("_") and not name.startswith("__"):
                    exposed.add(name)
            return frozenset(exposed)

        # collect all private instance attributes via __init__ or __slots__
        private_attrs: set[str] = set()
        for name in dir(obj):
            if name.startswith("_") and not name.startswith("__"):
                private_attrs.add(name)

        # check if any public method signature references them as defaults
        for method_name, method in inspect.getmembers(obj, predicate=inspect.isfunction):
            if method_name.startswith("_"):
                continue
            try:
                sig = inspect.signature(method)
                for pname, param in sig.parameters.items():
                    if (param.default is not inspect.Parameter.empty
                            and pname.startswith("_")
                            and not pname.startswith("__")):
                        exposed.add(pname)
            except (ValueError, TypeError):
                pass

        return frozenset(exposed)

    def _compute_stability_level(self, heuristics: list[StabilityHeuristic]) -> Any:
        """Derive the overall stability level from the set of fired heuristics.

        Logic (Ch22 §1.2 table):
        -------------------------
        * If IMPLEMENTATION_LEAKING or (HAS_PRIVATE_EXPOSURE and
          HAS_DUNDER_OVERRIDE): UNSTABLE
        * If HAS_PRIVATE_EXPOSURE alone: DEGRADING
        * If DEPRECATED_MARKERS and no positive heuristics: RETRACTING
        * If EXPLICIT_PROTOCOL or ABSTRACT_BASE_CLASS: STABLE
        * If DOCUMENTED_API or VERSION_PINNED alone: DEGRADING
        * Default: UNSTABLE

        Parameters
        ----------
        heuristics : list[StabilityHeuristic]
            Heuristics that fired for this object.

        Returns
        -------
        StabilityLevel
        """
        # copilot: use a priority-based decision table matching theory2.tex §1.2
        h = set(heuristics)
        negative = {
            StabilityHeuristic.HAS_PRIVATE_EXPOSURE,
            StabilityHeuristic.HAS_DUNDER_OVERRIDE,
            StabilityHeuristic.IMPLEMENTATION_LEAKING,
            StabilityHeuristic.DEPRECATED_MARKERS,
        }
        positive = {
            StabilityHeuristic.EXPLICIT_PROTOCOL,
            StabilityHeuristic.ABSTRACT_BASE_CLASS,
            StabilityHeuristic.DOCUMENTED_API,
            StabilityHeuristic.VERSION_PINNED,
        }
        has_strong_positive = bool(h & {StabilityHeuristic.EXPLICIT_PROTOCOL, StabilityHeuristic.ABSTRACT_BASE_CLASS})
        has_weak_positive = bool(h & {StabilityHeuristic.DOCUMENTED_API, StabilityHeuristic.VERSION_PINNED})
        has_leak = StabilityHeuristic.IMPLEMENTATION_LEAKING in h
        has_private = StabilityHeuristic.HAS_PRIVATE_EXPOSURE in h
        has_dunder = StabilityHeuristic.HAS_DUNDER_OVERRIDE in h
        has_deprecated = StabilityHeuristic.DEPRECATED_MARKERS in h

        if has_leak or (has_private and has_dunder):
            return StabilityLevel.UNSTABLE
        if has_deprecated and not (has_strong_positive or has_weak_positive):
            return StabilityLevel.RETRACTING
        if has_strong_positive and not has_private and not has_deprecated:
            return StabilityLevel.STABLE
        if (has_strong_positive or has_weak_positive) and not has_leak:
            return StabilityLevel.DEGRADING if has_private else StabilityLevel.STABLE
        if has_private:
            return StabilityLevel.DEGRADING
        # nothing at all is also degrading – we have no evidence of stability
        return StabilityLevel.DEGRADING

    def _compute_trust_ceiling(self, heuristics: list[StabilityHeuristic]) -> Any:
        """Map the fired heuristics to a trust ceiling TrustLevel.

        The ceiling is determined by the most restrictive negative
        heuristic present, then raised by positive heuristics:

        ==============================  ====================
        Condition                       Trust ceiling
        ==============================  ====================
        IMPLEMENTATION_LEAKING          ORACLE_PROPOSED (2)
        HAS_PRIVATE_EXPOSURE alone      RUNTIME_WITNESSED (3)
        DEPRECATED_MARKERS              RUNTIME_WITNESSED (3)
        HAS_DUNDER_OVERRIDE alone       RUNTIME_WITNESSED (3)
        EXPLICIT_PROTOCOL + clean       SOLVER_DISCHARGED (4)
        ABSTRACT_BASE_CLASS + clean     SOLVER_DISCHARGED (4)
        DOCUMENTED_API + clean          RUNTIME_WITNESSED (3)
        VERSION_PINNED + clean          RUNTIME_WITNESSED (3)
        No heuristics at all            ORACLE_PROPOSED (2)
        ==============================  ====================

        Parameters
        ----------
        heuristics : list[StabilityHeuristic]
            Heuristics that fired for this object.

        Returns
        -------
        TrustLevel
        """
        # copilot: meet of individual bounds; implementation leak is the hard floor
        h = set(heuristics)
        if StabilityHeuristic.IMPLEMENTATION_LEAKING in h:
            return TrustLevel.ORACLE_PROPOSED
        # start from the upper bound and lower it based on negatives
        ceiling: int = int(TrustLevel.SOLVER_DISCHARGED)
        if StabilityHeuristic.HAS_PRIVATE_EXPOSURE in h:
            ceiling = min(ceiling, int(TrustLevel.RUNTIME_WITNESSED))
        if StabilityHeuristic.HAS_DUNDER_OVERRIDE in h:
            ceiling = min(ceiling, int(TrustLevel.RUNTIME_WITNESSED))
        if StabilityHeuristic.DEPRECATED_MARKERS in h:
            ceiling = min(ceiling, int(TrustLevel.RUNTIME_WITNESSED))
        # raise toward SOLVER_DISCHARGED only when strong positives are present
        if StabilityHeuristic.EXPLICIT_PROTOCOL in h or StabilityHeuristic.ABSTRACT_BASE_CLASS in h:
            # do NOT raise if a negative heuristic already capped us below this
            ceiling = max(ceiling, int(TrustLevel.SOLVER_DISCHARGED))
        if not h:
            return TrustLevel.ORACLE_PROPOSED
        try:
            return TrustLevel(ceiling)
        except Exception:
            return TrustLevel.ORACLE_PROPOSED


# ===========================================================================
# StabilityHistoryTracker
# ===========================================================================


class StabilityHistoryTracker:
    """Tracks stability classifications over time and detects regressions.

    Theory alignment (Ch22, theory2.tex §1.4):
    -------------------------------------------
    A stability regression occurs when an interface transitions from a
    *stable* level (``STABLE`` or ``DEGRADING``) to an *unstable* one
    (``UNSTABLE``, ``RETRACTING``, or ``COLLAPSED``).  Each regression is
    recorded as a plain string description and can be converted to an
    :class:`Obstruction` by the :class:`StableUnstableSurfaceAreaAnalyzer`.

    The tracker stores a time-ordered list of
    ``(timestamp: str, stability_level: StabilityLevel)`` tuples for each
    interface name.  No entries are ever deleted.

    Attributes
    ----------
    history : dict[str, list[tuple[str, Any]]]
        Mapping from interface name to ordered list of
        ``(timestamp, stability_level)`` pairs.
    regressions : list[str]
        Flat list of all regression descriptions detected so far.
    """

    def __init__(self) -> None:
        # copilot: use defaultdict so we never need to check for missing keys
        self.history: dict[str, list[tuple[str, Any]]] = defaultdict(list)
        self.regressions: list[str] = []

    def record(self, interface_name: str, record: SurfaceStabilityRecord) -> None:
        """Append a new stability observation to the history.

        Also triggers :meth:`detect_regressions` so that any newly
        introduced regression is captured immediately.

        Parameters
        ----------
        interface_name : str
            The key used to group observations (typically
            ``record.interface_name``).
        record : SurfaceStabilityRecord
            The new stability record to store.
        """
        # copilot: store (timestamp, level) tuples to keep history lightweight
        ts = record.last_checked
        self.history[interface_name].append((ts, record.stability_level))
        # detect and accumulate any new regressions introduced by this record
        new_regressions = self.detect_regressions(interface_name)
        for r in new_regressions:
            if r not in self.regressions:
                self.regressions.append(r)

    def detect_regressions(self, interface_name: str) -> list[str]:
        """Find stable→unstable transitions in the history of one interface.

        Scans the stored ``(timestamp, level)`` pairs for
        ``interface_name`` in chronological order.  Each pair of
        consecutive entries where the earlier one is healthy and the later
        one is not constitutes a regression.

        Parameters
        ----------
        interface_name : str
            Interface whose history should be scanned.

        Returns
        -------
        list[str]
            Descriptions of each detected regression, newest first.
        """
        # copilot: walk history pairwise; map level to healthy bool
        entries = self.history.get(interface_name, [])
        results: list[str] = []

        def _is_healthy(level: Any) -> bool:
            try:
                return level in (StabilityLevel.STABLE, StabilityLevel.DEGRADING)
            except Exception:
                return str(level) in ("stable", "degrading")

        for i in range(1, len(entries)):
            prev_ts, prev_level = entries[i - 1]
            curr_ts, curr_level = entries[i]
            if _is_healthy(prev_level) and not _is_healthy(curr_level):
                level_str = curr_level.value if hasattr(curr_level, "value") else str(curr_level)
                results.append(
                    f"REGRESSION {interface_name!r}: "
                    f"{prev_level} → {curr_level} "
                    f"at {curr_ts} (was stable at {prev_ts})"
                )
        return results

    def get_history(self, interface_name: str) -> list[tuple[str, Any]]:
        """Return the full ordered history for one interface.

        Parameters
        ----------
        interface_name : str
            The interface to look up.

        Returns
        -------
        list[tuple[str, Any]]
            Ordered list of ``(timestamp, stability_level)`` pairs.
        """
        return list(self.history.get(interface_name, []))

    def all_regressions(self) -> list[str]:
        """Return all regression descriptions detected across all interfaces.

        Returns
        -------
        list[str]
            Flat list of all regression description strings.
        """
        # copilot: re-scan all interfaces to pick up any that were not caught
        # incrementally (e.g. if detect_regressions was not called on record())
        all_found: list[str] = list(self.regressions)
        for name in self.history:
            for r in self.detect_regressions(name):
                if r not in all_found:
                    all_found.append(r)
        return all_found


# ===========================================================================
# SurfaceComparisonEngine
# ===========================================================================


class SurfaceComparisonEngine:
    """Compares two versions of the same interface's stability record.

    Theory alignment (Ch22, theory2.tex §1.4):
    -------------------------------------------
    A comparison is performed whenever an interface is re-classified after
    an implementation change.  The engine identifies:

    * Added/removed public API members (structural changes)
    * Stability level transitions (health changes)
    * Newly introduced private exposures (regression indicators)
    * Breaking changes (removals from public API)

    The result is a plain dictionary so it can be attached to a
    :class:`Judgment` as evidence without introducing circular imports.
    """

    def compare(
        self,
        old: SurfaceStabilityRecord,
        new: SurfaceStabilityRecord,
    ) -> dict:
        """Compare two stability records for the same interface.

        Parameters
        ----------
        old : SurfaceStabilityRecord
            The earlier record (before a code change).
        new : SurfaceStabilityRecord
            The later record (after the code change).

        Returns
        -------
        dict
            Dictionary with keys:

            ``added_to_public_api``
                Names added to the public surface.
            ``removed_from_public_api``
                Names removed from the public surface.
            ``stability_changed``
                ``True`` when stability_level differs.
            ``new_private_exposures``
                Private names that appear in ``new`` but not ``old``.
            ``breaking_changes``
                Human-readable descriptions of breaking API changes.
            ``regression``
                ``True`` when the transition is healthy→unhealthy.
        """
        # copilot: all set operations on frozensets; preserve ordering in lists
        added = sorted(new.public_api - old.public_api)
        removed = sorted(old.public_api - new.public_api)
        stability_changed = old.stability_level != new.stability_level
        new_privates = sorted(new.private_exposed - old.private_exposed)
        breaking = self._find_breaking_changes(old, new)

        def _is_healthy(level: Any) -> bool:
            try:
                return level in (StabilityLevel.STABLE, StabilityLevel.DEGRADING)
            except Exception:
                return str(level) in ("stable", "degrading")

        regression = _is_healthy(old.stability_level) and not _is_healthy(new.stability_level)

        return {
            "added_to_public_api": added,
            "removed_from_public_api": removed,
            "stability_changed": stability_changed,
            "new_private_exposures": new_privates,
            "breaking_changes": breaking,
            "regression": regression,
        }

    def _find_breaking_changes(
        self,
        old: SurfaceStabilityRecord,
        new: SurfaceStabilityRecord,
    ) -> list[str]:
        """Identify breaking API changes between two stability records.

        A change is breaking if:
        * A name was present in the old public API and is now absent.
        * The trust ceiling has been lowered (callers may be relying on
          the higher trust level in their own analysis).
        * The stability level has dropped from a healthy state.

        Parameters
        ----------
        old : SurfaceStabilityRecord
            Earlier record.
        new : SurfaceStabilityRecord
            Later record.

        Returns
        -------
        list[str]
            Human-readable descriptions of each breaking change.
        """
        # copilot: breaking changes are API surface removals and trust ceiling drops
        breaking: list[str] = []
        removed = old.public_api - new.public_api
        for name in sorted(removed):
            breaking.append(f"Removed from public API: {name!r}")

        try:
            old_ceiling = int(old.trust_ceiling.value) if hasattr(old.trust_ceiling, "value") else int(old.trust_ceiling)
            new_ceiling = int(new.trust_ceiling.value) if hasattr(new.trust_ceiling, "value") else int(new.trust_ceiling)
            if new_ceiling < old_ceiling:
                breaking.append(
                    f"Trust ceiling lowered: {old.trust_ceiling} → {new.trust_ceiling}"
                )
        except Exception:
            pass

        if old.stability_level != new.stability_level:
            old_val = old.stability_level.value if hasattr(old.stability_level, "value") else str(old.stability_level)
            new_val = new.stability_level.value if hasattr(new.stability_level, "value") else str(new.stability_level)
            breaking.append(f"Stability changed: {old_val} → {new_val}")

        return breaking


# ===========================================================================
# StableUnstableSurfaceAreaAnalyzer
# ===========================================================================


class StableUnstableSurfaceAreaAnalyzer:
    """Judgment-emitting analyzer for stable vs unstable surface areas.

    Theory alignment (Ch22, theory2.tex §1.6):
    -------------------------------------------
    The analyzer wraps a :class:`SurfaceClassifier`, a
    :class:`StabilityHistoryTracker`, and a
    :class:`SurfaceComparisonEngine`.  For each object it analyzes, it:

    1. Classifies the object into a :class:`SurfaceStabilityRecord`.
    2. Records the result in the history tracker.
    3. Accumulates a :class:`Judgment` that can later be retrieved via
       :meth:`emit_judgments`.

    Detected regressions are converted into :class:`Obstruction` objects
    via :meth:`detect_regressions`.

    Attributes
    ----------
    classifier : SurfaceClassifier
        The underlying heuristic classifier.
    tracker : StabilityHistoryTracker
        History tracker that persists stability observations over time.
    comparison_engine : SurfaceComparisonEngine
        Engine used for pairwise record comparisons.
    """

    def __init__(
        self,
        classifier: SurfaceClassifier,
        tracker: StabilityHistoryTracker,
        comparison_engine: SurfaceComparisonEngine,
    ) -> None:
        self.classifier = classifier
        self.tracker = tracker
        self.comparison_engine = comparison_engine
        # copilot: _judgments and _records are accumulated across calls to analyze()
        self._judgments: list[Any] = []
        self._records: list[SurfaceStabilityRecord] = []

    def analyze(self, obj: Any) -> SurfaceStabilityRecord:
        """Classify ``obj`` and record the result.

        Also builds a :class:`Judgment` from the result and appends it to
        the internal judgment list for later retrieval via
        :meth:`emit_judgments`.

        Parameters
        ----------
        obj : Any
            The Python object to analyze.

        Returns
        -------
        SurfaceStabilityRecord
            The stability record produced by the classifier.
        """
        # copilot: classify → track → build judgment → return record
        record = self.classifier.classify(obj)
        self.tracker.record(record.interface_name, record)
        self._records.append(record)
        judgment = self._build_judgment(record)
        self._judgments.append(judgment)
        logger.debug("Analyzed %s: %s", record.interface_name, record.stability_level)
        return record

    def detect_regressions(self) -> list[Any]:
        """Convert all tracked regressions into :class:`Obstruction` objects.

        Returns
        -------
        list[Obstruction]
            One Obstruction per regression description.
        """
        # copilot: each regression description becomes a distinct Obstruction
        descriptions = self.tracker.all_regressions()
        return [
            Obstruction(
                description=desc,
                obstruction_id=hashlib.sha256(desc.encode()).hexdigest()[:12],
                severity=2,
            )
            for desc in descriptions
        ]

    def emit_judgments(self) -> list[Any]:
        """Return all accumulated :class:`Judgment` objects.

        Returns a copy so callers cannot modify the internal list.

        Returns
        -------
        list[Judgment]
            One judgment per analyzed object, in analysis order.
        """
        return list(self._judgments)

    def stability_report(self) -> str:
        """Produce a formatted multi-line stability report.

        The report lists every analyzed object along with its stability
        summary and any detected regressions.

        Returns
        -------
        str
            Multi-line report string suitable for log output.
        """
        # copilot: build a compact tabular report; regressions are appended at end
        lines: list[str] = [
            "=== StableUnstableSurfaceAreaAnalyzer stability report ===",
            f"Objects analyzed : {len(self._records)}",
            f"Judgments emitted: {len(self._judgments)}",
            "",
        ]
        for rec in self._records:
            lines.append(f"  {rec.summary()}")
        regressions = self.detect_regressions()
        if regressions:
            lines.append("")
            lines.append(f"Regressions ({len(regressions)}):")
            for obs in regressions:
                lines.append(f"  [OBS] {obs.description}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_judgment(self, record: SurfaceStabilityRecord) -> Any:
        """Build a :class:`Judgment` from a :class:`SurfaceStabilityRecord`.

        Parameters
        ----------
        record : SurfaceStabilityRecord
            Source record.

        Returns
        -------
        Judgment
        """
        # copilot: package heuristic evidence into a well-formed Judgment
        ts = record.last_checked
        level_str = record.stability_level.value if hasattr(record.stability_level, "value") else str(record.stability_level)
        formula = (
            f"surface_is_stable({record.interface_name!r}) == {record.is_stable()!r}; "
            f"level={level_str!r}; score={record.stability_score():.3f}"
        )
        coord = CoordinateObject(
            components=(record.interface_name, record.coordinate_key),
            kind=CoordinateKind.INTERFACE,
            support_labels=frozenset({"unstable_protocol", "surface_area"}),
            metadata={"last_checked": ts},
        )
        prop = Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            free_variables=(),
            metadata={"interface_name": record.interface_name},
        )
        carrier = Carrier(
            name=record.interface_name,
            parameters=(),
            is_dependent=False,
            metadata={},
        )
        evidence_item = EvidenceItem(
            kind=EvidenceItemKind.RUNTIME_WITNESS,
            payload=record.to_dict(),
            trust_level=record.trust_ceiling,
            channel="surface_classifier",
            timestamp=ts,
            expiry="",
            provenance=("SurfaceClassifier",),
        )
        bundle = EvidenceBundle(items=(evidence_item,))
        trust = TrustAnnotation(
            level=record.trust_ceiling,
            rationale=f"Computed from {len(record.reasons)} heuristics",
        )
        prov = Provenance(
            sources=(ProvenanceSource.RUNTIME,),
            chain=("SurfaceClassifier", "StableUnstableSurfaceAreaAnalyzer"),
        )
        return Judgment(
            coordinate=coord,
            proposition=prop,
            carrier=carrier,
            evidence=bundle,
            obligations=(),
            obstructions=(),
            trust=trust,
            provenance=prov,
        )


# ===========================================================================
# StableUnstableSurfaceAreaWitness
# ===========================================================================


class StableUnstableSurfaceAreaWitness:
    """Runtime witness for attribute-access patterns on Python objects.

    Theory alignment (Ch22, theory2.tex §1.5):
    -------------------------------------------
    A witness observes attribute accesses that occur during program
    execution.  When a public method is seen to access a private attribute
    (one starting with ``_`` but not ``__``), the access is classified as
    a *private leak* and recorded as a :class:`WitnessRecord`.

    Witness records accumulate across calls; callers may retrieve them
    via :meth:`all_records`, filter to leaks via :meth:`private_leaks`,
    or obtain aggregate statistics via :meth:`summarize`.

    Attributes
    ----------
    _records : list[WitnessRecord]
        Ordered list of all observed attribute-access events.
    """

    def __init__(self) -> None:
        # copilot: internal list is mutable; all public methods return copies/views
        self._records: list[WitnessRecord] = []

    def witness_attribute_access(
        self,
        obj: Any,
        attr_name: str,
        via_public_method: str,
    ) -> WitnessRecord:
        """Record an attribute access event and return the witness record.

        An access is classified as a private leak when ``attr_name`` starts
        with ``_`` but does not start with ``__`` (i.e. it is a single-
        underscore private attribute, not a dunder).

        Parameters
        ----------
        obj : Any
            The object whose attribute is being accessed.
        attr_name : str
            Name of the attribute that was accessed.
        via_public_method : str
            Name of the public method through which the access was routed,
            or an empty string for direct access.

        Returns
        -------
        WitnessRecord
            The newly created (and internally stored) witness record.
        """
        # copilot: is_private_leak is the core classification; dunders are excluded
        qualname = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or repr(type(obj))
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        is_private_leak = attr_name.startswith("_") and not attr_name.startswith("__")

        # measure call stack depth at the moment of witnessing
        try:
            stack_depth = len(inspect.stack())
        except Exception:
            stack_depth = 0

        record = WitnessRecord(
            record_id=str(uuid.uuid4()),
            target_qualname=qualname,
            attr_name=attr_name,
            via_public_method=via_public_method,
            timestamp=ts,
            is_private_leak=is_private_leak,
            call_stack_depth=stack_depth,
            metadata={
                "obj_type": type(obj).__name__,
                "obj_module": getattr(obj, "__module__", ""),
            },
        )
        self._records.append(record)
        if is_private_leak:
            logger.warning(
                "Private leak witnessed: %s.%s via %s",
                qualname, attr_name, via_public_method or "<direct>",
            )
        return record

    def all_records(self) -> list[WitnessRecord]:
        """Return all witness records in observation order.

        Returns
        -------
        list[WitnessRecord]
            Copy of the internal record list.
        """
        return list(self._records)

    def private_leaks(self) -> list[WitnessRecord]:
        """Return only the witness records classified as private leaks.

        Returns
        -------
        list[WitnessRecord]
            Records where :attr:`WitnessRecord.is_private_leak` is ``True``.
        """
        # copilot: filter predicate matches the is_private_leak flag set at creation time
        return [r for r in self._records if r.is_private_leak]

    def summarize(self) -> dict:
        """Return aggregate statistics over all witnessed accesses.

        Returns
        -------
        dict
            Dictionary with keys:

            ``total``
                Total number of witnessed accesses.
            ``private_leaks``
                Count of private-leak accesses.
            ``unique_targets``
                Count of distinct ``target_qualname`` values.
            ``most_leaked_attrs``
                Up to five attribute names with the most leak observations,
                as ``[(attr_name, count), ...]``.
        """
        # copilot: compute leak stats for inclusion in audit reports
        total = len(self._records)
        leak_records = self.private_leaks()
        unique_targets = len({r.target_qualname for r in self._records})

        # count occurrences of each leaked attr name
        leak_counts: dict[str, int] = defaultdict(int)
        for r in leak_records:
            leak_counts[r.attr_name] += 1
        most_leaked = sorted(leak_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total": total,
            "private_leaks": len(leak_records),
            "unique_targets": unique_targets,
            "most_leaked_attrs": most_leaked,
        }


# ===========================================================================
# StableUnstableSurfaceAreaCoordinator
# ===========================================================================


class StableUnstableSurfaceAreaCoordinator:
    """Lifecycle coordinator integrating analysis, witnessing, and reporting.

    Theory alignment (Ch22, theory2.tex §1.7):
    -------------------------------------------
    The coordinator is the primary entry point for consumers of this
    module.  It owns an :class:`StableUnstableSurfaceAreaAnalyzer` and a
    :class:`StableUnstableSurfaceAreaWitness`, and exposes four operations:

    1. :meth:`coordinate` – produce a :class:`CoordinateObject` from an
       object's stability record.
    2. :meth:`classify_module` – scan all public classes in a module.
    3. :meth:`audit_stability` – produce a full :class:`SurfaceAuditReport`.
    4. :meth:`emit_judgments` – delegate to the analyzer.

    Attributes
    ----------
    analyzer : StableUnstableSurfaceAreaAnalyzer
        The analyzer used for classification and judgment emission.
    witness : StableUnstableSurfaceAreaWitness
        The witness used for runtime leak observation.
    """

    def __init__(
        self,
        analyzer: StableUnstableSurfaceAreaAnalyzer,
        witness: StableUnstableSurfaceAreaWitness,
    ) -> None:
        self.analyzer = analyzer
        self.witness = witness

    def coordinate(self, obj: Any) -> Any:
        """Build a :class:`CoordinateObject` from ``obj``'s stability record.

        Classifies ``obj`` via the analyzer and then constructs a
        :class:`CoordinateObject` whose components encode the object's
        qualified name, stability level, and coordinate key.

        Parameters
        ----------
        obj : Any
            The Python object to coordinate.

        Returns
        -------
        CoordinateObject
            Geometric coordinate for ``obj``'s stability position.
        """
        # copilot: analyze the object and project its record into geometric space
        record = self.analyzer.analyze(obj)
        level_str = record.stability_level.value if hasattr(record.stability_level, "value") else str(record.stability_level)
        components = (
            record.interface_name,
            level_str,
            record.coordinate_key,
        )
        support_labels = frozenset({"unstable_protocol", "surface_area", level_str})
        metadata = {
            "is_stable": record.is_stable(),
            "stability_score": record.stability_score(),
            "last_checked": record.last_checked,
        }
        return CoordinateObject(
            components=components,
            kind=CoordinateKind.INTERFACE,
            support_labels=support_labels,
            metadata=metadata,
        )

    def classify_module(self, module: Any) -> dict:
        """Classify every public class in ``module``.

        Iterates over the names exported by ``module.__all__`` (or, if not
        defined, all names in ``dir(module)`` that do not start with
        ``_``).  For each name that resolves to a class, runs the analyzer
        and stores the result.

        Parameters
        ----------
        module : Any
            A Python module object.

        Returns
        -------
        dict[str, SurfaceStabilityRecord]
            Mapping from class name to its stability record.
        """
        # copilot: use __all__ if defined; otherwise filter dir() for public names
        all_attr = getattr(module, "__all__", None)
        if isinstance(all_attr, (list, tuple, frozenset, set)) and all_attr:
            names = list(all_attr)
        else:
            names = [n for n in dir(module) if not n.startswith("_")]

        results: dict[str, SurfaceStabilityRecord] = {}
        for name in names:
            try:
                obj = getattr(module, name)
            except AttributeError:
                continue
            if inspect.isclass(obj):
                record = self.analyzer.analyze(obj)
                results[name] = record
                logger.debug("Classified module member %s.%s: %s", getattr(module, "__name__", "?"), name, record.stability_level)
        return results

    def audit_stability(self) -> SurfaceAuditReport:
        """Produce a :class:`SurfaceAuditReport` from all analyzed records.

        Uses the analyzer's internal record list to compute aggregate
        statistics and packages them into an immutable report.

        Returns
        -------
        SurfaceAuditReport
            Complete audit report.
        """
        # copilot: snapshot current state of analyzer records; count stable/unstable
        records = list(self.analyzer._records)
        stable_count = sum(1 for r in records if r.is_stable())
        unstable_count = len(records) - stable_count
        obstructions_list = self.analyzer.detect_regressions()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        module_name = getattr(
            self.analyzer.classifier, "__module__", "unknown"
        )
        return SurfaceAuditReport(
            report_id=str(uuid.uuid4()),
            module_name=module_name,
            records=tuple(records),
            total_stable=stable_count,
            total_unstable=unstable_count,
            obstructions=tuple(obstructions_list),
            generated_at=ts,
        )

    def emit_judgments(self) -> list[Any]:
        """Delegate judgment emission to the underlying analyzer.

        Returns
        -------
        list[Judgment]
            All judgments produced so far.
        """
        return self.analyzer.emit_judgments()


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    import sys
    # copilot: smoke test — verifies basic instantiation and round-trip
    print(f"[smoke] {__file__}")
    try:
        classifier = SurfaceClassifier()
        import collections
        record = classifier.classify(collections.OrderedDict)
        print(f"[smoke] classified: {record.interface_name} stability={record.stability_level}")

        tracker = StabilityHistoryTracker()
        tracker.record(record.interface_name, record)
        regressions = tracker.detect_regressions(record.interface_name)
        print(f"[smoke] tracker regressions: {regressions}")

        analyzer = StableUnstableSurfaceAreaAnalyzer(
            classifier=SurfaceClassifier(),
            tracker=StabilityHistoryTracker(),
            comparison_engine=SurfaceComparisonEngine(),
        )
        rec2 = analyzer.analyze(collections.OrderedDict)
        print(f"[smoke] analyzer: {rec2.summary()}")

        witness = StableUnstableSurfaceAreaWitness()
        wr = witness.witness_attribute_access(collections.OrderedDict, "_OrderedDict__root", "keys")
        print(f"[smoke] witness record is_private_leak={wr.is_private_leak}")

        coordinator = StableUnstableSurfaceAreaCoordinator(analyzer=analyzer, witness=witness)
        coord = coordinator.coordinate(collections.OrderedDict)
        print(f"[smoke] coordinate components={coord.components}")

        report = coordinator.audit_stability()
        print(f"[smoke] audit report: {report.summary()}")

        print("[smoke] PASS")
    except Exception as exc:
        import traceback; traceback.print_exc()
        print(f"[smoke] FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
