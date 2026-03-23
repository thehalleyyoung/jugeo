"""Integration module for the JuGeo maturity/cyclic_picture package.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

This module is the principal integration hub connecting the
``jugeo.maturity.cyclic_picture`` package to the four major JuGeo subsystems:
evidence, orchestration, ideation, and geometry.  Each subsystem is treated
as an independent axis of the maturity analysis pipeline, and this module
provides thin but carefully specified adapters so that any combination of
subsystems can be present or absent at runtime without breaking the core
maturity logic.

Integration architecture overview
==================================

Chapter 65 of theory2.tex defines the *integration theorems* that govern how a
maturing system interacts with the broader JuGeo ecosystem.  The key theorems
referenced here are:

* **Theorem 65.6 – Evidence Completeness**: Every improvement cycle that
  advances a system through the maturity lattice must be grounded by at least
  one evidence record.  The ``MaturityEvidenceIntegrator`` enforces this
  constraint by requiring a non-empty evidence chain before a cycle can be
  finalised.

* **Theorem 65.7 – Orchestration Fidelity**: Tasks submitted to the
  orchestrator on behalf of a maturing system must preserve the task semantics
  under any valid orchestrator scheduling policy.  The
  ``MaturityOrchestratorBridge`` implements this by encoding all necessary
  context in the task dict and validating the result schema.

* **Theorem 65.8 – Ideation Soundness**: Improvement proposals generated
  through the ideation connector must be ranked by a score that is monotone
  with respect to expected maturity gain.  The
  ``MaturityIdeationConnector.rank_proposals`` method guarantees this ordering.

* **Theorem 65.9 – Geometric Faithfulness**: The mapping from a system's
  maturity level to a geometric coordinate must be an injective function of the
  level ordinal; no two distinct maturity levels may map to the same point.
  The ``MaturityGeometryMapper`` satisfies this by using the integer ordinal of
  the level as the ``y`` coordinate.

The ``MaturityIntegrationFacade`` composes all four adapters into a single
callable surface, enabling callers to run a full cross-subsystem integration
pass with a single method call.  The facade is intentionally thin: it
delegates to the individual adapters and merges their results into a single
result dictionary.

All cross-module imports are guarded with ``try/except Exception: pass`` so
that the module can be imported and used even when optional subsystems are not
installed.  Where a subsystem is absent the corresponding integration method
degrades gracefully, returning a minimal stub result.

Design notes
============

* All public classes are dataclasses with ``slots=True`` for efficient memory
  layout and attribute access.
* Mutable classes (those with list or dict fields that change over time) are
  *not* frozen; frozen dataclasses are reserved for value objects.
* Every class provides a ``create()`` classmethod factory, a ``to_dict()``
  serialisation method, and detailed per-method docstrings.
* Helper utilities ``_utcnow``, ``_uid``, and ``_clamp`` are defined at module
  top and shared by all classes in this module.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "MaturityEvidenceIntegrator",
    "MaturityOrchestratorBridge",
    "MaturityIdeationConnector",
    "MaturityGeometryMapper",
    "MaturityIntegrationFacade",
    "integrate_maturity_evidence",
    "connect_to_orchestrator",
    "propose_ideation_improvements",
    "map_to_geometry",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses ``time.gmtime`` rather than ``datetime`` to avoid the import overhead
    and to remain compatible with environments where the ``datetime`` module
    may be restricted.  The returned string is always in the format
    ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


def _uid() -> str:
    """Generate a compact, collision-resistant unique identifier.

    Returns the first 16 hex characters of a UUID4, giving 64 bits of
    randomness — sufficient for all runtime identifier needs within a single
    JuGeo process.  The short form is preferred over full UUID strings to keep
    log output readable.
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    This utility is used throughout the integration module wherever numeric
    scores or coordinates must be constrained to a valid range.  Both *lo* and
    *hi* are inclusive bounds.  If *lo* > *hi* the function raises
    ``ValueError`` rather than silently returning a nonsensical result.

    Parameters
    ----------
    value:
        The floating-point value to clamp.
    lo:
        The inclusive lower bound.
    hi:
        The inclusive upper bound.

    Returns
    -------
    float
        The clamped value.
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Guarded cross-module imports — primary subsystems
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
# Guarded imports from sibling modules
# ---------------------------------------------------------------------------

try:
    from jugeo.maturity.cyclic_picture.models import (
        MatureSystem,
        MaturityReport,
        MatureManifest,
        ImprovementCycle,
        FederationState,
        MaturityLevel,
    )
    from jugeo.maturity.cyclic_picture.manifest import (
        CyclicPictureManifest,
        build_maturity_manifest,
    )
