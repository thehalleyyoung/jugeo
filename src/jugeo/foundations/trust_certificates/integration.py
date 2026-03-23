"""Integration with other JuGeo packages for trust_certificates — Theory2 Ch6.

Provides bridge classes and the main integration surface for connecting
trust_certificates with jugeo.evidence.*, jugeo.judgments.*, and
jugeo.geometry.*.

Author: copilot
Reference: theory2.tex Chapter 6.
"""
from __future__ import annotations

import json
import time
import uuid
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
from collections import defaultdict

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
    from jugeo.evidence.provenance import ProvenanceNode, ProvenanceGraph
    from jugeo.evidence.certificates import Certificate, CertificateBuilder, CertificateStatus
    from jugeo.judgments.judgment_terms import JudgmentTerm
    from jugeo.errors import JuGeoError, StructuredFailure, FailureScope, EvidenceFamily
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Trust ordering constants  (mirrored from algorithms.py for standalone use)
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
    if hasattr(level, "name"):
        return _TRUST_ORDER.get(str(level.name).upper(), 1)
    if hasattr(level, "value") and isinstance(level.value, int):
        return level.value
    return 1


def _name_at_rank(rank: int) -> str:
    """Return the canonical trust level name for an integer rank."""
    clamped = max(0, min(rank, max(_RANK_TO_NAME.keys())))
    return _RANK_TO_NAME.get(clamped, "UNVERIFIED")


# ---------------------------------------------------------------------------
# 1. IntegrationConfig
# ---------------------------------------------------------------------------

