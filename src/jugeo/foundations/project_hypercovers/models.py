"""Theory2.tex Ch8 §8.1-§8.4 — core domain models for project sites, module covers,
fleet members, and hypercover decompositions.

copilot: shared-core models — central domain objects for Ch8 project hypercover machinery.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterator

from jugeo.evidence.certificates import Certificate, CertificateStatus
from jugeo.geometry.covers import Cover, CoverMetric
from jugeo.geometry.descent import DescentEngine, DescentResult, GluingData, LocalSection
from jugeo.geometry.hypercovers import CechNerve, HypercoverKind, HypercoverLevel
from jugeo.geometry.site import CoordinateKind, CoordinateObject, SemanticSite
from jugeo.judgments.judgment_terms import JudgmentKind, JudgmentTerm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supporting enumerations
# ---------------------------------------------------------------------------


class ProjectKind(str, Enum):
    """High-level classification of a project's primary purpose.

    Notes
    -----
    Theory2.tex §8.1 Table 8.1 — project kind taxonomy.
    """

    LIBRARY = "library"          # a reusable code library
    APPLICATION = "application"  # an executable application
    SERVICE = "service"          # a microservice or API
    FRAMEWORK = "framework"      # an extensible framework
    RESEARCH = "research"        # a research/experimental project
    HYBRID = "hybrid"            # mixed kind project


class CoverStrategy(str, Enum):
    """Strategy used when generating an admissible cover of a project site.

    Notes
    -----
    Theory2.tex §8.2 §8.2.1 — cover strategy taxonomy.
    """

    GREEDY = "greedy"            # greedy maximal cover
    OPTIMAL = "optimal"          # globally optimal (expensive)
    HIERARCHICAL = "hierarchical"  # cover by hierarchy
    RANDOM = "random"            # random sampling cover
    DEPENDENCY = "dependency"    # follow dependency graph
    SEMANTIC = "semantic"        # group by semantic similarity


class FleetStatus(str, Enum):
    """Operational status of a fleet member agent.

    Notes
    -----
    Theory2.tex §8.3 §8.3.2 — agent lifecycle states.
    """

    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"
    DEGRADED = "degraded"


class DecompositionStatus(str, Enum):
    """Processing status of a hypercover decomposition.

    Notes
    -----
    Theory2.tex §8.4 §8.4.1 — decomposition lifecycle.
    """

    INITIALIZING = "initializing"
    PARTIAL = "partial"
    COMPLETE = "complete"
    OBSTRUCTED = "obstructed"
    CONVERGED = "converged"
    FAILED = "failed"


class PatchRole(str, Enum):
    """Structural role of a patch within a cover.

    Notes
    -----
    Theory2.tex §8.2 §8.2.3 — patch role taxonomy.
    """

    PRIMARY = "primary"
    SUPPORT = "support"
    OVERLAP = "overlap"
    BOUNDARY = "boundary"


class TrustTier(IntEnum):
    """Ordinal trust tier assigned to fleet members.

    Notes
    -----
    Theory2.tex §8.3 §8.3.4 — trust hierarchy.  Higher values
    indicate greater confidence in agent outputs.
    """

    MINIMAL = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    MAXIMAL = 5


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoordinateMorphism:
    """A directed morphism between two coordinate objects in the project site.

    Parameters
    ----------
    source_id : str
        Identifier of the source coordinate.
    target_id : str
        Identifier of the target coordinate.
    morphism_kind : str
        Semantic kind of the morphism (e.g. ``"dependency"``,
        ``"inclusion"``, ``"refinement"``).
    weight : float, optional
        Numerical weight of the morphism; default ``1.0``.

    Notes
    -----
    Theory2.tex §8.1 §8.1.2 — morphisms of the coordinate category.
    """

    source_id: str
    target_id: str
    morphism_kind: str
    weight: float = 1.0

    def is_identity(self) -> bool:
        """Return ``True`` when source and target are the same object.

        Returns
        -------
        bool
            ``True`` iff ``source_id == target_id``.
        """
        return self.source_id == self.target_id

    def reverse(self) -> CoordinateMorphism:
        """Return the morphism with source and target swapped.

        Returns
        -------
        CoordinateMorphism
            New morphism with ``source_id`` and ``target_id`` exchanged.
        """
        return CoordinateMorphism(
            source_id=self.target_id,
            target_id=self.source_id,
            morphism_kind=self.morphism_kind,
            weight=self.weight,
        )

    def compose(self, other: CoordinateMorphism) -> CoordinateMorphism:
        """Compose ``self`` with ``other`` (``self`` then ``other``).

        Parameters
        ----------
        other : CoordinateMorphism
            Morphism whose ``source_id`` must equal ``self.target_id``.

        Returns
        -------
        CoordinateMorphism
            Composite morphism from ``self.source_id`` to ``other.target_id``
            with combined weight (geometric mean).

        Raises
        ------
        ValueError
            If the morphisms are not composable.
        """
        if self.target_id != other.source_id:
            raise ValueError(
                f"Cannot compose: self.target_id={self.target_id!r} != "
                f"other.source_id={other.source_id!r}"
            )
        composed_weight = math.sqrt(self.weight * other.weight)
        return CoordinateMorphism(
            source_id=self.source_id,
            target_id=other.target_id,
            morphism_kind=f"{self.morphism_kind}∘{other.morphism_kind}",
            weight=composed_weight,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable representation.
        """
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "morphism_kind": self.morphism_kind,
            "weight": self.weight,
        }


