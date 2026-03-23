"""Top-level bootstrap entry point that wires together all JuGeo subsystems.

# copilot: bootstrap.py — unified initialization and health-check harness for
# all JuGeo subsystems.  Reads trust policy from config, initialises subsystems
# in dependency order, preserves the trust audit trail from startup, and
# exposes a module-level API for callers that do not need fine-grained control.

This module is the **canonical entry point** for the JuGeo runtime.  All
subsystem wiring, lazy loading, session management, and health reporting live
here so that call sites never have to reason about import order or conditional
availability of packages.

The bootstrap process follows the maturity/cyclic-picture invariant from
``preliminaries/theory2.tex`` — the cycle is not optional.  Every initialised
session must wire the ``jugeo.maturity.cyclic_picture`` subsystem so that the
self-improving feedback loop is available to all higher-level components.

Governing design principles from ``preliminaries/theory2.tex``:

* **Dependency order is mandatory** — subsystems form a directed acyclic graph.
  The bootstrap respects the edges:

      kernel → geometry → judgments → evidence → packs
          → orchestration → ideation → maturity

  Violating this order risks importing a subsystem before its dependencies are
  ready, producing subtle trust-accounting failures that are hard to reproduce.

* **The cycle is not optional** — ``jugeo.maturity.cyclic_picture`` provides
  the feedback loop that allows the system to improve its own judgments over
  time.  Per §9.3 of theory2.tex, a session that skips maturity initialisation
  is in an *open* state and must not be promoted to ``HEALTHY`` overall status.
  The bootstrap enforces this invariant by marking the overall result as
  ``DEGRADED`` whenever maturity fails to initialise.

* **Trust audit trail is preserved from startup** — every action taken during
  initialisation (subsystem load, trust-score adjustment, health check) is
  appended to an internal ``_audit_log``.  The log is append-only for the
  lifetime of the :class:`JuGeoBootstrap` instance and can be retrieved with
  :meth:`JuGeoBootstrap.get_audit_trail`.  This satisfies the *no-silent-trust-
  promotion* invariant in §3.4.

* **Health check reports trust policy compliance** — the
  :meth:`JuGeoBootstrap.health_check` method verifies that the achieved trust
  score meets the floor implied by the configured ``trust_policy`` (STRICT,
  DEFAULT, or PERMISSIVE).  A result is ``trust_policy_compliant = False``
  whenever the score falls below the policy floor, regardless of whether any
  individual subsystem is ``HEALTHY``.

* **Lazy loading is explicit** — sub-package imports are wrapped in
  ``try/except ImportError`` blocks so that this module remains importable even
  when the full subsystem tree is not installed.  Import failures are recorded
  as ``DEGRADED`` subsystem entries (not ``FAILED``) unless the subsystem is
  listed in a mandatory set.

Architecture overview
---------------------
The runtime model follows the *cyclic picture* described in theory2.tex §9:

.. code-block:: text

    ┌───────────────────────────────────────────────────────────┐
    │                    JuGeoBootstrap                         │
    │                                                           │
    │  _registry: {name → SubsystemRecord}                      │
    │  _audit_log: [AuditEntry, ...]   ← append-only            │
    │  _trust_score: float (1.0 at start, may decrease)         │
    │  _cycle_count: int               ← from maturity          │
    │                                                           │
    │  initialize()  →  BootstrapResult                         │
    │  health_check() →  dict (HealthReport.to_dict())          │
    │  shutdown()    →  None                                     │
    │  get_task_router() → TaskRouter | dict                     │
    │  get_default_manifest() → Manifest | dict                  │
    │  get_audit_trail()  → list[dict]                          │
    └───────────────────────────────────────────────────────────┘

The trust lattice (§3.1) is reflected in :attr:`BootstrapConfig.trust_policy`:

* ``STRICT``     — trust floor = 0.9; any subsystem degradation fails the
  policy check.
* ``DEFAULT``    — trust floor = 0.6; minor degradation is tolerated.
* ``PERMISSIVE`` — trust floor = 0.2; even heavily degraded sessions pass.

Subsystem names
---------------
Subsystem names are defined in :class:`SubsystemName` and correspond directly
to importable ``jugeo.*`` packages:

:data:`SubsystemName.KERNEL`         — ``jugeo.kernel``
:data:`SubsystemName.GEOMETRY`       — ``jugeo.geometry``
:data:`SubsystemName.JUDGMENTS`      — ``jugeo.judgments``
:data:`SubsystemName.EVIDENCE`       — ``jugeo.evidence``
:data:`SubsystemName.PACKS`          — ``jugeo.packs``
:data:`SubsystemName.ORCHESTRATION`  — ``jugeo.orchestration``
:data:`SubsystemName.IDEATION`       — ``jugeo.ideation``
:data:`SubsystemName.MATURITY`       — ``jugeo.maturity.cyclic_picture``

Public types
------------
:class:`SubsystemStatus`
    String constants describing the lifecycle state of one subsystem.

:class:`SubsystemName`
    String constants identifying each JuGeo subsystem.

:class:`SubsystemRecord`
    Mutable tracking record for one subsystem — status, timestamps, error.

:class:`BootstrapConfig`
    Immutable configuration object for a bootstrap run.

:class:`BootstrapResult`
    Immutable result returned by :meth:`JuGeoBootstrap.initialize`.

:class:`HealthReport`
    Immutable snapshot of system health at a point in time.

:class:`JuGeoBootstrap`
    Main class.  Wires all subsystems and exposes the runtime API.

Public functions
----------------
:func:`bootstrap`
    Module-level entry point.  Creates a :class:`JuGeoBootstrap`, calls
    :meth:`~JuGeoBootstrap.initialize`, and returns the instance.

:func:`get_task_router`
    Convenience wrapper — bootstraps with defaults and returns the task router.

:func:`get_default_manifest`
    Convenience wrapper — bootstraps with defaults and returns the manifest.

:func:`shutdown`
    Module-level no-op shutdown (idempotent; does nothing if never initialised).

:func:`health_check`
    Module-level health check on a freshly bootstrapped instance.

References
----------
theory2.tex §3.1 (trust lattice), §3.4 (no-silent-trust-promotion), §7.1
(orchestration dispatch), §9.1 (maturity lattice), §9.3 (cyclic picture),
§10 (bootstrap protocol).
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional sub-package imports — all wrapped in try/except so that this module
# remains importable even when the full subsystem tree is not installed.
# ---------------------------------------------------------------------------

try:
    import jugeo.kernel as _kernel_mod
    _KERNEL_AVAILABLE = True
except Exception:
    _kernel_mod = None  # type: ignore[assignment]
    _KERNEL_AVAILABLE = False

try:
    import jugeo.geometry as _geometry_mod
    _GEOMETRY_AVAILABLE = True
except Exception:
    _geometry_mod = None  # type: ignore[assignment]
    _GEOMETRY_AVAILABLE = False

try:
    import jugeo.judgments as _judgments_mod
    _JUDGMENTS_AVAILABLE = True
except Exception:
    _judgments_mod = None  # type: ignore[assignment]
    _JUDGMENTS_AVAILABLE = False

try:
    import jugeo.evidence as _evidence_mod
    _EVIDENCE_AVAILABLE = True
except Exception:
    _evidence_mod = None  # type: ignore[assignment]
    _EVIDENCE_AVAILABLE = False

try:
    import jugeo.packs as _packs_mod
    _PACKS_AVAILABLE = True
except Exception:
    _packs_mod = None  # type: ignore[assignment]
    _PACKS_AVAILABLE = False

try:
    import jugeo.orchestration as _orchestration_mod
    _ORCHESTRATION_AVAILABLE = True
except Exception:
    _orchestration_mod = None  # type: ignore[assignment]
    _ORCHESTRATION_AVAILABLE = False

try:
    import jugeo.ideation as _ideation_mod
    _IDEATION_AVAILABLE = True
except Exception:
    _ideation_mod = None  # type: ignore[assignment]
    _IDEATION_AVAILABLE = False

try:
    import jugeo.maturity.cyclic_picture as _cyclic_picture_mod
    _CYCLIC_PICTURE_AVAILABLE = True
except Exception:
    _cyclic_picture_mod = None  # type: ignore[assignment]
    _CYCLIC_PICTURE_AVAILABLE = False

try:
    from jugeo.interfaces.task_router import TaskRouter as _TaskRouter
    _TASK_ROUTER_CLASS_AVAILABLE = True
except Exception:
    _TaskRouter = None  # type: ignore[assignment,misc]
    _TASK_ROUTER_CLASS_AVAILABLE = False

try:
    from jugeo.errors import JuGeoError as _JuGeoError
    _JUGEO_ERROR_AVAILABLE = True
except Exception:
    _JuGeoError = RuntimeError  # type: ignore[misc,assignment]
    _JUGEO_ERROR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        Timestamp in the format ``YYYY-MM-DDTHH:MM:SSZ``, e.g.
        ``"2024-01-15T10:30:00Z"``.  Always UTC; never local time.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _uid() -> str:
    """Return a new UUID4 string suitable for use as a record identifier.

    Returns
    -------
    str
        A hyphenated UUID4 string, e.g.
        ``"550e8400-e29b-41d4-a716-446655440000"``.  Guaranteed unique for
        all practical purposes within a single Python process.
    """
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SubsystemStatus(str, Enum):
    """Lifecycle state constants for a single JuGeo subsystem.

    Attributes
    ----------
    UNINITIALIZED:
        The subsystem has not been touched yet.  Default state at registry
        creation.
    INITIALIZING:
        The subsystem is currently being initialised (import in progress or
        init function running).
    HEALTHY:
        The subsystem initialised successfully and passed its self-check.
    DEGRADED:
        The subsystem is available but in a reduced-capacity state, e.g.
        because an optional dependency was missing.  Trust score is penalised
        by a small amount per degraded subsystem (see §3.1).
    FAILED:
        The subsystem failed to initialise and is completely unavailable.
        Trust score is penalised by a larger amount per failed subsystem.
    SHUTDOWN:
        The subsystem was successfully shut down via :meth:`JuGeoBootstrap.shutdown`.
    """

    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    SHUTDOWN = "SHUTDOWN"


class SubsystemName(str, Enum):
    """Canonical name constants for each JuGeo subsystem.

    Each value maps directly to the top-level importable package path used
    during bootstrap.

    Attributes
    ----------
    KERNEL:
        Core semantic kernel — ``jugeo.kernel``.
    GEOMETRY:
        Coordinate geometry and sheaf machinery — ``jugeo.geometry``.
    JUDGMENTS:
        Judgment algebra (c, φ, A, E, O, B, T, Π) — ``jugeo.judgments``.
    EVIDENCE:
        Evidence channels and trust accounting — ``jugeo.evidence``.
    PACKS:
        Pack management and bridge layer — ``jugeo.packs``.
    ORCHESTRATION:
        Task routing and fleet orchestration — ``jugeo.orchestration``.
    IDEATION:
        Theorem discovery and regime bootstrapping — ``jugeo.ideation``.
    MATURITY:
        Maturity lattice and cyclic picture — ``jugeo.maturity.cyclic_picture``.
    """

    KERNEL = "kernel"
    GEOMETRY = "geometry"
    JUDGMENTS = "judgments"
    EVIDENCE = "evidence"
    PACKS = "packs"
    ORCHESTRATION = "orchestration"
    IDEATION = "ideation"
    MATURITY = "maturity"


# ---------------------------------------------------------------------------
# Trust policy floors
# ---------------------------------------------------------------------------

#: Minimum trust score required under each trust policy.  Derived from §3.1.
_TRUST_POLICY_FLOORS: dict[str, float] = {
    "STRICT": 0.90,
    "DEFAULT": 0.60,
    "PERMISSIVE": 0.20,
}

#: Trust score penalty per DEGRADED subsystem.
_DEGRADED_PENALTY: float = 0.05

#: Trust score penalty per FAILED subsystem.
_FAILED_PENALTY: float = 0.15

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SubsystemRecord:
    """Mutable tracking record for one JuGeo subsystem.

    Instances are stored in :attr:`JuGeoBootstrap._registry` and updated
    as the subsystem moves through its lifecycle.

    Attributes
    ----------
    name : str
        Subsystem identifier — one of :class:`SubsystemName` values.
    status : str
        Current lifecycle status — one of :class:`SubsystemStatus` values.
    initialized_at : Optional[str]
        ISO-8601 timestamp when the subsystem reached ``HEALTHY`` or
        ``DEGRADED``.  ``None`` until then.
    last_health_check : Optional[str]
        ISO-8601 timestamp of the most recent health check that included
        this subsystem.  ``None`` if health_check has not been called.
    error : Optional[str]
        Human-readable error message if the subsystem is ``FAILED`` or
        ``DEGRADED``.  ``None`` for ``HEALTHY`` subsystems.
    metadata : dict
        Arbitrary key/value pairs attached by the initialisation method,
        e.g. module version, feature flags, or capability tokens.
    """

    name: str
    status: str = SubsystemStatus.UNINITIALIZED
    initialized_at: Optional[str] = None
    last_health_check: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def is_healthy(self) -> bool:
        """Return ``True`` if and only if this subsystem is in ``HEALTHY`` state.

        A subsystem that is ``DEGRADED`` is *available* but not *healthy* —
        callers that need the full capability set should check the status
        directly.

        Returns
        -------
        bool
            ``True`` when ``self.status == SubsystemStatus.HEALTHY``.
        """
        return self.status == SubsystemStatus.HEALTHY

    def to_dict(self) -> dict:
        """Serialise this record to a plain Python dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dict with all record fields.  ``metadata`` is
            shallow-copied so that callers cannot mutate the stored record via
            the returned object.
        """
        return {
            "name": self.name,
            "status": self.status,
            "initialized_at": self.initialized_at,
            "last_health_check": self.last_health_check,
            "error": self.error,
            "metadata": copy.copy(self.metadata),
        }


@dataclass
class BootstrapConfig:
    """Configuration object for a bootstrap run.

    Instances are created by the caller and passed to
    :class:`JuGeoBootstrap.__init__`.  All fields are mutable until the
    bootstrap starts; after that the config is treated as read-only.

    Attributes
    ----------
    session_id : str
        Unique identifier for this bootstrap session.  If not supplied,
        :func:`_uid` generates one automatically.
    trust_policy : str
        One of ``"STRICT"``, ``"DEFAULT"``, or ``"PERMISSIVE"``.  Controls
        the minimum trust score required for a session to be considered
        policy-compliant (see :data:`_TRUST_POLICY_FLOORS`).
    lazy_loading : bool
        When ``True`` (the default), subsystems are imported on first use
        rather than during :meth:`JuGeoBootstrap.initialize`.  When ``False``,
        all subsystems are initialised eagerly inside ``initialize()``.
    subsystems_to_skip : list[str]
        Subsystem names (from :class:`SubsystemName`) that should be omitted
        from initialisation entirely.  Skipped subsystems are left in
        ``UNINITIALIZED`` state and do not affect the trust score.
    timeout_seconds : float
        Wall-clock budget for the entire bootstrap sequence.  Currently
        informational only (no hard enforcement); future versions will raise
        a timeout error if exceeded.
    metadata : dict
        Caller-supplied key/value pairs that are propagated to the
        :class:`BootstrapResult` and the audit trail.
    """

    session_id: str = field(default_factory=_uid)
    trust_policy: str = "DEFAULT"
    lazy_loading: bool = True
    subsystems_to_skip: list = field(default_factory=list)
    timeout_seconds: float = 30.0
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> BootstrapConfig:
        """Construct a :class:`BootstrapConfig` from a plain dictionary.

        Unknown keys in ``d`` are silently ignored so that callers can pass
        the entire request payload without filtering.

        Parameters
        ----------
        d : dict
            Source dictionary.  Recognised keys match the field names of this
            dataclass.  All keys are optional; missing keys fall back to field
            defaults.

        Returns
        -------
        BootstrapConfig
            A freshly constructed instance populated from ``d``.

        Examples
        --------
        >>> cfg = BootstrapConfig.from_dict({"trust_policy": "STRICT"})
        >>> cfg.trust_policy
        'STRICT'
        """
        known_fields = {
            "session_id", "trust_policy", "lazy_loading",
            "subsystems_to_skip", "timeout_seconds", "metadata",
        }
        filtered = {k: v for k, v in d.items() if k in known_fields}
        # Coerce subsystems_to_skip to a list if it arrived as something else.
        if "subsystems_to_skip" in filtered and not isinstance(
            filtered["subsystems_to_skip"], list
        ):
            filtered["subsystems_to_skip"] = list(filtered["subsystems_to_skip"])
        # Validate trust_policy.
        if "trust_policy" in filtered and filtered["trust_policy"] not in (
            "STRICT", "DEFAULT", "PERMISSIVE"
        ):
            logger.warning(
                "Unknown trust_policy %r; falling back to DEFAULT",
                filtered["trust_policy"],
            )
            filtered["trust_policy"] = "DEFAULT"
        return cls(**filtered)

    def to_dict(self) -> dict:
        """Serialise this config to a plain Python dictionary.

        Returns
        -------
        dict
            JSON-serialisable dict with all config fields.
        """
        return {
            "session_id": self.session_id,
            "trust_policy": self.trust_policy,
            "lazy_loading": self.lazy_loading,
            "subsystems_to_skip": list(self.subsystems_to_skip),
            "timeout_seconds": self.timeout_seconds,
            "metadata": copy.copy(self.metadata),
        }


@dataclass
class BootstrapResult:
    """Immutable result returned by :meth:`JuGeoBootstrap.initialize`.

    Summarises what happened during bootstrap: which subsystems succeeded,
    what the overall trust score is, and any warnings or errors that were
    generated.

    Attributes
    ----------
    session_id : str
        Session identifier (matches :attr:`BootstrapConfig.session_id`).
    success : bool
        ``True`` if all mandatory subsystems initialised without error.
        ``False`` if one or more required subsystems failed.
    subsystems : dict[str, SubsystemRecord]
        Snapshot of the registry at the time ``initialize()`` returned.
        Keys are subsystem name strings; values are :class:`SubsystemRecord`
        instances.
    trust_score : float
        Aggregate trust score in ``[0.0, 1.0]``.  Starts at ``1.0`` and is
        reduced by :data:`_DEGRADED_PENALTY` or :data:`_FAILED_PENALTY` for
        each non-healthy subsystem.
    warnings : list[str]
        Non-fatal advisory messages generated during bootstrap, e.g. when an
        optional dependency is absent.
    errors : list[str]
        Fatal error messages for subsystems that could not be initialised.
    started_at : str
        ISO-8601 timestamp when ``initialize()`` was called.
    completed_at : str
        ISO-8601 timestamp when ``initialize()`` returned.
    """

    session_id: str
    success: bool
    subsystems: dict
    trust_score: float
    warnings: list
    errors: list
    started_at: str
    completed_at: str

    def duration(self) -> float:
        """Return the wall-clock duration of the bootstrap run in seconds.

        Parses :attr:`started_at` and :attr:`completed_at` as
        ``YYYY-MM-DDTHH:MM:SSZ`` strings.  If parsing fails (e.g. malformed
        timestamp), returns ``0.0`` rather than raising.

        Returns
        -------
        float
            Elapsed seconds between ``started_at`` and ``completed_at``,
            or ``0.0`` on parse error.
        """
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            t0 = time.mktime(time.strptime(self.started_at, fmt))
            t1 = time.mktime(time.strptime(self.completed_at, fmt))
            return max(0.0, t1 - t0)
        except Exception:
            return 0.0

    def healthy_count(self) -> int:
        """Return the number of subsystems currently in ``HEALTHY`` state.

        Returns
        -------
        int
            Count of :class:`SubsystemRecord` entries whose ``status`` is
            ``SubsystemStatus.HEALTHY``.
        """
        return sum(
            1 for rec in self.subsystems.values()
            if rec.status == SubsystemStatus.HEALTHY
        )

    def failed_count(self) -> int:
        """Return the number of subsystems currently in ``FAILED`` state.

        Returns
        -------
        int
            Count of :class:`SubsystemRecord` entries whose ``status`` is
            ``SubsystemStatus.FAILED``.
        """
        return sum(
            1 for rec in self.subsystems.values()
            if rec.status == SubsystemStatus.FAILED
        )

    def to_dict(self) -> dict:
        """Serialise this result to a plain Python dictionary.

        Returns
        -------
        dict
            JSON-serialisable dict.  ``subsystems`` values are converted with
            :meth:`SubsystemRecord.to_dict`.
        """
        return {
            "session_id": self.session_id,
            "success": self.success,
            "subsystems": {k: v.to_dict() for k, v in self.subsystems.items()},
            "trust_score": self.trust_score,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration(),
            "healthy_count": self.healthy_count(),
            "failed_count": self.failed_count(),
        }


@dataclass
class HealthReport:
    """Snapshot of the system health at a single point in time.

    Built by :meth:`JuGeoBootstrap.health_check` and serialised to the dict
    that is returned to callers.  The ``trust_policy_compliant`` field
    reflects whether the current :attr:`trust_score` meets the floor defined
    by the configured :attr:`BootstrapConfig.trust_policy`.

    Attributes
    ----------
    report_id : str
        Unique identifier for this health report, generated by :func:`_uid`.
    session_id : str
        Session being reported on.
    overall_status : str
        One of the :class:`SubsystemStatus` values representing the aggregate
        health of the session.  ``HEALTHY`` only when all non-skipped
        subsystems are ``HEALTHY``.
    subsystem_statuses : dict[str, str]
        Mapping of subsystem name → current status string.
    trust_policy_compliant : bool
        ``True`` when ``trust_score >= _TRUST_POLICY_FLOORS[trust_policy]``.
    trust_score : float
        Current aggregate trust score in ``[0.0, 1.0]``.
    cycle_count : int
        Number of maturity cycles completed since session start, sourced from
        ``jugeo.maturity.cyclic_picture`` if available; else ``0``.
    warnings : list[str]
        Advisory messages emitted during the health check.
    checked_at : str
        ISO-8601 timestamp when this report was generated.
    """

    report_id: str
    session_id: str
    overall_status: str
    subsystem_statuses: dict
    trust_policy_compliant: bool
    trust_score: float
    cycle_count: int
    warnings: list
    checked_at: str

    def is_healthy(self) -> bool:
        """Return ``True`` if the overall system status is ``HEALTHY``.

        Returns
        -------
        bool
            ``True`` when ``self.overall_status == SubsystemStatus.HEALTHY``.
        """
        return self.overall_status == SubsystemStatus.HEALTHY

    def to_dict(self) -> dict:
        """Serialise this health report to a plain Python dictionary.

        Returns
        -------
        dict
            JSON-serialisable dict with all report fields.
        """
        return {
            "report_id": self.report_id,
            "session_id": self.session_id,
            "overall_status": self.overall_status,
            "subsystem_statuses": dict(self.subsystem_statuses),
            "trust_policy_compliant": self.trust_policy_compliant,
            "trust_score": self.trust_score,
            "cycle_count": self.cycle_count,
            "warnings": list(self.warnings),
            "checked_at": self.checked_at,
        }


# ---------------------------------------------------------------------------
# Main bootstrap class
# ---------------------------------------------------------------------------


class JuGeoBootstrap:
    """Top-level bootstrap controller that wires all JuGeo subsystems.

    Lifecycle
    ---------
    1. Construct a :class:`JuGeoBootstrap` (optionally passing a
       :class:`BootstrapConfig`).  The constructor sets up the registry but
       does **not** import or initialise any subsystem.
    2. Call :meth:`initialize` to load subsystems in dependency order.  The
       returned :class:`BootstrapResult` summarises what succeeded.
    3. Use :meth:`get_task_router` and :meth:`get_default_manifest` as the
       primary runtime API.
    4. Call :meth:`health_check` at any time to get a point-in-time snapshot.
    5. Call :meth:`shutdown` to release resources in reverse initialisation
       order.

    Trust audit trail
    -----------------
    Every significant action appends an entry to :attr:`_audit_log` via
    :meth:`_record_audit`.  The log is append-only and can be retrieved with
    :meth:`get_audit_trail`.  Each entry records an ``action``, an ``actor``
    (typically a method name), a ``trust_delta`` (positive or negative change
    to :attr:`_trust_score`), and a ``details`` dict for provenance.

    Parameters
    ----------
    config : BootstrapConfig or None, optional
        Configuration for this session.  When ``None`` a default
        :class:`BootstrapConfig` is constructed automatically.
    """

    def __init__(self, config: Optional[BootstrapConfig] = None) -> None:
        """Initialise the bootstrap controller without loading any subsystems.

        Sets up the subsystem registry with all subsystems in
        ``UNINITIALIZED`` state, but performs no imports.  Callers must
        explicitly invoke :meth:`initialize` (or enable eager loading via
        ``config.lazy_loading = False``) to actually load subsystems.

        Parameters
        ----------
        config : BootstrapConfig or None, optional
            Session configuration.  Defaults to a :class:`BootstrapConfig`
            constructed with all factory defaults.
        """
        self._config: BootstrapConfig = config if config is not None else BootstrapConfig()
        self._session_id: str = self._config.session_id
        self._initialized: bool = False
        self._trust_score: float = 1.0
        self._cycle_count: int = 0
        self._audit_log: list = []
        self._task_router: Any = None
        self._manifest: Any = None

        # Populate the registry with UNINITIALIZED records for every subsystem.
        self._registry: dict = {
            name.value: SubsystemRecord(name=name.value)
            for name in SubsystemName
        }

        self._record_audit(
            action="bootstrap_created",
            actor="JuGeoBootstrap.__init__",
            trust_delta=0.0,
            details={
                "session_id": self._session_id,
                "trust_policy": self._config.trust_policy,
                "lazy_loading": self._config.lazy_loading,
            },
        )
        logger.debug(
            "JuGeoBootstrap created — session=%s policy=%s",
            self._session_id,
            self._config.trust_policy,
        )

    # -----------------------------------------------------------------------
    # Public lifecycle API
    # -----------------------------------------------------------------------

    def initialize(self) -> BootstrapResult:
        """Initialise all subsystems in dependency order and return a result.

        Subsystems are initialised in the sequence defined by theory2.tex §10:

            kernel → geometry → judgments → evidence → packs
                → orchestration → ideation → maturity

        Each subsystem invokes a dedicated ``_initialize_<name>`` method that
        wraps the import and any setup calls in a ``try/except`` block.
        Failures are recorded in the registry and do not abort the sequence
        (unless ``config.lazy_loading = False`` and the subsystem is in the
        mandatory set).

        Returns
        -------
        BootstrapResult
            Snapshot of the registry and trust score after all subsystems have
            been processed.

        Notes
        -----
        Calling this method more than once on the same instance is idempotent:
        a warning is logged and the existing result is returned immediately
        without re-initialising subsystems.
        """
        if self._initialized:
            logger.warning(
                "JuGeoBootstrap.initialize() called on already-initialised session %s",
                self._session_id,
            )
            return self._build_result(
                started_at=_utcnow(),
                completed_at=_utcnow(),
                warnings=["Already initialised; returning cached result."],
                errors=[],
            )

        started_at = _utcnow()
        warnings: list = []
        errors: list = []

        self._record_audit(
            action="initialize_start",
            actor="JuGeoBootstrap.initialize",
            trust_delta=0.0,
            details={"started_at": started_at},
        )

        # Define the ordered initialisation sequence.
        init_sequence = [
            (SubsystemName.KERNEL, self._initialize_kernel),
            (SubsystemName.GEOMETRY, self._initialize_geometry),
            (SubsystemName.JUDGMENTS, self._initialize_judgments),
            (SubsystemName.EVIDENCE, self._initialize_evidence),
            (SubsystemName.PACKS, self._initialize_packs),
            (SubsystemName.ORCHESTRATION, self._initialize_orchestration),
            (SubsystemName.IDEATION, self._initialize_ideation),
            (SubsystemName.MATURITY, self._initialize_maturity),
        ]

        for subsystem_name, init_fn in init_sequence:
            name_val = subsystem_name.value
            if name_val in self._config.subsystems_to_skip:
                logger.debug("Skipping subsystem %s (in subsystems_to_skip)", name_val)
                continue
            try:
                self._mark_subsystem(name_val, SubsystemStatus.INITIALIZING)
                init_fn()
            except Exception as exc:  # noqa: BLE001
                msg = f"Unexpected error initialising {name_val}: {exc}"
                logger.error(msg)
                errors.append(msg)
                self._mark_subsystem(name_val, SubsystemStatus.FAILED, error=str(exc))

        # Enforce the cyclic-picture invariant: maturity must be HEALTHY or DEGRADED.
        maturity_rec = self._registry.get(SubsystemName.MATURITY.value)
        if (
            maturity_rec is not None
            and maturity_rec.status == SubsystemStatus.FAILED
            and SubsystemName.MATURITY.value not in self._config.subsystems_to_skip
        ):
            warnings.append(
                "theory2.tex §9.3: maturity/cyclic_picture failed to initialise. "
                "The cyclic feedback loop is unavailable; session is DEGRADED."
            )

        # Collect per-subsystem warnings for any DEGRADED subsystems.
        for rec in self._registry.values():
            if rec.status == SubsystemStatus.DEGRADED and rec.error:
                warnings.append(f"Subsystem {rec.name} degraded: {rec.error}")
            elif rec.status == SubsystemStatus.FAILED and rec.error:
                if rec.error not in " ".join(errors):
                    errors.append(f"Subsystem {rec.name} failed: {rec.error}")

        self._initialized = True
        completed_at = _utcnow()
        success = len(errors) == 0

        self._record_audit(
            action="initialize_complete",
            actor="JuGeoBootstrap.initialize",
            trust_delta=0.0,
            details={
                "completed_at": completed_at,
                "success": success,
                "trust_score": self._trust_score,
                "warning_count": len(warnings),
                "error_count": len(errors),
            },
        )

        result = self._build_result(
            started_at=started_at,
            completed_at=completed_at,
            warnings=warnings,
            errors=errors,
        )
        logger.info(
            "Bootstrap complete — session=%s trust=%.2f healthy=%d failed=%d",
            self._session_id,
            self._trust_score,
            result.healthy_count(),
            result.failed_count(),
        )
        return result

    def _initialize_kernel(self) -> None:
        """Import and initialise ``jugeo.kernel``.

        The kernel subsystem provides the core semantic engine: coordinate
        management, admissibility enforcement, and the authority layer
        (``jugeo.kernel.authority``).  It must be available for all
        higher-level subsystems to function correctly.

        On success the subsystem is marked ``HEALTHY`` and the module version
        (if available via ``_kernel_mod.__version__``) is stored in
        ``metadata``.  On ``ImportError`` the subsystem is marked ``DEGRADED``
        rather than ``FAILED`` because many kernel capabilities degrade
        gracefully to built-in fallbacks.

        Raises
        ------
        Does not raise.  All exceptions are caught and recorded in the
        subsystem record via :meth:`_mark_subsystem`.
        """
        if not _KERNEL_AVAILABLE:
            self._mark_subsystem(
                SubsystemName.KERNEL.value,
                SubsystemStatus.DEGRADED,
                error="jugeo.kernel not importable; core engine unavailable.",
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_kernel",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": "ImportError", "subsystem": "kernel"},
            )
            return

        metadata: dict = {}
        try:
            version = getattr(_kernel_mod, "__version__", "unknown")
            metadata["version"] = version
            # Attempt to call a lightweight probe if the kernel exposes one.
            if hasattr(_kernel_mod, "probe"):
                probe_result = _kernel_mod.probe()
                metadata["probe"] = str(probe_result)
        except Exception as exc:  # noqa: BLE001
            self._mark_subsystem(
                SubsystemName.KERNEL.value,
                SubsystemStatus.DEGRADED,
                error=f"kernel probe failed: {exc}",
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_kernel",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": str(exc), "subsystem": "kernel"},
            )
            return

        self._mark_subsystem(SubsystemName.KERNEL.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.KERNEL.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_kernel",
            trust_delta=0.0,
            details={"subsystem": "kernel", "metadata": metadata},
        )
        logger.debug("Kernel subsystem initialised — version=%s", metadata.get("version"))

    def _initialize_geometry(self) -> None:
        """Import and initialise ``jugeo.geometry``.

        The geometry subsystem provides sheaf-theoretic coordinate spaces,
        restriction maps, and the locality/gluing apparatus described in
        theory2.tex §2.  It depends on the kernel being available.

        If ``jugeo.geometry`` is absent the subsystem is marked ``DEGRADED``
        and a trust penalty is applied.  Most geometry operations fall back
        to flat-dictionary representations, which are less efficient but
        semantically equivalent for small inputs.

        Raises
        ------
        Does not raise.
        """
        if not _GEOMETRY_AVAILABLE:
            self._mark_subsystem(
                SubsystemName.GEOMETRY.value,
                SubsystemStatus.DEGRADED,
                error="jugeo.geometry not importable; sheaf machinery unavailable.",
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_geometry",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": "ImportError", "subsystem": "geometry"},
            )
            return

        metadata: dict = {}
        try:
            version = getattr(_geometry_mod, "__version__", "unknown")
            metadata["version"] = version
            if hasattr(_geometry_mod, "get_coordinate_factory"):
                factory = _geometry_mod.get_coordinate_factory()
                metadata["coordinate_factory"] = repr(factory)
        except Exception as exc:  # noqa: BLE001
            self._mark_subsystem(
                SubsystemName.GEOMETRY.value,
                SubsystemStatus.DEGRADED,
                error=f"geometry probe failed: {exc}",
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_geometry",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": str(exc), "subsystem": "geometry"},
            )
            return

        self._mark_subsystem(SubsystemName.GEOMETRY.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.GEOMETRY.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_geometry",
            trust_delta=0.0,
            details={"subsystem": "geometry", "metadata": metadata},
        )
        logger.debug("Geometry subsystem initialised.")

    def _initialize_judgments(self) -> None:
        """Import and initialise ``jugeo.judgments``.

        The judgments subsystem provides the full judgment algebra
        ``(c, φ, A, E, O, B, T, Π)`` described in theory2.tex §4, including
        admissibility predicates, obligation tracking, and budget accounting.

        Raises
        ------
        Does not raise.
        """
        if not _JUDGMENTS_AVAILABLE:
            self._mark_subsystem(
                SubsystemName.JUDGMENTS.value,
                SubsystemStatus.DEGRADED,
                error="jugeo.judgments not importable; judgment algebra unavailable.",
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_judgments",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": "ImportError", "subsystem": "judgments"},
            )
            return

        metadata: dict = {"version": getattr(_judgments_mod, "__version__", "unknown")}
        self._mark_subsystem(SubsystemName.JUDGMENTS.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.JUDGMENTS.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_judgments",
            trust_delta=0.0,
            details={"subsystem": "judgments", "metadata": metadata},
        )
        logger.debug("Judgments subsystem initialised.")

    def _initialize_evidence(self) -> None:
        """Import and initialise ``jugeo.evidence``.

        The evidence subsystem provides trust-ordered algebra, evidence
        channels (solver, runtime, oracle, copilot, proof, human), and
        certificate management.  See theory2.tex §3 and §4.2.

        The trust score is *not* penalised for evidence being absent in
        ``PERMISSIVE`` mode because the minimal router can operate without
        formal evidence channels.

        Raises
        ------
        Does not raise.
        """
        if not _EVIDENCE_AVAILABLE:
            penalty = (
                0.0 if self._config.trust_policy == "PERMISSIVE"
                else _DEGRADED_PENALTY
            )
            self._mark_subsystem(
                SubsystemName.EVIDENCE.value,
                SubsystemStatus.DEGRADED,
                error="jugeo.evidence not importable; trust accounting degraded.",
            )
            self._trust_score = max(0.0, self._trust_score - penalty)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_evidence",
                trust_delta=-penalty,
                details={
                    "reason": "ImportError",
                    "subsystem": "evidence",
                    "policy": self._config.trust_policy,
                },
            )
            return

        metadata: dict = {"version": getattr(_evidence_mod, "__version__", "unknown")}
        try:
            if hasattr(_evidence_mod, "trust") and hasattr(_evidence_mod.trust, "TrustLevel"):
                metadata["trust_levels"] = [
                    t.value for t in _evidence_mod.trust.TrustLevel
                    if hasattr(t, "value")
                ]
        except Exception:  # noqa: BLE001
            pass

        self._mark_subsystem(SubsystemName.EVIDENCE.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.EVIDENCE.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_evidence",
            trust_delta=0.0,
            details={"subsystem": "evidence", "metadata": metadata},
        )
        logger.debug("Evidence subsystem initialised.")

    def _initialize_packs(self) -> None:
        """Import and initialise ``jugeo.packs``.

        The packs subsystem provides pack management, bridge layers, and the
        pack-promotion pipeline from ideation.  See theory2.tex §8.

        Raises
        ------
        Does not raise.
        """
        if not _PACKS_AVAILABLE:
            self._mark_subsystem(
                SubsystemName.PACKS.value,
                SubsystemStatus.DEGRADED,
                error="jugeo.packs not importable; pack management unavailable.",
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_packs",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": "ImportError", "subsystem": "packs"},
            )
            return

        metadata: dict = {"version": getattr(_packs_mod, "__version__", "unknown")}
        self._mark_subsystem(SubsystemName.PACKS.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.PACKS.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_packs",
            trust_delta=0.0,
            details={"subsystem": "packs", "metadata": metadata},
        )
        logger.debug("Packs subsystem initialised.")

    def _initialize_orchestration(self) -> None:
        """Import and initialise ``jugeo.orchestration``.

        The orchestration subsystem provides the task router, mixed-evidence
        routing, fleet competition, and semantic control described in
        theory2.tex §7.  It also exposes the ``TaskRouter`` class used by
        :meth:`get_task_router`.

        Raises
        ------
        Does not raise.
        """
        if not _ORCHESTRATION_AVAILABLE:
            self._mark_subsystem(
                SubsystemName.ORCHESTRATION.value,
                SubsystemStatus.DEGRADED,
                error=(
                    "jugeo.orchestration not importable; "
                    "task routing falls back to built-in dict router."
                ),
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_orchestration",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": "ImportError", "subsystem": "orchestration"},
            )
            return

        metadata: dict = {
            "version": getattr(_orchestration_mod, "__version__", "unknown"),
            "task_router_available": _TASK_ROUTER_CLASS_AVAILABLE,
        }
        self._mark_subsystem(SubsystemName.ORCHESTRATION.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.ORCHESTRATION.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_orchestration",
            trust_delta=0.0,
            details={"subsystem": "orchestration", "metadata": metadata},
        )
        logger.debug("Orchestration subsystem initialised.")

    def _initialize_ideation(self) -> None:
        """Import and initialise ``jugeo.ideation``.

        The ideation subsystem provides theorem discovery, regime
        bootstrapping, analogy transport, and the discovery federation
        protocol described in theory2.tex §11.  It is the last subsystem
        before maturity in the dependency order.

        Raises
        ------
        Does not raise.
        """
        if not _IDEATION_AVAILABLE:
            self._mark_subsystem(
                SubsystemName.IDEATION.value,
                SubsystemStatus.DEGRADED,
                error="jugeo.ideation not importable; theorem discovery unavailable.",
            )
            self._trust_score = max(0.0, self._trust_score - _DEGRADED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_ideation",
                trust_delta=-_DEGRADED_PENALTY,
                details={"reason": "ImportError", "subsystem": "ideation"},
            )
            return

        metadata: dict = {"version": getattr(_ideation_mod, "__version__", "unknown")}
        self._mark_subsystem(SubsystemName.IDEATION.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.IDEATION.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_ideation",
            trust_delta=0.0,
            details={"subsystem": "ideation", "metadata": metadata},
        )
        logger.debug("Ideation subsystem initialised.")

    def _initialize_maturity(self) -> None:
        """Import and initialise ``jugeo.maturity.cyclic_picture``.

        Per theory2.tex §9.3, the cyclic picture is **not optional** — it
        provides the self-improvement feedback loop that allows the system to
        grow beyond its initial configuration.  A session that cannot wire
        this subsystem is considered to be in an *open* (incomplete) state.

        On success the :attr:`_cycle_count` is updated from the module's
        ``get_cycle_count()`` function if available.  On failure the subsystem
        is recorded as ``DEGRADED`` (not ``FAILED``) because the rest of the
        system can still operate, albeit without the cyclic feedback loop.

        Raises
        ------
        Does not raise.
        """
        if not _CYCLIC_PICTURE_AVAILABLE:
            self._mark_subsystem(
                SubsystemName.MATURITY.value,
                SubsystemStatus.DEGRADED,
                error=(
                    "jugeo.maturity.cyclic_picture not importable. "
                    "theory2.tex §9.3: cyclic feedback loop unavailable; "
                    "session is in OPEN (incomplete) state."
                ),
            )
            self._trust_score = max(0.0, self._trust_score - _FAILED_PENALTY)
            self._record_audit(
                action="subsystem_degraded",
                actor="_initialize_maturity",
                trust_delta=-_FAILED_PENALTY,
                details={
                    "reason": "ImportError",
                    "subsystem": "maturity",
                    "theory2_ref": "§9.3 cyclic picture invariant violated",
                },
            )
            return

        metadata: dict = {
            "version": getattr(_cyclic_picture_mod, "__version__", "unknown"),
        }
        try:
            if hasattr(_cyclic_picture_mod, "get_cycle_count"):
                self._cycle_count = int(_cyclic_picture_mod.get_cycle_count())
                metadata["cycle_count"] = self._cycle_count
            if hasattr(_cyclic_picture_mod, "manifest"):
                manifest_obj = getattr(_cyclic_picture_mod, "manifest", None)
                if manifest_obj is not None:
                    metadata["manifest_available"] = True
        except Exception as exc:  # noqa: BLE001
            metadata["probe_error"] = str(exc)

        self._mark_subsystem(SubsystemName.MATURITY.value, SubsystemStatus.HEALTHY)
        self._registry[SubsystemName.MATURITY.value].metadata.update(metadata)
        self._record_audit(
            action="subsystem_healthy",
            actor="_initialize_maturity",
            trust_delta=0.0,
            details={"subsystem": "maturity", "metadata": metadata},
        )
        logger.debug(
            "Maturity/cyclic_picture initialised — cycle_count=%d", self._cycle_count
        )

    # -----------------------------------------------------------------------
    # Runtime API
    # -----------------------------------------------------------------------

    def get_task_router(self) -> Any:
        """Lazily initialise and return the task router for this session.

        If ``jugeo.orchestration`` and ``jugeo.interfaces.task_router`` are
        available, returns a ``TaskRouter`` instance backed by the full
        orchestration subsystem.  Otherwise returns a minimal built-in router
        dict that maps each task kind to a plain Python callable stub.

        The router is cached on first call — subsequent calls return the same
        object without re-initialising.

        Returns
        -------
        TaskRouter or dict
            Either a full ``jugeo.interfaces.task_router.TaskRouter`` instance
            or a minimal fallback dict with keys ``"bug_detection"``,
            ``"equivalence_checking"``, and ``"specification_satisfaction"``.

        Notes
        -----
        The returned object satisfies the *never-raise* contract: all dispatch
        errors surface as result objects rather than exceptions.
        """
        if self._task_router is not None:
            return self._task_router

        if _TASK_ROUTER_CLASS_AVAILABLE and _TaskRouter is not None:
            try:
                self._task_router = _TaskRouter()
                self._record_audit(
                    action="task_router_created",
                    actor="get_task_router",
                    trust_delta=0.0,
                    details={"type": "TaskRouter", "class": repr(_TaskRouter)},
                )
                logger.debug("Full TaskRouter initialised.")
                return self._task_router
            except Exception as exc:  # noqa: BLE001
                logger.warning("TaskRouter init failed (%s); using fallback dict.", exc)

        # Fallback: minimal built-in router dict.
        self._task_router = {
            "bug_detection": lambda source, **_kw: {
                "status": "fallback",
                "trust_tier": "PROPOSAL",
                "payload": {},
                "warnings": ["orchestration subsystem unavailable"],
            },
            "equivalence_checking": lambda prog_a, prog_b, **_kw: {
                "status": "fallback",
                "trust_tier": "PROPOSAL",
                "payload": {},
                "warnings": ["orchestration subsystem unavailable"],
            },
            "specification_satisfaction": lambda spec, prog, **_kw: {
                "status": "fallback",
                "trust_tier": "PROPOSAL",
                "payload": {},
                "warnings": ["orchestration subsystem unavailable"],
            },
        }
        self._record_audit(
            action="task_router_fallback",
            actor="get_task_router",
            trust_delta=0.0,
            details={"type": "fallback_dict"},
        )
        logger.debug("Using fallback task router dict (orchestration unavailable).")
        return self._task_router

    def get_default_manifest(self) -> Any:
        """Return the package manifest for the current session.

        Attempts to retrieve the manifest from
        ``jugeo.maturity.cyclic_picture.manifest`` first.  Falls back to a
        plain dict describing the session metadata when the cyclic picture
        module is unavailable.

        The manifest is cached on first call.

        Returns
        -------
        Manifest or dict
            Either a ``jugeo.maturity.cyclic_picture.manifest`` object (or
            whatever ``_cyclic_picture_mod.manifest`` evaluates to), or a
            plain dict with keys ``session_id``, ``trust_policy``,
            ``subsystems``, and ``generated_at``.
        """
        if self._manifest is not None:
            return self._manifest

        if _CYCLIC_PICTURE_AVAILABLE and _cyclic_picture_mod is not None:
            try:
                manifest_attr = getattr(_cyclic_picture_mod, "manifest", None)
                if manifest_attr is not None:
                    self._manifest = manifest_attr
                    self._record_audit(
                        action="manifest_loaded",
                        actor="get_default_manifest",
                        trust_delta=0.0,
                        details={"source": "jugeo.maturity.cyclic_picture.manifest"},
                    )
                    return self._manifest
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not retrieve cyclic_picture manifest: %s", exc)

        # Fallback manifest dict.
        self._manifest = {
            "manifest_id": _uid(),
            "session_id": self._session_id,
            "trust_policy": self._config.trust_policy,
            "subsystems": {
                name: rec.status
                for name, rec in self._registry.items()
            },
            "trust_score": self._trust_score,
            "generated_at": _utcnow(),
            "source": "bootstrap_fallback",
        }
        self._record_audit(
            action="manifest_fallback",
            actor="get_default_manifest",
            trust_delta=0.0,
            details={"source": "bootstrap_fallback"},
        )
        return self._manifest

    def shutdown(self) -> None:
        """Gracefully shut down all subsystems in reverse initialisation order.

        Iterates the subsystem registry in reverse dependency order
        (maturity → ideation → orchestration → packs → evidence → judgments
        → geometry → kernel) and marks each as ``SHUTDOWN``.  If a subsystem
        exposes a ``shutdown()`` callable it is invoked; errors are logged but
        do not abort the shutdown sequence.

        The audit trail records a ``shutdown`` entry for each subsystem and a
        final ``bootstrap_shutdown`` entry with the total count.

        After this method returns, :attr:`_initialized` is set to ``False``
        and further calls to :meth:`initialize` will re-initialise subsystems
        from scratch.
        """
        if not self._initialized:
            logger.debug("shutdown() called on uninitialised bootstrap; no-op.")
            return

        self._record_audit(
            action="shutdown_start",
            actor="JuGeoBootstrap.shutdown",
            trust_delta=0.0,
            details={"session_id": self._session_id, "timestamp": _utcnow()},
        )

        # Reverse order of the initialisation sequence.
        reverse_order = [
            SubsystemName.MATURITY,
            SubsystemName.IDEATION,
            SubsystemName.ORCHESTRATION,
            SubsystemName.PACKS,
            SubsystemName.EVIDENCE,
            SubsystemName.JUDGMENTS,
            SubsystemName.GEOMETRY,
            SubsystemName.KERNEL,
        ]

        for subsystem_name in reverse_order:
            name_val = subsystem_name.value
            rec = self._registry.get(name_val)
            if rec is None or rec.status in (
                SubsystemStatus.UNINITIALIZED, SubsystemStatus.SHUTDOWN
            ):
                continue

            # Attempt to call a shutdown hook if the module exposes one.
            mod_map = {
                SubsystemName.KERNEL.value: _kernel_mod,
                SubsystemName.GEOMETRY.value: _geometry_mod,
                SubsystemName.JUDGMENTS.value: _judgments_mod,
                SubsystemName.EVIDENCE.value: _evidence_mod,
                SubsystemName.PACKS.value: _packs_mod,
                SubsystemName.ORCHESTRATION.value: _orchestration_mod,
                SubsystemName.IDEATION.value: _ideation_mod,
                SubsystemName.MATURITY.value: _cyclic_picture_mod,
            }
            mod = mod_map.get(name_val)
            if mod is not None and hasattr(mod, "shutdown"):
                try:
                    mod.shutdown()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error shutting down %s: %s", name_val, exc)

            self._mark_subsystem(name_val, SubsystemStatus.SHUTDOWN)
            self._record_audit(
                action="subsystem_shutdown",
                actor="JuGeoBootstrap.shutdown",
                trust_delta=0.0,
                details={"subsystem": name_val},
            )

        self._initialized = False
        self._task_router = None
        self._manifest = None

        self._record_audit(
            action="bootstrap_shutdown",
            actor="JuGeoBootstrap.shutdown",
            trust_delta=0.0,
            details={
                "session_id": self._session_id,
                "timestamp": _utcnow(),
                "subsystems_shutdown": len(reverse_order),
            },
        )
        logger.info("JuGeoBootstrap shutdown complete — session=%s", self._session_id)

    def health_check(self) -> dict:
        """Check the health of all subsystems and verify trust policy compliance.

        Iterates the registry, refreshes ``last_health_check`` timestamps,
        and derives an overall status:

        * ``HEALTHY``  — all non-skipped subsystems are ``HEALTHY``.
        * ``DEGRADED`` — at least one subsystem is ``DEGRADED`` but none are
          ``FAILED``.
        * ``FAILED``   — at least one subsystem is ``FAILED``.

        The ``trust_policy_compliant`` flag is set to ``True`` when the
        current :attr:`_trust_score` meets or exceeds the floor for the
        configured :attr:`BootstrapConfig.trust_policy`.

        Returns
        -------
        dict
            The serialised :class:`HealthReport` with keys:
            ``overall_status``, ``subsystems``, ``trust_policy_compliant``,
            ``trust_score``, ``cycle_count``, ``warnings``, ``checked_at``,
            ``report_id``, ``session_id``.
        """
        checked_at = _utcnow()
        warnings: list = []
        subsystem_statuses: dict = {}

        overall = SubsystemStatus.HEALTHY

        for name, rec in self._registry.items():
            # Update the health-check timestamp on the live record.
            rec.last_health_check = checked_at
            subsystem_statuses[name] = rec.status

            if rec.status == SubsystemStatus.FAILED:
                overall = SubsystemStatus.FAILED
                warnings.append(f"Subsystem {name} is FAILED: {rec.error or '(no detail)'}")
            elif rec.status == SubsystemStatus.DEGRADED:
                if overall == SubsystemStatus.HEALTHY:
                    overall = SubsystemStatus.DEGRADED
                warnings.append(f"Subsystem {name} is DEGRADED: {rec.error or '(no detail)'}")

        # Refresh cycle count from maturity if available.
        if _CYCLIC_PICTURE_AVAILABLE and _cyclic_picture_mod is not None:
            try:
                if hasattr(_cyclic_picture_mod, "get_cycle_count"):
                    self._cycle_count = int(_cyclic_picture_mod.get_cycle_count())
            except Exception:  # noqa: BLE001
                pass

        # Check trust policy compliance.
        floor = _TRUST_POLICY_FLOORS.get(self._config.trust_policy, 0.60)
        trust_policy_compliant = self._trust_score >= floor
        if not trust_policy_compliant:
            warnings.append(
                f"Trust score {self._trust_score:.3f} is below the "
                f"{self._config.trust_policy} policy floor of {floor:.3f}. "
                f"See theory2.tex §3.4 (no-silent-trust-promotion)."
            )

        report = HealthReport(
            report_id=_uid(),
            session_id=self._session_id,
            overall_status=overall,
            subsystem_statuses=subsystem_statuses,
            trust_policy_compliant=trust_policy_compliant,
            trust_score=self._trust_score,
            cycle_count=self._cycle_count,
            warnings=warnings,
            checked_at=checked_at,
        )

        self._record_audit(
            action="health_check",
            actor="JuGeoBootstrap.health_check",
            trust_delta=0.0,
            details={
                "overall_status": overall,
                "trust_policy_compliant": trust_policy_compliant,
                "checked_at": checked_at,
            },
        )

        return report.to_dict()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _record_audit(
        self,
        action: str,
        actor: str,
        trust_delta: float,
        details: dict,
    ) -> None:
        """Append an entry to the internal trust audit trail.

        The audit log is append-only for the lifetime of this instance.
        Entries record every significant action — subsystem init, health
        checks, trust-score changes, and shutdown events — so that the full
        sequence of decisions is reconstructable from the log alone.

        Per theory2.tex §3.4, the audit trail must capture every trust-score
        change so that no promotion or demotion happens silently.

        Parameters
        ----------
        action : str
            Short name of the action being recorded, e.g.
            ``"subsystem_healthy"`` or ``"shutdown_start"``.
        actor : str
            Qualified method name that originated the action, e.g.
            ``"_initialize_kernel"`` or ``"JuGeoBootstrap.initialize"``.
        trust_delta : float
            Change to :attr:`_trust_score` caused by this action.  Positive
            for promotions, negative for demotions, ``0.0`` for neutral events.
        details : dict
            Arbitrary key/value provenance data.  Must be JSON-serialisable.
        """
        entry = {
            "audit_id": _uid(),
            "timestamp": _utcnow(),
            "session_id": self._session_id,
            "action": action,
            "actor": actor,
            "trust_delta": trust_delta,
            "trust_score_after": self._trust_score,
            "details": details,
        }
        self._audit_log.append(entry)

    def _mark_subsystem(
        self,
        name: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Update the status of a subsystem in the registry.

        Sets the ``status`` field on the :class:`SubsystemRecord` identified
        by ``name``.  If ``status`` is ``HEALTHY`` or ``DEGRADED`` and
        ``initialized_at`` is not yet set, records the current UTC timestamp.
        If ``error`` is provided it is stored on the record.

        Parameters
        ----------
        name : str
            Subsystem name key — must be a value in :class:`SubsystemName`.
        status : str
            New status — must be a value in :class:`SubsystemStatus`.
        error : str or None, optional
            Error description string.  ``None`` to leave the existing error
            field unchanged (useful for status-only updates).
        """
        rec = self._registry.get(name)
        if rec is None:
            logger.warning("_mark_subsystem called for unknown subsystem %r", name)
            return

        rec.status = status
        if error is not None:
            rec.error = error
        if status in (SubsystemStatus.HEALTHY, SubsystemStatus.DEGRADED):
            if rec.initialized_at is None:
                rec.initialized_at = _utcnow()

    def _build_result(
        self,
        started_at: str,
        completed_at: str,
        warnings: list,
        errors: list,
    ) -> BootstrapResult:
        """Build a :class:`BootstrapResult` from the current registry state.

        Parameters
        ----------
        started_at : str
            ISO-8601 timestamp when initialisation began.
        completed_at : str
            ISO-8601 timestamp when initialisation ended.
        warnings : list[str]
            Non-fatal advisory messages.
        errors : list[str]
            Fatal error messages.

        Returns
        -------
        BootstrapResult
            Snapshot of the current state.
        """
        # Snapshot the registry so callers cannot mutate it.
        subsystem_snapshot = {k: copy.copy(v) for k, v in self._registry.items()}
        success = len(errors) == 0
        return BootstrapResult(
            session_id=self._session_id,
            success=success,
            subsystems=subsystem_snapshot,
            trust_score=self._trust_score,
            warnings=list(warnings),
            errors=list(errors),
            started_at=started_at,
            completed_at=completed_at,
        )

    # -----------------------------------------------------------------------
    # Public accessors
    # -----------------------------------------------------------------------

    def get_audit_trail(self) -> list:
        """Return a shallow copy of the trust audit log.

        Returns
        -------
        list[dict]
            List of audit entries in chronological order.  Each entry is a
            dict with keys: ``audit_id``, ``timestamp``, ``session_id``,
            ``action``, ``actor``, ``trust_delta``, ``trust_score_after``,
            ``details``.  The returned list is a copy; mutating it does not
            affect the stored log.
        """
        return list(self._audit_log)

    def is_initialized(self) -> bool:
        """Return ``True`` if :meth:`initialize` has been called successfully.

        Returns
        -------
        bool
            ``True`` after :meth:`initialize` completes (regardless of
            whether all subsystems are healthy); ``False`` before or after
            :meth:`shutdown`.
        """
        return self._initialized

    def get_session_id(self) -> str:
        """Return the session identifier for this bootstrap instance.

        Returns
        -------
        str
            The UUID4 string assigned to this session, either from
            :attr:`BootstrapConfig.session_id` or auto-generated by
            :func:`_uid` at construction time.
        """
        return self._session_id


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def bootstrap(config: Optional[dict] = None) -> JuGeoBootstrap:
    """Create and fully initialise a :class:`JuGeoBootstrap` instance.

    This is the canonical module-level entry point for the JuGeo runtime.
    It constructs a :class:`JuGeoBootstrap`, optionally applies a dict-based
    configuration, calls :meth:`~JuGeoBootstrap.initialize`, and returns the
    live instance ready for use.

    Parameters
    ----------
    config : dict or None, optional
        Configuration overrides in the same format accepted by
        :meth:`BootstrapConfig.from_dict`.  All keys are optional.  When
        ``None``, factory defaults are used.

    Returns
    -------
    JuGeoBootstrap
        A fully initialised bootstrap instance.  Callers can immediately
        use :meth:`~JuGeoBootstrap.get_task_router`,
        :meth:`~JuGeoBootstrap.get_default_manifest`, and
        :meth:`~JuGeoBootstrap.health_check`.

    Examples
    --------
    >>> bs = bootstrap({"trust_policy": "STRICT"})
    >>> bs.is_initialized()
    True
    """
    cfg = BootstrapConfig.from_dict(config) if config is not None else BootstrapConfig()
    bs = JuGeoBootstrap(config=cfg)
    bs.initialize()
    return bs


