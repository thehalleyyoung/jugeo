"""
Integration of hypercover_treaties with cover_design, state_space, and orchestration.

This module provides the glue layer that connects the hypercover treaty system to the
broader jugeo pipeline.  A *treaty* is a bilateral agreement between two overlapping
patches of a cover that encodes the gluing constraints required for local sections to
extend to a global section.  Three external sub-systems interact with treaties:

  1. **cover_design** – generates or refines the cover itself; treaty constraints feed
     back as quality signals that guide boundary placement and patch sizing.
  2. **state_space** – maintains the runtime state of each patch; treaties track which
     state invariants must hold at boundaries.
  3. **orchestration** – schedules computation across patches; the orchestrator needs
     to know when treaties are formed, violated, or expire so that it can reschedule
     dependent tasks.

The integration layer is deliberately thin: it does *not* re-implement treaty logic
(that lives in :mod:`jugeo.generation.hypercover_treaties.core`) but instead provides
adapters, registries, bridges, and event buses that let those three sub-systems share
information in a disciplined way.

Design principles
-----------------
* **Immutability at the boundary**: all data crossing a bridge is serialised to plain
  ``dict`` / ``frozenset`` / ``tuple`` before being handed off so that receivers cannot
  mutate the treaty's internal state.
* **Trust algebra**: every constraint or notification carries a :class:`TrustTier` so
  that downstream consumers can apply appropriate scepticism.
* **Structured failures**: errors are raised via ``raise_with_scope`` (or its stub) so
  that the caller always receives a machine-readable failure code.
* **Idempotency**: registering the same treaty twice, or pushing the same constraint
  twice, is a no-op that logs a warning rather than raising.

    # copilot: integration of hypercover_treaties with cover_design, state_space, and orchestration

Attributes
----------
MAX_REGISTRY_SIZE : int
    Hard cap on the number of treaties stored in a single :class:`TreatyRegistry`.
NOTIFICATION_TIMEOUT_S : float
    Seconds the orchestrator bridge will wait for an acknowledgement before giving up.
INTEGRATION_VERSION : str
    Semantic version of this integration layer; bump on any breaking interface change.
EVENT_TREATY_FORMED : str
    Event type emitted when a new treaty is successfully registered.
EVENT_TREATY_VIOLATED : str
    Event type emitted when a runtime check detects a treaty violation.
EVENT_TREATY_EXPIRED : str
    Event type emitted when a treaty's TTL elapses without renewal.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import abc
import collections
import datetime
import functools
import hashlib
import itertools
import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

# ---------------------------------------------------------------------------
# jugeo.errors – with fallback stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False

    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"

    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"

    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]

    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message

    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

# ---------------------------------------------------------------------------
# jugeo.judgments.judgment_terms – with fallback stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind,
        JudgmentStatus,
        PropositionKind,
        ProvenanceSource,
        TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"

    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
MAX_REGISTRY_SIZE: int = 1000
"""Hard cap on treaties per :class:`TreatyRegistry` instance."""

NOTIFICATION_TIMEOUT_S: float = 5.0
"""Seconds the orchestrator bridge waits before abandoning an ACK wait."""

INTEGRATION_VERSION: str = "1.0.0"
"""Semantic version of this integration layer."""

EVENT_TREATY_FORMED: str = "treaty.formed"
"""Fired when a treaty is successfully created and stored."""

EVENT_TREATY_VIOLATED: str = "treaty.violated"
"""Fired when a runtime check reveals a gluing constraint mismatch."""

EVENT_TREATY_EXPIRED: str = "treaty.expired"
"""Fired when a treaty's TTL elapses without renewal."""

_SENTINEL: object = object()
"""Module-private sentinel used to distinguish *not provided* from ``None``."""

# ---------------------------------------------------------------------------
# Trust algebra
# ---------------------------------------------------------------------------
class TrustTier(IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float.

    The five tiers form a totally-ordered lattice.  ``join`` (⊕) takes the
    higher tier; ``meet`` (⊖) takes the lower.  ``promote`` (↑_π) increments
    by one level up to the maximum; ``demote`` (↓_χ) decrements by one level
    down to the minimum.

    These operations are useful when combining evidence from multiple sources:
    a constraint that is simultaneously runtime-witnessed and solver-backed
    should be elevated to ``PROOF_BACKED`` via repeated ``join`` calls.

    Examples
    --------
    >>> TrustTier.REVIEWED.join(TrustTier.VERIFIED)
    <TrustTier.VERIFIED: 3>
    >>> TrustTier.PROOF_BACKED.promote()
    <TrustTier.PROOF_BACKED: 5>
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Return the least upper bound (supremum) of *self* and *other*."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Return the greatest lower bound (infimum) of *self* and *other*."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Increment trust by one tier, capped at :attr:`PROOF_BACKED`."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """Decrement trust by one tier, floored at :attr:`PROPOSAL`."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))


# ---------------------------------------------------------------------------
# Mandatory dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Every assertion produced or consumed by the integration layer is packaged
    as a :class:`Judgment` so that its epistemic status is always explicit.

    Parameters
    ----------
    context:
        The context in which the formula is evaluated (e.g. a patch identifier
        or a cover configuration snapshot).
    formula:
        The proposition being judged.  May be a string, a structured object, or
        any hashable value.
    assumptions:
        Tuple of auxiliary assumptions on which the judgment depends.
    evidence:
        Tuple of evidence items (e.g. solver certificates, runtime witnesses).
    obligations:
        Tuple of proof obligations that remain to be discharged before the
        judgment can be elevated.
    burden:
        A description of the burden-of-proof rule in force.
    trust:
        The current :class:`TrustTier` of the judgment.
    provenance:
        Machine-readable record of how the judgment was created.
    """

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


@dataclass(frozen=True)
class CechObstruction:
    """A Čech cohomology obstruction to gluing local sections.

    When local sections on overlapping patches *cannot* be glued to a global
    section, there exists a non-trivial Čech 1-cocycle witnessing the failure.
    This dataclass records that witness together with its cohomology class and a
    human-readable description.

    Parameters
    ----------
    cover_id:
        Identifier of the cover on which the obstruction was detected.
    cocycle:
        Frozen set of ``(patch_i, patch_j, section_value)`` triples forming
        the offending 1-cocycle.
    cohomology_class:
        String label of the Čech cohomology class (e.g. ``"H^1(U, F)[3]"``).
    description:
        Human-readable summary of the obstruction and its consequences.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return ``True`` iff the cocycle is empty (obstruction vanishes)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _new_id(prefix: str = "") -> str:
    """Generate a compact, collision-resistant identifier.

    Uses :func:`uuid.uuid4` (random UUID) and returns the first 12 hex
    characters, optionally prefixed.

    Parameters
    ----------
    prefix:
        Short string prepended to the identifier, separated by ``-``.

    Returns
    -------
    str
        An identifier of the form ``"<prefix>-<12hex>"`` or ``"<12hex>"`` when
        the prefix is empty.
    """
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}-{raw}" if prefix else raw


