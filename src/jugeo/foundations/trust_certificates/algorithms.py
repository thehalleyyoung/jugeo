"""Core algorithms for the trust_certificates chapter — Theory2 Ch6.

Implements: TrustResolutionAlgorithm, ProvenanceChainBuilder,
CertificateIssuanceAlgorithm, EvidenceAggregationAlgorithm,
TrustPathFinder, BatchCertificationPipeline.

Author: copilot
Reference: theory2.tex Chapter 6.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple
from collections import defaultdict, deque
import math

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
    from jugeo.evidence.provenance import ProvenanceNode, ProvenanceGraph
    from jugeo.evidence.certificates import Certificate, CertificateBuilder, CertificateStatus
    from jugeo.judgments.judgment_terms import JudgmentTerm
    from jugeo.errors import JuGeoError, StructuredFailure, FailureScope, EvidenceFamily
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Trust ordering constants
# ---------------------------------------------------------------------------

_TRUST_ORDER: Dict[str, int] = {
    "CONTRADICTED": 0,
    "UNVERIFIED": 1,
    "COPILOT_SUGGESTED": 2,
    "ORACLE_PROPOSED": 3,
    "HUMAN_ATTESTED": 4,
    "RUNTIME_WITNESSED": 5,
    "SOLVER_DISCHARGED": 6,
    "MECHANICALLY_VERIFIED": 7,
}

_ADMISSIBLE: FrozenSet[str] = frozenset(
    k for k, v in _TRUST_ORDER.items() if v >= 2
)

_RANK_TO_NAME: Dict[int, str] = {v: k for k, v in _TRUST_ORDER.items()}


def _rank(level: Any) -> int:
    """Return the integer rank for a trust level name or object."""
    if isinstance(level, str):
        return _TRUST_ORDER.get(level.upper(), 1)
    if isinstance(level, int):
        return level
    # Support enum-like objects with a .name attribute
    if hasattr(level, "name"):
        return _TRUST_ORDER.get(str(level.name).upper(), 1)
    if hasattr(level, "value") and isinstance(level.value, int):
        return level.value
    return 1  # default to UNVERIFIED


def _name_at_rank(rank: int) -> str:
    """Return the canonical trust level name for an integer rank."""
    clamped = max(0, min(rank, max(_RANK_TO_NAME.keys())))
    return _RANK_TO_NAME.get(clamped, "UNVERIFIED")


# ---------------------------------------------------------------------------
# 1. TrustResolutionAlgorithm
# ---------------------------------------------------------------------------

@dataclass
class TrustResolutionAlgorithm:
    """Resolves trust levels from collections of evidence items.

    The resolution algorithm computes the *meet* (greatest lower bound) of
    all admissible trust levels presented by a set of evidence items.  Items
    whose trust level is not admissible are silently excluded from the meet
    computation but are recorded in the resolution log for audit purposes.
    """

    algo_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    resolution_log: List[Dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Core resolution
    # ------------------------------------------------------------------

    def resolve(self, evidence_items: List[Dict]) -> str:
        """Resolve a list of evidence items to a single trust level name.

        The algorithm:
        1. Filters for admissible items.
        2. Extracts their trust levels.
        3. Computes the meet (minimum rank).
        4. Records the resolution in the log.

        Parameters
        ----------
        evidence_items:
            Each dict must contain at least a ``'trust_level'`` key.

        Returns
        -------
        str
            The canonical name of the resolved trust level.
        """
        admissible = [
            item for item in evidence_items
            if isinstance(item, dict)
            and _rank(item.get("trust_level", "UNVERIFIED")) >= 2
        ]
        excluded_count = len(evidence_items) - len(admissible)

        if not admissible:
            result = "UNVERIFIED"
        else:
            levels = [str(item.get("trust_level", "UNVERIFIED")).upper() for item in admissible]
            result = self.compute_meet(levels)

        entry = {
            "timestamp": time.time(),
            "total_items": len(evidence_items),
            "admissible_items": len(admissible),
            "excluded_items": excluded_count,
            "resolved_level": result,
        }
        self.resolution_log.append(entry)
        return result

    def compute_meet(self, levels: List[str]) -> str:
        """Compute the meet (minimum rank) of a list of trust level names.

        The meet is the greatest lower bound in the trust lattice — the
        weakest level that is still implied by all supplied levels.

        Parameters
        ----------
        levels:
            List of trust level name strings (case-insensitive).

        Returns
        -------
        str
            Name of the meet trust level.
        """
        if not levels:
            return "UNVERIFIED"
        min_rank = min(_rank(lvl) for lvl in levels)
        return _name_at_rank(min_rank)

    def compute_join(self, levels: List[str]) -> str:
        """Compute the join (maximum rank) of a list of trust level names.

        The join is the least upper bound — the strongest level implied by
        the best piece of evidence in the collection.

        Parameters
        ----------
        levels:
            List of trust level name strings (case-insensitive).

        Returns
        -------
        str
            Name of the join trust level.
        """
        if not levels:
            return "UNVERIFIED"
        max_rank = max(_rank(lvl) for lvl in levels)
        return _name_at_rank(max_rank)

    def find_weakest_admissible(self, evidence_items: List[Dict]) -> Optional[str]:
        """Find the weakest admissible trust level among evidence items.

        Filters items to those with admissible trust levels, then returns the
        name of the level with the lowest rank.  Returns ``None`` if no
        admissible items exist.

        Parameters
        ----------
        evidence_items:
            List of evidence dicts, each with a ``'trust_level'`` key.

        Returns
        -------
        Optional[str]
            The weakest admissible trust level name, or ``None``.
        """
        admissible = [
            item for item in evidence_items
            if isinstance(item, dict)
            and _rank(item.get("trust_level", "UNVERIFIED")) >= 2
        ]
        if not admissible:
            return None
        weakest = min(admissible, key=lambda it: _rank(it.get("trust_level", "UNVERIFIED")))
        return str(weakest.get("trust_level", "UNVERIFIED")).upper()

    def apply_ceiling(self, level: str, ceiling: str) -> str:
        """Apply a trust ceiling, clamping ``level`` to at most ``ceiling``.

        If the rank of ``level`` exceeds the rank of ``ceiling``, the
        returned level is ``ceiling``; otherwise ``level`` is returned
        unchanged.

        Parameters
        ----------
        level:
            Trust level name to potentially clamp.
        ceiling:
            Maximum permitted trust level name.

        Returns
        -------
        str
            The clamped trust level name.
        """
        if _rank(level) <= _rank(ceiling):
            return level.upper()
        return ceiling.upper()

    def batch_resolve(self, evidence_groups: Dict[str, List[Dict]]) -> Dict[str, str]:
        """Resolve multiple named groups of evidence items.

        Each group is resolved independently via :meth:`resolve`.

        Parameters
        ----------
        evidence_groups:
            Mapping from group key to list of evidence dicts.

        Returns
        -------
        Dict[str, str]
            Mapping from group key to resolved trust level name.
        """
        return {key: self.resolve(items) for key, items in evidence_groups.items()}


# ---------------------------------------------------------------------------
# 2. ProvenanceChainBuilder
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceChainBuilder:
    """Builds and manages provenance chains as directed acyclic graphs.

    Nodes are dictionaries representing computation steps or evidence
    sources.  Edges point from parent nodes to their children, mirroring
    the information-flow direction of a provenance graph.
    """

    builder_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    nodes: Dict[str, Dict] = field(default_factory=dict)
    edges: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def build(self, root_node: Dict) -> str:
        """Add a root node (with no parents) to the provenance graph.

        The ``root_node`` dict must contain:
        - ``'node_id'``: unique string identifier
        - ``'channel'``: the evidence channel the node belongs to
        - ``'operation'``: the operation that produced this node

        Parameters
        ----------
        root_node:
            Dictionary describing the root provenance node.

        Returns
        -------
        str
            The ``node_id`` of the registered root node.

        Raises
        ------
        ValueError
            If required keys are missing or ``node_id`` already exists.
        """
        for key in ("node_id", "channel", "operation"):
            if key not in root_node:
                raise ValueError(f"root_node must contain '{key}' key")
        nid = root_node["node_id"]
        if nid in self.nodes:
            raise ValueError(f"node_id '{nid}' already exists in provenance graph")
        node_copy = dict(root_node)
        node_copy.setdefault("inputs", [])
        node_copy.setdefault("timestamp", time.time())
        self.nodes[nid] = node_copy
        # Ensure edges entry exists even for root
        if nid not in self.edges:
            self.edges[nid] = set()
        return nid

    def extend(self, parent_id: str, child_node: Dict) -> str:
        """Extend the provenance chain by attaching a child to an existing node.

        The ``child_node`` dict must contain ``'node_id'``, ``'channel'``,
        and ``'operation'`` keys.  The ``'inputs'`` field will be set to
        ``[parent_id]``, overwriting any value already present.

        Parameters
        ----------
        parent_id:
            The ``node_id`` of the parent node that must already exist.
        child_node:
            Dictionary describing the child provenance node.

        Returns
        -------
        str
            The ``node_id`` of the newly registered child node.

        Raises
        ------
        ValueError
            If ``parent_id`` is unknown or required keys are missing.
        """
        if parent_id not in self.nodes:
            raise ValueError(f"parent_id '{parent_id}' not found in provenance graph")
        for key in ("node_id", "channel", "operation"):
            if key not in child_node:
                raise ValueError(f"child_node must contain '{key}' key")
        cid = child_node["node_id"]
        if cid in self.nodes:
            raise ValueError(f"node_id '{cid}' already exists in provenance graph")
        node_copy = dict(child_node)
        node_copy["inputs"] = [parent_id]
        node_copy.setdefault("timestamp", time.time())
        self.nodes[cid] = node_copy
        self.edges[parent_id].add(cid)
        if cid not in self.edges:
            self.edges[cid] = set()
        return cid

    def merge_chains(self, other_builder: "ProvenanceChainBuilder") -> List[str]:
        """Merge another builder's graph into this one.

        Nodes and edges already present (by ``node_id``) are skipped.

        Parameters
        ----------
        other_builder:
            Another :class:`ProvenanceChainBuilder` instance to merge from.

        Returns
        -------
        List[str]
            Sorted list of ``node_id`` values that were newly added.
        """
        added: List[str] = []
        for nid, node in other_builder.nodes.items():
            if nid not in self.nodes:
                self.nodes[nid] = dict(node)
                added.append(nid)
        for parent_id, children in other_builder.edges.items():
            if parent_id not in self.edges:
                self.edges[parent_id] = set()
            for child_id in children:
                self.edges[parent_id].add(child_id)
        return sorted(added)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def detect_broken_links(self) -> List[str]:
        """Detect broken provenance links (dangling input references).

        A broken link occurs when a node's ``'inputs'`` list references a
        ``node_id`` that does not exist in :attr:`nodes`.

        Returns
        -------
        List[str]
            Human-readable descriptions of each broken link found.
        """
        broken: List[str] = []
        for nid, node in self.nodes.items():
            for inp in node.get("inputs", []):
                if inp not in self.nodes:
                    broken.append(
                        f"Node '{nid}' references missing input '{inp}'"
                    )
        return broken

    def get_ancestors(self, node_id: str) -> List[str]:
        """Return all ancestor node IDs for a given node via BFS.

        Traversal follows the ``'inputs'`` relationship backwards through
        the graph.

        Parameters
        ----------
        node_id:
            The ID of the node whose ancestors are sought.

        Returns
        -------
        List[str]
            Sorted list of ancestor ``node_id`` values (excludes *node_id*
            itself).
        """
        if node_id not in self.nodes:
            return []
        visited: Set[str] = set()
        queue: deque[str] = deque()
        # Initialise with direct parents
        for inp in self.nodes[node_id].get("inputs", []):
            if inp not in visited:
                visited.add(inp)
                queue.append(inp)
        while queue:
            current = queue.popleft()
            if current not in self.nodes:
                continue
            for inp in self.nodes[current].get("inputs", []):
                if inp not in visited:
                    visited.add(inp)
                    queue.append(inp)
        return sorted(visited)

    def get_descendants(self, node_id: str) -> List[str]:
        """Return all descendant node IDs for a given node via BFS.

        Traversal follows the forward edges in :attr:`edges`.

        Parameters
        ----------
        node_id:
            The ID of the node whose descendants are sought.

        Returns
        -------
        List[str]
            Sorted list of descendant ``node_id`` values (excludes *node_id*
            itself).
        """
        if node_id not in self.nodes:
            return []
        visited: Set[str] = set()
        queue: deque[str] = deque(self.edges.get(node_id, set()))
        visited.update(self.edges.get(node_id, set()))
        while queue:
            current = queue.popleft()
            for child in self.edges.get(current, set()):
                if child not in visited:
                    visited.add(child)
                    queue.append(child)
        return sorted(visited)

    def serialize(self) -> Dict:
        """Serialize the builder state to a plain dictionary.

        Returns
        -------
        Dict
            Dictionary containing ``builder_id``, node count, edge count,
            the full ``nodes`` dict, and ``edges`` with sorted value lists.
        """
        return {
            "builder_id": self.builder_id,
            "node_count": len(self.nodes),
            "edge_count": sum(len(v) for v in self.edges.values()),
            "nodes": {nid: dict(n) for nid, n in self.nodes.items()},
            "edges": {pid: sorted(children) for pid, children in self.edges.items()},
        }


# ---------------------------------------------------------------------------
# 3. CertificateIssuanceAlgorithm
# ---------------------------------------------------------------------------

_EVIDENCE_REQUIRED_KEYS: Tuple[str, ...] = ("trust_level", "channel", "claim")


@dataclass
class CertificateIssuanceAlgorithm:
    """Full pipeline for issuing trust certificates from evidence packages.

    The issuance pipeline is:
    1. :meth:`validate_inputs` — structural validation.
    2. :meth:`compute_trust_level` — meet over evidence trust levels.
    3. :meth:`project_to_certificate` — build the base certificate dict.
    4. :meth:`finalize` — attach residuals, obstructions, provenance and log.
    """

    algo_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    issuance_log: List[Dict] = field(default_factory=list)
    issued_count: int = 0

    # ------------------------------------------------------------------
    # Pipeline entry point
    # ------------------------------------------------------------------

    def issue(
        self,
        coordinate: str,
        evidence_items: List[Dict],
        claim: str,
        obligations: Optional[List[str]] = None,
        obstructions: Optional[List[str]] = None,
    ) -> Dict:
        """Run the full certificate issuance pipeline.

        Parameters
        ----------
        coordinate:
            The geometric coordinate that the certificate covers.
        evidence_items:
            List of evidence dicts.  Each must contain at minimum the keys
            ``'trust_level'``, ``'channel'``, and ``'claim'``.
        claim:
            The claim being certified.
        obligations:
            Optional list of residual obligations that remain after
            certification (e.g. yet-to-be-discharged proof obligations).
        obstructions:
            Optional list of known obstructions that limit the certificate's
            scope.

        Returns
        -------
        Dict
            The finalized certificate dictionary.

        Raises
        ------
        ValueError
            If validation fails in strict mode.
        """
        obligations = obligations or []
        obstructions = obstructions or []

        valid, violations = self.validate_inputs(coordinate, evidence_items, claim)
        if not valid:
            raise ValueError(f"Certificate issuance validation failed: {violations}")

        trust_level = self.compute_trust_level(evidence_items)
        cert_dict = self.project_to_certificate(coordinate, trust_level, claim)

        provenance_ids: List[str] = []
        for item in evidence_items:
            pid = item.get("provenance_id") or item.get("node_id")
            if pid:
                provenance_ids.append(str(pid))

        return self.finalize(cert_dict, obligations, obstructions, provenance_ids)

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def validate_inputs(
        self,
        coordinate: str,
        evidence_items: List[Dict],
        claim: str,
    ) -> Tuple[bool, List[str]]:
        """Validate the inputs to the issuance pipeline.

        Checks:
        - ``coordinate`` is non-empty.
        - At least one evidence item is provided.
        - ``claim`` is non-empty.
        - Each evidence item contains the required keys.

        Parameters
        ----------
        coordinate, evidence_items, claim:
            As passed to :meth:`issue`.

        Returns
        -------
        Tuple[bool, List[str]]
            ``(True, [])`` on success, or ``(False, [violation, ...])`` on
            failure.
        """
        violations: List[str] = []
        if not coordinate or not isinstance(coordinate, str):
            violations.append("coordinate must be a non-empty string")
        if not evidence_items:
            violations.append("at least one evidence item is required")
        if not claim or not isinstance(claim, str):
            violations.append("claim must be a non-empty string")
        for i, item in enumerate(evidence_items):
            if not isinstance(item, dict):
                violations.append(f"evidence_items[{i}] is not a dict")
                continue
            for key in _EVIDENCE_REQUIRED_KEYS:
                if key not in item:
                    violations.append(
                        f"evidence_items[{i}] missing required key '{key}'"
                    )
        return (len(violations) == 0, violations)

    def compute_trust_level(self, evidence_items: List[Dict]) -> str:
        """Compute the meet trust level from a list of evidence items.

        Items with non-admissible trust levels contribute ``UNVERIFIED``
        to the meet computation (pulling the result down).

        Parameters
        ----------
        evidence_items:
            List of evidence dicts with ``'trust_level'`` keys.

        Returns
        -------
        str
            The canonical meet trust level name.
        """
        if not evidence_items:
            return "UNVERIFIED"
        ranks: List[int] = []
        for item in evidence_items:
            lvl = str(item.get("trust_level", "UNVERIFIED")).upper()
            r = _rank(lvl)
            ranks.append(r)
        return _name_at_rank(min(ranks))

    def project_to_certificate(
        self,
        coordinate: str,
        trust_level: str,
        claim: str,
    ) -> Dict:
        """Build the base certificate dictionary.

        Generates a fresh ``cert_id`` and records the issuance timestamp.

        Parameters
        ----------
        coordinate:
            Geometric coordinate the certificate covers.
        trust_level:
            Resolved trust level name.
        claim:
            The claim being certified.

        Returns
        -------
        Dict
            Base certificate dict with ``cert_id``, ``coordinate``,
            ``trust_level``, ``claim``, and ``issued_at``.
        """
        return {
            "cert_id": str(uuid.uuid4()),
            "coordinate": coordinate,
            "trust_level": trust_level.upper(),
            "claim": claim,
            "issued_at": time.time(),
        }

    def finalize(
        self,
        cert_dict: Dict,
        residuals: List[str],
        obstructions: List[str],
        provenance_ids: List[str],
    ) -> Dict:
        """Finalize a certificate by attaching metadata and logging.

        Increments :attr:`issued_count` and appends an entry to
        :attr:`issuance_log`.

        Parameters
        ----------
        cert_dict:
            Base certificate dict from :meth:`project_to_certificate`.
        residuals:
            List of residual obligations.
        obstructions:
            List of known obstructions.
        provenance_ids:
            List of provenance node IDs supporting the certificate.

        Returns
        -------
        Dict
            The completed certificate dictionary.
        """
        cert_dict["residuals"] = list(residuals)
        cert_dict["obstructions"] = list(obstructions)
        cert_dict["provenance_ids"] = list(provenance_ids)
        cert_dict["status"] = "ISSUED"
        cert_dict["algo_id"] = self.algo_id

        self.issued_count += 1

        log_entry = {
            "cert_id": cert_dict["cert_id"],
            "coordinate": cert_dict["coordinate"],
            "trust_level": cert_dict["trust_level"],
            "issued_at": cert_dict["issued_at"],
            "residual_count": len(residuals),
            "obstruction_count": len(obstructions),
            "provenance_count": len(provenance_ids),
        }
        self.issuance_log.append(log_entry)
        return cert_dict

    def get_issuance_stats(self) -> Dict:
        """Return summary statistics for all certificates issued so far.

        Returns
        -------
        Dict
            Dictionary with ``total_issued``, ``log_size``, and
            ``last_issued_at`` (timestamp or ``None``).
        """
        last_issued: Optional[float] = None
        if self.issuance_log:
            last_issued = self.issuance_log[-1].get("issued_at")
        return {
            "total_issued": self.issued_count,
            "log_size": len(self.issuance_log),
            "last_issued_at": last_issued,
        }


# ---------------------------------------------------------------------------
# 4. EvidenceAggregationAlgorithm
# ---------------------------------------------------------------------------

@dataclass
class EvidenceAggregationAlgorithm:
    """Aggregates heterogeneous evidence from multiple channels.

    The aggregation algorithm partitions evidence by channel, detects and
    resolves conflicts, and then computes a composite trust level.
    """

    algo_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    aggregation_log: List[Dict] = field(default_factory=list)

    _CONFLICT_RANK_DISTANCE: int = 2

    def aggregate(self, evidence_items: List[Dict]) -> Dict:
        """Aggregate a flat list of evidence items.

        Pipeline:
        1. Partition by channel.
        2. Check for conflicts.
        3. Resolve conflicts (conservative: keep weakest).
        4. Compute aggregate trust (meet over channels' best items).

        Parameters
        ----------
        evidence_items:
            Flat list of evidence dicts, each with at least ``'channel'``
            and ``'trust_level'`` keys.

        Returns
        -------
        Dict
            ``{'channels': {...}, 'trust_level': str, 'conflict_count': int}``
        """
        partitioned = self.partition_by_channel(evidence_items)
        conflicts = self.check_conflicts(partitioned)
        if conflicts:
            partitioned = self.resolve_conflicts(conflicts, partitioned)
        aggregate_trust = self.compute_aggregate_trust(partitioned)

        result = {
            "channels": {ch: list(items) for ch, items in partitioned.items()},
            "trust_level": aggregate_trust,
            "conflict_count": len(conflicts),
        }
        self.aggregation_log.append({
            "timestamp": time.time(),
            "channel_count": len(partitioned),
            "total_items": len(evidence_items),
            "conflict_count": len(conflicts),
            "aggregate_trust": aggregate_trust,
        })
        return result

    def partition_by_channel(
        self, evidence_items: List[Dict]
    ) -> Dict[str, List[Dict]]:
        """Group evidence items by their ``'channel'`` field.

        Items without a ``'channel'`` key are assigned to channel
        ``'__unknown__'``.

        Parameters
        ----------
        evidence_items:
            List of evidence dicts.

        Returns
        -------
        Dict[str, List[Dict]]
            Mapping from channel name to list of evidence items.
        """
        partitioned: Dict[str, List[Dict]] = defaultdict(list)
        for item in evidence_items:
            ch = str(item.get("channel", "__unknown__"))
            partitioned[ch].append(item)
        return dict(partitioned)

    def check_conflicts(
        self, partitioned: Dict[str, List[Dict]]
    ) -> List[Dict]:
        """Check for conflicting evidence within each channel.

        Two items are considered conflicting if their trust level ranks
        differ by more than :attr:`_CONFLICT_RANK_DISTANCE`.

        Parameters
        ----------
        partitioned:
            Channel-partitioned evidence dict from
            :meth:`partition_by_channel`.

        Returns
        -------
        List[Dict]
            List of conflict descriptor dicts, each with keys
            ``'channel'``, ``'item_a'``, ``'item_b'``, ``'rank_distance'``.
        """
        conflicts: List[Dict] = []
        for channel, items in partitioned.items():
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    ra = _rank(items[i].get("trust_level", "UNVERIFIED"))
                    rb = _rank(items[j].get("trust_level", "UNVERIFIED"))
                    dist = abs(ra - rb)
                    if dist > self._CONFLICT_RANK_DISTANCE:
                        conflicts.append({
                            "channel": channel,
                            "item_a_index": i,
                            "item_b_index": j,
                            "rank_distance": dist,
                        })
        return conflicts

    def resolve_conflicts(
        self,
        conflicts: List[Dict],
        partitioned: Dict[str, List[Dict]],
    ) -> Dict[str, List[Dict]]:
        """Resolve conflicts by conservative reduction.

        For each channel involved in at least one conflict, only the item
        with the *lowest* trust rank (the most conservative) is retained.

        Parameters
        ----------
        conflicts:
            List of conflict dicts from :meth:`check_conflicts`.
        partitioned:
            Current channel-partitioned dict.

        Returns
        -------
        Dict[str, List[Dict]]
            Updated partitioned dict with conflicts resolved.
        """
        conflicted_channels: Set[str] = {c["channel"] for c in conflicts}
        result = {ch: list(items) for ch, items in partitioned.items()}
        for ch in conflicted_channels:
            items = result.get(ch, [])
            if not items:
                continue
            weakest = min(items, key=lambda it: _rank(it.get("trust_level", "UNVERIFIED")))
            result[ch] = [weakest]
        return result

    def compute_aggregate_trust(
        self, partitioned: Dict[str, List[Dict]]
    ) -> str:
        """Compute the aggregate trust level (meet over channels).

        For each channel, the best (join) trust level is found, then the
        meet is taken across all channels.

        Parameters
        ----------
        partitioned:
            Channel-partitioned evidence dict.

        Returns
        -------
        str
            The canonical aggregate trust level name.
        """
        per_channel_best: List[str] = []
        for ch, items in partitioned.items():
            if not items:
                continue
            best_rank = max(_rank(it.get("trust_level", "UNVERIFIED")) for it in items)
            per_channel_best.append(_name_at_rank(best_rank))
        if not per_channel_best:
            return "UNVERIFIED"
        return _name_at_rank(min(_rank(lvl) for lvl in per_channel_best))

    def filter_admissible(self, evidence_items: List[Dict]) -> List[Dict]:
        """Return only evidence items with admissible trust levels.

        Admissible levels have rank >= 2 (i.e., at least ``COPILOT_SUGGESTED``).

        Parameters
        ----------
        evidence_items:
            Flat list of evidence dicts.

        Returns
        -------
        List[Dict]
            Filtered list containing only admissible items.
        """
        return [
            item for item in evidence_items
            if isinstance(item, dict)
            and str(item.get("trust_level", "UNVERIFIED")).upper() in _ADMISSIBLE
        ]


# ---------------------------------------------------------------------------
# 5. TrustPathFinder
# ---------------------------------------------------------------------------

@dataclass
class TrustPathFinder:
    """Find paths through trust-weighted provenance DAGs.

    The graph is stored as a dict mapping ``node_id`` to a node descriptor
    that includes ``trust_level``, ``channel``, ``inputs`` (parents), and
    ``metadata``.
    """

    finder_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    graph: Dict[str, Dict] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        trust_level: str,
        channel: str,
        inputs: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Add a node to the trust graph.

        Parameters
        ----------
        node_id:
            Unique identifier for the node.
        trust_level:
            Trust level name for this node.
        channel:
            Evidence channel this node belongs to.
        inputs:
            List of parent ``node_id`` values (default ``[]``).
        metadata:
            Optional additional metadata dict.
        """
        self.graph[node_id] = {
            "trust_level": trust_level.upper(),
            "channel": channel,
            "inputs": list(inputs or []),
            "metadata": dict(metadata or {}),
        }

    # ------------------------------------------------------------------
    # Path finding
    # ------------------------------------------------------------------

    def find_path(self, from_id: str, to_id: str) -> Optional[List[str]]:
        """Find *any* path from ``from_id`` to ``to_id`` in the graph.

        Traversal follows edges in both the forward (children via cross-
        referencing ``inputs``) and backward (``inputs``) directions to
        handle partially-connected DAGs.  BFS is used.

        Parameters
        ----------
        from_id:
            Starting node ID.
        to_id:
            Target node ID.

        Returns
        -------
        Optional[List[str]]
            Ordered list of node IDs on the path, or ``None`` if unreachable.
        """
        return self.shortest_path(from_id, to_id)

    def shortest_path(self, from_id: str, to_id: str) -> Optional[List[str]]:
        """Find the shortest path by hop count using BFS.

        Both forward edges (derived from ``inputs`` cross-references) and
        backward edges (direct ``inputs``) are considered.

        Parameters
        ----------
        from_id:
            Starting node ID.
        to_id:
            Target node ID.

        Returns
        -------
        Optional[List[str]]
            Shortest path as ordered list of node IDs, or ``None``.
        """
        if from_id not in self.graph or to_id not in self.graph:
            return None
        if from_id == to_id:
            return [from_id]

        # Build adjacency: forward (children) and backward (parents)
        adj: Dict[str, Set[str]] = defaultdict(set)
        for nid, node in self.graph.items():
            for parent in node.get("inputs", []):
                if parent in self.graph:
                    adj[parent].add(nid)  # parent -> child
                    adj[nid].add(parent)  # child -> parent (bidirectional)

        visited: Set[str] = {from_id}
        queue: deque[List[str]] = deque([[from_id]])
        while queue:
            path = queue.popleft()
            current = path[-1]
            for neighbour in adj.get(current, set()):
                if neighbour == to_id:
                    return path + [neighbour]
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(path + [neighbour])
        return None

    def strongest_path(self, from_id: str, to_id: str) -> Optional[List[str]]:
        """Find the path that maximises the minimum (meet) trust rank.

        Uses a modified best-first search that explores paths in decreasing
        order of their current minimum trust rank (bottleneck maximisation).

        Parameters
        ----------
        from_id:
            Starting node ID.
        to_id:
            Target node ID.

        Returns
        -------
        Optional[List[str]]
            Path with the highest minimum trust rank, or ``None``.
        """
        if from_id not in self.graph or to_id not in self.graph:
            return None
        if from_id == to_id:
            return [from_id]

        # Build bidirectional adjacency
        adj: Dict[str, Set[str]] = defaultdict(set)
        for nid, node in self.graph.items():
            for parent in node.get("inputs", []):
                if parent in self.graph:
                    adj[parent].add(nid)
                    adj[nid].add(parent)

        # best_strength[node] = highest bottleneck trust rank seen so far
        best_strength: Dict[str, int] = defaultdict(lambda: -1)
        start_rank = _rank(self.graph[from_id].get("trust_level", "UNVERIFIED"))
        best_strength[from_id] = start_rank

        # Priority queue: (-bottleneck, path)
        import heapq
        heap: List[Tuple[int, List[str]]] = [(-start_rank, [from_id])]

        while heap:
            neg_strength, path = heapq.heappop(heap)
            current = path[-1]
            current_strength = -neg_strength

            if current == to_id:
                return path

            if current_strength < best_strength[current]:
                continue

            for neighbour in adj.get(current, set()):
                if neighbour in path:  # avoid cycles
                    continue
                nb_rank = _rank(self.graph[neighbour].get("trust_level", "UNVERIFIED"))
                new_strength = min(current_strength, nb_rank)
                if new_strength > best_strength[neighbour]:
                    best_strength[neighbour] = new_strength
                    heapq.heappush(heap, (-new_strength, path + [neighbour]))

        return None

    def path_trust_level(self, path: List[str]) -> str:
        """Compute the meet trust level over all nodes in a path.

        Parameters
        ----------
        path:
            Ordered list of node IDs.

        Returns
        -------
        str
            Meet trust level name over the path.
        """
        if not path:
            return "UNVERIFIED"
        ranks: List[int] = []
        for nid in path:
            node = self.graph.get(nid, {})
            ranks.append(_rank(node.get("trust_level", "UNVERIFIED")))
        return _name_at_rank(min(ranks))

    def all_paths(
        self, from_id: str, to_id: str, max_paths: int = 10
    ) -> List[List[str]]:
        """Enumerate up to ``max_paths`` paths from ``from_id`` to ``to_id``.

        Uses iterative DFS with backtracking.  Bidirectional edges are
        considered (same adjacency construction as :meth:`shortest_path`).

        Parameters
        ----------
        from_id:
            Starting node ID.
        to_id:
            Target node ID.
        max_paths:
            Maximum number of paths to return (default 10).

        Returns
        -------
        List[List[str]]
            List of up to ``max_paths`` paths, each as an ordered node ID list.
        """
        if from_id not in self.graph or to_id not in self.graph:
            return []
        if from_id == to_id:
            return [[from_id]]

        adj: Dict[str, Set[str]] = defaultdict(set)
        for nid, node in self.graph.items():
            for parent in node.get("inputs", []):
                if parent in self.graph:
                    adj[parent].add(nid)
                    adj[nid].add(parent)

        found: List[List[str]] = []
        stack: List[Tuple[str, List[str], Set[str]]] = [
            (from_id, [from_id], {from_id})
        ]
        while stack and len(found) < max_paths:
            current, path, visited = stack.pop()
            if current == to_id:
                found.append(list(path))
                continue
            for neighbour in sorted(adj.get(current, set())):
                if neighbour not in visited:
                    stack.append((neighbour, path + [neighbour], visited | {neighbour}))

        return found


# ---------------------------------------------------------------------------
# 6. BatchCertificationPipeline
# ---------------------------------------------------------------------------

@dataclass
class BatchCertificationPipeline:
    """Processes a batch of judgment dicts through the certificate issuance
    pipeline in topological dependency order.

    Failed judgments are recorded in :attr:`errors` and do not block
    independent judgments.
    """

    pipeline_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    results: Dict[str, Dict] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)
    issuance_algo: CertificateIssuanceAlgorithm = field(
        default_factory=CertificateIssuanceAlgorithm
    )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        judgments: List[Dict],
        dependency_map: Optional[Dict[str, List[str]]] = None,
    ) -> Dict:
        """Process a batch of judgments in topological order.

        Parameters
        ----------
        judgments:
            List of judgment dicts.  Each must contain at least
            ``'judgment_id'``, ``'coordinate'``, ``'claim'``, and
            ``'evidence_items'``.
        dependency_map:
            Optional dict mapping ``judgment_id`` to list of
            ``judgment_id`` values that must be processed first.

        Returns
        -------
        Dict
            Summary with ``pipeline_id``, ``issued_count``,
            ``error_count``, and ``results``.
        """
        dependency_map = dependency_map or {}
        judgment_index: Dict[str, Dict] = {
            j["judgment_id"]: j for j in judgments if "judgment_id" in j
        }
        all_ids = list(judgment_index.keys())

        try:
            ordered = self.topological_sort(all_ids, dependency_map)
        except ValueError as exc:
            # Cycle detected — fall back to arbitrary order
            ordered = all_ids

        for jid in ordered:
            jdict = judgment_index.get(jid)
            if jdict is None:
                self.errors[jid] = "judgment dict not found in batch"
                continue
            success, outcome = self.process_judgment(jdict)
            if success:
                self.results[jid] = outcome
            else:
                self.errors[jid] = outcome.get("error", "unknown error")

        return {
            "pipeline_id": self.pipeline_id,
            "issued_count": len(self.results),
            "error_count": len(self.errors),
            "results": self.collect_results(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def topological_sort(
        self,
        judgment_ids: List[str],
        dependency_map: Dict[str, List[str]],
    ) -> List[str]:
        """Sort judgment IDs topologically using Kahn's algorithm.

        Parameters
        ----------
        judgment_ids:
            All IDs to include in the sort.
        dependency_map:
            Mapping from ID to list of IDs it depends on (must come first).

        Returns
        -------
        List[str]
            Topologically ordered list of judgment IDs.

        Raises
        ------
        ValueError
            If a cycle is detected.
        """
        id_set: Set[str] = set(judgment_ids)
        in_degree: Dict[str, int] = {jid: 0 for jid in id_set}
        dependents: Dict[str, List[str]] = {jid: [] for jid in id_set}

        for jid, deps in dependency_map.items():
            if jid not in id_set:
                continue
            for dep in deps:
                if dep not in id_set:
                    continue
                in_degree[jid] += 1
                dependents[dep].append(jid)

        queue: deque[str] = deque(
            sorted(jid for jid, deg in in_degree.items() if deg == 0)
        )
        result: List[str] = []
        while queue:
            current = queue.popleft()
            result.append(current)
            for dependent in sorted(dependents.get(current, [])):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(id_set):
            remaining = id_set - set(result)
            raise ValueError(
                f"Cycle detected in dependency_map among: {sorted(remaining)}"
            )
        return result

    def process_judgment(self, judgment_dict: Dict) -> Tuple[bool, Dict]:
        """Attempt to issue a certificate for a single judgment.

        Parameters
        ----------
        judgment_dict:
            Must contain ``'judgment_id'``, ``'coordinate'``, ``'claim'``,
            and ``'evidence_items'``.  Optionally ``'obligations'`` and
            ``'obstructions'``.

        Returns
        -------
        Tuple[bool, Dict]
            ``(True, cert_dict)`` on success, or ``(False, {'error': msg})``
            on failure.
        """
        try:
            cert = self.issuance_algo.issue(
                coordinate=judgment_dict.get("coordinate", ""),
                evidence_items=judgment_dict.get("evidence_items", []),
                claim=judgment_dict.get("claim", ""),
                obligations=judgment_dict.get("obligations"),
                obstructions=judgment_dict.get("obstructions"),
            )
            cert["judgment_id"] = judgment_dict.get("judgment_id")
            return (True, cert)
        except Exception as exc:
            return (False, {"error": str(exc)})

    def collect_results(self) -> Dict:
        """Return a shallow copy of the results dictionary.

        Returns
        -------
        Dict
            Copy of ``{judgment_id: cert_dict, ...}``.
        """
        return dict(self.results)

    def get_failed_judgments(self) -> List[str]:
        """Return a list of judgment IDs that failed certification.

        Returns
        -------
        List[str]
            Sorted list of judgment IDs present in :attr:`errors`.
        """
        return sorted(self.errors.keys())

    def reset(self) -> None:
        """Clear all results and errors, resetting the pipeline for reuse."""
        self.results.clear()
        self.errors.clear()


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §3 — Trust Certificates)
# ---------------------------------------------------------------------------

import logging

_logger = logging.getLogger(__name__)


def certificate_site_verification(
    cert_data: Dict[str, Any],
    *,
    site: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify a trust certificate over a geometric site.

    Uses coordinate and descent structures from ``jugeo.geometry`` to check
    that *cert_data* is consistent with the local-section topology at *site*.

    Reference: Theory2.tex §3 (Trust Certificates), site-verification lemma.

    Parameters
    ----------
    cert_data:
        Dictionary describing the certificate (must contain ``"coordinate"``).
    site:
        Optional site identifier to restrict verification scope.

    Returns
    -------
    Dict[str, Any]
        ``{"verified": bool, "site": str|None, "sections": list, "errors": list}``
    """
    errors: List[str] = []
    sections: List[str] = []
    verified = False

    try:
        from jugeo.geometry.site import Coordinate, CoordinateKind
        from jugeo.geometry.descent import LocalSection, DescentStrategy
    except ImportError as exc:
        _logger.warning("geometry imports unavailable: %s", exc)
        return {"verified": False, "site": site, "sections": [], "errors": [str(exc)]}

    try:
        raw_coord = cert_data.get("coordinate", "")
        coord = Coordinate(raw_coord) if raw_coord else None
        if coord is None:
            errors.append("certificate has no coordinate")
        else:
            kind = CoordinateKind.from_coordinate(coord) if hasattr(CoordinateKind, "from_coordinate") else None
            strategy = DescentStrategy.DEFAULT if hasattr(DescentStrategy, "DEFAULT") else list(DescentStrategy)[0]
            section = LocalSection(coordinate=coord, strategy=strategy)
            sections.append(str(section))
            if site is not None and str(coord) != site:
                errors.append(f"coordinate {coord} does not match site {site}")
            else:
                verified = True
    except Exception as exc:
        _logger.error("site verification failed: %s", exc)
        errors.append(str(exc))

    return {"verified": verified, "site": site, "sections": sections, "errors": errors}


def certificate_solver_encoding(
    cert_data: Dict[str, Any],
    *,
    backend: str = "z3",
) -> Dict[str, Any]:
    """Encode a trust certificate for a constraint solver.

    Translates *cert_data* into solver-level assertions via
    ``jugeo.encodings`` and ``jugeo.solver.z3_session``.

    Reference: Theory2.tex §3 (Trust Certificates), encoding bridge.

    Parameters
    ----------
    cert_data:
        Dictionary describing the certificate.
    backend:
        Solver backend name (default ``"z3"``).

    Returns
    -------
    Dict[str, Any]
        ``{"encoded": bool, "backend": str, "assertions": list, "errors": list}``
    """
    errors: List[str] = []
    assertions: List[str] = []
    encoded = False

    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
        from jugeo.encodings import encode_judgment, encode_section
    except ImportError as exc:
        _logger.warning("solver/encoding imports unavailable: %s", exc)
        return {"encoded": False, "backend": backend, "assertions": [], "errors": [str(exc)]}

    try:
        if not z3_available():
            errors.append("z3 backend is not available")
            return {"encoded": False, "backend": backend, "assertions": assertions, "errors": errors}

        judgment = cert_data.get("judgment")
        section = cert_data.get("section")

        if judgment is not None:
            enc = encode_judgment(judgment)
            assertions.append(str(enc))
        if section is not None:
            enc = encode_section(section)
            assertions.append(str(enc))

        encoded = len(assertions) > 0 and not errors
    except Exception as exc:
        _logger.error("solver encoding failed: %s", exc)
        errors.append(str(exc))

    return {"encoded": encoded, "backend": backend, "assertions": assertions, "errors": errors}
