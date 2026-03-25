"""Core debugging algorithms for obstruction localization, root cause analysis,
repair frontier computation, triage, and countermodel extraction.

All algorithms operate on string-based IDs and do not import from jugeo internals.
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from jugeo.se_theory.debugging.models import (
    CohomologyClass,
    CountermodelReport,
    DescentTrace,
    LocalSection,
    Morphism,
    Obstruction,
    ObstructionCluster,
    ObstructionSeverity,
    Overlap,
    RepairFrontier,
    RepairPlan,
    RepairStrategy,
    RootCauseAnalysis,
    TriageReport,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_adjacency(morphisms: list[Morphism]) -> dict[str, list[str]]:
    """Build forward adjacency list from morphisms (source → [targets])."""
    adj: dict[str, list[str]] = defaultdict(list)
    for m in morphisms:
        adj[m.source].append(m.target)
    return dict(adj)


def _build_reverse_adjacency(morphisms: list[Morphism]) -> dict[str, list[str]]:
    """Build reverse adjacency list (target → [sources])."""
    radj: dict[str, list[str]] = defaultdict(list)
    for m in morphisms:
        radj[m.target].append(m.source)
    return dict(radj)


def _reachable(start: str, adjacency: dict[str, list[str]]) -> set[str]:
    """BFS to find all nodes reachable from start."""
    visited: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in adjacency.get(node, []):
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


def _topological_sort(nodes: list[str], adjacency: dict[str, list[str]]) -> list[str]:
    """Kahn's algorithm for topological sort. Returns [] if cycle detected."""
    in_degree: dict[str, int] = defaultdict(int)
    node_set = set(nodes)
    for node in nodes:
        for neighbor in adjacency.get(node, []):
            if neighbor in node_set:
                in_degree[neighbor] += 1

    queue: deque[str] = deque(n for n in nodes if in_degree[n] == 0)
    result: list[str] = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor in node_set:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
    return result


# ---------------------------------------------------------------------------
# ObstructionLocalizer
# ---------------------------------------------------------------------------