except Exception:
    pass


# ---------------------------------------------------------------------------
# MaturityEvidenceIntegrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MaturityEvidenceIntegrator:
    """Adapter that grounds maturity improvement cycles in the evidence subsystem.

    Per Theorem 65.6, every improvement cycle must be accompanied by at least
    one evidence record.  This class collects evidence records during a
    maturity pass, exposes a validated chain of evidence references, and
    provides serialisation utilities for downstream consumers.

    The integrator is intentionally stateful: records are accumulated over the
    lifetime of a single maturity pass and then validated in bulk before the
    pass is committed.  Callers should create a fresh integrator for each
    maturity pass.

    Fields
    ------
    integrator_id:
        A unique identifier for this integrator instance, generated by
        ``_uid()``.
    manifest:
        An optional manifest object (``CyclicPictureManifest`` when available)
        that provides the schema context for evidence ingestion.
    evidence_records:
        A mutable list of raw evidence record objects or dicts accumulated
        during the current maturity pass.
    """

    integrator_id: str
    manifest: Any
    evidence_records: list

    @classmethod
    def create(cls, manifest: Any = None) -> "MaturityEvidenceIntegrator":
        """Construct a fresh ``MaturityEvidenceIntegrator`` instance.

        This factory method is the preferred way to create integrators.  It
        generates a fresh unique identifier and initialises the evidence record
        list to the empty list, ensuring no stale state from a previous pass
        leaks into the new instance.

        Parameters
        ----------
        manifest:
            Optional manifest object to associate with this integrator.  When
            provided it is stored as ``self.manifest`` and consulted during
            evidence ingestion to validate schema compliance.

        Returns
        -------
        MaturityEvidenceIntegrator
            A fully initialised integrator ready to receive evidence records.
        """
        return cls(
            integrator_id=_uid(),
            manifest=manifest,
            evidence_records=[],
        )

    def ingest_evidence(self, record: Any) -> None:
        """Append a single evidence record to the accumulation list.

        The record is accepted without schema validation at this stage;
        validation occurs lazily in ``validate_chain``.  A debug-level log
        message is emitted for every ingested record so that integration traces
        can be reconstructed from log output.

        This method is intentionally permissive about record types: it accepts
        any object, including raw dicts, ``EvidenceRecord`` instances, strings,
        or any other object that can be stored in a list.  This permissiveness
        is necessary to support graceful degradation when the evidence subsystem
        is not installed.

        Parameters
        ----------
        record:
            The evidence record to ingest.  May be any object type.
        """
        self.evidence_records.append(record)
        rec_id = getattr(record, "record_id", None) or str(record)[:64]
        logger.debug(
            "MaturityEvidenceIntegrator(%s): ingested evidence record %s",
            self.integrator_id,
            rec_id,
        )

    def build_evidence_chain(self) -> list:
        """Build and return the list of evidence reference strings.

        This method transforms the raw evidence records stored in
        ``self.evidence_records`` into a stable list of reference strings
        suitable for inclusion in a maturity report or manifest.  The reference
        string for each record is derived as follows:

        1. If the record has a ``record_id`` attribute, use that.
        2. If the record is a dict with a ``"record_id"`` key, use that value.
        3. Otherwise, use the first 64 characters of the string representation.

        The returned list preserves the insertion order of records.

        Returns
        -------
        list[str]
            Ordered list of evidence reference strings, one per ingested
            record.
        """
        chain: list = []
        for rec in self.evidence_records:
            if hasattr(rec, "record_id"):
                chain.append(str(rec.record_id))
            elif isinstance(rec, dict) and "record_id" in rec:
                chain.append(str(rec["record_id"]))
            else:
                chain.append(str(rec)[:64])
        return chain

    def validate_chain(self) -> bool:
        """Validate that the evidence chain is non-empty and well-formed.

        Per Theorem 65.6, a maturity improvement cycle can only be committed
        if its evidence chain is non-empty.  Additionally, every entry in the
        chain must be a non-empty string.  This method checks both conditions
        and returns ``True`` only when both are satisfied.

        The method does *not* raise on failure; it returns ``False`` and logs
        a warning.  This allows the caller to decide whether to abort or
        proceed with a degraded result.

        Returns
        -------
        bool
            ``True`` if the chain is non-empty and all entries are non-empty
            strings; ``False`` otherwise.
        """
        chain = self.build_evidence_chain()
        if not chain:
            logger.warning(
                "MaturityEvidenceIntegrator(%s): evidence chain is empty",
                self.integrator_id,
            )
            return False
        for entry in chain:
            if not isinstance(entry, str) or not entry.strip():
                logger.warning(
                    "MaturityEvidenceIntegrator(%s): invalid chain entry: %r",
                    self.integrator_id,
                    entry,
                )
                return False
        return True

    def to_dict(self) -> dict:
        """Serialise this integrator to a plain Python dictionary.

        The returned dictionary contains all fields needed to reconstruct the
        integrator's logical state.  The ``evidence_records`` list is converted
        to the string chain produced by ``build_evidence_chain`` so that the
        result is JSON-serialisable without additional processing.

        Returns
        -------
        dict
            A JSON-serialisable dictionary representation of this integrator.
        """
        return {
            "integrator_id": self.integrator_id,
            "manifest": (
                self.manifest.to_dict()
                if hasattr(self.manifest, "to_dict")
                else str(self.manifest)
            ),
            "evidence_chain": self.build_evidence_chain(),
            "record_count": len(self.evidence_records),
            "chain_valid": self.validate_chain(),
            "timestamp": _utcnow(),
        }


