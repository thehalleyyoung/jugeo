"""Catalog of all 128 mathematical field nodes for the synthesis tournament.
# copilot: synthesis frontier fields catalog -- 128 mathematical field nodes
"""
from __future__ import annotations

import logging
import re
import uuid

_log = logging.getLogger(__name__)

try:
    from jugeo.ideation.synthesis_frontier.models import (
        DomainArea,
        FieldNode,
        PropositionKind,
        PropositionRecord,
    )
    _USING_STUBS = False
except ImportError:
    _log.warning("jugeo models not importable; using stubs")
    import time
    from dataclasses import dataclass, field as _dc_field
    from enum import Enum

    _USING_STUBS = True

    class DomainArea(str, Enum):  # type: ignore[no-redef]
        MATHEMATICS = "mathematics"
        COMPUTER_SCIENCE = "computer_science"
        PHYSICS = "physics"
        LOGIC = "logic"
        STATISTICS = "statistics"
        THEORETICAL_ECONOMICS = "theoretical_economics"
        LINGUISTICS = "linguistics"
        PHILOSOPHY_OF_MIND = "philosophy_of_mind"

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        AXIOM = "axiom"
        THEOREM = "theorem"
        LEMMA = "lemma"
        COROLLARY = "corollary"
        CONJECTURE = "conjecture"
        DEFINITION = "definition"
        EXAMPLE = "example"
        BRIDGE_THEOREM = "bridge_theorem"
        SYNTHESIS_RESULT = "synthesis_result"
        PROPOSITION = "proposition"
        REMARK = "remark"
        CONSTRUCTION = "construction"

    @dataclass(frozen=True)
    class PropositionRecord:  # type: ignore[no-redef]
        prop_id: str
        kind: object
        title: str
        statement: str
        proof_sketch: str
        why_useful: str
        source_field: str
        target_fields: tuple
        metaphor_tags: tuple
        trust_tier: str
        leverage_score: float
        proof_difficulty: str
        dependencies: tuple
        judgment_coordinate: str
        metadata: dict = _dc_field(default_factory=dict)

        def summary(self) -> str:
            return f"[{self.kind}] {self.title}: {self.statement[:80]}"

    @dataclass
    class FieldNode:  # type: ignore[no-redef]
        field_id: str
        name: str
        domain: object
        description: str
        core_objects: tuple
        core_morphisms: tuple
        key_theorems: tuple
        propositions: list = _dc_field(default_factory=list)
        metaphor_links: list = _dc_field(default_factory=list)
        judgment_site: dict = _dc_field(default_factory=dict)
        round_number: int = 0
        constituent_fields: tuple = ()
        llm_summary: str = ""
        trust_tier: str = "PROPOSAL"
        metadata: dict = _dc_field(default_factory=dict)

        def proposition_count(self) -> int:
            return len(self.propositions)

        def summary_line(self) -> str:
            return (
                f"FieldNode({self.name!r}, round={self.round_number}, "
                f"props={self.proposition_count()}, "
                f"constituents={len(self.constituent_fields)})"
            )

        def top_propositions(self, n: int = 10) -> list:
            return sorted(self.propositions, key=lambda p: -p.leverage_score)[:n]


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert a field name to a stable slug identifier."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")[:40]


def _field(name: str, description: str, props: list, keywords: tuple, judgment_site: str) -> FieldNode:
    """Create a leaf FieldNode with the given name, description, and propositions."""
    slug = _slugify(name)
    js: dict = {"notes": judgment_site} if isinstance(judgment_site, str) else judgment_site
    return FieldNode(
        field_id=slug,
        name=name,
        domain=DomainArea.MATHEMATICS,
        description=description,
        core_objects=tuple(keywords[:5]),
        core_morphisms=(),
        key_theorems=(),
        propositions=list(props),
        judgment_site=js,
        round_number=0,
        constituent_fields=(slug,),
    )


def _prop(
    title: str,
    statement: str,
    kind: object,
    field_id: str,
    importance: float = 0.7,
    tags: tuple = (),
) -> PropositionRecord:
    """Create a PropositionRecord with sensible defaults."""
    is_defn = getattr(kind, "value", kind) == "definition"
    sketch = "N/A — stipulative definition." if is_defn else ""
    return PropositionRecord(
        prop_id=str(uuid.uuid4())[:12],
        kind=kind,
        title=title,
        statement=statement,
        proof_sketch=sketch,
        why_useful="",
        source_field=field_id,
        target_fields=(),
        metaphor_tags=tuple(tags),
        trust_tier="PROPOSAL",
        leverage_score=float(importance),
        proof_difficulty="MEDIUM",
        dependencies=(),
        judgment_coordinate="",
    )


# ---------------------------------------------------------------------------
# Field 1: Type Theory
# ---------------------------------------------------------------------------
_TYPE_THEORY = _field(
    name="Type Theory",
    description=(
        "Type theory is a foundational framework in which every mathematical object has a "
        "designated type, and proofs are identified with programs via the Curry-Howard "
        "correspondence. Martin-Löf type theory provides a constructive foundation through "
        "dependent types, identity types, and a cumulative universe hierarchy U0:U1:U2:..."
    ),
    props=[
        _prop("Curry-Howard Correspondence",
              "Propositions-as-types: A->B is implication, A*B is conjunction, and "
              "Sigma(x:A).B(x) is existential quantification; type-checking is proof-checking "
              "and beta-reduction is proof normalisation.",
              PropositionKind.THEOREM, "type_theory", importance=0.98,
              tags=("foundations", "logic", "programs")),
        _prop("Normalization Theorem",
              "Every well-typed term in simply-typed lambda calculus has a unique beta-normal "
              "form; strong normalisation holds for Martin-Lof type theory, making "
              "type-checking decidable.",
              PropositionKind.THEOREM, "type_theory", importance=0.92,
              tags=("reduction", "decidability")),
        _prop("Girard Paradox",
              "Martin-Lof's original type theory with Type:Type is inconsistent; Girard "
              "encoded Burali-Forti's ordinal paradox at the type level, requiring the "
              "cumulative universe hierarchy U0:U1:... for consistency.",
              PropositionKind.THEOREM, "type_theory", importance=0.90,
              tags=("paradox", "universes", "consistency")),
        _prop("W-Types as Initial Algebras",
              "The W-type W(x:A).B(x) is the initial algebra of the polynomial functor "
              "P(X)=Sigma(a:A).X^B(a), providing uniform construction of well-founded "
              "inductive types including naturals, lists, and trees.",
              PropositionKind.THEOREM, "type_theory", importance=0.84,
              tags=("inductive-types", "initial-algebra")),
    ],
    keywords=("types", "terms", "propositions", "proofs", "lambda", "dependent", "universes", "martin-lof"),
    judgment_site=(
        "Types-as-propositions makes the judgment 'a:A' the fundamental epistemic act; "
        "identity types Id_A(a,b) encode the path space of judgment identifications, and "
        "universe levels stratify the complexity geometry of valid judgments."
    ),
)

# ---------------------------------------------------------------------------
# Field 2: Category Theory
# ---------------------------------------------------------------------------
_CATEGORY_THEORY = _field(
    name="Category Theory",
    description=(
        "Category theory studies mathematical structures and their relationships through objects "
        "and morphisms, emphasizing universal properties over internal structure. "
        "Functors, natural transformations, adjunctions, and limits provide a unified language "
        "for coherent relationships between disparate mathematical domains."
    ),
    props=[
        _prop("Yoneda Lemma",
              "For any locally small category C, object c, and functor F:C->Set, there is a "
              "natural isomorphism Nat(Hom(c,-),F)=F(c); the Yoneda embedding y:C->[C^op,Set] "
              "is fully faithful, so objects are determined by their representable functors.",
              PropositionKind.THEOREM, "category_theory", importance=0.99,
              tags=("yoneda", "representability", "embedding")),
        _prop("Adjoint Functor Theorem (Freyd GAFT)",
              "A functor R:D->C between locally small complete categories has a left adjoint "
              "if and only if it preserves all small limits and satisfies the solution-set "
              "condition.",
              PropositionKind.THEOREM, "category_theory", importance=0.95,
              tags=("adjunction", "limits", "freyd")),
        _prop("Kan Extension Theorem",
              "Given F:C->D and K:C->E the left Kan extension (Lan_K F)(e)=coend^c Hom(Kc,e)*Fc "
              "subsumes limits, colimits, and is the universal approximation of F along K.",
              PropositionKind.THEOREM, "category_theory", importance=0.93,
              tags=("kan-extension", "coend")),
        _prop("Beck Monadicity Theorem",
              "A functor U:D->C is monadic iff it has a left adjoint, reflects isomorphisms, "
              "and D has and U preserves coequalizers of U-split pairs.",
              PropositionKind.THEOREM, "category_theory", importance=0.90,
              tags=("monad", "Beck", "monadicity")),
    ],
    keywords=("functor", "adjoint", "natural-transformation", "limit", "colimit", "monad", "yoneda", "universal-property"),
    judgment_site=(
        "Universal properties and adjunctions model the geometry of optimal judgment sites; "
        "functors are coherent inference maps, and the Yoneda lemma shows an object is "
        "determined by how it appears from all external judgment perspectives."
    ),
)

# ---------------------------------------------------------------------------
# Field 3: Homotopy Type Theory
# ---------------------------------------------------------------------------
_HoTT = _field(
    name="Homotopy Type Theory",
    description=(
        "Homotopy Type Theory reinterprets Martin-Lof type theory through homotopy theory: "
        "types are spaces, terms are points, and identity proofs are paths. "
        "The Univalence Axiom equates equivalent types with equal types, collapsing the "
        "distinction between isomorphism and propositional equality."
    ),
    props=[
        _prop("Univalence Axiom",
              "For any two types A,B:U the canonical map (A=B)->(A~=B) is an equivalence; "
              "equivalent types are indistinguishable and mathematics is invariant under "
              "equivalence.",
              PropositionKind.AXIOM, "hott", importance=0.99,
              tags=("univalence", "equivalence", "foundations")),
        _prop("Function Extensionality",
              "If f,g:Pi(x:A).B(x) satisfy f(x)=g(x) for all x then f=g; this follows "
              "from Univalence and makes pointwise-equal functions propositionally equal.",
              PropositionKind.THEOREM, "hott", importance=0.92,
              tags=("extensionality", "functions")),
        _prop("Higher Inductive Types",
              "HITs allow constructors for both points and paths; the circle S1 has base:S1 "
              "and loop:base=base, giving a synthetic proof pi_1(S1)=Z via the universal "
              "cover encoded as a type family over S1.",
              PropositionKind.DEFINITION, "hott", importance=0.91,
              tags=("HIT", "circle", "homotopy-groups")),
        _prop("Truncation and h-Levels",
              "A type is a proposition (h-level 1) if all inhabitants are equal; a set "
              "(h-level 2) if all identity types are propositions; the h-level hierarchy "
              "corresponds to the classical Postnikov tower.",
              PropositionKind.DEFINITION, "hott", importance=0.86,
              tags=("truncation", "h-levels", "Postnikov")),
    ],
    keywords=("homotopy", "paths", "equivalence", "univalence", "HIT", "types", "spaces", "groupoid"),
    judgment_site=(
        "Judgments form a higher groupoid; identity types are path spaces and Univalence "
        "ensures the judgment geometry is invariant under equivalence, so equivalent "
        "judgment frameworks are genuinely identical."
    ),
)

# ---------------------------------------------------------------------------
# Field 4: Topos Theory
# ---------------------------------------------------------------------------
_TOPOS_THEORY = _field(
    name="Topos Theory",
    description=(
        "A topos is a category behaving like a generalized universe of sets, possessing a "
        "subobject classifier Omega and all finite limits. "
        "Grothendieck topoi arise as sheaf categories on sites, underpinning etale cohomology, "
        "while elementary topoi axiomatize constructive set theory."
    ),
    props=[
        _prop("Giraud Theorem",
              "A category is a Grothendieck topos iff it has a small generating set, is "
              "cocomplete, has finite limits, and has universal effective epimorphisms with "
              "disjoint coproducts; this characterizes sheaf categories intrinsically.",
              PropositionKind.THEOREM, "topos_theory", importance=0.97,
              tags=("grothendieck", "sheaves", "characterization")),
        _prop("Lawvere-Tierney Topology",
              "In any topos E, a Lawvere-Tierney topology j:Omega->Omega satisfies j*j=j, "
              "j*top=top, j*and=and*(j*j); each topology classifies a subtopos via its "
              "sheaf subcategory.",
              PropositionKind.DEFINITION, "topos_theory", importance=0.90,
              tags=("topology", "sheaves", "modality")),
        _prop("Classifying Topos",
              "For any geometric theory T, there exists a classifying topos Set[T] such that "
              "T-models in any Grothendieck topos E correspond naturally to geometric morphisms "
              "E->Set[T]; this makes topos theory the geometry of logical theories.",
              PropositionKind.THEOREM, "topos_theory", importance=0.88,
              tags=("classifying-topos", "geometric-morphism")),
        _prop("Diaconescu Theorem",
              "The axiom of choice holds in an elementary topos E iff every epimorphism in "
              "E splits; in particular, AC implies every object is projective.",
              PropositionKind.THEOREM, "topos_theory", importance=0.85,
              tags=("axiom-of-choice", "epimorphism")),
    ],
    keywords=("sheaves", "subobject-classifier", "site", "grothendieck", "elementary-topos", "modality", "logic"),
    judgment_site=(
        "A topos is a judgment universe: Omega is the object of truth values encoding "
        "epistemic modalities, and sheaves capture how local judgments on a cover cohere "
        "into global knowledge over the base site."
    ),
)

# ---------------------------------------------------------------------------
# Field 5: Algebraic Topology
# ---------------------------------------------------------------------------
_ALGEBRAIC_TOPOLOGY = _field(
    name="Algebraic Topology",
    description=(
        "Algebraic topology assigns algebraic invariants -- homotopy groups, homology, "
        "cohomology, characteristic classes -- to topological spaces to distinguish them "
        "and study continuous maps. The van Kampen theorem, Hurewicz theorem, Whitehead "
        "theorem, and Poincare duality are cornerstones."
    ),
    props=[
        _prop("van Kampen Theorem",
              "If X=U union V with U,V open and U intersect V path-connected, then "
              "pi_1(X)=pi_1(U)*_{pi_1(U intersect V)} pi_1(V) is the amalgamated free product; "
              "this computes fundamental groups of spaces assembled by gluing.",
              PropositionKind.THEOREM, "algebraic_topology", importance=0.95,
              tags=("van-kampen", "fundamental-group", "pushout")),
        _prop("Hurewicz Theorem",
              "If X is (n-1)-connected for n>=2, the Hurewicz map h:pi_n(X)->H_n(X;Z) is an "
              "isomorphism; for n=1 with abelian pi_1, h induces pi_1(X)=H_1(X;Z).",
              PropositionKind.THEOREM, "algebraic_topology", importance=0.93,
              tags=("hurewicz", "homotopy-groups", "homology")),
        _prop("Poincare Duality",
              "For a closed orientable n-manifold M, cap product with [M] in H_n(M;Z) gives "
              "natural isomorphisms H^k(M;Z)=H_{n-k}(M;Z) for all k, linking cohomology "
              "in complementary dimensions.",
              PropositionKind.THEOREM, "algebraic_topology", importance=0.96,
              tags=("poincare-duality", "manifold", "cap-product")),
        _prop("Whitehead Theorem",
              "A map f:X->Y between simply connected CW complexes inducing isomorphisms on all "
              "homotopy groups is a homotopy equivalence; the theorem fails for general spaces.",
              PropositionKind.THEOREM, "algebraic_topology", importance=0.91,
              tags=("whitehead", "weak-equivalence", "CW")),
        _prop("Serre Spectral Sequence",
              "For a fibration F->E->B with B simply connected, E^2_{p,q}=H_p(B;H_q(F)) "
              "converges to H_{p+q}(E); this is the primary tool for computing homology of "
              "total spaces from base and fiber data.",
              PropositionKind.THEOREM, "algebraic_topology", importance=0.90,
              tags=("spectral-sequence", "fibration", "serre")),
    ],
    keywords=("homotopy", "homology", "cohomology", "fibration", "CW-complex", "characteristic-class", "manifold"),
    judgment_site=(
        "Topological spaces model judgment contexts up to continuous deformation; "
        "homological invariants measure obstructions to trivializing a judgment family, "
        "and the Hurewicz theorem links the first non-trivial homotopy to the first "
        "non-trivial homology obstruction."
    ),
)

# ---------------------------------------------------------------------------
# Field 6: Differential Geometry
# ---------------------------------------------------------------------------
_DIFF_GEOM = _field(
    name="Differential Geometry",
    description=(
        "Differential geometry studies smooth manifolds, Riemannian metrics, connections, "
        "and curvature using calculus and linear algebra. Major results include Stokes "
        "theorem, Gauss-Bonnet, Nash embedding, Frobenius integrability, and Chern-Weil "
        "theory of characteristic classes."
    ),
    props=[
        _prop("Stokes Theorem",
              "For a compact oriented n-manifold with boundary M and (n-1)-form omega: "
              "integral_{dM} omega = integral_M d(omega); this unifies Green, Gauss, and "
              "the classical Stokes theorem.",
              PropositionKind.THEOREM, "differential_geometry", importance=0.97,
              tags=("stokes", "integration", "differential-forms")),
        _prop("Gauss-Bonnet Theorem",
              "For a compact oriented 2-manifold (M,g): integral_M K dA = 2*pi*chi(M) "
              "where K is Gaussian curvature and chi(M) the Euler characteristic; local "
              "curvature determines global topology.",
              PropositionKind.THEOREM, "differential_geometry", importance=0.96,
              tags=("gauss-bonnet", "curvature", "euler-characteristic")),
        _prop("Nash Embedding Theorem",
              "Every smooth Riemannian manifold (M,g) embeds isometrically in R^N for "
              "sufficiently large N; for C^1 embeddings N=2m+1 suffices (Nash-Kuiper), "
              "for C^inf N=m(3m+11)/2 suffices.",
              PropositionKind.THEOREM, "differential_geometry", importance=0.92,
              tags=("nash", "isometric-embedding")),
        _prop("Chern-Weil Theory",
              "Given a principal G-bundle with connection A, the Chern-Weil homomorphism "
              "sends Ad-invariant polynomials on Lie(G) to de Rham cohomology classes on "
              "the base, independent of the connection choice.",
              PropositionKind.THEOREM, "differential_geometry", importance=0.90,
              tags=("chern-weil", "characteristic-classes", "connections")),
    ],
    keywords=("manifold", "connection", "curvature", "geodesic", "differential-forms", "riemannian", "fiber-bundle"),
    judgment_site=(
        "The space of judgments is a smooth manifold; valid inference paths are geodesics "
        "and epistemic curvature measures how parallel transport of beliefs around a "
        "judgment loop may fail to return to the original position."
    ),
)

# ---------------------------------------------------------------------------
# Field 7: Algebraic Geometry
# ---------------------------------------------------------------------------
_ALGEBRAIC_GEOMETRY = _field(
    name="Algebraic Geometry",
    description=(
        "Algebraic geometry studies solution sets of polynomial equations -- varieties, "
        "schemes, and stacks -- using the interplay of ring theory and geometry. "
        "Grothendieck's reformulation through schemes and sheaves unified arithmetic "
        "and geometry, driving proofs of the Weil conjectures and Fermat's Last Theorem."
    ),
    props=[
        _prop("Hilbert Nullstellensatz",
              "Over an algebraically closed field k, I(V(J))=sqrt(J) for any ideal J; "
              "maximal ideals of k[x1,...,xn] correspond to points of k^n, establishing "
              "the fundamental algebra-geometry dictionary.",
              PropositionKind.THEOREM, "algebraic_geometry", importance=0.97,
              tags=("nullstellensatz", "radical", "varieties")),
        _prop("Riemann-Roch Theorem",
              "For a smooth projective curve C of genus g and divisor D: "
              "dim H0(C,O(D)) - dim H0(C,K_C*O(-D)) = deg D - g + 1; "
              "Hirzebruch-Riemann-Roch generalises this to higher-dimensional varieties.",
              PropositionKind.THEOREM, "algebraic_geometry", importance=0.96,
              tags=("riemann-roch", "divisor", "genus")),
        _prop("Serre Duality",
              "For a smooth projective n-dimensional variety X: "
              "H^i(X,F) = H^{n-i}(X,omega_X tensor F*)* for coherent F, where omega_X is "
              "the dualizing sheaf.",
              PropositionKind.THEOREM, "algebraic_geometry", importance=0.93,
              tags=("serre-duality", "dualizing-sheaf", "coherent-sheaves")),
        _prop("Flat Base Change",
              "For a flat morphism f:X->S and Cartesian square with g:T->S, the natural "
              "map g* R^i f_* F -> R^i f'_* g'* F is an isomorphism for quasi-coherent F; "
              "flatness captures continuous variation of fibers.",
              PropositionKind.THEOREM, "algebraic_geometry", importance=0.87,
              tags=("flat-morphism", "base-change", "cohomology")),
    ],
    keywords=("variety", "scheme", "sheaf", "morphism", "cohomology", "divisor", "polynomial", "ring"),
    judgment_site=(
        "Schemes are the judgment universes of arithmetic-geometric reasoning; morphisms "
        "encode coherent transformation of judgment sites, and the etale topology provides "
        "the finest resolution for algebraic-geometric judgments."
    ),
)

# ---------------------------------------------------------------------------
# Field 8: Number Theory
# ---------------------------------------------------------------------------
_NUMBER_THEORY = _field(
    name="Number Theory",
    description=(
        "Number theory studies the integers and their arithmetic properties: primality, "
        "factorization, congruences, and L-functions. Deep connections to algebraic geometry "
        "and representation theory drove the proofs of the Modularity Theorem and "
        "Fermat's Last Theorem."
    ),
    props=[
        _prop("Prime Number Theorem",
              "The prime counting function pi(x) satisfies pi(x) ~ x/log x as x->inf; "
              "equivalently psi(x)~x for the Chebyshev function, proved using zeta(s) != 0 "
              "on Re(s)=1.",
              PropositionKind.THEOREM, "number_theory", importance=0.97,
              tags=("primes", "PNT", "zeta")),
        _prop("Riemann Hypothesis",
              "All non-trivial zeros of zeta(s)=sum n^{-s} lie on the critical line Re(s)=1/2; "
              "this controls the error term in the PNT and has profound consequences throughout "
              "analytic and algebraic number theory.",
              PropositionKind.CONJECTURE, "number_theory", importance=0.99,
              tags=("riemann-hypothesis", "zeta", "millennium")),
        _prop("Quadratic Reciprocity",
              "For distinct odd primes p,q: (p/q)(q/p)=(-1)^{(p-1)(q-1)/4}; p is a "
              "quadratic residue mod q iff q is mod p, unless p=q=3 (mod 4).",
              PropositionKind.THEOREM, "number_theory", importance=0.94,
              tags=("quadratic-reciprocity", "legendre-symbol")),
        _prop("Modularity Theorem (Wiles-Taylor)",
              "Every elliptic curve E/Q is modular: there exists a weight-2 newform f with "
              "L(E,s)=L(f,s); this implies Fermat's Last Theorem x^n+y^n=z^n has no "
              "integer solutions for n>=3.",
              PropositionKind.THEOREM, "number_theory", importance=0.98,
              tags=("modularity", "elliptic-curves", "fermat", "wiles")),
    ],
    keywords=("primes", "zeta-function", "modular-forms", "elliptic-curves", "L-functions", "integers", "arithmetic"),
    judgment_site=(
        "The integers are the primordial judgment lattice; prime factorization encodes "
        "irreducible judgment components, and L-functions measure the spectral distribution "
        "of judgment nodes across the arithmetic spectrum."
    ),
)


# ---------------------------------------------------------------------------
# Field 9: Representation Theory
# ---------------------------------------------------------------------------
_REPRESENTATION_THEORY = _field(
    name="Representation Theory",
    description=(
        "Representation theory studies abstract algebraic structures by representing them as "
        "linear transformations of vector spaces. The theory of characters, irreducible "
        "representations, and the classification of semisimple Lie algebra representations "
        "underpins modern physics and mathematics."
    ),
    props=[
        _prop("Schur Lemma",
              "If V,W are irreducible G-representations over an algebraically closed field, "
              "every G-homomorphism f:V->W is either 0 or an isomorphism; End_G(V)=k.",
              PropositionKind.LEMMA, "representation_theory", importance=0.95,
              tags=("schur", "irreducible")),
        _prop("Maschke Theorem",
              "If G is finite and char(k) does not divide |G|, every G-representation over "
              "k is completely reducible; equivalently the group algebra kG is semisimple.",
              PropositionKind.THEOREM, "representation_theory", importance=0.93,
              tags=("maschke", "semisimple", "complete-reducibility")),
        _prop("Peter-Weyl Theorem",
              "For a compact group G, matrix coefficients of irreducible unitary representations "
              "form an orthonormal basis for L2(G); the regular representation decomposes as "
              "L2(G) = direct sum over pi of V_pi tensor V_pi*.",
              PropositionKind.THEOREM, "representation_theory", importance=0.94,
              tags=("peter-weyl", "compact-group", "L2")),
        _prop("Weyl Character Formula",
              "The character of the irreducible g-representation with highest weight lambda is "
              "chi_lambda = (sum_{w in W} sgn(w) e^{w(lambda+rho)}) / (sum_{w in W} sgn(w) e^{w*rho}) "
              "where rho is the Weyl vector and W the Weyl group.",
              PropositionKind.THEOREM, "representation_theory", importance=0.96,
              tags=("weyl-character", "highest-weight")),
    ],
    keywords=("character", "irreducible", "group", "Lie-algebra", "weight", "Weyl-group", "module", "semisimple"),
    judgment_site=(
        "Representations are judgment-to-action maps; irreducible representations are minimal "
        "judgment modes, and the character encodes the full epistemic signature of how a "
        "judgment acts on each dimension of the representation space."
    ),
)

# ---------------------------------------------------------------------------
# Field 10: Functional Analysis
# ---------------------------------------------------------------------------
_FUNCTIONAL_ANALYSIS = _field(
    name="Functional Analysis",
    description=(
        "Functional analysis extends linear algebra to infinite-dimensional spaces, studying "
        "Banach and Hilbert spaces, bounded linear operators, and the interplay of topological "
        "and algebraic structure. The Hahn-Banach, open mapping, and spectral theorems are "
        "keystones."
    ),
    props=[
        _prop("Hahn-Banach Theorem",
              "Let V be a real vector space with sublinear p; if phi:W->R is linear on W<=V "
              "with phi<=p on W then phi extends to Phi:V->R with Phi<=p everywhere, "
              "guaranteeing non-triviality of the dual.",
              PropositionKind.THEOREM, "functional_analysis", importance=0.98,
              tags=("hahn-banach", "extension", "duality")),
        _prop("Uniform Boundedness Principle",
              "If {T_alpha} are bounded linear operators on Banach space X with "
              "sup_alpha norm(T_alpha x) < inf for each x, then sup_alpha norm(T_alpha) < inf; "
              "pointwise boundedness implies uniform boundedness.",
              PropositionKind.THEOREM, "functional_analysis", importance=0.94,
              tags=("banach-steinhaus", "uniform-boundedness")),
        _prop("Spectral Theorem for Self-Adjoint Operators",
              "Every bounded self-adjoint T on Hilbert space H is unitarily equivalent to "
              "multiplication by lambda on L2(sigma(T),mu); equivalently T=integral lambda dE(lambda) "
              "for a unique spectral measure E.",
              PropositionKind.THEOREM, "functional_analysis", importance=0.97,
              tags=("spectral-theorem", "self-adjoint", "Hilbert-space")),
        _prop("Open Mapping Theorem",
              "A surjective bounded linear operator T:X->Y between Banach spaces is an open "
              "map; a bijective bounded operator has a bounded inverse.",
              PropositionKind.THEOREM, "functional_analysis", importance=0.93,
              tags=("open-mapping", "banach", "bounded-inverse")),
    ],
    keywords=("Banach-space", "Hilbert-space", "operator", "spectrum", "dual", "weak-topology", "compact"),
    judgment_site=(
        "Banach spaces are the metric judgment spaces; the Hahn-Banach theorem guarantees "
        "every locally coherent partial judgment extends to a globally coherent full judgment "
        "respecting the ambient norm geometry."
    ),
)

# ---------------------------------------------------------------------------
# Field 11: Operator Algebras
# ---------------------------------------------------------------------------
_OPERATOR_ALGEBRAS = _field(
    name="Operator Algebras",
    description=(
        "Operator algebras -- C*-algebras, von Neumann algebras -- are norm-closed or weakly "
        "closed subalgebras of B(H), studied as noncommutative generalizations of function "
        "algebras and measure spaces. They arise in quantum mechanics, ergodic theory, and "
        "group actions."
    ),
    props=[
        _prop("Gelfand-Naimark Theorem",
              "Every commutative unital C*-algebra A is isometrically *-isomorphic to C(X) "
              "for the compact Hausdorff space X=Spec(A); every abstract C*-algebra embeds "
              "isometrically in B(H).",
              PropositionKind.THEOREM, "operator_algebras", importance=0.97,
              tags=("gelfand-naimark", "C*-algebra", "spectrum")),
        _prop("GNS Construction",
              "Every state phi on a C*-algebra A determines a Hilbert space H_phi, "
              "representation pi_phi:A->B(H_phi), and cyclic vector Omega_phi with "
              "phi(a)=<pi_phi(a)Omega_phi, Omega_phi>.",
              PropositionKind.THEOREM, "operator_algebras", importance=0.95,
              tags=("GNS", "state", "representation")),
        _prop("Tomita-Takesaki Theory",
              "For a von Neumann algebra M with cyclic separating vector Omega, the modular "
              "operator Delta and conjugation J satisfy JMJ=M' and Delta^{it} M Delta^{-it}=M; "
              "sigma_t(a)=Delta^{it} a Delta^{-it} is the modular automorphism group.",
              PropositionKind.THEOREM, "operator_algebras", importance=0.93,
              tags=("tomita-takesaki", "modular-automorphism", "KMS")),
        _prop("Jones Index Theorem",
              "For an irreducible II_1 subfactor N<=M, the index [M:N] takes values in "
              "{4cos^2(pi/n): n>=3} union [4,inf]; subfactor theory connects to knot "
              "invariants and conformal field theory.",
              PropositionKind.THEOREM, "operator_algebras", importance=0.91,
              tags=("jones-index", "subfactors", "knot-invariants")),
    ],
    keywords=("C*-algebra", "von-Neumann", "state", "modular", "factor", "trace", "noncommutative"),
    judgment_site=(
        "Von Neumann algebras are the quantum judgment algebras; states are probability "
        "measures over judgment outcomes, and the modular automorphism group captures the "
        "intrinsic time-flow of the judgment geometry at thermal equilibrium."
    ),
)

# ---------------------------------------------------------------------------
# Field 12: Probability Theory
# ---------------------------------------------------------------------------
_PROBABILITY_THEORY = _field(
    name="Probability Theory",
    description=(
        "Probability theory provides the measure-theoretic foundation for reasoning under "
        "uncertainty, built on Kolmogorov's axioms. It studies random variables, expectations, "
        "conditional probability, and limit theorems including the strong law of large numbers "
        "and the central limit theorem."
    ),
    props=[
        _prop("Kolmogorov Axioms",
              "A probability space (Omega,F,P) has F a sigma-algebra and P a countably "
              "additive non-negative measure with P(Omega)=1; all classical probability "
              "is derived from these three axioms.",
              PropositionKind.AXIOM, "probability_theory", importance=0.98,
              tags=("kolmogorov", "measure", "sigma-algebra")),
        _prop("Central Limit Theorem",
              "If X_1,...,X_n are i.i.d. with mean mu and variance sigma^2 < inf, then "
              "(sum X_i - n*mu)/(sigma*sqrt(n)) converges in distribution to N(0,1); "
              "Gaussian universality is the hallmark of collective averaging.",
              PropositionKind.THEOREM, "probability_theory", importance=0.97,
              tags=("CLT", "Gaussian", "convergence")),
        _prop("Doob Martingale Convergence Theorem",
              "Every L^1-bounded martingale (M_n) converges a.s. to a limit M_inf in L^1; "
              "every non-negative supermartingale converges a.s., providing the fundamental "
              "stability result for stochastic processes.",
              PropositionKind.THEOREM, "probability_theory", importance=0.93,
              tags=("martingale", "convergence", "doob")),
        _prop("Radon-Nikodym Theorem",
              "If nu << mu on a sigma-finite measure space, there exists measurable f>=0 "
              "with nu(A)=integral_A f dmu; the Radon-Nikodym derivative dnu/dmu is the "
              "likelihood ratio and underpins conditional expectation.",
              PropositionKind.THEOREM, "probability_theory", importance=0.91,
              tags=("radon-nikodym", "absolute-continuity")),
    ],
    keywords=("random-variable", "expectation", "martingale", "measure", "convergence", "Gaussian", "conditional"),
    judgment_site=(
        "Probability measures are judgment weights; conditional probability is the geometry "
        "of Bayesian judgment update, and martingales encode the epistemic principle that "
        "rational forecasts cannot be systematically exploited."
    ),
)

# ---------------------------------------------------------------------------
# Field 13: Stochastic Processes
# ---------------------------------------------------------------------------
_STOCHASTIC_PROCESSES = _field(
    name="Stochastic Processes",
    description=(
        "Stochastic processes study collections of random variables indexed by time or space, "
        "including Brownian motion, Markov chains, martingales, and SDEs. Ito calculus "
        "provides the key tool for analyzing SDEs and is the foundation of mathematical "
        "finance."
    ),
    props=[
        _prop("Ito Lemma",
              "If f is C^2 and dX_t = mu_t dt + sigma_t dW_t, then "
              "df(X_t) = f'(X_t)dX_t + (1/2)f''(X_t)sigma_t^2 dt; the Ito correction "
              "term (1/2)f''sigma^2 dt distinguishes stochastic from ordinary calculus.",
              PropositionKind.THEOREM, "stochastic_processes", importance=0.98,
              tags=("ito", "SDE", "calculus")),
        _prop("Martingale Representation Theorem",
              "Every martingale M adapted to the Brownian filtration can be written as "
              "M_t = M_0 + integral_0^t H_s dW_s for unique previsible H; this is the basis "
              "for the Black-Scholes complete-market hedging argument.",
              PropositionKind.THEOREM, "stochastic_processes", importance=0.95,
              tags=("martingale-representation", "brownian-motion", "hedging")),
        _prop("Feynman-Kac Formula",
              "The solution to -du/dt + (1/2)sigma^2 d^2u/dx^2 + b du/dx - ru = f with "
              "terminal condition g(x) is u(t,x) = E[e^{-integral_t^T r(X_s)ds} g(X_T) | X_t=x], "
              "linking PDEs to expectations over diffusion paths.",
              PropositionKind.THEOREM, "stochastic_processes", importance=0.92,
              tags=("feynman-kac", "PDE", "diffusion")),
        _prop("Optional Stopping Theorem",
              "If M is a uniformly integrable martingale and tau is a stopping time, then "
              "E[M_tau] = E[M_0]; sufficient conditions: tau bounded or M bounded on [0,tau].",
              PropositionKind.THEOREM, "stochastic_processes", importance=0.90,
              tags=("optional-stopping", "martingale", "stopping-time")),
    ],
    keywords=("brownian-motion", "martingale", "Markov", "SDE", "filtration", "stopping-time", "Ito"),
    judgment_site=(
        "Stochastic processes model the temporal unfolding of uncertain judgment paths; "
        "the Markov property captures the sufficiency of current state for future judgment, "
        "and Ito calculus corrects for the roughness of optimal epistemic trajectories."
    ),
)