@dataclass
class IntegrationConfig:
    """Configuration object for the :class:`TrustCertificatesIntegration`.

    Controls which bridges are enabled, their endpoint URLs, and operational
    parameters such as timeout and retry limits.

    All bridge enable-flags default to conservative values: evidence and
    judgment bridges are on, geometry bridge is off.  In non-strict mode
    bridges may be enabled without a configured URL (useful for testing).
    """

    config_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    enable_evidence_bridge: bool = True
    enable_judgment_bridge: bool = True
    enable_geometry_bridge: bool = False
    strict_mode: bool = True
    evidence_system_url: str = ""
    judgment_system_url: str = ""
    geometry_system_url: str = ""
    timeout_seconds: float = 30.0
    max_retries: int = 3

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Validate the configuration, returning a list of violation strings.

        Checks:
        - ``timeout_seconds`` > 0.
        - ``max_retries`` >= 0.
        - In ``strict_mode``, each enabled bridge must have a non-empty URL.

        Returns
        -------
        List[str]
            Empty list if valid; otherwise, list of human-readable violations.
        """
        violations: List[str] = []
        if self.timeout_seconds <= 0:
            violations.append(
                f"timeout_seconds must be > 0, got {self.timeout_seconds}"
            )
        if self.max_retries < 0:
            violations.append(
                f"max_retries must be >= 0, got {self.max_retries}"
            )
        if self.strict_mode:
            if self.enable_evidence_bridge and not self.evidence_system_url:
                violations.append(
                    "evidence bridge is enabled but evidence_system_url is empty "
                    "(set strict_mode=False to skip this check)"
                )
            if self.enable_judgment_bridge and not self.judgment_system_url:
                violations.append(
                    "judgment bridge is enabled but judgment_system_url is empty "
                    "(set strict_mode=False to skip this check)"
                )
            if self.enable_geometry_bridge and not self.geometry_system_url:
                violations.append(
                    "geometry bridge is enabled but geometry_system_url is empty "
                    "(set strict_mode=False to skip this check)"
                )
        return violations

    def to_dict(self) -> Dict:
        """Return all fields as a plain dictionary.

        Returns
        -------
        Dict
            Shallow dict of all dataclass fields.
        """
        return dataclasses.asdict(self)

    def with_evidence_bridge(self, url: str) -> "IntegrationConfig":
        """Return a new config with the evidence bridge enabled and URL set.

        Parameters
        ----------
        url:
            URL of the evidence subsystem endpoint.

        Returns
        -------
        IntegrationConfig
            New instance with ``enable_evidence_bridge=True`` and
            ``evidence_system_url=url``.
        """
        return dataclasses.replace(
            self,
            enable_evidence_bridge=True,
            evidence_system_url=url,
        )

    def with_judgment_bridge(self, url: str) -> "IntegrationConfig":
        """Return a new config with the judgment bridge enabled and URL set.

        Parameters
        ----------
        url:
            URL of the judgment subsystem endpoint.

        Returns
        -------
        IntegrationConfig
            New instance with ``enable_judgment_bridge=True`` and
            ``judgment_system_url=url``.
        """
        return dataclasses.replace(
            self,
            enable_judgment_bridge=True,
            judgment_system_url=url,
        )

    def is_any_bridge_enabled(self) -> bool:
        """Return ``True`` if at least one bridge is enabled.

        Returns
        -------
        bool
        """
        return (
            self.enable_evidence_bridge
            or self.enable_judgment_bridge
            or self.enable_geometry_bridge
        )


# ---------------------------------------------------------------------------
# 2. IntegrationReport
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntegrationReport:
    """Immutable health-check report for the integration layer.

    Produced by :meth:`TrustCertificatesIntegration.validate_integration` and
    summarises the per-bridge health status together with any failures or
    warnings encountered during validation.
    """

    report_id: str
    checked_at: float
    evidence_bridge_ok: bool
    judgment_bridge_ok: bool
    geometry_bridge_ok: bool
    failures: Tuple[str, ...]
    warnings: Tuple[str, ...]

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Return ``True`` if all bridges are healthy and no failures exist.

        Returns
        -------
        bool
            ``True`` when ``failures`` is empty and all bridge status flags
            are ``True``.
        """
        return (
            not self.failures
            and self.evidence_bridge_ok
            and self.judgment_bridge_ok
            and self.geometry_bridge_ok
        )

    def list_failures(self) -> List[str]:
        """Return the failures tuple as a plain list.

        Returns
        -------
        List[str]
        """
        return list(self.failures)

    def serialize(self) -> Dict:
        """Serialise the report to a plain dictionary.

        Returns
        -------
        Dict
            All report fields as a dict, with tuples converted to lists.
        """
        return {
            "report_id": self.report_id,
            "checked_at": self.checked_at,
            "evidence_bridge_ok": self.evidence_bridge_ok,
            "judgment_bridge_ok": self.judgment_bridge_ok,
            "geometry_bridge_ok": self.geometry_bridge_ok,
            "failures": list(self.failures),
            "warnings": list(self.warnings),
        }

    def summary(self) -> str:
        """Return a human-readable multi-line summary of the report.

        Returns
        -------
        str
            Multi-line string with bridge statuses, failures, and warnings.
        """
        lines: List[str] = [
            f"IntegrationReport [{self.report_id}]",
            f"  checked_at       : {self.checked_at:.3f}",
            f"  evidence_bridge  : {'OK' if self.evidence_bridge_ok else 'FAIL'}",
            f"  judgment_bridge  : {'OK' if self.judgment_bridge_ok else 'FAIL'}",
            f"  geometry_bridge  : {'OK' if self.geometry_bridge_ok else 'FAIL'}",
            f"  overall_healthy  : {self.is_healthy()}",
        ]
        if self.failures:
            lines.append("  failures:")
            for f in self.failures:
                lines.append(f"    - {f}")
        if self.warnings:
            lines.append("  warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. EvidenceBridge
# ---------------------------------------------------------------------------

@dataclass
class EvidenceBridge:
    """Bridge between the trust_certificates layer and jugeo.evidence.*.

    Handles import of trust levels, provenance nodes, and certificates from
    the evidence subsystem, as well as export of newly issued certificates
    back to the evidence archive.
    """

    bridge_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    imported_trust_levels: List[Dict] = field(default_factory=list)
    imported_provenance_nodes: List[Dict] = field(default_factory=list)
    imported_certificates: List[Dict] = field(default_factory=list)
    export_log: List[Dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Import helpers
    # ------------------------------------------------------------------

    def import_trust_level(self, raw: Any) -> Dict:
        """Convert a raw trust level representation to a canonical dict.

        Accepts strings, enum-like objects (with ``.name``), or dicts with
        a ``'name'`` key.  The returned dict always contains ``'name'`` and
        ``'rank'``.  Unknown level names default to rank 1 (``UNVERIFIED``).

        Parameters
        ----------
        raw:
            Raw trust level value in any supported format.

        Returns
        -------
        Dict
            ``{'name': str, 'rank': int}``

        Raises
        ------
        ValueError
            If the input cannot be interpreted as a trust level.
        """
        if isinstance(raw, str):
            name = raw.upper()
        elif isinstance(raw, dict):
            name = str(raw.get("name", raw.get("trust_level", "UNVERIFIED"))).upper()
        elif hasattr(raw, "name"):
            name = str(raw.name).upper()
        elif hasattr(raw, "value") and isinstance(raw.value, int):
            name = _name_at_rank(raw.value)
        else:
            raise ValueError(f"Cannot interpret trust level from {type(raw).__name__!r}")

        rank = _TRUST_ORDER.get(name, 1)
        canonical = {"name": name, "rank": rank}
        self.imported_trust_levels.append(canonical)
        return canonical

    def import_provenance_node(self, raw: Any) -> Dict:
        """Convert a raw provenance node to a canonical dict.

        Accepts ``ProvenanceNode`` instances (or duck-typed equivalents) or
        plain dicts.  Populates missing fields with sensible defaults.

        Parameters
        ----------
        raw:
            Raw provenance node (ProvenanceNode, dict, or compatible object).

        Returns
        -------
        Dict
            Canonical provenance node dict with keys:
            ``node_id``, ``source_channel``, ``operation``, ``inputs``,
            ``timestamp``, ``coordinate``, ``trust_at_creation``.

        Raises
        ------
        ValueError
            If the input is not a recognised provenance node format.
        """
        if isinstance(raw, dict):
            src = raw
        elif hasattr(raw, "__dict__"):
            src = raw.__dict__
        elif hasattr(raw, "_asdict"):
            src = raw._asdict()
        else:
            raise ValueError(
                f"Cannot interpret provenance node from {type(raw).__name__!r}"
            )

        node = {
            "node_id": str(
                src.get("node_id") or src.get("id") or str(uuid.uuid4())
            ),
            "source_channel": str(
                src.get("source_channel") or src.get("channel") or "unknown"
            ),
            "operation": str(src.get("operation") or "unknown"),
            "inputs": list(src.get("inputs") or []),
            "timestamp": float(src.get("timestamp") or time.time()),
            "coordinate": str(src.get("coordinate") or ""),
            "trust_at_creation": str(
                src.get("trust_at_creation") or src.get("trust_level") or "UNVERIFIED"
            ).upper(),
        }
        self.imported_provenance_nodes.append(node)
        return node

    def import_certificate(self, raw: Any) -> Dict:
        """Convert a raw Certificate or dict to a canonical certificate dict.

        The canonical form requires: ``cert_id``, ``coordinate``,
        ``trust_level``, ``claim``, ``issued_at``.  Missing fields are
        populated with defaults; extraneous fields are preserved.

        Parameters
        ----------
        raw:
            Raw certificate (Certificate instance or dict).

        Returns
        -------
        Dict
            Canonical certificate dict.

        Raises
        ------
        ValueError
            If required fields cannot be inferred from the input.
        """
        if isinstance(raw, dict):
            src = raw
        elif hasattr(raw, "__dict__"):
            src = raw.__dict__
        elif hasattr(raw, "_asdict"):
            src = raw._asdict()
        else:
            raise ValueError(
                f"Cannot interpret certificate from {type(raw).__name__!r}"
            )

        cert_id = str(src.get("cert_id") or src.get("id") or str(uuid.uuid4()))
        coordinate = str(src.get("coordinate") or src.get("coord") or "")
        trust_level = str(
            src.get("trust_level") or src.get("level") or "UNVERIFIED"
        ).upper()
        claim = str(src.get("claim") or src.get("statement") or "")
        issued_at = float(src.get("issued_at") or src.get("timestamp") or time.time())

        if not coordinate:
            raise ValueError("Certificate is missing a 'coordinate' field")
        if not claim:
            raise ValueError("Certificate is missing a 'claim' field")

        canonical = {
            "cert_id": cert_id,
            "coordinate": coordinate,
            "trust_level": trust_level,
            "claim": claim,
            "issued_at": issued_at,
            "residuals": list(src.get("residuals") or []),
            "obstructions": list(src.get("obstructions") or []),
            "provenance_ids": list(src.get("provenance_ids") or []),
            "status": str(src.get("status") or "ISSUED"),
        }
        self.imported_certificates.append(canonical)
        return canonical

    def export_certificate(self, cert_dict: Dict) -> Dict:
        """Validate and enrich a certificate dict for export.

        Checks that the required keys are present, stamps the export with
        the current timestamp and bridge ID, and records the export in
        :attr:`export_log`.

        Parameters
        ----------
        cert_dict:
            Certificate dict to export (must contain ``cert_id``,
            ``coordinate``, ``trust_level``, ``claim``).

        Returns
        -------
        Dict
            Copy of ``cert_dict`` with ``exported_at`` and ``bridge_id``
            metadata added.

        Raises
        ------
        ValueError
            If required keys are missing.
        """
        required = ("cert_id", "coordinate", "trust_level", "claim")
        missing = [k for k in required if k not in cert_dict]
        if missing:
            raise ValueError(
                f"Certificate is missing required export keys: {missing}"
            )

        enriched = dict(cert_dict)
        enriched["exported_at"] = time.time()
        enriched["exporting_bridge_id"] = self.bridge_id

        log_entry = {
            "cert_id": cert_dict["cert_id"],
            "coordinate": cert_dict["coordinate"],
            "trust_level": cert_dict["trust_level"],
            "exported_at": enriched["exported_at"],
        }
        self.export_log.append(log_entry)
        return enriched

    def sync_evidence_archive(self, archive_items: List[Any]) -> Tuple[int, int]:
        """Bulk-import a list of heterogeneous archive items.

        Each item is tried first as a certificate, then as a provenance node,
        then as a trust level.  Items that cannot be interpreted in any form
        are counted as failures.

        Parameters
        ----------
        archive_items:
            List of raw evidence archive items.

        Returns
        -------
        Tuple[int, int]
            ``(success_count, failure_count)``
        """
        success_count = 0
        failure_count = 0
        for item in archive_items:
            imported = False
            # Try certificate first (most specific)
            try:
                self.import_certificate(item)
                imported = True
            except Exception:
                pass
            # Try provenance node
            if not imported:
                try:
                    self.import_provenance_node(item)
                    imported = True
                except Exception:
                    pass
            # Try trust level
            if not imported:
                try:
                    self.import_trust_level(item)
                    imported = True
                except Exception:
                    pass
            if imported:
                success_count += 1
            else:
                failure_count += 1
        return (success_count, failure_count)

    def get_import_stats(self) -> Dict:
        """Return summary counts of imported objects.

        Returns
        -------
        Dict
            ``{'imported_trust_levels': int, 'imported_provenance_nodes': int,
            'imported_certificates': int}``
        """
        return {
            "imported_trust_levels": len(self.imported_trust_levels),
            "imported_provenance_nodes": len(self.imported_provenance_nodes),
            "imported_certificates": len(self.imported_certificates),
        }


# ---------------------------------------------------------------------------
# 4. JudgmentBridge
# ---------------------------------------------------------------------------

_JUDGMENT_CANONICAL_KEYS: Tuple[str, ...] = (
    "coordinate",
    "claim",
    "admissibility_condition",
    "evidence_list",
    "obligation_ids",
    "bounding_domain",
    "trust_tier",
    "provenance_ids",
)


@dataclass
class JudgmentBridge:
    """Bridge between the trust_certificates layer and jugeo.judgments.*.

    Converts JudgmentTerm objects or plain dicts to canonical form,
    maps issued certificates to judgments, and validates that trust
    levels satisfy judgment requirements.
    """

    bridge_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    judgment_cache: Dict[str, Dict] = field(default_factory=dict)
    certificate_map: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def import_judgment_term(self, raw: Any) -> Dict:
        """Convert a JudgmentTerm or dict to a canonical judgment dict.

        The canonical form has the following keys:
        - ``coordinate`` — geometric coordinate
        - ``claim`` — the claim being judged
        - ``admissibility_condition`` — condition for admissibility
        - ``evidence_list`` — list of evidence references
        - ``obligation_ids`` — list of obligation IDs
        - ``bounding_domain`` — bounding domain string
        - ``trust_tier`` — required minimum trust level
        - ``provenance_ids`` — list of provenance node IDs

        The imported judgment is cached by its ``judgment_id``.

        Parameters
        ----------
        raw:
            JudgmentTerm instance or dict.

        Returns
        -------
        Dict
            Canonical judgment dict (includes ``judgment_id``).

        Raises
        ------
        ValueError
            If the input is not a recognised judgment format.
        """
        if isinstance(raw, dict):
            src = raw
        elif hasattr(raw, "__dict__"):
            src = raw.__dict__
        elif hasattr(raw, "_asdict"):
            src = raw._asdict()
        else:
            raise ValueError(
                f"Cannot interpret judgment from {type(raw).__name__!r}"
            )

        judgment_id = str(
            src.get("judgment_id") or src.get("id") or str(uuid.uuid4())
        )
        canonical: Dict = {"judgment_id": judgment_id}
        canonical["coordinate"] = str(src.get("coordinate") or "")
        canonical["claim"] = str(src.get("claim") or src.get("statement") or "")
        canonical["admissibility_condition"] = str(
            src.get("admissibility_condition") or src.get("condition") or ""
        )
        canonical["evidence_list"] = list(
            src.get("evidence_list") or src.get("evidence") or []
        )
        canonical["obligation_ids"] = list(
            src.get("obligation_ids") or src.get("obligations") or []
        )
        canonical["bounding_domain"] = str(
            src.get("bounding_domain") or src.get("domain") or ""
        )
        canonical["trust_tier"] = str(
            src.get("trust_tier") or src.get("trust_level") or "UNVERIFIED"
        ).upper()
        canonical["provenance_ids"] = list(
            src.get("provenance_ids") or src.get("provenance") or []
        )

        self.judgment_cache[judgment_id] = canonical
        return canonical

    def export_certificate_for_judgment(
        self, judgment_id: str, cert_dict: Dict
    ) -> Dict:
        """Associate a certificate with a judgment and enrich it.

        The ``judgment_id`` is embedded into ``cert_dict`` under the key
        ``'judgment_id'``, and the association is recorded in
        :attr:`certificate_map`.

        Parameters
        ----------
        judgment_id:
            ID of the judgment the certificate was issued for.  Must exist
            in :attr:`judgment_cache`.
        cert_dict:
            The certificate dictionary to enrich.

        Returns
        -------
        Dict
            Enriched certificate dict with ``'judgment_id'`` added.

        Raises
        ------
        KeyError
            If ``judgment_id`` is not found in the cache.
        """
        if judgment_id not in self.judgment_cache:
            raise KeyError(
                f"judgment_id '{judgment_id}' not found in judgment cache; "
                "call import_judgment_term first"
            )
        enriched = dict(cert_dict)
        enriched["judgment_id"] = judgment_id
        cert_id = cert_dict.get("cert_id", str(uuid.uuid4()))
        enriched["cert_id"] = cert_id
        self.certificate_map[judgment_id] = cert_id
        return enriched

    def validate_judgment_trust(
        self, judgment_id: str, cert_dict: Dict
    ) -> Tuple[bool, List[str]]:
        """Validate that a certificate satisfies its associated judgment.

        Checks:
        1. The certificate's trust level rank >= the judgment's ``trust_tier``
           rank.
        2. All judgment ``obligation_ids`` appear in the certificate's
           ``residuals`` list.

        Parameters
        ----------
        judgment_id:
            ID of the judgment to validate against.
        cert_dict:
            Certificate dict to check.

        Returns
        -------
        Tuple[bool, List[str]]
            ``(True, [])`` if valid; ``(False, [violation, ...])`` otherwise.

        Raises
        ------
        KeyError
            If ``judgment_id`` is not in the cache.
        """
        if judgment_id not in self.judgment_cache:
            raise KeyError(
                f"judgment_id '{judgment_id}' not found in judgment cache"
            )
        judgment = self.judgment_cache[judgment_id]
        violations: List[str] = []

        cert_rank = _rank(cert_dict.get("trust_level", "UNVERIFIED"))
        required_rank = _rank(judgment.get("trust_tier", "UNVERIFIED"))
        if cert_rank < required_rank:
            violations.append(
                f"Certificate trust level '{cert_dict.get('trust_level')}' "
                f"(rank {cert_rank}) is below required trust tier "
                f"'{judgment.get('trust_tier')}' (rank {required_rank})"
            )

        cert_residuals: Set[str] = set(cert_dict.get("residuals", []))
        for obligation_id in judgment.get("obligation_ids", []):
            if obligation_id not in cert_residuals:
                violations.append(
                    f"Judgment obligation '{obligation_id}' not discharged in "
                    f"certificate residuals"
                )

        return (len(violations) == 0, violations)

    def get_unmapped_judgments(self) -> List[str]:
        """Return judgment IDs that have no associated certificate.

        Returns
        -------
        List[str]
            Sorted list of uncertified judgment IDs.
        """
        return sorted(
            jid for jid in self.judgment_cache if jid not in self.certificate_map
        )

    def clear_cache(self) -> None:
        """Clear judgment cache and certificate map.

        Resets the bridge to its initial (empty) state without affecting the
        ``bridge_id``.
        """
        self.judgment_cache.clear()
        self.certificate_map.clear()


# ---------------------------------------------------------------------------
# 5. GeometryBridge
# ---------------------------------------------------------------------------

@dataclass
class GeometryBridge:
    """Bridge between trust_certificates and jugeo.geometry.*.

    Handles restriction of certificates to covering patches, transport of
    certificates across coordinate maps, and compatibility checks against
    descent conditions.
    """

    bridge_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cover_map: Dict[str, List[str]] = field(default_factory=dict)
    transport_log: List[Dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Certificate restriction and transport
    # ------------------------------------------------------------------

    def restrict_certificate_to_cover(
        self, cert_dict: Dict, cover_coordinate: str
    ) -> Dict:
        """Restrict a certificate to a covering patch coordinate.

        The restricted certificate covers ``cover_coordinate`` instead of
        the original coordinate.  Trust is attenuated by one rank to reflect
        the additional restriction step.

        Parameters
        ----------
        cert_dict:
            Source certificate dict.
        cover_coordinate:
            The coordinate of the covering patch.

        Returns
        -------
        Dict
            New certificate dict restricted to ``cover_coordinate``.
        """
        original_rank = _rank(cert_dict.get("trust_level", "UNVERIFIED"))
        attenuated_rank = max(0, original_rank - 1)
        attenuated_level = _name_at_rank(attenuated_rank)

        restricted = dict(cert_dict)
        restricted["cert_id"] = str(uuid.uuid4())
        restricted["coordinate"] = cover_coordinate
        restricted["trust_level"] = attenuated_level
        restricted["restricted_from"] = cert_dict.get("coordinate", "")
        restricted["restricted_from_cert_id"] = cert_dict.get("cert_id", "")
        restricted["restriction_bridge_id"] = self.bridge_id
        restricted["restricted_at"] = time.time()
        return restricted

    def transport_certificate(
        self,
        cert_dict: Dict,
        source_coord: str,
        target_coord: str,
        transport_map: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """Transport a certificate from ``source_coord`` to ``target_coord``.

        If ``transport_map`` is provided, it is used to validate that the
        transport is well-defined (a key ``(source_coord, target_coord)`` or
        ``source_coord`` must be present).  Trust is attenuated by one rank
        during transport.

        Parameters
        ----------
        cert_dict:
            Certificate to transport.
        source_coord:
            Source coordinate (should match ``cert_dict['coordinate']``).
        target_coord:
            Target coordinate.
        transport_map:
            Optional dict describing valid transports.  If provided, checked
            for key ``source_coord`` or tuple key ``(source_coord, target_coord)``.

        Returns
        -------
        Dict
            Transported certificate dict.

        Raises
        ------
        ValueError
            If ``transport_map`` is provided but the transport is not defined
            within it.
        """
        if transport_map is not None:
            key_pair = f"{source_coord}->{target_coord}"
            if (
                source_coord not in transport_map
                and key_pair not in transport_map
            ):
                raise ValueError(
                    f"Transport not defined for '{source_coord}' -> '{target_coord}' "
                    f"in the provided transport_map"
                )

        original_rank = _rank(cert_dict.get("trust_level", "UNVERIFIED"))
        attenuated_rank = max(0, original_rank - 1)
        attenuated_level = _name_at_rank(attenuated_rank)

        transported = dict(cert_dict)
        transported["cert_id"] = str(uuid.uuid4())
        transported["coordinate"] = target_coord
        transported["trust_level"] = attenuated_level
        transported["transported_from"] = source_coord
        transported["transported_from_cert_id"] = cert_dict.get("cert_id", "")
        transported["transport_bridge_id"] = self.bridge_id
        transported["transported_at"] = time.time()

        self.transport_log.append({
            "source_coord": source_coord,
            "target_coord": target_coord,
            "original_trust": cert_dict.get("trust_level", "UNVERIFIED"),
            "attenuated_trust": attenuated_level,
            "transported_at": transported["transported_at"],
        })
        return transported

    def check_descent_compatibility(
        self, cert_dict: Dict, covering_certs: List[Dict]
    ) -> Tuple[bool, List[str]]:
        """Check that a certificate is compatible with its covering certificates.

        For each covering certificate, the base certificate's trust level rank
        must not exceed the covering certificate's trust level rank (descent
        conditions require the cover to be at least as trusted as the base).

        Parameters
        ----------
        cert_dict:
            Base certificate whose compatibility is checked.
        covering_certs:
            List of covering certificate dicts.

        Returns
        -------
        Tuple[bool, List[str]]
            ``(True, [])`` if compatible; ``(False, [violation, ...])``
            otherwise.
        """
        base_rank = _rank(cert_dict.get("trust_level", "UNVERIFIED"))
        violations: List[str] = []
        for cover in covering_certs:
            cover_rank = _rank(cover.get("trust_level", "UNVERIFIED"))
            cover_coord = cover.get("coordinate", "unknown")
            if base_rank > cover_rank:
                violations.append(
                    f"Base certificate trust '{cert_dict.get('trust_level')}' "
                    f"(rank {base_rank}) exceeds covering certificate at "
                    f"'{cover_coord}' with trust "
                    f"'{cover.get('trust_level')}' (rank {cover_rank}): "
                    f"descent condition violated"
                )
        return (len(violations) == 0, violations)

    def register_cover(
        self, coordinate: str, cover_coordinates: List[str]
    ) -> None:
        """Register a cover for a coordinate.

        Parameters
        ----------
        coordinate:
            The coordinate being covered.
        cover_coordinates:
            List of covering patch coordinates.
        """
        self.cover_map[coordinate] = list(cover_coordinates)

    def get_cover(self, coordinate: str) -> List[str]:
        """Return the registered covering coordinates for ``coordinate``.

        Parameters
        ----------
        coordinate:
            Coordinate to look up.

        Returns
        -------
        List[str]
            List of covering coordinates, or ``[]`` if none registered.
        """
        return list(self.cover_map.get(coordinate, []))


# ---------------------------------------------------------------------------
# 6. TrustCertificatesIntegration
# ---------------------------------------------------------------------------

@dataclass
class TrustCertificatesIntegration:
    """Main integration facade for the trust_certificates subsystem.

    Combines the three bridges (evidence, judgment, geometry) under a single
    entry point.  Use :meth:`validate_integration` to perform a health check
    and obtain an :class:`IntegrationReport`.
    """

    integration_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: IntegrationConfig = field(default_factory=IntegrationConfig)
    evidence_bridge: EvidenceBridge = field(default_factory=EvidenceBridge)
    judgment_bridge: JudgmentBridge = field(default_factory=JudgmentBridge)
    geometry_bridge: GeometryBridge = field(default_factory=GeometryBridge)
    last_report: Optional[IntegrationReport] = field(default=None)

    # Internal metadata
    _channel_names: List[str] = field(default_factory=list)
    _integration_metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Bridge integration methods
    # ------------------------------------------------------------------

    def integrate_with_evidence_system(self, evidence_data: Dict) -> bool:
        """Import evidence data from the evidence subsystem.

        If the evidence bridge is disabled in :attr:`config`, this method
        returns ``True`` immediately without performing any work.

        Expects ``evidence_data`` to contain any combination of the keys:
        - ``'trust_levels'``: list of raw trust level objects
        - ``'provenance_nodes'``: list of raw provenance nodes
        - ``'certificates'``: list of raw certificate objects
        - ``'archive'``: flat list of heterogeneous archive items

        Parameters
        ----------
        evidence_data:
            Dict of evidence objects to import.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on error.
        """
        if not self.config.enable_evidence_bridge:
            return True
        try:
            for raw in evidence_data.get("trust_levels", []):
                self.evidence_bridge.import_trust_level(raw)
            for raw in evidence_data.get("provenance_nodes", []):
                self.evidence_bridge.import_provenance_node(raw)
            for raw in evidence_data.get("certificates", []):
                self.evidence_bridge.import_certificate(raw)
            archive = evidence_data.get("archive", [])
            if archive:
                self.evidence_bridge.sync_evidence_archive(archive)
            return True
        except Exception:
            return False

    def integrate_with_judgment_system(self, judgment_data: Dict) -> bool:
        """Import judgment data and map certificates to judgments.

        If the judgment bridge is disabled in :attr:`config`, returns ``True``
        immediately.

        Expects ``judgment_data`` to contain:
        - ``'judgments'``: list of raw judgment objects to import.
        - ``'certificate_assignments'``: list of dicts with
          ``{'judgment_id': str, 'cert_dict': Dict}`` entries (optional).

        Parameters
        ----------
        judgment_data:
            Dict of judgment objects to import and process.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on error.
        """
        if not self.config.enable_judgment_bridge:
            return True
        try:
            for raw in judgment_data.get("judgments", []):
                self.judgment_bridge.import_judgment_term(raw)
            for assignment in judgment_data.get("certificate_assignments", []):
                jid = assignment.get("judgment_id")
                cert = assignment.get("cert_dict")
                if jid and cert:
                    self.judgment_bridge.export_certificate_for_judgment(jid, cert)
            return True
        except Exception:
            return False

    def integrate_with_geometry(self, geometry_data: Dict) -> bool:
        """Register geometry cover maps from geometry subsystem data.

        If the geometry bridge is disabled in :attr:`config`, returns ``True``
        immediately.

        Expects ``geometry_data`` to contain:
        - ``'cover_maps'``: list of dicts with ``{'coordinate': str,
          'cover_coordinates': List[str]}`` entries.

        Parameters
        ----------
        geometry_data:
            Dict describing geometry cover relationships.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on error.
        """
        if not self.config.enable_geometry_bridge:
            return True
        try:
            for cover_entry in geometry_data.get("cover_maps", []):
                coord = cover_entry.get("coordinate", "")
                covers = list(cover_entry.get("cover_coordinates", []))
                if coord:
                    self.geometry_bridge.register_cover(coord, covers)
            return True
        except Exception:
            return False

    def register_channels(self, channel_names: List[str]) -> None:
        """Record a list of evidence channel names in integration metadata.

        Parameters
        ----------
        channel_names:
            List of channel name strings to register.
        """
        for name in channel_names:
            if name not in self._channel_names:
                self._channel_names.append(name)
        self._integration_metadata["registered_channels"] = list(self._channel_names)

    # ------------------------------------------------------------------
    # Validation and status
    # ------------------------------------------------------------------

    def validate_integration(self) -> IntegrationReport:
        """Run all bridge health checks and produce an IntegrationReport.

        Health checks:
        - Config validation (violations → failures).
        - Evidence bridge: at least one imported item if enabled.
        - Judgment bridge: cache non-empty if enabled.
        - Geometry bridge: cover_map non-empty if enabled.
        - Warnings for bridges enabled but with no data.

        Returns
        -------
        IntegrationReport
            Stored in :attr:`last_report` and returned.
        """
        failures: List[str] = []
        warnings: List[str] = []

        # Validate config
        config_violations = self.config.validate()
        for v in config_violations:
            failures.append(f"[Config] {v}")

        # Evidence bridge health
        evidence_ok = True
        if self.config.enable_evidence_bridge:
            stats = self.evidence_bridge.get_import_stats()
            total_imported = sum(stats.values())
            if total_imported == 0:
                warnings.append(
                    "[EvidenceBridge] enabled but no evidence has been imported"
                )
            if not self.config.evidence_system_url and self.config.strict_mode:
                failures.append(
                    "[EvidenceBridge] enabled with strict_mode but no URL configured"
                )
                evidence_ok = False
        else:
            # Not enabled is considered OK for health purposes
            evidence_ok = True

        # Judgment bridge health
        judgment_ok = True
        if self.config.enable_judgment_bridge:
            if not self.judgment_bridge.judgment_cache:
                warnings.append(
                    "[JudgmentBridge] enabled but judgment cache is empty"
                )
            if not self.config.judgment_system_url and self.config.strict_mode:
                failures.append(
                    "[JudgmentBridge] enabled with strict_mode but no URL configured"
                )
                judgment_ok = False
        else:
            judgment_ok = True

        # Geometry bridge health
        geometry_ok = True
        if self.config.enable_geometry_bridge:
            if not self.geometry_bridge.cover_map:
                warnings.append(
                    "[GeometryBridge] enabled but no cover maps registered"
                )
            if not self.config.geometry_system_url and self.config.strict_mode:
                failures.append(
                    "[GeometryBridge] enabled with strict_mode but no URL configured"
                )
                geometry_ok = False
        else:
            geometry_ok = True

        report = IntegrationReport(
            report_id=str(uuid.uuid4()),
            checked_at=time.time(),
            evidence_bridge_ok=evidence_ok,
            judgment_bridge_ok=judgment_ok,
            geometry_bridge_ok=geometry_ok,
            failures=tuple(failures),
            warnings=tuple(warnings),
        )
        self.last_report = report
        return report

    def get_status(self) -> Dict:
        """Return a summary status dict for the integration layer.

        Returns
        -------
        Dict
            Dict with ``integration_id``, ``config_summary``,
            ``bridge_statuses``, and ``last_report_summary``.
        """
        evidence_stats = self.evidence_bridge.get_import_stats()
        judgment_stats = {
            "cached_judgments": len(self.judgment_bridge.judgment_cache),
            "mapped_certificates": len(self.judgment_bridge.certificate_map),
            "unmapped_judgments": len(self.judgment_bridge.get_unmapped_judgments()),
        }
        geometry_stats = {
            "registered_covers": len(self.geometry_bridge.cover_map),
            "transport_log_entries": len(self.geometry_bridge.transport_log),
        }

        last_report_summary: Optional[str] = None
        if self.last_report is not None:
            last_report_summary = self.last_report.summary()

        return {
            "integration_id": self.integration_id,
            "config_summary": {
                "config_id": self.config.config_id,
                "evidence_bridge_enabled": self.config.enable_evidence_bridge,
                "judgment_bridge_enabled": self.config.enable_judgment_bridge,
                "geometry_bridge_enabled": self.config.enable_geometry_bridge,
                "strict_mode": self.config.strict_mode,
                "timeout_seconds": self.config.timeout_seconds,
                "max_retries": self.config.max_retries,
            },
            "bridge_statuses": {
                "evidence": evidence_stats,
                "judgment": judgment_stats,
                "geometry": geometry_stats,
            },
            "registered_channels": list(self._channel_names),
            "last_report_summary": last_report_summary,
        }
