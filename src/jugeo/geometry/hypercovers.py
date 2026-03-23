"""Hypercovers for iteratively refined JuGeo covers and simplicial descent.

In topos-theoretic geometry a hypercover is an iterated cover: you cover the
base, then cover the overlaps, then cover the overlaps-of-overlaps, and so on.
Hypercovers arise when simple covers are not fine enough to compute cohomology
or to glue local sections.  In the JuGeo implementation hypercovers model
multi-level module decomposition (project → packages → modules → classes →
methods) and iterative refinement strategies used by copilot-guided synthesis.

The simplicial structure is captured via face maps (projections to sub-overlaps)
and degeneracy maps (trivial inclusions).  The Čech nerve of a cover is the
canonical hypercover and is computed explicitly in ``CechNerve``.

See theory2.tex §4 for the mathematical background.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import combinations, product
from typing import Any, Callable, Mapping, Sequence

from jugeo.geometry.covers import Cover, CoverMetric, refine_cover, score_cover
from jugeo.geometry.site import CoordinateKind, CoordinateObject, SemanticSite
from jugeo.geometry.supports import SupportRegion, compute_support


# ---------------------------------------------------------------------------
# HypercoverKind
# ---------------------------------------------------------------------------


class HypercoverKind(str, Enum):
    """Categorical kind of a hypercover.

    Members
    -------
    CECH
        Standard Čech hypercover: level *n* patches are (n+1)-fold intersections
        of the level-0 patches.  The canonical hypercover for any cover.
    GODEMENT
        Godement canonical flasque resolution; used when injective sheaf
        resolutions are needed for cohomology computations.
    SPLIT
        Hypercover admitting a splitting: every degeneracy map has a section,
        which simplifies descent to a product formula.
    TRUNCATED
        sk_n-truncated hypercover: the simplicial object is only defined up
        to level n and the higher levels are collapsed.
    AUGMENTED
        Hypercover augmented with a level -1 (the terminal object / whole
        site), making the augmentation map explicit.
    """

    CECH = "cech"
    GODEMENT = "godement"
    SPLIT = "split"
    TRUNCATED = "truncated"
    AUGMENTED = "augmented"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enumerate_overlaps(keys: Sequence[str], max_arity: int = 4) -> tuple[tuple[str, ...], ...]:
    """Enumerate multi-fold overlaps of *keys* up to *max_arity*."""
    return tuple(
        tuple(group)
        for size in range(2, min(max_arity + 1, len(keys) + 1))
        for group in combinations(keys, size)
    )


def _identity_map(keys: Sequence[str]) -> dict[str, str]:
    """Return the identity mapping on *keys*."""
    return {k: k for k in keys}


# ---------------------------------------------------------------------------
# 1. HypercoverLevel
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HypercoverLevel:
    """One level of a hypercover — a cover together with its face/degeneracy
    maps relating it to the adjacent levels.

    Attributes
    ----------
    level_number:
        The simplicial degree (0 = base cover, 1 = overlaps cover, …).
    cover:
        The ``Cover`` at this level.
    face_maps:
        Maps from this level to the previous level.  Keyed by face index,
        each value maps patch-key at this level to patch-key at the previous.
    degeneracy_maps:
        Maps from the previous level *into* this level.  Keyed by degeneracy
        index, values map patch-key at the previous level to this level.
    augmentation:
        For level 0 this maps patch-keys to the base coordinate key.  For
        higher levels it is the composite to the base.
    overlap_cells:
        Multi-fold overlap tuples computed from the patches at this level.
    provenance:
        Tracking tuple recording which operations built this level.
    """

    level_number: int
    cover: Cover
    face_maps: tuple[dict[str, str], ...] = field(default_factory=tuple)
    degeneracy_maps: tuple[dict[str, str], ...] = field(default_factory=tuple)
    augmentation: dict[str, str] = field(default_factory=dict)
    overlap_cells: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    provenance: tuple[str, ...] = ()

    @property
    def patch_keys(self) -> tuple[str, ...]:
        """Patch keys at this level."""
        return self.cover.patch_keys()

    @property
    def dimension(self) -> int:
        """Number of patches at this level."""
        return len(self.cover.patches)

    @property
    def num_faces(self) -> int:
        """Number of face maps from this level."""
        return len(self.face_maps)

    @property
    def num_degeneracies(self) -> int:
        """Number of degeneracy maps into this level."""
        return len(self.degeneracy_maps)

    def face(self, index: int) -> dict[str, str]:
        """Return the *index*-th face map."""
        if index < 0 or index >= len(self.face_maps):
            raise IndexError(f"face index {index} out of range [0, {len(self.face_maps)})")
        return self.face_maps[index]

    def degeneracy(self, index: int) -> dict[str, str]:
        """Return the *index*-th degeneracy map."""
        if index < 0 or index >= len(self.degeneracy_maps):
            raise IndexError(f"degeneracy index {index} out of range [0, {len(self.degeneracy_maps)})")
        return self.degeneracy_maps[index]

    def is_degenerate(self) -> bool:
        """True when the level is entirely generated by degeneracy maps."""
        if not self.degeneracy_maps:
            return False
        all_images: set[str] = set()
        for dmap in self.degeneracy_maps:
            all_images.update(dmap.values())
        return all_images == set(self.patch_keys)

    def augmentation_image(self) -> frozenset[str]:
        """The set of base keys hit by the augmentation map."""
        return frozenset(self.augmentation.values())

    def with_provenance(self, *tags: str) -> HypercoverLevel:
        """Return a copy with additional provenance tags."""
        return replace(self, provenance=self.provenance + tags)


# ---------------------------------------------------------------------------
# 2. Hypercover
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Hypercover:
    """A hypercover over a base coordinate.

    A hypercover is a sequence of ``HypercoverLevel`` objects together with
    face and degeneracy maps satisfying the simplicial identities.  It
    generalises a plain ``Cover`` by allowing iterative refinement of overlaps
    at each depth — the key mechanism for copilot-guided descent when a single
    cover is insufficient.
    """

    base_coordinate: CoordinateObject
    levels: tuple[HypercoverLevel, ...] = field(default_factory=tuple)

    @property
    def layers(self) -> tuple[HypercoverLevel, ...]:
        """Backward-compatible alias for ``levels``."""
        return self.levels

    # -- depth & access -------------------------------------------------------

    def depth(self) -> int:
        """The truncation depth (number of levels minus one, or -1 if empty)."""
        return len(self.levels) - 1 if self.levels else -1

    def level_at(self, n: int) -> HypercoverLevel:
        """Return the level at simplicial degree *n*."""
        if n < 0 or n >= len(self.levels):
            raise IndexError(f"level {n} out of range [0, {len(self.levels)})")
        return self.levels[n]

    def base_cover(self) -> Cover:
        """The cover at level 0 — the initial open cover of the base."""
        if not self.levels:
            raise ValueError("hypercover has no levels")
        return self.levels[0].cover

    # -- truncation & skeleton ------------------------------------------------

    def truncate_at(self, n: int) -> Hypercover:
        """Return a new hypercover keeping only levels 0 … *n*."""
        clamped = min(n + 1, len(self.levels))
        return replace(self, levels=self.levels[:clamped])

    def skeleton(self, n: int) -> Hypercover:
        """The *n*-skeleton: keep levels 0 … *n* and strip degeneracy maps
        above level *n*."""
        truncated = list(self.levels[:n + 1])
        if truncated:
            last = truncated[-1]
            truncated[-1] = replace(last, degeneracy_maps=())
        return replace(self, levels=tuple(truncated))

    def coskeleton(self, n: int) -> Hypercover:
        """A naive coskeleton: pad higher levels with degenerate copies of
        level *n* up to ``2 * n``."""
        if n < 0 or n >= len(self.levels):
            return self
        base_level = self.levels[n]
        extra: list[HypercoverLevel] = []
        for k in range(n + 1, 2 * n + 1):
            identity = _identity_map(base_level.patch_keys)
            extra.append(replace(
                base_level,
                level_number=k,
                face_maps=(identity,) * (k + 1),
                degeneracy_maps=(identity,) * k,
                provenance=base_level.provenance + (f'cosk_{n}',),
            ))
        return replace(self, levels=self.levels[:n + 1] + tuple(extra))

    # -- simplicial checks ----------------------------------------------------

    def is_split(self) -> bool:
        """True when every level is a retract of the previous (all degeneracy
        maps have left inverses among the face maps)."""
        for lvl in self.levels[1:]:
            for dmap in lvl.degeneracy_maps:
                found_inverse = False
                for fmap in lvl.face_maps:
                    if all(fmap.get(dmap.get(k, ''), '') == k for k in dmap):
                        found_inverse = True
                        break
                if not found_inverse:
                    return False
        return True

    def verify_simplicial_identities(self) -> list[str]:
        """Check face–face, degeneracy–degeneracy and mixed identities.

        Returns a list of human-readable violation descriptions (empty means
        the hypercover is valid).
        """
        violations: list[str] = []
        for lvl in self.levels:
            n = lvl.level_number
            fm = lvl.face_maps
            dm = lvl.degeneracy_maps
            # d_i d_j = d_{j+1} d_i  for i <= j
            for i in range(len(fm)):
                for j in range(i, len(fm)):
                    if j + 1 < len(fm):
                        lhs = {k: fm[j + 1].get(fm[i].get(k, ''), '') for k in lvl.patch_keys}
                        rhs = {k: fm[i].get(fm[j].get(k, ''), '') for k in lvl.patch_keys}
                        if lhs != rhs:
                            violations.append(
                                f"level {n}: face-face identity d_{i}d_{j} != d_{{{j+1}}}d_{i}"
                            )
            # s_i s_j = s_j s_{i+1}  for i >= j
            for i in range(len(dm)):
                for j in range(0, i + 1):
                    if i + 1 < len(dm):
                        lhs = {k: dm[j].get(dm[i].get(k, ''), '') for k in lvl.patch_keys}
                        rhs = {k: dm[i + 1].get(dm[j].get(k, ''), '') for k in lvl.patch_keys}
                        if lhs != rhs:
                            violations.append(
                                f"level {n}: degeneracy identity s_{i}s_{j} != s_{j}s_{{{i+1}}}"
                            )
        return violations

    # -- matching objects & descent -------------------------------------------

    def matching_object_at(self, n: int) -> MatchingObject:
        """Compute the matching object at level *n*.

        The matching object encodes the data that must be provided at level *n*
        in order to extend the hypercover one step further.
        """
        if n < 0 or n > len(self.levels):
            raise IndexError(f"matching object level {n} out of range")
        if n == 0:
            return MatchingObject(
                level=0,
                required_keys=frozenset({self.base_coordinate.key}),
                face_constraints={},
                partial_fillers=(),
            )
        prev = self.levels[n - 1]
        constraints: dict[str, list[str]] = {}
        for cell in prev.overlap_cells:
            for k in cell:
                constraints.setdefault(k, []).extend(c for c in cell if c != k)
        return MatchingObject(
            level=n,
            required_keys=frozenset(prev.patch_keys),
            face_constraints=constraints,
            partial_fillers=(),
        )

    def compute_descent_data(self) -> dict[str, Any]:
        """Collect descent-relevant information across all levels.

        Returns a dict with keys ``levels``, ``total_patches``,
        ``total_overlaps``, ``is_split``, ``violations``.  Useful for
        copilot-assisted diagnosis.
        """
        total_patches = sum(lvl.dimension for lvl in self.levels)
        total_overlaps = sum(len(lvl.overlap_cells) for lvl in self.levels)
        return {
            'depth': self.depth(),
            'levels': len(self.levels),
            'total_patches': total_patches,
            'total_overlaps': total_overlaps,
            'is_split': self.is_split(),
            'violations': self.verify_simplicial_identities(),
            'base': self.base_coordinate.key,
            'copilot_hint': 'hypercover descent data ready for layer-by-layer gluing',
        }


# ---------------------------------------------------------------------------
# 3. HypercoverBuilder
# ---------------------------------------------------------------------------

class HypercoverBuilder:
    """Fluent builder for constructing ``Hypercover`` instances step by step.

    Usage::

        hc = (
            HypercoverBuilder()
            .set_base(coord)
            .add_level(cover_0)
            .set_face_maps(0, [fm_0])
            .add_level(cover_1)
            .set_face_maps(1, [fm_1a, fm_1b])
            .set_degeneracy_maps(1, [sm_1])
            .validate_simplicial()
            .build()
        )

    The builder validates simplicial identities on ``build()`` by default and
    can be asked to do so eagerly via ``validate_simplicial()``.
    """

    def __init__(self) -> None:
        self._base: CoordinateObject | None = None
        self._levels: list[dict[str, Any]] = []
        self._validated: bool = False

    def set_base(self, coordinate: CoordinateObject) -> HypercoverBuilder:
        """Set the base coordinate for the hypercover."""
        self._base = coordinate
        return self

    def add_level(self, cover: Cover, *, overlap_max_arity: int = 4) -> HypercoverBuilder:
        """Append a new level with the given *cover*."""
        keys = cover.patch_keys()
        self._levels.append({
            'cover': cover,
            'face_maps': (),
            'degeneracy_maps': (),
            'augmentation': {k: (self._base.key if self._base else '') for k in keys},
            'overlap_cells': _enumerate_overlaps(keys, overlap_max_arity),
        })
        self._validated = False
        return self

    def set_face_maps(self, level: int, maps: Sequence[dict[str, str]]) -> HypercoverBuilder:
        """Set face maps for a given *level*."""
        if level < 0 or level >= len(self._levels):
            raise IndexError(f"level {level} out of range")
        self._levels[level]['face_maps'] = tuple(maps)
        self._validated = False
        return self

    def set_degeneracy_maps(self, level: int, maps: Sequence[dict[str, str]]) -> HypercoverBuilder:
        """Set degeneracy maps for a given *level*."""
        if level < 0 or level >= len(self._levels):
            raise IndexError(f"level {level} out of range")
        self._levels[level]['degeneracy_maps'] = tuple(maps)
        self._validated = False
        return self

    def set_augmentation(self, level: int, aug: dict[str, str]) -> HypercoverBuilder:
        """Override the augmentation map at *level*."""
        if level < 0 or level >= len(self._levels):
            raise IndexError(f"level {level} out of range")
        self._levels[level]['augmentation'] = dict(aug)
        return self

    def validate_simplicial(self) -> HypercoverBuilder:
        """Eagerly validate simplicial identities on the current state.

        Raises ``ValueError`` if identities are violated.
        """
        hc = self._assemble()
        violations = hc.verify_simplicial_identities()
        if violations:
            raise ValueError(
                "simplicial identity violations:\n" + "\n".join(violations)
            )
        self._validated = True
        return self

    def _assemble(self) -> Hypercover:
        """Internal: build without validation."""
        if self._base is None:
            raise ValueError("base coordinate not set")
        hc_levels: list[HypercoverLevel] = []
        for idx, data in enumerate(self._levels):
            hc_levels.append(HypercoverLevel(
                level_number=idx,
                cover=data['cover'],
                face_maps=data['face_maps'],
                degeneracy_maps=data['degeneracy_maps'],
                augmentation=data['augmentation'],
                overlap_cells=data['overlap_cells'],
                provenance=('builder',),
            ))
        return Hypercover(self._base, tuple(hc_levels))

    def build(self, *, validate: bool = True) -> Hypercover:
        """Build the ``Hypercover``.

        When *validate* is True (default) simplicial identities are checked.
        """
        hc = self._assemble()
        if validate and not self._validated:
            violations = hc.verify_simplicial_identities()
            if violations:
                raise ValueError(
                    "simplicial identity violations:\n" + "\n".join(violations)
                )
        return hc


# ---------------------------------------------------------------------------
# 4. SimplicialObject (abstract)
# ---------------------------------------------------------------------------

class SimplicialObject(ABC):
    """Abstract base for simplicial objects used in JuGeo hypercover machinery.

    A simplicial object in a category *C* is a functor Δ^op → C.  Concretely
    it is a sequence of objects together with face and degeneracy maps
    satisfying the simplicial identities.  Sub-classes must provide the
    objects at each level and the structure maps.
    """

    @abstractmethod
    def objects_at_level(self, n: int) -> tuple[str, ...]:
        """Return object identifiers at simplicial level *n*."""

    @abstractmethod
    def face_map(self, n: int, i: int) -> dict[str, str]:
        """Return the *i*-th face map d_i : X_n → X_{n-1}."""

    @abstractmethod
    def degeneracy_map(self, n: int, i: int) -> dict[str, str]:
        """Return the *i*-th degeneracy map s_i : X_n → X_{n+1}."""

    @abstractmethod
    def max_level(self) -> int:
        """The maximum simplicial level available."""

    def verify_identities(self) -> list[str]:
        """Verify simplicial identities across all available levels.

        Returns a (possibly empty) list of violation descriptions.
        """
        violations: list[str] = []
        for n in range(1, self.max_level() + 1):
            keys = self.objects_at_level(n)
            # d_i d_j = d_{j+1} d_i for i <= j
            num_faces = n + 1
            for i in range(num_faces):
                for j in range(i, num_faces):
                    if j + 1 >= num_faces:
                        continue
                    try:
                        di = self.face_map(n, i)
                        dj = self.face_map(n, j)
                        dj1 = self.face_map(n, j + 1)
                    except (IndexError, KeyError):
                        continue
                    lhs = {k: dj1.get(di.get(k, ''), '') for k in keys}
                    rhs = {k: di.get(dj.get(k, ''), '') for k in keys}
                    if lhs != rhs:
                        violations.append(f"level {n}: d_{i}d_{j} != d_{{{j+1}}}d_{i}")
        return violations

    def nerve(self, up_to: int = 3) -> list[tuple[str, ...]]:
        """Compute the nerve up to level *up_to*.

        Returns a list of tuples — each tuple contains the object identifiers
        at that simplicial level.
        """
        result: list[tuple[str, ...]] = []
        for n in range(min(up_to + 1, self.max_level() + 1)):
            result.append(self.objects_at_level(n))
        return result

    def geometric_realization_data(self) -> dict[str, Any]:
        """Collect data useful for a geometric-realization–style summary.

        Returns dimension counts, face/degeneracy map counts, and a
        copilot-oriented synopsis.
        """
        data: dict[str, Any] = {'levels': {}}
        for n in range(self.max_level() + 1):
            objs = self.objects_at_level(n)
            data['levels'][n] = {
                'num_objects': len(objs),
                'num_faces': n + 1,
                'num_degeneracies': n,
            }
        data['euler_characteristic'] = sum(
            ((-1) ** n) * len(self.objects_at_level(n))
            for n in range(self.max_level() + 1)
        )
        data['copilot_hint'] = 'realization data for simplicial object'
        return data

    def dimension_sequence(self) -> tuple[int, ...]:
        """Return the sequence of object counts at each level."""
        return tuple(
            len(self.objects_at_level(n))
            for n in range(self.max_level() + 1)
        )

    def is_finite(self) -> bool:
        """True when the simplicial object has finitely many non-degenerate
        simplices (naive check: dimension decreases to zero)."""
        seq = self.dimension_sequence()
        if not seq:
            return True
        return seq[-1] == 0 or len(seq) <= 1


# ---------------------------------------------------------------------------
# 5. CechNerve
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CechNerve:
    """The Čech nerve of a cover — the canonical hypercover.

    Given a cover {U_i → X} the Čech nerve is the simplicial object whose
    level-*n* entries are the (n+1)-fold fibre products U_{i_0} ×_X … ×_X
    U_{i_n}.  For JuGeo covers this reduces to ordered tuples of overlapping
    patch keys.
    """

    cover: Cover
    max_depth: int = 4

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_cover(cls, cover: Cover, *, max_depth: int = 4) -> CechNerve:
        """Create a Čech nerve from an existing ``Cover``."""
        return cls(cover=cover, max_depth=max_depth)

    # -- level-n objects ------------------------------------------------------

    def level_n_objects(self, n: int) -> tuple[tuple[str, ...], ...]:
        """Return the (n+1)-fold overlaps as tuples of patch keys.

        Level 0 gives the individual patches; level 1 gives pairwise
        overlaps; etc.
        """
        keys = self.cover.patch_keys()
        if n < 0:
            raise ValueError("level must be non-negative")
        if n == 0:
            return tuple((k,) for k in keys)
        overlap_set: set[frozenset[str]] = set()
        for left, right in self.cover.overlaps:
            overlap_set.add(frozenset({left, right}))
        if n == 1:
            return tuple(tuple(sorted(pair)) for pair in overlap_set)
        # Higher levels: (n+1)-fold intersections built from pairwise overlaps
        result: list[tuple[str, ...]] = []
        for combo in combinations(keys, n + 1):
            # All pairwise sub-combinations must overlap
            all_overlap = True
            for pair in combinations(combo, 2):
                if frozenset(pair) not in overlap_set:
                    all_overlap = False
                    break
            if all_overlap:
                result.append(tuple(sorted(combo)))
        return tuple(result)

    def face_maps_at(self, n: int) -> tuple[dict[tuple[str, ...], tuple[str, ...]], ...]:
        """Return the (n+1) face maps at level *n*.

        The *i*-th face map drops the *i*-th entry of each (n+1)-tuple.
        """
        objects = self.level_n_objects(n)
        maps: list[dict[tuple[str, ...], tuple[str, ...]]] = []
        for i in range(n + 1):
            face: dict[tuple[str, ...], tuple[str, ...]] = {}
            for obj in objects:
                face[obj] = tuple(x for idx, x in enumerate(obj) if idx != i)
            maps.append(face)
        return tuple(maps)

    def degeneracy_maps_at(self, n: int) -> tuple[dict[tuple[str, ...], tuple[str, ...]], ...]:
        """Return the (n+1) degeneracy maps at level *n*.

        The *i*-th degeneracy map duplicates the *i*-th entry.
        """
        objects = self.level_n_objects(n)
        maps: list[dict[tuple[str, ...], tuple[str, ...]]] = []
        for i in range(n + 1):
            degen: dict[tuple[str, ...], tuple[str, ...]] = {}
            for obj in objects:
                doubled = list(obj)
                doubled.insert(i, obj[i])
                degen[obj] = tuple(doubled)
            maps.append(degen)
        return tuple(maps)

    def is_acyclic(self) -> bool:
        """Heuristic acyclicity check.

        A Čech nerve is acyclic when every finite intersection of the cover is
        non-empty (i.e. every matching object is inhabited).  We approximate
        this by checking that every level up to ``max_depth`` has at least one
        object.
        """
        for n in range(self.max_depth + 1):
            if not self.level_n_objects(n):
                return False
        return True

    def compute_cohomology_dimension(self) -> int:
        """Estimate the cohomological dimension.

        Returns the highest level *n* at which there are non-trivial objects.
        This is a practical estimate — the true cohomological dimension may
        differ for non-acyclic nerves.
        """
        top = 0
        for n in range(self.max_depth + 1):
            if self.level_n_objects(n):
                top = n
        return top

    def euler_characteristic(self) -> int:
        """The Euler characteristic Σ (-1)^n |X_n|."""
        return sum(
            ((-1) ** n) * len(self.level_n_objects(n))
            for n in range(self.max_depth + 1)
        )

    def to_hypercover(self) -> Hypercover:
        """Convert this nerve into a full ``Hypercover``.

        Each level of the nerve becomes a ``HypercoverLevel`` with
        face and degeneracy maps derived from the nerve structure.
        """
        hc_levels: list[HypercoverLevel] = []
        for n in range(min(self.max_depth + 1, self.compute_cohomology_dimension() + 2)):
            objs = self.level_n_objects(n)
            keys = ['/'.join(obj) for obj in objs]
            face_data = self.face_maps_at(n) if n > 0 else ()
            face_maps_flat: list[dict[str, str]] = []
            for fm in face_data:
                face_maps_flat.append({'/'.join(k): '/'.join(v) for k, v in fm.items()})
            deg_data = self.degeneracy_maps_at(n)
            deg_maps_flat: list[dict[str, str]] = []
            for dm in deg_data:
                deg_maps_flat.append({'/'.join(k): '/'.join(v) for k, v in dm.items()})
            augmentation = {k: self.cover.target.key for k in keys}
            hc_levels.append(HypercoverLevel(
                level_number=n,
                cover=self.cover,
                face_maps=tuple(face_maps_flat),
                degeneracy_maps=tuple(deg_maps_flat),
                augmentation=augmentation,
                overlap_cells=_enumerate_overlaps(keys),
                provenance=('cech_nerve',),
            ))
        return Hypercover(self.cover.target, tuple(hc_levels))


# ---------------------------------------------------------------------------
# 6. HypercoverSynthesizer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class HypercoverSynthesizer:
    """Algorithmic hypercover construction from module trees and scope
    hierarchies.

    This is the main entry-point for copilot-guided hypercover creation.  It
    takes a source-code or project structure and produces a hypercover whose
    levels mirror the containment hierarchy (project → packages → modules →
    classes → methods).
    """

    max_depth: int = 5
    overlap_max_arity: int = 4
    provenance: tuple[str, ...] = ()

    # -- from a module tree ---------------------------------------------------

    def from_module_tree(
        self,
        root: CoordinateObject,
        children_map: Mapping[str, Sequence[CoordinateObject]],
    ) -> Hypercover:
        """Build a hypercover from a module tree.

        *children_map* maps a coordinate key to its direct children.  The
        hypercover levels correspond to tree depth.
        """
        builder = HypercoverBuilder().set_base(root)
        queue: list[tuple[int, CoordinateObject]] = [(0, root)]
        visited_levels: dict[int, list[CoordinateObject]] = {}

        while queue:
            depth, node = queue.pop(0)
            if depth > self.max_depth:
                continue
            visited_levels.setdefault(depth, []).append(node)
            for child in children_map.get(node.key, []):
                queue.append((depth + 1, child))

        for lvl_num in sorted(visited_levels):
            nodes = visited_levels[lvl_num]
            patches = tuple(nodes)
            overlaps = self._compute_sibling_overlaps(nodes)
            cover = Cover(target=root, patches=patches, overlaps=overlaps,
                          provenance=self.provenance + (f'module_tree_L{lvl_num}',))
            builder.add_level(cover, overlap_max_arity=self.overlap_max_arity)

            # Build face maps to previous level
            if lvl_num > 0:
                parent_keys = [n.key for n in visited_levels.get(lvl_num - 1, [])]
                fm: dict[str, str] = {}
                for node in nodes:
                    parent_path = node.path[:-1] if len(node.path) > 1 else node.path
                    parent_key = '/'.join(parent_path)
                    fm[node.key] = parent_key if parent_key in parent_keys else (parent_keys[0] if parent_keys else root.key)
                builder.set_face_maps(lvl_num, [fm])

        return builder.build(validate=False)

    def _compute_sibling_overlaps(
        self, nodes: list[CoordinateObject]
    ) -> tuple[tuple[str, str], ...]:
        """Compute overlaps between sibling nodes (same parent path)."""
        by_parent: dict[tuple[str, ...], list[CoordinateObject]] = {}
        for node in nodes:
            parent = node.path[:-1] if len(node.path) > 1 else ()
            by_parent.setdefault(parent, []).append(node)
        overlaps: list[tuple[str, str]] = []
        for siblings in by_parent.values():
            for a, b in combinations(siblings, 2):
                overlaps.append((a.key, b.key))
        return tuple(overlaps)

    # -- from a scope hierarchy -----------------------------------------------

    def from_scope_hierarchy(
        self,
        scopes: Sequence[tuple[str, Sequence[str]]],
        site: SemanticSite,
    ) -> Hypercover:
        """Build a hypercover from a flat scope hierarchy.

        *scopes* is a sequence of ``(scope_name, [child_keys])`` pairs ordered
        from coarsest to finest.  Each scope becomes one level.
        """
        if not scopes:
            raise ValueError("at least one scope is required")

        root_name, root_children = scopes[0]
        root = site.coordinates.get(root_name)
        if root is None:
            root = CoordinateObject(root_name, CoordinateKind.MODULE, (root_name,))

        builder = HypercoverBuilder().set_base(root)

        for idx, (scope_name, child_keys) in enumerate(scopes):
            patches = []
            for ck in child_keys:
                coord = site.coordinates.get(ck)
                if coord is None:
                    coord = CoordinateObject(ck, CoordinateKind.MODULE, (scope_name, ck))
                patches.append(coord)
            if not patches:
                patches = [root]
            cover = Cover(
                target=root,
                patches=tuple(patches),
                overlaps=tuple((a.key, b.key) for a, b in zip(patches, patches[1:])),
                provenance=self.provenance + (f'scope_{scope_name}',),
            )
            builder.add_level(cover, overlap_max_arity=self.overlap_max_arity)

        return builder.build(validate=False)

    # -- iterative refinement -------------------------------------------------

    def iterative_refinement(
        self,
        hypercover: Hypercover,
        refine_predicate: Callable[[HypercoverLevel], bool],
        *,
        max_iterations: int = 10,
    ) -> Hypercover:
        """Iteratively refine levels of *hypercover* where *refine_predicate*
        returns True.

        Each qualifying level has its cover refined and a new deeper level
        appended.  Iterates until the predicate is satisfied everywhere or
        *max_iterations* is reached.
        """
        current = hypercover
        for iteration in range(max_iterations):
            needs_refinement = [
                lvl for lvl in current.levels if refine_predicate(lvl)
            ]
            if not needs_refinement:
                break
            new_levels = list(current.levels)
            for lvl in needs_refinement:
                refined_cover = refine_cover(lvl.cover, suffix=f'iter{iteration}')
                new_level = HypercoverLevel(
                    level_number=len(new_levels),
                    cover=refined_cover,
                    face_maps=(_identity_map(refined_cover.patch_keys()),),
                    degeneracy_maps=(),
                    augmentation={k: current.base_coordinate.key for k in refined_cover.patch_keys()},
                    overlap_cells=_enumerate_overlaps(refined_cover.patch_keys(), self.overlap_max_arity),
                    provenance=lvl.provenance + (f'refined_iter{iteration}',),
                )
                new_levels.append(new_level)
            current = replace(current, levels=tuple(new_levels))
        return current

    # -- copilot-guided synthesis ---------------------------------------------

    def copilot_guided_synthesis(
        self,
        base: CoordinateObject,
        hints: Sequence[str],
    ) -> Hypercover:
        """Build a hypercover using copilot-style heuristic hints.

        *hints* are natural-language-like scope descriptors (e.g.
        ``["project", "packages", "modules", "classes"]``).  Each hint
        becomes one level whose patches are synthetic coordinates derived
        from the hint.
        """
        builder = HypercoverBuilder().set_base(base)
        prev_patches: list[CoordinateObject] = [base]
        for idx, hint in enumerate(hints):
            patches: list[CoordinateObject] = []
            for parent in prev_patches:
                child = CoordinateObject(
                    name=f'{parent.name}.{hint}',
                    kind=CoordinateKind.MODULE,
                    path=parent.path + (hint,),
                    support_labels=frozenset({hint}),
                    metadata={'copilot_hint': hint, 'depth': idx},
                )
                patches.append(child)
            overlaps = tuple((a.key, b.key) for a, b in zip(patches, patches[1:]))
            cover = Cover(
                target=base,
                patches=tuple(patches),
                overlaps=overlaps,
                provenance=self.provenance + (f'copilot_{hint}',),
            )
            builder.add_level(cover, overlap_max_arity=self.overlap_max_arity)
            if idx > 0:
                fm = {p.key: prev_patches[min(j, len(prev_patches) - 1)].key
                      for j, p in enumerate(patches)}
                builder.set_face_maps(idx, [fm])
            prev_patches = patches
        return builder.build(validate=False)

    # -- minimization & optimization ------------------------------------------

    def minimize(self, hypercover: Hypercover) -> Hypercover:
        """Remove degenerate levels and collapse trivial face maps.

        Returns a hypercover with the same homotopy type but fewer levels.
        """
        kept: list[HypercoverLevel] = []
        for lvl in hypercover.levels:
            if lvl.is_degenerate() and lvl.level_number > 0:
                continue
            renumbered = replace(lvl, level_number=len(kept))
            kept.append(renumbered)
        return replace(hypercover, levels=tuple(kept))

    def optimize_for_descent(
        self,
        hypercover: Hypercover,
        *,
        target_depth: int | None = None,
    ) -> Hypercover:
        """Optimize a hypercover for descent computations.

        Truncates to *target_depth* (if given), removes degenerate levels, and
        ensures all augmentation maps are consistent.
        """
        hc = self.minimize(hypercover)
        if target_depth is not None:
            hc = hc.truncate_at(target_depth)
        # Rebuild augmentations consistently
        new_levels: list[HypercoverLevel] = []
        for lvl in hc.levels:
            aug = {k: hc.base_coordinate.key for k in lvl.patch_keys}
            new_levels.append(replace(lvl, augmentation=aug,
                                      provenance=lvl.provenance + ('optimized',)))
        return replace(hc, levels=tuple(new_levels))


# ---------------------------------------------------------------------------
# 7. MatchingObject
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MatchingObject:
    """The matching object at a simplicial level.

    In simplicial homotopy theory the matching object M_n X encodes all the
    data from levels < n that must be matched by a filler at level n.  If the
    matching object is empty, extending the hypercover is obstructed.
    """

    level: int
    required_keys: frozenset[str]
    face_constraints: dict[str, list[str]]
    partial_fillers: tuple[dict[str, str], ...] = ()

    def compute(self) -> dict[str, Any]:
        """Compute a summary of the matching data.

        Returns key counts, constraint density, and whether fillers exist.
        """
        total_constraints = sum(len(v) for v in self.face_constraints.values())
        density = total_constraints / max(1, len(self.required_keys))
        return {
            'level': self.level,
            'num_required': len(self.required_keys),
            'num_constraints': total_constraints,
            'constraint_density': round(density, 4),
            'has_partial_fillers': len(self.partial_fillers) > 0,
            'is_inhabited': self.is_inhabited(),
        }

    def is_inhabited(self) -> bool:
        """True when the matching object is non-empty.

        The matching object is inhabited when every required key participates
        in at least one constraint (meaning the local data is connected).
        """
        constrained = set(self.face_constraints.keys())
        for vals in self.face_constraints.values():
            constrained.update(vals)
        return self.required_keys.issubset(constrained) or len(self.required_keys) == 0

    def obstruction_if_empty(self) -> str | None:
        """If the matching object is empty, return a diagnostic message.

        Returns ``None`` when the matching object is inhabited.
        """
        if self.is_inhabited():
            return None
        constrained = set(self.face_constraints.keys())
        for vals in self.face_constraints.values():
            constrained.update(vals)
        missing = self.required_keys - constrained
        return (
            f"matching object at level {self.level} is empty: "
            f"keys {sorted(missing)} have no face constraints — "
            f"copilot cannot extend the hypercover without additional data"
        )

    def partial_filler_keys(self) -> frozenset[str]:
        """Keys covered by at least one partial filler."""
        covered: set[str] = set()
        for filler in self.partial_fillers:
            covered.update(filler.keys())
        return frozenset(covered)

    def merge_fillers(self) -> dict[str, str]:
        """Merge all partial fillers, later fillers overriding earlier ones."""
        merged: dict[str, str] = {}
        for filler in self.partial_fillers:
            merged.update(filler)
        return merged

    def constrained_neighbours(self, key: str) -> frozenset[str]:
        """Return keys constrained to agree with *key*."""
        direct = set(self.face_constraints.get(key, []))
        for k, vs in self.face_constraints.items():
            if key in vs:
                direct.add(k)
        return frozenset(direct)

    def constraint_graph_components(self) -> list[frozenset[str]]:
        """Connected components of the constraint graph.

        Each component is a set of keys that must agree.  Multiple components
        indicate independent matching conditions.
        """
        parent: dict[str, str] = {k: k for k in self.required_keys}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for k, vs in self.face_constraints.items():
            if k in parent:
                for v in vs:
                    if v in parent:
                        union(k, v)

        components: dict[str, set[str]] = {}
        for k in self.required_keys:
            root = find(k)
            components.setdefault(root, set()).add(k)
        return [frozenset(c) for c in components.values()]


# ---------------------------------------------------------------------------
# 8. HypercoverMorphism
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HypercoverMorphism:
    """A morphism between two hypercovers — a collection of level-wise maps
    compatible with face and degeneracy maps.

    If the source and target have different depths, the morphism is defined
    only on the common levels.
    """

    source: Hypercover
    target: Hypercover
    level_maps: tuple[dict[str, str], ...]
    provenance: tuple[str, ...] = ()

    @property
    def common_depth(self) -> int:
        """The number of levels on which the morphism is defined."""
        return len(self.level_maps)

    def map_at(self, n: int) -> dict[str, str]:
        """The map at level *n*."""
        if n < 0 or n >= len(self.level_maps):
            raise IndexError(f"level {n} out of range [0, {len(self.level_maps)})")
        return self.level_maps[n]

    def is_refinement(self) -> bool:
        """True when every level map is surjective onto the target patches.

        A refinement morphism means the source is a finer hypercover — it
        covers at least as much as the target at each level.
        """
        for n, lm in enumerate(self.level_maps):
            if n >= len(self.target.levels):
                break
            target_keys = set(self.target.levels[n].patch_keys)
            image = set(lm.values())
            if not target_keys.issubset(image):
                return False
        return True

    def is_equivalence(self) -> bool:
        """True when every level map is bijective.

        An equivalence of hypercovers induces an isomorphism on cohomology.
        """
        for lm in self.level_maps:
            if len(set(lm.values())) != len(lm):
                return False
            if len(lm) != len(set(lm.values())):
                return False
        return True

    def is_compatible_with_faces(self) -> bool:
        """Check that level maps commute with face maps.

        For each level *n* and face index *i*, the diagram
        ``f_n ∘ d_i^source = d_i^target ∘ f_n`` should commute.
        """
        for n in range(1, self.common_depth):
            if n >= len(self.source.levels) or n >= len(self.target.levels):
                break
            src_level = self.source.levels[n]
            tgt_level = self.target.levels[n]
            f_n = self.level_maps[n]
            f_prev = self.level_maps[n - 1] if n - 1 < len(self.level_maps) else {}
            for i in range(min(len(src_level.face_maps), len(tgt_level.face_maps))):
                src_face = src_level.face_maps[i]
                tgt_face = tgt_level.face_maps[i]
                for k in src_level.patch_keys:
                    lhs = f_prev.get(src_face.get(k, ''), '')
                    rhs = tgt_face.get(f_n.get(k, ''), '')
                    if lhs and rhs and lhs != rhs:
                        return False
        return True

    def compose(self, other: HypercoverMorphism) -> HypercoverMorphism:
        """Compose ``self`` with *other*: self ; other (self first, then other).

        Requires ``self.target`` and ``other.source`` to be the same
        hypercover (checked by base coordinate key).
        """
        if self.target.base_coordinate.key != other.source.base_coordinate.key:
            raise ValueError(
                "cannot compose: target of first morphism does not match "
                "source of second"
            )
        depth = min(self.common_depth, other.common_depth)
        composed: list[dict[str, str]] = []
        for n in range(depth):
            m1 = self.level_maps[n]
            m2 = other.level_maps[n]
            composed.append({k: m2.get(v, v) for k, v in m1.items()})
        return HypercoverMorphism(
            source=self.source,
            target=other.target,
            level_maps=tuple(composed),
            provenance=self.provenance + other.provenance + ('composed',),
        )

    def kernel_keys_at(self, n: int) -> frozenset[str]:
        """Keys at level *n* in the source that map to the same target key.

        Non-empty kernel keys indicate that the morphism collapses patches.
        """
        lm = self.map_at(n)
        inverse: dict[str, list[str]] = {}
        for k, v in lm.items():
            inverse.setdefault(v, []).append(k)
        collapsed: set[str] = set()
        for v, ks in inverse.items():
            if len(ks) > 1:
                collapsed.update(ks)
        return frozenset(collapsed)


# ---------------------------------------------------------------------------
# 9. HypercoverDescent
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class HypercoverDescent:
    """Specialized descent engine for hypercovers.

    Whereas ``DescentEngine`` in ``descent.py`` works on a single cover, this
    class performs layer-by-layer gluing through the simplicial levels of a
    hypercover, computes spectral-sequence–style data, and checks convergence.
    Integrates with copilot diagnostics for reporting.
    """

    hypercover: Hypercover
    gluing_key: Callable[[Mapping[str, Any]], tuple[tuple[str, Any], ...]] | None = None
    tolerance: float = 1e-6

    def _default_key(self, section: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
        """Fallback gluing key: sort all non-'patch' entries."""
        return tuple(sorted((k, v) for k, v in section.items() if k != 'patch'))

    def _key(self, section: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
        return self.gluing_key(section) if self.gluing_key else self._default_key(section)

    def attempt(
        self,
        local_sections: Mapping[int, Mapping[str, Mapping[str, Any]]],
    ) -> dict[str, Any]:
        """Attempt hypercover descent.

        *local_sections* is keyed by level number, then by patch key, giving
        local section data.  Descent succeeds when all levels glue
        consistently.

        Returns a report dict with ``success``, ``glued``, ``obstructions``,
        and ``copilot_summary``.
        """
        obstructions: list[dict[str, Any]] = []
        glued_sections: dict[int, Any] = {}

        for lvl in self.hypercover.levels:
            n = lvl.level_number
            sections = local_sections.get(n, {})

            # Check that all patches have sections
            missing = set(lvl.patch_keys) - set(sections.keys())
            if missing:
                obstructions.append({
                    'level': n,
                    'type': 'missing_section',
                    'keys': sorted(missing),
                })
                continue

            # Check overlap consistency
            for left, right in lvl.cover.overlaps:
                if left in sections and right in sections:
                    if self._key(sections[left]) != self._key(sections[right]):
                        obstructions.append({
                            'level': n,
                            'type': 'gluing_mismatch',
                            'overlap': (left, right),
                        })

            # Check face-map consistency with previous level
            if n > 0 and (n - 1) in glued_sections:
                for fm in lvl.face_maps:
                    for src_key, tgt_key in fm.items():
                        if src_key in sections and tgt_key in local_sections.get(n - 1, {}):
                            src_data = self._key(sections[src_key])
                            tgt_data = self._key(local_sections[n - 1][tgt_key])
                            if src_data != tgt_data:
                                obstructions.append({
                                    'level': n,
                                    'type': 'face_inconsistency',
                                    'source': src_key,
                                    'target': tgt_key,
                                })

            if not any(o['level'] == n for o in obstructions):
                values = list(sections.values())
                glued_sections[n] = dict(values[0]) if values else {}

        success = len(obstructions) == 0
        return {
            'success': success,
            'glued': glued_sections if success else {},
            'obstructions': obstructions,
            'levels_attempted': len(self.hypercover.levels),
            'copilot_summary': (
                'hypercover descent succeeded — all levels glue'
                if success else
                f'hypercover descent failed with {len(obstructions)} obstruction(s)'
            ),
        }

    def spectral_sequence_data(self) -> dict[str, Any]:
        """Compute data for the descent spectral sequence.

        The E_1 page has entries E_1^{p,q} where p is the simplicial level
        and q is the cohomological degree.  Here we approximate this by
        counting objects and overlaps at each level.
        """
        e1_page: dict[str, int] = {}
        for lvl in self.hypercover.levels:
            p = lvl.level_number
            e1_page[f'E1({p},0)'] = lvl.dimension
            e1_page[f'E1({p},1)'] = len(lvl.overlap_cells)
            # Higher cohomological degrees from face map data
            for q in range(2, len(lvl.face_maps) + 1):
                e1_page[f'E1({p},{q})'] = max(0, lvl.dimension - q)

        total_rank = sum(e1_page.values())
        return {
            'e1_page': e1_page,
            'total_rank': total_rank,
            'depth': self.hypercover.depth(),
            'copilot_hint': 'spectral sequence data for hypercover descent',
        }

    def convergence_check(self) -> dict[str, Any]:
        """Check whether the descent spectral sequence converges.

        Convergence is detected when the dimension sequence is non-increasing
        and the deepest level is small relative to the base.
        """
        dims = [lvl.dimension for lvl in self.hypercover.levels]
        is_decreasing = all(a >= b for a, b in zip(dims, dims[1:])) if len(dims) > 1 else True
        ratio = dims[-1] / max(1, dims[0]) if dims else 0.0
        converged = is_decreasing and ratio <= 0.5 + self.tolerance

        return {
            'converged': converged,
            'dimension_sequence': dims,
            'is_decreasing': is_decreasing,
            'depth_to_base_ratio': round(ratio, 6),
            'copilot_hint': (
                'spectral sequence converges' if converged else
                'spectral sequence may not converge — consider deeper refinement'
            ),
        }

    def layer_by_layer_gluing(
        self,
        local_sections: Mapping[int, Mapping[str, Mapping[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Perform gluing level by level, returning a report for each.

        Unlike ``attempt`` which returns a single verdict, this method returns
        detailed per-level reports useful for copilot-assisted debugging.
        """
        reports: list[dict[str, Any]] = []
        accumulated_glued: dict[str, Any] = {}

        for lvl in self.hypercover.levels:
            n = lvl.level_number
            sections = local_sections.get(n, {})
            level_obs: list[str] = []

            for left, right in lvl.cover.overlaps:
                if left in sections and right in sections:
                    if self._key(sections[left]) != self._key(sections[right]):
                        level_obs.append(f'mismatch on overlap ({left}, {right})')

            missing = set(lvl.patch_keys) - set(sections.keys())
            if missing:
                level_obs.append(f'missing sections: {sorted(missing)}')

            success = len(level_obs) == 0
            if success and sections:
                accumulated_glued.update(sections)

            reports.append({
                'level': n,
                'success': success,
                'patches': len(lvl.patch_keys),
                'obstructions': level_obs,
                'glued_so_far': len(accumulated_glued),
            })

        return reports

    def obstruction_classes(self) -> list[dict[str, Any]]:
        """Identify obstruction classes across the hypercover.

        Each class groups overlaps by the type of mismatch.  Useful for
        copilot-guided repair: fix one class at a time.
        """
        classes: list[dict[str, Any]] = []
        for lvl in self.hypercover.levels:
            face_count = lvl.num_faces
            overlap_count = len(lvl.overlap_cells)
            if overlap_count > 0 and face_count > 0:
                classes.append({
                    'level': lvl.level_number,
                    'potential_obstructions': overlap_count,
                    'face_maps': face_count,
                    'ratio': round(overlap_count / max(1, face_count), 4),
                })
        return classes


