"""Data models for the fibered category over a web application.

Each dataclass corresponds to a mathematical object in the fibered
category formalism:

* **LanguageFiber** – a fiber (per-language site) in the total category.
* **FiberedCoordinate** – an object living in a particular fiber.
* **CartesianLift** – a cartesian (or opcartesian) lift along a base
  morphism connecting two language fibers.
* **FiberDescentResult** – the outcome of a descent check within a
  single fiber.
* **FiberedSiteResult** – the global descent result aggregating all
  fibers and boundary conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# LanguageFiber
# ---------------------------------------------------------------------------

@dataclass
class LanguageFiber:
    """A fiber (per-language site) in the web-application fibered category.

    Each language (Python, JS, HTML, CSS, SQL, template) defines its own
    site with coordinate kinds, internal morphisms, and a Grothendieck
    topology.
    """

    name: str
    coordinate_kinds: list[str] = field(default_factory=list)
    morphism_kinds: list[str] = field(default_factory=list)
    internal_topology: dict = field(default_factory=dict)
    description: str = ""

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "coordinate_kinds": list(self.coordinate_kinds),
            "morphism_kinds": list(self.morphism_kinds),
            "internal_topology": dict(self.internal_topology),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LanguageFiber:
        return cls(
            name=data["name"],
            coordinate_kinds=list(data.get("coordinate_kinds", [])),
            morphism_kinds=list(data.get("morphism_kinds", [])),
            internal_topology=dict(data.get("internal_topology", {})),
            description=data.get("description", ""),
        )


# ---------------------------------------------------------------------------
# FiberedCoordinate
# ---------------------------------------------------------------------------

@dataclass
class FiberedCoordinate:
    """An object (coordinate) living inside a particular language fiber.

    *coordinate_id* is globally unique across the total category, while
    *local_id* is unique only within the fiber identified by *fiber_name*.
    """

    coordinate_id: str
    fiber_name: str
    local_id: str
    kind: str
    metadata: dict = field(default_factory=dict)

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "coordinate_id": self.coordinate_id,
            "fiber_name": self.fiber_name,
            "local_id": self.local_id,
            "kind": self.kind,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> FiberedCoordinate:
        return cls(
            coordinate_id=data["coordinate_id"],
            fiber_name=data["fiber_name"],
            local_id=data["local_id"],
            kind=data["kind"],
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# CartesianLift
# ---------------------------------------------------------------------------

@dataclass
class CartesianLift:
    """A (op)cartesian lift along a base morphism between language fibers.

    The lift connects a coordinate in *source_fiber* to a coordinate in
    *target_fiber*.  When *is_cartesian* is ``True`` the lift satisfies the
    universal property of cartesian morphisms in the fibered category.
    """

    morphism_id: str
    source_fiber: str
    target_fiber: str
    lift_type: str
    source_coord: str
    target_coord: str
    is_cartesian: bool = True
    metadata: dict = field(default_factory=dict)

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "morphism_id": self.morphism_id,
            "source_fiber": self.source_fiber,
            "target_fiber": self.target_fiber,
            "lift_type": self.lift_type,
            "source_coord": self.source_coord,
            "target_coord": self.target_coord,
            "is_cartesian": self.is_cartesian,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> CartesianLift:
        return cls(
            morphism_id=data["morphism_id"],
            source_fiber=data["source_fiber"],
            target_fiber=data["target_fiber"],
            lift_type=data["lift_type"],
            source_coord=data["source_coord"],
            target_coord=data["target_coord"],
            is_cartesian=data.get("is_cartesian", True),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# FiberDescentResult
# ---------------------------------------------------------------------------

@dataclass
class FiberDescentResult:
    """Outcome of a descent check within a single language fiber.

    *local_obstructions* lists problems internal to the fiber (e.g. a
    Python route with no handler).  *boundary_obstructions* lists problems
    at the boundary with other fibers (e.g. a template variable with no
    provided context).
    """

    fiber_name: str
    local_obstructions: list[dict] = field(default_factory=list)
    boundary_obstructions: list[dict] = field(default_factory=list)
    passed: bool = True
    coverage_score: float = 1.0

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "fiber_name": self.fiber_name,
            "local_obstructions": [dict(o) for o in self.local_obstructions],
            "boundary_obstructions": [
                dict(o) for o in self.boundary_obstructions
            ],
            "passed": self.passed,
            "coverage_score": self.coverage_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FiberDescentResult:
        return cls(
            fiber_name=data["fiber_name"],
            local_obstructions=[
                dict(o) for o in data.get("local_obstructions", [])
            ],
            boundary_obstructions=[
                dict(o) for o in data.get("boundary_obstructions", [])
            ],
            passed=data.get("passed", True),
            coverage_score=data.get("coverage_score", 1.0),
        )


# ---------------------------------------------------------------------------
# FiberedSiteResult
# ---------------------------------------------------------------------------

@dataclass
class FiberedSiteResult:
    """Global descent result aggregating all fibers and boundary conditions.

    *fibers* maps each fiber name to its ``LanguageFiber.to_dict()``
    representation.  *lifts* contains all cartesian lifts as dicts.
    *global_descent* lists obstructions that span multiple fibers, while
    *per_fiber_descent* maps each fiber name to its
    ``FiberDescentResult.to_dict()``.
    """

    fibers: dict = field(default_factory=dict)
    lifts: list[dict] = field(default_factory=list)
    global_descent: list[dict] = field(default_factory=list)
    per_fiber_descent: dict = field(default_factory=dict)
    overall_passed: bool = True
    total_obstructions: int = 0

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "fibers": {k: dict(v) for k, v in self.fibers.items()},
            "lifts": [dict(l) for l in self.lifts],
            "global_descent": [dict(o) for o in self.global_descent],
            "per_fiber_descent": {
                k: dict(v) for k, v in self.per_fiber_descent.items()
            },
            "overall_passed": self.overall_passed,
            "total_obstructions": self.total_obstructions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FiberedSiteResult:
        return cls(
            fibers={
                k: dict(v) for k, v in data.get("fibers", {}).items()
            },
            lifts=[dict(l) for l in data.get("lifts", [])],
            global_descent=[
                dict(o) for o in data.get("global_descent", [])
            ],
            per_fiber_descent={
                k: dict(v)
                for k, v in data.get("per_fiber_descent", {}).items()
            },
            overall_passed=data.get("overall_passed", True),
            total_obstructions=data.get("total_obstructions", 0),
        )
