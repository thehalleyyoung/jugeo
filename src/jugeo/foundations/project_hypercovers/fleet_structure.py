"""Theory2.tex Ch8 §8.3 — Fleet Structure.

A fleet is a collection of verification agents, each assigned a patch (or set
of patches) from a module cover, collectively responsible for producing
evidence that a judgment holds on the covered coordinate.  The fleet provides
the human-in-the-loop / AI-in-the-loop layer of the JuGeo trust stack.

copilot: shared-core §8.3 implementation — fleet machinery for LLM-assisted
trust accumulation.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.geometry.hypercovers import HypercoverLevel, CechNerve, HypercoverKind  # noqa: F401
from jugeo.geometry.site import CoordinateObject, SemanticSite, CoordinateKind  # noqa: F401
from jugeo.foundations.project_hypercovers.models import (
    ProjectSite,
    ModuleCover,
    FleetMember,
    HypercoverDecomposition,
    ProjectKind,
    CoverStrategy,
    FleetStatus,
    DecompositionStatus,
    CoordinateMorphism,
    OverlapCell,
    CohomologyClass,
    TrustTier,
)


# ---------------------------------------------------------------------------
# FleetCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetCoordinator:
    """Manages assignment of fleet members to patches of a module cover.

    Theory2.tex §8.3, Def 8.15.

    The coordinator tracks which fleet members exist, which module cover is
    being covered, and which members are assigned to which patches.  It
    exposes query helpers (fully covered?, overloaded?, uncovered patches) and
    high-level auto-assignment strategies.

    Parameters
    ----------
    fleet : list[FleetMember]
        Initial list of fleet members (may be empty).
    cover : ModuleCover or None
        The module cover whose patches must be assigned.  May be set later
        via :meth:`set_cover`.
    assignment_map : dict[str, list[str]]
        Mapping from patch_id to list of member_ids currently assigned to it.
    _member_index : dict[str, FleetMember]
        Internal index from member_id to FleetMember for O(1) lookup.
    """

    fleet: list[FleetMember] = field(default_factory=list)
    cover: ModuleCover | None = None
    assignment_map: dict[str, list[str]] = field(default_factory=dict)
    _member_index: dict[str, FleetMember] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Member management
    # ------------------------------------------------------------------

    def add_member(self, member: FleetMember) -> None:
        """Register a new fleet member with this coordinator.

        Parameters
        ----------
        member : FleetMember
            The fleet member to register.  If a member with the same
            ``member_id`` already exists it is replaced.

        Returns
        -------
        None

        Notes
        -----
        The member is appended to ``fleet`` and indexed in ``_member_index``.
        Existing assignments are preserved if the member_id is already present.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1", agent_kind="llm")
        >>> coord.add_member(m)
        >>> coord.get_member("m1") is m
        True
        """
        existing_ids = {m.member_id for m in self.fleet}
        if member.member_id in existing_ids:
            self.fleet = [m if m.member_id != member.member_id else member for m in self.fleet]
        else:
            self.fleet.append(member)
        self._member_index[member.member_id] = member

    def remove_member(self, member_id: str) -> FleetMember | None:
        """Remove a fleet member by ID and return it, or None if not found.

        Parameters
        ----------
        member_id : str
            The ID of the member to remove.

        Returns
        -------
        FleetMember or None
            The removed member, or ``None`` if no member had that ID.

        Notes
        -----
        All patch assignments belonging to the removed member are also cleaned
        up from ``assignment_map``.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1")
        >>> coord.add_member(m)
        >>> removed = coord.remove_member("m1")
        >>> removed.member_id == "m1"
        True
        """
        member = self._member_index.pop(member_id, None)
        if member is None:
            return None
        self.fleet = [m for m in self.fleet if m.member_id != member_id]
        # Clean up assignments
        for patch_id in list(self.assignment_map.keys()):
            if member_id in self.assignment_map[patch_id]:
                self.assignment_map[patch_id] = [
                    mid for mid in self.assignment_map[patch_id] if mid != member_id
                ]
        return member

    def get_member(self, member_id: str) -> FleetMember | None:
        """Retrieve a fleet member by ID.

        Parameters
        ----------
        member_id : str
            The unique identifier of the member to retrieve.

        Returns
        -------
        FleetMember or None
            The member with the given ID, or ``None`` if not found.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> coord.get_member("nonexistent") is None
        True
        """
        return self._member_index.get(member_id)

    # ------------------------------------------------------------------
    # Cover management
    # ------------------------------------------------------------------

    def set_cover(self, cover: ModuleCover) -> None:
        """Set the module cover to be assigned.

        Parameters
        ----------
        cover : ModuleCover
            The module cover whose patches are to be distributed among the
            fleet members.

        Returns
        -------
        None

        Notes
        -----
        Initialises empty ``assignment_map`` entries for any new patch IDs
        not already present in the current map.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> cov = ModuleCover(patches={"p1": ["c1", "c2"], "p2": ["c3"]})
        >>> coord.set_cover(cov)
        >>> "p1" in coord.assignment_map
        True
        """
        self.cover = cover
        for patch_id in cover.patches:
            if patch_id not in self.assignment_map:
                self.assignment_map[patch_id] = []

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    def assign(self, member_id: str, patch_id: str) -> bool:
        """Assign a member to a patch if the member is capable and available.

        Parameters
        ----------
        member_id : str
            ID of the fleet member to assign.
        patch_id : str
            ID of the patch to assign the member to.

        Returns
        -------
        bool
            ``True`` if the assignment was made; ``False`` if the member was
            not found, already at capacity, failed/suspended, or the patch
            does not exist in the current cover.

        Notes
        -----
        Updates both ``assignment_map[patch_id]`` and
        ``member.assigned_patches``.  Does not enforce capability matching
        (use :class:`LoadBalancer` for that).

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1", max_patches=3)
        >>> cov = ModuleCover(patches={"p1": ["c1"]})
        >>> coord.add_member(m)
        >>> coord.set_cover(cov)
        >>> coord.assign("m1", "p1")
        True
        """
        member = self._member_index.get(member_id)
        if member is None:
            return False
        if member.status in (FleetStatus.FAILED, FleetStatus.SUSPENDED):
            return False
        if len(member.assigned_patches) >= member.max_patches:
            return False
        if self.cover is not None and patch_id not in self.cover.patches:
            return False
        # Check not already assigned
        if patch_id in member.assigned_patches:
            return True  # idempotent
        member.assigned_patches.append(patch_id)
        if patch_id not in self.assignment_map:
            self.assignment_map[patch_id] = []
        if member_id not in self.assignment_map[patch_id]:
            self.assignment_map[patch_id].append(member_id)
        # Update load factor
        if member.max_patches > 0:
            member.load_factor = len(member.assigned_patches) / member.max_patches
        if member.status == FleetStatus.IDLE:
            member.status = FleetStatus.ACTIVE
        return True

    def unassign(self, member_id: str, patch_id: str) -> bool:
        """Remove an assignment between a member and a patch.

        Parameters
        ----------
        member_id : str
            ID of the fleet member to unassign.
        patch_id : str
            ID of the patch to unassign from.

        Returns
        -------
        bool
            ``True`` if the assignment existed and was removed; ``False``
            otherwise.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1")
        >>> cov = ModuleCover(patches={"p1": ["c1"]})
        >>> coord.add_member(m); coord.set_cover(cov); coord.assign("m1", "p1")
        True
        >>> coord.unassign("m1", "p1")
        True
        """
        member = self._member_index.get(member_id)
        if member is None:
            return False
        if patch_id not in member.assigned_patches:
            return False
        member.assigned_patches.remove(patch_id)
        if patch_id in self.assignment_map and member_id in self.assignment_map[patch_id]:
            self.assignment_map[patch_id].remove(member_id)
        # Update load factor
        if member.max_patches > 0:
            member.load_factor = len(member.assigned_patches) / member.max_patches
        if len(member.assigned_patches) == 0:
            member.status = FleetStatus.IDLE
        return True

    def assign_all(self, strategy: str = "round_robin") -> dict[str, list[str]]:
        """Auto-assign all unassigned cover patches to available members.

        Parameters
        ----------
        strategy : str, optional
            Assignment strategy.  One of ``"round_robin"``, ``"least_loaded"``,
            ``"capability_match"``.  Default ``"round_robin"``.

        Returns
        -------
        dict[str, list[str]]
            The updated ``assignment_map`` after all possible assignments.

        Notes
        -----
        *round_robin*: cycles through available members in list order.
        *least_loaded*: always assigns to the member with the smallest
            ``load_factor`` that is not at capacity.
        *capability_match*: assigns to the member whose ``capabilities``
            list has the most overlap with the patch's coordinate IDs
            (uses coordinate ID prefixes as synthetic capability tags).

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> for i in range(3):
        ...     coord.add_member(FleetMember(member_id=f"m{i}", max_patches=5))
        >>> cov = ModuleCover(patches={f"p{i}": [f"c{i}"] for i in range(9)})
        >>> coord.set_cover(cov)
        >>> result = coord.assign_all("round_robin")
        >>> len(result)
        9
        """
        if self.cover is None:
            return self.assignment_map
        available = [m for m in self.fleet if m.is_available()]
        if not available:
            return self.assignment_map

        patches_to_assign = [
            pid for pid in self.cover.patches
            if not self.assignment_map.get(pid)
        ]

        if strategy == "round_robin":
            idx = 0
            for patch_id in patches_to_assign:
                attempts = 0
                while attempts < len(available):
                    member = available[idx % len(available)]
                    idx += 1
                    if self.assign(member.member_id, patch_id):
                        break
                    attempts += 1

        elif strategy == "least_loaded":
            for patch_id in patches_to_assign:
                candidates = sorted(
                    [m for m in available if m.is_available()],
                    key=lambda m: m.load_factor,
                )
                for member in candidates:
                    if self.assign(member.member_id, patch_id):
                        break

        elif strategy == "capability_match":
            for patch_id in patches_to_assign:
                patch_coords = self.cover.patches.get(patch_id, [])
                # synthetic capability tags: first path segment of each coord
                required_caps = {c.split(".")[0] for c in patch_coords}
                best_member: FleetMember | None = None
                best_score = -1.0
                for member in available:
                    if not member.is_available():
                        continue
                    overlap = required_caps & set(member.capabilities)
                    score = len(overlap) / max(len(required_caps), 1)
                    if score > best_score:
                        best_score = score
                        best_member = member
                if best_member is not None:
                    self.assign(best_member.member_id, patch_id)

        return self.assignment_map

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_patch_members(self, patch_id: str) -> list[FleetMember]:
        """Return all fleet members assigned to a given patch.

        Parameters
        ----------
        patch_id : str
            The ID of the patch to query.

        Returns
        -------
        list[FleetMember]
            Members currently assigned to ``patch_id`` (may be empty).

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1")
        >>> cov = ModuleCover(patches={"p1": ["c1"]})
        >>> coord.add_member(m); coord.set_cover(cov); coord.assign("m1", "p1")
        True
        >>> [x.member_id for x in coord.get_patch_members("p1")]
        ['m1']
        """
        member_ids = self.assignment_map.get(patch_id, [])
        return [self._member_index[mid] for mid in member_ids if mid in self._member_index]

    def get_member_patches(self, member_id: str) -> list[str]:
        """Return the list of patch IDs assigned to a given member.

        Parameters
        ----------
        member_id : str
            The ID of the fleet member to query.

        Returns
        -------
        list[str]
            Patch IDs assigned to the member.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1")
        >>> coord.add_member(m)
        >>> coord.get_member_patches("m1")
        []
        """
        member = self._member_index.get(member_id)
        if member is None:
            return []
        return list(member.assigned_patches)

    def is_fully_covered(self) -> bool:
        """Return True when every patch in the cover has at least one member.

        Returns
        -------
        bool
            ``True`` iff every patch key in ``cover.patches`` appears in
            ``assignment_map`` with a non-empty member list.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> cov = ModuleCover(patches={"p1": ["c1"]})
        >>> coord.set_cover(cov)
        >>> coord.is_fully_covered()
        False
        """
        if self.cover is None:
            return True
        return all(
            bool(self.assignment_map.get(pid))
            for pid in self.cover.patches
        )

    def uncovered_patches(self) -> list[str]:
        """Return patch IDs in the cover that have no assigned members.

        Returns
        -------
        list[str]
            Sorted list of patch IDs lacking any assignment.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> cov = ModuleCover(patches={"p1": ["c1"], "p2": ["c2"]})
        >>> coord.set_cover(cov)
        >>> sorted(coord.uncovered_patches())
        ['p1', 'p2']
        """
        if self.cover is None:
            return []
        return sorted(
            pid for pid in self.cover.patches
            if not self.assignment_map.get(pid)
        )

    def overloaded_members(self) -> list[FleetMember]:
        """Return members whose load_factor exceeds 0.8.

        Returns
        -------
        list[FleetMember]
            Members with ``load_factor > 0.8``, sorted by load descending.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1", load_factor=0.9)
        >>> coord.add_member(m)
        >>> coord.overloaded_members()[0].member_id
        'm1'
        """
        return sorted(
            [m for m in self.fleet if m.load_factor > 0.8],
            key=lambda m: m.load_factor,
            reverse=True,
        )

    def fleet_status(self) -> dict[str, Any]:
        """Produce a summary dict of the fleet's current state.

        Returns
        -------
        dict[str, Any]
            Keys: ``total_members``, ``covered_patches``, ``uncovered_patches``,
            ``avg_load``, ``overloaded_count``, ``total_patches``.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> status = coord.fleet_status()
        >>> "total_members" in status
        True
        """
        n = len(self.fleet)
        avg_load = (sum(m.load_factor for m in self.fleet) / n) if n > 0 else 0.0
        uncovered = self.uncovered_patches()
        total_patches = len(self.cover.patches) if self.cover else 0
        covered = total_patches - len(uncovered)
        return {
            "total_members": n,
            "total_patches": total_patches,
            "covered_patches": covered,
            "uncovered_patches": len(uncovered),
            "avg_load": round(avg_load, 4),
            "overloaded_count": len(self.overloaded_members()),
        }

    def validate(self) -> list[str]:
        """Return a list of validation errors for this coordinator.

        Returns
        -------
        list[str]
            Human-readable error strings.  Empty list means no errors.

        Notes
        -----
        Checks: duplicate member IDs, members assigned to non-existent patches,
        assignment_map entries not mirrored in member.assigned_patches.

        Examples
        --------
        >>> coord = FleetCoordinator()
        >>> coord.validate()
        []
        """
        errors: list[str] = []
        seen_ids: set[str] = set()
        for m in self.fleet:
            if m.member_id in seen_ids:
                errors.append(f"Duplicate member_id: {m.member_id}")
            seen_ids.add(m.member_id)

        if self.cover is not None:
            for mid, member in self._member_index.items():
                for pid in member.assigned_patches:
                    if pid not in self.cover.patches:
                        errors.append(f"Member {mid} assigned to unknown patch {pid}")

        # Check consistency between assignment_map and member.assigned_patches
        for patch_id, member_ids in self.assignment_map.items():
            for mid in member_ids:
                member = self._member_index.get(mid)
                if member is None:
                    errors.append(f"assignment_map references unknown member {mid}")
                elif patch_id not in member.assigned_patches:
                    errors.append(
                        f"assignment_map[{patch_id}] has {mid} but member lacks patch"
                    )

        return errors


# ---------------------------------------------------------------------------
# LoadBalancer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoadBalancer:
    """Distributes patches among fleet members by capability and current load.

    Theory2.tex §8.3.

    Parameters
    ----------
    max_load : float
        Maximum acceptable load fraction per member (default 0.85).
    capability_weights : dict[str, float]
        Per-capability priority weight used in capability-match scoring.
        Higher weight → stronger preference when assigning patches that
        require that capability.
    """

    max_load: float = 0.85
    capability_weights: dict[str, float] = field(default_factory=dict)

    def compute_load_scores(self, members: list[FleetMember]) -> dict[str, float]:
        """Compute a load score in [0, 1] for each member.

        Parameters
        ----------
        members : list[FleetMember]
            The fleet members to score.

        Returns
        -------
        dict[str, float]
            Mapping from member_id to load score.  0.0 = completely free,
            1.0 = at maximum capacity.

        Notes
        -----
        Load score is computed as ``assigned_patches / max_patches`` clamped
        to [0, 1].

        Examples
        --------
        >>> lb = LoadBalancer()
        >>> m = FleetMember(member_id="m1", max_patches=4)
        >>> m.assigned_patches = ["p1", "p2"]
        >>> scores = lb.compute_load_scores([m])
        >>> scores["m1"]
        0.5
        """
        result: dict[str, float] = {}
        for member in members:
            if member.max_patches <= 0:
                result[member.member_id] = 1.0
            else:
                raw = len(member.assigned_patches) / member.max_patches
                result[member.member_id] = min(1.0, max(0.0, raw))
        return result

    def find_best_member_for_patch(
        self,
        patch_id: str,
        members: list[FleetMember],
        patch_coords: list[str],
    ) -> FleetMember | None:
        """Find the available member best suited for a given patch.

        Parameters
        ----------
        patch_id : str
            ID of the patch to assign (unused in current logic but kept for
            API completeness and future use).
        members : list[FleetMember]
            Candidate members.
        patch_coords : list[str]
            Coordinate IDs in the patch, used for capability matching.

        Returns
        -------
        FleetMember or None
            The member with the lowest combined load + capability-mismatch
            penalty, or ``None`` if no member is available.

        Notes
        -----
        Scoring: ``score = load_score + (1 - cap_match) * 0.3``.  Lower is
        better.  Members at or above ``max_load`` are excluded.

        Examples
        --------
        >>> lb = LoadBalancer()
        >>> m = FleetMember(member_id="m1", capabilities=["auth"])
        >>> lb.find_best_member_for_patch("p1", [m], ["auth.login"])
        FleetMember(...)
        """
        load_scores = self.compute_load_scores(members)
        best: FleetMember | None = None
        best_score = float("inf")
        for member in members:
            if not member.is_available():
                continue
            load = load_scores.get(member.member_id, 1.0)
            if load >= self.max_load:
                continue
            cap_match = self.compute_capability_match_score(member, patch_coords)
            combined = load + (1.0 - cap_match) * 0.3
            if combined < best_score:
                best_score = combined
                best = member
        return best

    def balance(self, coordinator: FleetCoordinator) -> dict[str, list[str]]:
        """Rebalance all patch assignments to minimise maximum load.

        Parameters
        ----------
        coordinator : FleetCoordinator
            The coordinator whose assignments are to be rebalanced.

        Returns
        -------
        dict[str, list[str]]
            The new assignment_map after rebalancing.

        Notes
        -----
        Algorithm:
        1. Collect all current assignments.
        2. Clear all assignments.
        3. Sort patches by number of required capabilities (desc).
        4. Re-assign using :meth:`find_best_member_for_patch`.

        Examples
        --------
        >>> lb = LoadBalancer()
        >>> coord = FleetCoordinator()
        >>> # after populating coord, call lb.balance(coord)
        """
        if coordinator.cover is None or not coordinator.fleet:
            return coordinator.assignment_map

        # Clear assignments
        for member in coordinator.fleet:
            member.assigned_patches.clear()
            member.load_factor = 0.0
            member.status = FleetStatus.IDLE
        for pid in coordinator.assignment_map:
            coordinator.assignment_map[pid] = []

        # Sort patches by coord count descending (larger patches first)
        patch_order = sorted(
            coordinator.cover.patches.keys(),
            key=lambda pid: len(coordinator.cover.patches.get(pid, [])),
            reverse=True,
        )
        for patch_id in patch_order:
            patch_coords = coordinator.cover.patches.get(patch_id, [])
            best = self.find_best_member_for_patch(
                patch_id, coordinator.fleet, patch_coords
            )
            if best is not None:
                coordinator.assign(best.member_id, patch_id)

        return coordinator.assignment_map

    def compute_capability_match_score(
        self, member: FleetMember, patch_coords: list[str]
    ) -> float:
        """Score how well a member's capabilities match the patch requirements.

        Parameters
        ----------
        member : FleetMember
            The fleet member to evaluate.
        patch_coords : list[str]
            Coordinate IDs in the patch.

        Returns
        -------
        float
            Score in [0, 1].  1.0 means all required capabilities are covered.

        Notes
        -----
        Required capabilities are derived by splitting each coord ID on '.'
        and taking the first segment.  Each required capability is weighted by
        ``capability_weights`` if present, otherwise weight 1.0.

        Examples
        --------
        >>> lb = LoadBalancer(capability_weights={"auth": 2.0})
        >>> m = FleetMember(member_id="m1", capabilities=["auth"])
        >>> lb.compute_capability_match_score(m, ["auth.login", "util.helper"])
        0.75
        """
        if not patch_coords:
            return 1.0
        required = {c.split(".")[0] for c in patch_coords}
        if not required:
            return 1.0
        total_weight = 0.0
        matched_weight = 0.0
        for cap in required:
            w = self.capability_weights.get(cap, 1.0)
            total_weight += w
            if cap in member.capabilities:
                matched_weight += w
        if total_weight == 0.0:
            return 1.0
        return matched_weight / total_weight

    def suggest_new_members(
        self, coordinator: FleetCoordinator, cover: ModuleCover
    ) -> list[dict[str, Any]]:
        """Suggest new member specifications to fill uncovered patches.

        Parameters
        ----------
        coordinator : FleetCoordinator
            The coordinator with existing assignments.
        cover : ModuleCover
            The module cover being assigned.

        Returns
        -------
        list[dict[str, Any]]
            List of spec dicts, each with keys ``agent_kind``,
            ``required_capabilities``, and ``suggested_patches``.

        Notes
        -----
        For each uncovered patch a spec is generated whose
        ``required_capabilities`` are the first-segment prefixes of the
        patch's coordinate IDs.

        Examples
        --------
        >>> lb = LoadBalancer()
        >>> coord = FleetCoordinator()
        >>> cov = ModuleCover(patches={"p1": ["auth.login", "auth.logout"]})
        >>> coord.set_cover(cov)
        >>> specs = lb.suggest_new_members(coord, cov)
        >>> specs[0]["required_capabilities"]
        ['auth']
        """
        uncovered = coordinator.uncovered_patches()
        specs: list[dict[str, Any]] = []
        # Group uncovered patches by shared capability requirements
        groups: dict[frozenset[str], list[str]] = {}
        for pid in uncovered:
            coords = cover.patches.get(pid, [])
            caps = frozenset(c.split(".")[0] for c in coords)
            if caps not in groups:
                groups[caps] = []
            groups[caps].append(pid)
        for caps, patch_ids in groups.items():
            specs.append({
                "agent_kind": "llm",
                "required_capabilities": sorted(caps),
                "suggested_patches": patch_ids,
                "max_patches": max(len(patch_ids), 5),
            })
        return specs

    def simulate_balance(
        self, members: list[FleetMember], patches: dict[str, list[str]]
    ) -> dict[str, float]:
        """Compute per-member load after a hypothetical assignment.

        Parameters
        ----------
        members : list[FleetMember]
            Fleet members to consider.
        patches : dict[str, list[str]]
            Mapping from patch_id to list of member_ids assigned to it.

        Returns
        -------
        dict[str, float]
            Mapping from member_id to simulated load fraction.

        Notes
        -----
        Load is computed as ``number_of_assigned_patches / max_patches``.
        Members not present in ``patches`` have load 0.0.

        Examples
        --------
        >>> lb = LoadBalancer()
        >>> m = FleetMember(member_id="m1", max_patches=4)
        >>> lb.simulate_balance([m], {"p1": ["m1"], "p2": ["m1"]})
        {'m1': 0.5}
        """
        counts: dict[str, int] = {m.member_id: 0 for m in members}
        for member_ids in patches.values():
            for mid in member_ids:
                if mid in counts:
                    counts[mid] += 1
        result: dict[str, float] = {}
        member_map = {m.member_id: m for m in members}
        for mid, count in counts.items():
            member = member_map.get(mid)
            max_p = member.max_patches if member else 1
            result[mid] = min(1.0, count / max(max_p, 1))
        return result


# ---------------------------------------------------------------------------
# TrustAggregator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrustAggregator:
    """Aggregates trust levels across fleet members covering a coordinate.

    Theory2.tex §8.3, Def 8.18.

    Parameters
    ----------
    aggregation_mode : str
        One of ``"weighted_average"``, ``"minimum"``, ``"product"``,
        ``"majority"``.  Controls how individual member trusts are combined.
    trust_floor : float
        Minimum trust below which a coordinate is considered untrusted
        (default 0.3).
    """

    aggregation_mode: str = "weighted_average"
    trust_floor: float = 0.3

    def aggregate(self, members: list[FleetMember], coord_id: str) -> float:
        """Aggregate trust of all members who cover a coord.

        Parameters
        ----------
        members : list[FleetMember]
            All fleet members (filtering by assignment is done internally).
        coord_id : str
            The coordinate whose trust is to be aggregated.

        Returns
        -------
        float
            Aggregated trust score in [0, 1].  Returns ``trust_floor`` when
            no members cover the coordinate.

        Notes
        -----
        Only members whose ``assigned_patches`` contains a patch that
        includes ``coord_id`` in its coordinate list contribute to the
        aggregation.  For simplicity (no cover reference here) we use
        member.trust_level and weight by 1.0 uniformly.

        Examples
        --------
        >>> agg = TrustAggregator()
        >>> m = FleetMember(member_id="m1", trust_level=0.8)
        >>> agg.aggregate([m], "any_coord")
        0.8
        """
        if not members:
            return self.trust_floor
        trust_values = [m.trust_level for m in members]
        weights = [1.0] * len(trust_values)
        if self.aggregation_mode == "weighted_average":
            result = self._weighted_average(trust_values, weights)
        elif self.aggregation_mode == "minimum":
            result = self._minimum(trust_values)
        elif self.aggregation_mode == "product":
            result = self._product(trust_values)
        elif self.aggregation_mode == "majority":
            result = self._majority(trust_values)
        else:
            result = self._weighted_average(trust_values, weights)
        return max(self.trust_floor, min(1.0, result))

    def _weighted_average(
        self, trust_values: list[float], weights: list[float]
    ) -> float:
        """Compute weighted average of trust values.

        Parameters
        ----------
        trust_values : list[float]
            Trust scores in [0, 1].
        weights : list[float]
            Corresponding non-negative weights.

        Returns
        -------
        float
            Weighted mean of the trust values.

        Examples
        --------
        >>> agg = TrustAggregator()
        >>> agg._weighted_average([0.6, 0.8], [1.0, 2.0])
        0.7333333333333333
        """
        if not trust_values:
            return 0.0
        total_weight = sum(weights)
        if total_weight == 0.0:
            return sum(trust_values) / len(trust_values)
        return sum(v * w for v, w in zip(trust_values, weights)) / total_weight

    def _minimum(self, trust_values: list[float]) -> float:
        """Return the minimum trust value (pessimistic aggregation).

        Parameters
        ----------
        trust_values : list[float]
            Trust scores.

        Returns
        -------
        float
            Minimum trust score.

        Examples
        --------
        >>> TrustAggregator()._minimum([0.9, 0.4, 0.7])
        0.4
        """
        return min(trust_values) if trust_values else 0.0

    def _product(self, trust_values: list[float]) -> float:
        """Return the product of trust values (independent-evidence aggregation).

        Parameters
        ----------
        trust_values : list[float]
            Trust scores.

        Returns
        -------
        float
            Product of all trust scores, clamped to [0, 1].

        Notes
        -----
        For very long lists this tends toward 0; best for small fleets.

        Examples
        --------
        >>> TrustAggregator()._product([0.9, 0.8])
        0.72
        """
        result = 1.0
        for v in trust_values:
            result *= max(0.0, min(1.0, v))
        return result

    def _majority(self, trust_values: list[float]) -> float:
        """Aggregate by majority rule.

        Parameters
        ----------
        trust_values : list[float]
            Trust scores.

        Returns
        -------
        float
            Mean when mean > 0.5; minimum otherwise.

        Notes
        -----
        Theory2.tex §8.3 defines majority trust as: if more than half the
        members vote "trusted" (trust > 0.5) return the mean; otherwise
        return the minimum as a safety signal.

        Examples
        --------
        >>> TrustAggregator()._majority([0.8, 0.7, 0.9])
        0.8
        """
        if not trust_values:
            return 0.0
        mean_val = sum(trust_values) / len(trust_values)
        return mean_val if mean_val > 0.5 else min(trust_values)

    def aggregate_fleet_trust(
        self, coordinator: FleetCoordinator, site: ProjectSite
    ) -> dict[str, float]:
        """Compute aggregated trust for all coordinates in a project site.

        Parameters
        ----------
        coordinator : FleetCoordinator
            The fleet coordinator with current assignments.
        site : ProjectSite
            The project site whose coordinates need trust scores.

        Returns
        -------
        dict[str, float]
            Mapping from coord_id to aggregated trust in [0, 1].

        Notes
        -----
        For each coordinate we find all fleet members whose patches include
        that coordinate (by checking coordinator's cover), then aggregate.

        Examples
        --------
        >>> agg = TrustAggregator()
        >>> coord_map = agg.aggregate_fleet_trust(coordinator, site)
        """
        result: dict[str, float] = {}
        cover = coordinator.cover

        for coord_id in site.coordinates:
            covering_members: list[FleetMember] = []
            if cover is not None:
                for patch_id, patch_coords in cover.patches.items():
                    if coord_id in patch_coords:
                        covering_members.extend(coordinator.get_patch_members(patch_id))
            # Deduplicate by member_id
            seen: set[str] = set()
            unique_members: list[FleetMember] = []
            for m in covering_members:
                if m.member_id not in seen:
                    seen.add(m.member_id)
                    unique_members.append(m)
            result[coord_id] = self.aggregate(unique_members, coord_id)

        return result

    def find_low_trust_coordinates(
        self,
        coordinator: FleetCoordinator,
        site: ProjectSite,
        threshold: float = 0.5,
    ) -> list[str]:
        """Return coordinate IDs whose aggregated trust is below a threshold.

        Parameters
        ----------
        coordinator : FleetCoordinator
            The fleet coordinator.
        site : ProjectSite
            The project site.
        threshold : float, optional
            Trust threshold (default 0.5).

        Returns
        -------
        list[str]
            Coordinate IDs with trust < threshold, sorted by trust ascending.

        Examples
        --------
        >>> agg = TrustAggregator()
        >>> low = agg.find_low_trust_coordinates(coordinator, site, 0.6)
        """
        trust_map = self.aggregate_fleet_trust(coordinator, site)
        low = [(cid, t) for cid, t in trust_map.items() if t < threshold]
        low.sort(key=lambda x: x[1])
        return [cid for cid, _ in low]

    def compute_fleet_trust_summary(
        self, coordinator: FleetCoordinator, site: ProjectSite
    ) -> dict[str, Any]:
        """Compute aggregate trust statistics for the whole fleet.

        Parameters
        ----------
        coordinator : FleetCoordinator
            The fleet coordinator.
        site : ProjectSite
            The project site.

        Returns
        -------
        dict[str, Any]
            Keys: ``mean_trust``, ``min_trust``, ``max_trust``,
            ``low_trust_count``, ``trust_histogram`` (5-bucket list).

        Examples
        --------
        >>> agg = TrustAggregator()
        >>> summary = agg.compute_fleet_trust_summary(coordinator, site)
        >>> "mean_trust" in summary
        True
        """
        trust_map = self.aggregate_fleet_trust(coordinator, site)
        if not trust_map:
            return {
                "mean_trust": 0.0, "min_trust": 0.0, "max_trust": 0.0,
                "low_trust_count": 0, "trust_histogram": [0, 0, 0, 0, 0],
            }
        values = list(trust_map.values())
        mean_t = sum(values) / len(values)
        min_t = min(values)
        max_t = max(values)
        low_count = sum(1 for v in values if v < 0.5)
        # 5-bucket histogram: [0,0.2), [0.2,0.4), [0.4,0.6), [0.6,0.8), [0.8,1.0]
        histogram = [0, 0, 0, 0, 0]
        for v in values:
            bucket = min(4, int(v * 5))
            histogram[bucket] += 1
        return {
            "mean_trust": round(mean_t, 4),
            "min_trust": round(min_t, 4),
            "max_trust": round(max_t, 4),
            "low_trust_count": low_count,
            "trust_histogram": histogram,
        }

    def trust_to_tier(self, trust: float) -> TrustTier:
        """Map a continuous trust score to a discrete :class:`TrustTier`.

        Parameters
        ----------
        trust : float
            Trust score in [0, 1].

        Returns
        -------
        TrustTier
            Corresponding trust tier.

        Notes
        -----
        Thresholds from Theory2.tex §8.3 Table 8.3:
        VERIFIED ≥ 0.90, HIGH ≥ 0.75, MEDIUM ≥ 0.50, LOW ≥ 0.30,
        UNTRUSTED < 0.30.

        Examples
        --------
        >>> TrustAggregator().trust_to_tier(0.95)
        <TrustTier.VERIFIED: 'verified'>
        """
        if trust >= 0.90:
            return TrustTier.VERIFIED
        if trust >= 0.75:
            return TrustTier.HIGH
        if trust >= 0.50:
            return TrustTier.MEDIUM
        if trust >= 0.30:
            return TrustTier.LOW
        return TrustTier.UNTRUSTED


# ---------------------------------------------------------------------------
# FleetMonitor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetMonitor:
    """Tracks status and evidence production of fleet members.

    Theory2.tex §8.3.

    Parameters
    ----------
    coordinator : FleetCoordinator or None
        The coordinator being monitored.  May be attached later via
        :meth:`attach`.
    event_log : list[dict[str, Any]]
        Ordered list of logged events.
    alert_thresholds : dict[str, float]
        Metric-name → threshold mapping used by :meth:`check_alerts`.
    """

    coordinator: FleetCoordinator | None = None
    event_log: list[dict[str, Any]] = field(default_factory=list)
    alert_thresholds: dict[str, float] = field(default_factory=dict)

    def attach(self, coordinator: FleetCoordinator) -> None:
        """Attach this monitor to a fleet coordinator.

        Parameters
        ----------
        coordinator : FleetCoordinator
            The coordinator to monitor.

        Returns
        -------
        None

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> coord = FleetCoordinator()
        >>> monitor.attach(coord)
        >>> monitor.coordinator is coord
        True
        """
        self.coordinator = coordinator

    def log_event(
        self, member_id: str, event_kind: str, data: dict[str, Any]
    ) -> None:
        """Append an event to the log with the current timestamp.

        Parameters
        ----------
        member_id : str
            The fleet member that generated the event.
        event_kind : str
            Short string categorising the event (e.g. ``"patch_started"``,
            ``"evidence_produced"``).
        data : dict[str, Any]
            Additional event data.

        Returns
        -------
        None

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> monitor.log_event("m1", "patch_started", {"patch_id": "p1"})
        >>> len(monitor.event_log)
        1
        """
        self.event_log.append({
            "member_id": member_id,
            "event_kind": event_kind,
            "timestamp": time.time(),
            "data": data,
        })

    def check_member_health(self, member_id: str) -> dict[str, Any]:
        """Return a health dict for a single fleet member.

        Parameters
        ----------
        member_id : str
            ID of the member to inspect.

        Returns
        -------
        dict[str, Any]
            Keys: ``status``, ``load``, ``patch_count``, ``evidence_count``,
            ``last_event_time``.

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1")
        >>> coord.add_member(m); monitor.attach(coord)
        >>> health = monitor.check_member_health("m1")
        >>> "status" in health
        True
        """
        if self.coordinator is None:
            return {"error": "no coordinator attached"}
        member = self.coordinator.get_member(member_id)
        if member is None:
            return {"error": f"member {member_id} not found"}
        member_events = [e for e in self.event_log if e["member_id"] == member_id]
        last_time = member_events[-1]["timestamp"] if member_events else None
        return {
            "status": member.status.value,
            "load": round(member.load_factor, 4),
            "patch_count": len(member.assigned_patches),
            "evidence_count": len(member.evidence_items),
            "last_event_time": last_time,
        }

    def check_fleet_health(self) -> dict[str, Any]:
        """Aggregate health status across all fleet members.

        Returns
        -------
        dict[str, Any]
            Keys: ``healthy_count``, ``degraded_count``, ``failed_count``,
            ``idle_count``, ``total_evidence``.

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> monitor.attach(FleetCoordinator())
        >>> monitor.check_fleet_health()["healthy_count"]
        0
        """
        if self.coordinator is None:
            return {"error": "no coordinator attached"}
        counts = {"healthy": 0, "degraded": 0, "failed": 0, "idle": 0}
        total_evidence = 0
        for member in self.coordinator.fleet:
            if member.status in (FleetStatus.ACTIVE, FleetStatus.COMPLETED):
                counts["healthy"] += 1
            elif member.status == FleetStatus.DEGRADED:
                counts["degraded"] += 1
            elif member.status == FleetStatus.FAILED:
                counts["failed"] += 1
            elif member.status == FleetStatus.IDLE:
                counts["idle"] += 1
            total_evidence += len(member.evidence_items)
        return {
            "healthy_count": counts["healthy"],
            "degraded_count": counts["degraded"],
            "failed_count": counts["failed"],
            "idle_count": counts["idle"],
            "total_evidence": total_evidence,
        }

    def get_events(
        self,
        member_id: str | None = None,
        event_kind: str | None = None,
        since: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return filtered events from the event log.

        Parameters
        ----------
        member_id : str or None
            If given, restrict to events from this member.
        event_kind : str or None
            If given, restrict to events of this kind.
        since : float or None
            If given, restrict to events with timestamp >= since (Unix time).

        Returns
        -------
        list[dict[str, Any]]
            Matching events in chronological order.

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> monitor.log_event("m1", "ping", {})
        >>> len(monitor.get_events(member_id="m1"))
        1
        """
        events = self.event_log
        if member_id is not None:
            events = [e for e in events if e["member_id"] == member_id]
        if event_kind is not None:
            events = [e for e in events if e["event_kind"] == event_kind]
        if since is not None:
            events = [e for e in events if e["timestamp"] >= since]
        return events

    def set_alert(self, metric: str, threshold: float) -> None:
        """Register an alert threshold for a named metric.

        Parameters
        ----------
        metric : str
            Metric name (e.g. ``"load"``, ``"failed_count"``).
        threshold : float
            Trigger value; alert fires when metric value >= threshold.

        Returns
        -------
        None

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> monitor.set_alert("load", 0.9)
        >>> monitor.alert_thresholds["load"]
        0.9
        """
        self.alert_thresholds[metric] = threshold

    def check_alerts(self) -> list[dict[str, Any]]:
        """Check all registered thresholds and return triggered alerts.

        Returns
        -------
        list[dict[str, Any]]
            Each dict has keys ``metric``, ``threshold``, ``current_value``,
            ``triggered_at``.

        Notes
        -----
        Supported metrics: ``load`` (max member load), ``failed_count``,
        ``idle_count``, ``uncovered_patches``.

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> monitor.set_alert("failed_count", 1)
        >>> monitor.check_alerts()  # returns alerts if failed members exist
        []
        """
        alerts: list[dict[str, Any]] = []
        if self.coordinator is None:
            return alerts
        health = self.check_fleet_health()
        metrics: dict[str, float] = {
            "load": max(
                (m.load_factor for m in self.coordinator.fleet), default=0.0
            ),
            "failed_count": float(health.get("failed_count", 0)),
            "idle_count": float(health.get("idle_count", 0)),
            "uncovered_patches": float(len(self.coordinator.uncovered_patches())),
        }
        for metric, threshold in self.alert_thresholds.items():
            value = metrics.get(metric, 0.0)
            if value >= threshold:
                alerts.append({
                    "metric": metric,
                    "threshold": threshold,
                    "current_value": value,
                    "triggered_at": time.time(),
                })
        return alerts

    def evidence_summary(self) -> dict[str, int]:
        """Return per-member evidence item counts.

        Returns
        -------
        dict[str, int]
            Mapping from member_id to number of evidence items produced.

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> monitor.attach(FleetCoordinator())
        >>> monitor.evidence_summary()
        {}
        """
        if self.coordinator is None:
            return {}
        return {m.member_id: len(m.evidence_items) for m in self.coordinator.fleet}

    def stalled_members(self) -> list[str]:
        """Return member IDs with no events in the last 60 seconds.

        Returns
        -------
        list[str]
            Member IDs of stalled members (ACTIVE or DEGRADED with no recent
            events).

        Notes
        -----
        A member is considered stalled if it has status ACTIVE or DEGRADED
        and either has no events at all or its most recent event was more
        than 60 seconds ago.

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> monitor.attach(FleetCoordinator())
        >>> monitor.stalled_members()
        []
        """
        if self.coordinator is None:
            return []
        cutoff = time.time() - 60.0
        stalled: list[str] = []
        for member in self.coordinator.fleet:
            if member.status not in (FleetStatus.ACTIVE, FleetStatus.DEGRADED):
                continue
            member_events = [
                e for e in self.event_log if e["member_id"] == member.member_id
            ]
            if not member_events or member_events[-1]["timestamp"] < cutoff:
                stalled.append(member.member_id)
        return stalled

    def force_status_update(
        self, member_id: str, status: FleetStatus
    ) -> None:
        """Forcibly update a fleet member's status.

        Parameters
        ----------
        member_id : str
            ID of the member to update.
        status : FleetStatus
            The new status value.

        Returns
        -------
        None

        Notes
        -----
        Logs a ``"forced_status_update"`` event.

        Examples
        --------
        >>> monitor = FleetMonitor()
        >>> coord = FleetCoordinator()
        >>> m = FleetMember(member_id="m1")
        >>> coord.add_member(m); monitor.attach(coord)
        >>> monitor.force_status_update("m1", FleetStatus.SUSPENDED)
        """
        if self.coordinator is None:
            return
        member = self.coordinator.get_member(member_id)
        if member is None:
            return
        old_status = member.status
        member.status = status
        self.log_event(member_id, "forced_status_update", {
            "old_status": old_status.value,
            "new_status": status.value,
        })


# ---------------------------------------------------------------------------
# FleetPlanner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetPlanner:
    """Plans an optimal fleet configuration for a project site and cover.

    Theory2.tex §8.3, Alg 8.20.

    Parameters
    ----------
    min_members_per_patch : int
        Minimum number of members required per patch (default 1).
    max_members_per_patch : int
        Maximum members to assign per patch (default 3).
    target_coverage_redundancy : float
        Target average number of members per patch (default 1.5).
    preferred_capabilities : list[str]
        Capability tags to prefer when generating member specs.
    """

    min_members_per_patch: int = 1
    max_members_per_patch: int = 3
    target_coverage_redundancy: float = 1.5
    preferred_capabilities: list[str] = field(default_factory=list)

    def plan(self, site: ProjectSite, cover: ModuleCover) -> dict[str, Any]:
        """Produce a complete fleet plan for a site and cover.

        Parameters
        ----------
        site : ProjectSite
            The project site to cover.
        cover : ModuleCover
            The module cover specifying patches and coordinates.

        Returns
        -------
        dict[str, Any]
            Keys: ``required_member_count``, ``member_specs``,
            ``patch_assignments``, ``estimated_trust``, ``coverage_plan``.

        Notes
        -----
        Algorithm (Theory2.tex Alg 8.20):
        1. Compute required count via :meth:`compute_required_count`.
        2. Generate member specs via :meth:`generate_member_specs`.
        3. Simulate the fleet via :meth:`simulate_fleet`.
        4. Evaluate and return the plan.

        Examples
        --------
        >>> planner = FleetPlanner()
        >>> plan = planner.plan(site, cover)
        >>> "required_member_count" in plan
        True
        """
        count = self.compute_required_count(cover)
        specs = self.generate_member_specs(cover, count)
        coordinator = self.simulate_fleet(specs, cover)
        metrics = self.evaluate_plan(coordinator, site)
        return {
            "required_member_count": count,
            "member_specs": specs,
            "patch_assignments": dict(coordinator.assignment_map),
            "estimated_trust": metrics.get("avg_trust", 0.7),
            "coverage_plan": metrics,
        }

    def compute_required_count(self, cover: ModuleCover) -> int:
        """Compute the required number of fleet members for a cover.

        Parameters
        ----------
        cover : ModuleCover
            The module cover to plan for.

        Returns
        -------
        int
            Estimated number of members needed.

        Notes
        -----
        Formula: ``ceil(n_patches * redundancy / avg_max_patches_per_member)``
        where avg_max_patches_per_member defaults to 5.

        Examples
        --------
        >>> planner = FleetPlanner(target_coverage_redundancy=2.0)
        >>> cover = ModuleCover(patches={f"p{i}": [] for i in range(10)})
        >>> planner.compute_required_count(cover)
        4
        """
        n_patches = cover.patch_count()
        avg_max = 5  # average max patches per member
        raw = n_patches * self.target_coverage_redundancy / avg_max
        return max(self.min_members_per_patch, math.ceil(raw))

    def generate_member_specs(
        self, cover: ModuleCover, count: int
    ) -> list[dict[str, Any]]:
        """Generate ``count`` member specification dicts for a cover.

        Parameters
        ----------
        cover : ModuleCover
            The module cover defining patch structure.
        count : int
            Number of member specs to generate.

        Returns
        -------
        list[dict[str, Any]]
            Each dict has keys: ``agent_kind``, ``capabilities``,
            ``suggested_patches``, ``max_patches``.

        Notes
        -----
        Patches are distributed round-robin across specs.  Capabilities
        are derived from coordinate ID prefixes plus ``preferred_capabilities``.

        Examples
        --------
        >>> planner = FleetPlanner(preferred_capabilities=["type_check"])
        >>> specs = planner.generate_member_specs(cover, 2)
        >>> len(specs)
        2
        """
        if count <= 0:
            return []
        patch_ids = cover.patch_ids()
        # Collect all capability tags from coordinates
        all_caps: set[str] = set(self.preferred_capabilities)
        for coords in cover.patches.values():
            for c in coords:
                all_caps.add(c.split(".")[0])
        all_caps_list = sorted(all_caps)

        specs: list[dict[str, Any]] = []
        for i in range(count):
            # Round-robin patch distribution
            suggested = [patch_ids[j] for j in range(i, len(patch_ids), count)]
            # Each member gets a rotating subset of capabilities
            cap_subset_size = max(1, len(all_caps_list) // max(count, 1) + 1)
            start = (i * cap_subset_size) % max(len(all_caps_list), 1)
            caps = all_caps_list[start: start + cap_subset_size]
            if not caps and all_caps_list:
                caps = all_caps_list[:1]
            specs.append({
                "agent_kind": "llm",
                "capabilities": caps,
                "suggested_patches": suggested,
                "max_patches": max(5, math.ceil(len(patch_ids) / max(count, 1)) + 2),
            })
        return specs

    def simulate_fleet(
        self, member_specs: list[dict[str, Any]], cover: ModuleCover
    ) -> FleetCoordinator:
        """Create a FleetCoordinator from specs and perform assignments.

        Parameters
        ----------
        member_specs : list[dict[str, Any]]
            Member specs as produced by :meth:`generate_member_specs`.
        cover : ModuleCover
            The module cover to assign.

        Returns
        -------
        FleetCoordinator
            A populated, fully-assigned coordinator.

        Examples
        --------
        >>> planner = FleetPlanner()
        >>> specs = planner.generate_member_specs(cover, 3)
        >>> coord = planner.simulate_fleet(specs, cover)
        >>> isinstance(coord, FleetCoordinator)
        True
        """
        coordinator = FleetCoordinator()
        coordinator.set_cover(cover)
        fleet = assemble_fleet(member_specs)
        for member in fleet:
            coordinator.add_member(member)
        # Assign suggested patches first, then fill remaining
        for member in fleet:
            suggested = member.metadata.get("suggested_patches", [])
            for pid in suggested:
                coordinator.assign(member.member_id, pid)
        # Fill remaining with round_robin
        lb = LoadBalancer()
        lb.balance(coordinator)
        return coordinator

    def evaluate_plan(
        self, coordinator: FleetCoordinator, site: ProjectSite
    ) -> dict[str, float]:
        """Compute quality metrics for a fleet plan.

        Parameters
        ----------
        coordinator : FleetCoordinator
            The simulated coordinator.
        site : ProjectSite
            The project site.

        Returns
        -------
        dict[str, float]
            Keys: ``coverage_fraction``, ``avg_load``, ``avg_trust``,
            ``redundancy_factor``.

        Examples
        --------
        >>> planner = FleetPlanner()
        >>> metrics = planner.evaluate_plan(coordinator, site)
        >>> 0.0 <= metrics["coverage_fraction"] <= 1.0
        True
        """
        n_patches = len(coordinator.cover.patches) if coordinator.cover else 0
        n_covered = n_patches - len(coordinator.uncovered_patches())
        coverage_fraction = n_covered / n_patches if n_patches > 0 else 0.0

        n_members = len(coordinator.fleet)
        avg_load = (
            sum(m.load_factor for m in coordinator.fleet) / n_members
            if n_members > 0 else 0.0
        )

        agg = TrustAggregator()
        trust_map = agg.aggregate_fleet_trust(coordinator, site)
        avg_trust = (
            sum(trust_map.values()) / len(trust_map) if trust_map else 0.7
        )

        # Redundancy: average assignments per patch
        total_assignments = sum(
            len(mids) for mids in coordinator.assignment_map.values()
        )
        redundancy_factor = total_assignments / n_patches if n_patches > 0 else 0.0

        return {
            "coverage_fraction": round(coverage_fraction, 4),
            "avg_load": round(avg_load, 4),
            "avg_trust": round(avg_trust, 4),
            "redundancy_factor": round(redundancy_factor, 4),
        }

    def optimize_plan(
        self, site: ProjectSite, cover: ModuleCover, iterations: int = 5
    ) -> dict[str, Any]:
        """Try multiple fleet configurations and return the best one.

        Parameters
        ----------
        site : ProjectSite
            The project site.
        cover : ModuleCover
            The module cover.
        iterations : int, optional
            Number of candidate configurations to try (default 5).

        Returns
        -------
        dict[str, Any]
            The plan dict from :meth:`plan` with the best evaluation score.

        Notes
        -----
        Score = coverage_fraction * 0.4 + avg_trust * 0.4 + (1 - avg_load) * 0.2.

        Examples
        --------
        >>> planner = FleetPlanner()
        >>> best = planner.optimize_plan(site, cover, iterations=3)
        >>> "required_member_count" in best
        True
        """
        base_count = self.compute_required_count(cover)
        best_plan: dict[str, Any] | None = None
        best_score = -1.0

        for i in range(iterations):
            count = max(1, base_count + i - iterations // 2)
            specs = self.generate_member_specs(cover, count)
            coordinator = self.simulate_fleet(specs, cover)
            metrics = self.evaluate_plan(coordinator, site)
            score = (
                metrics["coverage_fraction"] * 0.4
                + metrics["avg_trust"] * 0.4
                + (1.0 - metrics["avg_load"]) * 0.2
            )
            if score > best_score:
                best_score = score
                best_plan = {
                    "required_member_count": count,
                    "member_specs": specs,
                    "patch_assignments": dict(coordinator.assignment_map),
                    "estimated_trust": metrics.get("avg_trust", 0.7),
                    "coverage_plan": metrics,
                    "optimization_score": round(score, 4),
                }

        return best_plan or self.plan(site, cover)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def assemble_fleet(member_specs: list[dict[str, Any]]) -> list[FleetMember]:
    """Create FleetMember objects from specification dicts.

    Parameters
    ----------
    member_specs : list[dict[str, Any]]
        Each dict may contain: ``member_id``, ``agent_kind``,
        ``capabilities``, ``suggested_patches``, ``max_patches``,
        ``trust_level``, and any other key stored in ``metadata``.

    Returns
    -------
    list[FleetMember]
        Assembled fleet members in the same order as ``member_specs``.

    Notes
    -----
    Missing ``member_id`` values are auto-generated.  ``suggested_patches``
    is stored in ``member.metadata`` for later use by :class:`FleetPlanner`.

    Examples
    --------
    >>> specs = [{"agent_kind": "llm", "capabilities": ["auth"], "max_patches": 5}]
    >>> fleet = assemble_fleet(specs)
    >>> fleet[0].agent_kind
    'llm'
    """
    members: list[FleetMember] = []
    for spec in member_specs:
        member = FleetMember(
            member_id=spec.get("member_id", uuid.uuid4().hex[:16]),
            agent_kind=spec.get("agent_kind", "llm"),
            capabilities=list(spec.get("capabilities", [])),
            max_patches=int(spec.get("max_patches", 5)),
            trust_level=float(spec.get("trust_level", 0.7)),
            metadata={k: v for k, v in spec.items()
                      if k not in ("member_id", "agent_kind", "capabilities",
                                   "max_patches", "trust_level")},
        )
        members.append(member)
    return members


def assign_fleet_to_cover(
    fleet: list[FleetMember],
    cover: ModuleCover,
    strategy: str = "round_robin",
) -> FleetCoordinator:
    """Build a FleetCoordinator, add members, and assign all patches.

    Parameters
    ----------
    fleet : list[FleetMember]
        Fleet members to register.
    cover : ModuleCover
        Module cover to assign.
    strategy : str, optional
        Assignment strategy passed to :meth:`FleetCoordinator.assign_all`.

    Returns
    -------
    FleetCoordinator
        A populated coordinator with all possible assignments made.

    Examples
    --------
    >>> fleet = assemble_fleet([{"agent_kind": "llm"}])
    >>> coord = assign_fleet_to_cover(fleet, cover, "round_robin")
    >>> isinstance(coord, FleetCoordinator)
    True
    """
    coordinator = FleetCoordinator()
    coordinator.set_cover(cover)
    for member in fleet:
        coordinator.add_member(member)
    coordinator.assign_all(strategy=strategy)
    return coordinator


def compute_fleet_trust(
    coordinator: FleetCoordinator,
    site: ProjectSite,
    mode: str = "weighted_average",
) -> dict[str, float]:
    """Compute per-coordinate trust using TrustAggregator.

    Parameters
    ----------
    coordinator : FleetCoordinator
        The fleet coordinator with current assignments.
    site : ProjectSite
        The project site.
    mode : str, optional
        Aggregation mode (default ``"weighted_average"``).

    Returns
    -------
    dict[str, float]
        Mapping from coord_id to trust score in [0, 1].

    Examples
    --------
    >>> trust_map = compute_fleet_trust(coordinator, site, "minimum")
    >>> all(0 <= v <= 1 for v in trust_map.values())
    True
    """
    agg = TrustAggregator(aggregation_mode=mode)
    return agg.aggregate_fleet_trust(coordinator, site)


def plan_and_assemble_fleet(
    site: ProjectSite, cover: ModuleCover
) -> FleetCoordinator:
    """Full pipeline: plan, assemble, and assign a fleet.

    Parameters
    ----------
    site : ProjectSite
        The project site to cover.
    cover : ModuleCover
        The module cover defining patches.

    Returns
    -------
    FleetCoordinator
        A fully populated and assigned fleet coordinator.

    Notes
    -----
    Uses :class:`FleetPlanner` with default settings and the ``"round_robin"``
    assignment strategy.

    Examples
    --------
    >>> coord = plan_and_assemble_fleet(site, cover)
    >>> coord.is_fully_covered()  # True when enough members were planned
    True
    """
    planner = FleetPlanner()
    plan = planner.plan(site, cover)
    fleet = assemble_fleet(plan["member_specs"])
    return assign_fleet_to_cover(fleet, cover, strategy="least_loaded")


def generate_fleet_report(
    coordinator: FleetCoordinator, site: ProjectSite
) -> str:
    """Return a human-readable fleet report string.

    Parameters
    ----------
    coordinator : FleetCoordinator
        The fleet coordinator to report on.
    site : ProjectSite
        The project site.

    Returns
    -------
    str
        Multi-line human-readable report covering fleet status, member
        summary, trust summary, and validation errors.

    Examples
    --------
    >>> print(generate_fleet_report(coordinator, site))  # doctest: +SKIP
    === Fleet Report ===
    ...
    """
    lines: list[str] = ["=== Fleet Report ==="]
    status = coordinator.fleet_status()
    lines.append(f"Members   : {status['total_members']}")
    lines.append(f"Patches   : {status['covered_patches']}/{status['total_patches']} covered")
    lines.append(f"Avg Load  : {status['avg_load']:.2%}")
    lines.append(f"Overloaded: {status['overloaded_count']}")
    lines.append("")

    lines.append("--- Members ---")
    for member in coordinator.fleet:
        lines.append(
            f"  {member.member_id[:8]} [{member.agent_kind}] "
            f"status={member.status.value} load={member.load_factor:.2f} "
            f"patches={len(member.assigned_patches)}"
        )

    lines.append("")
    agg = TrustAggregator()
    summary = agg.compute_fleet_trust_summary(coordinator, site)
    lines.append("--- Trust Summary ---")
    lines.append(f"  Mean : {summary['mean_trust']:.3f}")
    lines.append(f"  Min  : {summary['min_trust']:.3f}")
    lines.append(f"  Max  : {summary['max_trust']:.3f}")
    lines.append(f"  Low-trust coords: {summary['low_trust_count']}")
    lines.append(f"  Histogram: {summary['trust_histogram']}")

    errors = coordinator.validate()
    if errors:
        lines.append("")
        lines.append("--- Validation Errors ---")
        for err in errors:
            lines.append(f"  [!] {err}")

    return "\n".join(lines)


# copilot: §8.3 fleet-structure implementation — FleetCoordinator, LoadBalancer,
# TrustAggregator, FleetMonitor, FleetPlanner are designed for LLM-assisted
# fleet management and trust accumulation workflows.
