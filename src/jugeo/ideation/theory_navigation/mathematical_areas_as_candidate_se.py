"""Mathematical areas as candidate semantic regimes.

Every mathematical framework — type theory, category theory, algebraic geometry,
homotopy type theory, sheaf theory, homological algebra, and others — can be
treated as a *candidate semantic regime*: a coherent vocabulary and set of proof
techniques that may be leveraged to phrase, attack, or dissolve a given
obstruction.  This module encodes that perspective in a first-class way.

The central abstraction is the :class:`SemanticRegimeProfile` — a frozen
dataclass that captures the semantic fingerprint of a mathematical area:

* Its canonical keywords (the technical vocabulary of the area).
* The obstruction classes it can handle (e.g. ``'H1_cohomological'``,
  ``'extension_class'``, ``'fibration_path'``).
* The proof techniques it provides (e.g. ``'spectral_sequence'``,
  ``'univalence_transport'``, ``'adjoint_functor_theorem'``).
* Scalar ``relevance_score`` and ``maturity_level`` fields that capture how
  well-established and how useful the area is expected to be for the current
  purpose.

The :class:`MathAreasSemanticRegimesAnalyzer` scores each area against a
purpose keyword list and an obstruction class list, then ranks them.  The
:meth:`~MathAreasSemanticRegimesAnalyzer.compute_compatibility` method
measures the structural overlap between two areas — useful for finding
bridge regimes that can translate proofs between frameworks.

The module ships with :data:`BUILTIN_AREA_PROFILES` — a dictionary of
expert-curated profiles for the 15 mathematical areas defined in the
:class:`MathematicalArea` enum.

Module layout::

    ──────────────────────────────────────────────────────────────────────────
    Symbol                                  Kind        Purpose
    ──────────────────────────────────────────────────────────────────────────
    MathematicalArea                        Enum        canonical 15 areas
    SemanticRegimeProfile                   dataclass   area semantic fingerprint
    RegimeCompatibility                     dataclass   pairwise area overlap
    AreaSelectionResult                     dataclass   final selection + rationale
    MathAreasSemanticRegimesAnalyzer        class       scoring + ranking + compat
    MathAreasSemanticRegimesWitness         class       selection history + stats
    MathAreasSemanticRegimesCoordinator     class       orchestrator / entry-point
    BUILTIN_AREA_PROFILES                   dict        expert-curated profiles
    ──────────────────────────────────────────────────────────────────────────

Private helpers::

    _clamp(v, lo, hi)       clamp float to [lo, hi]
    _now_iso()              current UTC time as ISO-8601 string
    _tokenize(text)         split text into lowercase tokens
    _jaccard(a, b)          Jaccard similarity between two token sets
    _profile_id()           generate a short random profile ID

# copilot: mathematical areas as candidate semantic regimes — area scoring and selection

Reference: theory2.tex §§ semantic regimes, area-based navigation, obstruction handling.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional cross-package imports
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.theory_navigation.models import (
        TheoryNode,
        TheorySpace,
        NavigationStrategy,
        PurposeCondition,
        NodeMaturity,
    )
except ImportError:
    TheoryNode = None  # type: ignore[assignment,misc]
    TheorySpace = None  # type: ignore[assignment,misc]
    NavigationStrategy = None  # type: ignore[assignment,misc]
    PurposeCondition = None  # type: ignore[assignment,misc]
    NodeMaturity = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        Value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        Clamped value.

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.1)
    0.0
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp string.
    """
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Tokenise *text* into a set of lowercase alphabetic tokens (length > 1).

    Parameters
    ----------
    text:
        Raw text to tokenise.

    Returns
    -------
    set[str]
        Normalised token set.

    Examples
    --------
    >>> sorted(_tokenize("Functor, adjunction!"))
    ['adjunction', 'functor']
    """
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9]*", text.lower())
    return {t for t in tokens if len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets.

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        ``|a ∩ b| / |a ∪ b|``, or 0.0 when both are empty.

    Examples
    --------
    >>> _jaccard({"a","b","c"}, {"b","c","d"})
    0.5
    """
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _profile_id() -> str:
    """Generate a short random profile identifier.

    Returns
    -------
    str
        String of the form ``'prof-xxxxxxxx'``.

    Examples
    --------
    >>> pid = _profile_id()
    >>> pid.startswith("prof-")
    True
    """
    return f"prof-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# MathematicalArea enum
# ---------------------------------------------------------------------------