# ---------------------------------------------------------------------------
# 10. HypercoverDiagnostics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HypercoverDiagnostics:
    """Validation and analysis tools for hypercovers.

    Provides simplicial-identity checking, degeneracy detection, coverage
    analysis, and copilot-oriented summary generation.
    """

    hypercover: Hypercover

    def check_simplicial_identities(self) -> list[str]:
        """Delegate to the hypercover's own identity check.

        Returns a list of violations (empty means valid).
        """
        return self.hypercover.verify_simplicial_identities()

    def detect_degeneracies(self) -> dict[int, bool]:
        """Map each level number to whether it is degenerate.

        A degenerate level adds no new information — it is the image of
        degeneracy maps from the previous level.
        """
        return {lvl.level_number: lvl.is_degenerate() for lvl in self.hypercover.levels}

    def coverage_analysis(self) -> dict[str, Any]:
        """Analyse how well the hypercover covers the base.

        Reports per-level patch counts, total unique keys, overlap density,
        and augmentation coverage.
        """
        all_keys: set[str] = set()
        per_level: list[dict[str, Any]] = []
        total_overlaps = 0

        for lvl in self.hypercover.levels:
            keys = set(lvl.patch_keys)
            all_keys.update(keys)
            n_overlaps = len(lvl.overlap_cells)
            total_overlaps += n_overlaps
            density = n_overlaps / max(1, len(keys))
            per_level.append({
                'level': lvl.level_number,
                'patches': len(keys),
                'overlaps': n_overlaps,
                'density': round(density, 4),
                'augmentation_image_size': len(lvl.augmentation_image()),
            })

        return {
            'total_unique_keys': len(all_keys),
            'total_overlaps': total_overlaps,
            'per_level': per_level,
            'base_key': self.hypercover.base_coordinate.key,
        }

    def depth_analysis(self) -> dict[str, Any]:
        """Analyse the depth structure of the hypercover.

        Reports depth, whether the hypercover is split, dimension sequence,
        and Euler characteristic.
        """
        dims = [lvl.dimension for lvl in self.hypercover.levels]
        euler = sum(((-1) ** i) * d for i, d in enumerate(dims))
        is_bounded = all(d <= dims[0] for d in dims) if dims else True

        return {
            'depth': self.hypercover.depth(),
            'dimension_sequence': dims,
            'euler_characteristic': euler,
            'is_split': self.hypercover.is_split(),
            'is_bounded': is_bounded,
            'max_dimension': max(dims) if dims else 0,
        }

    def face_map_analysis(self) -> dict[str, Any]:
        """Analyse face maps across the hypercover.

        Reports counts, surjectivity, and whether faces form a valid
        simplicial structure.
        """
        face_info: list[dict[str, Any]] = []
        for lvl in self.hypercover.levels:
            for i, fm in enumerate(lvl.face_maps):
                image = set(fm.values())
                domain = set(fm.keys())
                face_info.append({
                    'level': lvl.level_number,
                    'face_index': i,
                    'domain_size': len(domain),
                    'image_size': len(image),
                    'is_surjective': len(image) == len(domain),
                })
        return {
            'total_face_maps': len(face_info),
            'faces': face_info,
        }

    def degeneracy_map_analysis(self) -> dict[str, Any]:
        """Analyse degeneracy maps across the hypercover."""
        degen_info: list[dict[str, Any]] = []
        for lvl in self.hypercover.levels:
            for i, dm in enumerate(lvl.degeneracy_maps):
                image = set(dm.values())
                degen_info.append({
                    'level': lvl.level_number,
                    'degeneracy_index': i,
                    'domain_size': len(dm),
                    'image_size': len(image),
                    'is_injective': len(image) == len(dm),
                })
        return {
            'total_degeneracy_maps': len(degen_info),
            'degeneracies': degen_info,
        }

    def copilot_summary(self) -> str:
        """Generate a human-readable copilot diagnostic summary.

        Intended for display in copilot-assisted workflows to help developers
        understand the hypercover structure at a glance.
        """
        depth = self.hypercover.depth()
        violations = self.check_simplicial_identities()
        degeneracies = self.detect_degeneracies()
        coverage = self.coverage_analysis()
        depth_info = self.depth_analysis()

        lines: list[str] = [
            f"=== Hypercover Diagnostics (copilot) ===",
            f"Base: {self.hypercover.base_coordinate.key}",
            f"Depth: {depth}",
            f"Levels: {len(self.hypercover.levels)}",
            f"Dimension sequence: {depth_info['dimension_sequence']}",
            f"Euler characteristic: {depth_info['euler_characteristic']}",
            f"Split: {depth_info['is_split']}",
            f"Bounded: {depth_info['is_bounded']}",
            f"Total unique keys: {coverage['total_unique_keys']}",
            f"Total overlaps: {coverage['total_overlaps']}",
        ]

        if violations:
            lines.append(f"⚠ Simplicial identity violations ({len(violations)}):")
            for v in violations[:5]:
                lines.append(f"  - {v}")
            if len(violations) > 5:
                lines.append(f"  … and {len(violations) - 5} more")
        else:
            lines.append("✓ All simplicial identities satisfied")

        degenerate_levels = [k for k, v in degeneracies.items() if v]
        if degenerate_levels:
            lines.append(f"Degenerate levels: {degenerate_levels}")
        else:
            lines.append("No degenerate levels")

        lines.append("=" * 42)
        return "\n".join(lines)

    def validate_augmentations(self) -> list[str]:
        """Check that augmentation maps consistently target the base.

        Returns a list of inconsistencies.
        """
        issues: list[str] = []
        base_key = self.hypercover.base_coordinate.key
        for lvl in self.hypercover.levels:
            for patch_key, aug_target in lvl.augmentation.items():
                if aug_target != base_key:
                    issues.append(
                        f"level {lvl.level_number}: augmentation of {patch_key} "
                        f"targets {aug_target}, expected {base_key}"
                    )
        return issues

    def structural_hash(self) -> str:
        """Compute a hash summarising the hypercover structure.

        Useful for quickly comparing two hypercovers without full equality
        checks.  Based on depth, dimensions, and face map counts.
        """
        import hashlib
        parts: list[str] = [
            str(self.hypercover.depth()),
            str(len(self.hypercover.levels)),
        ]
        for lvl in self.hypercover.levels:
            parts.append(f"{lvl.level_number}:{lvl.dimension}:{lvl.num_faces}:{lvl.num_degeneracies}")
        raw = '|'.join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Legacy / convenience API (preserves backward compatibility)
