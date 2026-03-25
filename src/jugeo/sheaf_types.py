"""
sheaf_types.py — Core algebraic and topological types for cech_model_risk.

Defines the fundamental data structures for representing financial models as
sheaves over market data spaces. Provides abstract base classes and protocols
used by all other modules in the cech_model_risk package.
"""

from __future__ import annotations

import abc
import itertools
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy import linalg
from scipy.sparse import csr_matrix

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Scalar = float | complex
Vector = npt.NDArray[np.float64]
Matrix = npt.NDArray[np.float64]
SectionData = npt.NDArray[np.float64] | pd.Series | pd.DataFrame

T = TypeVar("T")


# ---------------------------------------------------------------------------
# NerveSimplex
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=True)
class NerveSimplex:
    """An oriented simplex in the nerve of an open cover.

    Attributes
    ----------
    vertices : tuple[int, ...]
        Sorted indices of the open sets forming this simplex.
    dimension : int
        Dimension of the simplex (len(vertices) - 1).
    orientation : int
        +1 or -1, determined by the ordering of vertices.
    """

    vertices: tuple[int, ...]
    dimension: int = field(init=False)
    orientation: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimension", len(self.vertices) - 1)
        if not self.vertices == tuple(sorted(self.vertices)):
            raise ValueError("Vertices must be sorted in ascending order.")

    def boundary(self) -> list[NerveSimplex]:
        """Return the oriented boundary simplices of codimension 1."""
        faces = []
        for i, _ in enumerate(self.vertices):
            face_verts = self.vertices[:i] + self.vertices[i + 1 :]
            if face_verts:
                sign = (-1) ** i * self.orientation
                faces.append(NerveSimplex(vertices=face_verts, orientation=sign))
        return faces

    def __repr__(self) -> str:
        return f"NerveSimplex({list(self.vertices)}, dim={self.dimension})"


# ---------------------------------------------------------------------------
# StalkFiber
# ---------------------------------------------------------------------------

@dataclass
class StalkFiber:
    """The stalk of a sheaf at a point, or the fiber over an open set.

    Attributes
    ----------
    open_set_id : int
        Index of the open set (or point) over which this fiber lives.
    data : SectionData
        The actual algebraic data in this fiber (array, Series, DataFrame).
    dimension : int
        Dimension of the fiber as a vector space.
    metadata : dict[str, Any]
        Additional metadata (e.g., model name, calibration date).
    """

    open_set_id: int
    data: SectionData
    dimension: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_vector(self) -> Vector:
        """Flatten fiber data to a 1-D numpy array."""
        if isinstance(self.data, pd.DataFrame):
            return self.data.to_numpy().ravel().astype(np.float64)
        if isinstance(self.data, pd.Series):
            return self.data.to_numpy().astype(np.float64)
        return np.asarray(self.data, dtype=np.float64).ravel()

    def norm(self) -> float:
        """Euclidean norm of the fiber data."""
        return float(np.linalg.norm(self.to_vector()))


# ---------------------------------------------------------------------------
# SheafSection
# ---------------------------------------------------------------------------

@dataclass
class SheafSection:
    """A section of a sheaf over an open set or collection of open sets.

    Attributes
    ----------
    domain_ids : tuple[int, ...]
        The open set indices that constitute the domain of this section.
    fibers : dict[int, StalkFiber]
        Mapping from open-set index to stalk data.
    """

    domain_ids: tuple[int, ...]
    fibers: dict[int, StalkFiber] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not set(self.domain_ids).issubset(self.fibers.keys()):
            raise ValueError(
                "All domain_ids must have a corresponding entry in fibers."
            )

    def restrict(self, subset_ids: Sequence[int]) -> "SheafSection":
        """Return the section restricted to a subset of open sets."""
        subset = tuple(sorted(i for i in subset_ids if i in self.fibers))
        return SheafSection(
            domain_ids=subset,
            fibers={i: self.fibers[i] for i in subset},
        )

    def to_vector(self) -> Vector:
        """Concatenate all fiber vectors into one global section vector."""
        parts = [self.fibers[i].to_vector() for i in sorted(self.domain_ids)]
        return np.concatenate(parts) if parts else np.array([], dtype=np.float64)

    def discrepancy(self, other: "SheafSection") -> float:
        """L2 norm of the difference between two sections on shared domain."""
        shared = sorted(set(self.domain_ids) & set(other.domain_ids))
        if not shared:
            return float("nan")
        a = np.concatenate([self.fibers[i].to_vector() for i in shared])
        b = np.concatenate([other.fibers[i].to_vector() for i in shared])
        return float(np.linalg.norm(a - b))


