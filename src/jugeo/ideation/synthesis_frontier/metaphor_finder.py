from __future__ import annotations
"""Metaphor finder for synthesis frontier — discovers cross-domain analogies.
# copilot: synthesis frontier metaphor finder — structural analogies across math domains
"""

import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model imports with stubs fallback
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.synthesis_frontier.models import MetaphorLink, FieldNode
except Exception:  # pragma: no cover
    @dataclass(frozen=True)
    class MetaphorLink:  # type: ignore[no-redef]
        link_id: str
        source_field: str
        target_field: str
        source_concept: str
        target_concept: str
        metaphor_description: str
        bridge_propositions: tuple
        strength: float
        kind: str
        llm_judge_score: float
        llm_judge_reasoning: str

        @classmethod
        def make(
            cls,
            source_field_id: str,
            target_field_id: str,
            source_concept: str,
            target_concept: str,
            description: str,
            strength: float,
            kind: str,
            supporting_propositions: tuple = (),
            is_known_classical: bool = False,
        ) -> "MetaphorLink":
            return cls(
                link_id=str(uuid.uuid4())[:12],
                source_field=source_field_id,
                target_field=target_field_id,
                source_concept=source_concept,
                target_concept=target_concept,
                metaphor_description=description,
                bridge_propositions=supporting_propositions,
                strength=strength,
                kind=kind,
                llm_judge_score=strength,
                llm_judge_reasoning="stub",
            )

    @dataclass
    class FieldNode:  # type: ignore[no-redef]
        field_id: str
        name: str
        description: str
        propositions: list = None
        keywords: tuple = ()
        constituent_fields: tuple = ()

        def __post_init__(self):
            if self.propositions is None:
                self.propositions = []

        @classmethod
        def make(
            cls,
            name: str,
            description: str,
            keywords: tuple = (),
        ) -> "FieldNode":
            field_id = name.lower().replace(" ", "_")
            return cls(
                field_id=field_id,
                name=name,
                description=description,
                keywords=keywords,
            )


def _make_metaphor_link(
    source_field: str,
    target_field: str,
    source_concept: str,
    target_concept: str,
    description: str,
    strength: float,
    kind: str,
    supporting_propositions: tuple = (),
) -> MetaphorLink:
    """Create a MetaphorLink, handling both real and stub model."""
    try:
        return MetaphorLink(
            link_id=str(uuid.uuid4())[:12],
            source_field=source_field,
            target_field=target_field,
            source_concept=source_concept,
            target_concept=target_concept,
            metaphor_description=description,
            bridge_propositions=supporting_propositions,
            strength=strength,
            kind=kind,
            llm_judge_score=0.0,
            llm_judge_reasoning="",
        )
    except TypeError:
        # Stub or alternate signature
        return MetaphorLink.make(  # type: ignore[union-attr]
            source_field_id=source_field,
            target_field_id=target_field,
            source_concept=source_concept,
            target_concept=target_concept,
            description=description,
            strength=strength,
            kind=kind,
            supporting_propositions=supporting_propositions,
        )


def _get_keywords(node: FieldNode) -> tuple[str, ...]:
    """Extract a normalised keyword tuple from a FieldNode.

    Handles both the real FieldNode (which has core_objects, core_morphisms,
    key_theorems, but no 'keywords' attribute) and the lightweight stub
    (which carries a 'keywords' tuple directly).
    """
    # Prefer an explicit keywords attribute (stub FieldNode)
    kw = getattr(node, "keywords", None)
    if kw:
        return tuple(str(k).lower() for k in kw)

    # Fall back to mining the real FieldNode's rich fields
    parts: list[str] = []
    for attr in ("core_objects", "core_morphisms", "key_theorems"):
        val = getattr(node, attr, ())
        for item in val:
            parts.extend(str(item).lower().split())

    desc = getattr(node, "description", "")
    if desc:
        # Include high-value words from the description (length > 4)
        for word in desc.lower().split():
            cleaned = word.strip(".,;:()[]{}\"'")
            if len(cleaned) > 4:
                parts.append(cleaned)

    name = getattr(node, "name", "")
    if name:
        parts.extend(name.lower().split())

    return tuple(dict.fromkeys(parts))  # deduplicate, preserve order