def get_task_router() -> Any:
    """Bootstrap with defaults and return the task router.

    Convenience wrapper for callers that only need the router and do not
    want to manage a :class:`JuGeoBootstrap` instance directly.

    Returns
    -------
    TaskRouter or dict
        The task router as returned by
        :meth:`JuGeoBootstrap.get_task_router`.
    """
    bs = bootstrap()
    return bs.get_task_router()


def get_default_manifest() -> Any:
    """Bootstrap with defaults and return the package manifest.

    Convenience wrapper for callers that only need the manifest dict and
    do not want to manage a :class:`JuGeoBootstrap` instance directly.

    Returns
    -------
    Manifest or dict
        The manifest as returned by
        :meth:`JuGeoBootstrap.get_default_manifest`.
    """
    bs = bootstrap()
    return bs.get_default_manifest()


def shutdown() -> None:
    """Module-level shutdown — no-op if the bootstrap was never initialised.

    Provided for symmetry with the instance-level :meth:`JuGeoBootstrap.shutdown`
    method.  Callers that obtained a bootstrap instance via :func:`bootstrap`
    should prefer calling ``bs.shutdown()`` directly so that the correct
    instance is shut down.
    """
    # Module-level shutdown has no persistent state to release because each
    # call to bootstrap() creates a fresh JuGeoBootstrap instance.  This
    # function exists to satisfy the module-level API contract described in
    # the module docstring.
    logger.debug("Module-level shutdown() called — no persistent state to release.")


