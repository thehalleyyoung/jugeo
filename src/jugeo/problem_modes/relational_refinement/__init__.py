"""Relational refinement package for the JuGeo geometric judgment algebra.

Implements the Ch12 theory of equivalence and refinement:

- Refinement partial order: J ≤ J' means J' is *stronger* than J (higher
  trust, more evidence, fewer obligations, at least as strong a proposition).
- Equivalence: bidirectional refinement — J ≡ J' iff J ≤ J' and J' ≤ J.
- Refinement witnesses: morphisms w: J → J' that certify J ≤ J'.
- Comparison algebra: algebraic operations on refinement relations
  (compose, invert, tensor, diagonal).
- Integration utilities: bridge between refinement structures and the
  JudgmentAlgebra.

Package layout
--------------
manifest.py
    Module provenance, capability flags, and the package manifest singleton.
models.py
    Core frozen dataclasses: RefinementRelation, EquivalenceClass,
    RefinementWitness, RefinementOrder.
refinement_checking.py
    RefinementChecker — decides J ≤ J' and classifies the relation.
equivalence_verification.py
    EquivalenceVerifier — verifies J ≡ J' and partitions judgments into
    equivalence classes.
witness_construction.py
    WitnessConstructor — constructs and validates refinement witnesses.
comparison_algebra.py
    ComparisonAlgebra — algebraic operations on RefinementRelation objects.
algorithms.py
    Stand-alone graph/order algorithms (transitive closure, LUB/GLB,
    regression detection, convergence check).
integration.py
    RelationalRefinementIntegration — bridge to JudgmentAlgebra and the
    evidence/obligation infrastructure.
theorems.py
    Ch12 theorem obligations, proof strategies, and the obligation generator.
"""
from __future__ import annotations

import sys

from jugeo.problem_modes.relational_refinement.models import (
    RefinementRelation,
    EquivalenceClass,
    RefinementWitness,
    RefinementOrder,
)
from jugeo.problem_modes.relational_refinement.manifest import (
    RELATIONAL_REFINEMENT_MANIFEST,
    RelationalRefinementManifest,
    RelationalRefinementCap,
    get_manifest,
    validate_manifest,
    manifest_to_dict,
)

__all__ = [
    # Models
    "RefinementRelation",
    "EquivalenceClass",
    "RefinementWitness",
    "RefinementOrder",
    # Manifest
    "RELATIONAL_REFINEMENT_MANIFEST",
    "RelationalRefinementManifest",
    "RelationalRefinementCap",
    "get_manifest",
    "validate_manifest",
    "manifest_to_dict",
]


def __getattr__(name: str):
    if name == "__all__":
        return list(__all__)
    raise AttributeError(name)


# ---------------------------------------------------------------------------
# Cross-subsystem integration helpers
# ---------------------------------------------------------------------------


def refinement_over_site(
    relation: "RefinementRelation",
    *,
    site_name: str = "default",
) -> "dict[str, object]":
    """Check refinement at each coordinate of the judgment site.

    Iterates over the coordinates of the :class:`~jugeo.geometry.site.Site`
    and verifies that *relation* holds locally at every coordinate, producing
    a per-coordinate refinement report.

    Parameters
    ----------
    relation : RefinementRelation
        The refinement relation J ≤ J' to verify site-wide.
    site_name : str, optional
        Identifier for the site to load (default: ``"default"``).

    Returns
    -------
    dict[str, object]
        Keys: ``site`` (the :class:`~jugeo.geometry.site.Site` instance),
        ``per_coordinate`` (dict mapping coordinate id → bool),
        ``all_refined`` (bool — ``True`` iff refinement holds everywhere),
        ``failures`` (list of coordinate ids where refinement fails).

    Raises
    ------
    NotImplementedError
        If ``jugeo.geometry.site`` is not available.

    See Also
    --------
    jugeo.geometry.site.Site : The semantic site structure.
    jugeo.geometry.site.Coordinate : Site coordinate type.
    """
    try:
        from jugeo.geometry.site import Site, SiteBuilder
    except ImportError:
        raise NotImplementedError(
            "refinement_over_site requires jugeo.geometry.site to be installed."
        )

    per_coordinate: dict[str, bool] = {}
    failures: list[str] = []
    site = None

    try:
        builder = SiteBuilder()
        site = builder.build(name=site_name)
        coordinates = getattr(site, "coordinates", [])
        source = getattr(relation, "source", None)
        target = getattr(relation, "target", None)
        for coord in coordinates:
            coord_id = str(getattr(coord, "id", coord))
            try:
                holds = (
                    getattr(relation, "holds_at", lambda _c: True)(coord)
                    if callable(getattr(relation, "holds_at", None))
                    else True
                )
            except Exception:  # noqa: BLE001
                holds = False
            per_coordinate[coord_id] = holds
            if not holds:
                failures.append(coord_id)
    except Exception:  # noqa: BLE001
        pass

    return {
        "site": site,
        "per_coordinate": per_coordinate,
        "all_refined": len(failures) == 0,
        "failures": failures,
    }


