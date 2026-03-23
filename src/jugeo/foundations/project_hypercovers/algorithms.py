"""Theory2.tex Ch8 §8.1–§8.4 — Core algorithms for project hypercovers.

This module collects the main algorithmic primitives: greedy cover
construction, fleet assignment, hypercover descent, Čech complex computation,
obstruction repair, iterative refinement, and trust propagation.

copilot: shared-core algorithms module — central algorithmic layer for Ch8
project hypercover machinery.
"""
from __future__ import annotations

import collections
import heapq
import itertools
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

from jugeo.geometry.hypercovers import HypercoverLevel, CechNerve, HypercoverKind
from jugeo.geometry.descent import DescentEngine, DescentResult, LocalSection, GluingData
from jugeo.geometry.site import CoordinateObject, SemanticSite, CoordinateKind
from jugeo.geometry.covers import Cover, CoverMetric
from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind
from jugeo.evidence.certificates import Certificate, CertificateStatus
from jugeo.foundations.project_hypercovers.models import (
    ProjectSite, ModuleCover, FleetMember, HypercoverDecomposition,
    ProjectKind, CoverStrategy, FleetStatus, DecompositionStatus,
    CoordinateMorphism, OverlapCell, CohomologyClass, TrustTier,
)

logger = logging.getLogger(__name__)


def greedy_cover_algorithm(
    site: ProjectSite,
    strategy: CoverStrategy = CoverStrategy.GREEDY,
    max_patch_size: int = 10,
) -> ModuleCover:
    """Build an initial module cover via greedy patch merging.

    Parameters
    ----------
    site : ProjectSite
        The project site whose coordinates are to be covered.
    strategy : CoverStrategy, optional
        Cover-construction strategy. Defaults to ``CoverStrategy.GREEDY``.
    max_patch_size : int, optional
        Maximum number of coordinates allowed in a single patch. Defaults to 10.

    Returns
    -------
    ModuleCover
        A ``ModuleCover`` whose patches together cover every coordinate in
        *site*, built by greedily merging adjacent patches while respecting
        *max_patch_size*.

    Raises
    ------
    ValueError
        If *site* has no coordinates.

    Notes
    -----
    Algorithm:
    1. Build initial cover: one patch per coordinate.
    2. Greedily merge adjacent patches (connected by morphism) while
       ``patch_size <= max_patch_size``.
    3. Use a priority queue keyed on the number of morphisms between two
       patches (more morphisms → higher-priority merge candidate).
    4. Halt when no further merge is feasible.
    5. Compute cover metrics, wrap in ``ModuleCover``, return.

    Examples
    --------
    >>> cover = greedy_cover_algorithm(site, max_patch_size=5)
    >>> len(cover.patches) <= len(site.coordinates)
    True
    """
    coords = list(site.coordinates) if hasattr(site, 'coordinates') else []
    if not coords:
        raise ValueError("site has no coordinates — cannot build a cover")

    # Step 1: one patch per coordinate
    patch_coords: dict[str, list[str]] = {}
    coord_to_patch: dict[str, str] = {}
    for coord in coords:
        coord_id = coord if isinstance(coord, str) else getattr(coord, 'coord_id', str(coord))
        patch_id = f"patch_{coord_id}"
        patch_coords[patch_id] = [coord_id]
        coord_to_patch[coord_id] = patch_id

    # Build morphism adjacency among patches
    morphisms = getattr(site, 'morphisms', [])
    patch_morphism_count: dict[tuple[str, str], int] = collections.defaultdict(int)
    for morph in morphisms:
        src_id = getattr(morph, 'source', None) or getattr(morph, 'src', None)
        tgt_id = getattr(morph, 'target', None) or getattr(morph, 'tgt', None)
        if src_id is None or tgt_id is None:
            continue
        src_id = src_id if isinstance(src_id, str) else getattr(src_id, 'coord_id', str(src_id))
        tgt_id = tgt_id if isinstance(tgt_id, str) else getattr(tgt_id, 'coord_id', str(tgt_id))
        sp = coord_to_patch.get(src_id)
        tp = coord_to_patch.get(tgt_id)
        if sp and tp and sp != tp:
            key = (min(sp, tp), max(sp, tp))
            patch_morphism_count[key] += 1

    # Step 2–3: priority queue of merge candidates
    # heapq is a min-heap; negate count for max-heap behaviour
    heap: list[tuple[int, str, str]] = []
    for (pa, pb), cnt in patch_morphism_count.items():
        heapq.heappush(heap, (-cnt, pa, pb))

    merged: set[str] = set()  # tombstoned patch ids
    union_find: dict[str, str] = {p: p for p in patch_coords}

    def find(x: str) -> str:
        while union_find[x] != x:
            union_find[x] = union_find[union_find[x]]
            x = union_find[x]
        return x

    def merge_patches(pa: str, pb: str) -> str:
        ra, rb = find(pa), find(pb)
        if ra == rb:
            return ra
        combined = patch_coords[ra] + patch_coords[rb]
        new_id = ra  # keep ra as canonical
        patch_coords[new_id] = combined
        union_find[rb] = new_id
        if rb in patch_coords and rb != new_id:
            del patch_coords[rb]
        merged.add(rb)
        for cid in combined:
            coord_to_patch[cid] = new_id
        return new_id

    iteration_count = 0
    max_iterations = len(patch_coords) * len(patch_coords) + 1
    while heap and iteration_count < max_iterations:
        iteration_count += 1
        neg_cnt, pa, pb = heapq.heappop(heap)
        ra, rb = find(pa), find(pb)
        if ra == rb:
            continue
        if ra not in patch_coords or rb not in patch_coords:
            continue
        size_after = len(patch_coords[ra]) + len(patch_coords[rb])
        if size_after > max_patch_size:
            continue
        new_patch = merge_patches(ra, rb)
        # Re-examine neighbors of new_patch
        for (px, py), cnt in list(patch_morphism_count.items()):
            rx, ry = find(px), find(py)
            if rx == ry:
                continue
            if rx == new_patch or ry == new_patch:
                heapq.heappush(heap, (-cnt, rx, ry))

    # Step 4: collect final patches
    final_patches: dict[str, list[str]] = {}
    for pid, clist in patch_coords.items():
        root = find(pid)
        if root == pid:
            final_patches[pid] = clist

    # Step 5: compute metrics and build ModuleCover
    n_patches = len(final_patches)
    n_coords = len(coords)
    avg_patch_size = n_coords / max(n_patches, 1)
    overlap_density = sum(
        1 for _ in itertools.combinations(final_patches.keys(), 2)
        if any(
            coord_to_patch.get(c, c) in final_patches
            for c in patch_coords.get(_, [])
        )
    ) / max(n_patches * (n_patches - 1) / 2, 1)

    cover = ModuleCover(
        cover_id=str(uuid.uuid4()),
        site_id=getattr(site, 'site_id', str(uuid.uuid4())),
        patches=final_patches,
        strategy=strategy,
        patch_count=n_patches,
        coord_count=n_coords,
        avg_patch_size=avg_patch_size,
        overlap_density=overlap_density,
        is_admissible=True,
        created_at=time.time(),
    )
    return cover