# ---------------------------------------------------------------------------
# Field 14: Information Theory
# ---------------------------------------------------------------------------
_INFORMATION_THEORY = _field(
    name="Information Theory",
    description=(
        "Information theory, founded by Shannon, provides a mathematical framework for "
        "quantifying, storing, and communicating information. Shannon entropy, channel "
        "capacity, and the coding theorems establish fundamental limits on compression "
        "and reliable communication over noisy channels."
    ),
    props=[
        _prop("Shannon Entropy",
              "For a discrete random variable X with distribution p, "
              "H(X) = -sum p(x) log_2 p(x) is the average minimum bits to describe an "
              "outcome; entropy is maximized by the uniform distribution.",
              PropositionKind.DEFINITION, "information_theory", importance=0.98,
              tags=("entropy", "shannon")),
        _prop("Channel Capacity Theorem",
              "The maximum reliable communication rate over a noisy channel is "
              "C = max_{p(x)} I(X;Y) bits per channel use; for AWGN: C = (1/2)log_2(1+SNR), "
              "achievable with random coding and vanishing error probability.",
              PropositionKind.THEOREM, "information_theory", importance=0.99,
              tags=("channel-capacity", "mutual-information", "AWGN")),
        _prop("Source Coding Theorem",
              "A source with entropy H can be compressed to H bits/symbol but no fewer; "
              "for any epsilon>0 codes of rate H+epsilon achieve vanishing error, but "
              "no code of rate H-epsilon can.",
              PropositionKind.THEOREM, "information_theory", importance=0.95,
              tags=("source-coding", "compression", "entropy")),
        _prop("Data Processing Inequality",
              "If X->Y->Z is a Markov chain then I(X;Z) <= I(X;Y); post-processing cannot "
              "increase mutual information, and T(Y) is sufficient iff I(X;T(Y))=I(X;Y).",
              PropositionKind.THEOREM, "information_theory", importance=0.92,
              tags=("data-processing", "Markov", "sufficient-statistic")),
    ],
    keywords=("entropy", "mutual-information", "channel-capacity", "compression", "KL-divergence", "coding"),
    judgment_site=(
        "Information theory is the geometry of epistemic efficiency: entropy measures "
        "irreducible uncertainty, channel capacity bounds evidence transmission rate, and "
        "the data processing inequality captures monotone degradation of judgment quality."
    ),
)

# ---------------------------------------------------------------------------
# Field 15: Statistical Mechanics
# ---------------------------------------------------------------------------
_STATISTICAL_MECHANICS = _field(
    name="Statistical Mechanics",
    description=(
        "Statistical mechanics derives macroscopic thermodynamic properties from microscopic "
        "probabilistic laws governing large particle ensembles. Key concepts include partition "
        "functions, phase transitions, the Ising model, and the Gibbs variational principle."
    ),
    props=[
        _prop("Boltzmann Entropy Formula",
              "S = k_B ln(Omega) where Omega is the number of microstates consistent with "
              "the macrostate; this identifies thermodynamic entropy with statistical "
              "uncertainty and is the foundation of equilibrium statistical mechanics.",
              PropositionKind.DEFINITION, "statistical_mechanics", importance=0.98,
              tags=("boltzmann", "entropy", "microstates")),
        _prop("Peierls Argument",
              "The 2D Ising model on Z^2 has a phase transition at T_c: for T<T_c "
              "spontaneous magnetization exists (two Gibbs measures), proved by bounding "
              "the energy cost of domain-wall contours.",
              PropositionKind.THEOREM, "statistical_mechanics", importance=0.94,
              tags=("ising", "phase-transition", "peierls")),
        _prop("Fluctuation-Dissipation Theorem",
              "In thermal equilibrium at temperature T, the linear response (dissipation) "
              "to an external perturbation equals (1/2k_BT) times the equilibrium "
              "fluctuation correlation; dissipation is measurable from equilibrium noise.",
              PropositionKind.THEOREM, "statistical_mechanics", importance=0.93,
              tags=("fluctuation-dissipation", "linear-response", "equilibrium")),
        _prop("Gibbs Variational Principle",
              "The equilibrium Gibbs measure minimizes free energy F(mu)=E_mu[H]-T S(mu) "
              "over all probability measures; equilibrium is the optimal trade-off between "
              "energy minimization and entropy maximization.",
              PropositionKind.THEOREM, "statistical_mechanics", importance=0.92,
              tags=("gibbs", "free-energy", "variational")),
    ],
    keywords=("partition-function", "Ising", "phase-transition", "entropy", "Gibbs", "Hamiltonian", "fluctuation"),
    judgment_site=(
        "Statistical mechanics models collective judgment of macroscopic ensembles; the "
        "Gibbs free energy is the cost functional for collective equilibria, and phase "
        "transitions mark critical thresholds where judgment geometry changes qualitatively."
    ),
)

# ---------------------------------------------------------------------------
# Field 16: Quantum Mechanics
# ---------------------------------------------------------------------------
_QUANTUM_MECHANICS = _field(
    name="Quantum Mechanics",
    description=(
        "Quantum mechanics is the fundamental theory governing microscopic systems, formulated "
        "on Hilbert spaces with self-adjoint observable operators, unitary time evolution, and "
        "the Born rule for measurement probabilities. Bell's theorem rules out local "
        "hidden variable theories."
    ),
    props=[
        _prop("Schrodinger Equation",
              "The state |psi(t)> evolves as i hbar d|psi>/dt = H|psi> with solution "
              "|psi(t)> = e^{-iHt/hbar}|psi(0)>; unitary U(t)=e^{-iHt/hbar} "
              "preserves the norm and probability interpretation.",
              PropositionKind.AXIOM, "quantum_mechanics", importance=0.99,
              tags=("schrodinger", "hamiltonian", "unitary")),
        _prop("Heisenberg Uncertainty Principle",
              "For canonical observables Q,P with [Q,P]=i hbar: DeltaQ * DeltaP >= hbar/2 "
              "in any state; this is a fundamental property of quantum states, not a "
              "measurement-disturbance effect.",
              PropositionKind.THEOREM, "quantum_mechanics", importance=0.97,
              tags=("uncertainty", "canonical-commutation")),
        _prop("Born Rule",
              "The probability of measuring eigenvalue lambda for observable A in state "
              "|psi> is P(lambda) = norm(P_lambda |psi>)^2 where P_lambda is the spectral "
              "projector; this bridges the Hilbert space formalism with experiment.",
              PropositionKind.AXIOM, "quantum_mechanics", importance=0.98,
              tags=("born-rule", "measurement", "probability")),
        _prop("Bell Theorem",
              "No local hidden variable theory reproduces all quantum predictions; quantum "
              "correlations violate the CHSH inequality |E(a,b)-E(a,b')+E(a',b)+E(a',b')| <= 2 "
              "with quantum maximum 2*sqrt(2) (Tsirelson bound).",
              PropositionKind.THEOREM, "quantum_mechanics", importance=0.96,
              tags=("Bell", "entanglement", "nonlocality", "CHSH")),
    ],
    keywords=("Hilbert-space", "observable", "unitary", "entanglement", "measurement", "Hamiltonian", "superposition"),
    judgment_site=(
        "The quantum judgment lattice is orthomodular, not Boolean; superposition encodes "
        "coexistence of incompatible judgment potentials resolved only upon measurement, "
        "and entanglement captures non-local judgment correlations without causal signaling."
    ),
)

# ---------------------------------------------------------------------------
# Field 17: Quantum Field Theory
# ---------------------------------------------------------------------------
_QFT = _field(
    name="Quantum Field Theory",
    description=(
        "Quantum field theory combines quantum mechanics with special relativity for systems "
        "with infinitely many degrees of freedom. It underpins the Standard Model via gauge "
        "theories, path integrals, renormalization group, and operator product expansions."
    ),
    props=[
        _prop("Noether Theorem",
              "To every continuous symmetry of action S=integral L d^4x there corresponds "
              "a conserved current J^mu with d_mu J^mu=0; spacetime translation symmetry "
              "gives energy-momentum conservation and internal symmetries give charge conservation.",
              PropositionKind.THEOREM, "qft", importance=0.99,
              tags=("noether", "symmetry", "conservation")),
        _prop("CPT Theorem",
              "Any local relativistic QFT invariant under the Lorentz group is automatically "
              "invariant under the combined CPT symmetry; this does not require invariance "
              "under C, P, or T separately.",
              PropositionKind.THEOREM, "qft", importance=0.95,
              tags=("CPT", "Lorentz", "antiparticles")),
        _prop("Renormalization Group (Wilson)",
              "The Callan-Symanzik equation governs running of coupling g via beta(g)=mu dg/dmu; "
              "fixed points are scale-invariant CFTs and RG flow between them describes "
              "universality classes of phase transitions.",
              PropositionKind.THEOREM, "qft", importance=0.97,
              tags=("RG", "beta-function", "fixed-point", "universality")),
        _prop("Spin-Statistics Theorem",
              "In a local relativistic QFT, integer-spin fields satisfy commutation relations "
              "(bosons) and half-integer spin fields satisfy anti-commutation relations "
              "(fermions); this follows from Lorentz invariance and locality.",
              PropositionKind.THEOREM, "qft", importance=0.94,
              tags=("spin-statistics", "bosons", "fermions")),
    ],
    keywords=("gauge-theory", "path-integral", "renormalization", "Lagrangian", "field", "S-matrix", "operator-product"),
    judgment_site=(
        "Quantum fields are judgment-valued operators at every spacetime point; the path "
        "integral sums over all judgment trajectories weighted by the action, and the RG "
        "describes how judgment geometry transforms with the resolution scale."
    ),
)

# ---------------------------------------------------------------------------
# Field 18: String Theory
# ---------------------------------------------------------------------------
_STRING_THEORY = _field(
    name="String Theory",
    description=(
        "String theory proposes one-dimensional strings as fundamental constituents, requiring "
        "ten spacetime dimensions and supersymmetry. It yields quantum gravity and deep "
        "mathematical results including mirror symmetry, the AdS/CFT correspondence, and "
        "connections to the Monster group."
    ),
    props=[
        _prop("AdS/CFT Correspondence",
              "Type IIB string theory on AdS5 x S5 is dual to N=4 super-Yang-Mills on the "
              "4d boundary; the bulk partition function equals the CFT generating functional, "
              "relating quantum gravity to a conformal field theory.",
              PropositionKind.CONJECTURE, "string_theory", importance=0.99,
              tags=("AdS-CFT", "holography", "duality")),
        _prop("Monstrous Moonshine (Borcherds)",
              "The coefficients of j(tau)=q^{-1}+744+196884q+... are sums of dimensions of "
              "irreducible Monster group representations; Borcherds proved this using the "
              "Monster vertex operator algebra.",
              PropositionKind.THEOREM, "string_theory", importance=0.95,
              tags=("moonshine", "Monster-group", "modular-forms")),
        _prop("Mirror Symmetry",
              "For a Calabi-Yau threefold X with Hodge numbers (h^{1,1},h^{2,1}) there "
              "exists a mirror X-tilde with Hodge numbers (h^{2,1},h^{1,1}); the A-model "
              "on X equals the B-model on X-tilde, equating Gromov-Witten invariants with periods.",
              PropositionKind.CONJECTURE, "string_theory", importance=0.97,
              tags=("mirror-symmetry", "Calabi-Yau", "Hodge")),
        _prop("Calabi-Yau Theorem (Yau)",
              "On any compact Kahler manifold M with c_1(M)=0, every Kahler class contains "
              "a unique Ricci-flat Kahler metric; Calabi-Yau manifolds are the natural "
              "compactification spaces for string theory.",
              PropositionKind.THEOREM, "string_theory", importance=0.93,
              tags=("Calabi-Yau", "Ricci-flat", "Yau")),
    ],
    keywords=("strings", "Calabi-Yau", "AdS-CFT", "supersymmetry", "duality", "M-theory", "holography"),
    judgment_site=(
        "String theory is the geometry of the ultimate judgment web; the worldsheet is "
        "judgment history, and AdS/CFT shows any complete bulk judgment geometry is "
        "encoded on its boundary conformal field theory."
    ),
)

# ---------------------------------------------------------------------------
# Field 19: General Relativity
# ---------------------------------------------------------------------------
_GENERAL_RELATIVITY = _field(
    name="General Relativity",
    description=(
        "General relativity is Einstein's theory of gravitation in which spacetime is a "
        "pseudo-Riemannian manifold whose curvature is sourced by mass-energy via the "
        "Einstein field equations. Penrose singularity theorem, Birkhoff theorem, positive "
        "mass theorem, and Hawking radiation are key results."
    ),
    props=[
        _prop("Einstein Field Equations",
              "G_{mu nu} + Lambda g_{mu nu} = (8 pi G / c^4) T_{mu nu} where G_{mu nu} = "
              "R_{mu nu} - (1/2) R g_{mu nu} is the Einstein tensor; this equates spacetime "
              "curvature with matter-energy content.",
              PropositionKind.AXIOM, "general_relativity", importance=0.99,
              tags=("einstein", "curvature", "spacetime")),
        _prop("Penrose Singularity Theorem",
              "If a spacetime satisfies the null energy condition and contains a trapped "
              "surface, it contains an incomplete null geodesic; singularities are inevitable "
              "inside black holes from generic initial data.",
              PropositionKind.THEOREM, "general_relativity", importance=0.96,
              tags=("penrose", "singularity", "trapped-surface")),
        _prop("Positive Mass Theorem (Schoen-Yau)",
              "The ADM mass of an asymptotically flat spacetime satisfying the dominant energy "
              "condition is non-negative; equality holds iff the spacetime is Minkowski space.",
              PropositionKind.THEOREM, "general_relativity", importance=0.93,
              tags=("positive-mass", "ADM", "Schoen-Yau")),
        _prop("Hawking Radiation",
              "A black hole of mass M emits thermal radiation at T_H = hbar c^3 / (8 pi G M k_B); "
              "Bekenstein-Hawking entropy S = A c^4 / (4 G hbar) combines quantum mechanics, "
              "gravity, and thermodynamics.",
              PropositionKind.THEOREM, "general_relativity", importance=0.95,
              tags=("hawking-radiation", "black-hole-thermodynamics", "entropy")),
    ],
    keywords=("spacetime", "curvature", "geodesic", "black-hole", "Lorentzian", "Riemann", "singularity"),
    judgment_site=(
        "Spacetime curvature is the geometry of causal judgment; light cones define "
        "structures of possible judgments, geodesics are optimal inference paths, and "
        "Einstein equations express how accumulated judgments curve future epistemic possibilities."
    ),
)

# ---------------------------------------------------------------------------
# Field 20: Symplectic Geometry
# ---------------------------------------------------------------------------
_SYMPLECTIC_GEOMETRY = _field(
    name="Symplectic Geometry",
    description=(
        "Symplectic geometry studies manifolds with a closed non-degenerate 2-form omega, "
        "arising as phase spaces in classical mechanics and moduli spaces in gauge theory. "
        "Darboux theorem, Gromov non-squeezing, Arnold-Liouville integrability, and Floer "
        "homology are cornerstones."
    ),
    props=[
        _prop("Darboux Theorem",
              "Every symplectic manifold (M^{2n}, omega) is locally symplectomorphic to "
              "(R^{2n}, sum dxi ^ dyi); unlike Riemannian geometry, symplectic geometry "
              "has no local invariants.",
              PropositionKind.THEOREM, "symplectic_geometry", importance=0.95,
              tags=("darboux", "local-triviality")),
        _prop("Gromov Non-Squeezing Theorem",
              "A symplectic ball B^{2n}(r) embeds symplectically in the cylinder B^2(R) x R^{2n-2} "
              "iff r<=R; this rigidity result introduced J-holomorphic curves and the "
              "Gromov width symplectic invariant.",
              PropositionKind.THEOREM, "symplectic_geometry", importance=0.97,
              tags=("gromov", "non-squeezing", "rigidity")),
        _prop("Arnold-Liouville Theorem",
              "A completely integrable Hamiltonian system with n commuting integrals foliates "
              "phase space by Lagrangian tori on which motion is quasi-periodic; the system "
              "is solvable in action-angle coordinates.",
              PropositionKind.THEOREM, "symplectic_geometry", importance=0.94,
              tags=("arnold-liouville", "integrable", "action-angle")),
        _prop("Moment Map and Symplectic Reduction",
              "If G acts on (M,omega) with equivariant moment map mu:M->g*, the quotient "
              "mu^{-1}(0)/G inherits a canonical symplectic form (Marsden-Weinstein-Meyer); "
              "this is the basis for gauge theory reduction.",
              PropositionKind.THEOREM, "symplectic_geometry", importance=0.93,
              tags=("moment-map", "symplectic-reduction")),
    ],
    keywords=("symplectic-form", "Hamiltonian", "Lagrangian", "moment-map", "Floer", "J-holomorphic", "phase-space"),
    judgment_site=(
        "Phase space is the primary judgment geometry of classical mechanics; the symplectic "
        "form encodes duality between judgment sites (positions) and velocities (momenta), "
        "and Hamiltonian flow is canonical judgment evolution."
    ),
)

# ---------------------------------------------------------------------------
# Field 21: Poisson Geometry
# ---------------------------------------------------------------------------
_POISSON_GEOMETRY = _field(
    name="Poisson Geometry",
    description=(
        "Poisson geometry generalizes symplectic geometry by allowing a degenerate Poisson "
        "bracket; symplectic leaves give the Weinstein splitting structure. Poisson-Lie groups, "
        "Lie bialgebras, and Kontsevich's deformation quantization are central results."
    ),
    props=[
        _prop("Weinstein Splitting Theorem",
              "Near any point x in a Poisson manifold (M,pi), local coordinates (qi,pi,zj) "
              "exist with pi = sum d/dqi ^ d/dpi + sum phi_{ij}(z) d/dzi ^ d/dzj, "
              "separating the symplectic and transverse directions.",
              PropositionKind.THEOREM, "poisson_geometry", importance=0.95,
              tags=("weinstein-splitting", "symplectic-leaves")),
        _prop("Kontsevich Formality Theorem",
              "The Hochschild cochain complex C^inf(M) is formal as an L-infinity-algebra, "
              "quasi-isomorphic to the Schouten-Nijenhuis algebra of polyvector fields; "
              "consequently every Poisson manifold has a canonical formal deformation quantization.",
              PropositionKind.THEOREM, "poisson_geometry", importance=0.96,
              tags=("kontsevich", "formality", "deformation-quantization")),
        _prop("Poisson-Lie Groups and Lie Bialgebras",
              "A Poisson-Lie group (G,pi) has multiplication m:GxG->G a Poisson map; "
              "its infinitesimal data is a Lie bialgebra (g,g*) with compatible brackets "
              "classified by solutions to the classical Yang-Baxter equation.",
              PropositionKind.DEFINITION, "poisson_geometry", importance=0.90,
              tags=("Poisson-Lie", "Lie-bialgebra", "Yang-Baxter")),
        _prop("Symplectic Groupoid Integration",
              "Every integrable Poisson manifold (M,pi) is the base of a symplectic groupoid "
              "Sigma(M)=>M constructed as cotangent path space modulo homotopy; its Lie "
              "algebroid is T*M with the bracket induced by pi.",
              PropositionKind.THEOREM, "poisson_geometry", importance=0.88,
              tags=("symplectic-groupoid", "Lie-algebroid")),
    ],
    keywords=("Poisson-bracket", "symplectic-leaves", "Lie-bialgebra", "deformation-quantization", "groupoid"),
    judgment_site=(
        "Poisson geometry is the geometry of partial judgment duality; symplectic leaves are "
        "non-degenerate judgment strata, and the Poisson bracket encodes the infinitesimal "
        "structure of how judgments transform into their conjugate questions."
    ),
)

# ---------------------------------------------------------------------------
# Field 22: Lie Theory
# ---------------------------------------------------------------------------
_LIE_THEORY = _field(
    name="Lie Theory",
    description=(
        "Lie theory studies continuous symmetry groups (Lie groups) and their infinitesimal "
        "counterparts (Lie algebras) through the exponential map. The classification of "
        "semisimple Lie algebras via root systems and Dynkin diagrams, Ado's theorem, and "
        "the Peter-Weyl decomposition are central results."
    ),
    props=[
        _prop("Lie Third Theorem",
              "Every finite-dimensional real Lie algebra g is the Lie algebra of a unique "
              "(up to isomorphism) simply connected Lie group G; the correspondence g->G "
              "is functorial from Lie algebras to simply connected groups.",
              PropositionKind.THEOREM, "lie_theory", importance=0.93,
              tags=("lie-third-theorem", "simply-connected")),
        _prop("Killing Form and Cartan Criterion",
              "The Killing form B(X,Y)=Tr(ad_X * ad_Y) is non-degenerate iff g is semisimple; "
              "g is solvable iff B(g,[g,g])=0 (Cartan's solvability criterion).",
              PropositionKind.THEOREM, "lie_theory", importance=0.92,
              tags=("killing-form", "semisimple", "solvable")),
        _prop("Root System Classification",
              "Simple complex Lie algebras are classified by irreducible reduced root systems: "
              "Dynkin diagrams A_n, B_n, C_n, D_n and five exceptionals G2, F4, E6, E7, E8; "
              "the root system encodes the entire structure.",
              PropositionKind.THEOREM, "lie_theory", importance=0.97,
              tags=("root-system", "Dynkin-diagram", "classification")),
        _prop("Baker-Campbell-Hausdorff Formula",
              "exp(X)*exp(Y) = exp(X+Y+(1/2)[X,Y]+(1/12)[X,[X,Y]]-(1/12)[Y,[X,Y]]+...) "
              "encodes group multiplication entirely in iterated Lie brackets, connecting "
              "the group and algebra structure.",
              PropositionKind.THEOREM, "lie_theory", importance=0.89,
              tags=("BCH", "exponential-map", "Lie-bracket")),
    ],
    keywords=("Lie-group", "Lie-algebra", "root-system", "Dynkin", "semisimple", "representation", "exponential"),
    judgment_site=(
        "Lie groups are the symmetry groups of judgment geometry; the Lie algebra gives "
        "infinitesimal judgment symmetry, and root system classification provides the "
        "irreducible judgment symmetry catalogue."
    ),
)

# ---------------------------------------------------------------------------
# Field 23: Combinatorics
# ---------------------------------------------------------------------------
_COMBINATORICS = _field(
    name="Combinatorics",
    description=(
        "Combinatorics studies discrete structures -- permutations, graphs, partitions, "
        "matroids -- focusing on enumeration, existence, and optimization. Algebraic methods "
        "(generating functions, symmetric functions), probabilistic arguments, and topological "
        "tools interact richly in modern combinatorics."
    ),
    props=[
        _prop("Ramsey Theorem",
              "For any r,s>=1 there exists R(r,s) such that any 2-coloring of K_{R(r,s)} "
              "contains a red K_r or blue K_s; the probabilistic method gives R(r,r) > 2^{r/2}.",
              PropositionKind.THEOREM, "combinatorics", importance=0.95,
              tags=("ramsey", "coloring", "extremal")),
        _prop("Burnside Lemma",
              "|X/G| = (1/|G|) sum_{g in G} |Fix(g)| counts orbits of finite group G on set X; "
              "this underlies Polya enumeration and the enumeration of symmetry classes.",
              PropositionKind.LEMMA, "combinatorics", importance=0.90,
              tags=("burnside", "group-action", "orbits")),
        _prop("Mobius Inversion Formula",
              "For functions on locally finite poset P with Mobius function mu: "
              "g(x) = sum_{y<=x} f(y) iff f(x) = sum_{y<=x} mu(y,x) g(y); this unifies "
              "inclusion-exclusion and number-theoretic Mobius inversion.",
              PropositionKind.THEOREM, "combinatorics", importance=0.92,
              tags=("mobius-inversion", "poset", "inclusion-exclusion")),
        _prop("Erdos-Ko-Rado Theorem",
              "If F is an intersecting k-family of subsets of [n] with n>=2k, then "
              "|F| <= C(n-1,k-1); equality holds only for stars (all sets through a fixed element).",
              PropositionKind.THEOREM, "combinatorics", importance=0.88,
              tags=("erdos-ko-rado", "intersecting-family")),
    ],
    keywords=("enumeration", "graph", "partition", "generating-function", "Ramsey", "poset", "symmetric-functions"),
    judgment_site=(
        "Combinatorics counts discrete judgment configurations; Mobius inversion inverts "
        "the judgment accumulation map, Ramsey theory guarantees unavoidable patterns in "
        "large systems, and generating functions encode the analytic geometry of enumeration."
    ),
)

# ---------------------------------------------------------------------------
# Field 24: Graph Theory
# ---------------------------------------------------------------------------
_GRAPH_THEORY = _field(
    name="Graph Theory",
    description=(
        "Graph theory studies discrete structures of vertices and edges, with applications "
        "from network analysis to topological combinatorics. Euler formula, the four color "
        "theorem, Menger theorem, Turan theorem, and the Robertson-Seymour graph minor "
        "theorem are foundational results."
    ),
    props=[
        _prop("Four Color Theorem",
              "Every planar graph is 4-colorable; vertices of any graph embedded in the "
              "sphere can be colored with 4 colors so adjacent vertices differ, proved by "
              "Appel-Haken 1976 using computer assistance.",
              PropositionKind.THEOREM, "graph_theory", importance=0.95,
              tags=("four-color", "planar", "chromatic")),
        _prop("Euler Formula",
              "For a connected planar graph: V - E + F = 2 where V,E,F are vertices, edges, "
              "and faces; this computes the Euler characteristic of the sphere and classifies "
              "the five Platonic solids.",
              PropositionKind.THEOREM, "graph_theory", importance=0.96,
              tags=("euler-formula", "planar", "euler-characteristic")),
        _prop("Menger Theorem",
              "The maximum number of internally vertex-disjoint s-t paths equals the minimum "
              "vertex cut separating s from t; the edge version gives the max-flow min-cut "
              "theorem.",
              PropositionKind.THEOREM, "graph_theory", importance=0.93,
              tags=("menger", "max-flow", "connectivity")),
        _prop("Robertson-Seymour Graph Minor Theorem",
              "In any infinite sequence of graphs, one is a minor of another; every "
              "minor-closed class is characterized by finitely many forbidden minors, "
              "proved by Robertson-Seymour 2004.",
              PropositionKind.THEOREM, "graph_theory", importance=0.94,
              tags=("graph-minor", "Robertson-Seymour", "well-quasi-order")),
    ],
    keywords=("vertex", "edge", "planar", "coloring", "connectivity", "minor", "chromatic-polynomial"),
    judgment_site=(
        "Graphs are the skeletal judgment networks; vertices are judgment nodes and edges "
        "are direct inference steps; planarity constrains realizable judgment geometries, "
        "and graph minors capture essential network topology."
    ),
)


# ---------------------------------------------------------------------------
# Field 25: Matroid Theory
# ---------------------------------------------------------------------------
_MATROID_THEORY = _field(
    name="Matroid Theory",
    description=(
        "Matroid theory abstracts linear independence to arbitrary combinatorial settings, "
        "capturing the common structure of vector spaces, forests in graphs, and transversals. "
        "Whitney theorem, Tutte polynomial, matroid intersection, and the proof of Rota's "
        "conjecture via Hodge theory are landmark results."
    ),
    props=[
        _prop("Matroid Intersection Theorem",
              "A maximum-weight common independent set of two matroids M1,M2 on the same "
              "ground set can be found in polynomial time; this subsumes bipartite matching "
              "and minimum spanning arborescence.",
              PropositionKind.THEOREM, "matroid_theory", importance=0.93,
              tags=("matroid-intersection", "optimization", "polynomial-time")),
        _prop("Tutte Polynomial",
              "T_M(x,y) encodes all deletion-contraction invariants of a matroid; specializes "
              "to the chromatic polynomial (x=1-t), reliability polynomial, Potts model "
              "partition function, and Jones polynomial.",
              PropositionKind.DEFINITION, "matroid_theory", importance=0.92,
              tags=("tutte-polynomial", "deletion-contraction")),
        _prop("Rota Conjecture (Adiprasito-Huh-Katz)",
              "The coefficients of the characteristic polynomial of any matroid form a "
              "log-concave sequence; proved 2015 using Hodge theory for matroids and the "
              "combinatorial Hodge-Riemann bilinear relation.",
              PropositionKind.THEOREM, "matroid_theory", importance=0.96,
              tags=("rota-conjecture", "log-concavity", "Hodge-theory")),
        _prop("Cryptomorphisms of Matroids",
              "A matroid is equivalently defined by its independent sets, bases, circuits, "
              "rank function, closure operator, or flat lattice; these cryptomorphic "
              "definitions reveal the structure's richly polyvalent algebraic nature.",
              PropositionKind.DEFINITION, "matroid_theory", importance=0.85,
              tags=("cryptomorphism", "rank-function", "circuits")),
    ],
    keywords=("independence", "rank", "circuits", "Tutte-polynomial", "minor", "log-concavity", "Hodge"),
    judgment_site=(
        "Matroids are the geometry of abstract independence; the rank function measures "
        "dimensionality of a judgment collection, circuits mark minimal dependencies, "
        "and the Tutte polynomial encodes the full partition function of judgment independence."
    ),
)

# ---------------------------------------------------------------------------
# Field 26: Order Theory
# ---------------------------------------------------------------------------
_ORDER_THEORY = _field(
    name="Order Theory",
    description=(
        "Order theory studies partially and totally ordered sets, lattices, Galois connections, "
        "and well-orders. Dilworth theorem, Zorn's lemma, Birkhoff representation theorem, "
        "and the Knaster-Tarski fixed-point theorem are central."
    ),
    props=[
        _prop("Dilworth Theorem",
              "In a finite poset P, the minimum number of chains covering P equals the maximum "
              "antichain size; dually the minimum antichain cover equals the longest chain "
              "length (Mirsky theorem).",
              PropositionKind.THEOREM, "order_theory", importance=0.94,
              tags=("dilworth", "chain", "antichain")),
        _prop("Zorn Lemma",
              "If every chain in a non-empty poset P has an upper bound in P then P has a "
              "maximal element; Zorn's lemma is equivalent to the Axiom of Choice and the "
              "Well-Ordering Theorem.",
              PropositionKind.THEOREM, "order_theory", importance=0.97,
              tags=("zorns-lemma", "axiom-of-choice")),
        _prop("Birkhoff Representation Theorem",
              "Every finite distributive lattice L is isomorphic to J(P) -- the lattice of "
              "order ideals of its poset of join-irreducibles; this gives a duality between "
              "finite distributive lattices and finite posets.",
              PropositionKind.THEOREM, "order_theory", importance=0.93,
              tags=("birkhoff", "distributive-lattice", "order-ideals")),
        _prop("Knaster-Tarski Fixed Point Theorem",
              "Every monotone f on a complete lattice L has a fixed point; Fix(f) forms a "
              "complete lattice with least fixed point mu f = meet{x: f(x)<=x} and greatest "
              "fixed point nu f = join{x: x<=f(x)}.",
              PropositionKind.THEOREM, "order_theory", importance=0.92,
              tags=("tarski", "fixed-point", "complete-lattice")),
    ],
    keywords=("partial-order", "lattice", "chain", "antichain", "Galois-connection", "fixed-point", "well-order"),
    judgment_site=(
        "Ordered sets are the judgment precedence structures; chains are sequences of "
        "logically dependent judgments, antichains are mutually incomparable judgment sets, "
        "and Galois connections encode the duality between question-formation and evidence-gathering."
    ),
)

# ---------------------------------------------------------------------------
# Field 27: Lattice Theory
# ---------------------------------------------------------------------------
_LATTICE_THEORY = _field(
    name="Lattice Theory",
    description=(
        "Lattice theory studies algebraic structures with join and meet satisfying absorption "
        "and associativity, generalizing set-theoretic union and intersection. Distributive, "
        "modular, and orthomodular lattices arise in logic, geometry, and quantum mechanics."
    ),
    props=[
        _prop("Stone Representation Theorem",
              "Every Boolean algebra B is isomorphic to the clopen-set algebra of its Stone "
              "space X=Spec(B), a compact totally disconnected Hausdorff space; Stone duality "
              "is the archetype of categorical algebra-geometry duality.",
              PropositionKind.THEOREM, "lattice_theory", importance=0.97,
              tags=("stone-duality", "boolean-algebra", "profinite")),
        _prop("Priestley Duality",
              "The category of bounded distributive lattices is dually equivalent to Priestley "
              "spaces (compact totally order-disconnected); Heyting algebras correspond to "
              "Esakia spaces, providing duality for intuitionistic logic.",
              PropositionKind.THEOREM, "lattice_theory", importance=0.90,
              tags=("Priestley-duality", "Heyting-algebra", "intuitionistic")),
        _prop("Orthomodular Lattice and Quantum Logic",
              "The projection lattice P(H) of a Hilbert space is orthomodular: P<=Q implies "
              "P join (P-perp meet Q)=Q but the distributive law fails; Birkhoff-von Neumann "
              "proposed this as the lattice of quantum propositions.",
              PropositionKind.DEFINITION, "lattice_theory", importance=0.91,
              tags=("orthomodular", "quantum-logic", "projections")),
        _prop("Dedekind Numbers",
              "The free distributive lattice on n generators has size equal to the Dedekind "
              "number D(n) (monotone Boolean functions on n variables); D(8)=56130437228687557907788 "
              "was computed in 2023, closing a long-open problem.",
              PropositionKind.THEOREM, "lattice_theory", importance=0.83,
              tags=("dedekind-numbers", "free-distributive-lattice")),
    ],
    keywords=("join", "meet", "distributive", "modular", "boolean-algebra", "Stone-duality", "orthomodular"),
    judgment_site=(
        "Lattices are algebraic models of judgment combination; join is the weakest judgment "
        "following from either input, meet is the strongest implied by both, and Stone duality "
        "reveals Boolean judgment algebras as clopen-region algebras of a profinite space."
    ),
)