# ---------------------------------------------------------------------------
# RestrictionMap
# ---------------------------------------------------------------------------

@dataclass
class RestrictionMap:
    """A linear map between stalks encoding the sheaf restriction morphism.

    Implements ρ_{UV}: F(U) → F(V) for V ⊆ U.

    Attributes
    ----------
    source_id : int
        Open-set index of the larger set U.
    target_id : int
        Open-set index of the smaller set V.
    matrix : Matrix
        Dense matrix representation (target_dim × source_dim).
    """

    source_id: int
    target_id: int
    matrix: Matrix

    def __post_init__(self) -> None:
        self.matrix = np.asarray(self.matrix, dtype=np.float64)

    def apply(self, fiber: StalkFiber) -> StalkFiber:
        """Apply the restriction map to a stalk fiber."""
        v = fiber.to_vector()
        result = self.matrix @ v
        return StalkFiber(
            open_set_id=self.target_id,
            data=result,
            dimension=result.shape[0],
            metadata={**fiber.metadata, "restricted_from": self.source_id},
        )

    def compose(self, other: "RestrictionMap") -> "RestrictionMap":
        """Compose self ∘ other (other applied first)."""
        if self.source_id != other.target_id:
            raise ValueError("Maps are not composable: source/target mismatch.")
        return RestrictionMap(
            source_id=other.source_id,
            target_id=self.target_id,
            matrix=self.matrix @ other.matrix,
        )

    def is_identity(self, tol: float = 1e-10) -> bool:
        """Return True if the matrix is close to the identity."""
        m = self.matrix
        if m.shape[0] != m.shape[1]:
            return False
        return bool(np.allclose(m, np.eye(m.shape[0]), atol=tol))

    def rank(self) -> int:
        return int(np.linalg.matrix_rank(self.matrix))


# ---------------------------------------------------------------------------
# OpenCover
# ---------------------------------------------------------------------------

@dataclass
class OpenCover:
    """A finite open cover of a topological space (or market-data manifold).

    Attributes
    ----------
    sets : dict[int, set[Any]]
        Mapping from open-set index to a set of data-point indices it covers.
    ambient_points : set[Any]
        All points in the ambient space.
    """

    sets: dict[int, set[Any]]
    ambient_points: set[Any] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.ambient_points:
            self.ambient_points = set().union(*self.sets.values())

    def intersection(self, *ids: int) -> set[Any]:
        """Return the intersection of the given open sets."""
        if not ids:
            return set(self.ambient_points)
        result = self.sets[ids[0]]
        for i in ids[1:]:
            result = result & self.sets[i]
        return result

    def is_cover(self) -> bool:
        """Check that the union of all open sets equals the ambient space."""
        union: set[Any] = set().union(*self.sets.values())
        return union >= self.ambient_points

    def nerve(self) -> list[NerveSimplex]:
        """Compute the nerve of the cover (all non-empty intersections)."""
        return build_nerve(self)

    def refinement(self, other: "OpenCover") -> bool:
        """Return True if self refines other (each set of self is in some set of other)."""
        for u in self.sets.values():
            if not any(u.issubset(v) for v in other.sets.values()):
                return False
        return True


# ---------------------------------------------------------------------------
# CoboundaryOperator
# ---------------------------------------------------------------------------

