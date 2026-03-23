"""
Local-to-global structure for Theory2.tex Ch4. Implements how local data defined
over patches of a cover assembles into global data via compatibility and gluing.

copilot: shared-core marker

This module provides the computational infrastructure for the local-to-global
principle in sheaf theory: data defined consistently on overlapping patches of a
cover extends uniquely to global data.  The sheaf axiom—separation and gluing—is
the organising principle; every public class in this module either enforces it or
diagnoses its failure.

Key abstractions:

- ``LocalSection``: an element of F(U) for a single coordinate patch U.
- ``GlobalSection``: an assembled element of F(X) built from compatible locals.
- ``CoverCompatibility``: tracks pairwise overlap consistency across a cover.
- ``LocalToGlobalMap``: the assembly functor that maps a collection of local
  sections to a global section (or reports the obstruction).

Module-level entry points ``assemble_global_section``,
``verify_locality_principle``, and ``compute_local_restrictions`` provide
convenient wrappers for the most frequent descent operations.

References: Theory2.tex Chapter 4, §4.1–§4.4.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import combinations
from typing import Any

from jugeo.geometry.covers import Cover, CoverMember, OverlapDatum
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
)

__all__ = [
    "SectionKind",
    "LocalToGlobalStrategy",
    "LocalSection",
    "GlobalSection",
    "CoverCompatibility",
    "LocalToGlobalMap",
    "assemble_global_section",
    "verify_locality_principle",
    "compute_local_restrictions",
]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SectionKind(str, Enum):
    """Discriminant for the assembly stage of a section.

    copilot: shared-core marker

    Attributes:
        LOCAL: Data is confined to a single patch with no global guarantee.
        GLOBAL: Data has been assembled successfully across all patches of a
            cover and satisfies the gluing axiom.
        PARTIAL_GLOBAL: An intermediate state: some patches have
            representatives but the full gluing has not been verified.
    """

    LOCAL = "local"
    GLOBAL = "global"
    PARTIAL_GLOBAL = "partial_global"


class LocalToGlobalStrategy(str, Enum):
    """Strategy that governs how ``LocalToGlobalMap.apply`` assembles sections.

    copilot: shared-core marker

    Attributes:
        EAGER: Attempt assembly immediately upon receiving all local sections.
            Uses a single pass and fails fast on the first incompatibility.
        EXHAUSTIVE: Try all admissible orderings of the local sections and
            return the best (highest-trust) assembled result.
        ITERATIVE: Start from a seed section and iteratively merge compatible
            neighbours, looping until no further progress is possible.
        LAZY: Defer the actual assembly until the global section is first
            accessed; useful when construction cost is high.
    """

    EAGER = "eager"
    EXHAUSTIVE = "exhaustive"
    ITERATIVE = "iterative"
    LAZY = "lazy"


# ---------------------------------------------------------------------------
# LocalSection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocalSection:
    """A section of semantic data defined over a single coordinate patch.

    copilot: shared-core marker

    ``LocalSection`` is the fundamental unit of local data in the descent
    machinery.  Each instance carries a payload associated with a specific
    ``Coordinate``, a kind discriminant, a trust score in ``[0, 1]``, and a
    provenance chain recording every operation that produced it.

    In the sheaf-theoretic sense, ``LocalSection`` corresponds to an element
    of F(U) for an open set U in the site; ``transport_along`` and ``restrict``
    implement the restriction maps of the presheaf.

    Fields:
        coordinate: The semantic patch over which this section is defined.
        data: The payload—arbitrary semantic data associated with the section.
        kind: Whether the section is local, global, or partially assembled.
        trust_score: A float in ``[0.0, 1.0]`` quantifying epistemic
            confidence; decays under restriction and transport.
        provenance: Ordered tuple of operation labels recording the
            derivation history of this section.
    """

    coordinate: Coordinate
    data: Any
    kind: SectionKind = SectionKind.LOCAL
    trust_score: float = 1.0
    provenance: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Core sheaf operations
    # ------------------------------------------------------------------

    def restrict(self, sub_coordinate: Coordinate) -> LocalSection:
        """Restrict this section to a descendant patch.

        copilot: shared-core marker

        Implements the presheaf axiom ρ_{UV}: F(V) → F(U) for U ⊆ V.  The
        trust score undergoes a small multiplicative decay (0.97) to reflect
        the information loss inherent in restriction to a sub-context.

        Args:
            sub_coordinate: A ``Coordinate`` that must be a strict descendant
                of ``self.coordinate`` in the component hierarchy.

        Returns:
            A new ``LocalSection`` over *sub_coordinate* carrying the same
            data payload with kind reset to ``LOCAL`` and updated provenance.

        Raises:
            ValueError: If *sub_coordinate* is not a descendant of
                ``self.coordinate``.
        """
        if not self.coordinate.is_prefix_of(sub_coordinate):
            raise ValueError(
                f"Cannot restrict '{self.coordinate.name}' → '{sub_coordinate.name}': "
                f"sub_coordinate is not a descendant in the component hierarchy."
            )
        restricted_trust = max(0.0, self.trust_score * 0.97)
        return replace(
            self,
            coordinate=sub_coordinate,
            kind=SectionKind.LOCAL,
            trust_score=restricted_trust,
            provenance=self.provenance + (f"restrict({sub_coordinate.name})",),
        )

    def is_compatible_with(self, other: LocalSection) -> bool:
        """Decide whether two sections can be glued on their coordinate overlap.

        copilot: shared-core marker

        Two sections s ∈ F(U) and t ∈ F(V) are *compatible* if their
        restrictions to U ∩ V agree.  In practice this method checks:

        1. Structural type consistency (both payloads share the same Python
           type, or one section is already GLOBAL).
        2. For ``dict`` payloads: no conflicting values on shared keys.
        3. For ``list`` payloads: neither list contradicts the other as a
           prefix sequence.
        4. Combined trust (arithmetic mean) ≥ 0.5.

        Args:
            other: The section to compare against.

        Returns:
            ``True`` if the sections can be glued without introducing a
            contradiction; ``False`` otherwise.
        """
        # A GLOBAL section is vacuously compatible with anything
        if self.kind == SectionKind.GLOBAL or other.kind == SectionKind.GLOBAL:
            return True

        # Structural type must match
        if type(self.data) is not type(other.data):
            return False

        # Dict payloads: check for key-level conflicts
        if isinstance(self.data, dict) and isinstance(other.data, dict):
            shared_keys = set(self.data.keys()) & set(other.data.keys())
            for k in shared_keys:
                if self.data[k] != other.data[k]:
                    return False

        # List payloads: one must be a prefix of the other
        if isinstance(self.data, list) and isinstance(other.data, list):
            shorter, longer = (
                (self.data, other.data)
                if len(self.data) <= len(other.data)
                else (other.data, self.data)
            )
            if shorter and longer[: len(shorter)] != shorter:
                return False

        # Combined trust threshold
        combined = (self.trust_score + other.trust_score) / 2.0
        if combined < 0.5:
            return False

        return True

    def transport_along(self, morphism: Morphism) -> LocalSection:
        """Transport this section along a morphism to a new coordinate.

        copilot: shared-core marker

        In sheaf theory this is the pullback f*(s) of s ∈ F(V) to F(U) along
        f: U → V.  The trust decay depends on morphism kind:

        - ``RESTRICTION``: 5 % decay (some information is projected away).
        - ``INCLUSION``: no decay (inclusions are lossless embeddings).
        - ``TRANSPORT``: 10 % decay (general lossy transport).
        - ``REFINEMENT``: 3 % decay (mild reindexing with minimal loss).

        Args:
            morphism: A ``Morphism`` whose *source* equals ``self.coordinate``.

        Returns:
            A new ``LocalSection`` over ``morphism.target`` with adjusted
            trust score and an extended provenance chain.

        Raises:
            ValueError: If ``morphism.source`` does not match
                ``self.coordinate``.
        """
        if morphism.source != self.coordinate:
            raise ValueError(
                f"Morphism source '{morphism.source.name}' does not match "
                f"section coordinate '{self.coordinate.name}'."
            )
        decay_table: dict[MorphismKind, float] = {
            MorphismKind.RESTRICTION: 0.95,
            MorphismKind.INCLUSION: 1.00,
            MorphismKind.TRANSPORT: 0.90,
            MorphismKind.REFINEMENT: 0.97,
        }
        decay = decay_table.get(morphism.kind, 0.95)
        transported_trust = max(0.0, self.trust_score * decay)
        label = morphism.label or morphism.kind.value
        return replace(
            self,
            coordinate=morphism.target,
            trust_score=transported_trust,
            provenance=self.provenance + (f"transport_along({label})",),
        )

    def extend_to_global(self, cover: Cover) -> GlobalSection | None:
        """Attempt to extend this section to a global section via *cover*.

        copilot: shared-core marker

        For each patch in *cover* this method first tries direct restriction
        (if the patch is a descendant of ``self.coordinate``), then falls back
        to synthetic transport via a ``TRANSPORT`` morphism.  Returns ``None``
        if the cover has no members or if extension to any patch fails
        irreparably.

        Args:
            cover: The ``Cover`` over which to assemble the global section.

        Returns:
            A ``GlobalSection`` if extension succeeds on every patch, or
            ``None`` if *cover* is empty or extension fails.
        """
        if not cover.members:
            return None

        local_reps: dict[str, LocalSection] = {}
        gluing: dict[tuple[str, str], Any] = {}

        for member in cover.members:
            patch_key = member.source_key
            src_coord = member.source_coordinate

            # Prefer direct restriction when possible
            if self.coordinate.is_prefix_of(src_coord):
                try:
                    local_reps[patch_key] = self.restrict(src_coord)
                    continue
                except ValueError:
                    pass

            # Fall back to synthetic transport morphism
            synth = Morphism(
                source=self.coordinate,
                target=src_coord,
                kind=MorphismKind.TRANSPORT,
                label=f"ext_to_{patch_key}",
            )
            try:
                local_reps[patch_key] = self.transport_along(synth)
            except ValueError:
                return None

        # Build gluing data from the cover's overlap information
        for od in cover.overlap_data:
            lk = od.left_member.source_key
            rk = od.right_member.source_key
            gluing[(lk, rk)] = {
                "overlap_coord": od.overlap_coordinate.name,
                "status": "glued",
            }

        return GlobalSection(
            base_cover=cover,
            local_representatives=local_reps,
            gluing_map=gluing,
            is_unique=True,
            provenance=self.provenance + ("extend_to_global",),
        )

    def validate(self) -> bool:
        """Validate the internal consistency of this section.

        copilot: shared-core marker

        Performs the following checks in order:

        1. ``coordinate`` has at least one component (not the empty root).
        2. ``trust_score`` lies in the closed interval ``[0.0, 1.0]``.
        3. ``kind`` is a genuine ``SectionKind`` member.
        4. For ``LOCAL`` and ``GLOBAL`` kinds the data payload is not ``None``.

        Returns:
            ``True`` if every check passes; ``False`` on the first failure.
        """
        if not self.coordinate.components:
            return False
        if not (0.0 <= self.trust_score <= 1.0):
            return False
        if not isinstance(self.kind, SectionKind):
            return False
        if self.kind in (SectionKind.LOCAL, SectionKind.GLOBAL) and self.data is None:
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        """Serialise this section to a plain dictionary suitable for logging.

        copilot: shared-core marker

        The dictionary is JSON-safe: all leaf values are strings, numbers,
        lists, or nested dicts.  The data payload is represented by its type
        name and a truncated repr (≤ 256 chars) rather than its full value, to
        avoid inadvertently serialising large objects.

        Returns:
            Dict with keys ``coordinate``, ``kind``, ``trust_score``,
            ``provenance``, ``data_type``, ``data_repr``.
        """
        return {
            "coordinate": self.coordinate.serialize(),
            "kind": self.kind.value,
            "trust_score": round(self.trust_score, 6),
            "provenance": list(self.provenance),
            "data_type": type(self.data).__name__,
            "data_repr": repr(self.data)[:256],
        }

    def summary(self) -> str:
        """Return a one-line human-readable description of this section.

        copilot: shared-core marker

        Includes the coordinate name, kind, trust score (3 d.p.), and the
        last three provenance steps for compact display in logs and REPLs.

        Returns:
            A concise single-line string representation.
        """
        recent = list(self.provenance[-3:])
        prov_str = " > ".join(recent) if recent else "(none)"
        return (
            f"LocalSection[{self.kind.value}] "
            f"@ '{self.coordinate.name}' "
            f"trust={self.trust_score:.3f} "
            f"prov=({prov_str})"
        )

    def merge_with(self, other: LocalSection) -> LocalSection:
        """Merge this section with a compatible section on their common ancestor.

        copilot: shared-core marker

        Merging is the inverse of restriction: given s ∈ F(U) and t ∈ F(V),
        if they are compatible their merge lives in F(common_ancestor(U, V)).

        Data merge rules:

        - ``dict`` payloads: key-wise union; ``self`` takes precedence on
          conflicts.
        - ``list`` payloads: concatenation with order-preserving deduplication.
        - All other payloads: ``self.data`` wins.

        The resulting trust score is the *minimum* of both scores (conservative
        worst-case assumption).  The resulting kind is ``GLOBAL`` when both
        sections share the exact same coordinate, ``PARTIAL_GLOBAL`` otherwise.

        Args:
            other: A ``LocalSection`` that is compatible with ``self``.

        Returns:
            A new ``LocalSection`` anchored at the common ancestor coordinate.

        Raises:
            ValueError: If ``self.is_compatible_with(other)`` returns ``False``.
        """
        if not self.is_compatible_with(other):
            raise ValueError(
                f"Cannot merge incompatible sections:\n"
                f"  self:  {self.summary()}\n"
                f"  other: {other.summary()}"
            )
        common_coord = self.coordinate.common_ancestor(other.coordinate)

        # Merge payloads
        if isinstance(self.data, dict) and isinstance(other.data, dict):
            merged_data: Any = {**other.data, **self.data}
        elif isinstance(self.data, list) and isinstance(other.data, list):
            seen: set[Any] = set()
            merged_list: list[Any] = []
            for item in self.data + other.data:
                try:
                    if item not in seen:
                        seen.add(item)
                        merged_list.append(item)
                except TypeError:
                    # unhashable items always appended
                    merged_list.append(item)
            merged_data = merged_list
        else:
            merged_data = self.data

        # Kind depends on whether the common ancestor equals both coords
        if self.coordinate == other.coordinate:
            result_kind = SectionKind.GLOBAL
        else:
            result_kind = SectionKind.PARTIAL_GLOBAL

        return replace(
            self,
            coordinate=common_coord,
            data=merged_data,
            kind=result_kind,
            trust_score=min(self.trust_score, other.trust_score),
            provenance=self.provenance + (f"merge_with({other.coordinate.name})",),
        )


# ---------------------------------------------------------------------------
# GlobalSection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlobalSection:
    """An assembled global section across all patches of a cover.

    copilot: shared-core marker

    ``GlobalSection`` corresponds to an element of the limit F(X) =
    lim_{U ∈ 𝒰} F(U) over a cover 𝒰.  It stores a local representative for
    every patch and the gluing data (transition functions) that witness
    compatibility on each overlap.

    Instances are produced by ``assemble_global_section``,
    ``LocalSection.extend_to_global``, or ``LocalToGlobalMap.apply``.

    Fields:
        base_cover: The ``Cover`` over which the section is defined.
        local_representatives: Map from patch key (``member.source_key``) to
            the corresponding ``LocalSection``.
        gluing_map: Map from ``(patch_i_key, patch_j_key)`` pairs to arbitrary
            gluing data witnessing agreement on the overlap.
        is_unique: ``True`` when the gluing is known to produce a unique
            global section (i.e. the sheaf condition holds fully).
        provenance: Derivation history as an ordered tuple of labels.
    """

    base_cover: Cover
    local_representatives: dict[str, LocalSection] = field(default_factory=dict)
    gluing_map: dict[tuple[str, str], Any] = field(default_factory=dict)
    is_unique: bool = True
    provenance: tuple[str, ...] = ()

    def restrict_to_patch(self, patch_id: str) -> LocalSection:
        """Return the local representative for *patch_id*.

        copilot: shared-core marker

        Args:
            patch_id: A key from ``local_representatives`` (typically the
                ``source_key`` of a ``CoverMember``).

        Returns:
            The ``LocalSection`` stored for *patch_id*.

        Raises:
            KeyError: If *patch_id* is not present in the section.
        """
        if patch_id not in self.local_representatives:
            available = list(self.local_representatives.keys())
            raise KeyError(
                f"Patch '{patch_id}' not found in GlobalSection. "
                f"Available patches: {available}"
            )
        return self.local_representatives[patch_id]

    def verify_descent_condition(self) -> bool:
        """Check that all pairwise overlap conditions (the Čech cocycle) hold.

        copilot: shared-core marker

        The descent condition requires that for every pair of patches (i, j),
        the restriction of section_i to the overlap agrees with the restriction
        of section_j to the overlap.  This method approximates that check by
        verifying pairwise compatibility using ``LocalSection.is_compatible_with``.

        Returns:
            ``True`` if all pairwise compatibility checks pass; ``False`` if
            any pair is incompatible.
        """
        reps = self.local_representatives
        ids = list(reps.keys())
        for i, j in combinations(ids, 2):
            if not reps[i].is_compatible_with(reps[j]):
                return False
        return True

    def is_consistent(self) -> bool:
        """Check internal consistency: all representatives and the cover agree.

        copilot: shared-core marker

        Consistency requires:

        1. Every stored representative passes its own ``validate()`` check.
        2. The descent condition holds (all pairs are compatible).
        3. The gluing_map keys reference valid patch IDs.

        Returns:
            ``True`` if all consistency checks pass.
        """
        reps = self.local_representatives
        # 1. Individual validation
        for sec in reps.values():
            if not sec.validate():
                return False
        # 2. Pairwise descent condition
        if not self.verify_descent_condition():
            return False
        # 3. Gluing map references valid patches
        valid_ids = set(reps.keys())
        for lk, rk in self.gluing_map.keys():
            if lk not in valid_ids or rk not in valid_ids:
                return False
        return True

    def refine(self, new_cover: Cover) -> GlobalSection:
        """Pullback this global section to a finer cover.

        copilot: shared-core marker

        Given a refinement ``new_cover`` of ``self.base_cover``, produces a
        new ``GlobalSection`` where each patch of *new_cover* obtains a local
        representative by transporting from the corresponding representative in
        ``self``.  Patches of *new_cover* that share a coordinate prefix with
        an existing representative are handled by restriction; others use
        synthetic transport.

        Args:
            new_cover: A ``Cover`` that refines ``self.base_cover``.

        Returns:
            A new ``GlobalSection`` over *new_cover*.
        """
        new_reps: dict[str, LocalSection] = {}
        new_gluing: dict[tuple[str, str], Any] = {}

        for member in new_cover.members:
            pk = member.source_key
            src = member.source_coordinate

            # Try to match against an existing representative
            best_rep: LocalSection | None = None
            for existing_sec in self.local_representatives.values():
                if existing_sec.coordinate.is_prefix_of(src):
                    best_rep = existing_sec
                    break

            if best_rep is None:
                # Fall back to common-ancestor representative
                for existing_sec in self.local_representatives.values():
                    ancestor = existing_sec.coordinate.common_ancestor(src)
                    if ancestor.components:
                        best_rep = existing_sec
                        break

            if best_rep is not None:
                synth = Morphism(
                    source=best_rep.coordinate,
                    target=src,
                    kind=MorphismKind.RESTRICTION,
                    label=f"refine_to_{pk}",
                )
                try:
                    new_reps[pk] = best_rep.transport_along(synth)
                except ValueError:
                    new_reps[pk] = replace(
                        best_rep,
                        coordinate=src,
                        provenance=best_rep.provenance + (f"refine_to_{pk}",),
                    )
            else:
                # No suitable representative found; create a minimal placeholder
                new_reps[pk] = LocalSection(
                    coordinate=src,
                    data=None,
                    kind=SectionKind.PARTIAL_GLOBAL,
                    trust_score=0.0,
                    provenance=("refine_placeholder",),
                )

        # Rebuild gluing map for new cover overlaps
        for od in new_cover.overlap_data:
            lk = od.left_member.source_key
            rk = od.right_member.source_key
            new_gluing[(lk, rk)] = {
                "overlap_coord": od.overlap_coordinate.name,
                "status": "refined",
            }

        return GlobalSection(
            base_cover=new_cover,
            local_representatives=new_reps,
            gluing_map=new_gluing,
            is_unique=self.is_unique,
            provenance=self.provenance + ("refine",),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialise this global section to a plain dictionary.

        copilot: shared-core marker

        Returns:
            Dict with keys ``base_cover_target``, ``patch_count``,
            ``is_unique``, ``provenance``, ``representatives`` (a list of
            per-patch summaries), and ``gluing_pair_count``.
        """
        return {
            "base_cover_target": self.base_cover.target.name,
            "patch_count": len(self.local_representatives),
            "is_unique": self.is_unique,
            "provenance": list(self.provenance),
            "representatives": [s.as_dict() for s in self.local_representatives.values()],
            "gluing_pair_count": len(self.gluing_map),
        }

    def summary(self) -> str:
        """Return a one-line description of this global section.

        copilot: shared-core marker

        Returns:
            A concise string including patch count, uniqueness flag, and
            the last three provenance steps.
        """
        n = len(self.local_representatives)
        unique_str = "unique" if self.is_unique else "non-unique"
        recent = list(self.provenance[-3:])
        prov_str = " > ".join(recent) if recent else "(none)"
        return (
            f"GlobalSection[{unique_str}] "
            f"over '{self.base_cover.target.name}' "
            f"patches={n} "
            f"prov=({prov_str})"
        )

    def patch_ids(self) -> list[str]:
        """Return the list of patch identifiers for this section.

        copilot: shared-core marker

        Returns:
            Sorted list of patch key strings from ``local_representatives``.
        """
        return sorted(self.local_representatives.keys())

    def overlap_data(self, i: str, j: str) -> Any:
        """Return the gluing datum for the overlap between patches *i* and *j*.

        copilot: shared-core marker

        Looks up ``(i, j)`` and ``(j, i)`` in the gluing map (both orderings
        are tried) and returns the stored gluing witness.

        Args:
            i: Key of the first patch.
            j: Key of the second patch.

        Returns:
            The gluing datum, or ``None`` if no explicit gluing data exists for
            this pair.
        """
        datum = self.gluing_map.get((i, j))
        if datum is None:
            datum = self.gluing_map.get((j, i))
        return datum