# ---------------------------------------------------------------------------
# MetaphorKind
# ---------------------------------------------------------------------------


class MetaphorKind(str, Enum):
    """Structural classification of a cross-domain metaphor."""

    STRUCTURAL = "STRUCTURAL"
    FUNCTORIAL = "FUNCTORIAL"
    DUALISTIC = "DUALISTIC"
    ADJOINT = "ADJOINT"
    GALOIS = "GALOIS"
    COHOMOLOGICAL = "COHOMOLOGICAL"
    CATEGORICAL = "CATEGORICAL"
    TOPOLOGICAL = "TOPOLOGICAL"
    ALGEBRAIC = "ALGEBRAIC"
    LOGICAL = "LOGICAL"

    def description(self) -> str:
        """Human-readable description of this metaphor kind."""
        _descs = {
            "STRUCTURAL": "A structural isomorphism or deep formal analogy between the two domains.",
            "FUNCTORIAL": "A functor (or functor-like map) from one domain's objects to the other's.",
            "DUALISTIC": "A duality: the two domains are each other's 'mirror image' in a precise sense.",
            "ADJOINT": "An adjunction F \u22a3 G with a universal property connecting the two domains.",
            "GALOIS": "A Galois-type correspondence linking subobjects in one domain to subobjects in another.",
            "COHOMOLOGICAL": "A cohomological connection: obstructions, extensions, or classification theorems.",
            "CATEGORICAL": "A categorical equivalence or correspondence at the level of whole categories.",
            "TOPOLOGICAL": "A topological connection: continuity, compactness, or homotopy-type reasoning.",
            "ALGEBRAIC": "An algebraic correspondence: shared algebraic laws or structures.",
            "LOGICAL": "A logical correspondence: propositions, proofs, or deductive systems.",
        }
        return _descs.get(self.value, self.value)

    def model_kind_str(self) -> str:
        """Map to the MetaphorLink 'kind' vocabulary used in models.py."""
        _map = {
            "STRUCTURAL": "ISOMORPHISM",
            "FUNCTORIAL": "FUNCTOR",
            "DUALISTIC": "DUALITY",
            "ADJOINT": "ADJUNCTION",
            "GALOIS": "DUALITY",
            "COHOMOLOGICAL": "ANALOGY",
            "CATEGORICAL": "FUNCTOR",
            "TOPOLOGICAL": "ANALOGY",
            "ALGEBRAIC": "ANALOGY",
            "LOGICAL": "ISOMORPHISM",
        }
        return _map.get(self.value, "ANALOGY")


# ---------------------------------------------------------------------------
# MetaphorPattern
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetaphorPattern:
    """A named, reusable pattern for detecting cross-domain metaphors.

    Instances are matched against pairs of FieldNodes by checking keyword
    overlap.  When the overlap score exceeds zero, a MetaphorCandidate is
    emitted.

    Attributes
    ----------
    pattern_id : str
        Short stable identifier, e.g. ``"curry-howard"``.
    name : str
        Human-readable name of the pattern.
    description : str
        Multi-sentence explanation of the metaphor.
    source_keywords : tuple[str, ...]
        Keywords expected in the *source* field for this pattern to apply.
    target_keywords : tuple[str, ...]
        Keywords expected in the *target* field for this pattern to apply.
    strength_hint : float
        Prior estimate of the metaphor's faithfulness in [0, 1].
    examples : tuple[str, ...]
        Concrete illustrative examples of the pattern in action.
    """

    pattern_id: str
    name: str
    description: str
    source_keywords: tuple[str, ...]
    target_keywords: tuple[str, ...]
    strength_hint: float
    examples: tuple[str, ...]

    def overlap_score(self, field_kws: tuple[str, ...], pattern_kws: tuple[str, ...]) -> float:
        """Compute Jaccard-like overlap between field keywords and pattern keywords.

        Parameters
        ----------
        field_kws:
            Keywords extracted from a FieldNode.
        pattern_kws:
            The source or target keyword tuple from this pattern.

        Returns
        -------
        float
            Intersection size / pattern size (capped at 1.0).  Returns 0.0
            when the pattern keyword set is empty.
        """
        if not pattern_kws:
            return 0.0
        field_set = set(field_kws)
        pattern_set = set(pattern_kws)
        hits = len(field_set & pattern_set)
        return hits / len(pattern_set)

    def match(
        self,
        field_a_kws: tuple[str, ...],
        field_b_kws: tuple[str, ...],
    ) -> tuple[float, bool]:
        """Try both orientations of the pattern against two keyword sets.

        Returns
        -------
        tuple[float, bool]
            (score, is_reversed) — score is the combined overlap; is_reversed
            indicates whether field_b matched source and field_a matched target.
        """
        fwd_src = self.overlap_score(field_a_kws, self.source_keywords)
        fwd_tgt = self.overlap_score(field_b_kws, self.target_keywords)
        rev_src = self.overlap_score(field_b_kws, self.source_keywords)
        rev_tgt = self.overlap_score(field_a_kws, self.target_keywords)

        fwd_score = (fwd_src + fwd_tgt) / 2.0
        rev_score = (rev_src + rev_tgt) / 2.0

        if fwd_score >= rev_score:
            return fwd_score, False
        return rev_score, True