@dataclass
class CoboundaryOperator:
    """The Čech coboundary operator δ^k: C^k → C^{k+1}.

    Attributes
    ----------
    degree : int
        Cohomological degree k.
    matrix : Matrix | csr_matrix
        Coboundary matrix (sparse or dense).
    source_cochains : list[NerveSimplex]
        Basis of k-cochains (source).
    target_cochains : list[NerveSimplex]
        Basis of (k+1)-cochains (target).
    """

    degree: int
    matrix: Matrix | csr_matrix
    source_cochains: list[NerveSimplex]
    target_cochains: list[NerveSimplex]

    def kernel_dimension(self) -> int:
        """Dimension of the kernel (space of cocycles)."""
        m = self.matrix.toarray() if hasattr(self.matrix, "toarray") else self.matrix
        return int(m.shape[1] - np.linalg.matrix_rank(m))

    def image_dimension(self) -> int:
        """Dimension of the image (space of coboundaries)."""
        m = self.matrix.toarray() if hasattr(self.matrix, "toarray") else self.matrix
        return int(np.linalg.matrix_rank(m))

    def apply(self, cochain: Vector) -> Vector:
        """Apply the coboundary operator to a cochain vector."""
        m = self.matrix.toarray() if hasattr(self.matrix, "toarray") else self.matrix
        return m @ cochain


# ---------------------------------------------------------------------------
# CohomologyGroup
# ---------------------------------------------------------------------------

@dataclass
class CohomologyGroup:
    """The k-th Čech cohomology group Ȟ^k of a sheaf.

    Attributes
    ----------
    degree : int
        Cohomological degree k.
    betti_number : int
        Rank of the free part (dimension over ℝ).
    cocycle_basis : list[Vector]
        Representative cocycles spanning the cohomology.
    risk_interpretation : str
        Human-readable interpretation of the cohomology class in risk terms.
    """

    degree: int
    betti_number: int
    cocycle_basis: list[Vector] = field(default_factory=list)
    risk_interpretation: str = ""

    def is_trivial(self) -> bool:
        """True if the cohomology group is zero (no topological obstruction)."""
        return self.betti_number == 0

    def summary(self) -> pd.Series:
        return pd.Series(
            {
                "degree": self.degree,
                "betti_number": self.betti_number,
                "trivial": self.is_trivial(),
                "risk_interpretation": self.risk_interpretation,
            }
        )


# ---------------------------------------------------------------------------
# Sheaf (abstract base)
# ---------------------------------------------------------------------------

class Sheaf(abc.ABC):
    """Abstract base class for a sheaf of vector spaces over an open cover.

    Subclasses must implement :meth:`stalk`, :meth:`restriction`, and
    :meth:`sections`.
    """

    @abc.abstractmethod
    def stalk(self, open_set_id: int) -> StalkFiber:
        """Return the stalk (fiber) over the given open set."""
        ...

    @abc.abstractmethod
    def restriction(self, source_id: int, target_id: int) -> RestrictionMap:
        """Return ρ_{source → target} for target ⊆ source."""
        ...

    @abc.abstractmethod
    def sections(self, domain_ids: Sequence[int]) -> SheafSection:
        """Return global sections over the given collection of open sets."""
        ...

    def stalks_dataframe(self, ids: Sequence[int]) -> pd.DataFrame:
        """Collect stalk metadata into a DataFrame for inspection."""
        rows = []
        for i in ids:
            sf = self.stalk(i)
            rows.append(
                {
                    "open_set_id": sf.open_set_id,
                    "dimension": sf.dimension,
                    "norm": sf.norm(),
                    **sf.metadata,
                }
            )
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# SheafMorphism
# ---------------------------------------------------------------------------

@dataclass
class SheafMorphism:
    """A morphism φ: F → G between two sheaves.

    For each open set U, provides a linear map φ_U: F(U) → G(U) that
    commutes with the restriction maps of both sheaves.

    Attributes
    ----------
    source_sheaf_id : str
        Identifier of the source sheaf F.
    target_sheaf_id : str
        Identifier of the target sheaf G.
    component_maps : dict[int, Matrix]
        Per-open-set linear maps φ_U indexed by open-set id.
    """

    source_sheaf_id: str
    target_sheaf_id: str
    component_maps: dict[int, Matrix]

    def apply(self, section: SheafSection) -> SheafSection:
        """Push-forward a section of F to a section of G."""
        new_fibers: dict[int, StalkFiber] = {}
        for oid, fiber in section.fibers.items():
            phi = self.component_maps.get(oid)
            if phi is None:
                raise KeyError(f"No component map for open set {oid}.")
            phi = np.asarray(phi, dtype=np.float64)
            out = phi @ fiber.to_vector()
            new_fibers[oid] = StalkFiber(
                open_set_id=oid, data=out, dimension=out.shape[0]
            )
        return SheafSection(domain_ids=section.domain_ids, fibers=new_fibers)

    def is_isomorphism(self, tol: float = 1e-10) -> bool:
        """Check whether all component maps are invertible."""
        for phi in self.component_maps.values():
            phi = np.asarray(phi, dtype=np.float64)
            if phi.shape[0] != phi.shape[1]:
                return False
            if abs(np.linalg.det(phi)) < tol:
                return False
        return True


