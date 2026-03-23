"""Section — Obligation Splitting.

# copilot: foundations/oracle_federation §obligation-splitting — Theory2.tex
# Chapter: Controlled oracles, solver federation, and runtime witnesses

This module implements **obligation splitting** as described in Theory2.tex:
the decomposition of a *compound obligation* into independently-dischargeable
sub-obligations, each of which can be dispatched to a different oracle, solver,
or verification agent.

Background
----------
In the JuGeo trust model, a *verification obligation* is a claim that must be
discharged before a coordinate's trust tier can be promoted.  Obligations are
first-class objects in the judgment tuple ``(c, φ, A, E, O, B, T, Π)``,
where ``O`` is the set of pending obligations.

A compound obligation is one that cannot be directly discharged by a single
agent because it requires reasoning across multiple semantic domains or
verification strategies.  Obligation splitting decomposes it into a set of
*sub-obligations* with the property that:

  - Each sub-obligation can be independently discharged by a single agent.
  - The conjunction of discharged sub-obligations implies the original obligation.
  - The splitting is *sound*: if any sub-obligation is violated, the original
    obligation is violated.

Theory2.tex §7.3 (extended) defines splitting via a **splitting scheme** — a
labelled tree decomposition of the obligation into a DAG of sub-obligations
with declared dependencies.

Theory2.tex invariants
----------------------
- Judgments are tuples ``(c, φ, A, E, O, B, T, Π)`` — never booleans.
- Trust is an ordered algebra ``PROPOSAL → REVIEWED → VERIFIED`` — never a float.
- Oracle proposals always enter at ``PROPOSAL``; a discharged sub-obligation
  can raise the tier of the parent obligation only when all siblings are also
  discharged.

Public API
----------
- :class:`ObligationKind` — enum of obligation types
- :class:`DischargeStatus` — enum of discharge states
- :class:`SplittingSchemeKind` — enum of splitting strategies
- :class:`SubObligation` — a single independently-dischargeable obligation unit
- :class:`CompoundObligation` — the original multi-part obligation
- :class:`SplittingScheme` — the tree decomposition of a compound obligation
- :class:`DischargeRecord` — evidence that a sub-obligation was discharged
- :class:`ObligationGraph` — DAG of sub-obligations with dependencies
- :class:`ObligationSplittingCoordinator` — orchestrates splitting and discharge tracking
- :class:`ObligationSplittingAnalyzer` — analyzes obligation health
- :class:`ObligationSplittingWitness` — immutable certificate
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


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ObligationKind(Enum):
    """Classifies the type of a verification obligation.

    Theory2.tex defines several obligation kinds that map onto different
    verification strategies.

    SAFETY
        The formula must hold in all reachable states (safety property).
    LIVENESS
        The formula must eventually hold (liveness / progress property).
    TERMINATION
        The computation must terminate.
    TYPE_CORRECTNESS
        The term must have the declared type.
    INVARIANT
        An invariant must be preserved across all transitions.
    POSTCONDITION
        A postcondition must hold after a function call.
    PRECONDITION
        A precondition must hold before a function call.
    CUSTOM
        A domain-specific obligation not covered by the above.
    """

    SAFETY = "safety"
    LIVENESS = "liveness"
    TERMINATION = "termination"
    TYPE_CORRECTNESS = "type_correctness"
    INVARIANT = "invariant"
    POSTCONDITION = "postcondition"
    PRECONDITION = "precondition"
    CUSTOM = "custom"


class DischargeStatus(Enum):
    """Lifecycle status of a sub-obligation discharge attempt."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DISCHARGED = "discharged"      # Successfully proven
    REFUTED = "refuted"            # Counterexample found
    TIMEOUT = "timeout"
    ERROR = "error"
    BLOCKED = "blocked"            # Waiting for a dependency


class SplittingSchemeKind(Enum):
    """The decomposition strategy used to split a compound obligation.

    CONJUNCTION
        The compound obligation is a conjunction; each conjunct becomes a
        sub-obligation that can be independently discharged.
    CASE_SPLIT
        The compound obligation is split by case analysis; each case branch
        is a sub-obligation.
    MODULAR
        The obligation is split along module boundaries; each module's
        local obligation is a sub-obligation.
    INDUCTION
        Inductive splitting: a base case and one or more inductive step
        obligations.
    CUSTOM
        Domain-specific splitting.
    """

    CONJUNCTION = "conjunction"
    CASE_SPLIT = "case_split"
    MODULAR = "modular"
    INDUCTION = "induction"
    CUSTOM = "custom"