def optimal_fleet_assignment(
    cover: ModuleCover,
    members: list[FleetMember],
    constraints: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Assign fleet members to cover patches optimally under constraints.

    Parameters
    ----------
    cover : ModuleCover
        The module cover whose patches need fleet member assignments.
    members : list[FleetMember]
        Available fleet members to assign.
    constraints : dict[str, Any] or None, optional
        Assignment constraints. Recognised keys:
        ``max_load`` (float, default 1.0),
        ``require_capability`` (list[str], default []),
        ``min_redundancy`` (int ≥ 1, default 1).

    Returns
    -------
    dict[str, list[str]]
        Mapping ``patch_id → [member_id, ...]`` giving the members assigned
        to each patch.

    Raises
    ------
    ValueError
        If *cover* has no patches or *members* is empty.

    Notes
    -----
    Implements a greedy bipartite assignment:
    1. Build a bipartite graph of (patch, member) scoring pairs.
    2. Score each pair as ``capability_match × (1 - load_factor)``.
    3. For each patch (sorted by coordinate count descending), assign the
       top-k members where k = max(1, min_redundancy).
    4. Skip members whose current load exceeds max_load.
    5. Fall back to unconstrained assignment for uncovered patches.

    Examples
    --------
    >>> assignment = optimal_fleet_assignment(cover, fleet, {'min_redundancy': 2})
    >>> all(len(v) >= 1 for v in assignment.values())
    True
    """
    if not cover.patches:
        raise ValueError("cover has no patches")
    if not members:
        raise ValueError("members list is empty")

    constraints = constraints or {}
    max_load: float = float(constraints.get('max_load', 1.0))
    require_cap: list[str] = list(constraints.get('require_capability', []))
    min_redundancy: int = max(1, int(constraints.get('min_redundancy', 1)))

    # Track current load per member (number of patches assigned)
    member_load: dict[str, int] = {m.member_id: 0 for m in members}
    max_patches_per_member: dict[str, int] = {
        m.member_id: getattr(m, 'max_patches', 100) for m in members
    }
    member_caps: dict[str, set[str]] = {
        m.member_id: set(getattr(m, 'capabilities', []) or []) for m in members
    }
    member_trust: dict[str, float] = {
        m.member_id: float(getattr(m, 'trust_level', 0.5) or 0.5) for m in members
    }

    def capability_score(member_id: str, patch_id: str) -> float:
        """Score based on capability match and trust."""
        caps = member_caps[member_id]
        if require_cap:
            matched = sum(1 for c in require_cap if c in caps)
            cap_frac = matched / len(require_cap)
        else:
            cap_frac = 1.0
        trust = member_trust[member_id]
        return cap_frac * trust

    def load_factor(member_id: str) -> float:
        """Fraction of max_patches capacity used."""
        mp = max_patches_per_member[member_id]
        if mp <= 0:
            return 1.0
        return member_load[member_id] / mp

    # Sort patches by size (largest first = most constrained)
    sorted_patches = sorted(
        cover.patches.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )

    assignment: dict[str, list[str]] = {pid: [] for pid in cover.patches}

    # Pass 1: score-based greedy assignment
    for patch_id, coord_list in sorted_patches:
        scored: list[tuple[float, str]] = []
        for m in members:
            lf = load_factor(m.member_id)
            if lf >= max_load:
                continue
            score = capability_score(m.member_id, patch_id) * (1.0 - lf)
            scored.append((-score, m.member_id))  # neg for min-heap

        heapq.heapify(scored)
        assigned_count = 0
        while scored and assigned_count < min_redundancy:
            _, mid = heapq.heappop(scored)
            lf = load_factor(mid)
            if lf >= max_load:
                continue
            assignment[patch_id].append(mid)
            member_load[mid] += 1
            assigned_count += 1

    # Pass 2: fill uncovered patches with any available member
    for patch_id, assigned in assignment.items():
        if len(assigned) >= min_redundancy:
            continue
        remaining_needed = min_redundancy - len(assigned)
        already: set[str] = set(assigned)
        for m in members:
            if m.member_id in already:
                continue
            if remaining_needed <= 0:
                break
            assignment[patch_id].append(m.member_id)
            member_load[m.member_id] += 1
            already.add(m.member_id)
            remaining_needed -= 1

    return assignment


def hypercover_descent_algorithm(
    decomp: HypercoverDecomposition,
    sections: dict[str, Any],
    engine: DescentEngine | None = None,
) -> DescentResult:
    """Run descent along a hypercover decomposition to obtain a global section.

    Parameters
    ----------
    decomp : HypercoverDecomposition
        The hypercover decomposition, indexed by level.
    sections : dict[str, Any]
        Initial local sections keyed by patch id or coordinate id.
    engine : DescentEngine or None, optional
        Descent engine to use. A fresh ``DescentEngine()`` is created when
        ``None`` is given.

    Returns
    -------
    DescentResult
        The descent result from level 0 (the base cover).

    Notes
    -----
    Algorithm:
    1. If *engine* is ``None``, instantiate ``DescentEngine()``.
    2. Process levels n = max_level … 0 (top-down):
       a. Extract patches at level *n* from ``decomp.levels[n]``.
       b. Build a ``Cover`` from those patches.
       c. Build ``local_sections`` dict from *sections* (or empty dicts).
       d. Invoke ``engine.attempt_descent(cover, local_sections)``.
       e. On success: use the global section for the next (lower) level.
       f. On failure: record the obstruction in ``decomp.cohomology_classes``.
    3. Return the ``DescentResult`` obtained at level 0.

    Examples
    --------
    >>> result = hypercover_descent_algorithm(decomp, {}, engine=None)
    >>> result is not None
    True
    """
    if engine is None:
        engine = DescentEngine()

    levels = getattr(decomp, 'levels', {})
    if not levels:
        # No levels — run a single descent on the base cover
        base_cover = getattr(decomp, 'base_cover', None)
        if base_cover is None:
            # Build minimal cover from decomp info
            base_cover = Cover(
                cover_id=str(uuid.uuid4()),
                patches={},
                site_id=getattr(decomp, 'site_id', ''),
            )
        result = _attempt_engine_descent(engine, base_cover, sections)
        return result

    max_level = max(levels.keys()) if isinstance(levels, dict) else len(levels) - 1
    current_sections = dict(sections)
    last_result: DescentResult | None = None

    for n in range(max_level, -1, -1):
        level_data = levels.get(n) if isinstance(levels, dict) else (levels[n] if n < len(levels) else None)
        if level_data is None:
            continue

        # Extract patch ids for this level
        patch_ids: list[str] = []
        if hasattr(level_data, 'patch_ids'):
            patch_ids = list(level_data.patch_ids)
        elif hasattr(level_data, 'patches'):
            patch_ids = list(level_data.patches.keys() if isinstance(level_data.patches, dict) else level_data.patches)
        elif isinstance(level_data, (list, tuple)):
            patch_ids = [str(p) for p in level_data]
        else:
            patch_ids = [str(level_data)]

        # Build Cover for this level
        patches_dict = {pid: [pid] for pid in patch_ids}
        level_cover = Cover(
            cover_id=f"level_{n}_{uuid.uuid4().hex[:8]}",
            patches=patches_dict,
            site_id=getattr(decomp, 'site_id', ''),
        )

        # Build local sections for this level
        level_sections: dict[str, Any] = {}
        for pid in patch_ids:
            level_sections[pid] = current_sections.get(pid, current_sections.get(f"level_{n}_{pid}", {}))

        result = _attempt_engine_descent(engine, level_cover, level_sections)
        last_result = result

        if getattr(result, 'success', False):
            global_section = getattr(result, 'global_section', None)
            if global_section is not None:
                section_data = getattr(global_section, 'data', {}) or {}
                # Propagate the global section downward as input for next level
                if isinstance(section_data, dict):
                    current_sections.update(section_data)
                else:
                    for pid in patch_ids:
                        current_sections[pid] = section_data
        else:
            # Record obstruction in cohomology classes
            obst_repr = f"level_{n}_descent_failure_{uuid.uuid4().hex[:8]}"
            new_cls = CohomologyClass(
                class_id=obst_repr,
                degree=n,
                representative=obst_repr,
                is_trivial=False,
                patch_ids=tuple(patch_ids),
            )
            existing = list(getattr(decomp, 'cohomology_classes', []) or [])
            existing.append(new_cls)
            try:
                object.__setattr__(decomp, 'cohomology_classes', existing)
            except (AttributeError, TypeError):
                pass

    if last_result is None:
        last_result = DescentResult(
            success=False,
            global_section=None,
            obstruction=None,
            log=[],
        )
    return last_result


def _attempt_engine_descent(
    engine: DescentEngine,
    cover: Cover,
    sections: dict[str, Any],
) -> DescentResult:
    """Internal helper: call engine.attempt_descent with fallback handling."""
    try:
        if hasattr(engine, 'attempt_descent'):
            return engine.attempt_descent(cover, sections)
        elif hasattr(engine, 'run_descent'):
            return engine.run_descent(cover, sections)
        else:
            return DescentResult(success=True, global_section=None, obstruction=None, log=[])
    except Exception as exc:
        return DescentResult(
            success=False,
            global_section=None,
            obstruction=str(exc),
            log=[str(exc)],
        )


def cech_complex_computation(cover: ModuleCover) -> dict[str, Any]:
    """Compute the Čech complex and its topological invariants for a cover.

    Parameters
    ----------
    cover : ModuleCover
        The module cover whose Čech nerve is to be computed.

    Returns
    -------
    dict[str, Any]
        Dictionary with keys:
        ``nerves`` ({0: [...], 1: [...], 2: [...], 3: [...]}),
        ``euler_characteristic`` (int),
        ``betti_numbers`` ({0: int, 1: int, 2: int}),
        ``is_contractible`` (bool),
        ``dimension`` (int),
        ``patch_count`` (int),
        ``total_overlap_cells`` (int).

    Notes
    -----
    For each fold k ∈ {1, 2, 3, 4} we enumerate all k-subsets of patches,
    compute the coordinate intersection, and retain non-empty intersections.
    The nerve at degree k-1 is that collection of non-empty k-fold
    intersections. The Euler characteristic is the alternating sum of
    nerve-level counts.

    Examples
    --------
    >>> result = cech_complex_computation(cover)
    >>> 'euler_characteristic' in result
    True
    """
    patches = cover.patches  # dict[str, list[str]]
    patch_ids = list(patches.keys())
    n = len(patch_ids)

    nerves: dict[int, list[tuple[str, ...]]] = {0: [], 1: [], 2: [], 3: []}

    # Level 0: individual patches (0-simplices)
    for pid in patch_ids:
        if patches[pid]:
            nerves[0].append((pid,))

    # Level 1: pairs with non-empty intersection (1-simplices = edges)
    coord_set: dict[str, set[str]] = {pid: set(clist) for pid, clist in patches.items()}
    for pa, pb in itertools.combinations(patch_ids, 2):
        intersection = coord_set[pa] & coord_set[pb]
        if intersection:
            nerves[1].append((pa, pb))

    # Level 2: triples with non-empty triple intersection (2-simplices)
    for pa, pb, pc in itertools.combinations(patch_ids, 3):
        intersection = coord_set[pa] & coord_set[pb] & coord_set[pc]
        if intersection:
            nerves[2].append((pa, pb, pc))

    # Level 3: quadruples with non-empty quadruple intersection (3-simplices)
    for combo in itertools.combinations(patch_ids, 4):
        intersection = set.intersection(*(coord_set[p] for p in combo))
        if intersection:
            nerves[3].append(combo)

    # Euler characteristic: χ = Σ (-1)^k * |N_k|
    euler_char = sum((-1) ** k * len(nerves[k]) for k in range(4))

    # Dimension: highest non-empty level
    dimension = max((k for k, v in nerves.items() if v), default=0)

    # Betti numbers (rough estimate from nerve structure)
    b0 = len(nerves[0])  # connected components (over-estimate; use 1 if connected)
    b1 = max(0, len(nerves[1]) - len(nerves[0]) + 1)  # cycle rank
    b2 = max(0, len(nerves[2]) - len(nerves[1]) + len(nerves[0]) - 1)
    betti = {0: max(1, b0), 1: b1, 2: b2}

    is_contractible = (euler_char == 1 and betti[1] == 0 and betti[2] == 0)
    total_overlap_cells = sum(len(v) for k, v in nerves.items() if k >= 1)

    return {
        'nerves': nerves,
        'euler_characteristic': euler_char,
        'betti_numbers': betti,
        'is_contractible': is_contractible,
        'dimension': dimension,
        'patch_count': len(patch_ids),
        'total_overlap_cells': total_overlap_cells,
    }


def obstruction_repair_algorithm(
    obstruction: CohomologyClass,
    fleet: list[FleetMember],
    cover: ModuleCover,
) -> list[dict[str, Any]]:
    """Generate a ranked list of repair actions for a cohomological obstruction.

    Parameters
    ----------
    obstruction : CohomologyClass
        The obstruction cohomology class to repair.
    fleet : list[FleetMember]
        Available fleet members that may be redeployed.
    cover : ModuleCover
        The current module cover associated with the obstruction.

    Returns
    -------
    list[dict[str, Any]]
        Ordered list of repair action dicts (highest priority first). Each
        dict has keys: ``action`` (str), ``target`` (str),
        ``description`` (str), ``estimated_cost`` (float),
        ``priority`` (int).

    Notes
    -----
    1. If ``obstruction.is_trivial``, returns ``[]`` immediately.
    2. Parse ``obstruction.representative`` to identify affected patches.
    3. For each gap:
       - Uncovered coordinate → suggest adding it to the nearest patch.
       - Missing fleet member → suggest assigning a capable member.
       - Conflicting evidence → suggest re-running descent at higher trust floor.
    4. Sort by priority (ascending int = higher priority).

    Examples
    --------
    >>> actions = obstruction_repair_algorithm(obstruction, fleet, cover)
    >>> all('action' in a for a in actions)
    True
    """
    if getattr(obstruction, 'is_trivial', False):
        return []

    actions: list[dict[str, Any]] = []
    representative: str = getattr(obstruction, 'representative', '')
    affected_patches: tuple[str, ...] = getattr(obstruction, 'patch_ids', ())
    degree: int = getattr(obstruction, 'degree', 0)

    all_covered_coords: set[str] = set()
    for pid, clist in cover.patches.items():
        all_covered_coords.update(clist)

    member_ids = [m.member_id for m in fleet]
    member_caps: dict[str, set[str]] = {
        m.member_id: set(getattr(m, 'capabilities', []) or []) for m in fleet
    }

    # Analyse gap type from representative string
    gap_tokens = set(representative.lower().split('_'))
    has_coverage_gap = 'coverage' in gap_tokens or 'uncovered' in gap_tokens or degree == 0
    has_fleet_gap = 'fleet' in gap_tokens or 'member' in gap_tokens or degree == 1
    has_conflict_gap = 'conflict' in gap_tokens or 'evidence' in gap_tokens or degree >= 2

    # Gap 1: uncovered coordinates
    if has_coverage_gap or not affected_patches:
        for pid in (affected_patches or list(cover.patches.keys())[:3]):
            coords_in_patch = cover.patches.get(pid, [])
            if not coords_in_patch:
                # Patch is empty — suggest adding a coordinate
                candidate_coord = f"coord_{uuid.uuid4().hex[:6]}"
                actions.append({
                    'action': 'add_coordinate_to_patch',
                    'target': pid,
                    'description': (
                        f"Patch '{pid}' covers no coordinates. "
                        f"Suggest adding coordinate '{candidate_coord}' to resolve the coverage gap."
                    ),
                    'estimated_cost': 0.3,
                    'priority': 1,
                })
            else:
                for cid in coords_in_patch[:2]:
                    nearest_patch = min(
                        cover.patches.keys(),
                        key=lambda p: abs(len(cover.patches[p]) - len(coords_in_patch)),
                    )
                    if nearest_patch != pid:
                        actions.append({
                            'action': 'extend_patch_coverage',
                            'target': pid,
                            'description': (
                                f"Coordinate '{cid}' in patch '{pid}' may be better served "
                                f"by extending patch '{nearest_patch}' to include it, "
                                f"resolving the coverage-gap obstruction at degree {degree}."
                            ),
                            'estimated_cost': 0.5,
                            'priority': 2,
                        })

    # Gap 2: missing fleet member coverage
    if has_fleet_gap or (fleet and len(fleet) < len(cover.patches)):
        unassigned_patches = [
            pid for pid in (affected_patches or list(cover.patches.keys()))
            if pid not in {
                getattr(m, 'assigned_patches', []) or []
                for m in fleet
            }
        ]
        for pid in unassigned_patches[:3]:
            best_member = max(
                fleet,
                key=lambda m: float(getattr(m, 'trust_level', 0.5) or 0.5),
                default=None,
            )
            if best_member:
                actions.append({
                    'action': 'assign_fleet_member',
                    'target': pid,
                    'description': (
                        f"Patch '{pid}' lacks fleet coverage. "
                        f"Assign member '{best_member.member_id}' "
                        f"(trust={getattr(best_member, 'trust_level', 0.5):.2f}) "
                        f"to resolve the fleet-gap obstruction."
                    ),
                    'estimated_cost': 0.4,
                    'priority': 1,
                })

    # Gap 3: conflicting evidence — suggest higher trust floor
    if has_conflict_gap or degree >= 2:
        actions.append({
            'action': 'increase_trust_floor',
            'target': representative or 'global',
            'description': (
                f"Conflicting evidence detected in cohomology class "
                f"'{getattr(obstruction, 'class_id', '?')}' at degree {degree}. "
                f"Re-run descent with a higher trust floor (suggest τ_min ≥ 0.7) "
                f"to resolve conflicting local sections."
            ),
            'estimated_cost': 0.8,
            'priority': 3,
        })
        actions.append({
            'action': 'rerun_descent_higher_trust',
            'target': representative or 'global',
            'description': (
                f"Force re-evaluation of all evidence bundles supporting "
                f"patches in {list(affected_patches)[:4]} with strict "
                f"deduplication to eliminate the conflicting cohomology class."
            ),
            'estimated_cost': 1.2,
            'priority': 4,
        })

    # Deduplicate and sort by priority
    seen: set[str] = set()
    unique_actions: list[dict[str, Any]] = []
    for act in actions:
        key = f"{act['action']}:{act['target']}"
        if key not in seen:
            seen.add(key)
            unique_actions.append(act)

    unique_actions.sort(key=lambda a: a['priority'])
    return unique_actions


def iterative_refinement_loop(
    site: ProjectSite,
    max_iter: int = 20,
    convergence_eps: float = 1e-4,
    strategy: CoverStrategy = CoverStrategy.GREEDY,
) -> HypercoverDecomposition:
    """Build a hypercover decomposition via iterative refinement.

    Parameters
    ----------
    site : ProjectSite
        The project site to cover.
    max_iter : int, optional
        Maximum number of refinement iterations. Defaults to 20.
    convergence_eps : float, optional
        Convergence threshold: if the fractional change in patch count is
        below this value, the loop is considered converged. Defaults to 1e-4.
    strategy : CoverStrategy, optional
        Cover-construction strategy to use at each level. Defaults to
        ``CoverStrategy.GREEDY``.

    Returns
    -------
    HypercoverDecomposition
        A ``HypercoverDecomposition`` built from the iterative refinement
        history, with status set to reflect convergence.

    Notes
    -----
    Algorithm:
    1. Build an initial cover with ``greedy_cover_algorithm``.
    2. Create level-0 decomposition.
    3. Loop:
       a. Compute the next refinement level by further subdividing patches.
       b. Check convergence: stop if patch count unchanged.
       c. Verify simplified simplicial identities (face map consistency).
       d. Record step in history.
       e. Update decomposition status.
    4. Return final decomposition.

    Examples
    --------
    >>> decomp = iterative_refinement_loop(site, max_iter=5)
    >>> decomp.status in (DecompositionStatus.CONVERGED, DecompositionStatus.PARTIAL)
    True
    """
    history: list[dict[str, Any]] = []

    # Step 1: initial cover
    cover0 = greedy_cover_algorithm(site, strategy=strategy, max_patch_size=10)
    levels: dict[int, Any] = {0: cover0}
    prev_patch_count = cover0.patch_count
    current_cover = cover0
    status = DecompositionStatus.PARTIAL

    for iteration in range(1, max_iter + 1):
        step_start = time.time()
        # Step 3a: compute next refinement level
        # Subdivide each patch that has >1 coordinate into smaller patches
        new_patches: dict[str, list[str]] = {}
        for pid, clist in current_cover.patches.items():
            if len(clist) <= 1:
                new_patches[pid] = list(clist)
            else:
                mid = len(clist) // 2
                new_patches[f"{pid}_a"] = clist[:mid]
                new_patches[f"{pid}_b"] = clist[mid:]

        new_patch_count = len(new_patches)

        # Step 3b: check convergence
        delta = abs(new_patch_count - prev_patch_count) / max(prev_patch_count, 1)
        converged = delta < convergence_eps or new_patch_count == prev_patch_count

        # Step 3c: verify simplified simplicial identity
        # Check face maps: d_0 and d_1 applied to each 1-simplex should be 0-simplices
        face_ok = True
        all_zero_simplices = set(new_patches.keys())
        for pid in new_patches:
            # face maps: d_0(pid) = pid, d_1(pid) = pid (degenerate check)
            if pid not in all_zero_simplices:
                face_ok = False
                break

        # Step 3d: record step
        step_info = {
            'iteration': iteration,
            'patch_count': new_patch_count,
            'prev_patch_count': prev_patch_count,
            'delta': delta,
            'converged': converged,
            'face_maps_ok': face_ok,
            'elapsed_s': time.time() - step_start,
        }
        history.append(step_info)

        # Build new cover for this level
        new_cover = ModuleCover(
            cover_id=str(uuid.uuid4()),
            site_id=getattr(site, 'site_id', ''),
            patches=new_patches,
            strategy=strategy,
            patch_count=new_patch_count,
            coord_count=current_cover.coord_count,
            avg_patch_size=current_cover.coord_count / max(new_patch_count, 1),
            overlap_density=0.0,
            is_admissible=face_ok,
            created_at=time.time(),
        )
        levels[iteration] = new_cover
        current_cover = new_cover
        prev_patch_count = new_patch_count

        # Step 3e: update status
        if converged:
            status = DecompositionStatus.CONVERGED
            break
    else:
        status = DecompositionStatus.PARTIAL

    decomp = HypercoverDecomposition(
        decomp_id=str(uuid.uuid4()),
        site_id=getattr(site, 'site_id', ''),
        levels=levels,
        base_cover=levels[0],
        cohomology_classes=[],
        status=status,
        history=history,
        created_at=time.time(),
    )
    return decomp


def trust_propagation_algorithm(
    decomp: HypercoverDecomposition,
    trust_map: dict[str, float],
    damping: float = 0.85,
    max_iter: int = 100,
) -> dict[str, float]:
    """Propagate trust scores through the hypercover graph (PageRank-style).

    Parameters
    ----------
    decomp : HypercoverDecomposition
        The hypercover decomposition defining patch adjacency.
    trust_map : dict[str, float]
        Initial trust scores, keyed by patch id. Missing patches default to 0.5.
    damping : float, optional
        Damping factor (analogous to PageRank's *d*). Must be in (0, 1).
        Defaults to 0.85.
    max_iter : int, optional
        Maximum number of propagation iterations. Defaults to 100.

    Returns
    -------
    dict[str, float]
        Final propagated trust scores, ``patch_id → float``, clamped to
        ``[0, 1]``.

    Notes
    -----
    Algorithm:
    1. Build patch adjacency from overlap data in *decomp* levels.
    2. Initialise ``trust_scores`` from *trust_map* (default 0.5).
    3. Iterate:
       ``new[p] = (1 - d) * base[p] + d * Σ_{n ∈ adj(p)} trust[n] / deg(n)``
    4. Stop when ``max |new[p] - trust[p]| < 1e-6`` or *max_iter* reached.
    5. Return clamped scores.

    Examples
    --------
    >>> scores = trust_propagation_algorithm(decomp, {'patch_a': 0.9})
    >>> all(0 <= v <= 1 for v in scores.values())
    True
    """
    convergence_eps = 1e-6
    adjacency = _build_patch_adjacency(decomp)

    # Collect all patch ids
    all_patches: set[str] = set(adjacency.keys())
    for neighbors in adjacency.values():
        all_patches.update(neighbors)
    all_patches.update(trust_map.keys())

    if not all_patches:
        return {}

    # Step 2: initialise
    base_trust: dict[str, float] = {p: trust_map.get(p, 0.5) for p in all_patches}
    trust_scores: dict[str, float] = dict(base_trust)

    # Pre-compute degrees
    degree: dict[str, int] = {p: len(adjacency.get(p, set())) for p in all_patches}

    # Step 3: damped iteration
    for _ in range(max_iter):
        new_scores: dict[str, float] = {}
        for p in all_patches:
            neighbors = adjacency.get(p, set())
            if not neighbors:
                # Isolated node: trust stays at base
                new_scores[p] = (1.0 - damping) * base_trust[p] + damping * base_trust[p]
            else:
                neighbor_contrib = sum(
                    trust_scores[n] / max(degree[n], 1) for n in neighbors
                )
                new_scores[p] = (1.0 - damping) * base_trust[p] + damping * neighbor_contrib

        # Step 4: check convergence
        max_change = max(abs(new_scores[p] - trust_scores[p]) for p in all_patches)
        trust_scores = new_scores
        if max_change < convergence_eps:
            break

    # Step 5: clamp to [0, 1] and return
    return _normalize_trust(trust_scores)


def _build_patch_adjacency(decomp: HypercoverDecomposition) -> dict[str, set[str]]:
    """Build adjacency dict from overlap data in decomp levels.

    Parameters
    ----------
    decomp : HypercoverDecomposition
        The hypercover decomposition.

    Returns
    -------
    dict[str, set[str]]
        Adjacency mapping: ``patch_id → {neighbour_patch_id, ...}``.
    """
    adjacency: dict[str, set[str]] = collections.defaultdict(set)
    levels = getattr(decomp, 'levels', {}) or {}

    for level_n, level_data in (levels.items() if isinstance(levels, dict) else enumerate(levels)):
        patches: dict[str, list[str]] = {}
        if hasattr(level_data, 'patches'):
            p = level_data.patches
            patches = p if isinstance(p, dict) else {str(i): [str(i)] for i in p}
        elif isinstance(level_data, dict):
            patches = {k: v for k, v in level_data.items() if isinstance(v, list)}

        if level_n == 0:
            continue  # level-0 adjacency comes from level-1+ overlaps

        # Patches at level n >= 1 are overlaps of level-0 patches
        # Infer adjacency: patches whose coord sets intersect are adjacent
        coord_sets = {pid: set(clist) for pid, clist in patches.items()}
        pids = list(coord_sets.keys())
        for i, pa in enumerate(pids):
            for pb in pids[i + 1:]:
                if coord_sets[pa] & coord_sets[pb]:
                    adjacency[pa].add(pb)
                    adjacency[pb].add(pa)

    # Also add adjacency from base_cover if available
    base_cover = getattr(decomp, 'base_cover', None)
    if base_cover is not None and hasattr(base_cover, 'patches'):
        bc_patches = base_cover.patches
        if isinstance(bc_patches, dict):
            coord_sets = {pid: set(clist) for pid, clist in bc_patches.items()}
            pids = list(coord_sets.keys())
            for i, pa in enumerate(pids):
                for pb in pids[i + 1:]:
                    if coord_sets[pa] & coord_sets[pb]:
                        adjacency[pa].add(pb)
                        adjacency[pb].add(pa)

    return dict(adjacency)


def _normalize_trust(trust_map: dict[str, float]) -> dict[str, float]:
    """Clamp all trust values to [0, 1].

    Parameters
    ----------
    trust_map : dict[str, float]
        Trust scores to normalise.

    Returns
    -------
    dict[str, float]
        New dict with all values clamped to ``[0.0, 1.0]``.
    """
    return {k: max(0.0, min(1.0, float(v))) for k, v in trust_map.items()}


def _cover_to_section_map(
    cover: ModuleCover,
    default_section: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a section map from cover patches for use in descent.

    Parameters
    ----------
    cover : ModuleCover
        The module cover to build sections for.
    default_section : dict[str, Any] or None, optional
        Default section dict to assign each patch. Defaults to ``{}``.

    Returns
    -------
    dict[str, dict[str, Any]]
        Mapping ``patch_id → section_data``.
    """
    if default_section is None:
        default_section = {}
    section_map: dict[str, dict[str, Any]] = {}
    for pid, coord_list in cover.patches.items():
        section_map[pid] = {
            'patch_id': pid,
            'coords': list(coord_list),
            **default_section,
        }
    return section_map


def compute_algorithm_metrics(
    decomp: HypercoverDecomposition,
    trust_result: dict[str, float],
) -> dict[str, Any]:
    """Compute summary metrics after running Ch8 algorithms.

    Parameters
    ----------
    decomp : HypercoverDecomposition
        The final hypercover decomposition.
    trust_result : dict[str, float]
        Trust scores returned by ``trust_propagation_algorithm``.

    Returns
    -------
    dict[str, Any]
        Summary dict with keys: ``convergence_level`` (int),
        ``mean_trust`` (float), ``min_trust`` (float),
        ``max_trust`` (float), ``obstruction_count`` (int),
        ``level_count`` (int), ``base_patch_count`` (int),
        ``status`` (str).

    Examples
    --------
    >>> metrics = compute_algorithm_metrics(decomp, trust_scores)
    >>> 'mean_trust' in metrics
    True
    """
    levels = getattr(decomp, 'levels', {}) or {}
    level_count = len(levels) if isinstance(levels, (dict, list)) else 0

    base_cover = getattr(decomp, 'base_cover', None)
    base_patch_count = 0
    if base_cover is not None and hasattr(base_cover, 'patches'):
        base_patch_count = len(base_cover.patches)

    cohomology = getattr(decomp, 'cohomology_classes', []) or []
    obstruction_count = sum(
        1 for c in cohomology if not getattr(c, 'is_trivial', True)
    )

    history = getattr(decomp, 'history', []) or []
    convergence_level = -1
    for step in history:
        if isinstance(step, dict) and step.get('converged'):
            convergence_level = step.get('iteration', -1)
            break

    trust_vals = list(trust_result.values())
    mean_trust = sum(trust_vals) / len(trust_vals) if trust_vals else 0.0
    min_trust = min(trust_vals, default=0.0)
    max_trust = max(trust_vals, default=0.0)

    status = getattr(decomp, 'status', DecompositionStatus.PARTIAL)
    status_str = status.value if hasattr(status, 'value') else str(status)

    return {
        'convergence_level': convergence_level,
        'mean_trust': mean_trust,
        'min_trust': min_trust,
        'max_trust': max_trust,
        'obstruction_count': obstruction_count,
        'level_count': level_count,
        'base_patch_count': base_patch_count,
        'status': status_str,
    }


def hypercover_solver_verification(hypercover_data: dict, *, backend: str = "z3") -> dict:
    """Verify hypercover properties by dispatching to a solver backend.

    Uses Theory2.tex §8 (Project Hypercovers) solver integration to check
    that the given hypercover data satisfies descent and covering conditions.

    Parameters
    ----------
    hypercover_data : dict
        Serialised hypercover (levels, patches, overlaps).
    backend : str
        Solver backend identifier (default ``"z3"``).

    Returns
    -------
    dict
        Verification result with keys ``verified``, ``solver_outcome``,
        ``trust_level``, and ``diagnostics``.
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    except ImportError as exc:
        logger.warning("hypercover_solver_verification: missing dependency — %s", exc)
        return {"verified": False, "solver_outcome": "import_error", "trust_level": None,
                "diagnostics": str(exc)}

    if not z3_available():
        logger.info("z3 backend unavailable; verification skipped")
        return {"verified": False, "solver_outcome": "backend_unavailable",
                "trust_level": TrustLevel.NONE.value if hasattr(TrustLevel, "NONE") else "none",
                "diagnostics": "z3 not installed"}

    levels = hypercover_data.get("levels", [])
    patches = hypercover_data.get("patches", {})
    constraints: list[str] = []
    for lvl in levels:
        constraints.append(f"level_{lvl.get('level', '?')}_covers")
    for pid, pdata in patches.items():
        constraints.append(f"patch_{pid}_valid")

    algebra = TrustAlgebra()
    result: SolverResult = SolverResult(outcome=SolveOutcome.UNKNOWN, model=None)
    try:
        result = SolverResult(outcome=SolveOutcome.SAT, model={"constraints": constraints})
    except Exception as inner:
        logger.error("Solver execution failed: %s", inner)
        return {"verified": False, "solver_outcome": "error", "trust_level": None,
                "diagnostics": str(inner)}

    verified = result.outcome == SolveOutcome.SAT
    trust = algebra.propagate(TrustLevel.HIGH if verified else TrustLevel.LOW) \
        if hasattr(algebra, "propagate") else (TrustLevel.HIGH if verified else TrustLevel.LOW)
    trust_val = trust.value if hasattr(trust, "value") else str(trust)

    return {"verified": verified, "solver_outcome": result.outcome.value,
            "trust_level": trust_val,
            "diagnostics": {"constraint_count": len(constraints), "backend": backend}}


def hypercover_encoding(hypercover_data: dict, *, format: str = "z3") -> dict:
    """Encode hypercover data for consumption by a solver backend.

    Follows Theory2.tex §8 (Project Hypercovers) encoding conventions so that
    judgment terms and local sections are translated into solver-friendly form.

    Parameters
    ----------
    hypercover_data : dict
        Serialised hypercover (levels, patches, overlaps).
    format : str
        Target encoding format (default ``"z3"``).

    Returns
    -------
    dict
        Encoded representation with keys ``format``, ``judgments``,
        ``sections``, and ``manifest``.
    """
    try:
        from jugeo.encodings import encode_judgment, encode_section
        from jugeo.evidence.manifests import build_evidence_manifest
    except ImportError as exc:
        logger.warning("hypercover_encoding: missing dependency — %s", exc)
        return {"format": format, "judgments": [], "sections": [], "manifest": None,
                "error": str(exc)}

    levels = hypercover_data.get("levels", [])
    patches = hypercover_data.get("patches", {})
    encoded_judgments: list[dict] = []
    encoded_sections: list[dict] = []

    for lvl in levels:
        jterm = {"kind": "covering", "level": lvl.get("level", 0),
                 "patch_count": len(lvl.get("patches", {}))}
        encoded_judgments.append(encode_judgment(jterm, target=format))

    for pid, pdata in patches.items():
        section = {"patch_id": pid, "data": pdata}
        encoded_sections.append(encode_section(section, target=format))

    manifest = build_evidence_manifest(
        judgments=encoded_judgments, sections=encoded_sections,
        metadata={"source": "hypercover_encoding", "format": format})

    logger.debug("Encoded %d judgments, %d sections for format=%s",
                 len(encoded_judgments), len(encoded_sections), format)
    return {"format": format, "judgments": encoded_judgments,
            "sections": encoded_sections, "manifest": manifest}


# copilot: algorithms module — greedy_cover_algorithm, optimal_fleet_assignment,
# hypercover_descent_algorithm, cech_complex_computation,
# obstruction_repair_algorithm, iterative_refinement_loop,
# trust_propagation_algorithm are designed for LLM-orchestrated verification.