def _stable_hash(data: Any) -> str:
    """Return a stable SHA-256 hex digest of the string representation of *data*.

    This is intentionally **not** cryptographically secure; it is used only to
    produce deterministic short keys for caching and deduplication.

    Parameters
    ----------
    data:
        Any object whose ``repr`` is stable across interpreter restarts.

    Returns
    -------
    str
        First 16 characters of the SHA-256 hex digest.
    """
    payload = repr(data).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _validate_treaty_dict(treaty: dict) -> None:
    """Raise :class:`JuGeoError` if *treaty* is missing required keys.

    A valid treaty dict must contain at least ``"patch_a_id"``,
    ``"patch_b_id"``, and ``"constraints"``.

    Parameters
    ----------
    treaty:
        The candidate treaty mapping.

    Raises
    ------
    JuGeoError
        If any required key is absent or has an obviously wrong type.
    """
    required: Tuple[str, ...] = ("patch_a_id", "patch_b_id", "constraints")
    for key in required:
        if key not in treaty:
            raise_with_scope(
                "TREATY_VALIDATION_ERROR",
                message=f"Treaty dict missing required key '{key}'",
                provenance={"treaty_keys": list(treaty.keys())},
            )
    if not isinstance(treaty["constraints"], (list, dict, tuple)):
        raise_with_scope(
            "TREATY_CONSTRAINT_TYPE_ERROR",
            message=(
                f"Treaty 'constraints' must be list/dict/tuple, "
                f"got {type(treaty['constraints']).__name__}"
            ),
        )


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.utcnow().isoformat()


def _normalise_constraints(raw: Any) -> List[dict]:
    """Coerce *raw* constraints into a list of dicts.

    Constraints in a treaty dict may arrive as a ``list``, a single ``dict``,
    a ``tuple``, or anything else (logged as warning, treated as empty).

    Parameters
    ----------
    raw:
        The ``"constraints"`` value from a treaty dict.

    Returns
    -------
    list[dict]
        Normalised list of constraint mappings.
    """
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, tuple):
        return [c for c in raw if isinstance(c, dict)]
    logger.warning("_normalise_constraints: unexpected type %s, treating as empty", type(raw).__name__)
    return []


def _compute_boundary_quality(
    constraints: List[dict],
    *,
    base_quality: float = 0.5,
    constraint_weight: float = 0.1,
) -> float:
    """Heuristically estimate boundary quality from a list of constraints.

    The quality metric is a value in ``[0.0, 1.0]`` where 1.0 means the
    boundary is fully constrained and geometrically clean.

    Parameters
    ----------
    constraints:
        List of constraint dicts.
    base_quality:
        Prior quality estimate before constraints are considered.
    constraint_weight:
        Additive contribution per unit of (constraint × trust).

    Returns
    -------
    float
        Quality estimate in ``[0.0, 1.0]``.
    """
    if not constraints:
        return base_quality
    trust_values: List[float] = []
    for c in constraints:
        raw_trust = c.get("trust", TrustTier.PROPOSAL.value)
        try:
            tier = TrustTier(int(raw_trust))
            trust_values.append(tier.value / TrustTier.PROOF_BACKED.value)
        except (ValueError, KeyError):
            trust_values.append(0.2)
    mean_trust = math.fsum(trust_values) / len(trust_values)
    delta = constraint_weight * len(constraints) * mean_trust
    return min(1.0, base_quality + delta)


def _build_provenance(source: str, extra: Optional[dict] = None) -> dict:
    """Construct a structured provenance dict for a :class:`Judgment`.

    Parameters
    ----------
    source:
        Short identifier of the component producing the judgment.
    extra:
        Optional additional key–value pairs to merge into the provenance.

    Returns
    -------
    dict
        Provenance mapping with ``"source"``, ``"timestamp"``, and
        ``"integration_version"``.
    """
    prov: dict = {
        "source": source,
        "timestamp": _now_iso(),
        "integration_version": INTEGRATION_VERSION,
    }
    if extra:
        prov.update(extra)
    return prov