# ---------------------------------------------------------------------------
# Field 28: Universal Algebra
# ---------------------------------------------------------------------------
_UNIVERSAL_ALGEBRA = _field(
    name="Universal Algebra",
    description=(
        "Universal algebra studies algebraic structures abstractly via signatures, varieties, "
        "term algebras, and congruences, unifying groups, rings, lattices, and modules. "
        "Birkhoff's HSP theorem, Maltsev conditions, and tame congruence theory are central."
    ),
    props=[
        _prop("Birkhoff HSP Theorem",
              "A class K of same-signature algebras is a variety (equationally definable) iff "
              "K is closed under homomorphic images (H), subalgebras (S), and direct products "
              "(P); this is the fundamental theorem of equational logic.",
              PropositionKind.THEOREM, "universal_algebra", importance=0.97,
              tags=("Birkhoff-HSP", "variety", "equational-logic")),
        _prop("Maltsev Condition",
              "A variety V has permuting congruences iff there is a ternary term p with "
              "p(x,y,y)=x and p(x,x,y)=y; this Maltsev condition detects group-like structure "
              "and is the paradigm for characterizing congruence properties by term conditions.",
              PropositionKind.THEOREM, "universal_algebra", importance=0.93,
              tags=("Maltsev", "congruence-permutability")),
        _prop("Free Algebra and Term Algebra",
              "For signature Sigma and variable set X, the term algebra T_Sigma(X) is the "
              "free Sigma-algebra on X; any map X->A to a Sigma-algebra A extends uniquely "
              "to a homomorphism T_Sigma(X)->A.",
              PropositionKind.THEOREM, "universal_algebra", importance=0.89,
              tags=("free-algebra", "term-algebra", "universal-property")),
        _prop("Subdirectly Irreducible Algebras",
              "Every algebra is a subdirect product of subdirectly irreducible algebras "
              "(Birkhoff 1944); a subdirectly irreducible algebra has a unique smallest "
              "non-trivial congruence, making these the atomic building blocks of a variety.",
              PropositionKind.THEOREM, "universal_algebra", importance=0.86,
              tags=("subdirect-product", "subdirectly-irreducible")),
    ],
    keywords=("variety", "congruence", "term-algebra", "homomorphism", "equational-logic", "Maltsev", "HSP"),
    judgment_site=(
        "Universal algebra is the equational logic of judgment; a variety is a class of "
        "judgment algebras defined by the equations they satisfy, and Birkhoff's theorem "
        "identifies the closure conditions characterizing valid judgment model classes."
    ),
)

# ---------------------------------------------------------------------------
# Field 29: Model Theory
# ---------------------------------------------------------------------------
_MODEL_THEORY = _field(
    name="Model Theory",
    description=(
        "Model theory studies the relationship between formal first-order theories and their "
        "mathematical models using compactness, elementary equivalence, and stability. "
        "Lowenheim-Skolem, Morley's categoricity theorem, and Shelah's classification "
        "theory are central results."
    ),
    props=[
        _prop("Compactness Theorem",
              "A set T of first-order sentences has a model iff every finite T0 subset of T "
              "has a model; compactness follows from completeness via ultraproducts and is "
              "the workhorse for constructing non-standard models.",
              PropositionKind.THEOREM, "model_theory", importance=0.98,
              tags=("compactness", "finitary", "ultraproduct")),
        _prop("Lowenheim-Skolem Theorem",
              "If a countable theory T has an infinite model it has models of every infinite "
              "cardinality; no countable theory can categorically characterize uncountable "
              "structures (Skolem paradox).",
              PropositionKind.THEOREM, "model_theory", importance=0.96,
              tags=("Lowenheim-Skolem", "cardinality")),
        _prop("Morley Categoricity Theorem",
              "If a complete theory in a countable language is categorical in some uncountable "
              "cardinal kappa, it is categorical in all uncountable cardinals; Morley's 1965 "
              "theorem founded stability theory.",
              PropositionKind.THEOREM, "model_theory", importance=0.97,
              tags=("Morley", "categoricity", "stability")),
        _prop("Shelah Classification Theory",
              "A complete theory T is stable iff it has no two-cardinal model; stable theories "
              "fall in the hierarchy omega-stable -> superstable -> stable, each level "
              "permitting increasingly detailed structure theorems.",
              PropositionKind.THEOREM, "model_theory", importance=0.93,
              tags=("stability", "Shelah", "classification")),
    ],
    keywords=("model", "theory", "compactness", "categoricity", "stability", "type", "ultraproduct"),
    judgment_site=(
        "Model theory studies judgment satisfiability: a model is a world where all current "
        "judgments are simultaneously true, compactness says local consistency implies global "
        "consistency, and stability classifies theories by richness of their judgment type spaces."
    ),
)

# ---------------------------------------------------------------------------
# Field 30: Proof Theory
# ---------------------------------------------------------------------------
_PROOF_THEORY = _field(
    name="Proof Theory",
    description=(
        "Proof theory studies formal proofs as combinatorial objects, investigating their "
        "structure, transformations, and logical content. Gentzen's cut elimination, Godel's "
        "incompleteness theorems, ordinal analysis, and the Curry-Howard-Lambek correspondence "
        "are landmark results."
    ),
    props=[
        _prop("Gentzen Cut Elimination",
              "In the sequent calculus LK (resp. LJ for intuitionistic), every provable sequent "
              "has a cut-free proof; the Hauptsatz establishes the subformula property and is "
              "key to consistency proofs and proof-search.",
              PropositionKind.THEOREM, "proof_theory", importance=0.99,
              tags=("cut-elimination", "Gentzen", "subformula-property")),
        _prop("Godel Second Incompleteness Theorem",
              "Any consistent formal system F containing enough arithmetic cannot prove Con(F); "
              "the unprovability is witnessed by a specific Pi_1 sentence encoding the "
              "non-existence of a proof of contradiction in F.",
              PropositionKind.THEOREM, "proof_theory", importance=0.99,
              tags=("Godel", "incompleteness", "consistency")),
        _prop("Gentzen Consistency Proof",
              "The consistency of Peano Arithmetic PA is provable using transfinite induction "
              "up to epsilon_0 (the least ordinal fixed point of omega^x=x); the proof-theoretic "
              "ordinal of PA is exactly epsilon_0.",
              PropositionKind.THEOREM, "proof_theory", importance=0.94,
              tags=("ordinal-analysis", "PA", "epsilon-zero")),
        _prop("Curry-Howard-Lambek Correspondence",
              "There is a three-way equivalence: intuitionistic propositions-as-types, "
              "simply-typed lambda calculus (proofs as programs), and morphisms in a cartesian "
              "closed category; cut elimination = beta-reduction = composition.",
              PropositionKind.THEOREM, "proof_theory", importance=0.98,
              tags=("Curry-Howard-Lambek", "CCC", "normalization")),
    ],
    keywords=("cut-elimination", "proof", "sequent-calculus", "Godel", "ordinal", "consistency", "normalization"),
    judgment_site=(
        "Proof theory is the intrinsic geometry of judgment derivation; a proof is a tree "
        "of judgment steps, cut elimination removes circular justifications, and ordinal "
        "analysis measures the well-foundedness depth of the derivation system."
    ),
)

# ---------------------------------------------------------------------------
# Field 31: Recursion Theory
# ---------------------------------------------------------------------------
_RECURSION_THEORY = _field(
    name="Recursion Theory",
    description=(
        "Recursion theory studies the class of algorithmically computable functions and the "
        "hierarchy of undecidable problems. Church's thesis, the halting problem, Rice's "
        "theorem, Kleene's recursion theorem, and the arithmetical hierarchy are central."
    ),
    props=[
        _prop("Church-Turing Thesis",
              "Every effectively computable function is Turing-computable; the Turing machine, "
              "lambda-calculus, mu-recursive functions, and Post systems all define the same "
              "class, suggesting an absolute notion of computability.",
              PropositionKind.AXIOM, "recursion_theory", importance=0.99,
              tags=("church-turing", "computability")),
        _prop("Halting Problem is Undecidable",
              "There is no Turing machine that decides whether an arbitrary machine M halts on "
              "input w; the proof by diagonalization is the paradigmatic undecidability argument "
              "to which all other undecidable problems reduce.",
              PropositionKind.THEOREM, "recursion_theory", importance=0.98,
              tags=("halting-problem", "undecidability", "diagonalization")),
        _prop("Rice Theorem",
              "Every non-trivial semantic property of the partial function computed by a Turing "
              "machine is undecidable; the only decidable index properties are the trivial ones "
              "(all machines or no machines).",
              PropositionKind.THEOREM, "recursion_theory", importance=0.94,
              tags=("rice", "undecidability", "semantic")),
        _prop("Kleene Recursion Theorem",
              "For any total computable function f there exists index e with phi_e = phi_{f(e)}; "
              "every effective transformation of programs has a fixed-point program, making "
              "self-referential programs inevitable.",
              PropositionKind.THEOREM, "recursion_theory", importance=0.91,
              tags=("kleene-recursion", "fixed-point", "self-reference")),
        _prop("Arithmetical Hierarchy",
              "The arithmetical hierarchy stratifies definable sets of naturals by quantifier "
              "alternation: Sigma^0_n sets are defined by n-1 alternating blocks starting with "
              "existential; the halting problem is Sigma^0_1-complete.",
              PropositionKind.DEFINITION, "recursion_theory", importance=0.89,
              tags=("arithmetical-hierarchy", "definability", "quantifiers")),
    ],
    keywords=("computability", "Turing-machine", "halting-problem", "recursive", "undecidable", "oracle", "degrees"),
    judgment_site=(
        "Computability theory defines the boundary of mechanically verifiable judgment; the "
        "halting problem is the prototype undecidable judgment, and the arithmetical hierarchy "
        "stratifies the complexity geometry of judgment definability."
    ),
)

# ---------------------------------------------------------------------------
# Field 32: Complexity Theory
# ---------------------------------------------------------------------------
_COMPLEXITY_THEORY = _field(
    name="Complexity Theory",
    description=(
        "Complexity theory classifies computational problems by resource requirements -- time, "
        "space, randomness -- and studies relative difficulty via reductions. The P vs NP "
        "question, Cook-Levin theorem, PCP theorem, and time hierarchy theorem are cornerstones."
    ),
    props=[
        _prop("Cook-Levin Theorem",
              "SAT is NP-complete: every problem in NP reduces in polynomial time to SAT; "
              "this established the theory of NP-completeness and the central role of SAT "
              "as the canonical hard decision problem.",
              PropositionKind.THEOREM, "complexity_theory", importance=0.99,
              tags=("Cook-Levin", "NP-completeness", "SAT")),
        _prop("P vs NP Conjecture",
              "The class P of polynomial-time decidable problems is widely conjectured to be "
              "a strict subset of NP (polynomial-time verifiable); this is the leading open "
              "problem in theoretical computer science.",
              PropositionKind.CONJECTURE, "complexity_theory", importance=0.99,
              tags=("P-vs-NP", "millennium", "open-problem")),
        _prop("Time Hierarchy Theorem",
              "For time-constructible f,g with f(n) log f(n) = o(g(n)): "
              "DTIME(f(n)) is a strict subset of DTIME(g(n)); more time strictly increases "
              "computational power, ruling out collapse of the time hierarchy.",
              PropositionKind.THEOREM, "complexity_theory", importance=0.94,
              tags=("time-hierarchy", "diagonalization")),
        _prop("PCP Theorem",
              "NP = PCP(log n, O(1)): every NP proof can be probabilistically checked using "
              "O(log n) random bits and constant query complexity; this implies approximation "
              "hardness for many optimization problems.",
              PropositionKind.THEOREM, "complexity_theory", importance=0.97,
              tags=("PCP", "inapproximability", "probabilistic-proof")),
    ],
    keywords=("P", "NP", "polynomial-time", "reduction", "NP-complete", "circuit", "randomness", "SAT"),
    judgment_site=(
        "Complexity theory measures the computational geometry of judgment difficulty; NP "
        "captures judgments verifiable by a polynomial-time witness, and P vs NP asks whether "
        "finding and checking judgments are equally hard."
    ),
)

# ---------------------------------------------------------------------------
# Field 33: Lambda Calculus
# ---------------------------------------------------------------------------
_LAMBDA_CALCULUS = _field(
    name="Lambda Calculus",
    description=(
        "Lambda calculus is the minimal Turing-complete model of computation based on anonymous "
        "function abstraction and application, serving as the theoretical foundation for "
        "functional programming. Church-Rosser, Y combinator, and Bohm's theorem are key results."
    ),
    props=[
        _prop("Church-Rosser Theorem",
              "The lambda-calculus satisfies the diamond property: if M ->* N1 and M ->* N2 "
              "then there exists L with N1 ->* L and N2 ->* L; this implies uniqueness of "
              "normal forms whenever they exist.",
              PropositionKind.THEOREM, "lambda_calculus", importance=0.97,
              tags=("church-rosser", "confluence", "normal-form")),
        _prop("Y Combinator (Fixed-Point Combinator)",
              "For any lambda-term F, Y F = F(Y F) where Y = lambda f.(lambda x.f(x x))(lambda x.f(x x)); "
              "every function has a fixed point in pure lambda-calculus, enabling recursive "
              "definitions without explicit recursion.",
              PropositionKind.THEOREM, "lambda_calculus", importance=0.95,
              tags=("Y-combinator", "fixed-point", "recursion")),
        _prop("Bohm Theorem",
              "If M != N are distinct closed normal forms in lambda-calculus, then there exists "
              "a context C[.] such that C[M] reduces to True and C[N] reduces to False; "
              "distinct normal forms are observationally distinguishable.",
              PropositionKind.THEOREM, "lambda_calculus", importance=0.88,
              tags=("bohm", "observational-equivalence", "normal-forms")),
        _prop("Scott-Curry Theorem",
              "No non-trivial property of lambda-terms invariant under beta-eta equality is "
              "decidable; this is the lambda-calculus analogue of Rice's theorem, showing "
              "semantic properties of programs are undecidable.",
              PropositionKind.THEOREM, "lambda_calculus", importance=0.85,
              tags=("Scott-Curry", "undecidability")),
    ],
    keywords=("lambda", "beta-reduction", "normal-form", "combinator", "Church-encoding", "confluence"),
    judgment_site=(
        "Lambda calculus is the geometry of pure judgment transformation; a term is a "
        "judgment context and application is judgment composition; the Y combinator shows "
        "every judgment transformation has a self-referential fixed point."
    ),
)

# ---------------------------------------------------------------------------
# Field 34: Linear Logic
# ---------------------------------------------------------------------------
_LINEAR_LOGIC = _field(
    name="Linear Logic",
    description=(
        "Linear logic, introduced by Girard, is a resource-sensitive refinement of classical "
        "logic where propositions are consumed when used. The !-modality controls reuse, "
        "coherence spaces provide denotational semantics, and the geometry of interaction "
        "gives a dynamic account of cut elimination."
    ),
    props=[
        _prop("Cut Elimination in Linear Logic",
              "Linear logic's sequent calculus admits cut elimination; cut-free proofs enjoy "
              "the subformula property, and proof net normalization corresponds to parallel "
              "reduction of multiplicative proof nets.",
              PropositionKind.THEOREM, "linear_logic", importance=0.97,
              tags=("cut-elimination", "proof-nets")),
        _prop("Exponential Modality",
              "The !A modality ('of course A') in linear logic allows unlimited use of A; "
              "dereliction !A-oA, weakening !A-o1, and contraction !A-o!A tensor !A restore "
              "the structural rules, recovering intuitionistic logic.",
              PropositionKind.DEFINITION, "linear_logic", importance=0.93,
              tags=("exponential", "modality", "structural-rules")),
        _prop("Coherence Spaces",
              "A coherence space X=(|X|,coh_X) with reflexive symmetric coherence relation "
              "provides a denotational model for linear logic; stable linear maps between "
              "coherence spaces interpret proofs as functionals.",
              PropositionKind.DEFINITION, "linear_logic", importance=0.88,
              tags=("coherence-spaces", "denotational-semantics")),
        _prop("Geometry of Interaction",
              "Girard's GoI interprets proofs as partial isometries in a C*-algebra; "
              "cut elimination corresponds to executing the feedback operator "
              "exe(sigma,rho) = sigma(1-rho sigma)^{-1} rho, giving a dynamic account of computation.",
              PropositionKind.THEOREM, "linear_logic", importance=0.90,
              tags=("GoI", "C*-algebra", "execution")),
    ],
    keywords=("linear-logic", "resource-sensitivity", "proof-nets", "exponential", "coherence-spaces", "GoI"),
    judgment_site=(
        "Linear logic is the geometry of resource-aware judgment; each hypothesis is a "
        "judgment token consumed by use, the !-modality marks permanently available judgment "
        "resources, and proof nets visualize the causal flow of judgment energy."
    ),
)

# ---------------------------------------------------------------------------
# Field 35: Modal Logic
# ---------------------------------------------------------------------------
_MODAL_LOGIC = _field(
    name="Modal Logic",
    description=(
        "Modal logic extends propositional logic with possibility and necessity operators, "
        "interpreted over Kripke frames of possible worlds. The completeness theorem, S4 "
        "and intuitionistic logic correspondence, and bisimulation invariance are central results."
    ),
    props=[
        _prop("Kripke Completeness Theorem",
              "The normal modal logic K is sound and complete with respect to the class of "
              "all Kripke frames; S4 (reflexive transitive) is complete for preorders, and "
              "S5 (equivalence relations) for the universal modality.",
              PropositionKind.THEOREM, "modal_logic", importance=0.97,
              tags=("kripke", "completeness", "possible-worlds")),
        _prop("Godel S4-Intuitionistic Translation",
              "Intuitionistic propositional logic IPC embeds faithfully into S4 via the "
              "translation prefixing each subformula with box; IPC is complete for S4 over "
              "topological spaces with box interpreted as topological interior.",
              PropositionKind.THEOREM, "modal_logic", importance=0.93,
              tags=("Godel-translation", "S4", "intuitionistic", "topology")),
        _prop("Modal Correspondence Theory",
              "Reflexivity (forall x R(x,x)) corresponds to axiom T: box phi -> phi; "
              "transitivity corresponds to axiom 4: box phi -> box box phi; the correspondence "
              "between modal axioms and first-order frame conditions is systematic (van Benthem).",
              PropositionKind.THEOREM, "modal_logic", importance=0.90,
              tags=("correspondence-theory", "van-Benthem", "frame-conditions")),
        _prop("Bisimulation Invariance",
              "Modal formulas are invariant under bisimulation: if two Kripke models are "
              "bisimilar at a world then that world satisfies the same modal formulas in both; "
              "van Benthem's theorem: bisimulation-invariant first-order formulas are exactly "
              "the modally definable ones.",
              PropositionKind.THEOREM, "modal_logic", importance=0.88,
              tags=("bisimulation", "van-Benthem-theorem")),
    ],
    keywords=("necessity", "possibility", "Kripke-frame", "accessibility", "bisimulation", "S4", "S5"),
    judgment_site=(
        "Modal logic is the geometry of epistemic accessibility; box phi means phi holds at "
        "all accessible judgment sites, diamond phi at some site; bisimulation invariance "
        "captures that modal judgment cannot distinguish structurally equivalent worlds."
    ),
)

# ---------------------------------------------------------------------------
# Field 36: Dependent Type Theory
# ---------------------------------------------------------------------------
_DEPENDENT_TYPE_THEORY = _field(
    name="Dependent Type Theory",
    description=(
        "Dependent type theory is the foundation for modern proof assistants (Coq, Agda, Lean) "
        "featuring types that depend on values (Pi-types and Sigma-types), identity types, "
        "and a universe hierarchy. Canonicity, normalization by evaluation, and the setoid "
        "model are key results."
    ),
    props=[
        _prop("Martin-Lof Identity Type",
              "For any type A and a:A, the identity type Id_A(a,a) has canonical inhabitant "
              "refl_a; path induction (J-rule) says any property P(b,p) of a path p:Id_A(a,b) "
              "follows from P(a,refl_a), making equality eliminable.",
              PropositionKind.DEFINITION, "dependent_type_theory", importance=0.97,
              tags=("identity-type", "path-induction", "J-rule")),
        _prop("Pi-types and Sigma-types",
              "Pi(x:A).B(x) is the type of dependent functions; Sigma(x:A).B(x) is the type "
              "of dependent pairs; together they provide universal and existential quantification "
              "and form the core of dependent type theory.",
              PropositionKind.DEFINITION, "dependent_type_theory", importance=0.95,
              tags=("Pi-type", "Sigma-type", "dependent-quantification")),
        _prop("Canonicity Theorem",
              "In Martin-Lof type theory, every closed term of type N reduces to a numeral; "
              "this ensures the theory is computationally meaningful and its proofs carry "
              "extractable computational content.",
              PropositionKind.THEOREM, "dependent_type_theory", importance=0.92,
              tags=("canonicity", "normal-form", "computational-content")),
        _prop("Normalization by Evaluation",
              "NbE converts a type theory's normalizer into an evaluator by evaluating terms "
              "into semantic values and reading them back; this provides a completeness argument "
              "for definitional equality and a clean practical implementation.",
              PropositionKind.THEOREM, "dependent_type_theory", importance=0.88,
              tags=("NbE", "normalization", "semantics")),
    ],
    keywords=("dependent-types", "Pi-type", "Sigma-type", "identity-type", "proof-assistant", "universes", "Coq"),
    judgment_site=(
        "Dependent type theory is the canonical judgment-site formalism; every judgment "
        "is a typing judgment Gamma |- t : A; Pi-types encode universal judgment, Sigma-types "
        "existential judgment, and identity types record judgment paths (equalities)."
    ),
)

# ---------------------------------------------------------------------------
# Field 37: Homological Algebra
# ---------------------------------------------------------------------------
_HOMOLOGICAL_ALGEBRA = _field(
    name="Homological Algebra",
    description=(
        "Homological algebra studies chain complexes, exact sequences, and derived functors "
        "(Ext and Tor) using resolutions and spectral sequences. It underpins algebraic "
        "topology, algebraic geometry, and representation theory, and is the technical heart "
        "of sheaf cohomology and derived categories."
    ),
    props=[
        _prop("Long Exact Sequence of a Pair",
              "A short exact sequence 0->A_->A->A__->0 of chain maps induces a long exact "
              "sequence ...->H_n(A_)->H_n(A)->H_n(A__)->H_{n-1}(A_)->...; the connecting "
              "homomorphism delta is natural.",
              PropositionKind.THEOREM, "homological_algebra", importance=0.97,
              tags=("long-exact-sequence", "connecting-homomorphism")),
        _prop("Horseshoe Lemma",
              "Given 0->A_->A->A__->0 and projective resolutions P_->A_ and P__->A__, "
              "there exists a projective resolution P->A fitting into 0->P_->P->P__->0.",
              PropositionKind.LEMMA, "homological_algebra", importance=0.88,
              tags=("horseshoe", "projective-resolution")),
        _prop("Ext and Tor Functors",
              "Ext^n_R(M,N) is the n-th right derived functor of Hom_R(M,-); "
              "Tor_n^R(M,N) is the n-th left derived functor of M tensor_R -; "
              "Ext^1 classifies extensions and Tor_1 detects flatness.",
              PropositionKind.DEFINITION, "homological_algebra", importance=0.95,
              tags=("Ext", "Tor", "derived-functors")),
        _prop("Grothendieck Spectral Sequence",
              "For composable functors F:A->B and G:B->C with F carrying injectives to "
              "G-acyclics, there is a spectral sequence E_2^{p,q}=(R^p G)(R^q F)(A) converging "
              "to R^{p+q}(GF)(A).",
              PropositionKind.THEOREM, "homological_algebra", importance=0.93,
              tags=("Grothendieck-spectral-sequence", "derived-functors")),
    ],
    keywords=("chain-complex", "exact-sequence", "Ext", "Tor", "resolution", "spectral-sequence", "derived-functor"),
    judgment_site=(
        "Homological algebra measures obstructions to exactness in judgment sequences; Ext "
        "groups classify ways a judgment can be extended, and spectral sequences compute "
        "the homological complexity of composed judgment operations."
    ),
)

# ---------------------------------------------------------------------------
# Field 38: K-Theory
# ---------------------------------------------------------------------------
_K_THEORY = _field(
    name="K-Theory",
    description=(
        "K-theory studies vector bundles (topological K-theory) and projective modules "
        "(algebraic K-theory) as invariants of spaces and rings. Bott periodicity, the "
        "Atiyah-Singer index theorem, Adams operations, and Swan theorem connect K-theory "
        "to analysis, geometry, and algebra."
    ),
    props=[
        _prop("Bott Periodicity",
              "Complex K-theory satisfies K-tilde^n(X) = K-tilde^{n+2}(X) (2-periodicity); "
              "real K-theory satisfies 8-periodicity; this makes K-theory computable and "
              "is the foundation of the Atiyah-Singer index theorem.",
              PropositionKind.THEOREM, "k_theory", importance=0.98,
              tags=("bott-periodicity", "K-theory", "periodicity")),
        _prop("Atiyah-Singer Index Theorem",
              "For an elliptic differential operator D on compact manifold M, "
              "ind(D) = integral_M ch(sigma(D)) td(TM) where ch is the Chern character "
              "and td is the Todd class; the analytical index equals the topological index.",
              PropositionKind.THEOREM, "k_theory", importance=0.99,
              tags=("Atiyah-Singer", "index-theorem", "elliptic-operator")),
        _prop("Adams Operations",
              "For each k>=1, Adams operations psi^k: K(X)->K(X) are ring homomorphisms "
              "with psi^k(L)=L^{tensor k} on line bundles; they encode the lambda-ring "
              "structure and detect torsion in homotopy groups of spheres.",
              PropositionKind.DEFINITION, "k_theory", importance=0.88,
              tags=("adams-operations", "lambda-ring")),
        _prop("Swan Theorem",
              "K^0(X) = K_0(C(X)): the topological K-theory equals the algebraic K_0 of "
              "the ring of continuous functions; more generally K_0 of a ring classifies "
              "finitely generated projective modules up to stable isomorphism.",
              PropositionKind.THEOREM, "k_theory", importance=0.90,
              tags=("Swan", "projective-modules", "Grothendieck-group")),
    ],
    keywords=("vector-bundle", "K-group", "Bott-periodicity", "index-theorem", "Adams-operations", "algebraic-K"),
    judgment_site=(
        "K-theory measures the stable classification of judgment bundles; Bott periodicity "
        "shows the judgment bundle landscape repeats with period 2 (or 8), and the index "
        "theorem computes the net number of independent judgment modes of an elliptic operator."
    ),
)

# ---------------------------------------------------------------------------
# Field 39: Cobordism Theory
# ---------------------------------------------------------------------------
_COBORDISM_THEORY = _field(
    name="Cobordism Theory",
    description=(
        "Cobordism theory studies manifolds up to the equivalence of bounding a manifold of "
        "one higher dimension. Thom theorem, the Pontryagin-Thom construction, the cobordism "
        "ring, and Lurie cobordism hypothesis connecting TQFTs to higher categories are central."
    ),
    props=[
        _prop("Thom Theorem",
              "Every mod-2 homology class in H_*(X; Z/2) can be represented by a smooth "
              "manifold map; unoriented cobordism is computed via the Thom spectrum MO and "
              "the cobordism ring Omega_*^O = (Z/2)[x_2, x_4, x_5, ...].",
              PropositionKind.THEOREM, "cobordism_theory", importance=0.95,
              tags=("Thom", "cobordism-ring", "Thom-spectrum")),
        _prop("Pontryagin-Thom Construction",
              "Framed cobordism classes of n-manifolds in R^{n+k} biject with pi_{n+k}(S^k) "
              "via the collapse map; this equates geometric cobordism with stable homotopy "
              "groups of spheres.",
              PropositionKind.THEOREM, "cobordism_theory", importance=0.97,
              tags=("Pontryagin-Thom", "framed-cobordism", "stable-homotopy")),
        _prop("Cobordism Ring",
              "The oriented cobordism ring Omega_*^SO tensored with Q is a polynomial ring "
              "Q[CP^2, CP^4, ...]; complex cobordism MU_* = Z[x_1, x_2, ...] with |x_n|=2n "
              "(Milnor-Quillen theorem).",
              PropositionKind.THEOREM, "cobordism_theory", importance=0.90,
              tags=("cobordism-ring", "oriented", "complex-cobordism")),
        _prop("Cobordism Hypothesis (Lurie)",
              "The (inf,n)-category of fully dualizable objects in a symmetric monoidal "
              "(inf,n)-category C classifies framed fully extended TQFTs valued in C; "
              "this is the universal property of the framed bordism (inf,n)-category.",
              PropositionKind.THEOREM, "cobordism_theory", importance=0.94,
              tags=("cobordism-hypothesis", "TQFT", "fully-dualizable")),
    ],
    keywords=("cobordism", "manifold", "TQFT", "Pontryagin-Thom", "framed", "stable-homotopy", "bordism"),
    judgment_site=(
        "Cobordism theory classifies judgment geometries up to bounding; two judgment manifolds "
        "are equivalent if they bound a higher-dimensional judgment space, and TQFTs assign "
        "algebraic invariants functorially to judgment geometries."
    ),
)

# ---------------------------------------------------------------------------
# Field 40: Motivic Cohomology
# ---------------------------------------------------------------------------
_MOTIVIC_COHOMOLOGY = _field(
    name="Motivic Cohomology",
    description=(
        "Motivic cohomology provides a universal cohomology theory for algebraic varieties "
        "interpolating between algebraic K-theory and algebraic cycles. Voevodsky proof of "
        "the Bloch-Kato conjecture, A1-homotopy theory, and higher Chow groups are landmarks."
    ),
    props=[
        _prop("Bloch-Kato Conjecture (Voevodsky)",
              "For any field F and prime l, the Milnor K-theory map K^M_n(F)/l -> "
              "H^n_et(F, mu_l^n) is an isomorphism; Voevodsky proved this using motivic "
              "cohomology and the Bloch-Lichtenbaum spectral sequence.",
              PropositionKind.THEOREM, "motivic_cohomology", importance=0.99,
              tags=("Bloch-Kato", "Milnor-K-theory", "etale-cohomology", "Voevodsky")),
        _prop("A1-Homotopy Theory",
              "Morel-Voevodsky A1-homotopy theory is the homotopy theory of algebraic "
              "varieties where A^1 plays the role of the unit interval; the stable version "
              "SH(k) is the stable motivic homotopy category over field k.",
              PropositionKind.DEFINITION, "motivic_cohomology", importance=0.95,
              tags=("A1-homotopy", "motivic-stable", "Morel-Voevodsky")),
        _prop("Higher Chow Groups",
              "Bloch higher Chow groups CH^n(X,m) provide a concrete model for motivic "
              "cohomology H^{2n-m}(X, Z(n)); the Bloch-Lichtenbaum spectral sequence "
              "E_2^{p,q} = H^{p-q}(F, Z(-q)) => K_{-p-q}(F) connects these to K-theory.",
              PropositionKind.THEOREM, "motivic_cohomology", importance=0.91,
              tags=("higher-Chow", "motivic-cohomology", "K-theory")),
        _prop("Voevodsky Cancellation Theorem",
              "For smooth varieties X,Y over k, the suspension map [X,Y]_A1 -> [Sigma X, Sigma Y]_A1 "
              "is a bijection; this is the motivic analogue of the Freudenthal suspension theorem "
              "and shows the motivic stable category is well-behaved.",
              PropositionKind.THEOREM, "motivic_cohomology", importance=0.88,
              tags=("cancellation", "suspension", "motivic-homotopy")),
    ],
    keywords=("motivic-cohomology", "A1-homotopy", "Milnor-K-theory", "Bloch-Kato", "Chow-groups", "Voevodsky"),
    judgment_site=(
        "Motivic cohomology is the universal judgment cohomology for algebraic varieties; "
        "A1-homotopy treats the affine line as the contractible judgment path, and "
        "Bloch-Kato shows Milnor K-theory captures the full etale judgment topology."
    ),
)

# ---------------------------------------------------------------------------
# Field 41: Derived Categories
# ---------------------------------------------------------------------------
_DERIVED_CATEGORIES = _field(
    name="Derived Categories",
    description=(
        "Derived categories, introduced by Grothendieck and Verdier, are triangulated "
        "categories obtained by inverting quasi-isomorphisms of chain complexes. Verdier "
        "duality, t-structures, Bondal-Orlov reconstruction, and derived Morita theory "
        "are central results."
    ),
    props=[
        _prop("Verdier Duality",
              "For a locally compact space X, the dualizing functor D_X = R Hom(-, omega_X) "
              "where omega_X is the dualizing complex satisfies D_X^2 = id; Verdier duality "
              "generalises Poincare duality to singular spaces.",
              PropositionKind.THEOREM, "derived_categories", importance=0.96,
              tags=("verdier-duality", "dualizing-complex")),
        _prop("Triangulated Category Axioms",
              "A triangulated category has a shift functor [1] and distinguished triangles "
              "X->Y->Z->X[1] satisfying rotation, completion, and octahedral axioms; "
              "the derived category D(A) of an abelian category A is the universal example.",
              PropositionKind.DEFINITION, "derived_categories", importance=0.93,
              tags=("triangulated-category", "distinguished-triangle")),
        _prop("t-Structures and Hearts",
              "A t-structure (D^{<=0}, D^{>=0}) on a triangulated category D determines an "
              "abelian heart A = D^{<=0} intersect D^{>=0}; the standard t-structure on D(A) "
              "has heart A itself.",
              PropositionKind.DEFINITION, "derived_categories", importance=0.90,
              tags=("t-structure", "heart", "abelian-category")),
        _prop("Bondal-Orlov Reconstruction",
              "If X is smooth projective with ample or anti-ample canonical bundle, X can be "
              "reconstructed from D^b(Coh X); any autoequivalence of D^b(Coh X) is a composition "
              "of standard generators (shift, line bundle twist, automorphism).",
              PropositionKind.THEOREM, "derived_categories", importance=0.91,
              tags=("Bondal-Orlov", "reconstruction", "derived-equivalence")),
    ],
    keywords=("derived-category", "triangulated", "t-structure", "Verdier-duality", "quasi-isomorphism", "heart"),
    judgment_site=(
        "Derived categories are the homotopical judgment algebras; quasi-isomorphisms are "
        "judgment equivalences, t-structures stratify judgment complexity, and Verdier "
        "duality expresses the self-duality of judgment geometry on a manifold."
    ),
)