# ---------------------------------------------------------------------------
# CoverCompatibility
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoverCompatibility:
    """Tracks pairwise overlap-consistency for a collection of local sections.

    copilot: shared-core marker

    ``CoverCompatibility`` is a *mutable* helper that computes and caches
    whether each pair of sections (indexed by patch ID) is compatible on their
    coordinate overlap.  The cached table is populated lazily on first access
    and can be invalidated by directly clearing ``compatibility_table``.

    Fields:
        cover: The ``Cover`` supplying the overlap geometry.
        sections: Map from patch ID to ``LocalSection``.
        compatibility_table: Cached map from ``(i, j)`` pairs to bool.
        checked_at: UNIX timestamp recorded when ``check_all`` was last run.
    """

    cover: Cover
    sections: dict[str, LocalSection] = field(default_factory=dict)
    compatibility_table: dict[tuple[str, str], bool] = field(default_factory=dict)
    checked_at: float = field(default_factory=time.time)

    def check_pair(self, i: str, j: str) -> bool:
        """Check compatibility between patch *i* and patch *j*.

        copilot: shared-core marker

        The result is cached in ``compatibility_table`` under the canonical
        key ``(min(i,j), max(i,j))``.  Identical patches are trivially
        compatible.

        Args:
            i: Key of the first patch.
            j: Key of the second patch.

        Returns:
            ``True`` if the sections are compatible on their overlap.

        Raises:
            KeyError: If *i* or *j* are not present in ``self.sections``.
        """
        if i not in self.sections:
            raise KeyError(f"Patch '{i}' not in sections.")
        if j not in self.sections:
            raise KeyError(f"Patch '{j}' not in sections.")
        if i == j:
            return True
        canonical = (min(i, j), max(i, j))
        if canonical not in self.compatibility_table:
            result = self.sections[i].is_compatible_with(self.sections[j])
            self.compatibility_table[canonical] = result
        return self.compatibility_table[canonical]

    def check_all(self) -> dict[tuple[str, str], bool]:
        """Compute compatibility for every pair of patches.

        copilot: shared-core marker

        Iterates over all ``C(n, 2)`` pairs where n = len(sections), caches
        each result, updates ``checked_at``, and returns the full table.

        Returns:
            Dict mapping ``(i, j)`` pairs (canonical order) to bool.
        """
        ids = sorted(self.sections.keys())
        for i, j in combinations(ids, 2):
            self.check_pair(i, j)
        self.checked_at = time.time()
        return dict(self.compatibility_table)

    def find_violations(self) -> list[tuple[str, str]]:
        """Return all pairs of patches whose sections are incompatible.

        copilot: shared-core marker

        Ensures ``check_all`` has been run first, then filters the table for
        entries where the value is ``False``.

        Returns:
            Sorted list of ``(i, j)`` pairs representing incompatible overlaps.
        """
        self.check_all()
        return sorted(k for k, v in self.compatibility_table.items() if not v)

    def is_fully_compatible(self) -> bool:
        """Return ``True`` if every pairwise check passes.

        copilot: shared-core marker

        Runs ``check_all`` if the table has not been populated yet, then
        checks for the absence of violations.

        Returns:
            ``True`` iff the compatibility table contains no ``False`` entries.
        """
        if not self.compatibility_table:
            self.check_all()
        return all(self.compatibility_table.values())

    def compatible_subset(self) -> list[str]:
        """Find a maximal subset of patches that are mutually compatible.

        copilot: shared-core marker

        Uses a greedy removal strategy: compute the patch with the most
        violations and remove it iteratively until the remaining set is fully
        compatible.

        Returns:
            Sorted list of patch IDs forming a mutually compatible subset.
        """
        self.check_all()
        remaining = set(self.sections.keys())

        while True:
            # Count violations per patch in current remaining set
            violation_counts: dict[str, int] = {p: 0 for p in remaining}
            any_violation = False
            for i, j in combinations(sorted(remaining), 2):
                canon = (min(i, j), max(i, j))
                if not self.compatibility_table.get(canon, True):
                    violation_counts[i] += 1
                    violation_counts[j] += 1
                    any_violation = True

            if not any_violation:
                break

            # Remove the patch with the most violations (ties: alphabetical)
            worst = max(violation_counts, key=lambda p: (violation_counts[p], p))
            remaining.discard(worst)

        return sorted(remaining)

    def summary(self) -> str:
        """Return a human-readable summary of the compatibility status.

        copilot: shared-core marker

        Returns:
            Multi-line string with patch count, violation count, and checked
            timestamp.
        """
        n_patches = len(self.sections)
        n_pairs = len(self.compatibility_table)
        n_violations = self.violation_count()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.checked_at))
        return (
            f"CoverCompatibility: {n_patches} patches, "
            f"{n_pairs} pairs checked, "
            f"{n_violations} violations, "
            f"checked_at={ts}"
        )

    def as_overlap_graph(self) -> dict[str, list[str]]:
        """Represent compatibility as an undirected adjacency list.

        copilot: shared-core marker

        Each patch maps to the list of patches it is compatible with
        (excluding itself).  Patches for which ``check_all`` has not been run
        will have incomplete neighbour lists.

        Returns:
            Dict mapping patch ID to sorted list of compatible patch IDs.
        """
        self.check_all()
        ids = sorted(self.sections.keys())
        graph: dict[str, list[str]] = {p: [] for p in ids}
        for i, j in combinations(ids, 2):
            canon = (min(i, j), max(i, j))
            if self.compatibility_table.get(canon, False):
                graph[i].append(j)
                graph[j].append(i)
        # Sort neighbour lists
        for p in graph:
            graph[p].sort()
        return graph

    def violation_count(self) -> int:
        """Return the number of incompatible patch pairs.

        copilot: shared-core marker

        Returns:
            Count of pairs ``(i, j)`` in ``compatibility_table`` where the
            value is ``False``.
        """
        return sum(1 for v in self.compatibility_table.values() if not v)


