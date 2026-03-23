"""Theory2.tex Ch8 §8.2 — Module Covers.

A module cover of a project site is an admissible cover in its Grothendieck
topology: a finite family of patches (sub-sites) together with gluing data
specifying how overlapping patches agree.

copilot: shared-core §8.2 implementation — module cover construction and
refinement for LLM-assisted verification workflows.
"""
from __future__ import annotations

import collections
import hashlib
import itertools
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from jugeo.evidence.certificates import Certificate, CertificateStatus
from jugeo.geometry.covers import Cover, CoverMetric
from jugeo.geometry.descent import DescentEngine, DescentResult, GluingData, LocalSection
from jugeo.geometry.hypercovers import CechNerve, HypercoverKind, HypercoverLevel
from jugeo.geometry.site import CoordinateKind, CoordinateObject, SemanticSite
from jugeo.judgments.judgment_terms import JudgmentKind, JudgmentTerm
from jugeo.foundations.project_hypercovers.models import (
    CohomologyClass,
    CoverStrategy,
    CoordinateMorphism,
    DecompositionStatus,
    FleetMember,
    FleetStatus,
    HypercoverDecomposition,
    ModuleCover,
    OverlapCell,
    ProjectKind,
    ProjectSite,
    TrustTier,
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _short_id() -> str:
    """Return an 8-character hex prefix from a fresh UUID4.

    Returns
    -------
    str
        Lowercase hex string of length 8.
    """
    return uuid.uuid4().hex[:8]


def _coord_keys(site: ProjectSite) -> list[str]:
    """Return sorted list of coordinate IDs from a project site.

    Parameters
    ----------
    site : ProjectSite
        The project site whose coordinates are enumerated.

    Returns
    -------
    list[str]
        Alphabetically sorted coordinate identifiers.
    """
    return sorted(site.coordinates.keys())


def _path_prefix(coord_id: str, level: int = 1) -> str:
    """Extract the first *level* path components from a slash-separated id.

    Parameters
    ----------
    coord_id : str
        A coordinate identifier that may contain ``/`` separators.
    level : int, optional
        Number of leading components to retain, by default 1.

    Returns
    -------
    str
        The prefix up to ``level`` components, joined by ``/``.

    Notes
    -----
    If the number of components is less than *level*, the whole string is
    returned unchanged.

    Examples
    --------
    >>> _path_prefix("a/b/c", 2)
    'a/b'
    >>> _path_prefix("standalone", 2)
    'standalone'
    """
    parts = coord_id.split("/")
    return "/".join(parts[:level])


def _intersection(sets: list[list[str]]) -> list[str]:
    """Return sorted intersection of multiple lists treated as sets.

    Parameters
    ----------
    sets : list[list[str]]
        A collection of lists.  Empty outer list returns ``[]``.

    Returns
    -------
    list[str]
        Sorted list of elements common to all input lists.
    """
    if not sets:
        return []
    result: set[str] = set(sets[0])
    for s in sets[1:]:
        result &= set(s)
    return sorted(result)


# ---------------------------------------------------------------------------
# 1. CoverBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoverBuilder:
    """Construct :class:`ModuleCover` objects for a :class:`ProjectSite`.

    Parameters
    ----------
    strategy : CoverStrategy
        Algorithm used to partition/overlap coordinates into patches.
    max_patch_size : int
        Upper bound on the number of coordinates in a single patch.
    min_patch_size : int
        Lower bound; patches with fewer coordinates are considered degenerate.
    overlap_tolerance : float
        Fraction of total coordinate assignments that may be duplicated.
    _cover_cache : dict
        Internal memoisation keyed by ``(site_id, strategy)``.

    Notes
    -----
    The builder is intentionally strategy-agnostic at the public API: callers
    set ``strategy`` and call :meth:`build`.  Individual strategies are
    implemented as private methods.
    """

    strategy: CoverStrategy = field(default_factory=lambda: CoverStrategy.GREEDY)
    max_patch_size: int = 15
    min_patch_size: int = 1
    overlap_tolerance: float = 0.3
    _cover_cache: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, site: ProjectSite) -> ModuleCover:
        """Dispatch to the appropriate strategy builder and return a cover.

        Parameters
        ----------
        site : ProjectSite
            Project site to be covered.

        Returns
        -------
        ModuleCover
            An admissible cover built according to ``self.strategy``.

        Raises
        ------
        ValueError
            If ``self.strategy`` is not a recognised :class:`CoverStrategy`.
        """
        cache_key = (id(site), self.strategy)
        if cache_key in self._cover_cache:
            return self._cover_cache[cache_key]

        dispatch: dict[CoverStrategy, Callable[[ProjectSite], ModuleCover]] = {
            CoverStrategy.GREEDY: self._build_greedy,
            CoverStrategy.HIERARCHICAL: self._build_hierarchical,
            CoverStrategy.DEPENDENCY: self._build_dependency,
            CoverStrategy.SEMANTIC: self._build_semantic,
            CoverStrategy.RANDOM: self._build_random,
        }
        builder_fn = dispatch.get(self.strategy)
        if builder_fn is None:
            raise ValueError(f"Unknown strategy: {self.strategy!r}")

        cover = builder_fn(site)
        self._cover_cache[cache_key] = cover
        return cover

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _build_greedy(self, site: ProjectSite) -> ModuleCover:
        """Greedy merge strategy.

        Start each coordinate as a singleton patch; greedily merge adjacent
        pairs (connected by a morphism in ``site.morphisms``) while the
        merged patch size does not exceed ``self.max_patch_size``.  Repeat
        until no further merges are possible.

        Parameters
        ----------
        site : ProjectSite
            Project site whose morphisms define adjacency.

        Returns
        -------
        ModuleCover
            Cover produced by greedy merging.

        Notes
        -----
        Runs in O(|coords|² × |morphisms|) in the worst case but is fast for
        sparse morphism graphs typical of real project sites.
        """
        coords = _coord_keys(site)
        # Union-find keyed by coord_id → canonical representative
        parent: dict[str, str] = {c: c for c in coords}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> bool:
            rx, ry = find(x), find(y)
            if rx == ry:
                return False
            grp_x = [c for c in coords if find(c) == rx]
            grp_y = [c for c in coords if find(c) == ry]
            if len(grp_x) + len(grp_y) > self.max_patch_size:
                return False
            parent[ry] = rx
            return True

        changed = True
        while changed:
            changed = False
            for morph in getattr(site, "morphisms", []):
                src = getattr(morph, "source", None)
                tgt = getattr(morph, "target", None)
                if src and tgt and src in parent and tgt in parent:
                    if union(src, tgt):
                        changed = True

        grouping: dict[str, list[str]] = collections.defaultdict(list)
        for c in coords:
            grouping[find(c)].append(c)

        return self._assign_to_patches(site, grouping)

    def _build_hierarchical(self, site: ProjectSite) -> ModuleCover:
        """Hierarchical grouping by path prefix.

        Group coordinates by their first path component; split oversized
        groups further by the second path component.

        Parameters
        ----------
        site : ProjectSite
            Project site.

        Returns
        -------
        ModuleCover
            Hierarchically structured cover.
        """
        coords = _coord_keys(site)
        level1: dict[str, list[str]] = collections.defaultdict(list)
        for c in coords:
            level1[_path_prefix(c, 1)].append(c)

        grouping: dict[str, list[str]] = {}
        for prefix, members in level1.items():
            if len(members) <= self.max_patch_size:
                grouping[prefix] = members
            else:
                level2: dict[str, list[str]] = collections.defaultdict(list)
                for c in members:
                    level2[_path_prefix(c, 2)].append(c)
                for sub_prefix, sub_members in level2.items():
                    key = f"{prefix}/{sub_prefix}" if sub_prefix != prefix else f"{prefix}/_"
                    grouping[key] = sub_members

        return self._assign_to_patches(site, grouping)

    def _build_dependency(self, site: ProjectSite) -> ModuleCover:
        """BFS dependency-based cover.

        Perform BFS up to depth 2 from each "root" coordinate (in-degree 0),
        collecting reachable neighbours into a patch.  Coordinates not
        reached by any BFS are added as singleton patches.

        Parameters
        ----------
        site : ProjectSite
            Project site with a ``morphisms`` attribute.

        Returns
        -------
        ModuleCover
            Dependency-clustered cover.
        """
        coords = _coord_keys(site)
        adj: dict[str, list[str]] = collections.defaultdict(list)
        in_deg: dict[str, int] = {c: 0 for c in coords}

        for morph in getattr(site, "morphisms", []):
            src = getattr(morph, "source", None)
            tgt = getattr(morph, "target", None)
            if src and tgt and src in in_deg and tgt in in_deg:
                adj[src].append(tgt)
                in_deg[tgt] += 1

        roots = [c for c, d in in_deg.items() if d == 0] or coords[:1]
        covered: set[str] = set()
        grouping: dict[str, list[str]] = {}

        for root in roots:
            patch: list[str] = []
            queue: collections.deque[tuple[str, int]] = collections.deque([(root, 0)])
            visited: set[str] = {root}
            while queue and len(patch) < self.max_patch_size:
                node, depth = queue.popleft()
                patch.append(node)
                covered.add(node)
                if depth < 2:
                    for nb in adj[node]:
                        if nb not in visited and len(patch) < self.max_patch_size:
                            visited.add(nb)
                            queue.append((nb, depth + 1))
            grouping[f"dep_{root}"] = patch

        for c in coords:
            if c not in covered:
                grouping[f"singleton_{c}"] = [c]

        return self._assign_to_patches(site, grouping)

    def _build_semantic(self, site: ProjectSite) -> ModuleCover:
        """Group coordinates by their :class:`CoordinateKind` value.

        Parameters
        ----------
        site : ProjectSite
            Project site whose coordinates carry ``kind`` attributes.

        Returns
        -------
        ModuleCover
            Semantically homogeneous patches.
        """
        coords = _coord_keys(site)
        grouping: dict[str, list[str]] = collections.defaultdict(list)
        for c in coords:
            coord_obj = site.coordinates.get(c)
            kind_val = (
                coord_obj.kind.value
                if coord_obj and hasattr(coord_obj, "kind")
                else "unknown"
            )
            grouping[kind_val].append(c)

        # Split oversized semantic groups by hash
        final_grouping: dict[str, list[str]] = {}
        for kind_key, members in grouping.items():
            if len(members) <= self.max_patch_size:
                final_grouping[kind_key] = members
            else:
                chunks = [
                    members[i : i + self.max_patch_size]
                    for i in range(0, len(members), self.max_patch_size)
                ]
                for idx, chunk in enumerate(chunks):
                    final_grouping[f"{kind_key}_{idx}"] = chunk

        return self._assign_to_patches(site, final_grouping)

    def _build_random(self, site: ProjectSite) -> ModuleCover:
        """Deterministic pseudo-random cover via MD5 sort.

        Sort coordinates by their MD5 hash for a reproducible but
        content-independent ordering, then slice into patches of size
        ``ceil(n / 4)``.

        Parameters
        ----------
        site : ProjectSite
            Project site.

        Returns
        -------
        ModuleCover
            Pseudo-randomly partitioned cover.
        """
        coords = _coord_keys(site)
        sorted_coords = sorted(
            coords, key=lambda c: hashlib.md5(c.encode()).hexdigest()
        )
        n = len(sorted_coords)
        patch_size = max(1, math.ceil(n / 4))
        grouping: dict[str, list[str]] = {}
        for idx, chunk in enumerate(
            sorted_coords[i : i + patch_size] for i in range(0, n, patch_size)
        ):
            grouping[f"rand_{idx:03d}"] = list(chunk)

        return self._assign_to_patches(site, grouping)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _assign_to_patches(
        self, site: ProjectSite, grouping: dict[str, list[str]]
    ) -> ModuleCover:
        """Materialise a grouping dict into a :class:`ModuleCover`.

        Parameters
        ----------
        site : ProjectSite
            Source project site.
        grouping : dict[str, list[str]]
            Mapping from patch name to list of coordinate IDs.

        Returns
        -------
        ModuleCover
            Fully initialised cover with overlaps and coverage score computed.
        """
        cover = ModuleCover(
            cover_id=_short_id(),
            site_id=getattr(site, "site_id", _short_id()),
            patches=dict(grouping),
            strategy=self.strategy,
        )
        # Compute overlaps and score using helpers
        comp = OverlapComputer()
        cover.pairwise_overlaps = comp.compute_pairwise(cover)
        cover.coverage_score = self._coverage_score(cover, site)
        return cover

    @staticmethod
    def _coverage_score(cover: ModuleCover, site: ProjectSite) -> float:
        """Compute fraction of site coordinates present in at least one patch.

        Parameters
        ----------
        cover : ModuleCover
            Cover to score.
        site : ProjectSite
            Reference site.

        Returns
        -------
        float
            Coverage fraction in [0, 1].
        """
        all_coords = set(_coord_keys(site))
        covered = set(itertools.chain.from_iterable(cover.patches.values()))
        if not all_coords:
            return 1.0
        return len(covered & all_coords) / len(all_coords)

    def validate_cover(self, cover: ModuleCover, site: ProjectSite) -> list[str]:
        """Run validation checks on *cover* against *site*.

        Parameters
        ----------
        cover : ModuleCover
            Cover to validate.
        site : ProjectSite
            Reference project site.

        Returns
        -------
        list[str]
            List of human-readable error strings.  Empty list means valid.
        """
        errors: list[str] = []
        all_coords = set(_coord_keys(site))
        covered = set(itertools.chain.from_iterable(cover.patches.values()))

        # Check full coverage
        missing = all_coords - covered
        if missing:
            errors.append(f"Uncovered coordinates: {sorted(missing)}")

        # Check no empty patches
        for name, members in cover.patches.items():
            if not members:
                errors.append(f"Empty patch: {name!r}")

        # Check overlap tolerance
        comp = OverlapComputer()
        frac = comp.overlap_fraction(cover)
        if frac > self.overlap_tolerance:
            errors.append(
                f"Overlap fraction {frac:.3f} exceeds tolerance {self.overlap_tolerance:.3f}"
            )

        return errors

    def from_topology(
        self, topology: dict[str, list[str]], site: ProjectSite
    ) -> ModuleCover:
        """Construct a :class:`ModuleCover` directly from a topology dict.

        Parameters
        ----------
        topology : dict[str, list[str]]
            Mapping from patch name to list of coordinate IDs.
        site : ProjectSite
            Project site for coverage scoring.

        Returns
        -------
        ModuleCover
            Cover derived from the given topology.
        """
        return self._assign_to_patches(site, topology)