# ---------------------------------------------------------------------------
# Field 42: Infinity Categories
# ---------------------------------------------------------------------------
_INFINITY_CATEGORIES = _field(
    name="Infinity Categories",
    description=(
        "Infinity categories (quasi-categories) are simplicial sets where every inner horn "
        "has a filler, providing a model for (inf,1)-categories. Lurie foundational work, "
        "straightening/unstraightening, and presentable infinity-categories are central."
    ),
    props=[
        _prop("Joyal Model Structure",
              "The category of simplicial sets carries the Joyal model structure where fibrant "
              "objects are quasi-categories and weak equivalences are categorical equivalences; "
              "this is Quillen equivalent to the Bergner model structure on simplicial categories.",
              PropositionKind.THEOREM, "infinity_categories", importance=0.96,
              tags=("Joyal", "quasi-category", "model-structure")),
        _prop("Lurie Straightening/Unstraightening",
              "For a quasi-category C, the straightening/unstraightening equivalence gives "
              "Fun(C, Spaces) equivalent to Fun^{cart}(C^op, sSet); this classifies left "
              "fibrations over C by presheaves of spaces.",
              PropositionKind.THEOREM, "infinity_categories", importance=0.94,
              tags=("straightening", "Lurie", "fibration-classification")),
        _prop("Higher Adjoint Functor Theorem",
              "A functor F:C->D of presentable infinity-categories preserves small colimits "
              "iff it has a right adjoint; it preserves small limits iff it has a left adjoint "
              "if C is also accessible.",
              PropositionKind.THEOREM, "infinity_categories", importance=0.92,
              tags=("higher-adjoint", "presentable", "adjoint-functor-theorem")),
        _prop("Infinity-Categorical Barr-Beck",
              "A functor U:D->C of infinity-categories is monadic iff it is conservative "
              "and D has and U preserves geometric realizations of U-split simplicial objects; "
              "this characterizes monadic functors in the infinity-categorical setting.",
              PropositionKind.THEOREM, "infinity_categories", importance=0.88,
              tags=("Barr-Beck", "monadicity", "simplicial")),
    ],
    keywords=("quasi-category", "infinity-category", "simplicial-set", "Lurie", "presentable", "adjoint"),
    judgment_site=(
        "Infinity categories are the fully coherent judgment systems; higher morphisms are "
        "judgment homotopies, and the Yoneda lemma at the infinity level says objects are "
        "determined by all ways of mapping into them with full coherence data."
    ),
)

# ---------------------------------------------------------------------------
# Field 43: Higher Gauge Theory
# ---------------------------------------------------------------------------
_HIGHER_GAUGE_THEORY = _field(
    name="Higher Gauge Theory",
    description=(
        "Higher gauge theory extends classical gauge theory to higher categorical structures, "
        "using 2-connections on principal 2-bundles and non-abelian gerbes. The Peiffer "
        "identity, String 2-group, and higher parallel transport are central constructions."
    ),
    props=[
        _prop("2-Connections and Fake Curvature",
              "A 2-connection on a principal 2-bundle consists of a Lie-algebra 1-form A "
              "and a 2-form B satisfying the fake curvature condition F_A + partial(B) = 0 "
              "where partial is the differential crossed module map.",
              PropositionKind.DEFINITION, "higher_gauge_theory", importance=0.90,
              tags=("2-connection", "fake-curvature", "crossed-module")),
        _prop("Peiffer Identity",
              "In a crossed module (G,H,partial,alpha), the Peiffer identity states "
              "alpha(partial(h))(h_prime) = h h_prime h^{-1} for h,h_prime in H; this is "
              "the algebraic encoding of the interchange law for 2-cells in a strict 2-group.",
              PropositionKind.DEFINITION, "higher_gauge_theory", importance=0.88,
              tags=("Peiffer-identity", "crossed-module", "2-group")),
        _prop("Non-Abelian Gerbes",
              "A gerbe on X with band G is a stack locally equivalent to BG; non-abelian "
              "gerbes (Breen-Messing) are classified by H^2(X,G) and generalise line bundles "
              "(G=U(1)) to higher-dimensional gauge objects.",
              PropositionKind.DEFINITION, "higher_gauge_theory", importance=0.87,
              tags=("gerbe", "stack", "non-abelian")),
        _prop("String 2-Group",
              "The String group String(n) is the 3-connected cover of Spin(n) and forms a "
              "smooth 2-group; string structures on a manifold are reductions of the frame "
              "bundle to String(n) and are needed for the Green-Schwarz anomaly cancellation.",
              PropositionKind.DEFINITION, "higher_gauge_theory", importance=0.85,
              tags=("String-group", "2-group", "spin-structure")),
    ],
    keywords=("2-connection", "gerbe", "2-group", "higher-gauge", "crossed-module", "Peiffer", "String-group"),
    judgment_site=(
        "Higher gauge theory extends judgment transport to higher dimensions; a 2-connection "
        "transports judgment paths not just judgment values, and the fake curvature condition "
        "ensures coherent higher-order judgment transport around 2-dimensional surfaces."
    ),
)

# ---------------------------------------------------------------------------
# Field 44: Noncommutative Geometry
# ---------------------------------------------------------------------------
_NONCOMMUTATIVE_GEOMETRY = _field(
    name="Noncommutative Geometry",
    description=(
        "Noncommutative geometry, developed by Connes, replaces classical geometric spaces "
        "by spectral triples (A, H, D) where A is a noncommutative algebra and D is a Dirac "
        "operator. Cyclic cohomology, the distance formula, and the noncommutative index "
        "theorem are central."
    ),
    props=[
        _prop("Connes Spectral Triple",
              "A spectral triple (A, H, D) consists of a *-algebra A on Hilbert space H "
              "and self-adjoint Dirac operator D such that [D,a] is bounded for a in A "
              "and (1+D^2)^{-1/2} is compact; this encodes the full metric geometry.",
              PropositionKind.DEFINITION, "noncommutative_geometry", importance=0.98,
              tags=("spectral-triple", "Dirac-operator", "Connes")),
        _prop("Connes Distance Formula",
              "The geodesic distance between states on a Riemannian manifold M is recovered as "
              "d(phi,psi) = sup{|phi(a)-psi(a)|: a in A, norm([D,a])<=1}; this generalises "
              "Riemannian distance to noncommutative C*-algebras.",
              PropositionKind.THEOREM, "noncommutative_geometry", importance=0.95,
              tags=("distance-formula", "metric", "geodesic")),
        _prop("Cyclic Cohomology and Chern Character",
              "The cyclic cohomology HC^*(A) receives a Chern character ch: K_*(A)->HC_*(A) "
              "from K-theory; this is the noncommutative generalisation of the classical "
              "Chern-Weil homomorphism.",
              PropositionKind.DEFINITION, "noncommutative_geometry", importance=0.92,
              tags=("cyclic-cohomology", "Chern-character", "K-theory")),
        _prop("Noncommutative Torus",
              "The noncommutative torus A_theta is the C*-algebra generated by unitaries U,V "
              "with UV = e^{2 pi i theta} VU; for irrational theta it is a simple C*-algebra "
              "that is the prototype noncommutative manifold and models foliated tori.",
              PropositionKind.EXAMPLE, "noncommutative_geometry", importance=0.88,
              tags=("noncommutative-torus", "C*-algebra", "foliation")),
    ],
    keywords=("spectral-triple", "Dirac-operator", "cyclic-cohomology", "C*-algebra", "distance-formula", "Connes"),
    judgment_site=(
        "Noncommutative geometry is the quantum judgment space geometry; the Dirac operator "
        "encodes the metric structure of judgment space, cyclic cohomology detects "
        "non-classical judgment topology, and the distance formula generalises to "
        "noncommutative judgment algebras."
    ),
)

# ---------------------------------------------------------------------------
# Field 45: Tropical Geometry
# ---------------------------------------------------------------------------
_TROPICAL_GEOMETRY = _field(
    name="Tropical Geometry",
    description=(
        "Tropical geometry studies algebraic geometry over the tropical semiring (R union {-inf}, "
        "max, +), turning algebraic varieties into piecewise-linear polyhedral complexes. "
        "The correspondence theorem, tropical Bezout, and the tropical Grassmannian are "
        "central results."
    ),
    props=[
        _prop("Tropical Bezout Theorem",
              "Two tropical plane curves of degrees d and e intersect in exactly d*e points "
              "counted with multiplicity; this tropical Bezout theorem holds without the "
              "algebraically closed hypothesis required in classical geometry.",
              PropositionKind.THEOREM, "tropical_geometry", importance=0.92,
              tags=("tropical-bezout", "intersection", "multiplicity")),
        _prop("Correspondence Theorem (Mikhalkin)",
              "Genus-g degree-d curves in CP^2 through 3d+g-1 Tevelev-position points "
              "biject with genus-g tropical curves of degree d through the corresponding "
              "tropical points, giving a combinatorial formula for Gromov-Witten invariants.",
              PropositionKind.THEOREM, "tropical_geometry", importance=0.97,
              tags=("Mikhalkin", "correspondence", "Gromov-Witten")),
        _prop("Tropical Grassmannian",
              "The tropical Grassmannian Trop G(2,n) is a simplicial fan whose maximal cones "
              "are indexed by trivalent phylogenetic trees on n leaves; it equals the space of "
              "phylogenetic trees and is the tropical limit of the classical Grassmannian.",
              PropositionKind.THEOREM, "tropical_geometry", importance=0.88,
              tags=("tropical-Grassmannian", "phylogenetic-tree", "valuated-matroid")),
        _prop("Balancing Condition",
              "A tropical variety of dimension k in R^n is a weighted polyhedral complex "
              "satisfying the balancing condition: at each codimension-1 face, the weighted "
              "sum of primitive integer vectors of adjacent facets is zero.",
              PropositionKind.DEFINITION, "tropical_geometry", importance=0.86,
              tags=("balancing", "tropical-cycle", "polyhedral-complex")),
    ],
    keywords=("tropical-semiring", "polyhedral-complex", "valuation", "Mikhalkin", "Gromov-Witten", "Newton-polygon"),
    judgment_site=(
        "Tropical geometry is the piecewise-linear shadow of algebraic judgment geometry; "
        "degeneration from complex to tropical captures the essential combinatorial skeleton, "
        "and the correspondence theorem equates complex and tropical judgment counts."
    ),
)

# ---------------------------------------------------------------------------
# Field 46: Arithmetic Geometry
# ---------------------------------------------------------------------------
_ARITHMETIC_GEOMETRY = _field(
    name="Arithmetic Geometry",
    description=(
        "Arithmetic geometry studies arithmetic properties of algebraic varieties, combining "
        "number theory with algebraic geometry. Faltings theorem, Deligne proof of the Weil "
        "conjectures, Arakelov theory, and Scholze perfectoid spaces are landmark results."
    ),
    props=[
        _prop("Faltings Theorem (Mordell Conjecture)",
              "A smooth projective curve of genus g>=2 over Q has only finitely many rational "
              "points; Faltings 1983 proof introduced the theory of heights and Arakelov "
              "geometry and established p-adic methods as central tools.",
              PropositionKind.THEOREM, "arithmetic_geometry", importance=0.99,
              tags=("Faltings", "Mordell-conjecture", "rational-points")),
        _prop("Weil Conjectures (Deligne)",
              "For smooth projective X over F_q, the zeta function Z(X,t) is rational (Dwork), "
              "satisfies a functional equation (Grothendieck), and the roots on the w-th "
              "cohomology have absolute value q^{-w/2} (Deligne Riemann hypothesis).",
              PropositionKind.THEOREM, "arithmetic_geometry", importance=0.99,
              tags=("Weil-conjectures", "Deligne", "zeta-function")),
        _prop("Arakelov Theory",
              "Arakelov geometry compactifies arithmetic surfaces X/Z by adding Hermitian "
              "metrics at archimedean places; arithmetic intersection theory gives a product "
              "formula hat-c_1(L1).hat-c_1(L2) generalising intersection theory on surfaces.",
              PropositionKind.DEFINITION, "arithmetic_geometry", importance=0.90,
              tags=("Arakelov", "arithmetic-intersection", "height")),
        _prop("Perfectoid Spaces (Scholze)",
              "A perfectoid space is a Huber space over a perfectoid field K where the "
              "Frobenius phi: O_X^+/p -> O_X^+/p is an isomorphism; the tilting equivalence "
              "X <-> X^flat reduces mixed characteristic questions to characteristic p.",
              PropositionKind.DEFINITION, "arithmetic_geometry", importance=0.96,
              tags=("perfectoid", "Scholze", "tilting", "p-adic")),
    ],
    keywords=("rational-points", "Weil-conjectures", "Arakelov", "perfectoid", "Faltings", "p-adic", "height"),
    judgment_site=(
        "Arithmetic geometry is the judgment geometry of integer points; the height of a "
        "judgment measures arithmetic complexity, and the Weil conjectures establish deep "
        "structural symmetry in how finite-field judgment counts grow."
    ),
)

# ---------------------------------------------------------------------------
# Field 47: Etale Cohomology
# ---------------------------------------------------------------------------
_ETALE_COHOMOLOGY = _field(
    name="Etale Cohomology",
    description=(
        "Etale cohomology, developed by Grothendieck and collaborators, is a cohomology theory "
        "for algebraic varieties providing l-adic Galois representations. The proper base "
        "change theorem, purity, the Weil conjectures via Lefschetz trace formula, and "
        "Grothendieck six functors are central."
    ),
    props=[
        _prop("Proper Base Change Theorem",
              "For a proper morphism f:X->S and geometric point s->S, the natural map "
              "(R^i f_* F)_s -> H^i(X_s, F) is an isomorphism for any torsion sheaf F; "
              "etale cohomology commutes with proper base change.",
              PropositionKind.THEOREM, "etale_cohomology", importance=0.96,
              tags=("proper-base-change", "geometric-fiber")),
        _prop("Purity Theorem",
              "For a closed immersion i:Z->X of codimension c of smooth schemes, "
              "i^! F = i* F(-c)[-2c] (the Tate twist); this is the algebraic analogue "
              "of Poincare-Lefschetz duality.",
              PropositionKind.THEOREM, "etale_cohomology", importance=0.93,
              tags=("purity", "Tate-twist", "codimension")),
        _prop("Weil Conjectures via l-adic Cohomology",
              "For smooth projective X over F_q, the Lefschetz trace formula gives "
              "Z(X,t) = product_i det(1-Frob*t | H^i_et(X,Q_l))^{(-1)^{i+1}}; "
              "eigenvalues of Frobenius on H^i have absolute value q^{i/2} (Deligne).",
              PropositionKind.THEOREM, "etale_cohomology", importance=0.99,
              tags=("Weil-conjectures", "Frobenius", "l-adic", "Deligne")),
        _prop("Grothendieck Six Functors",
              "Grothendieck six operations (f*, f_*, f_!, f^!, tensor^L, R Hom) on derived "
              "categories of etale sheaves satisfy adjunctions and base-change formulas; "
              "they provide the complete toolbox for etale cohomological calculations.",
              PropositionKind.THEOREM, "etale_cohomology", importance=0.95,
              tags=("six-functors", "Grothendieck", "derived-category")),
    ],
    keywords=("etale-cohomology", "l-adic", "Galois-representation", "Frobenius", "Weil-conjectures", "site"),
    judgment_site=(
        "Etale cohomology is the finest algebraic-geometric judgment topology; etale covers "
        "are the minimal resolutions for algebraic judgments, and the Galois action on etale "
        "cohomology encodes the full arithmetic judgment symmetry."
    ),
)

# ---------------------------------------------------------------------------
# Field 48: Perverse Sheaves
# ---------------------------------------------------------------------------
_PERVERSE_SHEAVES = _field(
    name="Perverse Sheaves",
    description=(
        "Perverse sheaves are a t-structure on the derived category of constructible sheaves "
        "on a stratified space, introduced by Beilinson-Bernstein-Deligne-Gabber (BBD). The "
        "decomposition theorem, intersection cohomology, and the Riemann-Hilbert correspondence "
        "are landmark results."
    ),
    props=[
        _prop("BBD Decomposition Theorem",
              "The direct image of a semisimple perverse sheaf under a proper morphism f:X->Y "
              "is semisimple and decomposes as a direct sum of shifted IC sheaves IC(Z,L); "
              "this is the deepest theorem in the subject and implies purity of intersection cohomology.",
              PropositionKind.THEOREM, "perverse_sheaves", importance=0.99,
              tags=("BBD", "decomposition-theorem", "semisimple", "IC-sheaf")),
        _prop("Intersection Cohomology (Goresky-MacPherson)",
              "For a complex algebraic variety X with singularities, IH^*(X;Q) satisfies "
              "Poincare duality and the Lefschetz hyperplane theorem; it is computed by the "
              "IC sheaf IC_X = j_{!*} Q_U[dim X] where U is the smooth locus.",
              PropositionKind.DEFINITION, "perverse_sheaves", importance=0.97,
              tags=("intersection-cohomology", "Goresky-MacPherson", "Poincare-duality")),
        _prop("Riemann-Hilbert Correspondence",
              "The Riemann-Hilbert functor RH: D^b_{rh}(D_X) -> Perv(X) from bounded derived "
              "category of regular holonomic D-modules to perverse sheaves is an equivalence "
              "of triangulated categories.",
              PropositionKind.THEOREM, "perverse_sheaves", importance=0.96,
              tags=("Riemann-Hilbert", "D-modules", "regular-holonomic")),
        _prop("Kazhdan-Lusztig Conjecture (Proved)",
              "The multiplicity of irreducible L(mu) in Verma module M(lambda) equals the "
              "Kazhdan-Lusztig polynomial P_{w_mu,w_lambda}(1); proved using the geometry of "
              "Schubert varieties and the BBD decomposition theorem.",
              PropositionKind.THEOREM, "perverse_sheaves", importance=0.95,
              tags=("Kazhdan-Lusztig", "Verma-module", "Schubert-variety")),
        _prop("Middle Perversity and Self-Duality",
              "The middle perversity t-structure on D^b_c(X) is self-dual under Verdier duality; "
              "Perv(X) is an abelian category stable under D_X, making it the canonical "
              "self-dual abelian subcategory of D^b_c(X).",
              PropositionKind.THEOREM, "perverse_sheaves", importance=0.90,
              tags=("middle-perversity", "self-dual", "Verdier-duality")),
    ],
    keywords=("perverse-sheaf", "IC-sheaf", "intersection-cohomology", "D-modules", "BBD", "Kazhdan-Lusztig"),
    judgment_site=(
        "Perverse sheaves are the judgment cohomology theory for singular judgment spaces; "
        "intersection cohomology restores Poincare duality at singular judgment loci, and the "
        "decomposition theorem shows judgment complexity decomposes into irreducible IC pieces "
        "under proper judgment maps."
    ),
)



# ---------------------------------------------------------------------------
# Field 49: Numerical Analysis
# ---------------------------------------------------------------------------
_NUMERICAL_ANALYSIS = _field(
    name="Numerical Analysis",
    description=(
        "Numerical analysis develops and analyzes algorithms for obtaining "
        "numerical solutions to mathematical problems. It concerns stability, "
        "convergence, error bounds, and computational efficiency of methods for "
        "solving differential equations, linear systems, and optimization problems."
    ),
    props=[
        _prop("Lax Equivalence Theorem",
              "For a consistent finite difference method approximating a well-posed linear initial value problem, stability is necessary and sufficient for convergence.",
              PropositionKind.THEOREM, "numerical_analysis", importance=0.96,
              tags=("stability", "convergence", "finite-difference")),
        _prop("Runge-Kutta Methods",
              "A family of implicit and explicit iterative methods for approximating solutions to ordinary differential equations, including the fourth-order method with local truncation error of order five.",
              PropositionKind.DEFINITION, "numerical_analysis", importance=0.90,
              tags=("ode", "iterative-methods")),
        _prop("Backward Differentiation Formula",
              "Implicit multistep methods for solving stiff ordinary differential equations, with the k-step BDF method having order k for k up to 6 and being A-stable for k up to 2.",
              PropositionKind.THEOREM, "numerical_analysis", importance=0.88,
              tags=("stiff-equations", "stability")),
        _prop("Condition Number Bound",
              "For a matrix A, the relative error in the solution of Ax=b is bounded by the condition number times the relative error in b, providing a measure of problem sensitivity.",
              PropositionKind.THEOREM, "numerical_analysis", importance=0.89,
              tags=("linear-systems", "error-analysis")),
    ],
    keywords=("convergence", "stability", "error-analysis", "finite-difference", "iteration", "conditioning", "approximation", "discretization"),
    judgment_site=(
        "Numerical analysis grounds judgments about computational feasibility and "
        "accuracy, determining when approximate solutions can be trusted."
    ),
)

# ---------------------------------------------------------------------------
# Field 50: Approximation Theory
# ---------------------------------------------------------------------------
_APPROXIMATION_THEORY = _field(
    name="Approximation Theory",
    description=(
        "Approximation theory studies how functions can be approximated by simpler "
        "functions, particularly polynomials and rational functions. It provides "
        "theoretical foundations for interpolation, extrapolation, and best approximation "
        "in various norms."
    ),
    props=[
        _prop("Weierstrass Approximation Theorem",
              "Every continuous function on a closed interval can be uniformly approximated arbitrarily well by polynomial functions.",
              PropositionKind.THEOREM, "approximation_theory", importance=0.97,
              tags=("polynomials", "uniform-approximation", "density")),
        _prop("Stone-Weierstrass Theorem",
              "A subalgebra of continuous functions on a compact Hausdorff space that separates points and contains constants is dense in the uniform norm.",
              PropositionKind.THEOREM, "approximation_theory", importance=0.94,
              tags=("functional-analysis", "density")),
        _prop("Jackson's Theorem",
              "For a continuous function with modulus of continuity omega, the degree of approximation by polynomials of degree n is bounded by a constant times omega(1/n).",
              PropositionKind.THEOREM, "approximation_theory", importance=0.87,
              tags=("convergence-rate", "polynomials")),
        _prop("Chebyshev Equioscillation Theorem",
              "The polynomial of degree at most n that best approximates a continuous function in the uniform norm is unique and characterized by equioscillating error at n+2 points.",
              PropositionKind.THEOREM, "approximation_theory", importance=0.91,
              tags=("best-approximation", "polynomials", "optimality")),
    ],
    keywords=("polynomial-approximation", "uniform-convergence", "best-approximation", "interpolation", "orthogonal-polynomials", "rational-approximation", "splines", "density"),
    judgment_site=(
        "Approximation theory justifies claims about when simplified models adequately "
        "represent complex functions for practical purposes."
    ),
)

# ---------------------------------------------------------------------------
# Field 51: Control Theory
# ---------------------------------------------------------------------------
_CONTROL_THEORY = _field(
    name="Control Theory",
    description=(
        "Control theory analyzes dynamical systems with inputs, focusing on how to "
        "influence system behavior through feedback. It encompasses stability analysis, "
        "optimal control, state estimation, and robust control design."
    ),
    props=[
        _prop("Kalman-Bucy Filter",
              "For linear stochastic systems with Gaussian noise, the conditional mean of the state given observations satisfies a linear differential equation that minimizes mean squared error.",
              PropositionKind.THEOREM, "control_theory", importance=0.95,
              tags=("state-estimation", "optimal-filtering", "stochastic")),
        _prop("Pontryagin Maximum Principle",
              "Necessary conditions for optimal control of dynamical systems, stating that optimal trajectories maximize the Hamiltonian along the state and costate trajectories.",
              PropositionKind.THEOREM, "control_theory", importance=0.97,
              tags=("optimal-control", "calculus-of-variations")),
        _prop("Lyapunov Stability Criterion",
              "A system is asymptotically stable if there exists a positive definite Lyapunov function whose time derivative along trajectories is negative definite.",
              PropositionKind.THEOREM, "control_theory", importance=0.93,
              tags=("stability", "nonlinear-systems")),
        _prop("Linear Quadratic Regulator",
              "For linear systems with quadratic cost, the optimal control is linear state feedback with gain determined by solving the algebraic Riccati equation.",
              PropositionKind.THEOREM, "control_theory", importance=0.90,
              tags=("optimal-control", "linear-systems")),
    ],
    keywords=("feedback", "stability", "optimal-control", "observability", "controllability", "state-space", "Riccati-equation", "robust-control"),
    judgment_site=(
        "Control theory enables judgments about system manipulability and the achievability "
        "of desired behaviors under uncertainty and constraints."
    ),
)

# ---------------------------------------------------------------------------
# Field 52: Optimization Theory
# ---------------------------------------------------------------------------
_OPTIMIZATION_THEORY = _field(
    name="Optimization Theory",
    description=(
        "Optimization theory studies methods for finding extrema of functions subject "
        "to constraints. It includes convex optimization, nonlinear programming, "
        "duality theory, and algorithmic aspects of finding optimal solutions."
    ),
    props=[
        _prop("Karush-Kuhn-Tucker Conditions",
              "For a nonlinear optimization problem with inequality constraints, if the constraint qualifications hold, then at any local optimum the gradient of the objective is a linear combination of constraint gradients with nonnegative multipliers.",
              PropositionKind.THEOREM, "optimization_theory", importance=0.96,
              tags=("constrained-optimization", "necessary-conditions", "nonlinear")),
        _prop("Strong Duality Theorem",
              "For a convex optimization problem satisfying Slater's condition, the optimal values of the primal and dual problems are equal and dual optimal solutions exist.",
              PropositionKind.THEOREM, "optimization_theory", importance=0.94,
              tags=("convex-optimization", "duality")),
        _prop("Lagrange Multiplier Theorem",
              "At a local extremum of a function subject to equality constraints, the gradient of the objective is a linear combination of the gradients of the constraints.",
              PropositionKind.THEOREM, "optimization_theory", importance=0.92,
              tags=("constrained-optimization", "necessary-conditions")),
        _prop("Subgradient Optimality Condition",
              "For a convex function, a point is a global minimum if and only if zero is a subgradient at that point.",
              PropositionKind.THEOREM, "optimization_theory", importance=0.88,
              tags=("convex-analysis", "optimality")),
    ],
    keywords=("convex-optimization", "duality", "KKT-conditions", "gradient-methods", "constraint-qualifications", "Lagrangian", "subdifferential", "global-minimum"),
    judgment_site=(
        "Optimization theory grounds claims about best achievable outcomes and provides "
        "epistemic warrant for resource allocation decisions."
    ),
)

# ---------------------------------------------------------------------------
# Field 53: Signal Processing (Mathematical)
# ---------------------------------------------------------------------------
_SIGNAL_PROCESSING_MATHEMATICAL = _field(
    name="Signal Processing (Mathematical)",
    description=(
        "Mathematical signal processing develops the theoretical foundations for "
        "analyzing, transforming, and reconstructing signals. It draws on Fourier "
        "analysis, sampling theory, filter design, and time-frequency analysis."
    ),
    props=[
        _prop("Nyquist-Shannon Sampling Theorem",
              "A bandlimited continuous signal with maximum frequency f_max can be perfectly reconstructed from samples taken at frequency 2*f_max or higher.",
              PropositionKind.THEOREM, "signal_processing_mathematical", importance=0.98,
              tags=("sampling", "reconstruction", "bandlimited")),
        _prop("Parseval's Theorem for Fourier Transform",
              "The total energy of a signal in the time domain equals the total energy in the frequency domain, establishing isometry of the Fourier transform in L2.",
              PropositionKind.THEOREM, "signal_processing_mathematical", importance=0.91,
              tags=("energy", "Fourier-transform", "isometry")),
        _prop("Wiener-Khinchin Theorem",
              "The power spectral density of a wide-sense stationary random process is the Fourier transform of its autocorrelation function.",
              PropositionKind.THEOREM, "signal_processing_mathematical", importance=0.89,
              tags=("spectral-analysis", "stochastic-processes")),
        _prop("Uncertainty Principle for Signals",
              "A signal cannot be simultaneously arbitrarily localized in both time and frequency, with the product of time and frequency spreads bounded below by a constant.",
              PropositionKind.THEOREM, "signal_processing_mathematical", importance=0.87,
              tags=("time-frequency", "uncertainty")),
    ],
    keywords=("Fourier-transform", "sampling", "filtering", "spectral-analysis", "wavelets", "time-frequency", "convolution", "bandlimited"),
    judgment_site=(
        "Signal processing theory determines epistemic limits on information recovery "
        "from measurements and the fidelity of reconstructions."
    ),
)

# ---------------------------------------------------------------------------
# Field 54: Dynamical Systems
# ---------------------------------------------------------------------------
_DYNAMICAL_SYSTEMS = _field(
    name="Dynamical Systems",
    description=(
        "Dynamical systems theory studies evolution of systems over time, including "
        "qualitative behavior, stability, bifurcations, and chaos. It applies to "
        "continuous and discrete systems, with applications across sciences and engineering."
    ),
    props=[
        _prop("Poincare-Bendixson Theorem",
              "A nonempty compact limit set of a two-dimensional continuous dynamical system that contains no fixed points must be a periodic orbit.",
              PropositionKind.THEOREM, "dynamical_systems", importance=0.94,
              tags=("periodic-orbits", "planar-systems", "limit-sets")),
        _prop("Hartman-Grobman Theorem",
              "Near a hyperbolic equilibrium point, a nonlinear dynamical system is topologically conjugate to its linearization.",
              PropositionKind.THEOREM, "dynamical_systems", importance=0.92,
              tags=("linearization", "hyperbolic-points", "local-behavior")),
        _prop("Lyapunov Exponent Characterization",
              "The Lyapunov exponents measure the average exponential rates of divergence or convergence of nearby trajectories, characterizing sensitive dependence on initial conditions.",
              PropositionKind.DEFINITION, "dynamical_systems", importance=0.89,
              tags=("chaos", "stability", "exponential-growth")),
        _prop("Stable Manifold Theorem",
              "Near a hyperbolic fixed point, there exist stable and unstable invariant manifolds tangent to the corresponding eigenspaces of the linearization.",
              PropositionKind.THEOREM, "dynamical_systems", importance=0.90,
              tags=("invariant-manifolds", "hyperbolic-dynamics")),
    ],
    keywords=("attractors", "bifurcations", "chaos", "stability", "periodic-orbits", "phase-space", "invariant-manifolds", "ergodic-theory"),
    judgment_site=(
        "Dynamical systems theory grounds judgments about long-term predictability and "
        "qualitative behavior of evolving systems."
    ),
)

# ---------------------------------------------------------------------------
# Field 55: Mathematical Fluid Dynamics
# ---------------------------------------------------------------------------
_MATHEMATICAL_FLUID_DYNAMICS = _field(
    name="Mathematical Fluid Dynamics",
    description=(
        "Mathematical fluid dynamics studies the equations governing fluid motion, "
        "including existence, uniqueness, regularity, and stability of solutions. "
        "It addresses both incompressible and compressible flows, turbulence, and "
        "wave phenomena."
    ),
    props=[
        _prop("Navier-Stokes Existence Theory",
              "For smooth initial data in two dimensions, the incompressible Navier-Stokes equations admit unique global smooth solutions; the three-dimensional case remains open.",
              PropositionKind.THEOREM, "mathematical_fluid_dynamics", importance=0.97,
              tags=("existence", "regularity", "Navier-Stokes")),
        _prop("Bernoulli's Principle",
              "For inviscid, incompressible, steady flow along a streamline, the sum of pressure, kinetic energy per unit volume, and potential energy per unit volume is constant.",
              PropositionKind.THEOREM, "mathematical_fluid_dynamics", importance=0.88,
              tags=("inviscid-flow", "conservation-laws")),
        _prop("Kelvin Circulation Theorem",
              "For an inviscid, barotropic flow with conservative body forces, the circulation around a closed curve moving with the fluid is constant in time.",
              PropositionKind.THEOREM, "mathematical_fluid_dynamics", importance=0.86,
              tags=("vorticity", "conservation", "inviscid")),
        _prop("Leray-Hopf Weak Solutions",
              "The three-dimensional incompressible Navier-Stokes equations admit global weak solutions satisfying an energy inequality, though uniqueness is not known.",
              PropositionKind.THEOREM, "mathematical_fluid_dynamics", importance=0.93,
              tags=("weak-solutions", "energy-inequality")),
    ],
    keywords=("Navier-Stokes", "Euler-equations", "vorticity", "turbulence", "boundary-layers", "incompressible-flow", "compressible-flow", "viscosity"),
    judgment_site=(
        "Mathematical fluid dynamics establishes epistemic foundations for claims about "
        "flow behavior and the reliability of computational simulations."
    ),
)

# ---------------------------------------------------------------------------
# Field 56: Elasticity Theory
# ---------------------------------------------------------------------------
_ELASTICITY_THEORY = _field(
    name="Elasticity Theory",
    description=(
        "Elasticity theory describes the mechanical behavior of deformable solid bodies "
        "under forces and constraints. It encompasses stress-strain relationships, "
        "equilibrium equations, and boundary value problems for elastic materials."
    ),
    props=[
        _prop("Cauchy Stress Theorem",
              "At each point in a continuous medium, there exists a stress tensor such that the traction vector on any surface through the point depends linearly on the surface normal.",
              PropositionKind.THEOREM, "elasticity_theory", importance=0.91,
              tags=("stress-tensor", "continuum-mechanics")),
        _prop("Saint-Venant's Principle",
              "If a system of forces acting on a small portion of an elastic body is replaced by a statically equivalent system, the stress distribution is substantially unchanged at distances large compared to the linear dimensions of the loaded region.",
              PropositionKind.THEOREM, "elasticity_theory", importance=0.87,
              tags=("boundary-conditions", "stress-distribution")),
        _prop("Kirchhoff-Love Plate Theory",
              "For thin elastic plates, the plate normal remains perpendicular to the mid-surface after deformation, reducing the three-dimensional elasticity problem to a two-dimensional one.",
              PropositionKind.THEOREM, "elasticity_theory", importance=0.85,
              tags=("plate-theory", "thin-structures")),
        _prop("Lame's Solution for Thick Cylinders",
              "The stress distribution in a thick-walled cylinder under internal and external pressure can be expressed in closed form using Lame's equations for axisymmetric elasticity.",
              PropositionKind.THEOREM, "elasticity_theory", importance=0.83,
              tags=("stress-analysis", "cylindrical-coordinates")),
    ],
    keywords=("stress-tensor", "strain", "Hooke's-law", "boundary-value-problems", "constitutive-relations", "linear-elasticity", "deformation", "equilibrium"),
    judgment_site=(
        "Elasticity theory provides epistemic grounds for structural engineering judgments "
        "about material behavior and failure predictions."
    ),
)