# Module-level KNOWN_PATTERNS — NOT a class-body field
KNOWN_PATTERNS: list[MetaphorPattern] = [
    MetaphorPattern(
        pattern_id="curry-howard",
        name="Curry-Howard Correspondence",
        description="Propositions correspond to types; proofs correspond to programs; logical connectives correspond to type constructors.",
        source_keywords=("proof", "proposition", "logic", "intuitionistic"),
        target_keywords=("type", "term", "program", "lambda"),
        strength_hint=0.95,
        examples=("Implication \u2194 Function type", "Conjunction \u2194 Product type", "Disjunction \u2194 Sum type"),
    ),
    MetaphorPattern(
        pattern_id="galois-connection",
        name="Galois Connection",
        description="An adjunction between posets: a monotone Galois connection provides a duality between two ordered structures.",
        source_keywords=("field", "extension", "polynomial", "roots"),
        target_keywords=("group", "automorphism", "symmetry", "subgroup"),
        strength_hint=0.90,
        examples=("Field extensions \u2194 Subgroups of Galois group", "Fixed fields \u2194 Stabilizer subgroups"),
    ),
    MetaphorPattern(
        pattern_id="stone-duality",
        name="Stone Duality",
        description="Boolean algebras dually correspond to Stone spaces (compact Hausdorff totally disconnected spaces).",
        source_keywords=("boolean", "algebra", "lattice", "logic"),
        target_keywords=("topology", "space", "compact", "clopen"),
        strength_hint=0.88,
        examples=("Boolean algebra \u2194 Stone space", "Filters \u2194 Points", "Ultrafilters \u2194 Points in Stone\u2013\u010cech compactification"),
    ),
    MetaphorPattern(
        pattern_id="nerve-realization",
        name="Nerve-Realization Adjunction",
        description="The geometric realization and singular complex functors form an adjunction between simplicial sets and topological spaces.",
        source_keywords=("simplicial", "nerve", "category", "small"),
        target_keywords=("topology", "space", "geometric", "realization"),
        strength_hint=0.85,
        examples=("Nerve of a category \u2194 Classifying space", "Singular complex \u2194 Simplicial set"),
    ),
    MetaphorPattern(
        pattern_id="homotopy-type",
        name="Homotopy Hypothesis",
        description="\u221e-groupoids correspond to homotopy types; homotopy theory and higher category theory are equivalent.",
        source_keywords=("homotopy", "path", "loop", "space"),
        target_keywords=("groupoid", "infinity", "higher", "category"),
        strength_hint=0.92,
        examples=("\u03c0_n(X) \u2194 n-morphisms of the \u221e-groupoid", "Weak equivalences \u2194 Internal equivalences"),
    ),
    MetaphorPattern(
        pattern_id="cohomology-extension",
        name="Cohomology as Obstruction",
        description="Cohomology groups measure the failure of local data to glue into global sections; they classify extensions and deformations.",
        source_keywords=("cohomology", "sheaf", "obstruction", "cocycle"),
        target_keywords=("extension", "deformation", "classification", "principal"),
        strength_hint=0.83,
        examples=("H^1 classifies principal bundles", "H^2 classifies central extensions", "Ext groups classify module extensions"),
    ),
    MetaphorPattern(
        pattern_id="adjoint-functor",
        name="Adjoint Functor Pattern",
        description="Free-forgetful adjunctions pervade mathematics: free constructions are left adjoints to forgetful functors.",
        source_keywords=("free", "functor", "adjoint", "left"),
        target_keywords=("forgetful", "algebra", "structure", "right"),
        strength_hint=0.87,
        examples=("Free group \u2194 Sets", "Free module \u2194 Abelian groups", "Free monoid \u2194 Sets"),
    ),
    MetaphorPattern(
        pattern_id="pontryagin-duality",
        name="Pontryagin Duality",
        description="Locally compact abelian groups are dual to their character groups; Fourier transform implements this duality.",
        source_keywords=("abelian", "group", "character", "harmonic"),
        target_keywords=("dual", "fourier", "frequency", "spectrum"),
        strength_hint=0.86,
        examples=("Z \u2194 U(1)", "R \u2194 R", "Finite group \u2194 Its dual group"),
    ),
    MetaphorPattern(
        pattern_id="grothendieck-duality",
        name="Grothendieck Duality",
        description="A vast generalization of Serre duality to morphisms of schemes; the dualizing sheaf implements duality in coherent cohomology.",
        source_keywords=("sheaf", "scheme", "coherent", "dualizing"),
        target_keywords=("duality", "cohomology", "algebraic", "derived"),
        strength_hint=0.82,
        examples=("Serre duality \u2194 Grothendieck duality for smooth varieties", "Dualizing complex in derived categories"),
    ),
    MetaphorPattern(
        pattern_id="morita-equivalence",
        name="Morita Equivalence",
        description="Two rings are Morita equivalent if their module categories are equivalent; this generalizes isomorphism for rings.",
        source_keywords=("ring", "module", "algebra", "representation"),
        target_keywords=("category", "equivalence", "functor", "adjoint"),
        strength_hint=0.80,
        examples=("Matrix rings \u2194 Base ring (Morita equivalent)", "C*-algebras \u2194 Groupoid algebras"),
    ),
    MetaphorPattern(
        pattern_id="langlands-correspondence",
        name="Langlands Correspondence",
        description="A conjectural web of dualities between automorphic forms, Galois representations, and L-functions.",
        source_keywords=("galois", "representation", "l-function", "number"),
        target_keywords=("automorphic", "form", "group", "reductive"),
        strength_hint=0.78,
        examples=("Elliptic curves \u2194 Modular forms (Shimura-Taniyama)", "Local Langlands: Weil-Deligne reps \u2194 smooth reps of GL_n"),
    ),
    MetaphorPattern(
        pattern_id="mirror-symmetry",
        name="Mirror Symmetry",
        description="A duality between symplectic geometry (A-model) and complex geometry (B-model) arising from string theory.",
        source_keywords=("symplectic", "lagrangian", "a-model", "string"),
        target_keywords=("complex", "holomorphic", "b-model", "calabi-yau"),
        strength_hint=0.75,
        examples=("Fukaya category \u2194 Derived category of coherent sheaves", "Gromov-Witten invariants \u2194 periods"),
    ),
    MetaphorPattern(
        pattern_id="baez-dolan-tqft",
        name="Cobordism Hypothesis",
        description="Fully extended TQFTs are classified by their value on a point; this is an \u221e-categorical classification theorem.",
        source_keywords=("cobordism", "tqft", "manifold", "boundary"),
        target_keywords=("infinity", "category", "dualizable", "fully"),
        strength_hint=0.79,
        examples=("1D TQFT \u2194 Frobenius algebra", "2D TQFT \u2194 Commutative Frobenius algebra", "Fully extended \u2194 \u221e-category with duals"),
    ),
    MetaphorPattern(
        pattern_id="noncommutative-space",
        name="Gelfand Duality / NC Geometry",
        description="Commutative C*-algebras correspond to compact Hausdorff spaces; noncommutative C*-algebras are 'noncommutative spaces'.",
        source_keywords=("c*-algebra", "operator", "commutative", "spectrum"),
        target_keywords=("space", "topology", "continuous", "compact"),
        strength_hint=0.84,
        examples=("C(X) \u2194 X for compact Hausdorff X", "Noncommutative torus \u2194 quantum torus"),
    ),
    MetaphorPattern(
        pattern_id="linear-logic-resources",
        name="Linear Logic as Resource Sensitivity",
        description="Linear logic tracks the use of hypotheses exactly once; it models resource-sensitive computation and quantum information.",
        source_keywords=("linear", "logic", "proof", "sequent"),
        target_keywords=("resource", "quantum", "monoidal", "session"),
        strength_hint=0.77,
        examples=("!A (exponential) \u2194 Reusable resource", "A \u2297 B \u2194 Parallel resources", "A \u22b8 B \u2194 Linear function consuming A"),
    ),
]


