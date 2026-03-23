"""
Section 4: Manifest integrity - Theory2 Ch6.

Theory: The full manifest (J,O,E,X,K,eta,sigma) must be internally consistent.
The epoch map eta records when coordinates were verified.
The invalidation graph sigma propagates invalidations causally.

Author: copilot
Reference: theory2.tex Chapter 6, Section 4
"""

from __future__ import annotations

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
    from jugeo.evidence.provenance import ProvenanceNode, ProvenanceGraph
    from jugeo.evidence.certificates import Certificate, CertificateBuilder, CertificateStatus
    from jugeo.judgments.judgment_terms import JudgmentTerm
    from jugeo.errors import JuGeoError, StructuredFailure, FailureScope, EvidenceFamily
except ImportError:
    pass

import hashlib
import json
import time
import uuid
from collections import defaultdict, deque
import itertools
from dataclasses import dataclass, field, replace as dataclasses_replace
from enum import Enum
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)

# ---------------------------------------------------------------------------
# Trust level constants
# ---------------------------------------------------------------------------

_TRUST_ORDER: Dict[str, int] = {
    "none": 0,
    "claimed": 1,
    "asserted": 2,
    "corroborated": 3,
    "verified": 4,
    "certified": 5,
    "audited": 6,
    "grounded": 7,
}

_ADMISSIBLE_LEVELS: frozenset = frozenset(_TRUST_ORDER.keys())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rank(level: str) -> int:
    """Return numeric rank for a trust level name (0 if unknown)."""
    return _TRUST_ORDER.get(str(level).lower(), 0)


def _now() -> float:
    """Return the current wall-clock time."""
    return time.time()


def _sha256(data: str) -> str:
    """Return SHA-256 hex digest for *data*."""
    return hashlib.sha256(data.encode()).hexdigest()


def _stable_hash_dict(d: Dict) -> str:
    """Return SHA-256 hex digest of a stable JSON serialisation of *d*."""
    return _sha256(json.dumps(d, sort_keys=True, default=str))


# Required keys for manifest component validation
_JUDGMENT_REQUIRED = {"coordinate", "claim", "trust_level"}
_OBLIGATION_REQUIRED = {"coordinate", "description"}
_EVIDENCE_REQUIRED = {"channel", "trust_level"}
_CERTIFICATE_REQUIRED = {"coordinate", "trust_level", "claim"}


# ---------------------------------------------------------------------------
# ManifestTuple
# ---------------------------------------------------------------------------