# ---------------------------------------------------------------------------
# Field 57: Mathematical Biology
# ---------------------------------------------------------------------------
_MATHEMATICAL_BIOLOGY = _field(
    name="Mathematical Biology",
    description=(
        "Mathematical biology applies mathematical techniques to understand biological "
        "systems and processes. It includes population dynamics, pattern formation, "
        "biochemical networks, and evolutionary dynamics."
    ),
    props=[
        _prop("Lotka-Volterra Predator-Prey Model",
              "The classical predator-prey system exhibits periodic oscillations in population sizes, with the predator population lagging behind the prey population.",
              PropositionKind.THEOREM, "mathematical_biology", importance=0.90,
              tags=("population-dynamics", "oscillations", "ecology")),
        _prop("Fisher-KPP Equation",
              "The reaction-diffusion equation with logistic growth admits traveling wave solutions with minimum speed determined by the growth and diffusion parameters, modeling gene spread.",
              PropositionKind.THEOREM, "mathematical_biology", importance=0.88,
              tags=("reaction-diffusion", "traveling-waves", "genetics")),
        _prop("Turing Instability for Pattern Formation",
              "A spatially homogeneous steady state stable to homogeneous perturbations can be unstable to spatially heterogeneous perturbations when diffusion coefficients differ, generating patterns.",
              PropositionKind.THEOREM, "mathematical_biology", importance=0.92,
              tags=("pattern-formation", "morphogenesis", "instability")),
        _prop("Quasi-Steady-State Approximation",
              "In enzyme kinetics, when enzyme-substrate complex formation is fast compared to product formation, the complex concentration can be approximated by its quasi-equilibrium value, yielding Michaelis-Menten kinetics.",
              PropositionKind.THEOREM, "mathematical_biology", importance=0.84,
              tags=("enzyme-kinetics", "biochemistry", "time-scales")),
    ],
    keywords=("population-dynamics", "reaction-diffusion", "pattern-formation", "biochemical-networks", "evolution", "epidemiology", "neuroscience", "ecology"),
    judgment_site=(
        "Mathematical biology enables quantitative judgments about biological mechanisms "
        "and the predictive validity of mechanistic models."
    ),
)

# ---------------------------------------------------------------------------
# Field 58: Epidemiological Modeling
# ---------------------------------------------------------------------------
_EPIDEMIOLOGICAL_MODELING = _field(
    name="Epidemiological Modeling",
    description=(
        "Epidemiological modeling uses mathematical frameworks to understand disease "
        "transmission dynamics and evaluate intervention strategies. Key approaches "
        "include compartmental models, network models, and spatial spread."
    ),
    props=[
        _prop("Kermack-McKendrick Threshold Theorem",
              "In the basic SIR epidemic model, an epidemic occurs if and only if the basic reproduction number R0 exceeds one, establishing a threshold for disease invasion.",
              PropositionKind.THEOREM, "epidemiological_modeling", importance=0.95,
              tags=("epidemic-threshold", "SIR-model", "reproduction-number")),
        _prop("Basic Reproduction Number R0",
              "The basic reproduction number is the expected number of secondary infections produced by a typical infected individual in a completely susceptible population, determining whether an epidemic can occur.",
              PropositionKind.DEFINITION, "epidemiological_modeling", importance=0.93,
              tags=("reproduction-number", "epidemic-threshold")),
        _prop("Ross-Macdonald Model",
              "The vector-borne disease transmission model incorporating human and mosquito populations shows that R0 depends on the square of vector density, explaining the super-linear relationship between vector control and disease reduction.",
              PropositionKind.THEOREM, "epidemiological_modeling", importance=0.89,
              tags=("vector-borne", "malaria", "transmission")),
        _prop("Final Size Relation",
              "In the SIR model, the final proportion of the population that remains susceptible satisfies a transcendental equation relating it to R0 and the initial conditions.",
              PropositionKind.THEOREM, "epidemiological_modeling", importance=0.87,
              tags=("SIR-model", "epidemic-outcome")),
    ],
    keywords=("SIR-model", "reproduction-number", "transmission-dynamics", "compartmental-models", "intervention-strategies", "epidemic-threshold", "vaccination", "herd-immunity"),
    judgment_site=(
        "Epidemiological modeling grounds public health judgments about disease dynamics "
        "and the expected effectiveness of interventions."
    ),
)

# ---------------------------------------------------------------------------
# Field 59: Operations Research
# ---------------------------------------------------------------------------
_OPERATIONS_RESEARCH = _field(
    name="Operations Research",
    description=(
        "Operations research applies analytical methods to optimize decision-making "
        "in complex systems. It encompasses linear programming, network flows, "
        "scheduling, queueing theory, and inventory management."
    ),
    props=[
        _prop("Simplex Method Optimality",
              "For a linear program in standard form, if the simplex method terminates at a basic feasible solution with no improving directions, then that solution is optimal.",
              PropositionKind.THEOREM, "operations_research", importance=0.93,
              tags=("linear-programming", "simplex", "optimality")),
        _prop("Max-Flow Min-Cut Theorem",
              "In a flow network, the maximum value of a feasible flow equals the minimum capacity of a cut separating the source from the sink.",
              PropositionKind.THEOREM, "operations_research", importance=0.95,
              tags=("network-flows", "duality", "graph-theory")),
        _prop("Little's Law",
              "For a stable queueing system, the long-run average number of customers in the system equals the long-run average arrival rate multiplied by the long-run average time a customer spends in the system.",
              PropositionKind.THEOREM, "operations_research", importance=0.90,
              tags=("queueing-theory", "conservation-law")),
        _prop("Branch and Bound Method",
              "Integer programming problems can be solved by recursively partitioning the feasible region and computing bounds to prune subproblems that cannot contain optimal solutions.",
              PropositionKind.THEOREM, "operations_research", importance=0.86,
              tags=("integer-programming", "algorithms")),
    ],
    keywords=("linear-programming", "integer-programming", "network-optimization", "queueing-theory", "scheduling", "inventory-theory", "decision-analysis", "combinatorial-optimization"),
    judgment_site=(
        "Operations research provides epistemic warrant for resource allocation decisions "
        "and claims about system efficiency."
    ),
)

# ---------------------------------------------------------------------------
# Field 60: Game Theory
# ---------------------------------------------------------------------------
_GAME_THEORY = _field(
    name="Game Theory",
    description=(
        "Game theory studies strategic interactions between rational agents. It includes "
        "non-cooperative games, cooperative games, mechanism design, and evolutionary "
        "game theory, with applications across economics, politics, and biology."
    ),
    props=[
        _prop("Nash Equilibrium Existence Theorem",
              "Every finite game has at least one Nash equilibrium, possibly in mixed strategies, where no player can unilaterally improve their payoff.",
              PropositionKind.THEOREM, "game_theory", importance=0.98,
              tags=("Nash-equilibrium", "existence", "strategic-form")),
        _prop("Minimax Theorem",
              "In a two-player zero-sum game, the maximum of the minimum payoffs equals the minimum of the maximum payoffs, and this value can be achieved by mixed strategies.",
              PropositionKind.THEOREM, "game_theory", importance=0.95,
              tags=("zero-sum", "minimax", "mixed-strategies")),
        _prop("Subgame Perfect Equilibrium",
              "A strategy profile is subgame perfect if it induces a Nash equilibrium in every subgame of the extensive form game, ruling out non-credible threats.",
              PropositionKind.DEFINITION, "game_theory", importance=0.91,
              tags=("extensive-form", "refinements", "credibility")),
        _prop("Folk Theorem for Repeated Games",
              "In infinitely repeated games with sufficiently patient players, any individually rational payoff profile can be sustained as a Nash equilibrium outcome using trigger strategies.",
              PropositionKind.THEOREM, "game_theory", importance=0.89,
              tags=("repeated-games", "cooperation", "folk-theorem")),
    ],
    keywords=("Nash-equilibrium", "strategic-form", "extensive-form", "mixed-strategies", "zero-sum-games", "repeated-games", "evolutionary-stability", "rationality"),
    judgment_site=(
        "Game theory grounds judgments about rational strategic behavior and predictions "
        "of outcomes in interactive decision-making."
    ),
)

# ---------------------------------------------------------------------------
# Field 61: Mechanism Design
# ---------------------------------------------------------------------------
_MECHANISM_DESIGN = _field(
    name="Mechanism Design",
    description=(
        "Mechanism design, or reverse game theory, designs rules and institutions to "
        "achieve desired outcomes when participants act strategically with private "
        "information. Applications include auctions, voting systems, and market design."
    ),
    props=[
        _prop("Revelation Principle",
              "For any mechanism and any Bayesian Nash equilibrium of that mechanism, there exists a direct revelation mechanism where truthful reporting is an equilibrium and yields the same outcome.",
              PropositionKind.THEOREM, "mechanism_design", importance=0.96,
              tags=("incentive-compatibility", "revelation", "equilibrium")),
        _prop("Gibbard-Satterthwaite Theorem",
              "For deterministic voting mechanisms with at least three alternatives, if the mechanism is strategy-proof and onto, then it must be dictatorial.",
              PropositionKind.THEOREM, "mechanism_design", importance=0.94,
              tags=("voting", "impossibility", "strategy-proofness")),
        _prop("Myerson's Optimal Auction Theorem",
              "For a seller with independent private value bidders, the revenue-maximizing auction allocates to the bidder with highest virtual valuation and extracts payment via a carefully constructed reserve price.",
              PropositionKind.THEOREM, "mechanism_design", importance=0.92,
              tags=("auction-theory", "revenue-maximization")),
        _prop("Vickrey-Clarke-Groves Mechanism",
              "The VCG mechanism achieves efficient outcomes in dominant strategies by charging each agent their externality on others, incentivizing truthful reporting of values.",
              PropositionKind.THEOREM, "mechanism_design", importance=0.90,
              tags=("efficiency", "dominant-strategies", "externalities")),
    ],
    keywords=("incentive-compatibility", "strategy-proofness", "auctions", "voting", "mechanism-design", "VCG", "revenue-maximization", "private-information"),
    judgment_site=(
        "Mechanism design theory justifies institutional choices and grounds claims about "
        "achieving social goals despite strategic behavior."
    ),
)

# ---------------------------------------------------------------------------
# Field 62: Computational Geometry
# ---------------------------------------------------------------------------
_COMPUTATIONAL_GEOMETRY = _field(
    name="Computational Geometry",
    description=(
        "Computational geometry develops efficient algorithms for geometric problems "
        "involving points, lines, polygons, and polyhedra. Key problems include convex "
        "hulls, triangulations, range searching, and proximity structures."
    ),
    props=[
        _prop("Graham Scan Algorithm",
              "The convex hull of n points in the plane can be computed in O(n log n) time by sorting points angularly and maintaining a stack of hull vertices.",
              PropositionKind.THEOREM, "computational_geometry", importance=0.88,
              tags=("convex-hull", "algorithms", "complexity")),
        _prop("Voronoi Diagram Properties",
              "The Voronoi diagram of n points in the plane partitions space into regions where each region consists of points closest to a given site, with complexity O(n).",
              PropositionKind.THEOREM, "computational_geometry", importance=0.91,
              tags=("Voronoi-diagrams", "proximity", "duality")),
        _prop("Delaunay Triangulation Optimality",
              "The Delaunay triangulation of a point set maximizes the minimum angle among all triangulations and is dual to the Voronoi diagram.",
              PropositionKind.THEOREM, "computational_geometry", importance=0.89,
              tags=("triangulation", "Delaunay", "optimality")),
        _prop("Zone Theorem",
              "The zone of a line in an arrangement of n lines in the plane, defined as the set of cells intersected by the line, has complexity O(n).",
              PropositionKind.THEOREM, "computational_geometry", importance=0.84,
              tags=("arrangements", "complexity", "zones")),
    ],
    keywords=("convex-hull", "Voronoi-diagrams", "Delaunay-triangulation", "range-searching", "point-location", "arrangements", "visibility", "mesh-generation"),
    judgment_site=(
        "Computational geometry establishes algorithmic feasibility for geometric problems "
        "and complexity-theoretic limits on computation."
    ),
)

# ---------------------------------------------------------------------------
# Field 63: Finite Element Methods
# ---------------------------------------------------------------------------
_FINITE_ELEMENT_METHODS = _field(
    name="Finite Element Methods",
    description=(
        "Finite element methods provide systematic numerical techniques for solving "
        "partial differential equations by discretizing domains into elements and "
        "approximating solutions with piecewise polynomials. Theory addresses convergence, "
        "error estimates, and stability."
    ),
    props=[
        _prop("Lax-Milgram Theorem",
              "A continuous, coercive bilinear form on a Hilbert space admits a unique solution to the abstract variational problem, providing existence for weak formulations of elliptic PDEs.",
              PropositionKind.THEOREM, "finite_element_methods", importance=0.95,
              tags=("variational-formulation", "existence", "elliptic")),
        _prop("Cea's Lemma",
              "The finite element solution to an elliptic problem is quasi-optimal in the energy norm, with error bounded by a constant times the best approximation error in the finite element space.",
              PropositionKind.LEMMA, "finite_element_methods", importance=0.92,
              tags=("error-estimates", "quasi-optimality")),
        _prop("Galerkin Orthogonality",
              "The error between the exact solution and finite element solution is orthogonal to the finite element space in the bilinear form, providing the foundation for error analysis.",
              PropositionKind.THEOREM, "finite_element_methods", importance=0.89,
              tags=("orthogonality", "error-analysis")),
        _prop("Aubin-Nitsche Duality Trick",
              "For elliptic problems with sufficient regularity, the L2 error can be bounded by the energy norm error times an approximation error for the dual problem, often improving convergence rates.",
              PropositionKind.THEOREM, "finite_element_methods", importance=0.87,
              tags=("duality", "L2-estimates", "regularity")),
    ],
    keywords=("weak-formulation", "Galerkin-method", "error-estimates", "convergence", "mesh-refinement", "variational-problems", "elliptic-PDE", "polynomial-approximation"),
    judgment_site=(
        "Finite element theory provides epistemic justification for computational solutions "
        "to PDEs and quantifies approximation reliability."
    ),
)

# ---------------------------------------------------------------------------
# Field 64: Analytic Number Theory
# ---------------------------------------------------------------------------
_ANALYTIC_NUMBER_THEORY = _field(
    name="Analytic Number Theory",
    description=(
        "Analytic number theory applies methods from mathematical analysis to study "
        "properties of integers, particularly the distribution of prime numbers. "
        "Central tools include the Riemann zeta function, L-functions, and sieve methods."
    ),
    props=[
        _prop("Prime Number Theorem",
              "The number of primes less than x is asymptotic to x/log(x) as x approaches infinity, establishing the density of primes in the integers.",
              PropositionKind.THEOREM, "analytic_number_theory", importance=0.99,
              tags=("prime-distribution", "asymptotics", "density")),
        _prop("Dirichlet's Theorem on Primes in Arithmetic Progressions",
              "For coprime integers a and m, there are infinitely many primes congruent to a modulo m, distributed equally among all residue classes coprime to m.",
              PropositionKind.THEOREM, "analytic_number_theory", importance=0.96,
              tags=("primes", "arithmetic-progressions", "L-functions")),
        _prop("Riemann Hypothesis",
              "The Riemann zeta function has all its nontrivial zeros on the critical line with real part equal to 1/2, implying strong error bounds for prime counting.",
              PropositionKind.THEOREM, "analytic_number_theory", importance=0.98,
              tags=("zeta-function", "open-problem", "zeros")),
        _prop("Functional Equation of Zeta Function",
              "The Riemann zeta function satisfies a functional equation relating zeta(s) to zeta(1-s), exhibiting symmetry about the critical line.",
              PropositionKind.THEOREM, "analytic_number_theory", importance=0.93,
              tags=("zeta-function", "symmetry", "functional-equation")),
    ],
    keywords=("zeta-function", "L-functions", "prime-distribution", "Dirichlet-series", "sieve-methods", "exponential-sums", "modular-forms", "analytic-continuation"),
    judgment_site=(
        "Analytic number theory establishes asymptotic laws for arithmetic functions, "
        "grounding probabilistic judgments about number-theoretic properties."
    ),
)

# ---------------------------------------------------------------------------
# Field 65: Algebraic Number Theory
# ---------------------------------------------------------------------------
_ALGEBRAIC_NUMBER_THEORY = _field(
    name="Algebraic Number Theory",
    description=(
        "Algebraic number theory studies number fields and their rings of integers, "
        "including ideals, units, class groups, and local-global principles. It provides "
        "algebraic tools for understanding Diophantine equations."
    ),
    props=[
        _prop("Class Number Formula",
              "The Dedekind zeta function of a number field at s=1 has a simple pole with residue equal to a product involving the class number, regulator, and discriminant.",
              PropositionKind.THEOREM, "algebraic_number_theory", importance=0.94,
              tags=("class-number", "zeta-functions", "analytic")),
        _prop("Dedekind's Theorem on Prime Splitting",
              "The factorization of a prime p in a number field extension is determined by the factorization of the minimal polynomial modulo p when the extension is unramified.",
              PropositionKind.THEOREM, "algebraic_number_theory", importance=0.91,
              tags=("prime-ideals", "ramification", "splitting")),
        _prop("Minkowski Bound",
              "Every ideal class in the class group of a number field contains an ideal with norm bounded by a constant depending on the discriminant and degree.",
              PropositionKind.THEOREM, "algebraic_number_theory", importance=0.89,
              tags=("class-group", "geometry-of-numbers")),
        _prop("Dirichlet Unit Theorem",
              "The unit group of the ring of integers in a number field is finitely generated, with rank equal to r1 + r2 - 1 where r1 and r2 are the numbers of real and complex embeddings.",
              PropositionKind.THEOREM, "algebraic_number_theory", importance=0.92,
              tags=("units", "structure-theorem")),
    ],
    keywords=("number-fields", "ideal-theory", "class-groups", "units", "ramification", "local-fields", "adeles", "Galois-cohomology"),
    judgment_site=(
        "Algebraic number theory grounds structural claims about arithmetic in field "
        "extensions and solvability of Diophantine problems."
    ),
)

# ---------------------------------------------------------------------------
# Field 66: Diophantine Geometry
# ---------------------------------------------------------------------------
_DIOPHANTINE_GEOMETRY = _field(
    name="Diophantine Geometry",
    description=(
        "Diophantine geometry studies integer and rational solutions to polynomial "
        "equations using geometric methods. It unifies classical Diophantine problems "
        "with algebraic geometry, including elliptic curves and higher-dimensional varieties."
    ),
    props=[
        _prop("Mordell-Weil Theorem",
              "The group of rational points on an elliptic curve over a number field is finitely generated, decomposing into a finite torsion group and a free abelian group.",
              PropositionKind.THEOREM, "diophantine_geometry", importance=0.97,
              tags=("elliptic-curves", "rational-points", "finitely-generated")),
        _prop("Siegel's Theorem",
              "A curve of genus at least one defined over a number field has only finitely many integral points over the ring of S-integers for any finite set S of places.",
              PropositionKind.THEOREM, "diophantine_geometry", importance=0.90,
              tags=("integral-points", "genus", "finiteness")),
        _prop("Faltings' Theorem",
              "A curve of genus at least two defined over a number field has only finitely many rational points, proving the Mordell conjecture.",
              PropositionKind.THEOREM, "diophantine_geometry", importance=0.96,
              tags=("Mordell-conjecture", "rational-points", "genus")),
        _prop("Nagell-Lutz Theorem",
              "For an elliptic curve defined over the rationals with integer coefficients, any torsion point has integer coordinates and the y-coordinate divides the discriminant.",
              PropositionKind.THEOREM, "diophantine_geometry", importance=0.86,
              tags=("elliptic-curves", "torsion", "integral-points")),
    ],
    keywords=("elliptic-curves", "rational-points", "Diophantine-equations", "heights", "abelian-varieties", "genus", "Mordell-conjecture", "arithmetic-geometry"),
    judgment_site=(
        "Diophantine geometry establishes finiteness and decidability results for "
        "integer solutions, grounding epistemic claims about solvability."
    ),
)

# ---------------------------------------------------------------------------
# Field 67: Additive Combinatorics
# ---------------------------------------------------------------------------
_ADDITIVE_COMBINATORICS = _field(
    name="Additive Combinatorics",
    description=(
        "Additive combinatorics studies the additive structure of sets, particularly "
        "sumsets and arithmetic progressions. It combines combinatorial, analytic, "
        "and algebraic techniques to understand patterns in integers and finite groups."
    ),
    props=[
        _prop("Szemeredi's Theorem",
              "Every subset of the integers with positive upper density contains arbitrarily long arithmetic progressions, establishing pattern regularity in dense sets.",
              PropositionKind.THEOREM, "additive_combinatorics", importance=0.97,
              tags=("arithmetic-progressions", "density", "patterns")),
        _prop("Green-Tao Theorem",
              "The prime numbers contain arbitrarily long arithmetic progressions, extending Szemeredi's theorem to the primes despite their zero density.",
              PropositionKind.THEOREM, "additive_combinatorics", importance=0.96,
              tags=("primes", "arithmetic-progressions", "relative-density")),
        _prop("Freiman's Theorem",
              "If a finite set A of integers has small doubling, meaning the size of A+A is at most K times the size of A, then A is contained in a generalized arithmetic progression of bounded dimension.",
              PropositionKind.THEOREM, "additive_combinatorics", importance=0.91,
              tags=("sumsets", "structure-theorem", "doubling")),
        _prop("Roth's Theorem",
              "Any subset of the integers with positive upper density contains a three-term arithmetic progression, providing the first nontrivial case of Szemeredi's theorem.",
              PropositionKind.THEOREM, "additive_combinatorics", importance=0.89,
              tags=("arithmetic-progressions", "density", "three-term")),
    ],
    keywords=("sumsets", "arithmetic-progressions", "density", "Fourier-analysis", "Freiman-theorem", "Szemeredi-theorem", "patterns", "inverse-theorems"),
    judgment_site=(
        "Additive combinatorics reveals inevitable patterns in arithmetic structures, "
        "grounding judgments about regularity in number-theoretic settings."
    ),
)

# ---------------------------------------------------------------------------
# Field 68: Galois Theory
# ---------------------------------------------------------------------------
_GALOIS_THEORY = _field(
    name="Galois Theory",
    description=(
        "Galois theory establishes the connection between field extensions and group "
        "theory, providing a criterion for solvability of polynomial equations by "
        "radicals. It is fundamental to understanding symmetries in algebraic equations."
    ),
    props=[
        _prop("Fundamental Theorem of Galois Theory",
              "For a Galois extension, there is a bijective correspondence between intermediate fields and subgroups of the Galois group, reversing inclusions and preserving normality.",
              PropositionKind.THEOREM, "galois_theory", importance=0.98,
              tags=("correspondence", "field-extensions", "Galois-group")),
        _prop("Abel-Ruffini Theorem",
              "There is no general algebraic solution in radicals to polynomial equations of degree five or higher, as the symmetric group S_n is not solvable for n at least 5.",
              PropositionKind.THEOREM, "galois_theory", importance=0.96,
              tags=("solvability", "radicals", "impossibility")),
        _prop("Galois Correspondence for Normality",
              "An intermediate field in a Galois extension corresponds to a normal subgroup of the Galois group if and only if the intermediate extension is Galois.",
              PropositionKind.THEOREM, "galois_theory", importance=0.91,
              tags=("normal-subgroups", "Galois-extensions")),
        _prop("Primitive Element Theorem",
              "A finite separable extension of fields is simple, meaning it is generated by a single element, enabling the construction of Galois closures.",
              PropositionKind.THEOREM, "galois_theory", importance=0.88,
              tags=("simple-extensions", "separability")),
    ],
    keywords=("Galois-group", "field-extensions", "solvability", "radicals", "separability", "splitting-fields", "automorphisms", "polynomial-equations"),
    judgment_site=(
        "Galois theory determines the epistemic limits of algebraic solvability and "
        "grounds structural claims about polynomial equations."
    ),
)
# ---------------------------------------------------------------------------
# Field 69: Commutative Algebra
# ---------------------------------------------------------------------------
_COMMUTATIVE_ALGEBRA = _field(
    name="Commutative Algebra",
    description=(
        "Studies commutative rings and their ideals, modules, and localizations. "
        "Central to algebraic geometry and number theory. "
        "Focuses on prime ideals, Noetherian rings, and dimension theory. "
        "Provides foundational tools for studying polynomial rings and varieties."
    ),
    props=[
        _prop("Hilbert's Basis Theorem",
              "Every ideal in a polynomial ring over a Noetherian ring is finitely generated.",
              PropositionKind.THEOREM, "commutative_algebra", importance=0.97,
              tags=("polynomial-rings", "noetherian")),
        _prop("Nakayama's Lemma",
              "If M is a finitely generated module over a local ring R with maximal ideal m, and mM = M, then M = 0.",
              PropositionKind.LEMMA, "commutative_algebra", importance=0.94,
              tags=("local-rings", "modules")),
        _prop("Krull's Principal Ideal Theorem",
              "In a Noetherian ring, every principal ideal generated by a non-unit has height at most one.",
              PropositionKind.THEOREM, "commutative_algebra", importance=0.92,
              tags=("dimension-theory", "prime-ideals")),
        _prop("Primary Decomposition",
              "In a Noetherian ring, every ideal can be expressed as a finite intersection of primary ideals.",
              PropositionKind.THEOREM, "commutative_algebra", importance=0.90,
              tags=("ideals", "noetherian")),
    ],
    keywords=("noetherian-rings", "ideals", "modules", "localization", "prime-ideals", "krull-dimension", "integral-extensions", "completion"),
    judgment_site=(
        "Assessing abstract algebraic structures requires judging which properties are preserved under various constructions. "
        "Epistemic care involves tracking which finiteness conditions ensure desired conclusions."
    ),
)

# ---------------------------------------------------------------------------
# Field 70: Ring Theory
# ---------------------------------------------------------------------------
_RING_THEORY = _field(
    name="Ring Theory",
    description=(
        "Studies rings, their structure, and their representations. "
        "Includes both commutative and noncommutative rings. "
        "Key topics include division rings, matrix rings, and simple rings."
    ),
    props=[
        _prop("Wedderburn's Theorem",
              "Every finite division ring is a field.",
              PropositionKind.THEOREM, "ring_theory", importance=0.95,
              tags=("division-rings", "finite-rings")),
        _prop("Artin-Wedderburn Theorem",
              "Every semisimple ring is isomorphic to a finite product of matrix rings over division rings.",
              PropositionKind.THEOREM, "ring_theory", importance=0.96,
              tags=("semisimple-rings", "structure-theory")),
        _prop("Jacobson's Theorem",
              "A ring in which every element satisfies x^n = x for some n > 1 depending on x is commutative.",
              PropositionKind.THEOREM, "ring_theory", importance=0.88,
              tags=("commutativity", "jacobson-radical")),
        _prop("Hopkins-Levitzki Theorem",
              "Every left Artinian ring is left Noetherian.",
              PropositionKind.THEOREM, "ring_theory", importance=0.87,
              tags=("artinian-rings", "chain-conditions")),
    ],
    keywords=("noncommutative-rings", "ideals", "modules", "representations", "division-rings", "matrix-rings", "radical-theory", "simple-rings"),
    judgment_site=(
        "Evaluating ring-theoretic structures demands careful attention to left versus right properties. "
        "Epistemic precision requires distinguishing when commutativity can be dropped or assumed."
    ),
)

# ---------------------------------------------------------------------------
# Field 71: Field Theory (Algebra)
# ---------------------------------------------------------------------------
_FIELD_THEORY_ALGEBRA = _field(
    name="Field Theory (Algebra)",
    description=(
        "Studies field extensions, Galois theory, and algebraic closure. "
        "Central to solving polynomial equations and understanding symmetries. "
        "Connects algebra with number theory and algebraic geometry."
    ),
    props=[
        _prop("Fundamental Theorem of Galois Theory",
              "For a finite Galois extension, there is a bijection between intermediate fields and subgroups of the Galois group, reversing inclusions.",
              PropositionKind.THEOREM, "field_theory_algebra", importance=0.98,
              tags=("galois-theory", "field-extensions")),
        _prop("Abel-Ruffini Theorem",
              "There is no general algebraic solution in radicals to polynomial equations of degree five or higher.",
              PropositionKind.THEOREM, "field_theory_algebra", importance=0.93,
              tags=("solvability", "polynomials")),
        _prop("Steinitz's Theorem",
              "Any two algebraic closures of a field are isomorphic as field extensions.",
              PropositionKind.THEOREM, "field_theory_algebra", importance=0.90,
              tags=("algebraic-closure", "isomorphism")),
        _prop("Primitive Element Theorem",
              "Every finite separable extension is simple, generated by a single element.",
              PropositionKind.THEOREM, "field_theory_algebra", importance=0.89,
              tags=("separable-extensions", "generators")),
    ],
    keywords=("galois-theory", "field-extensions", "algebraic-closure", "separable-extensions", "inseparable-extensions", "transcendence", "splitting-fields", "automorphisms"),
    judgment_site=(
        "Reasoning about field extensions requires tracking which elements generate which fields. "
        "Epistemic rigor involves carefully distinguishing separable from inseparable phenomena."
    ),
)

# ---------------------------------------------------------------------------
# Field 72: Quadratic Forms
# ---------------------------------------------------------------------------
_QUADRATIC_FORMS = _field(
    name="Quadratic Forms",
    description=(
        "Studies homogeneous polynomials of degree two and their representations. "
        "Critical for number theory, geometry, and optimization. "
        "Analyzes which integers are represented by quadratic forms over various fields."
    ),
    props=[
        _prop("Hasse-Minkowski Theorem",
              "A quadratic form over the rationals represents zero nontrivially if and only if it represents zero over all completions of the rationals.",
              PropositionKind.THEOREM, "quadratic_forms", importance=0.96,
              tags=("local-global", "rational-forms")),
        _prop("Witt's Cancellation Theorem",
              "If two quadratic forms become isometric after adding the same nondegenerate form, they were already isometric.",
              PropositionKind.THEOREM, "quadratic_forms", importance=0.91,
              tags=("witt-group", "isometry")),
        _prop("Sylvester's Law of Inertia",
              "The number of positive and negative eigenvalues of a symmetric matrix is invariant under congruence.",
              PropositionKind.THEOREM, "quadratic_forms", importance=0.93,
              tags=("real-forms", "signature")),
        _prop("Lagrange's Four Square Theorem",
              "Every nonnegative integer can be represented as the sum of four integer squares.",
              PropositionKind.THEOREM, "quadratic_forms", importance=0.90,
              tags=("representations", "integers")),
    ],
    keywords=("quadratic-forms", "witt-group", "isometry", "local-global-principle", "signature", "representations", "diophantine-equations", "lattices"),
    judgment_site=(
        "Judging whether a quadratic form represents zero requires integrating local and global information. "
        "Epistemic care involves respecting the local-global principle and understanding completions."
    ),
)

# ---------------------------------------------------------------------------
# Field 73: Modular Forms
# ---------------------------------------------------------------------------
_MODULAR_FORMS = _field(
    name="Modular Forms",
    description=(
        "Studies holomorphic functions on the upper half-plane with specific transformation properties. "
        "Central to number theory, algebraic geometry, and physics. "
        "Connects to elliptic curves, L-functions, and representation theory. "
        "Key tool in proving Fermat's Last Theorem."
    ),
    props=[
        _prop("Ramanujan's Tau Conjecture",
              "The tau function satisfies the bound |tau(p)| <= 2p^(11/2) for all primes p, proven by Deligne.",
              PropositionKind.THEOREM, "modular_forms", importance=0.95,
              tags=("ramanujan", "bounds")),
        _prop("Hecke Operators Theory",
              "Hecke operators on spaces of modular forms are simultaneously diagonalizable on cusp forms.",
              PropositionKind.THEOREM, "modular_forms", importance=0.92,
              tags=("hecke-operators", "eigenforms")),
        _prop("Modularity Theorem",
              "Every elliptic curve over the rationals is modular, arising from a modular form.",
              PropositionKind.THEOREM, "modular_forms", importance=0.99,
              tags=("elliptic-curves", "shimura-taniyama")),
        _prop("Dimension Formula for Modular Forms",
              "The dimension of the space of modular forms of weight k for the full modular group can be computed explicitly.",
              PropositionKind.THEOREM, "modular_forms", importance=0.88,
              tags=("dimension-theory", "vector-spaces")),
    ],
    keywords=("modular-forms", "elliptic-curves", "l-functions", "hecke-operators", "cusp-forms", "automorphic-forms", "congruence-subgroups", "petersson-inner-product"),
    judgment_site=(
        "Evaluating modular form conjectures requires synthesizing algebraic and analytic perspectives. "
        "Epistemic judgment involves connecting discrete arithmetic objects with continuous analytic functions."
    ),
)

# ---------------------------------------------------------------------------
# Field 74: Harmonic Analysis
# ---------------------------------------------------------------------------
_HARMONIC_ANALYSIS = _field(
    name="Harmonic Analysis",
    description=(
        "Studies the representation of functions as superpositions of basic waves. "
        "Generalizes Fourier analysis to various groups and spaces. "
        "Central to signal processing, PDE theory, and quantum mechanics."
    ),
    props=[
        _prop("Plancherel Theorem",
              "The Fourier transform extends to an isometry from L^2 to L^2.",
              PropositionKind.THEOREM, "harmonic_analysis", importance=0.96,
              tags=("fourier-transform", "isometry")),
        _prop("Riesz-Thorin Interpolation Theorem",
              "If a linear operator is bounded on L^p0 and L^p1, it is bounded on L^p for p between p0 and p1.",
              PropositionKind.THEOREM, "harmonic_analysis", importance=0.94,
              tags=("interpolation", "lp-spaces")),
        _prop("Hausdorff-Young Theorem",
              "The Fourier transform maps L^p to L^q where 1/p + 1/q = 1, for 1 <= p <= 2.",
              PropositionKind.THEOREM, "harmonic_analysis", importance=0.91,
              tags=("fourier-transform", "lp-bounds")),
        _prop("Poisson Summation Formula",
              "The sum of a function over integers equals the sum of its Fourier transform over integers, up to normalization.",
              PropositionKind.THEOREM, "harmonic_analysis", importance=0.89,
              tags=("fourier-series", "summation")),
    ],
    keywords=("fourier-analysis", "wavelets", "singular-integrals", "multipliers", "maximal-functions", "littlewood-paley-theory", "hardy-spaces", "bmo"),
    judgment_site=(
        "Assessing convergence of Fourier series requires judging which function spaces allow reconstruction. "
        "Epistemic precision involves understanding the trade-offs between time and frequency localization."
    ),
)

# ---------------------------------------------------------------------------
# Field 75: PDE Theory
# ---------------------------------------------------------------------------
_PDE_THEORY = _field(
    name="PDE Theory",
    description=(
        "Studies partial differential equations and their solutions. "
        "Encompasses elliptic, parabolic, and hyperbolic equations. "
        "Fundamental to physics, engineering, and geometry. "
        "Methods include energy estimates, maximum principles, and fixed point theorems."
    ),
    props=[
        _prop("Cauchy-Kovalevskaya Theorem",
              "For analytic data and analytic coefficients, there exists a unique local analytic solution to the Cauchy problem.",
              PropositionKind.THEOREM, "pde_theory", importance=0.93,
              tags=("existence", "analytic-solutions")),
        _prop("Lax-Milgram Theorem",
              "A continuous coercive bilinear form on a Hilbert space induces an isomorphism via the Riesz representation.",
              PropositionKind.THEOREM, "pde_theory", importance=0.95,
              tags=("weak-solutions", "elliptic-pdes")),
        _prop("Sobolev Embedding Theorem",
              "Sobolev spaces W^{k,p} embed into L^q or continuous functions under dimensional constraints on k, p, q, and n.",
              PropositionKind.THEOREM, "pde_theory", importance=0.97,
              tags=("sobolev-spaces", "embeddings")),
        _prop("Schauder Estimates",
              "Solutions to elliptic equations with Holder continuous coefficients have Holder continuous derivatives with controlled norms.",
              PropositionKind.THEOREM, "pde_theory", importance=0.90,
              tags=("regularity", "elliptic-pdes")),
    ],
    keywords=("elliptic-equations", "parabolic-equations", "hyperbolic-equations", "sobolev-spaces", "weak-solutions", "regularity-theory", "maximum-principle", "energy-methods"),
    judgment_site=(
        "Judging PDE solution existence and uniqueness requires balancing regularity assumptions with generality. "
        "Epistemic care involves choosing appropriate function spaces for the physical problem at hand."
    ),
)

