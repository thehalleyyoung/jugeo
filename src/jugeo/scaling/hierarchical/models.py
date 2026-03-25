"""Data models for the Hierarchical Site module.

Defines the core types used by HierarchicalSite, HierarchicalDescent,
GeometricPartitioner, and related subsystems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# SiteLevel
# ---------------------------------------------------------------------------


class SiteLevel(int, Enum):
    """Hierarchy levels mirroring Python project structure.

    Values are ordered from coarsest (PROJECT) to finest (EXPRESSION) so
    that numeric comparison gives the expected ordering.
    """

    PROJECT = 0
    PACKAGE = 1
    MODULE = 2
    CLASS = 3
    FUNCTION = 4
    BRANCH = 5
    EXPRESSION = 6

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_coarser_than(self, other: SiteLevel) -> bool:
        """Return True if self is higher (coarser) than other."""
        return self.value < other.value

    def is_finer_than(self, other: SiteLevel) -> bool:
        """Return True if self is lower (finer) than other."""
        return self.value > other.value

    def label(self) -> str:
        """Human-readable label."""
        return self.name.lower()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> str:
        return self.name

    @classmethod
    def from_dict(cls, value: str | int) -> SiteLevel:
        if isinstance(value, int):
            return cls(value)
        return cls[value.upper()]


# ---------------------------------------------------------------------------
# HierarchicalCoordinate
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HierarchicalCoordinate:
    """A single coordinate in the hierarchical site.

    Coordinates form a tree: every non-root coordinate has a parent, and the
    parent's level is always strictly coarser than the child's level.
    """

    id: str
    name: str
    level: SiteLevel
    parent_id: Optional[str]
    children_ids: list[str]
    package: str
    module: str
    depth: int
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        coord_id: str,
        name: str,
        level: SiteLevel,
        *,
        parent_id: Optional[str] = None,
        package: str = "",
        module: str = "",
        depth: int = 0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> HierarchicalCoordinate:
        return cls(
            id=coord_id,
            name=name,
            level=level,
            parent_id=parent_id,
            children_ids=[],
            package=package,
            module=module,
            depth=depth,
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level.to_dict(),
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "package": self.package,
            "module": self.module,
            "depth": self.depth,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchicalCoordinate:
        return cls(
            id=data["id"],
            name=data["name"],
            level=SiteLevel.from_dict(data["level"]),
            parent_id=data.get("parent_id"),
            children_ids=list(data.get("children_ids", [])),
            package=data.get("package", ""),
            module=data.get("module", ""),
            depth=data.get("depth", 0),
            metadata=dict(data.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_root(self) -> bool:
        return self.parent_id is None

    def is_leaf(self) -> bool:
        return len(self.children_ids) == 0

    def qualified_name(self) -> str:
        parts = [p for p in (self.package, self.module, self.name) if p]
        return ".".join(parts)


# ---------------------------------------------------------------------------
# LevelView
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LevelView:
    """A cross-section of the hierarchical site at a single level.

    Provides a read-only snapshot of all coordinates, morphisms, and covers
    that exist at a particular SiteLevel.
    """

    level: SiteLevel
    coordinates: list[str]  # coordinate ids
    morphisms: list[dict[str, Any]]
    covers: list[dict[str, Any]]
    coordinate_count: int
    morphism_count: int

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        level: SiteLevel,
        coordinates: list[str],
        morphisms: list[dict[str, Any]],
        covers: list[dict[str, Any]],
    ) -> LevelView:
        return cls(
            level=level,
            coordinates=coordinates,
            morphisms=morphisms,
            covers=covers,
            coordinate_count=len(coordinates),
            morphism_count=len(morphisms),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.to_dict(),
            "coordinates": list(self.coordinates),
            "morphisms": [dict(m) for m in self.morphisms],
            "covers": [dict(c) for c in self.covers],
            "coordinate_count": self.coordinate_count,
            "morphism_count": self.morphism_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LevelView:
        return cls(
            level=SiteLevel.from_dict(data["level"]),
            coordinates=list(data.get("coordinates", [])),
            morphisms=[dict(m) for m in data.get("morphisms", [])],
            covers=[dict(c) for c in data.get("covers", [])],
            coordinate_count=data.get("coordinate_count", 0),
            morphism_count=data.get("morphism_count", 0),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        return self.coordinate_count == 0

    def cover_ids(self) -> list[str]:
        return [c["id"] for c in self.covers if "id" in c]


# ---------------------------------------------------------------------------
# HierarchicalCoverMember
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HierarchicalCoverMember:
    """One member of a HierarchicalCover.

    Represents a named sub-region of a cover together with summary
    statistics about its internal and external connectivity.
    """

    id: str
    name: str
    level: SiteLevel
    coordinate_ids: list[str]
    internal_morphism_count: int
    external_morphism_count: int

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        member_id: str,
        name: str,
        level: SiteLevel,
        coordinate_ids: Optional[list[str]] = None,
        internal_morphism_count: int = 0,
        external_morphism_count: int = 0,
    ) -> HierarchicalCoverMember:
        return cls(
            id=member_id,
            name=name,
            level=level,
            coordinate_ids=list(coordinate_ids or []),
            internal_morphism_count=internal_morphism_count,
            external_morphism_count=external_morphism_count,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level.to_dict(),
            "coordinate_ids": list(self.coordinate_ids),
            "internal_morphism_count": self.internal_morphism_count,
            "external_morphism_count": self.external_morphism_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchicalCoverMember:
        return cls(
            id=data["id"],
            name=data["name"],
            level=SiteLevel.from_dict(data["level"]),
            coordinate_ids=list(data.get("coordinate_ids", [])),
            internal_morphism_count=data.get("internal_morphism_count", 0),
            external_morphism_count=data.get("external_morphism_count", 0),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def size(self) -> int:
        return len(self.coordinate_ids)

    def connectivity_ratio(self) -> float:
        total = self.internal_morphism_count + self.external_morphism_count
        if total == 0:
            return 0.0
        return self.internal_morphism_count / total


# ---------------------------------------------------------------------------
# HierarchicalCover
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HierarchicalCover:
    """A cover of coordinates at a given level of the hierarchy.

    Covers can be nested: a cover at PACKAGE level is subdivided into
    covers at MODULE level, and so on.
    """

    id: str
    level: SiteLevel
    members: list[HierarchicalCoverMember]
    parent_cover_id: Optional[str]
    child_cover_ids: list[str]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        cover_id: str,
        level: SiteLevel,
        members: Optional[list[HierarchicalCoverMember]] = None,
        parent_cover_id: Optional[str] = None,
    ) -> HierarchicalCover:
        return cls(
            id=cover_id,
            level=level,
            members=list(members or []),
            parent_cover_id=parent_cover_id,
            child_cover_ids=[],
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level.to_dict(),
            "members": [m.to_dict() for m in self.members],
            "parent_cover_id": self.parent_cover_id,
            "child_cover_ids": list(self.child_cover_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchicalCover:
        return cls(
            id=data["id"],
            level=SiteLevel.from_dict(data["level"]),
            members=[HierarchicalCoverMember.from_dict(m) for m in data.get("members", [])],
            parent_cover_id=data.get("parent_cover_id"),
            child_cover_ids=list(data.get("child_cover_ids", [])),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def member_ids(self) -> list[str]:
        return [m.id for m in self.members]

    def total_coordinates(self) -> int:
        return sum(len(m.coordinate_ids) for m in self.members)

    def is_root_cover(self) -> bool:
        return self.parent_cover_id is None

    def is_leaf_cover(self) -> bool:
        return len(self.child_cover_ids) == 0


# ---------------------------------------------------------------------------
# DescentLevel
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DescentLevel:
    """Result of running the descent algorithm at a single level.

    Records which overlap pairs were found and how many checks passed or
    failed, along with any detected obstructions.
    """

    level: SiteLevel
    overlap_pairs: list[tuple[str, str]]
    checks_required: int
    checks_passed: int
    checks_failed: int
    obstructions: list[Any]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        level: SiteLevel,
        overlap_pairs: Optional[list[tuple[str, str]]] = None,
        obstructions: Optional[list[Any]] = None,
    ) -> DescentLevel:
        pairs = list(overlap_pairs or [])
        return cls(
            level=level,
            overlap_pairs=pairs,
            checks_required=len(pairs),
            checks_passed=0,
            checks_failed=0,
            obstructions=list(obstructions or []),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.to_dict(),
            "overlap_pairs": [list(p) for p in self.overlap_pairs],
            "checks_required": self.checks_required,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "obstructions": list(self.obstructions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DescentLevel:
        return cls(
            level=SiteLevel.from_dict(data["level"]),
            overlap_pairs=[tuple(p) for p in data.get("overlap_pairs", [])],
            checks_required=data.get("checks_required", 0),
            checks_passed=data.get("checks_passed", 0),
            checks_failed=data.get("checks_failed", 0),
            obstructions=list(data.get("obstructions", [])),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def passed(self) -> bool:
        return self.checks_failed == 0

    def completion_ratio(self) -> float:
        if self.checks_required == 0:
            return 1.0
        done = self.checks_passed + self.checks_failed
        return done / self.checks_required


# ---------------------------------------------------------------------------
# HierarchicalDescentResult
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HierarchicalDescentResult:
    """Aggregated result of running hierarchical descent across all levels.

    Levels are processed bottom-up (EXPRESSION → PROJECT) so the first
    failure level is the finest level where a check failed.
    """

    levels: list[DescentLevel]
    overall_passed: bool
    first_failure_level: Optional[SiteLevel]
    total_checks: int
    total_passed: int
    total_failed: int
    duration_ms: float

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_levels(
        cls,
        levels: list[DescentLevel],
        duration_ms: float = 0.0,
    ) -> HierarchicalDescentResult:
        total_checks = sum(d.checks_required for d in levels)
        total_passed = sum(d.checks_passed for d in levels)
        total_failed = sum(d.checks_failed for d in levels)
        overall_passed = total_failed == 0

        first_failure_level: Optional[SiteLevel] = None
        for d in levels:
            if d.checks_failed > 0:
                first_failure_level = d.level
                break

        return cls(
            levels=list(levels),
            overall_passed=overall_passed,
            first_failure_level=first_failure_level,
            total_checks=total_checks,
            total_passed=total_passed,
            total_failed=total_failed,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "levels": [d.to_dict() for d in self.levels],
            "overall_passed": self.overall_passed,
            "first_failure_level": (
                self.first_failure_level.to_dict() if self.first_failure_level else None
            ),
            "total_checks": self.total_checks,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HierarchicalDescentResult:
        ffl_raw = data.get("first_failure_level")
        return cls(
            levels=[DescentLevel.from_dict(d) for d in data.get("levels", [])],
            overall_passed=data.get("overall_passed", True),
            first_failure_level=(SiteLevel.from_dict(ffl_raw) if ffl_raw is not None else None),
            total_checks=data.get("total_checks", 0),
            total_passed=data.get("total_passed", 0),
            total_failed=data.get("total_failed", 0),
            duration_ms=data.get("duration_ms", 0.0),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def level_result(self, level: SiteLevel) -> Optional[DescentLevel]:
        for d in self.levels:
            if d.level == level:
                return d
        return None

    def failure_summary(self) -> dict[str, Any]:
        return {
            "overall_passed": self.overall_passed,
            "first_failure_level": (
                self.first_failure_level.label() if self.first_failure_level else None
            ),
            "total_checks": self.total_checks,
            "total_failed": self.total_failed,
        }


# ---------------------------------------------------------------------------
# PartitionAssignment
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PartitionAssignment:
    """Assignment of a set of coordinates to one partition (worker).

    The estimated_cost field is used by the scheduler to balance work across
    available workers.
    """

    partition_id: str
    level: SiteLevel
    coordinate_ids: list[str]
    estimated_cost: float
    worker_id: Optional[str]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        partition_id: str,
        level: SiteLevel,
        coordinate_ids: Optional[list[str]] = None,
        estimated_cost: float = 0.0,
        worker_id: Optional[str] = None,
    ) -> PartitionAssignment:
        return cls(
            partition_id=partition_id,
            level=level,
            coordinate_ids=list(coordinate_ids or []),
            estimated_cost=estimated_cost,
            worker_id=worker_id,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition_id": self.partition_id,
            "level": self.level.to_dict(),
            "coordinate_ids": list(self.coordinate_ids),
            "estimated_cost": self.estimated_cost,
            "worker_id": self.worker_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PartitionAssignment:
        return cls(
            partition_id=data["partition_id"],
            level=SiteLevel.from_dict(data["level"]),
            coordinate_ids=list(data.get("coordinate_ids", [])),
            estimated_cost=data.get("estimated_cost", 0.0),
            worker_id=data.get("worker_id"),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def size(self) -> int:
        return len(self.coordinate_ids)

    def assign_worker(self, worker_id: str) -> None:
        object.__setattr__(self, "worker_id", worker_id)


# ---------------------------------------------------------------------------
# GeometricPartitioning
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GeometricPartitioning:
    """Result of partitioning a hierarchical site for parallel verification.

    balance_ratio is min_partition_cost / max_partition_cost and lies in
    [0, 1]; a ratio close to 1 means well-balanced partitions.
    """

    total_coordinates: int
    partitions: list[PartitionAssignment]
    total_estimated_cost: float
    max_partition_cost: float
    balance_ratio: float

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_partitions(
        cls,
        total_coordinates: int,
        partitions: list[PartitionAssignment],
    ) -> GeometricPartitioning:
        if not partitions:
            return cls(
                total_coordinates=total_coordinates,
                partitions=[],
                total_estimated_cost=0.0,
                max_partition_cost=0.0,
                balance_ratio=1.0,
            )

        costs = [p.estimated_cost for p in partitions]
        total_cost = sum(costs)
        max_cost = max(costs)
        min_cost = min(costs)
        ratio = min_cost / max_cost if max_cost > 0 else 1.0

        return cls(
            total_coordinates=total_coordinates,
            partitions=list(partitions),
            total_estimated_cost=total_cost,
            max_partition_cost=max_cost,
            balance_ratio=ratio,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_coordinates": self.total_coordinates,
            "partitions": [p.to_dict() for p in self.partitions],
            "total_estimated_cost": self.total_estimated_cost,
            "max_partition_cost": self.max_partition_cost,
            "balance_ratio": self.balance_ratio,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeometricPartitioning:
        return cls(
            total_coordinates=data.get("total_coordinates", 0),
            partitions=[PartitionAssignment.from_dict(p) for p in data.get("partitions", [])],
            total_estimated_cost=data.get("total_estimated_cost", 0.0),
            max_partition_cost=data.get("max_partition_cost", 0.0),
            balance_ratio=data.get("balance_ratio", 1.0),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def partition_count(self) -> int:
        return len(self.partitions)

    def is_balanced(self, threshold: float = 0.8) -> bool:
        return self.balance_ratio >= threshold

    def partition_by_id(self, partition_id: str) -> Optional[PartitionAssignment]:
        for p in self.partitions:
            if p.partition_id == partition_id:
                return p
        return None

    def level_partitions(self, level: SiteLevel) -> list[PartitionAssignment]:
        return [p for p in self.partitions if p.level == level]


__all__ = [
    "SiteLevel",
    "HierarchicalCoordinate",
    "LevelView",
    "HierarchicalCoverMember",
    "HierarchicalCover",
    "DescentLevel",
    "HierarchicalDescentResult",
    "PartitionAssignment",
    "GeometricPartitioning",
]
