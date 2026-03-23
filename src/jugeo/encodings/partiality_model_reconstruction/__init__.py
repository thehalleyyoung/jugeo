r"""partiality_model_reconstruction — Z3 encodings VI: partial functions,
algebraic data surfaces, exception-valued semantics, and model reconstruction.
Theory2.tex Ch31.

This package implements the encodings and algorithms described in Chapter 31
of theory2.tex.  It is structured as follows:

- :mod:`.manifest` — package manifest and component registry
- :mod:`.models` — core data models (PartialFunctionEncoding, etc.)
- :mod:`.partial_functions` — §31.1 partial functions as Z3 relations
- :mod:`.exception_semantics` — §31.2 exception-valued semantics
- :mod:`.algebraic_surfaces` — §31.3 algebraic data type surfaces
- :mod:`.model_reconstruction` — §31.4 full model reconstruction pipeline
- :mod:`.algorithms` — core algorithms
- :mod:`.integration` — JuGeo solver integration
- :mod:`.theorems` — theorem statements and Z3 encodings

.. math::

   f : A \\rightharpoonup B
   \\;\\xrightarrow{\\text{encode}}\\;
   (\\mathrm{dom}_f,\\, R_f)
   \\;\\xrightarrow{\\text{solve}}\\;
   M
   \\;\\xrightarrow{\\text{reconstruct}}\\;
   \\text{Evidence}
"""
from __future__ import annotations

__version__ = "0.1.0"
__chapter__ = "theory2.tex Ch31"

# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.manifest import (
        ManifestStatus,
        ComponentKind,
        ComponentRecord,
        PackageManifest,
        ManifestValidator,
        PACKAGE_MANIFEST,
    )
    _MANIFEST_AVAILABLE = True
except ImportError:
    _MANIFEST_AVAILABLE = False

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.models import (
        PartialityKind,
        ExceptionKind,
        ReconstructionStatus,
        TrustAnnotationKind,
        PartialFunctionEncoding,
        ExceptionValuedSemantics,
        AlgebraicSurface,
        ModelReconstruction,
        BranchSensitivity,
    )
    _MODELS_AVAILABLE = True
except ImportError:
    _MODELS_AVAILABLE = False

# ---------------------------------------------------------------------------
# partial_functions
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.partial_functions import (
        DomainPredicateKind,
        TotalizationKind,
        CompositionMode,
        DomainPredicate,
        PartialFunctionLattice,
        GuardedEncoding,
        TotalizationStrategy,
        build_domain_predicate,
        encode_partial_as_relation,
        totalize_with_default,
        compose_partial_functions,
    )
    _S01_AVAILABLE = True
except ImportError:
    _S01_AVAILABLE = False

# ---------------------------------------------------------------------------
# exception_semantics
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.exception_semantics import (
        PropagationRule,
        SumTypeKind,
        ExceptionSort,
        MaybeEncoding,
        EitherEncoding,
        ExceptionPropagationGraph,
        encode_maybe_in_z3,
        encode_either_in_z3,
        propagate_exception_strict,
        resolve_handler,
    )
    _S02_AVAILABLE = True
except ImportError:
    _S02_AVAILABLE = False

# ---------------------------------------------------------------------------
# algebraic_surfaces
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.algebraic_surfaces import (
        ConstructorArity,
        SurfaceKind,
        ProjectionMode,
        ConstructorSpec,
        RecognizerPredicate,
        AlgebraicFold,
        SurfaceProjection,
        build_constructor_spec,
        declare_algebraic_surface,
        fold_over_surface,
        project_surface_field,
    )
    _S03_AVAILABLE = True
except ImportError:
    _S03_AVAILABLE = False

# ---------------------------------------------------------------------------
# model_reconstruction
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.model_reconstruction import (
        AssemblyPhase,
        CompletionStrategy,
        ReconstructionPipeline,
        PartialModelAssembler,
        TrustAnnotator,
        EvidencePackager,
        run_full_reconstruction,
        assemble_from_partial_models,
        annotate_with_trust,
    )
    _S04_AVAILABLE = True
except ImportError:
    _S04_AVAILABLE = False

# ---------------------------------------------------------------------------
# algorithms
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.algorithms import (
        AlgorithmStatus,
        MergeStrategy,
        ValidationLevel,
        AlgorithmResult,
        AlgorithmRegistry,
        encode_partial_function,
        decode_z3_model_to_surface,
        reconstruct_evidence_from_model,
        compute_branch_sensitivity,
        totalize_partial,
        merge_reconstructed_models,
        validate_model_faithfulness,
    )
    _ALGORITHMS_AVAILABLE = True
except ImportError:
    _ALGORITHMS_AVAILABLE = False

# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.integration import (
        SessionState,
        BridgeStatus,
        PartialityEncodingSession,
        ModelReconstructionPipeline,
        ExceptionSemanticsBridge,
        CopilotReconstructionAssist,
        create_encoding_session,
        create_reconstruction_pipeline,
        create_exception_bridge,
        create_copilot_assist,
    )
    _INTEGRATION_AVAILABLE = True
except ImportError:
    _INTEGRATION_AVAILABLE = False

# ---------------------------------------------------------------------------
# theorems
# ---------------------------------------------------------------------------
try:
    from jugeo.encodings.partiality_model_reconstruction.theorems import (
        VerificationStatus,
        TheoremKind,
        Theorem,
        TheoremRegistry,
        CopilotTheoremAssist,
        THEOREM_TOTALITY_UNDER_RESTRICTION,
        THEOREM_EXCEPTION_PROPAGATION_MONOTONICITY,
        THEOREM_ALGEBRAIC_SURFACE_FAITHFULNESS,
        THEOREM_MODEL_RECONSTRUCTION_SOUNDNESS,
        THEOREM_BRANCH_SENSITIVITY_CORRECTNESS,
        THEOREM_REGISTRY,
    )
    _THEOREMS_AVAILABLE = True
except ImportError:
    _THEOREMS_AVAILABLE = False

# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------
__all__ = [
    # manifest
    "ManifestStatus", "ComponentKind", "ComponentRecord",
    "PackageManifest", "ManifestValidator", "PACKAGE_MANIFEST",
    # models
    "PartialityKind", "ExceptionKind", "ReconstructionStatus", "TrustAnnotationKind",
    "PartialFunctionEncoding", "ExceptionValuedSemantics", "AlgebraicSurface",
    "ModelReconstruction", "BranchSensitivity",
    # s01
    "DomainPredicateKind", "TotalizationKind", "CompositionMode",
    "DomainPredicate", "PartialFunctionLattice", "GuardedEncoding", "TotalizationStrategy",
    "build_domain_predicate", "encode_partial_as_relation",
    "totalize_with_default", "compose_partial_functions",
    # s02
    "PropagationRule", "SumTypeKind",
    "ExceptionSort", "MaybeEncoding", "EitherEncoding", "ExceptionPropagationGraph",
    "encode_maybe_in_z3", "encode_either_in_z3",
    "propagate_exception_strict", "resolve_handler",
    # s03
    "ConstructorArity", "SurfaceKind", "ProjectionMode",
    "ConstructorSpec", "RecognizerPredicate", "AlgebraicFold", "SurfaceProjection",
    "build_constructor_spec", "declare_algebraic_surface",
    "fold_over_surface", "project_surface_field",
    # s04
    "AssemblyPhase", "CompletionStrategy",
    "ReconstructionPipeline", "PartialModelAssembler", "TrustAnnotator", "EvidencePackager",
    "run_full_reconstruction", "assemble_from_partial_models", "annotate_with_trust",
    # algorithms
    "AlgorithmStatus", "MergeStrategy", "ValidationLevel",
    "AlgorithmResult", "AlgorithmRegistry",
    "encode_partial_function", "decode_z3_model_to_surface",
    "reconstruct_evidence_from_model", "compute_branch_sensitivity",
    "totalize_partial", "merge_reconstructed_models", "validate_model_faithfulness",
    # integration
    "SessionState", "BridgeStatus",
    "PartialityEncodingSession", "ModelReconstructionPipeline",
    "ExceptionSemanticsBridge", "CopilotReconstructionAssist",
    "create_encoding_session", "create_reconstruction_pipeline",
    "create_exception_bridge", "create_copilot_assist",
    # theorems
    "VerificationStatus", "TheoremKind",
    "Theorem", "TheoremRegistry", "CopilotTheoremAssist",
    "THEOREM_TOTALITY_UNDER_RESTRICTION",
    "THEOREM_EXCEPTION_PROPAGATION_MONOTONICITY",
    "THEOREM_ALGEBRAIC_SURFACE_FAITHFULNESS",
    "THEOREM_MODEL_RECONSTRUCTION_SOUNDNESS",
    "THEOREM_BRANCH_SENSITIVITY_CORRECTNESS",
    "THEOREM_REGISTRY",
    # version
    "__version__", "__chapter__",
]


# ---------------------------------------------------------------------------
# Extended API documentation
# ---------------------------------------------------------------------------
#
# The following constants and helper functions provide additional
# introspection and convenience for callers of this package.
# ---------------------------------------------------------------------------