# ---------------------------------------------------------------------------
# CechComplex
# ---------------------------------------------------------------------------

@dataclass
class CechComplex:
    """The Čech cochain complex associated with a sheaf and an open cover.

    Stores the coboundary operators δ^0, δ^1, … and provides methods to
    compute cohomology.

    Attributes
    ----------
    sheaf_id : str
        Identifier of the underlying sheaf.
    cover : OpenCover
        The open cover used to define the complex.
    coboundaries : list[CoboundaryOperator]
        Coboundary operators ordered by degree.
    nerve_simplices : dict[int, list[NerveSimplex]]
        Simplices of the nerve grouped by dimension.
    """

    sheaf_id: str
    cover: OpenCover
    coboundaries: list[CoboundaryOperator] = field(default_factory=list)
    nerve_simplices: dict[int, list[NerveSimplex]] = field(default_factory=dict)

    def cohomology(self, degree: int) -> CohomologyGroup:
        """Compute the k-th cohomology group."""
        if degree >= len(self.coboundaries):
            return CohomologyGroup(degree=degree, betti_number=0)
        delta_k = self.coboundaries[degree]
        ker_dim = delta_k.kernel_dimension()
        img_dim = (
            self.coboundaries[degree - 1].image_dimension()
            if degree > 0
            else 0
        )
        betti = max(0, ker_dim - img_dim)
        return CohomologyGroup(degree=degree, betti_number=betti)

    def euler_characteristic(self) -> int:
        """Alternating sum of Betti numbers (topological Euler characteristic)."""
        return sum(
            (-1) ** k * self.cohomology(k).betti_number
            for k in range(len(self.coboundaries) + 1)
        )

    def cohomology_table(self) -> pd.DataFrame:
        """Return a DataFrame with Betti numbers for each degree."""
        rows = [self.cohomology(k).summary() for k in range(len(self.coboundaries) + 1)]
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# ModelRiskMeasure
# ---------------------------------------------------------------------------

@dataclass
class ModelRiskMeasure:
    """A quantitative model-risk measure derived from sheaf cohomology.

    Attributes
    ----------
    name : str
        Name of the risk measure (e.g., "H1_betti", "gluing_defect").
    value : float
        Numerical value of the measure.
    cohomology_degree : int
        The cohomological degree from which this measure is derived.
    confidence : float
        Estimated confidence/reliability in [0, 1].
    metadata : dict[str, Any]
        Supporting data (e.g., calibration details, market date).
    """

    name: str
    value: float
    cohomology_degree: int
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_elevated(self, threshold: float) -> bool:
        """Return True if the risk measure exceeds the given threshold."""
        return self.value > threshold

    def to_series(self) -> pd.Series:
        return pd.Series(
            {
                "name": self.name,
                "value": self.value,
                "cohomology_degree": self.cohomology_degree,
                "confidence": self.confidence,
            }
        )


# ---------------------------------------------------------------------------
# ModelAtlas
# ---------------------------------------------------------------------------