# ---------------------------------------------------------------------------
# 2. OverlapComputer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OverlapComputer:
    """Compute overlap structures for a :class:`ModuleCover`.

    Parameters
    ----------
    max_fold : int
        Maximum fold of overlap to compute (e.g. 3 = pairwise + triple).

    Notes
    -----
    Methods are pure functions of the cover; no state is mutated.
    """

    max_fold: int = 3

    def compute_pairwise(
        self, cover: ModuleCover
    ) -> dict[tuple[str, str], list[str]]:
        """Compute all non-empty pairwise patch intersections.

        Parameters
        ----------
        cover : ModuleCover
            Cover whose patches are intersected.

        Returns
        -------
        dict[tuple[str, str], list[str]]
            Mapping from sorted patch-name pair to sorted intersection coords.
        """
        result: dict[tuple[str, str], list[str]] = {}
        patch_names = sorted(cover.patches.keys())
        for a, b in itertools.combinations(patch_names, 2):
            inter = _intersection([cover.patches[a], cover.patches[b]])
            if inter:
                result[(a, b)] = inter
        return result

    def compute_triple(
        self, cover: ModuleCover
    ) -> dict[tuple[str, str, str], list[str]]:
        """Compute all non-empty triple patch intersections.

        Parameters
        ----------
        cover : ModuleCover
            Cover whose patches are intersected.

        Returns
        -------
        dict[tuple[str, str, str], list[str]]
            Mapping from sorted patch-name triple to sorted intersection coords.
        """
        result: dict[tuple[str, str, str], list[str]] = {}
        patch_names = sorted(cover.patches.keys())
        for a, b, c in itertools.combinations(patch_names, 3):
            inter = _intersection([cover.patches[a], cover.patches[b], cover.patches[c]])
            if inter:
                result[(a, b, c)] = inter
        return result

    def compute_all_folds(self, cover: ModuleCover) -> dict[int, dict]:
        """Compute all fold intersections from 1 to ``self.max_fold``.

        Parameters
        ----------
        cover : ModuleCover
            Target cover.

        Returns
        -------
        dict[int, dict]
            Keys are fold integers; fold 1 maps patch names to their own lists.
        """
        folds: dict[int, dict] = {}
        patch_names = sorted(cover.patches.keys())

        # Fold 1: each patch is its own "intersection"
        folds[1] = {(n,): list(cover.patches[n]) for n in patch_names}

        for k in range(2, min(self.max_fold, len(patch_names)) + 1):
            fold_dict: dict[tuple[str, ...], list[str]] = {}
            for combo in itertools.combinations(patch_names, k):
                inter = _intersection([cover.patches[name] for name in combo])
                if inter:
                    fold_dict[combo] = inter
            folds[k] = fold_dict

        return folds

    def compute_overlap_cells(self, cover: ModuleCover) -> list[OverlapCell]:
        """Create :class:`OverlapCell` objects for every non-empty multi-fold overlap.

        Parameters
        ----------
        cover : ModuleCover
            Source cover.

        Returns
        -------
        list[OverlapCell]
            Overlap cells ordered by fold then patch combination.
        """
        cells: list[OverlapCell] = []
        folds = self.compute_all_folds(cover)
        for fold_k, fold_dict in sorted(folds.items()):
            if fold_k < 2:
                continue
            for combo, coords in fold_dict.items():
                cell = OverlapCell(
                    cell_id=_short_id(),
                    patch_names=list(combo),
                    coord_ids=coords,
                    fold=fold_k,
                )
                cells.append(cell)
        return cells

    def overlap_fraction(self, cover: ModuleCover) -> float:
        """Fraction of total coordinate assignments that are duplicate.

        Parameters
        ----------
        cover : ModuleCover
            Cover to measure.

        Returns
        -------
        float
            ``(total_assignments - unique_coords) / total_assignments``,
            or 0.0 if the cover is empty.
        """
        all_assigned = list(itertools.chain.from_iterable(cover.patches.values()))
        total = len(all_assigned)
        if total == 0:
            return 0.0
        unique = len(set(all_assigned))
        return (total - unique) / total

    def find_most_overlapping_pair(self, cover: ModuleCover) -> tuple[str, str] | None:
        """Find the patch pair with the largest intersection.

        Parameters
        ----------
        cover : ModuleCover
            Target cover.

        Returns
        -------
        tuple[str, str] | None
            Pair with maximum intersection size, or ``None`` if none exists.
        """
        pairwise = self.compute_pairwise(cover)
        if not pairwise:
            return None
        return max(pairwise, key=lambda k: len(pairwise[k]))

    def find_isolated_patches(self, cover: ModuleCover) -> list[str]:
        """Return patches with no pairwise overlap with any other patch.

        Parameters
        ----------
        cover : ModuleCover
            Target cover.

        Returns
        -------
        list[str]
            Sorted list of isolated patch names.
        """
        pairwise = self.compute_pairwise(cover)
        overlapping: set[str] = set()
        for a, b in pairwise.keys():
            overlapping.add(a)
            overlapping.add(b)
        all_names = set(cover.patches.keys())
        return sorted(all_names - overlapping)

    def overlap_graph(self, cover: ModuleCover) -> dict[str, list[str]]:
        """Build an adjacency dict from pairwise overlaps.

        Parameters
        ----------
        cover : ModuleCover
            Target cover.

        Returns
        -------
        dict[str, list[str]]
            Undirected adjacency: ``{patch_name: [neighbour, ...]}``.
        """
        adj: dict[str, list[str]] = {n: [] for n in cover.patches}
        pairwise = self.compute_pairwise(cover)
        for a, b in pairwise.keys():
            adj[a].append(b)
            adj[b].append(a)
        return adj


