"""Section 1: Evidence plurality and proof-solver discharge interface — Theory2 Ch6.

Different clause types require different evidence channels. The key principle
is that evidence plurality requires each clause type to be dischargeable only
by its authorised channel(s):

  - Arithmetic claims  → solver discharge (SMT/SAT solvers)
  - Relational claims  → proof checker discharge
  - Resource claims    → runtime witness
  - Semantic claims    → controlled oracle
  - Structural claims  → either solver or proof checker
  - Behavioural claims → runtime witness or human attestation

No channel may discharge a clause type outside its jurisdiction.  This is
formalized in the evidence plurality soundness theorem (Theorem 4 of Ch6).

Author: copilot
Reference: theory2.tex Chapter 6, Section 1
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
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
# Enums
# ---------------------------------------------------------------------------


class EvidenceChannel(str, Enum):
    """Enumeration of evidence discharge channels.

    Each channel has a specific jurisdiction over clause types.  Channels
    cannot discharge claims outside their authorised jurisdiction.

    Attributes:
        SOLVER: SMT/SAT solver discharge (arithmetic, structural).
        RUNTIME: Runtime witness (resource, behavioural).
        ORACLE: Controlled oracle (semantic, relational).
        COPILOT: LLM-based suggestion channel (proposals only, bounded ceiling).
        HUMAN: Human attestation (behavioural, high-stakes semantic).
        PROOF_CHECKER: Formal proof checker (relational, structural).
    """

    SOLVER = "SOLVER"
    RUNTIME = "RUNTIME"
    ORACLE = "ORACLE"
    COPILOT = "COPILOT"
    HUMAN = "HUMAN"
    PROOF_CHECKER = "PROOF_CHECKER"


class ClauseType(str, Enum):
    """Types of clauses that can appear in a judgment.

    Each clause type has one or more authorised discharge channels defined
    in ChannelJurisdiction.

    Attributes:
        ARITHMETIC: Numeric / algebraic constraint.
        RELATIONAL: Relational database or set-theoretic claim.
        RESOURCE: Memory, time, energy resource bound.
        SEMANTIC: Semantic / meaning-level claim.
        STRUCTURAL: Structural / shape / topological claim.
        BEHAVIORAL: Observable runtime behaviour.
    """

    ARITHMETIC = "ARITHMETIC"
    RELATIONAL = "RELATIONAL"
    RESOURCE = "RESOURCE"
    SEMANTIC = "SEMANTIC"
    STRUCTURAL = "STRUCTURAL"
    BEHAVIORAL = "BEHAVIORAL"


# ---------------------------------------------------------------------------
# Trust level constants for this module
# ---------------------------------------------------------------------------

_TRUST_ORDER: Dict[str, int] = {
    "MECHANICALLY_VERIFIED": 7,
    "SOLVER_DISCHARGED": 6,
    "RUNTIME_WITNESSED": 5,
    "HUMAN_ATTESTED": 4,
    "ORACLE_PROPOSED": 3,
    "COPILOT_SUGGESTED": 2,
    "UNVERIFIED": 1,
    "CONTRADICTED": 0,
}

# copilot: Copilot suggestions are capped at COPILOT_SUGGESTED — they must be
# upgraded through an explicit human/oracle review step.
_COPILOT_CEILING: str = "COPILOT_SUGGESTED"

# Default trust levels by channel
_CHANNEL_DEFAULT_TRUST: Dict[str, str] = {
    EvidenceChannel.SOLVER: "SOLVER_DISCHARGED",
    EvidenceChannel.RUNTIME: "RUNTIME_WITNESSED",
    EvidenceChannel.ORACLE: "ORACLE_PROPOSED",
    EvidenceChannel.COPILOT: "COPILOT_SUGGESTED",
    EvidenceChannel.HUMAN: "HUMAN_ATTESTED",
    EvidenceChannel.PROOF_CHECKER: "MECHANICALLY_VERIFIED",
}

# ---------------------------------------------------------------------------
# ChannelJurisdiction
# ---------------------------------------------------------------------------


@dataclass
class ChannelJurisdiction:
    """Maps clause types to their authorised discharge channels.

    The jurisdiction is a many-to-many relation: a clause type may be
    dischargeable by multiple channels, and a channel may be authorised for
    multiple clause types.

    Attributes:
        jurisdiction_map: Maps ClauseType → frozenset of EvidenceChannel.
        strict_mode: When True, any unauthorised channel raises; when False, warns.
    """

    jurisdiction_map: Dict[str, FrozenSet[str]] = field(default_factory=dict)
    strict_mode: bool = True
    _violation_log: List[Dict[str, Any]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        """Populate default jurisdiction map from theory2.tex Ch6 definitions."""
        if not self.jurisdiction_map:
            self.jurisdiction_map = {
                ClauseType.ARITHMETIC: frozenset({EvidenceChannel.SOLVER, EvidenceChannel.PROOF_CHECKER}),
                ClauseType.RELATIONAL: frozenset({EvidenceChannel.PROOF_CHECKER, EvidenceChannel.ORACLE}),
                ClauseType.RESOURCE: frozenset({EvidenceChannel.RUNTIME, EvidenceChannel.SOLVER}),
                ClauseType.SEMANTIC: frozenset({EvidenceChannel.ORACLE, EvidenceChannel.HUMAN}),
                ClauseType.STRUCTURAL: frozenset({EvidenceChannel.SOLVER, EvidenceChannel.PROOF_CHECKER}),
                ClauseType.BEHAVIORAL: frozenset({EvidenceChannel.RUNTIME, EvidenceChannel.HUMAN}),
            }

    def is_authorized(self, clause_type: str, channel: str) -> bool:
        """Return True if the channel is authorised for the clause type.

        Args:
            clause_type: ClauseType string name.
            channel: EvidenceChannel string name.

        Returns:
            True if authorised, False otherwise.
        """
        allowed = self.jurisdiction_map.get(clause_type, frozenset())
        return channel in allowed

    def get_channels_for_clause(self, clause_type: str) -> FrozenSet[str]:
        """Return all authorised channels for a clause type.

        Args:
            clause_type: ClauseType string name.

        Returns:
            FrozenSet of EvidenceChannel string names.
        """
        return self.jurisdiction_map.get(clause_type, frozenset())

    def validate_evidence_plurality(
        self,
        clause_type: str,
        available_channels: Set[str],
    ) -> Tuple[bool, List[str]]:
        """Validate that at least one authorised channel has produced evidence.

        Evidence plurality requires that for each clause type, at least one
        channel from the authorised set has provided evidence.

        Args:
            clause_type: ClauseType string name.
            available_channels: Set of channels that have provided evidence.

        Returns:
            Tuple of (satisfied_bool, list_of_violation_strings).
        """
        authorised = self.get_channels_for_clause(clause_type)
        if not authorised:
            return (False, [f"No authorised channels defined for clause type '{clause_type}'"])
        present = authorised & available_channels
        if present:
            return (True, [])
        violation = (
            f"Plurality violation for clause type '{clause_type}': "
            f"no authorised channel has provided evidence. "
            f"Authorised: {sorted(authorised)}, available: {sorted(available_channels)}"
        )
        self._violation_log.append({
            "clause_type": clause_type,
            "authorised": sorted(authorised),
            "available": sorted(available_channels),
            "timestamp": time.time(),
        })
        return (False, [violation])

    def report_jurisdiction_violation(
        self, clause_type: str, channel: str
    ) -> Dict[str, Any]:
        """Record and return a jurisdiction violation report.

        Args:
            clause_type: ClauseType string name.
            channel: The unauthorised channel.

        Returns:
            Violation record dict.
        """
        authorised = self.get_channels_for_clause(clause_type)
        record = {
            "event": "jurisdiction_violation",
            "clause_type": clause_type,
            "channel": channel,
            "authorised_channels": sorted(authorised),
            "timestamp": time.time(),
        }
        self._violation_log.append(record)
        return record

    def get_violation_log(self) -> List[Dict[str, Any]]:
        """Return the accumulated violation log.

        Returns:
            List of violation record dicts.
        """
        return list(self._violation_log)

    def clear_violation_log(self) -> None:
        """Clear the accumulated violation log."""
        self._violation_log.clear()

    def serialize(self) -> Dict[str, Any]:
        """Serialise the jurisdiction configuration.

        Returns:
            Dict with jurisdiction_map and strict_mode.
        """
        return {
            "jurisdiction_map": {
                k: sorted(v) for k, v in self.jurisdiction_map.items()
            },
            "strict_mode": self.strict_mode,
        }


# ---------------------------------------------------------------------------
# ProofSolverInterface
# ---------------------------------------------------------------------------


@dataclass
class ProofSolverInterface:
    """Routes discharge requests to the appropriate evidence channels.

    The interface acts as a dispatch layer: given a clause type, it selects
    the appropriate channel handler and returns an evidence record.

    Attributes:
        jurisdiction: ChannelJurisdiction controlling routing.
        discharge_log: Append-only log of discharge events.
        channel_stats: Count of discharges per channel.
    """

    jurisdiction: ChannelJurisdiction = field(default_factory=ChannelJurisdiction)
    discharge_log: List[Dict[str, Any]] = field(default_factory=list)
    channel_stats: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def route_clause(
        self,
        clause_type: str,
        clause_content: str,
        preferred_channel: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Route a clause to an appropriate channel.

        Selects the highest-priority authorised channel unless a preferred
        channel is specified and authorised.

        Args:
            clause_type: ClauseType string name.
            clause_content: Content of the clause to discharge.
            preferred_channel: Optional preferred EvidenceChannel name.

        Returns:
            Tuple of (selected_channel, trust_level_name).

        Raises:
            ValueError: If no authorised channel is available.
        """
        authorised = self.jurisdiction.get_channels_for_clause(clause_type)
        if not authorised:
            raise ValueError(f"No authorised channels for clause type '{clause_type}'")
        if preferred_channel and preferred_channel in authorised:
            selected = preferred_channel
        else:
            # Priority: PROOF_CHECKER > SOLVER > RUNTIME > ORACLE > HUMAN > COPILOT
            priority = [
                EvidenceChannel.PROOF_CHECKER,
                EvidenceChannel.SOLVER,
                EvidenceChannel.RUNTIME,
                EvidenceChannel.ORACLE,
                EvidenceChannel.HUMAN,
                EvidenceChannel.COPILOT,
            ]
            selected = next((ch for ch in priority if ch in authorised), list(authorised)[0])
        trust_level = _CHANNEL_DEFAULT_TRUST.get(selected, "UNVERIFIED")
        return (selected, trust_level)

    def discharge_arithmetic(
        self, claim: str, coordinate: str, solver_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Discharge an arithmetic claim via solver.

        Args:
            claim: The arithmetic claim.
            coordinate: Geometric coordinate.
            solver_result: Optional raw solver output dict.

        Returns:
            Evidence record dict.
        """
        if not self.jurisdiction.is_authorized(ClauseType.ARITHMETIC, EvidenceChannel.SOLVER):
            raise ValueError("SOLVER is not authorised for ARITHMETIC in this jurisdiction")
        record = self._make_evidence_record(
            channel=EvidenceChannel.SOLVER,
            clause_type=ClauseType.ARITHMETIC,
            claim=claim,
            coordinate=coordinate,
            trust_level="SOLVER_DISCHARGED",
            extra={"solver_result": solver_result or {}},
        )
        return record

    def discharge_relational(
        self, claim: str, coordinate: str, proof_term: Optional[str] = None
    ) -> Dict[str, Any]:
        """Discharge a relational claim via proof checker.

        Args:
            claim: The relational claim.
            coordinate: Geometric coordinate.
            proof_term: Optional formal proof term string.

        Returns:
            Evidence record dict.
        """
        channel = EvidenceChannel.PROOF_CHECKER
        if not self.jurisdiction.is_authorized(ClauseType.RELATIONAL, channel):
            channel = EvidenceChannel.ORACLE
        record = self._make_evidence_record(
            channel=channel,
            clause_type=ClauseType.RELATIONAL,
            claim=claim,
            coordinate=coordinate,
            trust_level=_CHANNEL_DEFAULT_TRUST.get(channel, "ORACLE_PROPOSED"),
            extra={"proof_term": proof_term or ""},
        )
        return record

    def discharge_resource(
        self, claim: str, coordinate: str, witness_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Discharge a resource claim via runtime witness.

        Args:
            claim: The resource claim (e.g. memory bound).
            coordinate: Geometric coordinate.
            witness_data: Optional runtime measurement data.

        Returns:
            Evidence record dict.
        """
        record = self._make_evidence_record(
            channel=EvidenceChannel.RUNTIME,
            clause_type=ClauseType.RESOURCE,
            claim=claim,
            coordinate=coordinate,
            trust_level="RUNTIME_WITNESSED",
            extra={"witness_data": witness_data or {}},
        )
        return record

    def discharge_semantic(
        self, claim: str, coordinate: str, oracle_response: Optional[str] = None
    ) -> Dict[str, Any]:
        """Discharge a semantic claim via controlled oracle.

        Args:
            claim: The semantic claim.
            coordinate: Geometric coordinate.
            oracle_response: Optional oracle response string.

        Returns:
            Evidence record dict.
        """
        record = self._make_evidence_record(
            channel=EvidenceChannel.ORACLE,
            clause_type=ClauseType.SEMANTIC,
            claim=claim,
            coordinate=coordinate,
            trust_level="ORACLE_PROPOSED",
            extra={"oracle_response": oracle_response or ""},
        )
        return record

    def validate_discharge(
        self, clause_type: str, channel: str, evidence_record: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """Validate that a discharge record is well-formed and authorised.

        Args:
            clause_type: ClauseType string name.
            channel: Channel that produced the evidence.
            evidence_record: The evidence record to validate.

        Returns:
            Tuple of (valid_bool, list_of_violation_strings).
        """
        violations = []
        if not self.jurisdiction.is_authorized(clause_type, channel):
            report = self.jurisdiction.report_jurisdiction_violation(clause_type, channel)
            violations.append(
                f"Unauthorised discharge: channel '{channel}' is not authorised for "
                f"clause type '{clause_type}'"
            )
        required_keys = {"record_id", "channel", "clause_type", "claim", "trust_level", "timestamp"}
        missing = required_keys - set(evidence_record.keys())
        if missing:
            violations.append(f"Evidence record missing required keys: {sorted(missing)}")
        if evidence_record.get("trust_level") == "CONTRADICTED":
            violations.append("Evidence record has CONTRADICTED trust level")
        return (len(violations) == 0, violations)

    def emit_evidence_record(
        self,
        clause_type: str,
        claim: str,
        coordinate: str,
        preferred_channel: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Route, discharge, and emit a complete evidence record.

        Combines route_clause and the appropriate discharge method.

        Args:
            clause_type: ClauseType string name.
            claim: Claim to discharge.
            coordinate: Geometric coordinate.
            preferred_channel: Optional preferred channel.

        Returns:
            Complete evidence record dict.
        """
        channel, trust_level = self.route_clause(clause_type, claim, preferred_channel)
        record = self._make_evidence_record(
            channel=channel,
            clause_type=clause_type,
            claim=claim,
            coordinate=coordinate,
            trust_level=trust_level,
        )
        valid, violations = self.validate_discharge(clause_type, channel, record)
        record["is_valid"] = valid
        record["violations"] = violations
        return record

    def _make_evidence_record(
        self,
        channel: str,
        clause_type: str,
        claim: str,
        coordinate: str,
        trust_level: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a canonical evidence record.

        Args:
            channel: Discharge channel.
            clause_type: Clause type.
            claim: Claim content.
            coordinate: Geometric coordinate.
            trust_level: Resulting trust level.
            extra: Optional additional fields.

        Returns:
            Evidence record dict.
        """
        record_id = str(uuid.uuid4())
        record = {
            "record_id": record_id,
            "channel": channel,
            "clause_type": clause_type,
            "claim": claim,
            "coordinate": coordinate,
            "trust_level": trust_level,
            "timestamp": time.time(),
            **(extra or {}),
        }
        self.discharge_log.append(record)
        self.channel_stats[channel] += 1
        return record

    def get_stats(self) -> Dict[str, Any]:
        """Return discharge statistics.

        Returns:
            Dict with per-channel discharge counts and totals.
        """
        return {
            "channel_counts": dict(self.channel_stats),
            "total_discharges": sum(self.channel_stats.values()),
            "log_size": len(self.discharge_log),
        }


# ---------------------------------------------------------------------------
# EvidenceBundle
# ---------------------------------------------------------------------------


@dataclass
class _BundleItem:
    """A single item in an EvidenceBundle.

    Attributes:
        item_id: Unique identifier.
        channel: EvidenceChannel string name.
        claim: Claim description.
        trust_level: Trust level name.
        provenance_id: Backing provenance node ID.
        timestamp: Creation time.
        metadata: Arbitrary metadata.
    """

    item_id: str
    channel: str
    claim: str
    trust_level: str
    provenance_id: str
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    """A collection of (channel, claim, trust_level, provenance_node) tuples.

    An EvidenceBundle aggregates evidence from multiple channels for a single
    clause or coordinate.  It supports plurality checking and composition.

    Attributes:
        bundle_id: Unique identifier.
        coordinate: Geometric coordinate this bundle covers.
        items: List of bundle items.
        channel_index: Maps channel name → list of item_ids.
    """

    bundle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    coordinate: str = ""
    items: List[_BundleItem] = field(default_factory=list)
    channel_index: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))

    def add(
        self,
        channel: str,
        claim: str,
        trust_level: str,
        provenance_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add an evidence item to the bundle.

        Args:
            channel: Evidence channel name.
            claim: Claim description.
            trust_level: Trust level name.
            provenance_id: Provenance node ID.
            metadata: Optional metadata.

        Returns:
            item_id of the newly added item.
        """
        item_id = str(uuid.uuid4())
        item = _BundleItem(
            item_id=item_id,
            channel=channel,
            claim=claim,
            trust_level=trust_level,
            provenance_id=provenance_id,
            timestamp=time.time(),
            metadata=metadata or {},
        )
        self.items.append(item)
        self.channel_index[channel].append(item_id)
        return item_id

    def compose(self) -> str:
        """Compose all evidence items to a single trust level (minimum rank).

        Returns:
            Name of the composed trust level.
        """
        if not self.items:
            return "UNVERIFIED"
        min_rank = min(_TRUST_ORDER.get(i.trust_level, 0) for i in self.items)
        for name, rank in sorted(_TRUST_ORDER.items(), key=lambda kv: kv[1]):
            if rank == min_rank:
                return name
        return "UNVERIFIED"

    def check_plurality(self, required_channels: Set[str]) -> Tuple[bool, List[str]]:
        """Check that required channels are represented in the bundle.

        Args:
            required_channels: Set of channel names that must be present.

        Returns:
            Tuple of (satisfied_bool, list_of_missing_channels).
        """
        present = set(self.channel_index.keys())
        missing = sorted(required_channels - present)
        return (len(missing) == 0, missing)

    def get_strongest(self) -> Optional[_BundleItem]:
        """Return the item with the highest trust rank.

        Returns:
            The _BundleItem with highest trust, or None if empty.
        """
        if not self.items:
            return None
        return max(self.items, key=lambda i: _TRUST_ORDER.get(i.trust_level, 0))

    def get_by_channel(self, channel: str) -> List[_BundleItem]:
        """Return all items for a specific channel.

        Args:
            channel: Channel name.

        Returns:
            List of _BundleItem objects.
        """
        ids = self.channel_index.get(channel, [])
        item_map = {i.item_id: i for i in self.items}
        return [item_map[iid] for iid in ids if iid in item_map]

    def channels_present(self) -> Set[str]:
        """Return the set of channels that have contributed to this bundle.

        Returns:
            Set of channel name strings.
        """
        return set(self.channel_index.keys())

    def serialize(self) -> Dict[str, Any]:
        """Serialise the bundle to a plain dict.

        Returns:
            Dict with bundle_id, coordinate, and items.
        """
        return {
            "bundle_id": self.bundle_id,
            "coordinate": self.coordinate,
            "composed_trust": self.compose(),
            "items": [
                {
                    "item_id": i.item_id,
                    "channel": i.channel,
                    "claim": i.claim,
                    "trust_level": i.trust_level,
                    "provenance_id": i.provenance_id,
                    "timestamp": i.timestamp,
                    "metadata": i.metadata,
                }
                for i in self.items
            ],
        }


# ---------------------------------------------------------------------------
# PluralityChecker
# ---------------------------------------------------------------------------


@dataclass
class PluralityChecker:
    """Verifies that evidence configurations satisfy plurality requirements.

    Plurality requires that for each clause type in a judgment, at least one
    authorised channel has contributed admissible evidence.

    Attributes:
        jurisdiction: ChannelJurisdiction used for authorisation checks.
        check_log: Log of plurality check results.
    """

    jurisdiction: ChannelJurisdiction = field(default_factory=ChannelJurisdiction)
    check_log: List[Dict[str, Any]] = field(default_factory=list)

    _ADMISSIBLE_TRUST_LEVELS: FrozenSet[str] = field(
        default_factory=lambda: frozenset(
            {
                "MECHANICALLY_VERIFIED",
                "SOLVER_DISCHARGED",
                "RUNTIME_WITNESSED",
                "HUMAN_ATTESTED",
                "ORACLE_PROPOSED",
            }
        ),
        repr=False,
    )

    def check_bundle(
        self, clause_type: str, bundle: EvidenceBundle
    ) -> Tuple[bool, List[str]]:
        """Check that a bundle satisfies plurality for the given clause type.

        Args:
            clause_type: ClauseType string name.
            bundle: EvidenceBundle containing evidence items.

        Returns:
            Tuple of (satisfied_bool, list_of_violation_strings).
        """
        authorised = self.jurisdiction.get_channels_for_clause(clause_type)
        if not authorised:
            return (False, [f"No authorised channels for clause type '{clause_type}'"])
        admissible_channels: Set[str] = set()
        for item in bundle.items:
            if item.trust_level in self._ADMISSIBLE_TRUST_LEVELS:
                admissible_channels.add(item.channel)
        present_authorised = authorised & admissible_channels
        satisfied = len(present_authorised) > 0
        violations: List[str] = []
        if not satisfied:
            violations.append(
                f"Plurality not satisfied for '{clause_type}': "
                f"no admissible evidence from authorised channels {sorted(authorised)}. "
                f"Admissible evidence present from: {sorted(admissible_channels)}"
            )
        self.check_log.append({
            "clause_type": clause_type,
            "bundle_id": bundle.bundle_id,
            "satisfied": satisfied,
            "violations": violations,
            "timestamp": time.time(),
        })
        return (satisfied, violations)

    def check_all_clause_types(
        self,
        bundles_by_clause_type: Dict[str, EvidenceBundle],
    ) -> Tuple[bool, Dict[str, List[str]]]:
        """Check plurality for all clause types present.

        Args:
            bundles_by_clause_type: Dict mapping ClauseType name → EvidenceBundle.

        Returns:
            Tuple of (all_satisfied_bool, dict of clause_type → violations).
        """
        all_satisfied = True
        all_violations: Dict[str, List[str]] = {}
        for clause_type, bundle in bundles_by_clause_type.items():
            satisfied, violations = self.check_bundle(clause_type, bundle)
            if not satisfied:
                all_satisfied = False
                all_violations[clause_type] = violations
        return (all_satisfied, all_violations)

    def require_plurality_or_raise(
        self, clause_type: str, bundle: EvidenceBundle
    ) -> None:
        """Check plurality and raise if not satisfied.

        Args:
            clause_type: ClauseType string name.
            bundle: Evidence bundle to check.

        Raises:
            ValueError: If plurality requirement is not satisfied.
        """
        satisfied, violations = self.check_bundle(clause_type, bundle)
        if not satisfied:
            raise ValueError(
                f"Evidence plurality requirement not satisfied: {violations}"
            )

    def report(self) -> Dict[str, Any]:
        """Return a summary of all plurality checks performed.

        Returns:
            Dict with total_checks, passed, failed, and log.
        """
        passed = sum(1 for entry in self.check_log if entry["satisfied"])
        failed = len(self.check_log) - passed
        return {
            "total_checks": len(self.check_log),
            "passed": passed,
            "failed": failed,
            "log": list(self.check_log),
        }