# ---------------------------------------------------------------------------
# Field 76: Distribution Theory
# ---------------------------------------------------------------------------
_DISTRIBUTION_THEORY = _field(
    name="Distribution Theory",
    description=(
        "Studies generalized functions allowing differentiation of non-smooth objects. "
        "Provides rigorous framework for delta functions and weak derivatives. "
        "Essential for modern PDE theory and quantum field theory."
    ),
    props=[
        _prop("Schwartz Kernel Theorem",
              "Every continuous linear operator between spaces of test functions is given by a distributional kernel.",
              PropositionKind.THEOREM, "distribution_theory", importance=0.94,
              tags=("operators", "kernels")),
        _prop("Structure Theorem for Distributions",
              "Every distribution is locally a finite-order derivative of a continuous function.",
              PropositionKind.THEOREM, "distribution_theory", importance=0.92,
              tags=("structure", "derivatives")),
        _prop("Paley-Wiener Theorem",
              "The Fourier transform characterizes distributions of compact support as entire functions of exponential type.",
              PropositionKind.THEOREM, "distribution_theory", importance=0.89,
              tags=("fourier-transform", "support")),
        _prop("Malgrange-Ehrenpreis Theorem",
              "Every nonzero constant-coefficient linear partial differential operator has a fundamental solution.",
              PropositionKind.THEOREM, "distribution_theory", importance=0.91,
              tags=("fundamental-solutions", "existence")),
    ],
    keywords=("distributions", "generalized-functions", "test-functions", "tempered-distributions", "sobolev-spaces", "weak-derivatives", "convolution", "support"),
    judgment_site=(
        "Reasoning about distributions requires tracking duality between test functions and functionals. "
        "Epistemic precision involves respecting which operations preserve distribution properties."
    ),
)

# ---------------------------------------------------------------------------
# Field 77: Microlocal Analysis
# ---------------------------------------------------------------------------
_MICROLOCAL_ANALYSIS = _field(
    name="Microlocal Analysis",
    description=(
        "Studies singularities of distributions and operators using phase space methods. "
        "Combines PDE theory with symplectic geometry. "
        "Key tool for understanding wave propagation and scattering."
    ),
    props=[
        _prop("Propagation of Singularities Theorem",
              "Singularities of solutions to hyperbolic PDEs propagate along bicharacteristic curves in phase space.",
              PropositionKind.THEOREM, "microlocal_analysis", importance=0.95,
              tags=("singularities", "wave-propagation")),
        _prop("Egorov's Theorem",
              "Conjugation of a pseudodifferential operator by a Fourier integral operator yields another pseudodifferential operator with transformed symbol.",
              PropositionKind.THEOREM, "microlocal_analysis", importance=0.90,
              tags=("pseudodifferential-operators", "conjugation")),
        _prop("Hormander's Propagation Theorem",
              "The wave front set of a solution to a pseudodifferential equation is controlled by the characteristic set.",
              PropositionKind.THEOREM, "microlocal_analysis", importance=0.93,
              tags=("wavefront-set", "characteristics")),
    ],
    keywords=("wavefront-set", "pseudodifferential-operators", "fourier-integral-operators", "symbol-calculus", "microlocalization", "propagation", "semiclassical-analysis", "quantization"),
    judgment_site=(
        "Assessing microlocal properties requires simultaneously tracking position and frequency information. "
        "Epistemic care involves understanding phase space geometry underlying differential operators."
    ),
)

# ---------------------------------------------------------------------------
# Field 78: Several Complex Variables
# ---------------------------------------------------------------------------
_SEVERAL_COMPLEX_VARIABLES = _field(
    name="Several Complex Variables",
    description=(
        "Studies holomorphic functions of multiple complex variables. "
        "Dramatically different from one-variable theory due to rigidity phenomena. "
        "Connects to algebraic geometry, PDE theory, and complex geometry."
    ),
    props=[
        _prop("Hartogs' Extension Theorem",
              "A holomorphic function on the complement of a compact set in C^n for n >= 2 extends holomorphically to the entire domain.",
              PropositionKind.THEOREM, "several_complex_variables", importance=0.95,
              tags=("extension", "rigidity")),
        _prop("Oka's Coherence Theorem",
              "The sheaf of holomorphic functions on a complex manifold is coherent.",
              PropositionKind.THEOREM, "several_complex_variables", importance=0.92,
              tags=("sheaf-theory", "coherence")),
        _prop("Cartan's Theorem A",
              "For a coherent sheaf on a Stein manifold, global sections generate stalks.",
              PropositionKind.THEOREM, "several_complex_variables", importance=0.90,
              tags=("stein-manifolds", "coherent-sheaves")),
        _prop("Cartan's Theorem B",
              "Higher cohomology groups of coherent sheaves on Stein manifolds vanish.",
              PropositionKind.THEOREM, "several_complex_variables", importance=0.91,
              tags=("cohomology", "stein-manifolds")),
    ],
    keywords=("holomorphic-functions", "stein-manifolds", "pseudoconvexity", "d-bar-problem", "coherent-sheaves", "analytic-continuation", "domains-of-holomorphy", "plurisubharmonic-functions"),
    judgment_site=(
        "Judging holomorphic extension phenomena requires understanding pseudoconvexity and domain geometry. "
        "Epistemic rigor involves recognizing when one-variable intuitions fail in higher dimensions."
    ),
)

# ---------------------------------------------------------------------------
# Field 79: Potential Theory
# ---------------------------------------------------------------------------
_POTENTIAL_THEORY = _field(
    name="Potential Theory",
    description=(
        "Studies harmonic functions and their generalizations. "
        "Closely related to probability theory through Brownian motion. "
        "Fundamental for understanding equilibrium phenomena in physics."
    ),
    props=[
        _prop("Maximum Principle",
              "A harmonic function on a bounded domain attains its maximum on the boundary.",
              PropositionKind.THEOREM, "potential_theory", importance=0.95,
              tags=("harmonic-functions", "maximum-principle")),
        _prop("Harnack's Inequality",
              "Positive harmonic functions on a domain satisfy uniform bounds relating values at different points.",
              PropositionKind.THEOREM, "potential_theory", importance=0.93,
              tags=("harmonic-functions", "bounds")),
        _prop("Perron's Method",
              "The Dirichlet problem can be solved by taking the supremum over subharmonic functions bounded by boundary data.",
              PropositionKind.THEOREM, "potential_theory", importance=0.90,
              tags=("dirichlet-problem", "subharmonic")),
        _prop("Wiener's Criterion",
              "A boundary point is regular for the Dirichlet problem if and only if a certain capacity-based integral diverges.",
              PropositionKind.THEOREM, "potential_theory", importance=0.89,
              tags=("regularity", "capacity")),
    ],
    keywords=("harmonic-functions", "subharmonic-functions", "dirichlet-problem", "capacity", "green-functions", "equilibrium-measures", "balayage", "fine-topology"),
    judgment_site=(
        "Assessing regularity of boundary points requires subtle capacity-theoretic judgments. "
        "Epistemic care involves understanding probabilistic interpretations of harmonic measure."
    ),
)

# ---------------------------------------------------------------------------
# Field 80: Ergodic Theory
# ---------------------------------------------------------------------------
_ERGODIC_THEORY = _field(
    name="Ergodic Theory",
    description=(
        "Studies dynamical systems with an invariant measure and long-term average behavior. "
        "Connects dynamics with probability theory and statistical mechanics. "
        "Provides rigorous foundation for time averages equaling space averages."
    ),
    props=[
        _prop("Birkhoff Ergodic Theorem",
              "For a measure-preserving transformation, time averages converge almost everywhere to space averages.",
              PropositionKind.THEOREM, "ergodic_theory", importance=0.98,
              tags=("ergodic-theorem", "convergence")),
        _prop("Von Neumann Ergodic Theorem",
              "For a unitary operator on L^2, Cesaro averages converge in L^2 norm to the projection onto invariant functions.",
              PropositionKind.THEOREM, "ergodic_theory", importance=0.94,
              tags=("l2-convergence", "unitary-operators")),
        _prop("Poincare Recurrence Theorem",
              "In a measure-preserving system, almost every point returns arbitrarily close to itself infinitely often.",
              PropositionKind.THEOREM, "ergodic_theory", importance=0.92,
              tags=("recurrence", "invariant-measure")),
        _prop("Ergodic Decomposition Theorem",
              "Every invariant measure decomposes uniquely into ergodic measures.",
              PropositionKind.THEOREM, "ergodic_theory", importance=0.88,
              tags=("decomposition", "ergodic-measures")),
    ],
    keywords=("dynamical-systems", "measure-preserving", "ergodicity", "mixing", "recurrence", "entropy", "invariant-measures", "cesaro-averages"),
    judgment_site=(
        "Judging ergodic behavior requires assessing whether time evolution mixes the space sufficiently. "
        "Epistemic precision involves distinguishing ergodicity from stronger mixing properties."
    ),
)

# ---------------------------------------------------------------------------
# Field 81: Measure Theory
# ---------------------------------------------------------------------------
_MEASURE_THEORY = _field(
    name="Measure Theory",
    description=(
        "Studies measures, integration, and measurable functions. "
        "Provides rigorous foundation for probability and analysis. "
        "Generalizes length, area, and volume to abstract spaces."
    ),
    props=[
        _prop("Radon-Nikodym Theorem",
              "If nu is absolutely continuous with respect to mu, there exists a measurable function f such that nu equals f times mu.",
              PropositionKind.THEOREM, "measure_theory", importance=0.96,
              tags=("absolute-continuity", "derivatives")),
        _prop("Fubini's Theorem",
              "For product measures, the double integral equals iterated integrals when the function is integrable.",
              PropositionKind.THEOREM, "measure_theory", importance=0.95,
              tags=("product-measures", "integration")),
        _prop("Lebesgue Differentiation Theorem",
              "For locally integrable functions, averages over shrinking balls converge to the function value almost everywhere.",
              PropositionKind.THEOREM, "measure_theory", importance=0.93,
              tags=("differentiation", "averages")),
        _prop("Egorov's Theorem",
              "Pointwise convergence almost everywhere implies uniform convergence on a set of arbitrarily large measure.",
              PropositionKind.THEOREM, "measure_theory", importance=0.87,
              tags=("convergence", "uniform-convergence")),
    ],
    keywords=("measures", "integration", "sigma-algebras", "measurable-functions", "lebesgue-measure", "borel-sets", "convergence-theorems", "signed-measures"),
    judgment_site=(
        "Reasoning about measurability requires judging which sets and functions are well-behaved. "
        "Epistemic care involves tracking null sets and almost-everywhere properties."
    ),
)

# ---------------------------------------------------------------------------
# Field 82: Banach Space Theory
# ---------------------------------------------------------------------------
_BANACH_SPACE_THEORY = _field(
    name="Banach Space Theory",
    description=(
        "Studies complete normed vector spaces and linear operators between them. "
        "Central to functional analysis and operator theory. "
        "Provides abstract framework for solving infinite-dimensional problems."
    ),
    props=[
        _prop("Hahn-Banach Theorem",
              "Every continuous linear functional on a subspace extends to the whole space preserving the norm.",
              PropositionKind.THEOREM, "banach_space_theory", importance=0.98,
              tags=("extension", "linear-functionals")),
        _prop("Banach-Steinhaus Theorem",
              "A pointwise bounded family of bounded linear operators is uniformly bounded.",
              PropositionKind.THEOREM, "banach_space_theory", importance=0.95,
              tags=("uniform-boundedness", "operators")),
        _prop("Open Mapping Theorem",
              "A surjective bounded linear operator between Banach spaces is open.",
              PropositionKind.THEOREM, "banach_space_theory", importance=0.96,
              tags=("surjectivity", "open-maps")),
        _prop("Closed Graph Theorem",
              "A linear operator between Banach spaces with closed graph is bounded.",
              PropositionKind.THEOREM, "banach_space_theory", importance=0.94,
              tags=("closed-graph", "boundedness")),
    ],
    keywords=("banach-spaces", "linear-operators", "dual-spaces", "weak-topology", "reflexivity", "compact-operators", "spectral-theory", "boundedness"),
    judgment_site=(
        "Assessing operator properties requires judging which topologies reveal functional structure. "
        "Epistemic precision involves understanding when weak and strong convergence differ."
    ),
)

# ---------------------------------------------------------------------------
# Field 83: Spectral Theory
# ---------------------------------------------------------------------------
_SPECTRAL_THEORY = _field(
    name="Spectral Theory",
    description=(
        "Studies eigenvalues and eigenvectors of linear operators. "
        "Generalizes linear algebra to infinite dimensions. "
        "Fundamental for quantum mechanics and PDE analysis."
    ),
    props=[
        _prop("Spectral Theorem for Self-Adjoint Operators",
              "Every bounded self-adjoint operator on a Hilbert space is unitarily equivalent to a multiplication operator.",
              PropositionKind.THEOREM, "spectral_theory", importance=0.98,
              tags=("self-adjoint", "diagonalization")),
        _prop("Weyl's Law",
              "The counting function for eigenvalues of the Laplacian has an asymptotic expansion with leading term proportional to volume.",
              PropositionKind.THEOREM, "spectral_theory", importance=0.92,
              tags=("eigenvalue-asymptotics", "laplacian")),
        _prop("Fredholm Alternative",
              "For a compact operator, either the equation Tx = y has a unique solution for all y, or the homogeneous equation has nontrivial solutions.",
              PropositionKind.THEOREM, "spectral_theory", importance=0.93,
              tags=("compact-operators", "solvability")),
        _prop("Min-Max Theorem",
              "Eigenvalues of self-adjoint operators can be characterized variationally by minimizing or maximizing the Rayleigh quotient.",
              PropositionKind.THEOREM, "spectral_theory", importance=0.90,
              tags=("variational-principles", "eigenvalues")),
    ],
    keywords=("eigenvalues", "eigenvectors", "self-adjoint-operators", "spectrum", "resolvents", "compact-operators", "essential-spectrum", "rayleigh-quotient"),
    judgment_site=(
        "Judging spectral properties requires understanding which operator perturbations preserve essential features. "
        "Epistemic care involves distinguishing discrete from essential spectrum."
    ),
)

# ---------------------------------------------------------------------------
# Field 84: Calculus of Variations
# ---------------------------------------------------------------------------
_CALCULUS_OF_VARIATIONS = _field(
    name="Calculus of Variations",
    description=(
        "Studies optimization of functionals, often arising from physical principles. "
        "Seeks functions that minimize or maximize integral expressions. "
        "Fundamental for mechanics, geometry, and optimal control."
    ),
    props=[
        _prop("Euler-Lagrange Equation",
              "A smooth function minimizing an integral functional satisfies the Euler-Lagrange differential equation.",
              PropositionKind.THEOREM, "calculus_of_variations", importance=0.97,
              tags=("optimization", "necessary-conditions")),
        _prop("Mountain Pass Theorem",
              "A functional satisfying Palais-Smale condition and certain geometric conditions has a critical point at a mountain pass level.",
              PropositionKind.THEOREM, "calculus_of_variations", importance=0.93,
              tags=("critical-points", "minimax")),
        _prop("Direct Method",
              "A weakly lower semicontinuous coercive functional on a reflexive space attains its infimum.",
              PropositionKind.THEOREM, "calculus_of_variations", importance=0.91,
              tags=("existence", "minimization")),
        _prop("Noether's Theorem",
              "Every differentiable symmetry of the action corresponds to a conservation law.",
              PropositionKind.THEOREM, "calculus_of_variations", importance=0.95,
              tags=("symmetry", "conservation-laws")),
    ],
    keywords=("functionals", "optimization", "euler-lagrange", "critical-points", "variational-methods", "geodesics", "minimal-surfaces", "constraint-optimization"),
    judgment_site=(
        "Assessing minimizers requires judging which function spaces support compactness arguments. "
        "Epistemic precision involves understanding coercivity and lower semicontinuity conditions."
    ),
)

# ---------------------------------------------------------------------------
# Field 85: Nonlinear Analysis
# ---------------------------------------------------------------------------
_NONLINEAR_ANALYSIS = _field(
    name="Nonlinear Analysis",
    description=(
        "Studies nonlinear equations and operators using topological and variational methods. "
        "Encompasses fixed point theory, degree theory, and bifurcation. "
        "Essential for nonlinear PDEs, dynamics, and applied mathematics."
    ),
    props=[
        _prop("Brouwer Fixed Point Theorem",
              "Every continuous map from a closed ball to itself has a fixed point.",
              PropositionKind.THEOREM, "nonlinear_analysis", importance=0.96,
              tags=("fixed-points", "topology")),
        _prop("Schauder Fixed Point Theorem",
              "Every continuous compact map from a convex closed subset of a Banach space to itself has a fixed point.",
              PropositionKind.THEOREM, "nonlinear_analysis", importance=0.94,
              tags=("fixed-points", "compact-operators")),
        _prop("Implicit Function Theorem",
              "If F(x,y) = 0 and the partial derivative with respect to y is invertible, then y is locally a function of x.",
              PropositionKind.THEOREM, "nonlinear_analysis", importance=0.95,
              tags=("implicit-functions", "invertibility")),
        _prop("Lyapunov-Schmidt Reduction",
              "Nonlinear equations can be decomposed into finite-dimensional bifurcation equations and infinite-dimensional auxiliary equations.",
              PropositionKind.THEOREM, "nonlinear_analysis", importance=0.88,
              tags=("bifurcation", "reduction")),
    ],
    keywords=("nonlinear-equations", "fixed-points", "degree-theory", "bifurcation", "critical-point-theory", "monotone-operators", "variational-inequalities", "implicit-functions"),
    judgment_site=(
        "Judging existence of solutions to nonlinear equations requires topological and variational reasoning. "
        "Epistemic care involves choosing which linearization or approximation scheme is appropriate."
    ),
)

# ---------------------------------------------------------------------------
# Field 86: Coding Theory
# ---------------------------------------------------------------------------
_CODING_THEORY = _field(
    name="Coding Theory",
    description=(
        "Studies error-correcting codes for reliable data transmission and storage. "
        "Combines algebra, combinatorics, and information theory. "
        "Essential for communication systems and data storage."
    ),
    props=[
        _prop("Shannon's Noisy Channel Coding Theorem",
              "For any channel with positive capacity, there exist codes achieving arbitrarily low error probability at rates below capacity.",
              PropositionKind.THEOREM, "coding_theory", importance=0.98,
              tags=("information-theory", "channel-capacity")),
        _prop("Hamming Bound",
              "The number of codewords in a code with minimum distance d is bounded by the sphere-packing bound.",
              PropositionKind.THEOREM, "coding_theory", importance=0.91,
              tags=("bounds", "minimum-distance")),
        _prop("Singleton Bound",
              "For a code of length n and minimum distance d, the number of information symbols is at most n - d + 1.",
              PropositionKind.THEOREM, "coding_theory", importance=0.90,
              tags=("bounds", "mds-codes")),
        _prop("MacWilliams Identity",
              "The weight enumerator of a code and its dual are related by a linear transformation.",
              PropositionKind.THEOREM, "coding_theory", importance=0.87,
              tags=("weight-enumerators", "duality")),
    ],
    keywords=("error-correction", "linear-codes", "cyclic-codes", "hamming-codes", "reed-solomon", "minimum-distance", "decoding-algorithms", "channel-capacity"),
    judgment_site=(
        "Designing codes requires judging trade-offs between rate, distance, and decoding complexity. "
        "Epistemic precision involves understanding which algebraic structures enable efficient error correction."
    ),
)

# ---------------------------------------------------------------------------
# Field 87: Cryptography (Mathematical)
# ---------------------------------------------------------------------------
_CRYPTOGRAPHY_MATHEMATICAL = _field(
    name="Cryptography (Mathematical)",
    description=(
        "Studies mathematical foundations of secure communication and computation. "
        "Based on computational hardness assumptions from number theory and algebra. "
        "Includes public-key cryptography, zero-knowledge proofs, and cryptographic protocols."
    ),
    props=[
        _prop("RSA Security Assumption",
              "Factoring large semiprimes is computationally intractable, ensuring security of RSA encryption.",
              PropositionKind.DEFINITION, "cryptography_mathematical", importance=0.96,
              tags=("public-key", "factoring")),
        _prop("Diffie-Hellman Key Exchange Protocol",
              "Two parties can establish a shared secret over a public channel using modular exponentiation.",
              PropositionKind.DEFINITION, "cryptography_mathematical", importance=0.95,
              tags=("key-exchange", "discrete-log")),
        _prop("Discrete Logarithm Problem Hardness",
              "Computing discrete logarithms in suitable groups is believed to be computationally hard.",
              PropositionKind.DEFINITION, "cryptography_mathematical", importance=0.93,
              tags=("discrete-log", "hardness")),
        _prop("Goldwasser-Micali Semantic Security",
              "A cryptosystem is semantically secure if ciphertext reveals no partial information about plaintext to polynomial-time adversaries.",
              PropositionKind.DEFINITION, "cryptography_mathematical", importance=0.90,
              tags=("semantic-security", "provable-security")),
    ],
    keywords=("public-key-cryptography", "rsa", "elliptic-curves", "discrete-logarithm", "zero-knowledge", "homomorphic-encryption", "digital-signatures", "provable-security"),
    judgment_site=(
        "Assessing cryptographic security requires judging which computational problems remain intractable. "
        "Epistemic care involves distinguishing proven security reductions from heuristic assumptions."
    ),
)

# ---------------------------------------------------------------------------
# Field 88: Formal Language Theory
# ---------------------------------------------------------------------------
_FORMAL_LANGUAGE_THEORY = _field(
    name="Formal Language Theory",
    description=(
        "Studies formal grammars, automata, and the languages they generate. "
        "Foundational for compiler design, natural language processing, and computability. "
        "Classifies languages by computational complexity of recognition."
    ),
    props=[
        _prop("Chomsky Hierarchy",
              "Formal languages are classified into four types: regular, context-free, context-sensitive, and recursively enumerable.",
              PropositionKind.THEOREM, "formal_language_theory", importance=0.96,
              tags=("classification", "grammars")),
        _prop("Pumping Lemma for Regular Languages",
              "Every sufficiently long string in a regular language can be pumped by repeating a substring.",
              PropositionKind.LEMMA, "formal_language_theory", importance=0.91,
              tags=("regular-languages", "non-regularity")),
        _prop("Pumping Lemma for Context-Free Languages",
              "Every sufficiently long string in a context-free language can be pumped with synchronized repetitions.",
              PropositionKind.LEMMA, "formal_language_theory", importance=0.89,
              tags=("context-free", "non-context-free")),
        _prop("Rice's Theorem",
              "Every non-trivial semantic property of Turing machine languages is undecidable.",
              PropositionKind.THEOREM, "formal_language_theory", importance=0.93,
              tags=("undecidability", "turing-machines")),
    ],
    keywords=("automata", "grammars", "regular-languages", "context-free-languages", "turing-machines", "decidability", "parsing", "computational-complexity"),
    judgment_site=(
        "Judging language membership and grammar equivalence requires understanding automata-theoretic methods. "
        "Epistemic precision involves recognizing which language properties are decidable versus undecidable."
    ),
)
# ---------------------------------------------------------------------------
# Field 89: Automata Theory
# ---------------------------------------------------------------------------
_AUTOMATA_THEORY = _field(
    name="Automata Theory",
    description=(
        "Automata theory studies abstract machines and computational models. "
        "It establishes the foundations of formal languages and their recognizability. "
        "The theory provides a mathematical framework for understanding computation limits and decidability."
    ),
    props=[
        _prop("Myhill-Nerode Theorem",
              "A language is regular if and only if its Myhill-Nerode equivalence relation has finite index.",
              PropositionKind.THEOREM, "automata_theory", importance=0.92,
              tags=("regularity", "equivalence")),
        _prop("Pumping Lemma for Regular Languages",
              "Every regular language satisfies the pumping property: sufficiently long strings contain a repeatable substring.",
              PropositionKind.LEMMA, "automata_theory", importance=0.88,
              tags=("regular", "pumping")),
        _prop("Kleene's Theorem",
              "A language is regular if and only if it can be described by a regular expression.",
              PropositionKind.THEOREM, "automata_theory", importance=0.94,
              tags=("regular", "expression")),
        _prop("Deterministic Finite Automaton",
              "A DFA is a quintuple consisting of states, alphabet, transition function, start state, and accept states.",
              PropositionKind.DEFINITION, "automata_theory", importance=0.90,
              tags=("automaton", "finite")),
    ],
    keywords=("finite automata", "regular languages", "state machines", "determinism", "nondeterminism", "pumping lemma", "Kleene star", "transition function"),
    judgment_site=(
        "Automata theory grounds epistemic judgments about what can be computed with finite resources. "
        "It provides precise boundaries between decidable and undecidable recognition problems."
    ),
)

# ---------------------------------------------------------------------------
# Field 90: Computability Theory
# ---------------------------------------------------------------------------
_COMPUTABILITY_THEORY = _field(
    name="Computability Theory",
    description=(
        "Computability theory investigates which problems can be solved algorithmically. "
        "It characterizes the fundamental limitations of mechanical computation. "
        "The field establishes hierarchies of computational difficulty and undecidability."
    ),
    props=[
        _prop("Church-Turing Thesis",
              "The intuitive notion of algorithmic computability coincides with Turing machine computability.",
              PropositionKind.THEOREM, "computability_theory", importance=0.98,
              tags=("thesis", "computability")),
        _prop("Halting Problem Undecidability",
              "There exists no algorithm that decides whether an arbitrary Turing machine halts on a given input.",
              PropositionKind.THEOREM, "computability_theory", importance=0.97,
              tags=("undecidable", "halting")),
        _prop("Rice's Theorem",
              "Every non-trivial semantic property of Turing machines is undecidable.",
              PropositionKind.THEOREM, "computability_theory", importance=0.93,
              tags=("undecidability", "semantic")),
        _prop("Recursively Enumerable Set",
              "A set is recursively enumerable if it is the domain of some computable partial function.",
              PropositionKind.DEFINITION, "computability_theory", importance=0.89,
              tags=("enumerable", "computable")),
    ],
    keywords=("Turing machines", "decidability", "halting problem", "recursive functions", "Church-Turing", "reduction", "oracle machines", "computable"),
    judgment_site=(
        "Computability theory delimits the scope of algorithmic knowledge acquisition. "
        "It reveals fundamental epistemic limits on what can be mechanically verified or refuted."
    ),
)

# ---------------------------------------------------------------------------
# Field 91: Descriptive Complexity
# ---------------------------------------------------------------------------
_DESCRIPTIVE_COMPLEXITY = _field(
    name="Descriptive Complexity",
    description=(
        "Descriptive complexity connects computational complexity to logic. "
        "It characterizes complexity classes by the logical resources needed to describe problems. "
        "The field provides a logical perspective on computational hardness."
    ),
    props=[
        _prop("Fagin's Theorem",
              "A property is in NP if and only if it is expressible in existential second-order logic.",
              PropositionKind.THEOREM, "descriptive_complexity", importance=0.95,
              tags=("NP", "second-order")),
        _prop("Immerman-Szelepscenyi Theorem",
              "Nondeterministic space complexity classes are closed under complementation.",
              PropositionKind.THEOREM, "descriptive_complexity", importance=0.91,
              tags=("space", "complement")),
        _prop("First-Order Logic Captures AC0",
              "Properties definable in first-order logic with ordering are exactly those computable by constant-depth circuits.",
              PropositionKind.THEOREM, "descriptive_complexity", importance=0.88,
              tags=("first-order", "circuits")),
    ],
    keywords=("logical expressibility", "second-order logic", "Fagin theorem", "finite model theory", "complexity classes", "existential quantification", "descriptive", "capture"),
    judgment_site=(
        "Descriptive complexity frames computational questions as matters of logical expressibility. "
        "It provides an epistemic bridge between syntax and computational resources."
    ),
)

# ---------------------------------------------------------------------------
# Field 92: Circuit Complexity
# ---------------------------------------------------------------------------
_CIRCUIT_COMPLEXITY = _field(
    name="Circuit Complexity",
    description=(
        "Circuit complexity studies Boolean circuit models of computation. "
        "It investigates size and depth bounds for computing functions. "
        "The field provides concrete lower bound techniques and separation results."
    ),
    props=[
        _prop("Shannon's Counting Argument",
              "Almost all Boolean functions require exponentially large circuits.",
              PropositionKind.THEOREM, "circuit_complexity", importance=0.87,
              tags=("counting", "lower-bound")),
        _prop("Parity Not in AC0",
              "The parity function cannot be computed by constant-depth unbounded fan-in circuits of polynomial size.",
              PropositionKind.THEOREM, "circuit_complexity", importance=0.93,
              tags=("parity", "AC0")),
        _prop("Monotone Circuit",
              "A monotone circuit uses only AND and OR gates without negation.",
              PropositionKind.DEFINITION, "circuit_complexity", importance=0.84,
              tags=("monotone", "gates")),
        _prop("Razborov-Smolensky Theorem",
              "AC0 circuits with MOD-p gates cannot compute MOD-q for distinct primes p and q.",
              PropositionKind.THEOREM, "circuit_complexity", importance=0.90,
              tags=("modular", "AC0")),
    ],
    keywords=("Boolean circuits", "circuit depth", "fan-in", "lower bounds", "monotone circuits", "AC0", "Razborov", "gate complexity"),
    judgment_site=(
        "Circuit complexity establishes concrete resource requirements for computation. "
        "It grounds epistemic claims about computational hardness in combinatorial structure."
    ),
)

# ---------------------------------------------------------------------------
# Field 93: Algorithmic Game Theory
# ---------------------------------------------------------------------------
_ALGORITHMIC_GAME_THEORY = _field(
    name="Algorithmic Game Theory",
    description=(
        "Algorithmic game theory analyzes computational aspects of strategic interaction. "
        "It studies the complexity of computing equilibria and mechanism design. "
        "The field merges game-theoretic concepts with algorithmic analysis."
    ),
    props=[
        _prop("Nash Equilibrium Existence",
              "Every finite game has at least one mixed-strategy Nash equilibrium.",
              PropositionKind.THEOREM, "algorithmic_game_theory", importance=0.96,
              tags=("equilibrium", "existence")),
        _prop("PPAD-Completeness of Nash",
              "Computing a Nash equilibrium in a bimatrix game is PPAD-complete.",
              PropositionKind.THEOREM, "algorithmic_game_theory", importance=0.92,
              tags=("PPAD", "complexity")),
        _prop("Price of Anarchy",
              "The price of anarchy measures the efficiency loss due to selfish behavior in equilibrium.",
              PropositionKind.DEFINITION, "algorithmic_game_theory", importance=0.88,
              tags=("anarchy", "efficiency")),
        _prop("Vickrey-Clarke-Groves Mechanism",
              "VCG mechanisms are truthful and maximize social welfare in dominant strategy equilibrium.",
              PropositionKind.THEOREM, "algorithmic_game_theory", importance=0.90,
              tags=("mechanism", "truthful")),
    ],
    keywords=("Nash equilibrium", "PPAD", "mechanism design", "price of anarchy", "strategic behavior", "computational complexity", "truthfulness", "auctions"),
    judgment_site=(
        "Algorithmic game theory reveals computational limits on rational strategic reasoning. "
        "It shows that finding optimal strategies can be inherently intractable."
    ),
)

# ---------------------------------------------------------------------------
# Field 94: Distributed Computing Theory
# ---------------------------------------------------------------------------
_DISTRIBUTED_COMPUTING_THEORY = _field(
    name="Distributed Computing Theory",
    description=(
        "Distributed computing theory studies computation across multiple agents. "
        "It analyzes the fundamental limits of coordination and consensus. "
        "The field characterizes the role of timing, failures, and communication in distributed protocols."
    ),
    props=[
        _prop("CAP Theorem",
              "A distributed system cannot simultaneously guarantee consistency, availability, and partition tolerance.",
              PropositionKind.THEOREM, "distributed_computing_theory", importance=0.94,
              tags=("consistency", "availability")),
        _prop("Fischer-Lynch-Paterson Impossibility",
              "No deterministic asynchronous consensus protocol can tolerate even a single process failure.",
              PropositionKind.THEOREM, "distributed_computing_theory", importance=0.97,
              tags=("consensus", "impossibility")),
        _prop("Byzantine Agreement Lower Bound",
              "Byzantine agreement requires at least 3f+1 processes to tolerate f Byzantine failures.",
              PropositionKind.THEOREM, "distributed_computing_theory", importance=0.91,
              tags=("Byzantine", "agreement")),
    ],
    keywords=("consensus", "Byzantine fault tolerance", "asynchronous", "CAP theorem", "FLP impossibility", "distributed protocols", "synchronization", "message passing"),
    judgment_site=(
        "Distributed computing theory establishes fundamental constraints on collaborative knowledge formation. "
        "It reveals inherent trade-offs between consistency, availability, and fault tolerance in distributed systems."
    ),
)

# ---------------------------------------------------------------------------
# Field 95: Extremal Combinatorics
# ---------------------------------------------------------------------------
_EXTREMAL_COMBINATORICS = _field(
    name="Extremal Combinatorics",
    description=(
        "Extremal combinatorics determines maximum or minimum sizes of combinatorial structures. "
        "It investigates how global properties constrain local configurations. "
        "The field employs algebraic, probabilistic, and topological methods to establish sharp bounds."
    ),
    props=[
        _prop("Turan's Theorem",
              "The maximum number of edges in a triangle-free graph on n vertices is achieved by a complete balanced bipartite graph.",
              PropositionKind.THEOREM, "extremal_combinatorics", importance=0.93,
              tags=("graph", "Turan")),
        _prop("Erdos-Ko-Rado Theorem",
              "The maximum size of an intersecting family of k-subsets of an n-set is the binomial coefficient C(n-1,k-1) when n is sufficiently large.",
              PropositionKind.THEOREM, "extremal_combinatorics", importance=0.90,
              tags=("intersecting", "family")),
        _prop("Mantel's Theorem",
              "A triangle-free graph on n vertices has at most n-squared over 4 edges.",
              PropositionKind.THEOREM, "extremal_combinatorics", importance=0.87,
              tags=("triangle-free", "edges")),
        _prop("Sperner's Theorem",
              "The maximum size of an antichain in the power set of an n-element set is C(n, floor(n/2)).",
              PropositionKind.THEOREM, "extremal_combinatorics", importance=0.89,
              tags=("antichain", "Sperner")),
    ],
    keywords=("extremal problems", "Turan graphs", "intersecting families", "hypergraphs", "Sperner theorem", "Erdos-Ko-Rado", "density", "forbidden substructures"),
    judgment_site=(
        "Extremal combinatorics reveals how constraints propagate through discrete structures. "
        "It provides sharp quantitative bounds on the limits of combinatorial configurations."
    ),
)