@dataclass(frozen=True, slots=True)
class OverlapCell:
    """A simplex in the Čech nerve representing the overlap of patches.

    Parameters
    ----------
    patch_ids : tuple[str, ...]
        Ordered tuple of patch identifiers that form this cell.
    overlap_kind : str
        Semantic label for the kind of overlap (e.g. ``"intersection"``,
        ``"containment"``).
    witnesses : tuple[str, ...], optional
        Coordinate IDs that witness the non-empty intersection.

    Notes
    -----
    Theory2.tex §8.2 §8.2.4 — simplicial structure of the nerve.
    """

    patch_ids: tuple[str, ...]
    overlap_kind: str
    witnesses: tuple[str, ...] = ()

    @property
    def dimension(self) -> int:
        """Simplicial dimension of this cell (= number of patches minus 1).

        Returns
        -------
        int
            Non-negative integer dimension.
        """
        return max(0, len(self.patch_ids) - 1)

    @property
    def is_empty(self) -> bool:
        """Return ``True`` when no witnesses confirm the intersection.

        Returns
        -------
        bool
        """
        return len(self.witnesses) == 0

    def contains_patch(self, patch_id: str) -> bool:
        """Check whether a given patch participates in this cell.

        Parameters
        ----------
        patch_id : str
            Patch identifier to look up.

        Returns
        -------
        bool
            ``True`` iff ``patch_id`` appears in ``patch_ids``.
        """
        return patch_id in self.patch_ids

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "patch_ids": list(self.patch_ids),
            "overlap_kind": self.overlap_kind,
            "witnesses": list(self.witnesses),
            "dimension": self.dimension,
            "is_empty": self.is_empty,
        }


@dataclass(frozen=True, slots=True)
class CohomologyClass:
    """A cohomology class arising from the Čech complex of a cover.

    Parameters
    ----------
    level : int
        Cohomological degree.
    class_id : str
        Stable identifier for this class.
    representative : dict[str, Any]
        A representative cocycle stored as a dictionary.
    is_trivial : bool, optional
        ``True`` when the class is zero in cohomology; default ``False``.

    Notes
    -----
    Theory2.tex §8.4 §8.4.3 — obstructions and cohomology.
    """

    level: int
    class_id: str
    representative: dict[str, Any]
    is_trivial: bool = False

    def obstruct(self, other: CohomologyClass) -> bool:
        """Return ``True`` when ``self`` is an obstruction relative to ``other``.

        Two classes obstruct each other when they are at the same level, neither
        is trivial, and their representatives share at least one key with
        differing values.

        Parameters
        ----------
        other : CohomologyClass
            The class to compare against.

        Returns
        -------
        bool
            ``True`` iff an obstruction is detected.
        """
        if self.is_trivial or other.is_trivial:
            return False
        if self.level != other.level:
            return False
        shared_keys = set(self.representative) & set(other.representative)
        for key in shared_keys:
            if self.representative[key] != other.representative[key]:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "level": self.level,
            "class_id": self.class_id,
            "representative": self.representative,
            "is_trivial": self.is_trivial,
        }