class MathematicalArea(str, Enum):
    """Enumeration of the 15 canonical mathematical areas used as semantic regimes.

    Each member is a string-valued enum whose value is a snake_case identifier
    suitable for use as a dict key or in log messages.

    Members
    -------
    TYPE_THEORY:
        Martin-Löf type theory, dependent types, propositions-as-types.
    CATEGORY_THEORY:
        Functors, natural transformations, adjunctions, limits, colimits.
    ALGEBRAIC_GEOMETRY:
        Schemes, sheaves on varieties, cohomology of coherent sheaves.
    HOMOTOPY_TYPE_THEORY:
        Univalence axiom, higher inductive types, path spaces, fibrations.
    TOPOS_THEORY:
        Elementary toposes, geometric morphisms, internal logic, classifying toposes.
    HOMOLOGICAL_ALGEBRA:
        Chain complexes, derived functors, Ext, Tor, spectral sequences.
    DIFFERENTIAL_GEOMETRY:
        Smooth manifolds, vector bundles, connections, curvature, holonomy.
    REPRESENTATION_THEORY:
        Group representations, modules, characters, Schur's lemma, weight theory.
    COMBINATORICS:
        Enumerative combinatorics, graph theory, generating functions, matroids.
    NUMBER_THEORY:
        Arithmetic, modular forms, L-functions, Galois representations.
    LOGIC:
        First-order and higher-order logic, proof theory, model theory.
    SET_THEORY:
        Axiomatic set theory, large cardinals, forcing, inner models.
    SHEAF_THEORY:
        Sheaves on Grothendieck sites, descent, étale cohomology.
    K_THEORY:
        Algebraic and topological K-theory, Grothendieck group, K-homology.
    MOTIVIC_COHOMOLOGY:
        Motivic cohomology, algebraic cycles, the motivic spectral sequence.
    """

    TYPE_THEORY = "type_theory"
    CATEGORY_THEORY = "category_theory"
    ALGEBRAIC_GEOMETRY = "algebraic_geometry"
    HOMOTOPY_TYPE_THEORY = "homotopy_type_theory"
    TOPOS_THEORY = "topos_theory"
    HOMOLOGICAL_ALGEBRA = "homological_algebra"
    DIFFERENTIAL_GEOMETRY = "differential_geometry"
    REPRESENTATION_THEORY = "representation_theory"
    COMBINATORICS = "combinatorics"
    NUMBER_THEORY = "number_theory"
    LOGIC = "logic"
    SET_THEORY = "set_theory"
    SHEAF_THEORY = "sheaf_theory"
    K_THEORY = "k_theory"
    MOTIVIC_COHOMOLOGY = "motivic_cohomology"


# ---------------------------------------------------------------------------
# Builtin area profiles
# ---------------------------------------------------------------------------