# ---------------------------------------------------------------------------
# MetaphorCandidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetaphorCandidate:
    """An intermediate candidate metaphor discovered by PatternMatcher.

    MetaphorCandidates are lightweight records produced during the scanning
    phase; they are later converted to full MetaphorLink objects by
    MetaphorFinder.

    Attributes
    ----------
    candidate_id : str
        Short unique identifier.
    field_a_id : str
        field_id of the first field.
    field_b_id : str
        field_id of the second field.
    source_concept : str
        Representative concept in field_a.
    target_concept : str
        Representative concept in field_b.
    kind : MetaphorKind
        Structural classification of the metaphor.
    strength_estimate : float
        Estimated strength in [0, 1].
    pattern_matched : str
        pattern_id of the KNOWN_PATTERNS entry that triggered this candidate,
        or "keyword-intersection" for directly detected overlaps.
    evidence : tuple[str, ...]
        Specific keywords or phrases that triggered this candidate.
    """

    candidate_id: str
    field_a_id: str
    field_b_id: str
    source_concept: str
    target_concept: str
    kind: MetaphorKind
    strength_estimate: float
    pattern_matched: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "field_a_id": self.field_a_id,
            "field_b_id": self.field_b_id,
            "source_concept": self.source_concept,
            "target_concept": self.target_concept,
            "kind": self.kind.value,
            "strength_estimate": self.strength_estimate,
            "pattern_matched": self.pattern_matched,
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# PatternMatcher
# ---------------------------------------------------------------------------