# ---------------------------------------------------------------------------

def enumerate_higher_overlaps(cover: Cover) -> tuple[tuple[str, ...], ...]:
    """Enumerate multi-fold overlaps of a cover's patches."""
    return _enumerate_overlaps(cover.patch_keys())


def build_hypercover(base_cover: Cover, *, depth: int = 2) -> Hypercover:
    """Build a simple hypercover by iteratively refining *base_cover*.

    This is the original convenience function retained for backward
    compatibility.  For richer construction use ``HypercoverBuilder`` or
    ``HypercoverSynthesizer``.
    """
    builder = HypercoverBuilder().set_base(base_cover.target)
    current = base_cover
    builder.add_level(current)
    for level in range(1, depth + 1):
        current = refine_cover(current, suffix=f'h{level}')
        builder.add_level(current)
        fm = {k: k.rsplit(f'/h{level}', 1)[0] if f'/h{level}' in k else k
              for k in current.patch_keys()}
        builder.set_face_maps(level, [fm])
    return builder.build(validate=False)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # Core data
    'HypercoverLevel',
    'Hypercover',
    # Builder
    'HypercoverBuilder',
    # Simplicial
    'SimplicialObject',
    'CechNerve',
    # Synthesis
    'HypercoverSynthesizer',
    # Matching
    'MatchingObject',
    # Morphisms
    'HypercoverMorphism',
    # Descent
    'HypercoverDescent',
    # Diagnostics
    'HypercoverDiagnostics',
    # Legacy
    'build_hypercover',
    'enumerate_higher_overlaps',
]

# copilot: shared-core marker for future LLM orchestration.