class ObstructionLocalizer:
    """Localize descent failures in a site by examining sections and overlaps.

    The central insight: an obstruction exists at a coordinate when a local
    section fails to satisfy its proposition, or when two sections disagree
    over their shared overlap.
    """

    # ---- Classification keyword maps ----
    _TYPE_KEYWORDS = frozenset([
        "typeerror", "type error", "wrong type", "expected type", "int but got",
        "str but got", "cannot convert", "type mismatch", "incompatible type",
        "not subscriptable", "has no attribute", "object is not",
    ])
    _NULL_KEYWORDS = frozenset([
        "nonetype", "null", "none", "nil", "nullpointer", "null reference",
        "attributeerror", "cannot read property", "undefined is not an object",
    ])
    _BOUNDS_KEYWORDS = frozenset([
        "indexerror", "out of bounds", "index out of range", "array index",
        "list index", "buffer overflow", "overflow", "underflow",
    ])
    _CONTRACT_KEYWORDS = frozenset([
        "precondition", "postcondition", "invariant", "contract", "violated",
        "requires", "ensures",
    ])
    _PROTOCOL_KEYWORDS = frozenset([
        "protocol", "http", "grpc", "rpc", "request", "response", "header",
        "status code", "timeout", "connection refused", "socket",
    ])
    _CONCURRENCY_KEYWORDS = frozenset([
        "race", "deadlock", "livelock", "lock", "mutex", "semaphore",
        "thread", "concurrent", "atomic",
    ])
    _IMPORT_KEYWORDS = frozenset([
        "importerror", "modulenotfounderror", "no module named", "cannot import",
        "import error", "circular import",
    ])
    _PERMISSION_KEYWORDS = frozenset([
        "permissionerror", "access denied", "forbidden", "unauthorized",
        "403", "401", "no permission",
    ])
    _ENCODING_KEYWORDS = frozenset([
        "unicodedecodeerror", "unicodeencodeerror", "encoding", "codec",
        "utf-8", "ascii", "decode error",
    ])
    _LOGIC_KEYWORDS = frozenset([
        "assertionerror", "assertion", "wrong result", "incorrect", "logic",
        "expected", "got", "should be",
    ])
    _RESOURCE_KEYWORDS = frozenset([
        "resource leak", "file not closed", "connection leak", "handle leak",
        "ioerror", "oserror", "too many open files",
    ])
    _MEMORY_KEYWORDS = frozenset([
        "memoryerror", "out of memory", "memory leak", "segfault",
        "segmentation fault",
    ])
    _DEADLOCK_KEYWORDS = frozenset(["deadlock", "circular wait", "livelock"])
    _RACE_KEYWORDS = frozenset(["race condition", "data race", "toctou"])
    _API_KEYWORDS = frozenset([
        "api misuse", "wrong parameter", "invalid argument", "valueerror",
        "bad request", "400",
    ])
    _CONFIG_KEYWORDS = frozenset([
        "configuration", "config", "setting", "environment variable",
        "missing key", "keyerror", "not configured",
    ])
    _STATE_KEYWORDS = frozenset([
        "state corruption", "inconsistent state", "corrupted", "invalid state",
        "unexpected state",
    ])
    _ASSERTION_KEYWORDS = frozenset(["assertionerror", "assert"])

    def localize_descent_failure(
        self,
        sections: list[LocalSection],
        overlaps: list[Overlap],
        morphisms: list[Morphism],
    ) -> list[Obstruction]:
        """Find all coordinates and overlaps where descent fails.

        Returns a list of Obstruction objects, one per failure point.
        """
        obstructions: list[Obstruction] = []
        section_map = {s.coordinate_id: s for s in sections}

        # 1. Check local consistency of each section
        for section in sections:
            if not self._check_local_consistency(section, section.proposition):
                failure_detail = f"section at {section.coordinate_id!r} fails proposition: {section.proposition!r}"
                cls = self.classify_obstruction(
                    section.coordinate_id,
                    section.proposition,
                    failure_detail,
                )
                blast, downstream = self.compute_blast_radius(section.coordinate_id, morphisms)
                is_crit = any(
                    m.is_critical_path
                    for m in morphisms
                    if m.source == section.coordinate_id
                )
                sev = self.severity_from_blast_radius(blast, is_crit)
                obs = Obstruction.make(
                    coordinate_id=section.coordinate_id,
                    proposition=section.proposition,
                    cohomology_class=cls,
                    severity=sev,
                    blast_radius=blast,
                    downstream_ids=downstream,
                    countermodel={
                        "coordinate": section.coordinate_id,
                        "value": section.value,
                        "failure": failure_detail,
                        "metadata": dict(section.metadata),
                    },
                )
                obstructions.append(obs)

        # 2. Check overlap agreement
        for overlap in overlaps:
            sec_a = section_map.get(overlap.coordinate_a)
            sec_b = section_map.get(overlap.coordinate_b)
            if sec_a is None or sec_b is None:
                continue
            if not self._check_overlap_agreement(sec_a, sec_b, overlap.shared_coordinates):
                failure_detail = (
                    f"sections {overlap.coordinate_a!r} and {overlap.coordinate_b!r} "
                    f"disagree on shared coords {overlap.shared_coordinates}"
                )
                coord_a, coord_b = overlap.coordinate_a, overlap.coordinate_b
                # The overlap failure is attributed to the later/downstream coordinate
                blast_a, down_a = self.compute_blast_radius(coord_a, morphisms)
                blast_b, down_b = self.compute_blast_radius(coord_b, morphisms)
                primary = coord_b if blast_b >= blast_a else coord_a
                blast = max(blast_a, blast_b)
                downstream = list(set(down_a + down_b))
                is_crit = any(
                    m.is_critical_path
                    for m in morphisms
                    if m.source == primary
                )
                sev = self.severity_from_blast_radius(blast, is_crit)
                obs = Obstruction.make(
                    coordinate_id=primary,
                    proposition=f"overlap agreement: {coord_a} ∩ {coord_b}",
                    cohomology_class=CohomologyClass.CONTRACT_VIOLATION,
                    severity=sev,
                    blast_radius=blast,
                    downstream_ids=downstream,
                    overlap_id=overlap.overlap_id,
                    countermodel={
                        "overlap_id": overlap.overlap_id,
                        "coordinate_a": coord_a,
                        "value_a": sec_a.value,
                        "coordinate_b": coord_b,
                        "value_b": sec_b.value,
                        "shared": overlap.shared_coordinates,
                        "failure": failure_detail,
                    },
                )
                obstructions.append(obs)

        return obstructions

    def _check_local_consistency(self, section: LocalSection, proposition: str) -> bool:
        """Does the section satisfy its local proposition?

        A section is locally consistent if:
        - It is marked valid
        - It has a non-None value (unless the proposition permits None)
        - No error keywords appear in value string representation
        """
        if not section.is_valid:
            return False
        if section.value is None:
            # None is permissible unless proposition demands non-null
            prop_lower = proposition.lower()
            if any(kw in prop_lower for kw in ("non-null", "not none", "required", "must exist")):
                return False
        val_str = str(section.value or "").lower()
        error_signals = ("error", "exception", "fail", "traceback", "panic")
        if any(sig in val_str for sig in error_signals):
            return False
        return True

    def _check_overlap_agreement(
        self,
        section_a: LocalSection,
        section_b: LocalSection,
        shared_coords: list[str],
    ) -> bool:
        """Do two sections agree on shared coordinates?

        Agreement is checked by:
        1. If both values are dicts, compare on shared_coords keys
        2. Otherwise, compare string representations for equality
        """
        if not shared_coords:
            return True
        if isinstance(section_a.value, dict) and isinstance(section_b.value, dict):
            for key in shared_coords:
                val_a = section_a.value.get(key)
                val_b = section_b.value.get(key)
                if val_a != val_b:
                    return False
            return True
        # Fallback: if both are invalid, that's a disagreement
        if not section_a.is_valid or not section_b.is_valid:
            return False
        # Both valid with non-dict values: compare repr
        return str(section_a.value) == str(section_b.value)

    def classify_obstruction(
        self,
        coordinate_id: str,
        proposition: str,
        failure_detail: str,
    ) -> CohomologyClass:
        """Classify a bug by analyzing the failure detail text.

        Uses a keyword-based classifier over the combined text of the
        coordinate ID, proposition, and failure description.
        """
        combined = f"{coordinate_id} {proposition} {failure_detail}".lower()

        def _matches(keywords: frozenset[str]) -> bool:
            return any(kw in combined for kw in keywords)

        # Order matters — more specific checks first
        if _matches(self._DEADLOCK_KEYWORDS):
            return CohomologyClass.DEADLOCK
        if _matches(self._RACE_KEYWORDS):
            return CohomologyClass.RACE_CONDITION
        if _matches(self._CONCURRENCY_KEYWORDS):
            return CohomologyClass.CONCURRENCY_HAZARD
        if _matches(self._IMPORT_KEYWORDS):
            return CohomologyClass.IMPORT_ERROR
        if _matches(self._PERMISSION_KEYWORDS):
            return CohomologyClass.PERMISSION_ERROR
        if _matches(self._MEMORY_KEYWORDS):
            return CohomologyClass.MEMORY_LEAK
        if _matches(self._RESOURCE_KEYWORDS):
            return CohomologyClass.RESOURCE_LEAK
        if _matches(self._ENCODING_KEYWORDS):
            return CohomologyClass.ENCODING_MISMATCH
        if _matches(self._BOUNDS_KEYWORDS):
            return CohomologyClass.BOUNDS_VIOLATION
        if _matches(self._NULL_KEYWORDS):
            return CohomologyClass.NULL_REFERENCE
        if _matches(self._TYPE_KEYWORDS):
            return CohomologyClass.TYPE_ERROR
        if _matches(self._CONTRACT_KEYWORDS):
            return CohomologyClass.CONTRACT_VIOLATION
        if _matches(self._PROTOCOL_KEYWORDS):
            return CohomologyClass.PROTOCOL_VIOLATION
        if _matches(self._STATE_KEYWORDS):
            return CohomologyClass.STATE_CORRUPTION
        if _matches(self._CONFIG_KEYWORDS):
            return CohomologyClass.CONFIGURATION_ERROR
        if _matches(self._API_KEYWORDS):
            return CohomologyClass.API_MISUSE
        if _matches(self._ASSERTION_KEYWORDS):
            return CohomologyClass.ASSERTION_FAILURE
        if _matches(self._LOGIC_KEYWORDS):
            return CohomologyClass.LOGIC_ERROR
        return CohomologyClass.UNKNOWN

    def compute_blast_radius(
        self,
        coordinate_id: str,
        morphisms: list[Morphism],
    ) -> tuple[int, list[str]]:
        """Count and list all downstream dependents via BFS.

        The blast radius is the number of downstream coordinates that would
        be affected by a change at coordinate_id.
        """
        adj = _build_adjacency(morphisms)
        reachable = _reachable(coordinate_id, adj)
        reachable.discard(coordinate_id)
        return len(reachable), sorted(reachable)

    def severity_from_blast_radius(
        self,
        blast_radius: int,
        is_critical_path: bool,
    ) -> ObstructionSeverity:
        """Assign severity based on blast radius and criticality.

        Critical-path obstructions are always at least ERROR.
        """
        if is_critical_path and blast_radius >= 5:
            return ObstructionSeverity.BLOCKER
        if is_critical_path:
            return ObstructionSeverity.CRITICAL
        if blast_radius >= 20:
            return ObstructionSeverity.BLOCKER
        if blast_radius >= 10:
            return ObstructionSeverity.CRITICAL
        if blast_radius >= 4:
            return ObstructionSeverity.ERROR
        if blast_radius >= 1:
            return ObstructionSeverity.WARNING
        return ObstructionSeverity.INFO