# ---------------------------------------------------------------------------
# Field 96: Ramsey Theory
# ---------------------------------------------------------------------------
_RAMSEY_THEORY = _field(
    name="Ramsey Theory",
    description=(
        "Ramsey theory studies the emergence of order in large structures. "
        "It guarantees that sufficiently large systems contain structured subconfigurations. "
        "The field quantifies the threshold sizes at which regularity becomes unavoidable."
    ),
    props=[
        _prop("Ramsey's Theorem",
              "For any positive integers r and k, there exists a minimum number R(r,k) such that any 2-coloring of edges of the complete graph on R(r,k) vertices contains a monochromatic clique of size r or k.",
              PropositionKind.THEOREM, "ramsey_theory", importance=0.95,
              tags=("Ramsey", "coloring")),
        _prop("Van der Waerden's Theorem",
              "For any positive integers r and k, there exists N such that any r-coloring of {1,2,...,N} contains a monochromatic arithmetic progression of length k.",
              PropositionKind.THEOREM, "ramsey_theory", importance=0.92,
              tags=("arithmetic", "progression")),
        _prop("Hales-Jewett Theorem",
              "For any finite alphabet and positive integer k, there exists a dimension n such that any coloring of the n-dimensional combinatorial cube contains a monochromatic combinatorial line.",
              PropositionKind.THEOREM, "ramsey_theory", importance=0.89,
              tags=("combinatorial", "line")),
        _prop("Schur's Theorem",
              "For any positive integer r, there exists N such that any r-coloring of {1,2,...,N} contains a monochromatic solution to x+y=z.",
              PropositionKind.THEOREM, "ramsey_theory", importance=0.86,
              tags=("Schur", "equation")),
    ],
    keywords=("Ramsey numbers", "coloring", "arithmetic progressions", "Van der Waerden", "Hales-Jewett", "pigeonhole principle", "regularity", "monochromatic"),
    judgment_site=(
        "Ramsey theory demonstrates that complete disorder is impossible in sufficiently large systems. "
        "It reveals unavoidable patterns that constrain the space of possible configurations."
    ),
)

# ---------------------------------------------------------------------------
# Field 97: Design Theory
# ---------------------------------------------------------------------------
_DESIGN_THEORY = _field(
    name="Design Theory",
    description=(
        "Design theory constructs balanced and symmetric combinatorial configurations. "
        "It studies block designs, Latin squares, and related structures. "
        "The field has applications to experimental design, coding theory, and finite geometry."
    ),
    props=[
        _prop("Fisher's Inequality",
              "In a symmetric balanced incomplete block design, the number of blocks equals the number of points.",
              PropositionKind.THEOREM, "design_theory", importance=0.90,
              tags=("Fisher", "BIBD")),
        _prop("Steiner System",
              "A Steiner system S(t,k,v) is a collection of k-subsets of a v-set such that every t-subset is contained in exactly one block.",
              PropositionKind.DEFINITION, "design_theory", importance=0.87,
              tags=("Steiner", "system")),
        _prop("Bruck-Ryser-Chowla Theorem",
              "If a symmetric BIBD with parameters (v,k,lambda) exists and v is even, then k-lambda must be a perfect square.",
              PropositionKind.THEOREM, "design_theory", importance=0.88,
              tags=("symmetric", "necessary")),
        _prop("Kirkman Schoolgirl Problem",
              "Fifteen schoolgirls can walk in five rows of three for seven days so that no two walk together twice, constructing a resolvable Steiner triple system.",
              PropositionKind.THEOREM, "design_theory", importance=0.84,
              tags=("Kirkman", "resolvable")),
    ],
    keywords=("block designs", "BIBD", "Steiner systems", "Latin squares", "Fisher inequality", "balanced", "combinatorial design", "resolvability"),
    judgment_site=(
        "Design theory provides methods for constructing balanced experimental frameworks. "
        "It ensures systematic coverage and symmetry in discrete structures used for inference."
    ),
)

# ---------------------------------------------------------------------------
# Field 98: Riemannian Geometry
# ---------------------------------------------------------------------------
_RIEMANNIAN_GEOMETRY = _field(
    name="Riemannian Geometry",
    description=(
        "Riemannian geometry studies smooth manifolds equipped with inner products on tangent spaces. "
        "It provides the mathematical foundation for general relativity and geometric analysis. "
        "The field investigates curvature, geodesics, and the interplay between local and global geometry."
    ),
    props=[
        _prop("Gauss-Bonnet Theorem",
              "For a closed orientable surface, the integral of Gaussian curvature equals 2 pi times the Euler characteristic.",
              PropositionKind.THEOREM, "riemannian_geometry", importance=0.95,
              tags=("curvature", "topology")),
        _prop("Hopf-Rinow Theorem",
              "A Riemannian manifold is geodesically complete if and only if it is complete as a metric space.",
              PropositionKind.THEOREM, "riemannian_geometry", importance=0.91,
              tags=("completeness", "geodesics")),
        _prop("Myers' Theorem",
              "A complete Riemannian manifold with Ricci curvature bounded below by a positive constant is compact with finite fundamental group.",
              PropositionKind.THEOREM, "riemannian_geometry", importance=0.88,
              tags=("Ricci", "compactness")),
        _prop("Riemannian Metric",
              "A Riemannian metric on a smooth manifold is a smoothly varying positive definite inner product on each tangent space.",
              PropositionKind.DEFINITION, "riemannian_geometry", importance=0.90,
              tags=("metric", "manifold")),
    ],
    keywords=("curvature", "geodesics", "Ricci tensor", "sectional curvature", "manifolds", "Gauss-Bonnet", "completeness", "Riemannian metric"),
    judgment_site=(
        "Riemannian geometry provides the framework for understanding curved spaces and their properties. "
        "It enables epistemic claims about the global structure arising from local curvature conditions."
    ),
)

# ---------------------------------------------------------------------------
# Field 99: Geometric Analysis
# ---------------------------------------------------------------------------
_GEOMETRIC_ANALYSIS = _field(
    name="Geometric Analysis",
    description=(
        "Geometric analysis applies analytic techniques to geometric problems on manifolds. "
        "It studies partial differential equations arising from geometric variational problems. "
        "The field connects curvature flows, minimal surfaces, and harmonic maps to topology."
    ),
    props=[
        _prop("Uniformization Theorem",
              "Every simply connected Riemann surface is conformally equivalent to the plane, sphere, or unit disk.",
              PropositionKind.THEOREM, "geometric_analysis", importance=0.96,
              tags=("conformal", "Riemann")),
        _prop("Atiyah-Singer Index Theorem",
              "The analytic index of an elliptic operator on a compact manifold equals its topological index.",
              PropositionKind.THEOREM, "geometric_analysis", importance=0.98,
              tags=("index", "elliptic")),
        _prop("Schoen-Yau Positive Mass Theorem",
              "An asymptotically flat Riemannian manifold with non-negative scalar curvature has non-negative total mass.",
              PropositionKind.THEOREM, "geometric_analysis", importance=0.93,
              tags=("mass", "curvature")),
        _prop("Yamabe Problem Solution",
              "Every compact Riemannian manifold admits a metric of constant scalar curvature in its conformal class.",
              PropositionKind.THEOREM, "geometric_analysis", importance=0.91,
              tags=("Yamabe", "scalar")),
    ],
    keywords=("minimal surfaces", "harmonic maps", "heat flow", "Ricci flow", "index theory", "elliptic operators", "conformal geometry", "mean curvature"),
    judgment_site=(
        "Geometric analysis reveals how analytic methods constrain geometric structures. "
        "It demonstrates deep connections between curvature conditions and topological invariants."
    ),
)

# ---------------------------------------------------------------------------
# Field 100: Convex Geometry
# ---------------------------------------------------------------------------
_CONVEX_GEOMETRY = _field(
    name="Convex Geometry",
    description=(
        "Convex geometry studies the properties of convex sets and bodies in Euclidean space. "
        "It investigates volume, surface area, and geometric inequalities. "
        "The field has connections to optimization, functional analysis, and discrete geometry."
    ),
    props=[
        _prop("Brunn-Minkowski Inequality",
              "For compact sets A and B in Euclidean space, the volume of the Minkowski sum satisfies a concavity inequality.",
              PropositionKind.THEOREM, "convex_geometry", importance=0.94,
              tags=("volume", "Minkowski")),
        _prop("Blaschke Selection Theorem",
              "Every bounded sequence of convex bodies in Euclidean space has a subsequence converging in Hausdorff distance.",
              PropositionKind.THEOREM, "convex_geometry", importance=0.88,
              tags=("compactness", "Blaschke")),
        _prop("Isoperimetric Inequality",
              "Among all sets of given volume in Euclidean space, the ball has the smallest surface area.",
              PropositionKind.THEOREM, "convex_geometry", importance=0.92,
              tags=("isoperimetric", "surface")),
        _prop("Support Function",
              "The support function of a convex body uniquely determines the body and is sublinear.",
              PropositionKind.DEFINITION, "convex_geometry", importance=0.86,
              tags=("support", "function")),
    ],
    keywords=("convex bodies", "Brunn-Minkowski", "isoperimetric", "mixed volumes", "Minkowski sum", "support functions", "geometric inequalities", "volume"),
    judgment_site=(
        "Convex geometry establishes fundamental inequalities constraining geometric configurations. "
        "It provides sharp quantitative bounds on volume, surface area, and related functionals."
    ),
)

# ---------------------------------------------------------------------------
# Field 101: Discrete Geometry
# ---------------------------------------------------------------------------
_DISCRETE_GEOMETRY = _field(
    name="Discrete Geometry",
    description=(
        "Discrete geometry studies combinatorial and geometric properties of finite point sets and arrangements. "
        "It investigates packing, covering, and incidence problems. "
        "The field combines techniques from combinatorics, topology, and convexity."
    ),
    props=[
        _prop("Erdos-Szekeres Theorem",
              "Any sequence of at least (r-1)(s-1)+1 distinct real numbers contains a monotone subsequence of length r or s.",
              PropositionKind.THEOREM, "discrete_geometry", importance=0.89,
              tags=("monotone", "sequence")),
        _prop("Kepler Conjecture",
              "The density of a sphere packing in three-dimensional Euclidean space is at most pi over square root of 18.",
              PropositionKind.THEOREM, "discrete_geometry", importance=0.95,
              tags=("packing", "spheres")),
        _prop("Szemeredi-Trotter Theorem",
              "The number of incidences between n points and m lines in the plane is at most O(n^(2/3) m^(2/3) + n + m).",
              PropositionKind.THEOREM, "discrete_geometry", importance=0.91,
              tags=("incidences", "combinatorial")),
        _prop("Happy Ending Problem",
              "Any set of five points in general position in the plane contains four points forming a convex quadrilateral.",
              PropositionKind.THEOREM, "discrete_geometry", importance=0.84,
              tags=("convex", "position")),
    ],
    keywords=("sphere packing", "incidence geometry", "point configurations", "Erdos problems", "polytopes", "lattice points", "geometric combinatorics", "Kepler"),
    judgment_site=(
        "Discrete geometry reveals combinatorial structure in finite geometric configurations. "
        "It quantifies how geometric constraints limit the possible arrangements of discrete objects."
    ),
)

# ---------------------------------------------------------------------------
# Field 102: Geometric Group Theory
# ---------------------------------------------------------------------------
_GEOMETRIC_GROUP_THEORY = _field(
    name="Geometric Group Theory",
    description=(
        "Geometric group theory studies groups through their actions on geometric spaces. "
        "It investigates the large-scale geometry of groups and their Cayley graphs. "
        "The field connects algebraic properties to quasi-isometric invariants."
    ),
    props=[
        _prop("Gromov's Polynomial Growth Theorem",
              "A finitely generated group has polynomial growth if and only if it is virtually nilpotent.",
              PropositionKind.THEOREM, "geometric_group_theory", importance=0.96,
              tags=("growth", "nilpotent")),
        _prop("Svarc-Milnor Lemma",
              "A group acting properly and cocompactly on a proper geodesic metric space is quasi-isometric to that space.",
              PropositionKind.LEMMA, "geometric_group_theory", importance=0.90,
              tags=("quasi-isometry", "action")),
        _prop("Dehn's Algorithm",
              "The word problem in a hyperbolic group is solvable in linear time using Dehn's algorithm.",
              PropositionKind.THEOREM, "geometric_group_theory", importance=0.87,
              tags=("word-problem", "hyperbolic")),
        _prop("Cayley Graph",
              "The Cayley graph of a group with respect to a generating set has vertices corresponding to group elements and edges connecting elements differing by a generator.",
              PropositionKind.DEFINITION, "geometric_group_theory", importance=0.88,
              tags=("Cayley", "graph")),
    ],
    keywords=("Cayley graphs", "hyperbolic groups", "quasi-isometry", "word problem", "growth functions", "Gromov hyperbolicity", "geometric actions", "asymptotic geometry"),
    judgment_site=(
        "Geometric group theory reveals how algebraic structure manifests in large-scale geometry. "
        "It establishes that many group properties are invariant under quasi-isometry."
    ),
)

# ---------------------------------------------------------------------------
# Field 103: Metric Geometry
# ---------------------------------------------------------------------------
_METRIC_GEOMETRY = _field(
    name="Metric Geometry",
    description=(
        "Metric geometry studies spaces defined only by distance functions. "
        "It generalizes Riemannian geometry to non-smooth settings. "
        "The field investigates curvature bounds, optimal transport, and metric measure spaces."
    ),
    props=[
        _prop("Gromov-Hausdorff Convergence",
              "The Gromov-Hausdorff distance metrizes the space of compact metric spaces up to isometry.",
              PropositionKind.THEOREM, "metric_geometry", importance=0.91,
              tags=("convergence", "Hausdorff")),
        _prop("Alexandrov Spaces",
              "Alexandrov spaces with curvature bounded below satisfy a synthetic triangle comparison condition.",
              PropositionKind.DEFINITION, "metric_geometry", importance=0.88,
              tags=("Alexandrov", "curvature")),
        _prop("Gromov's Compactness Theorem",
              "The class of compact metric spaces with diameter and cardinality bounds is precompact in Gromov-Hausdorff distance.",
              PropositionKind.THEOREM, "metric_geometry", importance=0.90,
              tags=("compactness", "Gromov")),
    ],
    keywords=("metric spaces", "Gromov-Hausdorff", "Alexandrov spaces", "CAT(k) spaces", "curvature bounds", "geodesic spaces", "comparison geometry", "optimal transport"),
    judgment_site=(
        "Metric geometry extends geometric reasoning beyond smooth manifolds. "
        "It provides synthetic frameworks for studying curvature and convergence in general metric spaces."
    ),
)

# ---------------------------------------------------------------------------
# Field 104: Sub-Riemannian Geometry
# ---------------------------------------------------------------------------
_SUB_RIEMANNIAN_GEOMETRY = _field(
    name="Sub-Riemannian Geometry",
    description=(
        "Sub-Riemannian geometry studies manifolds with constraints on allowable directions. "
        "It generalizes Riemannian geometry by restricting velocities to a sub-bundle of the tangent bundle. "
        "The field has applications to control theory, hypoelliptic operators, and geometric measure theory."
    ),
    props=[
        _prop("Chow-Rashevsky Theorem",
              "A sub-Riemannian manifold is connected if and only if the distribution satisfies the bracket-generating condition.",
              PropositionKind.THEOREM, "sub_riemannian_geometry", importance=0.92,
              tags=("connectivity", "bracket")),
        _prop("Hormander's Theorem",
              "A sum of squares of vector fields satisfying the bracket condition generates a hypoelliptic operator.",
              PropositionKind.THEOREM, "sub_riemannian_geometry", importance=0.94,
              tags=("hypoelliptic", "Hormander")),
        _prop("Heisenberg Group",
              "The Heisenberg group is the prototypical example of a sub-Riemannian manifold with step-2 nilpotent structure.",
              PropositionKind.DEFINITION, "sub_riemannian_geometry", importance=0.87,
              tags=("Heisenberg", "nilpotent")),
        _prop("Gromov's Tangent Cone Theorem",
              "Sub-Riemannian manifolds admit metric tangent cones that are nilpotent Lie groups.",
              PropositionKind.THEOREM, "sub_riemannian_geometry", importance=0.89,
              tags=("tangent", "cone")),
    ],
    keywords=("horizontal distributions", "bracket-generating", "Carnot groups", "hypoelliptic", "Hormander condition", "sub-Laplacian", "optimal control", "nilpotent"),
    judgment_site=(
        "Sub-Riemannian geometry models systems with kinematic constraints on motion. "
        "It reveals how bracket relations determine connectivity and smoothness properties."
    ),
)

# ---------------------------------------------------------------------------
# Field 105: Geometric Measure Theory
# ---------------------------------------------------------------------------
_GEOMETRIC_MEASURE_THEORY = _field(
    name="Geometric Measure Theory",
    description=(
        "Geometric measure theory extends measure theory to study geometric properties of sets. "
        "It investigates rectifiability, currents, and variational problems with irregular boundaries. "
        "The field provides tools for analyzing minimal surfaces and singularities in variational problems."
    ),
    props=[
        _prop("Federer-Fleming Theorem",
              "The space of integral currents provides a compactness framework for solving Plateau's problem.",
              PropositionKind.THEOREM, "geometric_measure_theory", importance=0.93,
              tags=("currents", "Plateau")),
        _prop("Rectifiable Set",
              "A set is countably rectifiable if it is covered up to measure zero by Lipschitz images of Euclidean space.",
              PropositionKind.DEFINITION, "geometric_measure_theory", importance=0.87,
              tags=("rectifiable", "measure")),
        _prop("Allard's Regularity Theorem",
              "Varifolds with small first variation and bounded mean curvature are smooth away from a small singular set.",
              PropositionKind.THEOREM, "geometric_measure_theory", importance=0.90,
              tags=("regularity", "varifolds")),
        _prop("Hausdorff Measure",
              "The d-dimensional Hausdorff measure generalizes volume to arbitrary dimension and measures the size of fractal sets.",
              PropositionKind.DEFINITION, "geometric_measure_theory", importance=0.89,
              tags=("Hausdorff", "dimension")),
    ],
    keywords=("rectifiability", "currents", "varifolds", "Hausdorff measure", "minimal surfaces", "Plateau problem", "singular sets", "Federer-Fleming"),
    judgment_site=(
        "Geometric measure theory provides rigorous foundations for studying irregular geometric objects. "
        "It extends classical geometric analysis to sets and surfaces with singularities."
    ),
)

# ---------------------------------------------------------------------------
# Field 106: Random Matrix Theory
# ---------------------------------------------------------------------------
_RANDOM_MATRIX_THEORY = _field(
    name="Random Matrix Theory",
    description=(
        "Random matrix theory studies spectral properties of matrices with random entries. "
        "It reveals universal behavior in eigenvalue distributions across diverse random matrix ensembles. "
        "The field has applications to quantum physics, number theory, and high-dimensional statistics."
    ),
    props=[
        _prop("Wigner's Semicircle Law",
              "The empirical eigenvalue distribution of large Wigner matrices converges to the semicircle distribution.",
              PropositionKind.THEOREM, "random_matrix_theory", importance=0.94,
              tags=("semicircle", "Wigner")),
        _prop("Tracy-Widom Distribution",
              "The largest eigenvalue of Gaussian unitary ensemble matrices, appropriately rescaled, converges to the Tracy-Widom distribution.",
              PropositionKind.THEOREM, "random_matrix_theory", importance=0.92,
              tags=("Tracy-Widom", "extreme")),
        _prop("Marchenko-Pastur Law",
              "The singular value distribution of large rectangular random matrices with independent entries converges to the Marchenko-Pastur distribution.",
              PropositionKind.THEOREM, "random_matrix_theory", importance=0.90,
              tags=("singular", "Marchenko-Pastur")),
        _prop("Gaussian Orthogonal Ensemble",
              "The GOE consists of real symmetric matrices with independent Gaussian entries on and above the diagonal.",
              PropositionKind.DEFINITION, "random_matrix_theory", importance=0.87,
              tags=("GOE", "ensemble")),
    ],
    keywords=("eigenvalue distribution", "Wigner matrices", "universality", "Tracy-Widom", "GUE", "GOE", "semicircle law", "spectral statistics"),
    judgment_site=(
        "Random matrix theory reveals universal patterns in high-dimensional random systems. "
        "It demonstrates that eigenvalue statistics transcend particular matrix models."
    ),
)

# ---------------------------------------------------------------------------
# Field 107: Percolation Theory
# ---------------------------------------------------------------------------
_PERCOLATION_THEORY = _field(
    name="Percolation Theory",
    description=(
        "Percolation theory studies connectivity in random graphs and lattices. "
        "It investigates phase transitions between connected and disconnected regimes. "
        "The field models flow through porous media, epidemic spreading, and network robustness."
    ),
    props=[
        _prop("Critical Probability Existence",
              "For any infinite connected graph, there exists a critical probability at which an infinite cluster emerges almost surely.",
              PropositionKind.THEOREM, "percolation_theory", importance=0.91,
              tags=("critical", "phase")),
        _prop("Harris Inequality",
              "For increasing events in bond percolation, the probability of their intersection is at least the product of their probabilities.",
              PropositionKind.THEOREM, "percolation_theory", importance=0.88,
              tags=("FKG", "correlation")),
        _prop("Kesten's Theorem",
              "The critical probability for bond percolation on the square lattice is exactly one-half.",
              PropositionKind.THEOREM, "percolation_theory", importance=0.93,
              tags=("Kesten", "square-lattice")),
        _prop("Percolation Cluster",
              "A percolation cluster is a maximal connected component of occupied sites or bonds in a random graph.",
              PropositionKind.DEFINITION, "percolation_theory", importance=0.85,
              tags=("cluster", "connectivity")),
    ],
    keywords=("phase transition", "critical probability", "infinite cluster", "bond percolation", "site percolation", "lattice models", "connectivity", "subcritical"),
    judgment_site=(
        "Percolation theory reveals threshold phenomena in random connectivity. "
        "It demonstrates sharp transitions between local and global connectivity regimes."
    ),
)

# ---------------------------------------------------------------------------
# Field 108: Stochastic PDE
# ---------------------------------------------------------------------------
_STOCHASTIC_PDE = _field(
    name="Stochastic PDE",
    description=(
        "Stochastic partial differential equations incorporate random forcing into evolution equations. "
        "They model physical systems with inherent noise and fluctuations. "
        "The field studies well-posedness, regularity, and long-time behavior of stochastic dynamics."
    ),
    props=[
        _prop("Kardar-Parisi-Zhang Universality",
              "The KPZ equation describes a universality class of growth processes with one-sided fluctuations and local interactions.",
              PropositionKind.THEOREM, "stochastic_pde", importance=0.94,
              tags=("KPZ", "universality")),
        _prop("Da Prato-Zabczyk Regularity",
              "Solutions to stochastic evolution equations with multiplicative noise have improved spatial regularity compared to the noise.",
              PropositionKind.THEOREM, "stochastic_pde", importance=0.89,
              tags=("regularity", "noise")),
        _prop("Stochastic Heat Equation",
              "The stochastic heat equation is a linear SPDE with additive or multiplicative space-time white noise.",
              PropositionKind.DEFINITION, "stochastic_pde", importance=0.87,
              tags=("heat", "white-noise")),
        _prop("Hairer's Regularity Structures",
              "Regularity structures provide a solution theory for singular SPDEs by constructing local polynomial models for solutions.",
              PropositionKind.THEOREM, "stochastic_pde", importance=0.96,
              tags=("regularity", "singular")),
    ],
    keywords=("white noise", "KPZ equation", "regularity structures", "stochastic heat equation", "multiplicative noise", "Ito calculus", "Wiener process", "singular SPDEs"),
    judgment_site=(
        "Stochastic PDE theory extends deterministic dynamics to noisy environments. "
        "It reveals how randomness interacts with nonlinearity to produce universal scaling behavior."
    ),
)
# ---------------------------------------------------------------------------
# Field 109: Rough Path Theory
# ---------------------------------------------------------------------------
_ROUGH_PATH_THEORY = _field(
    name="Rough Path Theory",
    description=(
        "Rough path theory provides a framework for defining integrals and solving "
        "differential equations driven by irregular paths, extending classical "
        "Ito calculus to non-semimartingale settings. Developed by Terry Lyons, "
        "it enables pathwise analysis of stochastic differential equations."
    ),
    props=[
        _prop("Lyons Universal Limit Theorem",
              "The solution map to a differential equation driven by a rough path is continuous in the rough path topology.",
              PropositionKind.THEOREM, "rough_path_theory", importance=0.96,
              tags=("continuity", "rough-paths")),
        _prop("Rough Path Lift",
              "A continuous path of finite p-variation with p < 2 admits a canonical lift to the space of rough paths.",
              PropositionKind.THEOREM, "rough_path_theory", importance=0.92,
              tags=("lift", "p-variation")),
        _prop("Young Integral",
              "If f has finite p-variation and g has finite q-variation with 1/p + 1/q > 1, the integral of f with respect to g is well-defined.",
              PropositionKind.THEOREM, "rough_path_theory", importance=0.88,
              tags=("integration", "young")),
        _prop("Rough Differential Equation",
              "A differential equation dy = f(y)dx where x is a rough path and f is sufficiently smooth.",
              PropositionKind.DEFINITION, "rough_path_theory", importance=0.90,
              tags=("rde", "definition")),
    ],
    keywords=("rough paths", "p-variation", "controlled paths", "stochastic analysis", "Lyons theory", "signatures", "Young integral", "RDE"),
    judgment_site=(
        "Rough path theory exemplifies how abstract mathematical structures can "
        "rigorously capture intuitions about continuous but highly irregular phenomena."
    ),
)

# ---------------------------------------------------------------------------
# Field 110: Malliavin Calculus
# ---------------------------------------------------------------------------
_MALLIAVIN_CALCULUS = _field(
    name="Malliavin Calculus",
    description=(
        "Malliavin calculus is a stochastic calculus of variations for functionals "
        "of Brownian motion and more general stochastic processes. It provides a "
        "probabilistic proof of Hormander's theorem on hypoelliptic operators and "
        "is fundamental in quantitative finance for computing Greeks."
    ),
    props=[
        _prop("Clark-Ocone Formula",
              "For a square-integrable random variable F in the domain of the Malliavin derivative, F equals its expectation plus the integral of the conditional expectation of its derivative.",
              PropositionKind.THEOREM, "malliavin_calculus", importance=0.93,
              tags=("representation", "martingale")),
        _prop("Integration by Parts Formula",
              "The Malliavin derivative operator and the Skorohod integral are adjoint operators on appropriate Sobolev spaces.",
              PropositionKind.THEOREM, "malliavin_calculus", importance=0.91,
              tags=("integration", "adjoint")),
        _prop("Malliavin Derivative",
              "The directional derivative of a functional of a stochastic process along a Cameron-Martin direction.",
              PropositionKind.DEFINITION, "malliavin_calculus", importance=0.89,
              tags=("derivative", "functional")),
        _prop("Malliavin Covariance Matrix",
              "For a random vector, the matrix of Malliavin derivatives characterizes the smoothness of its law.",
              PropositionKind.DEFINITION, "malliavin_calculus", importance=0.87,
              tags=("covariance", "smoothness")),
    ],
    keywords=("stochastic calculus", "Wiener chaos", "Skorohod integral", "Clark-Ocone", "Hormander theorem", "hypoellipticity", "Greeks", "finance"),
    judgment_site=(
        "Malliavin calculus demonstrates how functional analytic methods can unveil "
        "the fine structure of probability distributions arising from stochastic dynamics."
    ),
)

# ---------------------------------------------------------------------------
# Field 111: Extreme Value Theory
# ---------------------------------------------------------------------------
_EXTREME_VALUE_THEORY = _field(
    name="Extreme Value Theory",
    description=(
        "Extreme value theory studies the statistical behavior of extreme events "
        "and tail distributions. It characterizes the limiting distributions of "
        "maxima and minima of random samples, with applications in risk management, "
        "climate science, and engineering reliability."
    ),
    props=[
        _prop("Fisher-Tippett-Gnedenko Theorem",
              "The limiting distribution of properly normalized maxima of independent identically distributed random variables belongs to one of three types: Gumbel, Frechet, or Weibull.",
              PropositionKind.THEOREM, "extreme_value_theory", importance=0.95,
              tags=("limit-theorem", "maxima")),
        _prop("Pickands-Balkema-de Haan Theorem",
              "For exceedances over a high threshold, the conditional excess distribution converges to the generalized Pareto distribution.",
              PropositionKind.THEOREM, "extreme_value_theory", importance=0.92,
              tags=("threshold", "GPD")),
        _prop("Generalized Extreme Value Distribution",
              "A three-parameter family unifying the Gumbel, Frechet, and Weibull distributions via a shape parameter.",
              PropositionKind.DEFINITION, "extreme_value_theory", importance=0.90,
              tags=("GEV", "distribution")),
        _prop("Return Level",
              "The value exceeded on average once every T time periods, computed from the extreme value distribution.",
              PropositionKind.DEFINITION, "extreme_value_theory", importance=0.85,
              tags=("return-period", "risk")),
    ],
    keywords=("extreme values", "maxima", "tail distribution", "GEV", "GPD", "threshold exceedances", "risk analysis", "return periods"),
    judgment_site=(
        "Extreme value theory grounds our judgments about rare but impactful events "
        "in rigorous asymptotic theory rather than unfounded extrapolation."
    ),
)

# ---------------------------------------------------------------------------
# Field 112: High-Dimensional Statistics
# ---------------------------------------------------------------------------
_HIGH_DIMENSIONAL_STATISTICS = _field(
    name="High-Dimensional Statistics",
    description=(
        "High-dimensional statistics addresses statistical inference when the number "
        "of parameters is comparable to or larger than the sample size. This regime "
        "requires new theoretical frameworks and computational methods, with sparsity "
        "and regularization playing central roles."
    ),
    props=[
        _prop("Lasso Consistency",
              "Under appropriate conditions on the design matrix and sparsity, the Lasso estimator consistently recovers the support of the true parameter vector.",
              PropositionKind.THEOREM, "high_dimensional_statistics", importance=0.94,
              tags=("lasso", "sparsity")),
        _prop("Restricted Isometry Property",
              "A matrix satisfies RIP if all sparse vectors are approximately preserved in norm, enabling sparse recovery guarantees.",
              PropositionKind.DEFINITION, "high_dimensional_statistics", importance=0.91,
              tags=("RIP", "compressed-sensing")),
        _prop("Marchenko-Pastur Theorem",
              "The limiting spectral distribution of sample covariance matrices when dimensions grow proportionally with sample size.",
              PropositionKind.THEOREM, "high_dimensional_statistics", importance=0.89,
              tags=("random-matrices", "covariance")),
        _prop("Sure Independence Screening",
              "A fast variable screening method that provably retains all relevant variables with high probability in ultra-high dimensions.",
              PropositionKind.THEOREM, "high_dimensional_statistics", importance=0.86,
              tags=("screening", "variable-selection")),
    ],
    keywords=("high dimensions", "sparsity", "lasso", "RIP", "random matrices", "variable selection", "regularization", "asymptotics"),
    judgment_site=(
        "High-dimensional statistics reveals that classical intuitions break down when "
        "dimensions are large, requiring fundamentally new principles for valid inference."
    ),
)

# ---------------------------------------------------------------------------
# Field 113: Concentration Inequalities
# ---------------------------------------------------------------------------
_CONCENTRATION_INEQUALITIES = _field(
    name="Concentration Inequalities",
    description=(
        "Concentration inequalities provide quantitative bounds on the deviation of "
        "random variables from their expectations. These powerful tools are essential "
        "in probability theory, statistics, learning theory, and randomized algorithms."
    ),
    props=[
        _prop("Hoeffding Inequality",
              "For independent bounded random variables, the probability that their sum deviates from its expectation by more than t decays exponentially in t squared.",
              PropositionKind.THEOREM, "concentration_inequalities", importance=0.95,
              tags=("bounded", "exponential")),
        _prop("Bernstein Inequality",
              "A concentration inequality that incorporates variance information, providing tighter bounds than Hoeffding for low-variance variables.",
              PropositionKind.THEOREM, "concentration_inequalities", importance=0.92,
              tags=("variance", "sub-exponential")),
        _prop("Talagrand Convex Distance Inequality",
              "A powerful concentration result for product measures involving the convex distance functional, applicable to empirical processes.",
              PropositionKind.THEOREM, "concentration_inequalities", importance=0.90,
              tags=("Talagrand", "convex-distance")),
        _prop("Sub-Gaussian Random Variable",
              "A random variable whose moment generating function is dominated by that of a Gaussian, exhibiting Gaussian-like tail behavior.",
              PropositionKind.DEFINITION, "concentration_inequalities", importance=0.88,
              tags=("sub-gaussian", "tails")),
    ],
    keywords=("concentration", "tail bounds", "Hoeffding", "Bernstein", "sub-gaussian", "Talagrand", "martingale inequalities", "measure concentration"),
    judgment_site=(
        "Concentration inequalities formalize the fundamental intuition that averages "
        "of many independent quantities are typically close to their expected values."
    ),
)

# ---------------------------------------------------------------------------
# Field 114: Integrable Systems
# ---------------------------------------------------------------------------
_INTEGRABLE_SYSTEMS = _field(
    name="Integrable Systems",
    description=(
        "Integrable systems are dynamical systems with sufficiently many conserved "
        "quantities to permit explicit solution. They exhibit remarkable mathematical "
        "structure involving Lax pairs, inverse scattering, and algebraic geometry, "
        "bridging analysis, geometry, and mathematical physics."
    ),
    props=[
        _prop("Liouville-Arnold Theorem",
              "A Hamiltonian system with n degrees of freedom and n independent Poisson-commuting integrals in involution is integrable by quadratures, and its motion occurs on invariant tori.",
              PropositionKind.THEOREM, "integrable_systems", importance=0.96,
              tags=("Hamiltonian", "integrability")),
        _prop("Lax Pair Representation",
              "A nonlinear evolution equation can be written as the compatibility condition of two linear operators, enabling solution via inverse scattering.",
              PropositionKind.THEOREM, "integrable_systems", importance=0.94,
              tags=("Lax-pair", "inverse-scattering")),
        _prop("Toda Lattice Integrability",
              "The Toda lattice possesses sufficiently many conserved quantities and is completely integrable via the inverse scattering method.",
              PropositionKind.THEOREM, "integrable_systems", importance=0.88,
              tags=("Toda", "lattice")),
        _prop("Action-Angle Variables",
              "Canonical coordinates for integrable systems where the actions are conserved and the angles evolve linearly in time.",
              PropositionKind.DEFINITION, "integrable_systems", importance=0.90,
              tags=("action-angle", "canonical")),
    ],
    keywords=("integrability", "Lax pairs", "inverse scattering", "Hamiltonian", "conserved quantities", "action-angle", "KdV", "solitons"),
    judgment_site=(
        "Integrable systems exemplify how hidden symmetries and algebraic structure "
        "can render apparently complex dynamics completely transparent."
    ),
)

