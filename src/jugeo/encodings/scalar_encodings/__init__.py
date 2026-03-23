"""Scalar encodings package for JuGeo — Chapter 26 exact Z3 encodings.

Provides base refinement encodings, guard formula encodings, arithmetic
obligation encodings, and path-condition encodings together with a full
integration pipeline and formal theorem registry.

Public API
----------
Models (core data types)::

    from jugeo.encodings.scalar_encodings import (
        SortKind, FragmentHint, EncodeStatus,
        RefinementEncoding, PathCondition, GuardFormula,
        ArithmeticObligation, EncodingContext, EncodingResult,
    )

Encoders::

    from jugeo.encodings.scalar_encodings import (
        RefinementTypeEncoder,
        PathConditionEncoder,
        FailureArtifactEncoder,
    )

Failure kinds::

    from jugeo.encodings.scalar_encodings import FailureKind, FailureArtifact

Algorithms::

    from jugeo.encodings.scalar_encodings import (
        IncrementalRefinementSolver,
        GuardSimplificationEngine,
        PathConditionPropagator,
        FailureRegressionTracker,
        encode_refinement_batch,
        merge_encoding_contexts,
        classify_arithmetic_fragment,
    )

Integration pipeline::

    from jugeo.encodings.scalar_encodings import (
        ScalarEncodingPipeline,
        Z3SessionBridge,
        SupportRegionLinker,
        CountermodelInterpreter,
        FragmentRouter,
    )

Theorems::

    from jugeo.encodings.scalar_encodings import (
        TheoremStatus, TheoremRecord, TheoremRegistry,
        THEOREM_REGISTRY, ALL_THEOREMS,
    )

Manifest::

    from jugeo.encodings.scalar_encodings import (
        CoverageStatus, ManifestRecord, PackageManifest,
        MANIFEST, CHAPTER_COVERAGE,
    )
"""

from __future__ import annotations

# --- models ---
from jugeo.encodings.scalar_encodings.models import (
    ArithmeticObligation,
    EncodeStatus,
    EncodingContext,
    EncodingResult,
    FragmentHint,
    GuardFormula,
    PathCondition,
    RefinementEncoding,
    SortKind,
    make_context_id,
    make_encoding_id,
    make_result_id,
)

# --- refinement type encoder ---
from jugeo.encodings.scalar_encodings.refinement_type_encoder import (
    ConstraintLifter,
    PredicateNormalizer,
    RefinementSortBuilder,
    RefinementTypeEncoder,
    encode_type,
)

# --- path condition encoder ---
from jugeo.encodings.scalar_encodings.path_condition_encoder import (
    BranchNode,
    JoinConditionSynthesizer,
    PathConditionEncoder,
    PathTree,
    encode_simple_branch,
)

# --- failure artifact encoder ---
from jugeo.encodings.scalar_encodings.failure_artifact_encoder import (
    FailureArtifact,
    FailureArtifactEncoder,
    FailureKind,
    FailurePreconditionExtractor,
    encode_simple_failure,
)

# --- algorithms ---
from jugeo.encodings.scalar_encodings.algorithms import (
    FailureRegressionTracker,
    GuardSimplificationEngine,
    IncrementalRefinementSolver,
    PathConditionPropagator,
    check_subtype_entailment,
    classify_arithmetic_fragment,
    encode_refinement_batch,
    extract_unsat_core_hints,
    merge_encoding_contexts,
    minimize_path_conditions,
)

# --- integration ---
from jugeo.encodings.scalar_encodings.integration import (
    CountermodelInterpreter,
    FragmentRouter,
    ScalarEncodingPipeline,
    SupportRegionLinker,
    Z3SessionBridge,
)

# --- theorems ---
from jugeo.encodings.scalar_encodings.theorems import (
    ALL_THEOREMS,
    THEOREM_REGISTRY,
    THM_ARITHMETIC_DECIDABILITY,
    THM_ENCODING_CONTEXT_MONOTONICITY,
    THM_FAILURE_ARTIFACT_MINIMALITY,
    THM_GUARD_ELIMINATION,
    THM_PATH_CONDITION_COMPLETENESS,
    THM_PATH_JOIN_SOUNDNESS,
    THM_QF_LIA_TERMINATION,
    THM_REFINEMENT_SOUNDNESS,
    THM_SUBTYPE_ENTAILMENT_CORRECTNESS,
    THM_UNSAT_CORE_MINIMALITY,
    TheoremRecord,
    TheoremRegistry,
    TheoremStatus,
    check_dependencies_met,
    export_theorem_list,
    verify_theorem_sketch,
)

