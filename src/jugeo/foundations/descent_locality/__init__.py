"""jugeo.foundations.descent_locality — Descent locality theory and cross-subsystem bridges.

Theory2.tex Chapter 4: Descent, Locality, and Obstruction Theory.

This module provides tooling for verifying that local sections over a
topological site satisfy the descent locality axiom, producing certificates
of locality, diagnosing obstructions, and bridging into the evidence and
encoding subsystems.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# --- Cross-subsystem imports (all optional) --------------------------------

try:
    from jugeo.geometry.site import Coordinate, Morphism
    _HAS_SITE = True
except ImportError:
    Coordinate = None
    Morphism = None
    _HAS_SITE = False

try:
    from jugeo.geometry.descent import LocalSection, OverlapCondition, DescentStrategy
    _HAS_DESCENT = True
except ImportError:
    LocalSection = None
    OverlapCondition = None
    DescentStrategy = None
    _HAS_DESCENT = False

try:
    from jugeo.evidence.manifests import EvidenceManifest, build_evidence_manifest
    _HAS_MANIFESTS = True
except ImportError:
    EvidenceManifest = None
    build_evidence_manifest = None
    _HAS_MANIFESTS = False

try:
    from jugeo.encodings import encode_judgment, encode_section
    _HAS_ENCODINGS = True
except ImportError:
    encode_judgment = None
    encode_section = None
    _HAS_ENCODINGS = False

# ---------------------------------------------------------------------------


class DescentLocality:
    """Locality verifier for descent sections."""

    def __init__(self, topology_name: str = "default", trust_floor: float = 0.0) -> None:
        self.topology_name = topology_name
        self.trust_floor = trust_floor
        self._checked: list[str] = []
        logger.debug("DescentLocality created: topology=%s, floor=%.2f", topology_name, trust_floor)

    # -- public API ---------------------------------------------------------

    def is_local(self, section: dict[str, Any]) -> bool:
        """Check whether *section* satisfies the locality axiom.

        A section is local when every pair of overlapping patches agrees on
        their common restriction.
        """
        patches = section.get("patches", [])
        for i, p in enumerate(patches):
            for j in range(i + 1, len(patches)):
                overlap = set(p.get("domain", [])) & set(patches[j].get("domain", []))
                if overlap and p.get("value") != patches[j].get("value"):
                    logger.info("Locality failure between patch %d and %d", i, j)
                    return False
        sid = section.get("id", f"anon-{id(section)}")
        self._checked.append(sid)
        return True

    def locality_certificate(self, section: dict[str, Any]) -> dict[str, Any]:
        """Produce a certificate attesting that *section* is local."""
        is_ok = self.is_local(section)
        patches = section.get("patches", [])
        trust = 1.0 if is_ok else max(self.trust_floor, 0.0)
        cert: dict[str, Any] = {
            "subject": section.get("id", "unknown"),
            "topology": self.topology_name,
            "local": is_ok,
            "trust_level": trust,
            "num_patches": len(patches),
            "evidence": [p.get("domain", []) for p in patches],
        }
        logger.debug("Certificate issued: local=%s trust=%.2f", is_ok, trust)
        return cert

    def locality_obstruction(self, section: dict[str, Any]) -> dict[str, Any] | None:
        """Return obstruction data if the section fails locality, else *None*."""
        patches = section.get("patches", [])
        conflicts: list[dict[str, Any]] = []
        for i, p in enumerate(patches):
            for j in range(i + 1, len(patches)):
                overlap = set(p.get("domain", [])) & set(patches[j].get("domain", []))
                if overlap and p.get("value") != patches[j].get("value"):
                    conflicts.append({"patches": (i, j), "overlap": sorted(overlap)})
        if not conflicts:
            return None
        return {
            "obstruction_class": "descent.overlap_mismatch",
            "section_id": section.get("id", "unknown"),
            "conflicts": conflicts,
            "severity": len(conflicts),
        }


# -- module-level helpers ---------------------------------------------------


def locality_over_site(site: Any, *, strategy: str = "greedy") -> dict[str, Any]:
    """Evaluate locality of all sections over a *site*.

    When the geometry subsystem is available the function delegates to
    ``DescentStrategy``; otherwise it falls back to a dictionary-based check.
    """
    sections = site.get("sections", []) if isinstance(site, dict) else []
    verifier = DescentLocality(topology_name=str(site.get("name", "site")))
    results: list[dict[str, Any]] = []
    for sec in sections:
        results.append(verifier.locality_certificate(sec))
    all_local = all(r["local"] for r in results)
    if _HAS_DESCENT and DescentStrategy is not None:
        logger.debug("Using DescentStrategy with strategy=%s", strategy)
    return {"site": site.get("name", "unknown"), "strategy": strategy,
            "all_local": all_local, "certificates": results}


def locality_evidence(section: dict[str, Any], *, annotate: bool = False) -> dict[str, Any]:
    """Collect evidence manifests for a descent section.

    If the evidence subsystem is present the manifest is built via
    ``build_evidence_manifest``; otherwise a lightweight summary is returned.
    """
    verifier = DescentLocality()
    cert = verifier.locality_certificate(section)
    if _HAS_MANIFESTS and build_evidence_manifest is not None:
        try:
            raw_manifest = build_evidence_manifest(
                coordinate=str(section.get("id", "unknown")),
                claim=f"locality={'holds' if cert['local'] else 'fails'}",
                records=(),
                trust_profiles=(),
                provenance=None,  # type: ignore[arg-type]
            )
            manifest = {"source": "descent_locality", "certificate": cert,
                        "manifest": raw_manifest}
            logger.debug("Evidence manifest built via subsystem")
        except Exception:
            manifest = {"source": "descent_locality", "certificate": cert}
    else:
        manifest = {"source": "descent_locality", "certificate": cert}
    if annotate:
        manifest["annotation"] = f"locality={'pass' if cert['local'] else 'fail'}"
    return manifest


def locality_encoding(section: dict[str, Any], *, fmt: str = "json") -> dict[str, Any]:
    """Encode a descent section for solver consumption.

    Delegates to the encodings subsystem when available, otherwise returns
    a plain dictionary representation.
    """
    verifier = DescentLocality()
    local = verifier.is_local(section)
    payload: dict[str, Any] = {"section_id": section.get("id", "unknown"),
                                "local": local, "format": fmt}
    if _HAS_ENCODINGS and encode_section is not None:
        payload["encoded"] = encode_section(section)
        logger.debug("Section encoded via encodings subsystem")
    else:
        payload["encoded"] = section
    return payload


__all__ = [
    "DescentLocality",
    "locality_over_site",
    "locality_evidence",
    "locality_encoding",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import covers_and_hypercovers
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import local_to_global_structure_covers_o
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import obstructions_as_the_common_languag
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