class ObligationStatus(Enum):
    """Overall status of a compound obligation."""

    OPEN = "open"
    PARTIALLY_DISCHARGED = "partially_discharged"
    FULLY_DISCHARGED = "fully_discharged"
    VIOLATED = "violated"
    SPLIT = "split"


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubObligation:
    """A single independently-dischargeable obligation unit.

    A ``SubObligation`` is the leaf node in the splitting tree.  It carries
    the formula that must be proven, the agent responsible for discharging it,
    and its current status.

    Theory2.tex: a sub-obligation ``oᵢ`` is dischargeable when there exists
    evidence ``eᵢ`` such that ``E(c) ⊨ oᵢ`` at trust tier ≥ the required tier.

    Parameters
    ----------
    sub_id:
        Unique identifier.
    parent_id:
        The :class:`CompoundObligation` this sub-obligation belongs to.
    formula:
        The logical formula to be proven (free-form string or dict).
    kind:
        :class:`ObligationKind`.
    assigned_agent:
        The oracle/solver responsible for discharging this sub-obligation.
    required_tier:
        The minimum trust tier required for a valid discharge.
    status:
        Current :class:`DischargeStatus`.
    priority:
        Numeric priority for scheduling (higher = more urgent).
    dependencies:
        IDs of sub-obligations that must be discharged before this one.
    created_at:
        Unix timestamp of creation.
    metadata:
        Extension key-value pairs.
    """

    sub_id: str = field(default_factory=lambda: "so_" + uuid.uuid4().hex[:12])
    parent_id: str = ""
    formula: str = ""
    kind: str = ObligationKind.CUSTOM.value
    assigned_agent: str = ""
    required_tier: str = "PROPOSAL"
    status: str = DischargeStatus.PENDING.value
    priority: int = 0
    dependencies: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def is_discharged(self) -> bool:
        return self.status == DischargeStatus.DISCHARGED.value

    def is_blocked(self) -> bool:
        return self.status == DischargeStatus.BLOCKED.value

    def with_status(self, status: DischargeStatus) -> SubObligation:
        """Return a copy of this sub-obligation with the given status."""
        return replace(self, status=status.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_id": self.sub_id,
            "parent_id": self.parent_id,
            "formula": self.formula,
            "kind": self.kind,
            "assigned_agent": self.assigned_agent,
            "required_tier": self.required_tier,
            "status": self.status,
            "priority": self.priority,
            "dependencies": list(self.dependencies),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SubObligation:
        return cls(
            sub_id=d.get("sub_id", "so_" + uuid.uuid4().hex[:12]),
            parent_id=d.get("parent_id", ""),
            formula=d.get("formula", ""),
            kind=d.get("kind", ObligationKind.CUSTOM.value),
            assigned_agent=d.get("assigned_agent", ""),
            required_tier=d.get("required_tier", "PROPOSAL"),
            status=d.get("status", DischargeStatus.PENDING.value),
            priority=int(d.get("priority", 0)),
            dependencies=tuple(d.get("dependencies", [])),
            created_at=float(d.get("created_at", time.time())),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class CompoundObligation:
    """A compound (multi-part) obligation that must be split before discharge.

    A ``CompoundObligation`` is the root of the splitting tree.  It carries
    the original formula and the coordinate at which the obligation was raised.

    Parameters
    ----------
    obligation_id:
        Unique identifier.
    coordinate:
        The coordinate at which this obligation is raised.
    formula:
        The original compound formula.
    kind:
        :class:`ObligationKind` of the compound obligation.
    status:
        :class:`ObligationStatus`.
    required_tier:
        Minimum trust tier for the final discharge.
    sub_obligation_ids:
        IDs of the :class:`SubObligation` objects produced by splitting.
    splitting_scheme_id:
        The :class:`SplittingScheme` used to decompose this obligation.
    raised_by:
        Agent or system that raised the obligation.
    raised_at:
        Unix timestamp of creation.
    metadata:
        Extension key-value pairs.
    """

    obligation_id: str = field(default_factory=lambda: "co_" + uuid.uuid4().hex[:12])
    coordinate: str = ""
    formula: str = ""
    kind: str = ObligationKind.CUSTOM.value
    status: str = ObligationStatus.OPEN.value
    required_tier: str = "PROPOSAL"
    sub_obligation_ids: tuple[str, ...] = ()
    splitting_scheme_id: str = ""
    raised_by: str = ""
    raised_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def is_split(self) -> bool:
        return bool(self.sub_obligation_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "coordinate": self.coordinate,
            "formula": self.formula,
            "kind": self.kind,
            "status": self.status,
            "required_tier": self.required_tier,
            "sub_obligation_ids": list(self.sub_obligation_ids),
            "splitting_scheme_id": self.splitting_scheme_id,
            "raised_by": self.raised_by,
            "raised_at": self.raised_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CompoundObligation:
        return cls(
            obligation_id=d.get("obligation_id", "co_" + uuid.uuid4().hex[:12]),
            coordinate=d.get("coordinate", ""),
            formula=d.get("formula", ""),
            kind=d.get("kind", ObligationKind.CUSTOM.value),
            status=d.get("status", ObligationStatus.OPEN.value),
            required_tier=d.get("required_tier", "PROPOSAL"),
            sub_obligation_ids=tuple(d.get("sub_obligation_ids", [])),
            splitting_scheme_id=d.get("splitting_scheme_id", ""),
            raised_by=d.get("raised_by", ""),
            raised_at=float(d.get("raised_at", time.time())),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class SplittingScheme:
    """A decomposition strategy for a compound obligation.

    The splitting scheme declares how to split a compound formula into
    independently-dischargeable sub-obligations.

    Parameters
    ----------
    scheme_id:
        Unique identifier.
    kind:
        :class:`SplittingSchemeKind`.
    obligation_id:
        The compound obligation being split.
    sub_formulas:
        The list of sub-formulas produced by the split (in order).
    conjunctive:
        If True, all sub-obligations must be discharged for the parent to be
        discharged.  If False, at least one must be discharged (disjunctive split).
    description:
        Human-readable description of the splitting rationale.
    soundness_proof:
        A reference to or embedding of the soundness argument for this split.
    created_at:
        Unix timestamp.
    """

    scheme_id: str = field(default_factory=lambda: "ss_" + uuid.uuid4().hex[:12])
    kind: str = SplittingSchemeKind.CONJUNCTION.value
    obligation_id: str = ""
    sub_formulas: tuple[str, ...] = ()
    conjunctive: bool = True
    description: str = ""
    soundness_proof: str = ""
    created_at: float = field(default_factory=time.time)

    def is_sound(self) -> bool:
        """Return True if the scheme has a soundness proof recorded."""
        return bool(self.soundness_proof)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme_id": self.scheme_id,
            "kind": self.kind,
            "obligation_id": self.obligation_id,
            "sub_formulas": list(self.sub_formulas),
            "conjunctive": self.conjunctive,
            "description": self.description,
            "soundness_proof": self.soundness_proof,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SplittingScheme:
        return cls(
            scheme_id=d.get("scheme_id", "ss_" + uuid.uuid4().hex[:12]),
            kind=d.get("kind", SplittingSchemeKind.CONJUNCTION.value),
            obligation_id=d.get("obligation_id", ""),
            sub_formulas=tuple(d.get("sub_formulas", [])),
            conjunctive=bool(d.get("conjunctive", True)),
            description=d.get("description", ""),
            soundness_proof=d.get("soundness_proof", ""),
            created_at=float(d.get("created_at", time.time())),
        )


@dataclass(frozen=True, slots=True)
class DischargeRecord:
    """Evidence that a sub-obligation was discharged by an agent.

    A ``DischargeRecord`` is produced when an agent (oracle, solver, or human)
    successfully discharges a :class:`SubObligation`.  It carries the evidence
    and the trust tier of the discharge.

    Theory2.tex: a discharge record is the object-level evidence ``eᵢ`` that
    satisfies sub-obligation ``oᵢ``.

    Parameters
    ----------
    record_id:
        Unique identifier.
    sub_obligation_id:
        The sub-obligation that was discharged.
    agent_id:
        The discharging agent.
    trust_tier:
        The trust tier at which the discharge was accepted.
    evidence_summary:
        A short description or reference to the evidence.
    evidence_content:
        The actual evidence content (free-form dict).
    discharged_at:
        Unix timestamp.
    latency_seconds:
        Time taken by the agent to produce the discharge.
    metadata:
        Extension key-value pairs.
    """

    record_id: str = field(default_factory=lambda: "dr_" + uuid.uuid4().hex[:12])
    sub_obligation_id: str = ""
    agent_id: str = ""
    trust_tier: str = "PROPOSAL"
    evidence_summary: str = ""
    evidence_content: dict = field(default_factory=dict)
    discharged_at: float = field(default_factory=time.time)
    latency_seconds: float = 0.0
    metadata: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        body = json.dumps(
            {"sub_obligation_id": self.sub_obligation_id,
             "agent_id": self.agent_id,
             "trust_tier": self.trust_tier,
             "evidence_content": self.evidence_content},
            sort_keys=True,
        )
        return hashlib.sha256(body.encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "sub_obligation_id": self.sub_obligation_id,
            "agent_id": self.agent_id,
            "trust_tier": self.trust_tier,
            "evidence_summary": self.evidence_summary,
            "evidence_content": dict(self.evidence_content),
            "discharged_at": self.discharged_at,
            "latency_seconds": self.latency_seconds,
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DischargeRecord:
        return cls(
            record_id=d.get("record_id", "dr_" + uuid.uuid4().hex[:12]),
            sub_obligation_id=d.get("sub_obligation_id", ""),
            agent_id=d.get("agent_id", ""),
            trust_tier=d.get("trust_tier", "PROPOSAL"),
            evidence_summary=d.get("evidence_summary", ""),
            evidence_content=dict(d.get("evidence_content", {})),
            discharged_at=float(d.get("discharged_at", time.time())),
            latency_seconds=float(d.get("latency_seconds", 0.0)),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass
class ObligationGraph:
    """A DAG of sub-obligations with declared inter-dependencies.

    The obligation graph is the primary data structure for tracking the
    progress of a split obligation.  It supports:
    - Adding and removing sub-obligations.
    - Dependency resolution (topological ordering).
    - Status aggregation to determine if the parent is fully discharged.
    - Detection of cycles (invalid dependency structures).

    Theory2.tex: the obligation graph corresponds to the nerve of the
    splitting cover — each sub-obligation is a vertex, dependencies are edges.
    """

    graph_id: str = field(default_factory=lambda: "og_" + uuid.uuid4().hex[:12])
    obligation_id: str = ""
    nodes: dict[str, SubObligation] = field(default_factory=dict)  # sub_id → SubObligation
    edges: dict[str, list[str]] = field(default_factory=dict)  # sub_id → [dependency sub_ids]
    discharge_records: dict[str, DischargeRecord] = field(default_factory=dict)  # sub_id → DischargeRecord

    def add_node(self, sub: SubObligation) -> None:
        """Add a sub-obligation as a node in the graph."""
        self.nodes[sub.sub_id] = sub
        if sub.dependencies:
            self.edges[sub.sub_id] = list(sub.dependencies)
        logger.debug("Added node %s to obligation graph %s", sub.sub_id, self.graph_id)

    def remove_node(self, sub_id: str) -> SubObligation | None:
        """Remove a sub-obligation from the graph."""
        node = self.nodes.pop(sub_id, None)
        self.edges.pop(sub_id, None)
        return node

    def record_discharge(self, sub_id: str, record: DischargeRecord) -> None:
        """Record that *sub_id* was discharged by *record*.

        Updates the node's status to ``DISCHARGED``.
        """
        if sub_id in self.nodes:
            self.nodes[sub_id] = self.nodes[sub_id].with_status(DischargeStatus.DISCHARGED)
        self.discharge_records[sub_id] = record

    def record_refutation(self, sub_id: str) -> None:
        """Mark *sub_id* as refuted (counterexample found)."""
        if sub_id in self.nodes:
            self.nodes[sub_id] = self.nodes[sub_id].with_status(DischargeStatus.REFUTED)

    def ready_nodes(self) -> list[SubObligation]:
        """Return sub-obligations that are PENDING and have all dependencies discharged."""
        result = []
        for sub_id, sub in self.nodes.items():
            if sub.status != DischargeStatus.PENDING.value:
                continue
            deps = self.edges.get(sub_id, [])
            if all(
                self.nodes.get(d, SubObligation()).status == DischargeStatus.DISCHARGED.value
                for d in deps
            ):
                result.append(sub)
        return sorted(result, key=lambda s: -s.priority)

    def all_discharged(self) -> bool:
        """Return True if all nodes are DISCHARGED."""
        return all(
            s.status == DischargeStatus.DISCHARGED.value for s in self.nodes.values()
        )

    def any_refuted(self) -> bool:
        """Return True if any node is REFUTED."""
        return any(s.status == DischargeStatus.REFUTED.value for s in self.nodes.values())

    def topological_order(self) -> list[str]:
        """Return sub_ids in topological order (dependencies before dependents).

        Raises
        ------
        ValueError
            If a cycle is detected.
        """
        visited: set[str] = set()
        stack: list[str] = []
        temp: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in temp:
                raise ValueError(f"Cycle detected at node {node_id!r}")
            if node_id in visited:
                return
            temp.add(node_id)
            for dep in self.edges.get(node_id, []):
                if dep in self.nodes:
                    visit(dep)
            temp.discard(node_id)
            visited.add(node_id)
            stack.append(node_id)

        for nid in self.nodes:
            visit(nid)
        return stack

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for s in self.nodes.values():
            counts[s.status] = counts.get(s.status, 0) + 1
        return {
            "total": len(self.nodes),
            "by_status": counts,
            "all_discharged": self.all_discharged(),
            "any_refuted": self.any_refuted(),
            "ready_count": len(self.ready_nodes()),
        }


# ---------------------------------------------------------------------------
# Witness (immutable certificate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObligationSplittingWitness:
    """Immutable certificate produced by an obligation splitting run.

    Captures the compound obligation, the splitting scheme applied, the
    sub-obligations created, discharge records, and the Theory2.tex
    judgment tuple ``(c, φ, A, E, O, B, T, Π)``.

    Parameters
    ----------
    witness_id:
        Globally unique identifier.
    coordinate:
        The coordinate at which the obligation was raised.
    compound_obligation_id:
        The :class:`CompoundObligation` that was split.
    splitting_scheme_id:
        The :class:`SplittingScheme` applied.
    sub_obligation_ids:
        IDs of all :class:`SubObligation` objects produced.
    discharged_sub_ids:
        IDs of sub-obligations that have been discharged.
    refuted_sub_ids:
        IDs of sub-obligations that have been refuted.
    final_status:
        The :class:`ObligationStatus` of the compound obligation.
    final_tier:
        Trust tier of the obligation discharge (PROPOSAL until all subs discharged).
    judgment_tuple:
        ``(c, φ, A, E, O, B, T, Π)`` as a dict.
    discharge_records:
        All :class:`DischargeRecord` objects collected.
    created_at:
        ISO-8601 UTC timestamp.
    metadata:
        Extension key-value pairs.
    """

    witness_id: str = field(default_factory=lambda: "osw_" + uuid.uuid4().hex[:12])
    coordinate: str = ""
    compound_obligation_id: str = ""
    splitting_scheme_id: str = ""
    sub_obligation_ids: tuple[str, ...] = ()
    discharged_sub_ids: tuple[str, ...] = ()
    refuted_sub_ids: tuple[str, ...] = ()
    final_status: str = ObligationStatus.OPEN.value
    final_tier: str = "PROPOSAL"
    judgment_tuple: dict = field(default_factory=dict)
    discharge_records: tuple[DischargeRecord, ...] = ()
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict = field(default_factory=dict)

    # ---- serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "witness_id": self.witness_id,
            "coordinate": self.coordinate,
            "compound_obligation_id": self.compound_obligation_id,
            "splitting_scheme_id": self.splitting_scheme_id,
            "sub_obligation_ids": list(self.sub_obligation_ids),
            "discharged_sub_ids": list(self.discharged_sub_ids),
            "refuted_sub_ids": list(self.refuted_sub_ids),
            "final_status": self.final_status,
            "final_tier": self.final_tier,
            "judgment_tuple": dict(self.judgment_tuple),
            "discharge_records": [r.to_dict() for r in self.discharge_records],
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObligationSplittingWitness:
        return cls(
            witness_id=d.get("witness_id", "osw_" + uuid.uuid4().hex[:12]),
            coordinate=d.get("coordinate", ""),
            compound_obligation_id=d.get("compound_obligation_id", ""),
            splitting_scheme_id=d.get("splitting_scheme_id", ""),
            sub_obligation_ids=tuple(d.get("sub_obligation_ids", [])),
            discharged_sub_ids=tuple(d.get("discharged_sub_ids", [])),
            refuted_sub_ids=tuple(d.get("refuted_sub_ids", [])),
            final_status=d.get("final_status", ObligationStatus.OPEN.value),
            final_tier=d.get("final_tier", "PROPOSAL"),
            judgment_tuple=dict(d.get("judgment_tuple", {})),
            discharge_records=tuple(
                DischargeRecord.from_dict(r) for r in d.get("discharge_records", [])
            ),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            metadata=dict(d.get("metadata", {})),
        )

    def validate(self) -> list[str]:
        """Return invariant violations (empty = valid).

        Checks:
        - ``final_tier`` is a known tier label.
        - ``judgment_tuple`` is non-empty.
        - ``sub_obligation_ids`` is non-empty.
        - discharged and refuted sub-IDs are subsets of sub-obligation IDs.
        """
        errors: list[str] = []
        if self.final_tier not in _TRUST_ORDER:
            errors.append(f"final_tier {self.final_tier!r} not in trust algebra")
        if not self.judgment_tuple:
            errors.append("judgment_tuple must be non-empty (Theory2.tex invariant)")
        if not self.sub_obligation_ids:
            errors.append("sub_obligation_ids must be non-empty")
        sub_set = set(self.sub_obligation_ids)
        for sid in self.discharged_sub_ids:
            if sid not in sub_set:
                errors.append(f"Discharged sub_id {sid!r} not in sub_obligation_ids")
        for sid in self.refuted_sub_ids:
            if sid not in sub_set:
                errors.append(f"Refuted sub_id {sid!r} not in sub_obligation_ids")
        return errors

    def merge(self, other: ObligationSplittingWitness) -> ObligationSplittingWitness:
        """Merge two obligation witnesses (conservative: weaker tier, union of sub-IDs)."""
        my_rank = _TRUST_ORDER.get(self.final_tier, 0)
        other_rank = _TRUST_ORDER.get(other.final_tier, 0)
        merged_tier = self.final_tier if my_rank <= other_rank else other.final_tier
        merged_sub_ids = tuple(set(self.sub_obligation_ids) | set(other.sub_obligation_ids))
        merged_discharged = tuple(set(self.discharged_sub_ids) | set(other.discharged_sub_ids))
        merged_refuted = tuple(set(self.refuted_sub_ids) | set(other.refuted_sub_ids))
        merged_records = self.discharge_records + other.discharge_records
        merged_meta = {
            **self.metadata, **other.metadata,
            "merged_from": [self.witness_id, other.witness_id],
        }
        # Status: violated > open > partially > fully
        status_priority = {
            ObligationStatus.VIOLATED.value: 3,
            ObligationStatus.OPEN.value: 2,
            ObligationStatus.PARTIALLY_DISCHARGED.value: 1,
            ObligationStatus.FULLY_DISCHARGED.value: 0,
        }
        merged_status = max(
            [self.final_status, other.final_status],
            key=lambda s: status_priority.get(s, 2),
        )
        return replace(
            self,
            witness_id="osw_" + uuid.uuid4().hex[:12],
            final_tier=merged_tier,
            sub_obligation_ids=merged_sub_ids,
            discharged_sub_ids=merged_discharged,
            refuted_sub_ids=merged_refuted,
            final_status=merged_status,
            discharge_records=merged_records,
            metadata=merged_meta,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def discharge_fraction(self) -> float:
        """Return the fraction of sub-obligations that have been discharged."""
        if not self.sub_obligation_ids:
            return 0.0
        return len(self.discharged_sub_ids) / len(self.sub_obligation_ids)

    def content_hash(self) -> str:
        content = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


@dataclass
class ObligationSplittingCoordinator:
    """Orchestrates obligation splitting and discharge tracking.

    Implements Theory2.tex obligation splitting:
    1. Accept a :class:`CompoundObligation` and a :class:`SplittingScheme`.
    2. Construct :class:`SubObligation` objects from the scheme.
    3. Build an :class:`ObligationGraph` with declared dependencies.
    4. Track discharge events and update the graph.
    5. Compute the final status and emit a :class:`ObligationSplittingWitness`.

    Parameters
    ----------
    coordinator_id:
        Unique identifier.
    obligations:
        All registered compound obligations.
    graphs:
        Mapping from obligation_id to :class:`ObligationGraph`.
    schemes:
        Registered splitting schemes.
    history:
        All witnesses produced.
    default_scheme_kind:
        Default splitting strategy.
    """

    coordinator_id: str = field(default_factory=lambda: "osc_" + uuid.uuid4().hex[:12])
    obligations: dict[str, CompoundObligation] = field(default_factory=dict)
    graphs: dict[str, ObligationGraph] = field(default_factory=dict)
    schemes: dict[str, SplittingScheme] = field(default_factory=dict)
    history: list[ObligationSplittingWitness] = field(default_factory=list)
    default_scheme_kind: str = SplittingSchemeKind.CONJUNCTION.value

    # ---- registration ----

    def register_obligation(self, obligation: CompoundObligation) -> None:
        """Register a compound obligation."""
        self.obligations[obligation.obligation_id] = obligation
        logger.debug(
            "Registered obligation %s for coord %s",
            obligation.obligation_id, obligation.coordinate,
        )

    def register_scheme(self, scheme: SplittingScheme) -> None:
        """Register a splitting scheme."""
        self.schemes[scheme.scheme_id] = scheme

    # ---- splitting ----

    def split(
        self,
        obligation: CompoundObligation,
        scheme: SplittingScheme,
        agent_assignment: dict[str, str] | None = None,
    ) -> list[SubObligation]:
        """Apply *scheme* to *obligation* and return the sub-obligations.

        Parameters
        ----------
        obligation:
            The compound obligation to split.
        scheme:
            The splitting scheme to apply.
        agent_assignment:
            Optional mapping from sub-formula index (as string) to agent_id.
            E.g. ``{"0": "solver_z3", "1": "lean_oracle"}``.

        Returns
        -------
        list[SubObligation]
            One sub-obligation per sub-formula in the scheme.
        """
        subs = []
        assignment = agent_assignment or {}
        for i, formula in enumerate(scheme.sub_formulas):
            agent = assignment.get(str(i), "")
            sub = SubObligation(
                parent_id=obligation.obligation_id,
                formula=formula,
                kind=obligation.kind,
                assigned_agent=agent,
                required_tier=obligation.required_tier,
                status=DischargeStatus.PENDING.value,
                priority=len(scheme.sub_formulas) - i,  # earlier = higher priority
            )
            subs.append(sub)
        return subs

    def build_graph(
        self,
        obligation: CompoundObligation,
        subs: list[SubObligation],
        dependency_map: dict[str, list[str]] | None = None,
    ) -> ObligationGraph:
        """Build an :class:`ObligationGraph` for *obligation* with *subs*.

        Parameters
        ----------
        obligation:
            The parent compound obligation.
        subs:
            The sub-obligations (already created by :meth:`split`).
        dependency_map:
            Optional mapping from sub_id to list of dependency sub_ids.
        """
        graph = ObligationGraph(
            obligation_id=obligation.obligation_id,
        )
        dep_map = dependency_map or {}
        for i, sub in enumerate(subs):
            # If no explicit dependency map, assume linear chain (for INDUCTION)
            deps: list[str] = dep_map.get(sub.sub_id, [])
            if not deps and i > 0 and obligation.kind == ObligationKind.TERMINATION.value:
                deps = [subs[i - 1].sub_id]
            final_sub = replace(sub, dependencies=tuple(deps))
            graph.add_node(final_sub)
        return graph

    # ---- discharge ----

    def record_discharge(
        self,
        obligation_id: str,
        sub_id: str,
        agent_id: str,
        evidence: dict[str, Any],
        claimed_tier: str = "PROPOSAL",
    ) -> DischargeRecord:
        """Record that *sub_id* in *obligation_id* was discharged by *agent_id*.

        The claimed tier is accepted at face value but stored as ``PROPOSAL``
        unless the parent obligation has all sub-obligations discharged AND
        the required tier is higher (in which case the coordinator can promote).
        """
        # Oracle proposals enter at PROPOSAL
        accepted_tier = "PROPOSAL"
        record = DischargeRecord(
            sub_obligation_id=sub_id,
            agent_id=agent_id,
            trust_tier=accepted_tier,
            evidence_summary=f"Discharged by {agent_id}",
            evidence_content=evidence,
        )
        graph = self.graphs.get(obligation_id)
        if graph:
            graph.record_discharge(sub_id, record)
        return record

    def record_refutation(
        self,
        obligation_id: str,
        sub_id: str,
    ) -> None:
        """Mark *sub_id* as refuted (counterexample found)."""
        graph = self.graphs.get(obligation_id)
        if graph:
            graph.record_refutation(sub_id)

    # ---- status computation ----

    def compute_status(
        self,
        obligation: CompoundObligation,
        graph: ObligationGraph,
        scheme: SplittingScheme,
    ) -> tuple[ObligationStatus, str]:
        """Compute the :class:`ObligationStatus` and trust tier of *obligation*.

        Returns
        -------
        (status, tier)
        """
        if graph.any_refuted():
            return ObligationStatus.VIOLATED, "PROPOSAL"
        if graph.all_discharged():
            # All sub-obligations discharged → tier can be promoted to required tier
            tier = obligation.required_tier
            return ObligationStatus.FULLY_DISCHARGED, tier
        n_total = len(graph.nodes)
        n_discharged = sum(
            1 for s in graph.nodes.values()
            if s.status == DischargeStatus.DISCHARGED.value
        )
        if n_discharged == 0:
            return ObligationStatus.OPEN, "PROPOSAL"
        return ObligationStatus.PARTIALLY_DISCHARGED, "PROPOSAL"

    # ---- main entry point ----

    def run(
        self,
        obligation: CompoundObligation,
        scheme: SplittingScheme | None = None,
        agent_assignment: dict[str, str] | None = None,
        dependency_map: dict[str, list[str]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObligationSplittingWitness:
        """Split *obligation*, set up graph, and produce an initial witness.

        This method performs the *splitting* step.  To record discharge events,
        call :meth:`record_discharge` and then :meth:`checkpoint` to produce
        an updated witness.

        Parameters
        ----------
        obligation:
            The compound obligation to split.
        scheme:
            The splitting scheme.  If None, a default conjunction scheme is
            created from the obligation's formula.
        agent_assignment:
            Mapping from sub-formula index to agent_id.
        dependency_map:
            Explicit dependency map for the obligation graph.
        metadata:
            Extra metadata.

        Returns
        -------
        ObligationSplittingWitness
        """
        if scheme is None:
            # Create a default 2-part conjunction scheme
            scheme = SplittingScheme(
                kind=self.default_scheme_kind,
                obligation_id=obligation.obligation_id,
                sub_formulas=(f"{obligation.formula} [part 1]", f"{obligation.formula} [part 2]"),
                conjunctive=True,
                description="Auto-generated default conjunction split",
                soundness_proof="",
            )

        self.register_obligation(obligation)
        self.register_scheme(scheme)

        subs = self.split(obligation, scheme, agent_assignment)
        graph = self.build_graph(obligation, subs, dependency_map)
        self.graphs[obligation.obligation_id] = graph

        status, tier = self.compute_status(obligation, graph, scheme)
        sub_ids = tuple(graph.nodes.keys())
        discharged_ids = tuple(
            sid for sid, s in graph.nodes.items()
            if s.status == DischargeStatus.DISCHARGED.value
        )
        refuted_ids = tuple(
            sid for sid, s in graph.nodes.items()
            if s.status == DischargeStatus.REFUTED.value
        )

        w = ObligationSplittingWitness(
            coordinate=obligation.coordinate,
            compound_obligation_id=obligation.obligation_id,
            splitting_scheme_id=scheme.scheme_id,
            sub_obligation_ids=sub_ids,
            discharged_sub_ids=discharged_ids,
            refuted_sub_ids=refuted_ids,
            final_status=status.value,
            final_tier=tier,
            judgment_tuple=self._build_judgment_tuple(
                obligation, sub_ids, tier, status
            ),
            discharge_records=tuple(graph.discharge_records.values()),
            metadata=metadata or {},
        )
        self.history.append(w)
        logger.info(
            "Obligation split: id=%s subs=%d status=%s tier=%s",
            obligation.obligation_id, len(subs), status.value, tier,
        )
        return w

    def checkpoint(
        self,
        obligation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ObligationSplittingWitness:
        """Produce an updated witness reflecting current discharge progress.

        Call this after recording one or more discharge events to get a fresh
        witness with updated status.
        """
        obligation = self.obligations.get(obligation_id)
        graph = self.graphs.get(obligation_id)
        if obligation is None or graph is None:
            raise ValueError(f"Unknown obligation_id {obligation_id!r}")
        scheme_id = obligation.splitting_scheme_id or (
            list(self.schemes.keys())[-1] if self.schemes else ""
        )
        scheme = self.schemes.get(scheme_id, SplittingScheme())
        status, tier = self.compute_status(obligation, graph, scheme)
        sub_ids = tuple(graph.nodes.keys())
        discharged_ids = tuple(
            sid for sid, s in graph.nodes.items()
            if s.status == DischargeStatus.DISCHARGED.value
        )
        refuted_ids = tuple(
            sid for sid, s in graph.nodes.items()
            if s.status == DischargeStatus.REFUTED.value
        )
        w = ObligationSplittingWitness(
            coordinate=obligation.coordinate,
            compound_obligation_id=obligation_id,
            splitting_scheme_id=scheme_id,
            sub_obligation_ids=sub_ids,
            discharged_sub_ids=discharged_ids,
            refuted_sub_ids=refuted_ids,
            final_status=status.value,
            final_tier=tier,
            judgment_tuple=self._build_judgment_tuple(
                obligation, sub_ids, tier, status
            ),
            discharge_records=tuple(graph.discharge_records.values()),
            metadata=metadata or {},
        )
        self.history.append(w)
        return w

    def _build_judgment_tuple(
        self,
        obligation: CompoundObligation,
        sub_ids: tuple[str, ...],
        trust_tier: str,
        status: ObligationStatus,
    ) -> dict[str, Any]:
        return {
            "c": obligation.coordinate,
            "phi": obligation.formula,
            "A": obligation.raised_by,
            "E": {"sub_count": len(sub_ids), "status": status.value},
            "O": list(sub_ids),
            "B": self.default_scheme_kind,
            "T": trust_tier,
            "Pi": self.coordinator_id,
        }

    # ---- introspection ----

    def open_obligations(self) -> list[CompoundObligation]:
        """Return all obligations that are not yet fully discharged."""
        return [
            o for o in self.obligations.values()
            if o.status not in (ObligationStatus.FULLY_DISCHARGED.value, ObligationStatus.VIOLATED.value)
        ]

    def ready_sub_obligations(self, obligation_id: str) -> list[SubObligation]:
        """Return sub-obligations that are ready to be discharged."""
        graph = self.graphs.get(obligation_id)
        if graph is None:
            return []
        return graph.ready_nodes()

    def discharge_fraction(self, obligation_id: str) -> float:
        """Return the fraction of sub-obligations discharged for *obligation_id*."""
        graph = self.graphs.get(obligation_id)
        if graph is None:
            return 0.0
        n_total = len(graph.nodes)
        if n_total == 0:
            return 0.0
        n_done = sum(
            1 for s in graph.nodes.values()
            if s.status == DischargeStatus.DISCHARGED.value
        )
        return n_done / n_total

    def validate(self) -> list[str]:
        """Return invariant violations for this coordinator."""
        errors: list[str] = []
        for oid, obs in self.obligations.items():
            if obs.required_tier not in _TRUST_ORDER:
                errors.append(f"Obligation {oid!r} has invalid required_tier {obs.required_tier!r}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinator_id": self.coordinator_id,
            "obligation_count": len(self.obligations),
            "scheme_count": len(self.schemes),
            "history_count": len(self.history),
            "default_scheme_kind": self.default_scheme_kind,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObligationSplittingCoordinator:
        return cls(
            coordinator_id=d.get("coordinator_id", "osc_" + uuid.uuid4().hex[:12]),
            default_scheme_kind=d.get("default_scheme_kind", SplittingSchemeKind.CONJUNCTION.value),
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


@dataclass
class ObligationSplittingAnalyzer:
    """Analyzes a corpus of obligation splitting witnesses to assess discharge health.

    Provides metrics on discharge rates, refutation rates, obligation age,
    agent productivity, and anomaly detection.

    Theory2.tex relevance: a healthy obligation lifecycle should see most
    obligations reach FULLY_DISCHARGED with low refutation rates.  High
    refutation rates indicate that the splitting scheme is producing sub-
    obligations that cannot be discharged — a signal for revision.
    """

    analyzer_id: str = field(default_factory=lambda: "osa_" + uuid.uuid4().hex[:12])
    witnesses: list[ObligationSplittingWitness] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict)

    def load(self, witnesses: Sequence[ObligationSplittingWitness]) -> None:
        self.witnesses = list(witnesses)
        self._cache.clear()

    def append(self, witness: ObligationSplittingWitness) -> None:
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
            "discharge_stats": self._discharge_stats(),
            "agent_productivity": self._agent_productivity(),
            "anomalies": self._detect_anomalies(),
        }
        self._cache["analysis"] = result
        return result

    def score(self) -> float:
        """Return an obligation-health score in [0, 1].

        Higher = more obligations fully discharged, fewer violated or open.
        """
        if not self.witnesses:
            return 0.0
        a = self.analyze()
        total = a["total"]
        fully = a["status_distribution"].get(ObligationStatus.FULLY_DISCHARGED.value, 0)
        violated = a["status_distribution"].get(ObligationStatus.VIOLATED.value, 0)
        anomaly_count = len(a.get("anomalies", []))
        health = fully / total if total > 0 else 0.0
        penalty = (violated / total if total > 0 else 0.0) + anomaly_count * 0.05
        return max(0.0, min(1.0, health - penalty))

    def report(self) -> str:
        """Return a human-readable multi-line report."""
        a = self.analyze()
        lines = [
            "=== ObligationSplitting Analysis Report ===",
            f"Total witnesses: {a['total']}",
            f"Obligation-health score: {self.score():.3f}",
            "",
            "--- Status distribution ---",
        ]
        for status, count in a["status_distribution"].items():
            lines.append(f"  {status}: {count}")
        lines += ["", "--- Discharge stats ---"]
        for k, v in a["discharge_stats"].items():
            lines.append(f"  {k}: {v}")
        lines += ["", "--- Agent productivity ---"]
        for agent, count in a["agent_productivity"].items():
            lines.append(f"  {agent}: {count} discharges")
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
            "fully_discharged_rate": (
                self.analyze()["status_distribution"].get(
                    ObligationStatus.FULLY_DISCHARGED.value, 0
                ) / max(1, len(self.witnesses))
            ),
        }

    # ---- specialist metrics ----

    def discharge_fraction_per_witness(self) -> list[tuple[str, float]]:
        """Return (witness_id, discharge_fraction) for all witnesses."""
        return [
            (w.witness_id, w.discharge_fraction()) for w in self.witnesses
        ]

    def most_productive_agents(self, top_n: int = 5) -> list[tuple[str, int]]:
        """Return the *top_n* agents by number of discharge records."""
        productivity = self._agent_productivity()
        return sorted(productivity.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    def violated_obligations(self) -> list[ObligationSplittingWitness]:
        """Return witnesses where the obligation was violated."""
        return [
            w for w in self.witnesses
            if w.final_status == ObligationStatus.VIOLATED.value
        ]

    def stuck_obligations(self) -> list[ObligationSplittingWitness]:
        """Return witnesses that are OPEN (no sub-obligations discharged at all)."""
        return [
            w for w in self.witnesses
            if w.final_status == ObligationStatus.OPEN.value
        ]

    # ---- private helpers ----

    def _status_distribution(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            counts[w.final_status] = counts.get(w.final_status, 0) + 1
        return counts

    def _tier_distribution(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            counts[w.final_tier] = counts.get(w.final_tier, 0) + 1
        return counts

    def _discharge_stats(self) -> dict[str, Any]:
        total_sub = sum(len(w.sub_obligation_ids) for w in self.witnesses)
        total_discharged = sum(len(w.discharged_sub_ids) for w in self.witnesses)
        total_refuted = sum(len(w.refuted_sub_ids) for w in self.witnesses)
        fractions = [w.discharge_fraction() for w in self.witnesses]
        mean_frac = sum(fractions) / len(fractions) if fractions else 0.0
        return {
            "total_sub_obligations": total_sub,
            "total_discharged": total_discharged,
            "total_refuted": total_refuted,
            "mean_discharge_fraction": mean_frac,
            "refutation_rate": total_refuted / max(1, total_sub),
        }

    def _agent_productivity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in self.witnesses:
            for dr in w.discharge_records:
                counts[dr.agent_id] = counts.get(dr.agent_id, 0) + 1
        return counts

    def _detect_anomalies(self) -> list[str]:
        anomalies = []
        stats = self._discharge_stats()
        refutation_rate = stats.get("refutation_rate", 0.0)
        if refutation_rate > 0.2:
            anomalies.append(
                f"High refutation rate {refutation_rate:.1%} — "
                "splitting scheme may be producing un-dischargeable sub-obligations"
            )
        mean_frac = stats.get("mean_discharge_fraction", 0.0)
        if mean_frac < 0.3 and len(self.witnesses) > 3:
            anomalies.append(
                f"Low mean discharge fraction {mean_frac:.1%} — "
                "many obligations are stuck or agents are unassigned"
            )
        return anomalies


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== obligation_splitting.py smoke test ===")

    # Create a compound obligation
    obligation = CompoundObligation(
        coordinate="prog.sum_correct",
        formula="sum(0..n) = n*(n+1)/2",
        kind=ObligationKind.SAFETY.value,
        required_tier="VERIFIED",
        raised_by="verification_harness",
    )

    # Create a conjunction splitting scheme
    scheme = SplittingScheme(
        kind=SplittingSchemeKind.CONJUNCTION.value,
        obligation_id=obligation.obligation_id,
        sub_formulas=(
            "base_case: sum(0..0) = 0*(0+1)/2",
            "inductive_step: sum(0..n+1) = sum(0..n) + (n+1)",
        ),
        conjunctive=True,
        description="Proof by induction on n",
        soundness_proof="Standard induction principle for natural numbers",
    )

    # Build coordinator and run
    coordinator = ObligationSplittingCoordinator()
    witness = coordinator.run(
        obligation=obligation,
        scheme=scheme,
        agent_assignment={"0": "lean_solver", "1": "lean_solver"},
    )
    print(f"Witness ID: {witness.witness_id}")
    print(f"Sub-obligations: {witness.sub_obligation_ids}")
    print(f"Final status: {witness.final_status}")
    print(f"Final tier: {witness.final_tier}")
    assert witness.final_tier == "PROPOSAL"  # Not yet discharged
    assert witness.final_status == ObligationStatus.OPEN.value

    # Validate witness
    w_errors = witness.validate()
    assert w_errors == [], f"Witness errors: {w_errors}"

    # Simulate discharge of base case
    sub_id_0 = witness.sub_obligation_ids[0]
    record = coordinator.record_discharge(
        obligation_id=obligation.obligation_id,
        sub_id=sub_id_0,
        agent_id="lean_solver",
        evidence={"proof": "sum(0) = 0 = 0*(0+1)/2 by definition"},
        claimed_tier="VERIFIED",
    )
    print(f"Discharged sub-obligation {sub_id_0}: tier={record.trust_tier}")

    # Checkpoint
    w2 = coordinator.checkpoint(obligation.obligation_id)
    print(f"After one discharge: status={w2.final_status}, fraction={w2.discharge_fraction():.2f}")

    # Discharge second sub-obligation
    sub_id_1 = witness.sub_obligation_ids[1]
    coordinator.record_discharge(
        obligation_id=obligation.obligation_id,
        sub_id=sub_id_1,
        agent_id="lean_solver",
        evidence={"proof": "inductive step verified by lean4"},
        claimed_tier="VERIFIED",
    )
    w3 = coordinator.checkpoint(obligation.obligation_id)
    print(f"After all discharges: status={w3.final_status}, tier={w3.final_tier}")
    assert w3.final_status == ObligationStatus.FULLY_DISCHARGED.value
    assert w3.final_tier == "VERIFIED"

    # Roundtrip
    d = w3.to_dict()
    w4 = ObligationSplittingWitness.from_dict(d)
    assert w4.witness_id == w3.witness_id

    # Merge
    w5 = w3.merge(w4)
    print(f"Merged witness ID: {w5.witness_id}")

    # Analyzer
    analyzer = ObligationSplittingAnalyzer(witnesses=[witness, w2, w3])
    score = analyzer.score()
    print(f"Obligation-health score: {score:.3f}")
    print(analyzer.report())

    # Graph topology
    graph = coordinator.graphs[obligation.obligation_id]
    summary = graph.summary()
    print(f"Graph summary: {summary}")
    assert summary["all_discharged"] is True

    print("\n[PASS] All smoke tests passed.")
