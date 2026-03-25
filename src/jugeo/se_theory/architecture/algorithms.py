"""Core architectural analysis algorithms for sheaf-theoretic SE.

All algorithms use pure Python stdlib only — no numpy/scipy dependencies.
The key insight: software architecture IS cover design:

- Coupling = overlap density between cover members
- Cohesion = internal edge density within a cover member
- Circular dependencies = non-trivial SCCs in the dependency graph
- Dependency depth = longest path in the condensed DAG
- Instability = efferent / (afferent + efferent) coupling
- Abstractness = fraction of abstract coordinates

Algorithms provided:
- TarjanSCC: Tarjan's strongly connected components
- CoverAnalyzer: Compute architectural quality metrics
- CoverSuggester: Suggest optimal cover via graph partitioning
- ArchitectureEnforcer: Verify code against declared architecture
- ArchitectureTracker: Track architectural evolution over time
"""
from __future__ import annotations

import fnmatch
import json
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jugeo.se_theory.architecture.models import (
    ArchitecturalDecision,
    ArchitecturalDecisionKind,
    ArchitecturalDrift,
    ArchitecturalManifest,
    ArchitecturalSnapshot,
    BoundaryViolation,
    CoverMember,
    CoverMemberKind,
    CoverQualityMetrics,
    DeclaredBoundary,
)


# ---------------------------------------------------------------------------
# TarjanSCC — strongly connected components
# ---------------------------------------------------------------------------


class TarjanSCC:
    """Tarjan's algorithm for finding strongly connected components.

    This is the foundational algorithm for detecting circular dependencies
    in the architectural graph.  SCCs with more than one node represent
    true dependency cycles.
    """

    @staticmethod
    def find_sccs(adjacency: dict[str, list[str]]) -> list[list[str]]:
        """Return all SCCs including singletons.

        Parameters
        ----------
        adjacency : dict mapping node -> list of successors

        Returns
        -------
        list of lists, each inner list is one SCC.
        """
        index_counter = [0]
        index: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        on_stack: dict[str, bool] = {}
        stack: list[str] = []
        sccs: list[list[str]] = []

        all_nodes: set[str] = set(adjacency.keys())
        for targets in adjacency.values():
            for t in targets:
                all_nodes.add(t)

        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True

            for w in adjacency.get(v, []):
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], index[w])

            if lowlink[v] == index[v]:
                scc: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)

        for node in sorted(all_nodes):
            if node not in index:
                strongconnect(node)

        return sccs

    @staticmethod
    def find_nontrivial_sccs(adjacency: dict[str, list[str]]) -> list[list[str]]:
        """Return only SCCs with more than one node (true cycles)."""
        all_sccs = TarjanSCC.find_sccs(adjacency)
        nontrivial = []
        for scc in all_sccs:
            if len(scc) > 1:
                nontrivial.append(scc)
            elif len(scc) == 1:
                # Check for self-loop
                node = scc[0]
                if node in adjacency.get(node, []):
                    nontrivial.append(scc)
        return nontrivial

    @staticmethod
    def condense_to_dag(
        adjacency: dict[str, list[str]], sccs: list[list[str]]
    ) -> dict[str, list[str]]:
        """Replace SCCs with super-nodes to produce a DAG.

        Super-nodes are labelled "SCC_0", "SCC_1", etc. for non-trivial
        SCCs.  Singleton SCCs keep their original node name.

        Returns
        -------
        New adjacency dict representing the condensed DAG.
        """
        # Map each node to its super-node label
        node_to_super: dict[str, str] = {}
        super_idx = 0
        for scc in sccs:
            if len(scc) > 1:
                label = f"SCC_{super_idx}"
                super_idx += 1
                for node in scc:
                    node_to_super[node] = label
            else:
                node = scc[0]
                # Check self-loop
                if node in adjacency.get(node, []):
                    label = f"SCC_{super_idx}"
                    super_idx += 1
                    node_to_super[node] = label
                else:
                    node_to_super[node] = node

        # Build condensed adjacency
        dag: dict[str, list[str]] = defaultdict(list)
        seen_edges: set[tuple[str, str]] = set()

        for src, targets in adjacency.items():
            src_super = node_to_super.get(src, src)
            for tgt in targets:
                tgt_super = node_to_super.get(tgt, tgt)
                if src_super != tgt_super:
                    edge = (src_super, tgt_super)
                    if edge not in seen_edges:
                        seen_edges.add(edge)
                        dag[src_super].append(tgt_super)

        # Ensure all super-nodes appear as keys
        for label in set(node_to_super.values()):
            if label not in dag:
                dag[label] = []

        return dict(dag)