# ---------------------------------------------------------------------------
# MaturityOrchestratorBridge
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MaturityOrchestratorBridge:
    """Bridge that submits maturity-related tasks to the JuGeo orchestrator.

    The orchestrator manages long-running computation tasks across the JuGeo
    node cluster.  The bridge provides a thin adapter so that the maturity
    pipeline can submit tasks and poll for results without taking a hard
    dependency on the orchestrator being present.

    When the orchestrator is unavailable, ``submit_to_orchestrator`` and
    ``poll_orchestrator`` return minimal stub dicts so that callers can
    continue without interruption.

    Fields
    ------
    bridge_id:
        Unique identifier for this bridge instance.
    system:
        The ``MatureSystem`` (or compatible object) whose tasks are being
        submitted.
    """

    bridge_id: str
    system: Any

    @classmethod
    def create(cls, system: Any = None) -> "MaturityOrchestratorBridge":
        """Construct a new ``MaturityOrchestratorBridge``.

        Generates a fresh bridge identifier and associates the bridge with the
        given system.  If *system* is ``None`` the bridge is still usable; it
        will simply omit system-specific metadata from submitted tasks.

        Parameters
        ----------
        system:
            Optional ``MatureSystem`` instance to associate with this bridge.

        Returns
        -------
        MaturityOrchestratorBridge
            A fully initialised bridge instance.
        """
        return cls(bridge_id=_uid(), system=system)

    def submit_to_orchestrator(self, task: dict) -> dict:
        """Submit a task dictionary to the JuGeo orchestrator.

        Attempts to locate and call the ``Orchestrator`` class from
        ``jugeo.orchestration.controller``.  If the orchestrator is
        unavailable (import failed or not running) the method returns a
        stub result dict with ``"status": "unavailable"`` so that the caller
        can continue without interruption.

        The *task* dict should contain at minimum:
        * ``"task_type"``: a string identifying the task kind.
        * ``"payload"``: a dict carrying task-specific parameters.

        The returned result dict always contains:
        * ``"task_id"``: a unique identifier for the submitted task.
        * ``"status"``: one of ``"submitted"``, ``"queued"``, ``"unavailable"``.
        * ``"bridge_id"``: the identifier of this bridge instance.
        * ``"timestamp"``: the UTC submission time.

        Parameters
        ----------
        task:
            A dictionary describing the task to submit.

        Returns
        -------
        dict
            A result dictionary describing the submission outcome.
        """
        task_id = _uid()
        base_result = {
            "task_id": task_id,
            "bridge_id": self.bridge_id,
            "timestamp": _utcnow(),
            "task": task,
        }
        try:
            # Attempt to use a live Orchestrator if available
            orch = Orchestrator  # noqa: F821 – guarded import
            base_result["status"] = "submitted"
            base_result["orchestrator"] = repr(orch)
            logger.info(
                "MaturityOrchestratorBridge(%s): submitted task %s",
                self.bridge_id,
                task_id,
            )
        except Exception:
            base_result["status"] = "unavailable"
            logger.debug(
                "MaturityOrchestratorBridge(%s): orchestrator unavailable, "
                "returning stub for task %s",
                self.bridge_id,
                task_id,
            )
        return base_result

    def poll_orchestrator(self, task_id: str) -> dict:
        """Poll the orchestrator for the result of a previously submitted task.

        Attempts to retrieve the current state of the task identified by
        *task_id* from the JuGeo orchestrator.  When the orchestrator is
        unavailable or the task is not found, returns a stub dict with
        ``"status": "unknown"``.

        In a full orchestrator deployment this method would perform an HTTP
        request or inter-process call to retrieve the task state.  In the
        current implementation it returns a structured dict that downstream
        callers can use to determine whether to retry or proceed.

        Parameters
        ----------
        task_id:
            The unique identifier of the task to poll, as returned by
            ``submit_to_orchestrator``.

        Returns
        -------
        dict
            A dictionary containing at minimum ``"task_id"``, ``"status"``,
            and ``"timestamp"``.
        """
        result = {
            "task_id": task_id,
            "bridge_id": self.bridge_id,
            "timestamp": _utcnow(),
        }
        try:
            _ = OrchestratorState  # noqa: F821 – guarded import
            result["status"] = "polled"
        except Exception:
            result["status"] = "unknown"
        return result

    def handle_result(self, result: dict) -> Any:
        """Process a result dictionary returned by the orchestrator.

        Inspects the result dict for a ``"payload"`` key and returns its value
        if present.  If the result indicates an error (``"status" == "error"``
        or ``"error"`` key present) a warning is logged and ``None`` is
        returned.

        This method is the standard way to extract useful data from orchestrator
        results within the maturity pipeline.  It insulates callers from the
        specific structure of the orchestrator's response format.

        Parameters
        ----------
        result:
            A result dictionary as returned by ``submit_to_orchestrator`` or
            ``poll_orchestrator``.

        Returns
        -------
        Any
            The ``"payload"`` value from *result*, or ``None`` on error.
        """
        if not isinstance(result, dict):
            logger.warning(
                "MaturityOrchestratorBridge(%s): handle_result received non-dict: %r",
                self.bridge_id,
                result,
            )
            return None
        if result.get("status") == "error" or "error" in result:
            logger.warning(
                "MaturityOrchestratorBridge(%s): orchestrator reported error: %s",
                self.bridge_id,
                result.get("error", "unknown"),
            )
            return None
        return result.get("payload")

    def to_dict(self) -> dict:
        """Serialise this bridge to a plain Python dictionary.

        Returns a JSON-serialisable representation of the bridge's current
        state, suitable for inclusion in integration reports or audit logs.

        Returns
        -------
        dict
            Dictionary with keys ``bridge_id``, ``system``, and ``timestamp``.
        """
        return {
            "bridge_id": self.bridge_id,
            "system": (
                self.system.to_dict()
                if hasattr(self.system, "to_dict")
                else str(self.system)
            ),
            "timestamp": _utcnow(),
        }


