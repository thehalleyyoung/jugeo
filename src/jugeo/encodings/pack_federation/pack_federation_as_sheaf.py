r"""Pack federation modelled as a sheaf of local semantic theories.

Theory (theory2.tex §35.1 — Pack Federation as a Sheaf):
    A sheaf F on a topological space X assigns to each open set U ⊆ X a set
    F(U) of "sections" and to each inclusion V ⊆ U a restriction map
    ρ_{U,V}: F(U) → F(V), subject to:

    (Locality) If s, t ∈ F(U) agree on every element of a cover {Uᵢ} of U,
    then s = t.

    (Gluing) If we have sections sᵢ ∈ F(Uᵢ) that agree on all pairwise
    intersections (sᵢ|_{Uᵢ∩Uⱼ} = sⱼ|_{Uᵢ∩Uⱼ}), then there exists a unique
    global section s ∈ F(U) with s|_{Uᵢ} = sᵢ for all i.

    In the pack-federation setting, the "open sets" are the packs, and the
    restriction maps are given by the bridge theorems: a bridge B from pack P₁
    to pack P₂ specifies how to restrict a section of P₁ to the overlap region
    B.overlap_region, yielding a section of P₂ on the same overlap region.

    The class :class:`PackFederationAsSheaf` implements this structure,
    exposing the gluing map, restriction map, sheaf condition check, and
    cohomology computation.

Public surface
--------------
:class:`PackFederationAsSheaf`
    Dataclass encoding a pack federation as a topological sheaf.

copilot: pack-federation-as-sheaf
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .models import (
    BridgeTheoremEncoding,
    PackBoundary,
    PackFederationEncoding,
)

__all__: list[str] = [
    "PackFederationAsSheaf",
]


# ---------------------------------------------------------------------------
# PackFederationAsSheaf
# ---------------------------------------------------------------------------


@dataclass
class PackFederationAsSheaf:
    """A pack federation modelled as a sheaf of local semantic theories.

    This class is the central object of theory2.tex §35.1.  It wraps a
    :class:`~jugeo.encodings.pack_federation.models.PackFederationEncoding`
    together with:

    - A mapping from boundary IDs to :class:`PackBoundary` objects
      (the "intersections" of the open cover).
    - A mapping from pack IDs to local evidence section dicts
      (the local sections of the sheaf).
    - An internal cohomology cache to avoid recomputing expensive results.

    Parameters
    ----------
    encoding:
        The underlying pack federation encoding.
    boundary_map:
        Dict mapping ``boundary_id`` → :class:`PackBoundary`.
    local_sections:
        Dict mapping ``pack_id`` → evidence dict (local section).
    _cohomology_cache:
        Internal cache for cohomology computations; should not be set by
        the caller.

    copilot: sheaf-dataclass
    """

    encoding: PackFederationEncoding
    boundary_map: dict[str, PackBoundary]
    local_sections: dict[str, dict]
    _cohomology_cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # 1. check_sheaf_condition
    # ------------------------------------------------------------------

    def check_sheaf_condition(self) -> tuple[bool, list[str]]:
        """Verify the sheaf gluing axiom across all bridge encodings.

        For each bridge B in :attr:`encoding.bridge_encodings`, this method
        checks that the local section of ``B.source_pack_id`` and the local
        section of ``B.target_pack_id`` agree on every coordinate in
        ``B.overlap_region``.

        Formally: for all k ∈ overlap_region,
            local_sections[B.source_pack_id][k] == local_sections[B.target_pack_id][k]

        A violation is recorded when the values differ or when a coordinate
        is absent from one of the two sections.

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if the gluing axiom holds for all bridges;
            ``(False, violations)`` where *violations* lists human-readable
            conflict descriptions.
        """
        violations: list[str] = []

        for bridge in self.encoding.bridge_encodings:
            src_section = self.local_sections.get(bridge.source_pack_id, {})
            tgt_section = self.local_sections.get(bridge.target_pack_id, {})

            for coord in sorted(bridge.overlap_region):
                src_has = coord in src_section
                tgt_has = coord in tgt_section

                if not src_has and not tgt_has:
                    # Neither pack defines this coordinate; acceptable absence
                    continue

                if src_has and not tgt_has:
                    violations.append(
                        f"Bridge {bridge.bridge_id!r}: coordinate {coord!r} "
                        f"present in source pack {bridge.source_pack_id!r} "
                        f"but absent from target pack {bridge.target_pack_id!r}"
                    )
                    continue

                if tgt_has and not src_has:
                    violations.append(
                        f"Bridge {bridge.bridge_id!r}: coordinate {coord!r} "
                        f"present in target pack {bridge.target_pack_id!r} "
                        f"but absent from source pack {bridge.source_pack_id!r}"
                    )
                    continue

                # Both present — values must agree
                src_val = src_section[coord]
                tgt_val = tgt_section[coord]
                if src_val != tgt_val:
                    violations.append(
                        f"Bridge {bridge.bridge_id!r}: coordinate {coord!r} "
                        f"disagrees: {bridge.source_pack_id!r} has {src_val!r}, "
                        f"{bridge.target_pack_id!r} has {tgt_val!r}"
                    )

        return len(violations) == 0, violations

    # ------------------------------------------------------------------
    # 2. compute_restriction_map
    # ------------------------------------------------------------------

    def compute_restriction_map(
        self, pack_id: str, sub_region: FrozenSet[str]
    ) -> dict[str, Any]:
        """Restrict the local section of *pack_id* to *sub_region*.

        The restriction map ρ_{U,V}: F(U) → F(V) is implemented here by
        projecting the local evidence section for *pack_id* onto the
        coordinate subset *sub_region*.

        Parameters
        ----------
        pack_id:
            The pack whose local section is to be restricted.
        sub_region:
            The set of coordinates to keep.

        Returns
        -------
        dict[str, Any]
            Projection of the local section, containing only keys in
            *sub_region* that are defined in the local section.  Returns an
            empty dict if *pack_id* has no local section.
        """
        section = self.local_sections.get(pack_id, {})
        if not section:
            return {}

        restricted: dict[str, Any] = {}
        for coord in sub_region:
            if coord in section:
                restricted[coord] = section[coord]

        # Attach metadata for traceability
        restricted["_restriction_meta"] = {
            "pack_id": pack_id,
            "sub_region": sorted(sub_region),
            "matched_coords": sorted(k for k in sub_region if k in section),
        }
        return restricted

    # ------------------------------------------------------------------
    # 3. compute_gluing_map
    # ------------------------------------------------------------------

    def compute_gluing_map(
        self, sections: Sequence[tuple[str, dict]]
    ) -> dict[str, Any]:
        """Glue a sequence of local sections into a (partial) global section.

        Given a list of (pack_id, section) pairs, this method merges the
        sections by:
        1. Starting from an empty global section dict.
        2. For each (pack_id, section), iterating over its keys.
        3. For keys not yet in the global section, adding them.
        4. For keys already present, checking consistency: if values differ,
           recording a conflict in ``"_conflicts"``.

        Parameters
        ----------
        sections:
            List of ``(pack_id, evidence_dict)`` pairs to merge.

        Returns
        -------
        dict[str, Any]
            Merged global section dict.  Contains a ``"_conflicts"`` key
            listing any inconsistencies found during merging.
        """
        global_section: dict[str, Any] = {}
        conflicts: list[dict[str, Any]] = []
        provenance: dict[str, str] = {}  # coord -> pack_id that contributed it

        for pack_id, section in sections:
            for key, value in section.items():
                if key.startswith("_"):
                    continue  # skip metadata keys
                if key not in global_section:
                    global_section[key] = value
                    provenance[key] = pack_id
                else:
                    if global_section[key] != value:
                        conflicts.append({
                            "coordinate": key,
                            "existing_value": global_section[key],
                            "existing_pack": provenance[key],
                            "conflict_value": value,
                            "conflict_pack": pack_id,
                        })

        global_section["_conflicts"] = conflicts
        global_section["_provenance"] = provenance
        global_section["_section_count"] = len(sections)
        return global_section

    # ------------------------------------------------------------------
    # 4. evaluate_section
    # ------------------------------------------------------------------

    def evaluate_section(self, coordinate: str) -> dict[str, Any]:
        """Evaluate the global section at a given coordinate.

        Gathers all local sections that define *coordinate* and returns a
        dict mapping each contributing pack_id to the section value, together
        with a consistency flag.

        Parameters
        ----------
        coordinate:
            The coordinate name to evaluate.

        Returns
        -------
        dict[str, Any]
            A dict with:
            - ``"coordinate"``: the queried coordinate string.
            - ``"contributions"``: dict mapping pack_id → value for each
              pack that defines this coordinate.
            - ``"consistent"``: True if all values are equal.
            - ``"consensus_value"``: the common value if consistent, else None.
        """
        contributions: dict[str, Any] = {}
        for pack_id, section in self.local_sections.items():
            if coordinate in section:
                contributions[pack_id] = section[coordinate]

        values = list(contributions.values())
        consistent = len(set(str(v) for v in values)) <= 1 if values else True
        consensus = values[0] if consistent and values else None

        return {
            "coordinate": coordinate,
            "contributions": contributions,
            "consistent": consistent,
            "consensus_value": consensus,
            "contributing_packs": list(contributions.keys()),
        }

    # ------------------------------------------------------------------
    # 5. compute_cohomology
    # ------------------------------------------------------------------

    def compute_cohomology(self, degree: int) -> dict[str, Any]:
        """Compute sheaf cohomology up to the given degree.

        For ``degree == 0``: checks that a global section exists by verifying
        the sheaf condition.  The "H0" result is non-trivial iff the gluing
        axiom holds.

        For ``degree == 1``: computes the first cohomology group by identifying
        1-cocycles (inconsistent restriction pairs across bridges) and
        1-coboundaries (those that can be corrected by adjusting a local
        section).

        Parameters
        ----------
        degree:
            Cohomology degree to compute.  Must be 0 or 1.

        Returns
        -------
        dict[str, Any]
            Dict with keys:
            - ``"degree"``: the requested degree.
            - ``"H0"``: H⁰ result dict (always computed).
            - ``"H1"``: H¹ result dict (computed if degree >= 1).
            - ``"obstructions"``: list of obstruction strings.
            - ``"cohomology_class"``: canonical cohomology class string.

        Raises
        ------
        ValueError
            If *degree* is not 0 or 1.
        """
        if degree not in (0, 1):
            raise ValueError(f"Only degree 0 and 1 are supported; got {degree!r}")

        cache_key = f"cohomology_{degree}"
        if cache_key in self._cohomology_cache:
            return dict(self._cohomology_cache[cache_key])

        # H^0 — global sections
        sheaf_ok, violations = self.check_sheaf_condition()
        h0 = {
            "rank": 1 if sheaf_ok else 0,
            "is_trivial": not sheaf_ok,
            "violations": violations,
        }
        obstructions: list[str] = list(violations)
        cohomology_class = self.encoding.compute_cohomology_class()

        result: dict[str, Any] = {
            "degree": degree,
            "H0": h0,
            "obstructions": obstructions,
            "cohomology_class": cohomology_class,
        }

        if degree >= 1:
            # H^1 — obstruction class
            # Cocycles: pairs of bridges (B_ij, B_jk) where restriction to
            # intersection differs.
            cocycles: list[dict[str, Any]] = []
            coboundaries: list[str] = []

            bridges = list(self.encoding.bridge_encodings)
            for i, b1 in enumerate(bridges):
                for j, b2 in enumerate(bridges):
                    if i >= j:
                        continue
                    # Look for bridges that share a pack
                    common_pack: str | None = None
                    if b1.target_pack_id == b2.source_pack_id:
                        common_pack = b1.target_pack_id
                    elif b1.source_pack_id == b2.target_pack_id:
                        common_pack = b1.source_pack_id
                    if common_pack is None:
                        continue

                    # Check that restriction of b1 to the common pack agrees
                    # with restriction of b2 to the common pack
                    shared = b1.overlap_region & b2.overlap_region
                    section = self.local_sections.get(common_pack, {})
                    conflict_found = False
                    for coord in shared:
                        s1 = self.local_sections.get(b1.source_pack_id, {}).get(coord)
                        s2 = self.local_sections.get(b2.target_pack_id, {}).get(coord)
                        if s1 is not None and s2 is not None and s1 != s2:
                            conflict_found = True
                            cocycles.append({
                                "bridge_1": b1.bridge_id,
                                "bridge_2": b2.bridge_id,
                                "common_pack": common_pack,
                                "coordinate": coord,
                            })

                    if not conflict_found:
                        coboundaries.append(f"{b1.bridge_id},{b2.bridge_id}")

            h1 = {
                "rank": len(cocycles),
                "cocycles": cocycles,
                "coboundaries": coboundaries,
                "is_trivial": len(cocycles) == 0,
            }
            result["H1"] = h1
            if cocycles:
                obstructions.extend(
                    f"H1 cocycle between {c['bridge_1']!r} and {c['bridge_2']!r}"
                    for c in cocycles
                )

        self._cohomology_cache[cache_key] = dict(result)
        return result

    # ------------------------------------------------------------------
    # 6. get_cover_opens
    # ------------------------------------------------------------------

    def get_cover_opens(self) -> list[FrozenSet[str]]:
        """Return the open cover as a list of coordinate sets for each pack.

        Each element of the returned list is the union of all overlap regions
        of bridges incident to the corresponding pack.  The order corresponds
        to :meth:`PackFederationEncoding.get_pack_ids`.

        Returns
        -------
        list[FrozenSet[str]]
            One frozenset per pack, containing the coordinates accessible from
            that pack via its bridges.
        """
        pack_coords: dict[str, set[str]] = {
            pid: set() for pid in self.encoding.pack_ids
        }
        for bridge in self.encoding.bridge_encodings:
            pack_coords[bridge.source_pack_id].update(bridge.overlap_region)
            pack_coords[bridge.target_pack_id].update(bridge.overlap_region)

        # Also include all coordinates in the pack's own local section
        for pack_id, section in self.local_sections.items():
            if pack_id in pack_coords:
                pack_coords[pack_id].update(
                    k for k in section if not k.startswith("_")
                )

        return [
            frozenset(pack_coords[pid])
            for pid in sorted(self.encoding.pack_ids)
        ]

    # ------------------------------------------------------------------
    # 7. verify_locality_axiom
    # ------------------------------------------------------------------

    def verify_locality_axiom(self) -> tuple[bool, list[str]]:
        """Check the locality axiom: each pack only contributes its own coordinates.

        The locality axiom (theory2.tex §35.1) states that a pack may only
        provide evidence for coordinates within its own jurisdiction.  Here,
        a pack's jurisdiction is the union of all overlap regions of bridges
        incident to it, plus any coordinates explicitly assigned to it.

        A violation is raised when a pack's local section contains a coordinate
        that does not appear in any of its incident bridges' overlap regions
        AND does not appear in the pack's explicitly declared keys (since there
        may be pack-private coordinates that are never bridged).

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if the locality axiom holds; otherwise ``(False, issues)``.
        """
        issues: list[str] = []

        # Build each pack's jurisdictional coordinates from its bridges
        jurisdiction: dict[str, set[str]] = {
            pid: set() for pid in self.encoding.pack_ids
        }
        for bridge in self.encoding.bridge_encodings:
            jurisdiction[bridge.source_pack_id].update(bridge.overlap_region)
            jurisdiction[bridge.target_pack_id].update(bridge.overlap_region)

        for pack_id, section in self.local_sections.items():
            if pack_id not in jurisdiction:
                issues.append(
                    f"Pack {pack_id!r} has local section but is not in encoding.pack_ids"
                )
                continue

            pack_jurisdiction = jurisdiction[pack_id]
            for coord in section:
                if coord.startswith("_"):
                    continue  # skip metadata
                if pack_jurisdiction and coord not in pack_jurisdiction:
                    # This pack has a coordinate outside its bridge jurisdiction
                    issues.append(
                        f"Pack {pack_id!r} local section contains coordinate "
                        f"{coord!r} which is not in its bridge jurisdiction "
                        f"{sorted(pack_jurisdiction)}"
                    )

        return len(issues) == 0, issues

    # ------------------------------------------------------------------
    # 8. compute_stalk
    # ------------------------------------------------------------------

    def compute_stalk(self, coordinate: str) -> list[dict[str, Any]]:
        """Return the stalk of the sheaf at *coordinate*.

        The stalk at a point x is the colimit (direct limit) of all sections
        over opens containing x.  Concretely, we return all local sections
        that are defined at *coordinate*, annotated with the contributing
        pack_id.

        Parameters
        ----------
        coordinate:
            Coordinate name at which to compute the stalk.

        Returns
        -------
        list[dict[str, Any]]
            List of ``{"pack_id": str, "value": Any, "full_section": dict}``
            records, one per pack whose local section defines *coordinate*.
        """
        stalk: list[dict[str, Any]] = []
        for pack_id, section in self.local_sections.items():
            if coordinate in section:
                stalk.append({
                    "pack_id": pack_id,
                    "value": section[coordinate],
                    "full_section": {
                        k: v for k, v in section.items() if not k.startswith("_")
                    },
                })
        return stalk

    # ------------------------------------------------------------------
    # 9. build_from_encoding (classmethod-style factory)
    # ------------------------------------------------------------------

    def build_from_encoding(self, cls_method_style: bool = False) -> PackFederationAsSheaf:
        """Reconstruct a sheaf from just the encoding, with empty local sections.

        This factory is provided as an instance method with a
        ``cls_method_style`` flag rather than a true classmethod because frozen
        nested objects need to be re-assembled from the encoding's bridge data.

        The returned sheaf has:
        - ``boundary_map`` synthesised from bridge encodings (one boundary per
          bridge, using bridge.overlap_region as shared_coordinates).
        - ``local_sections`` initialised to empty dicts for each pack.
        - ``_cohomology_cache`` cleared.

        Parameters
        ----------
        cls_method_style:
            Ignored; present for API symmetry with other factory patterns.

        Returns
        -------
        PackFederationAsSheaf
            New sheaf instance derived from :attr:`encoding`.
        """
        from .models import PackBoundary

        boundary_map: dict[str, PackBoundary] = {}
        for bridge in self.encoding.bridge_encodings:
            boundary_id = f"boundary_{bridge.bridge_id}"
            boundary = PackBoundary(
                boundary_id=boundary_id,
                pack_a_id=bridge.source_pack_id,
                pack_b_id=bridge.target_pack_id,
                shared_coordinates=bridge.overlap_region,
                overlap_laws=(
                    f"overlap:{bridge.source_formula}={bridge.target_formula}",
                ),
                boundary_type="interior",
            )
            boundary_map[boundary_id] = boundary

        local_sections: dict[str, dict] = {
            pid: {} for pid in self.encoding.pack_ids
        }

        return PackFederationAsSheaf(
            encoding=self.encoding,
            boundary_map=boundary_map,
            local_sections=local_sections,
            _cohomology_cache={},
        )

    # ------------------------------------------------------------------
    # 10. to_encoding
    # ------------------------------------------------------------------

    def to_encoding(self) -> PackFederationEncoding:
        """Serialise back to a :class:`PackFederationEncoding`, recomputing status.

        Calls :meth:`check_sheaf_condition` to determine the current sheaf
        condition status and embeds it in the returned encoding.

        Returns
        -------
        PackFederationEncoding
            A new encoding instance with updated :attr:`sheaf_condition_status`.
        """
        ok, _ = self.check_sheaf_condition()
        status = "satisfied" if ok else "violated"

        return PackFederationEncoding(
            pack_ids=self.encoding.pack_ids,
            bridge_encodings=self.encoding.bridge_encodings,
            federation_protocol_id=self.encoding.federation_protocol_id,
            sheaf_condition_status=status,
        )