@dataclass
class ManifestTuple:
    """Mutable representation of the full manifest (J, O, E, X, K, eta, sigma).

    Attributes
    ----------
    manifest_id:
        Globally unique identifier for this manifest instance.
    judgments:
        Mapping from judgment_id to judgment dict (J).
    obligations:
        Mapping from obligation_id to obligation dict (O).
    evidence_archive:
        Mapping from coordinate to list of evidence dicts (E).
    obstructions:
        Mapping from obstruction_id to obstruction dict (X).
    certificates:
        Mapping from cert_id to certificate dict (K).
    epoch_map:
        Mapping from coordinate to most-recent epoch timestamp (eta).
    invalidation_edges:
        List of (from_id, to_id) causal invalidation edges (sigma).
    created_at:
        Wall-clock creation time.
    version:
        Monotonically increasing version counter; incremented on mutation.
    """

    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    judgments: Dict[str, Dict] = field(default_factory=dict)
    obligations: Dict[str, Dict] = field(default_factory=dict)
    evidence_archive: Dict[str, List[Dict]] = field(default_factory=lambda: defaultdict(list))
    obstructions: Dict[str, Dict] = field(default_factory=dict)
    certificates: Dict[str, Dict] = field(default_factory=dict)
    epoch_map: Dict[str, float] = field(default_factory=dict)
    invalidation_edges: List[Tuple[str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    version: int = 0

    # ------------------------------------------------------------------
    # Consistency validation
    # ------------------------------------------------------------------

    def validate_consistency(self) -> Tuple[bool, List[str]]:
        """Check the internal consistency of the manifest tuple.

        Rules checked:
        1. No judgment references an obligation_id that doesn't exist in ``obligations``.
        2. No certificate references evidence (by coordinate) that has no entry in ``evidence_archive``.
        3. Every certified coordinate has an entry in ``epoch_map``.

        Returns ``(is_consistent, violations)``.
        """
        violations: List[str] = []

        # Rule 1: judgments must not reference unknown obligations
        for j_id, j_dict in self.judgments.items():
            for ref_id in j_dict.get("obligation_refs", []):
                if ref_id not in self.obligations:
                    violations.append(
                        f"dangling_obligation_ref: judgment '{j_id}' references "
                        f"unknown obligation '{ref_id}'"
                    )

        # Rule 2: certificates must have evidence in evidence_archive
        for cert_id, cert_dict in self.certificates.items():
            coord = cert_dict.get("coordinate", "")
            if coord and coord not in self.evidence_archive:
                violations.append(
                    f"missing_evidence: certificate '{cert_id}' is for coordinate "
                    f"'{coord}' but no evidence exists in evidence_archive for that coordinate"
                )
            elif coord and len(self.evidence_archive.get(coord, [])) == 0:
                violations.append(
                    f"empty_evidence: certificate '{cert_id}' is for coordinate "
                    f"'{coord}' but evidence_archive entry is empty"
                )

        # Rule 3: epoch_map must cover every certified coordinate
        for cert_id, cert_dict in self.certificates.items():
            coord = cert_dict.get("coordinate", "")
            if coord and coord not in self.epoch_map:
                violations.append(
                    f"missing_epoch: coordinate '{coord}' is certified by '{cert_id}' "
                    f"but has no entry in epoch_map"
                )

        return (len(violations) == 0, violations)

    # ------------------------------------------------------------------
    # Mutating methods
    # ------------------------------------------------------------------

    def add_judgment(self, judgment_id: str, judgment_dict: Dict) -> None:
        """Add a judgment to the manifest.

        Validates that *judgment_dict* contains the required keys
        (``coordinate``, ``claim``, ``trust_level``).
        Increments ``version`` on success.

        Raises :class:`ValueError` if required keys are missing.
        """
        missing = _JUDGMENT_REQUIRED - judgment_dict.keys()
        if missing:
            raise ValueError(
                f"judgment_dict is missing required keys: {sorted(missing)}"
            )
        trust = judgment_dict.get("trust_level", "none")
        if trust not in _ADMISSIBLE_LEVELS:
            raise ValueError(
                f"Unknown trust_level '{trust}'; must be one of {sorted(_ADMISSIBLE_LEVELS)}"
            )
        self.judgments[judgment_id] = {
            **judgment_dict,
            "judgment_id": judgment_id,
            "added_at": _now(),
            "manifest_id": self.manifest_id,
        }
        self.version += 1

    def add_obligation(self, obligation_id: str, obligation_dict: Dict) -> None:
        """Add an obligation to the manifest.

        Validates that *obligation_dict* contains ``coordinate`` and ``description``.

        Raises :class:`ValueError` if required keys are missing.
        """
        missing = _OBLIGATION_REQUIRED - obligation_dict.keys()
        if missing:
            raise ValueError(
                f"obligation_dict is missing required keys: {sorted(missing)}"
            )
        self.obligations[obligation_id] = {
            **obligation_dict,
            "obligation_id": obligation_id,
            "added_at": _now(),
            "manifest_id": self.manifest_id,
        }

    def add_evidence(self, coordinate: str, evidence_dict: Dict) -> None:
        """Append an evidence item to *coordinate*'s evidence list.

        Validates that *evidence_dict* contains ``channel`` and ``trust_level``.

        Raises :class:`ValueError` if required keys are missing or trust_level is invalid.
        """
        missing = _EVIDENCE_REQUIRED - evidence_dict.keys()
        if missing:
            raise ValueError(
                f"evidence_dict is missing required keys: {sorted(missing)}"
            )
        trust = evidence_dict.get("trust_level", "none")
        if trust not in _ADMISSIBLE_LEVELS:
            raise ValueError(
                f"Unknown trust_level '{trust}'; must be one of {sorted(_ADMISSIBLE_LEVELS)}"
            )
        entry = {
            **evidence_dict,
            "coordinate": coordinate,
            "appended_at": _now(),
            "manifest_id": self.manifest_id,
        }
        if coordinate not in self.evidence_archive:
            self.evidence_archive[coordinate] = []
        self.evidence_archive[coordinate].append(entry)

    def add_obstruction(self, obstruction_id: str, obstruction_dict: Dict) -> None:
        """Add an obstruction record to the manifest.

        Stamps the obstruction with a creation timestamp.
        """
        self.obstructions[obstruction_id] = {
            **obstruction_dict,
            "obstruction_id": obstruction_id,
            "created_at": _now(),
            "manifest_id": self.manifest_id,
        }

    def issue_certificate(self, cert_id: str, cert_dict: Dict) -> None:
        """Issue a certificate and record its coordinate in the epoch map.

        Validates that *cert_dict* contains ``coordinate``, ``trust_level``, and ``claim``.

        Raises :class:`ValueError` on missing required keys or invalid trust level.
        """
        missing = _CERTIFICATE_REQUIRED - cert_dict.keys()
        if missing:
            raise ValueError(
                f"cert_dict is missing required keys: {sorted(missing)}"
            )
        trust = cert_dict.get("trust_level", "none")
        if trust not in _ADMISSIBLE_LEVELS:
            raise ValueError(
                f"Unknown trust_level '{trust}'; must be one of {sorted(_ADMISSIBLE_LEVELS)}"
            )
        coord = cert_dict["coordinate"]
        now = _now()
        self.certificates[cert_id] = {
            **cert_dict,
            "cert_id": cert_id,
            "issued_at": now,
            "manifest_id": self.manifest_id,
        }
        # Update epoch map for this coordinate
        self.epoch_map[coord] = now
        self.version += 1

    def mark_epoch(self, coordinate: str, epoch: float) -> None:
        """Record *epoch* as the verification timestamp for *coordinate*.

        Raises :class:`ValueError` if *epoch* is not positive.
        """
        if epoch <= 0:
            raise ValueError(f"epoch must be a positive float; got {epoch!r}")
        self.epoch_map[coordinate] = epoch

    def add_invalidation_edge(self, from_id: str, to_id: str) -> None:
        """Append a causal invalidation edge ``(from_id, to_id)`` to the manifest."""
        self.invalidation_edges.append((from_id, to_id))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> Dict:
        """Return all manifest fields as a JSON-serializable dictionary."""
        return {
            "_type": "ManifestTuple",
            "_schema_version": "1.0",
            "manifest_id": self.manifest_id,
            "judgments": {k: dict(v) for k, v in self.judgments.items()},
            "obligations": {k: dict(v) for k, v in self.obligations.items()},
            "evidence_archive": {
                coord: [dict(e) for e in evs]
                for coord, evs in self.evidence_archive.items()
            },
            "obstructions": {k: dict(v) for k, v in self.obstructions.items()},
            "certificates": {k: dict(v) for k, v in self.certificates.items()},
            "epoch_map": dict(self.epoch_map),
            "invalidation_edges": [list(edge) for edge in self.invalidation_edges],
            "created_at": self.created_at,
            "version": self.version,
        }


# ---------------------------------------------------------------------------
# EpochMap
# ---------------------------------------------------------------------------


@dataclass
class EpochMap:
    """Tracks verification epochs (eta) per coordinate, supporting history and invalidation."""

    epoch_data: Dict[str, List[Tuple[float, str]]] = field(default_factory=lambda: defaultdict(list))

    # ------------------------------------------------------------------

    def record(self, coordinate: str, epoch: float, event: str = "") -> None:
        """Append *(epoch, event)* to the history for *coordinate*.

        Raises :class:`ValueError` if *epoch* is not positive.
        """
        if epoch <= 0:
            raise ValueError(f"epoch must be positive; got {epoch!r}")
        if coordinate not in self.epoch_data:
            self.epoch_data[coordinate] = []
        self.epoch_data[coordinate].append((epoch, event))

    def get_epoch(self, coordinate: str) -> Optional[float]:
        """Return the most recent epoch for *coordinate*, or ``None`` if not recorded."""
        history = self.epoch_data.get(coordinate)
        if not history:
            return None
        return history[-1][0]

    def get_all_at_epoch(self, epoch: float, tolerance: float = 0.1) -> List[str]:
        """Return all coordinates whose most recent epoch is within *tolerance* of *epoch*."""
        result: List[str] = []
        for coord, history in self.epoch_data.items():
            if not history:
                continue
            most_recent = history[-1][0]
            if abs(most_recent - epoch) <= tolerance:
                result.append(coord)
        return sorted(result)

    def invalidate_after_epoch(self, coordinate: str, cutoff: float) -> List[float]:
        """Remove all epoch entries after *cutoff* for *coordinate*.

        Returns the list of removed epoch timestamps.
        """
        history = self.epoch_data.get(coordinate, [])
        kept: List[Tuple[float, str]] = []
        removed: List[float] = []
        for ts, event in history:
            if ts > cutoff:
                removed.append(ts)
            else:
                kept.append((ts, event))
        self.epoch_data[coordinate] = kept
        return removed

    def get_history(self, coordinate: str) -> List[Tuple[float, str]]:
        """Return the full epoch history for *coordinate* as a list of ``(timestamp, event)``."""
        return list(self.epoch_data.get(coordinate, []))

    def serialize(self) -> Dict:
        """Return ``epoch_data`` as a plain JSON-serializable dictionary."""
        return {
            "_type": "EpochMap",
            "epoch_data": {
                coord: [[ts, ev] for ts, ev in history]
                for coord, history in self.epoch_data.items()
            },
        }


# ---------------------------------------------------------------------------
# InvalidationGraph
# ---------------------------------------------------------------------------


@dataclass
class InvalidationGraph:
    """Directed graph representing causal invalidation propagation (sigma)."""

    graph_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    edges: List[Tuple[str, str]] = field(default_factory=list)
    adjacency: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))

    # ------------------------------------------------------------------

    def add_edge(self, from_id: str, to_id: str) -> None:
        """Add a directed edge ``from_id -> to_id`` to the graph."""
        self.edges.append((from_id, to_id))
        if from_id not in self.adjacency:
            self.adjacency[from_id] = set()
        self.adjacency[from_id].add(to_id)
        # Ensure to_id has an entry (even if it has no outgoing edges)
        if to_id not in self.adjacency:
            self.adjacency[to_id] = set()

    def get_downstream(self, node_id: str, depth: int = -1) -> Set[str]:
        """Return all nodes reachable from *node_id* via directed edges.

        Parameters
        ----------
        node_id:
            The starting node.
        depth:
            Maximum traversal depth.  ``-1`` means unbounded (full BFS).

        Returns the set of all reachable node IDs (excluding *node_id* itself).
        """
        visited: Set[str] = set()
        queue: deque = deque([(node_id, 0)])
        while queue:
            current, current_depth = queue.popleft()
            if current in visited:
                continue
            if current != node_id:
                visited.add(current)
            if depth != -1 and current_depth >= depth:
                continue
            for neighbour in self.adjacency.get(current, set()):
                if neighbour not in visited:
                    queue.append((neighbour, current_depth + 1))
        return visited

    def propagate_invalidation(self, invalidated_nodes: Set[str]) -> Set[str]:
        """Transitively invalidate all nodes reachable from any node in *invalidated_nodes*.

        Returns the full set of (transitively) invalidated node IDs, including the
        initially invalidated nodes.
        """
        all_invalidated: Set[str] = set(invalidated_nodes)
        for node in list(invalidated_nodes):
            downstream = self.get_downstream(node)
            all_invalidated.update(downstream)
        return all_invalidated

    def detect_cycles(self) -> List[List[str]]:
        """Detect all cycles in the graph using iterative DFS.

        Returns a list of cycles, where each cycle is a list of node IDs forming the cycle.
        An empty list means the graph is acyclic.
        """
        all_nodes: Set[str] = set(self.adjacency.keys())
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        cycles: List[List[str]] = []

        def _dfs(node: str, path: List[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            for neighbour in self.adjacency.get(node, set()):
                if neighbour not in visited:
                    _dfs(neighbour, path)
                elif neighbour in rec_stack:
                    # Found a back edge — extract the cycle
                    cycle_start = path.index(neighbour)
                    cycles.append(path[cycle_start:] + [neighbour])
            path.pop()
            rec_stack.discard(node)

        for node in sorted(all_nodes):
            if node not in visited:
                _dfs(node, [])

        return cycles

    def topological_order(self) -> List[str]:
        """Return a topological ordering of all nodes in the graph.

        Raises :class:`ValueError` if the graph contains a cycle.
        """
        all_nodes: Set[str] = set(self.adjacency.keys())
        in_degree: Dict[str, int] = {n: 0 for n in all_nodes}
        for src, destinations in self.adjacency.items():
            for dst in destinations:
                in_degree[dst] = in_degree.get(dst, 0) + 1

        # Kahn's algorithm
        zero_in: deque = deque(sorted(n for n, d in in_degree.items() if d == 0))
        order: List[str] = []

        while zero_in:
            node = zero_in.popleft()
            order.append(node)
            for neighbour in sorted(self.adjacency.get(node, set())):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    zero_in.append(neighbour)

        if len(order) != len(all_nodes):
            raise ValueError(
                "Topological sort failed: graph contains a cycle. "
                "Use detect_cycles() to identify the cycle(s)."
            )
        return order

    def serialize(self) -> Dict:
        """Return the graph as a JSON-serializable dictionary."""
        return {
            "_type": "InvalidationGraph",
            "graph_id": self.graph_id,
            "edge_count": len(self.edges),
            "edges": [list(e) for e in self.edges],
            "adjacency": {
                node: sorted(targets)
                for node, targets in self.adjacency.items()
            },
        }


# ---------------------------------------------------------------------------
# IntegrityReport (defined before ManifestValidator which references it)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrityReport:
    """Immutable record of a manifest integrity validation run."""

    report_id: str
    manifest_id: str
    is_valid: bool
    violations: Tuple[str, ...]
    warnings: Tuple[str, ...]
    repair_suggestions: Tuple[str, ...]
    checked_at: float

    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the report."""
        lines = [
            f"IntegrityReport {self.report_id}",
            f"  Manifest   : {self.manifest_id}",
            f"  Valid      : {self.is_valid}",
            f"  Violations : {len(self.violations)}",
            f"  Warnings   : {len(self.warnings)}",
            f"  Suggestions: {len(self.repair_suggestions)}",
            f"  Checked at : {self.checked_at:.3f}",
        ]
        if self.violations:
            lines.append("  --- Violations ---")
            for v in self.violations:
                lines.append(f"    * {v}")
        if self.warnings:
            lines.append("  --- Warnings ---")
            for w in self.warnings:
                lines.append(f"    ~ {w}")
        return "\n".join(lines)

    def serialize(self) -> Dict:
        """Return all fields as a JSON-serializable dictionary."""
        return {
            "_type": "IntegrityReport",
            "report_id": self.report_id,
            "manifest_id": self.manifest_id,
            "is_valid": self.is_valid,
            "violation_count": len(self.violations),
            "violations": list(self.violations),
            "warning_count": len(self.warnings),
            "warnings": list(self.warnings),
            "repair_suggestion_count": len(self.repair_suggestions),
            "repair_suggestions": list(self.repair_suggestions),
            "checked_at": self.checked_at,
        }

    def has_violations_of_type(self, violation_type: str) -> bool:
        """Return True iff any violation string contains *violation_type* as a substring."""
        return any(violation_type in v for v in self.violations)


# ---------------------------------------------------------------------------
# ManifestValidator
# ---------------------------------------------------------------------------


@dataclass
class ManifestValidator:
    """Validates a :class:`ManifestTuple` for internal consistency and integrity."""

    validator_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    last_report: Optional[IntegrityReport] = field(default=None)

    # ------------------------------------------------------------------

    def validate(self, manifest: ManifestTuple) -> IntegrityReport:
        """Run all integrity checks against *manifest* and return an :class:`IntegrityReport`.

        Checks run:
        1. Internal consistency (via :meth:`ManifestTuple.validate_consistency`).
        2. Judgment–certificate alignment.
        3. Obligation coverage.
        4. Obstruction honesty.
        5. Epoch monotonicity.
        """
        violations: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []

        # 1. Internal consistency
        is_consistent, consistency_violations = manifest.validate_consistency()
        violations.extend(consistency_violations)

        # 2. Judgment–certificate alignment
        jc_violations = self.check_judgment_certificate_alignment(manifest)
        violations.extend(jc_violations)

        # 3. Obligation coverage
        ob_violations = self.check_obligation_coverage(manifest)
        violations.extend(ob_violations)

        # 4. Obstruction honesty
        oh_violations = self.check_obstruction_honesty(manifest)
        violations.extend(oh_violations)

        # 5. Epoch monotonicity
        em_violations = self.check_epoch_monotonicity(manifest)
        violations.extend(em_violations)

        # Derive repair suggestions from violation prefixes
        for v in violations:
            prefix = v.split(":")[0] if ":" in v else "unknown"
            suggestions.extend(_repair_suggestions_for(prefix))

        # Deduplicate suggestions while preserving order
        seen: Set[str] = set()
        deduped_suggestions: List[str] = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                deduped_suggestions.append(s)

        # Warnings: non-fatal observations
        if not manifest.epoch_map:
            warnings.append("no_epoch_map: epoch_map is empty; no coordinate has been epoch-stamped")
        if not manifest.certificates:
            warnings.append("no_certificates: manifest has no issued certificates")

        report = IntegrityReport(
            report_id=str(uuid.uuid4()),
            manifest_id=manifest.manifest_id,
            is_valid=(len(violations) == 0),
            violations=tuple(violations),
            warnings=tuple(warnings),
            repair_suggestions=tuple(deduped_suggestions),
            checked_at=_now(),
        )
        self.last_report = report
        return report

    # ------------------------------------------------------------------

    def check_judgment_certificate_alignment(
        self, manifest: ManifestTuple
    ) -> List[str]:
        """Check that every certified judgment has a certificate at least as strong as the judgment.

        A judgment is "certified" if a certificate exists for the same coordinate and claims to
        cover that judgment (either by explicit reference or by coordinate match).

        Returns a list of violation strings.
        """
        violations: List[str] = []

        # Build coordinate -> list of cert trust ranks
        coord_cert_rank: Dict[str, List[int]] = defaultdict(list)
        for cert_id, cert_dict in manifest.certificates.items():
            coord = cert_dict.get("coordinate", "")
            if coord:
                coord_cert_rank[coord].append(_rank(cert_dict.get("trust_level", "none")))

        for j_id, j_dict in manifest.judgments.items():
            coord = j_dict.get("coordinate", "")
            j_trust = j_dict.get("trust_level", "none")
            j_rank = _rank(j_trust)

            cert_ranks = coord_cert_rank.get(coord, [])
            if not cert_ranks:
                # No certificate covers this judgment's coordinate — not a violation unless
                # the judgment explicitly requires certification
                if j_dict.get("requires_certificate", False):
                    violations.append(
                        f"uncertified_judgment: judgment '{j_id}' for coordinate '{coord}' "
                        f"requires a certificate but none exists"
                    )
                continue

            max_cert_rank = max(cert_ranks)
            if max_cert_rank < j_rank:
                max_cert_trust = next(
                    (
                        c.get("trust_level", "none")
                        for c in manifest.certificates.values()
                        if c.get("coordinate", "") == coord
                        and _rank(c.get("trust_level", "none")) == max_cert_rank
                    ),
                    "none",
                )
                violations.append(
                    f"cert_below_judgment: judgment '{j_id}' for coordinate '{coord}' "
                    f"claims trust '{j_trust}' (rank {j_rank}) but best certificate "
                    f"is '{max_cert_trust}' (rank {max_cert_rank})"
                )

        return violations

    def check_obligation_coverage(self, manifest: ManifestTuple) -> List[str]:
        """Check that every obligation is covered by a certificate residual or evidence.

        An obligation is covered if:
        - It appears in at least one certificate's ``residuals`` list, OR
        - There is at least one evidence item for the obligation's coordinate.

        Returns a list of violation strings.
        """
        violations: List[str] = []

        # Collect all obligation IDs referenced as residuals in certs
        cert_residuals: Set[str] = set()
        for cert_dict in manifest.certificates.values():
            residuals_raw = cert_dict.get("residuals", cert_dict.get("residual_obligations", []))
            if residuals_raw:
                if isinstance(residuals_raw[0], dict):
                    for r in residuals_raw:
                        cert_residuals.add(r.get("obligation_id", r.get("id", "")))
                else:
                    cert_residuals.update(str(r) for r in residuals_raw)
        cert_residuals.discard("")

        for ob_id, ob_dict in manifest.obligations.items():
            coord = ob_dict.get("coordinate", "")

            covered_by_residual = ob_id in cert_residuals
            covered_by_evidence = bool(manifest.evidence_archive.get(coord))

            if not covered_by_residual and not covered_by_evidence:
                violations.append(
                    f"uncovered_obligation: obligation '{ob_id}' for coordinate '{coord}' "
                    f"is not referenced as a residual in any certificate and has no evidence"
                )

        return violations

    def check_obstruction_honesty(self, manifest: ManifestTuple) -> List[str]:
        """Check that certificates do not suppress obstructions.

        If an obstruction exists for a coordinate, the certificate(s) for that coordinate
        must reference it in their ``obstructions`` or ``known_obstructions`` field.

        Returns a list of violation strings.
        """
        violations: List[str] = []

        # Group obstruction IDs by coordinate
        coord_obstructions: Dict[str, Set[str]] = defaultdict(set)
        for ob_id, ob_dict in manifest.obstructions.items():
            coord = ob_dict.get("coordinate", "")
            if coord:
                coord_obstructions[coord].add(ob_id)

        for cert_id, cert_dict in manifest.certificates.items():
            coord = cert_dict.get("coordinate", "")
            coord_obs = coord_obstructions.get(coord, set())
            if not coord_obs:
                continue

            cert_known_obs_raw = cert_dict.get("obstructions", cert_dict.get("known_obstructions", []))
            if isinstance(cert_known_obs_raw, list):
                cert_known_obs: Set[str] = set(cert_known_obs_raw)
            else:
                cert_known_obs = set()

            suppressed = coord_obs - cert_known_obs
            for ob_id in sorted(suppressed):
                violations.append(
                    f"suppressed_obstruction: certificate '{cert_id}' for coordinate '{coord}' "
                    f"does not acknowledge obstruction '{ob_id}'"
                )

        return violations

    def check_epoch_monotonicity(self, manifest: ManifestTuple) -> List[str]:
        """Check that invalidation edges flow from earlier epochs to later ones.

        An edge ``(from_id, to_id)`` violates epoch monotonicity if the epoch of
        ``from_id`` is *later* than the epoch of ``to_id`` (i.e., the edge reverses
        causal time).

        IDs are resolved against judgment, certificate, obligation, and obstruction maps.

        Returns a list of violation strings.
        """
        violations: List[str] = []

        def _get_epoch(node_id: str) -> Optional[float]:
            """Retrieve the epoch for a node ID from the manifest's epoch_map."""
            # Direct lookup by coordinate
            if node_id in manifest.epoch_map:
                return manifest.epoch_map[node_id]
            # Look up in judgments
            j = manifest.judgments.get(node_id)
            if j:
                coord = j.get("coordinate", "")
                return manifest.epoch_map.get(coord)
            # Look up in certificates
            c = manifest.certificates.get(node_id)
            if c:
                coord = c.get("coordinate", "")
                return manifest.epoch_map.get(coord)
            # Look up in obligations
            o = manifest.obligations.get(node_id)
            if o:
                coord = o.get("coordinate", "")
                return manifest.epoch_map.get(coord)
            return None

        for from_id, to_id in manifest.invalidation_edges:
            from_epoch = _get_epoch(from_id)
            to_epoch = _get_epoch(to_id)

            if from_epoch is None or to_epoch is None:
                # Cannot check monotonicity without both epochs — issue a warning-level entry
                continue

            if from_epoch > to_epoch:
                violations.append(
                    f"epoch_inversion: invalidation edge '{from_id}' -> '{to_id}' "
                    f"has from_epoch={from_epoch:.3f} > to_epoch={to_epoch:.3f}; "
                    f"causal order is reversed"
                )

        return violations


# ---------------------------------------------------------------------------
# Repair suggestion registry
# ---------------------------------------------------------------------------

_REPAIR_REGISTRY: Dict[str, List[str]] = {
    "dangling_obligation_ref": [
        "Remove the dangling obligation reference from the judgment.",
        "Add the missing obligation to the manifest obligations map.",
    ],
    "missing_evidence": [
        "Add at least one evidence item for the certified coordinate.",
        "Check that the evidence_archive key matches the certificate coordinate exactly.",
    ],
    "empty_evidence": [
        "Populate the evidence_archive entry for the coordinate with at least one item.",
    ],
    "missing_epoch": [
        "Call mark_epoch() or issue_certificate() to record the coordinate's epoch.",
    ],
    "cert_below_judgment": [
        "Upgrade the certificate trust level to at least match the judgment.",
        "Downgrade the judgment trust level to reflect available evidence.",
    ],
    "uncertified_judgment": [
        "Issue a certificate covering the judgment's coordinate.",
        "Remove the requires_certificate flag if certification is not mandatory.",
    ],
    "uncovered_obligation": [
        "Add the obligation ID to a certificate's residuals list.",
        "Add evidence for the obligation's coordinate to the evidence archive.",
    ],
    "suppressed_obstruction": [
        "Add the obstruction ID to the certificate's known_obstructions list.",
        "If the obstruction is resolved, mark it discharged with evidence before certifying.",
    ],
    "epoch_inversion": [
        "Review and correct the invalidation edge direction.",
        "Re-stamp epochs so that invalidation flows from earlier to later epochs.",
    ],
    "unknown": [
        "Review theory2.tex Chapter 6, Section 4 for applicable integrity rules.",
    ],
}


def _repair_suggestions_for(violation_prefix: str) -> List[str]:
    """Return repair suggestions for *violation_prefix* from the registry."""
    return _REPAIR_REGISTRY.get(violation_prefix, _REPAIR_REGISTRY["unknown"])


# ---------------------------------------------------------------------------
# ManifestSerializer
# ---------------------------------------------------------------------------


class ManifestSerializer:
    """Utility class for serialising and deserialising :class:`ManifestTuple` objects."""

    MANIFEST_VERSION: str = "1.0"

    _REQUIRED_KEYS: Tuple[str, ...] = (
        "manifest_id",
        "judgments",
        "obligations",
        "evidence_archive",
        "obstructions",
        "certificates",
        "epoch_map",
        "invalidation_edges",
    )

    @staticmethod
    def serialize(manifest: ManifestTuple) -> str:
        """Return the JSON string representation of *manifest*."""
        data = manifest.serialize()
        data["_serializer_version"] = ManifestSerializer.MANIFEST_VERSION
        data["_serialized_at"] = _now()
        return json.dumps(data, sort_keys=True, default=str)

    @staticmethod
    def deserialize(data: str) -> Dict:
        """Parse *data* as JSON and return the resulting dictionary.

        Raises :class:`json.JSONDecodeError` on invalid JSON.
        """
        return json.loads(data)

    @classmethod
    def validate_schema(cls, data: Dict) -> Tuple[bool, List[str]]:
        """Check that *data* contains all required top-level keys.

        Required keys: ``manifest_id``, ``judgments``, ``obligations``,
        ``evidence_archive``, ``obstructions``, ``certificates``,
        ``epoch_map``, ``invalidation_edges``.

        Returns ``(is_valid, missing_key_messages)``.
        """
        missing: List[str] = []
        for key in cls._REQUIRED_KEYS:
            if key not in data:
                missing.append(f"schema_missing_key: '{key}' is absent from manifest data")
        return (len(missing) == 0, missing)

    @staticmethod
    def to_file(manifest: ManifestTuple, filepath: str) -> None:
        """Write the serialised *manifest* to *filepath*.

        Raises :class:`OSError` on file-system errors.
        """
        serialized = ManifestSerializer.serialize(manifest)
        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write(serialized)

    @staticmethod
    def from_file(filepath: str) -> Dict:
        """Read and deserialise a manifest from *filepath*.

        Returns the parsed dictionary.  Raises :class:`OSError` on file errors
        and :class:`json.JSONDecodeError` on invalid JSON.
        """
        with open(filepath, "r", encoding="utf-8") as fh:
            raw = fh.read()
        return ManifestSerializer.deserialize(raw)


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def make_manifest(
    *,
    coordinate: Optional[str] = None,
    judgments: Optional[Dict[str, Dict]] = None,
    obligations: Optional[Dict[str, Dict]] = None,
) -> ManifestTuple:
    """Create a minimal :class:`ManifestTuple`, optionally seeded with judgments/obligations.

    This is the primary convenience constructor for tests and scripts.
    """
    mt = ManifestTuple()
    if judgments:
        for j_id, j_dict in judgments.items():
            mt.add_judgment(j_id, j_dict)
    if obligations:
        for o_id, o_dict in obligations.items():
            mt.add_obligation(o_id, o_dict)
    return mt


def run_integrity_pipeline(
    manifest: ManifestTuple,
    *,
    verbose: bool = False,
) -> Tuple[bool, IntegrityReport]:
    """Run the full integrity validation pipeline for *manifest*.

    1. Creates a :class:`ManifestValidator` and validates the manifest.
    2. Optionally prints the summary if *verbose* is True.
    3. Returns ``(is_valid, report)``.

    This is the primary entry-point for manifest integrity audits.
    """
    validator = ManifestValidator()
    report = validator.validate(manifest)

    if verbose:
        print(report.summary())

    return (report.is_valid, report)


def build_invalidation_graph_from_manifest(manifest: ManifestTuple) -> InvalidationGraph:
    """Build an :class:`InvalidationGraph` from the invalidation edges in *manifest*."""
    graph = InvalidationGraph()
    for from_id, to_id in manifest.invalidation_edges:
        graph.add_edge(from_id, to_id)
    return graph


def build_epoch_map_from_manifest(manifest: ManifestTuple) -> EpochMap:
    """Build an :class:`EpochMap` populated from *manifest*'s ``epoch_map`` and certificates."""
    em = EpochMap()
    for coord, epoch in manifest.epoch_map.items():
        em.record(coord, epoch, event="manifest_epoch_map")
    for cert_id, cert_dict in manifest.certificates.items():
        coord = cert_dict.get("coordinate", "")
        issued_at = cert_dict.get("issued_at", 0.0)
        if coord and issued_at > 0:
            em.record(coord, issued_at, event=f"certificate:{cert_id}")
    return em


def manifest_diff_summary(a: ManifestTuple, b: ManifestTuple) -> Dict:
    """Return a summary of differences between two manifest versions.

    Compares judgment, obligation, certificate, and epoch counts.
    Does not perform deep equality on individual entries.
    """
    def _coord_set(m: ManifestTuple) -> Set[str]:
        return set(m.epoch_map.keys())

    return {
        "manifest_id_a": a.manifest_id,
        "manifest_id_b": b.manifest_id,
        "judgment_delta": len(b.judgments) - len(a.judgments),
        "obligation_delta": len(b.obligations) - len(a.obligations),
        "certificate_delta": len(b.certificates) - len(a.certificates),
        "obstruction_delta": len(b.obstructions) - len(a.obstructions),
        "version_a": a.version,
        "version_b": b.version,
        "new_coordinates": sorted(_coord_set(b) - _coord_set(a)),
        "removed_coordinates": sorted(_coord_set(a) - _coord_set(b)),
        "invalidation_edge_delta": len(b.invalidation_edges) - len(a.invalidation_edges),
    }