# Map from pattern_id prefix / kind hint to MetaphorKind
_KIND_HINTS: dict[str, MetaphorKind] = {
    "curry-howard": MetaphorKind.LOGICAL,
    "galois": MetaphorKind.GALOIS,
    "stone-duality": MetaphorKind.DUALISTIC,
    "nerve": MetaphorKind.CATEGORICAL,
    "homotopy": MetaphorKind.TOPOLOGICAL,
    "cohomology": MetaphorKind.COHOMOLOGICAL,
    "adjoint": MetaphorKind.ADJOINT,
    "pontryagin": MetaphorKind.DUALISTIC,
    "grothendieck": MetaphorKind.COHOMOLOGICAL,
    "morita": MetaphorKind.CATEGORICAL,
    "langlands": MetaphorKind.STRUCTURAL,
    "mirror": MetaphorKind.DUALISTIC,
    "baez": MetaphorKind.CATEGORICAL,
    "noncommutative": MetaphorKind.ALGEBRAIC,
    "linear-logic": MetaphorKind.LOGICAL,
}


def _infer_kind(pattern_id: str) -> MetaphorKind:
    """Infer a MetaphorKind from a pattern_id string."""
    for prefix, kind in _KIND_HINTS.items():
        if pattern_id.startswith(prefix):
            return kind
    return MetaphorKind.STRUCTURAL