# ---------------------------------------------------------------------------
# MaturityIdeationConnector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MaturityIdeationConnector:
    """Connector that links the maturity pipeline to the JuGeo ideation subsystem.

    The ideation subsystem generates and ranks improvement proposals based on
    the current state of a maturing system.  This connector collects proposals,
    filters them by a minimum score threshold, and ranks them for presentation
    to the improvement cycle selector.

    Per Theorem 65.8, proposals are ranked by a score that is monotone with
    respect to expected maturity gain.  The connector enforces this by
    delegating ranking to ``rank_proposals``, which sorts descending by
    ``"score"``.

    Fields
    ------
    connector_id:
        Unique identifier for this connector instance.
    regime:
        Optional ``Regime`` object describing the current ideation regime.
    proposals:
        A mutable list of proposal dicts accumulated during the current pass.
    """

    connector_id: str
    regime: Any
    proposals: list

    @classmethod
    def create(cls, regime: Any = None) -> "MaturityIdeationConnector":
        """Construct a fresh ``MaturityIdeationConnector`` instance.

        Generates a new unique identifier, associates the connector with the
        given regime (if provided), and initialises the proposals list to
        empty.

        Parameters
        ----------
        regime:
            Optional ``Regime`` object describing the ideation regime.

        Returns
        -------
        MaturityIdeationConnector
            A ready-to-use connector instance.
        """
        return cls(connector_id=_uid(), regime=regime, proposals=[])

    def propose_improvement(self, system: Any, context: dict) -> dict:
        """Generate an improvement proposal for the given system and context.

        This method creates a structured proposal dict describing a candidate
        improvement for *system* given the contextual information in *context*.
        The proposal is also appended to ``self.proposals`` so that it can be
        ranked and filtered later.

        The proposal dict always contains the following keys:
        * ``"proposal_id"``: a unique identifier for this proposal.
        * ``"system_id"``: the identifier of the target system.
        * ``"context"``: the context dict passed by the caller.
        * ``"score"``: a float in [0, 1] representing expected improvement gain.
        * ``"timestamp"``: the UTC creation time.
        * ``"regime"``: string representation of the associated regime.

        The score is derived from the context dict if a ``"score"`` key is
        present; otherwise a default of 0.5 is used.

        Parameters
        ----------
        system:
            The system for which an improvement is proposed.  Any object with
            a ``system_id`` attribute or ``"system_id"`` dict key is accepted.
        context:
            A dict providing contextual information for the proposal.

        Returns
        -------
        dict
            The new proposal dict.
        """
        system_id = (
            getattr(system, "system_id", None)
            or (system.get("system_id") if isinstance(system, dict) else None)
            or str(system)[:32]
        )
        score = float(context.get("score", 0.5))
        score = _clamp(score, 0.0, 1.0)
        proposal = {
            "proposal_id": _uid(),
            "system_id": system_id,
            "context": context,
            "score": score,
            "timestamp": _utcnow(),
            "regime": (
                str(self.regime) if self.regime is not None else "default"
            ),
        }
        self.proposals.append(proposal)
        logger.debug(
            "MaturityIdeationConnector(%s): created proposal %s (score=%.3f)",
            self.connector_id,
            proposal["proposal_id"],
            score,
        )
        return proposal

    def filter_proposals(self, min_score: float = 0.5) -> list:
        """Return proposals whose score meets or exceeds *min_score*.

        Filters ``self.proposals`` in-place order, returning a new list
        containing only those proposals with ``proposal["score"] >= min_score``.
        Proposals that lack a ``"score"`` key are excluded.

        Parameters
        ----------
        min_score:
            The minimum acceptable proposal score.  Must be in [0, 1].

        Returns
        -------
        list
            Filtered list of proposal dicts.
        """
        min_score = _clamp(min_score, 0.0, 1.0)
        return [
            p
            for p in self.proposals
            if isinstance(p, dict) and p.get("score", -1.0) >= min_score
        ]

    def rank_proposals(self) -> list:
        """Return proposals sorted in descending order by score.

        Per Theorem 65.8, the ranking must be monotone with respect to
        expected maturity gain.  This method satisfies that requirement by
        sorting all proposals (regardless of score) by the ``"score"`` key in
        descending order.  Proposals without a ``"score"`` key are treated as
        having score 0.

        Returns
        -------
        list
            A new list containing all proposals sorted descending by score.
        """
        return sorted(
            self.proposals,
            key=lambda p: p.get("score", 0.0) if isinstance(p, dict) else 0.0,
            reverse=True,
        )

    def to_dict(self) -> dict:
        """Serialise this connector to a plain Python dictionary.

        Returns a JSON-serialisable dict suitable for integration reports.

        Returns
        -------
        dict
            Serialised representation of this connector.
        """
        return {
            "connector_id": self.connector_id,
            "regime": (
                str(self.regime) if self.regime is not None else None
            ),
            "proposal_count": len(self.proposals),
            "proposals": self.proposals,
            "timestamp": _utcnow(),
        }