@dataclass
class ModelAtlas:
    """A collection of financial models charted over overlapping market regimes.

    Represents a model atlas as a sheaf: each chart is an open set carrying
    model parameters, and transition maps are restriction maps between charts.

    Attributes
    ----------
    atlas_id : str
        Unique identifier for this atlas.
    charts : dict[int, dict[str, Any]]
        Per-open-set model specifications (parameters, type, etc.).
    cover : OpenCover
        The open cover of the market-data space.
    transition_maps : dict[tuple[int, int], RestrictionMap]
        Transition (restriction) maps ρ_{i→j} for all overlapping pairs (i, j).
    risk_measures : list[ModelRiskMeasure]
        Computed risk measures for this atlas.
    """

    atlas_id: str
    charts: dict[int, dict[str, Any]]
    cover: OpenCover
    transition_maps: dict[tuple[int, int], RestrictionMap] = field(default_factory=dict)
    risk_measures: list[ModelRiskMeasure] = field(default_factory=list)

    def add_transition(self, source: int, target: int, matrix: Matrix) -> None:
        """Register a transition map between two overlapping charts."""
        self.transition_maps[(source, target)] = RestrictionMap(
            source_id=source, target_id=target, matrix=np.asarray(matrix, dtype=np.float64)
        )

    def consistency_matrix(self) -> pd.DataFrame:
        """Return a DataFrame showing pairwise consistency (rank) of transitions."""
        pairs = list(self.transition_maps.keys())
        rows = []
        for src, tgt in pairs:
            rm = self.transition_maps[(src, tgt)]
            rows.append({"source": src, "target": tgt, "rank": rm.rank()})
        return pd.DataFrame(rows)

    def risk_summary(self) -> pd.DataFrame:
        """Return a DataFrame of all registered risk measures."""
        return pd.DataFrame([rm.to_series() for rm in self.risk_measures])


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

@runtime_checkable
class SupportsSectionConversion(Protocol):
    """Protocol for objects that can convert to/from section vectors."""

    def to_vector(self) -> Vector: ...
    def from_vector(self, v: Vector) -> "SupportsSectionConversion": ...


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def build_nerve(cover: OpenCover) -> list[NerveSimplex]:
    """Build the full nerve of an open cover.

    Returns all simplices (of every dimension) whose corresponding
    intersection is non-empty.

    Parameters
    ----------
    cover : OpenCover
        The open cover whose nerve is to be computed.

    Returns
    -------
    list[NerveSimplex]
        All non-empty nerve simplices, sorted by dimension then vertices.
    """
    ids = sorted(cover.sets.keys())
    simplices: list[NerveSimplex] = []
    for r in range(1, len(ids) + 1):
        for combo in itertools.combinations(ids, r):
            pts = cover.sets[combo[0]]
            for c in combo[1:]:
                pts = pts & cover.sets[c]
            if pts:
                simplices.append(NerveSimplex(vertices=combo))
    return simplices


def compute_intersection_cover(
    cover: OpenCover,
) -> dict[tuple[int, ...], set[Any]]:
    """Compute all pairwise (and higher-order) intersections in the cover.

    Parameters
    ----------
    cover : OpenCover
        The open cover.

    Returns
    -------
    dict mapping sorted index tuples to the corresponding intersection sets.
    """
    ids = sorted(cover.sets.keys())
    result: dict[tuple[int, ...], set[Any]] = {}
    for r in range(1, len(ids) + 1):
        for combo in itertools.combinations(ids, r):
            inter = cover.intersection(*combo)
            if inter:
                result[combo] = inter
    return result


def validate_sheaf_axioms(
    sheaf: Sheaf,
    cover: OpenCover,
    tol: float = 1e-8,
) -> dict[str, bool]:
    """Validate the sheaf axioms for a sheaf over a given cover.

    Checks:
    1. Locality: sections agreeing on every open set agree on the whole.
    2. Gluing: compatible local sections extend to a global section.

    Parameters
    ----------
    sheaf : Sheaf
        The sheaf to validate.
    cover : OpenCover
        The open cover.
    tol : float
        Numerical tolerance for comparisons.

    Returns
    -------
    dict with keys "locality" and "gluing", each mapped to a bool.
    """
    ids = sorted(cover.sets.keys())
    locality_ok = True
    gluing_ok = True

    for i, j in itertools.combinations(ids, 2):
        inter = cover.intersection(i, j)
        if not inter:
            continue
        si = sheaf.stalk(i)
        sj = sheaf.stalk(j)
        rij = sheaf.restriction(i, j) if si.dimension >= sj.dimension else None
        rji = sheaf.restriction(j, i) if sj.dimension >= si.dimension else None

        if rij is not None:
            vi = rij.apply(si).to_vector()
            vj = sj.to_vector()
            if not np.allclose(vi, vj, atol=tol):
                locality_ok = False

        if rji is not None:
            vj = rji.apply(sj).to_vector()
            vi = si.to_vector()
            if not np.allclose(vj, vi, atol=tol):
                gluing_ok = False

    return {"locality": locality_ok, "gluing": gluing_ok}