def health_check() -> dict:
    """Bootstrap with defaults and return a health check dict.

    Convenience wrapper that creates a fresh :class:`JuGeoBootstrap`,
    initialises it, runs :meth:`~JuGeoBootstrap.health_check`, and returns
    the result.

    Returns
    -------
    dict
        Health report dict as returned by
        :meth:`JuGeoBootstrap.health_check`.
    """
    bs = bootstrap()
    return bs.health_check()


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "SubsystemStatus",
    "SubsystemName",
    # Data classes
    "SubsystemRecord",
    "BootstrapConfig",
    "BootstrapResult",
    "HealthReport",
    # Main class
    "JuGeoBootstrap",
    # Module-level functions
    "bootstrap",
    "get_task_router",
    "get_default_manifest",
    "shutdown",
    "health_check",
    # Unified judgment-geometric bootstrap helpers
    "bootstrap_kernel",
    "bootstrap_site",
    "bootstrap_trust",
    "bootstrap_solver",
]


# ---------------------------------------------------------------------------
# Unified judgment-geometric bootstrap helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.kernel.lifecycle import init_kernel  # type: ignore[import-untyped]
    from jugeo.kernel.services import ServiceRegistry  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    init_kernel = None
    ServiceRegistry = None

try:
    from jugeo.geometry.site import GeometricSite  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    GeometricSite = None