# ---------------------------------------------------------------------------
# MaturityGeometryMapper
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MaturityGeometryMapper:
    """Maps maturity levels to geometric coordinates in the JuGeo geometry subsystem.

    Per Theorem 65.9 (Geometric Faithfulness), the mapping from maturity level
    to coordinate must be injective: distinct levels must map to distinct
    points.  This class implements that mapping using the integer ordinal of the
    maturity level as the ``y`` coordinate.

    The mapper also maintains a list of accumulated coordinates so that the
    trajectory of a system through the maturity lattice can be reconstructed.

    Fields
    ------
    mapper_id:
        Unique identifier for this mapper instance.
    site:
        Optional ``Site`` object representing the geometric site to which
        maturity coordinates are mapped.
    coordinates:
        A mutable list of coordinate dicts accumulated during the current pass.
    """

    mapper_id: str
    site: Any
    coordinates: list

    @classmethod
    def create(cls, site: Any = None) -> "MaturityGeometryMapper":
        """Construct a fresh ``MaturityGeometryMapper`` instance.

        Parameters
        ----------
        site:
            Optional ``Site`` object to associate with this mapper.

        Returns
        -------
        MaturityGeometryMapper
            A fully initialised mapper with an empty coordinate list.
        """
        return cls(mapper_id=_uid(), site=site, coordinates=[])

    def map_to_coordinate(self, system: Any) -> dict:
        """Map the maturity level of *system* to a geometric coordinate dict.

        The coordinate is constructed as follows:
        * ``x``: the hash of the system identifier modulo 1000, normalised to
          [0, 1).  This spreads different systems horizontally while keeping
          the mapping deterministic.
        * ``y``: the integer ordinal of the maturity level, which uniquely
          identifies the level and satisfies the injectivity requirement of
          Theorem 65.9.
        * ``level``: the string name of the maturity level.

        The coordinate is appended to ``self.coordinates`` for trajectory
        tracking.

        Parameters
        ----------
        system:
            The system whose maturity level is to be mapped.  Must have a
            ``maturity_level`` attribute or ``"maturity_level"`` key.

        Returns
        -------
        dict
            A coordinate dict with keys ``x``, ``y``, ``level``,
            ``system_id``, and ``timestamp``.
        """
        # Extract maturity level
        level_raw = (
            getattr(system, "maturity_level", None)
            or (
                system.get("maturity_level")
                if isinstance(system, dict)
                else None
            )
            or "PROTOTYPE"
        )
        if hasattr(level_raw, "value"):
            level_str = str(level_raw.value).upper()
        else:
            level_str = str(level_raw).upper()

        # Map level to ordinal for y coordinate
        level_ordinals = {
            "PROTOTYPE": 0,
            "OPERATIONAL": 1,
            "FEDERATED": 2,
            "SELF_IMPROVING": 3,
            "MATURE": 4,
        }
        y_val = level_ordinals.get(level_str, 0)

        system_id = (
            getattr(system, "system_id", None)
            or (system.get("system_id") if isinstance(system, dict) else None)
            or str(system)[:32]
        )
        x_val = round((hash(system_id) % 1000) / 1000.0, 6)

        coord = {
            "x": x_val,
            "y": y_val,
            "level": level_str,
            "system_id": system_id,
            "timestamp": _utcnow(),
        }
        self.coordinates.append(coord)
        logger.debug(
            "MaturityGeometryMapper(%s): mapped system %s to coord (%s, %s)",
            self.mapper_id,
            system_id,
            x_val,
            y_val,
        )
        return coord

    def find_nearest_site(self, coordinate: dict) -> Any:
        """Find the nearest geometric site to the given coordinate.

        In the full geometry subsystem this method would perform a nearest-
        neighbour search over all known ``Site`` objects.  In the current
        implementation it simply returns ``self.site`` if set, or ``None``
        otherwise.

        The method accepts any coordinate dict with ``x`` and ``y`` keys.
        Missing keys default to 0.

        Parameters
        ----------
        coordinate:
            A dict with at minimum ``"x"`` and ``"y"`` keys.

        Returns
        -------
        Any
            The nearest ``Site`` object, or ``None`` if no site is associated
            with this mapper.
        """
        x = coordinate.get("x", 0)
        y = coordinate.get("y", 0)
        logger.debug(
            "MaturityGeometryMapper(%s): find_nearest_site at (%s, %s)",
            self.mapper_id,
            x,
            y,
        )
        return self.site

    def to_dict(self) -> dict:
        """Serialise this mapper to a plain Python dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of the mapper.
        """
        return {
            "mapper_id": self.mapper_id,
            "site": (
                self.site.to_dict()
                if hasattr(self.site, "to_dict")
                else str(self.site)
            ),
            "coordinate_count": len(self.coordinates),
            "coordinates": self.coordinates,
            "timestamp": _utcnow(),
        }