# ---------------------------------------------------------------------------
# RootCauseTracer
# ---------------------------------------------------------------------------

class RootCauseTracer:
    """Trace the causal chain from symptom obstructions back to root causes.

    A root cause is the earliest coordinate in the morphism partial order
    where descent fails — all symptoms downstream are consequences.
    """

    def trace_descent(
        self,
        start_coord: str,
        morphisms: list[Morphism],
        sections: dict[str, LocalSection],
    ) -> DescentTrace:
        """Follow the morphism chain from start_coord, recording where it fails.

        Walks forward along morphisms, checking section validity at each step.
        """
        adj = _build_adjacency(morphisms)
        chain: list[tuple[str, str, str]] = []
        visited: set[str] = set()
        current = start_coord
        failure_point: str | None = None

        # BFS-style trace along the longest path
        queue: deque[str] = deque([start_coord])
        end_coord = start_coord

        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            end_coord = node

            sec = sections.get(node)
            if sec is not None and not sec.is_valid:
                failure_point = node
                break

            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    # find the morphism kind
                    kind = "dependency"
                    for m in morphisms:
                        if m.source == node and m.target == neighbor:
                            kind = m.kind
                            break
                    chain.append((node, neighbor, kind))
                    queue.append(neighbor)

        return DescentTrace.make(
            start_coordinate=start_coord,
            end_coordinate=end_coord,
            morphism_chain=chain,
            failure_point=failure_point,
        )

    def find_root_cause(
        self,
        obstruction: Obstruction,
        morphisms: list[Morphism],
        sections: dict[str, LocalSection],
    ) -> RootCauseAnalysis:
        """Find the earliest coordinate in the morphism chain where descent fails.

        Walks backwards along morphisms from the symptom coordinate, collecting
        the causal chain.
        """
        radj = _build_reverse_adjacency(morphisms)
        visited: set[str] = set()
        root = self._walk_backwards(obstruction.coordinate_id, radj, sections, visited)
        if root is None:
            root = obstruction.coordinate_id

        # Build causal chain from root to symptom via BFS
        adj = _build_adjacency(morphisms)
        causal_chain = self._shortest_path(root, obstruction.coordinate_id, adj)
        if not causal_chain:
            causal_chain = [root, obstruction.coordinate_id]

        root_sec = sections.get(root)
        root_proposition = root_sec.proposition if root_sec else obstruction.proposition

        # Find alternative roots: other invalid sections that are ancestors
        alt_roots: list[str] = []
        ancestors = _reachable(obstruction.coordinate_id, radj)
        for anc in ancestors:
            if anc == root:
                continue
            sec = sections.get(anc)
            if sec is not None and not sec.is_valid:
                alt_roots.append(anc)

        return RootCauseAnalysis.make(
            symptom_id=obstruction.id,
            root_coordinate_id=root,
            root_proposition=root_proposition,
            causal_chain=causal_chain,
            confidence=0.9 if root != obstruction.coordinate_id else 0.5,
            alternative_roots=alt_roots[:5],
        )

    def _walk_backwards(
        self,
        coord: str,
        radj: dict[str, list[str]],
        sections: dict[str, LocalSection],
        visited: set[str],
    ) -> str | None:
        """Walk backwards along reverse morphisms to find the root failing coordinate.

        Traverses ALL ancestors (not just invalid ones), returning the earliest
        invalid coordinate found along any reverse path. Returns None only when
        no invalid ancestor exists.
        """
        if coord in visited:
            return None
        visited.add(coord)

        parents = radj.get(coord, [])
        if not parents:
            # No parents: this is the root of the chain.
            # Return this coord only if it's invalid.
            sec = sections.get(coord)
            if sec is not None and not sec.is_valid:
                return coord
            return None

        earliest: str | None = None
        for parent in parents:
            # Always walk into parents to find the earliest invalid ancestor
            candidate = self._walk_backwards(parent, radj, sections, visited)
            if candidate is not None:
                earliest = candidate

        # If no invalid ancestor found, check whether this coord itself is invalid
        if earliest is None:
            sec = sections.get(coord)
            if sec is not None and not sec.is_valid:
                return coord

        return earliest

    def _shortest_path(
        self,
        source: str,
        target: str,
        adj: dict[str, list[str]],
    ) -> list[str]:
        """BFS shortest path from source to target."""
        if source == target:
            return [source]
        visited: set[str] = {source}
        queue: deque[list[str]] = deque([[source]])
        while queue:
            path = queue.popleft()
            node = path[-1]
            for neighbor in adj.get(node, []):
                if neighbor == target:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return []

    def multiple_root_causes(
        self,
        obstructions: list[Obstruction],
        morphisms: list[Morphism],
        sections: dict[str, LocalSection],
    ) -> dict[str, list[str]]:
        """Group obstructions by their shared root cause.

        Returns mapping: root_coordinate_id → [obstruction_ids]
        """
        result: dict[str, list[str]] = defaultdict(list)
        for obs in obstructions:
            rca = self.find_root_cause(obs, morphisms, sections)
            result[rca.root_coordinate_id].append(obs.id)
        return dict(result)

    def causal_graph(
        self,
        obstructions: list[Obstruction],
        morphisms: list[Morphism],
    ) -> dict[str, list[str]]:
        """Build a DAG of causal relationships between obstructions.

        An obstruction A causes obstruction B if A's coordinate is an
        ancestor of B's coordinate in the morphism graph.
        Returns mapping: obstruction_id → [caused_obstruction_ids]
        """
        adj = _build_adjacency(morphisms)
        # Precompute reachability for each obstruction coordinate
        coord_to_obs: dict[str, str] = {o.coordinate_id: o.id for o in obstructions}
        obs_reachable: dict[str, set[str]] = {}
        for obs in obstructions:
            obs_reachable[obs.id] = _reachable(obs.coordinate_id, adj)

        causal: dict[str, list[str]] = {o.id: [] for o in obstructions}
        for obs_a in obstructions:
            for obs_b in obstructions:
                if obs_a.id == obs_b.id:
                    continue
                # A causes B if B's coord is reachable from A's coord
                if obs_b.coordinate_id in obs_reachable[obs_a.id]:
                    causal[obs_a.id].append(obs_b.id)
        return causal


