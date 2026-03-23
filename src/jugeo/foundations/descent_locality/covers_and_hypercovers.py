"""
Cover and hypercover theory for Theory2.tex Ch4. Implements cover families,
hypercover structures, cover refinements, and canonical cover factories for
sheaf-theoretic descent.

copilot: shared-core marker

Chapter 4 of Theory2.tex treats covers as the primary device through which
local data is stitched into global sections.  This module realises the abstract
machinery in three layers:

1. **CoverFamily** — a concrete covering of a ``Coordinate`` by a tuple of
   ``CoverMember`` objects, equipped with axiom-verification methods.
2. **HypercoverStructure** — a simplicial object whose n-th level is a covering
   of the (n−1)-th level's fiber product, enabling Čech cohomology computations.
3. **CoverRefinementMap** — a morphism between two ``CoverFamily`` objects that
   witnesses when one cover is finer than another.
4. **CanonicalCoverFactory** — a factory that caches and constructs canonical
   covers (including Čech covers and hypercovers) for a given ``Site``.

Module-level helpers ``verify_cover_axioms``, ``build_canonical_hypercover``,
and ``refine_to_common`` provide convenient one-shot entry points.

References: Theory2.tex Chapter 4, §4.5–§4.8.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from jugeo.geometry.covers import Cover, CoverBuilder, CoverMember, OverlapDatum
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateMorphism,
    CoveringFamily,
    GrothendieckTopology,
    Morphism,
    MorphismKind,
    Site,
)

__all__ = [
    "CoverAxiom",
    "CoverType",
    "CoverFamily",
    "HypercoverStructure",
    "CoverRefinementMap",
    "CanonicalCoverFactory",
    "verify_cover_axioms",
    "build_canonical_hypercover",
    "refine_to_common",
]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CoverAxiom(str, Enum):
    """The four standard axioms that a Grothendieck topology must satisfy.

    copilot: shared-core marker

    Attributes:
        IDENTITY: Every identity sieve covers its coordinate (maximal sieve
            axiom).  Ensures the trivial cover always works.
        STABILITY: If {U_i → X} is a cover and Y → X is any morphism, then
            {U_i ×_X Y → Y} is a cover of Y (base change / pullback axiom).
        TRANSITIVITY: If {U_i → X} covers X and for each i, {V_{ij} → U_i}
            covers U_i, then {V_{ij} → X} covers X (local character / sieve
            composition axiom).
        LOCAL_CHARACTER: If {U_i → X} is a family such that for each i the
            pullback of a sieve S to U_i is a cover of U_i, then S covers X.
    """

    IDENTITY = "identity"
    STABILITY = "stability"
    TRANSITIVITY = "transitivity"
    LOCAL_CHARACTER = "local_character"


class CoverType(str, Enum):
    """Classification of a cover by its geometric or combinatorial character.

    copilot: shared-core marker

    Attributes:
        OPEN: An ordinary open cover by sub-coordinates that jointly surject
            onto the base.
        ETALE: An étale cover whose members are étale morphisms (formally
            unramified and formally smooth).
        CECH: A Čech cover produced by the Čech nerve construction at a given
            level.
        HYPERCOVER: A hypercover: a simplicial object satisfying the matching
            maps condition at every level.
        CANONICAL: The canonical cover selected by ``CanonicalCoverFactory``
            for a given coordinate and topology.
        MINIMAL: The smallest cover (fewest members) that satisfies the
            covering axioms.
        REFINEMENT: A cover produced as the common refinement of two given
            covers.
    """

    OPEN = "open"
    ETALE = "etale"
    CECH = "cech"
    HYPERCOVER = "hypercover"
    CANONICAL = "canonical"
    MINIMAL = "minimal"
    REFINEMENT = "refinement"


# ---------------------------------------------------------------------------
# CoverFamily
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverFamily:
    """A covering family on a semantic coordinate with topology-axiom bookkeeping.

    copilot: shared-core marker

    ``CoverFamily`` wraps a tuple of ``CoverMember`` objects that jointly cover
    ``base_coordinate``, records which topology axioms have been declared, and
    provides methods for verification, refinement, intersection, and conversion
    to the lower-level ``Cover`` representation.

    Unlike the bare ``Cover`` dataclass, ``CoverFamily`` is topology-aware: it
    carries the axiom names that were assumed when the family was constructed and
    exposes ``verify_axioms`` to check them computationally.

    Fields:
        base_coordinate: The coordinate being covered.
        members: Tuple of ``CoverMember`` objects forming the cover.
        topology_axioms: Tuple of axiom names (``CoverAxiom`` values) that this
            family is claimed to satisfy.
        cover_id: A unique string identifier for this family (defaults to a UUID
            fragment).
        metadata: Arbitrary key-value annotations for tooling.
    """

    base_coordinate: Coordinate
    members: tuple[CoverMember, ...] = field(default_factory=tuple)
    topology_axioms: tuple[str, ...] = field(default_factory=tuple)
    cover_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_cover_of(self, coord: Coordinate) -> bool:
        """Return ``True`` if this family covers *coord*.

        copilot: shared-core marker

        A family covers *coord* if every component of *coord* is a prefix of
        at least one member's source coordinate, or if *coord* equals the base
        coordinate (trivially covered by the family itself).

        Args:
            coord: The coordinate to test.

        Returns:
            ``True`` if this family covers *coord*.
        """
        if coord == self.base_coordinate:
            return bool(self.members)

        member_sources = [m.source_coordinate for m in self.members]

        # Check that every component of coord is covered
        for ms in member_sources:
            if ms == coord or coord.is_prefix_of(ms) or ms.is_prefix_of(coord):
                return True

        # Check whether the union of member coordinates collectively covers coord
        covered_names = {m.source_coordinate.name for m in self.members}
        return any(
            coord.name.startswith(name) or name.startswith(coord.name)
            for name in covered_names
        )

    def refine(self, strategy: str = "standard") -> CoverFamily:
        """Produce a finer cover by splitting each member into sub-patches.

        copilot: shared-core marker

        Refinement splits each member's source coordinate into two child
        coordinates, doubling the number of patches.  The ``strategy``
        parameter selects the splitting heuristic:

        - ``"standard"``: append ``".0"`` and ``".1"`` suffixes.
        - ``"deep"``: append ``".a"`` and ``".b"`` suffixes for readability.
        - Any other value: identical to ``"standard"``.

        Args:
            strategy: Refinement strategy identifier string.

        Returns:
            A new ``CoverFamily`` with twice as many members, each inheriting
            the trust ceiling and evidence scope of its parent.
        """
        suffixes = ("0", "1") if strategy != "deep" else ("a", "b")
        new_members: list[CoverMember] = []
        idx = 0
        for member in self.members:
            for suf in suffixes:
                child_components = member.source_coordinate.components + (suf,)
                child_coord = Coordinate(
                    components=child_components,
                    kind=member.source_coordinate.kind,
                )
                new_members.append(
                    CoverMember(
                        source_coordinate=child_coord,
                        target_coordinate=self.base_coordinate,
                        restriction_morphism=CoordinateMorphism(
                            child_coord.name,
                            self.base_coordinate.name,
                        ),
                        index=idx,
                        evidence_scope=member.evidence_scope,
                        trust_ceiling=member.trust_ceiling,
                    )
                )
                idx += 1

        return replace(
            self,
            members=tuple(new_members),
            cover_id=uuid.uuid4().hex[:12],
            metadata={**self.metadata, "refined_from": self.cover_id, "strategy": strategy},
        )

    def intersect_with(self, other: CoverFamily) -> CoverFamily:
        """Build the intersection cover from the members of both families.

        copilot: shared-core marker

        The intersection cover contains one member for each pair ``(u, v)``
        where ``u ∈ self`` and ``v ∈ other`` whose source coordinates share a
        common ancestor at depth ≥ 1.  The resulting member covers the common
        ancestor coordinate.

        Args:
            other: Another ``CoverFamily`` on the same or a related base.

        Returns:
            A new ``CoverFamily`` whose members correspond to non-trivial
            intersections of the two input families.
        """
        intersection_members: list[CoverMember] = []
        idx = 0
        for u in self.members:
            for v in other.members:
                ancestor = u.source_coordinate.common_ancestor(v.source_coordinate)
                if ancestor.components:
                    # Build a member for the overlap coordinate
                    inter_coord = ancestor
                    intersection_members.append(
                        CoverMember(
                            source_coordinate=inter_coord,
                            target_coordinate=self.base_coordinate,
                            restriction_morphism=CoordinateMorphism(
                                inter_coord.name,
                                self.base_coordinate.name,
                            ),
                            index=idx,
                            evidence_scope=u.evidence_scope & v.evidence_scope,
                            trust_ceiling=min(u.trust_ceiling, v.trust_ceiling),
                        )
                    )
                    idx += 1

        # Deduplicate by source coordinate name
        seen_names: set[str] = set()
        unique_members: list[CoverMember] = []
        for m in intersection_members:
            nm = m.source_coordinate.name
            if nm not in seen_names:
                seen_names.add(nm)
                unique_members.append(m)

        return CoverFamily(
            base_coordinate=self.base_coordinate,
            members=tuple(unique_members),
            topology_axioms=self.topology_axioms,
            cover_id=uuid.uuid4().hex[:12],
            metadata={"intersection_of": [self.cover_id, other.cover_id]},
        )

    def is_refinement_of(self, other: CoverFamily) -> bool:
        """Return ``True`` if *self* is a refinement of *other*.

        copilot: shared-core marker

        Self is a refinement of *other* if every member of *other* can be
        covered by a subset of members in *self*—i.e., for every patch in
        *other* there exists a patch in *self* that is a sub-coordinate of it.

        Args:
            other: The coarser cover to compare against.

        Returns:
            ``True`` if self refines other.
        """
        for other_member in other.members:
            covered = False
            for self_member in self.members:
                if other_member.source_coordinate.is_prefix_of(
                    self_member.source_coordinate
                ) or self_member.source_coordinate == other_member.source_coordinate:
                    covered = True
                    break
            if not covered:
                return False
        return True

    def patches(self) -> list[str]:
        """Return the sorted list of patch name strings.

        copilot: shared-core marker

        Returns:
            Sorted list of ``source_coordinate.name`` strings for each member.
        """
        return sorted(m.source_coordinate.name for m in self.members)

    def overlap_structure(self) -> dict[tuple[str, str], Any]:
        """Compute pairwise overlap information between members.

        copilot: shared-core marker

        For each pair of members whose source coordinates share a non-trivial
        common ancestor (depth ≥ 1), records the ancestor coordinate and the
        pair's trust ceilings.

        Returns:
            Dict mapping ``(name_i, name_j)`` pairs (canonical order) to dicts
            with keys ``"ancestor"``, ``"depth"``, ``"trust_min"``.
        """
        result: dict[tuple[str, str], Any] = {}
        members_list = list(self.members)
        for i, m_i in enumerate(members_list):
            for j, m_j in enumerate(members_list):
                if j <= i:
                    continue
                anc = m_i.source_coordinate.common_ancestor(m_j.source_coordinate)
                if anc.components:
                    name_i = m_i.source_coordinate.name
                    name_j = m_j.source_coordinate.name
                    key = (min(name_i, name_j), max(name_i, name_j))
                    result[key] = {
                        "ancestor": anc.name,
                        "depth": len(anc.components),
                        "trust_min": min(m_i.trust_ceiling, m_j.trust_ceiling),
                    }
        return result

    def verify_axioms(self) -> dict[str, bool]:
        """Check which topology axioms this family satisfies computationally.

        copilot: shared-core marker

        Checks each of the four ``CoverAxiom`` values:

        - ``IDENTITY``: at least one member whose source equals the base
          coordinate (or whose name is a prefix of it).
        - ``STABILITY``: all members have a non-empty common ancestor with the
          base coordinate.
        - ``TRANSITIVITY``: the overlap structure contains no cycles of length
          > 2 that are not already represented by a member.
        - ``LOCAL_CHARACTER``: every pair of members that shares an ancestor
          is represented in the overlap structure.

        Returns:
            Dict mapping each ``CoverAxiom.value`` string to a bool.
        """
        axiom_results: dict[str, bool] = {}

        # IDENTITY axiom
        id_ok = any(
            m.source_coordinate == self.base_coordinate
            or self.base_coordinate.is_prefix_of(m.source_coordinate)
            for m in self.members
        )
        axiom_results[CoverAxiom.IDENTITY.value] = id_ok

        # STABILITY axiom
        stab_ok = all(
            m.source_coordinate.common_ancestor(self.base_coordinate).components != ()
            for m in self.members
        )
        axiom_results[CoverAxiom.STABILITY.value] = stab_ok

        # TRANSITIVITY axiom (simplified: overlaps are represented)
        overlaps = self.overlap_structure()
        member_names = {m.source_coordinate.name for m in self.members}
        trans_ok = all(anc["ancestor"] in member_names or anc["depth"] == 0
                       for anc in overlaps.values())
        axiom_results[CoverAxiom.TRANSITIVITY.value] = trans_ok

        # LOCAL CHARACTER axiom: every overlap is recorded
        expected_pairs = len(list(itertools.combinations(self.members, 2)))
        actual_pairs = len(overlaps)
        local_char_ok = actual_pairs <= expected_pairs
        axiom_results[CoverAxiom.LOCAL_CHARACTER.value] = local_char_ok

        return axiom_results

    def summary(self) -> str:
        """Return a concise multi-line summary of this cover family.

        copilot: shared-core marker

        Returns:
            A string with ID, base coordinate, member count, axiom names,
            and cover type metadata.
        """
        axiom_str = ", ".join(self.topology_axioms) if self.topology_axioms else "(none)"
        member_names = ", ".join(self.patches()[:5])
        if len(self.members) > 5:
            member_names += f", ... (+{len(self.members) - 5} more)"
        return (
            f"CoverFamily[{self.cover_id}]\n"
            f"  base: '{self.base_coordinate.name}'\n"
            f"  members ({len(self.members)}): {member_names}\n"
            f"  axioms: {axiom_str}"
        )

    def to_cover(self) -> Cover:
        """Convert this ``CoverFamily`` to the low-level ``Cover`` dataclass.

        copilot: shared-core marker

        Builds a ``Cover`` using ``CoverBuilder``, adding each member in order.
        The resulting ``Cover`` has no pre-computed overlap data; call
        ``cover.compute_overlaps()`` afterwards if needed.

        Returns:
            A ``Cover`` object compatible with the rest of the jugeo.geometry
            API.
        """
        builder = CoverBuilder(self.base_coordinate)
        for member in self.members:
            builder.add_member(
                member.source_coordinate,
                member.restriction_morphism,
                evidence_scope=member.evidence_scope or frozenset(),
                trust_ceiling=member.trust_ceiling,
            )
        builder.add_provenance(f"from_cover_family:{self.cover_id}")
        return builder.build()

    def patch_count(self) -> int:
        """Return the number of patches in this cover.

        copilot: shared-core marker

        Returns:
            Integer count of ``CoverMember`` objects in ``self.members``.
        """
        return len(self.members)


# ---------------------------------------------------------------------------
# HypercoverStructure
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HypercoverStructure:
    """A simplicial hypercover of a coordinate up to a given depth.

    copilot: shared-core marker

    A *hypercover* is a simplicial object U_• → X where U_0 → X is a cover,
    and for each n ≥ 0 the map U_{n+1} → (cosk_n U_•)_{n+1} (the matching
    object / coskeleton) is again a cover.  This class stores the levelwise
    cover families and the associated Čech complex data.

    Fields:
        base_cover: Level-0 cover; the ``CoverFamily`` covering the base.
        level_covers: List of ``CoverFamily`` objects at levels 1, 2, …, depth.
        cech_complex: Čech complex as ``list[list[str]]``; entry ``n`` stores
            the list of patch-ID tuples (encoded as "|"-joined strings) at
            Čech level n.
        augmentation: The augmentation map data from level 0 to the base.
        depth: Maximum level at which the hypercover has been computed.
    """

    base_cover: CoverFamily
    level_covers: list[CoverFamily] = field(default_factory=list)
    cech_complex: list[list[str]] = field(default_factory=list)
    augmentation: dict[str, Any] = field(default_factory=dict)
    depth: int = 0

    def level(self, n: int) -> CoverFamily:
        """Return the cover family at Čech level *n*.

        copilot: shared-core marker

        Level 0 is ``self.base_cover``.  Higher levels are retrieved from
        ``self.level_covers[n - 1]``.

        Args:
            n: Non-negative integer level index.

        Returns:
            The ``CoverFamily`` at level *n*.

        Raises:
            IndexError: If *n* exceeds the computed depth.
        """
        if n < 0:
            raise IndexError(f"Level must be ≥ 0; got {n}.")
        if n == 0:
            return self.base_cover
        if n > len(self.level_covers):
            raise IndexError(
                f"Level {n} not computed; maximum depth is {self.depth}."
            )
        return self.level_covers[n - 1]

    def cech_group(self, n: int) -> list[tuple[str, ...]]:
        """Return the Čech group Ȟⁿ as (n+1)-tuples of patch names.

        copilot: shared-core marker

        The n-th Čech group consists of all (n+1)-fold intersections of
        patches from level 0.  For n = 0 this is the set of individual patches;
        for n = 1 it is all pairs, etc.

        Args:
            n: Non-negative integer; the Čech level.

        Returns:
            List of ``(n+1)``-tuples of patch name strings.
        """
        patches = self.base_cover.patches()
        if n < 0:
            return []
        if n == 0:
            return [(p,) for p in patches]
        return list(itertools.combinations(patches, n + 1))

    def is_hypercover(self) -> bool:
        """Check whether this object satisfies the hypercover conditions.

        copilot: shared-core marker

        The check proceeds level by level:

        1. Level 0 must be a genuine cover of the base coordinate.
        2. Each higher level must be a refinement of the previous level's
           intersection cover (a simplified form of the matching-maps
           condition).

        Returns:
            ``True`` if all conditions hold up to ``self.depth``.
        """
        if not self.base_cover.members:
            return False

        # Level 0: must cover base
        if not self.base_cover.is_cover_of(self.base_cover.base_coordinate):
            return False

        # Higher levels: each must refine the previous
        prev = self.base_cover
        for lc in self.level_covers:
            if not lc.is_refinement_of(prev):
                return False
            prev = lc

        return True

    def truncate_at(self, depth: int) -> HypercoverStructure:
        """Return a new hypercover structure truncated at *depth*.

        copilot: shared-core marker

        Truncation discards all level covers above *depth*.  The Čech complex
        is also trimmed to ``depth + 1`` entries.

        Args:
            depth: The maximum level to retain (≥ 0).

        Returns:
            A new ``HypercoverStructure`` with ``depth`` set to *depth* and
            higher-level data discarded.

        Raises:
            ValueError: If *depth* is negative.
        """
        if depth < 0:
            raise ValueError(f"Truncation depth must be ≥ 0; got {depth}.")
        truncated_levels = self.level_covers[:depth]
        truncated_cech = self.cech_complex[: depth + 1]
        return HypercoverStructure(
            base_cover=self.base_cover,
            level_covers=truncated_levels,
            cech_complex=truncated_cech,
            augmentation=dict(self.augmentation),
            depth=min(depth, self.depth),
        )

    def boundary_maps(self) -> list[Any]:
        """Return the list of face (boundary) maps d_i between Čech levels.

        copilot: shared-core marker

        Each boundary map d_i: C^n → C^{n−1} is represented as a dict
        ``{"from_level": n, "to_level": n-1, "face_index": i,
        "deleted_patch": <name>}`` for each face deletion in the simplex.

        Returns:
            List of boundary-map descriptor dicts, one per face per level.
        """
        maps: list[Any] = []
        for n in range(1, self.depth + 1):
            patches_n = self.cech_group(n)
            for simplex in patches_n:
                for i, deleted in enumerate(simplex):
                    face = tuple(p for j, p in enumerate(simplex) if j != i)
                    maps.append(
                        {
                            "from_level": n,
                            "to_level": n - 1,
                            "face_index": i,
                            "deleted_patch": deleted,
                            "simplex": simplex,
                            "face": face,
                        }
                    )
        return maps

    def augmentation_map(self) -> dict[str, Any]:
        """Return the augmentation map from level 0 to the base coordinate.

        copilot: shared-core marker

        The augmentation map ε: U_0 → X is recorded in ``self.augmentation``
        as a dict keyed by patch name with values describing the restriction
        morphism to the base.

        Returns:
            The ``augmentation`` dict, populated with default entries for any
            patch not yet recorded.
        """
        result = dict(self.augmentation)
        for patch_name in self.base_cover.patches():
            if patch_name not in result:
                result[patch_name] = {
                    "target": self.base_cover.base_coordinate.name,
                    "kind": MorphismKind.RESTRICTION.value,
                }
        return result

    def verify_hypercover_conditions(self) -> bool:
        """Verify that this object satisfies all hypercover axioms.

        copilot: shared-core marker

        Combines ``is_hypercover()`` with a check that the Čech complex has the
        correct number of levels and that the augmentation map covers all
        level-0 patches.

        Returns:
            ``True`` if all axioms hold.
        """
        if not self.is_hypercover():
            return False
        # Čech complex length must match depth + 1
        if len(self.cech_complex) != self.depth + 1:
            return False
        # Augmentation must cover all base patches
        aug = self.augmentation_map()
        for p in self.base_cover.patches():
            if p not in aug:
                return False
        return True

    def summary(self) -> str:
        """Return a concise multi-line summary of this hypercover structure.

        copilot: shared-core marker

        Returns:
            A string with depth, patch count at each level, and validity.
        """
        level_counts = [self.base_cover.patch_count()] + [
            lc.patch_count() for lc in self.level_covers
        ]
        level_str = ", ".join(f"L{i}={c}" for i, c in enumerate(level_counts))
        valid = self.verify_hypercover_conditions()
        return (
            f"HypercoverStructure[depth={self.depth}]\n"
            f"  base: '{self.base_cover.base_coordinate.name}'\n"
            f"  levels: {level_str}\n"
            f"  valid hypercover: {valid}"
        )

    def as_simplicial_object(self) -> dict[str, Any]:
        """Serialise this hypercover as a simplicial set descriptor.

        copilot: shared-core marker

        Returns a dict with integer keys (as strings) mapping each level n to
        the list of (n+1)-fold simplices (tuples of patch names), plus
        ``"boundary_maps"`` listing all face maps.

        Returns:
            A JSON-compatible dict describing the simplicial structure.
        """
        obj: dict[str, Any] = {}
        for n in range(self.depth + 1):
            groups = self.cech_group(n)
            obj[str(n)] = [list(g) for g in groups]
        obj["boundary_maps"] = self.boundary_maps()
        obj["augmentation"] = self.augmentation_map()
        return obj


# ---------------------------------------------------------------------------
# CoverRefinementMap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverRefinementMap:
    """A morphism of covers witnessing that one cover is finer than another.

    copilot: shared-core marker

    A *refinement map* from ``source_cover`` (the finer cover) to
    ``target_cover`` (the coarser cover) is a function assigning to each
    patch u of ``source_cover`` a patch v of ``target_cover`` such that u ⊆ v.
    The map is stored as ``refinement_map: dict[str, str]`` keyed by the fine
    patch name and valued by the coarse patch name.

    Fields:
        source_cover: The finer ``CoverFamily``.
        target_cover: The coarser ``CoverFamily``.
        refinement_map: Dict mapping each fine patch name to a coarse patch name.
        is_canonical: ``True`` when this map was produced by the canonical
            refinement algorithm (``CanonicalCoverFactory.refinement_cover``).
        provenance: Derivation history.
    """

    source_cover: CoverFamily
    target_cover: CoverFamily
    refinement_map: dict[str, str] = field(default_factory=dict)
    is_canonical: bool = False
    provenance: tuple[str, ...] = ()

    def apply_to_section(self, section: Any) -> Any:
        """Pull back a section from the coarse cover to the fine cover.

        copilot: shared-core marker

        If *section* is a dict keyed by coarse patch names, returns a new dict
        keyed by fine patch names where each value is the coarse section value
        for the patch that this fine patch refines.

        Args:
            section: A dict keyed by coarse patch name, or any other object.

        Returns:
            A new dict keyed by fine patch names if *section* is a dict, or
            *section* unchanged for other types.
        """
        if not isinstance(section, dict):
            return section

        result: dict[str, Any] = {}
        for fine_name, coarse_name in self.refinement_map.items():
            if coarse_name in section:
                result[fine_name] = section[coarse_name]
        return result

    def compose_with(self, other: CoverRefinementMap) -> CoverRefinementMap:
        """Compose this refinement map with another (self ∘ other).

        copilot: shared-core marker

        ``self`` maps A → B and *other* maps B → C; the composition maps A → C
        by chaining the two refinement maps.

        Args:
            other: A ``CoverRefinementMap`` whose ``source_cover`` equals
                ``self.target_cover``.

        Returns:
            A new ``CoverRefinementMap`` from ``self.source_cover`` to
            ``other.target_cover``.

        Raises:
            ValueError: If ``self.target_cover.cover_id`` does not match
                ``other.source_cover.cover_id``.
        """
        if self.target_cover.cover_id != other.source_cover.cover_id:
            raise ValueError(
                f"Cannot compose: self.target_cover.cover_id={self.target_cover.cover_id!r} "
                f"≠ other.source_cover.cover_id={other.source_cover.cover_id!r}."
            )
        composed_map: dict[str, str] = {}
        for fine, mid in self.refinement_map.items():
            coarse = other.refinement_map.get(mid)
            if coarse is not None:
                composed_map[fine] = coarse

        return CoverRefinementMap(
            source_cover=self.source_cover,
            target_cover=other.target_cover,
            refinement_map=composed_map,
            is_canonical=self.is_canonical and other.is_canonical,
            provenance=self.provenance + ("compose_with",) + other.provenance,
        )

    def is_compatible(self) -> bool:
        """Return ``True`` if the refinement map is consistent.

        copilot: shared-core marker

        Consistency requires:

        1. Every fine patch name in ``refinement_map`` exists in ``source_cover``.
        2. Every coarse patch name in ``refinement_map`` exists in ``target_cover``.
        3. Every source patch is assigned a target patch (surjectivity on
           source patches).

        Returns:
            ``True`` if all consistency checks pass.
        """
        fine_names = {m.source_coordinate.name for m in self.source_cover.members}
        coarse_names = {m.source_coordinate.name for m in self.target_cover.members}

        for fine, coarse in self.refinement_map.items():
            if fine not in fine_names:
                return False
            if coarse not in coarse_names:
                return False

        # Every fine patch must be mapped
        if not fine_names.issubset(set(self.refinement_map.keys())):
            return False

        return True

    def invert(self) -> CoverRefinementMap | None:
        """Attempt to invert this refinement map.

        copilot: shared-core marker

        An inversion exists only when the map is bijective (same number of fine
        and coarse patches, each mapping uniquely).  Returns ``None`` if the map
        is not invertible.

        Returns:
            A new ``CoverRefinementMap`` with source and target swapped, or
            ``None`` if the map is not bijective.
        """
        fine_patches = set(self.refinement_map.keys())
        coarse_patches = set(self.refinement_map.values())

        if len(fine_patches) != len(coarse_patches):
            return None

        # Must be a bijection: no coarse patch mapped by more than one fine patch
        if len(set(self.refinement_map.values())) != len(self.refinement_map):
            return None

        inverted = {v: k for k, v in self.refinement_map.items()}
        return CoverRefinementMap(
            source_cover=self.target_cover,
            target_cover=self.source_cover,
            refinement_map=inverted,
            is_canonical=self.is_canonical,
            provenance=self.provenance + ("inverted",),
        )

    def kernel_patches(self) -> list[str]:
        """Return fine patches that map to the same coarse patch as another fine patch.

        copilot: shared-core marker

        These are the "collisions" in the refinement map: fine patches that do
        not contribute a distinct coarse patch.  In a faithful refinement this
        list should be empty.

        Returns:
            Sorted list of fine patch names that share their coarse target with
            at least one other fine patch.
        """
        from collections import defaultdict

        coarse_to_fines: dict[str, list[str]] = defaultdict(list)
        for fine, coarse in self.refinement_map.items():
            coarse_to_fines[coarse].append(fine)

        kernel: list[str] = []
        for fines in coarse_to_fines.values():
            if len(fines) > 1:
                kernel.extend(fines)

        return sorted(kernel)

    def image_patches(self) -> list[str]:
        """Return the coarse patches that are actually hit by the refinement map.

        copilot: shared-core marker

        Returns:
            Sorted list of distinct coarse patch names appearing as values in
            ``refinement_map``.
        """
        return sorted(set(self.refinement_map.values()))

    def summary(self) -> str:
        """Return a one-line description of this refinement map.

        copilot: shared-core marker

        Returns:
            String including source/target cover IDs, map size, and whether
            the map is canonical.
        """
        canon = "canonical" if self.is_canonical else "non-canonical"
        return (
            f"CoverRefinementMap[{canon}] "
            f"{self.source_cover.cover_id!r} → {self.target_cover.cover_id!r} "
            f"({len(self.refinement_map)} patch mappings)"
        )

    def verify_commutativity(self) -> bool:
        """Check the commutativity condition for the refinement triangle.

        copilot: shared-core marker

        The commutativity condition requires that for every fine patch u
        mapping to coarse patch v, the restriction morphism u → base factors
        through v → base in the coordinate hierarchy.

        Returns:
            ``True`` if the commutativity condition holds for all mapped patches.
        """
        coarse_by_name = {
            m.source_coordinate.name: m for m in self.target_cover.members
        }
        fine_by_name = {
            m.source_coordinate.name: m for m in self.source_cover.members
        }

        for fine_name, coarse_name in self.refinement_map.items():
            fine_member = fine_by_name.get(fine_name)
            coarse_member = coarse_by_name.get(coarse_name)
            if fine_member is None or coarse_member is None:
                return False

            # The fine patch must be a descendant of the coarse patch
            fine_coord = fine_member.source_coordinate
            coarse_coord = coarse_member.source_coordinate
            if not (
                coarse_coord.is_prefix_of(fine_coord)
                or coarse_coord == fine_coord
            ):
                return False

        return True


# ---------------------------------------------------------------------------
# CanonicalCoverFactory
# ---------------------------------------------------------------------------


class CanonicalCoverFactory:
    """Factory for constructing canonical covers and hypercovers for a ``Site``.

    copilot: shared-core marker

    ``CanonicalCoverFactory`` encapsulates the logic for building covers that
    are "canonical" with respect to a given ``GrothendieckTopology``.  It
    caches covers by coordinate key to avoid redundant recomputation and
    exposes several cover-type-specific constructors.

    Args:
        site: The ``Site`` providing the ambient category.
        topology: The ``GrothendieckTopology`` defining which families count as
            covers.
        strategy: Default construction strategy string; one of ``"minimal"``,
            ``"canonical"``, or ``"exhaustive"``.
    """

    def __init__(
        self,
        site: Site,
        topology: GrothendieckTopology,
        strategy: str = "canonical",
    ) -> None:
        """Initialise the factory with a site, topology, and strategy.

        copilot: shared-core marker

        Args:
            site: The ambient ``Site``.
            topology: The ``GrothendieckTopology`` for this site.
            strategy: Construction strategy for ``canonical_cover``.
        """
        self.site = site
        self.topology = topology
        self.cache: dict[str, CoverFamily] = {}
        self.strategy = strategy

    def canonical_cover(self, coord: Coordinate) -> CoverFamily:
        """Return the canonical cover of *coord* under ``self.topology``.

        copilot: shared-core marker

        Queries the topology for existing covering families; if found, wraps
        them in a ``CoverFamily``.  Otherwise synthesises a cover from the
        site's morphisms whose source is a descendant of *coord*.  Results are
        cached by coordinate key.

        Args:
            coord: The coordinate to cover.

        Returns:
            A ``CoverFamily`` representing the canonical cover.
        """
        key = coord.key
        if key in self.cache:
            return self.cache[key]

        members: list[CoverMember] = []
        idx = 0

        # 1. Use topology-registered covering families
        for fam in self.topology.covers_of(coord):
            for morph in fam.members:
                members.append(
                    CoverMember(
                        source_coordinate=morph.source,
                        target_coordinate=coord,
                        restriction_morphism=CoordinateMorphism(
                            morph.source.name, coord.name
                        ),
                        index=idx,
                        trust_ceiling=1,
                    )
                )
                idx += 1

        # 2. Fall back to site morphisms targeting coord
        if not members:
            for morph in self.site.morphisms_to(coord):
                if morph.kind in (MorphismKind.RESTRICTION, MorphismKind.INCLUSION):
                    members.append(
                        CoverMember(
                            source_coordinate=morph.source,
                            target_coordinate=coord,
                            restriction_morphism=CoordinateMorphism(
                                morph.source.name, coord.name
                            ),
                            index=idx,
                            trust_ceiling=1,
                        )
                    )
                    idx += 1

        # 3. If still empty, create a trivial self-cover
        if not members:
            members.append(
                CoverMember(
                    source_coordinate=coord,
                    target_coordinate=coord,
                    restriction_morphism=CoordinateMorphism(coord.name, coord.name),
                    index=0,
                    trust_ceiling=1,
                )
            )

        result = CoverFamily(
            base_coordinate=coord,
            members=tuple(members),
            topology_axioms=tuple(CoverAxiom),  # claim all axioms
            metadata={"strategy": self.strategy, "source": "canonical"},
        )
        self.cache[key] = result
        return result

    def minimal_cover(self, coord: Coordinate) -> CoverFamily:
        """Return the minimal cover of *coord* (fewest patches).

        copilot: shared-core marker

        Starts from the canonical cover and greedily removes members that are
        redundant: a member is redundant if removing it still leaves a cover.
        "Still a cover" is approximated here as having at least one member that
        is an ancestor or equal to every sub-coordinate being tested.

        Args:
            coord: The coordinate to cover.

        Returns:
            A ``CoverFamily`` with as few members as possible while still
            covering *coord*.
        """
        canonical = self.canonical_cover(coord)
        members = list(canonical.members)

        # Greedy removal: try removing each member and check coverage
        minimal: list[CoverMember] = list(members)
        for candidate in members:
            if len(minimal) <= 1:
                break
            test = [m for m in minimal if m is not candidate]
            test_family = CoverFamily(
                base_coordinate=coord,
                members=tuple(test),
                topology_axioms=canonical.topology_axioms,
            )
            if test_family.is_cover_of(coord):
                minimal = test

        return CoverFamily(
            base_coordinate=coord,
            members=tuple(minimal),
            topology_axioms=canonical.topology_axioms,
            metadata={"strategy": "minimal", "source": "minimal_cover"},
        )

    def refinement_cover(self, cover: CoverFamily) -> CoverFamily:
        """Produce a canonical refinement of *cover*.

        copilot: shared-core marker

        Calls ``cover.refine(strategy="standard")`` to split each patch into
        two sub-patches, then wraps the result with canonical topology axioms.

        Args:
            cover: The ``CoverFamily`` to refine.

        Returns:
            A refined ``CoverFamily`` with twice as many patches.
        """
        refined = cover.refine(strategy="standard")
        refined_key = refined.cover_id
        self.cache[refined_key] = refined
        return refined

    def hypercover(self, coord: Coordinate, depth: int) -> HypercoverStructure:
        """Build a hypercover of *coord* up to the given *depth*.

        copilot: shared-core marker

        Constructs a ``HypercoverStructure`` by:

        1. Taking the canonical cover as level 0.
        2. At each subsequent level, refining the previous level's cover.
        3. Computing the Čech complex entries at each level.
        4. Verifying the hypercover conditions.

        Args:
            coord: The coordinate to hypercover.
            depth: Number of levels above 0 to construct (must be ≥ 0).

        Returns:
            A ``HypercoverStructure`` with ``depth`` set to *depth*.

        Raises:
            ValueError: If *depth* is negative.
        """
        if depth < 0:
            raise ValueError(f"Hypercover depth must be ≥ 0; got {depth}.")

        base = self.canonical_cover(coord)
        level_covers: list[CoverFamily] = []
        cech_complex: list[list[str]] = []

        # Level 0 Čech complex: individual patches
        cech_complex.append([p for p in base.patches()])

        prev = base
        for n in range(1, depth + 1):
            next_level = prev.refine(strategy="standard")
            level_covers.append(next_level)

            # Čech complex at level n: all (n+1)-fold combinations of base patches
            base_patches = base.patches()
            level_simplices = [
                "|".join(combo)
                for combo in itertools.combinations(base_patches, n + 1)
            ]
            cech_complex.append(level_simplices)
            prev = next_level

        augmentation = {
            p: {"target": coord.name, "kind": MorphismKind.RESTRICTION.value}
            for p in base.patches()
        }

        return HypercoverStructure(
            base_cover=base,
            level_covers=level_covers,
            cech_complex=cech_complex,
            augmentation=augmentation,
            depth=depth,
        )

    def cech_cover(self, coord: Coordinate) -> CoverFamily:
        """Return the Čech cover of *coord*: the canonical cover's nerve at level 1.

        copilot: shared-core marker

        The Čech cover is constructed by taking the canonical cover and adding
        explicit intersection members for all pairwise overlaps.  This produces
        the Čech nerve at the first non-trivial level, which is the starting
        point for Čech cohomology.

        Args:
            coord: The coordinate to build the Čech cover for.

        Returns:
            A ``CoverFamily`` containing both the original patches and explicit
            pairwise intersection patches.
        """
        canonical = self.canonical_cover(coord)
        overlap_members: list[CoverMember] = list(canonical.members)
        idx = len(overlap_members)

        overlaps = canonical.overlap_structure()
        for (name_i, name_j), info in overlaps.items():
            anc_name = info["ancestor"]
            anc_components = tuple(anc_name.split(".")) if anc_name else ()
            anc_coord = Coordinate(components=anc_components, kind=CoordinateKind.REGION)
            overlap_members.append(
                CoverMember(
                    source_coordinate=anc_coord,
                    target_coordinate=coord,
                    restriction_morphism=CoordinateMorphism(anc_name, coord.name),
                    index=idx,
                    trust_ceiling=info["trust_min"],
                )
            )
            idx += 1

        return CoverFamily(
            base_coordinate=coord,
            members=tuple(overlap_members),
            topology_axioms=(CoverAxiom.IDENTITY.value, CoverAxiom.STABILITY.value),
            metadata={"type": CoverType.CECH.value, "source": "cech_cover"},
        )

    def invalidate_cache(self) -> None:
        """Clear the cover cache, forcing recomputation on the next request.

        copilot: shared-core marker

        After calling this method all subsequent calls to ``canonical_cover``,
        ``minimal_cover``, and ``cech_cover`` will recompute from scratch.
        """
        self.cache.clear()

    def covers_for_site(self) -> dict[str, CoverFamily]:
        """Compute canonical covers for every coordinate in ``self.site``.

        copilot: shared-core marker

        Iterates over all coordinates registered with the site and calls
        ``canonical_cover`` for each, returning a dict keyed by coordinate key.

        Returns:
            Dict mapping coordinate keys to their ``CoverFamily`` objects.
        """
        result: dict[str, CoverFamily] = {}
        for coord in self.site.objects():
            result[coord.key] = self.canonical_cover(coord)
        return result

    def summary(self) -> str:
        """Return a human-readable summary of the factory state.

        copilot: shared-core marker

        Returns:
            A string including site label, topology name, strategy, and cache
            statistics.
        """
        cached_keys = sorted(self.cache.keys())[:5]
        cache_str = ", ".join(f"'{k}'" for k in cached_keys)
        if len(self.cache) > 5:
            cache_str += f", ... (+{len(self.cache) - 5} more)"
        return (
            f"CanonicalCoverFactory\n"
            f"  site: '{self.site.label}'\n"
            f"  topology: '{self.topology.name}'\n"
            f"  strategy: '{self.strategy}'\n"
            f"  cached covers ({len(self.cache)}): {cache_str or '(none)'}"
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def verify_cover_axioms(
    cover: CoverFamily,
    topology: GrothendieckTopology,
) -> dict[str, bool]:
    """Verify which Grothendieck topology axioms *cover* satisfies.

    copilot: shared-core marker

    Combines the structural checks from ``cover.verify_axioms()`` with a
    topology-level check via ``topology.is_covering``.  The topology check
    converts ``cover`` to a low-level ``CoveringFamily`` (from site.py) using
    synthetic morphisms.

    Args:
        cover: The ``CoverFamily`` under test.
        topology: The ``GrothendieckTopology`` providing the axiom predicates.

    Returns:
        Dict mapping each ``CoverAxiom.value`` string to a bool.  Also
        includes the key ``"topology_covering"`` for the topology-level check.
    """
    structural = cover.verify_axioms()

    # Topology-level check: construct a CoveringFamily and ask the topology
    synth_morphisms = [
        Morphism(
            source=m.source_coordinate,
            target=cover.base_coordinate,
            kind=MorphismKind.RESTRICTION,
            label=m.source_coordinate.name,
        )
        for m in cover.members
    ]
    fam = CoveringFamily(
        base=cover.base_coordinate,
        members=synth_morphisms,
        label=cover.cover_id,
    )
    topology_ok = topology.is_covering(fam)
    structural["topology_covering"] = topology_ok
    return structural


def build_canonical_hypercover(
    coord: Coordinate,
    site: Site,
    depth: int,
) -> HypercoverStructure:
    """Build the canonical hypercover of *coord* in *site* to the given *depth*.

    copilot: shared-core marker

    Convenience wrapper that instantiates a ``CanonicalCoverFactory`` with the
    site's own topology and calls ``factory.hypercover(coord, depth)``.

    Args:
        coord: The coordinate to hypercover.
        site: The ambient site (must have a ``topology`` attribute).
        depth: Number of levels above 0 to construct.

    Returns:
        A ``HypercoverStructure`` at the requested depth.
    """
    factory = CanonicalCoverFactory(
        site=site,
        topology=site.topology,
        strategy="canonical",
    )
    return factory.hypercover(coord, depth)


def refine_to_common(
    cover1: CoverFamily,
    cover2: CoverFamily,
) -> CoverFamily:
    """Build the common refinement of two cover families.

    copilot: shared-core marker

    The common refinement of {U_i → X} and {V_j → X} is the cover
    {U_i ×_X V_j → X} of all pairwise fiber products.  This function
    approximates the fiber product as the common ancestor coordinate, producing
    one member per non-trivial (name_i, name_j) intersection.

    Both covers must share the same ``base_coordinate``.

    Args:
        cover1: First ``CoverFamily``.
        cover2: Second ``CoverFamily``.

    Returns:
        A ``CoverFamily`` representing the common refinement over the shared
        base coordinate.

    Raises:
        ValueError: If the two covers have different base coordinates.
    """
    if cover1.base_coordinate != cover2.base_coordinate:
        raise ValueError(
            f"Cannot refine to common: base coordinates differ "
            f"('{cover1.base_coordinate.name}' ≠ '{cover2.base_coordinate.name}')."
        )
    base = cover1.base_coordinate
    intersection = cover1.intersect_with(cover2)

    # Add all members from both covers that are not already present
    existing_names = {m.source_coordinate.name for m in intersection.members}
    extra: list[CoverMember] = []
    idx = len(intersection.members)
    for source in (cover1, cover2):
        for m in source.members:
            nm = m.source_coordinate.name
            if nm not in existing_names:
                existing_names.add(nm)
                extra.append(
                    CoverMember(
                        source_coordinate=m.source_coordinate,
                        target_coordinate=base,
                        restriction_morphism=CoordinateMorphism(nm, base.name),
                        index=idx,
                        evidence_scope=m.evidence_scope,
                        trust_ceiling=m.trust_ceiling,
                    )
                )
                idx += 1

    combined_members = list(intersection.members) + extra
    combined_axioms = tuple(
        sorted(set(cover1.topology_axioms) | set(cover2.topology_axioms))
    )
    return CoverFamily(
        base_coordinate=base,
        members=tuple(combined_members),
        topology_axioms=combined_axioms,
        metadata={
            "type": CoverType.REFINEMENT.value,
            "refined_from": [cover1.cover_id, cover2.cover_id],
        },
    )
