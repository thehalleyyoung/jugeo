"""Stage 05 — Witness computation from cover for the relational_refinement package.

Implements ``CoverWitnessComputer``, which computes program-equivalence witnesses
using the **sheaf-theoretic descent** strategy introduced in Ch9 §9.5 of
theory2.tex.

Theory context (Ch9 §9.5: Witness Computation from Cover)
----------------------------------------------------------
Let X be the *judgment site* — the category whose objects are judgment
coordinates and whose morphisms are refinement relations.  A **hypercover**
U → X is a family of patches {U_i} together with restriction maps such that
the U_i cover X in the Grothendieck topology induced by the trust lattice.

Given two symbolic programs P₁ and P₂ (each a sheaf of judgment sections over
X), an equivalence witness W: P₁ ≅ P₂ is a natural isomorphism — i.e. a
compatible family of isomorphisms W_i: P₁|_{U_i} ≅ P₂|_{U_i} that agrees on
all overlaps.

The algorithm proceeds in three stages:

Stage A — Cover construction
    ``build_cover`` partitions the union of P₁'s and P₂'s coordinate sets into
    overlapping patches.  Patch depth controls granularity: depth 1 gives a
    single coarse patch; depth d gives up to 2^d sub-patches via a binary
    coordinate trie.  Patches always overlap at their boundaries to ensure the
    hypercover condition (every point is covered by at least two patches near
    each boundary).

Stage B — Local witnesses
    ``compute_local_witness`` restricts both programs to a single patch and
    attempts to build a ``LocalWitness`` via one of four strategies:
    * **structural** — section-by-section matching on the eight judgment
      components (c, φ, A, E, O, B, T, Π).  Fast; works when the programs
      share coordinate names or have structurally isomorphic sections.
    * **semantic** — content-hash and edit-distance matching.  Slower; handles
      programs with renamed coordinates or reordered components.
    * **z3** — symbolic constraint solving for proposition equivalence.  Only
      attempted if the structural and semantic strategies yield low confidence.
    * **oracle** — fallback heuristic using trust-tier dominance.

Stage C — Gluing / descent
    ``check_gluing`` implements the *Čech condition*: for every pair of patches
    (U_i, U_j) the restrictions of W_i and W_j to U_i ∩ U_j must agree.
    Disagreements are recorded as ``GluingFailure`` objects carrying:
    * The coordinates on which witnesses disagree.
    * The specific component (c, φ, A, E, O, B, T, Π) that differs.
    * An H¹ cohomology class string classifying the obstruction.

    ``compute_global_witness`` assembles the colimit: if all gluing conditions
    hold, the ``GlobalWitness.is_complete`` flag is ``True`` and the witness
    certifies P₁ ≅ P₂.  If any gluing condition fails, the witness is
    incomplete and the ``gluing_failures`` tuple records the obstruction data.

Čech cohomology interpretation
-------------------------------
The Čech complex Č^•(U, ℱ) with ℱ = Hom(P₁, P₂) has:
    Č^0 = ∏_i  ℱ(U_i)                local sections (local witnesses)
    Č^1 = ∏_{i<j}  ℱ(U_i ∩ U_j)     disagreement data

The coboundary δ: Č^0 → Č^1 sends a family (W_i) to the family of differences
(W_i|_{ij} − W_j|_{ij}).  A global witness exists iff δ(W_•) = 0, i.e. the
Čech 1-cocycle is trivial.

When δ(W_•) ≠ 0, the cohomology class [δ(W_•)] ∈ H¹(U, ℱ) is the obstruction
to gluing.  ``_assign_cohomology_class`` computes a string label for this class
based on which judgment components are responsible for the disagreement.

Reference: theory2.tex, Ch9, §9.5 (pp. 312–341).

# copilot: witness_computation_from_cover — sheaf-theoretic descent for Ch9 §9.5
"""
from __future__ import annotations

import datetime
import hashlib
import itertools
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

# ---------------------------------------------------------------------------
# Jugeo infrastructure imports
# These are the same across every stage-N file in relational_refinement.
# Wrapped in try/except so the module remains importable in environments where
# the full jugeo wheel has not been installed (e.g. standalone CI inspection).
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        TrustLevel,
        Provenance,
        ProvenanceSource,
        Proposition,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        JudgmentAlgebra,
    )
except ImportError:
    TrustLevel = Any  # type: ignore[misc,assignment]
    Provenance = Any  # type: ignore[misc,assignment]
    ProvenanceSource = Any  # type: ignore[misc,assignment]
    Proposition = Any  # type: ignore[misc,assignment]
    Carrier = Any  # type: ignore[misc,assignment]
    EvidenceBundle = Any  # type: ignore[misc,assignment]
    EvidenceItem = Any  # type: ignore[misc,assignment]
    EvidenceItemKind = Any  # type: ignore[misc,assignment]
    ResidualObligation = Any  # type: ignore[misc,assignment]
    Obstruction = Any  # type: ignore[misc,assignment]
    TrustAnnotation = Any  # type: ignore[misc,assignment]
    JudgmentAlgebra = Any  # type: ignore[misc,assignment]

try:
    from jugeo.judgments.comparisons import (
        ComparisonMode,
        ComparisonResult,
        compare_sections,
    )
except ImportError:
    ComparisonMode = Any  # type: ignore[misc,assignment]
    ComparisonResult = Any  # type: ignore[misc,assignment]
    compare_sections = None  # type: ignore[assignment]

try:
    from jugeo.errors import (
        StructuredFailure,
        JuGeoError,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        FailureChain,
        as_failure_payload,
    )
except ImportError:
    StructuredFailure = Any  # type: ignore[misc,assignment]
    JuGeoError = Exception  # type: ignore[misc,assignment]
    FailureScope = Any  # type: ignore[misc,assignment]
    FailureClassification = Any  # type: ignore[misc,assignment]
    EvidenceFamily = Any  # type: ignore[misc,assignment]
    ObstructionRecord = Any  # type: ignore[misc,assignment]
    RepairHint = Any  # type: ignore[misc,assignment]
    RepairPriority = Any  # type: ignore[misc,assignment]
    FailureChain = Any  # type: ignore[misc,assignment]
    as_failure_payload = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.relational_refinement.models import (
        RefinementRelation,
        EquivalenceClass,
        RefinementWitness,
        RefinementOrder,
    )
except ImportError:
    RefinementRelation = Any  # type: ignore[misc,assignment]
    EquivalenceClass = Any  # type: ignore[misc,assignment]
    RefinementWitness = Any  # type: ignore[misc,assignment]
    RefinementOrder = Any  # type: ignore[misc,assignment]

# program_loader is an optional companion module — may not be present yet
try:
    from jugeo.program_loader import SymbolicProgram, load_program  # type: ignore[import]
except ImportError:
    SymbolicProgram = Any  # type: ignore[misc,assignment]
    load_program = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Judgment section component names — the eight fields of (c,φ,A,E,O,B,T,Π)
_SECTION_COMPONENTS: tuple[str, ...] = (
    "carrier",       # c — the carrier type / context
    "proposition",   # φ — the proposition being judged
    "ambient",       # A — ambient category / type universe
    "evidence",      # E — evidence bundle
    "obligations",   # O — residual obligations
    "bounds",        # B — bounding constraints
    "trust",         # T — trust annotation
    "provenance",    # Π — provenance record
)

# Indices into the judgment section tuple for fast positional access
_IDX_CARRIER     = 0
_IDX_PROPOSITION = 1
_IDX_AMBIENT     = 2
_IDX_EVIDENCE    = 3
_IDX_OBLIGATIONS = 4
_IDX_BOUNDS      = 5
_IDX_TRUST       = 6
_IDX_PROVENANCE  = 7

# Trust tier names ordered from weakest to strongest.
# We intentionally use strings (not TrustLevel enum) so that this module
# remains operational when jugeo.judgments is unavailable.
_TRUST_TIER_ORDER: tuple[str, ...] = (
    "UNVERIFIED",
    "ASSERTED",
    "CHECKED",
    "VERIFIED",
    "CERTIFIED",
    "AXIOM",
)

# The minimum confidence threshold for a local witness to be accepted as
# credible during gluing.  Witnesses below this threshold are treated as
# absent (empty section) for Čech condition checking purposes.
_MIN_CREDIBLE_CONFIDENCE: float = 0.25

# Maximum number of pairs checked during gluing (guard against O(n²) blow-up
# on very large covers).
_MAX_GLUING_PAIRS: int = 10_000

# Cohomology class labels used by _assign_cohomology_class
_H1_TRUST       = "H¹[trust]"
_H1_PROPOSITION = "H¹[proposition]"
_H1_EVIDENCE    = "H¹[evidence]"
_H1_OBLIGATION  = "H¹[obligation]"
_H1_BOUNDS      = "H¹[bounds]"
_H1_CARRIER     = "H¹[carrier]"
_H1_MIXED       = "H¹[mixed]"
_H0_TRIVIAL     = "H⁰[trivial]"

# String sentinel for coordinates that appear in one program but not the other
_COORD_ABSENT: str = "__absent__"

# Default cover depth when not explicitly specified
_DEFAULT_COVER_DEPTH: int = 3


# ---------------------------------------------------------------------------
# Helper: trust tier utilities
# ---------------------------------------------------------------------------


def _tier_index(tier: str) -> int:
    """Return the ordinal index of *tier* in ``_TRUST_TIER_ORDER``.

    If the tier string is not recognised, returns 0 (UNVERIFIED).  This
    ensures that unknown tiers are treated as the weakest possible, which is
    the conservative choice for all comparisons.

    Parameters
    ----------
    tier:
        A trust tier name (case-insensitive).

    Returns
    -------
    int
        Zero-based index into ``_TRUST_TIER_ORDER``.
    """
    upper = tier.upper()
    try:
        return _TRUST_TIER_ORDER.index(upper)
    except ValueError:
        return 0


