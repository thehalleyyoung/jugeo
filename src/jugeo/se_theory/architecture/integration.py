"""Integration with JuGeo site geometry and import graph analysis.

This module bridges the architecture analysis algorithms with the
site/geometry layer.  It works via duck-typing against site objects
rather than hard imports, allowing the architecture module to remain
standalone while still integrating with the full JuGeo stack.

Key integrations:
- SiteArchitectureAnalyzer: Analyze architecture from a Site object
- ImportGraphArchitecture: Analyze architecture from import edges
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from jugeo.se_theory.architecture.algorithms import (
    ArchitectureEnforcer,
    CoverAnalyzer,
    CoverSuggester,
    TarjanSCC,
)
from jugeo.se_theory.architecture.models import (
    ArchitecturalDecision,
    ArchitecturalDecisionKind,
    ArchitecturalManifest,
    BoundaryViolation,
    CoverMember,
    CoverMemberKind,
    CoverQualityMetrics,
)


# ---------------------------------------------------------------------------
# SiteArchitectureAnalyzer
# ---------------------------------------------------------------------------


class SiteArchitectureAnalyzer:
    """Analyze architecture from a JuGeo Site object (duck-typed).

    Expects the site object to have:
    - site.coordinates: iterable of objects with .id or .components
    - site.morphisms: iterable of objects with .source and .target
      (where source/target have .id or are strings)

    This class does NOT import from jugeo.geometry.site directly.
    """

    def __init__(self) -> None:
        pass

    def analyze_site(self, site: Any) -> CoverQualityMetrics:
        """Extract coordinates and morphisms from site, run full analysis."""
        coord_ids = self._extract_coordinate_ids(site)
        morphisms = self._extract_morphisms(site)
        cover_members = self.coordinates_to_cover_members(site)
        return CoverAnalyzer.full_quality_analysis(cover_members, morphisms)

    def suggest_covers_for_site(self, site: Any) -> list[CoverMember]:
        """Run CoverSuggester on site's coordinates."""
        coord_ids = self._extract_coordinate_ids(site)
        morphisms = self._extract_morphisms(site)
        return CoverSuggester.suggest_cover(coord_ids, morphisms)

    def check_site_architecture(
        self,
        site: Any,
        manifest: ArchitecturalManifest,
    ) -> list[BoundaryViolation]:
        """Check site against architectural manifest."""
        coord_ids = self._extract_coordinate_ids(site)
        morphisms = self._extract_morphisms(site)
        return ArchitectureEnforcer.check_boundaries(manifest, coord_ids, morphisms)

    def site_to_adjacency(self, site: Any) -> dict[str, list[str]]:
        """Convert site's morphisms to adjacency list.

        Uses duck-typing: morphisms have .source and .target attributes
        which are either Coordinate objects (with .id) or strings.
        """
        adjacency: dict[str, list[str]] = defaultdict(list)
        morphisms = self._extract_morphisms(site)
        for src, tgt in morphisms:
            adjacency[src].append(tgt)
        return dict(adjacency)

    def coordinates_to_cover_members(self, site: Any) -> list[CoverMember]:
        """Group site's coordinates by package prefix.

        Uses the first dot-separated segment of the coordinate id as
        the group key.  E.g., "jugeo.geometry.site.Coordinate" goes
        into the "jugeo" group.
        """
        coord_ids = self._extract_coordinate_ids(site)
        morphisms = self._extract_morphisms(site)

        # Group by first path segment
        groups: dict[str, list[str]] = defaultdict(list)
        for cid in coord_ids:
            parts = cid.split(".")
            group_key = parts[0] if parts else cid
            groups[group_key].append(cid)

        # Build morphism sets for internal/external classification
        morphism_set = set(morphisms)
        coord_to_group: dict[str, str] = {}
        for group_key, coords in groups.items():
            for c in coords:
                coord_to_group[c] = group_key

        members: list[CoverMember] = []
        for group_key, coords in sorted(groups.items()):
            internal: list[str] = []
            external: list[str] = []

            for src, tgt in morphisms:
                if src in coords or tgt in coords:
                    src_group = coord_to_group.get(src, "")
                    tgt_group = coord_to_group.get(tgt, "")
                    morph_str = f"{src}->{tgt}"
                    if src_group == group_key and tgt_group == group_key:
                        internal.append(morph_str)
                    else:
                        external.append(morph_str)

            members.append(
                CoverMember(
                    id=group_key,
                    name=group_key,
                    kind=CoverMemberKind.PACKAGE,
                    coordinates=sorted(coords),
                    internal_morphisms=internal,
                    external_morphisms=external,
                )
            )

        return members

    def _extract_coordinate_ids(self, site: Any) -> list[str]:
        """Extract coordinate ids from site.

        Handles multiple possible site shapes via duck-typing:
        - site.coordinates as a list of objects with .id
        - site.coordinates as a list of objects with .components
        - site.coordinates as a list of strings
        - site as a dict with 'coordinates' key
        """
        coords_attr = getattr(site, "coordinates", None)
        if coords_attr is None and isinstance(site, dict):
            coords_attr = site.get("coordinates", [])

        if coords_attr is None:
            return []

        result: list[str] = []
        for c in coords_attr:
            if hasattr(c, "id"):
                result.append(c.id)
            elif hasattr(c, "components"):
                result.append(".".join(str(x) for x in c.components))
            elif isinstance(c, str):
                result.append(c)
            else:
                result.append(str(c))
        return result

    def _extract_morphisms(self, site: Any) -> list[tuple[str, str]]:
        """Extract (source_id, target_id) tuples from site.morphisms.

        Handles:
        - site.morphisms as list of objects with .source/.target (Coordinate-like)
        - site.morphisms as list of tuples
        - site as a dict with 'morphisms' key
        """
        morphisms_attr = getattr(site, "morphisms", None)
        if morphisms_attr is None and isinstance(site, dict):
            morphisms_attr = site.get("morphisms", [])

        if morphisms_attr is None:
            return []

        result: list[tuple[str, str]] = []
        for m in morphisms_attr:
            if hasattr(m, "source") and hasattr(m, "target"):
                src = self._resolve_coord_id(m.source)
                tgt = self._resolve_coord_id(m.target)
                result.append((src, tgt))
            elif isinstance(m, (tuple, list)) and len(m) >= 2:
                result.append((str(m[0]), str(m[1])))
            else:
                continue
        return result

    @staticmethod
    def _resolve_coord_id(coord: Any) -> str:
        """Resolve a coordinate to its string id."""
        if isinstance(coord, str):
            return coord
        if hasattr(coord, "id"):
            return coord.id
        if hasattr(coord, "components"):
            return ".".join(str(x) for x in coord.components)
        return str(coord)