# ---------------------------------------------------------------------------
# LocalToGlobalMap
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LocalToGlobalMap:
    """The assembly functor mapping local sections to a global section.

    copilot: shared-core marker

    ``LocalToGlobalMap`` orchestrates the full descent procedure: it takes a
    list of ``LocalSection`` objects defined over patches of a ``Cover`` and
    tries to produce a single compatible ``GlobalSection``.  The assembly
    strategy is controlled by the ``strategy`` field.

    Fields:
        cover: The ``Cover`` supplying the patch geometry.
        source_sections: Input local sections (need not be pre-indexed by patch).
        target_global: Output global section if assembly succeeded.
        strategy: One of the ``LocalToGlobalStrategy`` value strings.
        log: Mutable list of diagnostic messages appended during ``apply``.
    """

    cover: Cover
    source_sections: list[LocalSection] = field(default_factory=list)
    target_global: GlobalSection | None = None
    strategy: str = LocalToGlobalStrategy.EAGER.value
    log: list[str] = field(default_factory=list)

    def apply(self) -> GlobalSection | None:
        """Assemble source sections into a global section.

        copilot: shared-core marker

        Dispatches to the appropriate strategy implementation:

        - ``EAGER``: Index sections by coordinate key, check pairwise
          compatibility, and assemble in a single pass.
        - ``EXHAUSTIVE``: Same as EAGER but re-attempts after reordering on
          failure, trying up to ``min(n!, 6)`` orderings for n ≤ 3.
        - ``ITERATIVE``: Seeds with the highest-trust section and iteratively
          merges compatible neighbours.
        - ``LAZY``: Returns ``None`` immediately; assembly is deferred.

        Updates ``self.target_global`` and ``self.log`` in place.

        Returns:
            A ``GlobalSection`` on success, or ``None`` if assembly fails or
            the LAZY strategy is in use.
        """
        self.log.clear()
        strat = LocalToGlobalStrategy(self.strategy)

        if strat == LocalToGlobalStrategy.LAZY:
            self.log.append("LAZY strategy: assembly deferred.")
            return None

        if not self.source_sections:
            self.log.append("No source sections provided.")
            return None

        if strat == LocalToGlobalStrategy.EAGER:
            result = self._assemble_eager()
        elif strat == LocalToGlobalStrategy.EXHAUSTIVE:
            result = self._assemble_exhaustive()
        else:  # ITERATIVE
            result = self._assemble_iterative()

        self.target_global = result
        return result

    def _assemble_eager(self) -> GlobalSection | None:
        """Single-pass eager assembly."""
        indexed = self._index_sections()
        compat = CoverCompatibility(cover=self.cover, sections=indexed)
        violations = compat.find_violations()
        if violations:
            self.log.append(f"EAGER: {len(violations)} compatibility violations found.")
            return None

        gluing: dict[tuple[str, str], Any] = {}
        for od in self.cover.overlap_data:
            lk = od.left_member.source_key
            rk = od.right_member.source_key
            gluing[(lk, rk)] = {"overlap": od.overlap_coordinate.name}

        self.log.append(f"EAGER: assembled {len(indexed)} patches.")
        return GlobalSection(
            base_cover=self.cover,
            local_representatives=indexed,
            gluing_map=gluing,
            is_unique=True,
            provenance=("local_to_global_eager",),
        )

    def _assemble_exhaustive(self) -> GlobalSection | None:
        """Try multiple orderings and return the best result."""
        from itertools import permutations

        sections = list(self.source_sections)
        n = min(len(sections), 3)
        best: GlobalSection | None = None
        best_trust = -1.0

        for perm in permutations(range(len(sections)), n):
            subset = [sections[k] for k in perm]
            indexed: dict[str, LocalSection] = {}
            for sec in subset:
                indexed[sec.coordinate.key] = sec
            compat = CoverCompatibility(cover=self.cover, sections=indexed)
            if compat.is_fully_compatible():
                avg_trust = sum(s.trust_score for s in indexed.values()) / max(len(indexed), 1)
                if avg_trust > best_trust:
                    best_trust = avg_trust
                    gluing: dict[tuple[str, str], Any] = {
                        (od.left_member.source_key, od.right_member.source_key): {}
                        for od in self.cover.overlap_data
                    }
                    best = GlobalSection(
                        base_cover=self.cover,
                        local_representatives=indexed,
                        gluing_map=gluing,
                        is_unique=True,
                        provenance=("local_to_global_exhaustive",),
                    )

        if best is None:
            self.log.append("EXHAUSTIVE: no compatible ordering found.")
        else:
            self.log.append(f"EXHAUSTIVE: best trust={best_trust:.3f}.")
        return best

    def _assemble_iterative(self) -> GlobalSection | None:
        """Seed from highest-trust section and grow iteratively."""
        if not self.source_sections:
            return None

        # Seed: highest-trust section
        seed = max(self.source_sections, key=lambda s: s.trust_score)
        accumulated: dict[str, LocalSection] = {seed.coordinate.key: seed}

        remaining = [s for s in self.source_sections if s is not seed]
        progress = True
        while progress and remaining:
            progress = False
            for sec in list(remaining):
                compat = True
                for held in accumulated.values():
                    if not held.is_compatible_with(sec):
                        compat = False
                        break
                if compat:
                    accumulated[sec.coordinate.key] = sec
                    remaining.remove(sec)
                    progress = True

        self.log.append(
            f"ITERATIVE: merged {len(accumulated)} sections, "
            f"{len(remaining)} incompatible sections excluded."
        )
        gluing: dict[tuple[str, str], Any] = {
            (od.left_member.source_key, od.right_member.source_key): {}
            for od in self.cover.overlap_data
        }
        return GlobalSection(
            base_cover=self.cover,
            local_representatives=accumulated,
            gluing_map=gluing,
            is_unique=len(remaining) == 0,
            provenance=("local_to_global_iterative",),
        )

    def _index_sections(self) -> dict[str, LocalSection]:
        """Index source sections by coordinate key, deduplicating by trust."""
        indexed: dict[str, LocalSection] = {}
        for sec in self.source_sections:
            key = sec.coordinate.key
            if key not in indexed or sec.trust_score > indexed[key].trust_score:
                indexed[key] = sec
        return indexed

    def check_injectivity(self) -> bool:
        """Return ``True`` if distinct source sections map to distinct patches.

        copilot: shared-core marker

        Injectivity means no two source sections share the same coordinate
        key; duplicated keys would indicate redundant or conflicting data.

        Returns:
            ``True`` if all source coordinates are distinct.
        """
        keys = [s.coordinate.key for s in self.source_sections]
        return len(keys) == len(set(keys))

    def check_surjectivity(self) -> bool:
        """Return ``True`` if every patch in the cover has a representative.

        copilot: shared-core marker

        Surjectivity means every member of ``self.cover`` has a corresponding
        source section, ensuring no patch is left uncovered.

        Returns:
            ``True`` if every cover member's source key appears among the
            source sections.
        """
        cover_keys = {m.source_key for m in self.cover.members}
        section_keys = {s.coordinate.key for s in self.source_sections}
        return cover_keys.issubset(section_keys)

    def is_isomorphism(self) -> bool:
        """Return ``True`` if the map is both injective and surjective.

        copilot: shared-core marker

        Returns:
            ``True`` iff ``check_injectivity()`` and ``check_surjectivity()``
            both return ``True``.
        """
        return self.check_injectivity() and self.check_surjectivity()

    def kernel(self) -> list[LocalSection]:
        """Return sections that contribute no data to the global assembly.

        copilot: shared-core marker

        A section is in the kernel if its payload is falsy (``None``, empty
        dict, empty list, empty string, ``0``, etc.), indicating it carries no
        substantive information.

        Returns:
            List of source sections with falsy data payloads.
        """
        return [s for s in self.source_sections if not s.data]

    def image(self) -> list[LocalSection]:
        """Return sections that successfully contribute to the global section.

        copilot: shared-core marker

        A section is in the image if it passes ``validate()`` and its
        coordinate key appears in ``target_global.local_representatives``
        (if assembly has been attempted).

        Returns:
            List of source sections that are present in the assembled global
            section, or all valid sections if assembly has not been attempted.
        """
        valid = [s for s in self.source_sections if s.validate()]
        if self.target_global is None:
            return valid
        represented = set(self.target_global.local_representatives.keys())
        return [s for s in valid if s.coordinate.key in represented]

    def failure_report(self) -> dict[str, Any]:
        """Produce a diagnostic report when assembly fails or is incomplete.

        copilot: shared-core marker

        Returns:
            Dict with keys ``strategy``, ``source_count``, ``assembled``,
            ``injective``, ``surjective``, ``kernel_count``, ``image_count``,
            ``log``.
        """
        return {
            "strategy": self.strategy,
            "source_count": len(self.source_sections),
            "assembled": self.target_global is not None,
            "injective": self.check_injectivity(),
            "surjective": self.check_surjectivity(),
            "kernel_count": len(self.kernel()),
            "image_count": len(self.image()),
            "log": list(self.log),
        }

    def summary(self) -> str:
        """Return a one-line description of this map.

        copilot: shared-core marker

        Returns:
            A concise string including strategy, section counts, and assembly
            status.
        """
        status = "assembled" if self.target_global is not None else "not assembled"
        return (
            f"LocalToGlobalMap[{self.strategy}] "
            f"src={len(self.source_sections)} "
            f"cover='{self.cover.target.name}' "
            f"status={status}"
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def assemble_global_section(
    sections: list[LocalSection],
    cover: Cover,
    *,
    strategy: LocalToGlobalStrategy = LocalToGlobalStrategy.EAGER,
) -> GlobalSection | None:
    """Assemble a list of local sections into a global section over *cover*.

    copilot: shared-core marker

    This is the primary public entry point for the descent assembly.  It
    constructs a ``LocalToGlobalMap``, runs it with the requested strategy,
    and returns the resulting ``GlobalSection`` or ``None`` on failure.

    Args:
        sections: Local sections to assemble.  They need not be pre-indexed.
        cover: The ``Cover`` supplying the patch structure and overlap geometry.
        strategy: Assembly strategy; defaults to ``EAGER``.

    Returns:
        A ``GlobalSection`` if assembly succeeds, or ``None`` if compatibility
        violations prevent gluing.

    Example::

        gs = assemble_global_section(my_sections, my_cover)
        if gs is None:
            print("Gluing failed – check compatibility.")
        else:
            print(gs.summary())
    """
    mapper = LocalToGlobalMap(
        cover=cover,
        source_sections=list(sections),
        strategy=strategy.value,
    )
    return mapper.apply()


def verify_locality_principle(
    section: GlobalSection,
    cover: Cover,
) -> bool:
    """Verify that a global section satisfies the locality principle on *cover*.

    copilot: shared-core marker

    The *locality principle* (separation axiom) states that if two global
    sections agree on every patch of a cover, they are equal.  This function
    checks the weaker single-section form: the section's local representatives
    are mutually compatible and their data is internally consistent.

    Args:
        section: The global section under test.
        cover: The cover providing the patch geometry.

    Returns:
        ``True`` if the section satisfies the locality principle.
    """
    if not section.is_consistent():
        return False

    # Verify that every cover member is represented
    cover_keys = {m.source_key for m in cover.members}
    rep_keys = set(section.local_representatives.keys())
    if not cover_keys.issubset(rep_keys):
        return False

    # Check descent condition
    if not section.verify_descent_condition():
        return False

    return True


def compute_local_restrictions(
    global_section: GlobalSection,
    cover: Cover,
) -> dict[str, LocalSection]:
    """Compute the restriction of *global_section* to each patch of *cover*.

    copilot: shared-core marker

    For each member of *cover*, this function retrieves or derives the
    corresponding local representative.  If the global section already
    stores a representative for a patch, it is returned directly; otherwise
    the function attempts restriction from the base coordinate.

    Args:
        global_section: The global section to restrict.
        cover: The cover supplying the target patches.

    Returns:
        Dict mapping each cover member's ``source_key`` to its ``LocalSection``.
    """
    result: dict[str, LocalSection] = {}
    reps = global_section.local_representatives

    for member in cover.members:
        pk = member.source_key
        if pk in reps:
            result[pk] = reps[pk]
            continue

        # Derive by restriction/transport from the closest stored representative
        src = member.source_coordinate
        best: LocalSection | None = None
        for stored_sec in reps.values():
            if stored_sec.coordinate.is_prefix_of(src):
                best = stored_sec
                break

        if best is not None:
            try:
                result[pk] = best.restrict(src)
            except ValueError:
                synth = Morphism(
                    source=best.coordinate,
                    target=src,
                    kind=MorphismKind.RESTRICTION,
                    label=f"derive_{pk}",
                )
                result[pk] = best.transport_along(synth)
        else:
            # Placeholder with zero trust
            result[pk] = LocalSection(
                coordinate=src,
                data=None,
                kind=SectionKind.PARTIAL_GLOBAL,
                trust_score=0.0,
                provenance=("derive_placeholder",),
            )

    return result
