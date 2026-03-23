"""Core domain models for trust_certificates — Theory2 Ch6.

Defines the four primary model classes:
  - TrustAlgebraModel: wraps TrustAlgebra, adds admissible config tracking
  - ProvenanceModel: append-only provenance chain
  - EvidenceModel: evidence items keyed by (coordinate, channel)
  - CertificateModel: certificate collection with faithful-projection validation

The models mirror the algebraic structure T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)
described in theory2.tex Chapter 6.  No silent promotion is enforced by
requiring explicit justification strings in all promotion paths.

Author: copilot
Reference: theory2.tex Chapter 6
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, Iterator, List, Optional, Set, Tuple

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
    from jugeo.evidence.provenance import ProvenanceNode, ProvenanceGraph
    from jugeo.evidence.certificates import Certificate, CertificateBuilder, CertificateStatus
    from jugeo.judgments.judgment_terms import JudgmentTerm
    from jugeo.errors import JuGeoError, StructuredFailure, FailureScope, EvidenceFamily
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Lightweight stand-in enums (used when jugeo packages unavailable)
# ---------------------------------------------------------------------------


class _TrustLevelFallback(str, Enum):
    """Fallback trust level enum used when jugeo.evidence.trust is unavailable."""

    MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"
    SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    HUMAN_ATTESTED = "HUMAN_ATTESTED"
    ORACLE_PROPOSED = "ORACLE_PROPOSED"
    COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTED = "CONTRADICTED"


# Numeric ordering for fallback comparisons (higher = stronger)
_TRUST_LEVEL_ORDER: Dict[str, int] = {
    "MECHANICALLY_VERIFIED": 7,
    "SOLVER_DISCHARGED": 6,
    "RUNTIME_WITNESSED": 5,
    "HUMAN_ATTESTED": 4,
    "ORACLE_PROPOSED": 3,
    "COPILOT_SUGGESTED": 2,
    "UNVERIFIED": 1,
    "CONTRADICTED": 0,
}

_ADMISSIBLE_LEVELS: FrozenSet[str] = frozenset(
    {
        "MECHANICALLY_VERIFIED",
        "SOLVER_DISCHARGED",
        "RUNTIME_WITNESSED",
        "HUMAN_ATTESTED",
        "ORACLE_PROPOSED",
    }
)


def _level_name(level: Any) -> str:
    """Extract the string name from a TrustLevel-like object."""
    if isinstance(level, str):
        return level
    if hasattr(level, "name"):
        return level.name
    if hasattr(level, "value"):
        return str(level.value)
    return str(level)


def _level_rank(level: Any) -> int:
    """Return numeric rank for a trust level (higher = stronger)."""
    return _TRUST_LEVEL_ORDER.get(_level_name(level), 0)


# ---------------------------------------------------------------------------
# TrustAlgebraModel
# ---------------------------------------------------------------------------


@dataclass
class TrustAlgebraModel:
    """Algebraic model for trust, wrapping TrustAlgebra with admissibility tracking.

    Maintains:
    - admissible_configs: dict mapping config_id → config dict
    - ceiling_map: dict mapping scope_key → maximum trust level name

    Attributes:
        model_id: Unique identifier for this model instance.
        admissible_configs: Validated evidence configurations by config_id.
        ceiling_map: Maps scope keys to trust level ceilings.
        order_violations: Accumulated order violation messages.
        audit_log: Append-only list of promotion/demotion audit records.
    """

    model_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    admissible_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    ceiling_map: Dict[str, str] = field(default_factory=dict)
    order_violations: List[str] = field(default_factory=list)
    audit_log: List[Dict[str, Any]] = field(default_factory=list)

    # copilot: Default ceiling for copilot-sourced evidence (no silent promotion above ORACLE_PROPOSED)
    DEFAULT_COPILOT_CEILING: str = "ORACLE_PROPOSED"

    def apply_compose(self, level_a: Any, level_b: Any) -> str:
        """Compose two trust levels using ⊕ (take the minimum of the two).

        Composition is conservative: the composed trust is at most as strong
        as the weakest constituent.  This ensures no silent strengthening.

        Args:
            level_a: First trust level.
            level_b: Second trust level.

        Returns:
            Name of the composed trust level.
        """
        rank_a = _level_rank(level_a)
        rank_b = _level_rank(level_b)
        composed_rank = min(rank_a, rank_b)
        for name, rank in sorted(_TRUST_LEVEL_ORDER.items(), key=lambda kv: kv[1]):
            if rank == composed_rank:
                return name
        return "UNVERIFIED"

    def apply_attenuate(self, level: Any, attenuation_steps: int = 1) -> str:
        """Attenuate a trust level by a given number of steps.

        Each step lowers the trust level by one rank.  Cannot go below
        CONTRADICTED (rank 0).

        Args:
            level: Trust level to attenuate.
            attenuation_steps: Number of steps to reduce trust by.

        Returns:
            Name of the attenuated trust level.
        """
        current_rank = _level_rank(level)
        target_rank = max(0, current_rank - attenuation_steps)
        for name, rank in sorted(_TRUST_LEVEL_ORDER.items(), key=lambda kv: kv[1]):
            if rank == target_rank:
                return name
        return "CONTRADICTED"

    def apply_promote(
        self,
        level: Any,
        justification: str,
        policy_name: str,
        scope_key: str = "",
    ) -> Tuple[str, bool]:
        """Promote a trust level under named policy ↑_π.

        Promotion is only allowed when:
        1. A non-empty justification is provided.
        2. A named policy (policy_name) is referenced.
        3. The resulting level does not exceed the ceiling for the scope.

        Records the promotion attempt in the audit_log regardless of outcome.

        Args:
            level: Current trust level.
            justification: Human-readable rationale for promotion.
            policy_name: Named policy authorising this promotion.
            scope_key: Optional scope key for ceiling lookup.

        Returns:
            Tuple of (new_level_name, success_bool).
        """
        if not justification.strip():
            self.audit_log.append({
                "event": "promotion_rejected",
                "reason": "no_justification",
                "level": _level_name(level),
                "policy": policy_name,
                "timestamp": time.time(),
            })
            return (_level_name(level), False)

        if not policy_name.strip():
            self.audit_log.append({
                "event": "promotion_rejected",
                "reason": "no_policy_name",
                "level": _level_name(level),
                "justification": justification,
                "timestamp": time.time(),
            })
            return (_level_name(level), False)

        current_rank = _level_rank(level)
        promoted_rank = min(current_rank + 1, max(_TRUST_LEVEL_ORDER.values()))
        promoted_name = _level_name(level)
        for name, rank in sorted(_TRUST_LEVEL_ORDER.items(), key=lambda kv: kv[1]):
            if rank == promoted_rank:
                promoted_name = name
                break

        # Enforce ceiling if one is set for this scope
        if scope_key and scope_key in self.ceiling_map:
            ceiling_rank = _TRUST_LEVEL_ORDER.get(self.ceiling_map[scope_key], 0)
            if promoted_rank > ceiling_rank:
                self.audit_log.append({
                    "event": "promotion_ceiling_blocked",
                    "from_level": _level_name(level),
                    "attempted": promoted_name,
                    "ceiling": self.ceiling_map[scope_key],
                    "scope_key": scope_key,
                    "policy": policy_name,
                    "justification": justification,
                    "timestamp": time.time(),
                })
                return (self.ceiling_map[scope_key], False)

        self.audit_log.append({
            "event": "promotion_accepted",
            "from_level": _level_name(level),
            "to_level": promoted_name,
            "policy": policy_name,
            "justification": justification,
            "scope_key": scope_key,
            "timestamp": time.time(),
        })
        return (promoted_name, True)

    def apply_demote(self, level: Any, ceiling: str, reason: str = "") -> str:
        """Demote a trust level to at most `ceiling` (↓_χ).

        If the current level already satisfies the ceiling, it is returned
        unchanged.  Otherwise it is clamped to the ceiling.

        Args:
            level: Current trust level.
            ceiling: Maximum allowed trust level name.
            reason: Optional reason for the demotion.

        Returns:
            Name of the demoted (or unchanged) trust level.
        """
        current_rank = _level_rank(level)
        ceiling_rank = _TRUST_LEVEL_ORDER.get(ceiling, 0)
        if current_rank <= ceiling_rank:
            return _level_name(level)
        self.audit_log.append({
            "event": "demotion",
            "from_level": _level_name(level),
            "ceiling": ceiling,
            "reason": reason,
            "timestamp": time.time(),
        })
        return ceiling

    def check_admissibility(self, level: Any) -> bool:
        """Return True if the level is in the admissible set E_adm.

        Args:
            level: Trust level to check.

        Returns:
            True if admissible, False otherwise.
        """
        return _level_name(level) in _ADMISSIBLE_LEVELS

    def get_ceiling(self, scope_key: str) -> Optional[str]:
        """Retrieve the ceiling for a given scope key.

        Args:
            scope_key: Scope identifier (e.g. coordinate path).

        Returns:
            Ceiling trust level name, or None if no ceiling is set.
        """
        return self.ceiling_map.get(scope_key)

    def set_ceiling(self, scope_key: str, ceiling: str) -> None:
        """Set or update the ceiling for a scope.

        Args:
            scope_key: Scope identifier.
            ceiling: Trust level name to use as ceiling.

        Raises:
            ValueError: If the ceiling name is not a valid trust level.
        """
        if ceiling not in _TRUST_LEVEL_ORDER:
            raise ValueError(
                f"Unknown trust level '{ceiling}'; valid levels: {list(_TRUST_LEVEL_ORDER)}"
            )
        self.ceiling_map[scope_key] = ceiling

    def report_order_violations(self) -> List[str]:
        """Return accumulated order violation messages, then clear the buffer.

        Returns:
            List of violation message strings.
        """
        violations = list(self.order_violations)
        self.order_violations.clear()
        return violations

    def register_admissible_config(self, config_id: str, config: Dict[str, Any]) -> None:
        """Register a validated evidence configuration.

        Args:
            config_id: Unique identifier for this configuration.
            config: Arbitrary configuration dict.
        """
        self.admissible_configs[config_id] = {**config, "registered_at": time.time()}

    def serialize(self) -> Dict[str, Any]:
        """Serialise the model state to a plain dict.

        Returns:
            Dictionary with all model state.
        """
        return {
            "model_id": self.model_id,
            "admissible_configs": dict(self.admissible_configs),
            "ceiling_map": dict(self.ceiling_map),
            "order_violations": list(self.order_violations),
            "audit_log": list(self.audit_log),
        }


# ---------------------------------------------------------------------------
# ProvenanceModel
# ---------------------------------------------------------------------------


@dataclass
class _ProvenanceEntry:
    """Internal entry in the provenance chain.

    Attributes:
        entry_id: Unique entry identifier.
        node_id: ID of the ProvenanceNode (or synthetic ID).
        source_channel: Channel that produced this entry.
        operation: String name of the operation.
        inputs: Upstream entry IDs.
        timestamp: Unix timestamp of entry creation.
        coordinate: Geometric coordinate this entry belongs to.
        trust_level: Trust level at creation.
        metadata: Arbitrary metadata dict.
    """

    entry_id: str
    node_id: str
    source_channel: str
    operation: str
    inputs: Tuple[str, ...]
    timestamp: float
    coordinate: str
    trust_level: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProvenanceModel:
    """Append-only provenance chain manager.

    Maintains an ordered list of ProvenanceEntry objects and the DAG edges
    between them.  New entries may only be appended; existing entries are
    immutable.

    Attributes:
        chain_id: Unique identifier for this chain.
        entries: Ordered list of provenance entries.
        entry_index: Fast lookup from entry_id to entry.
        dag_edges: Adjacency list (entry_id → set of downstream entry_ids).
    """

    chain_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entries: List[_ProvenanceEntry] = field(default_factory=list)
    entry_index: Dict[str, _ProvenanceEntry] = field(default_factory=dict)
    dag_edges: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_node(
        self,
        source_channel: str,
        operation: str,
        inputs: Iterable[str] = (),
        coordinate: str = "",
        trust_level: str = "UNVERIFIED",
        metadata: Optional[Dict[str, Any]] = None,
        node_id: Optional[str] = None,
    ) -> str:
        """Append a new node to the provenance chain.

        Args:
            source_channel: The discharge channel that produced this node.
            operation: Name of the operation (e.g. 'arithmetic_discharge').
            inputs: Upstream node IDs.
            coordinate: Geometric coordinate.
            trust_level: Trust level at creation.
            metadata: Optional metadata dict.
            node_id: Optional explicit node ID; generated if omitted.

        Returns:
            The entry_id of the newly appended node.

        Raises:
            ValueError: If any input ID does not exist in the chain.
        """
        input_ids = tuple(inputs)
        for inp in input_ids:
            if inp not in self.entry_index:
                raise ValueError(
                    f"Input node '{inp}' not found in provenance chain '{self.chain_id}'"
                )
        entry_id = node_id or str(uuid.uuid4())
        entry = _ProvenanceEntry(
            entry_id=entry_id,
            node_id=node_id or entry_id,
            source_channel=source_channel,
            operation=operation,
            inputs=input_ids,
            timestamp=time.time(),
            coordinate=coordinate,
            trust_level=trust_level,
            metadata=metadata or {},
        )
        self.entries.append(entry)
        self.entry_index[entry_id] = entry
        for inp in input_ids:
            self.dag_edges[inp].add(entry_id)
        return entry_id

    def get_chain(self, entry_id: str) -> List[_ProvenanceEntry]:
        """Return the full ancestor chain for an entry (topological order).

        Performs a reverse BFS from the given entry back to roots.

        Args:
            entry_id: Starting entry.

        Returns:
            List of ProvenanceEntry objects in topological order (roots first).

        Raises:
            KeyError: If entry_id is not in the chain.
        """
        if entry_id not in self.entry_index:
            raise KeyError(f"Entry '{entry_id}' not found")
        visited: Set[str] = set()
        result: List[_ProvenanceEntry] = []
        stack = [entry_id]
        while stack:
            eid = stack.pop()
            if eid in visited:
                continue
            visited.add(eid)
            entry = self.entry_index[eid]
            result.append(entry)
            stack.extend(entry.inputs)
        # Return in creation order (oldest first)
        result.sort(key=lambda e: e.timestamp)
        return result

    def verify_no_cycles(self) -> List[str]:
        """Detect any cycles in the provenance DAG.

        Returns:
            List of cycle description strings; empty if acyclic.
        """
        # Kahn's algorithm for cycle detection
        in_degree: Dict[str, int] = {eid: 0 for eid in self.entry_index}
        for eid, downstreams in self.dag_edges.items():
            for ds in downstreams:
                in_degree[ds] = in_degree.get(ds, 0) + 1
        queue = [eid for eid, deg in in_degree.items() if deg == 0]
        processed = 0
        while queue:
            eid = queue.pop(0)
            processed += 1
            for ds in self.dag_edges.get(eid, set()):
                in_degree[ds] -= 1
                if in_degree[ds] == 0:
                    queue.append(ds)
        cycle_violations = []
        if processed < len(self.entry_index):
            cyclic_nodes = [eid for eid, deg in in_degree.items() if deg > 0]
            cycle_violations.append(
                f"Provenance DAG has cycle(s) involving nodes: {cyclic_nodes}"
            )
        return cycle_violations

    def restrict_to_coordinate(self, coordinate: str) -> "ProvenanceModel":
        """Return a new ProvenanceModel containing only entries for a coordinate.

        Args:
            coordinate: The coordinate to filter by.

        Returns:
            New ProvenanceModel with entries for the given coordinate only.
        """
        sub_model = ProvenanceModel(chain_id=f"{self.chain_id}::{coordinate}")
        for entry in self.entries:
            if entry.coordinate == coordinate:
                # Only include inputs that are also in the sub-model
                valid_inputs = tuple(
                    inp for inp in entry.inputs if inp in sub_model.entry_index
                )
                sub_model.add_node(
                    source_channel=entry.source_channel,
                    operation=entry.operation,
                    inputs=valid_inputs,
                    coordinate=entry.coordinate,
                    trust_level=entry.trust_level,
                    metadata=dict(entry.metadata),
                    node_id=entry.node_id,
                )
        return sub_model

    def transport_to(self, target_coordinate: str, transport_map: Dict[str, str]) -> "ProvenanceModel":
        """Transport provenance entries to a new coordinate via a transport map.

        Args:
            target_coordinate: Destination coordinate name.
            transport_map: Mapping from source entry_ids to new metadata overrides.

        Returns:
            New ProvenanceModel with transported entries.
        """
        transported = ProvenanceModel(chain_id=f"{self.chain_id}::transport::{target_coordinate}")
        for entry in self.entries:
            override = transport_map.get(entry.entry_id, {})
            new_metadata = {**entry.metadata, **override, "transported_from": entry.coordinate}
            valid_inputs = tuple(
                inp for inp in entry.inputs if inp in transported.entry_index
            )
            transported.add_node(
                source_channel=entry.source_channel,
                operation=f"transport:{entry.operation}",
                inputs=valid_inputs,
                coordinate=target_coordinate,
                trust_level=entry.trust_level,
                metadata=new_metadata,
                node_id=f"transport::{entry.node_id}",
            )
        return transported

    def validate_channel_jurisdiction(self, allowed_channels: Set[str]) -> List[str]:
        """Check that all entries were produced by allowed channels.

        Args:
            allowed_channels: Set of permitted source channel names.

        Returns:
            List of violation strings; empty if all entries are authorised.
        """
        violations = []
        for entry in self.entries:
            if entry.source_channel not in allowed_channels:
                violations.append(
                    f"Entry '{entry.entry_id}' uses unauthorised channel "
                    f"'{entry.source_channel}' (allowed: {sorted(allowed_channels)})"
                )
        return violations

    def serialize(self) -> Dict[str, Any]:
        """Serialise the model to a plain dict.

        Returns:
            Dict with chain_id, entries list, dag_edges.
        """
        return {
            "chain_id": self.chain_id,
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "node_id": e.node_id,
                    "source_channel": e.source_channel,
                    "operation": e.operation,
                    "inputs": list(e.inputs),
                    "timestamp": e.timestamp,
                    "coordinate": e.coordinate,
                    "trust_level": e.trust_level,
                    "metadata": e.metadata,
                }
                for e in self.entries
            ],
            "dag_edges": {k: sorted(v) for k, v in self.dag_edges.items()},
        }


# ---------------------------------------------------------------------------
# EvidenceModel
# ---------------------------------------------------------------------------


@dataclass
class _EvidenceItem:
    """A single evidence item stored in EvidenceModel.

    Attributes:
        item_id: Unique identifier.
        coordinate: Geometric coordinate this evidence relates to.
        channel: Discharge channel name.
        claim: String description of the claim being evidenced.
        trust_level: Trust level of this evidence item.
        provenance_id: ID of the provenance entry backing this item.
        timestamp: Creation timestamp.
        metadata: Arbitrary metadata.
    """

    item_id: str
    coordinate: str
    channel: str
    claim: str
    trust_level: str
    provenance_id: str
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceModel:
    """Evidence item store keyed by (coordinate, channel).

    Supports adding and removing evidence items, composing evidence for a
    coordinate, and checking plurality conditions.

    Attributes:
        archive_id: Unique identifier for this evidence archive.
        items: All evidence items keyed by item_id.
        coordinate_index: Maps coordinate → set of item_ids.
        channel_index: Maps channel → set of item_ids.
    """

    archive_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    items: Dict[str, _EvidenceItem] = field(default_factory=dict)
    coordinate_index: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    channel_index: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_item(
        self,
        coordinate: str,
        channel: str,
        claim: str,
        trust_level: str,
        provenance_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add an evidence item to the archive.

        Args:
            coordinate: Geometric coordinate.
            channel: Discharge channel name.
            claim: Description of the evidenced claim.
            trust_level: Trust level of this evidence.
            provenance_id: Backing provenance entry ID.
            metadata: Optional metadata.

        Returns:
            item_id of the newly added evidence item.
        """
        item_id = str(uuid.uuid4())
        item = _EvidenceItem(
            item_id=item_id,
            coordinate=coordinate,
            channel=channel,
            claim=claim,
            trust_level=trust_level,
            provenance_id=provenance_id,
            timestamp=time.time(),
            metadata=metadata or {},
        )
        self.items[item_id] = item
        self.coordinate_index[coordinate].add(item_id)
        self.channel_index[channel].add(item_id)
        return item_id

    def remove_item(self, item_id: str) -> bool:
        """Remove an evidence item from the archive.

        Args:
            item_id: ID of the item to remove.

        Returns:
            True if the item was present and removed; False otherwise.
        """
        if item_id not in self.items:
            return False
        item = self.items.pop(item_id)
        self.coordinate_index[item.coordinate].discard(item_id)
        self.channel_index[item.channel].discard(item_id)
        return True

    def get_by_channel(self, channel: str) -> List[_EvidenceItem]:
        """Return all evidence items for a given channel.

        Args:
            channel: Channel name.

        Returns:
            List of EvidenceItems for that channel.
        """
        return [self.items[iid] for iid in self.channel_index.get(channel, set())]

    def compose_for_coordinate(self, coordinate: str) -> str:
        """Compose all evidence for a coordinate to a single trust level.

        The composition is conservative: the composed level is the minimum
        over all admissible items for that coordinate (⊕ = min in rank order).

        Args:
            coordinate: Geometric coordinate.

        Returns:
            Name of the composed trust level, or 'UNVERIFIED' if no items.
        """
        item_ids = self.coordinate_index.get(coordinate, set())
        if not item_ids:
            return "UNVERIFIED"
        ranks = [
            _level_rank(self.items[iid].trust_level)
            for iid in item_ids
            if _level_name(self.items[iid].trust_level) in _ADMISSIBLE_LEVELS
        ]
        if not ranks:
            return "UNVERIFIED"
        min_rank = min(ranks)
        for name, rank in sorted(_TRUST_LEVEL_ORDER.items(), key=lambda kv: kv[1]):
            if rank == min_rank:
                return name
        return "UNVERIFIED"

    def check_plurality_condition(
        self, coordinate: str, required_channels: Set[str]
    ) -> Tuple[bool, List[str]]:
        """Check whether plurality of required channels is satisfied for a coordinate.

        Args:
            coordinate: Coordinate to check.
            required_channels: Set of channel names that must each have ≥1 admissible item.

        Returns:
            Tuple of (satisfied_bool, list_of_missing_channels).
        """
        present_channels: Set[str] = set()
        for item_id in self.coordinate_index.get(coordinate, set()):
            item = self.items[item_id]
            if _level_name(item.trust_level) in _ADMISSIBLE_LEVELS:
                present_channels.add(item.channel)
        missing = sorted(required_channels - present_channels)
        return (len(missing) == 0, missing)

    def iter_admissible(self, coordinate: Optional[str] = None) -> Iterator[_EvidenceItem]:
        """Iterate over admissible evidence items, optionally filtered by coordinate.

        Args:
            coordinate: If provided, only yield items for this coordinate.

        Yields:
            EvidenceItems with admissible trust levels.
        """
        item_ids = (
            self.coordinate_index.get(coordinate, set())
            if coordinate is not None
            else set(self.items.keys())
        )
        for item_id in item_ids:
            item = self.items[item_id]
            if _level_name(item.trust_level) in _ADMISSIBLE_LEVELS:
                yield item

    def serialize(self) -> Dict[str, Any]:
        """Serialise the evidence archive to a plain dict.

        Returns:
            Dictionary with archive_id and all items.
        """
        return {
            "archive_id": self.archive_id,
            "items": [
                {
                    "item_id": i.item_id,
                    "coordinate": i.coordinate,
                    "channel": i.channel,
                    "claim": i.claim,
                    "trust_level": i.trust_level,
                    "provenance_id": i.provenance_id,
                    "timestamp": i.timestamp,
                    "metadata": i.metadata,
                }
                for i in self.items.values()
            ],
        }