# ---------------------------------------------------------------------------
# CoverAnalyzer — architectural quality metrics
# ---------------------------------------------------------------------------


class CoverAnalyzer:
    """Compute architectural quality metrics over a cover decomposition.

    Given cover members (modules) and morphisms (dependencies), compute
    coupling, cohesion, interface widths, dependency depth, circular
    dependencies, instability, and abstractness.
    """

    @staticmethod
    def compute_coupling(
        cover_members: list[CoverMember],
        morphisms: list[tuple[str, str]],
    ) -> dict[str, float]:
        """Compute coupling scores for each cover member.

        Coupling for a member = shared_coordinates_with_others / (|member_coords| * num_others).
        Also considers cross-member morphisms.

        Returns dict member_id -> coupling score in [0, 1].
        """
        if not cover_members:
            return {}

        # Build coordinate -> member mapping
        coord_to_members: dict[str, list[str]] = defaultdict(list)
        for member in cover_members:
            for coord in member.coordinates:
                coord_to_members[coord].append(member.id)

        # Build member coordinate sets
        member_coords: dict[str, set[str]] = {
            m.id: set(m.coordinates) for m in cover_members
        }

        # Count cross-member morphisms per member
        member_coord_sets = {m.id: set(m.coordinates) for m in cover_members}
        cross_morphism_count: dict[str, int] = defaultdict(int)
        for src, tgt in morphisms:
            src_members = coord_to_members.get(src, [])
            tgt_members = coord_to_members.get(tgt, [])
            for sm in src_members:
                for tm in tgt_members:
                    if sm != tm:
                        cross_morphism_count[sm] += 1
                        cross_morphism_count[tm] += 1

        scores: dict[str, float] = {}
        num_others = max(len(cover_members) - 1, 1)

        for member in cover_members:
            if not member.coordinates:
                scores[member.id] = 0.0
                continue

            # Count shared coordinates
            shared_count = 0
            for coord in member.coordinates:
                owners = coord_to_members.get(coord, [])
                if len(owners) > 1:
                    shared_count += 1

            # Combine shared coordinates with cross-member morphisms
            coord_coupling = shared_count / (len(member.coordinates) * num_others) if member.coordinates else 0.0
            morphism_coupling = cross_morphism_count.get(member.id, 0) / (
                len(member.coordinates) * num_others * 2
            ) if member.coordinates else 0.0

            raw_score = coord_coupling + morphism_coupling
            scores[member.id] = min(1.0, raw_score)

        return scores

    @staticmethod
    def compute_cohesion(
        cover_members: list[CoverMember],
        morphisms: list[tuple[str, str]],
    ) -> dict[str, float]:
        """Compute cohesion scores for each cover member.

        Cohesion = internal_edges / max_possible_internal_edges.
        max_possible = n*(n-1)/2 for n coordinates.

        Returns dict member_id -> cohesion score in [0, 1].
        """
        if not cover_members:
            return {}

        # Build morphism set for fast lookup
        morphism_set: set[tuple[str, str]] = set(morphisms)

        scores: dict[str, float] = {}
        for member in cover_members:
            coords = set(member.coordinates)
            n = len(coords)
            if n <= 1:
                scores[member.id] = 1.0
                continue

            max_possible = n * (n - 1) / 2
            internal_edges = 0
            for src, tgt in morphisms:
                if src in coords and tgt in coords:
                    internal_edges += 1

            # Also count declared internal morphisms
            for morph_str in member.internal_morphisms:
                parts = morph_str.split("->")
                if len(parts) == 2:
                    s, t = parts[0].strip(), parts[1].strip()
                    if s in coords and t in coords and (s, t) not in morphism_set:
                        internal_edges += 1

            scores[member.id] = min(1.0, internal_edges / max_possible) if max_possible > 0 else 1.0

        return scores

    @staticmethod
    def compute_interface_widths(
        cover_members: list[CoverMember],
    ) -> dict[tuple[str, str], int]:
        """Compute interface widths between all pairs of cover members.

        Width = number of shared coordinates.
        Returns dict (member_a_id, member_b_id) -> width.
        Only includes pairs with width > 0.
        """
        widths: dict[tuple[str, str], int] = {}

        for i, m_a in enumerate(cover_members):
            coords_a = set(m_a.coordinates)
            for j in range(i + 1, len(cover_members)):
                m_b = cover_members[j]
                coords_b = set(m_b.coordinates)
                shared = coords_a & coords_b
                if shared:
                    widths[(m_a.id, m_b.id)] = len(shared)

        return widths

    @staticmethod
    def compute_dependency_depth(morphisms: list[tuple[str, str]]) -> int:
        """Compute the longest path in the dependency graph.

        Cycles are collapsed to single nodes using Tarjan's SCC.
        The depth is the longest path in the resulting DAG.
        """
        if not morphisms:
            return 0

        adjacency: dict[str, list[str]] = defaultdict(list)
        for src, tgt in morphisms:
            adjacency[src].append(tgt)

        sccs = TarjanSCC.find_sccs(dict(adjacency))
        dag = TarjanSCC.condense_to_dag(dict(adjacency), sccs)

        # Topological sort + longest path
        in_degree: dict[str, int] = defaultdict(int)
        all_nodes: set[str] = set(dag.keys())
        for targets in dag.values():
            for t in targets:
                in_degree[t] += 1
                all_nodes.add(t)

        # BFS-based longest path (Kahn's algorithm variant)
        dist: dict[str, int] = {n: 0 for n in all_nodes}
        queue: list[str] = [n for n in all_nodes if in_degree.get(n, 0) == 0]

        while queue:
            next_queue: list[str] = []
            for node in queue:
                for neighbor in dag.get(node, []):
                    if dist[node] + 1 > dist[neighbor]:
                        dist[neighbor] = dist[node] + 1
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_queue.append(neighbor)
            queue = next_queue

        return max(dist.values()) if dist else 0

    @staticmethod
    def detect_circular_dependencies(
        morphisms: list[tuple[str, str]],
    ) -> list[list[str]]:
        """Find circular dependency cycles in the morphism graph.

        Returns list of cycles (each cycle is a list of node ids).
        Only returns non-trivial SCCs (size > 1).
        """
        adjacency: dict[str, list[str]] = defaultdict(list)
        for src, tgt in morphisms:
            adjacency[src].append(tgt)
        return TarjanSCC.find_nontrivial_sccs(dict(adjacency))

    @staticmethod
    def compute_instability(
        member_id: str,
        morphisms: list[tuple[str, str]],
    ) -> float:
        """Compute instability for a member: Ce / (Ca + Ce).

        Ce = efferent (outgoing) dependencies.
        Ca = afferent (incoming) dependencies.
        Returns 0.5 if both are 0.
        """
        ce = sum(1 for src, _ in morphisms if src == member_id)
        ca = sum(1 for _, tgt in morphisms if tgt == member_id)

        if ce + ca == 0:
            return 0.5
        return ce / (ce + ca)

    @staticmethod
    def compute_abstractness(member: CoverMember) -> float:
        """Compute abstractness: abstract_coordinates / total_coordinates.

        Heuristic: coordinates containing "abstract", "interface",
        "base", or "protocol" in their id are considered abstract.
        """
        if not member.coordinates:
            return 0.0

        abstract_keywords = {"abstract", "interface", "base", "protocol"}
        abstract_count = 0
        for coord in member.coordinates:
            coord_lower = coord.lower()
            if any(kw in coord_lower for kw in abstract_keywords):
                abstract_count += 1

        return abstract_count / len(member.coordinates)

    @staticmethod
    def full_quality_analysis(
        cover_members: list[CoverMember],
        morphisms: list[tuple[str, str]],
        cover_id: str = "",
    ) -> CoverQualityMetrics:
        """Run complete quality analysis on a cover.

        Returns a CoverQualityMetrics with all fields populated.
        """
        if not cover_id:
            cover_id = f"cover_{uuid.uuid4().hex[:8]}"

        coupling_scores = CoverAnalyzer.compute_coupling(cover_members, morphisms)
        cohesion_scores = CoverAnalyzer.compute_cohesion(cover_members, morphisms)
        interface_widths = CoverAnalyzer.compute_interface_widths(cover_members)
        dep_depth = CoverAnalyzer.compute_dependency_depth(morphisms)
        cycles = CoverAnalyzer.detect_circular_dependencies(morphisms)

        # Build member-level morphisms for instability
        member_ids = {m.id for m in cover_members}
        member_morphisms: list[tuple[str, str]] = []
        coord_to_member: dict[str, str] = {}
        for m in cover_members:
            for c in m.coordinates:
                coord_to_member[c] = m.id
        for src, tgt in morphisms:
            src_m = coord_to_member.get(src)
            tgt_m = coord_to_member.get(tgt)
            if src_m and tgt_m and src_m != tgt_m:
                member_morphisms.append((src_m, tgt_m))

        instability_scores = {
            m.id: CoverAnalyzer.compute_instability(m.id, member_morphisms)
            for m in cover_members
        }
        abstractness_scores = {
            m.id: CoverAnalyzer.compute_abstractness(m) for m in cover_members
        }

        # Count orphan coordinates
        covered_coords: set[str] = set()
        for m in cover_members:
            covered_coords.update(m.coordinates)
        all_coords: set[str] = set()
        for src, tgt in morphisms:
            all_coords.add(src)
            all_coords.add(tgt)
        orphan_count = len(all_coords - covered_coords)

        # Aggregate coupling/cohesion
        avg_coupling = (
            sum(coupling_scores.values()) / len(coupling_scores)
            if coupling_scores
            else 0.0
        )
        avg_cohesion = (
            sum(cohesion_scores.values()) / len(cohesion_scores)
            if cohesion_scores
            else 0.0
        )

        # Interface width stats
        width_values = list(interface_widths.values())
        avg_width = sum(width_values) / len(width_values) if width_values else 0.0
        max_width = max(width_values) if width_values else 0

        return CoverQualityMetrics(
            cover_id=cover_id,
            coupling_score=avg_coupling,
            cohesion_score=avg_cohesion,
            avg_interface_width=avg_width,
            max_interface_width=max_width,
            dependency_depth=dep_depth,
            circular_dependency_count=len(cycles),
            instability_scores=instability_scores,
            abstractness_scores=abstractness_scores,
            orphan_coordinate_count=orphan_count,
            total_members=len(cover_members),
            total_overlaps=len(interface_widths),
        )