# ---------------------------------------------------------------------------
# RepairFrontierComputer
# ---------------------------------------------------------------------------

class RepairFrontierComputer:
    """Compute minimal repair frontiers (minimum vertex cuts) for obstructions."""

    def compute_repair_frontier(
        self,
        obstruction: Obstruction,
        morphisms: list[Morphism],
        sections: dict[str, LocalSection],
    ) -> RepairFrontier:
        """Minimal set of coordinates to modify to resolve the obstruction.

        Uses minimum vertex cut between the root and all symptom coordinates.
        """
        adj = _build_adjacency(morphisms)
        radj = _build_reverse_adjacency(morphisms)

        # Find all ancestors (potential repair points)
        ancestors = _reachable(obstruction.coordinate_id, radj)
        ancestors.add(obstruction.coordinate_id)

        # The frontier is the minimal vertex cut between the root and the symptom
        targets = list(obstruction.downstream_ids) or [obstruction.coordinate_id]
        min_cut = self._minimal_vertex_cut(
            obstruction.coordinate_id, targets, morphisms
        )
        if not min_cut:
            min_cut = [obstruction.coordinate_id]

        code_complexity = {
            coord_id: len(sections[coord_id].metadata.get("loc", [1])) + 1
            if coord_id in sections else 1
            for coord_id in min_cut
        }
        effort = self.estimate_effort(min_cut, code_complexity)
        strategy = self.strategy_for_obstruction(obstruction, morphisms)

        # Side effects: all downstream of the cut that aren't already failing
        side_effects: set[str] = set()
        for coord in min_cut:
            downstream = _reachable(coord, adj)
            downstream.discard(coord)
            side_effects.update(downstream)
        side_effects -= set(min_cut)
        side_effects -= {obstruction.coordinate_id}

        return RepairFrontier.make(
            obstruction_id=obstruction.id,
            minimal_coordinates=min_cut,
            estimated_effort=effort,
            strategy=strategy,
            side_effects=sorted(side_effects)[:10],
        )

    def _minimal_vertex_cut(
        self,
        source: str,
        targets: list[str],
        morphisms: list[Morphism],
    ) -> list[str]:
        """Find a minimum vertex cut between source and targets.

        Uses a greedy approximation: find nodes that lie on paths from
        source to all target nodes, preferring nodes closer to the source.
        """
        adj = _build_adjacency(morphisms)
        radj = _build_reverse_adjacency(morphisms)

        # Nodes reachable from source
        fwd = _reachable(source, adj)
        # Nodes that can reach any target (backward from targets)
        bwd: set[str] = set()
        for t in targets:
            bwd |= _reachable(t, radj)
            bwd.add(t)

        # Candidate cut nodes: on some path source→target
        candidates = (fwd & bwd) - {source}
        if not candidates:
            return [source]

        # Sort by distance from source (BFS levels)
        levels: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque([(source, 0)])
        while queue:
            node, level = queue.popleft()
            if node in levels:
                continue
            levels[node] = level
            for neighbor in adj.get(node, []):
                if neighbor not in levels:
                    queue.append((neighbor, level + 1))

        sorted_candidates = sorted(candidates, key=lambda n: levels.get(n, 999))

        # Greedy: pick the smallest set that cuts all paths
        cut: list[str] = []
        remaining_targets = set(targets)
        covered: set[str] = set()

        for node in sorted_candidates:
            if not remaining_targets:
                break
            # Check if removing this node disconnects source from any target
            newly_covered = set()
            for t in remaining_targets:
                # Simple check: is t reachable from source via node?
                if node in _reachable(source, adj) and t in _reachable(node, adj):
                    newly_covered.add(t)
            if newly_covered:
                cut.append(node)
                remaining_targets -= newly_covered

        if not cut:
            cut = [source]
        return cut

    def estimate_effort(
        self,
        coordinates: list[str],
        code_complexity: dict[str, float],
    ) -> float:
        """Estimate repair effort from coordinate complexity.

        Effort is the sum of per-coordinate complexity, defaulting to 1.0 per
        coordinate if no complexity data is available. Normalized to hours.
        """
        if not coordinates:
            return 0.0
        total = 0.0
        for coord in coordinates:
            complexity = code_complexity.get(coord, 1.0)
            # Simple heuristic: 1 story point per 50 LoC
            total += max(1.0, float(complexity) / 50.0)
        return round(total, 2)

    def compute_repair_plan(
        self,
        obstructions: list[Obstruction],
        morphisms: list[Morphism],
        sections: dict[str, LocalSection],
    ) -> RepairPlan:
        """Topologically order repairs, handling prerequisites."""
        frontiers = [
            self.compute_repair_frontier(obs, morphisms, sections)
            for obs in obstructions
        ]
        prereqs = self.identify_prerequisites(frontiers)
        for frontier in frontiers:
            frontier.prerequisites = prereqs.get(frontier.obstruction_id, [])

        ordered = self._topological_sort_repairs(frontiers)
        blast_radius = 0
        for obs in obstructions:
            blast_radius = max(blast_radius, obs.blast_radius)

        strategies = [f.strategy.value for f in ordered]
        strategy_summary = _summarize_strategies(strategies)

        return RepairPlan.make(
            obstructions=[o.id for o in obstructions],
            ordered_repairs=ordered,
            blast_radius=blast_radius,
            strategy_summary=strategy_summary,
        )

    def _topological_sort_repairs(
        self,
        frontiers: list[RepairFrontier],
    ) -> list[RepairFrontier]:
        """Sort repairs by prerequisites (dependency order).

        Uses Kahn's algorithm over the prerequisite graph.
        """
        id_to_frontier = {f.obstruction_id: f for f in frontiers}
        adj: dict[str, list[str]] = {f.obstruction_id: [] for f in frontiers}
        in_degree: dict[str, int] = {f.obstruction_id: 0 for f in frontiers}

        for frontier in frontiers:
            for prereq_id in frontier.prerequisites:
                if prereq_id in adj:
                    adj[prereq_id].append(frontier.obstruction_id)
                    in_degree[frontier.obstruction_id] += 1

        queue: deque[str] = deque(
            fid for fid, deg in in_degree.items() if deg == 0
        )
        result: list[RepairFrontier] = []
        while queue:
            fid = queue.popleft()
            result.append(id_to_frontier[fid])
            for successor in adj[fid]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        # Append any remaining (cycles)
        remaining_ids = set(id_to_frontier) - {f.obstruction_id for f in result}
        for fid in sorted(remaining_ids):
            result.append(id_to_frontier[fid])

        return result

    def identify_prerequisites(
        self,
        frontiers: list[RepairFrontier],
    ) -> dict[str, list[str]]:
        """Determine which repairs must be completed before others.

        A repair F_a is a prerequisite of F_b if F_a's coordinates overlap
        with F_b's coordinates or appear in F_b's side_effects.
        """
        prereqs: dict[str, list[str]] = {f.obstruction_id: [] for f in frontiers}

        for i, frontier_b in enumerate(frontiers):
            coords_b = set(frontier_b.minimal_coordinates)
            for j, frontier_a in enumerate(frontiers):
                if i == j:
                    continue
                coords_a = set(frontier_a.minimal_coordinates)
                # If a's side effects touch b's coordinates, a must come first
                if coords_b & set(frontier_a.side_effects):
                    prereqs[frontier_b.obstruction_id].append(frontier_a.obstruction_id)

        return prereqs

    def strategy_for_obstruction(
        self,
        obstruction: Obstruction,
        morphisms: list[Morphism],
    ) -> RepairStrategy:
        """Choose the best repair strategy for an obstruction.

        Decision tree based on cohomology class and blast radius.
        """
        cls = obstruction.cohomology_class
        blast = obstruction.blast_radius

        if cls in (CohomologyClass.CONTRACT_VIOLATION, CohomologyClass.PROTOCOL_VIOLATION):
            return RepairStrategy.INTERFACE_RENEGOTIATION
        if cls in (CohomologyClass.DEADLOCK, CohomologyClass.RACE_CONDITION,
                   CohomologyClass.CONCURRENCY_HAZARD):
            return RepairStrategy.MANUAL_REVIEW
        if cls in (CohomologyClass.CONFIGURATION_ERROR, CohomologyClass.IMPORT_ERROR):
            return RepairStrategy.LOCAL_FIX
        if cls == CohomologyClass.STATE_CORRUPTION:
            return RepairStrategy.COVER_REFINEMENT
        if blast >= 10:
            return RepairStrategy.PROPAGATED_FIX
        if cls in (CohomologyClass.TYPE_ERROR, CohomologyClass.NULL_REFERENCE,
                   CohomologyClass.BOUNDS_VIOLATION):
            return RepairStrategy.LOCAL_FIX
        if cls == CohomologyClass.LOGIC_ERROR:
            return RepairStrategy.EVIDENCE_REFRESH
        return RepairStrategy.LOCAL_FIX


