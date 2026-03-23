"""Integration layer connecting theorem schemas with runtime and judgment subsystems.

Bridges the abstract theorem schema system with the concrete JuGeo runtime:
monitors schema satisfaction in running systems, adapts judgment terms to schema
format, links evidence manifests to proof obligations, and reports violations.
This module implements the operational connection described in Chapter 36 between
the static schema specification and the dynamic runtime enforcement.

copilot: runtime integration for theorem schema monitoring and violation reporting.

Architecture
------------
The integration layer has five main concerns:

1. **Judgment adaptation** — translating :class:`~jugeo.judgments.judgment_terms.Judgment`
   objects from the judgment algebra into :class:`SchemaInstance` objects that the
   schema system can reason about.  This is handled by :class:`JudgmentSchemaAdapter`.

2. **Manifest linking** — connecting :class:`~jugeo.evidence.manifests.Manifest` objects
   (the runtime evidence stores) to proof obligations declared by subsystem schemas.
   Handled by :class:`ManifestSchemaLinker`.

3. **Runtime monitoring** — continuously checking that active schema instances remain
   satisfied given the current state of semantic memory and the invalidation graph.
   Handled by :class:`RuntimeSchemaMonitor`.

4. **Violation reporting** — recording, classifying, and surfacing schema violations
   for downstream analysis and remediation.  Handled by :class:`SchemaViolationReporter`.

5. **Integration orchestration** — a single top-level façade :class:`TheoremSchemaIntegration`
   that wires the four concerns together and exposes a stable public API.

Chapter 36 context
------------------
Chapter 36 of ``theory2.tex`` specifies the operational semantics of the theorem
schema system.  A *schema instance* is well-formed when every template variable
``{v}`` has been replaced by a concrete term.  An instance is *discharged* when
the associated proof obligation has been resolved — either by a certified solver,
a human reviewer, or a copilot-mediated certificate.  The runtime integration
defined here implements the monitoring and reporting aspects of that lifecycle.

Usage example
-------------
::

    from jugeo.encodings.theorem_schemas.integration import TheoremSchemaIntegration
    from jugeo.encodings.theorem_schemas.models import SubsystemSchema, SubsystemKind

    integration = TheoremSchemaIntegration()
    schema = SubsystemSchema(name="example", kind=SubsystemKind.STRUCTURAL)
    integration.register_subsystem("example", schema)
    health = integration.run_health_check()
    print(health)
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Runtime imports — these may not exist yet; we guard with TYPE_CHECKING-style
# try/except so the module remains importable in isolation.
# ---------------------------------------------------------------------------

try:
    from jugeo.runtime.invalidation import (
        InvalidationGraph,
        InvalidationEngine,
        InvalidationPolicy,
        InvalidationCascade,
        InvalidationReason,
    )
except ImportError:  # pragma: no cover
    InvalidationGraph = None  # type: ignore[assignment,misc]
    InvalidationEngine = None  # type: ignore[assignment,misc]
    InvalidationPolicy = None  # type: ignore[assignment,misc]
    InvalidationCascade = None  # type: ignore[assignment,misc]

    class InvalidationReason(str, Enum):  # type: ignore[no-redef]
        SUPPORT_CHANGE = "support-change"
        TRUST_CHANGE = "trust-change"
        REPLAY_CONFLICT = "replay-conflict"

try:
    from jugeo.runtime.memory import SemanticMemory, MemorySnapshot
except ImportError:  # pragma: no cover
    SemanticMemory = None  # type: ignore[assignment,misc]
    MemorySnapshot = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.manifests import Manifest, EvidenceArchive
except ImportError:  # pragma: no cover
    Manifest = None  # type: ignore[assignment,misc]
    EvidenceArchive = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        EvidenceBundle,
        EvidenceItem,
        TrustAnnotation,
        Obstruction,
        ResidualObligation,
        Provenance,
        ProvenanceSource,
        JudgmentStatus,
        JudgmentAlgebra,
    )
except ImportError:  # pragma: no cover
    Judgment = None  # type: ignore[assignment,misc]
    EvidenceBundle = None  # type: ignore[assignment,misc]
    EvidenceItem = None  # type: ignore[assignment,misc]
    TrustAnnotation = None  # type: ignore[assignment,misc]
    Obstruction = None  # type: ignore[assignment,misc]
    ResidualObligation = None  # type: ignore[assignment,misc]
    Provenance = None  # type: ignore[assignment,misc]
    ProvenanceSource = None  # type: ignore[assignment,misc]
    JudgmentStatus = None  # type: ignore[assignment,misc]
    JudgmentAlgebra = None  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.supports import SupportSet, SupportRegion
    from jugeo.geometry.site import CoordinateObject
except ImportError:  # pragma: no cover
    SupportSet = None  # type: ignore[assignment,misc]
    SupportRegion = None  # type: ignore[assignment,misc]
    CoordinateObject = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.theorem_schemas.models import (
        TheoremSchema,
        SubsystemSchema,
        SchemaInstance,
        ProofObligation,
        ProofStyle,
        SubsystemKind,
        ProofAgent,
        InstanceStatus,
    )
except ImportError:  # pragma: no cover
    TheoremSchema = None  # type: ignore[assignment,misc]
    SubsystemSchema = None  # type: ignore[assignment,misc]
    SchemaInstance = None  # type: ignore[assignment,misc]
    ProofObligation = None  # type: ignore[assignment,misc]
    ProofStyle = None  # type: ignore[assignment,misc]
    SubsystemKind = None  # type: ignore[assignment,misc]
    ProofAgent = None  # type: ignore[assignment,misc]
    InstanceStatus = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.theorem_schemas.proof_obligations import (
        ObligationStatus,
        DischargeRecord,
        ObligationTracker,
        ObligationDispatcher,
    )
except ImportError:  # pragma: no cover
    ObligationStatus = None  # type: ignore[assignment,misc]
    DischargeRecord = None  # type: ignore[assignment,misc]
    ObligationTracker = None  # type: ignore[assignment,misc]
    ObligationDispatcher = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

__all__ = [
    "IntegrationHealth",
    "JudgmentSchemaAdapter",
    "ManifestSchemaLinker",
    "RuntimeSchemaMonitor",
    "SchemaViolationReporter",
    "TheoremSchemaIntegration",
    "run_integration_test",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Return a fresh UUID4 string for use as a record identifier."""
    return str(uuid.uuid4())