# ---------------------------------------------------------------------------
# CoverSuggester — graph partitioning for cover synthesis
# ---------------------------------------------------------------------------


class CoverSuggester:
    """Suggest optimal architectural covers using graph partitioning.

    Uses a Kernighan-Lin style bisection algorithm to partition
    coordinates into groups minimizing inter-group edges.
    """

    @staticmethod
    def suggest_cover(
        coordinates: list[str],
        morphisms: list[tuple[str, str]],
        target_coupling: float = 0.3,
        max_members: int = 10,
    ) -> list[CoverMember]:
        """Partition coordinates into cover members.

        Uses recursive Kernighan-Lin bisection to minimize
        inter-group edges (coupling).
        """
        if not coordinates:
            return []

        adjacency = CoverSuggester._build_adjacency(coordinates, morphisms)
        k = CoverSuggester._estimate_k(len(coordinates), morphisms)
        k = min(k, max_members)

        node_set = set(coordinates)
        clusters = CoverSuggester._recursive_partition(node_set, adjacency, k)
        return CoverSuggester._assign_to_clusters(clusters)

    @staticmethod
    def _build_adjacency(
        coordinates: list[str],
        morphisms: list[tuple[str, str]],
    ) -> dict[str, set[str]]:
        """Build undirected adjacency from morphisms."""
        adj: dict[str, set[str]] = defaultdict(set)
        coord_set = set(coordinates)
        for src, tgt in morphisms:
            if src in coord_set and tgt in coord_set:
                adj[src].add(tgt)
                adj[tgt].add(src)
        # Ensure all coordinates appear
        for c in coordinates:
            if c not in adj:
                adj[c] = set()
        return dict(adj)

    @staticmethod
    def _kernighan_lin_partition(
        nodes: set[str],
        adjacency: dict[str, set[str]],
    ) -> tuple[set[str], set[str]]:
        """Kernighan-Lin bisection to minimize cut edges.

        Iteratively swaps node pairs between partitions to reduce
        the number of cross-partition edges.
        """
        if len(nodes) <= 1:
            return nodes, set()

        node_list = sorted(nodes)
        mid = len(node_list) // 2
        part_a = set(node_list[:mid])
        part_b = set(node_list[mid:])

        improved = True
        max_iterations = min(len(nodes) * 2, 100)
        iteration = 0

        while improved and iteration < max_iterations:
            improved = False
            iteration += 1
            best_gain = 0
            best_swap: tuple[str | None, str | None] = (None, None)

            # Compute D values: D(v) = external_cost(v) - internal_cost(v)
            d_values: dict[str, int] = {}
            for v in nodes:
                my_part = part_a if v in part_a else part_b
                other_part = part_b if v in part_a else part_a
                internal = len(adjacency.get(v, set()) & my_part)
                external = len(adjacency.get(v, set()) & other_part)
                d_values[v] = external - internal

            # Find best swap
            for a_node in sorted(part_a):
                for b_node in sorted(part_b):
                    cost_ab = 1 if b_node in adjacency.get(a_node, set()) else 0
                    gain = d_values[a_node] + d_values[b_node] - 2 * cost_ab
                    if gain > best_gain:
                        best_gain = gain
                        best_swap = (a_node, b_node)

            if best_swap[0] is not None and best_swap[1] is not None:
                part_a.remove(best_swap[0])
                part_b.remove(best_swap[1])
                part_a.add(best_swap[1])
                part_b.add(best_swap[0])
                improved = True

        return part_a, part_b

    @staticmethod
    def _recursive_partition(
        nodes: set[str],
        adjacency: dict[str, set[str]],
        k: int,
    ) -> list[set[str]]:
        """Recursively partition nodes into k groups."""
        if k <= 1 or len(nodes) <= 1:
            return [nodes] if nodes else []

        part_a, part_b = CoverSuggester._kernighan_lin_partition(nodes, adjacency)

        if not part_a:
            return [part_b] if part_b else []
        if not part_b:
            return [part_a] if part_a else []

        # Distribute k among sub-partitions proportionally
        ratio = len(part_a) / len(nodes) if nodes else 0.5
        k_a = max(1, round(k * ratio))
        k_b = max(1, k - k_a)

        result: list[set[str]] = []
        result.extend(CoverSuggester._recursive_partition(part_a, adjacency, k_a))
        result.extend(CoverSuggester._recursive_partition(part_b, adjacency, k_b))
        return result

    @staticmethod
    def _estimate_k(
        n_coordinates: int,
        morphisms: list[tuple[str, str]],
    ) -> int:
        """Estimate number of clusters from graph density.

        k = max(2, n // 5) clamped to reasonable range.
        """
        if n_coordinates <= 2:
            return 1
        k = max(2, n_coordinates // 5)
        return min(k, 20)

    @staticmethod
    def _assign_to_clusters(
        clusters: list[set[str]],
        kind: CoverMemberKind = CoverMemberKind.MODULE,
    ) -> list[CoverMember]:
        """Create CoverMember objects from cluster sets."""
        members: list[CoverMember] = []
        for i, cluster in enumerate(clusters):
            if not cluster:
                continue
            member = CoverMember(
                id=f"cluster_{i}",
                name=f"cluster_{i}",
                kind=kind,
                coordinates=sorted(cluster),
            )
            members.append(member)
        return members

    @staticmethod
    def refine_cover(
        cover_members: list[CoverMember],
        morphisms: list[tuple[str, str]],
        quality_target: float = 0.7,
    ) -> list[CoverMember]:
        """Iteratively move boundary coordinates to improve coupling.

        Moves coordinates that have more external than internal edges
        to the member where they have the most connections.
        """
        if not cover_members:
            return []

        # Build mutable assignment: coord -> member_index
        coord_to_idx: dict[str, int] = {}
        for i, m in enumerate(cover_members):
            for c in m.coordinates:
                coord_to_idx[c] = i

        # Build adjacency
        all_coords = list(coord_to_idx.keys())
        adjacency = CoverSuggester._build_adjacency(all_coords, morphisms)

        max_iterations = 50
        for _ in range(max_iterations):
            moved = False
            for coord in sorted(coord_to_idx.keys()):
                current_idx = coord_to_idx[coord]
                neighbors = adjacency.get(coord, set())

                # Count neighbors per cluster
                cluster_counts: dict[int, int] = defaultdict(int)
                for nb in neighbors:
                    if nb in coord_to_idx:
                        cluster_counts[coord_to_idx[nb]] += 1

                # Find best cluster
                best_idx = current_idx
                best_count = cluster_counts.get(current_idx, 0)
                for idx, count in cluster_counts.items():
                    if count > best_count:
                        best_count = count
                        best_idx = idx

                if best_idx != current_idx:
                    coord_to_idx[coord] = best_idx
                    moved = True

            if not moved:
                break

        # Rebuild members
        clusters: dict[int, list[str]] = defaultdict(list)
        for coord, idx in coord_to_idx.items():
            clusters[idx].append(coord)

        result: list[CoverMember] = []
        for i, original in enumerate(cover_members):
            coords = sorted(clusters.get(i, []))
            if coords:
                result.append(
                    CoverMember(
                        id=original.id,
                        name=original.name,
                        kind=original.kind,
                        coordinates=coords,
                        internal_morphisms=original.internal_morphisms,
                        external_morphisms=original.external_morphisms,
                        metadata=original.metadata,
                    )
                )
        return result

    @staticmethod
    def suggest_splits(
        member: CoverMember,
        morphisms: list[tuple[str, str]],
    ) -> list[ArchitecturalDecision]:
        """Suggest splitting a member with poor internal cohesion."""
        if len(member.coordinates) < 4:
            return []

        coords = set(member.coordinates)
        internal_edges = 0
        total_possible = len(coords) * (len(coords) - 1) / 2

        for src, tgt in morphisms:
            if src in coords and tgt in coords:
                internal_edges += 1

        if total_possible == 0:
            return []

        cohesion = internal_edges / total_possible
        if cohesion >= 0.5:
            return []

        # Suggest a split
        adjacency = CoverSuggester._build_adjacency(
            list(coords), morphisms
        )
        part_a, part_b = CoverSuggester._kernighan_lin_partition(
            coords, adjacency
        )

        if not part_a or not part_b:
            return []

        return [
            ArchitecturalDecision(
                id=f"split_{member.id}_{uuid.uuid4().hex[:6]}",
                kind=ArchitecturalDecisionKind.SPLIT_PACKAGE,
                target_members=[member.id],
                description=(
                    f"Split {member.name} into two parts: "
                    f"{sorted(part_a)[:3]}... and {sorted(part_b)[:3]}..."
                ),
                expected_coupling_change=-0.1,
                expected_cohesion_change=0.2,
                blast_radius=len(member.coordinates),
                confidence=0.6,
                rationale=(
                    f"Member has low cohesion ({cohesion:.2f}). "
                    f"Internal edges: {internal_edges}/{int(total_possible)}."
                ),
            )
        ]

    @staticmethod
    def suggest_merges(
        members: list[CoverMember],
        morphisms: list[tuple[str, str]],
    ) -> list[ArchitecturalDecision]:
        """Suggest merging members with high mutual coupling."""
        decisions: list[ArchitecturalDecision] = []

        if len(members) < 2:
            return decisions

        # Build coord -> member mapping
        coord_to_member: dict[str, str] = {}
        for m in members:
            for c in m.coordinates:
                coord_to_member[c] = m.id

        # Count cross-member edges for each pair
        pair_edges: dict[tuple[str, str], int] = defaultdict(int)
        member_sizes: dict[str, int] = {m.id: len(m.coordinates) for m in members}

        for src, tgt in morphisms:
            src_m = coord_to_member.get(src)
            tgt_m = coord_to_member.get(tgt)
            if src_m and tgt_m and src_m != tgt_m:
                key = tuple(sorted([src_m, tgt_m]))
                pair_edges[key] += 1  # type: ignore[arg-type]

        for (m_a, m_b), edge_count in pair_edges.items():
            size_a = member_sizes.get(m_a, 1)
            size_b = member_sizes.get(m_b, 1)
            max_possible = size_a * size_b
            coupling = edge_count / max_possible if max_possible > 0 else 0

            if coupling > 0.3 or edge_count >= min(size_a, size_b):
                decisions.append(
                    ArchitecturalDecision(
                        id=f"merge_{m_a}_{m_b}_{uuid.uuid4().hex[:6]}",
                        kind=ArchitecturalDecisionKind.MERGE_MODULES,
                        target_members=[m_a, m_b],
                        description=f"Merge {m_a} and {m_b} (coupling={coupling:.2f})",
                        expected_coupling_change=-coupling,
                        expected_cohesion_change=0.1,
                        blast_radius=size_a + size_b,
                        confidence=min(0.9, coupling + 0.3),
                        rationale=(
                            f"{edge_count} cross-edges between {m_a} ({size_a} coords) "
                            f"and {m_b} ({size_b} coords)."
                        ),
                    )
                )

        return decisions


# ---------------------------------------------------------------------------
# ArchitectureEnforcer — boundary verification
# ---------------------------------------------------------------------------


class ArchitectureEnforcer:
    """Verify code respects declared architectural boundaries.

    Compares actual dependencies against an ArchitecturalManifest
    to detect boundary violations.
    """

    @staticmethod
    def load_manifest(manifest_path: str) -> ArchitecturalManifest:
        """Load an architectural manifest from a JSON file."""
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ArchitecturalManifest.from_dict(data)

    @staticmethod
    def check_boundaries(
        manifest: ArchitecturalManifest,
        coordinates: list[str],
        morphisms: list[tuple[str, str]],
    ) -> list[BoundaryViolation]:
        """Check all boundaries in a manifest against actual code.

        Returns list of violations found.
        """
        violations: list[BoundaryViolation] = []
        for boundary in manifest.declared_covers:
            violations.extend(
                ArchitectureEnforcer._check_import_rules(
                    boundary, coordinates, morphisms
                )
            )
        violations.extend(
            ArchitectureEnforcer._check_interface_contracts(
                manifest, coordinates, morphisms
            )
        )
        return violations

    @staticmethod
    def _match_pattern(coordinate: str, pattern: str) -> bool:
        """Glob-style pattern matching for coordinate ids.

        Supports '*' and '?' wildcards via fnmatch.
        """
        return fnmatch.fnmatch(coordinate, pattern)

    @staticmethod
    def _check_import_rules(
        boundary: DeclaredBoundary,
        coordinates: list[str],
        morphisms: list[tuple[str, str]],
    ) -> list[BoundaryViolation]:
        """Check that imports from boundary coordinates obey rules.

        A violation occurs when a coordinate in this boundary imports
        from a target that matches a disallowed pattern, or imports
        from a target that doesn't match any allowed pattern (if
        allowed patterns are specified).
        """
        violations: list[BoundaryViolation] = []

        # Find coordinates belonging to this boundary
        boundary_coords: set[str] = set()
        for coord in coordinates:
            for pattern in boundary.coordinate_patterns:
                if ArchitectureEnforcer._match_pattern(coord, pattern):
                    boundary_coords.add(coord)
                    break

        # Check each morphism from a boundary coordinate
        for src, tgt in morphisms:
            if src not in boundary_coords:
                continue
            # Skip internal morphisms (target also in boundary)
            if tgt in boundary_coords:
                continue

            # Check disallowed imports
            for pattern in boundary.disallowed_imports:
                if ArchitectureEnforcer._match_pattern(tgt, pattern):
                    violations.append(
                        BoundaryViolation(
                            boundary_name=boundary.name,
                            violating_coordinate=src,
                            violation_kind="UNDECLARED_IMPORT",
                            details=(
                                f"Import from '{src}' to '{tgt}' "
                                f"matches disallowed pattern '{pattern}'"
                            ),
                            severity="error",
                        )
                    )

            # Check allowed imports (if specified, target must match at least one)
            if boundary.allowed_imports:
                allowed = any(
                    ArchitectureEnforcer._match_pattern(tgt, p)
                    for p in boundary.allowed_imports
                )
                if not allowed:
                    violations.append(
                        BoundaryViolation(
                            boundary_name=boundary.name,
                            violating_coordinate=src,
                            violation_kind="UNDECLARED_IMPORT",
                            details=(
                                f"Import from '{src}' to '{tgt}' "
                                f"not in allowed list: {boundary.allowed_imports}"
                            ),
                            severity="warning",
                        )
                    )

        return violations

    @staticmethod
    def _check_interface_contracts(
        manifest: ArchitecturalManifest,
        coordinates: list[str],
        morphisms: list[tuple[str, str]],
    ) -> list[BoundaryViolation]:
        """Check that interface contracts are respected.

        Interface contracts are coordinate patterns that must exist.
        If a contract specifies a pattern and no coordinate matches,
        it's a violation.
        """
        violations: list[BoundaryViolation] = []
        for contract in manifest.interface_contracts:
            if not any(
                ArchitectureEnforcer._match_pattern(c, contract) for c in coordinates
            ):
                violations.append(
                    BoundaryViolation(
                        boundary_name="__manifest__",
                        violating_coordinate=contract,
                        violation_kind="INTERFACE_VIOLATION",
                        details=f"Interface contract '{contract}' has no matching coordinate",
                        severity="error",
                    )
                )
        return violations

    @staticmethod
    def _check_trust_requirements(
        boundary: DeclaredBoundary,
        coordinates: list[str],
        judgments: dict[str, str],
    ) -> list[BoundaryViolation]:
        """Check trust levels meet boundary requirements.

        Parameters
        ----------
        judgments : dict mapping coordinate_id -> trust_level
            Trust levels: "high", "normal", "low", "untrusted"
        """
        trust_order = {"untrusted": 0, "low": 1, "normal": 2, "high": 3}
        required_level = trust_order.get(boundary.trust_requirement, 2)

        violations: list[BoundaryViolation] = []
        for coord in coordinates:
            for pattern in boundary.coordinate_patterns:
                if ArchitectureEnforcer._match_pattern(coord, pattern):
                    actual_level = trust_order.get(
                        judgments.get(coord, "normal"), 2
                    )
                    if actual_level < required_level:
                        violations.append(
                            BoundaryViolation(
                                boundary_name=boundary.name,
                                violating_coordinate=coord,
                                violation_kind="TRUST_INSUFFICIENT",
                                details=(
                                    f"Coordinate '{coord}' has trust level "
                                    f"'{judgments.get(coord, 'normal')}' but "
                                    f"boundary requires '{boundary.trust_requirement}'"
                                ),
                                severity="error",
                            )
                        )
                    break

        return violations


# ---------------------------------------------------------------------------
# ArchitectureTracker — evolution tracking
# ---------------------------------------------------------------------------


class ArchitectureTracker:
    """Track architectural evolution over time.

    Takes snapshots of architectural quality and computes drift
    between snapshots to detect degradation.
    """

    def __init__(self) -> None:
        self._snapshots: list[ArchitecturalSnapshot] = []

    def take_snapshot(
        self,
        cover_members: list[CoverMember],
        morphisms: list[tuple[str, str]],
        violations: list[BoundaryViolation] | None = None,
    ) -> ArchitecturalSnapshot:
        """Take a snapshot of current architectural quality."""
        quality = CoverAnalyzer.full_quality_analysis(cover_members, morphisms)

        # Compute overlaps
        widths = CoverAnalyzer.compute_interface_widths(cover_members)

        snapshot = ArchitecturalSnapshot(
            id=f"snap_{uuid.uuid4().hex[:8]}",
            cover_quality=quality,
            member_count=len(cover_members),
            overlap_count=len(widths),
            violation_count=len(violations) if violations else 0,
            drift_from_manifest=0.0,
        )
        self._snapshots.append(snapshot)
        return snapshot

    def compute_drift(
        self, baseline_id: str, current_id: str
    ) -> ArchitecturalDrift:
        """Compute drift between two snapshots."""
        baseline: ArchitecturalSnapshot | None = None
        current: ArchitecturalSnapshot | None = None

        for snap in self._snapshots:
            if snap.id == baseline_id:
                baseline = snap
            if snap.id == current_id:
                current = snap

        if baseline is None or current is None:
            return ArchitecturalDrift(
                baseline_snapshot_id=baseline_id,
                current_snapshot_id=current_id,
            )

        bq = baseline.cover_quality
        cq = current.cover_quality

        coupling_delta = 0.0
        cohesion_delta = 0.0
        if bq and cq:
            coupling_delta = cq.coupling_score - bq.coupling_score
            cohesion_delta = cq.cohesion_score - bq.cohesion_score

        drift_score = abs(coupling_delta) + abs(cohesion_delta)
        needs_attention = (
            coupling_delta > 0.1 or cohesion_delta < -0.1 or drift_score > 0.2
        )

        return ArchitecturalDrift(
            baseline_snapshot_id=baseline_id,
            current_snapshot_id=current_id,
            coupling_delta=coupling_delta,
            cohesion_delta=cohesion_delta,
            drift_score=drift_score,
            needs_attention=needs_attention,
        )

    def trend_analysis(
        self, snapshots: list[ArchitecturalSnapshot] | None = None
    ) -> dict[str, list[float]]:
        """Return metric trends over time.

        Returns dict of metric_name -> list of values.
        """
        snaps = snapshots if snapshots is not None else self._snapshots
        trends: dict[str, list[float]] = {
            "coupling": [],
            "cohesion": [],
            "violations": [],
            "members": [],
            "overlaps": [],
        }
        for snap in snaps:
            if snap.cover_quality:
                trends["coupling"].append(snap.cover_quality.coupling_score)
                trends["cohesion"].append(snap.cover_quality.cohesion_score)
            else:
                trends["coupling"].append(0.0)
                trends["cohesion"].append(0.0)
            trends["violations"].append(float(snap.violation_count))
            trends["members"].append(float(snap.member_count))
            trends["overlaps"].append(float(snap.overlap_count))
        return trends

    def alert_on_degradation(
        self,
        drift: ArchitecturalDrift,
        thresholds: dict[str, float] | None = None,
    ) -> list[str]:
        """Generate alert strings when metrics degrade.

        Default thresholds:
        - coupling_delta > 0.1
        - cohesion_delta < -0.1
        - drift_score > 0.2
        """
        if thresholds is None:
            thresholds = {
                "coupling_delta": 0.1,
                "cohesion_delta": -0.1,
                "drift_score": 0.2,
            }

        alerts: list[str] = []
        coupling_thresh = thresholds.get("coupling_delta", 0.1)
        cohesion_thresh = thresholds.get("cohesion_delta", -0.1)
        drift_thresh = thresholds.get("drift_score", 0.2)

        if drift.coupling_delta > coupling_thresh:
            alerts.append(
                f"ALERT: Coupling increased by {drift.coupling_delta:.3f} "
                f"(threshold: {coupling_thresh:.3f})"
            )
        if drift.cohesion_delta < cohesion_thresh:
            alerts.append(
                f"ALERT: Cohesion decreased by {abs(drift.cohesion_delta):.3f} "
                f"(threshold: {abs(cohesion_thresh):.3f})"
            )
        if drift.drift_score > drift_thresh:
            alerts.append(
                f"ALERT: Drift score {drift.drift_score:.3f} exceeds "
                f"threshold {drift_thresh:.3f}"
            )
        if drift.new_violations:
            alerts.append(
                f"ALERT: {len(drift.new_violations)} new boundary violation(s)"
            )

        return alerts

    def history(self) -> list[ArchitecturalSnapshot]:
        """Return all tracked snapshots."""
        return list(self._snapshots)