def _make_judgment_from_constraint(
    constraint: dict,
    *,
    context: Any = None,
    source: str = "integration",
) -> Judgment:
    """Construct a :class:`Judgment` from a constraint dict.

    Parameters
    ----------
    constraint:
        The constraint dict; should contain at least ``"kind"`` and
        ``"payload"`` keys.
    context:
        The context in which the constraint applies.
    source:
        Short label of the caller, used in the provenance record.

    Returns
    -------
    Judgment
        A frozen :class:`Judgment` encapsulating the constraint.
    """
    raw_trust = constraint.get("trust", TrustTier.PROPOSAL.value)
    try:
        tier = TrustTier(int(raw_trust))
    except (ValueError, TypeError):
        tier = TrustTier.PROPOSAL

    evidence_raw = constraint.get("evidence", [])
    evidence: Tuple[Any, ...] = tuple(evidence_raw) if isinstance(evidence_raw, (list, tuple)) else ()

    assumptions_raw = constraint.get("assumptions", [])
    assumptions: Tuple[Any, ...] = tuple(assumptions_raw) if isinstance(assumptions_raw, (list, tuple)) else ()

    formula = constraint.get("payload", constraint.get("kind", "unknown_constraint"))
    provenance = _build_provenance(source, {"constraint_hash": _stable_hash(constraint)})

    return Judgment(
        context=context,
        formula=formula,
        assumptions=assumptions,
        evidence=evidence,
        obligations=(),
        burden="integration-layer-default",
        trust=tier,
        provenance=provenance,
    )


def _check_registry_capacity(registry_size: int, max_size: int = MAX_REGISTRY_SIZE) -> None:
    """Raise if the registry is at or beyond capacity.

    Parameters
    ----------
    registry_size:
        Current number of entries in the registry.
    max_size:
        Maximum allowed entries.

    Raises
    ------
    JuGeoError
        If ``registry_size >= max_size``.
    """
    if registry_size >= max_size:
        raise_with_scope(
            "REGISTRY_CAPACITY_EXCEEDED",
            message=(
                f"TreatyRegistry has reached its capacity of {max_size} treaties. "
                "Remove stale treaties before registering new ones."
            ),
        )


def _event_envelope(event_type: str, data: dict, *, source: str = "integration") -> dict:
    """Wrap *data* in a standard event envelope.

    Parameters
    ----------
    event_type:
        One of the ``EVENT_*`` constants defined in this module.
    data:
        Arbitrary payload to include in the envelope.
    source:
        Identifier of the emitting component.

    Returns
    -------
    dict
        Envelope with ``"event_type"``, ``"source"``, ``"timestamp"``,
        ``"integration_version"``, and ``"payload"`` keys.
    """
    return {
        "event_type": event_type,
        "source": source,
        "timestamp": _now_iso(),
        "integration_version": INTEGRATION_VERSION,
        "payload": data,
    }


def _dedup_constraints(constraints: List[dict]) -> List[dict]:
    """Remove duplicate constraints from a list, preserving insertion order.

    Parameters
    ----------
    constraints:
        Input list (may contain duplicates).

    Returns
    -------
    list[dict]
        Deduplicated list in insertion order.
    """
    seen: Set[str] = set()
    result: List[dict] = []
    for c in constraints:
        h = _stable_hash(c)
        if h not in seen:
            seen.add(h)
            result.append(c)
    return result


def _patch_pair_key(patch_a_id: str, patch_b_id: str) -> str:
    """Return a canonical key for an unordered patch pair.

    The key is independent of argument order so that
    ``_patch_pair_key("A", "B") == _patch_pair_key("B", "A")``.

    Parameters
    ----------
    patch_a_id:
        Identifier of the first patch.
    patch_b_id:
        Identifier of the second patch.

    Returns
    -------
    str
        Canonical key string.
    """
    a, b = sorted([patch_a_id, patch_b_id])
    return f"{a}::{b}"


# ---------------------------------------------------------------------------
# Core classes
# ---------------------------------------------------------------------------

class TreatyRegistry:
    """Stores all active treaties indexed by patch pair.

    :class:`TreatyRegistry` acts as the single source of truth for which
    treaties are currently in force.  Every treaty is identified by a unique
    ``treaty_id`` (assigned on registration if not already present) and can be
    looked up either by that ID or by the unordered pair of patch identifiers it
    governs.

    Parameters
    ----------
    registry_id:
        Human-readable label for this registry instance.

    Attributes
    ----------
    registry_id : str
    treaties : dict[str, dict]
    index_by_patch : dict[str, str]
    version : int
    """

    def __init__(self, registry_id: str = "") -> None:
        self.registry_id: str = registry_id or _new_id("registry")
        self.treaties: Dict[str, dict] = {}
        self.index_by_patch: Dict[str, str] = {}
        self.version: int = 0
        logger.debug("TreatyRegistry %s created", self.registry_id)

    def register(self, treaty: dict) -> str:
        """Store a new treaty and return its assigned ``treaty_id``.

        If the treaty is not yet tracked by this instance it is added.  If a
        treaty for the same patch pair already exists, the existing ID is
        returned (idempotency).

        Parameters
        ----------
        treaty:
            Treaty dict.  Must contain ``"patch_a_id"``, ``"patch_b_id"``, and
            ``"constraints"``.

        Returns
        -------
        str
            The ``treaty_id`` under which the treaty is stored.

        Raises
        ------
        JuGeoError
            If *treaty* fails validation or the registry is at capacity.
        """
        _validate_treaty_dict(treaty)
        pair_key = _patch_pair_key(treaty["patch_a_id"], treaty["patch_b_id"])
        if pair_key in self.index_by_patch:
            existing_id = self.index_by_patch[pair_key]
            logger.info(
                "TreatyRegistry.register: pair %s already registered as %s – skipping",
                pair_key, existing_id,
            )
            return existing_id

        _check_registry_capacity(len(self.treaties))
        treaty_id: str = treaty.get("treaty_id") or _new_id("treaty")
        stored = dict(treaty)
        stored["treaty_id"] = treaty_id
        stored.setdefault("registered_at", _now_iso())
        stored["constraints"] = _normalise_constraints(stored["constraints"])
        self.treaties[treaty_id] = stored
        self.index_by_patch[pair_key] = treaty_id
        self.bump_version()
        logger.info("TreatyRegistry.register: stored treaty %s for pair %s", treaty_id, pair_key)
        return treaty_id

    def lookup(self, patch_a_id: str, patch_b_id: str) -> Optional[dict]:
        """Return the treaty governing *patch_a_id* and *patch_b_id*, or ``None``.

        Parameters
        ----------
        patch_a_id:
            Identifier of the first patch.
        patch_b_id:
            Identifier of the second patch.

        Returns
        -------
        dict or None
        """
        pair_key = _patch_pair_key(patch_a_id, patch_b_id)
        treaty_id = self.index_by_patch.get(pair_key)
        if treaty_id is None:
            return None
        stored = self.treaties.get(treaty_id)
        return dict(stored) if stored is not None else None

    def list_all(self) -> List[dict]:
        """Return shallow copies of all stored treaties."""
        return [dict(t) for t in self.treaties.values()]

    def remove(self, treaty_id: str) -> bool:
        """Remove a treaty by its ID.

        Parameters
        ----------
        treaty_id:
            The ID of the treaty to remove.

        Returns
        -------
        bool
            ``True`` if the treaty existed and was removed, ``False`` otherwise.
        """
        treaty = self.treaties.pop(treaty_id, None)
        if treaty is None:
            logger.warning("TreatyRegistry.remove: treaty_id %s not found", treaty_id)
            return False
        pair_key = _patch_pair_key(treaty["patch_a_id"], treaty["patch_b_id"])
        self.index_by_patch.pop(pair_key, None)
        self.bump_version()
        logger.info("TreatyRegistry.remove: removed treaty %s", treaty_id)
        return True

    def bump_version(self) -> int:
        """Increment and return the version counter."""
        self.version += 1
        return self.version

    def __repr__(self) -> str:
        return (
            f"TreatyRegistry(id={self.registry_id!r}, "
            f"count={len(self.treaties)}, version={self.version})"
        )