class PatternMatcher:
    """Scans pairs of FieldNodes against KNOWN_PATTERNS to emit MetaphorCandidates.

    The matcher operates in two passes:

    1. **Pattern pass** — each pattern in KNOWN_PATTERNS is tried in both
       orientations against (field_a, field_b).  If the combined overlap
       score exceeds ``min_overlap``, a MetaphorCandidate is emitted.

    2. **Intersection pass** — direct keyword intersection regardless of any
       named pattern.  Shared keywords with a meaningful count produce an
       additional STRUCTURAL candidate.

    Parameters
    ----------
    min_overlap : float
        Minimum combined overlap score (default 0.05) to emit a candidate.
    top_k : int
        Maximum number of candidates to return per field pair (default 20).
    """

    def __init__(self, min_overlap: float = 0.05, top_k: int = 20) -> None:
        self.min_overlap = min_overlap
        self.top_k = top_k

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_matches(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
    ) -> list[MetaphorCandidate]:
        """Scan two FieldNodes for metaphor candidates.

        Parameters
        ----------
        field_a, field_b:
            The two fields to compare.

        Returns
        -------
        list[MetaphorCandidate]
            Candidates sorted by strength_estimate descending, capped at top_k.
        """
        kws_a = _get_keywords(field_a)
        kws_b = _get_keywords(field_b)
        candidates: list[MetaphorCandidate] = []

        candidates.extend(self._pattern_pass(field_a, field_b, kws_a, kws_b))
        candidates.extend(self._intersection_pass(field_a, field_b, kws_a, kws_b))

        # Deduplicate by pattern_matched
        seen: set[str] = set()
        unique: list[MetaphorCandidate] = []
        for c in sorted(candidates, key=lambda x: -x.strength_estimate):
            key = (c.pattern_matched, c.kind.value)
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique[: self.top_k]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pattern_pass(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
        kws_a: tuple[str, ...],
        kws_b: tuple[str, ...],
    ) -> list[MetaphorCandidate]:
        results: list[MetaphorCandidate] = []
        for pattern in KNOWN_PATTERNS:
            score, is_reversed = pattern.match(kws_a, kws_b)
            if score < self.min_overlap:
                continue

            strength = pattern.strength_hint * score
            if is_reversed:
                src_id, tgt_id = field_b.field_id, field_a.field_id
                src_kws = set(kws_b) & set(pattern.source_keywords)
                tgt_kws = set(kws_a) & set(pattern.target_keywords)
            else:
                src_id, tgt_id = field_a.field_id, field_b.field_id
                src_kws = set(kws_a) & set(pattern.source_keywords)
                tgt_kws = set(kws_b) & set(pattern.target_keywords)

            evidence = tuple(sorted(src_kws | tgt_kws))
            kind = _infer_kind(pattern.pattern_id)

            src_concept = ", ".join(sorted(src_kws)[:3]) or pattern.source_keywords[0]
            tgt_concept = ", ".join(sorted(tgt_kws)[:3]) or pattern.target_keywords[0]

            results.append(
                MetaphorCandidate(
                    candidate_id=str(uuid.uuid4())[:12],
                    field_a_id=src_id,
                    field_b_id=tgt_id,
                    source_concept=src_concept,
                    target_concept=tgt_concept,
                    kind=kind,
                    strength_estimate=min(strength, 1.0),
                    pattern_matched=pattern.pattern_id,
                    evidence=evidence,
                )
            )
        return results

    def _intersection_pass(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
        kws_a: tuple[str, ...],
        kws_b: tuple[str, ...],
    ) -> list[MetaphorCandidate]:
        shared = sorted(set(kws_a) & set(kws_b))
        if len(shared) < 2:
            return []

        # Score based on relative intersection size
        union_size = len(set(kws_a) | set(kws_b))
        jaccard = len(shared) / union_size if union_size else 0.0
        strength = min(jaccard * 3.0, 0.70)  # cap at 0.70 for raw intersection

        return [
            MetaphorCandidate(
                candidate_id=str(uuid.uuid4())[:12],
                field_a_id=field_a.field_id,
                field_b_id=field_b.field_id,
                source_concept=shared[0],
                target_concept=shared[1] if len(shared) > 1 else shared[0],
                kind=MetaphorKind.STRUCTURAL,
                strength_estimate=strength,
                pattern_matched="keyword-intersection",
                evidence=tuple(shared[:10]),
            )
        ]


# ---------------------------------------------------------------------------
# MetaphorFinder
# ---------------------------------------------------------------------------