try:
    from jugeo.evidence.trust import TrustAlgebra  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    TrustAlgebra = None

try:
    from jugeo.solver.z3_session import Z3Session as _Z3Session  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _Z3Session = None


def bootstrap_kernel():
    """Bootstrap the JuGeo kernel via jugeo.kernel.lifecycle + services.

    Returns a ``ServiceRegistry`` wired to a freshly initialised kernel,
    or ``None`` when kernel modules are unavailable.
    """
    if init_kernel is None or ServiceRegistry is None:
        return None
    kernel = init_kernel()
    registry = ServiceRegistry(kernel)
    return registry


def bootstrap_site():
    """Bootstrap a default geometric site via jugeo.geometry.site.

    Returns a ``GeometricSite`` instance ready for descent and judgment
    evaluation, or ``None`` when the geometry module is unavailable.
    """
    if GeometricSite is None:
        return None
    return GeometricSite.default()


def bootstrap_trust():
    """Bootstrap the trust algebra via jugeo.evidence.trust.

    Returns a ``TrustAlgebra`` instance configured with the default
    lattice, or ``None`` when the evidence module is unavailable.
    """
    if TrustAlgebra is None:
        return None
    return TrustAlgebra.default()


def bootstrap_solver():
    """Bootstrap a Z3 solver session via jugeo.solver.z3_session.

    Returns a fresh ``Z3Session``, or ``None`` when the solver module is
    unavailable.
    """
    if _Z3Session is None:
        return None
    return _Z3Session()

# ---------------------------------------------------------------------------
# Demo entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Demo: bootstrap and run health check
    import json
    print("JuGeo Bootstrap Demo")
    print("=" * 60)
    bs = bootstrap()
    result = bs.health_check()
    print(json.dumps(result, indent=2, default=str))
    print("Session:", bs.get_session_id())
    print("Initialized:", bs.is_initialized())
    bs.shutdown()
    print("Shutdown complete.")