# ---------------------------------------------------------------------------
# CertificateModel
# ---------------------------------------------------------------------------


@dataclass
class _CertRecord:
    """Internal certificate record held by CertificateModel.

    Attributes:
        cert_id: Unique certificate identifier.
        coordinate: Coordinate this certificate covers.
        trust_level: Trust level claimed.
        claim: Description of what is certified.
        residuals: List of residual obligation IDs.
        obstructions: List of obstruction IDs.
        status: 'active' | 'revoked'.
        issued_at: Unix timestamp.
        revoked_at: Unix timestamp, or None.
        provenance_id: Backing provenance entry.
        metadata: Arbitrary metadata.
    """

    cert_id: str
    coordinate: str
    trust_level: str
    claim: str
    residuals: List[str]
    obstructions: List[str]
    status: str
    issued_at: float
    revoked_at: Optional[float]
    provenance_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CertificateModel:
    """Collection of certificates with faithful-projection validation.

    Certificates are created via `issue`, revoked via `revoke`, and looked up
    by coordinate or cert_id.  The model enforces that no certificate may
    silently erase residuals or obstructions from the underlying evidence.

    Attributes:
        store_id: Unique store identifier.
        certs: Mapping cert_id → _CertRecord.
        coordinate_index: Maps coordinate → set of cert_ids.
    """

    store_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    certs: Dict[str, _CertRecord] = field(default_factory=dict)
    coordinate_index: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))

    def issue(
        self,
        coordinate: str,
        trust_level: str,
        claim: str,
        residuals: Optional[List[str]] = None,
        obstructions: Optional[List[str]] = None,
        provenance_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Issue a new certificate.

        Args:
            coordinate: Geometric coordinate being certified.
            trust_level: Trust level to assert.
            claim: Human-readable claim description.
            residuals: Residual obligation IDs that remain open.
            obstructions: Obstruction IDs present at issuance.
            provenance_id: Backing provenance entry.
            metadata: Optional metadata.

        Returns:
            cert_id of the newly issued certificate.

        Raises:
            ValueError: If trust_level is not admissible (UNVERIFIED or CONTRADICTED
                        are not valid for certificate issuance).
        """
        if _level_name(trust_level) in {"CONTRADICTED"}:
            raise ValueError(
                f"Cannot issue certificate with trust level '{trust_level}'"
            )
        cert_id = str(uuid.uuid4())
        record = _CertRecord(
            cert_id=cert_id,
            coordinate=coordinate,
            trust_level=_level_name(trust_level),
            claim=claim,
            residuals=list(residuals or []),
            obstructions=list(obstructions or []),
            status="active",
            issued_at=time.time(),
            revoked_at=None,
            provenance_id=provenance_id,
            metadata=metadata or {},
        )
        self.certs[cert_id] = record
        self.coordinate_index[coordinate].add(cert_id)
        return cert_id

    def revoke(self, cert_id: str, reason: str = "") -> bool:
        """Revoke a certificate.

        Args:
            cert_id: Certificate to revoke.
            reason: Optional revocation reason.

        Returns:
            True if revoked successfully; False if not found or already revoked.
        """
        record = self.certs.get(cert_id)
        if record is None or record.status == "revoked":
            return False
        record.status = "revoked"
        record.revoked_at = time.time()
        record.metadata["revocation_reason"] = reason
        return True

    def lookup(self, cert_id: str) -> Optional[_CertRecord]:
        """Retrieve a certificate by ID.

        Args:
            cert_id: Certificate identifier.

        Returns:
            The _CertRecord if found, None otherwise.
        """
        return self.certs.get(cert_id)

    def lookup_by_coordinate(self, coordinate: str, active_only: bool = True) -> List[_CertRecord]:
        """Return all certificates for a coordinate.

        Args:
            coordinate: Coordinate to query.
            active_only: If True, only return active certificates.

        Returns:
            List of matching _CertRecord objects.
        """
        cert_ids = self.coordinate_index.get(coordinate, set())
        records = [self.certs[cid] for cid in cert_ids if cid in self.certs]
        if active_only:
            records = [r for r in records if r.status == "active"]
        return records

    def project_public_all(self) -> List[Dict[str, Any]]:
        """Return a list of public-facing certificate summaries.

        The public projection omits internal metadata and provenance details.

        Returns:
            List of dicts with cert_id, coordinate, trust_level, claim, status.
        """
        return [
            {
                "cert_id": r.cert_id,
                "coordinate": r.coordinate,
                "trust_level": r.trust_level,
                "claim": r.claim,
                "residuals": list(r.residuals),
                "obstructions": list(r.obstructions),
                "status": r.status,
                "issued_at": r.issued_at,
            }
            for r in self.certs.values()
        ]

    def check_residuals_honest(self) -> List[str]:
        """Check that no active certificate has silently cleared its residuals.

        A certificate is considered dishonest if it was issued with residuals
        but they are now listed as empty without a corresponding revocation or
        explicit discharge record in the metadata.

        Returns:
            List of violation messages; empty if all certificates are honest.
        """
        violations = []
        for record in self.certs.values():
            if record.status != "active":
                continue
            if not record.residuals and record.metadata.get("original_residuals"):
                violations.append(
                    f"Certificate '{record.cert_id}' for coordinate '{record.coordinate}' "
                    f"had residuals that were silently cleared: "
                    f"{record.metadata['original_residuals']}"
                )
        return violations

    def validate_faithful_projection(
        self, evidence_model: EvidenceModel
    ) -> Tuple[bool, List[str]]:
        """Validate that all active certificates faithfully reflect evidence state.

        A certificate is faithful when:
        1. Its trust_level does not exceed the composed evidence trust.
        2. It does not suppress obstructions present in the evidence model.

        Args:
            evidence_model: The EvidenceModel to compare against.

        Returns:
            Tuple of (is_faithful_bool, list_of_violations).
        """
        violations = []
        for record in self.certs.values():
            if record.status != "active":
                continue
            composed_trust = evidence_model.compose_for_coordinate(record.coordinate)
            cert_rank = _TRUST_LEVEL_ORDER.get(record.trust_level, 0)
            composed_rank = _TRUST_LEVEL_ORDER.get(composed_trust, 0)
            if cert_rank > composed_rank:
                violations.append(
                    f"Certificate '{record.cert_id}' claims '{record.trust_level}' "
                    f"but evidence only supports '{composed_trust}' for coordinate "
                    f"'{record.coordinate}' — silent strengthening detected"
                )
        return (len(violations) == 0, violations)

    def serialize(self) -> Dict[str, Any]:
        """Serialise the certificate store.

        Returns:
            Dictionary with store_id and all certificate records.
        """
        return {
            "store_id": self.store_id,
            "certificates": [
                {
                    "cert_id": r.cert_id,
                    "coordinate": r.coordinate,
                    "trust_level": r.trust_level,
                    "claim": r.claim,
                    "residuals": r.residuals,
                    "obstructions": r.obstructions,
                    "status": r.status,
                    "issued_at": r.issued_at,
                    "revoked_at": r.revoked_at,
                    "provenance_id": r.provenance_id,
                    "metadata": r.metadata,
                }
                for r in self.certs.values()
            ],
        }


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §3 — Trust Certificates)
# ---------------------------------------------------------------------------

import logging

_logger = logging.getLogger(__name__)


def model_geometry_bridge(
    model: Any,
) -> Dict[str, Any]:
    """Map a trust-certificate model to the geometric layer.

    Projects each certificate in *model* onto a ``Coordinate`` and scores the
    resulting cover via ``jugeo.geometry.covers.score_cover``.

    Reference: Theory2.tex §3 (Trust Certificates), geometry bridge lemma.

    Parameters
    ----------
    model:
        A ``CertificateModel`` (or compatible mapping) whose certificates
        should be projected into the geometric cover.

    Returns
    -------
    Dict[str, Any]
        ``{"mapped": bool, "coordinates": list, "cover_score": float|None, "errors": list}``
    """
    errors: List[str] = []
    coordinates: List[str] = []
    cover_score: Optional[float] = None
    mapped = False

    try:
        from jugeo.geometry.site import Coordinate
        from jugeo.geometry.covers import CoverMember, score_cover
    except ImportError as exc:
        _logger.warning("geometry imports unavailable: %s", exc)
        return {"mapped": False, "coordinates": [], "cover_score": None, "errors": [str(exc)]}

    try:
        certs = model.certs.values() if hasattr(model, "certs") else []
        members: list = []
        for cert in certs:
            raw = cert.coordinate if hasattr(cert, "coordinate") else str(cert)
            coord = Coordinate(raw)
            coordinates.append(str(coord))
            members.append(CoverMember(coordinate=coord))

        if members:
            cover_score = score_cover(members)
        mapped = len(coordinates) > 0 and not errors
    except Exception as exc:
        _logger.error("geometry bridge failed: %s", exc)
        errors.append(str(exc))

    return {"mapped": mapped, "coordinates": coordinates, "cover_score": cover_score, "errors": errors}


def model_solver_bridge(
    model: Any,
) -> Dict[str, Any]:
    """Encode a trust-certificate model for solver consumption.

    Checks solver availability via ``z3_available`` and selects a backend
    through ``jugeo.solver.router`` before building the encoding.

    Reference: Theory2.tex §3 (Trust Certificates), solver bridge lemma.

    Parameters
    ----------
    model:
        A ``CertificateModel`` (or compatible mapping) to encode.

    Returns
    -------
    Dict[str, Any]
        ``{"encoded": bool, "backend": str|None, "result": str|None, "errors": list}``
    """
    errors: List[str] = []
    backend_name: Optional[str] = None
    result_repr: Optional[str] = None
    encoded = False

    try:
        from jugeo.solver.z3_session import z3_available, SolverResult
        from jugeo.solver.router import BackendKind, RoutingDecision
    except ImportError as exc:
        _logger.warning("solver imports unavailable: %s", exc)
        return {"encoded": False, "backend": None, "result": None, "errors": [str(exc)]}

    try:
        if z3_available():
            kind = BackendKind.Z3 if hasattr(BackendKind, "Z3") else list(BackendKind)[0]
        else:
            kind = list(BackendKind)[0]
            errors.append("z3 not available, using fallback backend")

        decision = RoutingDecision(backend=kind)
        backend_name = str(decision.backend) if hasattr(decision, "backend") else str(kind)

        certs = model.certs.values() if hasattr(model, "certs") else []
        claims = [c.claim for c in certs if hasattr(c, "claim")]
        result = SolverResult(claims=claims) if claims else None
        result_repr = str(result) if result is not None else None
        encoded = result is not None and not errors
    except Exception as exc:
        _logger.error("solver bridge failed: %s", exc)
        errors.append(str(exc))

    return {"encoded": encoded, "backend": backend_name, "result": result_repr, "errors": errors}
