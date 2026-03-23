"""Catalog of all 48 mathematical field nodes for the synthesis tournament.
# copilot: synthesis frontier fields catalog — 48 mathematical field nodes
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
# Exports
# ---------------------------------------------------------------------------

ALL_48_FIELDS: list[FieldNode] = [
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
]

assert len(ALL_48_FIELDS) == 48, f"Expected 48 fields, got {len(ALL_48_FIELDS)}"

FIELD_BY_ID: dict[str, FieldNode] = {f.field_id: f for f in ALL_48_FIELDS}
FIELD_BY_NAME: dict[str, FieldNode] = {f.name: f for f in ALL_48_FIELDS}


def get_fields_by_keywords(keywords: list[str]) -> list[FieldNode]:
    """Return fields whose keyword tuples overlap with the given keywords (case-insensitive)."""
    lower_kw = {k.lower() for k in keywords}
    results = []
    for f in ALL_48_FIELDS:
        field_kw = {k.lower() for k in f.keywords}
        if field_kw & lower_kw:
            results.append(f)
    return results


def get_fields_for_obstruction(obstruction_desc: str) -> list[FieldNode]:
    """Return fields relevant to an obstruction description via keyword/name matching."""
    lower_desc = obstruction_desc.lower()
    results = []
    for f in ALL_48_FIELDS:
        if f.name.lower() in lower_desc:
            results.append(f)
            continue
        for kw in f.keywords:
            if kw.lower() in lower_desc:
                results.append(f)
                break
    return results


if __name__ == "__main__":
    print(f"Loaded {len(ALL_48_FIELDS)} fields")
    for f in ALL_48_FIELDS[:3]:
        print(f.summary_line())
    results = get_fields_by_keywords(["functor", "adjoint"])
    print(f"Keyword search functor/adjoint found {len(results)} fields:")
    for f in results:
        print(f"  - {f.name}")
    obs = get_fields_for_obstruction("homotopy obstruction in algebraic topology")
    print(f"Obstruction search found {len(obs)} fields")