# ---------------------------------------------------------------------------
# Field 115: Soliton Theory
# ---------------------------------------------------------------------------
_SOLITON_THEORY = _field(
    name="Soliton Theory",
    description=(
        "Soliton theory studies stable, localized wave solutions of nonlinear partial "
        "differential equations that maintain their shape during propagation and interaction. "
        "Discovered in the study of water waves, solitons appear throughout physics and "
        "are intimately connected to integrable systems."
    ),
    props=[
        _prop("KdV Soliton Solution",
              "The Korteweg-de Vries equation admits exact soliton solutions of the form sech-squared, representing stable traveling waves.",
              PropositionKind.THEOREM, "soliton_theory", importance=0.94,
              tags=("KdV", "traveling-wave")),
        _prop("Inverse Scattering Transform",
              "A method for solving certain nonlinear evolution equations by transforming them to linear scattering problems, analogous to the Fourier transform for linear equations.",
              PropositionKind.THEOREM, "soliton_theory", importance=0.96,
              tags=("IST", "nonlinear")),
        _prop("N-Soliton Formula",
              "The general solution describing the interaction of N solitons, exhibiting elastic scattering with phase shifts but no energy transfer.",
              PropositionKind.THEOREM, "soliton_theory", importance=0.90,
              tags=("multi-soliton", "interaction")),
        _prop("Breather Solution",
              "A localized oscillatory solution of nonlinear wave equations that is periodic in time but localized in space.",
              PropositionKind.DEFINITION, "soliton_theory", importance=0.87,
              tags=("breather", "oscillation")),
    ],
    keywords=("solitons", "KdV", "inverse scattering", "nonlinear waves", "sine-Gordon", "breathers", "kinks", "integrability"),
    judgment_site=(
        "Soliton theory reveals how nonlinearity can stabilize rather than destabilize "
        "wave phenomena, challenging linear intuitions about dispersive systems."
    ),
)

# ---------------------------------------------------------------------------
# Field 116: Mathematical Fluid Mechanics
# ---------------------------------------------------------------------------
_MATHEMATICAL_FLUID_MECHANICS = _field(
    name="Mathematical Fluid Mechanics",
    description=(
        "Mathematical fluid mechanics rigorously studies the Navier-Stokes and Euler "
        "equations governing fluid flow. Central questions include existence, uniqueness, "
        "and regularity of solutions, as well as the mathematical description of "
        "turbulence, vortex dynamics, and boundary layers."
    ),
    props=[
        _prop("Leray Weak Solutions",
              "Weak solutions to the three-dimensional Navier-Stokes equations exist globally in time and satisfy an energy inequality.",
              PropositionKind.THEOREM, "mathematical_fluid_mechanics", importance=0.96,
              tags=("Navier-Stokes", "weak-solutions")),
        _prop("Beale-Kato-Majda Criterion",
              "Smooth solutions to the Euler equations remain smooth as long as the vorticity remains bounded in the supremum norm.",
              PropositionKind.THEOREM, "mathematical_fluid_mechanics", importance=0.93,
              tags=("Euler", "blowup-criterion")),
        _prop("Kelvin Circulation Theorem",
              "In an inviscid, barotropic fluid with conservative body forces, the circulation around a closed curve moving with the fluid is constant in time.",
              PropositionKind.THEOREM, "mathematical_fluid_mechanics", importance=0.89,
              tags=("circulation", "inviscid")),
        _prop("Vorticity Form",
              "The reformulation of fluid equations in terms of the curl of velocity, emphasizing rotational dynamics.",
              PropositionKind.DEFINITION, "mathematical_fluid_mechanics", importance=0.87,
              tags=("vorticity", "curl")),
    ],
    keywords=("Navier-Stokes", "Euler equations", "vorticity", "turbulence", "weak solutions", "regularity", "boundary layers", "fluid dynamics"),
    judgment_site=(
        "Mathematical fluid mechanics confronts us with profound open questions about "
        "the predictability and regularity of continuous media described by classical physics."
    ),
)

# ---------------------------------------------------------------------------
# Field 117: Kinetic Theory
# ---------------------------------------------------------------------------
_KINETIC_THEORY = _field(
    name="Kinetic Theory",
    description=(
        "Kinetic theory describes the statistical behavior of large systems of particles "
        "through distribution functions evolving under the Boltzmann or related kinetic "
        "equations. It bridges microscopic particle dynamics and macroscopic continuum "
        "mechanics, providing rigorous derivations of fluid equations."
    ),
    props=[
        _prop("Boltzmann H-Theorem",
              "The H-functional, a measure of entropy, is non-increasing along solutions of the Boltzmann equation, establishing irreversibility at the kinetic level.",
              PropositionKind.THEOREM, "kinetic_theory", importance=0.95,
              tags=("H-theorem", "entropy")),
        _prop("Maxwellian Distribution",
              "The unique equilibrium distribution for the Boltzmann equation is the Maxwellian, corresponding to thermal equilibrium.",
              PropositionKind.THEOREM, "kinetic_theory", importance=0.92,
              tags=("equilibrium", "Maxwellian")),
        _prop("DiPerna-Lions Theory",
              "Global weak solutions to the Boltzmann equation exist under physically reasonable assumptions using renormalized solutions.",
              PropositionKind.THEOREM, "kinetic_theory", importance=0.93,
              tags=("DiPerna-Lions", "existence")),
        _prop("Collision Operator",
              "The bilinear operator in the Boltzmann equation modeling binary collisions between particles.",
              PropositionKind.DEFINITION, "kinetic_theory", importance=0.88,
              tags=("collision", "operator")),
    ],
    keywords=("Boltzmann equation", "kinetic equations", "collision operator", "H-theorem", "Maxwellian", "DiPerna-Lions", "Vlasov", "rarefied gas"),
    judgment_site=(
        "Kinetic theory demonstrates how irreversibility and thermodynamic behavior emerge "
        "from time-reversible microscopic dynamics through statistical mechanisms."
    ),
)

# ---------------------------------------------------------------------------
# Field 118: Celestial Mechanics
# ---------------------------------------------------------------------------
_CELESTIAL_MECHANICS = _field(
    name="Celestial Mechanics",
    description=(
        "Celestial mechanics is the mathematical study of orbital motion under gravitational "
        "forces, traditionally focused on planetary motion but encompassing satellite dynamics, "
        "asteroid trajectories, and galactic structure. It combines classical mechanics, "
        "perturbation theory, and modern dynamical systems methods."
    ),
    props=[
        _prop("Kepler Laws",
              "Planetary orbits are ellipses with the sun at a focus; areas swept in equal times are equal; the square of orbital period is proportional to the cube of semi-major axis.",
              PropositionKind.THEOREM, "celestial_mechanics", importance=0.96,
              tags=("Kepler", "orbits")),
        _prop("KAM Theorem",
              "Under small perturbations of integrable Hamiltonian systems, most invariant tori persist, ensuring long-term stability of quasi-periodic motion.",
              PropositionKind.THEOREM, "celestial_mechanics", importance=0.97,
              tags=("KAM", "stability")),
        _prop("Lagrange Points",
              "Five equilibrium solutions in the circular restricted three-body problem where gravitational and centrifugal forces balance.",
              PropositionKind.THEOREM, "celestial_mechanics", importance=0.90,
              tags=("three-body", "equilibrium")),
        _prop("Delaunay Variables",
              "Action-angle variables for the Kepler problem adapted to perturbation theory in celestial mechanics.",
              PropositionKind.DEFINITION, "celestial_mechanics", importance=0.87,
              tags=("action-angle", "perturbation")),
    ],
    keywords=("orbital mechanics", "Kepler problem", "three-body problem", "KAM theory", "perturbation theory", "Lagrange points", "planetary motion", "gravity"),
    judgment_site=(
        "Celestial mechanics exemplifies how long-term predictability and stability questions "
        "in deterministic dynamics lead to deep mathematical theories of nearly integrable systems."
    ),
)

# ---------------------------------------------------------------------------
# Field 119: Random Geometry
# ---------------------------------------------------------------------------
_RANDOM_GEOMETRY = _field(
    name="Random Geometry",
    description=(
        "Random geometry studies geometric structures with intrinsic randomness, including "
        "random metrics, random surfaces, and random graphs embedded in space. Key examples "
        "include Liouville quantum gravity, random planar maps, and first-passage percolation, "
        "connecting probability theory to geometry and physics."
    ),
    props=[
        _prop("Brownian Map Universality",
              "The scaling limit of uniformly random planar maps under appropriate topology is the Brownian map, a universal random metric space.",
              PropositionKind.THEOREM, "random_geometry", importance=0.94,
              tags=("Brownian-map", "universality")),
        _prop("Liouville Quantum Gravity",
              "A random measure on a surface defined as the exponential of a log-correlated Gaussian field, arising as the continuum limit of random planar maps.",
              PropositionKind.DEFINITION, "random_geometry", importance=0.92,
              tags=("LQG", "random-measure")),
        _prop("KPZ Formula",
              "A formula relating the dimension of sets in the Euclidean plane to their dimension in Liouville quantum gravity, connecting random geometry to conformal field theory.",
              PropositionKind.THEOREM, "random_geometry", importance=0.93,
              tags=("KPZ", "scaling")),
        _prop("First-Passage Percolation",
              "The model where each edge of a lattice is assigned an independent random passage time, and one studies the induced random metric.",
              PropositionKind.DEFINITION, "random_geometry", importance=0.88,
              tags=("percolation", "metric")),
    ],
    keywords=("random geometry", "Liouville quantum gravity", "random planar maps", "Brownian map", "KPZ", "random metrics", "percolation", "universality"),
    judgment_site=(
        "Random geometry reveals universal structures emerging from geometric randomness, "
        "suggesting deep connections between probability, analysis, and quantum field theory."
    ),
)

# ---------------------------------------------------------------------------
# Field 120: Conformal Field Theory
# ---------------------------------------------------------------------------
_CONFORMAL_FIELD_THEORY = _field(
    name="Conformal Field Theory",
    description=(
        "Conformal field theory studies quantum field theories invariant under conformal "
        "transformations, exhibiting enhanced symmetry that allows exact solutions in two "
        "dimensions. CFT provides critical exponents for statistical mechanics models, "
        "underlies string theory, and connects to representation theory and geometry."
    ),
    props=[
        _prop("Virasoro Algebra Representations",
              "Unitary highest-weight representations of the Virasoro algebra are classified by central charge and conformal dimension, determining the structure of 2D CFTs.",
              PropositionKind.THEOREM, "conformal_field_theory", importance=0.95,
              tags=("Virasoro", "representations")),
        _prop("Operator Product Expansion",
              "The product of two local operators at nearby points can be expanded as a sum of local operators with coefficient functions determined by conformal symmetry.",
              PropositionKind.THEOREM, "conformal_field_theory", importance=0.93,
              tags=("OPE", "locality")),
        _prop("Cardy Formula",
              "An exact formula for the asymptotic growth of the number of states in a CFT, relating modular properties to thermodynamics.",
              PropositionKind.THEOREM, "conformal_field_theory", importance=0.91,
              tags=("Cardy", "modular")),
        _prop("Primary Field",
              "A field that transforms covariantly under conformal transformations and cannot be expressed as a derivative of other fields.",
              PropositionKind.DEFINITION, "conformal_field_theory", importance=0.89,
              tags=("primary", "conformal")),
    ],
    keywords=("conformal symmetry", "Virasoro algebra", "minimal models", "operator product expansion", "central charge", "modular invariance", "CFT", "two dimensions"),
    judgment_site=(
        "Conformal field theory demonstrates how symmetry principles alone can determine "
        "the complete structure of physical theories in special dimensions."
    ),
)

# ---------------------------------------------------------------------------
# Field 121: Topological Quantum Computation
# ---------------------------------------------------------------------------
_TOPOLOGICAL_QUANTUM_COMPUTATION = _field(
    name="Topological Quantum Computation",
    description=(
        "Topological quantum computation encodes quantum information in topological degrees "
        "of freedom, such as anyonic excitations in two-dimensional systems, making it "
        "inherently fault-tolerant against local errors. It connects quantum information "
        "theory, condensed matter physics, and knot theory."
    ),
    props=[
        _prop("Topological Protection",
              "Quantum information encoded in the fusion and braiding of anyons is protected from local perturbations by an energy gap and topological quantum numbers.",
              PropositionKind.THEOREM, "topological_quantum_computation", importance=0.95,
              tags=("protection", "anyons")),
        _prop("Fibonacci Anyon Universal Computation",
              "Braiding operations on Fibonacci anyons are universal for quantum computation, i.e., can approximate any unitary gate to arbitrary precision.",
              PropositionKind.THEOREM, "topological_quantum_computation", importance=0.93,
              tags=("Fibonacci", "universality")),
        _prop("Jones Polynomial from Anyons",
              "The Jones polynomial of a knot can be computed from the expectation value of Wilson loops in Chern-Simons theory, relating knot invariants to topological quantum field theory.",
              PropositionKind.THEOREM, "topological_quantum_computation", importance=0.90,
              tags=("Jones", "knots")),
        _prop("Modular Tensor Category",
              "The mathematical framework describing anyon types, their fusion rules, and braiding statistics in topological phases of matter.",
              PropositionKind.DEFINITION, "topological_quantum_computation", importance=0.91,
              tags=("MTC", "category")),
    ],
    keywords=("topological quantum computing", "anyons", "braiding", "fault tolerance", "Fibonacci anyons", "modular tensor category", "Chern-Simons", "TQFT"),
    judgment_site=(
        "Topological quantum computation illustrates how global topological properties can "
        "protect fragile quantum information from the ravages of decoherence."
    ),
)

# ---------------------------------------------------------------------------
# Field 122: Topological Data Analysis
# ---------------------------------------------------------------------------
_TOPOLOGICAL_DATA_ANALYSIS = _field(
    name="Topological Data Analysis",
    description=(
        "Topological data analysis applies algebraic topology to extract robust geometric "
        "and topological features from data. Through persistent homology and related methods, "
        "TDA identifies multi-scale structure insensitive to noise and parameterization, "
        "with applications across science and engineering."
    ),
    props=[
        _prop("Stability of Persistence Diagrams",
              "The bottleneck distance between persistence diagrams is bounded by the Gromov-Hausdorff distance between the underlying metric spaces, ensuring robustness to perturbations.",
              PropositionKind.THEOREM, "topological_data_analysis", importance=0.94,
              tags=("stability", "persistence")),
        _prop("Nerve Theorem",
              "The nerve of a good cover of a topological space is homotopy equivalent to the space, justifying the computation of homology from covers.",
              PropositionKind.THEOREM, "topological_data_analysis", importance=0.91,
              tags=("nerve", "cover")),
        _prop("Mapper Algorithm Convergence",
              "Under appropriate sampling conditions, the Mapper construction recovers topological features of the underlying space.",
              PropositionKind.THEOREM, "topological_data_analysis", importance=0.87,
              tags=("mapper", "sampling")),
        _prop("Persistence Module",
              "A functor from a poset to the category of vector spaces, encoding multi-scale topological information of filtered spaces.",
              PropositionKind.DEFINITION, "topological_data_analysis", importance=0.90,
              tags=("module", "functor")),
    ],
    keywords=("topological data analysis", "persistent homology", "persistence diagrams", "TDA", "Mapper", "nerve theorem", "barcodes", "stability"),
    judgment_site=(
        "Topological data analysis provides rigorous foundations for extracting shape "
        "information from data, complementing statistical and geometric perspectives."
    ),
)

# ---------------------------------------------------------------------------
# Field 123: Persistent Homology
# ---------------------------------------------------------------------------
_PERSISTENT_HOMOLOGY = _field(
    name="Persistent Homology",
    description=(
        "Persistent homology is a method from algebraic topology that computes topological "
        "features across multiple scales by tracking homology groups through a filtration. "
        "It provides barcodes and persistence diagrams that summarize the birth and death "
        "of topological features, forming the core of topological data analysis."
    ),
    props=[
        _prop("Structure Theorem for Persistence Modules",
              "A persistence module of finite type decomposes uniquely as a direct sum of interval modules, corresponding to bars in the barcode.",
              PropositionKind.THEOREM, "persistent_homology", importance=0.96,
              tags=("decomposition", "intervals")),
        _prop("Bottleneck Stability",
              "The bottleneck distance between persistence diagrams of two filtrations is bounded by the supremum distance between the filtration values.",
              PropositionKind.THEOREM, "persistent_homology", importance=0.94,
              tags=("bottleneck", "stability")),
        _prop("Persistent Homology Algorithm",
              "The persistence pairing can be computed in matrix multiplication time by reducing the boundary matrix to canonical form.",
              PropositionKind.THEOREM, "persistent_homology", importance=0.89,
              tags=("algorithm", "computation")),
        _prop("Barcode",
              "A multiset of intervals representing the lifespans of homological features in a filtration.",
              PropositionKind.DEFINITION, "persistent_homology", importance=0.92,
              tags=("barcode", "representation")),
    ],
    keywords=("persistent homology", "barcodes", "persistence diagrams", "filtrations", "bottleneck distance", "Vietoris-Rips", "Cech complex", "homology"),
    judgment_site=(
        "Persistent homology formalizes the notion that significant topological features "
        "persist across scales while noise appears and disappears quickly."
    ),
)

# ---------------------------------------------------------------------------
# Field 124: Applied Category Theory
# ---------------------------------------------------------------------------
_APPLIED_CATEGORY_THEORY = _field(
    name="Applied Category Theory",
    description=(
        "Applied category theory uses categorical structures to model systems in science, "
        "engineering, and computation. By emphasizing compositionality and abstraction, "
        "it provides unified frameworks for diverse phenomena including databases, dynamical "
        "systems, networks, and causal reasoning."
    ),
    props=[
        _prop("Grothendieck Construction",
              "A construction transforming indexed categories into fibrations, fundamental for modeling dependent types and databases.",
              PropositionKind.THEOREM, "applied_category_theory", importance=0.92,
              tags=("Grothendieck", "fibrations")),
        _prop("Monoidal Categories and Resource Theories",
              "Symmetric monoidal categories provide the natural setting for compositional resource theories in physics and computation.",
              PropositionKind.THEOREM, "applied_category_theory", importance=0.90,
              tags=("monoidal", "resources")),
        _prop("Operads for Compositionality",
              "Operads formalize operations with multiple inputs and one output, capturing compositionality in networks and algebraic structures.",
              PropositionKind.THEOREM, "applied_category_theory", importance=0.88,
              tags=("operads", "composition")),
        _prop("String Diagram",
              "A graphical calculus for morphisms in monoidal categories, making compositional reasoning visual and intuitive.",
              PropositionKind.DEFINITION, "applied_category_theory", importance=0.89,
              tags=("string-diagrams", "graphical")),
    ],
    keywords=("category theory", "compositionality", "monoidal categories", "string diagrams", "functors", "operads", "applied mathematics", "abstraction"),
    judgment_site=(
        "Applied category theory reveals how abstract mathematical structures can unify "
        "and clarify reasoning across seemingly disparate domains through compositional principles."
    ),
)

# ---------------------------------------------------------------------------
# Field 125: Geometric Deep Learning
# ---------------------------------------------------------------------------
_GEOMETRIC_DEEP_LEARNING = _field(
    name="Geometric Deep Learning",
    description=(
        "Geometric deep learning extends neural networks to non-Euclidean domains such as "
        "graphs, manifolds, and groups by incorporating geometric structure and symmetries. "
        "It provides a unified framework encompassing graph neural networks, convolutional "
        "networks on manifolds, and equivariant architectures."
    ),
    props=[
        _prop("Approximation by Graph Neural Networks",
              "Graph neural networks satisfying certain criteria can approximate any continuous permutation-equivariant function on graphs.",
              PropositionKind.THEOREM, "geometric_deep_learning", importance=0.92,
              tags=("GNN", "approximation")),
        _prop("Weisfeiler-Leman Equivalence",
              "The expressive power of message-passing graph neural networks is bounded by the Weisfeiler-Leman graph isomorphism test.",
              PropositionKind.THEOREM, "geometric_deep_learning", importance=0.94,
              tags=("WL", "expressiveness")),
        _prop("Gauge Equivariance",
              "Neural networks on geometric domains can be designed to be equivariant under gauge transformations, preserving physical invariances.",
              PropositionKind.THEOREM, "geometric_deep_learning", importance=0.89,
              tags=("gauge", "equivariance")),
        _prop("Geometric Prior",
              "Incorporating symmetries, invariances, and geometric structure as inductive biases in neural network architectures.",
              PropositionKind.DEFINITION, "geometric_deep_learning", importance=0.88,
              tags=("prior", "symmetry")),
    ],
    keywords=("geometric deep learning", "graph neural networks", "equivariance", "manifolds", "symmetry", "Weisfeiler-Leman", "GNN", "convolution"),
    judgment_site=(
        "Geometric deep learning demonstrates how encoding geometric structure and symmetries "
        "as architectural priors can dramatically improve learning efficiency and generalization."
    ),
)

# ---------------------------------------------------------------------------
# Field 126: Quantum Information Theory
# ---------------------------------------------------------------------------
_QUANTUM_INFORMATION_THEORY = _field(
    name="Quantum Information Theory",
    description=(
        "Quantum information theory studies the storage, transmission, and processing of "
        "information in quantum systems. It encompasses quantum entanglement, quantum "
        "communication, quantum error correction, and quantum algorithms, revealing fundamental "
        "differences from classical information theory."
    ),
    props=[
        _prop("No-Cloning Theorem",
              "There exists no quantum operation that can create an identical copy of an arbitrary unknown quantum state.",
              PropositionKind.THEOREM, "quantum_information_theory", importance=0.96,
              tags=("no-cloning", "fundamental")),
        _prop("Holevo Bound",
              "The maximum accessible classical information from n qubits prepared in ensemble states is bounded by the von Neumann entropy of the ensemble density matrix.",
              PropositionKind.THEOREM, "quantum_information_theory", importance=0.94,
              tags=("Holevo", "capacity")),
        _prop("Quantum Error Correction Threshold",
              "Quantum computation can be performed fault-tolerantly provided the physical error rate per gate is below a threshold value.",
              PropositionKind.THEOREM, "quantum_information_theory", importance=0.95,
              tags=("error-correction", "threshold")),
        _prop("Entanglement Entropy",
              "The von Neumann entropy of the reduced density matrix of a subsystem, quantifying quantum correlations.",
              PropositionKind.DEFINITION, "quantum_information_theory", importance=0.92,
              tags=("entanglement", "entropy")),
    ],
    keywords=("quantum information", "entanglement", "quantum communication", "quantum error correction", "qubits", "von Neumann entropy", "Holevo bound", "no-cloning"),
    judgment_site=(
        "Quantum information theory reveals how quantum mechanical principles impose fundamental "
        "constraints and enable surprising capabilities in information processing."
    ),
)

# ---------------------------------------------------------------------------
# Field 127: Mathematical Neuroscience
# ---------------------------------------------------------------------------
_MATHEMATICAL_NEUROSCIENCE = _field(
    name="Mathematical Neuroscience",
    description=(
        "Mathematical neuroscience develops and analyzes mathematical models of neural "
        "systems at multiple scales, from single neurons to networks and brain regions. "
        "It combines dynamical systems, probability, information theory, and data analysis "
        "to understand neural computation, learning, and behavior."
    ),
    props=[
        _prop("Hodgkin-Huxley Model",
              "A system of nonlinear ordinary differential equations describing action potential generation through voltage-gated ion channels, accurately reproducing neural spiking.",
              PropositionKind.THEOREM, "mathematical_neuroscience", importance=0.96,
              tags=("Hodgkin-Huxley", "action-potential")),
        _prop("Wilson-Cowan Equations",
              "Mean-field equations for excitatory and inhibitory neural populations exhibiting oscillations, bistability, and traveling waves.",
              PropositionKind.THEOREM, "mathematical_neuroscience", importance=0.91,
              tags=("Wilson-Cowan", "population")),
        _prop("Spike-Timing-Dependent Plasticity",
              "The synaptic weight change depends on the precise timing between pre- and post-synaptic spikes, with causal pairing strengthening connections.",
              PropositionKind.THEOREM, "mathematical_neuroscience", importance=0.93,
              tags=("STDP", "plasticity")),
        _prop("Firing Rate",
              "The average number of action potentials per unit time, a coarse-grained description of neural activity.",
              PropositionKind.DEFINITION, "mathematical_neuroscience", importance=0.87,
              tags=("firing-rate", "coarse-graining")),
    ],
    keywords=("mathematical neuroscience", "neural modeling", "Hodgkin-Huxley", "spike trains", "synaptic plasticity", "neural networks", "dynamical systems", "STDP"),
    judgment_site=(
        "Mathematical neuroscience exemplifies how quantitative modeling can illuminate the "
        "computational principles underlying complex biological information processing systems."
    ),
)

# ---------------------------------------------------------------------------
# Field 128: Computational Algebraic Geometry
# ---------------------------------------------------------------------------
_COMPUTATIONAL_ALGEBRAIC_GEOMETRY = _field(
    name="Computational Algebraic Geometry",
    description=(
        "Computational algebraic geometry develops algorithms and software for solving "
        "problems in algebraic geometry, particularly involving polynomial equations and "
        "ideals. Central tools include Groebner bases, resultants, and numerical continuation "
        "methods, with applications throughout mathematics, science, and engineering."
    ),
    props=[
        _prop("Buchberger Algorithm",
              "An algorithm for computing Groebner bases of polynomial ideals, terminating for all inputs under appropriate term orderings.",
              PropositionKind.THEOREM, "computational_algebraic_geometry", importance=0.95,
              tags=("Buchberger", "Groebner")),
        _prop("Groebner Basis Ideal Membership",
              "A polynomial belongs to an ideal if and only if its remainder under division by a Groebner basis is zero.",
              PropositionKind.THEOREM, "computational_algebraic_geometry", importance=0.93,
              tags=("membership", "division")),
        _prop("Bezout Theorem for Intersection Numbers",
              "In projective space, the number of intersection points of generic hypersurfaces equals the product of their degrees.",
              PropositionKind.THEOREM, "computational_algebraic_geometry", importance=0.91,
              tags=("Bezout", "intersection")),
        _prop("Elimination Ideal",
              "The ideal consisting of all polynomials in a given ideal that involve only a specified subset of variables, computed via Groebner bases.",
              PropositionKind.DEFINITION, "computational_algebraic_geometry", importance=0.89,
              tags=("elimination", "projection")),
    ],
    keywords=("computational algebraic geometry", "Groebner bases", "polynomial systems", "ideals", "Buchberger", "elimination", "resultants", "symbolic computation"),
    judgment_site=(
        "Computational algebraic geometry transforms abstract algebraic questions into algorithmic "
        "problems, enabling explicit solutions and fostering interaction between theory and computation."
    ),
)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

ALL_128_FIELDS: list[FieldNode] = [
    _TYPE_THEORY, _CATEGORY_THEORY, _HoTT, _TOPOS_THEORY, _ALGEBRAIC_TOPOLOGY,
    _DIFF_GEOM, _ALGEBRAIC_GEOMETRY, _NUMBER_THEORY, _REPRESENTATION_THEORY,
    _FUNCTIONAL_ANALYSIS, _OPERATOR_ALGEBRAS, _PROBABILITY_THEORY,
    _STOCHASTIC_PROCESSES, _INFORMATION_THEORY, _STATISTICAL_MECHANICS,
    _QUANTUM_MECHANICS, _QFT, _STRING_THEORY, _GENERAL_RELATIVITY,
    _SYMPLECTIC_GEOMETRY, _POISSON_GEOMETRY, _LIE_THEORY, _COMBINATORICS,
    _GRAPH_THEORY, _MATROID_THEORY, _ORDER_THEORY, _LATTICE_THEORY,
    _UNIVERSAL_ALGEBRA, _MODEL_THEORY, _PROOF_THEORY, _RECURSION_THEORY,
    _COMPLEXITY_THEORY, _LAMBDA_CALCULUS, _LINEAR_LOGIC, _MODAL_LOGIC,
    _DEPENDENT_TYPE_THEORY, _HOMOLOGICAL_ALGEBRA, _K_THEORY, _COBORDISM_THEORY,
    _MOTIVIC_COHOMOLOGY, _DERIVED_CATEGORIES, _INFINITY_CATEGORIES,
    _HIGHER_GAUGE_THEORY, _NONCOMMUTATIVE_GEOMETRY, _TROPICAL_GEOMETRY,
    _ARITHMETIC_GEOMETRY, _ETALE_COHOMOLOGY, _PERVERSE_SHEAVES,
    _NUMERICAL_ANALYSIS,
    _APPROXIMATION_THEORY,
    _CONTROL_THEORY,
    _OPTIMIZATION_THEORY,
    _SIGNAL_PROCESSING_MATHEMATICAL,
    _DYNAMICAL_SYSTEMS,
    _MATHEMATICAL_FLUID_DYNAMICS,
    _ELASTICITY_THEORY,
    _MATHEMATICAL_BIOLOGY,
    _EPIDEMIOLOGICAL_MODELING,
    _OPERATIONS_RESEARCH,
    _GAME_THEORY,
    _MECHANISM_DESIGN,
    _COMPUTATIONAL_GEOMETRY,
    _FINITE_ELEMENT_METHODS,
    _ANALYTIC_NUMBER_THEORY,
    _ALGEBRAIC_NUMBER_THEORY,
    _DIOPHANTINE_GEOMETRY,
    _ADDITIVE_COMBINATORICS,
    _GALOIS_THEORY,
    _COMMUTATIVE_ALGEBRA,
    _RING_THEORY,
    _FIELD_THEORY_ALGEBRA,
    _QUADRATIC_FORMS,
    _MODULAR_FORMS,
    _HARMONIC_ANALYSIS,
    _PDE_THEORY,
    _DISTRIBUTION_THEORY,
    _MICROLOCAL_ANALYSIS,
    _SEVERAL_COMPLEX_VARIABLES,
    _POTENTIAL_THEORY,
    _ERGODIC_THEORY,
    _MEASURE_THEORY,
    _BANACH_SPACE_THEORY,
    _SPECTRAL_THEORY,
    _CALCULUS_OF_VARIATIONS,
    _NONLINEAR_ANALYSIS,
    _CODING_THEORY,
    _CRYPTOGRAPHY_MATHEMATICAL,
    _FORMAL_LANGUAGE_THEORY,
    _AUTOMATA_THEORY,
    _COMPUTABILITY_THEORY,
    _DESCRIPTIVE_COMPLEXITY,
    _CIRCUIT_COMPLEXITY,
    _ALGORITHMIC_GAME_THEORY,
    _DISTRIBUTED_COMPUTING_THEORY,
    _EXTREMAL_COMBINATORICS,
    _RAMSEY_THEORY,
    _DESIGN_THEORY,
    _RIEMANNIAN_GEOMETRY,
    _GEOMETRIC_ANALYSIS,
    _CONVEX_GEOMETRY,
    _DISCRETE_GEOMETRY,
    _GEOMETRIC_GROUP_THEORY,
    _METRIC_GEOMETRY,
    _SUB_RIEMANNIAN_GEOMETRY,
    _GEOMETRIC_MEASURE_THEORY,
    _RANDOM_MATRIX_THEORY,
    _PERCOLATION_THEORY,
    _STOCHASTIC_PDE,
    _ROUGH_PATH_THEORY,
    _MALLIAVIN_CALCULUS,
    _EXTREME_VALUE_THEORY,
    _HIGH_DIMENSIONAL_STATISTICS,
    _CONCENTRATION_INEQUALITIES,
    _INTEGRABLE_SYSTEMS,
    _SOLITON_THEORY,
    _MATHEMATICAL_FLUID_MECHANICS,
    _KINETIC_THEORY,
    _CELESTIAL_MECHANICS,
    _RANDOM_GEOMETRY,
    _CONFORMAL_FIELD_THEORY,
    _TOPOLOGICAL_QUANTUM_COMPUTATION,
    _TOPOLOGICAL_DATA_ANALYSIS,
    _PERSISTENT_HOMOLOGY,
    _APPLIED_CATEGORY_THEORY,
    _GEOMETRIC_DEEP_LEARNING,
    _QUANTUM_INFORMATION_THEORY,
    _MATHEMATICAL_NEUROSCIENCE,
    _COMPUTATIONAL_ALGEBRAIC_GEOMETRY,
]

assert len(ALL_128_FIELDS) == 128, f"Expected 128 fields, got {len(ALL_128_FIELDS)}"

FIELD_BY_ID: dict[str, FieldNode] = {f.field_id: f for f in ALL_128_FIELDS}
FIELD_BY_NAME: dict[str, FieldNode] = {f.name: f for f in ALL_128_FIELDS}


def get_fields_by_keywords(keywords: list[str]) -> list[FieldNode]:
    """Return fields whose keyword tuples overlap with the given keywords (case-insensitive)."""
    lower_kw = {k.lower() for k in keywords}
    results = []
    for f in ALL_128_FIELDS:
        field_kw = {k.lower() for k in f.keywords}
        if field_kw & lower_kw:
            results.append(f)
    return results


def get_fields_for_obstruction(obstruction_desc: str) -> list[FieldNode]:
    """Return fields relevant to an obstruction description via keyword/name matching."""
    lower_desc = obstruction_desc.lower()
    results = []
    for f in ALL_128_FIELDS:
        if f.name.lower() in lower_desc:
            results.append(f)
            continue
        for kw in f.keywords:
            if kw.lower() in lower_desc:
                results.append(f)
                break
    return results


if __name__ == "__main__":
    print(f"Loaded {len(ALL_128_FIELDS)} fields")
    for f in ALL_128_FIELDS[:3]:
        print(f.summary_line())
    results = get_fields_by_keywords(["functor", "adjoint"])
    print(f"Keyword search functor/adjoint found {len(results)} fields:")
    for f in results:
        print(f"  - {f.name}")
    obs = get_fields_for_obstruction("homotopy obstruction in algebraic topology")
    print(f"Obstruction search found {len(obs)} fields")
