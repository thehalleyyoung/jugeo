"""
Section 3: Certificates as faithful projections - Theory2 Ch6.

Theory: A certificate faithfully projects the full manifest (J,O,E,X,K,eta,sigma)
preserving residuals and obstructions. No silent strengthening.

Author: copilot
Reference: theory2.tex Chapter 6, Section 3
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
from dataclasses import dataclass, field, replace as dataclasses_replace
from enum import Enum
from typing import (
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Any,
)
from collections import defaultdict

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
# Helper utilities
# ---------------------------------------------------------------------------


def _rank(level: str) -> int:
    """Return numeric rank for a trust level name, defaulting to 0."""
    return _TRUST_ORDER.get(level.lower(), 0)


def _sha256_dict(d: Dict) -> str:
    """Return a stable SHA-256 hex digest for a dictionary."""
    serialized = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _now() -> float:
    """Return the current wall-clock time."""
    return time.time()


# ---------------------------------------------------------------------------
# ManifestProjection
# ---------------------------------------------------------------------------


@dataclass
class ManifestProjection:
    """Represents a faithful projection of a full manifest tuple.

    Each field corresponds to a component of the manifest (J, O, E, X, K, eta, sigma).
    The projection carries residuals and obstructions forward so that nothing is silently
    dropped.
    """

    projection_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    coordinate: str = ""
    # J – judgments
    judgments: List[Dict] = field(default_factory=list)
    # O – obligations
    obligations: List[Dict] = field(default_factory=list)
    # E – evidence items
    evidence_items: List[Dict] = field(default_factory=list)
    # X – obstructions
    obstructions: List[Dict] = field(default_factory=list)
    # K – certificates
    certificates: List[Dict] = field(default_factory=list)
    # eta – epoch map
    epoch_map: Dict[str, float] = field(default_factory=dict)
    # sigma – invalidation edges
    invalidation_edges: List[Tuple[str, str]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Core projection
    # ------------------------------------------------------------------

    def project(self, manifest_dict: Dict) -> Dict:
        """Extract J,O,E,X,K,eta,sigma from *manifest_dict* and populate fields.

        Returns a summary dict describing what was extracted.
        """
        # Store the source manifest id if present
        self.source_manifest_id = manifest_dict.get("manifest_id", self.source_manifest_id)
        self.coordinate = manifest_dict.get("coordinate", "")

        # J – judgments (may be a list or dict keyed by judgment_id)
        raw_j = manifest_dict.get("judgments", [])
        if isinstance(raw_j, dict):
            self.judgments = list(raw_j.values())
        elif isinstance(raw_j, list):
            self.judgments = list(raw_j)
        else:
            self.judgments = []

        # O – obligations
        raw_o = manifest_dict.get("obligations", [])
        if isinstance(raw_o, dict):
            self.obligations = list(raw_o.values())
        elif isinstance(raw_o, list):
            self.obligations = list(raw_o)
        else:
            self.obligations = []

        # E – evidence
        raw_e = manifest_dict.get("evidence_archive", manifest_dict.get("evidence_items", []))
        if isinstance(raw_e, dict):
            # flatten per-coordinate lists
            flattened: List[Dict] = []
            for items in raw_e.values():
                if isinstance(items, list):
                    flattened.extend(items)
            self.evidence_items = flattened
        elif isinstance(raw_e, list):
            self.evidence_items = list(raw_e)
        else:
            self.evidence_items = []

        # X – obstructions
        raw_x = manifest_dict.get("obstructions", [])
        if isinstance(raw_x, dict):
            self.obstructions = list(raw_x.values())
        elif isinstance(raw_x, list):
            self.obstructions = list(raw_x)
        else:
            self.obstructions = []

        # K – certificates
        raw_k = manifest_dict.get("certificates", [])
        if isinstance(raw_k, dict):
            self.certificates = list(raw_k.values())
        elif isinstance(raw_k, list):
            self.certificates = list(raw_k)
        else:
            self.certificates = []

        # eta – epoch map
        raw_eta = manifest_dict.get("epoch_map", {})
        self.epoch_map = dict(raw_eta) if isinstance(raw_eta, dict) else {}

        # sigma – invalidation edges
        raw_sigma = manifest_dict.get("invalidation_edges", [])
        if isinstance(raw_sigma, list):
            self.invalidation_edges = [
                (e[0], e[1]) for e in raw_sigma if isinstance(e, (list, tuple)) and len(e) >= 2
            ]
        else:
            self.invalidation_edges = []

        # Build summary
        return {
            "projection_id": self.projection_id,
            "source_manifest_id": self.source_manifest_id,
            "coordinate": self.coordinate,
            "judgment_count": len(self.judgments),
            "obligation_count": len(self.obligations),
            "evidence_count": len(self.evidence_items),
            "obstruction_count": len(self.obstructions),
            "certificate_count": len(self.certificates),
            "epoch_entry_count": len(self.epoch_map),
            "invalidation_edge_count": len(self.invalidation_edges),
        }

    # ------------------------------------------------------------------
    # Faithfulness checks
    # ------------------------------------------------------------------

    def verify_faithfulness(self) -> Tuple[bool, List[str]]:
        """Check that the projection is faithful to the source manifest.

        Faithfulness requires:
        1. No certificate silently strengthens trust beyond what evidence supports.
        2. Residuals from obligations are preserved in certificates.
        3. Obstructions are not suppressed by certificates.

        Returns ``(is_faithful, violations)``.
        """
        violations: List[str] = []

        ok1, v1 = self.check_no_silent_strengthening()
        if not ok1:
            violations.extend(v1)

        ok2, v2 = self.check_residuals_preserved()
        if not ok2:
            violations.extend(v2)

        ok3, v3 = self.check_obstructions_preserved()
        if not ok3:
            violations.extend(v3)

        # Additionally verify that if there are obligations and no certs, that is a problem
        if self.obligations and not self.certificates:
            violations.append(
                f"faithfulness_gap: {len(self.obligations)} obligation(s) exist but no certificates were projected"
            )

        return (len(violations) == 0, violations)

    def check_residuals_preserved(self) -> Tuple[bool, List[str]]:
        """Check that each certificate carries forward residuals for its source obligations.

        If the manifest has obligations, each certificate should list at least one residual
        (an unmet obligation it acknowledges rather than silently discharging).

        Returns ``(ok, missing_messages)``.
        """
        missing: List[str] = []

        if not self.obligations:
            return (True, missing)

        obligation_ids: Set[str] = {
            o.get("obligation_id", o.get("id", "")) for o in self.obligations
        }
        obligation_ids.discard("")

        for cert in self.certificates:
            cert_id = cert.get("cert_id", cert.get("id", "<unknown>"))
            residuals = cert.get("residuals", cert.get("residual_obligations", []))
            if not residuals:
                missing.append(
                    f"missing_residual: certificate '{cert_id}' carries no residual obligations "
                    f"but {len(obligation_ids)} obligation(s) exist in source manifest"
                )
            else:
                residual_ids: Set[str] = set(residuals) if isinstance(residuals[0], str) else {
                    r.get("obligation_id", r.get("id", "")) for r in residuals
                }
                dropped = obligation_ids - residual_ids
                for oid in sorted(dropped):
                    missing.append(
                        f"erased_residual: certificate '{cert_id}' silently dropped obligation '{oid}'"
                    )

        return (len(missing) == 0, missing)

    def check_obstructions_preserved(self) -> Tuple[bool, List[str]]:
        """Check that obstructions from the source manifest appear in certificate metadata.

        A certificate for a coordinate that has obstructions must reference those obstructions,
        either as known-and-discharged or as unresolved.

        Returns ``(ok, suppression_messages)``.
        """
        suppressed: List[str] = []

        if not self.obstructions:
            return (True, suppressed)

        obstruction_ids: Set[str] = {
            x.get("obstruction_id", x.get("id", "")) for x in self.obstructions
        }
        obstruction_ids.discard("")

        for cert in self.certificates:
            cert_id = cert.get("cert_id", cert.get("id", "<unknown>"))
            cert_coord = cert.get("coordinate", "")
            cert_obstructions: Set[str] = set(
                cert.get("obstructions", cert.get("known_obstructions", []))
            )

            coord_obstructions = {
                oid for oid in obstruction_ids
                if any(
                    x.get("coordinate", "") == cert_coord
                    for x in self.obstructions
                    if x.get("obstruction_id", x.get("id", "")) == oid
                )
            }

            if coord_obstructions:
                missing_in_cert = coord_obstructions - cert_obstructions
                for oid in sorted(missing_in_cert):
                    suppressed.append(
                        f"suppressed_obstruction: certificate '{cert_id}' for coordinate "
                        f"'{cert_coord}' does not reference obstruction '{oid}'"
                    )

        return (len(suppressed) == 0, suppressed)

    def check_no_silent_strengthening(self) -> Tuple[bool, List[str]]:
        """Check that no certificate claims a higher trust level than its evidence supports.

        For each certificate, the maximum rank of associated evidence items must be >= the
        rank of the certificate's claimed trust level.

        Returns ``(ok, strengthening_messages)``.
        """
        violations: List[str] = []

        # Build an index: coordinate -> max evidence rank
        coord_evidence_rank: Dict[str, int] = {}
        for ev in self.evidence_items:
            coord = ev.get("coordinate", "")
            ev_trust = ev.get("trust_level", "none")
            ev_rank = _rank(ev_trust)
            existing = coord_evidence_rank.get(coord, -1)
            if ev_rank > existing:
                coord_evidence_rank[coord] = ev_rank

        for cert in self.certificates:
            cert_id = cert.get("cert_id", cert.get("id", "<unknown>"))
            cert_coord = cert.get("coordinate", "")
            cert_trust = cert.get("trust_level", "none")
            cert_rank = _rank(cert_trust)

            max_ev_rank = coord_evidence_rank.get(cert_coord, -1)
            if max_ev_rank < 0 and cert_rank > 0:
                violations.append(
                    f"silent_strengthening: certificate '{cert_id}' claims trust "
                    f"'{cert_trust}' (rank {cert_rank}) for coordinate '{cert_coord}' "
                    f"but no evidence exists for that coordinate"
                )
            elif cert_rank > max_ev_rank:
                supporting_trust = next(
                    (
                        ev.get("trust_level", "none")
                        for ev in self.evidence_items
                        if ev.get("coordinate", "") == cert_coord
                    ),
                    "none",
                )
                violations.append(
                    f"silent_strengthening: certificate '{cert_id}' claims trust "
                    f"'{cert_trust}' (rank {cert_rank}) but best evidence for "
                    f"'{cert_coord}' is '{supporting_trust}' (rank {max_ev_rank})"
                )

        return (len(violations) == 0, violations)

    # ------------------------------------------------------------------
    # Summary / serialization
    # ------------------------------------------------------------------

    def get_projection_summary(self) -> Dict:
        """Return all projection fields as a plain dictionary."""
        return {
            "projection_id": self.projection_id,
            "source_manifest_id": self.source_manifest_id,
            "coordinate": self.coordinate,
            "judgments": self.judgments,
            "obligations": self.obligations,
            "evidence_items": self.evidence_items,
            "obstructions": self.obstructions,
            "certificates": self.certificates,
            "epoch_map": self.epoch_map,
            "invalidation_edges": [list(e) for e in self.invalidation_edges],
        }

    def serialize(self) -> Dict:
        """Return a JSON-serializable representation of this projection."""
        d = self.get_projection_summary()
        d["_type"] = "ManifestProjection"
        d["_schema_version"] = "1.0"
        d["_hash"] = _sha256_dict(d)
        return d


# ---------------------------------------------------------------------------
# FaithfulnessChecker
# ---------------------------------------------------------------------------


@dataclass
class FaithfulnessChecker:
    """Runs faithfulness checks on a :class:`ManifestProjection` and records violations."""

    checker_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    violations: List[Dict] = field(default_factory=list)
    repair_suggestions: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------

    def check(self, projection: ManifestProjection) -> bool:
        """Run all faithfulness checks on *projection*.

        Accumulates violations into ``self.violations`` and returns ``True`` iff all checks pass.
        """
        is_faithful, violation_messages = projection.verify_faithfulness()

        for msg in violation_messages:
            vtype = msg.split(":")[0] if ":" in msg else "unknown"
            self.violations.append(
                {
                    "checker_id": self.checker_id,
                    "projection_id": projection.projection_id,
                    "violation_type": vtype,
                    "message": msg,
                    "timestamp": _now(),
                }
            )
            self.repair_suggestions.extend(self.suggest_repairs(vtype))

        return is_faithful

    def check_certificate_honest(
        self, cert_dict: Dict, evidence_list: List[Dict]
    ) -> Tuple[bool, str]:
        """Check a single certificate against a list of evidence items.

        Returns ``(honest, reason)`` where *reason* is an explanatory string.
        """
        cert_id = cert_dict.get("cert_id", cert_dict.get("id", "<unknown>"))
        cert_trust = cert_dict.get("trust_level", "none")
        cert_rank = _rank(cert_trust)
        cert_coord = cert_dict.get("coordinate", "")

        if not evidence_list:
            if cert_rank > 0:
                return (
                    False,
                    f"certificate '{cert_id}' claims trust '{cert_trust}' but no supporting evidence was provided",
                )
            return (True, "no evidence required for trust level 'none'")

        max_ev_rank = max(_rank(ev.get("trust_level", "none")) for ev in evidence_list)
        relevant_evidence = [
            ev for ev in evidence_list if ev.get("coordinate", "") == cert_coord
        ]
        max_relevant_rank = (
            max((_rank(ev.get("trust_level", "none")) for ev in relevant_evidence), default=-1)
            if relevant_evidence
            else max_ev_rank
        )

        if cert_rank > max_relevant_rank:
            best_ev_trust = (
                next(
                    (
                        ev.get("trust_level", "none")
                        for ev in sorted(
                            relevant_evidence or evidence_list,
                            key=lambda e: _rank(e.get("trust_level", "none")),
                            reverse=True,
                        )
                    ),
                    "none",
                )
            )
            return (
                False,
                f"certificate '{cert_id}' claims '{cert_trust}' (rank {cert_rank}) "
                f"but best evidence is '{best_ev_trust}' (rank {max_relevant_rank}): "
                f"silent strengthening detected",
            )

        residuals = cert_dict.get("residuals", cert_dict.get("residual_obligations", []))
        if not residuals:
            return (
                False,
                f"certificate '{cert_id}' carries no residuals; obligations may have been silently discharged",
            )

        return (True, f"certificate '{cert_id}' is honest at trust level '{cert_trust}'")

    def report_violations(self) -> List[Dict]:
        """Return all accumulated violation records."""
        return list(self.violations)

    def suggest_repairs(self, violation_type: str) -> List[str]:
        """Return repair suggestions for a given *violation_type* string."""
        suggestions: Dict[str, List[str]] = {
            "silent_strengthening": [
                "Demote the certificate trust level to match the strongest available evidence.",
                "Add additional corroborating evidence before re-certifying.",
                "Audit the evidence chain to confirm no fabricated support.",
            ],
            "missing_residual": [
                "Add explicit residual obligation entries to the certificate.",
                "Document which obligations were discharged and by what authority.",
                "Re-run certificate issuance with a residual-aware builder.",
            ],
            "erased_residual": [
                "Restore the erased obligation as a residual in the certificate.",
                "If the obligation was intentionally discharged, add a discharge record.",
                "Review certificate builder configuration for silent-discharge bugs.",
            ],
            "suppressed_obstruction": [
                "Add the obstruction reference to the certificate's known_obstructions field.",
                "If the obstruction is resolved, mark it discharged with evidence.",
                "Do not issue certificates for coordinates with unacknowledged obstructions.",
            ],
            "faithfulness_gap": [
                "Issue at least one certificate to cover the outstanding obligations.",
                "Verify that all obligation IDs are correctly linked to judgments.",
            ],
        }
        return suggestions.get(
            violation_type,
            [f"Review violation type '{violation_type}' in theory2.tex Chapter 6 Section 3."],
        )

    def clear(self) -> None:
        """Reset violations and repair suggestions."""
        self.violations = []
        self.repair_suggestions = []

    def serialize(self) -> Dict:
        """Return the checker state as a JSON-serializable dictionary."""
        return {
            "_type": "FaithfulnessChecker",
            "checker_id": self.checker_id,
            "violation_count": len(self.violations),
            "violations": self.violations,
            "repair_suggestion_count": len(self.repair_suggestions),
            "repair_suggestions": self.repair_suggestions,
        }


# ---------------------------------------------------------------------------
# CertificateProjector
# ---------------------------------------------------------------------------


@dataclass
class CertificateProjector:
    """Projects manifest components into draft certificate structures."""

    projector_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pending_residuals: List[str] = field(default_factory=list)
    pending_obstructions: List[str] = field(default_factory=list)
    draft_cert: Optional[Dict] = field(default=None)

    # ------------------------------------------------------------------

    def project_from_judgment(self, judgment_dict: Dict) -> Dict:
        """Extract coordinate, trust level and claim from *judgment_dict*.

        Initialises ``self.draft_cert`` with these fields and returns it.
        """
        coordinate = judgment_dict.get("coordinate", "")
        trust_level = judgment_dict.get("trust_level", "none")
        claim = judgment_dict.get("claim", judgment_dict.get("assertion", ""))
        judgment_id = judgment_dict.get("judgment_id", judgment_dict.get("id", str(uuid.uuid4())))

        self.draft_cert = {
            "cert_id": str(uuid.uuid4()),
            "projector_id": self.projector_id,
            "coordinate": coordinate,
            "trust_level": trust_level,
            "trust_rank": _rank(trust_level),
            "claim": claim,
            "source_judgment_id": judgment_id,
            "residuals": [],
            "obstructions": [],
            "metadata": {
                "projected_at": _now(),
                "projection_source": "judgment",
            },
            "status": "draft",
        }
        return dict(self.draft_cert)

    def project_from_manifest(self, manifest_dict: Dict) -> Dict:
        """Project from the primary judgment in *manifest_dict*.

        Calls :meth:`project_from_judgment` with the first available judgment and
        then appends residuals and obstructions extracted from the manifest.
        """
        judgments = manifest_dict.get("judgments", [])
        if isinstance(judgments, dict):
            primary_judgment = next(iter(judgments.values()), {})
        elif isinstance(judgments, list) and judgments:
            primary_judgment = judgments[0]
        else:
            primary_judgment = {
                "coordinate": manifest_dict.get("coordinate", ""),
                "trust_level": "none",
                "claim": "",
            }

        self.project_from_judgment(primary_judgment)

        # Extract obligation IDs as residuals
        obligations = manifest_dict.get("obligations", [])
        if isinstance(obligations, dict):
            obs_list = list(obligations.keys())
        elif isinstance(obligations, list):
            obs_list = [o.get("obligation_id", o.get("id", "")) for o in obligations if isinstance(o, dict)]
        else:
            obs_list = []
        self.add_residuals([oid for oid in obs_list if oid])

        # Extract obstruction IDs
        obstructions = manifest_dict.get("obstructions", [])
        if isinstance(obstructions, dict):
            obs_ids = list(obstructions.keys())
        elif isinstance(obstructions, list):
            obs_ids = [x.get("obstruction_id", x.get("id", "")) for x in obstructions if isinstance(x, dict)]
        else:
            obs_ids = []
        self.add_obstructions([xid for xid in obs_ids if xid])

        if self.draft_cert is not None:
            self.draft_cert["metadata"]["projection_source"] = "manifest"
            self.draft_cert["metadata"]["manifest_id"] = manifest_dict.get("manifest_id", "")

        return dict(self.draft_cert) if self.draft_cert else {}

    def add_residuals(self, residual_ids: List[str]) -> None:
        """Append *residual_ids* to ``self.pending_residuals``."""
        self.pending_residuals.extend(residual_ids)

    def add_obstructions(self, obstruction_ids: List[str]) -> None:
        """Append *obstruction_ids* to ``self.pending_obstructions``."""
        self.pending_obstructions.extend(obstruction_ids)

    def finalize(self) -> Dict:
        """Assemble the final certificate from draft and pending lists.

        Assigns a stable cert_id based on content, clears pending lists,
        and returns the completed certificate dict.
        """
        if self.draft_cert is None:
            raise ValueError("No draft certificate to finalize; call project_from_judgment first.")

        # Merge pending residuals/obstructions into draft
        existing_residuals: List[str] = self.draft_cert.get("residuals", [])
        merged_residuals = list(dict.fromkeys(existing_residuals + self.pending_residuals))

        existing_obstructions: List[str] = self.draft_cert.get("obstructions", [])
        merged_obstructions = list(dict.fromkeys(existing_obstructions + self.pending_obstructions))

        self.draft_cert["residuals"] = merged_residuals
        self.draft_cert["obstructions"] = merged_obstructions
        self.draft_cert["status"] = "finalized"
        self.draft_cert["finalized_at"] = _now()

        # Derive a content-based cert_id
        content_for_hash = {
            "coordinate": self.draft_cert["coordinate"],
            "trust_level": self.draft_cert["trust_level"],
            "claim": self.draft_cert["claim"],
            "residuals": sorted(merged_residuals),
            "obstructions": sorted(merged_obstructions),
        }
        self.draft_cert["cert_id"] = hashlib.sha256(
            json.dumps(content_for_hash, sort_keys=True).encode()
        ).hexdigest()[:32]

        final = dict(self.draft_cert)

        # Clear pending lists
        self.pending_residuals = []
        self.pending_obstructions = []

        return final

    def reset(self) -> None:
        """Clear all pending state and the draft certificate."""
        self.pending_residuals = []
        self.pending_obstructions = []
        self.draft_cert = None


# ---------------------------------------------------------------------------
# ProjectionRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectionRecord:
    """Immutable record of a single projection event."""

    record_id: str
    projection_id: str
    timestamp: float
    before_trust_level: str
    after_trust_level: str
    faithfulness_passed: bool
    violations: Tuple[str, ...]

    # ------------------------------------------------------------------

    def is_downgrade(self) -> bool:
        """Return True iff the projection lowered the trust level."""
        return _rank(self.before_trust_level) > _rank(self.after_trust_level)

    def is_upgrade(self) -> bool:
        """Return True iff the projection raised the trust level."""
        return _rank(self.after_trust_level) > _rank(self.before_trust_level)

    def serialize(self) -> Dict:
        """Return all fields as a JSON-serializable dictionary."""
        return {
            "_type": "ProjectionRecord",
            "record_id": self.record_id,
            "projection_id": self.projection_id,
            "timestamp": self.timestamp,
            "before_trust_level": self.before_trust_level,
            "after_trust_level": self.after_trust_level,
            "faithfulness_passed": self.faithfulness_passed,
            "violations": list(self.violations),
            "direction": "downgrade" if self.is_downgrade() else ("upgrade" if self.is_upgrade() else "same"),
        }


# ---------------------------------------------------------------------------
# ResidualPreserver
# ---------------------------------------------------------------------------


@dataclass
class ResidualPreserver:
    """Tracks open and discharged residual obligations to prevent silent erasure."""

    preserver_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    open_residuals: Dict[str, Dict] = field(default_factory=dict)
    discharged_residuals: Dict[str, Dict] = field(default_factory=dict)

    _REQUIRED_DISCHARGE_KEYS: Tuple[str, ...] = field(
        default=("discharged_by", "justification", "timestamp"), init=False, repr=False
    )

    # ------------------------------------------------------------------

    def check_residuals(
        self, cert_dict: Dict, obligation_ids: Set[str]
    ) -> Tuple[bool, List[str]]:
        """Check that *cert_dict* lists all *obligation_ids* as residuals.

        Returns ``(ok, missing_ids)``.
        """
        cert_residuals_raw = cert_dict.get("residuals", cert_dict.get("residual_obligations", []))
        if cert_residuals_raw and isinstance(cert_residuals_raw[0], dict):
            cert_residual_ids: Set[str] = {
                r.get("obligation_id", r.get("id", "")) for r in cert_residuals_raw
            }
        else:
            cert_residual_ids = set(cert_residuals_raw)

        missing = sorted(obligation_ids - cert_residual_ids)
        return (len(missing) == 0, missing)

    def report_erased_residuals(self, before: List[str], after: List[str]) -> List[str]:
        """Return residual IDs present in *before* but absent in *after*."""
        return sorted(set(before) - set(after))

    def require_explicit_discharge(self, residual_id: str, discharge_record: Dict) -> bool:
        """Validate that *discharge_record* has all required fields.

        Required fields: ``discharged_by``, ``justification``, ``timestamp``.
        Returns ``True`` if all fields are present and non-empty.
        """
        for key in self._REQUIRED_DISCHARGE_KEYS:
            value = discharge_record.get(key)
            if value is None or value == "":
                return False
        return True

    def add_open_residual(self, residual_id: str, info: Dict) -> None:
        """Register *residual_id* as an open residual with associated *info*."""
        self.open_residuals[residual_id] = {
            **info,
            "residual_id": residual_id,
            "opened_at": _now(),
            "preserver_id": self.preserver_id,
        }

    def discharge_residual(self, residual_id: str, discharge_record: Dict) -> bool:
        """Attempt to discharge *residual_id* using *discharge_record*.

        Validates the discharge record, moves the residual from open to discharged,
        and returns ``True`` on success.
        """
        if residual_id not in self.open_residuals:
            return False

        if not self.require_explicit_discharge(residual_id, discharge_record):
            return False

        info = self.open_residuals.pop(residual_id)
        self.discharged_residuals[residual_id] = {
            **info,
            "discharge_record": discharge_record,
            "discharged_at": _now(),
        }
        return True

    def list_open(self) -> List[str]:
        """Return a sorted list of all open residual IDs."""
        return sorted(self.open_residuals.keys())

    def serialize(self) -> Dict:
        """Return the full preserver state as a JSON-serializable dictionary."""
        return {
            "_type": "ResidualPreserver",
            "preserver_id": self.preserver_id,
            "open_residual_count": len(self.open_residuals),
            "discharged_residual_count": len(self.discharged_residuals),
            "open_residuals": {k: dict(v) for k, v in self.open_residuals.items()},
            "discharged_residuals": {k: dict(v) for k, v in self.discharged_residuals.items()},
        }


# ---------------------------------------------------------------------------
# ObstructionRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObstructionRecord:
    """Immutable record of a single obstruction in the manifest."""

    obstruction_id: str
    coordinate: str
    obstruction_type: str
    description: str
    created_at: float
    is_discharged: bool = False
    repair_hints: Tuple[str, ...] = ()

    # ------------------------------------------------------------------

    def serialize(self) -> Dict:
        """Return all fields as a JSON-serializable dictionary."""
        return {
            "_type": "ObstructionRecord",
            "obstruction_id": self.obstruction_id,
            "coordinate": self.coordinate,
            "obstruction_type": self.obstruction_type,
            "description": self.description,
            "created_at": self.created_at,
            "is_discharged": self.is_discharged,
            "repair_hints": list(self.repair_hints),
            "is_dischargeable": self.is_dischargeable(),
        }

    def is_dischargeable(self) -> bool:
        """Return True iff this obstruction is not yet discharged and has repair hints."""
        return (not self.is_discharged) and (len(self.repair_hints) > 0)

    def get_repair_hints(self) -> List[str]:
        """Return the repair hints as a mutable list."""
        return list(self.repair_hints)

    def with_discharge(self) -> ObstructionRecord:
        """Return a new :class:`ObstructionRecord` with ``is_discharged=True``."""
        return dataclasses_replace(self, is_discharged=True)

    def with_hint(self, hint: str) -> ObstructionRecord:
        """Return a new :class:`ObstructionRecord` with *hint* appended to ``repair_hints``."""
        return dataclasses_replace(self, repair_hints=self.repair_hints + (hint,))


# ---------------------------------------------------------------------------
# Module-level convenience factory functions
# ---------------------------------------------------------------------------


def make_projection(manifest_dict: Dict) -> ManifestProjection:
    """Create and populate a :class:`ManifestProjection` from *manifest_dict*."""
    mp = ManifestProjection()
    mp.project(manifest_dict)
    return mp


def make_obstruction(
    coordinate: str,
    obstruction_type: str,
    description: str,
    *,
    repair_hints: Tuple[str, ...] = (),
) -> ObstructionRecord:
    """Convenience factory for :class:`ObstructionRecord`."""
    return ObstructionRecord(
        obstruction_id=str(uuid.uuid4()),
        coordinate=coordinate,
        obstruction_type=obstruction_type,
        description=description,
        created_at=_now(),
        repair_hints=repair_hints,
    )


def run_faithfulness_pipeline(
    manifest_dict: Dict,
    *,
    verbose: bool = False,
) -> Tuple[bool, FaithfulnessChecker, ManifestProjection]:
    """Run the full faithfulness pipeline for *manifest_dict*.

    1. Creates a :class:`ManifestProjection` and projects the manifest.
    2. Runs :class:`FaithfulnessChecker` over the projection.
    3. Returns ``(passed, checker, projection)``.

    This is the primary entry-point for certificate faithfulness audits.
    """
    projection = ManifestProjection()
    projection.project(manifest_dict)

    checker = FaithfulnessChecker()
    passed = checker.check(projection)

    if verbose:
        for v in checker.report_violations():
            print(f"  [VIOLATION] {v['message']}")
        if passed:
            print("  [OK] Projection is faithful.")

    return (passed, checker, projection)


def build_projection_record(
    projection: ManifestProjection,
    before_trust: str,
    after_trust: str,
    checker: FaithfulnessChecker,
) -> ProjectionRecord:
    """Build an immutable :class:`ProjectionRecord` from a completed projection run."""
    violations_tuple: Tuple[str, ...] = tuple(
        v["message"] for v in checker.report_violations()
    )
    return ProjectionRecord(
        record_id=str(uuid.uuid4()),
        projection_id=projection.projection_id,
        timestamp=_now(),
        before_trust_level=before_trust,
        after_trust_level=after_trust,
        faithfulness_passed=(len(violations_tuple) == 0),
        violations=violations_tuple,
    )