#: Human-readable description of this package's role in the JuGeo system.
PACKAGE_DESCRIPTION: str = (
    "partiality_model_reconstruction implements Ch31 of theory2.tex. "
    "It encodes partial functions as Z3 relations with explicit domain "
    "predicates, provides exception-valued semantics via Z3 sum types, "
    "encodes algebraic data type 'surfaces' in Z3, and reconstructs "
    "JuGeo evidence objects from Z3 model witnesses. The package is "
    "fully integrated with the JuGeo trust algebra: every reconstructed "
    "evidence item carries a trust annotation, and copilot-assisted "
    "completions of partial models are clearly tagged at COPILOT_SUGGESTED "
    "trust level. No silent trust promotion is permitted."
)

#: Summary of the Z3 encoding strategy used by this package.
ENCODING_STRATEGY: str = (
    "Partial functions f: A ⇀ B are encoded as pairs (dom_f, R_f) where "
    "dom_f: A → Bool is the Z3 domain predicate and R_f: A × B → Bool is "
    "the Z3 relation. A hard axiom ∀x. dom_f(x) ⇒ ∃!y. R_f(x,y) is "
    "asserted to enforce functionality. Exception-valued functions are "
    "encoded as Z3 sum types (Maybe / Either). Algebraic data type surfaces "
    "use Z3's native algebraic datatype declarations which natively enforce "
    "constructor injectivity, recognizer exclusivity, and accessor "
    "round-trips. Model reconstruction validates every assignment against "
    "the original query before packaging as JuGeo evidence."
)

#: Mapping from component name to its primary responsibilities.
COMPONENT_RESPONSIBILITIES: dict[str, str] = {
    "manifest": (
        "Provides the package manifest, version metadata, component "
        "registry, and capability declarations. The manifest is the "
        "authoritative source for what this package exports and which "
        "theory2.tex sections it implements."
    ),
    "models": (
        "Defines the five core dataclasses: PartialFunctionEncoding, "
        "ExceptionValuedSemantics, AlgebraicSurface, ModelReconstruction, "
        "and BranchSensitivity. Each class has 5-10 methods implementing "
        "the Ch31 operations. All classes carry trust annotations and "
        "provenance metadata."
    ),
    "partial_functions": (
        "Implements §31.1: domain predicate construction, guard encoding, "
        "totalization strategies, partial function composition, and the "
        "partial function lattice. Classes: DomainPredicate, "
        "PartialFunctionLattice, GuardedEncoding, TotalizationStrategy."
    ),
    "exception_semantics": (
        "Implements §31.2: sum type (Maybe / Either) encoding of "
        "exception-valued functions in Z3, exception propagation rules, "
        "handler resolution, and copilot-assisted exception trace "
        "reconstruction. Classes: ExceptionSort, MaybeEncoding, "
        "EitherEncoding, ExceptionPropagationGraph."
    ),
    "algebraic_surfaces": (
        "Implements §31.3: constructor encoding, recognizer predicates, "
        "accessor functions, pattern matching, fold / unfold operations, "
        "and surface projections. Classes: ConstructorSpec, "
        "RecognizerPredicate, AlgebraicFold, SurfaceProjection."
    ),
    "model_reconstruction": (
        "Implements §31.4: full model reconstruction pipeline — Z3 model "
        "extraction, partial model assembly, trust annotation, evidence "
        "packaging, and copilot-assisted completion of partial models. "
        "Classes: ReconstructionPipeline, PartialModelAssembler, "
        "TrustAnnotator, EvidencePackager."
    ),
    "algorithms": (
        "Core algorithms: encode_partial_function, "
        "decode_z3_model_to_surface, reconstruct_evidence_from_model, "
        "compute_branch_sensitivity, totalize_partial, "
        "merge_reconstructed_models, validate_model_faithfulness."
    ),
    "integration": (
        "Integration with the broader JuGeo system: "
        "PartialityEncodingSession, ModelReconstructionPipeline, "
        "ExceptionSemanticsBridge, CopilotReconstructionAssist. "
        "The copilot integration hook allows the copilot assistant to "
        "propose completions for partial models at COPILOT_SUGGESTED "
        "trust level."
    ),
    "theorems": (
        "Theorem statements from Ch31: totality under restriction, "
        "exception propagation monotonicity, algebraic surface faithfulness, "
        "model reconstruction soundness, branch sensitivity correctness, "
        "composition closure, domain predicate stability, exception handler "
        "determinism, normal form uniqueness for surfaces, and copilot "
        "completion conservativity. Each theorem is a dataclass with "
        "statement, proof sketch, Z3 encoding, and verification status."
    ),
}