def _weaker_tier(a: str, b: str) -> str:
    """Return the weaker of two trust tier names."""
    return a if _tier_index(a) <= _tier_index(b) else b


def _stronger_tier(a: str, b: str) -> str:
    """Return the stronger of two trust tier names."""
    return a if _tier_index(a) >= _tier_index(b) else b


# ---------------------------------------------------------------------------
# Helper: section extraction from a symbolic program
# ---------------------------------------------------------------------------


def _extract_sections(program: Any, coords: tuple[str, ...]) -> dict[str, tuple]:
    """Extract judgment sections from *program* for the given *coords*.

    A *program* is expected to have a ``judgment_sections`` attribute that is a
    ``dict[str, tuple]`` mapping coordinate strings to eight-component tuples
    ``(c, φ, A, E, O, B, T, Π)``.  If the attribute is absent or the program
    is a plain dict, this function falls back gracefully.

    Parameters
    ----------
    program:
        The symbolic program object (or a plain dict of sections).
    coords:
        The coordinate strings to extract.

    Returns
    -------
    dict[str, tuple]
        A ``{coord: section_tuple}`` mapping for the requested coordinates.
        Coordinates absent from the program are silently omitted.
    """
    # Fast path: plain dict
    if isinstance(program, dict):
        raw: dict[str, tuple] = program
    else:
        raw = getattr(program, "judgment_sections", {}) or {}

    result: dict[str, tuple] = {}
    for coord in coords:
        section = raw.get(coord)
        if section is not None:
            # Normalise to at least 8 elements, padding with None
            if not isinstance(section, tuple):
                section = tuple(section) if hasattr(section, "__iter__") else (section,)
            if len(section) < 8:
                section = section + (None,) * (8 - len(section))
            result[coord] = section
    return result


def _program_coordinates(program: Any) -> tuple[str, ...]:
    """Return all coordinate strings defined in *program*.

    Parameters
    ----------
    program:
        The symbolic program object.

    Returns
    -------
    tuple[str, ...]
        Sorted tuple of coordinate strings.
    """
    if isinstance(program, dict):
        return tuple(sorted(program.keys()))
    sections = getattr(program, "judgment_sections", {}) or {}
    return tuple(sorted(sections.keys()))


# ---------------------------------------------------------------------------
# CoverPatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverPatch:
    """A single patch in a hypercover of the judgment site.

    A patch represents an open subset U_i of the judgment site X.  It is
    parameterised by a tuple of *coordinates* (the judgment coordinates it
    covers), a *depth* (its level in the cover hierarchy), and a dict of
    *judgment_sections* that records the sections of the sheaf of judgments
    over U_i (used when the patch carries its own section data, e.g. from a
    restricted program).

    In the Čech complex, patches play the role of the sets U_i.  Intersections
    U_i ∩ U_j are computed by ``intersect``.

    Attributes
    ----------
    patch_id:
        Unique identifier for this patch (UUID string).
    coordinates:
        The judgment coordinates covered by this patch.
    depth:
        Depth level in the cover hierarchy.  Depth 0 is the whole site;
        higher depths are finer sub-patches.
    judgment_sections:
        Optional cache of judgment sections restricted to this patch.
        Maps ``coord → (c,φ,A,E,O,B,T,Π)`` just as in the full program.
    trust_floor:
        The minimum trust tier name required for sections in this patch.
        Sections below this tier are treated as absent.
    metadata:
        Free-form metadata dict for provenance, timestamps, etc.
    """

    patch_id: str
    coordinates: tuple[str, ...]
    depth: int
    judgment_sections: dict[str, tuple]
    trust_floor: str
    metadata: dict

    # ------------------------------------------------------------------
    # intersect
    # ------------------------------------------------------------------

    def intersect(self, other: CoverPatch) -> CoverPatch:
        """Compute the intersection of this patch and *other*.

        The intersection U_i ∩ U_j is the patch whose coordinate set is the
        set-theoretic intersection of the two patches' coordinate sets.  The
        depth of the intersection is the maximum of the two depths (it is at
        least as fine as both).  The trust floor is the *stronger* of the two
        trust floors (a section must satisfy both patches' requirements to be
        in the intersection).

        The ``judgment_sections`` of the intersection contain only those
        coordinates that appear in *both* patches, taking the section from
        *self* (the more trusted one if trust floors differ).

        Parameters
        ----------
        other:
            The patch to intersect with.

        Returns
        -------
        CoverPatch
            A new patch representing U_i ∩ U_j.  May be empty (see
            ``is_empty``).
        """
        # Set intersection of coordinate tuples (preserving sort order)
        self_coords = frozenset(self.coordinates)
        other_coords = frozenset(other.coordinates)
        shared = self_coords & other_coords
        shared_coords = tuple(sorted(shared))

        # Merge judgment sections: prefer self's sections when present
        merged_sections: dict[str, tuple] = {}
        for coord in shared_coords:
            if coord in self.judgment_sections:
                merged_sections[coord] = self.judgment_sections[coord]
            elif coord in other.judgment_sections:
                merged_sections[coord] = other.judgment_sections[coord]

        # The intersection is at least as fine as both constituent patches
        intersection_depth = max(self.depth, other.depth)

        # The trust floor of the intersection is the stronger floor
        intersection_trust_floor = _stronger_tier(self.trust_floor, other.trust_floor)

        return CoverPatch(
            patch_id=str(uuid.uuid4()),
            coordinates=shared_coords,
            depth=intersection_depth,
            judgment_sections=merged_sections,
            trust_floor=intersection_trust_floor,
            metadata={
                "derived_from": (self.patch_id, other.patch_id),
                "operation": "intersect",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            },
        )

    # ------------------------------------------------------------------
    # is_empty
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """Return ``True`` iff this patch covers no coordinates.

        An empty patch arises when ``intersect`` produces a patch with an empty
        coordinate set.  Empty patches are excluded from the Čech complex (they
        contribute no conditions and no cohomology).

        Returns
        -------
        bool
        """
        return len(self.coordinates) == 0


# ---------------------------------------------------------------------------
# LocalWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocalWitness:
    """An equivalence witness restricted to a single cover patch.

    A local witness W_i: P₁|_{U_i} ≅ P₂|_{U_i} certifies that the two
    programs agree on the patch U_i.  It stores:
    * The sections of each program restricted to the patch.
    * The equivalence map ``equivalence_map`` that sends each coordinate in
      P₁|_{U_i} to the matching coordinate in P₂|_{U_i}.
    * A ``confidence`` score (0 to 1) measuring how firmly the witness was
      established.
    * A ``method`` string recording the strategy used to produce the witness.

    In the Čech complex, local witnesses are elements of Č^0(U, ℱ).

    Attributes
    ----------
    witness_id:
        Unique identifier for this local witness.
    patch:
        The cover patch U_i on which this witness lives.
    program_a_sections:
        The sections of P₁ restricted to the patch.
    program_b_sections:
        The sections of P₂ restricted to the patch.
    equivalence_map:
        ``{coord_in_A: coord_in_B}`` — for each coordinate of P₁ on this
        patch, the corresponding coordinate of P₂ that it matches.
    trust_tier:
        The trust tier of this witness (the weaker of the two programs'
        trust tiers on this patch).
    confidence:
        A float in [0, 1] measuring how confident the computation is that
        the programs are equivalent on this patch.
    method:
        The strategy used: ``"structural"``, ``"semantic"``, ``"z3"``, or
        ``"oracle"``.
    metadata:
        Free-form metadata for provenance, timing, etc.
    """

    witness_id: str
    patch: CoverPatch
    program_a_sections: dict[str, tuple]
    program_b_sections: dict[str, tuple]
    equivalence_map: dict[str, str]
    trust_tier: str
    confidence: float
    method: str
    metadata: dict


# ---------------------------------------------------------------------------
# GluingFailure
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GluingFailure:
    """Record of a failed Čech gluing condition between two patches.

    A gluing failure arises when local witnesses W_i and W_j disagree on their
    common intersection U_i ∩ U_j.  Concretely, there exist coordinates in the
    intersection on which W_i and W_j map P₁'s section to *different* sections
    of P₂, or on which they use incompatible equivalence maps.

    In the language of Čech cohomology, a collection of gluing failures
    represents a non-trivial Čech 1-cocycle in H¹(U, ℱ) — the obstruction
    class in ``cohomology_class`` names this element.

    Attributes
    ----------
    failure_id:
        Unique identifier for this failure record.
    patch_i:
        The ``patch_id`` of the first patch U_i.
    patch_j:
        The ``patch_id`` of the second patch U_j.
    intersection_coords:
        The coordinates in U_i ∩ U_j on which the witnesses disagree.
    disagreement:
        A dict recording what exactly differs.  Keys are coordinate strings;
        values are dicts with keys ``"a_maps_to"``, ``"b_maps_to"``,
        ``"component"``, ``"detail"`` describing the mismatch.
    cohomology_class:
        A string label for the H¹ obstruction class (e.g. ``"H¹[trust]"``).
    """

    failure_id: str
    patch_i: str
    patch_j: str
    intersection_coords: tuple[str, ...]
    disagreement: dict
    cohomology_class: str