# ---------------------------------------------------------------------------
# ImportGraphArchitecture
# ---------------------------------------------------------------------------


class ImportGraphArchitecture:
    """Analyze architecture from Python import graph edges.

    Works with import edges as (importer_module, imported_module) tuples.
    Groups modules by top-level package and detects cycles.
    """

    @staticmethod
    def from_import_edges(
        edges: list[tuple[str, str]],
    ) -> list[CoverMember]:
        """Group modules by top-level package, return CoverMember per package.

        For edge ("pkg_a.mod1", "pkg_b.mod2"), pkg_a and pkg_b become
        separate cover members.
        """
        # Collect all modules
        all_modules: set[str] = set()
        for src, tgt in edges:
            all_modules.add(src)
            all_modules.add(tgt)

        # Group by top-level package
        groups: dict[str, set[str]] = defaultdict(set)
        for mod in all_modules:
            parts = mod.split(".")
            pkg = parts[0] if parts else mod
            groups[pkg].add(mod)

        # Build members
        module_to_pkg: dict[str, str] = {}
        for pkg, modules in groups.items():
            for m in modules:
                module_to_pkg[m] = pkg

        members: list[CoverMember] = []
        for pkg, modules in sorted(groups.items()):
            internal: list[str] = []
            external: list[str] = []
            for src, tgt in edges:
                if src in modules or tgt in modules:
                    morph_str = f"{src}->{tgt}"
                    if module_to_pkg.get(src) == pkg and module_to_pkg.get(tgt) == pkg:
                        internal.append(morph_str)
                    else:
                        external.append(morph_str)

            members.append(
                CoverMember(
                    id=pkg,
                    name=pkg,
                    kind=CoverMemberKind.PACKAGE,
                    coordinates=sorted(modules),
                    internal_morphisms=internal,
                    external_morphisms=external,
                )
            )

        return members

    @staticmethod
    def detect_cycles(
        edges: list[tuple[str, str]],
    ) -> list[list[str]]:
        """Detect import cycles using Tarjan's SCC algorithm."""
        adjacency: dict[str, list[str]] = defaultdict(list)
        for src, tgt in edges:
            adjacency[src].append(tgt)
        return TarjanSCC.find_nontrivial_sccs(dict(adjacency))

    @staticmethod
    def suggest_cycle_breaks(
        cycles: list[list[str]],
        edges: list[tuple[str, str]],
    ) -> list[ArchitecturalDecision]:
        """Suggest breaking import cycles.

        For each cycle, suggest extracting a common interface to
        break the dependency.  Uses a heuristic: suggest removing
        the edge where the source has the most outgoing edges
        (likely a "hub" that should be split).
        """
        if not cycles:
            return []

        # Build adjacency for analysis
        out_degree: dict[str, int] = defaultdict(int)
        for src, _ in edges:
            out_degree[src] += 1

        # Map edges to cycle membership
        edge_cycle_count: dict[tuple[str, str], int] = defaultdict(int)
        for cycle in cycles:
            cycle_set = set(cycle)
            for src, tgt in edges:
                if src in cycle_set and tgt in cycle_set:
                    edge_cycle_count[(src, tgt)] += 1

        decisions: list[ArchitecturalDecision] = []
        for i, cycle in enumerate(cycles):
            cycle_set = set(cycle)
            # Find the edge to break: pick the one from the highest-degree node
            cycle_edges = [
                (src, tgt) for src, tgt in edges
                if src in cycle_set and tgt in cycle_set
            ]
            if not cycle_edges:
                continue

            # Choose edge from node with highest out-degree
            best_edge = max(cycle_edges, key=lambda e: out_degree[e[0]])

            decisions.append(
                ArchitecturalDecision(
                    id=f"break_cycle_{i}_{uuid.uuid4().hex[:6]}",
                    kind=ArchitecturalDecisionKind.RESOLVE_CIRCULAR,
                    target_members=sorted(cycle),
                    description=(
                        f"Break cycle {sorted(cycle)[:3]}... by extracting "
                        f"interface from '{best_edge[0]}' -> '{best_edge[1]}'"
                    ),
                    expected_coupling_change=-0.15,
                    expected_cohesion_change=0.05,
                    blast_radius=len(cycle),
                    confidence=0.5,
                    rationale=(
                        f"Circular dependency among {len(cycle)} modules. "
                        f"Suggest extracting shared interface to break "
                        f"edge {best_edge[0]} -> {best_edge[1]} "
                        f"(out-degree {out_degree[best_edge[0]]})."
                    ),
                )
            )

        return decisions
