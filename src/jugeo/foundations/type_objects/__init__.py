"""jugeo.foundations.type_objects — Type objects, coordinates, and carrier laws.

Theory2.tex Chapter 6: From Ordinary Annotations to Coordinated Type Objects.

This module bridges the categorical type-object theory to the concrete
implementations across jugeo subsystems: judgments, encodings, and evidence.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# --- Cross-subsystem imports ------------------------------------------------
try:
    from jugeo.judgments.judgment_terms import (
        Proposition, PropositionKind, TrustLevel as JTrustLevel,
        JudgmentStatus, EvidenceItemKind,
    )
    _HAS_JUDGMENTS = True
except ImportError:
    Proposition = None  # type: ignore[assignment,misc]
    PropositionKind = None  # type: ignore[assignment,misc]
    JTrustLevel = None  # type: ignore[assignment,misc]
    JudgmentStatus = None  # type: ignore[assignment,misc]
    EvidenceItemKind = None  # type: ignore[assignment,misc]
    _HAS_JUDGMENTS = False

try:
    from jugeo.encodings.scalar_encodings import encode_from_coordinate, trust_refined_encoding
    _HAS_ENCODINGS = True
except ImportError:
    encode_from_coordinate = None  # type: ignore[assignment,misc]
    trust_refined_encoding = None  # type: ignore[assignment,misc]
    _HAS_ENCODINGS = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustTier
    _HAS_TRUST = True
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustAlgebra = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    _HAS_TRUST = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def type_judgment(type_obj: dict[str, Any], *, status: str = "pending") -> dict[str, Any]:
    """Create a structured judgment term for a type object.

    Bridges Theory2.tex §6.1 (type annotations as coordinates) to the
    concrete ``jugeo.judgments.judgment_terms`` subsystem.
    """
    name = type_obj.get("name", "unknown")
    coordinate = type_obj.get("coordinate", {})
    evidence = type_obj.get("evidence", [])

    if _HAS_JUDGMENTS and Proposition is not None:
        kind = PropositionKind.TYPE_ANNOTATION if hasattr(PropositionKind, "TYPE_ANNOTATION") else PropositionKind(0)
        prop = Proposition(kind=kind, label=name, data=coordinate)
        trust = JTrustLevel.MEDIUM if hasattr(JTrustLevel, "MEDIUM") else JTrustLevel(1)
        ev_items = [
            {"kind": EvidenceItemKind.SOURCE_ANNOTATION if hasattr(EvidenceItemKind, "SOURCE_ANNOTATION") else "source", "ref": e}
            for e in evidence
        ]
        return {
            "proposition": prop,
            "status": JudgmentStatus[status.upper()] if hasattr(JudgmentStatus, status.upper()) else status,
            "trust": trust,
            "evidence": ev_items,
        }

    logger.debug("judgments subsystem unavailable; returning plain dict for %s", name)
    return {"name": name, "coordinate": coordinate, "status": status, "evidence": evidence}


def type_encoding(type_obj: dict[str, Any], *, backend: str = "z3") -> dict[str, Any]:
    """Encode a type object for solver consumption.

    Bridges Theory2.tex §6.2 (carrier-set encoding) to
    ``jugeo.encodings.scalar_encodings``.
    """
    name = type_obj.get("name", "unknown")
    coordinate = type_obj.get("coordinate", {})
    trust_hint = type_obj.get("trust", None)

    if _HAS_ENCODINGS and encode_from_coordinate is not None:
        base = encode_from_coordinate(coordinate, backend=backend)
        if trust_hint is not None and trust_refined_encoding is not None:
            refined = trust_refined_encoding(base, trust_level=trust_hint)
        else:
            refined = base
        return {"backend": backend, "encoding": refined, "source": name}

    scalar = sum(hash(str(v)) & 0xFFFF for v in coordinate.values())
    logger.debug("encodings subsystem unavailable; falling back to hash encoding for %s", name)
    return {"backend": backend, "encoding": scalar, "source": name, "fallback": True}


def type_trust(type_obj: dict[str, Any], *, policy: str = "default") -> dict[str, Any]:
    """Assign and compute trust level for a type object.

    Bridges Theory2.tex §6.3 (trust propagation along coordinate maps)
    to ``jugeo.evidence.trust``.
    """
    name = type_obj.get("name", "unknown")
    evidence = type_obj.get("evidence", [])
    raw_trust = type_obj.get("trust", 0.5)

    if _HAS_TRUST and TrustLevel is not None:
        level = TrustLevel(raw_trust)
        algebra = TrustAlgebra(policy=policy)
        for item in evidence:
            weight = item.get("weight", 1.0) if isinstance(item, dict) else 1.0
            level = algebra.incorporate(level, weight)
        tier = TrustTier.from_level(level) if hasattr(TrustTier, "from_level") else TrustTier(level.value)
        return {"name": name, "trust_level": level, "tier": tier, "policy": policy}

    combined = raw_trust
    for item in evidence:
        w = item.get("weight", 1.0) if isinstance(item, dict) else 1.0
        combined = combined * 0.9 + w * 0.1
    combined = max(0.0, min(1.0, combined))
    logger.debug("trust subsystem unavailable; computed fallback trust %.3f for %s", combined, name)
    return {"name": name, "trust_level": combined, "tier": "fallback", "policy": policy}


__all__ = [
    "type_judgment",
    "type_encoding",
    "type_trust",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import carrier_laws_transport_gluing_and
except Exception:
    pass
try:
    from . import coordinates_where_context_support
except Exception:
    pass
try:
    from . import from_ordinary_annotations_to_coord
except Exception:
    pass
try:
    from . import integration
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
    from . import theorems
except Exception:
    pass