# ---------------------------------------------------------------------------
# ObstructionTriager
# ---------------------------------------------------------------------------

class ObstructionTriager:
    """Triage obstructions by clustering, scoring, and generating repair hints."""

    def cluster_obstructions(
        self,
        obstructions: list[Obstruction],
    ) -> list[ObstructionCluster]:
        """Group obstructions by cohomology class + coordinate pattern.

        Primary grouping key is cohomology class; coordinate pattern is derived
        from the common prefix of all coordinates in the group. This ensures
        that N obstructions spanning K cohomology classes produce exactly K clusters,
        reducing human review load from N to K items.
        """
        # Group by cohomology class first
        class_groups: dict[str, list[str]] = defaultdict(list)
        class_coords: dict[str, list[str]] = defaultdict(list)

        for obs in obstructions:
            class_groups[obs.cohomology_class.value].append(obs.id)
            class_coords[obs.cohomology_class.value].append(obs.coordinate_id)

        clusters: list[ObstructionCluster] = []
        for cls_val, obs_ids in class_groups.items():
            cls = CohomologyClass(cls_val)
            coords = class_coords[cls_val]
            pattern = self._extract_pattern(coords)
            root_cause = self._infer_root_cause(cls, pattern)
            batch_fix = self.batch_fixes_for_class(cls, pattern)
            cluster = ObstructionCluster.make(
                cohomology_class=cls,
                coordinate_pattern=pattern,
                obstructions=obs_ids,
                common_root_cause=root_cause,
                suggested_batch_fix=batch_fix,
            )
            clusters.append(cluster)

        return sorted(clusters, key=lambda c: c.count, reverse=True)

    def _find_merge_target(
        self,
        existing: dict[tuple[str, str], list[str]],
        cls_val: str,
        pattern: str,
    ) -> tuple[str, str]:
        """Find a compatible existing cluster key to merge into, or return new key."""
        for (ecls, epat) in existing:
            if ecls != cls_val:
                continue
            # Merge if patterns share a common prefix of at least 3 chars
            common = os.path.commonprefix([epat, pattern]) if epat and pattern else ""
            if len(common) >= 3 or epat == pattern:
                return (ecls, epat)
        return (cls_val, pattern)

    def _extract_pattern(self, coordinate_ids: list[str]) -> str:
        """Find common prefix/pattern in coordinate names.

        When multiple coordinates share a common prefix, returns that prefix
        with a wildcard. When no common prefix exists, returns "*".
        """
        if not coordinate_ids:
            return "*"
        if len(coordinate_ids) == 1:
            cid = coordinate_ids[0]
            parts = cid.replace(".", "/").replace("::", "/").split("/")
            if len(parts) > 1:
                return "/".join(parts[:-1]) + "/*"
            return cid
        prefix = os.path.commonprefix(coordinate_ids)
        if prefix and len(prefix) >= 2:
            return prefix.rstrip("/_-") + "/*"
        return "*"

    def _infer_root_cause(self, cls: CohomologyClass, pattern: str) -> str | None:
        """Infer a human-readable root cause description for a cluster."""
        _descriptions = {
            CohomologyClass.TYPE_ERROR: "Type contract violated — likely interface mismatch",
            CohomologyClass.NULL_REFERENCE: "Null dereference — missing guard before access",
            CohomologyClass.BOUNDS_VIOLATION: "Collection access without bounds check",
            CohomologyClass.CONTRACT_VIOLATION: "Pre/postcondition not enforced at boundary",
            CohomologyClass.PROTOCOL_VIOLATION: "Protocol state machine divergence",
            CohomologyClass.CONCURRENCY_HAZARD: "Shared mutable state without synchronization",
            CohomologyClass.IMPORT_ERROR: "Missing or circular dependency in module graph",
            CohomologyClass.CONFIGURATION_ERROR: "Missing or malformed configuration value",
            CohomologyClass.LOGIC_ERROR: "Incorrect algorithm or business rule",
            CohomologyClass.STATE_CORRUPTION: "Inconsistent state transition sequence",
            CohomologyClass.DEADLOCK: "Circular lock acquisition order",
            CohomologyClass.RACE_CONDITION: "Non-atomic read-modify-write on shared data",
            CohomologyClass.MEMORY_LEAK: "Resource not released after use",
            CohomologyClass.RESOURCE_LEAK: "File/connection not closed on all paths",
            CohomologyClass.API_MISUSE: "Incorrect API argument order or missing required field",
            CohomologyClass.ENCODING_MISMATCH: "Character encoding inconsistency across boundaries",
            CohomologyClass.PERMISSION_ERROR: "Insufficient permissions for required operation",
            CohomologyClass.ASSERTION_FAILURE: "Runtime invariant violated",
            CohomologyClass.UNKNOWN: None,
        }
        return _descriptions.get(cls)

    def triage(
        self,
        obstructions: list[Obstruction],
        morphisms: list[Morphism],
        critical_paths: list[str] | None = None,
    ) -> TriageReport:
        """Full triage report for a set of obstructions."""
        critical_set = set(critical_paths or [])
        clusters = self.cluster_obstructions(obstructions)

        frontier_computer = RepairFrontierComputer()
        sections: dict[str, LocalSection] = {}  # not needed for effort estimation here

        auto_fixable = 0
        needs_manual = 0
        total_effort = 0.0

        for obs in obstructions:
            frontier = frontier_computer.compute_repair_frontier(obs, morphisms, sections)
            if self.auto_fixable(obs, frontier):
                auto_fixable += 1
            else:
                needs_manual += 1
            total_effort += frontier.estimated_effort

        return TriageReport.make(
            obstructions=obstructions,
            clusters=clusters,
            estimated_total_effort=round(total_effort, 2),
            auto_fixable_count=auto_fixable,
            needs_manual_count=needs_manual,
        )

    def auto_fixable(
        self,
        obstruction: Obstruction,
        repair_frontier: RepairFrontier,
    ) -> bool:
        """Can this obstruction be auto-fixed?

        Criteria: low blast radius, clear frontier (1-2 coords), non-critical severity,
        and a deterministic repair strategy.
        """
        if obstruction.severity in (ObstructionSeverity.BLOCKER, ObstructionSeverity.CRITICAL):
            return False
        if obstruction.blast_radius > 5:
            return False
        if repair_frontier.strategy == RepairStrategy.MANUAL_REVIEW:
            return False
        if len(repair_frontier.minimal_coordinates) > 3:
            return False
        if obstruction.cohomology_class in (
            CohomologyClass.DEADLOCK,
            CohomologyClass.RACE_CONDITION,
            CohomologyClass.STATE_CORRUPTION,
        ):
            return False
        return True

    def batch_fixes(self, cluster: ObstructionCluster) -> str | None:
        """Suggest a batch fix description for a cluster."""
        return self.batch_fixes_for_class(cluster.cohomology_class, cluster.coordinate_pattern)

    def batch_fixes_for_class(self, cls: CohomologyClass, pattern: str) -> str | None:
        """Generate batch fix suggestion for a cohomology class + pattern."""
        _fixes: dict[CohomologyClass, str] = {
            CohomologyClass.TYPE_ERROR: f"Add runtime type guards at all {pattern} entry points",
            CohomologyClass.NULL_REFERENCE: f"Add null checks before all dereferences in {pattern}",
            CohomologyClass.BOUNDS_VIOLATION: f"Add bounds validation in {pattern} collection accessors",
            CohomologyClass.CONTRACT_VIOLATION: f"Enforce contracts with decorators in {pattern}",
            CohomologyClass.IMPORT_ERROR: f"Audit and fix import graph in {pattern}",
            CohomologyClass.CONFIGURATION_ERROR: f"Add configuration validation on startup in {pattern}",
            CohomologyClass.ENCODING_MISMATCH: f"Standardize encoding to UTF-8 across {pattern}",
            CohomologyClass.API_MISUSE: f"Add argument validation and documentation in {pattern}",
            CohomologyClass.PERMISSION_ERROR: f"Audit permission checks in {pattern}",
        }
        return _fixes.get(cls)

    def priority_score(self, obstruction: Obstruction) -> float:
        """Score an obstruction by severity × blast_radius × criticality weight.

        Higher score = should be fixed sooner.
        """
        severity_weight = obstruction.severity.numeric_weight
        blast_factor = max(1, obstruction.blast_radius)
        criticality = 2.0 if obstruction.morphism_chain else 1.0
        return float(severity_weight * blast_factor * criticality)