# ---------------------------------------------------------------------------
# Main domain classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProjectSite:
    """A project viewed as a semantic site — a coordinate space equipped with a
    Grothendieck topology.

    Each module/package/class in the codebase corresponds to a coordinate
    object; morphisms are dependency/inclusion arrows; the topology is
    generated by module covers.

    Parameters
    ----------
    site_id : str
        UUID for this site; auto-generated.
    name : str
        Human-readable name.
    description : str
        Longer description.
    project_kind : ProjectKind
        Classification of the project.
    coordinates : dict[str, CoordinateObject]
        Map from coord ID to object.
    morphisms : list[CoordinateMorphism]
        Directed morphisms between coordinates.
    topology_kind : str
        Label for the topology (default ``"grothendieck"``).
    cover_strategy : CoverStrategy
        Default strategy for generating covers.
    created_at : float
        POSIX timestamp of creation.
    metadata : dict[str, Any]
        Arbitrary extra data.

    Notes
    -----
    Theory2.tex §8.1 — project site construction.
    """

    site_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    project_kind: ProjectKind = ProjectKind.LIBRARY
    coordinates: dict[str, CoordinateObject] = field(default_factory=dict)
    morphisms: list[CoordinateMorphism] = field(default_factory=list)
    topology_kind: str = "grothendieck"
    cover_strategy: CoverStrategy = CoverStrategy.GREEDY
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Coordinate management
    # ------------------------------------------------------------------

    def add_coordinate(self, coord: CoordinateObject) -> None:
        """Add a coordinate object to the site.

        Parameters
        ----------
        coord : CoordinateObject
            Object to register; must have a non-empty ``coord_id``.

        Raises
        ------
        ValueError
            If the coordinate has no ``coord_id``.
        """
        coord_id: str = getattr(coord, "coord_id", None) or getattr(coord, "id", None) or ""
        if not coord_id:
            raise ValueError("CoordinateObject must have a non-empty coord_id")
        self.coordinates[coord_id] = coord

    def remove_coordinate(self, coord_id: str) -> CoordinateObject | None:
        """Remove and return a coordinate object by ID.

        Parameters
        ----------
        coord_id : str
            Identifier of the coordinate to remove.

        Returns
        -------
        CoordinateObject or None
            The removed object, or ``None`` if not found.
        """
        removed = self.coordinates.pop(coord_id, None)
        if removed is not None:
            self.morphisms = [
                m for m in self.morphisms
                if m.source_id != coord_id and m.target_id != coord_id
            ]
        return removed

    def get_coordinate(self, coord_id: str) -> CoordinateObject | None:
        """Return a coordinate by ID, or ``None``.

        Parameters
        ----------
        coord_id : str
            Identifier to look up.

        Returns
        -------
        CoordinateObject or None
        """
        return self.coordinates.get(coord_id)

    def add_morphism(self, morph: CoordinateMorphism) -> None:
        """Append a morphism after validating that both endpoints exist.

        Parameters
        ----------
        morph : CoordinateMorphism
            Morphism to add.

        Raises
        ------
        KeyError
            If source or target coordinate is not registered.
        """
        if morph.source_id not in self.coordinates:
            raise KeyError(f"Source coordinate {morph.source_id!r} not found in site")
        if morph.target_id not in self.coordinates:
            raise KeyError(f"Target coordinate {morph.target_id!r} not found in site")
        self.morphisms.append(morph)

    def compute_grothendieck_topology(
        self,
        strategy: CoverStrategy | None = None,
    ) -> dict[str, list[list[str]]]:
        """Compute covering families for every coordinate in the site.

        For each coordinate ``c``, the result maps ``c.coord_id`` to a list
        of covering families.  Each family is a list of coordinate IDs that
        jointly cover ``c``.

        Parameters
        ----------
        strategy : CoverStrategy or None, optional
            Override the site's default cover strategy.

        Returns
        -------
        dict[str, list[list[str]]]
            Mapping ``coord_id -> list of covering families``.

        Notes
        -----
        Theory2.tex §8.1 §8.1.3 — Grothendieck topology generation.
        """
        effective = strategy or self.cover_strategy
        topology: dict[str, list[list[str]]] = {}

        outgoing: dict[str, list[str]] = {}
        for m in self.morphisms:
            outgoing.setdefault(m.source_id, []).append(m.target_id)

        for coord_id in self.coordinates:
            if effective == CoverStrategy.GREEDY:
                deps = outgoing.get(coord_id, [])
                family = sorted({coord_id} | set(deps))
                topology[coord_id] = [family]
            elif effective == CoverStrategy.HIERARCHICAL:
                parts = coord_id.split(".")
                families: list[list[str]] = []
                for prefix_len in range(1, len(parts) + 1):
                    prefix = ".".join(parts[:prefix_len])
                    group = [
                        cid for cid in self.coordinates
                        if cid.startswith(prefix)
                    ]
                    if group:
                        families.append(sorted(group))
                topology[coord_id] = families if families else [[coord_id]]
            elif effective == CoverStrategy.DEPENDENCY:
                visited: set[str] = set()
                stack = [coord_id]
                while stack:
                    cur = stack.pop()
                    if cur in visited:
                        continue
                    visited.add(cur)
                    stack.extend(outgoing.get(cur, []))
                topology[coord_id] = [sorted(visited)]
            else:
                topology[coord_id] = [[coord_id]]

        return topology

    def generate_cover(
        self,
        target_coord_id: str,
        strategy: CoverStrategy | None = None,
    ) -> Cover:
        """Build a ``Cover`` object for the given coordinate.

        Parameters
        ----------
        target_coord_id : str
            The coordinate to be covered.
        strategy : CoverStrategy or None, optional
            Override strategy; falls back to site default.

        Returns
        -------
        Cover
            Populated Cover instance.

        Raises
        ------
        KeyError
            If ``target_coord_id`` is not in the site.
        """
        if target_coord_id not in self.coordinates:
            raise KeyError(f"Coordinate {target_coord_id!r} not found in site")
        topology = self.compute_grothendieck_topology(strategy)
        families = topology.get(target_coord_id, [[target_coord_id]])
        cover_data = {
            "covered_id": target_coord_id,
            "families": families,
            "site_id": self.site_id,
        }
        return Cover(cover_data)

    def get_incoming_morphisms(self, coord_id: str) -> list[CoordinateMorphism]:
        """Return all morphisms whose target is ``coord_id``.

        Parameters
        ----------
        coord_id : str
            Target coordinate ID.

        Returns
        -------
        list[CoordinateMorphism]
        """
        return [m for m in self.morphisms if m.target_id == coord_id]

    def get_outgoing_morphisms(self, coord_id: str) -> list[CoordinateMorphism]:
        """Return all morphisms whose source is ``coord_id``.

        Parameters
        ----------
        coord_id : str
            Source coordinate ID.

        Returns
        -------
        list[CoordinateMorphism]
        """
        return [m for m in self.morphisms if m.source_id == coord_id]

    def restrict_to_subsite(self, coord_ids: list[str]) -> ProjectSite:
        """Create a new ``ProjectSite`` restricted to the given coordinates.

        Parameters
        ----------
        coord_ids : list[str]
            IDs of coordinates to keep.

        Returns
        -------
        ProjectSite
            New site with only the requested coordinates and the morphisms
            between them.
        """
        id_set = set(coord_ids)
        sub_coords = {cid: self.coordinates[cid] for cid in coord_ids if cid in self.coordinates}
        sub_morphisms = [
            m for m in self.morphisms
            if m.source_id in id_set and m.target_id in id_set
        ]
        return ProjectSite(
            name=f"{self.name}[restricted]",
            description=self.description,
            project_kind=self.project_kind,
            coordinates=sub_coords,
            morphisms=sub_morphisms,
            topology_kind=self.topology_kind,
            cover_strategy=self.cover_strategy,
        )

    def merge_with(self, other: ProjectSite) -> ProjectSite:
        """Return the union of this site and ``other``.

        Parameters
        ----------
        other : ProjectSite
            Site to merge with.

        Returns
        -------
        ProjectSite
            New site containing all coordinates and morphisms from both sites.
        """
        merged_coords = {**self.coordinates, **other.coordinates}
        seen_morphisms: set[tuple[str, str, str]] = set()
        merged_morphisms: list[CoordinateMorphism] = []
        for m in self.morphisms + other.morphisms:
            key = (m.source_id, m.target_id, m.morphism_kind)
            if key not in seen_morphisms:
                seen_morphisms.add(key)
                merged_morphisms.append(m)
        return ProjectSite(
            name=f"{self.name}+{other.name}",
            project_kind=self.project_kind,
            coordinates=merged_coords,
            morphisms=merged_morphisms,
            topology_kind=self.topology_kind,
            cover_strategy=self.cover_strategy,
        )

    def compute_connected_components(self) -> list[list[str]]:
        """Find connected components of the underlying undirected graph.

        Returns
        -------
        list[list[str]]
            Each sub-list contains the coord IDs in one component.

        Notes
        -----
        Uses a union-find (disjoint-set) algorithm for efficiency.
        """
        parent: dict[str, str] = {cid: cid for cid in self.coordinates}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for m in self.morphisms:
            if m.source_id in parent and m.target_id in parent:
                union(m.source_id, m.target_id)

        components: dict[str, list[str]] = {}
        for cid in self.coordinates:
            root = find(cid)
            components.setdefault(root, []).append(cid)
        return list(components.values())

    def coordinate_depth(self, coord_id: str) -> int:
        """Compute BFS depth of ``coord_id`` from site root(s).

        Roots are coordinates with no incoming morphisms.

        Parameters
        ----------
        coord_id : str
            Target coordinate.

        Returns
        -------
        int
            BFS depth, or ``-1`` if unreachable.
        """
        incoming_set = {m.target_id for m in self.morphisms}
        roots = [cid for cid in self.coordinates if cid not in incoming_set]
        if not roots:
            roots = list(self.coordinates.keys())[:1]

        out: dict[str, list[str]] = {}
        for m in self.morphisms:
            out.setdefault(m.source_id, []).append(m.target_id)

        from collections import deque
        visited: dict[str, int] = {}
        q: deque[tuple[str, int]] = deque()
        for r in roots:
            if r not in visited:
                visited[r] = 0
                q.append((r, 0))
        while q:
            cur, depth = q.popleft()
            if cur == coord_id:
                return depth
            for nb in out.get(cur, []):
                if nb not in visited:
                    visited[nb] = depth + 1
                    q.append((nb, depth + 1))
        return visited.get(coord_id, -1)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "site_id": self.site_id,
            "name": self.name,
            "description": self.description,
            "project_kind": self.project_kind.value,
            "topology_kind": self.topology_kind,
            "cover_strategy": self.cover_strategy.value,
            "created_at": self.created_at,
            "coordinate_count": len(self.coordinates),
            "morphism_count": len(self.morphisms),
            "morphisms": [m.to_dict() for m in self.morphisms],
            "metadata": self.metadata,
        }

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty list means valid).

        Returns
        -------
        list[str]
            Human-readable error messages.
        """
        errors: list[str] = []
        if not self.name:
            errors.append("ProjectSite.name must not be empty")
        for m in self.morphisms:
            if m.source_id not in self.coordinates:
                errors.append(f"Morphism source {m.source_id!r} not in coordinates")
            if m.target_id not in self.coordinates:
                errors.append(f"Morphism target {m.target_id!r} not in coordinates")
            if not math.isfinite(m.weight) or m.weight < 0:
                errors.append(f"Morphism weight {m.weight} is invalid")
        return errors

    def __len__(self) -> int:
        """Return number of coordinate objects in the site."""
        return len(self.coordinates)

    def __contains__(self, coord_id: str) -> bool:  # type: ignore[override]
        """Return ``True`` iff ``coord_id`` is registered."""
        return coord_id in self.coordinates


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModuleCover:
    """A cover of a project site by modules.

    An admissible cover in the Grothendieck topology of the project site.

    Parameters
    ----------
    cover_id : str
        UUID auto-generated.
    project_site_id : str
        ID of the covered project site.
    patches : dict[str, list[str]]
        Mapping ``patch_id -> list[coord_id]`` for each patch.
    cover_metric : float
        Scalar quality metric; updated by :meth:`compute_cover_metric`.
    refinement_level : int
        How many times this cover has been refined.
    overlap_map : dict[tuple[str, str], list[str]]
        Shared coordinates between patch pairs.
    strategy : CoverStrategy
        Strategy used to build this cover.
    created_at : float
        POSIX timestamp.

    Notes
    -----
    Theory2.tex §8.2 — module covers and admissibility.
    """

    cover_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_site_id: str = ""
    patches: dict[str, list[str]] = field(default_factory=dict)
    cover_metric: float = 0.0
    refinement_level: int = 0
    overlap_map: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    strategy: CoverStrategy = CoverStrategy.GREEDY
    created_at: float = field(default_factory=time.time)

    def compute_overlaps(self) -> dict[tuple[str, str], list[str]]:
        """Compute shared coordinates for every pair of patches.

        Populates ``overlap_map`` in place and returns it.

        Returns
        -------
        dict[tuple[str, str], list[str]]
            Mapping ``(patch_a, patch_b) -> shared coord IDs``.
        """
        self.overlap_map = {}
        patch_ids = list(self.patches.keys())
        for i in range(len(patch_ids)):
            for j in range(i + 1, len(patch_ids)):
                a, b = patch_ids[i], patch_ids[j]
                shared = sorted(set(self.patches[a]) & set(self.patches[b]))
                if shared:
                    self.overlap_map[(a, b)] = shared
        return self.overlap_map

    def refine(self, split_threshold: int = 3) -> ModuleCover:
        """Create a new cover by splitting patches larger than ``split_threshold``.

        Parameters
        ----------
        split_threshold : int, optional
            Maximum number of coordinates in a patch before it is split.

        Returns
        -------
        ModuleCover
            New refined cover.
        """
        new_patches: dict[str, list[str]] = {}
        for pid, coords in self.patches.items():
            if len(coords) <= split_threshold:
                new_patches[pid] = list(coords)
            else:
                mid = len(coords) // 2
                new_id_a = str(uuid.uuid4())
                new_id_b = str(uuid.uuid4())
                new_patches[new_id_a] = coords[:mid]
                new_patches[new_id_b] = coords[mid:]
        return ModuleCover(
            project_site_id=self.project_site_id,
            patches=new_patches,
            refinement_level=self.refinement_level + 1,
            strategy=self.strategy,
        )

    def coarsen(self, merge_threshold: float = 0.8) -> ModuleCover:
        """Merge patches that have high overlap ratio.

        Parameters
        ----------
        merge_threshold : float, optional
            Minimum Jaccard similarity to trigger a merge; default ``0.8``.

        Returns
        -------
        ModuleCover
            New coarsened cover.
        """
        self.compute_overlaps()
        merged_into: dict[str, str] = {}

        def canonical(pid: str) -> str:
            while pid in merged_into:
                pid = merged_into[pid]
            return pid

        new_patches: dict[str, list[str]] = {pid: list(c) for pid, c in self.patches.items()}
        for (a, b), shared in self.overlap_map.items():
            ca, cb = canonical(a), canonical(b)
            if ca == cb:
                continue
            set_a = set(new_patches.get(ca, []))
            set_b = set(new_patches.get(cb, []))
            union_size = len(set_a | set_b)
            if union_size == 0:
                continue
            jaccard = len(set_a & set_b) / union_size
            if jaccard >= merge_threshold:
                merged_into[cb] = ca
                new_patches[ca] = sorted(set_a | set_b)
                new_patches.pop(cb, None)

        return ModuleCover(
            project_site_id=self.project_site_id,
            patches=new_patches,
            refinement_level=self.refinement_level,
            strategy=self.strategy,
        )

    def validate_admissibility(self, topology: dict[str, list[list[str]]]) -> bool:
        """Check that every coordinate in the topology is covered by ≥1 patch.

        Parameters
        ----------
        topology : dict[str, list[list[str]]]
            Grothendieck topology from :meth:`ProjectSite.compute_grothendieck_topology`.

        Returns
        -------
        bool
            ``True`` iff admissibility holds.
        """
        covered = self.all_covered_coordinates()
        for coord_id in topology:
            if coord_id not in covered:
                return False
        return True

    def compute_cech_nerve(self) -> CechNerve:
        """Compute the Čech nerve of this cover.

        Returns
        -------
        CechNerve
            Čech nerve object built from the patches.
        """
        cover_data = {
            "cover_id": self.cover_id,
            "patches": self.patches,
        }
        base_cover = Cover(cover_data)
        return CechNerve(base_cover)

    def get_patch_for_coordinate(self, coord_id: str) -> list[str]:
        """Return the list of patch IDs that contain ``coord_id``.

        Parameters
        ----------
        coord_id : str
            Coordinate to look up.

        Returns
        -------
        list[str]
            Patch IDs containing the coordinate.
        """
        return [pid for pid, coords in self.patches.items() if coord_id in coords]

    def merge_patches(self, patch_a: str, patch_b: str) -> str:
        """Merge two patches into one and remove the originals.

        Parameters
        ----------
        patch_a : str
            First patch ID.
        patch_b : str
            Second patch ID.

        Returns
        -------
        str
            New patch ID for the merged patch.

        Raises
        ------
        KeyError
            If either patch ID is not found.
        """
        if patch_a not in self.patches:
            raise KeyError(f"Patch {patch_a!r} not found")
        if patch_b not in self.patches:
            raise KeyError(f"Patch {patch_b!r} not found")
        new_coords = sorted(set(self.patches[patch_a]) | set(self.patches[patch_b]))
        new_id = str(uuid.uuid4())
        del self.patches[patch_a]
        del self.patches[patch_b]
        self.patches[new_id] = new_coords
        return new_id

    def split_patch(self, patch_id: str, partition: list[list[str]]) -> list[str]:
        """Split a patch into sub-patches according to ``partition``.

        Parameters
        ----------
        patch_id : str
            Patch to split.
        partition : list[list[str]]
            Non-overlapping groups of coordinate IDs.

        Returns
        -------
        list[str]
            New patch IDs created from the partition.

        Raises
        ------
        KeyError
            If ``patch_id`` is not found.
        """
        if patch_id not in self.patches:
            raise KeyError(f"Patch {patch_id!r} not found")
        del self.patches[patch_id]
        new_ids: list[str] = []
        for group in partition:
            if group:
                nid = str(uuid.uuid4())
                self.patches[nid] = list(group)
                new_ids.append(nid)
        return new_ids

    def coverage_score(self) -> float:
        """Compute the fraction of all coordinates that appear in at least one patch.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.
        """
        covered = self.all_covered_coordinates()
        all_coords: set[str] = set()
        for coords in self.patches.values():
            all_coords.update(coords)
        if not all_coords:
            return 1.0
        return len(covered) / len(all_coords)

    def compute_cover_metric(self) -> CoverMetric:
        """Compute and cache a :class:`~jugeo.geometry.covers.CoverMetric`.

        Updates ``self.cover_metric`` as a side effect.

        Returns
        -------
        CoverMetric
        """
        score = self.coverage_score()
        self.cover_metric = score
        metric_data = {
            "cover_id": self.cover_id,
            "coverage_score": score,
            "patch_count": len(self.patches),
            "overlap_count": len(self.overlap_map),
        }
        return CoverMetric(metric_data)

    def all_covered_coordinates(self) -> set[str]:
        """Return the set of all coordinate IDs present in any patch.

        Returns
        -------
        set[str]
        """
        result: set[str] = set()
        for coords in self.patches.values():
            result.update(coords)
        return result

    def patch_sizes(self) -> dict[str, int]:
        """Return a mapping of patch ID to its number of coordinates.

        Returns
        -------
        dict[str, int]
        """
        return {pid: len(coords) for pid, coords in self.patches.items()}

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "cover_id": self.cover_id,
            "project_site_id": self.project_site_id,
            "patch_count": len(self.patches),
            "patches": {pid: list(c) for pid, c in self.patches.items()},
            "cover_metric": self.cover_metric,
            "refinement_level": self.refinement_level,
            "strategy": self.strategy.value,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetMember:
    """A single agent in a fleet that covers a coordinate.

    Parameters
    ----------
    member_id : str
        UUID auto-generated.
    agent_kind : str
        Kind label for this agent (e.g. ``"verifier"``, ``"synthesiser"``).
    assigned_patches : list[str]
        Patch IDs currently assigned to this member.
    trust_level : float
        Floating-point trust in ``[0.0, 1.0]``.
    status : FleetStatus
        Current operational status.
    capabilities : list[str]
        Declared capability labels.
    evidence_produced : list[str]
        IDs of certificates this member has produced.
    load_factor : float
        Cached load; refreshed by :meth:`compute_load`.
    max_patches : int
        Maximum number of patches this member can hold.
    metadata : dict[str, Any]
        Arbitrary extra data.

    Notes
    -----
    Theory2.tex §8.3 — fleet members and capacity.
    """

    member_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_kind: str = "generic"
    assigned_patches: list[str] = field(default_factory=list)
    trust_level: float = 0.5
    status: FleetStatus = FleetStatus.IDLE
    capabilities: list[str] = field(default_factory=list)
    evidence_produced: list[str] = field(default_factory=list)
    load_factor: float = 0.0
    max_patches: int = 10
    metadata: dict[str, Any] = field(default_factory=dict)

    def assign_patch(self, patch_id: str) -> bool:
        """Assign a patch to this member if capacity allows.

        Parameters
        ----------
        patch_id : str
            Patch to assign.

        Returns
        -------
        bool
            ``True`` iff the patch was successfully assigned.
        """
        if len(self.assigned_patches) >= self.max_patches:
            return False
        if patch_id not in self.assigned_patches:
            self.assigned_patches.append(patch_id)
        self.compute_load()
        return True

    def release_patch(self, patch_id: str) -> bool:
        """Release a patch from this member's assignment.

        Parameters
        ----------
        patch_id : str
            Patch to release.

        Returns
        -------
        bool
            ``True`` iff the patch was found and removed.
        """
        if patch_id in self.assigned_patches:
            self.assigned_patches.remove(patch_id)
            self.compute_load()
            return True
        return False

    def compute_load(self) -> float:
        """Recompute and cache the load factor.

        Returns
        -------
        float
            ``len(assigned_patches) / max_patches``.
        """
        self.load_factor = len(self.assigned_patches) / max(1, self.max_patches)
        return self.load_factor

    def check_capability(self, capability: str) -> bool:
        """Return ``True`` iff this member declares the given capability.

        Parameters
        ----------
        capability : str
            Capability label to check.

        Returns
        -------
        bool
        """
        return capability in self.capabilities

    def add_capability(self, capability: str) -> None:
        """Register a capability label (idempotent).

        Parameters
        ----------
        capability : str
            Label to add.
        """
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def produce_evidence(self, coord_id: str, evidence_kind: str) -> Certificate:
        """Produce a certificate stub for the given coordinate.

        Parameters
        ----------
        coord_id : str
            Coordinate for which evidence is produced.
        evidence_kind : str
            Kind label for the evidence (e.g. ``"type_check"``, ``"lint"``).

        Returns
        -------
        Certificate
            Newly created certificate stub.
        """
        cert_id = hashlib.sha256(
            f"{self.member_id}:{coord_id}:{evidence_kind}:{time.time()}".encode()
        ).hexdigest()[:16]
        cert = Certificate(
            certificate_id=cert_id,
            subject_id=coord_id,
            issuer_id=self.member_id,
            evidence_kind=evidence_kind,
            status=CertificateStatus.PENDING,
        )
        self.evidence_produced.append(cert_id)
        return cert

    def get_trust_score(self) -> TrustTier:
        """Convert the floating-point trust level to an ordinal :class:`TrustTier`.

        Returns
        -------
        TrustTier
            Discrete trust tier.
        """
        if self.trust_level >= 0.9:
            return TrustTier.MAXIMAL
        if self.trust_level >= 0.7:
            return TrustTier.HIGH
        if self.trust_level >= 0.5:
            return TrustTier.MEDIUM
        if self.trust_level >= 0.3:
            return TrustTier.LOW
        return TrustTier.MINIMAL

    def update_status(self, new_status: FleetStatus) -> None:
        """Update the operational status of this member.

        Parameters
        ----------
        new_status : FleetStatus
            New status to assign.
        """
        self.status = new_status

    def can_accept_work(self) -> bool:
        """Return ``True`` iff the member can receive additional patch assignments.

        Returns
        -------
        bool
            ``True`` when status is IDLE or ACTIVE and load < 1.0.
        """
        return self.status in (FleetStatus.IDLE, FleetStatus.ACTIVE) and self.compute_load() < 1.0

    def merge_with_member(self, other: FleetMember) -> FleetMember:
        """Create a new member that combines capabilities and patches from both.

        Parameters
        ----------
        other : FleetMember
            Member to merge with.

        Returns
        -------
        FleetMember
            New combined member.
        """
        combined_caps = sorted(set(self.capabilities) | set(other.capabilities))
        combined_patches = sorted(set(self.assigned_patches) | set(other.assigned_patches))
        avg_trust = (self.trust_level + other.trust_level) / 2.0
        return FleetMember(
            agent_kind=f"{self.agent_kind}+{other.agent_kind}",
            assigned_patches=combined_patches,
            trust_level=avg_trust,
            status=FleetStatus.IDLE,
            capabilities=combined_caps,
            max_patches=self.max_patches + other.max_patches,
        )

    def patch_count(self) -> int:
        """Return the number of currently assigned patches.

        Returns
        -------
        int
        """
        return len(self.assigned_patches)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "member_id": self.member_id,
            "agent_kind": self.agent_kind,
            "assigned_patches": list(self.assigned_patches),
            "trust_level": self.trust_level,
            "trust_tier": self.get_trust_score().name,
            "status": self.status.value,
            "capabilities": list(self.capabilities),
            "evidence_produced": list(self.evidence_produced),
            "load_factor": self.load_factor,
            "max_patches": self.max_patches,
            "metadata": self.metadata,
        }

    def validate(self) -> list[str]:
        """Return validation error strings; empty list means valid.

        Returns
        -------
        list[str]
        """
        errors: list[str] = []
        if not (0.0 <= self.trust_level <= 1.0):
            errors.append(f"trust_level {self.trust_level} not in [0, 1]")
        if self.max_patches < 1:
            errors.append(f"max_patches={self.max_patches} must be ≥ 1")
        if len(self.assigned_patches) > self.max_patches:
            errors.append(
                f"assigned_patches count {len(self.assigned_patches)} exceeds max_patches {self.max_patches}"
            )
        return errors


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HypercoverDecomposition:
    """A complete hypercover decomposition of a project site.

    Parameters
    ----------
    decomp_id : str
        UUID auto-generated.
    project_site_id : str
        ID of the project site being decomposed.
    levels : list[dict[str, Any]]
        Hypercover levels; each dict has keys ``level``, ``patches``, ``overlaps``.
    total_levels : int
        Count of levels added so far.
    descent_depth : int
        How many times :meth:`run_descent` has been invoked.
    status : DecompositionStatus
        Current lifecycle status.
    cohomology_classes : list[CohomologyClass]
        Collected cohomology classes from obstruction computations.
    refinement_history : list[dict[str, Any]]
        Log of refinement events.
    created_at : float
        POSIX timestamp.

    Notes
    -----
    Theory2.tex §8.4 — hypercover decompositions and descent.
    """

    decomp_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_site_id: str = ""
    levels: list[dict[str, Any]] = field(default_factory=list)
    total_levels: int = 0
    descent_depth: int = 0
    status: DecompositionStatus = DecompositionStatus.INITIALIZING
    cohomology_classes: list[CohomologyClass] = field(default_factory=list)
    refinement_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add_level(self, level_data: dict[str, Any]) -> int:
        """Append a new level to the decomposition.

        Parameters
        ----------
        level_data : dict[str, Any]
            Dictionary with at minimum a ``"patches"`` key.

        Returns
        -------
        int
            Zero-based index of the newly added level.
        """
        level_data = dict(level_data)
        level_data["level"] = self.total_levels
        self.levels.append(level_data)
        self.total_levels += 1
        if self.status == DecompositionStatus.INITIALIZING:
            self.status = DecompositionStatus.PARTIAL
        return self.total_levels - 1

    def get_level(self, n: int) -> dict[str, Any] | None:
        """Retrieve level ``n``, or ``None`` if out of range.

        Parameters
        ----------
        n : int
            Zero-based level index.

        Returns
        -------
        dict[str, Any] or None
        """
        if 0 <= n < len(self.levels):
            return self.levels[n]
        return None

    def compute_next_level(
        self, engine: DescentEngine | None = None
    ) -> dict[str, Any]:
        """Derive the (n+1)-th level from the current top level.

        The (n+1)-th level patches are the non-empty pairwise intersections
        of n-th level patches.

        Parameters
        ----------
        engine : DescentEngine or None, optional
            Optional engine used for intersection computation hints.

        Returns
        -------
        dict[str, Any]
            The newly created level dict, already appended to ``self.levels``.
        """
        if not self.levels:
            empty: dict[str, Any] = {"patches": {}, "overlaps": [], "level": 0}
            self.add_level(empty)
            return empty

        top = self.levels[-1]
        top_patches: dict[str, list[str]] = top.get("patches", {})
        patch_ids = list(top_patches.keys())
        new_patches: dict[str, list[str]] = {}
        new_overlaps: list[dict[str, Any]] = []

        for i in range(len(patch_ids)):
            for j in range(i + 1, len(patch_ids)):
                a, b = patch_ids[i], patch_ids[j]
                shared = sorted(set(top_patches[a]) & set(top_patches[b]))
                if shared:
                    nid = hashlib.md5(f"{a}∩{b}".encode()).hexdigest()[:8]
                    new_patches[nid] = shared
                    new_overlaps.append({"source_a": a, "source_b": b, "patch_id": nid})

        level_dict: dict[str, Any] = {
            "patches": new_patches,
            "overlaps": new_overlaps,
        }
        self.add_level(level_dict)
        return level_dict

    def run_descent(
        self,
        sections: dict[str, Any],
        engine: DescentEngine | None = None,
    ) -> DescentResult:
        """Execute a descent computation over the current level-0 patches.

        Parameters
        ----------
        sections : dict[str, Any]
            Local sections keyed by coordinate/patch ID.
        engine : DescentEngine or None, optional
            Descent engine to use; a default engine is constructed if ``None``.

        Returns
        -------
        DescentResult
            Result of the descent computation.
        """
        level0 = self.get_level(0) or {"patches": {}}
        patches: dict[str, list[str]] = level0.get("patches", {})

        local_sections = [
            LocalSection(patch_id=pid, data=sections.get(pid, {}))
            for pid in patches
        ]
        gluing = GluingData(sections=local_sections, overlap_map={})

        cover_data = {"cover_id": self.decomp_id, "patches": patches}
        cover = Cover(cover_data)

        if engine is None:
            engine = DescentEngine()

        result = engine.run(cover=cover, gluing_data=gluing)
        self.descent_depth += 1
        self.status = DecompositionStatus.PARTIAL
        return result

    def compute_obstruction(self, level: int) -> CohomologyClass | None:
        """Check for obstructions at the given level.

        Parameters
        ----------
        level : int
            Level index to inspect.

        Returns
        -------
        CohomologyClass or None
            A non-trivial class if an obstruction is found, trivial class if
            no obstruction, or ``None`` if the level does not exist.
        """
        level_data = self.get_level(level)
        if level_data is None:
            return None

        patches = level_data.get("patches", {})
        all_coords: set[str] = set()
        for coords in patches.values():
            all_coords.update(coords)

        # simple gap detection: check if any coord is isolated
        covered_by_multiple = set()
        for coord in all_coords:
            count = sum(1 for coords in patches.values() if coord in coords)
            if count >= 2:
                covered_by_multiple.add(coord)

        is_trivial = len(covered_by_multiple) == len(all_coords) or len(all_coords) == 0
        class_id = hashlib.md5(f"{self.decomp_id}:level{level}".encode()).hexdigest()[:12]
        representative = {
            "level": level,
            "gap_coords": sorted(all_coords - covered_by_multiple),
            "covered_count": len(covered_by_multiple),
        }
        cls = CohomologyClass(
            level=level,
            class_id=class_id,
            representative=representative,
            is_trivial=is_trivial,
        )
        self.cohomology_classes.append(cls)
        if not is_trivial:
            self.status = DecompositionStatus.OBSTRUCTED
        return cls

    def refine_at_level(
        self,
        level: int,
        strategy: CoverStrategy = CoverStrategy.GREEDY,
    ) -> bool:
        """Split all oversized patches at a given level.

        Parameters
        ----------
        level : int
            Level index to refine.
        strategy : CoverStrategy, optional
            Cover strategy hint for splitting logic.

        Returns
        -------
        bool
            ``True`` iff at least one patch was split.
        """
        level_data = self.get_level(level)
        if level_data is None:
            return False

        patches: dict[str, list[str]] = level_data.get("patches", {})
        refined = False
        to_add: dict[str, list[str]] = {}
        to_remove: list[str] = []

        for pid, coords in list(patches.items()):
            if len(coords) > 3:
                mid = len(coords) // 2
                id_a = str(uuid.uuid4())
                id_b = str(uuid.uuid4())
                to_add[id_a] = coords[:mid]
                to_add[id_b] = coords[mid:]
                to_remove.append(pid)
                refined = True

        for pid in to_remove:
            del patches[pid]
        patches.update(to_add)

        if refined:
            self.refinement_history.append({
                "level": level,
                "strategy": strategy.value,
                "timestamp": time.time(),
                "patches_split": len(to_remove),
            })
        return refined

    def flatten_to_cover(self, level: int = 0) -> ModuleCover:
        """Convert the patches at a given level to a :class:`ModuleCover`.

        Parameters
        ----------
        level : int, optional
            Level index to flatten; default ``0``.

        Returns
        -------
        ModuleCover
            Module cover containing the patches from the requested level.
        """
        level_data = self.get_level(level) or {"patches": {}}
        patches: dict[str, list[str]] = level_data.get("patches", {})
        return ModuleCover(
            project_site_id=self.project_site_id,
            patches={pid: list(coords) for pid, coords in patches.items()},
            refinement_level=level,
        )

    def get_cohomology_class(self, level: int) -> CohomologyClass | None:
        """Return the first stored cohomology class at the given level.

        Parameters
        ----------
        level : int
            Cohomological degree to look up.

        Returns
        -------
        CohomologyClass or None
        """
        for cls in self.cohomology_classes:
            if cls.level == level:
                return cls
        return None

    def validate_simplicial_identities(self) -> dict[str, bool]:
        """Check that simplicial face identities hold across available levels.

        For each adjacent triple of levels (n, n+1, n+2) the identity
        ``d_i ∘ d_j = d_{j-1} ∘ d_i`` (i < j) is approximated by checking
        that patch counts are non-increasing.

        Returns
        -------
        dict[str, bool]
            Mapping of identity name to satisfaction flag.
        """
        results: dict[str, bool] = {}
        for n in range(len(self.levels) - 2):
            lvl_n = self.levels[n]
            lvl_n1 = self.levels[n + 1]
            lvl_n2 = self.levels[n + 2]
            count_n = len(lvl_n.get("patches", {}))
            count_n1 = len(lvl_n1.get("patches", {}))
            count_n2 = len(lvl_n2.get("patches", {}))
            key = f"face_identity_level_{n}_{n+1}_{n+2}"
            results[key] = count_n >= count_n1 >= count_n2
        if not results:
            results["trivial_identity"] = True
        return results

    def add_cohomology_class(self, cls: CohomologyClass) -> None:
        """Append a cohomology class to the stored list.

        Parameters
        ----------
        cls : CohomologyClass
            Class to record.
        """
        self.cohomology_classes.append(cls)

    def is_converged(self) -> bool:
        """Return ``True`` when the decomposition has converged.

        Convergence is declared when status is ``CONVERGED``, or when the
        last two levels contain the same number of patches.

        Returns
        -------
        bool
        """
        if self.status == DecompositionStatus.CONVERGED:
            return True
        if self.total_levels >= 2:
            last = self.levels[-1].get("patches", {})
            prev = self.levels[-2].get("patches", {})
            if len(last) == len(prev):
                self.status = DecompositionStatus.CONVERGED
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "decomp_id": self.decomp_id,
            "project_site_id": self.project_site_id,
            "total_levels": self.total_levels,
            "descent_depth": self.descent_depth,
            "status": self.status.value,
            "cohomology_class_count": len(self.cohomology_classes),
            "cohomology_classes": [c.to_dict() for c in self.cohomology_classes],
            "levels": [
                {
                    "level": ld.get("level"),
                    "patch_count": len(ld.get("patches", {})),
                    "overlap_count": len(ld.get("overlaps", [])),
                }
                for ld in self.levels
            ],
            "refinement_history": list(self.refinement_history),
            "created_at": self.created_at,
        }


def model_solver_bridge(model_data: dict) -> dict:
    """Map a project model to its solver representation.

    Bridges the gap between the domain model layer (Theory2.tex §8 Project
    Hypercovers) and the solver subsystem so that model constraints can be
    checked mechanically.

    Parameters
    ----------
    model_data : dict
        Serialised model (project site, covers, fleet members).

    Returns
    -------
    dict
        Solver bridge payload with keys ``available``, ``backend``,
        ``constraints``, and ``routing``.
    """
    try:
        from jugeo.solver.z3_session import SolverResult, z3_available
        from jugeo.solver.router import BackendKind, RoutingDecision
    except ImportError as exc:
        logger.warning("model_solver_bridge: missing dependency — %s", exc)
        return {"available": False, "backend": None, "constraints": [],
                "routing": None, "error": str(exc)}

    available = z3_available()
    if not available:
        logger.info("Solver backend unavailable; bridge returns empty")
        return {"available": False, "backend": None, "constraints": [],
                "routing": None, "diagnostics": "z3 not installed"}

    routing = RoutingDecision(backend=BackendKind.Z3) \
        if hasattr(RoutingDecision, "__init__") else None
    backend_name = BackendKind.Z3.value if hasattr(BackendKind.Z3, "value") else "z3"

    constraints: list[dict] = []
    for key in ("project_site", "covers", "fleet_members", "decomposition"):
        entry = model_data.get(key)
        if entry is not None:
            constraints.append({"domain": key, "field_count": len(entry) if isinstance(entry, (list, dict)) else 1})

    logger.debug("model_solver_bridge: %d constraints, backend=%s", len(constraints), backend_name)
    return {"available": True, "backend": backend_name,
            "constraints": constraints,
            "routing": routing.to_dict() if hasattr(routing, "to_dict") else str(routing)}


def model_evidence_bridge(model_data: dict) -> dict:
    """Collect evidence artefacts for a project model.

    Combines trust algebra evaluation with evidence manifests as described in
    Theory2.tex §8 (Project Hypercovers) to produce a unified evidence record.

    Parameters
    ----------
    model_data : dict
        Serialised model (project site, covers, fleet members).

    Returns
    -------
    dict
        Evidence payload with keys ``trust_level``, ``manifest``, and
        ``component_count``.
    """
    try:
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra
        from jugeo.evidence.manifests import build_evidence_manifest
    except ImportError as exc:
        logger.warning("model_evidence_bridge: missing dependency — %s", exc)
        return {"trust_level": None, "manifest": None, "component_count": 0,
                "error": str(exc)}

    algebra = TrustAlgebra()
    components: list[dict] = []
    for key in ("project_site", "covers", "fleet_members", "decomposition"):
        entry = model_data.get(key)
        if entry is not None:
            components.append({"domain": key, "present": True})

    trust = algebra.propagate(TrustLevel.HIGH if len(components) >= 3 else TrustLevel.MEDIUM) \
        if hasattr(algebra, "propagate") else \
        (TrustLevel.HIGH if len(components) >= 3 else TrustLevel.MEDIUM)
    trust_val = trust.value if hasattr(trust, "value") else str(trust)

    manifest = build_evidence_manifest(
        components=components,
        metadata={"source": "model_evidence_bridge", "trust": trust_val})

    logger.debug("model_evidence_bridge: trust=%s, %d components", trust_val, len(components))
    return {"trust_level": trust_val, "manifest": manifest,
            "component_count": len(components)}


# copilot: shared-core models — central domain objects for Ch8 project hypercover machinery.
# All four main classes (ProjectSite, ModuleCover, FleetMember, HypercoverDecomposition)
# are designed for LLM-assisted verification workflows.
