"""
tensor_quantifier_encodings — Chapter 30 of theory2.tex.
==========================================================
Exact Z3 encodings V: tensor extents, affine legality, quantifier discipline,
witness extraction.

This package provides the encoding layer for multi-dimensional tensor shape
constraints, affine transformation legality conditions, and witness extraction
from Z3 solver results.  It is the reference implementation of Chapter 30 of
the JuGeo formal theory document (theory2.tex).

copilot notes: Import from this package's top-level for all public API.
The solver integration requires jugeo.solver (optional); all other modules
work standalone with or without a Z3 installation.

Submodules:
  manifest   — Package manifest and capability declarations.
  models     — Core dataclass models (TensorExtent, AffineLegality, etc.).
  why_tensors_matter          — Motivational examples and primer encoder.
  affine_normal_form_encoder  — Affine constraint encoding for Z3.
  quantifier_discipline       — Quantifier discipline checker and instantiator.
  witness_extractor           — Witness extraction from SAT/UNSAT results.
  algorithms — Pure Python algorithms (Fourier-Motzkin, Farkas, etc.).
  integration — Integration with the jugeo.solver layer.
  theorems   — Formal theorem statements for Chapter 30.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.manifest import (
    SubsystemManifest,
    TheoryProvenance,
    CapabilityDeclaration,
    DependencySpec,
    ManifestValidator,
    ManifestValidationError,
    TENSOR_QUANTIFIER_MANIFEST,
    CHAPTER_30_SECTIONS,
    STABLE_EXPORTS,
    PACKAGE_VERSION,
    THEORY_CHAPTER,
    get_manifest,
    validate_manifest,
)

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.models import (
    TensorLayout,
    TensorExtent,
    AffineLegality,
    DisciplineKind,
    QuantifierDiscipline,
    ExtractionStrategy,
    WitnessExtractor,
    ConstraintKind,
    TensorConstraint,
)

# ---------------------------------------------------------------------------
# s01
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.why_tensors_matter import (
    TensorMotivationExamples,
    TensorEncodingPrimer,
    why_arrays_of_arrays,
    qf_lia_decidability_argument,
    affine_index_normal_form,
)

# ---------------------------------------------------------------------------
# s02
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.affine_normal_form_encoder import (
    AffineNormalFormEncoder,
    gcd as _s02_gcd,
    gcd_list as _s02_gcd_list,
    matrix_vector_multiply,
)

# ---------------------------------------------------------------------------
# s03
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.quantifier_discipline import (
    DisciplineReport,
    QuantifierInfo,
    QuantifierDisciplineChecker,
    QuantifierInstantiator,
    DISCIPLINE_RULES,
    is_qf_formula,
    count_quantifier_alternations,
)

# ---------------------------------------------------------------------------
# s04
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.witness_extractor import (
    TensorWitness,
    DependenceWitness,
    FarkasCoefficients,
    TensorWitnessExtractor,
    AffineLegalityWitnessExtractor,
)

# ---------------------------------------------------------------------------
# algorithms
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.algorithms import (
    fourier_motzkin,
    farkas_lemma_certificate,
    affine_transformation_legality,
    broadcast_shape_unification,
    linearize_nd_index,
    compute_tensor_stride,
    affine_hull,
    normal_cone,
    copilot_derive_tiling_schedule,
    gcd,
    lcm,
    matrix_multiply,
    transpose_matrix,
    lex_compare,
    is_lex_positive,
)

# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.integration import (
    TensorQuantifierSolverIntegration,
    TensorEncodingContext,
)

# ---------------------------------------------------------------------------
# theorems
# ---------------------------------------------------------------------------
from jugeo.encodings.tensor_quantifier_encodings.theorems import (
    TensorQuantifierTheorem,
    AffineTransformLegalityTheorem,
    FarkasInfeasibilityTheorem,
    QuantifierEliminationTheorem,
    WitnessCompletenessTheorem,
    BroadcastCompatibilityTheorem,
    CHAPTER_30_THEOREMS,
    get_theorem_by_number,
    verify_all_theorems,
)

__all__ = [
    # manifest
    "SubsystemManifest",
    "TheoryProvenance",
    "CapabilityDeclaration",
    "DependencySpec",
    "ManifestValidator",
    "ManifestValidationError",
    "TENSOR_QUANTIFIER_MANIFEST",
    "CHAPTER_30_SECTIONS",
    "STABLE_EXPORTS",
    "PACKAGE_VERSION",
    "THEORY_CHAPTER",
    "get_manifest",
    "validate_manifest",
    # models
    "TensorLayout",
    "TensorExtent",
    "AffineLegality",
    "DisciplineKind",
    "QuantifierDiscipline",
    "ExtractionStrategy",
    "WitnessExtractor",
    "ConstraintKind",
    "TensorConstraint",
    # s01
    "TensorMotivationExamples",
    "TensorEncodingPrimer",
    "why_arrays_of_arrays",
    "qf_lia_decidability_argument",
    "affine_index_normal_form",
    # s02
    "AffineNormalFormEncoder",
    "matrix_vector_multiply",
    # s03
    "DisciplineReport",
    "QuantifierInfo",
    "QuantifierDisciplineChecker",
    "QuantifierInstantiator",
    "DISCIPLINE_RULES",
    "is_qf_formula",
    "count_quantifier_alternations",
    # s04
    "TensorWitness",
    "DependenceWitness",
    "FarkasCoefficients",
    "TensorWitnessExtractor",
    "AffineLegalityWitnessExtractor",
    # algorithms
    "fourier_motzkin",
    "farkas_lemma_certificate",
    "affine_transformation_legality",
    "broadcast_shape_unification",
    "linearize_nd_index",
    "compute_tensor_stride",
    "affine_hull",
    "normal_cone",
    "copilot_derive_tiling_schedule",
    "gcd",
    "lcm",
    "matrix_multiply",
    "transpose_matrix",
    "lex_compare",
    "is_lex_positive",
    # integration
    "TensorQuantifierSolverIntegration",
    "TensorEncodingContext",
    # theorems
    "TensorQuantifierTheorem",
    "AffineTransformLegalityTheorem",
    "FarkasInfeasibilityTheorem",
    "QuantifierEliminationTheorem",
    "WitnessCompletenessTheorem",
    "BroadcastCompatibilityTheorem",
    "CHAPTER_30_THEOREMS",
    "get_theorem_by_number",
    "verify_all_theorems",
]


# --- auto-registered submodules ---
try:
    from . import affine_and_quasi_affine_normal_for
except Exception:
    pass
try:
    from . import affine_normal_form_encoder
except Exception:
    pass
try:
    from . import algorithms
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
    from . import quantifier_discipline
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import why_tensors_matter
except Exception:
    pass
try:
    from . import why_this_family_matters_disproport
except Exception:
    pass
try:
    from . import witness_extraction_and_proof_burde
except Exception:
    pass
try:
    from . import witness_extractor
except Exception:
    pass