#: Expert-curated area profile data for all 15 mathematical areas.
#: Each entry contains ``keywords``, ``obstruction_handles``, and
#: ``proof_techniques``.  These are used by
#: :meth:`MathAreasSemanticRegimesAnalyzer.build_profile` as a default seed.
BUILTIN_AREA_PROFILES: dict[MathematicalArea, dict[str, Any]] = {
    MathematicalArea.TYPE_THEORY: {
        "name": "Type Theory",
        "description": (
            "Martin-Löf dependent type theory provides a foundation in which "
            "proofs and programs are unified via the propositions-as-types "
            "correspondence.  Dependent products, dependent sums, identity types, "
            "and universe hierarchies are the central constructs."
        ),
        "keywords": (
            "type", "dependent", "proposition", "proof", "term", "formation",
            "introduction", "elimination", "computation", "universe", "pi",
            "sigma", "identity", "refl", "indiscernibility", "judgmental",
            "definitional", "equality", "context", "substitution",
        ),
        "obstruction_handles": (
            "type_mismatch", "proof_term_gap", "dependent_product_obstruction",
            "universe_inconsistency", "definitional_inequality",
        ),
        "proof_techniques": (
            "induction_principle", "recursion", "pattern_matching",
            "type_checking", "normalisation", "canonicity",
            "proof_by_reflection", "universe_polymorphism",
        ),
        "maturity_level": 0.92,
    },
    MathematicalArea.CATEGORY_THEORY: {
        "name": "Category Theory",
        "description": (
            "Category theory provides a unifying language for modern mathematics "
            "through the study of objects, morphisms, functors, and natural "
            "transformations.  Universal properties, adjunctions, limits, and "
            "colimits are the primary organisational tools."
        ),
        "keywords": (
            "functor", "morphism", "category", "natural", "transformation",
            "adjunction", "limit", "colimit", "product", "coproduct",
            "pullback", "pushout", "equaliser", "coequaliser", "monoid",
            "monad", "comonad", "enriched", "internal", "profunctor",
            "kan", "extension", "yoneda", "representable", "presheaf",
        ),
        "obstruction_handles": (
            "naturality_obstruction", "adjunction_failure", "universal_property_gap",
            "commutativity_obstruction", "coherence_obstruction",
        ),
        "proof_techniques": (
            "adjoint_functor_theorem", "yoneda_lemma", "limit_construction",
            "kan_extension", "beck_monadicity", "coherence_theorem",
            "universal_arrow", "end_coend_calculus", "string_diagrams",
        ),
        "maturity_level": 0.95,
    },
    MathematicalArea.ALGEBRAIC_GEOMETRY: {
        "name": "Algebraic Geometry",
        "description": (
            "Algebraic geometry studies the geometry of solution sets of polynomial "
            "equations via the language of schemes, sheaves, cohomology, and "
            "morphisms of varieties.  Grothendieck's reformulation unified "
            "classical algebraic geometry with arithmetic."
        ),
        "keywords": (
            "scheme", "variety", "sheaf", "cohomology", "morphism", "fibre",
            "bundle", "divisor", "line", "section", "global", "local",
            "affine", "projective", "smooth", "singular", "resolution",
            "blowup", "intersection", "chow", "cycle", "motive", "etale",
            "grothendieck", "ringed", "space",
        ),
        "obstruction_handles": (
            "cohomological_obstruction", "scheme_obstruction",
            "deformation_obstruction", "lifting_obstruction", "extension_problem",
        ),
        "proof_techniques": (
            "cohomology_vanishing", "serre_duality", "riemann_roch",
            "descent_theory", "base_change", "flat_morphism_argument",
            "resolution_of_singularities", "blowup_sequence",
            "chern_class_computation", "intersection_theory",
        ),
        "maturity_level": 0.93,
    },
    MathematicalArea.HOMOTOPY_TYPE_THEORY: {
        "name": "Homotopy Type Theory",
        "description": (
            "Homotopy type theory (HoTT) interprets types as spaces and equality as "
            "paths, providing a constructive foundation for mathematics in which "
            "the univalence axiom and higher inductive types play central roles.  "
            "It bridges type theory, homotopy theory, and ∞-groupoid theory."
        ),
        "keywords": (
            "homotopy", "path", "fibration", "univalence", "truncation",
            "higher", "inductive", "hit", "circle", "sphere", "suspension",
            "loop", "transport", "apd", "identity", "equiv", "isequiv",
            "funext", "proptrunc", "settrunc", "mere", "proposition",
            "connected", "n-type", "groupoid",
        ),
        "obstruction_handles": (
            "path_obstruction", "fibration_lifting_failure",
            "univalence_application_gap", "higher_coherence_obstruction",
            "truncation_mismatch",
        ),
        "proof_techniques": (
            "path_induction", "transport_along_equivalence", "univalence_transport",
            "encode_decode_method", "cubical_reasoning",
            "flattening_lemma", "blakers_massey", "freudenthal_suspension",
            "hit_recursion", "hit_induction",
        ),
        "maturity_level": 0.82,
    },
    MathematicalArea.HOMOLOGICAL_ALGEBRA: {
        "name": "Homological Algebra",
        "description": (
            "Homological algebra studies algebraic structures using chain complexes, "
            "derived functors, and long exact sequences.  The Ext and Tor functors, "
            "spectral sequences, and derived categories are central tools for "
            "measuring obstructions to exactness and liftings."
        ),
        "keywords": (
            "complex", "chain", "exact", "sequence", "derived", "functor",
            "ext", "tor", "hom", "projective", "injective", "flat",
            "resolution", "cohomology", "homology", "spectral", "filtration",
            "differential", "graded", "dga", "triangulated", "derived",
            "category", "mapping", "cone", "fiber",
        ),
        "obstruction_handles": (
            "extension_class", "derived_functor_obstruction",
            "exact_sequence_gap", "splitting_obstruction",
            "Ext1_obstruction",
        ),
        "proof_techniques": (
            "long_exact_sequence", "horseshoe_lemma", "snake_lemma",
            "spectral_sequence_argument", "hypercohomology",
            "derived_base_change", "flat_resolution",
            "injective_resolution", "comparison_theorem",
        ),
        "maturity_level": 0.94,
    },
    MathematicalArea.SHEAF_THEORY: {
        "name": "Sheaf Theory",
        "description": (
            "Sheaf theory on Grothendieck sites provides the natural language for "
            "descent, gluing, and cohomology in modern algebraic geometry and "
            "topology.  Étale and flat cohomology, Čech cohomology, and the "
            "six-functor formalism are flagship applications."
        ),
        "keywords": (
            "sheaf", "presheaf", "topos", "site", "grothendieck", "topology",
            "gluing", "descent", "etale", "flat", "fppf", "zariski",
            "cech", "coboundary", "cocycle", "local", "global", "section",
            "stalk", "gerbe", "stack", "higher", "infinity", "direct",
            "image", "inverse",
        ),
        "obstruction_handles": (
            "sheaf_obstruction", "gluing_condition_failure",
            "descent_obstruction", "cech_cohomology_obstruction",
            "gerbe_obstruction",
        ),
        "proof_techniques": (
            "cech_computation", "leray_spectral_sequence",
            "cohomological_descent", "base_change_for_sheaves",
            "etale_proper_base_change", "smooth_base_change",
            "proper_direct_image", "dualising_complex",
        ),
        "maturity_level": 0.91,
    },
    MathematicalArea.TOPOS_THEORY: {
        "name": "Topos Theory",
        "description": (
            "Topos theory studies categories that behave like the category of sets — "
            "having a subobject classifier and being cartesian closed.  Elementary "
            "toposes provide internal logic (higher-order intuitionistic logic) and "
            "Grothendieck toposes classify geometric theories."
        ),
        "keywords": (
            "topos", "subobject", "classifier", "cartesian", "closed",
            "geometric", "morphism", "internal", "logic", "classifying",
            "locale", "point", "surjection", "inclusion", "essential",
            "atomic", "boolean", "two-valued", "localic", "hyperconnected",
        ),
        "obstruction_handles": (
            "internal_logic_obstruction", "subobject_classifier_gap",
            "geometric_morphism_obstruction", "classifying_topos_failure",
        ),
        "proof_techniques": (
            "internal_language_argument", "classifying_topos_construction",
            "geometric_theory_axiomatisation", "localisation",
            "comparison_with_sets", "barr_theorem",
        ),
        "maturity_level": 0.88,
    },
    MathematicalArea.DIFFERENTIAL_GEOMETRY: {
        "name": "Differential Geometry",
        "description": (
            "Differential geometry studies smooth manifolds equipped with additional "
            "geometric structures such as vector bundles, connections, curvature, and "
            "holonomy.  It provides the language of Riemannian geometry, gauge theory, "
            "and symplectic geometry."
        ),
        "keywords": (
            "manifold", "smooth", "tangent", "cotangent", "bundle", "vector",
            "connection", "curvature", "holonomy", "form", "differential",
            "de-rham", "stokes", "riemannian", "metric", "geodesic",
            "lie", "group", "algebra", "symplectic", "contact",
        ),
        "obstruction_handles": (
            "curvature_obstruction", "holonomy_obstruction",
            "integrability_obstruction", "flat_connection_failure",
        ),
        "proof_techniques": (
            "parallel_transport", "curvature_computation",
            "de_rham_cohomology", "stokes_theorem",
            "chern_weil_theory", "index_theorem", "lie_derivative",
        ),
        "maturity_level": 0.92,
    },
    MathematicalArea.REPRESENTATION_THEORY: {
        "name": "Representation Theory",
        "description": (
            "Representation theory studies how algebraic objects (groups, algebras, "
            "Lie algebras) act on vector spaces.  Characters, weights, modules, "
            "and cohomology of representations are central tools."
        ),
        "keywords": (
            "representation", "module", "character", "weight", "root",
            "lie", "group", "algebra", "induction", "restriction",
            "schur", "lemma", "semisimple", "irreducible", "decomposition",
            "tensor", "product", "ext", "cohomology",
        ),
        "obstruction_handles": (
            "representation_obstruction", "character_sum_failure",
            "extension_of_modules", "cohomological_vanishing_failure",
        ),
        "proof_techniques": (
            "character_theory", "weyl_character_formula",
            "schur_functor", "branching_rule", "kostant_formula",
            "bgg_resolution", "cohomological_argument",
        ),
        "maturity_level": 0.90,
    },
    MathematicalArea.K_THEORY: {
        "name": "K-Theory",
        "description": (
            "K-theory studies vector bundles and their stable equivalence classes. "
            "Algebraic K-theory, topological K-theory, and motivic K-theory each "
            "provide invariants of rings, spaces, and schemes respectively."
        ),
        "keywords": (
            "k-theory", "grothendieck", "group", "vector", "bundle", "stable",
            "equivalence", "bott", "periodicity", "adams", "operation",
            "algebraic", "topological", "motivic", "milnor", "quillen",
        ),
        "obstruction_handles": (
            "k_theoretic_obstruction", "stable_isomorphism_failure",
            "grothendieck_group_extension",
        ),
        "proof_techniques": (
            "k_group_computation", "bott_periodicity", "adams_operations",
            "k_theory_localization", "exact_triangle",
        ),
        "maturity_level": 0.87,
    },
    MathematicalArea.COMBINATORICS: {
        "name": "Combinatorics",
        "description": (
            "Combinatorics is the study of finite structures, counting problems, "
            "and discrete mathematics.  Generating functions, bijections, and "
            "algebraic combinatorics are key tools."
        ),
        "keywords": (
            "combinatorics", "generating", "function", "bijection", "counting",
            "partition", "permutation", "graph", "matroid", "poset",
            "lattice", "polytope", "symmetric", "function", "tableau",
        ),
        "obstruction_handles": (
            "counting_obstruction", "bijection_gap", "non_existence_certificate",
        ),
        "proof_techniques": (
            "generating_function_argument", "bijective_proof",
            "inclusion_exclusion", "mobius_inversion", "transfer_matrix",
        ),
        "maturity_level": 0.93,
    },
    MathematicalArea.NUMBER_THEORY: {
        "name": "Number Theory",
        "description": (
            "Number theory studies properties of integers and related structures. "
            "Modular forms, L-functions, and Galois representations are central "
            "to modern arithmetic geometry."
        ),
        "keywords": (
            "number", "prime", "integer", "modular", "form", "l-function",
            "galois", "representation", "arith", "p-adic", "adele",
            "conductor", "automorphic", "hecke", "langlands",
        ),
        "obstruction_handles": (
            "arithmetic_obstruction", "local_global_failure", "ramification_obstruction",
        ),
        "proof_techniques": (
            "modular_symbol", "l_function_argument", "sieve_method",
            "p_adic_method", "class_field_theory",
        ),
        "maturity_level": 0.94,
    },
    MathematicalArea.LOGIC: {
        "name": "Logic",
        "description": (
            "Mathematical logic studies formal proof systems, models, and "
            "computability.  Proof theory, model theory, and recursion theory "
            "underpin the foundations of mathematics."
        ),
        "keywords": (
            "logic", "proof", "model", "formula", "theory", "completeness",
            "soundness", "consistency", "axiom", "rule", "inference",
            "sequent", "natural", "deduction", "type",
        ),
        "obstruction_handles": (
            "consistency_obstruction", "incompleteness_obstacle", "undecidability",
        ),
        "proof_techniques": (
            "proof_search", "cut_elimination", "completeness_theorem",
            "compactness_argument", "ultraproduct",
        ),
        "maturity_level": 0.96,
    },
    MathematicalArea.SET_THEORY: {
        "name": "Set Theory",
        "description": (
            "Set theory provides one of the standard foundations for mathematics. "
            "Large cardinals, forcing, inner models, and determinacy are central "
            "topics in modern descriptive and combinatorial set theory."
        ),
        "keywords": (
            "set", "cardinal", "ordinal", "forcing", "model", "inner",
            "large", "measurable", "inaccessible", "zfc", "ac",
            "continuum", "hypothesis", "determinacy",
        ),
        "obstruction_handles": (
            "independence_obstruction", "large_cardinal_requirement",
            "forcing_extension_needed",
        ),
        "proof_techniques": (
            "forcing", "absoluteness_argument", "inner_model_construction",
            "reflection_principle",
        ),
        "maturity_level": 0.90,
    },
    MathematicalArea.MOTIVIC_COHOMOLOGY: {
        "name": "Motivic Cohomology",
        "description": (
            "Motivic cohomology provides a universal cohomology theory for algebraic "
            "varieties, serving as the source of many specialised cohomology theories "
            "via realisation functors.  The motivic spectral sequence connects it to "
            "algebraic K-theory."
        ),
        "keywords": (
            "motivic", "cohomology", "cycle", "algebraic", "k-theory",
            "realisation", "mixed", "motive", "bloch", "cycle", "complex",
            "milnor", "bloch-kato", "spectral", "sequence",
        ),
        "obstruction_handles": (
            "motivic_obstruction", "realisation_failure", "cycle_obstruction",
        ),
        "proof_techniques": (
            "motivic_spectral_sequence", "realisation_functor",
            "bloch_higher_chow_computation", "milnor_k_theory",
        ),
        "maturity_level": 0.78,
    },
}

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticRegimeProfile:
    """Semantic fingerprint of a mathematical area used as a candidate regime.

    Attributes
    ----------
    profile_id:
        Unique identifier, e.g. ``'prof-abcd1234'``.
    area:
        The :class:`MathematicalArea` this profile describes.
    name:
        Human-readable name, e.g. ``'Homotopy Type Theory'``.
    keywords:
        Tuple of canonical technical vocabulary tokens.
    obstruction_handles:
        Tuple of obstruction class labels this area can address.
    proof_techniques:
        Tuple of proof-technique labels this area provides.
    relevance_score:
        Score in [0, 1] measuring alignment with the current purpose keywords.
        Computed by :meth:`MathAreasSemanticRegimesAnalyzer.build_profile`.
    maturity_level:
        Expert-curated score in [0, 1] indicating how well-established this
        area is as a proof vehicle.
    description:
        Free-text summary of the area.
    """

    profile_id: str
    area: MathematicalArea
    name: str
    keywords: tuple[str, ...]
    obstruction_handles: tuple[str, ...]
    proof_techniques: tuple[str, ...]
    relevance_score: float
    maturity_level: float
    description: str


