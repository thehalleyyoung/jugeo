"""Unified encoding dispatcher for JuGeo — routes judgments and sections to
the appropriate encoding family and exposes a registry of all available
encoding subsystems with their decidability classifications.

Public API
----------
Dispatcher functions::

    from jugeo.encodings import (
        encode_judgment,
        encode_section,
        encoding_registry,
    )

The dispatcher consults the judgment's coordinate kind
(``jugeo.geometry.site.CoordinateKind``) to select the right encoding
family, then delegates to that family's pipeline.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Lazy / optional imports — every external subsystem is behind try/except
# so the package remains importable when only a subset of JuGeo is installed.
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import Judgment  # type: ignore[import]
except ImportError:
    Judgment = None  # type: ignore[assignment]

try:
    from jugeo.judgments.sections import Section  # type: ignore[import]
except ImportError:
    Section = None  # type: ignore[assignment]

try:
    from jugeo.geometry.site import CoordinateKind  # type: ignore[import]
except ImportError:
    CoordinateKind = None  # type: ignore[assignment]

try:
    from jugeo.solver.fragments import FragmentClassifier  # type: ignore[import]
except ImportError:
    FragmentClassifier = None  # type: ignore[assignment]

# Encoding-family imports (always present — sibling packages)
try:
    from jugeo.encodings.scalar_encodings import ScalarEncodingPipeline  # type: ignore[import]
except ImportError:
    ScalarEncodingPipeline = None  # type: ignore[assignment]

try:
    from jugeo.encodings.structural_frontier import (  # type: ignore[import]
        StructuralFrontierPipeline,
        DecidabilityClass,
    )
except ImportError:
    StructuralFrontierPipeline = None  # type: ignore[assignment]
    DecidabilityClass = None  # type: ignore[assignment]

try:
    from jugeo.encodings.theorem_schemas import build_complete_registry as _build_ts_registry  # type: ignore[import]
except ImportError:
    _build_ts_registry = None  # type: ignore[assignment]

try:
    from jugeo.encodings.doctrine_completion import DoctrineChecker  # type: ignore[import]
except ImportError:
    DoctrineChecker = None  # type: ignore[assignment]

try:
    from jugeo.encodings.incremental_memory import IncrementalUpdatePipeline  # type: ignore[import]
except ImportError:
    IncrementalUpdatePipeline = None  # type: ignore[assignment]

try:
    from jugeo.encodings.deduction_rules import REGISTRY as _deduction_registry  # type: ignore[import]
except ImportError:
    _deduction_registry = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Coordinate-kind → encoding-family routing table
# ---------------------------------------------------------------------------

_FAMILY_TABLE: dict[str, str] = {
    # CoordinateKind values → encoding family module names
    "SCALAR": "scalar_encodings",
    "STRUCTURAL": "structural_frontier",
    "DEDUCTIVE": "deduction_rules",
    "DOCTRINAL": "doctrine_completion",
    "INCREMENTAL": "incremental_memory",
}


def encode_judgment(judgment: Any) -> dict[str, Any]:
    """Encode a judgment by routing to the appropriate encoding family.

    The judgment's coordinate kind (from ``jugeo.geometry.site.CoordinateKind``)
    determines which encoding family pipeline is invoked.  If the coordinate
    kind is not recognised, a lightweight dictionary encoding is returned so
    that callers always get a result.

    Parameters
    ----------
    judgment:
        A ``jugeo.judgments.judgment_terms.Judgment`` instance.

    Returns
    -------
    dict[str, Any]
        Encoding result with at least ``family``, ``coordinate_kind``, and
        ``encoded`` keys.
    """
    coord_kind: str | None = None
    try:
        coord = getattr(judgment, "coordinate", None)
        if coord is not None:
            kind = getattr(coord, "kind", None)
            coord_kind = kind.value if hasattr(kind, "value") else str(kind)
    except Exception:
        pass

    family_name = _FAMILY_TABLE.get(coord_kind or "", "scalar_encodings")
    result: dict[str, Any] = {
        "family": family_name,
        "coordinate_kind": coord_kind,
        "judgment_id": getattr(judgment, "judgment_id", None),
    }

    if family_name == "scalar_encodings" and ScalarEncodingPipeline is not None:
        try:
            pipeline = ScalarEncodingPipeline()
            result["encoded"] = pipeline.encode(judgment)
        except Exception as exc:
            result["encoded"] = None
            result["error"] = str(exc)
    elif family_name == "structural_frontier" and StructuralFrontierPipeline is not None:
        try:
            pipeline = StructuralFrontierPipeline()
            result["encoded"] = pipeline.encode(judgment)
        except Exception as exc:
            result["encoded"] = None
            result["error"] = str(exc)
    else:
        result["encoded"] = None
        result["note"] = f"Pipeline for '{family_name}' not available"

    return result


def encode_section(section: Any) -> dict[str, Any]:
    """Produce a composite encoding for a judgment section.

    A section (``jugeo.judgments.sections.Section``) groups judgments over a
    coordinate region.  This function encodes each constituent judgment via
    ``encode_judgment`` and aggregates the results.

    Parameters
    ----------
    section:
        A ``jugeo.judgments.sections.Section`` instance.

    Returns
    -------
    dict[str, Any]
        Composite encoding result with ``section_id``, ``encodings`` (list),
        and ``family_summary`` keys.
    """
    section_id = getattr(section, "section_id", None)
    judgments = getattr(section, "judgments", None) or []

    encodings: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}
    for j in judgments:
        enc = encode_judgment(j)
        encodings.append(enc)
        fam = enc.get("family", "unknown")
        family_counts[fam] = family_counts.get(fam, 0) + 1

    return {
        "section_id": section_id,
        "judgment_count": len(encodings),
        "encodings": encodings,
        "family_summary": family_counts,
    }


def encoding_registry() -> dict[str, dict[str, Any]]:
    """Return all available encoding families with their decidability classifications.

    Each entry in the returned dictionary describes one encoding family
    (scalar_encodings, structural_frontier, deduction_rules, etc.) together
    with metadata sourced from the family's own manifest and, when available,
    decidability information from ``jugeo.solver.fragments.FragmentClassifier``.

    Returns
    -------
    dict[str, dict[str, Any]]
        Mapping from family name to a metadata dictionary containing at least
        ``available`` (bool) and ``decidability`` keys.
    """
    families: dict[str, dict[str, Any]] = {}

    # Scalar encodings
    families["scalar_encodings"] = {
        "available": ScalarEncodingPipeline is not None,
        "chapter_ref": "Ch26",
        "description": "Exact Z3 encodings: refinement types, path conditions, guards",
        "decidability": "QF_LIA (decidable)",
    }

    # Structural frontier
    families["structural_frontier"] = {
        "available": StructuralFrontierPipeline is not None,
        "chapter_ref": "Ch25",
        "description": "Structural frontier: decidability classification and repair",
        "decidability": "mixed (frontier-dependent)",
    }

    # Deduction rules
    families["deduction_rules"] = {
        "available": _deduction_registry is not None,
        "chapter_ref": "Ch33",
        "description": "Deduction rules: inference, transitions, structural/semantic",
        "decidability": "decidable (cut-free fragment)",
    }

    # Theorem schemas
    families["theorem_schemas"] = {
        "available": _build_ts_registry is not None,
        "chapter_ref": "Ch36",
        "description": "Subsystem theorem schemas: proof obligations per subsystem",
        "decidability": "schema-level (per-subsystem)",
    }

    # Doctrine completion
    families["doctrine_completion"] = {
        "available": DoctrineChecker is not None,
        "chapter_ref": "Ch37",
        "description": "Implementation-complete thesis doctrine evidence checking",
        "decidability": "decidable (evidence grounding)",
    }

    # Incremental memory
    families["incremental_memory"] = {
        "available": IncrementalUpdatePipeline is not None,
        "chapter_ref": "Ch34",
        "description": "Incremental memory: Glue construction for memory updates",
        "decidability": "decidable (finite glue)",
    }

    # Enrich with solver fragment classifier when available
    if FragmentClassifier is not None:
        try:
            classifier = FragmentClassifier()
            for name, meta in families.items():
                try:
                    fragment_info = classifier.classify(name)
                    meta["fragment_class"] = getattr(fragment_info, "name", str(fragment_info))
                except Exception:
                    pass
        except Exception:
            pass

    return families


__all__ = [
    "encode_judgment",
    "encode_section",
    "encoding_registry",
]


# --- auto-registered submodules ---
try:
    from . import collection_heap_encodings
except Exception:
    pass
try:
    from . import deduction_rules
except Exception:
    pass
try:
    from . import doctrine_completion
except Exception:
    pass
try:
    from . import incremental_memory
except Exception:
    pass
try:
    from . import ir_stack
except Exception:
    pass
try:
    from . import pack_federation
except Exception:
    pass
try:
    from . import partiality_model_reconstruction
except Exception:
    pass
try:
    from . import scalar_encodings
except Exception:
    pass
try:
    from . import sequence_mutation_encodings
except Exception:
    pass
try:
    from . import structural_frontier
except Exception:
    pass
try:
    from . import tensor_quantifier_encodings
except Exception:
    pass
try:
    from . import text_encodings
except Exception:
    pass
try:
    from . import theorem_schemas
except Exception:
    pass