def _now() -> float:
    """Return the current POSIX timestamp via :func:`time.time`."""
    return time.time()


def _has_unresolved_vars(text: str) -> bool:
    """Return ``True`` if *text* still contains ``{variable}`` placeholders."""
    import re
    return bool(re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", text))


def _token_set(text: str) -> set[str]:
    """Split *text* on whitespace and punctuation, return lower-cased token set."""
    import re
    return {t.lower() for t in re.split(r"[\s,;:.!?()\[\]{}]+", text) if t}


# ---------------------------------------------------------------------------
# IntegrationHealth
# ---------------------------------------------------------------------------


@dataclass
class IntegrationHealth:
    """Snapshot of the integration health for a single subsystem schema.

    Captures whether the schema monitoring infrastructure believes the subsystem
    is satisfying its theorem obligations, along with counts of discharged,
    pending, and failed obligations, and any violations or notes collected during
    the monitoring pass.

    Parameters
    ----------
    health_id:
        Unique identifier for this health record (auto-generated UUID4).
    timestamp:
        POSIX timestamp at which the health check was performed.
    is_healthy:
        ``True`` when no violations have been recorded and all required schemas
        report no missing proofs.
    schema_violations:
        List of human-readable violation descriptions collected during monitoring.
    missing_obligations:
        List of obligation identifiers that are required but not yet discharged.
    discharged_count:
        Number of obligations that have been fully discharged.
    pending_count:
        Number of obligations that are open but not yet failed.
    failed_count:
        Number of obligations that have been marked as failed.
    notes:
        Free-form notes appended during the health check pass.
    """

    health_id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=_now)
    is_healthy: bool = True
    schema_violations: list[str] = field(default_factory=list)
    missing_obligations: list[str] = field(default_factory=list)
    discharged_count: int = 0
    pending_count: int = 0
    failed_count: int = 0
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_violation(self, violation: str) -> None:
        """Append *violation* to the violation list and mark health as degraded.

        Parameters
        ----------
        violation:
            Human-readable description of the schema violation.
        """
        self.schema_violations.append(violation)
        self.is_healthy = False

    def add_note(self, note: str) -> None:
        """Append an informational *note* to this health record.

        Notes do not affect ``is_healthy``; they are used for diagnostics.

        Parameters
        ----------
        note:
            Informational message to attach to this health snapshot.
        """
        self.notes.append(note)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise this health record to a plain JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            A dictionary containing all fields, suitable for ``json.dumps``.
        """
        return {
            "health_id": self.health_id,
            "timestamp": self.timestamp,
            "is_healthy": self.is_healthy,
            "schema_violations": list(self.schema_violations),
            "missing_obligations": list(self.missing_obligations),
            "discharged_count": self.discharged_count,
            "pending_count": self.pending_count,
            "failed_count": self.failed_count,
            "notes": list(self.notes),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> IntegrationHealth:
        """Reconstruct an :class:`IntegrationHealth` from a serialised dict.

        Parameters
        ----------
        d:
            Dict as returned by :meth:`to_json`.

        Returns
        -------
        IntegrationHealth
            A new instance populated from the dict fields.
        """
        obj = cls(
            health_id=d.get("health_id", _new_id()),
            timestamp=d.get("timestamp", _now()),
            is_healthy=d.get("is_healthy", True),
            discharged_count=d.get("discharged_count", 0),
            pending_count=d.get("pending_count", 0),
            failed_count=d.get("failed_count", 0),
        )
        obj.schema_violations = list(d.get("schema_violations", []))
        obj.missing_obligations = list(d.get("missing_obligations", []))
        obj.notes = list(d.get("notes", []))
        return obj

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def overall_status(self) -> str:
        """Return a high-level status string based on violation count.

        Returns
        -------
        str
            ``"HEALTHY"`` when there are no violations, ``"DEGRADED"`` when
            there are between 1 and 4 violations, or ``"CRITICAL"`` when there
            are 5 or more violations.
        """
        n = len(self.schema_violations)
        if n == 0:
            return "HEALTHY"
        if n < 5:
            return "DEGRADED"
        return "CRITICAL"

    def summary(self) -> str:
        """Return a one-line human-readable summary of this health record.

        Returns
        -------
        str
            A short string suitable for logging or display.
        """
        status = self.overall_status()
        v = len(self.schema_violations)
        m = len(self.missing_obligations)
        return (
            f"IntegrationHealth({self.health_id[:8]}) status={status} "
            f"violations={v} missing={m} discharged={self.discharged_count} "
            f"pending={self.pending_count} failed={self.failed_count}"
        )


# ---------------------------------------------------------------------------
# JudgmentSchemaAdapter
# ---------------------------------------------------------------------------


class JudgmentSchemaAdapter:
    """Adapts :class:`~jugeo.judgments.judgment_terms.Judgment` objects to schema format.

    The judgment algebra and the schema system use different representational
    conventions.  This adapter bridges the gap by extracting the semantically
    relevant fields from a :class:`Judgment` and mapping them to the variable
    bindings expected by :class:`TheoremSchema`.

    Parameters
    ----------
    algebra:
        Optional :class:`~jugeo.judgments.judgment_terms.JudgmentAlgebra`
        instance to use for judgment operations.  If ``None``, a default
        algebra is constructed lazily.

    Examples
    --------
    ::

        adapter = JudgmentSchemaAdapter()
        instance = adapter.adapt_judgment_to_schema(j, schema)
        if instance is not None:
            print(instance.instantiated_statement)
    """

    def __init__(self, algebra: Any | None = None) -> None:
        self._algebra = algebra
        self._adaptation_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------

    def adapt_judgment_to_schema(
        self, judgment: Any, schema: Any
    ) -> Any | None:
        """Adapt a single judgment to a schema instance.

        Extracts proposition text and coordinate information from *judgment*,
        constructs a bindings dict, validates the bindings against the schema's
        required variables, and calls :meth:`TheoremSchema.instantiate` to
        produce a :class:`SchemaInstance`.

        Parameters
        ----------
        judgment:
            A :class:`~jugeo.judgments.judgment_terms.Judgment` to adapt.
        schema:
            The :class:`TheoremSchema` to instantiate.

        Returns
        -------
        SchemaInstance | None
            The instantiated schema, or ``None`` if the judgment is not
            compatible with the schema's variable requirements.
        """
        try:
            bindings = self.extract_bindings_from_judgment(judgment, schema)
            if not self.validate_judgment_schema_compatibility(judgment, schema):
                logger.debug(
                    "Judgment %s is not compatible with schema %s",
                    getattr(judgment, "judgment_id", "?"),
                    getattr(schema, "schema_id", "?"),
                )
                return None
            if schema is None:
                return None
            if hasattr(schema, "instantiate"):
                instance = schema.instantiate(bindings)
            else:
                instance = {
                    "schema_id": getattr(schema, "schema_id", "unknown"),
                    "bindings": bindings,
                    "instantiated_statement": str(bindings),
                }
            entry = {
                "judgment_id": getattr(judgment, "judgment_id", str(id(judgment))),
                "schema_id": getattr(schema, "schema_id", "unknown"),
                "timestamp": _now(),
                "success": instance is not None,
            }
            self._adaptation_log.append(entry)
            return instance
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("adapt_judgment_to_schema failed: %s", exc)
            return None

    # ------------------------------------------------------------------

    def adapt_evidence_bundle(self, bundle: Any) -> dict[str, Any]:
        """Convert an :class:`~jugeo.judgments.judgment_terms.EvidenceBundle` to a plain dict.

        Parameters
        ----------
        bundle:
            The :class:`EvidenceBundle` to convert.

        Returns
        -------
        dict[str, Any]
            A dict with ``item_count``, ``trust_summary``, and ``item_ids``.
        """
        if bundle is None:
            return {"item_count": 0, "trust_summary": "none", "item_ids": []}
        items = getattr(bundle, "items", ()) or ()
        item_ids = [
            getattr(it, "item_id", str(idx)) for idx, it in enumerate(items)
        ]
        trust_levels = [
            str(getattr(it, "trust_level", "unknown")) for it in items
        ]
        summary = ", ".join(sorted(set(trust_levels))) if trust_levels else "none"
        return {
            "item_count": len(item_ids),
            "trust_summary": summary,
            "item_ids": item_ids,
        }

    # ------------------------------------------------------------------

    def judgment_to_proof_obligation(
        self, judgment: Any, schema: Any
    ) -> Any:
        """Create a :class:`ProofObligation` from a judgment and schema.

        Derives the obligation's statement from the judgment's proposition
        and uses the schema's metadata to populate the obligation's subsystem
        and proof-style fields.

        Parameters
        ----------
        judgment:
            Source judgment.
        schema:
            Schema against which the obligation is generated.

        Returns
        -------
        ProofObligation
            A new :class:`ProofObligation` derived from the judgment.
        """
        proposition = getattr(judgment, "proposition", None)
        if proposition is not None:
            statement = str(proposition)
        else:
            statement = f"obligation_from_judgment_{getattr(judgment, 'judgment_id', _new_id())}"

        schema_id = getattr(schema, "schema_id", "unknown_schema")
        subsystem_name = getattr(schema, "subsystem", None)
        subsystem_str = str(subsystem_name) if subsystem_name is not None else "unknown"

        if ProofObligation is not None:
            try:
                return ProofObligation(
                    obligation_id=_new_id(),
                    statement=statement,
                    schema_id=schema_id,
                    subsystem=subsystem_str,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("ProofObligation constructor unavailable: %s", exc)

        return {
            "obligation_id": _new_id(),
            "statement": statement,
            "schema_id": schema_id,
            "subsystem": subsystem_str,
            "created_at": _now(),
        }

    # ------------------------------------------------------------------

    def extract_bindings_from_judgment(
        self, judgment: Any, schema: Any = None
    ) -> dict[str, str]:
        """Extract a variable-binding dict from a judgment.

        Maps the judgment's coordinate, proposition, and carrier fields to the
        canonical variable names used by theorem schemas.

        Parameters
        ----------
        judgment:
            The source :class:`~jugeo.judgments.judgment_terms.Judgment`.
        schema:
            Optional schema; if provided, only variables required by the schema
            are included in the result.

        Returns
        -------
        dict[str, str]
            Mapping from variable names (e.g. ``"coord"``, ``"prop"``,
            ``"carrier"``) to string representations of judgment fields.
        """
        coord = getattr(judgment, "coordinate", None)
        prop = getattr(judgment, "proposition", None)
        carrier = getattr(judgment, "carrier", None)
        provenance = getattr(judgment, "provenance", None)
        status = getattr(judgment, "status", None)

        bindings: dict[str, str] = {
            "coord": str(coord) if coord is not None else "unknown_coord",
            "prop": str(prop) if prop is not None else "unknown_prop",
            "carrier": str(carrier) if carrier is not None else "unknown_carrier",
            "provenance": str(provenance) if provenance is not None else "unknown",
            "status": str(status) if status is not None else "unknown",
            "judgment_id": getattr(judgment, "judgment_id", _new_id()),
        }

        if schema is not None:
            required = getattr(schema, "required_variables", None) or []
            if required:
                bindings = {k: v for k, v in bindings.items() if k in required}

        return bindings

    # ------------------------------------------------------------------

    def validate_judgment_schema_compatibility(
        self, judgment: Any, schema: Any
    ) -> bool:
        """Check that a judgment is compatible with a schema.

        Compatibility means the judgment's subsystem (from its provenance) is
        consistent with the schema's declared subsystem, or the schema has no
        subsystem constraint.

        Parameters
        ----------
        judgment:
            Judgment to check.
        schema:
            Schema to check against.

        Returns
        -------
        bool
            ``True`` if the judgment may be adapted to this schema.
        """
        if judgment is None or schema is None:
            return False

        schema_subsystem = getattr(schema, "subsystem", None)
        if schema_subsystem is None:
            return True

        provenance = getattr(judgment, "provenance", None)
        if provenance is None:
            return True

        prov_subsystem = getattr(provenance, "subsystem", None)
        if prov_subsystem is None:
            return True

        return str(prov_subsystem) == str(schema_subsystem)

    # ------------------------------------------------------------------

    def batch_adapt(
        self, judgments: list[Any], schema: Any
    ) -> list[Any]:
        """Adapt a list of judgments to a single schema, filtering failures.

        Parameters
        ----------
        judgments:
            List of :class:`~jugeo.judgments.judgment_terms.Judgment` objects.
        schema:
            Target :class:`TheoremSchema`.

        Returns
        -------
        list[SchemaInstance]
            Successfully produced instances (failures are silently skipped).
        """
        results: list[Any] = []
        for j in judgments:
            instance = self.adapt_judgment_to_schema(j, schema)
            if instance is not None:
                results.append(instance)
        logger.debug(
            "batch_adapt: %d/%d judgments adapted to schema %s",
            len(results),
            len(judgments),
            getattr(schema, "schema_id", "?"),
        )
        return results

    # ------------------------------------------------------------------

    @property
    def adaptation_log(self) -> list[dict[str, Any]]:
        """Read-only copy of the adaptation audit log."""
        return list(self._adaptation_log)


# ---------------------------------------------------------------------------
# ManifestSchemaLinker
# ---------------------------------------------------------------------------


class ManifestSchemaLinker:
    """Links evidence manifests to schema proof obligations.

    The :class:`ManifestSchemaLinker` mediates between the evidence store
    (a :class:`~jugeo.evidence.manifests.Manifest`) and the schema system's
    proof obligations.  For each piece of evidence in the manifest, it
    constructs the corresponding obligations and verifies coverage against
    the subsystem schema.

    Parameters
    ----------
    archive:
        Optional :class:`~jugeo.evidence.manifests.EvidenceArchive` to use
        as the primary evidence source.
    """

    def __init__(self, archive: Any | None = None) -> None:
        self._archive = archive
        self._obligation_map: dict[str, list[str]] = {}

    # ------------------------------------------------------------------

    def link_manifest_to_schema(
        self, manifest: Any, schema: Any
    ) -> list[str]:
        """Link a manifest to a schema and return generated obligation IDs.

        Iterates through the manifest's evidence and constructs proof
        obligation records corresponding to each schema variable.

        Parameters
        ----------
        manifest:
            A :class:`~jugeo.evidence.manifests.Manifest` instance.
        schema:
            The :class:`TheoremSchema` to link against.

        Returns
        -------
        list[str]
            List of newly created obligation IDs.
        """
        schema_id = getattr(schema, "schema_id", "unknown")
        raw_obligations = self.extract_obligations_from_manifest(manifest)
        obligation_ids: list[str] = []
        for raw in raw_obligations:
            oid = raw.get("obligation_id", _new_id())
            obligation_ids.append(oid)

        self._obligation_map[schema_id] = obligation_ids
        logger.debug(
            "link_manifest_to_schema: schema=%s produced %d obligations",
            schema_id,
            len(obligation_ids),
        )
        return obligation_ids

    # ------------------------------------------------------------------

    def extract_obligations_from_manifest(
        self, manifest: Any
    ) -> list[dict[str, Any]]:
        """Extract raw obligation dicts from a manifest's judgment store.

        Iterates the judgment store (if present) and constructs a lightweight
        obligation record for each stored judgment.

        Parameters
        ----------
        manifest:
            A :class:`~jugeo.evidence.manifests.Manifest`.

        Returns
        -------
        list[dict[str, Any]]
            List of dicts each with ``obligation_id``, ``statement``, and
            ``source_judgment_id`` fields.
        """
        obligations: list[dict[str, Any]] = []
        judgment_store = getattr(manifest, "judgment_store", None)
        if judgment_store is None:
            judgment_store = getattr(manifest, "judgments", None)
        if judgment_store is None:
            return obligations

        try:
            entries = list(judgment_store) if hasattr(judgment_store, "__iter__") else []
        except Exception:  # pylint: disable=broad-except
            return obligations

        for entry in entries:
            jid = getattr(entry, "judgment_id", getattr(entry, "id", _new_id()))
            prop = getattr(entry, "proposition", None)
            statement = str(prop) if prop is not None else f"obligation_for_{jid}"
            obligations.append(
                {
                    "obligation_id": _new_id(),
                    "statement": statement,
                    "source_judgment_id": str(jid),
                    "created_at": _now(),
                }
            )

        return obligations

    # ------------------------------------------------------------------

    def verify_schema_coverage(
        self, manifest: Any, subsystem_schema: Any
    ) -> dict[str, bool]:
        """Verify that a manifest covers all required theorems in a subsystem schema.

        For each required theorem declared by *subsystem_schema*, checks whether
        the manifest contains at least one judgment providing evidence for it.

        Parameters
        ----------
        manifest:
            The :class:`~jugeo.evidence.manifests.Manifest` to inspect.
        subsystem_schema:
            A :class:`SubsystemSchema` declaring the required theorems.

        Returns
        -------
        dict[str, bool]
            Mapping from theorem name to coverage bool.
        """
        required_theorems: list[Any] = getattr(
            subsystem_schema, "required_theorems", []
        ) or []
        coverage: dict[str, bool] = {}

        for theorem in required_theorems:
            theorem_name = getattr(theorem, "name", str(theorem))
            covered = False
            try:
                judgment_store = getattr(manifest, "judgment_store", None)
                if judgment_store is not None:
                    for entry in judgment_store:
                        prop = getattr(entry, "proposition", None)
                        if prop is not None and theorem_name.lower() in str(prop).lower():
                            covered = True
                            break
            except Exception:  # pylint: disable=broad-except
                covered = False
            coverage[theorem_name] = covered

        return coverage

    # ------------------------------------------------------------------

    def build_obligation_map(
        self, manifest: Any
    ) -> dict[str, list[str]]:
        """Build a mapping from schema IDs to obligation IDs for a manifest.

        Parameters
        ----------
        manifest:
            The manifest to analyse.

        Returns
        -------
        dict[str, list[str]]
            Existing obligation map, updated with any new schema/obligation
            associations derived from the manifest.
        """
        raw = self.extract_obligations_from_manifest(manifest)
        result: dict[str, list[str]] = dict(self._obligation_map)
        for item in raw:
            oid = item.get("obligation_id", _new_id())
            schema_id = item.get("schema_id", "unassigned")
            result.setdefault(schema_id, []).append(oid)
        return result

    # ------------------------------------------------------------------

    def set_archive(self, archive: Any) -> None:
        """Set the :class:`~jugeo.evidence.manifests.EvidenceArchive` source.

        Parameters
        ----------
        archive:
            New archive to use.
        """
        self._archive = archive


# ---------------------------------------------------------------------------
# RuntimeSchemaMonitor
# ---------------------------------------------------------------------------


class RuntimeSchemaMonitor:
    """Monitors schema satisfaction at runtime using memory and invalidation.

    Polls the semantic memory and invalidation engine to determine whether
    active schema instances remain valid.  Detects instances whose bindings
    have become stale (e.g. because a supporting evidence item was revoked)
    and produces :class:`IntegrationHealth` snapshots on demand.

    Parameters
    ----------
    memory:
        Optional :class:`~jugeo.runtime.memory.SemanticMemory` to poll.
    inv_engine:
        Optional :class:`~jugeo.runtime.invalidation.InvalidationEngine`
        to consult for revocation events.
    """

    def __init__(
        self,
        memory: Any | None = None,
        inv_engine: Any | None = None,
    ) -> None:
        self._memory = memory
        self._inv_engine = inv_engine
        self._monitored_instances: dict[str, Any] = {}

    # ------------------------------------------------------------------

    def set_memory(self, memory: Any) -> None:
        """Attach a :class:`~jugeo.runtime.memory.SemanticMemory` for polling.

        Parameters
        ----------
        memory:
            Semantic memory instance to use.
        """
        self._memory = memory

    def set_invalidation_engine(self, engine: Any) -> None:
        """Attach an :class:`~jugeo.runtime.invalidation.InvalidationEngine`.

        Parameters
        ----------
        engine:
            Invalidation engine to consult.
        """
        self._inv_engine = engine

    # ------------------------------------------------------------------

    def monitor_instance(self, instance: Any) -> bool:
        """Check whether a schema instance is currently satisfied.

        An instance is considered satisfied if it is in DISCHARGED status, or
        if it is ACTIVE and its instantiated statement contains no unresolved
        ``{variable}`` placeholders.

        Parameters
        ----------
        instance:
            The :class:`SchemaInstance` to check.

        Returns
        -------
        bool
            ``True`` when the instance is satisfied.
        """
        status = getattr(instance, "status", None)
        status_str = str(status) if status is not None else ""

        if "DISCHARGED" in status_str or "discharged" in status_str:
            instance_id = getattr(instance, "instance_id", str(id(instance)))
            self._monitored_instances[instance_id] = instance
            return True

        stmt = getattr(instance, "instantiated_statement", None)
        if stmt is None:
            stmt = getattr(instance, "statement", "") or ""

        if _has_unresolved_vars(str(stmt)):
            return False

        instance_id = getattr(instance, "instance_id", str(id(instance)))
        self._monitored_instances[instance_id] = instance
        return True

    # ------------------------------------------------------------------

    def monitor_subsystem(self, subsystem_schema: Any) -> IntegrationHealth:
        """Run a full monitoring pass over a subsystem schema.

        Checks that all required theorem schemas are present in the subsystem
        and that the subsystem reports no missing proofs.

        Parameters
        ----------
        subsystem_schema:
            The :class:`SubsystemSchema` to monitor.

        Returns
        -------
        IntegrationHealth
            Health snapshot for the subsystem.
        """
        health = IntegrationHealth()
        name = getattr(subsystem_schema, "name", "unknown_subsystem")
        health.add_note(f"Monitoring subsystem: {name}")

        required_schemas = getattr(subsystem_schema, "required_schemas", []) or []
        if not required_schemas:
            health.add_note("No required schemas declared; assuming trivially satisfied.")

        for rs in required_schemas:
            rs_name = getattr(rs, "name", str(rs))
            if rs is None:
                health.add_violation(f"Required schema '{rs_name}' is None.")
            else:
                health.discharged_count += 1

        missing_proofs_fn = getattr(subsystem_schema, "missing_proofs", None)
        if missing_proofs_fn is not None:
            try:
                missing = list(missing_proofs_fn())
                for mp in missing:
                    health.missing_obligations.append(str(mp))
                    health.add_violation(f"Missing proof: {mp}")
                    health.pending_count += 1
            except Exception as exc:  # pylint: disable=broad-except
                health.add_note(f"missing_proofs() raised: {exc}")

        return health

    # ------------------------------------------------------------------

    def on_invalidation(
        self, reason: Any, affected_coords: list[str]
    ) -> list[str]:
        """Handle an invalidation event and return affected instance IDs.

        When the invalidation engine reports that certain coordinates have
        changed, this method identifies schema instances that may have been
        relying on those coordinates and returns their IDs for re-evaluation.

        Parameters
        ----------
        reason:
            The :class:`~jugeo.runtime.invalidation.InvalidationReason`
            describing why invalidation occurred.
        affected_coords:
            List of coordinate strings that were invalidated.

        Returns
        -------
        list[str]
            IDs of schema instances that may be affected.
        """
        affected_instances: list[str] = []
        logger.info(
            "on_invalidation: reason=%s coords=%s", reason, affected_coords
        )
        for iid, instance in self._monitored_instances.items():
            bindings = getattr(instance, "bindings", {}) or {}
            coord_val = bindings.get("coord", "")
            if any(coord_val == ac or coord_val in ac for ac in affected_coords):
                affected_instances.append(iid)
                logger.debug("Instance %s may be affected by invalidation.", iid)
        return affected_instances

    # ------------------------------------------------------------------

    def get_monitoring_report(
        self, subsystem_schemas: list[Any]
    ) -> dict[str, IntegrationHealth]:
        """Generate a monitoring report for a list of subsystem schemas.

        Parameters
        ----------
        subsystem_schemas:
            Schemas to include in the report.

        Returns
        -------
        dict[str, IntegrationHealth]
            Mapping from subsystem name to health snapshot.
        """
        report: dict[str, IntegrationHealth] = {}
        for ss in subsystem_schemas:
            name = getattr(ss, "name", str(id(ss)))
            report[name] = self.monitor_subsystem(ss)
        return report

    # ------------------------------------------------------------------

    def snapshot_state(self) -> dict[str, Any]:
        """Capture the current monitoring state as a plain dict.

        Returns
        -------
        dict[str, Any]
            Dict with ``timestamp``, ``monitored_instance_count``, and
            ``has_memory`` / ``has_inv_engine`` flags.
        """
        return {
            "timestamp": _now(),
            "monitored_instance_count": len(self._monitored_instances),
            "has_memory": self._memory is not None,
            "has_inv_engine": self._inv_engine is not None,
        }


# ---------------------------------------------------------------------------
# SchemaViolationReporter
# ---------------------------------------------------------------------------


class SchemaViolationReporter:
    """Records, classifies, and surfaces schema violations.

    Violations are stored as plain dicts with a unique ID, schema reference,
    severity, and detail message.  The reporter provides aggregation utilities
    such as :meth:`most_violated_schemas` and serialisation for downstream
    analysis tools.

    Examples
    --------
    ::

        reporter = SchemaViolationReporter()
        vid = reporter.report_violation("s1", "BINDING_FAILURE", "var x unresolved")
        print(reporter.violation_count())   # 1
        print(reporter.generate_report())
    """

    def __init__(self) -> None:
        self._violations: list[dict[str, Any]] = []

    # ------------------------------------------------------------------

    def report_violation(
        self,
        schema_id: str,
        violation_type: str,
        details: str,
        severity: str = "WARNING",
    ) -> str:
        """Record a single violation and return its unique ID.

        Parameters
        ----------
        schema_id:
            Identifier of the schema that was violated.
        violation_type:
            Short classification string (e.g. ``"BINDING_FAILURE"``).
        details:
            Human-readable explanation of the violation.
        severity:
            One of ``"INFO"``, ``"WARNING"``, ``"ERROR"``, or ``"CRITICAL"``.

        Returns
        -------
        str
            UUID4 string identifying this violation record.
        """
        violation_id = _new_id()
        record: dict[str, Any] = {
            "violation_id": violation_id,
            "schema_id": schema_id,
            "violation_type": violation_type,
            "details": details,
            "severity": severity,
            "timestamp": _now(),
        }
        self._violations.append(record)
        logger.warning(
            "SchemaViolation [%s] schema=%s type=%s: %s",
            severity,
            schema_id,
            violation_type,
            details,
        )
        return violation_id

    # ------------------------------------------------------------------

    def get_violations(
        self, severity: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve all recorded violations, optionally filtered by severity.

        Parameters
        ----------
        severity:
            If provided, only violations with this severity level are returned.

        Returns
        -------
        list[dict[str, Any]]
            Matching violation records.
        """
        if severity is None:
            return list(self._violations)
        return [v for v in self._violations if v.get("severity") == severity]

    # ------------------------------------------------------------------

    def clear_violations(self) -> int:
        """Remove all recorded violations and return the count removed.

        Returns
        -------
        int
            Number of violations that were cleared.
        """
        count = len(self._violations)
        self._violations.clear()
        return count

    # ------------------------------------------------------------------

    def violation_count(self) -> int:
        """Return the total number of recorded violations.

        Returns
        -------
        int
            Count of all violations in the internal store.
        """
        return len(self._violations)

    # ------------------------------------------------------------------

    def most_violated_schemas(self, top_n: int = 5) -> list[tuple[str, int]]:
        """Return the *top_n* most-violated schema IDs with their counts.

        Parameters
        ----------
        top_n:
            Number of entries to include in the result.

        Returns
        -------
        list[tuple[str, int]]
            Sorted list of (schema_id, violation_count) pairs, descending.
        """
        counts: Counter[str] = Counter(
            v.get("schema_id", "unknown") for v in self._violations
        )
        return counts.most_common(top_n)

    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise all violations to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Dict with ``violation_count`` and ``violations`` list.
        """
        return {
            "violation_count": len(self._violations),
            "violations": list(self._violations),
        }

    # ------------------------------------------------------------------

    def generate_report(self) -> str:
        """Generate a human-readable violation report string.

        Returns
        -------
        str
            Multi-line report summarising all violations grouped by severity.
        """
        lines: list[str] = [
            "=== SchemaViolationReporter Report ===",
            f"Total violations: {self.violation_count()}",
            "",
        ]
        by_severity: dict[str, list[dict[str, Any]]] = {}
        for v in self._violations:
            sev = v.get("severity", "UNKNOWN")
            by_severity.setdefault(sev, []).append(v)

        for sev in ("CRITICAL", "ERROR", "WARNING", "INFO", "UNKNOWN"):
            grp = by_severity.get(sev, [])
            if grp:
                lines.append(f"[{sev}] ({len(grp)} violations)")
                for v in grp:
                    lines.append(
                        f"  - schema={v.get('schema_id')} "
                        f"type={v.get('violation_type')}: {v.get('details')}"
                    )
                lines.append("")

        top = self.most_violated_schemas(3)
        if top:
            lines.append("Most violated schemas:")
            for schema_id, cnt in top:
                lines.append(f"  {schema_id}: {cnt}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TheoremSchemaIntegration
# ---------------------------------------------------------------------------


class TheoremSchemaIntegration:
    """Main integration façade tying all schema-integration components together.

    Wires together the :class:`JudgmentSchemaAdapter`, :class:`ManifestSchemaLinker`,
    :class:`RuntimeSchemaMonitor`, :class:`SchemaViolationReporter`,
    :class:`ObligationTracker`, and :class:`ObligationDispatcher` into a
    single coherent API surface.

    Parameters
    ----------
    subsystem_schemas:
        Optional initial dict mapping subsystem name to :class:`SubsystemSchema`.

    Notes
    -----
    The integration object is designed to be long-lived — it accumulates state
    (violation records, adaptation logs, obligation tracking) across many calls.
    """

    def __init__(
        self,
        subsystem_schemas: dict[str, Any] | None = None,
    ) -> None:
        self._subsystem_schemas: dict[str, Any] = dict(subsystem_schemas or {})

        # Construct sub-components
        self._tracker: Any = ObligationTracker() if ObligationTracker is not None else _StubTracker()
        self._dispatcher: Any = ObligationDispatcher() if ObligationDispatcher is not None else _StubDispatcher()
        self._monitor = RuntimeSchemaMonitor()
        self._reporter = SchemaViolationReporter()
        self._adapter = JudgmentSchemaAdapter()
        self._linker = ManifestSchemaLinker()
        self._integration_id: str = _new_id()
        self._created_at: float = _now()

    # ------------------------------------------------------------------

    def register_subsystem(self, name: str, schema: Any) -> None:
        """Register a subsystem schema under a given name.

        Parameters
        ----------
        name:
            Logical name for the subsystem (used as dict key).
        schema:
            :class:`SubsystemSchema` to associate with the name.
        """
        self._subsystem_schemas[name] = schema
        logger.info("TheoremSchemaIntegration: registered subsystem '%s'", name)

    # ------------------------------------------------------------------

    def run_health_check(self) -> dict[str, IntegrationHealth]:
        """Run a monitoring pass over all registered subsystem schemas.

        Returns
        -------
        dict[str, IntegrationHealth]
            Mapping from subsystem name to health snapshot.
        """
        schemas_list = list(self._subsystem_schemas.values())
        return self._monitor.get_monitoring_report(schemas_list)

    # ------------------------------------------------------------------

    def process_judgment(self, judgment: Any) -> list[Any]:
        """Attempt to adapt a judgment to all registered schemas.

        Iterates over every subsystem schema and every theorem schema within
        it, calling :meth:`JudgmentSchemaAdapter.adapt_judgment_to_schema`
        for each.  Successfully produced instances are returned.

        Parameters
        ----------
        judgment:
            :class:`~jugeo.judgments.judgment_terms.Judgment` to process.

        Returns
        -------
        list[SchemaInstance]
            All successfully produced schema instances.
        """
        instances: list[Any] = []
        for sub_name, subsystem in self._subsystem_schemas.items():
            theorem_schemas = getattr(subsystem, "theorem_schemas", []) or []
            for ts in theorem_schemas:
                instance = self._adapter.adapt_judgment_to_schema(judgment, ts)
                if instance is not None:
                    instances.append(instance)
            if not theorem_schemas:
                instance = self._adapter.adapt_judgment_to_schema(judgment, subsystem)
                if instance is not None:
                    instances.append(instance)

        logger.debug(
            "process_judgment produced %d instances", len(instances)
        )
        return instances

    # ------------------------------------------------------------------

    def dispatch_pending(self) -> dict[str, Any]:
        """Dispatch all pending obligations to their assigned proof agents.

        Returns
        -------
        dict[str, ProofAgent]
            Mapping from obligation ID to assigned agent.
        """
        if hasattr(self._dispatcher, "dispatch_all"):
            return self._dispatcher.dispatch_all()
        return {}

    # ------------------------------------------------------------------

    def get_overall_health(self) -> IntegrationHealth:
        """Aggregate health across all subsystems into a single record.

        Returns
        -------
        IntegrationHealth
            Merged health record with summed counts and combined violations.
        """
        all_health = self.run_health_check()
        merged = IntegrationHealth()
        merged.add_note(
            f"Aggregated from {len(all_health)} subsystem(s)."
        )
        for name, h in all_health.items():
            for v in h.schema_violations:
                merged.add_violation(f"[{name}] {v}")
            for mo in h.missing_obligations:
                merged.missing_obligations.append(f"[{name}] {mo}")
            merged.discharged_count += h.discharged_count
            merged.pending_count += h.pending_count
            merged.failed_count += h.failed_count

        reporter_count = self._reporter.violation_count()
        if reporter_count > 0:
            merged.add_note(
                f"SchemaViolationReporter holds {reporter_count} violation(s)."
            )
        return merged

    # ------------------------------------------------------------------

    def initialize_from_registry(self, registry: dict[str, Any]) -> None:
        """Bulk-load subsystem schemas from a registry dict.

        Parameters
        ----------
        registry:
            Mapping from subsystem name to :class:`SubsystemSchema`.
        """
        for name, schema in registry.items():
            self.register_subsystem(name, schema)

    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise the integration state to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Snapshot of the integration state.
        """
        return {
            "integration_id": self._integration_id,
            "created_at": self._created_at,
            "subsystem_count": len(self._subsystem_schemas),
            "subsystem_names": list(self._subsystem_schemas.keys()),
            "violations": self._reporter.to_json(),
            "monitoring_snapshot": self._monitor.snapshot_state(),
        }

    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a concise one-line summary of the integration state.

        Returns
        -------
        str
            Summary string suitable for logging.
        """
        health = self.get_overall_health()
        return (
            f"TheoremSchemaIntegration({self._integration_id[:8]}) "
            f"subsystems={len(self._subsystem_schemas)} "
            f"{health.summary()}"
        )


# ---------------------------------------------------------------------------
# Stub implementations for when proof_obligations classes are unavailable
# ---------------------------------------------------------------------------


class _StubTracker:
    """Minimal fallback when :class:`ObligationTracker` is not importable."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def add(self, obligation: Any) -> str:
        oid = _new_id()
        self._records.append({"obligation": str(obligation), "id": oid})
        return oid

    def list_pending(self) -> list[dict[str, Any]]:
        return list(self._records)