@dataclass(frozen=True, slots=True)
class RegimeCompatibility:
    """Pairwise structural compatibility between two semantic regime profiles.

    Attributes
    ----------
    regime_a:
        Name (or profile_id) of the first regime.
    regime_b:
        Name (or profile_id) of the second regime.
    compatibility_score:
        Jaccard overlap of the two profiles' combined vocabularies.  In [0, 1].
    shared_techniques:
        Tuple of proof technique labels shared by both profiles.
    bridging_concepts:
        Tuple of keyword tokens appearing in both profiles' keyword sets.
    """

    regime_a: str
    regime_b: str
    compatibility_score: float
    shared_techniques: tuple[str, ...]
    bridging_concepts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AreaSelectionResult:
    """The result of selecting the best mathematical area for the current purpose.

    Attributes
    ----------
    selection_id:
        Unique identifier for this selection event.
    selected_area:
        The chosen :class:`MathematicalArea`.
    profile:
        The :class:`SemanticRegimeProfile` for the selected area.
    score:
        The composite score that led to selection.
    alternatives:
        Tuple of names of the other areas that were ranked but not selected.
    rationale:
        Human-readable explanation of the selection.
    timestamp:
        ISO-8601 UTC timestamp of the selection.
    """

    selection_id: str
    selected_area: MathematicalArea
    profile: SemanticRegimeProfile
    score: float
    alternatives: tuple[str, ...]
    rationale: str
    timestamp: str


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class MathAreasSemanticRegimesAnalyzer:
    """Score, rank, and select mathematical areas as candidate semantic regimes.

    The analyzer is stateless and all methods are pure functions of their
    inputs (apart from a small LRU-style compatibility cache).

    Parameters
    ----------
    builtin_profiles:
        Override the builtin profile data.  Defaults to
        :data:`BUILTIN_AREA_PROFILES`.

    Examples
    --------
    >>> analyzer = MathAreasSemanticRegimesAnalyzer()
    >>> profiles = analyzer.rank_areas(["functor", "adjunction"], [])
    >>> profiles[0].area in list(MathematicalArea)
    True
    """

    def __init__(
        self,
        builtin_profiles: dict[MathematicalArea, dict[str, Any]] | None = None,
    ) -> None:
        """Initialise the analyzer.

        Parameters
        ----------
        builtin_profiles:
            If provided, used instead of :data:`BUILTIN_AREA_PROFILES`.
        """
        self._profiles: dict[MathematicalArea, dict[str, Any]] = (
            builtin_profiles if builtin_profiles is not None else BUILTIN_AREA_PROFILES
        )
        self._compat_cache: dict[tuple[str, str], RegimeCompatibility] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_profile(
        self,
        area: MathematicalArea,
        purpose_keywords: list[str],
    ) -> SemanticRegimeProfile:
        """Construct a :class:`SemanticRegimeProfile` for *area* scored against *purpose_keywords*.

        The ``relevance_score`` is computed as the Jaccard similarity between
        the area's canonical keyword set and the purpose keyword set.

        Parameters
        ----------
        area:
            The :class:`MathematicalArea` to build a profile for.
        purpose_keywords:
            List of keyword strings derived from the current purpose.

        Returns
        -------
        SemanticRegimeProfile
            A fully populated and scored profile.

        Raises
        ------
        KeyError
            If *area* is not found in the builtin profiles dict.

        Examples
        --------
        >>> analyzer = MathAreasSemanticRegimesAnalyzer()
        >>> p = analyzer.build_profile(MathematicalArea.CATEGORY_THEORY, ["functor"])
        >>> p.area == MathematicalArea.CATEGORY_THEORY
        True
        """
        raw = self._profiles[area]
        purpose_set = _tokenize(" ".join(purpose_keywords))
        kw_set = set(raw["keywords"])
        relevance = _jaccard(purpose_set, kw_set)

        return SemanticRegimeProfile(
            profile_id=_profile_id(),
            area=area,
            name=str(raw.get("name", area.value)),
            keywords=tuple(raw["keywords"]),
            obstruction_handles=tuple(raw.get("obstruction_handles", ())),
            proof_techniques=tuple(raw.get("proof_techniques", ())),
            relevance_score=round(_clamp(relevance), 6),
            maturity_level=round(_clamp(float(raw.get("maturity_level", 0.8))), 6),
            description=str(raw.get("description", "")),
        )

    def rank_areas(
        self,
        purpose_keywords: list[str],
        obstruction_classes: list[str],
    ) -> list[SemanticRegimeProfile]:
        """Build profiles for all 15 areas and rank them by a composite score.

        The composite score is:

            ``0.6 * relevance_score + 0.25 * obstruction_coverage + 0.15 * maturity_level``

        where ``obstruction_coverage`` is the fraction of *obstruction_classes*
        that appear in the area's ``obstruction_handles`` tuple.

        Parameters
        ----------
        purpose_keywords:
            Keyword tokens from the current purpose condition.
        obstruction_classes:
            List of obstruction class labels that must be resolved.

        Returns
        -------
        list[SemanticRegimeProfile]
            All 15 profiles, sorted by composite score (descending).
            Ties broken alphabetically by area name.
        """
        profiles: list[SemanticRegimeProfile] = []
        obs_set = set(obstruction_classes)

        for area in MathematicalArea:
            try:
                profile = self.build_profile(area, purpose_keywords)
            except KeyError:
                logger.warning("No builtin profile found for area=%s", area.value)
                continue

            # Obstruction coverage
            handles_set = set(profile.obstruction_handles)
            if obs_set:
                coverage = len(obs_set & handles_set) / len(obs_set)
            else:
                coverage = 0.0

            composite = _clamp(
                0.6 * profile.relevance_score
                + 0.25 * coverage
                + 0.15 * profile.maturity_level
            )
            # Attach composite as relevance (profiles are frozen; replace)
            profiles.append(
                SemanticRegimeProfile(
                    profile_id=profile.profile_id,
                    area=profile.area,
                    name=profile.name,
                    keywords=profile.keywords,
                    obstruction_handles=profile.obstruction_handles,
                    proof_techniques=profile.proof_techniques,
                    relevance_score=round(composite, 6),
                    maturity_level=profile.maturity_level,
                    description=profile.description,
                )
            )

        profiles.sort(key=lambda p: (-p.relevance_score, p.name))
        return profiles

    def compute_compatibility(
        self,
        profile_a: SemanticRegimeProfile,
        profile_b: SemanticRegimeProfile,
    ) -> RegimeCompatibility:
        """Compute pairwise compatibility between two semantic regime profiles.

        Compatibility is measured as the Jaccard similarity of the union of
        each profile's keywords and obstruction_handles.

        Parameters
        ----------
        profile_a:
            First :class:`SemanticRegimeProfile`.
        profile_b:
            Second :class:`SemanticRegimeProfile`.

        Returns
        -------
        RegimeCompatibility
            A compatibility record with shared techniques and bridging concepts.

        Notes
        -----
        Results are cached by ``(profile_a.name, profile_b.name)`` to avoid
        redundant computation when ranking many profiles.
        """
        key = (profile_a.name, profile_b.name)
        if key in self._compat_cache:
            return self._compat_cache[key]

        kw_a = set(profile_a.keywords) | set(profile_a.obstruction_handles)
        kw_b = set(profile_b.keywords) | set(profile_b.obstruction_handles)
        score = _jaccard(kw_a, kw_b)

        shared_tech = tuple(
            sorted(set(profile_a.proof_techniques) & set(profile_b.proof_techniques))
        )
        bridging = tuple(sorted(set(profile_a.keywords) & set(profile_b.keywords)))

        compat = RegimeCompatibility(
            regime_a=profile_a.name,
            regime_b=profile_b.name,
            compatibility_score=round(_clamp(score), 6),
            shared_techniques=shared_tech,
            bridging_concepts=bridging,
        )
        self._compat_cache[key] = compat
        return compat

    def select_best(
        self,
        profiles: list[SemanticRegimeProfile],
        purpose: str,
    ) -> AreaSelectionResult:
        """Select the best area from a ranked list of profiles.

        The best profile is simply the first element of *profiles* (highest
        composite score).  A human-readable rationale is generated.

        Parameters
        ----------
        profiles:
            Pre-ranked list of :class:`SemanticRegimeProfile` instances
            (best first).
        purpose:
            Free-form purpose string (used for rationale generation).

        Returns
        -------
        AreaSelectionResult
            The selection result including rationale and alternatives list.

        Raises
        ------
        ValueError
            If *profiles* is empty.
        """
        if not profiles:
            raise ValueError("profiles list is empty — cannot select best area")

        best = profiles[0]
        alternatives = tuple(p.name for p in profiles[1:6])

        rationale_lines = [
            f"Selected area: '{best.name}' (composite_score={best.relevance_score:.4f}).",
            f"Obstruction handles: {', '.join(best.obstruction_handles[:3]) or 'none'}.",
            f"Key proof techniques: {', '.join(best.proof_techniques[:3]) or 'none'}.",
            f"Purpose: {purpose[:80]!r}.",
            f"Alternatives considered: {', '.join(alternatives) or 'none'}.",
        ]
        rationale = "  ".join(rationale_lines)

        return AreaSelectionResult(
            selection_id=f"sel-{uuid.uuid4().hex[:8]}",
            selected_area=best.area,
            profile=best,
            score=best.relevance_score,
            alternatives=alternatives,
            rationale=rationale,
            timestamp=_now_iso(),
        )

    def explain_selection(self, result: AreaSelectionResult) -> str:
        """Produce a detailed human-readable explanation of an :class:`AreaSelectionResult`.

        Parameters
        ----------
        result:
            A completed selection result.

        Returns
        -------
        str
            Multi-paragraph explanation string.
        """
        p = result.profile
        lines = [
            f"Area Selection Explanation",
            f"==========================",
            f"Selected:  {result.selected_area.value} ('{p.name}')",
            f"Score:     {result.score:.4f}",
            f"Maturity:  {p.maturity_level:.4f}",
            f"",
            f"Keywords ({len(p.keywords)}): {', '.join(p.keywords[:8])}{'...' if len(p.keywords) > 8 else ''}",
            f"",
            f"Obstruction handles: {', '.join(p.obstruction_handles)}",
            f"",
            f"Proof techniques: {', '.join(p.proof_techniques[:5])}",
            f"",
            f"Description: {p.description[:200]}",
            f"",
            f"Alternatives: {', '.join(result.alternatives) or '(none)'}",
            f"",
            f"Rationale: {result.rationale}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class MathAreasSemanticRegimesWitness:
    """Accumulates :class:`AreaSelectionResult` objects and provides statistics.

    The witness tracks which mathematical areas have been selected and how
    often, enabling coverage analysis and favourite-area reporting.

    Examples
    --------
    >>> witness = MathAreasSemanticRegimesWitness()
    >>> analyzer = MathAreasSemanticRegimesAnalyzer()
    >>> result = analyzer.select_best(
    ...     analyzer.rank_areas(["functor"], []), "prove adjunction"
    ... )
    >>> witness.record(result)
    >>> witness.favorite_area() == result.selected_area
    True
    """

    def __init__(self) -> None:
        """Initialise an empty witness."""
        self._results: list[AreaSelectionResult] = []

    def record(self, result: AreaSelectionResult) -> None:
        """Append *result* to the history.

        Parameters
        ----------
        result:
            A :class:`AreaSelectionResult` to record.
        """
        self._results.append(result)
        logger.debug(
            "Witness recorded area selection: area=%s score=%.4f",
            result.selected_area.value,
            result.score,
        )

    def coverage(self) -> dict[str, int]:
        """Return a dict mapping area names to selection counts.

        Returns
        -------
        dict[str, int]
            ``{area_name: count}`` for all areas that have been selected at
            least once, sorted by count descending.
        """
        counts: dict[str, int] = {}
        for r in self._results:
            name = r.profile.name
            counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def favorite_area(self) -> MathematicalArea | None:
        """Return the most frequently selected :class:`MathematicalArea`, or *None*.

        Returns
        -------
        MathematicalArea | None
            The area with the highest selection count, or ``None`` if empty.
        """
        if not self._results:
            return None
        counts: dict[MathematicalArea, int] = {}
        for r in self._results:
            counts[r.selected_area] = counts.get(r.selected_area, 0) + 1
        return max(counts, key=lambda a: counts[a])

    def export(self) -> list[dict[str, Any]]:
        """Serialise all recorded selection results to a list of plain dicts.

        Returns
        -------
        list[dict[str, Any]]
            Each entry contains ``selection_id``, ``area``, ``name``,
            ``score``, ``alternatives``, ``timestamp``.
        """
        return [
            {
                "selection_id": r.selection_id,
                "area": r.selected_area.value,
                "name": r.profile.name,
                "score": r.score,
                "alternatives": list(r.alternatives),
                "timestamp": r.timestamp,
            }
            for r in self._results
        ]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class MathAreasSemanticRegimesCoordinator:
    """Orchestrate the area-ranking and selection pipeline in a single call.

    Parameters
    ----------
    builtin_profiles:
        Optional override for the builtin profile data.

    Examples
    --------
    >>> coord = MathAreasSemanticRegimesCoordinator()
    >>> result = coord.run(["functor", "morphism"], [], "prove naturality")
    >>> result.selected_area in list(MathematicalArea)
    True
    """

    def __init__(
        self,
        builtin_profiles: dict[MathematicalArea, dict[str, Any]] | None = None,
    ) -> None:
        """Initialise the coordinator."""
        self._analyzer: MathAreasSemanticRegimesAnalyzer = MathAreasSemanticRegimesAnalyzer(
            builtin_profiles
        )
        self._witness: MathAreasSemanticRegimesWitness = MathAreasSemanticRegimesWitness()

    def run(
        self,
        purpose_keywords: list[str],
        obstruction_classes: list[str],
        purpose: str,
    ) -> AreaSelectionResult:
        """Rank all areas and select the best one for the given purpose.

        Parameters
        ----------
        purpose_keywords:
            Keyword tokens from the current purpose.
        obstruction_classes:
            Obstruction class labels to resolve.
        purpose:
            Free-form purpose string for rationale generation.

        Returns
        -------
        AreaSelectionResult
            The selected area with score, rationale, and alternatives.
        """
        profiles = self._analyzer.rank_areas(purpose_keywords, obstruction_classes)
        result = self._analyzer.select_best(profiles, purpose)
        self._witness.record(result)
        return result

    def report(self) -> dict[str, Any]:
        """Return a structured report of all selections made so far.

        Returns
        -------
        dict[str, Any]
            Report with keys: ``total_selections``, ``coverage``,
            ``favorite_area``, ``selections``.
        """
        fav = self._witness.favorite_area()
        return {
            "total_selections": len(self._witness._results),
            "coverage": self._witness.coverage(),
            "favorite_area": fav.value if fav else None,
            "selections": self._witness.export(),
        }


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Mathematical Areas as Candidate Semantic Regimes ===\n")

    coord = MathAreasSemanticRegimesCoordinator()

    scenarios = [
        (["functor", "adjunction", "morphism", "natural", "transformation"],
         ["naturality_obstruction"],
         "prove naturality of the constructed transformation"),
        (["path", "fibration", "homotopy", "univalence", "higher"],
         ["path_obstruction", "fibration_lifting_failure"],
         "resolve H1 fibration obstruction using path-space arguments"),
        (["sheaf", "cohomology", "gluing", "etale", "descent"],
         ["gluing_condition_failure", "descent_obstruction"],
         "compute étale cohomology and verify gluing"),
        (["derived", "functor", "ext", "exact", "sequence", "complex"],
         ["extension_class", "Ext1_obstruction"],
         "compute Ext1 and resolve the extension class"),
    ]

    for i, (kws, obs, purpose) in enumerate(scenarios, start=1):
        result = coord.run(kws, obs, purpose)
        print(f"Scenario {i}: {purpose!r}")
        print(f"  → Selected: {result.selected_area.value} (score={result.score:.4f})")
        print(f"  → Alternatives: {', '.join(result.alternatives[:3])}")
        print()

    report = coord.report()
    print(f"Report: total_selections={report['total_selections']}, "
          f"favorite_area={report['favorite_area']}")
    print(f"Coverage: {report['coverage']}")

    # Compatibility demo
    analyzer = MathAreasSemanticRegimesAnalyzer()
    pa = analyzer.build_profile(MathematicalArea.HOMOLOGICAL_ALGEBRA, [])
    pb = analyzer.build_profile(MathematicalArea.ALGEBRAIC_GEOMETRY, [])
    compat = analyzer.compute_compatibility(pa, pb)
    print(f"\nCompatibility {pa.name} ↔ {pb.name}: {compat.compatibility_score:.4f}")
    print(f"  Bridging concepts: {list(compat.bridging_concepts[:6])}")

    print("\nSmoke-test PASSED.")