# ---------------------------------------------------------------------------
# 3. AdmissibilityChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AdmissibilityChecker:
    """Check and repair admissibility of a :class:`ModuleCover`.

    A cover is *admissible* with respect to a Grothendieck topology if it
    satisfies coverage, locality, and closure conditions.

    Parameters
    ----------
    topology : dict
        Topology data; may specify which morphisms define covering families.
    strict : bool
        When ``True``, all checks are required to pass.
    """

    topology: dict = field(default_factory=dict)
    strict: bool = True

    def set_topology(self, topology: dict) -> None:
        """Replace the internal topology data.

        Parameters
        ----------
        topology : dict
            New topology specification.
        """
        object.__setattr__(self, "topology", topology)

    def check(self, cover: ModuleCover, site: ProjectSite) -> dict[str, bool]:
        """Run all admissibility checks and return a results dict.

        Parameters
        ----------
        cover : ModuleCover
            Cover to inspect.
        site : ProjectSite
            Reference site.

        Returns
        -------
        dict[str, bool]
            Keys: ``coverage``, ``locality``, ``refinement_closure``,
            ``composition_closure``.  Values are check outcomes.
        """
        return {
            "coverage": self.check_coverage(cover, site),
            "locality": self.check_locality(cover, site),
            "refinement_closure": self.check_refinement_closure(cover, site),
            "composition_closure": self.check_composition_closure(cover, site),
        }

    def check_coverage(self, cover: ModuleCover, site: ProjectSite) -> bool:
        """Check that every site coordinate appears in at least one patch.

        Parameters
        ----------
        cover : ModuleCover
            Cover to check.
        site : ProjectSite
            Reference site.

        Returns
        -------
        bool
            ``True`` iff every coordinate is covered.
        """
        all_coords = set(_coord_keys(site))
        covered = set(itertools.chain.from_iterable(cover.patches.values()))
        return all_coords <= covered

    def check_locality(self, cover: ModuleCover, site: ProjectSite) -> bool:
        """Check that each patch is locally connected via site morphisms.

        Connectivity is verified by BFS within the patch using the morphism
        graph restricted to patch members.

        Parameters
        ----------
        cover : ModuleCover
            Cover to check.
        site : ProjectSite
            Reference site with ``morphisms``.

        Returns
        -------
        bool
            ``True`` iff every patch is internally connected (or a singleton).
        """
        adj: dict[str, list[str]] = collections.defaultdict(list)
        for morph in getattr(site, "morphisms", []):
            src = getattr(morph, "source", None)
            tgt = getattr(morph, "target", None)
            if src and tgt:
                adj[src].append(tgt)
                adj[tgt].append(src)

        for patch_members in cover.patches.values():
            if len(patch_members) <= 1:
                continue
            member_set = set(patch_members)
            start = patch_members[0]
            visited: set[str] = {start}
            queue: collections.deque[str] = collections.deque([start])
            while queue:
                node = queue.popleft()
                for nb in adj[node]:
                    if nb in member_set and nb not in visited:
                        visited.add(nb)
                        queue.append(nb)
            if visited != member_set:
                return False
        return True

    def check_refinement_closure(self, cover: ModuleCover, site: ProjectSite) -> bool:
        """Spot-check that splitting the largest patch still covers the site.

        Parameters
        ----------
        cover : ModuleCover
            Cover to check.
        site : ProjectSite
            Reference site.

        Returns
        -------
        bool
            ``True`` iff the refined cover remains covering.
        """
        if not cover.patches:
            return False
        largest = max(cover.patches, key=lambda n: len(cover.patches[n]))
        members = cover.patches[largest]
        mid = max(1, len(members) // 2)
        refined_patches = dict(cover.patches)
        del refined_patches[largest]
        refined_patches[f"{largest}_a"] = members[:mid]
        refined_patches[f"{largest}_b"] = members[mid:]
        refined_cover = ModuleCover(
            cover_id=_short_id(),
            site_id=cover.site_id,
            patches=refined_patches,
            strategy=cover.strategy,
        )
        return self.check_coverage(refined_cover, site)

    def check_composition_closure(self, cover: ModuleCover, site: ProjectSite) -> bool:
        """Check that halving all patches still covers the site.

        Parameters
        ----------
        cover : ModuleCover
            Cover to check.
        site : ProjectSite
            Reference site.

        Returns
        -------
        bool
            ``True`` if the halved cover is still covering.
        """
        halved: dict[str, list[str]] = {}
        for name, members in cover.patches.items():
            mid = max(1, len(members) // 2)
            halved[f"{name}_lo"] = members[:mid]
            halved[f"{name}_hi"] = members[mid:]
        halved_cover = ModuleCover(
            cover_id=_short_id(),
            site_id=cover.site_id,
            patches=halved,
            strategy=cover.strategy,
        )
        return self.check_coverage(halved_cover, site)

    def is_admissible(self, cover: ModuleCover, site: ProjectSite) -> bool:
        """Return ``True`` iff all admissibility checks pass.

        Parameters
        ----------
        cover : ModuleCover
            Cover to assess.
        site : ProjectSite
            Reference site.

        Returns
        -------
        bool
            Overall admissibility verdict.
        """
        return all(self.check(cover, site).values())

    def repair_admissibility(
        self, cover: ModuleCover, site: ProjectSite
    ) -> ModuleCover:
        """Attempt to make a cover admissible by minimal surgery.

        Removes empty patches, then appends singleton patches for any
        uncovered coordinate.

        Parameters
        ----------
        cover : ModuleCover
            Potentially inadmissible cover.
        site : ProjectSite
            Reference site.

        Returns
        -------
        ModuleCover
            Repaired cover (may still fail locality checks for disconnected
            morphism graphs).
        """
        new_patches: dict[str, list[str]] = {
            name: members
            for name, members in cover.patches.items()
            if members
        }
        all_coords = set(_coord_keys(site))
        covered = set(itertools.chain.from_iterable(new_patches.values()))
        for c in sorted(all_coords - covered):
            new_patches[f"repair_{c}"] = [c]

        return ModuleCover(
            cover_id=_short_id(),
            site_id=cover.site_id,
            patches=new_patches,
            strategy=cover.strategy,
        )


# ---------------------------------------------------------------------------
# 4. CoverRefiner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoverRefiner:
    """Iteratively refine or coarsen a :class:`ModuleCover`.

    Parameters
    ----------
    max_iterations : int
        Maximum number of refinement steps.
    convergence_eps : float
        If the fractional change in refinement score is below this threshold
        the loop terminates early.
    target_overlap_fraction : float
        Desired overlap fraction; refinement tries to approach this value.
    """

    max_iterations: int = 20
    convergence_eps: float = 1e-4
    target_overlap_fraction: float = 0.2

    def refine_once(
        self, cover: ModuleCover, site: ProjectSite
    ) -> tuple[ModuleCover, float]:
        """Split large patches to reduce overlap above the target threshold.

        Parameters
        ----------
        cover : ModuleCover
            Current cover.
        site : ProjectSite
            Reference site.

        Returns
        -------
        tuple[ModuleCover, float]
            The refined cover and the absolute score delta.
        """
        old_score = self.compute_refinement_score(cover, site)
        n_coords = max(1, sum(len(v) for v in cover.patches.values()))
        split_threshold = max(2, math.ceil(n_coords / max(1, len(cover.patches))))

        new_patches: dict[str, list[str]] = {}
        for name, members in cover.patches.items():
            if len(members) > split_threshold:
                mid = len(members) // 2
                new_patches[f"{name}_0"] = members[:mid]
                new_patches[f"{name}_1"] = members[mid:]
            else:
                new_patches[name] = members

        new_cover = ModuleCover(
            cover_id=_short_id(),
            site_id=cover.site_id,
            patches=new_patches,
            strategy=cover.strategy,
        )
        new_score = self.compute_refinement_score(new_cover, site)
        return new_cover, abs(new_score - old_score)

    def refine_until_admissible(
        self, cover: ModuleCover, site: ProjectSite
    ) -> ModuleCover:
        """Repeatedly refine until the cover is admissible or iterations exhaust.

        Parameters
        ----------
        cover : ModuleCover
            Starting cover.
        site : ProjectSite
            Reference site.

        Returns
        -------
        ModuleCover
            The admissible cover (or best effort if max_iterations reached).
        """
        checker = AdmissibilityChecker()
        current = cover
        for _ in range(self.max_iterations):
            if checker.is_admissible(current, site):
                return current
            current, delta = self.refine_once(current, site)
            if delta < self.convergence_eps:
                break
        # Last resort: repair
        return checker.repair_admissibility(current, site)

    def coarsen_once(
        self, cover: ModuleCover, site: ProjectSite
    ) -> tuple[ModuleCover, float]:
        """Merge the most-overlapping pair of patches.

        Parameters
        ----------
        cover : ModuleCover
            Current cover.
        site : ProjectSite
            Reference site.

        Returns
        -------
        tuple[ModuleCover, float]
            Coarsened cover and absolute score delta.
        """
        old_score = self.compute_refinement_score(cover, site)
        comp = OverlapComputer()
        best_pair = comp.find_most_overlapping_pair(cover)

        if best_pair is None:
            return cover, 0.0

        a, b = best_pair
        merged = sorted(set(cover.patches[a]) | set(cover.patches[b]))
        new_patches = {
            name: members
            for name, members in cover.patches.items()
            if name not in {a, b}
        }
        new_patches[f"{a}__{b}"] = merged

        new_cover = ModuleCover(
            cover_id=_short_id(),
            site_id=cover.site_id,
            patches=new_patches,
            strategy=cover.strategy,
        )
        new_score = self.compute_refinement_score(new_cover, site)
        return new_cover, abs(new_score - old_score)

    def compute_refinement_score(
        self, cover: ModuleCover, site: ProjectSite
    ) -> float:
        """Score a cover as coverage × (1 − overlap_fraction).

        Parameters
        ----------
        cover : ModuleCover
            Cover to score.
        site : ProjectSite
            Reference site.

        Returns
        -------
        float
            Score in [0, 1]; higher is better.
        """
        all_coords = set(_coord_keys(site))
        covered = set(itertools.chain.from_iterable(cover.patches.values()))
        coverage = len(covered & all_coords) / max(1, len(all_coords))
        comp = OverlapComputer()
        overlap = comp.overlap_fraction(cover)
        return coverage * (1.0 - overlap)

    def iterative_refinement(
        self, cover: ModuleCover, site: ProjectSite
    ) -> list[tuple[ModuleCover, float]]:
        """Run up to ``max_iterations`` refinement steps and return history.

        Parameters
        ----------
        cover : ModuleCover
            Starting cover.
        site : ProjectSite
            Reference site.

        Returns
        -------
        list[tuple[ModuleCover, float]]
            List of (cover, score) pairs, one per iteration including initial.
        """
        history: list[tuple[ModuleCover, float]] = []
        current = cover
        history.append((current, self.compute_refinement_score(current, site)))

        for _ in range(self.max_iterations):
            new_cover, delta = self.refine_once(current, site)
            score = self.compute_refinement_score(new_cover, site)
            history.append((new_cover, score))
            current = new_cover
            if delta < self.convergence_eps:
                break

        return history


# ---------------------------------------------------------------------------
# 5. CechNerveComputer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CechNerveComputer:
    """Compute the Čech nerve of a :class:`ModuleCover`.

    The *k*-simplices of the Čech nerve are (k+1)-fold intersecting families
    of patches.  This gives a simplicial approximation to the homotopy type
    of the union of patches.

    Parameters
    ----------
    max_depth : int
        Maximum simplex dimension to compute (0-indexed, so depth 4 means
        up to 4-simplices = 5-fold intersections).
    """

    max_depth: int = 4

    def compute(self, cover: ModuleCover, site: ProjectSite) -> CechNerve:
        """Build the full Čech nerve simplicial complex.

        Parameters
        ----------
        cover : ModuleCover
            Source cover.
        site : ProjectSite
            Reference site.

        Returns
        -------
        CechNerve
            Nerve object constructed from the cover.

        Notes
        -----
        Delegates to :meth:`CechNerve.from_cover` after assembling a
        :class:`Cover` proxy.
        """
        patch_names = sorted(cover.patches.keys())
        cover_obj = Cover(
            cover_id=cover.cover_id,
            members=patch_names,
            member_coords=dict(cover.patches),
        )
        return CechNerve.from_cover(cover_obj)

    def compute_nerve_objects(
        self, cover: ModuleCover, level: int
    ) -> list[tuple[str, ...]]:
        """Return all (level+1)-fold intersecting patch tuples.

        Parameters
        ----------
        cover : ModuleCover
            Source cover.
        level : int
            Simplex dimension (0 = vertices, 1 = edges, …).

        Returns
        -------
        list[tuple[str, ...]]
            Sorted list of patch tuples whose intersection is non-empty.
        """
        patch_names = sorted(cover.patches.keys())
        k = level + 1
        result: list[tuple[str, ...]] = []
        for combo in itertools.combinations(patch_names, k):
            inter = _intersection([cover.patches[name] for name in combo])
            if inter:
                result.append(combo)
        return result

    def compute_face_maps(self, cover: ModuleCover, level: int) -> list[dict]:
        """Compute face maps d_i for the nerve at dimension *level*.

        The i-th face map sends a (level+1)-simplex σ to the level-simplex
        obtained by omitting the i-th element.

        Parameters
        ----------
        cover : ModuleCover
            Source cover.
        level : int
            Simplex dimension of the domain.

        Returns
        -------
        list[dict]
            One dict per i in 0..level; each maps simplex → face.
        """
        simplices = self.compute_nerve_objects(cover, level)
        face_maps: list[dict] = []
        for i in range(level + 1):
            face_map: dict[tuple[str, ...], tuple[str, ...]] = {}
            for sigma in simplices:
                face = tuple(sigma[j] for j in range(len(sigma)) if j != i)
                face_map[sigma] = face
            face_maps.append(face_map)
        return face_maps

    def compute_degeneracy_maps(self, cover: ModuleCover, level: int) -> list[dict]:
        """Compute degeneracy maps s_i for the nerve at dimension *level*.

        The i-th degeneracy map sends a level-simplex σ to the
        (level+1)-simplex obtained by repeating the i-th element.

        Parameters
        ----------
        cover : ModuleCover
            Source cover.
        level : int
            Simplex dimension of the domain.

        Returns
        -------
        list[dict]
            One dict per i in 0..level; each maps simplex → degenerate simplex.
        """
        simplices = self.compute_nerve_objects(cover, level)
        degeneracy_maps: list[dict] = []
        for i in range(level + 1):
            s_map: dict[tuple[str, ...], tuple[str, ...]] = {}
            for sigma in simplices:
                degenerate = tuple(
                    sigma[j] if j != i else sigma[i]
                    for j in range(len(sigma) + 1)
                    if j <= len(sigma)
                )
                # Insert sigma[i] at position i
                lst = list(sigma)
                lst.insert(i, sigma[i] if i < len(sigma) else sigma[-1])
                s_map[sigma] = tuple(lst)
            degeneracy_maps.append(s_map)
        return degeneracy_maps

    def nerve_is_contractible(self, cover: ModuleCover, site: ProjectSite) -> bool:
        """Test contractibility via Euler characteristic.

        A simplicial complex is heuristically considered contractible when
        its Euler characteristic equals 1.

        Parameters
        ----------
        cover : ModuleCover
            Source cover.
        site : ProjectSite
            Reference site (unused directly but required by public API).

        Returns
        -------
        bool
            ``True`` iff ``euler_characteristic(cover) == 1``.
        """
        return self.euler_characteristic(cover) == 1

    def euler_characteristic(self, cover: ModuleCover) -> int:
        """Compute the Euler characteristic of the Čech nerve.

        χ = Σ_{k=0}^{max_depth} (-1)^k × |k-simplices|

        Parameters
        ----------
        cover : ModuleCover
            Source cover.

        Returns
        -------
        int
            Euler characteristic of the nerve.
        """
        chi = 0
        for k in range(self.max_depth + 1):
            count = len(self.compute_nerve_objects(cover, k))
            chi += ((-1) ** k) * count
        return chi

    def to_simplicial_set(self, cover: ModuleCover) -> dict[str, Any]:
        """Serialise the Čech nerve to a plain dict.

        Parameters
        ----------
        cover : ModuleCover
            Source cover.

        Returns
        -------
        dict[str, Any]
            Keys ``cover_id``, ``max_depth``, ``euler_characteristic``,
            ``simplices`` (list by dimension), ``face_maps``,
            ``degeneracy_maps``.
        """
        simplices_by_dim: list[list[list[str]]] = []
        face_maps_by_dim: list[list[dict]] = []
        degeneracy_maps_by_dim: list[list[dict]] = []

        for k in range(self.max_depth + 1):
            objs = self.compute_nerve_objects(cover, k)
            simplices_by_dim.append([list(s) for s in objs])
            if objs:
                face_maps_by_dim.append(
                    [
                        {str(sigma): list(face) for sigma, face in fm.items()}
                        for fm in self.compute_face_maps(cover, k)
                    ]
                )
                degeneracy_maps_by_dim.append(
                    [
                        {str(sigma): list(degen) for sigma, degen in dm.items()}
                        for dm in self.compute_degeneracy_maps(cover, k)
                    ]
                )
            else:
                face_maps_by_dim.append([])
                degeneracy_maps_by_dim.append([])

        return {
            "cover_id": cover.cover_id,
            "max_depth": self.max_depth,
            "euler_characteristic": self.euler_characteristic(cover),
            "simplices": simplices_by_dim,
            "face_maps": face_maps_by_dim,
            "degeneracy_maps": degeneracy_maps_by_dim,
        }


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def build_module_cover(
    site: ProjectSite,
    strategy: CoverStrategy = CoverStrategy.GREEDY,
    max_patch_size: int = 15,
) -> ModuleCover:
    """Build a :class:`ModuleCover` for *site* using the given strategy.

    Parameters
    ----------
    site : ProjectSite
        Project site to cover.
    strategy : CoverStrategy, optional
        Construction strategy, by default ``GREEDY``.
    max_patch_size : int, optional
        Maximum number of coordinates per patch, by default 15.

    Returns
    -------
    ModuleCover
        Freshly built cover.
    """
    builder = CoverBuilder(strategy=strategy, max_patch_size=max_patch_size)
    return builder.build(site)


def refine_cover_until_admissible(
    cover: ModuleCover,
    site: ProjectSite,
    max_iter: int = 20,
) -> ModuleCover:
    """Refine *cover* until it satisfies admissibility conditions.

    Parameters
    ----------
    cover : ModuleCover
        Starting cover, possibly inadmissible.
    site : ProjectSite
        Reference site.
    max_iter : int, optional
        Maximum refinement iterations, by default 20.

    Returns
    -------
    ModuleCover
        Admissible cover (or best-effort after ``max_iter`` iterations).
    """
    refiner = CoverRefiner(max_iterations=max_iter)
    return refiner.refine_until_admissible(cover, site)


def score_cover_quality(cover: ModuleCover, site: ProjectSite) -> float:
    """Compute a quality score for a cover, penalising if checker is non-strict.

    Parameters
    ----------
    cover : ModuleCover
        Cover to score.
    site : ProjectSite
        Reference site.

    Returns
    -------
    float
        Quality score in [0, 1].  Full weight when admissibility passes under
        strict mode; 0.8× when the checker must relax to non-strict mode.
    """
    refiner = CoverRefiner()
    base_score = refiner.compute_refinement_score(cover, site)
    checker = AdmissibilityChecker(strict=True)
    if checker.is_admissible(cover, site):
        return base_score * 1.0
    return base_score * 0.8


def compute_cover_overlap_matrix(
    cover: ModuleCover,
) -> dict[str, dict[str, int]]:
    """Compute a symmetric overlap-count matrix for all patch pairs.

    Parameters
    ----------
    cover : ModuleCover
        Source cover.

    Returns
    -------
    dict[str, dict[str, int]]
        ``matrix[a][b]`` = number of coordinates in the intersection of
        patch *a* and patch *b*; diagonal = patch size.
    """
    patch_names = sorted(cover.patches.keys())
    matrix: dict[str, dict[str, int]] = {n: {} for n in patch_names}

    for n in patch_names:
        matrix[n][n] = len(cover.patches[n])

    comp = OverlapComputer()
    pairwise = comp.compute_pairwise(cover)
    for (a, b), coords in pairwise.items():
        sz = len(coords)
        matrix[a][b] = sz
        matrix[b][a] = sz

    # Fill missing entries with 0
    for a in patch_names:
        for b in patch_names:
            matrix[a].setdefault(b, 0)

    return matrix


def find_optimal_patch_count(
    site: ProjectSite,
    min_patches: int = 2,
    max_patches: int = 20,
) -> int:
    """Search for the patch count that maximises cover quality.

    Tries a range of ``max_patch_size`` values and scores each resulting
    cover, returning the patch count of the best-scoring cover.

    Parameters
    ----------
    site : ProjectSite
        Project site.
    min_patches : int, optional
        Minimum number of patches to consider, by default 2.
    max_patches : int, optional
        Maximum number of patches to consider, by default 20.

    Returns
    -------
    int
        Actual patch count of the highest-scoring cover found.

    Notes
    -----
    Larger max_patch_size values yield fewer, bigger patches; the function
    explores the inverse relationship between patch size and count.
    """
    n_coords = len(_coord_keys(site))
    if n_coords == 0:
        return min_patches

    best_score = -1.0
    best_count = min_patches

    for target_count in range(min_patches, max_patches + 1):
        patch_size = max(1, math.ceil(n_coords / target_count))
        builder = CoverBuilder(
            strategy=CoverStrategy.GREEDY, max_patch_size=patch_size
        )
        cover = builder.build(site)
        score = score_cover_quality(cover, site)
        actual_count = len(cover.patches)
        if score > best_score:
            best_score = score
            best_count = actual_count

    return best_count


# copilot: §8.2 module-covers implementation — CoverBuilder, OverlapComputer,
# AdmissibilityChecker, CoverRefiner, CechNerveComputer are designed for
# LLM-assisted module cover construction and analysis workflows.