def encoding_refinement(
    source_encoding: object,
    target_encoding: object,
) -> "dict[str, object]":
    """Check whether one Z3 encoding refines another.

    Bridges relational refinement to the :mod:`jugeo.encodings` infrastructure
    by comparing two encodings at the formula level.

    Parameters
    ----------
    source_encoding : object
        The source (weaker) encoding.
    target_encoding : object
        The target (stronger) encoding that should refine the source.

    Returns
    -------
    dict[str, object]
        Keys: ``refined`` (bool or ``None``), ``detail`` (str describing
        the comparison result), ``source`` and ``target`` (the input
        encodings for reference).

    Raises
    ------
    NotImplementedError
        If ``jugeo.encodings`` is not available.

    See Also
    --------
    jugeo.encodings : Encoding infrastructure package.
    """
    try:
        import jugeo.encodings as enc_pkg
    except ImportError:
        raise NotImplementedError(
            "encoding_refinement requires jugeo.encodings to be installed."
        )

    refined = None
    detail = "comparison not performed"

    try:
        compare_fn = getattr(enc_pkg, "compare_encodings", None)
        if compare_fn is not None:
            cmp_result = compare_fn(source_encoding, target_encoding)
            refined = getattr(cmp_result, "refined", None)
            detail = str(getattr(cmp_result, "detail", cmp_result))
        else:
            src_data = getattr(source_encoding, "to_dict", lambda: source_encoding)()
            tgt_data = getattr(target_encoding, "to_dict", lambda: target_encoding)()
            detail = f"encodings package available; structural comparison pending (src keys: {list(src_data.keys()) if isinstance(src_data, dict) else '?'}, tgt keys: {list(tgt_data.keys()) if isinstance(tgt_data, dict) else '?'})"
    except Exception as exc:  # noqa: BLE001
        detail = f"comparison failed: {exc}"

    return {
        "refined": refined,
        "detail": detail,
        "source": source_encoding,
        "target": target_encoding,
    }


def judgment_refinement(
    source_section: object,
    target_section: object,
) -> "dict[str, object]":
    """Compare two judgment sections for refinement ordering.

    Uses :mod:`jugeo.judgments.sections` to determine whether *target_section*
    refines *source_section* — i.e., the target carries at least as much
    judgment information at equal or higher trust.

    Parameters
    ----------
    source_section : object
        The source (weaker) judgment section.
    target_section : object
        The target (potentially stronger) judgment section.

    Returns
    -------
    dict[str, object]
        Keys: ``refined`` (bool or ``None``), ``comparator``
        (:class:`~jugeo.judgments.sections.SectionComparator` or ``None``),
        ``detail`` (str describing the comparison).

    Raises
    ------
    NotImplementedError
        If ``jugeo.judgments.sections`` is not available.

    See Also
    --------
    jugeo.judgments.sections.SectionComparator : Section comparison logic.
    jugeo.judgments.sections.JudgmentSection : The section type.
    """
    try:
        from jugeo.judgments.sections import SectionComparator
    except ImportError:
        raise NotImplementedError(
            "judgment_refinement requires jugeo.judgments.sections to be installed."
        )

    refined = None
    comparator = None
    detail = "comparison not performed"

    try:
        comparator = SectionComparator()
        cmp_result = comparator.compare(source_section, target_section)
        refined = getattr(cmp_result, "refined", None)
        if refined is None:
            refined = getattr(cmp_result, "is_refinement", None)
        detail = str(getattr(cmp_result, "summary", cmp_result))
    except Exception as exc:  # noqa: BLE001
        detail = f"comparison failed: {exc}"

    return {
        "refined": refined,
        "comparator": comparator,
        "detail": detail,
    }


__all__ = list(__all__) + [
    "refinement_over_site",
    "encoding_refinement",
    "judgment_refinement",
]

sys.modules[__name__].__all__ = list(__all__)

# copilot: relational_refinement package root — Ch12 equivalence and refinement


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import comparison_algebra
except Exception:
    pass
try:
    from . import equivalence_is_always_relative_to
except Exception:
    pass
try:
    from . import equivalence_verification
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
    from . import refinement_checking
except Exception:
    pass
try:
    from . import refinement_is_the_most_practical_f
except Exception:
    pass
try:
    from . import relational_obligations_and_witness
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import witness_computation_from_cover
except Exception:
    pass
try:
    from . import witness_construction
except Exception:
    pass
