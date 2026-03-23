"""Section — Evidence Federation: Reconciling Incomparable Support Channels.

# copilot: foundations/oracle_federation §evidence-federation — Theory2.tex
# Chapter: Controlled oracles, solver federation, and runtime witnesses

This module implements **evidence federation** as described in Theory2.tex: the
process of reconciling evidence that arrives from *incomparable* support channels
into a coherent section over the coordinate site.

Background
----------
In the JuGeo trust model, evidence arrives from multiple sources — solvers,
oracles, runtime witnesses, human attestations — each occupying a different
*channel* in the evidence category.  Two channels are **incomparable** when
neither dominates the other in the trust algebra: a solver discharge and a
human attestation are both ``VERIFIED``, but from orthogonal epistemic sources.

Evidence federation asks: given a family of incomparable evidence sections
``{eᵢ ∈ Eᵢ(c)}``, is there a canonical *federated section* ``ê ∈ E(c)`` that
is consistent with all of them?

Theory2.tex defines federation via a *reconciliation map* that:
1. Checks pairwise compatibility of sections (the *overlap condition*).
2. Computes a least upper bound in the evidence lattice (the *join*).
3. Flags irreconcilable conflicts as *obstruction classes*.

Theory2.tex invariants
----------------------
- Judgments are tuples ``(c, φ, A, E, O, B, T, Π)`` — never booleans.
- Trust is an ordered algebra ``PROPOSAL → REVIEWED → VERIFIED`` — never a float.
- Oracle proposals always enter at ``PROPOSAL``; federation cannot promote them
  unless a second independent channel at higher tier corroborates.

Public API
----------
- :class:`ChannelKind` — enum of evidence channel types
- :class:`ReconciliationPolicy` — policy controlling how conflicts are resolved
- :class:`SupportSection` — evidence section from one channel
- :class:`ChannelOrdering` — partial order relation between two channels
- :class:`FederationResult` — outcome of a single reconciliation run
- :class:`FederationPolicy` — configurable reconciliation policy
- :class:`ObstructionRecord` — a detected incompatibility between two sections
- :class:`EvidenceFederationReconcilingIncomparableCoordinator` — orchestrates federation
- :class:`EvidenceFederationReconcilingIncomparableAnalyzer` — analyzes federation health
- :class:`EvidenceFederationReconcilingIncomparableWitness` — immutable certificate
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.evidence.trust import TrustTier, TrustLevel, TrustProfile
except ImportError:
    TrustTier = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]
    TrustProfile = Any  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust algebra
# ---------------------------------------------------------------------------

_TRUST_ORDER: dict[str, int] = {
    "PROPOSAL": 0,
    "REVIEWED": 1,
    "VERIFIED": 2,
}


def _trust_join(t1: str, t2: str) -> str:
    """Return the least upper bound of two trust tier labels."""
    return t1 if _TRUST_ORDER.get(t1, 0) >= _TRUST_ORDER.get(t2, 0) else t2


def _trust_meet(t1: str, t2: str) -> str:
    """Return the greatest lower bound of two trust tier labels."""
    return t1 if _TRUST_ORDER.get(t1, 0) <= _TRUST_ORDER.get(t2, 0) else t2


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ChannelKind(Enum):
    """The epistemic kind of an evidence channel.

    Theory2.tex distinguishes channels by their epistemic provenance.  Two
    channels of different kinds may produce incomparable evidence for the same
    coordinate even when both are trustworthy.

    ORACLE
        Evidence from a controlled oracle (LLM, external tool).
    SOLVER
        Evidence from a formal solver (Z3, Lean, Coq).
    RUNTIME
        Evidence from a runtime witness (heap/stack/identity snapshot).
    HUMAN
        Evidence attested by a human reviewer.
    STATISTICAL
        Evidence from a statistical model or probabilistic checker.
    DERIVED
        Evidence inferred by the federation layer itself.
    """

    ORACLE = "oracle"
    SOLVER = "solver"
    RUNTIME = "runtime"
    HUMAN = "human"
    STATISTICAL = "statistical"
    DERIVED = "derived"


class ReconciliationStatus(Enum):
    """Outcome of a reconciliation attempt."""

    SUCCESS = "success"
    PARTIAL = "partial"           # Some sections reconciled, some obstructed
    OBSTRUCTED = "obstructed"     # Irreconcilable conflict found
    TRIVIAL = "trivial"           # Only one section; nothing to reconcile
    EMPTY = "empty"               # No sections provided


class ConflictKind(Enum):
    """Classifier for the type of conflict between two sections."""

    TRUST_INCOMPATIBILITY = "trust_incompatibility"
    CONTENT_CONTRADICTION = "content_contradiction"
    TEMPORAL_INCONSISTENCY = "temporal_inconsistency"
    SCHEMA_MISMATCH = "schema_mismatch"
    JURISDICTION_OVERLAP = "jurisdiction_overlap"


class OrderRelation(Enum):
    """The partial order relation between two evidence channels."""

    DOMINATES = "dominates"     # channel A ≥ channel B in trust
    DOMINATED = "dominated"     # channel A ≤ channel B
    EQUAL = "equal"             # A ≡ B
    INCOMPARABLE = "incomparable"  # Neither dominates


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportSection:
    """An evidence section from a single channel over a coordinate.

    A ``SupportSection`` represents the evidence that one particular channel
    (oracle, solver, human, …) provides for a claim at coordinate ``c``.
    It carries:
    - The raw evidence content (free-form dict).
    - The channel's trust tier for this section.
    - A fingerprint of the content for conflict detection.

    Theory2.tex: this is the local section ``sᵢ ∈ Eᵢ(c)`` that the federation
    algorithm needs to reconcile.

    Parameters
    ----------
    section_id:
        Unique identifier.
    channel_id:
        The channel that produced this section.
    channel_kind:
        The :class:`ChannelKind` of the channel.
    coordinate:
        The coordinate this section covers.
    content:
        The evidence content (free-form).
    trust_tier:
        The trust tier claimed by this channel for this section.
    timestamp:
        When this section was produced.
    schema_tag:
        A tag naming the content schema (for compatibility checks).
    provenance:
        Free-text provenance chain.
    metadata:
        Extension key-value pairs.
    """

    section_id: str = field(default_factory=lambda: "ss_" + uuid.uuid4().hex[:12])
    channel_id: str = ""
    channel_kind: str = ChannelKind.ORACLE.value
    coordinate: str = ""
    content: dict = field(default_factory=dict)
    trust_tier: str = "PROPOSAL"
    timestamp: float = field(default_factory=time.time)
    schema_tag: str = ""
    provenance: str = ""
    metadata: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint of the section content."""
        body = json.dumps(
            {"channel_id": self.channel_id, "coordinate": self.coordinate,
             "content": self.content, "trust_tier": self.trust_tier},
            sort_keys=True,
        )
        return hashlib.sha256(body.encode()).hexdigest()[:24]

    def is_compatible_with(self, other: SupportSection) -> bool:
        """Return True if this section and *other* could be reconciled.

        Compatibility is checked by:
        1. Same coordinate.
        2. Same schema tag (or at least one is empty).
        3. No direct content contradictions (key present in both with different values).
        """
        if self.coordinate != other.coordinate:
            return False
        if self.schema_tag and other.schema_tag and self.schema_tag != other.schema_tag:
            return False
        for k, v in self.content.items():
            if k in other.content and other.content[k] != v:
                if k not in ("timestamp", "section_id", "provenance"):
                    return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "channel_id": self.channel_id,
            "channel_kind": self.channel_kind,
            "coordinate": self.coordinate,
            "content": dict(self.content),
            "trust_tier": self.trust_tier,
            "timestamp": self.timestamp,
            "schema_tag": self.schema_tag,
            "provenance": self.provenance,
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SupportSection:
        return cls(
            section_id=d.get("section_id", "ss_" + uuid.uuid4().hex[:12]),
            channel_id=d.get("channel_id", ""),
            channel_kind=d.get("channel_kind", ChannelKind.ORACLE.value),
            coordinate=d.get("coordinate", ""),
            content=dict(d.get("content", {})),
            trust_tier=d.get("trust_tier", "PROPOSAL"),
            timestamp=float(d.get("timestamp", time.time())),
            schema_tag=d.get("schema_tag", ""),
            provenance=d.get("provenance", ""),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class ChannelOrdering:
    """Records the partial order relation between two evidence channels.

    Theory2.tex: two channels are *incomparable* when neither dominates the
    other in the trust algebra.  The federation algorithm must handle this case
    by computing a join rather than simply deferring to the dominant channel.

    Parameters
    ----------
    channel_a:
        First channel identifier.
    channel_b:
        Second channel identifier.
    relation:
        The :class:`OrderRelation` between a and b.
    basis:
        A free-text explanation of why this relation holds.
    """

    channel_a: str = ""
    channel_b: str = ""
    relation: str = OrderRelation.INCOMPARABLE.value
    basis: str = ""

    def is_incomparable(self) -> bool:
        return self.relation == OrderRelation.INCOMPARABLE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_a": self.channel_a,
            "channel_b": self.channel_b,
            "relation": self.relation,
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChannelOrdering:
        return cls(
            channel_a=d.get("channel_a", ""),
            channel_b=d.get("channel_b", ""),
            relation=d.get("relation", OrderRelation.INCOMPARABLE.value),
            basis=d.get("basis", ""),
        )


@dataclass(frozen=True, slots=True)
class ObstructionRecord:
    """Records a detected incompatibility between two evidence sections.

    An obstruction is a pair of sections that cannot be reconciled because they
    make contradictory claims about the same coordinate.  Theory2.tex represents
    obstructions as cohomological obstruction classes — here they are stored as
    structured records for audit purposes.

    Parameters
    ----------
    obstruction_id:
        Unique identifier.
    section_a_id:
        First conflicting section.
    section_b_id:
        Second conflicting section.
    coordinate:
        The coordinate at which the conflict occurs.
    conflict_kind:
        The :class:`ConflictKind` of the obstruction.
    description:
        Human-readable description of the conflict.
    resolution_hint:
        Optional hint for resolving the obstruction.
    is_fatal:
        If True, federation cannot proceed past this obstruction.
    """

    obstruction_id: str = field(default_factory=lambda: "obs_" + uuid.uuid4().hex[:12])
    section_a_id: str = ""
    section_b_id: str = ""
    coordinate: str = ""
    conflict_kind: str = ConflictKind.CONTENT_CONTRADICTION.value
    description: str = ""
    resolution_hint: str = ""
    is_fatal: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "section_a_id": self.section_a_id,
            "section_b_id": self.section_b_id,
            "coordinate": self.coordinate,
            "conflict_kind": self.conflict_kind,
            "description": self.description,
            "resolution_hint": self.resolution_hint,
            "is_fatal": self.is_fatal,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObstructionRecord:
        return cls(
            obstruction_id=d.get("obstruction_id", "obs_" + uuid.uuid4().hex[:12]),
            section_a_id=d.get("section_a_id", ""),
            section_b_id=d.get("section_b_id", ""),
            coordinate=d.get("coordinate", ""),
            conflict_kind=d.get("conflict_kind", ConflictKind.CONTENT_CONTRADICTION.value),
            description=d.get("description", ""),
            resolution_hint=d.get("resolution_hint", ""),
            is_fatal=bool(d.get("is_fatal", True)),
        )


@dataclass(frozen=True, slots=True)
class FederationPolicy:
    """Configuration controlling how the federation coordinator handles conflicts.

    Parameters
    ----------
    policy_id:
        Unique identifier.
    allow_partial:
        If True, partial reconciliation (some sections reconciled, some obstructed)
        is considered a success.
    trust_join_strategy:
        How to compute the trust tier of the federated section.
        ``'meet'`` takes the minimum (most conservative);
        ``'join'`` takes the maximum (most optimistic, risky);
        ``'majority'`` uses the tier held by the majority of sections.
    conflict_resolution:
        ``'raise'`` — raise an obstruction record;
        ``'drop_lower'`` — drop the lower-trust section;
        ``'merge_best_effort'`` — merge non-conflicting keys.
    max_obstructions:
        Maximum number of obstructions before federation is aborted.
    require_all_channels:
        If True, all registered channels must contribute a section.
    """

    policy_id: str = field(default_factory=lambda: "fp_" + uuid.uuid4().hex[:12])
    allow_partial: bool = True
    trust_join_strategy: str = "meet"
    conflict_resolution: str = "raise"
    max_obstructions: int = 5
    require_all_channels: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "allow_partial": self.allow_partial,
            "trust_join_strategy": self.trust_join_strategy,
            "conflict_resolution": self.conflict_resolution,
            "max_obstructions": self.max_obstructions,
            "require_all_channels": self.require_all_channels,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FederationPolicy:
        return cls(
            policy_id=d.get("policy_id", "fp_" + uuid.uuid4().hex[:12]),
            allow_partial=bool(d.get("allow_partial", True)),
            trust_join_strategy=d.get("trust_join_strategy", "meet"),
            conflict_resolution=d.get("conflict_resolution", "raise"),
            max_obstructions=int(d.get("max_obstructions", 5)),
            require_all_channels=bool(d.get("require_all_channels", False)),
        )


@dataclass(frozen=True, slots=True)
class FederationResult:
    """The output of a single evidence federation run.

    Parameters
    ----------
    run_id:
        Unique identifier of this run.
    coordinate:
        The coordinate being federated.
    input_sections:
        All :class:`SupportSection` objects that were presented.
    reconciled_sections:
        Sections that were successfully reconciled.
    federated_content:
        The merged evidence content (join of compatible sections).
    federated_tier:
        The trust tier of the federated section.
    obstructions:
        Detected incompatibilities.
    status:
        :class:`ReconciliationStatus`.
    elapsed_seconds:
        Wall-clock time.
    """

    run_id: str = field(default_factory=lambda: "fr_" + uuid.uuid4().hex[:12])
    coordinate: str = ""
    input_sections: tuple[SupportSection, ...] = ()
    reconciled_sections: tuple[str, ...] = ()  # section_ids
    federated_content: dict = field(default_factory=dict)
    federated_tier: str = "PROPOSAL"
    obstructions: tuple[ObstructionRecord, ...] = ()
    status: str = ReconciliationStatus.TRIVIAL.value
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "coordinate": self.coordinate,
            "input_sections": [s.to_dict() for s in self.input_sections],
            "reconciled_sections": list(self.reconciled_sections),
            "federated_content": dict(self.federated_content),
            "federated_tier": self.federated_tier,
            "obstructions": [o.to_dict() for o in self.obstructions],
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FederationResult:
        return cls(
            run_id=d.get("run_id", "fr_" + uuid.uuid4().hex[:12]),
            coordinate=d.get("coordinate", ""),
            input_sections=tuple(
                SupportSection.from_dict(s) for s in d.get("input_sections", [])
            ),
            reconciled_sections=tuple(d.get("reconciled_sections", [])),
            federated_content=dict(d.get("federated_content", {})),
            federated_tier=d.get("federated_tier", "PROPOSAL"),
            obstructions=tuple(
                ObstructionRecord.from_dict(o) for o in d.get("obstructions", [])
            ),
            status=d.get("status", ReconciliationStatus.TRIVIAL.value),
            elapsed_seconds=float(d.get("elapsed_seconds", 0.0)),
        )


# ---------------------------------------------------------------------------
# Witness (immutable certificate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceFederationReconcilingIncomparableWitness:
    """Immutable certificate produced by a completed evidence federation run.

    Captures the full audit trail: input sections, reconciliation outcome,
    obstructions, final trust tier, and the Theory2.tex judgment tuple.

    Theory2.tex invariant: ``final_tier`` is always a string label from the
    trust algebra — never a float, never a bool.  The judgment tuple
    ``(c, φ, A, E, O, B, T, Π)`` is stored in ``judgment_tuple``.

    Parameters
    ----------
    witness_id:
        Globally unique identifier.
    coordinate:
        The coordinate that was federated.
    channel_ids:
        All channels that contributed sections.
    federation_result:
        The :class:`FederationResult` produced.
    final_tier:
        Trust tier of the federated section (string label).
    obstruction_count:
        Total number of obstructions detected.
    judgment_tuple:
        Full ``(c, φ, A, E, O, B, T, Π)`` tuple as a dict.
    policy_id:
        The :class:`FederationPolicy` that governed this run.
    created_at:
        ISO-8601 UTC timestamp.
    metadata:
        Extension key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: "efriw_" + uuid.uuid4().hex[:12])
    coordinate: str = ""
    channel_ids: tuple[str, ...] = ()
    federation_result: FederationResult = field(default_factory=FederationResult)
    final_tier: str = "PROPOSAL"
    obstruction_count: int = 0
    judgment_tuple: dict = field(default_factory=dict)
    policy_id: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict = field(default_factory=dict)

    # ---- serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id,
            "coordinate": self.coordinate,
            "channel_ids": list(self.channel_ids),
            "federation_result": self.federation_result.to_dict(),
            "final_tier": self.final_tier,
            "obstruction_count": self.obstruction_count,
            "judgment_tuple": dict(self.judgment_tuple),
            "policy_id": self.policy_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceFederationReconcilingIncomparableWitness:
        return cls(
            witness_id=d.get("witness_id", "efriw_" + uuid.uuid4().hex[:12]),
            coordinate=d.get("coordinate", ""),
            channel_ids=tuple(d.get("channel_ids", [])),
            federation_result=FederationResult.from_dict(d.get("federation_result", {})),
            final_tier=d.get("final_tier", "PROPOSAL"),
            obstruction_count=int(d.get("obstruction_count", 0)),
            judgment_tuple=dict(d.get("judgment_tuple", {})),
            policy_id=d.get("policy_id", ""),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            metadata=dict(d.get("metadata", {})),
        )

    def validate(self) -> list[str]:
        """Return invariant violations (empty = valid).

        Checks:
        - ``final_tier`` is a known algebra label.
        - ``obstruction_count`` is non-negative.
        - ``judgment_tuple`` is non-empty.
        - ``channel_ids`` is non-empty.
        """
        errors: list[str] = []
        if self.final_tier not in _TRUST_ORDER:
            errors.append(f"final_tier {self.final_tier!r} not in trust algebra")
        if self.obstruction_count < 0:
            errors.append("obstruction_count must be non-negative")
        if not self.judgment_tuple:
            errors.append("judgment_tuple must be non-empty (Theory2.tex invariant)")
        if not self.channel_ids:
            errors.append("channel_ids must be non-empty")
        return errors

    def merge(
        self, other: EvidenceFederationReconcilingIncomparableWitness
    ) -> EvidenceFederationReconcilingIncomparableWitness:
        """Merge two federation witnesses (conservative: take weaker tier, sum obstructions)."""
        my_rank = _TRUST_ORDER.get(self.final_tier, 0)
        other_rank = _TRUST_ORDER.get(other.final_tier, 0)
        merged_tier = self.final_tier if my_rank <= other_rank else other.final_tier
        merged_channels = tuple(set(self.channel_ids) | set(other.channel_ids))
        merged_meta = {
            **self.metadata, **other.metadata,
            "merged_from": [self.witness_id, other.witness_id],
        }
        return replace(
            self,
            witness_id="efriw_" + uuid.uuid4().hex[:12],
            final_tier=merged_tier,
            channel_ids=merged_channels,
            obstruction_count=self.obstruction_count + other.obstruction_count,
            metadata=merged_meta,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def content_hash(self) -> str:
        content = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


@dataclass
class EvidenceFederationReconcilingIncomparableCoordinator:
    """Orchestrates the reconciliation of evidence from incomparable channels.

    Implements Theory2.tex evidence federation:
    1. Accept a collection of :class:`SupportSection` objects for a coordinate.
    2. Classify channel pairs as comparable or incomparable via
       :class:`ChannelOrdering`.
    3. Run pairwise compatibility checks and collect :class:`ObstructionRecord`s.
    4. Compute the federated section (content join + tier computation).
    5. Emit a :class:`EvidenceFederationReconcilingIncomparableWitness`.

    Parameters
    ----------
    coordinator_id:
        Unique identifier.
    policy:
        The :class:`FederationPolicy` governing reconciliation.
    channel_orderings:
        Known orderings between channel pairs.
    history:
        List of all witnesses produced.
    registered_channels:
        Set of known channel IDs.
    """

    coordinator_id: str = field(default_factory=lambda: "efric_" + uuid.uuid4().hex[:12])
    policy: FederationPolicy = field(default_factory=FederationPolicy)
    channel_orderings: list[ChannelOrdering] = field(default_factory=list)
    history: list[EvidenceFederationReconcilingIncomparableWitness] = field(default_factory=list)
    registered_channels: set = field(default_factory=set)

    # ---- channel registration ----

    def register_channel(self, channel_id: str) -> None:
        """Register a channel as a known participant in federation."""
        self.registered_channels.add(channel_id)
        logger.debug("Registered channel %s in federation coordinator %s", channel_id, self.coordinator_id)

    def declare_ordering(self, ordering: ChannelOrdering) -> None:
        """Record a known partial-order relation between two channels."""
        self.channel_orderings.append(ordering)

    def get_ordering(self, channel_a: str, channel_b: str) -> OrderRelation:
        """Look up the known ordering between two channels.

        If no ordering is recorded, returns ``INCOMPARABLE`` (safe default).
        """
        for o in self.channel_orderings:
            if o.channel_a == channel_a and o.channel_b == channel_b:
                return OrderRelation(o.relation)
            if o.channel_a == channel_b and o.channel_b == channel_a:
                # Reverse the relation
                rel = OrderRelation(o.relation)
                if rel == OrderRelation.DOMINATES:
                    return OrderRelation.DOMINATED
                if rel == OrderRelation.DOMINATED:
                    return OrderRelation.DOMINATES
                return rel
        return OrderRelation.INCOMPARABLE

    # ---- core federation algorithm ----

    def reconcile(
        self,
        sections: Sequence[SupportSection],
        coordinate: str | None = None,
    ) -> FederationResult:
        """Reconcile a collection of :class:`SupportSection` objects.

        Algorithm
        ---------
        1. Validate that all sections share the same coordinate (or use *coordinate*).
        2. Detect pairwise obstructions.
        3. Compute the federated content by merging compatible sections.
        4. Compute the federated trust tier using the policy's join strategy.
        5. Return a :class:`FederationResult`.
        """
        t0 = time.monotonic()
        sections = list(sections)

        if not sections:
            return FederationResult(
                coordinate=coordinate or "",
                status=ReconciliationStatus.EMPTY.value,
                elapsed_seconds=time.monotonic() - t0,
            )

        # Determine coordinate
        coord = coordinate or sections[0].coordinate
        for s in sections:
            if s.coordinate and s.coordinate != coord:
                return FederationResult(
                    coordinate=coord,
                    input_sections=tuple(sections),
                    status=ReconciliationStatus.OBSTRUCTED.value,
                    obstructions=(ObstructionRecord(
                        section_a_id=sections[0].section_id,
                        section_b_id=s.section_id,
                        coordinate=coord,
                        conflict_kind=ConflictKind.SCHEMA_MISMATCH.value,
                        description=f"Coordinate mismatch: {coord!r} vs {s.coordinate!r}",
                        is_fatal=True,
                    ),),
                    elapsed_seconds=time.monotonic() - t0,
                )

        if len(sections) == 1:
            return FederationResult(
                coordinate=coord,
                input_sections=tuple(sections),
                reconciled_sections=(sections[0].section_id,),
                federated_content=dict(sections[0].content),
                federated_tier=sections[0].trust_tier,
                status=ReconciliationStatus.TRIVIAL.value,
                elapsed_seconds=time.monotonic() - t0,
            )

        # Pairwise obstruction detection
        obstructions: list[ObstructionRecord] = []
        compatible_ids: list[str] = []
        for i in range(len(sections)):
            for j in range(i + 1, len(sections)):
                sa, sb = sections[i], sections[j]
                if not sa.is_compatible_with(sb):
                    obs = self._build_obstruction(sa, sb, coord)
                    obstructions.append(obs)
                    if len(obstructions) >= self.policy.max_obstructions:
                        return FederationResult(
                            coordinate=coord,
                            input_sections=tuple(sections),
                            reconciled_sections=tuple(compatible_ids),
                            federated_tier="PROPOSAL",
                            obstructions=tuple(obstructions),
                            status=ReconciliationStatus.OBSTRUCTED.value,
                            elapsed_seconds=time.monotonic() - t0,
                        )

        # Compute compatible set (those not involved in fatal obstructions)
        fatal_ids = set()
        for obs in obstructions:
            if obs.is_fatal:
                fatal_ids.add(obs.section_a_id)
                fatal_ids.add(obs.section_b_id)

        if self.policy.conflict_resolution == "drop_lower":
            # Drop the lower-trust section from each conflict pair
            for obs in obstructions:
                if obs.is_fatal:
                    sa = next((s for s in sections if s.section_id == obs.section_a_id), None)
                    sb = next((s for s in sections if s.section_id == obs.section_b_id), None)
                    if sa and sb:
                        dropped = obs.section_a_id if _TRUST_ORDER.get(sa.trust_tier, 0) <= _TRUST_ORDER.get(sb.trust_tier, 0) else obs.section_b_id
                        fatal_ids.discard(dropped)  # keep the better one
            compatible_sections = [s for s in sections if s.section_id not in fatal_ids]
        else:
            compatible_sections = [s for s in sections if s.section_id not in fatal_ids]

        compatible_ids = [s.section_id for s in compatible_sections]

        # Compute federated content (union of content dicts, last-write-wins for conflicts)
        federated_content: dict[str, Any] = {}
        for s in compatible_sections:
            federated_content.update(s.content)

        # Compute federated tier
        federated_tier = self._compute_federated_tier(compatible_sections)

        status = (
            ReconciliationStatus.SUCCESS.value
            if not obstructions
            else (
                ReconciliationStatus.PARTIAL.value
                if self.policy.allow_partial
                else ReconciliationStatus.OBSTRUCTED.value
            )
        )

        return FederationResult(
            coordinate=coord,
            input_sections=tuple(sections),
            reconciled_sections=tuple(compatible_ids),
            federated_content=federated_content,
            federated_tier=federated_tier,
            obstructions=tuple(obstructions),
            status=status,
            elapsed_seconds=time.monotonic() - t0,
        )

    def _build_obstruction(
        self, sa: SupportSection, sb: SupportSection, coord: str
    ) -> ObstructionRecord:
        """Build an :class:`ObstructionRecord` for an incompatible pair."""
        # Determine conflict kind
        if sa.schema_tag and sb.schema_tag and sa.schema_tag != sb.schema_tag:
            kind = ConflictKind.SCHEMA_MISMATCH.value
        elif abs(sa.timestamp - sb.timestamp) > 3600:
            kind = ConflictKind.TEMPORAL_INCONSISTENCY.value
        else:
            kind = ConflictKind.CONTENT_CONTRADICTION.value
        return ObstructionRecord(
            section_a_id=sa.section_id,
            section_b_id=sb.section_id,
            coordinate=coord,
            conflict_kind=kind,
            description=(
                f"Sections {sa.section_id!r} (channel={sa.channel_id}) and "
                f"{sb.section_id!r} (channel={sb.channel_id}) are incompatible"
            ),
            resolution_hint="Check schema tags and content keys for contradictions",
            is_fatal=True,
        )

    def _compute_federated_tier(self, sections: list[SupportSection]) -> str:
        """Compute the trust tier for the federated section.

        Strategy is controlled by ``self.policy.trust_join_strategy``.
        """
        if not sections:
            return "PROPOSAL"
        tiers = [s.trust_tier for s in sections]
        if self.policy.trust_join_strategy == "meet":
            result = tiers[0]
            for t in tiers[1:]:
                result = _trust_meet(result, t)
            return result
        elif self.policy.trust_join_strategy == "join":
            result = tiers[0]
            for t in tiers[1:]:
                result = _trust_join(result, t)
            return result
        else:  # majority
            from collections import Counter
            counts = Counter(tiers)
            return counts.most_common(1)[0][0]

    # ---- main entry point ----

    def run(
        self,
        sections: Sequence[SupportSection],
        coordinate: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceFederationReconcilingIncomparableWitness:
        """Federate *sections* and produce a witness.

        Parameters
        ----------
        sections:
            The input :class:`SupportSection` objects to reconcile.
        coordinate:
            Overrides the coordinate inferred from sections.
        metadata:
            Extra metadata attached to the witness.

        Returns
        -------
        EvidenceFederationReconcilingIncomparableWitness
        """
        result = self.reconcile(sections, coordinate)
        channel_ids = tuple(set(s.channel_id for s in sections))
        coord = coordinate or (sections[0].coordinate if sections else "")

        w = EvidenceFederationReconcilingIncomparableWitness(
            coordinate=coord,
            channel_ids=channel_ids,
            federation_result=result,
            final_tier=result.federated_tier,
            obstruction_count=len(result.obstructions),
            judgment_tuple=self._build_judgment_tuple(
                coord, channel_ids, result.federated_tier, result.obstructions
            ),
            policy_id=self.policy.policy_id,
            metadata=metadata or {},
        )
        self.history.append(w)
        logger.info(
            "Federation run complete: coord=%s status=%s tier=%s obstructions=%d",
            coord, result.status, result.federated_tier, len(result.obstructions),
        )
        return w

    def _build_judgment_tuple(
        self,
        coordinate: str,
        channel_ids: tuple[str, ...],
        trust_tier: str,
        obstructions: tuple[ObstructionRecord, ...],
    ) -> dict[str, Any]:
        """Build the ``(c, φ, A, E, O, B, T, Π)`` judgment tuple as a dict."""
        return {
            "c": coordinate,
            "phi": f"federated_evidence({','.join(channel_ids)})",
            "A": list(channel_ids),
            "E": {"channels": list(channel_ids), "federated": True},
            "O": [o.description for o in obstructions],
            "B": self.policy.trust_join_strategy,
            "T": trust_tier,
            "Pi": self.coordinator_id,
        }

    # ---- introspection ----

    def obstruction_rate(self) -> float:
        """Return the fraction of runs that produced at least one obstruction."""
        if not self.history:
            return 0.0
        with_obs = sum(1 for w in self.history if w.obstruction_count > 0)
        return with_obs / len(self.history)

    def success_rate(self) -> float:
        """Return the fraction of runs that produced a SUCCESS status."""
        if not self.history:
            return 0.0
        success = sum(
            1 for w in self.history
            if w.federation_result.status == ReconciliationStatus.SUCCESS.value
        )
        return success / len(self.history)

    def validate(self) -> list[str]:
        """Return invariant violations for this coordinator."""
        errors: list[str] = []
        if self.policy.trust_join_strategy not in ("meet", "join", "majority"):
            errors.append(f"Unknown trust_join_strategy: {self.policy.trust_join_strategy!r}")
        if self.policy.max_obstructions < 1:
            errors.append("max_obstructions must be at least 1")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinator_id": self.coordinator_id,
            "policy": self.policy.to_dict(),
            "channel_orderings": [o.to_dict() for o in self.channel_orderings],
            "history_count": len(self.history),
            "registered_channels": list(self.registered_channels),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceFederationReconcilingIncomparableCoordinator:
        c = cls(
            coordinator_id=d.get("coordinator_id", "efric_" + uuid.uuid4().hex[:12]),
            policy=FederationPolicy.from_dict(d.get("policy", {})),
        )
        for o in d.get("channel_orderings", []):
            c.channel_orderings.append(ChannelOrdering.from_dict(o))
        for ch in d.get("registered_channels", []):
            c.registered_channels.add(ch)
        return c


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


@dataclass
class EvidenceFederationReconcilingIncomparableAnalyzer:
    """Analyzes a corpus of federation witnesses to assess reconciliation health.

    Provides metrics on obstruction rates, channel contribution, tier distribution,
    and anomaly detection (channels that consistently cause conflicts).

    Theory2.tex relevance: a healthy federation should have a low obstruction rate
    and a stable trust-tier distribution.  High obstruction rates indicate that
    channels are producing fundamentally incompatible evidence — a signal for
    architectural review.
    """

    analyzer_id: str = field(default_factory=lambda: "efria_" + uuid.uuid4().hex[:12])
    witnesses: list[EvidenceFederationReconcilingIncomparableWitness] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict)

    def load(self, witnesses: Sequence[EvidenceFederationReconcilingIncomparableWitness]) -> None:
        self.witnesses = list(witnesses)
        self._cache.clear()

    def append(self, witness: EvidenceFederationReconcilingIncomparableWitness) -> None:
        self.witnesses.append(witness)
        self._cache.clear()

    # ---- core analysis ----

    def analyze(self) -> dict[str, Any]:
        """Return structured analysis of the witness corpus."""
        if "analysis" in self._cache:
            return self._cache["analysis"]  # type: ignore[return-value]
        result = {
            "total": len(self.witnesses),
            "status_distribution": self._status_distribution(),
            "tier_distribution": self._tier_distribution(),
            "obstruction_stats": self._obstruction_stats(),
            "channel_contribution": self._channel_contribution(),
            "anomalies": self._detect_anomalies(),
        }
        self._cache["analysis"] = result
        return result

    def score(self) -> float:
        """Return a federation-health score in [0, 1].

        Higher = healthier (lower obstruction rate, more successes).
        """
        if not self.witnesses:
            return 0.0
        a = self.analyze()
        total = a["total"]
        obs_count = a["obstruction_stats"].get("total_obstructions", 0)
        success_count = a["status_distribution"].get(ReconciliationStatus.SUCCESS.value, 0)
        anomaly_count = len(a.get("anomalies", []))
        health = success_count / total if total > 0 else 0.0
        penalty = (obs_count / (total + 1)) + (anomaly_count * 0.1)
        return max(0.0, min(1.0, health - penalty))

    def report(self) -> str:
        """Return a human-readable multi-line report."""
        a = self.analyze()
        lines = [
            "=== EvidenceFederation Analysis Report ===",
            f"Total witnesses: {a['total']}",
            f"Federation-health score: {self.score():.3f}",
            "",
            "--- Status distribution ---",
        ]
        for status, count in a["status_distribution"].items():
            lines.append(f"  {status}: {count}")
        lines += ["", "--- Trust tier distribution ---"]
        for tier, count in a["tier_distribution"].items():
            lines.append(f"  {tier}: {count}")
        lines += ["", "--- Obstruction stats ---"]
        obs = a["obstruction_stats"]
        lines.append(f"  Total obstructions: {obs.get('total_obstructions', 0)}")
        lines.append(f"  Runs with obstructions: {obs.get('runs_with_obstructions', 0)}")
        if a.get("anomalies"):
            lines += ["", "--- Anomalies ---"]
            for an in a["anomalies"]:
                lines.append(f"  {an}")
        return "\n".join(lines)

    def summarize(self) -> dict[str, Any]:
        return {
            "analyzer_id": self.analyzer_id,
            "total_witnesses": len(self.witnesses),
            "score": self.score(),
            "obstruction_rate": (
                self.analyze()["obstruction_stats"].get("runs_with_obstructions", 0)
                / max(1, len(self.witnesses))
            ),
        }

    # ---- channel analysis ----

    def channel_conflict_rate(self) -> dict[str, float]:
        """Return per-channel rate of appearing in at least one obstruction."""
        channel_total: dict[str, int] = {}
        channel_conflicts: dict[str, int] = {}
        for w in self.witnesses:
            for ch in w.channel_ids:
                channel_total[ch] = channel_total.get(ch, 0) + 1
            for obs in w.federation_result.obstructions:
                for sid in (obs.section_a_id, obs.section_b_id):
                    for s in w.federation_result.input_sections:
                        if s.section_id == sid:
                            channel_conflicts[s.channel_id] = channel_conflicts.get(s.channel_id, 0) + 1
        return {
            ch: channel_conflicts.get(ch, 0) / total
            for ch, total in channel_total.items()
        }

    # ---- private helpers ----

    def _status_distribution(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            s = w.federation_result.status
            counts[s] = counts.get(s, 0) + 1
        return counts

    def _tier_distribution(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            counts[w.final_tier] = counts.get(w.final_tier, 0) + 1
        return counts

    def _obstruction_stats(self) -> dict[str, Any]:
        total = sum(w.obstruction_count for w in self.witnesses)
        runs_with = sum(1 for w in self.witnesses if w.obstruction_count > 0)
        return {
            "total_obstructions": total,
            "runs_with_obstructions": runs_with,
            "obstruction_rate": runs_with / len(self.witnesses) if self.witnesses else 0.0,
        }

    def _channel_contribution(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            for ch in w.channel_ids:
                counts[ch] = counts.get(ch, 0) + 1
        return counts

    def _detect_anomalies(self) -> list[str]:
        anomalies = []
        for ch, rate in self.channel_conflict_rate().items():
            if rate > 0.4:
                anomalies.append(
                    f"Channel {ch!r} appears in conflicts {rate:.1%} of the time — "
                    "may be producing systematically incompatible evidence"
                )
        return anomalies


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== evidence_federation_reconciling_in.py smoke test ===")

    # Build two sections from incomparable channels
    s1 = SupportSection(
        channel_id="oracle_llm",
        channel_kind=ChannelKind.ORACLE.value,
        coordinate="prop.A_implies_B",
        content={"type": "implication", "confidence": "high"},
        trust_tier="PROPOSAL",
        schema_tag="logic_v1",
    )
    s2 = SupportSection(
        channel_id="solver_z3",
        channel_kind=ChannelKind.SOLVER.value,
        coordinate="prop.A_implies_B",
        content={"type": "implication", "verified": True},
        trust_tier="VERIFIED",
        schema_tag="logic_v1",
    )

    # Build coordinator
    policy = FederationPolicy(trust_join_strategy="meet", allow_partial=True)
    coordinator = EvidenceFederationReconcilingIncomparableCoordinator(policy=policy)
    coordinator.register_channel("oracle_llm")
    coordinator.register_channel("solver_z3")
    coordinator.declare_ordering(
        ChannelOrdering(
            channel_a="oracle_llm",
            channel_b="solver_z3",
            relation=OrderRelation.INCOMPARABLE.value,
            basis="Oracle and solver are epistemically orthogonal",
        )
    )

    # Validate coordinator
    violations = coordinator.validate()
    assert violations == [], f"Violations: {violations}"

    # Run federation
    witness = coordinator.run([s1, s2], coordinate="prop.A_implies_B")
    print(f"Witness ID: {witness.witness_id}")
    print(f"Final tier (meet of PROPOSAL, VERIFIED): {witness.final_tier}")
    assert witness.final_tier == "PROPOSAL"  # conservative meet

    # Validate witness
    w_errors = witness.validate()
    assert w_errors == [], f"Witness errors: {w_errors}"

    # Roundtrip
    d = witness.to_dict()
    w2 = EvidenceFederationReconcilingIncomparableWitness.from_dict(d)
    assert w2.witness_id == witness.witness_id

    # Merge
    w3 = witness.merge(w2)
    print(f"Merged witness tier: {w3.final_tier}")

    # Analyzer
    analyzer = EvidenceFederationReconcilingIncomparableAnalyzer(witnesses=[witness])
    print(f"Federation score: {analyzer.score():.3f}")
    print(analyzer.report())

    print("\n[PASS] All smoke tests passed.")