class CoverDesignBridge:
    """Feeds treaty constraints back to cover_design to improve boundary quality.

    Constraints accumulate in :attr:`pending_constraints` and are moved to
    :attr:`applied_constraints` when :meth:`apply_pending` is called.

    Parameters
    ----------
    bridge_id:
        Human-readable identifier for this bridge instance.
    cover_design_config:
        Configuration dict to pass to cover_design when applying constraints.

    Attributes
    ----------
    bridge_id : str
    cover_design_config : dict
    pending_constraints : list[dict]
    applied_constraints : list[dict]
    """

    def __init__(
        self,
        bridge_id: str = "",
        cover_design_config: Optional[dict] = None,
    ) -> None:
        self.bridge_id: str = bridge_id or _new_id("cdb")
        self.cover_design_config: dict = cover_design_config or {}
        self.pending_constraints: List[dict] = []
        self.applied_constraints: List[dict] = []
        logger.debug("CoverDesignBridge %s created", self.bridge_id)

    def push_constraint(self, treaty_id: str, constraint: dict) -> None:
        """Enqueue a constraint from *treaty_id* to be applied to cover_design.

        Duplicates (same stable hash) are silently dropped.

        Parameters
        ----------
        treaty_id:
            The treaty that produced the constraint.
        constraint:
            The constraint dict.
        """
        enriched = dict(constraint)
        enriched.setdefault("treaty_id", treaty_id)
        enriched.setdefault("pushed_at", _now_iso())
        existing_hashes = {_stable_hash(c) for c in self.pending_constraints}
        if _stable_hash(enriched) in existing_hashes:
            logger.debug(
                "CoverDesignBridge.push_constraint: duplicate constraint from %s – skipped",
                treaty_id,
            )
            return
        self.pending_constraints.append(enriched)
        logger.debug(
            "CoverDesignBridge.push_constraint: enqueued constraint from treaty %s (pending=%d)",
            treaty_id, len(self.pending_constraints),
        )

    def apply_pending(self) -> List[dict]:
        """Move all pending constraints to applied and return them.

        Returns
        -------
        list[dict]
            The constraints that were applied in this call.
        """
        if not self.pending_constraints:
            logger.debug("CoverDesignBridge.apply_pending: nothing pending")
            return []
        batch = _dedup_constraints(list(self.pending_constraints))
        self.pending_constraints.clear()
        for c in batch:
            c["applied_at"] = _now_iso()
            c["config_snapshot"] = dict(self.cover_design_config)
            self.applied_constraints.append(c)
        logger.info(
            "CoverDesignBridge.apply_pending: applied %d constraints (total applied=%d)",
            len(batch), len(self.applied_constraints),
        )
        return batch

    def get_boundary_quality(self, patch_id: str) -> float:
        """Estimate the boundary quality for *patch_id* from applied constraints.

        Parameters
        ----------
        patch_id:
            The patch whose boundary quality is being queried.

        Returns
        -------
        float
            Quality estimate in ``[0.0, 1.0]``.
        """
        relevant = [
            c for c in self.applied_constraints
            if c.get("patch_a_id") == patch_id or c.get("patch_b_id") == patch_id
        ]
        q = _compute_boundary_quality(relevant)
        logger.debug(
            "CoverDesignBridge.get_boundary_quality: patch=%s quality=%.3f (from %d constraints)",
            patch_id, q, len(relevant),
        )
        return q

    def to_judgment(self, constraint: dict) -> Judgment:
        """Convert a constraint dict to a :class:`Judgment`.

        Parameters
        ----------
        constraint:
            The constraint to lift into the judgment lattice.

        Returns
        -------
        Judgment
            The resulting frozen judgment.
        """
        return _make_judgment_from_constraint(
            constraint,
            context={"bridge_id": self.bridge_id, "config": self.cover_design_config},
            source="CoverDesignBridge",
        )

    def __repr__(self) -> str:
        return (
            f"CoverDesignBridge(id={self.bridge_id!r}, "
            f"pending={len(self.pending_constraints)}, "
            f"applied={len(self.applied_constraints)})"
        )