class MetaphorFinder:
    """High-level API for discovering MetaphorLinks between two FieldNodes.

    Combines PatternMatcher output with optional LLM enrichment to produce
    a ranked list of MetaphorLink objects ready for the synthesis tournament.

    Parameters
    ----------
    use_llm : bool
        When True, attempt to enrich metaphors via an LLM call.  Currently
        a no-op placeholder (reserved for future integration).
    min_strength : float
        Minimum strength threshold for emitted MetaphorLinks (default 0.05).
    top_k : int
        Maximum number of MetaphorLinks to return per field pair (default 15).
    """

    def __init__(self, use_llm: bool = False, min_strength: float = 0.05, top_k: int = 15) -> None:
        self.use_llm = use_llm
        self.min_strength = min_strength
        self.top_k = top_k
        self._matcher = PatternMatcher()

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def find_metaphors(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
    ) -> list[MetaphorLink]:
        """Discover MetaphorLinks between two FieldNodes.

        Algorithm
        ---------
        1. Run PatternMatcher to obtain MetaphorCandidates.
        2. Filter candidates below min_strength.
        3. Convert top-k candidates to MetaphorLink objects.
        4. Optionally enrich via LLM (placeholder).
        5. Return ranked list.

        Parameters
        ----------
        field_a, field_b :
            The two fields to compare.

        Returns
        -------
        list[MetaphorLink]
            Ranked MetaphorLinks, strongest first.
        """
        candidates = self._matcher.find_matches(field_a, field_b)
        candidates = [c for c in candidates if c.strength_estimate >= self.min_strength]
        candidates = candidates[: self.top_k]

        links: list[MetaphorLink] = []
        for candidate in candidates:
            link = self._candidate_to_link(candidate)
            links.append(link)

        if self.use_llm:
            links = self._enrich_with_llm(field_a, field_b, links)

        return self.rank_metaphors(links)

    def find_bridge_concepts(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
    ) -> list[tuple[str, str, float]]:
        """Return bridge concept pairs connecting the two fields.

        Each returned tuple is (concept_in_field_a, concept_in_field_b, strength).
        The list is sorted by strength descending.

        Parameters
        ----------
        field_a, field_b :
            The two fields to inspect.

        Returns
        -------
        list[tuple[str, str, float]]
            Bridge concept pairs with associated strength estimates.
        """
        kws_a = _get_keywords(field_a)
        kws_b = _get_keywords(field_b)

        bridges: list[tuple[str, str, float]] = []

        # Direct intersection
        shared = sorted(set(kws_a) & set(kws_b))
        for kw in shared:
            bridges.append((kw, kw, 0.5))

        # Pattern-derived bridges
        for pattern in KNOWN_PATTERNS:
            score, is_reversed = pattern.match(kws_a, kws_b)
            if score < 0.05:
                continue
            strength = pattern.strength_hint * score
            if is_reversed:
                src_kws = set(kws_b) & set(pattern.source_keywords)
                tgt_kws = set(kws_a) & set(pattern.target_keywords)
            else:
                src_kws = set(kws_a) & set(pattern.source_keywords)
                tgt_kws = set(kws_b) & set(pattern.target_keywords)
            for sk in sorted(src_kws)[:2]:
                for tk in sorted(tgt_kws)[:2]:
                    bridges.append((sk, tk, min(strength, 1.0)))

        # Deduplicate and sort
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str, float]] = []
        for a, b, s in sorted(bridges, key=lambda x: -x[2]):
            if (a, b) not in seen:
                seen.add((a, b))
                unique.append((a, b, s))
        return unique

    def rank_metaphors(self, metaphors: list[MetaphorLink]) -> list[MetaphorLink]:
        """Sort MetaphorLinks by strength descending.

        Parameters
        ----------
        metaphors :
            List of MetaphorLink objects to sort.

        Returns
        -------
        list[MetaphorLink]
            Same objects, sorted strongest-first.
        """
        return sorted(metaphors, key=lambda m: -m.strength)

    def summarize(self, metaphors: list[MetaphorLink]) -> str:
        """Produce a multi-line human-readable summary of discovered metaphors.

        Parameters
        ----------
        metaphors :
            The metaphors to summarise.

        Returns
        -------
        str
            A formatted text summary.
        """
        if not metaphors:
            return "No metaphors found."

        lines: list[str] = []
        lines.append(f"=== MetaphorFinder: {len(metaphors)} metaphor(s) found ===")
        lines.append("")
        for i, m in enumerate(metaphors, 1):
            lines.append(
                f"  {i:2d}. [{m.kind:>12s}]  {m.source_concept!r:30s} "
                f"\u2194  {m.target_concept!r:30s}  (strength={m.strength:.3f})"
            )
            if m.metaphor_description:
                # Indent description, wrap at ~80 chars
                desc = m.metaphor_description
                if len(desc) > 80:
                    desc = desc[:77] + "..."
                lines.append(f"       {desc}")
            lines.append("")
        lines.append(
            f"  Strength range: "
            f"{min(m.strength for m in metaphors):.3f} \u2013 "
            f"{max(m.strength for m in metaphors):.3f}"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _candidate_to_link(self, candidate: MetaphorCandidate) -> MetaphorLink:
        """Convert a MetaphorCandidate to a MetaphorLink."""
        # Look up the pattern for a richer description
        pattern = next(
            (p for p in KNOWN_PATTERNS if p.pattern_id == candidate.pattern_matched),
            None,
        )
        if pattern:
            description = pattern.description
            if pattern.examples:
                description += "  Examples: " + "; ".join(pattern.examples[:2]) + "."
        else:
            description = (
                f"Structural metaphor detected via shared keywords: "
                + ", ".join(candidate.evidence[:5])
                + "."
            )

        kind_str = candidate.kind.model_kind_str()

        return _make_metaphor_link(
            source_field=candidate.field_a_id,
            target_field=candidate.field_b_id,
            source_concept=candidate.source_concept,
            target_concept=candidate.target_concept,
            description=description,
            strength=candidate.strength_estimate,
            kind=kind_str,
        )

    def _enrich_with_llm(
        self,
        field_a: FieldNode,
        field_b: FieldNode,
        links: list[MetaphorLink],
    ) -> list[MetaphorLink]:
        """Placeholder for LLM-based metaphor enrichment.

        In a full implementation this would call an LLM to refine the
        description, boost/penalise strength, and potentially add entirely
        new metaphors not found by the keyword heuristics.

        Currently a no-op that returns the input unchanged.
        """
        _log.debug(
            "LLM enrichment requested for (%s, %s) but not yet implemented.",
            field_a.field_id,
            field_b.field_id,
        )
        return links


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def scan_field_pair(
    field_a: FieldNode,
    field_b: FieldNode,
    *,
    use_llm: bool = False,
    min_strength: float = 0.05,
    top_k: int = 15,
) -> list[MetaphorLink]:
    """Module-level convenience wrapper around MetaphorFinder.find_metaphors.

    Parameters
    ----------
    field_a, field_b :
        The two FieldNodes to scan.
    use_llm :
        Forward to MetaphorFinder.
    min_strength :
        Forward to MetaphorFinder.
    top_k :
        Forward to MetaphorFinder.

    Returns
    -------
    list[MetaphorLink]
        Ranked metaphors for the pair.
    """
    finder = MetaphorFinder(use_llm=use_llm, min_strength=min_strength, top_k=top_k)
    return finder.find_metaphors(field_a, field_b)


def summarize_pair(field_a: FieldNode, field_b: FieldNode) -> str:
    """Return a human-readable summary of metaphors between two fields."""
    finder = MetaphorFinder()
    metaphors = finder.find_metaphors(field_a, field_b)
    return finder.summarize(metaphors)


def bridge_concepts(field_a: FieldNode, field_b: FieldNode) -> list[tuple[str, str, float]]:
    """Return bridge concept pairs for two fields (convenience wrapper)."""
    return MetaphorFinder().find_bridge_concepts(field_a, field_b)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        from jugeo.ideation.synthesis_frontier.models import FieldNode
        field_a = FieldNode.make("Type Theory", "Types as propositions", keywords=("type", "term", "proof", "lambda", "proposition", "dependent"))
        field_b = FieldNode.make("Category Theory", "Functors and adjunctions", keywords=("functor", "adjoint", "category", "morphism", "natural"))
        finder = MetaphorFinder()
        metaphors = finder.find_metaphors(field_a, field_b)
        print(f"Found {len(metaphors)} metaphors")
        print(finder.summarize(metaphors))
        bridges = finder.find_bridge_concepts(field_a, field_b)
        print(f"Bridge concepts: {bridges[:3]}")
    except Exception as e:
        import traceback; traceback.print_exc()