def package_info() -> dict[str, object]:
    """Return a dictionary summarising this package.

    Includes version, chapter reference, component responsibilities,
    encoding strategy, and the theorem registry status.

    Returns
    -------
    dict[str, object]
        Human-readable package summary.
    """
    return {
        "package": "jugeo.encodings.partiality_model_reconstruction",
        "version": __version__,
        "chapter": __chapter__,
        "description": PACKAGE_DESCRIPTION,
        "encoding_strategy": ENCODING_STRATEGY,
        "components": list(COMPONENT_RESPONSIBILITIES),
        "component_count": len(COMPONENT_RESPONSIBILITIES),
    }


def list_components() -> list[str]:
    """Return the list of component module names in this package.

    Returns
    -------
    list[str]
        Module names relative to this package.
    """
    return list(COMPONENT_RESPONSIBILITIES)


def describe_component(name: str) -> str:
    """Return the description of a named component.

    Parameters
    ----------
    name:
        Component name, e.g. ``"models"`` or ``"partial_functions"``.

    Returns
    -------
    str
        Description string, or an empty string if not found.
    """
    return COMPONENT_RESPONSIBILITIES.get(name, "")


# ---------------------------------------------------------------------------
# Quickstart helpers
# ---------------------------------------------------------------------------


def quickstart_partial_function(
    name: str,
    domain_sort: str = "Int",
    range_sort: str = "Int",
) -> "PartialFunctionEncoding":
    """Convenience factory for a minimal :class:`PartialFunctionEncoding`.

    This is the recommended entry point for callers who want to quickly
    encode a partial function without configuring all fields manually.

    Parameters
    ----------
    name:
        Human-readable name for the function (e.g. ``"safe_div"``).
    domain_sort:
        Z3 sort name for the domain (default ``"Int"``).
    range_sort:
        Z3 sort name for the range (default ``"Int"``).

    Returns
    -------
    PartialFunctionEncoding
        A freshly constructed encoding with default domain predicate
        and relation names derived from *name*.
    """
    from jugeo.encodings.partiality_model_reconstruction.models import (
        PartialFunctionEncoding,
    )
    return PartialFunctionEncoding(
        name=name,
        domain_sort=domain_sort,
        range_sort=range_sort,
        domain_pred=f"dom_{name}",
        relation=f"rel_{name}",
    )


def quickstart_algebraic_surface(sort_name: str) -> "AlgebraicSurface":
    """Convenience factory for an :class:`AlgebraicSurface`.

    Parameters
    ----------
    sort_name:
        The Z3 sort name for this algebraic data type.

    Returns
    -------
    AlgebraicSurface
    """
    from jugeo.encodings.partiality_model_reconstruction.models import AlgebraicSurface
    return AlgebraicSurface(sort_name=sort_name)


def quickstart_exception_semantics(base_function: str) -> "ExceptionValuedSemantics":
    """Convenience factory for :class:`ExceptionValuedSemantics`.

    Parameters
    ----------
    base_function:
        Name of the underlying partial function.

    Returns
    -------
    ExceptionValuedSemantics
    """
    from jugeo.encodings.partiality_model_reconstruction.models import (
        ExceptionValuedSemantics,
    )
    return ExceptionValuedSemantics(base_function=base_function)


# ---------------------------------------------------------------------------
# Theorem registry convenience re-export
# ---------------------------------------------------------------------------

def get_ch31_theorem(theorem_id: str) -> object:
    """Look up a Ch31 theorem by its identifier.

    Parameters
    ----------
    theorem_id:
        Theorem identifier such as ``"T31.1"`` or
        ``"THEOREM_TOTALITY_UNDER_RESTRICTION"``.

    Returns
    -------
    object
        The theorem object, or ``None`` if not found.
    """
    try:
        from jugeo.encodings.partiality_model_reconstruction.theorems import (
            get_theorem,
        )
        return get_theorem(theorem_id)
    except Exception:
        return None


def ch31_theorem_status() -> str:
    """Return a formatted status report for all Ch31 theorems.

    Returns
    -------
    str
    """
    try:
        from jugeo.encodings.partiality_model_reconstruction.theorems import (
            ch31_status_report,
        )
        return ch31_status_report()
    except Exception:
        return "Ch31 theorem registry unavailable."



# --- auto-registered submodules ---
try:
    from . import algebraic_data_surfaces_without_pr
except Exception:
    pass
try:
    from . import algebraic_surfaces
except Exception:
    pass
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import effect_summaries_and_branch_sensit
except Exception:
    pass
try:
    from . import exception_semantics
except Exception:
    pass
try:
    from . import exception_valued_structural_semant
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
    from . import model_reconstruction
except Exception:
    pass
try:
    from . import model_reconstruction_as_a_first_cl
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import partial_functions
except Exception:
    pass
try:
    from . import reconstruction_witnesses
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import why_python_obligations_are_full_of
except Exception:
    pass