class OrchestratorTreatyBridge:
    """Notifies the orchestrator when treaties are formed or violated.

    Notifications accumulate in :attr:`pending_notifications` and are sent
    (flushed) as a batch when :meth:`flush_notifications` is called.

    Parameters
    ----------
    bridge_id:
        Human-readable identifier.
    orchestrator_endpoint:
        URL or queue name of the orchestrator.

    Attributes
    ----------
    bridge_id : str
    orchestrator_endpoint : str
    notification_log : list[dict]
    pending_notifications : list[dict]
    """

    def __init__(
        self,
        bridge_id: str = "",
        orchestrator_endpoint: str = "local://orchestrator",
    ) -> None:
        self.bridge_id: str = bridge_id or _new_id("orb")
        self.orchestrator_endpoint: str = orchestrator_endpoint
        self.notification_log: List[dict] = []
        self.pending_notifications: List[dict] = []
        logger.debug(
            "OrchestratorTreatyBridge %s created (endpoint=%s)",
            self.bridge_id, orchestrator_endpoint,
        )

    def notify_formation(self, treaty: dict) -> None:
        """Enqueue a :data:`EVENT_TREATY_FORMED` notification.

        Parameters
        ----------
        treaty:
            The newly formed treaty dict.
        """
        envelope = _event_envelope(
            EVENT_TREATY_FORMED,
            {"treaty_id": treaty.get("treaty_id"), "treaty": treaty},
            source=self.bridge_id,
        )
        self.pending_notifications.append(envelope)
        logger.info(
            "OrchestratorTreatyBridge.notify_formation: queued EVENT_TREATY_FORMED for treaty %s",
            treaty.get("treaty_id"),
        )

    def notify_violation(self, violation: dict) -> None:
        """Enqueue a :data:`EVENT_TREATY_VIOLATED` notification.

        Parameters
        ----------
        violation:
            Dict describing the violation.
        """
        envelope = _event_envelope(
            EVENT_TREATY_VIOLATED,
            violation,
            source=self.bridge_id,
        )
        self.pending_notifications.append(envelope)
        logger.warning(
            "OrchestratorTreatyBridge.notify_violation: queued EVENT_TREATY_VIOLATED for treaty %s",
            violation.get("treaty_id"),
        )

    def flush_notifications(self) -> List[dict]:
        """Send all pending notifications and return them.

        Returns
        -------
        list[dict]
            The notifications sent in this flush.
        """
        if not self.pending_notifications:
            return []
        batch = list(self.pending_notifications)
        self.pending_notifications.clear()
        for n in batch:
            n["flushed_at"] = _now_iso()
            n["endpoint"] = self.orchestrator_endpoint
            self.notification_log.append(n)
        logger.info(
            "OrchestratorTreatyBridge.flush_notifications: sent %d notification(s) to %s",
            len(batch), self.orchestrator_endpoint,
        )
        return batch

    def get_log(self) -> List[dict]:
        """Return a copy of the full notification log."""
        return [dict(n) for n in self.notification_log]

    def __repr__(self) -> str:
        return (
            f"OrchestratorTreatyBridge(id={self.bridge_id!r}, "
            f"endpoint={self.orchestrator_endpoint!r}, "
            f"pending={len(self.pending_notifications)}, "
            f"log={len(self.notification_log)})"
        )


class TreatyIntegration:
    """Manages the connections between treaties and other modules.

    :class:`TreatyIntegration` acts as the event bus for the integration layer.

    Parameters
    ----------
    integration_id:
        Human-readable identifier.

    Attributes
    ----------
    integration_id : str
    active_treaties : dict[str, dict]
    constraint_registry : dict[str, list[dict]]
    event_log : list[dict]
    """

    def __init__(self, integration_id: str = "") -> None:
        self.integration_id: str = integration_id or _new_id("integration")
        self.active_treaties: Dict[str, dict] = {}
        self.constraint_registry: Dict[str, List[dict]] = collections.defaultdict(list)
        self.event_log: List[dict] = []
        self._connections: Dict[str, List[str]] = collections.defaultdict(list)
        logger.debug("TreatyIntegration %s created", self.integration_id)

    def connect(self, treaty: dict, module: str) -> bool:
        """Connect *module* to *treaty* so it receives future events.

        Parameters
        ----------
        treaty:
            The treaty dict (must contain ``"treaty_id"``).
        module:
            Name of the module to connect.

        Returns
        -------
        bool
            ``True`` if the connection was newly established.
        """
        treaty_id = treaty.get("treaty_id")
        if not treaty_id:
            raise_with_scope(
                "CONNECT_MISSING_TREATY_ID",
                message="treaty dict must contain 'treaty_id' before connecting modules",
            )
        if treaty_id not in self.active_treaties:
            self.active_treaties[treaty_id] = dict(treaty)
        if module in self._connections[treaty_id]:
            return False
        self._connections[treaty_id].append(module)
        self.emit_event(
            "module.connected",
            {"treaty_id": treaty_id, "module": module},
        )
        logger.info("TreatyIntegration.connect: connected %r → treaty %s", module, treaty_id)
        return True

    def disconnect(self, treaty_id: str) -> bool:
        """Remove *treaty_id* and all its module connections.

        Parameters
        ----------
        treaty_id:
            The treaty to disconnect.

        Returns
        -------
        bool
            ``True`` if the treaty existed.
        """
        if treaty_id not in self.active_treaties:
            logger.warning("TreatyIntegration.disconnect: treaty_id %s not found", treaty_id)
            return False
        self.active_treaties.pop(treaty_id, None)
        removed_modules = self._connections.pop(treaty_id, [])
        self.emit_event(
            "module.disconnected",
            {"treaty_id": treaty_id, "removed_modules": removed_modules},
        )
        logger.info(
            "TreatyIntegration.disconnect: disconnected treaty %s (modules removed: %s)",
            treaty_id, removed_modules,
        )
        return True

    def get_connections(self, treaty_id: str) -> List[str]:
        """Return the list of module names connected to *treaty_id*.

        Parameters
        ----------
        treaty_id:
            The treaty to query.

        Returns
        -------
        list[str]
        """
        return list(self._connections.get(treaty_id, []))

    def emit_event(self, event: str, data: dict) -> None:
        """Record an event in :attr:`event_log`.

        Parameters
        ----------
        event:
            Event type string.
        data:
            Arbitrary payload dict.
        """
        envelope = _event_envelope(event, data, source=self.integration_id)
        self.event_log.append(envelope)
        logger.debug("TreatyIntegration.emit_event: %s – %s", event, data)

    def __repr__(self) -> str:
        return (
            f"TreatyIntegration(id={self.integration_id!r}, "
            f"treaties={len(self.active_treaties)}, "
            f"events={len(self.event_log)})"
        )


