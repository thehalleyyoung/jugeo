"""HierarchicalSite — multi-level site decomposition.

Replaces flat coordinate/morphism/cover dictionaries with a hierarchy that
mirrors a project's package → module → class → function → branch structure.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from jugeo.scaling.hierarchical.levels import LevelHeuristic
from jugeo.scaling.hierarchical.models import (
    HierarchicalCoordinate,
    HierarchicalCover,
    HierarchicalCoverMember,
    LevelView,
    SiteLevel,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# HierarchicalSite
# ---------------------------------------------------------------------------


class HierarchicalSite:
    """A multi-level site whose coordinates mirror a software project tree.

    Coordinates are stored in a dict keyed by their ``id``.  The tree
    structure is maintained through ``parent_id`` / ``children_ids`` links.
    Morphisms and covers are stored in flat lists with level-indexed look-ups
    maintained lazily.

    All mutation methods return ``self`` to allow method chaining.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, project_name: str) -> None:
        self.project_name: str = project_name

        # Primary stores
        self._coordinates: dict[str, HierarchicalCoordinate] = {}
        self._morphisms: list[dict[str, Any]] = []
        self._covers: dict[str, HierarchicalCover] = {}

        # Indexes rebuilt lazily or maintained incrementally
        self._level_index: dict[SiteLevel, list[str]] = {lvl: [] for lvl in SiteLevel}
        self._package_index: dict[str, list[str]] = {}

        # Root coordinate representing the project itself
        root_id = f"project:{project_name}"
        root = HierarchicalCoordinate.create(
            root_id,
            project_name,
            SiteLevel.PROJECT,
            package=project_name,
            depth=0,
        )
        self._coordinates[root_id] = root
        self._level_index[SiteLevel.PROJECT].append(root_id)
        self._root_id: str = root_id

    # ------------------------------------------------------------------
    # Coordinate management
    # ------------------------------------------------------------------

    def add_coordinate(
        self,
        coord_id: str,
        name: str,
        level: SiteLevel,
        parent_id: Optional[str] = None,
        package: str = "",
        module: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> HierarchicalSite:
        """Add a coordinate at the given level.

        If ``parent_id`` is provided and exists, the parent's
        ``children_ids`` list is updated.  The ``depth`` is inferred from
        the parent's depth + 1 when a parent is given.
        """
        depth = 0
        if parent_id and parent_id in self._coordinates:
            depth = self._coordinates[parent_id].depth + 1

        coord = HierarchicalCoordinate.create(
            coord_id,
            name,
            level,
            parent_id=parent_id,
            package=package,
            module=module,
            depth=depth,
            metadata=metadata,
        )
        self._coordinates[coord_id] = coord
        self._level_index[level].append(coord_id)

        # Update parent's children list
        if parent_id and parent_id in self._coordinates:
            parent = self._coordinates[parent_id]
            if coord_id not in parent.children_ids:
                parent.children_ids.append(coord_id)

        # Update package index
        if package:
            if package not in self._package_index:
                self._package_index[package] = []
            if coord_id not in self._package_index[package]:
                self._package_index[package].append(coord_id)

        return self

    # ------------------------------------------------------------------
    # Morphism management
    # ------------------------------------------------------------------

    def add_morphism(
        self,
        source_id: str,
        target_id: str,
        kind: str,
        label: str = "",
    ) -> HierarchicalSite:
        """Add a directed morphism between two coordinates.

        The morphism dict records the source/target levels so that
        cross-level queries don't need to look up each coordinate.
        """
        source_level: Optional[SiteLevel] = None
        target_level: Optional[SiteLevel] = None
        if source_id in self._coordinates:
            source_level = self._coordinates[source_id].level
        if target_id in self._coordinates:
            target_level = self._coordinates[target_id].level

        morph = {
            "id": _new_id(),
            "source_id": source_id,
            "target_id": target_id,
            "kind": kind,
            "label": label,
            "source_level": source_level.to_dict() if source_level else None,
            "target_level": target_level.to_dict() if target_level else None,
        }
        self._morphisms.append(morph)
        return self

    # ------------------------------------------------------------------
    # Cover management
    # ------------------------------------------------------------------

    def add_cover(
        self,
        cover_id: str,
        level: SiteLevel,
        member_defs: list[dict[str, Any]],
    ) -> HierarchicalSite:
        """Add a cover at the given level.

        ``member_defs`` is a list of dicts with keys:
        ``id``, ``name``, ``coordinate_ids``,
        optionally ``internal_morphism_count``, ``external_morphism_count``.
        """
        members = []
        for md in member_defs:
            member = HierarchicalCoverMember.create(
                member_id=md.get("id", _new_id()),
                name=md.get("name", ""),
                level=level,
                coordinate_ids=md.get("coordinate_ids", []),
                internal_morphism_count=md.get("internal_morphism_count", 0),
                external_morphism_count=md.get("external_morphism_count", 0),
            )
            members.append(member)

        cover = HierarchicalCover.create(
            cover_id=cover_id,
            level=level,
            members=members,
        )
        self._covers[cover_id] = cover
        return self

    # ------------------------------------------------------------------
    # Queries — level views
    # ------------------------------------------------------------------

    def get_level_view(self, level: SiteLevel) -> LevelView:
        """Return a snapshot of all coordinates, morphisms, and covers at level."""
        coord_ids = list(self._level_index.get(level, []))
        coord_set = set(coord_ids)

        morphisms = [
            m
            for m in self._morphisms
            if (
                m.get("source_level") == level.to_dict()
                and m.get("target_level") == level.to_dict()
            )
        ]

        covers = [
            c.to_dict()
            for c in self._covers.values()
            if c.level == level
        ]

        return LevelView.build(level, coord_ids, morphisms, covers)

    # ------------------------------------------------------------------
    # Queries — tree navigation
    # ------------------------------------------------------------------

    def get_subtree(self, coord_id: str) -> list[str]:
        """Return all descendants of a coordinate (BFS), excluding itself."""
        if coord_id not in self._coordinates:
            return []

        result: list[str] = []
        queue = list(self._coordinates[coord_id].children_ids)
        while queue:
            cid = queue.pop(0)
            result.append(cid)
            if cid in self._coordinates:
                queue.extend(self._coordinates[cid].children_ids)
        return result

    def get_ancestors(self, coord_id: str) -> list[str]:
        """Return all ancestors of a coordinate up to the root (excluding self)."""
        if coord_id not in self._coordinates:
            return []

        result: list[str] = []
        current_id: Optional[str] = self._coordinates[coord_id].parent_id
        while current_id is not None:
            result.append(current_id)
            if current_id in self._coordinates:
                current_id = self._coordinates[current_id].parent_id
            else:
                break
        return result

    def get_coordinate(self, coord_id: str) -> Optional[HierarchicalCoordinate]:
        """Return the coordinate with the given id, or None."""
        return self._coordinates.get(coord_id)

    # ------------------------------------------------------------------
    # Queries — level-filtered lists
    # ------------------------------------------------------------------

    def coordinates_at_level(self, level: SiteLevel) -> list[HierarchicalCoordinate]:
        """Return all coordinates at a given level."""
        return [
            self._coordinates[cid]
            for cid in self._level_index.get(level, [])
            if cid in self._coordinates
        ]

    def morphisms_at_level(self, level: SiteLevel) -> list[dict[str, Any]]:
        """Return morphisms where both source and target are at this level."""
        level_str = level.to_dict()
        return [
            m
            for m in self._morphisms
            if m.get("source_level") == level_str and m.get("target_level") == level_str
        ]

    def morphisms_across_levels(
        self, level_a: SiteLevel, level_b: SiteLevel
    ) -> list[dict[str, Any]]:
        """Return morphisms where source is at level_a and target is at level_b."""
        a_str = level_a.to_dict()
        b_str = level_b.to_dict()
        return [
            m
            for m in self._morphisms
            if m.get("source_level") == a_str and m.get("target_level") == b_str
        ]

    def covers_at_level(self, level: SiteLevel) -> list[HierarchicalCover]:
        """Return all covers at a given level."""
        return [c for c in self._covers.values() if c.level == level]

    # ------------------------------------------------------------------
    # Queries — statistics
    # ------------------------------------------------------------------

    def coordinate_count(self) -> int:
        return len(self._coordinates)

    def morphism_count(self) -> int:
        return len(self._morphisms)

    def level_statistics(self) -> dict[SiteLevel, dict[str, Any]]:
        """Return per-level summary statistics."""
        stats: dict[SiteLevel, dict[str, Any]] = {}
        for level in SiteLevel:
            coord_ids = self._level_index.get(level, [])
            morphs = self.morphisms_at_level(level)
            covers = self.covers_at_level(level)
            stats[level] = {
                "coordinate_count": len(coord_ids),
                "morphism_count": len(morphs),
                "cover_count": len(covers),
                "level": level.to_dict(),
            }
        return stats

    # ------------------------------------------------------------------
    # Restriction (sub-site extraction)
    # ------------------------------------------------------------------

    def restrict_to_package(self, package_name: str) -> HierarchicalSite:
        """Build a sub-site containing only coordinates in a given package."""
        sub = HierarchicalSite(f"{self.project_name}[{package_name}]")
        pkg_ids = set(self._package_index.get(package_name, []))

        for cid in pkg_ids:
            if cid in self._coordinates:
                c = self._coordinates[cid]
                # Re-insert without the root which the sub-site creates itself
                if c.level != SiteLevel.PROJECT:
                    sub.add_coordinate(
                        c.id,
                        c.name,
                        c.level,
                        parent_id=c.parent_id if c.parent_id in pkg_ids else None,
                        package=c.package,
                        module=c.module,
                        metadata=dict(c.metadata),
                    )

        # Copy morphisms whose both endpoints are in the sub-site
        sub_coord_ids = set(sub._coordinates.keys())
        for m in self._morphisms:
            if m["source_id"] in sub_coord_ids and m["target_id"] in sub_coord_ids:
                sub.add_morphism(m["source_id"], m["target_id"], m["kind"], m.get("label", ""))

        # Copy covers for that package
        for cover in self._covers.values():
            member_defs = []
            for member in cover.members:
                restricted_ids = [cid for cid in member.coordinate_ids if cid in sub_coord_ids]
                if restricted_ids:
                    member_defs.append(
                        {
                            "id": member.id,
                            "name": member.name,
                            "coordinate_ids": restricted_ids,
                            "internal_morphism_count": member.internal_morphism_count,
                            "external_morphism_count": member.external_morphism_count,
                        }
                    )
            if member_defs:
                sub.add_cover(cover.id, cover.level, member_defs)

        return sub

    def restrict_to_level_range(
        self, min_level: SiteLevel, max_level: SiteLevel
    ) -> HierarchicalSite:
        """Build a sub-site containing only coordinates within a level range.

        Both ``min_level`` and ``max_level`` are inclusive.  ``min_level``
        must be coarser than or equal to ``max_level`` numerically (i.e.
        min_level.value <= max_level.value).
        """
        sub = HierarchicalSite(f"{self.project_name}[{min_level.label()}:{max_level.label()}]")
        allowed = set(range(min_level.value, max_level.value + 1))

        # Remove the auto-created PROJECT root when PROJECT is not in range
        if SiteLevel.PROJECT.value not in allowed:
            auto_root = sub._root_id
            if auto_root in sub._coordinates:
                del sub._coordinates[auto_root]
                lvl_list = sub._level_index[SiteLevel.PROJECT]
                if auto_root in lvl_list:
                    lvl_list.remove(auto_root)

        allowed_ids: set[str] = set()

        for lvl_val in allowed:
            level = SiteLevel(lvl_val)
            for cid in self._level_index.get(level, []):
                if cid in self._coordinates:
                    c = self._coordinates[cid]
                    # Skip the automatically created project root for the sub-site
                    if c.id == self._root_id and level == SiteLevel.PROJECT:
                        continue
                    allowed_ids.add(cid)
                    sub.add_coordinate(
                        c.id,
                        c.name,
                        c.level,
                        parent_id=c.parent_id if c.parent_id in allowed_ids else None,
                        package=c.package,
                        module=c.module,
                        metadata=dict(c.metadata),
                    )

        for m in self._morphisms:
            if m["source_id"] in allowed_ids and m["target_id"] in allowed_ids:
                sub.add_morphism(m["source_id"], m["target_id"], m["kind"], m.get("label", ""))

        for cover in self._covers.values():
            if cover.level.value in allowed:
                member_defs = []
                for member in cover.members:
                    restricted_ids = [
                        cid for cid in member.coordinate_ids if cid in allowed_ids
                    ]
                    if restricted_ids:
                        member_defs.append(
                            {
                                "id": member.id,
                                "name": member.name,
                                "coordinate_ids": restricted_ids,
                                "internal_morphism_count": member.internal_morphism_count,
                                "external_morphism_count": member.external_morphism_count,
                            }
                        )
                if member_defs:
                    sub.add_cover(cover.id, cover.level, member_defs)

        return sub

    # ------------------------------------------------------------------
    # Level inference
    # ------------------------------------------------------------------

    def _infer_level(self, name: str) -> SiteLevel:
        """Heuristic level inference from a coordinate name."""
        return LevelHeuristic.infer_level_from_name(name)

    # ------------------------------------------------------------------
    # Class-method constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_flat_coordinates(
        cls,
        coordinates: list[dict[str, Any]],
        morphisms: list[dict[str, Any]],
    ) -> HierarchicalSite:
        """Build a HierarchicalSite by parsing dotted names in flat lists.

        Each coordinate dict must have ``id`` and ``name``.  The level is
        inferred from the dotted name depth.  Parent links are inferred by
        looking for a coordinate whose name is the prefix of this one.

        Example::

            coords = [
                {"id": "pkg", "name": "mypkg"},
                {"id": "mod", "name": "mypkg.mymod"},
                {"id": "fn",  "name": "mypkg.mymod.do_thing"},
            ]
        """
        # Determine project name from common prefix or first coord
        project_name = "project"
        if coordinates:
            # Try to find the shallowest part
            first_parts = coordinates[0]["name"].split(".")
            project_name = first_parts[0] if first_parts else "project"

        site = cls(project_name)

        # Index by full name for parent look-up
        name_to_id: dict[str, str] = {}
        for raw in coordinates:
            name_to_id[raw["name"]] = raw["id"]

        # Sort by number of parts so parents are always processed first
        sorted_coords = sorted(coordinates, key=lambda c: len(c["name"].split(".")))

        for raw in sorted_coords:
            name: str = raw["name"]
            coord_id: str = raw["id"]
            level = LevelHeuristic.infer_level_from_name(name)

            # Infer parent: longest prefix that exists in name_to_id
            parent_id: Optional[str] = None
            parts = name.split(".")
            for i in range(len(parts) - 1, 0, -1):
                prefix = ".".join(parts[:i])
                if prefix in name_to_id:
                    parent_id = name_to_id[prefix]
                    break
            if parent_id is None and name != project_name:
                parent_id = site._root_id

            # Infer package / module from name parts
            package = parts[0] if len(parts) >= 1 else ""
            module = ".".join(parts[:2]) if len(parts) >= 2 else ""

            site.add_coordinate(
                coord_id,
                name,
                level,
                parent_id=parent_id,
                package=package,
                module=module,
                metadata=raw.get("metadata", {}),
            )

        for m in morphisms:
            site.add_morphism(
                m["source_id"],
                m["target_id"],
                m.get("kind", "dependency"),
                m.get("label", ""),
            )

        return site

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize the entire site to a JSON-compatible dict."""
        return {
            "project_name": self.project_name,
            "root_id": self._root_id,
            "coordinates": [c.to_dict() for c in self._coordinates.values()],
            "morphisms": [dict(m) for m in self._morphisms],
            "covers": [c.to_dict() for c in self._covers.values()],
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> HierarchicalSite:
        """Deserialize a site from the dict produced by ``serialize()``."""
        site = cls.__new__(cls)
        site.project_name = data["project_name"]
        site._root_id = data.get("root_id", f"project:{data['project_name']}")
        site._coordinates = {}
        site._morphisms = []
        site._covers = {}
        site._level_index = {lvl: [] for lvl in SiteLevel}
        site._package_index = {}

        for raw in data.get("coordinates", []):
            coord = HierarchicalCoordinate.from_dict(raw)
            site._coordinates[coord.id] = coord
            site._level_index[coord.level].append(coord.id)
            if coord.package:
                if coord.package not in site._package_index:
                    site._package_index[coord.package] = []
                site._package_index[coord.package].append(coord.id)

        site._morphisms = [dict(m) for m in data.get("morphisms", [])]

        for raw in data.get("covers", []):
            cover = HierarchicalCover.from_dict(raw)
            site._covers[cover.id] = cover

        return site

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"HierarchicalSite(project={self.project_name!r}, "
            f"coords={self.coordinate_count()}, morphisms={self.morphism_count()})"
        )

    def __len__(self) -> int:
        return self.coordinate_count()


__all__ = ["HierarchicalSite"]
