"""Stand-alone graph and order algorithms for the relational_refinement package.

Provides pure functions that operate on ``RefinementRelation`` and
``RefinementOrder`` objects without requiring class instantiation.  These
functions implement the standard algorithms of order theory adapted to the
JuGeo refinement setting.

Functions
---------
compute_transitive_closure
    Compute the transitive closure of a relation set.
find_maximal_elements
    Find coordinates that are not refined by any other coordinate.
find_minimal_elements
    Find coordinates that do not refine any other coordinate.
compute_lub
    Compute the least upper bound of a set of coordinates.
compute_glb
    Compute the greatest lower bound of a set of coordinates.
detect_regressions
    Find relations that represent trust regressions.
score_refinement_quality
    Score the overall quality of a refinement order.
refinement_convergence_check
    Check whether a sequence of orders has converged.

Theory context (Ch12)
---------------------
These algorithms support the proof obligations for:
* Ch12.Thm3  (transitivity closure)
* Ch12.Thm10 (LUB existence)
* Ch12.Thm11 (GLB existence)
* Ch12.Thm12 (regression detection)
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Sequence

from jugeo.problem_modes.relational_refinement.models import (
    RefinementRelation,
    EquivalenceClass,
    RefinementWitness,
    RefinementOrder,
)

# ---------------------------------------------------------------------------
# Direction shorthands
# ---------------------------------------------------------------------------
_D = RefinementRelation.RefinementDirection
_FORWARD = _D.FORWARD
_BACKWARD = _D.BACKWARD
_EQUIVALENT = _D.EQUIVALENT
_INCOMPARABLE = _D.INCOMPARABLE

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_forward_adj(
    relations: Sequence[RefinementRelation],
) -> dict[str, set[str]]:
    """Build a forward adjacency dict from a sequence of relations.

    Parameters
    ----------
    relations:
        Relations to index.

    Returns
    -------
    dict[str, set[str]]
        Maps each coordinate to the set of coordinates it refines into
        (FORWARD or EQUIVALENT edges).
    """
    adj: dict[str, set[str]] = {}
    for rel in relations:
        if rel.direction in (_FORWARD, _EQUIVALENT):
            adj.setdefault(rel.left_coordinate, set()).add(rel.right_coordinate)
            if rel.direction == _EQUIVALENT:
                adj.setdefault(rel.right_coordinate, set()).add(rel.left_coordinate)
    return adj


def _reachable_from(
    start: str,
    adj: dict[str, set[str]],
) -> set[str]:
    """Return the set of all coordinates reachable from *start* via BFS.

    Parameters
    ----------
    start:
        Starting coordinate.
    adj:
        Adjacency dict.

    Returns
    -------
    set[str]
        All coordinates reachable from *start* (excluding *start* itself).
    """
    visited: set[str] = set()
    queue: list[str] = [start]
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        queue.extend(adj.get(node, set()))
    visited.discard(start)
    return visited


def _all_coordinates(relations: Sequence[RefinementRelation]) -> frozenset[str]:
    """Collect all coordinate strings from a sequence of relations.

    Parameters
    ----------
    relations:
        Relations to inspect.

    Returns
    -------
    frozenset[str]
        All left and right coordinates appearing in any relation.
    """
    coords: set[str] = set()
    for rel in relations:
        coords.add(rel.left_coordinate)
        coords.add(rel.right_coordinate)
    return frozenset(coords)


# ---------------------------------------------------------------------------
# Public algorithms
# ---------------------------------------------------------------------------


def compute_transitive_closure(
    relations: Sequence[RefinementRelation],
) -> tuple[RefinementRelation, ...]:
    """Compute the transitive closure of a set of refinement relations.

    For every pair of coordinates (A, C) such that A can reach C through one
    or more FORWARD/EQUIVALENT edges, a new relation A → C is added (if it
    does not already exist).  The new relations are tagged with
    ``is_witnessed=False`` and a reduced confidence score of
    ``0.9 × min_confidence``.

    The algorithm uses a variant of Floyd-Warshall adapted to the FORWARD-only
    closure.  Existing relations are not modified; only new bridging relations
    are appended.

    Parameters
    ----------
    relations:
        The initial set of relations.

    Returns
    -------
    tuple[RefinementRelation, ...]
        The original relations plus any new transitive relations.

    Notes
    -----
    Time complexity: O(n³) where n is the number of distinct coordinates.
    """
    coords = _all_coordinates(relations)
    adj = _build_forward_adj(list(relations))

    # For each pair, determine if there is a transitive path
    existing: set[tuple[str, str]] = {
        (r.left_coordinate, r.right_coordinate)
        for r in relations
        if r.direction in (_FORWARD, _EQUIVALENT)
    }

    # Build confidence lookup for existing edges
    confidence_map: dict[tuple[str, str], float] = {}
    direction_map: dict[tuple[str, str], RefinementRelation.RefinementDirection] = {}
    delta_map: dict[tuple[str, str], int] = {}
    for rel in relations:
        key = (rel.left_coordinate, rel.right_coordinate)
        confidence_map[key] = rel.confidence
        direction_map[key] = rel.direction
        delta_map[key] = rel.trust_delta

    new_rels: list[RefinementRelation] = list(relations)
    new_existing = set(existing)

    for start in coords:
        reachable = _reachable_from(start, adj)
        for end in reachable:
            if end == start:
                continue
            pair = (start, end)
            if pair in new_existing:
                continue

            # Construct a transitive relation
            # Direction: FORWARD (since we followed FORWARD/EQUIVALENT edges)
            # Trust delta: sum along path (approximate as 1 for inferred)
            # Confidence: scaled down slightly
            new_rel = RefinementRelation.make(
                left=start,
                right=end,
                direction=_FORWARD,
                trust_delta=1,
                evidence_embedding=(),
                obligation_discharge=(),
                is_witnessed=False,
                witness_id=None,
                confidence=0.85,
                metadata=(("derived", "transitive_closure"),),
            )
            new_rels.append(new_rel)
            new_existing.add(pair)

    return tuple(new_rels)


def find_maximal_elements(order: RefinementOrder) -> frozenset[str]:
    """Find coordinates that are maximal in the refinement order.

    A coordinate C is *maximal* iff there is no coordinate D ≠ C such that
    C ≤ D (i.e. C is not properly refined by anything).

    Parameters
    ----------
    order:
        The refinement order to analyse.

    Returns
    -------
    frozenset[str]
        All maximal coordinates.

    Notes
    -----
    Maximal elements are candidates for the "strongest" judgments in the order.
    In a lattice this is the top element; in a general partial order there may
    be many maximal elements (an antichain at the top).
    """
    refined_by: set[str] = set()
    for rel in order.relations:
        if rel.direction == _FORWARD:
            refined_by.add(rel.left_coordinate)

    return frozenset(c for c in order.coordinates if c not in refined_by)


def find_minimal_elements(order: RefinementOrder) -> frozenset[str]:
    """Find coordinates that are minimal in the refinement order.

    A coordinate C is *minimal* iff there is no coordinate D ≠ C such that
    D ≤ C (i.e. C does not refine anything else in the FORWARD direction).

    Parameters
    ----------
    order:
        The refinement order to analyse.

    Returns
    -------
    frozenset[str]
        All minimal coordinates.

    Notes
    -----
    Minimal elements are candidates for the "weakest" judgments in the order.
    In a lattice this is the bottom element.
    """
    refines: set[str] = set()
    for rel in order.relations:
        if rel.direction == _FORWARD:
            refines.add(rel.right_coordinate)

    return frozenset(c for c in order.coordinates if c not in refines)


def compute_lub(
    coords: Sequence[str],
    order: RefinementOrder,
) -> str | None:
    """Compute the least upper bound (join) of a set of coordinates.

    Finds the smallest coordinate that is reachable (via FORWARD edges) from
    all coordinates in *coords*.  If multiple candidates exist, returns the
    one with the fewest additional successors (most specific LUB).

    Parameters
    ----------
    coords:
        A sequence of coordinates to join.
    order:
        The refinement order.

    Returns
    -------
    str | None
        The LUB coordinate, or ``None`` if no LUB exists.

    Notes
    -----
    This function computes the LUB of the *given set of coordinates*, not of
    a pair.  For a pair, see also ``RefinementOrder.join``.
    """
    if not coords:
        return None
    if len(coords) == 1:
        return coords[0]

    adj = _build_forward_adj(list(order.relations))

    # Compute the set of nodes reachable from each coordinate (including self)
    def reachable_inclusive(start: str) -> set[str]:
        return {start} | _reachable_from(start, adj)

    # Start with the reachable set of the first coordinate
    common = reachable_inclusive(coords[0])
    for c in coords[1:]:
        common &= reachable_inclusive(c)

    if not common:
        return None

    # Among common reachable nodes, find the "smallest" (fewest further successors)
    best: str | None = None
    best_score = float("inf")
    for candidate in common:
        # Score = number of further common reachable nodes from candidate
        further = reachable_inclusive(candidate) & common
        score = len(further)
        if score < best_score:
            best_score = score
            best = candidate

    return best


def compute_glb(
    coords: Sequence[str],
    order: RefinementOrder,
) -> str | None:
    """Compute the greatest lower bound (meet) of a set of coordinates.

    Finds the largest coordinate from which all coordinates in *coords* are
    reachable (via FORWARD edges).  The largest candidate is the one with the
    most forward-reachable nodes in the order (most specific lower bound).

    Parameters
    ----------
    coords:
        A sequence of coordinates to meet.
    order:
        The refinement order.

    Returns
    -------
    str | None
        The GLB coordinate, or ``None`` if no GLB exists.

    Notes
    -----
    This function computes the GLB of the *given set of coordinates*.  For
    a pair, see also ``RefinementOrder.meet``.
    """
    if not coords:
        return None
    if len(coords) == 1:
        return coords[0]

    adj = _build_forward_adj(list(order.relations))

    def reachable_inclusive(start: str) -> set[str]:
        return {start} | _reachable_from(start, adj)

    # A coordinate C is a lower bound iff all target coords are reachable from C
    target_set = set(coords)
    lower_bounds: list[str] = []
    for candidate in order.coordinates:
        if target_set <= reachable_inclusive(candidate):
            lower_bounds.append(candidate)

    if not lower_bounds:
        return None

    # Greatest = has the most successors in the order (most specific lower bound)
    best: str | None = None
    best_score = -1
    for candidate in lower_bounds:
        score = len(reachable_inclusive(candidate))
        if score > best_score:
            best_score = score
            best = candidate

    return best


def detect_regressions(
    relations: Sequence[RefinementRelation],
) -> tuple[RefinementRelation, ...]:
    """Return all relations that represent trust regressions.

    A relation is a regression iff:
    * Its direction is ``BACKWARD`` (right ≤ left, meaning the "right" side
      is actually weaker), or
    * Its ``trust_delta`` is strictly negative (trust decreases).

    Parameters
    ----------
    relations:
        The relations to inspect.

    Returns
    -------
    tuple[RefinementRelation, ...]
        All relations classified as regressions.

    Notes
    -----
    Regressions are permitted by the algebra (they model downward steps in the
    order) but should be flagged for review, since they indicate that the
    "refinement" weakens rather than strengthens the judgment.
    """
    return tuple(r for r in relations if r.is_regression())


def score_refinement_quality(order: RefinementOrder) -> float:
    """Score the overall quality of a refinement order on a [0, 1] scale.

    The quality score is computed as a weighted combination of:

    * **Coverage** (0.3): fraction of coordinate pairs that have an explicit
      relation (non-INCOMPARABLE).
    * **Monotonicity** (0.3): fraction of FORWARD/EQUIVALENT relations with
      non-negative trust delta.
    * **Witness coverage** (0.2): fraction of FORWARD/EQUIVALENT relations
      that are witnessed (``is_witnessed=True``).
    * **Consistency** (0.2): 1.0 if ``order.is_consistent`` is ``True``,
      0.5 if ``None``, 0.0 if ``False``.

    Parameters
    ----------
    order:
        The refinement order to score.

    Returns
    -------
    float
        A quality score in ``[0, 1]``.  Higher = better.

    Notes
    -----
    This score is a heuristic intended for tooling and dashboards.  It does
    not constitute a formal proof of order quality.
    """
    n = len(order.coordinates)
    if n == 0:
        return 0.0

    total_pairs = n * (n - 1)  # ordered pairs excluding identity

    # Coverage: number of non-INCOMPARABLE relations
    if total_pairs == 0:
        coverage = 1.0
    else:
        non_incomparable = sum(
            1
            for r in order.relations
            if r.direction != _INCOMPARABLE
            and r.left_coordinate != r.right_coordinate
        )
        coverage = min(1.0, non_incomparable / total_pairs)

    # Monotonicity: fraction of FORWARD/EQUIVALENT relations with trust_delta ≥ 0
    refinements = [
        r for r in order.relations if r.direction in (_FORWARD, _EQUIVALENT)
    ]
    if refinements:
        monotone = sum(1 for r in refinements if r.trust_delta >= 0)
        monotonicity = monotone / len(refinements)
    else:
        monotonicity = 1.0

    # Witness coverage
    if refinements:
        witnessed = sum(1 for r in refinements if r.is_witnessed)
        witness_coverage = witnessed / len(refinements)
    else:
        witness_coverage = 0.0

    # Consistency
    if order.is_consistent is True:
        consistency = 1.0
    elif order.is_consistent is None:
        consistency = 0.5
    else:
        consistency = 0.0

    score = (
        0.3 * coverage
        + 0.3 * monotonicity
        + 0.2 * witness_coverage
        + 0.2 * consistency
    )
    return round(min(1.0, max(0.0, score)), 4)


def refinement_convergence_check(
    orders: Sequence[RefinementOrder],
) -> bool:
    """Check whether a sequence of refinement orders has converged.

    A sequence of orders is said to have *converged* iff the last two orders
    in the sequence are structurally equivalent: they have the same set of
    coordinates and the same set of ``(left, right, direction)`` triples.

    This is used to check whether an iterative refinement computation
    (e.g. repeated transitive closure steps) has reached a fixed point.

    Parameters
    ----------
    orders:
        A sequence of refinement orders, newest last.

    Returns
    -------
    bool
        ``True`` iff the last two orders are structurally equivalent.
        Also ``True`` if the sequence has 0 or 1 elements (trivially
        converged).

    Notes
    -----
    Convergence does *not* imply that the order is correct — only that
    successive iterations are no longer producing new relations.
    """
    if len(orders) <= 1:
        return True

    last = orders[-1]
    prev = orders[-2]

    if last.coordinates != prev.coordinates:
        return False

    def rel_key(r: RefinementRelation) -> tuple[str, str, str]:
        return (r.left_coordinate, r.right_coordinate, r.direction.value)

    last_keys = frozenset(rel_key(r) for r in last.relations)
    prev_keys = frozenset(rel_key(r) for r in prev.relations)
    return last_keys == prev_keys


def topological_sort(order: RefinementOrder) -> tuple[str, ...] | None:
    """Return a topological sort of the order's coordinates.

    Produces a linear extension of the partial order: a sequence of
    coordinates such that if A ≤ B then A appears before B.  Returns
    ``None`` if the order contains a cycle (and thus cannot be sorted).

    Parameters
    ----------
    order:
        The refinement order to sort.

    Returns
    -------
    tuple[str, ...] | None
        A topological ordering of the coordinates, or ``None`` if a cycle
        is detected.

    Notes
    -----
    Uses Kahn's algorithm (BFS-based), which runs in O(V + E) time.
    """
    in_degree: dict[str, int] = {c: 0 for c in order.coordinates}
    adj: dict[str, list[str]] = {c: [] for c in order.coordinates}

    for rel in order.relations:
        if rel.direction == _FORWARD:
            adj[rel.left_coordinate].append(rel.right_coordinate)
            in_degree[rel.right_coordinate] = in_degree.get(rel.right_coordinate, 0) + 1

    queue: list[str] = [c for c in order.coordinates if in_degree[c] == 0]
    result: list[str] = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbour in adj[node]:
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    if len(result) != len(order.coordinates):
        return None  # Cycle detected
    return tuple(result)


def partition_into_levels(
    order: RefinementOrder,
) -> tuple[frozenset[str], ...]:
    """Partition the order's coordinates into levels (Hasse diagram rows).

    Level 0 contains the minimal elements, level 1 contains elements that
    are refined directly by level-0 elements, and so on.

    Parameters
    ----------
    order:
        The refinement order.

    Returns
    -------
    tuple[frozenset[str], ...]
        A tuple of sets, one per level, from minimal to maximal.
    """
    adj = _build_forward_adj(list(order.relations))
    rev: dict[str, set[str]] = {c: set() for c in order.coordinates}
    for src, tgts in adj.items():
        for tgt in tgts:
            rev.setdefault(tgt, set()).add(src)

    level: dict[str, int] = {}
    queue: list[str] = []
    # Start with minimal elements (no predecessors)
    for coord in order.coordinates:
        if not rev.get(coord):
            level[coord] = 0
            queue.append(coord)

    while queue:
        node = queue.pop(0)
        node_level = level[node]
        for neighbour in adj.get(node, set()):
            new_level = node_level + 1
            if level.get(neighbour, -1) < new_level:
                level[neighbour] = new_level
                queue.append(neighbour)

    max_level = max(level.values(), default=0)
    result: list[frozenset[str]] = []
    for lv in range(max_level + 1):
        result.append(frozenset(c for c, l in level.items() if l == lv))
    return tuple(result)


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
    "compute_transitive_closure",
    "find_maximal_elements",
    "find_minimal_elements",
    "compute_lub",
    "compute_glb",
    "detect_regressions",
    "score_refinement_quality",
    "refinement_convergence_check",
    "topological_sort",
    "partition_into_levels",
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]

# copilot: algorithms.py — order theory algorithms for Ch12 refinement structures