class IntegrationLayer:
    """The top-level integration manager.

    :class:`IntegrationLayer` owns one instance of each sub-component and
    provides a unified API for the rest of the pipeline.

    Parameters
    ----------
    layer_id:
        Human-readable identifier.

    Attributes
    ----------
    layer_id : str
    registry : TreatyRegistry
    cover_bridge : CoverDesignBridge
    orchestrator_bridge : OrchestratorTreatyBridge
    integration : TreatyIntegration
    """

    def __init__(self, layer_id: str = "") -> None:
        self.layer_id: str = layer_id or _new_id("layer")
        self.registry: TreatyRegistry = TreatyRegistry()
        self.cover_bridge: CoverDesignBridge = CoverDesignBridge()
        self.orchestrator_bridge: OrchestratorTreatyBridge = OrchestratorTreatyBridge()
        self.integration: TreatyIntegration = TreatyIntegration()
        self._configured: bool = False
        logger.debug("IntegrationLayer %s created", self.layer_id)

    def setup(self, config: dict) -> None:
        """Initialise the integration layer from *config*.

        Parameters
        ----------
        config:
            Configuration mapping.  Accepted keys: ``"orchestrator_endpoint"``,
            ``"cover_design_config"``.
        """
        if config.get("orchestrator_endpoint"):
            self.orchestrator_bridge.orchestrator_endpoint = config["orchestrator_endpoint"]
        if config.get("cover_design_config"):
            self.cover_bridge.cover_design_config = dict(config["cover_design_config"])
        self._configured = True
        self.integration.emit_event("layer.setup", {"config": config, "layer_id": self.layer_id})
        logger.info(
            "IntegrationLayer %s setup complete with config keys: %s",
            self.layer_id, list(config.keys()),
        )

    def teardown(self) -> None:
        """Flush pending notifications and log a teardown event.

        After :meth:`teardown` the layer should not be used.
        """
        flushed = self.orchestrator_bridge.flush_notifications()
        self.integration.emit_event(
            "layer.teardown", {"flushed": len(flushed), "layer_id": self.layer_id}
        )
        self._configured = False
        logger.info(
            "IntegrationLayer %s teardown (flushed %d notifications)",
            self.layer_id, len(flushed),
        )

    def process_treaty(self, treaty: dict) -> Judgment:
        """Register a treaty end-to-end and return a :class:`Judgment`.

        Full pipeline:
        1. Validate and register in :attr:`registry`.
        2. Connect ``"cover_design"``, ``"orchestrator"``, ``"state_space"``.
        3. Push all constraints to :attr:`cover_bridge`.
        4. Notify the orchestrator of formation.
        5. Return a judgment summarising the outcome.

        Parameters
        ----------
        treaty:
            The treaty dict to process.

        Returns
        -------
        Judgment
        """
        treaty_id = self.registry.register(treaty)
        stored = self.registry.treaties[treaty_id]

        self.integration.connect(stored, "cover_design")
        self.integration.connect(stored, "orchestrator")
        self.integration.connect(stored, "state_space")

        constraints = stored.get("constraints", [])
        for c in constraints:
            self.cover_bridge.push_constraint(treaty_id, c)
        applied = self.cover_bridge.apply_pending()

        self.orchestrator_bridge.notify_formation(stored)

        trust = functools.reduce(
            TrustTier.join,
            (
                TrustTier(min(max(c.get("trust", TrustTier.PROPOSAL.value), 1), 5))
                for c in constraints
            ),
            TrustTier.PROPOSAL,
        ) if constraints else TrustTier.PROPOSAL

        provenance = _build_provenance(
            "IntegrationLayer",
            {
                "treaty_id": treaty_id,
                "applied_constraints": len(applied),
                "layer_id": self.layer_id,
            },
        )
        judgment = Judgment(
            context={"layer_id": self.layer_id, "treaty_id": treaty_id},
            formula=f"treaty_processed({treaty_id})",
            assumptions=tuple(stored.get("assumptions", [])),
            evidence=tuple({"applied_constraint": c} for c in applied),
            obligations=(),
            burden="integration-layer",
            trust=trust,
            provenance=provenance,
        )
        self.integration.emit_event(
            EVENT_TREATY_FORMED,
            {"treaty_id": treaty_id, "trust": trust.name},
        )
        logger.info(
            "IntegrationLayer.process_treaty: treaty %s processed (trust=%s, constraints=%d)",
            treaty_id, trust.name, len(constraints),
        )
        return judgment

    def get_status(self) -> dict:
        """Return a snapshot of the integration layer's current state.

        Returns
        -------
        dict
        """
        return {
            "layer_id": self.layer_id,
            "configured": self._configured,
            "integration_version": INTEGRATION_VERSION,
            "registry": {
                "id": self.registry.registry_id,
                "treaty_count": len(self.registry.treaties),
                "version": self.registry.version,
            },
            "cover_bridge": {
                "id": self.cover_bridge.bridge_id,
                "pending": len(self.cover_bridge.pending_constraints),
                "applied": len(self.cover_bridge.applied_constraints),
            },
            "orchestrator_bridge": {
                "id": self.orchestrator_bridge.bridge_id,
                "endpoint": self.orchestrator_bridge.orchestrator_endpoint,
                "pending": len(self.orchestrator_bridge.pending_notifications),
                "log": len(self.orchestrator_bridge.notification_log),
            },
            "integration": {
                "id": self.integration.integration_id,
                "active_treaties": len(self.integration.active_treaties),
                "events": len(self.integration.event_log),
            },
            "timestamp": _now_iso(),
        }

    def __repr__(self) -> str:
        return (
            f"IntegrationLayer(id={self.layer_id!r}, "
            f"configured={self._configured}, "
            f"treaties={len(self.registry.treaties)})"
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def integrate_with_cover_design(treaty: dict, cover_config: dict) -> dict:
    """Register treaty constraints with cover_design and return the result.

    Creates a temporary :class:`CoverDesignBridge`, pushes all constraints from
    *treaty*, applies them, and returns a result dict describing what happened.

    Parameters
    ----------
    treaty:
        Treaty dict with at least ``"patch_a_id"``, ``"patch_b_id"``, and
        ``"constraints"``.
    cover_config:
        Configuration passed to cover_design.

    Returns
    -------
    dict
        Result with keys ``"treaty_id"``, ``"applied_constraints"``,
        ``"boundary_quality_a"``, ``"boundary_quality_b"``, and ``"timestamp"``.

    Examples
    --------
    >>> t = {"patch_a_id": "P1", "patch_b_id": "P2", "constraints": [{"kind": "overlap"}]}
    >>> result = integrate_with_cover_design(t, {"resolution": 4})
    >>> "applied_constraints" in result
    True
    """
    _validate_treaty_dict(treaty)
    bridge = CoverDesignBridge(cover_design_config=cover_config)
    treaty_id = treaty.get("treaty_id") or _stable_hash(treaty)
    constraints = _normalise_constraints(treaty.get("constraints", []))
    for c in constraints:
        bridge.push_constraint(treaty_id, c)
    applied = bridge.apply_pending()
    qa = bridge.get_boundary_quality(treaty["patch_a_id"])
    qb = bridge.get_boundary_quality(treaty["patch_b_id"])
    result = {
        "treaty_id": treaty_id,
        "applied_constraints": len(applied),
        "boundary_quality_a": qa,
        "boundary_quality_b": qb,
        "timestamp": _now_iso(),
    }
    logger.info(
        "integrate_with_cover_design: treaty=%s applied=%d qa=%.3f qb=%.3f",
        treaty_id, len(applied), qa, qb,
    )
    return result


def register_treaty(registry: TreatyRegistry, treaty: dict) -> str:
    """Store a new treaty in *registry* and return its ID.

    Thin convenience wrapper around :meth:`TreatyRegistry.register`.

    Parameters
    ----------
    registry:
        The :class:`TreatyRegistry` in which to store the treaty.
    treaty:
        Treaty dict.

    Returns
    -------
    str
        The ``treaty_id`` under which the treaty is stored.
    """
    treaty_id = registry.register(treaty)
    logger.info("register_treaty: stored treaty %s in registry %s", treaty_id, registry.registry_id)
    return treaty_id


def lookup_treaty(
    registry: TreatyRegistry,
    patch_a: str,
    patch_b: str,
) -> Optional[dict]:
    """Find the treaty between *patch_a* and *patch_b* in *registry*.

    Thin convenience wrapper around :meth:`TreatyRegistry.lookup`.

    Parameters
    ----------
    registry:
        The registry to query.
    patch_a:
        Identifier of the first patch.
    patch_b:
        Identifier of the second patch.

    Returns
    -------
    dict or None
    """
    result = registry.lookup(patch_a, patch_b)
    if result is None:
        logger.debug("lookup_treaty: no treaty found for (%s, %s)", patch_a, patch_b)
    else:
        logger.debug(
            "lookup_treaty: found treaty %s for (%s, %s)",
            result.get("treaty_id"), patch_a, patch_b,
        )
    return result


def bridge_to_orchestrator(
    bridge: OrchestratorTreatyBridge,
    event: str,
    data: dict,
) -> None:
    """Notify the orchestrator of a treaty lifecycle event.

    Routes *event* to the appropriate notify method on *bridge* and immediately
    flushes so the orchestrator receives events in near-real-time.

    Parameters
    ----------
    bridge:
        The :class:`OrchestratorTreatyBridge` to use.
    event:
        The event type string (one of the ``EVENT_*`` constants).
    data:
        Arbitrary event payload.
    """
    if event == EVENT_TREATY_FORMED:
        bridge.notify_formation(data)
    elif event == EVENT_TREATY_VIOLATED:
        bridge.notify_violation(data)
    elif event == EVENT_TREATY_EXPIRED:
        bridge.notify_violation({"reason": "treaty_expired", **data})
    else:
        bridge.notify_violation({"reason": f"unknown_event:{event}", **data})
    flushed = bridge.flush_notifications()
    logger.info(
        "bridge_to_orchestrator: event=%s flushed=%d to %s",
        event, len(flushed), bridge.orchestrator_endpoint,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s – %(message)s")

    print(f"=== jugeo hypercover_treaties integration v{INTEGRATION_VERSION} ===\n")

    # ------------------------------------------------------------------
    # 1. Create an IntegrationLayer and configure it.
    # ------------------------------------------------------------------
    layer = IntegrationLayer(layer_id="smoke-test-layer")
    layer.setup({
        "orchestrator_endpoint": "http://localhost:9000/orchestrator",
        "cover_design_config": {"resolution": 8, "overlap_tolerance": 0.05},
    })
    print(f"Layer created: {layer}\n")

    # ------------------------------------------------------------------
    # 2. Build some treaty dicts.
    # ------------------------------------------------------------------
    treaty_alpha = {
        "patch_a_id": "patch-alpha",
        "patch_b_id": "patch-beta",
        "constraints": [
            {"kind": "continuity", "payload": "C0_boundary", "trust": TrustTier.RUNTIME_WITNESSED.value},
            {"kind": "overlap", "payload": "min_overlap_0.1", "trust": TrustTier.VERIFIED.value},
        ],
        "metadata": {"source": "smoke_test", "priority": "high"},
    }
    treaty_beta = {
        "patch_a_id": "patch-beta",
        "patch_b_id": "patch-gamma",
        "constraints": [
            {"kind": "orientation", "payload": "consistent_orientation", "trust": TrustTier.PROOF_BACKED.value},
        ],
    }
    treaty_gamma = {
        "patch_a_id": "patch-gamma",
        "patch_b_id": "patch-delta",
        "constraints": [],
    }

    # ------------------------------------------------------------------
    # 3. Register treaties through the layer.
    # ------------------------------------------------------------------
    j1 = layer.process_treaty(treaty_alpha)
    j2 = layer.process_treaty(treaty_beta)
    j3 = layer.process_treaty(treaty_gamma)
    print(f"Judgment for alpha treaty: trust={j1.trust.name}, formula={j1.formula!r}")
    print(f"Judgment for beta  treaty: trust={j2.trust.name}, formula={j2.formula!r}")
    print(f"Judgment for gamma treaty: trust={j3.trust.name}, formula={j3.formula!r}\n")

    # ------------------------------------------------------------------
    # 4. Look up treaties.
    # ------------------------------------------------------------------
    found = lookup_treaty(layer.registry, "patch-alpha", "patch-beta")
    print(f"Lookup (alpha↔beta): treaty_id={found['treaty_id']!r}")
    not_found = lookup_treaty(layer.registry, "patch-alpha", "patch-delta")
    print(f"Lookup (alpha↔delta): {not_found}\n")

    # ------------------------------------------------------------------
    # 5. standalone integrate_with_cover_design
    # ------------------------------------------------------------------
    standalone_treaty = {
        "patch_a_id": "P-standalone-A",
        "patch_b_id": "P-standalone-B",
        "constraints": [
            {"kind": "gluing", "payload": "G1_gluing", "trust": TrustTier.REVIEWED.value},
            {"kind": "gluing", "payload": "G2_gluing", "trust": TrustTier.VERIFIED.value},
        ],
    }
    cdr = integrate_with_cover_design(standalone_treaty, {"resolution": 16})
    print(f"integrate_with_cover_design result: {cdr}\n")

    # ------------------------------------------------------------------
    # 6. CoverDesignBridge: boundary quality queries
    # ------------------------------------------------------------------
    print(f"Boundary quality for patch-alpha: {layer.cover_bridge.get_boundary_quality('patch-alpha'):.3f}")
    print(f"Boundary quality for patch-beta:  {layer.cover_bridge.get_boundary_quality('patch-beta'):.3f}\n")

    # ------------------------------------------------------------------
    # 7. OrchestratorTreatyBridge: direct event bridging
    # ------------------------------------------------------------------
    violation = {
        "treaty_id": found["treaty_id"],
        "reason": "section_mismatch_at_t42",
        "patch_a_id": "patch-alpha",
        "patch_b_id": "patch-beta",
    }
    bridge_to_orchestrator(layer.orchestrator_bridge, EVENT_TREATY_VIOLATED, violation)
    print(f"Orchestrator log length after violation: {len(layer.orchestrator_bridge.notification_log)}")

    # ------------------------------------------------------------------
    # 8. CechObstruction smoke test
    # ------------------------------------------------------------------
    obs_nontrivial = CechObstruction(
        cover_id="cover-001",
        cocycle=frozenset([
            ("patch-alpha", "patch-beta", "section_A"),
            ("patch-beta", "patch-gamma", "section_B"),
        ]),
        cohomology_class="H^1(U, F)[1]",
        description="Mismatch of local sections on the alpha-beta-gamma triple overlap",
    )
    obs_trivial = CechObstruction(
        cover_id="cover-001",
        cocycle=frozenset(),
        cohomology_class="0",
        description="No obstruction",
    )
    print(f"\nObstruction non-trivial: {obs_nontrivial.is_trivial()=}")
    print(f"Obstruction trivial:     {obs_trivial.is_trivial()=}")

    # ------------------------------------------------------------------
    # 9. TrustTier algebra smoke test
    # ------------------------------------------------------------------
    t = TrustTier.REVIEWED
    print(f"\nTrustTier algebra: REVIEWED.join(PROOF_BACKED)={t.join(TrustTier.PROOF_BACKED).name}")
    print(f"TrustTier algebra: REVIEWED.meet(PROPOSAL)={t.meet(TrustTier.PROPOSAL).name}")
    print(f"TrustTier algebra: REVIEWED.promote()={t.promote().name}")
    print(f"TrustTier algebra: REVIEWED.demote()={t.demote().name}")

    # ------------------------------------------------------------------
    # 10. TreatyRegistry direct usage
    # ------------------------------------------------------------------
    standalone_reg = TreatyRegistry()
    t1_id = register_treaty(standalone_reg, {
        "patch_a_id": "X1", "patch_b_id": "X2",
        "constraints": [{"kind": "shared_boundary"}],
    })
    t2_id = register_treaty(standalone_reg, {
        "patch_a_id": "X2", "patch_b_id": "X3",
        "constraints": [],
    })
    print(f"\nStandalone registry: {standalone_reg}")
    print(f"  All treaties: {[t['treaty_id'] for t in standalone_reg.list_all()]}")
    removed = standalone_reg.remove(t1_id)
    print(f"  Removed {t1_id}: {removed}; remaining: {len(standalone_reg.treaties)}")

    # ------------------------------------------------------------------
    # 11. Status snapshot
    # ------------------------------------------------------------------
    status = layer.get_status()
    print(f"\nLayer status:")
    for k, v in status.items():
        print(f"  {k}: {v}")

    # ------------------------------------------------------------------
    # 12. Teardown
    # ------------------------------------------------------------------
    layer.teardown()
    print("\nLayer torn down successfully.")
    print("=== smoke test complete ===")