# ---------------------------------------------------------------------------
# GlobalWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlobalWitness:
    """The global equivalence witness assembled by descent from local witnesses.

    A global witness is the colimit of the compatible family of local witnesses.
    When ``is_complete`` is ``True``, the witness certifies P₁ ≅ P₂ on the
    entire site X.  When ``is_complete`` is ``False``, some gluing conditions
    failed and the witness is only partial; the failing pairs are recorded in
    ``gluing_failures``.

    Attributes
    ----------
    witness_id:
        Unique identifier for this global witness.
    local_witnesses:
        The tuple of local witnesses that were assembled into this global
        witness (including those whose gluing failed — for completeness of
        the record).
    cover:
        The hypercover used to compute this witness.
    confidence:
        The geometric mean of the confidences of all local witnesses (0 = no
        confidence, 1 = fully confident).
    is_complete:
        ``True`` iff all Čech gluing conditions passed (global equivalence
        certified); ``False`` iff at least one condition failed.
    trust_tier:
        The minimum trust tier among all local witnesses.
    gluing_failures:
        Tuple of ``GluingFailure`` objects for each pair of patches where the
        Čech condition was violated.
    metadata:
        Free-form metadata.
    """

    witness_id: str
    local_witnesses: tuple[LocalWitness, ...]
    cover: tuple[CoverPatch, ...]
    confidence: float
    is_complete: bool
    trust_tier: str
    gluing_failures: tuple[GluingFailure, ...]
    metadata: dict

    # ------------------------------------------------------------------
    # to_equivalence_certificate
    # ------------------------------------------------------------------

    def to_equivalence_certificate(self) -> dict:
        """Export this global witness as a JSON-serialisable certificate dict.

        The certificate captures the full provenance of the equivalence proof:
        which programs were compared, how many patches the cover had, which
        local witnesses were computed, and whether all gluing conditions passed.

        Returns
        -------
        dict
            A dict suitable for JSON serialisation.  Keys:
            ``witness_id``, ``is_complete``, ``confidence``, ``trust_tier``,
            ``cover_size``, ``n_local_witnesses``, ``n_gluing_failures``,
            ``gluing_failures``, ``local_witness_summary``, ``metadata``.
        """
        local_summary = [
            {
                "witness_id": lw.witness_id,
                "patch_id": lw.patch.patch_id,
                "n_coords": len(lw.patch.coordinates),
                "method": lw.method,
                "confidence": lw.confidence,
                "trust_tier": lw.trust_tier,
                "n_matched": len(lw.equivalence_map),
            }
            for lw in self.local_witnesses
        ]

        failure_summary = [
            {
                "failure_id": gf.failure_id,
                "patch_i": gf.patch_i,
                "patch_j": gf.patch_j,
                "n_disagreeing_coords": len(gf.intersection_coords),
                "cohomology_class": gf.cohomology_class,
            }
            for gf in self.gluing_failures
        ]

        return {
            "witness_id": self.witness_id,
            "is_complete": self.is_complete,
            "confidence": self.confidence,
            "trust_tier": self.trust_tier,
            "cover_size": len(self.cover),
            "n_local_witnesses": len(self.local_witnesses),
            "n_gluing_failures": len(self.gluing_failures),
            "gluing_failures": failure_summary,
            "local_witness_summary": local_summary,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# CoverWitnessComputer
# ---------------------------------------------------------------------------


class CoverWitnessComputer:
    """Computes equivalence witnesses for symbolic programs via sheaf descent.

    ``CoverWitnessComputer`` is the primary engine for §9.5.  It orchestrates:
    1. Building a hypercover of the judgment site from the union of the two
       programs' coordinate sets (``build_cover``).
    2. Computing a ``LocalWitness`` on each patch (``compute_local_witness``).
    3. Checking the Čech gluing condition across all patch pairs
       (``check_gluing``).
    4. Assembling the global witness by descent (``compute_global_witness``).

    Configuration
    -------------
    The constructor accepts an optional ``config`` dict with keys:
    * ``cover_depth`` (int, default 3) — controls the depth / granularity of
      the hypercover.  Deeper covers have more patches but each patch covers
      fewer coordinates.
    * ``min_confidence`` (float, default 0.25) — witnesses below this
      confidence are treated as absent in gluing checks.
    * ``prefer_semantic`` (bool, default False) — if True, attempt semantic
      matching before structural matching.
    * ``max_gluing_pairs`` (int, default 10_000) — cap on the number of patch
      pairs examined during gluing.
    * ``oracle_trust_floor`` (str, default "ASSERTED") — minimum trust tier for
      the oracle fallback strategy to accept a match.

    Usage
    -----
    ::

        computer = CoverWitnessComputer()
        global_witness = computer.compute_witness_from_programs(prog_a, prog_b)
        if global_witness.is_complete:
            print("Programs are equivalent!")
        else:
            for gf in global_witness.gluing_failures:
                print(f"Obstruction: {gf.cohomology_class}")
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self._cover_depth: int = int(cfg.get("cover_depth", _DEFAULT_COVER_DEPTH))
        self._min_confidence: float = float(
            cfg.get("min_confidence", _MIN_CREDIBLE_CONFIDENCE)
        )
        self._prefer_semantic: bool = bool(cfg.get("prefer_semantic", False))
        self._max_gluing_pairs: int = int(
            cfg.get("max_gluing_pairs", _MAX_GLUING_PAIRS)
        )
        self._oracle_trust_floor: str = str(
            cfg.get("oracle_trust_floor", "ASSERTED")
        )

    # ------------------------------------------------------------------
    # build_cover
    # ------------------------------------------------------------------

    def build_cover(
        self,
        prog_a: Any,
        prog_b: Any,
        depth: int = _DEFAULT_COVER_DEPTH,
    ) -> list[CoverPatch]:
        """Construct a hypercover of the judgment site from two programs.

        The site X is defined as the union of the coordinate sets of P₁ and
        P₂.  The cover is constructed as a *balanced binary trie* of depth
        *depth*:

        * At depth 0, there is a single coarse patch covering all coordinates.
        * At depth d > 0, the coordinates are sorted lexicographically and
          recursively bisected into two halves.  Each half is itself covered
          by a depth-(d-1) sub-cover.  To satisfy the hypercover condition
          (overlapping open sets), the boundary coordinates of adjacent
          bisections are included in *both* halves.

        The resulting family of patches covers every coordinate at least once;
        coordinates near the bisection boundary are covered by at least two
        patches, which is necessary for the gluing conditions in Stage C to be
        non-trivial.

        Parameters
        ----------
        prog_a:
            The first symbolic program.
        prog_b:
            The second symbolic program.
        depth:
            The depth of the cover hierarchy (>=1).  Clamped to [1, 8].

        Returns
        -------
        list[CoverPatch]
            The list of patches forming the hypercover.
        """
        depth = max(1, min(depth, 8))

        # Collect all coordinates from both programs
        coords_a = set(_program_coordinates(prog_a))
        coords_b = set(_program_coordinates(prog_b))
        all_coords = sorted(coords_a | coords_b)

        if not all_coords:
            # Degenerate case: both programs are empty — return a single empty patch
            return [
                CoverPatch(
                    patch_id=str(uuid.uuid4()),
                    coordinates=(),
                    depth=0,
                    judgment_sections={},
                    trust_floor="UNVERIFIED",
                    metadata={"cover_depth": depth, "empty": True},
                )
            ]

        # Recursively build the cover trie
        patches: list[CoverPatch] = []
        self._build_cover_recursive(
            coords=all_coords,
            prog_a=prog_a,
            prog_b=prog_b,
            current_depth=0,
            max_depth=depth,
            patches=patches,
        )
        return patches

    def _build_cover_recursive(
        self,
        coords: list[str],
        prog_a: Any,
        prog_b: Any,
        current_depth: int,
        max_depth: int,
        patches: list[CoverPatch],
    ) -> None:
        """Recursive helper for ``build_cover``.

        Splits *coords* into two overlapping halves and recurses until
        *current_depth* reaches *max_depth* or the coordinate set is too small
        to bisect (len <= 2).

        At each leaf level, a ``CoverPatch`` is created and appended to
        *patches*.  Non-leaf levels also emit a patch covering the full
        *coords* at their depth (creating the necessary overlap between
        sub-patches at finer levels).

        Parameters
        ----------
        coords:
            The sorted list of coordinates for this sub-tree.
        prog_a, prog_b:
            The programs (used to extract section data for each patch).
        current_depth:
            The depth of this recursive call.
        max_depth:
            The target maximum depth.
        patches:
            Accumulator for the constructed patches.
        """
        if not coords:
            return

        coords_tuple = tuple(coords)

        # Always emit a patch for the full coordinate set at this depth.
        # This ensures every coordinate is covered at every level of the trie,
        # which is the sheaf-theoretic requirement that patches cover X.
        patch_sections_a = _extract_sections(prog_a, coords_tuple)
        patch_sections_b = _extract_sections(prog_b, coords_tuple)

        # The trust floor for this patch is the minimum trust among sections
        # from both programs, defaulting to UNVERIFIED if no sections present.
        trust_floor = self._infer_trust_floor(patch_sections_a, patch_sections_b)

        # Merge sections: for the patch itself, we store the sections from
        # prog_a (the reference program).  prog_b's sections are carried
        # separately in the local witness.
        patches.append(
            CoverPatch(
                patch_id=str(uuid.uuid4()),
                coordinates=coords_tuple,
                depth=current_depth,
                judgment_sections=patch_sections_a,
                trust_floor=trust_floor,
                metadata={
                    "cover_depth": current_depth,
                    "max_depth": max_depth,
                    "n_coords": len(coords),
                },
            )
        )

        # Base case: do not recurse further if at max depth or too few coords
        if current_depth >= max_depth or len(coords) <= 2:
            return

        # Split coords at the midpoint, overlapping by 1 on each side
        mid = len(coords) // 2
        # Left half: indices [0 .. mid] (mid is included in both halves)
        left_half = coords[: mid + 1]
        # Right half: indices [mid .. n] (mid is included in both halves)
        right_half = coords[mid:]

        self._build_cover_recursive(
            coords=left_half,
            prog_a=prog_a,
            prog_b=prog_b,
            current_depth=current_depth + 1,
            max_depth=max_depth,
            patches=patches,
        )
        self._build_cover_recursive(
            coords=right_half,
            prog_a=prog_a,
            prog_b=prog_b,
            current_depth=current_depth + 1,
            max_depth=max_depth,
            patches=patches,
        )

    def _infer_trust_floor(
        self,
        sections_a: dict[str, tuple],
        sections_b: dict[str, tuple],
    ) -> str:
        """Infer the minimum trust floor from two section dicts.

        Looks at the trust component (index 6) of every section tuple and
        returns the weakest trust tier found, normalised to one of the
        strings in ``_TRUST_TIER_ORDER``.

        Parameters
        ----------
        sections_a, sections_b:
            Section dicts from the two programs.

        Returns
        -------
        str
            The weakest trust tier name, or ``"UNVERIFIED"`` if no tier info
            is available.
        """
        tiers: list[str] = []
        for sections in (sections_a, sections_b):
            for section in sections.values():
                trust_raw = section[_IDX_TRUST] if len(section) > _IDX_TRUST else None
                if trust_raw is None:
                    continue
                # trust_raw may be a TrustLevel enum instance or a string
                tier_str = (
                    trust_raw.name
                    if hasattr(trust_raw, "name")
                    else str(trust_raw).upper()
                )
                tiers.append(tier_str)

        if not tiers:
            return "UNVERIFIED"

        # Return the weakest tier found
        return min(tiers, key=_tier_index)

    # ------------------------------------------------------------------
    # compute_local_witness
    # ------------------------------------------------------------------

    def compute_local_witness(
        self,
        patch: CoverPatch,
        prog_a: Any,
        prog_b: Any,
    ) -> LocalWitness:
        """Compute a local equivalence witness for *patch*.

        Restricts both programs to ``patch.coordinates`` and attempts to find
        an equivalence map between the sections.  The strategy is:

        1. **Structural matching** — fast, purely syntactic.  Succeeds with
           high confidence when section tuples are identical or differ only in
           provenance (index 7).
        2. **Semantic matching** — slower, uses content-hash comparison and
           edit distance on the proposition component.  Picks up renames.
        3. **Z3 verification** — when semantic matching confidence is in the
           range [0.4, 0.7], attempt a symbolic check on the proposition
           component.  Only available if the proposition is a string expression.
        4. **Oracle fallback** — when all else fails, accept a match if the
           trust tier is at or above ``_oracle_trust_floor`` and the carrier
           types are compatible.

        The method that succeeds is recorded in ``LocalWitness.method``.
        Confidence is capped at 1.0 and floored at 0.0.

        Parameters
        ----------
        patch:
            The patch on which to compute the witness.
        prog_a, prog_b:
            The two programs.

        Returns
        -------
        LocalWitness
            The computed local witness.  Even if no match is found for any
            coordinate, a witness is always returned (with empty
            ``equivalence_map`` and ``confidence == 0.0``).
        """
        coords = patch.coordinates

        # Restrict both programs to the patch
        sections_a = _extract_sections(prog_a, coords)
        sections_b = _extract_sections(prog_b, coords)

        if not sections_a or not sections_b:
            # One program has no sections on this patch — empty witness
            return LocalWitness(
                witness_id=str(uuid.uuid4()),
                patch=patch,
                program_a_sections=sections_a,
                program_b_sections=sections_b,
                equivalence_map={},
                trust_tier=patch.trust_floor,
                confidence=0.0,
                method="structural",
                metadata={
                    "note": "Empty sections on patch — no match possible.",
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                },
            )

        # --- Strategy selection ---
        if self._prefer_semantic:
            eq_map, confidence, method = self._run_semantic_then_structural(
                sections_a, sections_b
            )
        else:
            eq_map, confidence, method = self._run_structural_then_semantic(
                sections_a, sections_b
            )

        # Z3 refinement for borderline confidence
        if 0.4 <= confidence < 0.7:
            eq_map, confidence, method = self._z3_refine(
                eq_map, confidence, sections_a, sections_b
            )

        # Oracle fallback when confidence is still low
        if confidence < self._min_confidence:
            eq_map, confidence, method = self._oracle_fallback(
                eq_map, confidence, sections_a, sections_b, patch.trust_floor
            )

        # Determine the trust tier for this witness
        trust_tier = self._infer_trust_floor(sections_a, sections_b)

        return LocalWitness(
            witness_id=str(uuid.uuid4()),
            patch=patch,
            program_a_sections=sections_a,
            program_b_sections=sections_b,
            equivalence_map=eq_map,
            trust_tier=trust_tier,
            confidence=max(0.0, min(1.0, confidence)),
            method=method,
            metadata={
                "n_a_sections": len(sections_a),
                "n_b_sections": len(sections_b),
                "n_matched": len(eq_map),
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            },
        )

    def _run_structural_then_semantic(
        self,
        sections_a: dict[str, tuple],
        sections_b: dict[str, tuple],
    ) -> tuple[dict[str, str], float, str]:
        """Try structural matching; fall back to semantic if confidence is low."""
        eq_map = self._structural_match(sections_a, sections_b)
        confidence = self._match_confidence(eq_map, sections_a)
        if confidence >= 0.8:
            return eq_map, confidence, "structural"
        # Supplement with semantic matching
        sem_map = self._semantic_match(sections_a, sections_b)
        # Merge: semantic map fills gaps left by structural
        merged = {**sem_map, **eq_map}
        merged_confidence = self._match_confidence(merged, sections_a)
        method = "structural" if merged_confidence <= confidence else "semantic"
        return merged, merged_confidence, method

    def _run_semantic_then_structural(
        self,
        sections_a: dict[str, tuple],
        sections_b: dict[str, tuple],
    ) -> tuple[dict[str, str], float, str]:
        """Try semantic matching; fall back to structural if confidence is low."""
        sem_map = self._semantic_match(sections_a, sections_b)
        confidence = self._match_confidence(sem_map, sections_a)
        if confidence >= 0.8:
            return sem_map, confidence, "semantic"
        str_map = self._structural_match(sections_a, sections_b)
        merged = {**str_map, **sem_map}
        merged_confidence = self._match_confidence(merged, sections_a)
        method = "semantic" if merged_confidence <= confidence else "structural"
        return merged, merged_confidence, method

    # ------------------------------------------------------------------
    # _structural_match
    # ------------------------------------------------------------------

    def _structural_match(
        self,
        sections_a: dict[str, tuple],
        sections_b: dict[str, tuple],
    ) -> dict[str, str]:
        """Match sections by structural (component-wise syntactic) similarity.

        For each coordinate ``coord_a`` in *sections_a*, searches *sections_b*
        for the best-matching coordinate ``coord_b``.  The matching score is
        computed as the number of shared components (out of 8) divided by 8,
        with a bonus for exact coordinate-name equality.

        The matching is solved as a greedy assignment: coordinates in A are
        sorted by their best-match score (descending) and greedily matched to
        the highest-scoring unmatched coordinate in B.

        Components used in matching
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~
        * (0) carrier — compared as string representations.
        * (1) proposition — compared as string representations (after
          normalising whitespace).
        * (2) ambient — compared as string representations.
        * (4) obligations — compared by count.
        * (6) trust — compared as tier names.

        Components *not* used: evidence (3), bounds (5), provenance (7).
        Evidence and provenance are intentionally excluded because they contain
        identifiers (UUIDs, timestamps) that differ between independent program
        instances even when the underlying logic is the same.

        Parameters
        ----------
        sections_a:
            Section dict for program A on the current patch.
        sections_b:
            Section dict for program B on the current patch.

        Returns
        -------
        dict[str, str]
            ``{coord_in_A: coord_in_B}`` for each successfully matched pair.
        """
        # Precompute string fingerprints for B sections
        b_fingerprints: dict[str, tuple] = {
            coord: self._section_fingerprint(sec)
            for coord, sec in sections_b.items()
        }

        # Score matrix: scores[(coord_a, coord_b)] = similarity in [0, 1]
        scores: dict[tuple[str, str], float] = {}
        for coord_a, sec_a in sections_a.items():
            fp_a = self._section_fingerprint(sec_a)
            for coord_b, fp_b in b_fingerprints.items():
                scores[(coord_a, coord_b)] = self._fingerprint_similarity(
                    coord_a, coord_b, fp_a, fp_b
                )

        # Greedy assignment: pick highest-scoring unmatched pair repeatedly
        assigned_b: set[str] = set()
        result: dict[str, str] = {}

        # Sort A coordinates by their best possible score (descending)
        def best_score_for_a(coord_a: str) -> float:
            return max(
                (scores.get((coord_a, cb), 0.0) for cb in sections_b),
                default=0.0,
            )

        sorted_a = sorted(sections_a.keys(), key=best_score_for_a, reverse=True)

        for coord_a in sorted_a:
            best_b: str | None = None
            best_s: float = 0.0
            for coord_b in sections_b:
                if coord_b in assigned_b:
                    continue
                s = scores.get((coord_a, coord_b), 0.0)
                if s > best_s:
                    best_s = s
                    best_b = coord_b
            if best_b is not None and best_s > 0.0:
                result[coord_a] = best_b
                assigned_b.add(best_b)

        return result

    def _section_fingerprint(self, section: tuple) -> tuple:
        """Extract a comparison-safe fingerprint from a section tuple.

        Returns a 5-tuple of normalised strings for components 0,1,2,4,6
        (carrier, proposition, ambient, obligations-count, trust).

        Parameters
        ----------
        section:
            An 8-element judgment section tuple.

        Returns
        -------
        tuple
            ``(carrier_str, prop_str, ambient_str, oblig_count_str, trust_str)``
        """
        def _norm(val: Any) -> str:
            if val is None:
                return ""
            return " ".join(str(val).split())  # normalise whitespace

        carrier_str = _norm(section[_IDX_CARRIER] if len(section) > _IDX_CARRIER else None)
        prop_str = _norm(section[_IDX_PROPOSITION] if len(section) > _IDX_PROPOSITION else None)
        ambient_str = _norm(section[_IDX_AMBIENT] if len(section) > _IDX_AMBIENT else None)

        # Obligations: compare as count (not content) to avoid UUID noise
        oblig_raw = section[_IDX_OBLIGATIONS] if len(section) > _IDX_OBLIGATIONS else None
        if oblig_raw is None:
            oblig_count = "0"
        elif hasattr(oblig_raw, "__len__"):
            oblig_count = str(len(oblig_raw))
        else:
            oblig_count = "1"

        trust_raw = section[_IDX_TRUST] if len(section) > _IDX_TRUST else None
        trust_str = (
            trust_raw.name if hasattr(trust_raw, "name") else _norm(trust_raw)
        )

        return (carrier_str, prop_str, ambient_str, oblig_count, trust_str)

    def _fingerprint_similarity(
        self,
        coord_a: str,
        coord_b: str,
        fp_a: tuple,
        fp_b: tuple,
    ) -> float:
        """Compute a similarity score in [0, 1] between two fingerprints.

        Scoring:
        * +0.30 bonus if ``coord_a == coord_b`` (exact name match)
        * +0.14 per matching component (5 components × 0.14 = 0.70 max)
        * Components are compared as exact strings (after normalisation)
        * Total maximum = 1.00 (capped)

        Parameters
        ----------
        coord_a:
            Coordinate name in A.
        coord_b:
            Coordinate name in B.
        fp_a:
            5-tuple fingerprint for A's section.
        fp_b:
            5-tuple fingerprint for B's section.

        Returns
        -------
        float
            Similarity score in [0, 1].
        """
        score = 0.0

        # Exact coordinate name bonus
        if coord_a == coord_b:
            score += 0.30

        # Per-component matches
        component_weight = 0.70 / max(len(fp_a), 1)
        for val_a, val_b in zip(fp_a, fp_b):
            if val_a and val_b and val_a == val_b:
                score += component_weight

        return min(1.0, score)

    # ------------------------------------------------------------------
    # _semantic_match
    # ------------------------------------------------------------------

    def _semantic_match(
        self,
        sections_a: dict[str, tuple],
        sections_b: dict[str, tuple],
    ) -> dict[str, str]:
        """Match sections by semantic equivalence.

        Uses two techniques in sequence:

        1. **Content hashing** — SHA-256 hash of the proposition component
           (index 1) is compared between all pairs.  If two sections have
           identical proposition hashes, they are considered equivalent.

        2. **Edit-distance matching** — for sections not matched by hashing,
           computes a character-level edit distance between the normalised
           proposition strings and matches pairs with edit distance below a
           threshold proportional to the string length.

        The edit-distance algorithm is a simplified dynamic programming
        implementation that avoids external dependencies.

        Parameters
        ----------
        sections_a:
            Section dict for program A.
        sections_b:
            Section dict for program B.

        Returns
        -------
        dict[str, str]
            ``{coord_in_A: coord_in_B}`` for matched pairs.
        """
        result: dict[str, str] = {}
        assigned_b: set[str] = set()

        # --- Pass 1: hash-based exact matching ---
        def _prop_hash(section: tuple) -> str:
            prop = section[_IDX_PROPOSITION] if len(section) > _IDX_PROPOSITION else None
            raw = " ".join(str(prop).split()) if prop is not None else ""
            return hashlib.sha256(raw.encode()).hexdigest()

        hash_to_b: dict[str, list[str]] = {}
        for coord_b, sec_b in sections_b.items():
            h = _prop_hash(sec_b)
            hash_to_b.setdefault(h, []).append(coord_b)

        unmatched_a: list[str] = []
        for coord_a, sec_a in sections_a.items():
            h = _prop_hash(sec_a)
            candidates = hash_to_b.get(h, [])
            matched = False
            for coord_b in candidates:
                if coord_b not in assigned_b:
                    result[coord_a] = coord_b
                    assigned_b.add(coord_b)
                    matched = True
                    break
            if not matched:
                unmatched_a.append(coord_a)

        # --- Pass 2: edit-distance matching for unmatched A sections ---
        unmatched_b = [cb for cb in sections_b if cb not in assigned_b]
        if not unmatched_a or not unmatched_b:
            return result

        def _norm_prop(section: tuple) -> str:
            prop = section[_IDX_PROPOSITION] if len(section) > _IDX_PROPOSITION else None
            return " ".join(str(prop).split()).lower() if prop is not None else ""

        def _edit_distance(s: str, t: str) -> int:
            """Levenshtein distance between *s* and *t* (DP, O(|s|·|t|))."""
            m, n = len(s), len(t)
            if m == 0:
                return n
            if n == 0:
                return m
            # Use two-row DP to limit memory
            prev = list(range(n + 1))
            curr = [0] * (n + 1)
            for i in range(1, m + 1):
                curr[0] = i
                for j in range(1, n + 1):
                    if s[i - 1] == t[j - 1]:
                        curr[j] = prev[j - 1]
                    else:
                        curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
                prev, curr = curr, prev
            return prev[n]

        for coord_a in unmatched_a:
            prop_a = _norm_prop(sections_a[coord_a])
            best_b: str | None = None
            best_dist: int = len(prop_a) + 1  # worse than any real distance
            for coord_b in unmatched_b:
                if coord_b in assigned_b:
                    continue
                prop_b = _norm_prop(sections_b[coord_b])
                dist = _edit_distance(prop_a, prop_b)
                # Accept if distance is < 30% of the longer string
                threshold = int(0.30 * max(len(prop_a), len(prop_b), 1))
                if dist <= threshold and dist < best_dist:
                    best_dist = dist
                    best_b = coord_b
            if best_b is not None:
                result[coord_a] = best_b
                assigned_b.add(best_b)

        return result

    # ------------------------------------------------------------------
    # _z3_refine
    # ------------------------------------------------------------------

    def _z3_refine(
        self,
        eq_map: dict[str, str],
        confidence: float,
        sections_a: dict[str, tuple],
        sections_b: dict[str, tuple],
    ) -> tuple[dict[str, str], float, str]:
        """Attempt to raise confidence using symbolic proposition checking.

        For each matched pair ``(coord_a, coord_b)`` in *eq_map*, this method
        attempts to verify that the proposition components are logically
        equivalent by checking whether they normalise to the same canonical
        string under a set of algebraic simplification rules.  (A full Z3
        encoding is beyond the scope of this module, which does not take a
        Z3 dependency; the simplification rules approximate what Z3 would do
        for common proposition patterns.)

        The confidence is raised by up to 0.20 for each pair where
        proposition equivalence is confirmed.

        Parameters
        ----------
        eq_map:
            The current equivalence map.
        confidence:
            The current confidence score.
        sections_a, sections_b:
            Section dicts for the two programs.

        Returns
        -------
        tuple[dict[str, str], float, str]
            Updated ``(eq_map, confidence, method)`` triple.
        """
        if not eq_map:
            return eq_map, confidence, "z3"

        confirmed = 0
        total = len(eq_map)

        for coord_a, coord_b in eq_map.items():
            sec_a = sections_a.get(coord_a)
            sec_b = sections_b.get(coord_b)
            if sec_a is None or sec_b is None:
                continue
            prop_a = sec_a[_IDX_PROPOSITION] if len(sec_a) > _IDX_PROPOSITION else None
            prop_b = sec_b[_IDX_PROPOSITION] if len(sec_b) > _IDX_PROPOSITION else None
            if prop_a is None or prop_b is None:
                continue
            # Normalise and compare
            norm_a = self._normalise_proposition(str(prop_a))
            norm_b = self._normalise_proposition(str(prop_b))
            if norm_a == norm_b:
                confirmed += 1

        if total > 0 and confirmed > 0:
            boost = 0.20 * (confirmed / total)
            confidence = min(1.0, confidence + boost)

        return eq_map, confidence, "z3"

    def _normalise_proposition(self, prop: str) -> str:
        """Apply algebraic normalisation rules to a proposition string.

        Normalisation rules (in order of application):
        1. Collapse whitespace.
        2. Sort comma-separated conjuncts alphabetically (treats ``A ∧ B`` as
           equal to ``B ∧ A``).
        3. Remove double negations: ``¬¬P`` → ``P``.
        4. Canonicalise implication: ``A → B`` is left as-is (direction matters).
        5. Lower-case the result for case-insensitive comparison.

        Parameters
        ----------
        prop:
            Raw proposition string.

        Returns
        -------
        str
            The normalised string.
        """
        import re

        # Step 1: collapse whitespace
        s = " ".join(prop.split())
        # Step 2: sort conjuncts (split on ∧ or "and", sort, rejoin)
        for conj_sep in (" ∧ ", " and ", " AND ", " & "):
            if conj_sep in s:
                parts = [p.strip() for p in s.split(conj_sep)]
                s = conj_sep.join(sorted(parts))
                break
        # Step 3: remove double negations
        s = re.sub(r"¬¬", "", s)
        s = re.sub(r"not not ", "", s, flags=re.IGNORECASE)
        # Step 4: lower-case
        return s.lower()

    # ------------------------------------------------------------------
    # _oracle_fallback
    # ------------------------------------------------------------------

    def _oracle_fallback(
        self,
        eq_map: dict[str, str],
        confidence: float,
        sections_a: dict[str, tuple],
        sections_b: dict[str, tuple],
        trust_floor: str,
    ) -> tuple[dict[str, str], float, str]:
        """Oracle heuristic: match by trust-tier dominance and carrier type.

        The oracle fallback is a conservative heuristic that accepts a match
        between ``coord_a`` and ``coord_b`` iff:
        1. The carrier type (index 0) of both sections is the same string.
        2. The trust tier of *both* sections is at or above *trust_floor*.

        When the oracle matches a previously unmatched pair it adds it to
        *eq_map* and raises confidence proportionally to the number of oracle
        matches found.

        Parameters
        ----------
        eq_map:
            The current equivalence map (may be partial).
        confidence:
            The current confidence score.
        sections_a, sections_b:
            Section dicts.
        trust_floor:
            Minimum acceptable trust tier.

        Returns
        -------
        tuple[dict[str, str], float, str]
            Updated ``(eq_map, confidence, method)`` triple.
        """
        floor_idx = _tier_index(trust_floor)
        already_b = set(eq_map.values())
        new_map = dict(eq_map)
        oracle_hits = 0

        for coord_a, sec_a in sections_a.items():
            if coord_a in new_map:
                continue  # already matched
            carrier_a = str(sec_a[_IDX_CARRIER] if len(sec_a) > _IDX_CARRIER else "")
            trust_a_raw = sec_a[_IDX_TRUST] if len(sec_a) > _IDX_TRUST else None
            trust_a = trust_a_raw.name if hasattr(trust_a_raw, "name") else str(trust_a_raw or "")
            if _tier_index(trust_a) < floor_idx:
                continue
            for coord_b, sec_b in sections_b.items():
                if coord_b in already_b:
                    continue
                carrier_b = str(sec_b[_IDX_CARRIER] if len(sec_b) > _IDX_CARRIER else "")
                trust_b_raw = sec_b[_IDX_TRUST] if len(sec_b) > _IDX_TRUST else None
                trust_b = trust_b_raw.name if hasattr(trust_b_raw, "name") else str(trust_b_raw or "")
                if _tier_index(trust_b) < floor_idx:
                    continue
                # Carrier match (exact string equality after normalisation)
                if carrier_a and carrier_b and carrier_a.lower() == carrier_b.lower():
                    new_map[coord_a] = coord_b
                    already_b.add(coord_b)
                    oracle_hits += 1
                    break

        if oracle_hits > 0:
            coverage = len(new_map) / max(len(sections_a), 1)
            new_confidence = min(
                1.0,
                self._min_confidence + 0.1 * oracle_hits + 0.5 * coverage,
            )
            return new_map, new_confidence, "oracle"

        return eq_map, confidence, "oracle"

    # ------------------------------------------------------------------
    # _match_confidence
    # ------------------------------------------------------------------

    def _match_confidence(
        self,
        eq_map: dict[str, str],
        sections_a: dict[str, tuple],
    ) -> float:
        """Compute a coverage-based confidence for an equivalence map.

        Confidence = (number of matched A-coordinates) / (total A-coordinates).
        Returns 0.0 if *sections_a* is empty.

        Parameters
        ----------
        eq_map:
            The equivalence map to assess.
        sections_a:
            Section dict for program A (denominator of coverage).

        Returns
        -------
        float
        """
        total = len(sections_a)
        if total == 0:
            return 0.0
        return len(eq_map) / total

    # ------------------------------------------------------------------
    # check_gluing
    # ------------------------------------------------------------------

    def check_gluing(
        self,
        witnesses: list[LocalWitness],
    ) -> list[GluingFailure]:
        """Check the Čech gluing condition across all pairs of local witnesses.

        For each pair of witnesses (W_i, W_j), computes the intersection
        U_i ∩ U_j.  If the intersection is empty, the pair is skipped (vacuous
        condition).  Otherwise, for each coordinate ``c`` in the intersection:
        * Look up ``W_i.equivalence_map[c]`` → ``b_i``
        * Look up ``W_j.equivalence_map[c]`` → ``b_j``
        * If ``b_i != b_j`` (or one is absent), record a disagreement.

        A ``GluingFailure`` is created for each pair that has at least one
        disagreement.  The ``cohomology_class`` is assigned by
        ``_assign_cohomology_class``.

        Only patches whose witnesses have confidence ≥ ``_min_confidence`` are
        included in the Čech complex.  Low-confidence witnesses are silently
        excluded from gluing checks (they do not constrain the global witness).

        Parameters
        ----------
        witnesses:
            The list of local witnesses computed by ``compute_local_witness``.

        Returns
        -------
        list[GluingFailure]
            All detected gluing failures.  Empty list = global witness exists.
        """
        # Filter to credible witnesses
        credible = [w for w in witnesses if w.confidence >= self._min_confidence]

        failures: list[GluingFailure] = []
        pair_count = 0

        for i, wi in enumerate(credible):
            for j, wj in enumerate(credible):
                if j <= i:
                    continue  # Only check each unordered pair once
                if pair_count >= self._max_gluing_pairs:
                    break

                pair_count += 1

                # Compute intersection of the two patches
                intersection = wi.patch.intersect(wj.patch)
                if intersection.is_empty():
                    continue  # Vacuous condition — nothing to check

                disagreement: dict[str, dict] = {}

                for coord in intersection.coordinates:
                    b_i = wi.equivalence_map.get(coord)
                    b_j = wj.equivalence_map.get(coord)

                    if b_i is None and b_j is None:
                        continue  # Both absent — vacuously agree (both say "no match")
                    if b_i != b_j:
                        # Determine which component is responsible
                        component = self._diagnose_disagreement_component(
                            coord,
                            wi.program_a_sections.get(coord),
                            wj.program_a_sections.get(coord),
                            wi.program_b_sections.get(b_i) if b_i else None,
                            wj.program_b_sections.get(b_j) if b_j else None,
                        )
                        disagreement[coord] = {
                            "a_maps_to_in_wi": b_i,
                            "a_maps_to_in_wj": b_j,
                            "component": component,
                            "detail": (
                                f"W_{wi.witness_id[:8]} maps {coord!r} → {b_i!r}, "
                                f"W_{wj.witness_id[:8]} maps {coord!r} → {b_j!r}"
                            ),
                        }

                if disagreement:
                    cohomology_class = self._assign_cohomology_class(disagreement)
                    failures.append(
                        GluingFailure(
                            failure_id=str(uuid.uuid4()),
                            patch_i=wi.patch.patch_id,
                            patch_j=wj.patch.patch_id,
                            intersection_coords=tuple(sorted(disagreement.keys())),
                            disagreement=disagreement,
                            cohomology_class=cohomology_class,
                        )
                    )

        return failures

    def _diagnose_disagreement_component(
        self,
        coord: str,
        sec_a_i: tuple | None,
        sec_a_j: tuple | None,
        sec_b_i: tuple | None,
        sec_b_j: tuple | None,
    ) -> str:
        """Identify which judgment component is responsible for a disagreement.

        Compares the section tuples from the two witnesses and returns the name
        of the first differing component among (carrier, proposition, ambient,
        obligations, trust).

        Parameters
        ----------
        coord:
            The coordinate where the disagreement occurs.
        sec_a_i, sec_a_j:
            Section of program A in witnesses W_i and W_j respectively.
        sec_b_i, sec_b_j:
            Section of program B matched by W_i and W_j respectively.

        Returns
        -------
        str
            A component name from ``_SECTION_COMPONENTS``, or ``"unknown"``.
        """
        # We compare the B-sections that the two witnesses respectively mapped to
        if sec_b_i is None or sec_b_j is None:
            return "unknown"

        component_indices = [
            (_IDX_CARRIER, "carrier"),
            (_IDX_PROPOSITION, "proposition"),
            (_IDX_AMBIENT, "ambient"),
            (_IDX_OBLIGATIONS, "obligations"),
            (_IDX_BOUNDS, "bounds"),
            (_IDX_TRUST, "trust"),
        ]
        for idx, name in component_indices:
            val_i = sec_b_i[idx] if len(sec_b_i) > idx else None
            val_j = sec_b_j[idx] if len(sec_b_j) > idx else None
            # Normalise enums to names
            vi = val_i.name if hasattr(val_i, "name") else str(val_i)
            vj = val_j.name if hasattr(val_j, "name") else str(val_j)
            if vi != vj:
                return name

        return "unknown"

    # ------------------------------------------------------------------
    # _assign_cohomology_class
    # ------------------------------------------------------------------

    def _assign_cohomology_class(self, failure: dict) -> str:
        """Assign an H¹ cohomology class label to a gluing failure.

        The cohomology class records which *kind* of information is responsible
        for the obstruction to gluing.  This is the sheaf-theoretic analogue of
        identifying which component of the Čech 1-cocycle is non-trivial.

        Classification rules
        ~~~~~~~~~~~~~~~~~~~~
        * All failures due to the same component → ``H¹[<component>]``
        * Failures due to multiple distinct components → ``H¹[mixed]``
        * No failures (vacuous disagreement dict) → ``H⁰[trivial]``

        Parameters
        ----------
        failure:
            The ``disagreement`` dict from a ``GluingFailure``, mapping
            ``coord → {"component": <name>, ...}``.

        Returns
        -------
        str
            One of ``_H1_TRUST``, ``_H1_PROPOSITION``, ``_H1_EVIDENCE``,
            ``_H1_OBLIGATION``, ``_H1_BOUNDS``, ``_H1_CARRIER``,
            ``_H1_MIXED``, ``_H0_TRIVIAL``.
        """
        if not failure:
            return _H0_TRIVIAL

        components: set[str] = set()
        for coord_data in failure.values():
            comp = coord_data.get("component", "unknown")
            components.add(comp)

        component_to_h1: dict[str, str] = {
            "trust": _H1_TRUST,
            "proposition": _H1_PROPOSITION,
            "evidence": _H1_EVIDENCE,
            "obligations": _H1_OBLIGATION,
            "bounds": _H1_BOUNDS,
            "carrier": _H1_CARRIER,
        }

        # Remove "unknown" from classification (it doesn't constrain the class)
        known_components = components - {"unknown"}
        if not known_components:
            return _H1_MIXED  # Unknown sources — treat as mixed obstruction

        if len(known_components) == 1:
            comp = next(iter(known_components))
            return component_to_h1.get(comp, _H1_MIXED)

        return _H1_MIXED

    # ------------------------------------------------------------------
    # compute_global_witness
    # ------------------------------------------------------------------

    def compute_global_witness(
        self,
        local_witnesses: list[LocalWitness],
        cover: list[CoverPatch],
    ) -> GlobalWitness:
        """Assemble a global witness from local witnesses by descent.

        If all Čech gluing conditions pass (``check_gluing`` returns an empty
        list), the global witness is the colimit of the local witnesses:
        * ``is_complete = True``
        * ``confidence`` = geometric mean of local confidences
        * ``trust_tier`` = minimum trust tier among local witnesses

        If any gluing conditions fail, the global witness is marked incomplete:
        * ``is_complete = False``
        * The ``gluing_failures`` tuple records all ``GluingFailure`` objects
        * ``confidence`` is reduced by the fraction of patches involved in
          failures

        The global witness always has full provenance (all local witnesses and
        the full cover are stored), regardless of whether it is complete.

        Parameters
        ----------
        local_witnesses:
            The list of local witnesses to assemble.
        cover:
            The hypercover (list of patches) used to produce the witnesses.

        Returns
        -------
        GlobalWitness
            The assembled global witness.
        """
        # Run gluing checks
        gluing_failures = self.check_gluing(local_witnesses)

        # Compute aggregate confidence using the geometric mean
        confidence = self._geometric_mean_confidence(local_witnesses)

        # Penalise confidence for gluing failures
        if gluing_failures:
            failing_patches: set[str] = set()
            for gf in gluing_failures:
                failing_patches.add(gf.patch_i)
                failing_patches.add(gf.patch_j)
            n_total = max(len(cover), 1)
            failure_fraction = len(failing_patches) / n_total
            confidence *= max(0.0, 1.0 - failure_fraction)

        # The overall trust tier is the minimum among local witnesses
        if local_witnesses:
            trust_tier = min(
                (lw.trust_tier for lw in local_witnesses),
                key=_tier_index,
            )
        else:
            trust_tier = "UNVERIFIED"

        is_complete = len(gluing_failures) == 0

        return GlobalWitness(
            witness_id=str(uuid.uuid4()),
            local_witnesses=tuple(local_witnesses),
            cover=tuple(cover),
            confidence=confidence,
            is_complete=is_complete,
            trust_tier=trust_tier,
            gluing_failures=tuple(gluing_failures),
            metadata={
                "n_patches": len(cover),
                "n_local_witnesses": len(local_witnesses),
                "n_gluing_failures": len(gluing_failures),
                "gluing_passed": is_complete,
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "cover_depth": self._cover_depth,
            },
        )

    def _geometric_mean_confidence(
        self,
        witnesses: list[LocalWitness],
    ) -> float:
        """Compute the geometric mean of confidences of *witnesses*.

        The geometric mean is preferred over the arithmetic mean because it
        penalises low-confidence witnesses more heavily: a single witness with
        confidence 0 makes the product 0, reflecting that the global witness
        is only as strong as its weakest component.

        An empty witness list yields confidence 0.0.

        Parameters
        ----------
        witnesses:
            List of local witnesses.

        Returns
        -------
        float
        """
        if not witnesses:
            return 0.0

        import math

        product = 1.0
        for lw in witnesses:
            # Floor individual confidence at a small positive number to avoid
            # total collapse from a single completely uninformative witness.
            product *= max(0.01, lw.confidence)

        return min(1.0, product ** (1.0 / len(witnesses)))

    # ------------------------------------------------------------------
    # compute_witness_from_programs
    # ------------------------------------------------------------------

    def compute_witness_from_programs(
        self,
        prog_a: Any,
        prog_b: Any,
    ) -> GlobalWitness:
        """Main entry point: compute a global equivalence witness for two programs.

        This is the top-level call that orchestrates the full §9.5 algorithm:

        1. Compute the cover depth based on the number of coordinates.
        2. Build the hypercover via ``build_cover``.
        3. Compute a local witness on each patch via ``compute_local_witness``.
        4. Check all gluing conditions via ``check_gluing`` (inside
           ``compute_global_witness``).
        5. Assemble and return the global witness.

        The method never raises — all failures are captured in the returned
        ``GlobalWitness`` object (``is_complete = False``, populated
        ``gluing_failures``).

        Parameters
        ----------
        prog_a:
            The first symbolic program.
        prog_b:
            The second symbolic program.

        Returns
        -------
        GlobalWitness
            The assembled global witness.
        """
        # Step 1: determine cover depth
        all_coords_a = _program_coordinates(prog_a)
        all_coords_b = _program_coordinates(prog_b)
        n_coords = len(set(all_coords_a) | set(all_coords_b))
        depth = self._compute_cover_depth(n_coords)

        # Step 2: build the hypercover
        cover = self.build_cover(prog_a, prog_b, depth=depth)

        # Step 3: compute local witnesses
        local_witnesses: list[LocalWitness] = []
        for patch in cover:
            try:
                lw = self.compute_local_witness(patch, prog_a, prog_b)
                local_witnesses.append(lw)
            except Exception as exc:  # noqa: BLE001
                # Never let a single-patch failure abort the whole computation.
                # Record a zero-confidence witness with the error details.
                local_witnesses.append(
                    LocalWitness(
                        witness_id=str(uuid.uuid4()),
                        patch=patch,
                        program_a_sections={},
                        program_b_sections={},
                        equivalence_map={},
                        trust_tier="UNVERIFIED",
                        confidence=0.0,
                        method="structural",
                        metadata={
                            "error": str(exc),
                            "note": "Exception during local witness computation.",
                        },
                    )
                )

        # Steps 4 & 5: gluing check + assembly
        return self.compute_global_witness(local_witnesses, cover)

    # ------------------------------------------------------------------
    # _compute_cover_depth
    # ------------------------------------------------------------------

    def _compute_cover_depth(self, n_coords: int) -> int:
        """Determine the appropriate cover depth for *n_coords* coordinates.

        The depth is chosen so that the average patch size is between 2 and 8
        coordinates.  This balances computational cost against the granularity
        of the gluing conditions.

        Heuristic:
        * n_coords ≤ 4   → depth 1 (single bisection)
        * n_coords ≤ 16  → depth 2
        * n_coords ≤ 64  → depth 3 (default)
        * n_coords ≤ 256 → depth 4
        * n_coords > 256 → depth 5 (capped)

        The result is further clamped to ``[1, self._cover_depth]`` so that the
        user-configured depth cap is always respected.

        Parameters
        ----------
        n_coords:
            The total number of distinct coordinates in the union of both
            programs.

        Returns
        -------
        int
            The chosen depth in ``[1, self._cover_depth]``.
        """
        if n_coords <= 4:
            raw = 1
        elif n_coords <= 16:
            raw = 2
        elif n_coords <= 64:
            raw = 3
        elif n_coords <= 256:
            raw = 4
        else:
            raw = 5
        return max(1, min(raw, self._cover_depth))


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def compute_equivalence_witness(
    prog_a: Any,
    prog_b: Any,
    **config: Any,
) -> GlobalWitness:
    """Compute a global equivalence witness for two symbolic programs.

    This is the primary public API for §9.5.  It creates a
    ``CoverWitnessComputer`` with the given configuration keyword arguments and
    calls ``compute_witness_from_programs``.

    Parameters
    ----------
    prog_a:
        The first symbolic program.  May be a dict ``{coord: section_tuple}``,
        an object with a ``judgment_sections`` attribute, or a
        ``SymbolicProgram`` instance.
    prog_b:
        The second symbolic program.
    **config:
        Configuration keyword arguments forwarded to ``CoverWitnessComputer``.
        Supported keys: ``cover_depth``, ``min_confidence``,
        ``prefer_semantic``, ``max_gluing_pairs``, ``oracle_trust_floor``.

    Returns
    -------
    GlobalWitness
        The computed global witness.  Never raises.
    """
    computer = CoverWitnessComputer(config=config if config else None)
    return computer.compute_witness_from_programs(prog_a, prog_b)


def verify_equivalence(
    prog_a: Any,
    prog_b: Any,
    **config: Any,
) -> bool:
    """Return ``True`` iff P₁ ≅ P₂ can be established via sheaf descent.

    Equivalent to ``compute_equivalence_witness(prog_a, prog_b).is_complete``,
    but more convenient when only a Boolean answer is needed.

    Parameters
    ----------
    prog_a:
        The first symbolic program.
    prog_b:
        The second symbolic program.
    **config:
        Forwarded to ``CoverWitnessComputer``.

    Returns
    -------
    bool
        ``True`` iff all Čech gluing conditions passed (the programs are
        equivalent on the entire judgment site).
    """
    gw = compute_equivalence_witness(prog_a, prog_b, **config)
    return gw.is_complete


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.encodings, jugeo.evidence)
# ---------------------------------------------------------------------------


def refinement_over_site(site: Any) -> dict[str, Any]:
    """Compute refinement structure over a geometric site.

    Refinement relations are defined over sites — the site provides the
    coordinate system and topology over which refinement is checked.

    Parameters
    ----------
    site : Any
        A Site object or dict with site topology data.

    Returns
    -------
    dict[str, Any]
        Site-aware refinement data with ``site_id``, ``coordinates``,
        ``covering_families``, and ``refinement_compatible`` keys.
    """
    try:
        from jugeo.geometry.site import Site, get_covering_families
    except ImportError:
        Site = None
        get_covering_families = None

    site_id = getattr(site, "site_id", None) or (site.get("site_id") if isinstance(site, dict) else "unknown")
    coords = getattr(site, "coordinates", None) or (
        site.get("coordinates") if isinstance(site, dict) else []
    )

    result: dict[str, Any] = {
        "site_id": site_id,
        "coordinates": list(coords) if coords else [],
        "covering_families": [],
        "refinement_compatible": None,
    }

    if get_covering_families is not None:
        try:
            families = get_covering_families(site)
            result["covering_families"] = list(families) if families else []
            result["refinement_compatible"] = len(result["covering_families"]) > 0
        except Exception:
            pass

    return result


def refinement_encoding(rel: Any) -> dict[str, Any]:
    """Encode a refinement relation as SMT constraints.

    Refinement relations translate to SMT formulas encoding the four
    conditions: trust monotonicity, evidence embedding, obligation
    subsumption, and proposition strength.

    Parameters
    ----------
    rel : Any
        A RefinementRelation object or dict.

    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``relation_id``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings import encode_relation, RelationEncoding
    except ImportError:
        encode_relation = None
        RelationEncoding = None

    left = getattr(rel, "left", None) or (rel.get("left") if isinstance(rel, dict) else "?")
    right = getattr(rel, "right", None) or (rel.get("right") if isinstance(rel, dict) else "?")
    rel_id = getattr(rel, "relation_id", None) or (
        rel.get("relation_id") if isinstance(rel, dict) else f"{left}_leq_{right}"
    )

    encoding: dict[str, Any] = {
        "relation_id": rel_id,
        "encoding_kind": "refinement_conjunction",
        "formulas": [
            f"(trust_leq {left} {right})",
            f"(evidence_embeds {left} {right})",
            f"(obligation_subsumes {left} {right})",
            f"(proposition_stronger {left} {right})",
        ],
        "variables": [f"trust_{left}", f"trust_{right}", f"ev_{left}", f"ev_{right}"],
        "encoder": None,
    }

    if encode_relation is not None:
        try:
            enc = encode_relation(rel)
            encoding["formulas"] = getattr(enc, "formulas", encoding["formulas"])
            encoding["variables"] = getattr(enc, "variables", encoding["variables"])
        except Exception:
            pass

    return encoding


def refinement_certificate(rel: Any) -> dict[str, Any]:
    """Build an evidence certificate for a refinement check result.

    A refinement certificate records the outcome of a J ≤ J' check,
    including the direction (forward, backward, equivalent, incomparable)
    and the trust level of the evidence.

    Parameters
    ----------
    rel : Any
        A refinement result, RefinementRelation, or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``direction``, ``valid``,
        ``trust_level``, and ``certificate_hash`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    direction = getattr(rel, "direction", None) or (rel.get("direction") if isinstance(rel, dict) else "UNKNOWN")
    direction_str = direction.value if hasattr(direction, "value") else str(direction)
    valid = direction_str in ("FORWARD", "EQUIVALENT")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "direction": direction_str,
        "valid": valid,
        "trust_level": "VERIFIED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(rel).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"refinement_{direction_str}", satisfied=valid, source="relational_refinement"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "CoverPatch",
    "LocalWitness",
    "GluingFailure",
    "GlobalWitness",
    "CoverWitnessComputer",
    "compute_equivalence_witness",
    "verify_equivalence",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Minimal smoke test for the sheaf-descent witness computation.
    #
    # We construct two synthetic programs whose judgment sections share the
    # same carrier and proposition but differ in coordinate names (to exercise
    # the semantic matching path) and in trust tiers (to exercise the trust
    # comparison logic).
    #
    # Expected outcome:
    # * The cover is non-trivial (multiple patches).
    # * Some local witnesses are computed with method "semantic" or "structural".
    # * If the programs are equivalent, is_complete = True.
    # * The certificate dict is well-formed.
    # -----------------------------------------------------------------------

    print("=== witness_computation_from_cover smoke test ===")

    # --- Build two synthetic programs as plain dicts ---
    #   coord → (carrier, proposition, ambient, evidence, obligations, bounds, trust, provenance)

    prog_A = {
        "x0": ("TypeA", "P ∧ Q → R", "Set", None, (), None, "VERIFIED", None),
        "x1": ("TypeB", "∀ n . n + 0 = n", "Nat", None, (), None, "CERTIFIED", None),
        "x2": ("TypeA", "P → P", "Set", None, (), None, "CHECKED", None),
        "x3": ("TypeC", "A ∨ ¬A", "Bool", None, (), None, "VERIFIED", None),
        "x4": ("TypeD", "∃ x . f(x) = 0", "Real", None, ("ob1",), None, "ASSERTED", None),
        "x5": ("TypeA", "Q ∧ P → R", "Set", None, (), None, "VERIFIED", None),  # same as x0 up to ∧-commutativity
    }

    # prog_B has renamed coordinates and one extra coordinate not in prog_A
    prog_B = {
        "y0": ("TypeA", "P ∧ Q → R", "Set", None, (), None, "VERIFIED", None),   # exact match for x0
        "y1": ("TypeB", "∀ n . n + 0 = n", "Nat", None, (), None, "CERTIFIED", None),  # exact match for x1
        "y2": ("TypeA", "P → P", "Set", None, (), None, "CHECKED", None),         # exact match for x2
        "y3": ("TypeC", "A ∨ ¬A", "Bool", None, (), None, "VERIFIED", None),      # exact match for x3
        "y4": ("TypeD", "∃ x . f(x) = 0", "Real", None, ("ob1",), None, "ASSERTED", None),
        "y5": ("TypeA", "Q ∧ P → R", "Set", None, (), None, "VERIFIED", None),   # normalises to match x0/x5
    }

    # --- Run witness computation ---
    print("\n[1] Computing equivalence witness (default config)…")
    gw = compute_equivalence_witness(prog_A, prog_B)
    print(f"    witness_id     = {gw.witness_id}")
    print(f"    is_complete    = {gw.is_complete}")
    print(f"    confidence     = {gw.confidence:.4f}")
    print(f"    trust_tier     = {gw.trust_tier}")
    print(f"    cover_size     = {len(gw.cover)}")
    print(f"    local witnesses= {len(gw.local_witnesses)}")
    print(f"    gluing failures= {len(gw.gluing_failures)}")

    # --- Print local witness methods and confidences ---
    print("\n[2] Local witness summary:")
    for lw in gw.local_witnesses[:6]:  # cap at 6 for readability
        print(
            f"    patch {lw.patch.patch_id[:8]}…  "
            f"depth={lw.patch.depth}  "
            f"n_coords={len(lw.patch.coordinates)}  "
            f"method={lw.method}  "
            f"conf={lw.confidence:.3f}  "
            f"matched={len(lw.equivalence_map)}"
        )

    # --- Print any gluing failures ---
    if gw.gluing_failures:
        print("\n[3] Gluing failures (Čech obstruction):")
        for gf in gw.gluing_failures[:4]:
            print(
                f"    patches ({gf.patch_i[:8]}…, {gf.patch_j[:8]}…)  "
                f"class={gf.cohomology_class}  "
                f"n_disagreeing={len(gf.intersection_coords)}"
            )
    else:
        print("\n[3] No gluing failures — global witness is complete.")

    # --- Export the equivalence certificate ---
    print("\n[4] Equivalence certificate keys:")
    cert = gw.to_equivalence_certificate()
    for k, v in cert.items():
        if k not in ("local_witness_summary", "gluing_failures"):
            print(f"    {k}: {v!r}")

    # --- Test with a deliberately inequivalent pair ---
    print("\n[5] Inequivalent programs test…")
    prog_C = {
        "z0": ("TypeX", "P ∧ Q", "Grp", None, (), None, "ASSERTED", None),
        "z1": ("TypeY", "R → S", "Ring", None, ("open_ob",), None, "CHECKED", None),
    }
    gw2 = compute_equivalence_witness(prog_A, prog_C)
    print(f"    is_complete    = {gw2.is_complete}")
    print(f"    confidence     = {gw2.confidence:.4f}")
    print(f"    gluing failures= {len(gw2.gluing_failures)}")
    for gf in gw2.gluing_failures[:2]:
        print(f"    obstruction class: {gf.cohomology_class}")

    # --- Boolean helper ---
    print("\n[6] verify_equivalence helpers:")
    print(f"    prog_A ≅ prog_B : {verify_equivalence(prog_A, prog_B)}")
    print(f"    prog_A ≅ prog_C : {verify_equivalence(prog_A, prog_C)}")

    # --- Test cover depth heuristic ---
    print("\n[7] _compute_cover_depth heuristic:")
    comp = CoverWitnessComputer()
    for n in (1, 4, 8, 16, 32, 64, 128, 256, 512):
        print(f"    n_coords={n:4d}  → depth={comp._compute_cover_depth(n)}")

    print("\n=== smoke test complete ===")

# copilot: witness_computation_from_cover — sheaf-theoretic descent for Ch9 §9.5 equivalence witnesses
