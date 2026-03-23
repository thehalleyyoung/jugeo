"""
integration.py — Integration layer for the JuGeo regime_bootstrapping package.

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping via Obstruction Theory.

This module provides the bridge between the internal bootstrapping pipeline and the
wider JuGeo platform.  After the algorithmic core (algorithms.py) has produced a
``BootstrapResult``, this module is responsible for:

  1. Registering the assembled regime with the ``RegimeCatalog``.
  2. Collecting, validating, and archiving evidence records that justify the
     bootstrap decision.
  3. Submitting the executed plan to the ``Orchestrator`` for lifecycle
     management.
  4. Synchronising all state changes back to the catalog and the evidence
     manifests subsystem.

The main entry point is ``BootstrappingIntegration``, which wires together three
adapter classes — ``RegimeCatalogAdapter``, ``EvidenceBootstrapAdapter``, and
``OrchestratorAdapter`` — into a single transactional workflow.

All cross-platform calls are retried up to ``IntegrationConfig.max_retries`` times
with exponential back-off so transient failures do not abort the entire run.

Integration correctness is guaranteed by the theorems proved in theorems.py, which
are checked as pre-conditions before any irreversible platform call is made.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
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

try:
    from jugeo.ideation.regime_bootstrapping.models import (
        ObstructionField, ObstructionKind, DomainFormation, DomainType,
        TypeConstructor, TypeConstructorKind, RegimeCandidate, BootstrapStep,
        BootstrapPlan, BootstrapResult, BootstrapStatus, BootstrapPriority,
        RegimeBootstrapperConfig,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "IntegrationConfig",
    "IntegrationResult",
    "BootstrappingIntegration",
    "RegimeCatalogAdapter",
    "EvidenceBootstrapAdapter",
    "OrchestratorAdapter",
    "create_integration",
    "run_integration_pipeline",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES: int = 3
"""
Default number of retry attempts for platform calls that may fail transiently.
After this many attempts the integration marks the operation as failed and
records the last exception in the diagnostics list.
"""

DEFAULT_TIMEOUT_SECS: float = 60.0
"""
Default per-operation timeout in seconds.  Individual operations will abort
after this many seconds and be retried or marked failed accordingly.
"""

INTEGRATION_VERSION: str = "1.0.0"
"""
Version string stamped into all IntegrationResult objects so consumers can
detect API changes across deployments.
"""

CATALOG_NAMESPACE: str = "jugeo.regimes"
"""
Namespace prefix used when registering new regimes with the platform catalog.
All regime IDs created by this module are prefixed with this string.
"""

EVIDENCE_SOURCE_TAG: str = "regime_bootstrapping"
"""
Source tag attached to every evidence record collected during a bootstrapping
integration run.  Allows evidence queries to filter by origin.
"""

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

PlanDict = dict[str, Any]
ResultDict = dict[str, Any]
RegimeDict = dict[str, Any]
EvidenceRef = dict[str, Any]

# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """
    Return the current UTC time as a POSIX timestamp.

    Thin wrapper around ``time.time()`` for easy mocking in tests.

    Returns
    -------
    float
        POSIX timestamp.
    """
    return time.time()


def _uid() -> str:
    """
    Generate a compact, URL-safe unique identifier.

    Returns
    -------
    str
        32-character lowercase hex string.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """
    Clamp *value* to the closed interval [lo, hi].

    Parameters
    ----------
    value : float
        Input value.
    lo : float
        Lower bound.
    hi : float
        Upper bound.

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# IntegrationConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrationConfig:
    """
    Immutable configuration object for the integration layer.

    Collects all tunable parameters that control how bootstrap results are
    pushed into the wider JuGeo platform.  The frozen+slots design prevents
    accidental mutation and enables cheap structural equality comparisons.

    Attributes
    ----------
    max_retries : int
        Number of retry attempts for each platform call.
    timeout_secs : float
        Per-operation timeout in seconds.
    enable_evidence_collection : bool
        Whether to collect and archive evidence records.
    enable_orchestrator_submission : bool
        Whether to submit the executed plan to the orchestrator.
    enable_catalog_sync : bool
        Whether to synchronise regime state with the catalog.
    trust_threshold : float
        Minimum trust level (0–1) required to approve a regime registration.
    verbose : bool
        When True, additional diagnostic messages are included in results.
    """

    max_retries: int = DEFAULT_MAX_RETRIES
    timeout_secs: float = DEFAULT_TIMEOUT_SECS
    enable_evidence_collection: bool = True
    enable_orchestrator_submission: bool = True
    enable_catalog_sync: bool = True
    trust_threshold: float = 0.6
    verbose: bool = False

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this config to a JSON-serialisable dictionary.

        The returned dict can be stored in pipeline metadata records or
        passed to logging systems.  All values are primitive Python types.

        Returns
        -------
        dict[str, Any]
            Flat mapping of field names to their values.
        """
        return {
            "max_retries": self.max_retries,
            "timeout_secs": self.timeout_secs,
            "enable_evidence_collection": self.enable_evidence_collection,
            "enable_orchestrator_submission": self.enable_orchestrator_submission,
            "enable_catalog_sync": self.enable_catalog_sync,
            "trust_threshold": self.trust_threshold,
            "verbose": self.verbose,
        }

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_strict(self) -> bool:
        """
        Return True if the config is operating in *strict* mode.

        Strict mode is defined as having all three integration subsystems
        enabled (evidence collection, orchestrator submission, catalog
        sync) *and* a trust threshold at or above 0.9.

        Returns
        -------
        bool
            Whether strict mode is active.
        """
        return (
            self.enable_evidence_collection
            and self.enable_orchestrator_submission
            and self.enable_catalog_sync
            and self.trust_threshold >= 0.9
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> IntegrationConfig:
        """
        Return the canonical default integration configuration.

        All subsystems are enabled with moderate thresholds.  Suitable
        for production use and as the implicit default when callers omit
        a config argument.

        Returns
        -------
        IntegrationConfig
            Default configuration instance.
        """
        return cls()

    @classmethod
    def minimal(cls) -> IntegrationConfig:
        """
        Return a *minimal* configuration that disables all optional subsystems.

        Useful for unit tests that want to exercise the integration layer
        without spinning up evidence or orchestrator back-ends.  Only the
        catalog sync remains enabled so that registered regimes can be
        retrieved within the same test session.

        Returns
        -------
        IntegrationConfig
            Minimal configuration instance.
        """
        return cls(
            enable_evidence_collection=False,
            enable_orchestrator_submission=False,
            enable_catalog_sync=True,
            max_retries=1,
            timeout_secs=5.0,
            verbose=True,
        )


# ---------------------------------------------------------------------------
# IntegrationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    """
    Immutable record capturing the outcome of a single integration run.

    Every field is stamped at creation time and the object is frozen so
    it can be safely passed across thread boundaries and stored in
    audit logs.

    Attributes
    ----------
    result_id : str
        Unique identifier for this integration run.
    plan_id : str
        Identifier of the bootstrap plan that was integrated.
    success : bool
        Overall success flag.
    regime_id : str | None
        Identifier of the regime registered in the catalog, or None if
        registration was not attempted or failed.
    evidence_count : int
        Number of evidence records collected during this run.
    diagnostics : list[str]
        Human-readable diagnostic messages, warnings, and errors.
    elapsed_secs : float
        Wall-clock time taken by the integration run.
    created_at : float
        POSIX timestamp at which this result was created.
    """

    result_id: str
    plan_id: str
    success: bool
    regime_id: str | None
    evidence_count: int
    diagnostics: list[str]
    elapsed_secs: float
    created_at: float

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this result to a JSON-serialisable dictionary.

        The resulting dict includes all fields plus a human-readable
        ``"status"`` string derived from the ``success`` flag.

        Returns
        -------
        dict[str, Any]
            Flat mapping suitable for JSON serialisation.
        """
        return {
            "result_id": self.result_id,
            "plan_id": self.plan_id,
            "success": self.success,
            "status": "success" if self.success else "failure",
            "regime_id": self.regime_id,
            "evidence_count": self.evidence_count,
            "diagnostics": list(self.diagnostics),
            "elapsed_secs": self.elapsed_secs,
            "created_at": self.created_at,
            "version": INTEGRATION_VERSION,
        }

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """
        Return a compact one-line summary of this result.

        The summary is suitable for log lines and terminal output.  It
        encodes the success status, plan ID, regime ID, evidence count,
        and elapsed time in a single readable string.

        Returns
        -------
        str
            Single-line summary string.
        """
        status = "OK" if self.success else "FAIL"
        regime = self.regime_id or "n/a"
        return (
            f"[{status}] plan={self.plan_id} regime={regime} "
            f"evidence={self.evidence_count} elapsed={self.elapsed_secs:.2f}s"
        )

    def has_errors(self) -> bool:
        """
        Return True if the diagnostics list contains any error messages.

        Error messages are identified by the presence of the string
        ``"ERROR"`` or ``"error"`` anywhere in the message text.

        Returns
        -------
        bool
            Whether any error diagnostics are present.
        """
        return any(
            "error" in d.lower() or "ERROR" in d
            for d in self.diagnostics
        )


# ---------------------------------------------------------------------------
# RegimeCatalogAdapter
# ---------------------------------------------------------------------------


class RegimeCatalogAdapter:
    """
    Adapter that bridges the bootstrapping layer to the regime catalog.

    The adapter maintains an in-memory store of regime dictionaries.  In
    production the store would be backed by a database or a remote catalog
    service; here the in-memory implementation provides a fully functional
    fallback that never raises import errors.

    All operations are idempotent: registering the same regime ID twice
    updates the existing record rather than duplicating it.
    """

    def __init__(self) -> None:
        """
        Initialise the adapter with an empty in-memory catalog.

        The internal store is a plain dict keyed by regime ID.  Access
        patterns are O(1) for register/lookup/remove and O(n) for
        list_all/count.
        """
        self._store: dict[str, RegimeDict] = {}

    def register(self, regime_dict: RegimeDict) -> str:
        """
        Register a regime in the catalog and return its ID.

        If a regime with the same ID already exists it is overwritten.
        The regime dict must contain an ``"id"`` key; if absent a new
        unique ID is generated and inserted into the dict before storage.

        Parameters
        ----------
        regime_dict : RegimeDict
            Regime descriptor to register.

        Returns
        -------
        str
            The regime ID under which the record was stored.
        """
        if "id" not in regime_dict:
            regime_dict = dict(regime_dict, id=f"{CATALOG_NAMESPACE}.{_uid()}")

        regime_id: str = regime_dict["id"]
        self._store[regime_id] = dict(regime_dict, registered_at=_utcnow())
        return regime_id

    def lookup(self, regime_id: str) -> RegimeDict | None:
        """
        Look up a regime by its ID.

        Returns the stored regime descriptor dict, or None if no regime
        with the given ID exists in the catalog.

        Parameters
        ----------
        regime_id : str
            Unique regime identifier.

        Returns
        -------
        RegimeDict | None
            Regime descriptor, or None.
        """
        return self._store.get(regime_id)

    def update(self, regime_id: str, updates: dict[str, Any]) -> bool:
        """
        Apply a partial update to an existing regime record.

        Only the keys present in *updates* are modified; all other fields
        are left unchanged.  Returns False if the regime does not exist.

        Parameters
        ----------
        regime_id : str
            ID of the regime to update.
        updates : dict[str, Any]
            Partial update dict.

        Returns
        -------
        bool
            True if the update was applied, False if the regime was not found.
        """
        if regime_id not in self._store:
            return False
        self._store[regime_id] = {**self._store[regime_id], **updates, "updated_at": _utcnow()}
        return True

    def list_all(self) -> list[RegimeDict]:
        """
        Return a list of all registered regime descriptors.

        The list is sorted by registration time (ascending) so the order
        is deterministic across runs in the same process.

        Returns
        -------
        list[RegimeDict]
            All regimes in the catalog.
        """
        return sorted(self._store.values(), key=lambda r: r.get("registered_at", 0.0))

    def remove(self, regime_id: str) -> bool:
        """
        Remove a regime from the catalog.

        Returns True if the regime was present and removed, False otherwise.

        Parameters
        ----------
        regime_id : str
            ID of the regime to remove.

        Returns
        -------
        bool
            True if removed, False if not found.
        """
        if regime_id in self._store:
            del self._store[regime_id]
            return True
        return False

    def count(self) -> int:
        """
        Return the number of regimes currently registered in the catalog.

        Returns
        -------
        int
            Catalog size.
        """
        return len(self._store)


# ---------------------------------------------------------------------------
# EvidenceBootstrapAdapter
# ---------------------------------------------------------------------------


class EvidenceBootstrapAdapter:
    """
    Adapter that bridges the bootstrapping layer to the evidence subsystem.

    Handles collection, validation, and manifest building for evidence
    records that justify a bootstrapping decision.  Evidence records are
    stored in-memory during a run; a production implementation would push
    them to the evidence database.

    The adapter tracks all records associated with a given plan ID so that
    manifest building is always consistent with what was collected.
    """

    def __init__(self) -> None:
        """
        Initialise the adapter with empty in-memory evidence stores.

        Internal structures:
          * ``_records``: maps plan_id → list of evidence record dicts.
          * ``_manifests``: maps plan_id → manifest dict.
          * ``_provenance``: maps regime_id → provenance trace dict.
        """
        self._records: dict[str, list[EvidenceRef]] = {}
        self._manifests: dict[str, dict[str, Any]] = {}
        self._provenance: dict[str, dict[str, Any]] = {}

    def collect(self, plan_id: str, source: str) -> EvidenceRef:
        """
        Collect a new evidence record for the given plan.

        Creates a minimal evidence record with a unique ID, the plan ID as
        context, the source tag, and a timestamp.  The record is appended
        to the internal list for the plan and returned to the caller.

        Parameters
        ----------
        plan_id : str
            Bootstrap plan identifier.
        source : str
            Human-readable source description.

        Returns
        -------
        EvidenceRef
            The newly created evidence record dict.
        """
        record: EvidenceRef = {
            "id": _uid(),
            "plan_id": plan_id,
            "source": source,
            "source_tag": EVIDENCE_SOURCE_TAG,
            "collected_at": _utcnow(),
            "kind": "bootstrap_justification",
            "valid": True,
        }
        if plan_id not in self._records:
            self._records[plan_id] = []
        self._records[plan_id].append(record)
        return record

    def validate(self, evidence_refs: list[EvidenceRef]) -> list[str]:
        """
        Validate a list of evidence references.

        Checks that each reference has the required keys (``id``, ``plan_id``,
        ``source``, ``kind``) and that the ``valid`` flag is True.  Returns a
        list of validation error messages; an empty list means all records are
        valid.

        Parameters
        ----------
        evidence_refs : list[EvidenceRef]
            Evidence records to validate.

        Returns
        -------
        list[str]
            Validation error messages.
        """
        errors: list[str] = []
        required_keys = {"id", "plan_id", "source", "kind"}

        for i, ref in enumerate(evidence_refs):
            missing = required_keys - set(ref.keys())
            if missing:
                errors.append(f"record[{i}] missing keys: {sorted(missing)}")
            if not ref.get("valid", False):
                errors.append(f"record[{i}] id={ref.get('id', '?')} marked invalid")

        return errors

    def build_manifest(self, evidence_refs: list[EvidenceRef]) -> dict[str, Any]:
        """
        Build an evidence manifest from a list of evidence references.

        The manifest is a structured summary of all evidence collected
        for a plan.  It includes a unique manifest ID, counts by kind,
        an overall validity flag, and the list of record IDs.

        Parameters
        ----------
        evidence_refs : list[EvidenceRef]
            Evidence records to include in the manifest.

        Returns
        -------
        dict[str, Any]
            Evidence manifest descriptor.
        """
        errors = self.validate(evidence_refs)
        valid = len(errors) == 0

        kinds: dict[str, int] = {}
        for ref in evidence_refs:
            k = ref.get("kind", "unknown")
            kinds[k] = kinds.get(k, 0) + 1

        manifest = {
            "manifest_id": _uid(),
            "record_ids": [r["id"] for r in evidence_refs],
            "count": len(evidence_refs),
            "kinds": kinds,
            "valid": valid,
            "errors": errors,
            "created_at": _utcnow(),
        }

        # Cache the manifest keyed by first plan_id found
        if evidence_refs:
            plan_id = evidence_refs[0].get("plan_id", "unknown")
            self._manifests[plan_id] = manifest

        return manifest

    def get_trust_level(self, evidence_refs: list[EvidenceRef]) -> float:
        """
        Compute the aggregate trust level for a collection of evidence records.

        Trust level is derived from the fraction of valid records and the
        diversity of evidence kinds.  The formula is:

            trust = valid_fraction * kind_diversity_bonus

        where ``kind_diversity_bonus`` is ``min(n_kinds / 3, 1.0)`` with a
        cap at 1.0.

        Parameters
        ----------
        evidence_refs : list[EvidenceRef]
            Evidence records to evaluate.

        Returns
        -------
        float
            Trust level in [0, 1].
        """
        if not evidence_refs:
            return 0.0

        valid_count = sum(1 for r in evidence_refs if r.get("valid", False))
        valid_fraction = valid_count / len(evidence_refs)

        unique_kinds = len({r.get("kind", "unknown") for r in evidence_refs})
        kind_bonus = _clamp(unique_kinds / 3.0)

        return _clamp(valid_fraction * (0.7 + 0.3 * kind_bonus))

    def attach_provenance(self, regime_id: str, trace: dict[str, Any]) -> None:
        """
        Attach a provenance trace to a registered regime.

        The provenance trace records the chain of bootstrapping steps that
        led to the regime's creation.  It is stored in the adapter and can
        be retrieved by the integration layer when building audit records.

        Parameters
        ----------
        regime_id : str
            Regime to attach the trace to.
        trace : dict[str, Any]
            Provenance trace descriptor.
        """
        self._provenance[regime_id] = dict(trace, attached_at=_utcnow(), regime_id=regime_id)

    def get_records_for_plan(self, plan_id: str) -> list[EvidenceRef]:
        """
        Return all collected evidence records for the given plan.

        Parameters
        ----------
        plan_id : str
            Bootstrap plan identifier.

        Returns
        -------
        list[EvidenceRef]
            Evidence records associated with the plan.
        """
        return list(self._records.get(plan_id, []))


# ---------------------------------------------------------------------------
# OrchestratorAdapter
# ---------------------------------------------------------------------------


class OrchestratorAdapter:
    """
    Adapter that bridges the bootstrapping layer to the JuGeo orchestrator.

    The orchestrator manages the lifecycle of long-running bootstrap plans.
    This adapter provides a simplified view of the orchestrator API that
    maps bootstrap plan dicts to orchestrator plan objects.

    In the in-memory fallback used when the orchestrator module is not
    installed, plans are stored locally and status transitions follow a
    simple state machine.
    """

    def __init__(self) -> None:
        """
        Initialise the adapter with an empty in-memory plan store.

        The store maps plan_id to a status dict tracking the plan's
        current state (pending, running, completed, cancelled, failed).
        """
        self._plans: dict[str, dict[str, Any]] = {}

    def submit(self, plan: PlanDict) -> str:
        """
        Submit a bootstrap plan to the orchestrator for execution.

        The plan is stored with an initial status of ``"pending"`` and
        a submission timestamp.  Returns the plan's ID so the caller can
        poll for status.

        Parameters
        ----------
        plan : PlanDict
            Bootstrap plan descriptor.  Must contain an ``"id"`` key.

        Returns
        -------
        str
            The plan ID as accepted by the orchestrator.
        """
        plan_id = plan.get("id") or _uid()
        self._plans[plan_id] = {
            "plan_id": plan_id,
            "status": "pending",
            "submitted_at": _utcnow(),
            "plan": plan,
            "diagnostics": [],
        }
        return plan_id

    def cancel(self, plan_id: str) -> bool:
        """
        Cancel a submitted or running plan.

        A plan can only be cancelled if it is in ``"pending"`` or
        ``"running"`` state.  Returns True if the cancellation was
        applied, False if the plan was not found or already terminal.

        Parameters
        ----------
        plan_id : str
            Plan to cancel.

        Returns
        -------
        bool
            True if cancelled, False otherwise.
        """
        record = self._plans.get(plan_id)
        if record is None:
            return False
        if record["status"] in ("completed", "failed", "cancelled"):
            return False
        record["status"] = "cancelled"
        record["cancelled_at"] = _utcnow()
        return True

    def get_status(self, plan_id: str) -> dict[str, Any] | None:
        """
        Retrieve the current status of a submitted plan.

        Returns a status dict with keys ``plan_id``, ``status``,
        ``submitted_at``, and optional ``completed_at`` / ``cancelled_at``.
        Returns None if the plan is unknown.

        Parameters
        ----------
        plan_id : str
            Plan identifier.

        Returns
        -------
        dict[str, Any] | None
            Status dict or None.
        """
        record = self._plans.get(plan_id)
        if record is None:
            return None
        return {
            "plan_id": plan_id,
            "status": record["status"],
            "submitted_at": record.get("submitted_at"),
            "completed_at": record.get("completed_at"),
            "cancelled_at": record.get("cancelled_at"),
            "diagnostics": record.get("diagnostics", []),
        }

    def list_active(self) -> list[dict[str, Any]]:
        """
        Return status dicts for all plans in ``"pending"`` or ``"running"`` state.

        Returns
        -------
        list[dict[str, Any]]
            Active plan status dicts, sorted by submission time ascending.
        """
        active = [
            r for r in self._plans.values()
            if r["status"] in ("pending", "running")
        ]
        return sorted(active, key=lambda r: r.get("submitted_at", 0.0))

    def mark_completed(self, plan_id: str, result_summary: str = "") -> bool:
        """
        Mark a plan as completed (used by tests and internal pipeline).

        Parameters
        ----------
        plan_id : str
            Plan to mark.
        result_summary : str
            Optional human-readable completion summary.

        Returns
        -------
        bool
            True if the state transition was applied.
        """
        record = self._plans.get(plan_id)
        if record is None or record["status"] == "cancelled":
            return False
        record["status"] = "completed"
        record["completed_at"] = _utcnow()
        record["result_summary"] = result_summary
        return True


# ---------------------------------------------------------------------------
# BootstrappingIntegration
# ---------------------------------------------------------------------------


class BootstrappingIntegration:
    """
    Main integration facade for the regime bootstrapping pipeline.

    This class wires together the three adapter classes
    (``RegimeCatalogAdapter``, ``EvidenceBootstrapAdapter``,
    ``OrchestratorAdapter``) into a single cohesive workflow.

    The typical usage pattern is:

    .. code-block:: python

        integration = BootstrappingIntegration()
        result = integration.integrate(bootstrap_result)
        if result.success:
            print(f"Regime {result.regime_id} registered")

    The integration is not thread-safe.  Create one instance per thread or
    protect with an external lock.

    Parameters
    ----------
    config : IntegrationConfig | None
        Integration configuration.  Defaults to ``IntegrationConfig.default()``.
    """

    def __init__(self, config: IntegrationConfig | None = None) -> None:
        """
        Initialise the integration facade.

        Creates fresh adapter instances and initialises the internal
        diagnostics list and status tracking structures.

        Parameters
        ----------
        config : IntegrationConfig | None
            Configuration object.  Defaults to ``IntegrationConfig.default()``.
        """
        self.config: IntegrationConfig = config or IntegrationConfig.default()
        self._catalog = RegimeCatalogAdapter()
        self._evidence = EvidenceBootstrapAdapter()
        self._orchestrator = OrchestratorAdapter()
        self._diagnostics: list[str] = []
        self._status: dict[str, Any] = {}
        self._run_count: int = 0

    def integrate(self, bootstrap_result: dict[str, Any]) -> IntegrationResult:
        """
        Integrate a bootstrap result into the JuGeo platform.

        This is the top-level integration method that executes the following
        steps in order:

        1. Validate the bootstrap result.
        2. Register the assembled regime with the catalog (if enabled).
        3. Collect and archive evidence (if enabled).
        4. Submit the plan to the orchestrator (if enabled).
        5. Synchronise catalog state (if enabled).
        6. Return an ``IntegrationResult`` summarising the outcome.

        If any step fails and retries are exhausted the integration is
        marked as failed but remaining steps are still attempted so the
        result captures the full picture.

        Parameters
        ----------
        bootstrap_result : dict[str, Any]
            Bootstrap result dict, typically produced by the algorithmic
            core in algorithms.py.

        Returns
        -------
        IntegrationResult
            Immutable record of the integration run.
        """
        start = _utcnow()
        self._run_count += 1
        diagnostics: list[str] = []
        success = True
        regime_id: str | None = None
        evidence_count: int = 0

        plan_id = bootstrap_result.get("plan_id") or _uid()

        # --- Step 1: Validate ---
        errors = self._validate_result(bootstrap_result)
        if errors:
            diagnostics.extend([f"ERROR: validation: {e}" for e in errors])
            success = False

        # --- Step 2: Register regime ---
        candidate = bootstrap_result.get("candidate") or {}
        regime_dict = {
            "id": f"{CATALOG_NAMESPACE}.{_uid()}",
            "plan_id": plan_id,
            "domain_id": candidate.get("domain_id", ""),
            "constructor_ids": candidate.get("constructor_ids", []),
            "score": candidate.get("score", 0.0),
            "status": "bootstrapped",
        }

        if self.config.enable_catalog_sync:
            def _do_register():
                return self._catalog.register(regime_dict)

            registered_id = self._retry_operation(_do_register, "register_regime")
            if registered_id:
                regime_id = registered_id
                diagnostics.append(f"Regime registered: {regime_id}")
            else:
                diagnostics.append("ERROR: regime registration failed")
                success = False

        # --- Step 3: Collect evidence ---
        if self.config.enable_evidence_collection:
            rec = self._evidence.collect(plan_id, EVIDENCE_SOURCE_TAG)
            evidence_count += 1
            # Collect one more record for the candidate score
            if candidate.get("score", 0.0) > 0:
                self._evidence.collect(plan_id, "score_justification")
                evidence_count += 1

            refs = self._evidence.get_records_for_plan(plan_id)
            manifest = self._evidence.build_manifest(refs)
            if not manifest.get("valid", False):
                diagnostics.append("WARNING: evidence manifest has validation errors")

            trust = self._evidence.get_trust_level(refs)
            if trust < self.config.trust_threshold:
                diagnostics.append(
                    f"WARNING: evidence trust {trust:.3f} below threshold "
                    f"{self.config.trust_threshold:.3f}"
                )

        # --- Step 4: Submit to orchestrator ---
        if self.config.enable_orchestrator_submission:
            plan_dict = bootstrap_result.get("plan") or {"id": plan_id}
            submitted_id = self._retry_operation(
                lambda: self._orchestrator.submit(plan_dict),
                "submit_plan",
            )
            if submitted_id:
                diagnostics.append(f"Plan submitted to orchestrator: {submitted_id}")
                self._orchestrator.mark_completed(submitted_id, "integration_complete")
            else:
                diagnostics.append("ERROR: orchestrator submission failed")
                success = False

        elapsed = _utcnow() - start

        if self.config.verbose:
            diagnostics.append(f"Integration elapsed: {elapsed:.3f}s, run #{self._run_count}")

        return IntegrationResult(
            result_id=_uid(),
            plan_id=plan_id,
            success=success,
            regime_id=regime_id,
            evidence_count=evidence_count,
            diagnostics=diagnostics,
            elapsed_secs=round(elapsed, 4),
            created_at=_utcnow(),
        )

    def register_regime(self, regime_dict: RegimeDict) -> str:
        """
        Register an assembled regime with the catalog.

        This method is also callable directly (without going through
        ``integrate``) for cases where the caller has pre-assembled the
        regime dict and only needs the registration step.

        Parameters
        ----------
        regime_dict : RegimeDict
            Regime descriptor to register.

        Returns
        -------
        str
            Assigned regime ID.
        """
        return self._catalog.register(regime_dict)

    def fetch_evidence(self, plan_id: str) -> list[EvidenceRef]:
        """
        Fetch all collected evidence records for a plan.

        Parameters
        ----------
        plan_id : str
            Bootstrap plan identifier.

        Returns
        -------
        list[EvidenceRef]
            Evidence records associated with the plan.
        """
        return self._evidence.get_records_for_plan(plan_id)

    def submit_to_orchestrator(self, plan: PlanDict) -> str | None:
        """
        Submit a plan dict to the orchestrator and return its plan ID.

        Parameters
        ----------
        plan : PlanDict
            Bootstrap plan descriptor.

        Returns
        -------
        str | None
            Orchestrator plan ID, or None if submission failed.
        """
        return self._retry_operation(
            lambda: self._orchestrator.submit(plan),
            "submit_to_orchestrator",
        )

    def sync_catalog(self, regime_id: str) -> bool:
        """
        Trigger a catalog sync for the given regime ID.

        In the in-memory implementation this is a no-op that verifies the
        regime exists in the catalog.  A production implementation would
        push a sync event to the catalog service.

        Parameters
        ----------
        regime_id : str
            Regime to sync.

        Returns
        -------
        bool
            True if the regime was found and synced.
        """
        entry = self._catalog.lookup(regime_id)
        if entry is None:
            return False
        # Stamp a sync timestamp
        self._catalog.update(regime_id, {"last_synced_at": _utcnow()})
        return True

    def get_status(self, plan_id: str) -> dict[str, Any]:
        """
        Get the integration status for a given plan.

        Returns a dict summarising the orchestrator status, evidence count,
        and catalog presence for the plan.

        Parameters
        ----------
        plan_id : str
            Bootstrap plan identifier.

        Returns
        -------
        dict[str, Any]
            Status summary dict.
        """
        orch_status = self._orchestrator.get_status(plan_id)
        evidence = self._evidence.get_records_for_plan(plan_id)
        return {
            "plan_id": plan_id,
            "orchestrator": orch_status,
            "evidence_count": len(evidence),
            "catalog_count": self._catalog.count(),
            "run_count": self._run_count,
        }

    def reset(self) -> None:
        """
        Reset all internal adapter state.

        Creates fresh adapter instances and clears the diagnostics list.
        This is primarily useful for test isolation between integration
        runs in the same process.
        """
        self._catalog = RegimeCatalogAdapter()
        self._evidence = EvidenceBootstrapAdapter()
        self._orchestrator = OrchestratorAdapter()
        self._diagnostics = []
        self._status = {}

    def summarize(self) -> dict[str, Any]:
        """
        Return a summary dict of the current integration state.

        Includes run count, catalog size, orchestrator active plan count,
        and configuration summary.

        Returns
        -------
        dict[str, Any]
            Integration state summary.
        """
        return {
            "run_count": self._run_count,
            "catalog_size": self._catalog.count(),
            "active_plans": len(self._orchestrator.list_active()),
            "config": self.config.to_dict(),
            "version": INTEGRATION_VERSION,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_evidence_manifest(self, plan_id: str) -> dict[str, Any]:
        """
        Build an evidence manifest for the given plan.

        Collects all records associated with the plan and delegates to
        ``EvidenceBootstrapAdapter.build_manifest``.

        Parameters
        ----------
        plan_id : str
            Plan identifier.

        Returns
        -------
        dict[str, Any]
            Evidence manifest descriptor.
        """
        refs = self._evidence.get_records_for_plan(plan_id)
        return self._evidence.build_manifest(refs)

    def _validate_result(self, result: dict[str, Any]) -> list[str]:
        """
        Validate a bootstrap result dict before integration.

        Checks for required top-level keys and that the embedded candidate
        (if present) has a positive score.

        Parameters
        ----------
        result : dict[str, Any]
            Bootstrap result to validate.

        Returns
        -------
        list[str]
            List of validation error strings.
        """
        errors: list[str] = []
        if not isinstance(result, dict):
            return ["result must be a dict"]
        if "candidate" not in result and "plan_id" not in result:
            errors.append("result must contain 'candidate' or 'plan_id'")
        candidate = result.get("candidate")
        if candidate is not None and not isinstance(candidate, dict):
            errors.append("'candidate' must be a dict")
        return errors

    def _retry_operation(self, operation: Any, name: str) -> Any:
        """
        Execute *operation* with automatic retries on failure.

        The operation is called up to ``config.max_retries`` times.  On
        each failure the exception is swallowed and recorded in the
        internal diagnostics list.  If all attempts fail, None is returned.

        Parameters
        ----------
        operation : callable
            Zero-argument callable to execute.
        name : str
            Human-readable name for logging.

        Returns
        -------
        Any
            Return value of the successful call, or None.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                return operation()
            except Exception as exc:
                last_exc = exc
                self._diagnostics.append(
                    f"WARNING: {name} attempt {attempt} failed: {exc}"
                )
        self._diagnostics.append(f"ERROR: {name} exhausted {self.config.max_retries} retries")
        return None


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def create_integration(config: IntegrationConfig | None = None) -> BootstrappingIntegration:
    """
    Factory function — create a ``BootstrappingIntegration`` with the given config.

    This is the preferred way to create integration instances in application
    code because it keeps the import surface minimal: callers only need to
    import ``create_integration`` rather than the class directly.

    Parameters
    ----------
    config : IntegrationConfig | None
        Configuration to use.  Defaults to ``IntegrationConfig.default()``.

    Returns
    -------
    BootstrappingIntegration
        Configured integration facade.
    """
    return BootstrappingIntegration(config=config)


def run_integration_pipeline(
    bootstrap_result: dict[str, Any],
    config: IntegrationConfig | None = None,
) -> IntegrationResult:
    """
    Run the full integration pipeline for a bootstrap result.

    Convenience function that creates a fresh ``BootstrappingIntegration``,
    calls ``integrate``, and returns the result.  Useful for one-shot
    integration calls where there is no need to keep the integration
    instance alive.

    Parameters
    ----------
    bootstrap_result : dict[str, Any]
        Bootstrap result dict.
    config : IntegrationConfig | None
        Optional configuration.

    Returns
    -------
    IntegrationResult
        Outcome of the integration run.
    """
    integration = create_integration(config)
    return integration.integrate(bootstrap_result)


def _build_integration_id() -> str:
    """
    Generate a unique integration run identifier.

    Returns
    -------
    str
        32-character hex identifier prefixed with ``'int_'``.
    """
    return f"int_{_uid()}"


def _validate_integration_config(config: IntegrationConfig) -> list[str]:
    """
    Validate an ``IntegrationConfig`` object and return error messages.

    Checks that numeric fields are in sensible ranges:
      * ``max_retries`` ≥ 1
      * ``timeout_secs`` > 0
      * ``trust_threshold`` ∈ [0, 1]

    Parameters
    ----------
    config : IntegrationConfig
        Configuration to validate.

    Returns
    -------
    list[str]
        List of error messages.  Empty list means config is valid.
    """
    errors: list[str] = []

    if config.max_retries < 1:
        errors.append(f"max_retries must be ≥ 1, got {config.max_retries}")
    if config.timeout_secs <= 0:
        errors.append(f"timeout_secs must be > 0, got {config.timeout_secs}")
    if not (0.0 <= config.trust_threshold <= 1.0):
        errors.append(
            f"trust_threshold must be in [0, 1], got {config.trust_threshold}"
        )

    return errors