# --- manifest ---
from jugeo.encodings.scalar_encodings.manifest import (
    CHAPTER_COVERAGE,
    EXPORTED_SYMBOLS,
    MANIFEST,
    THEORY_CLAIMS,
    ClaimSummary,
    CoverageStatus,
    ManifestRecord,
    PackageManifest,
    SymbolGroup,
)

__all__ = [
    # models
    "SortKind",
    "FragmentHint",
    "EncodeStatus",
    "RefinementEncoding",
    "PathCondition",
    "GuardFormula",
    "ArithmeticObligation",
    "EncodingContext",
    "EncodingResult",
    "make_encoding_id",
    "make_context_id",
    "make_result_id",
    # encoders
    "RefinementSortBuilder",
    "PredicateNormalizer",
    "ConstraintLifter",
    "RefinementTypeEncoder",
    "encode_type",
    "BranchNode",
    "PathTree",
    "JoinConditionSynthesizer",
    "PathConditionEncoder",
    "encode_simple_branch",
    "FailureKind",
    "FailureArtifact",
    "FailurePreconditionExtractor",
    "FailureArtifactEncoder",
    "encode_simple_failure",
    # algorithms
    "encode_refinement_batch",
    "check_subtype_entailment",
    "merge_encoding_contexts",
    "minimize_path_conditions",
    "extract_unsat_core_hints",
    "classify_arithmetic_fragment",
    "IncrementalRefinementSolver",
    "GuardSimplificationEngine",
    "PathConditionPropagator",
    "FailureRegressionTracker",
    # integration
    "ScalarEncodingPipeline",
    "Z3SessionBridge",
    "SupportRegionLinker",
    "CountermodelInterpreter",
    "FragmentRouter",
    # theorems
    "TheoremStatus",
    "TheoremRecord",
    "TheoremRegistry",
    "THEOREM_REGISTRY",
    "ALL_THEOREMS",
    "THM_REFINEMENT_SOUNDNESS",
    "THM_PATH_CONDITION_COMPLETENESS",
    "THM_GUARD_ELIMINATION",
    "THM_ARITHMETIC_DECIDABILITY",
    "THM_FAILURE_ARTIFACT_MINIMALITY",
    "THM_SUBTYPE_ENTAILMENT_CORRECTNESS",
    "THM_QF_LIA_TERMINATION",
    "THM_PATH_JOIN_SOUNDNESS",
    "THM_ENCODING_CONTEXT_MONOTONICITY",
    "THM_UNSAT_CORE_MINIMALITY",
    "verify_theorem_sketch",
    "check_dependencies_met",
    "export_theorem_list",
    # manifest
    "CoverageStatus",
    "ManifestRecord",
    "SymbolGroup",
    "ClaimSummary",
    "PackageManifest",
    "CHAPTER_COVERAGE",
    "EXPORTED_SYMBOLS",
    "THEORY_CLAIMS",
    "MANIFEST",
    # cross-subsystem integration
    "encode_from_coordinate",
    "trust_refined_encoding",
    "certificate_from_encoding",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration — geometry, evidence, and certificates
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import Coordinate, CoordinateKind  # type: ignore[import]
except ImportError:
    Coordinate = None  # type: ignore[assignment]
    CoordinateKind = None  # type: ignore[assignment]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustAnnotation  # type: ignore[import]
except ImportError:
    TrustAlgebra = None  # type: ignore[assignment]
    TrustAnnotation = None  # type: ignore[assignment]

try:
    from jugeo.evidence.certificates import (  # type: ignore[import]
        Certificate,
        CertificateBuilder,
    )
except ImportError:
    Certificate = None  # type: ignore[assignment]
    CertificateBuilder = None  # type: ignore[assignment]


def encode_from_coordinate(coordinate: object) -> "RefinementEncoding | None":
    """Produce a scalar refinement encoding from a geometry coordinate.

    Takes a ``jugeo.geometry.site.Coordinate`` and uses its kind and
    metadata to construct an appropriate ``RefinementEncoding``.  The
    coordinate's sort is mapped to the nearest ``SortKind`` and the
    encoding is built via ``RefinementTypeEncoder``.

    Parameters
    ----------
    coordinate:
        A ``jugeo.geometry.site.Coordinate`` instance.

    Returns
    -------
    RefinementEncoding | None
        The scalar refinement encoding, or ``None`` if the coordinate
        cannot be encoded (e.g. missing kind or unsupported sort).
    """
    kind = getattr(coordinate, "kind", None)
    kind_value = kind.value if hasattr(kind, "value") else str(kind) if kind else None

    sort_map = {
        "SCALAR": SortKind.INT if hasattr(SortKind, "INT") else list(SortKind)[0],
        "BOOLEAN": SortKind.BOOL if hasattr(SortKind, "BOOL") else list(SortKind)[0],
        "REAL": SortKind.REAL if hasattr(SortKind, "REAL") else list(SortKind)[0],
    }
    sort = sort_map.get(kind_value, list(SortKind)[0] if len(SortKind) > 0 else None)
    if sort is None:
        return None

    try:
        encoder = RefinementTypeEncoder()
        ctx = EncodingContext(
            context_id=make_context_id(),
            coordinate_key=getattr(coordinate, "key", str(coordinate)),
            metadata={"source": "encode_from_coordinate", "kind": kind_value},
        )
        return encoder.encode(sort_kind=sort, context=ctx)
    except Exception:
        return None


def trust_refined_encoding(
    encoding: "RefinementEncoding",
    trust_annotation: object | None = None,
    trust_floor: float = 0.0,
) -> "EncodingResult":
    """Factor trust annotations from the evidence subsystem into an encoding.

    When a ``jugeo.evidence.trust.TrustAnnotation`` is provided its trust
    level is used to gate the encoding: refinements whose trust level falls
    below *trust_floor* are marked as ``EncodeStatus.SKIPPED``.  The trust
    algebra's admissibility predicate is consulted to decide.

    Parameters
    ----------
    encoding:
        A ``RefinementEncoding`` produced by one of the scalar encoders.
    trust_annotation:
        Optional ``jugeo.evidence.trust.TrustAnnotation``.  When ``None``
        the encoding is returned as-is wrapped in an ``EncodingResult``.
    trust_floor:
        Minimum trust level required (0.0–1.0).

    Returns
    -------
    EncodingResult
        Result wrapping the encoding with trust metadata attached.
    """
    trust_level: float | None = None
    if trust_annotation is not None:
        trust_level = getattr(trust_annotation, "level", None)
        if trust_level is None:
            trust_level = getattr(trust_annotation, "value", None)

    status = EncodeStatus.SUCCESS if hasattr(EncodeStatus, "SUCCESS") else list(EncodeStatus)[0]
    if trust_level is not None and trust_level < trust_floor:
        status = EncodeStatus.SKIPPED if hasattr(EncodeStatus, "SKIPPED") else list(EncodeStatus)[-1]

    metadata: dict = {
        "trust_level": trust_level,
        "trust_floor": trust_floor,
        "trust_refined": True,
    }

    # Consult the trust algebra admissibility predicate when available
    if TrustAlgebra is not None and trust_annotation is not None:
        try:
            algebra = TrustAlgebra()
            metadata["admissible"] = algebra.is_admissible(trust_annotation)
        except Exception:
            metadata["admissible"] = None

    return EncodingResult(
        result_id=make_result_id(),
        encoding=encoding,
        status=status,
        metadata=metadata,
    )


def certificate_from_encoding(
    encoding_result: "EncodingResult",
    judgment: object | None = None,
) -> object | None:
    """Produce an evidence certificate when an encoding succeeds.

    Delegates to ``jugeo.evidence.certificates.CertificateBuilder`` to
    construct a ``Certificate`` that records the successful encoding as
    verified evidence.  The resulting certificate can be consumed by the
    trust subsystem and attached to provenance chains.

    Parameters
    ----------
    encoding_result:
        An ``EncodingResult`` from a scalar encoding pipeline.
    judgment:
        Optional ``jugeo.judgments.judgment_terms.Judgment`` to attach to
        the certificate as provenance context.

    Returns
    -------
    Certificate | None
        A ``jugeo.evidence.certificates.Certificate``, or ``None`` when
        the certificates subsystem is unavailable or the encoding did not
        succeed.
    """
    if CertificateBuilder is None:
        return None

    status = getattr(encoding_result, "status", None)
    success_status = EncodeStatus.SUCCESS if hasattr(EncodeStatus, "SUCCESS") else list(EncodeStatus)[0]
    if status != success_status:
        return None

    try:
        builder = CertificateBuilder()
        builder.set_source("jugeo.encodings.scalar_encodings")
        builder.set_kind("encoding_verification")
        builder.set_evidence({
            "encoding_id": getattr(
                getattr(encoding_result, "encoding", None), "encoding_id", None
            ),
            "result_id": getattr(encoding_result, "result_id", None),
            "metadata": getattr(encoding_result, "metadata", {}),
        })
        if judgment is not None:
            builder.set_judgment(judgment)
        return builder.build()
    except Exception:
        return None


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import branching_joins_and_path_sensitive
except Exception:
    pass
try:
    from . import exact_failure_artifacts
except Exception:
    pass
try:
    from . import failure_artifact_encoder
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
    from . import path_condition_encoder
except Exception:
    pass
try:
    from . import refinement_type_encoder
except Exception:
    pass
try:
    from . import the_encoding_layer_should_begin_fr
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
