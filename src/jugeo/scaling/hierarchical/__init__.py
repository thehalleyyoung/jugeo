"""Hierarchical Site sub-package for JuGeo's scaling infrastructure.

Multi-level site decomposition that replaces flat sites with a hierarchy
mirroring project structure (project → package → module → class →
function → branch → expression).
"""

from __future__ import annotations

from jugeo.scaling.hierarchical.descent import HierarchicalDescent, OverlapIndex
from jugeo.scaling.hierarchical.levels import LevelHeuristic, LevelPolicy
from jugeo.scaling.hierarchical.models import (
    DescentLevel,
    GeometricPartitioning,
    HierarchicalCoordinate,
    HierarchicalCover,
    HierarchicalCoverMember,
    HierarchicalDescentResult,
    LevelView,
    PartitionAssignment,
    SiteLevel,
)
from jugeo.scaling.hierarchical.partitioning import GeometricPartitioner, PartitionScheduler
from jugeo.scaling.hierarchical.site import HierarchicalSite

__all__ = [
    # Models
    "SiteLevel",
    "HierarchicalCoordinate",
    "LevelView",
    "HierarchicalCoverMember",
    "HierarchicalCover",
    "DescentLevel",
    "HierarchicalDescentResult",
    "PartitionAssignment",
    "GeometricPartitioning",
    # Site
    "HierarchicalSite",
    # Descent
    "HierarchicalDescent",
    "OverlapIndex",
    # Levels
    "LevelHeuristic",
    "LevelPolicy",
    # Partitioning
    "GeometricPartitioner",
    "PartitionScheduler",
]