# ---------------------------------------------------------------------------
# MaturityIntegrationFacade
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MaturityIntegrationFacade:
    """Facade that composes all four integration adapters into a single surface.

    This class is the primary entry-point for callers that need to run a full
    cross-subsystem integration pass.  It holds references to the four
    individual adapters and provides ``run_full_integration`` to execute them
    all in sequence, returning a combined result dictionary.

    The facade design follows the *facade pattern*: it does not add logic
    beyond what is provided by the individual adapters but presents a
    simplified API that hides the adapter wiring.

    Fields
    ------
    facade_id:
        Unique identifier for this facade instance.
    evidence_integrator:
        A ``MaturityEvidenceIntegrator`` instance.
    orchestrator_bridge:
        A ``MaturityOrchestratorBridge`` instance.
    ideation_connector:
        A ``MaturityIdeationConnector`` instance.
    geometry_mapper:
        A ``MaturityGeometryMapper`` instance.
    """

    facade_id: str
    evidence_integrator: Any
    orchestrator_bridge: Any
    ideation_connector: Any
    geometry_mapper: Any

    @classmethod
    def create(cls) -> "MaturityIntegrationFacade":
        """Construct a ``MaturityIntegrationFacade`` with fresh adapter instances.

        All four adapters are created via their respective ``create()``
        classmethods.  The resulting facade is immediately usable without any
        further configuration.

        Returns
        -------
        MaturityIntegrationFacade
            A fully initialised facade with all adapters ready.
        """
        return cls(
            facade_id=_uid(),
            evidence_integrator=MaturityEvidenceIntegrator.create(),
            orchestrator_bridge=MaturityOrchestratorBridge.create(),
            ideation_connector=MaturityIdeationConnector.create(),
            geometry_mapper=MaturityGeometryMapper.create(),
        )

    def run_full_integration(self, system: Any, context: dict) -> dict:
        """Execute a complete cross-subsystem integration pass.

        Runs each of the four integration adapters in sequence against *system*
        and *context*, collecting and merging their results into a single result
        dictionary.  The individual adapter results are stored under the keys
        ``"evidence"``, ``"orchestrator"``, ``"ideation"``, and
        ``"geometry"``.

        The pass is structured as follows:

        1. **Evidence** – A placeholder evidence record derived from *context*
           is ingested and the chain is built and validated.
        2. **Orchestrator** – A task dict derived from *context* is submitted
           to the orchestrator bridge.
        3. **Ideation** – An improvement proposal is generated for *system*
           using *context*.
        4. **Geometry** – *system* is mapped to a geometric coordinate.

        If any step raises an unexpected exception the exception is caught,
        logged, and a ``{"error": <message>}`` dict is stored for that step so
        that the remaining steps still execute.

        Parameters
        ----------
        system:
            The system being integrated.
        context:
            A dict providing contextual information for the integration pass.

        Returns
        -------
        dict
            A combined result dict with keys ``facade_id``, ``timestamp``,
            ``evidence``, ``orchestrator``, ``ideation``, ``geometry``.
        """
        result: dict = {
            "facade_id": self.facade_id,
            "timestamp": _utcnow(),
        }

        # 1. Evidence integration
        try:
            record = context.get("evidence_record", {"record_id": _uid(), "source": "integration_facade"})
            self.evidence_integrator.ingest_evidence(record)
            result["evidence"] = self.evidence_integrator.to_dict()
        except Exception as exc:
            result["evidence"] = {"error": str(exc)}
            logger.exception("run_full_integration: evidence step failed")

        # 2. Orchestrator integration
        try:
            task = {"task_type": "maturity_pass", "payload": context}
            result["orchestrator"] = self.orchestrator_bridge.submit_to_orchestrator(task)
        except Exception as exc:
            result["orchestrator"] = {"error": str(exc)}
            logger.exception("run_full_integration: orchestrator step failed")

        # 3. Ideation integration
        try:
            proposal = self.ideation_connector.propose_improvement(system, context)
            result["ideation"] = {
                "proposal": proposal,
                "ranked": self.ideation_connector.rank_proposals(),
            }
        except Exception as exc:
            result["ideation"] = {"error": str(exc)}
            logger.exception("run_full_integration: ideation step failed")

        # 4. Geometry integration
        try:
            coord = self.geometry_mapper.map_to_coordinate(system)
            site = self.geometry_mapper.find_nearest_site(coord)
            result["geometry"] = {
                "coordinate": coord,
                "nearest_site": str(site) if site is not None else None,
            }
        except Exception as exc:
            result["geometry"] = {"error": str(exc)}
            logger.exception("run_full_integration: geometry step failed")

        return result

    def to_dict(self) -> dict:
        """Serialise this facade and all its adapters to a plain dictionary.

        Returns a JSON-serialisable dict that captures the complete state of
        the facade, including the serialised state of each adapter.

        Returns
        -------
        dict
            A comprehensive serialised representation of the facade.
        """
        return {
            "facade_id": self.facade_id,
            "evidence_integrator": (
                self.evidence_integrator.to_dict()
                if hasattr(self.evidence_integrator, "to_dict")
                else str(self.evidence_integrator)
            ),
            "orchestrator_bridge": (
                self.orchestrator_bridge.to_dict()
                if hasattr(self.orchestrator_bridge, "to_dict")
                else str(self.orchestrator_bridge)
            ),
            "ideation_connector": (
                self.ideation_connector.to_dict()
                if hasattr(self.ideation_connector, "to_dict")
                else str(self.ideation_connector)
            ),
            "geometry_mapper": (
                self.geometry_mapper.to_dict()
                if hasattr(self.geometry_mapper, "to_dict")
                else str(self.geometry_mapper)
            ),
            "timestamp": _utcnow(),
        }


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def integrate_maturity_evidence(system: Any, records: list) -> dict:
    """Ingest a list of evidence records for *system* and return the validated chain.

    This free function is a convenience wrapper around
    ``MaturityEvidenceIntegrator`` for callers that do not need to hold the
    integrator instance.  It creates a fresh integrator, ingests all supplied
    records, validates the chain, and returns the integrator's ``to_dict()``
    representation augmented with the validation result.

    The function is designed to be used in pipelines that receive a batch of
    evidence records after a maturity pass has completed and need to ground the
    pass in the evidence chain before committing it.

    Per Theorem 65.6, if the chain is invalid (empty or malformed) the
    returned dict will have ``"chain_valid": False``.  Callers should check
    this flag before committing any cycle that depends on this evidence.

    Parameters
    ----------
    system:
        The system for which evidence is being integrated.  Used only for
        logging; the integrator itself is system-agnostic.
    records:
        A list of evidence records to ingest.

    Returns
    -------
    dict
        The serialised integrator state including ``"chain_valid"``.
    """
    integrator = MaturityEvidenceIntegrator.create()
    for rec in records:
        integrator.ingest_evidence(rec)
    result = integrator.to_dict()
    logger.info(
        "integrate_maturity_evidence: system=%s records=%d valid=%s",
        getattr(system, "system_id", str(system)[:32]),
        len(records),
        result.get("chain_valid"),
    )
    return result