# ---------------------------------------------------------------------------
# CountermodelAnalyzer
# ---------------------------------------------------------------------------

class CountermodelAnalyzer:
    """Extract, analyze, and convert countermodels from obstructions."""

    def extract_countermodel(
        self,
        obstruction: Obstruction,
        sections: dict[str, LocalSection],
    ) -> CountermodelReport:
        """Build a concrete failing input from the obstruction.

        The countermodel is the witness to the descent failure:
        concrete inputs that reproduce the bug.
        """
        section = sections.get(obstruction.coordinate_id)
        concrete_inputs: dict[str, Any] = {}
        expected_output: Any = None
        actual_output: Any = None

        if obstruction.countermodel:
            concrete_inputs = {
                k: v for k, v in obstruction.countermodel.items()
                if k not in ("failure",)
            }
            actual_output = obstruction.countermodel.get("failure") or obstruction.countermodel.get("actual_output")

        if section is not None:
            concrete_inputs["coordinate_value"] = section.value
            concrete_inputs["metadata"] = dict(section.metadata)
            if "expected" in section.metadata:
                expected_output = section.metadata["expected"]

        # Derive suggested test from the countermodel
        suggested_test = self._generate_test_stub(
            obstruction, concrete_inputs, expected_output, actual_output
        )

        reproducible = self.reproducibility_check(
            CountermodelReport.make(
                obstruction_id=obstruction.id,
                coordinate_id=obstruction.coordinate_id,
                proposition=obstruction.proposition,
                concrete_inputs=concrete_inputs,
                expected_output=expected_output,
                actual_output=actual_output,
                reproducible=True,
                suggested_test=suggested_test,
            )
        )

        return CountermodelReport.make(
            obstruction_id=obstruction.id,
            coordinate_id=obstruction.coordinate_id,
            proposition=obstruction.proposition,
            concrete_inputs=concrete_inputs,
            expected_output=expected_output,
            actual_output=actual_output,
            reproducible=reproducible,
            suggested_test=suggested_test,
        )

    def _generate_test_stub(
        self,
        obstruction: Obstruction,
        concrete_inputs: dict[str, Any],
        expected_output: Any,
        actual_output: Any,
    ) -> str | None:
        """Generate a pytest-style test stub from the countermodel."""
        if not concrete_inputs:
            return None
        coord = obstruction.coordinate_id.replace("/", "_").replace(".", "_").replace("-", "_")
        inputs_repr = repr(dict(concrete_inputs))
        expected_repr = repr(expected_output)
        actual_repr = repr(actual_output)
        return (
            f"def test_{coord}_regression():\n"
            f"    # Countermodel: {obstruction.proposition!r}\n"
            f"    inputs = {inputs_repr}\n"
            f"    expected = {expected_repr}\n"
            f"    # actual was: {actual_repr}\n"
            f"    # TODO: call the function under test with inputs and assert result == expected\n"
            f"    raise NotImplementedError('Fill in the test body')\n"
        )

    def countermodel_to_test(self, report: CountermodelReport) -> dict[str, Any]:
        """Convert a countermodel report to a structured test obligation dict."""
        return {
            "test_id": _new_id("test"),
            "obstruction_id": report.obstruction_id,
            "coordinate_id": report.coordinate_id,
            "proposition": report.proposition,
            "inputs": dict(report.concrete_inputs),
            "expected_output": report.expected_output,
            "actual_output": report.actual_output,
            "reproducible": report.reproducible,
            "suggested_test": report.suggested_test,
            "test_type": "regression",
            "priority": "high" if report.reproducible else "low",
            "created_at": _now_iso(),
        }

    def batch_countermodels(
        self,
        obstructions: list[Obstruction],
        sections: dict[str, LocalSection],
    ) -> list[CountermodelReport]:
        """Extract countermodels for all obstructions."""
        return [self.extract_countermodel(obs, sections) for obs in obstructions]

    def reproducibility_check(self, report: CountermodelReport) -> bool:
        """Verify the countermodel is reproducible.

        A countermodel is reproducible if:
        - It has concrete inputs
        - The actual output is not None or is a string description
        - It does not depend on external non-determinism (timing, random, etc.)
        """
        if not report.concrete_inputs:
            return False
        non_deterministic_signals = ("random", "time", "uuid", "now", "timestamp", "nondeterministic")
        inputs_str = str(report.concrete_inputs).lower()
        proposition_lower = report.proposition.lower()
        if any(sig in inputs_str for sig in non_deterministic_signals):
            return False
        if any(sig in proposition_lower for sig in non_deterministic_signals):
            return False
        return True


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------

def _summarize_strategies(strategy_values: list[str]) -> str:
    """Summarize a list of strategy values into a human-readable description."""
    if not strategy_values:
        return "No repairs needed."
    from collections import Counter
    counts = Counter(strategy_values)
    parts = [f"{count}× {strat.replace('_', ' ')}" for strat, count in counts.most_common()]
    return "Repair strategies: " + ", ".join(parts)


# ---------------------------------------------------------------------------
# Lazy import for os (used in cluster pattern extraction)
# ---------------------------------------------------------------------------
import os  # noqa: E402 — placed after module definitions for clarity
