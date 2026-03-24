"""
Data models for Čech cohomology computation.

These represent the combinatorial / algebraic structures used to compute
Čech cohomology of the web-application site: nerve cells, cochains,
cohomology groups, and the full Čech complex.

All models use @dataclass with to_dict / from_dict for serialisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


__all__ = [
    "NerveCell",
    "Cochain",
    "CohomologyGroup",
    "CechComplex",
]


# ---------------------------------------------------------------------------
# Nerve cells
# ---------------------------------------------------------------------------

@dataclass
class NerveCell:
    """A cell in the nerve of the covering.

    Parameters
    ----------
    dimension:
        0 = vertex (single covering set / language layer),
        1 = edge (pairwise intersection),
        2 = triangle (triple intersection), etc.
    vertices:
        Sorted list of covering-set names that form this cell.
    data:
        Arbitrary payload (e.g. the concrete overlap coordinates).
    cell_id:
        Optional human-readable identifier.
    """

    dimension: int
    vertices: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    cell_id: str = ""

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "vertices": list(self.vertices),
            "data": dict(self.data),
            "cell_id": self.cell_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NerveCell:
        return cls(
            dimension=data["dimension"],
            vertices=data.get("vertices", []),
            data=data.get("data", {}),
            cell_id=data.get("cell_id", ""),
        )


# ---------------------------------------------------------------------------
# Cochains
# ---------------------------------------------------------------------------

@dataclass
class Cochain:
    """A Čech cochain — a function from *n*-cells to values.

    ``values`` maps ``cell_id`` → local data dict.  The cochain is
    well-defined on the cells listed in ``cells``.
    """

    dimension: int
    cells: list[NerveCell] = field(default_factory=list)
    values: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "cells": [c.to_dict() for c in self.cells],
            "values": {k: dict(v) for k, v in self.values.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cochain:
        return cls(
            dimension=data["dimension"],
            cells=[NerveCell.from_dict(c) for c in data.get("cells", [])],
            values=data.get("values", {}),
        )


# ---------------------------------------------------------------------------
# Cohomology groups
# ---------------------------------------------------------------------------

@dataclass
class CohomologyGroup:
    """The *n*-th Čech cohomology group of the web-app site.

    ``generators`` describe the non-trivial cohomology classes (bugs).
    ``rank`` = max(0, len(generators) - len(relations)).
    ``is_trivial`` ⟺ rank == 0 (no obstructions).
    """

    dimension: int
    generators: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    rank: int = 0
    is_trivial: bool = True
    interpretation: str = ""

    def __post_init__(self) -> None:
        self.rank = max(0, len(self.generators) - len(self.relations))
        self.is_trivial = self.rank == 0

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "generators": list(self.generators),
            "relations": list(self.relations),
            "rank": self.rank,
            "is_trivial": self.is_trivial,
            "interpretation": self.interpretation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CohomologyGroup:
        group = cls(
            dimension=data["dimension"],
            generators=data.get("generators", []),
            relations=data.get("relations", []),
            interpretation=data.get("interpretation", ""),
        )
        # __post_init__ recomputes rank / is_trivial, but honour
        # explicit overrides from serialised data if present.
        return group


# ---------------------------------------------------------------------------
# Čech complex
# ---------------------------------------------------------------------------

@dataclass
class CechComplex:
    """The full Čech complex built from the nerve and its cochains.

    ``cochains_by_dim`` maps dimension → list of serialised cochains.
    ``coboundary_maps`` maps dimension → human-readable description of
    the coboundary operator at that level.
    """

    nerve: list[NerveCell] = field(default_factory=list)
    cochains_by_dim: dict[int, list[dict[str, Any]]] = field(
        default_factory=dict,
    )
    coboundary_maps: dict[int, str] = field(default_factory=dict)
    max_dimension: int = 2

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "nerve": [c.to_dict() for c in self.nerve],
            "cochains_by_dim": {
                str(k): list(v) for k, v in self.cochains_by_dim.items()
            },
            "coboundary_maps": dict(self.coboundary_maps),
            "max_dimension": self.max_dimension,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CechComplex:
        return cls(
            nerve=[
                NerveCell.from_dict(n) for n in data.get("nerve", [])
            ],
            cochains_by_dim={
                int(k): v
                for k, v in data.get("cochains_by_dim", {}).items()
            },
            coboundary_maps={
                int(k): v
                for k, v in data.get("coboundary_maps", {}).items()
            },
            max_dimension=data.get("max_dimension", 2),
        )