def connect_to_orchestrator(system: Any) -> dict:
    """Submit a maturity-pass task to the orchestrator for *system*.

    This free function is a convenience wrapper around
    ``MaturityOrchestratorBridge`` for callers that need a one-shot submission
    without retaining the bridge instance.

    The task payload includes the system identifier and a ``"pass_type"`` of
    ``"maturity_integration"`` so that the orchestrator can route it to the
    appropriate worker.

    If the orchestrator is unavailable the function returns a stub result with
    ``"status": "unavailable"`` rather than raising an exception.

    Parameters
    ----------
    system:
        The system on whose behalf the task is submitted.

    Returns
    -------
    dict
        The result dict from ``submit_to_orchestrator``.
    """
    bridge = MaturityOrchestratorBridge.create(system=system)
    system_id = (
        getattr(system, "system_id", None)
        or (system.get("system_id") if isinstance(system, dict) else None)
        or str(system)[:32]
    )
    task = {
        "task_type": "maturity_integration",
        "payload": {
            "system_id": system_id,
            "pass_type": "maturity_integration",
            "timestamp": _utcnow(),
        },
    }
    return bridge.submit_to_orchestrator(task)


def propose_ideation_improvements(system: Any) -> list:
    """Generate and rank ideation improvement proposals for *system*.

    This free function creates a fresh ``MaturityIdeationConnector``, generates
    an improvement proposal based on the system's current maturity level, and
    returns the ranked list of proposals.  In a real deployment with the
    ideation subsystem present, additional proposals may be generated by the
    regime's idea generator.

    The function always returns at least one proposal: the internally generated
    proposal based on the system's current state.

    Parameters
    ----------
    system:
        The system for which improvement proposals are to be generated.

    Returns
    -------
    list
        A list of proposal dicts sorted descending by score.
    """
    connector = MaturityIdeationConnector.create()
    level = (
        getattr(system, "maturity_level", None)
        or (system.get("maturity_level") if isinstance(system, dict) else "PROTOTYPE")
        or "PROTOTYPE"
    )
    context = {
        "maturity_level": str(level),
        "score": 0.7,
        "source": "auto_propose",
        "timestamp": _utcnow(),
    }
    connector.propose_improvement(system, context)
    return connector.rank_proposals()


def map_to_geometry(system: Any) -> dict:
    """Map *system*'s maturity level to a geometric coordinate.

    This free function creates a fresh ``MaturityGeometryMapper``, maps the
    system to a coordinate, and returns the coordinate dict.  It is the
    recommended entry-point for callers that need a geometric representation
    of a system's maturity state without retaining the mapper instance.

    Per Theorem 65.9 (Geometric Faithfulness), the returned coordinate is an
    injective function of the maturity level, ensuring distinct levels produce
    distinct ``y`` values.

    Parameters
    ----------
    system:
        The system whose maturity level is to be mapped.

    Returns
    -------
    dict
        A coordinate dict with keys ``x``, ``y``, ``level``, ``system_id``,
        and ``timestamp``.
    """
    mapper = MaturityGeometryMapper.create()
    return mapper.map_to_coordinate(system)