def check_gluing_condition(
    section_i: SheafSection,
    section_j: SheafSection,
    restriction_ij: RestrictionMap,
    restriction_ji: RestrictionMap,
    tol: float = 1e-8,
) -> bool:
    """Check whether two local sections satisfy the gluing condition on overlap.

    Two sections s_i ∈ F(U_i) and s_j ∈ F(U_j) are compatible iff
    ρ_{i→ij}(s_i) = ρ_{j→ij}(s_j).

    Parameters
    ----------
    section_i, section_j : SheafSection
        Local sections over U_i and U_j respectively.
    restriction_ij : RestrictionMap
        ρ from U_i to U_i ∩ U_j.
    restriction_ji : RestrictionMap
        ρ from U_j to U_i ∩ U_j.
    tol : float
        Numerical tolerance.

    Returns
    -------
    bool
        True if sections are compatible.
    """
    shared = sorted(set(section_i.domain_ids) & set(section_j.domain_ids))
    if not shared:
        return True

    vi = restriction_ij.apply(section_i.fibers[restriction_ij.source_id]).to_vector()
    vj = restriction_ji.apply(section_j.fibers[restriction_ji.source_id]).to_vector()
    return bool(np.allclose(vi, vj, atol=tol))


def restriction_matrix(
    source_dim: int,
    target_indices: Sequence[int],
) -> Matrix:
    """Build a canonical projection restriction matrix.

    Selects specific coordinates (target_indices) from a source-dimensional
    vector space.

    Parameters
    ----------
    source_dim : int
        Dimension of the source stalk.
    target_indices : Sequence[int]
        Indices of coordinates to retain.

    Returns
    -------
    Matrix of shape (len(target_indices), source_dim).
    """
    n = len(target_indices)
    mat = np.zeros((n, source_dim), dtype=np.float64)
    for row, col in enumerate(target_indices):
        mat[row, col] = 1.0
    return mat


def section_to_vector(section: SheafSection) -> Vector:
    """Flatten a SheafSection into a single concatenated numpy array."""
    return section.to_vector()


def vector_to_section(
    v: Vector,
    domain_ids: Sequence[int],
    fiber_dims: Sequence[int],
    metadata: Sequence[dict[str, Any]] | None = None,
) -> SheafSection:
    """Reconstruct a SheafSection from a flat vector.

    Parameters
    ----------
    v : Vector
        Flat numpy array produced by :func:`section_to_vector`.
    domain_ids : Sequence[int]
        Open-set indices for each fiber.
    fiber_dims : Sequence[int]
        Dimension of each fiber in the same order as domain_ids.
    metadata : optional list of metadata dicts per fiber.

    Returns
    -------
    SheafSection
    """
    if sum(fiber_dims) != len(v):
        raise ValueError(
            f"Sum of fiber dims ({sum(fiber_dims)}) ≠ vector length ({len(v)})."
        )
    fibers: dict[int, StalkFiber] = {}
    offset = 0
    meta_list = metadata or [{}] * len(domain_ids)
    for oid, dim, meta in zip(domain_ids, fiber_dims, meta_list):
        chunk = v[offset : offset + dim]
        fibers[oid] = StalkFiber(open_set_id=oid, data=chunk, dimension=dim, metadata=meta)
        offset += dim
    return SheafSection(domain_ids=tuple(sorted(domain_ids)), fibers=fibers)


def cohomology_dimension(
    coboundaries: list[CoboundaryOperator],
    degree: int,
) -> int:
    """Compute the dimension of the k-th cohomology group.

    H^k = ker(δ^k) / im(δ^{k-1})

    Parameters
    ----------
    coboundaries : list[CoboundaryOperator]
        Ordered list of coboundary operators δ^0, δ^1, …
    degree : int
        Cohomological degree k ≥ 0.

    Returns
    -------
    int
        Dimension of H^k (Betti number β_k).
    """
    if degree >= len(coboundaries):
        return 0
    ker_dim = coboundaries[degree].kernel_dimension()
    img_dim = coboundaries[degree - 1].image_dimension() if degree > 0 else 0
    return max(0, ker_dim - img_dim)