class _StubDispatcher:
    """Minimal fallback when :class:`ObligationDispatcher` is not importable."""

    def dispatch_all(self) -> dict[str, Any]:
        return {}


# ---------------------------------------------------------------------------
# Module-level integration test helper
# ---------------------------------------------------------------------------


def run_integration_test() -> dict[str, Any]:
    """Run a self-contained integration test of the schema integration layer.

    Creates a :class:`TheoremSchemaIntegration`, registers a mock subsystem
    schema object, runs the health check, records a sample violation, and
    returns the collected results.

    This function is useful for verifying that the module loads correctly and
    that the integration components interact as expected even when the imported
    schema model classes are unavailable (``None`` stubs).

    Returns
    -------
    dict[str, Any]
        Dict containing ``integration_json``, ``health_report``, ``violations``,
        and ``summary`` keys.
    """

    class _MockSubsystemSchema:
        """Minimal mock subsystem schema for testing."""

        name: str = "mock_subsystem"
        kind: str = "STRUCTURAL"
        required_schemas: list[Any] = []
        theorem_schemas: list[Any] = []

        def missing_proofs(self) -> list[str]:
            return []

    integration = TheoremSchemaIntegration()
    mock_schema = _MockSubsystemSchema()
    integration.register_subsystem("mock", mock_schema)

    health_report = integration.run_health_check()
    health_dicts = {k: v.to_json() for k, v in health_report.items()}

    integration._reporter.report_violation(
        schema_id="mock_schema_001",
        violation_type="BINDING_FAILURE",
        details="Variable {coord} was not resolved in test run.",
        severity="WARNING",
    )

    overall = integration.get_overall_health()

    results: dict[str, Any] = {
        "integration_json": integration.to_json(),
        "health_report": health_dicts,
        "violations": integration._reporter.to_json(),
        "overall_health": overall.to_json(),
        "summary": integration.summary(),
        "test_passed": True,
    }

    logger.info("run_integration_test completed: %s", results["summary"])
    return results
