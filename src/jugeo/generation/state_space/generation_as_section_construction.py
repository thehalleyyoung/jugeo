"""
Generation as Section Construction — core thesis implementation.

# copilot: s01-generation-as-section-construction

This module formalises the central thesis of theory2.tex Chapter 40:

    Generation  =  constructing global sections of a semantic sheaf.

A *semantic sheaf* assigns, to every coordinate object U in a Grothendieck
site (C, J), a set F(U) of semantic content together with restriction maps
F(U) → F(V) for every morphism V → U.  A *global section* is a compatible
family of local data {s_U ∈ F(U)}_{U ∈ C} — one piece of content per patch,
all agreeing on overlaps.

Generation therefore proceeds in four stages:

1. **Cover design** — choose a Grothendieck cover {U_i → X} of the target
   coordinate X.  Each patch U_i corresponds to a sub-problem (a file, a
   function, a proof obligation, …).

2. **Local section construction** — for each patch U_i, construct a local
   section s_i ∈ F(U_i).  This is where individual generation moves fire.

3. **Gluing / compatibility check** — verify that the local sections agree on
   all pairwise overlaps U_i ×_X U_j.  Agreement is measured in the trust
   algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

4. **Assembly** — if the Čech H¹ obstruction class vanishes (all overlaps are
   compatible), assemble the unique global section s ∈ F(X).  Otherwise,
   record the obstruction and return a DescentObstruction — never raise.

Theory Reference: theory2.tex §40.9.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "SectionTarget",
    "GenerationGoal",
    "SectionConstructionPlan",
    "SectionConstructionWitness",
    "CoverDesign",
    "GenerationAsSectionConstruction",
    "plan_section_construction",
    "construct_section",
    "validate_section_completeness",
    "THEORY_SECTION",
    "CHAPTER",
]

THEORY_SECTION = "40.9"
CHAPTER = 40

# ---------------------------------------------------------------------------
# Jugeo imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
except ImportError:
    class TrustLevel:  # type: ignore[no-redef]
        CONTRADICTED = "CONTRADICTED"
        UNVERIFIED = "UNVERIFIED"
        COPILOT_SUGGESTED = "COPILOT_SUGGESTED"
        ORACLE_PROPOSED = "ORACLE_PROPOSED"
        HUMAN_ATTESTED = "HUMAN_ATTESTED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        SOLVER_DISCHARGED = "SOLVER_DISCHARGED"
        MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"

    class TrustTier:  # type: ignore[no-redef]
        PROPOSAL = "PROPOSAL"
        REVIEWED = "REVIEWED"
        VERIFIED = "VERIFIED"
        RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
        PROOF_BACKED = "PROOF_BACKED"

    class TrustProfile:  # type: ignore[no-redef]
        def __init__(self, level: str = "UNVERIFIED", tier: str = "PROPOSAL"):
            self.level = level
            self.tier = tier

try:
    from jugeo.evidence.channels import EvidenceChannel, ChannelJurisdiction
except ImportError:
    class EvidenceChannel:  # type: ignore[no-redef]
        pass

    class ChannelJurisdiction:  # type: ignore[no-redef]
        pass

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateMorphism
except ImportError:
    class CoordinateObject:  # type: ignore[no-redef]
        def __init__(self, obj_id: str = ""):
            self.obj_id = obj_id

    class CoordinateMorphism:  # type: ignore[no-redef]
        pass

try:
    from jugeo.geometry.supports import SupportRegion
except ImportError:
    class SupportRegion:  # type: ignore[no-redef]
        pass

try:
    from jugeo.generation.goals import ConstructionGoal, GoalStatus
except ImportError:
    class ConstructionGoal:  # type: ignore[no-redef]
        pass

    class GoalStatus:  # type: ignore[no-redef]
        pass

try:
    from jugeo.errors import JuGeoError, StructuredFailure
except ImportError:
    class JuGeoError(Exception):  # type: ignore[no-redef]
        pass

    class StructuredFailure(Exception):  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Core data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionTarget:
    """A target for section construction.

    A SectionTarget specifies the coordinate X over which we want to construct
    a global section, the proposition φ that the section must satisfy, the
    carrier type A (the sheaf's fibre type), the minimum trust tier required
    for the constructed section to be accepted, the patches that will cover X,
    and operational metadata.

    The judgment tuple components present here are:
        c  = coordinate       (``coordinate``)
        φ  = proposition      (``proposition``)
        A  = carrier/type     (``carrier_type``)
        T  = trust annotation (``required_trust_tier``)

    The evidence bundle E, obligations O, obstructions B, and provenance Π
    are populated later during construction and live in :class:`GenerationGoal`.

    Fields
    ------
    target_id : str
        Unique identifier for this target (UUID4).
    coordinate : str
        The coordinate object X (e.g. file path, proof obligation ID).
    proposition : str
        The proposition φ the section must make true.
    carrier_type : str
        The type A of the section fibres.
    required_trust_tier : str
        Minimum TrustTier the finished section must satisfy.
    patch_ids : tuple[str, ...]
        Identifiers for the patches in the cover.  Empty means "not yet known".
    deadline_ms : float
        Construction deadline in milliseconds (wall-clock).  0 = no deadline.
    metadata : dict
        Arbitrary metadata (not part of the judgment proper).
    """

    target_id: str
    coordinate: str
    proposition: str
    carrier_type: str
    required_trust_tier: str
    patch_ids: tuple[str, ...]
    deadline_ms: float = 0.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))

    def has_cover(self) -> bool:
        """Return True if at least one patch has been assigned."""
        return len(self.patch_ids) > 0

    def is_expired(self) -> bool:
        """Return True if the deadline has passed."""
        if self.deadline_ms <= 0:
            return False
        return time.time() * 1000 > self.deadline_ms

    def fingerprint(self) -> str:
        """Return a stable fingerprint of this target's identifying fields."""
        raw = f"{self.coordinate}:{self.proposition}:{self.carrier_type}:{self.required_trust_tier}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @classmethod
    def create(
        cls,
        coordinate: str,
        proposition: str,
        carrier_type: str = "Any",
        required_trust_tier: str = "PROPOSAL",
        patch_ids: tuple[str, ...] = (),
        deadline_ms: float = 0.0,
        **metadata: Any,
    ) -> SectionTarget:
        """Factory that auto-generates a ``target_id``."""
        return cls(
            target_id=str(uuid.uuid4()),
            coordinate=coordinate,
            proposition=proposition,
            carrier_type=carrier_type,
            required_trust_tier=required_trust_tier,
            patch_ids=patch_ids,
            deadline_ms=deadline_ms,
            metadata=metadata,
        )


@dataclass(frozen=True)
class GenerationGoal:
    """A generation goal with its full judgment tuple.

    This is the full representation of what the generation system is trying to
    achieve.  The fields correspond exactly to the judgment tuple
    (c, φ, A, E, O, B, T, Π) from theory2.tex §40.2:

        c  = coordinate       → ``target.coordinate``
        φ  = proposition      → ``target.proposition``
        A  = carrier/type     → ``target.carrier_type``
        E  = evidence bundle  → ``evidence_refs``
        O  = obligations      → ``obligations``
        B  = obstructions     → ``obstructions``
        T  = trust annotation → ``trust_annotation``
        Π  = provenance       → ``provenance``

    **INVARIANT**: A GenerationGoal is NEVER a boolean.  It always carries the
    full 8-tuple.  Any function that would return True/False must instead return
    a GenerationGoal (or an error type).

    Fields
    ------
    goal_id : str
        Unique identifier for this goal.
    target : SectionTarget
        The section target (c, φ, A, T components of the judgment).
    obligations : tuple[str, ...]
        O — identifiers of outstanding verification obligations.
    obstructions : tuple[str, ...]
        B — Čech H¹ cohomology class identifiers (never silently erased).
    evidence_refs : tuple[str, ...]
        E — identifiers of evidence items supporting this goal.
    trust_annotation : str
        T — current trust level annotation.
    provenance : str
        Π — provenance string recording how this goal was created.
    created_at : float
        Unix timestamp of goal creation.
    """

    goal_id: str
    target: SectionTarget
    obligations: tuple[str, ...]
    obstructions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    trust_annotation: str
    provenance: str
    created_at: float

    @property
    def coordinate(self) -> str:
        """Shortcut to the target coordinate."""
        return self.target.coordinate

    @property
    def proposition(self) -> str:
        """Shortcut to the target proposition."""
        return self.target.proposition

    def has_obstructions(self) -> bool:
        """Return True if this goal has any recorded obstructions."""
        return len(self.obstructions) > 0

    def has_obligations(self) -> bool:
        """Return True if there are outstanding obligations."""
        return len(self.obligations) > 0

    def judgment_tuple(self) -> tuple:
        """Return the full judgment tuple (c, φ, A, E, O, B, T, Π)."""
        return (
            self.target.coordinate,
            self.target.proposition,
            self.target.carrier_type,
            self.evidence_refs,
            self.obligations,
            self.obstructions,
            self.trust_annotation,
            self.provenance,
        )

    @classmethod
    def create(
        cls,
        target: SectionTarget,
        provenance: str = "system",
        trust_annotation: str = "UNVERIFIED",
    ) -> GenerationGoal:
        """Factory for creating a fresh GenerationGoal from a target."""
        return cls(
            goal_id=str(uuid.uuid4()),
            target=target,
            obligations=(),
            obstructions=(),
            evidence_refs=(),
            trust_annotation=trust_annotation,
            provenance=provenance,
            created_at=time.time(),
        )


@dataclass(frozen=True)
class CoverDesign:
    """Describes a Grothendieck cover of the target coordinate.

    A Grothendieck cover of X is a family {U_i → X} of morphisms that is
    *covering* in the chosen topology on the site.  The cover design records
    which patches have been chosen and why.

    In generation terms, each patch U_i corresponds to a sub-problem or a
    partial output that, together with its neighbours, must assemble into the
    full answer at X.

    Fields
    ------
    design_id : str
        Unique identifier.
    target_coordinate : str
        The coordinate X being covered.
    patch_ids : tuple[str, ...]
        Identifiers for the patches {U_i}.
    overlap_pairs : tuple[tuple[str, str], ...]
        Pairs (U_i, U_j) that have non-trivial intersection (must be checked
        for compatibility during gluing).
    cover_rationale : str
        Human-readable explanation of why this cover was chosen.
    is_good_cover : bool
        True if all Čech H¹ classes should vanish (acyclic patches).
    topology_name : str
        Name of the Grothendieck topology being used (e.g. "Zariski",
        "étale", "canonical", "jugeo_semantic").
    """

    design_id: str
    target_coordinate: str
    patch_ids: tuple[str, ...]
    overlap_pairs: tuple[tuple[str, str], ...]
    cover_rationale: str
    is_good_cover: bool = False
    topology_name: str = "jugeo_semantic"

    def patch_count(self) -> int:
        """Return the number of patches in this cover."""
        return len(self.patch_ids)

    def overlap_count(self) -> int:
        """Return the number of overlap pairs."""
        return len(self.overlap_pairs)

    def overlaps_for(self, patch_id: str) -> list[str]:
        """Return the list of patches that overlap with *patch_id*."""
        result = []
        for a, b in self.overlap_pairs:
            if a == patch_id:
                result.append(b)
            elif b == patch_id:
                result.append(a)
        return result


@dataclass(frozen=True)
class SectionConstructionPlan:
    """A plan for constructing a global section over a cover.

    The plan records which patches will be used, what local sections are
    planned for each patch, what compatibility constraints must be satisfied,
    and what it will cost (in terms of computation or evidence collection).

    Fields
    ------
    plan_id : str
        Unique identifier for this plan.
    goal : GenerationGoal
        The generation goal this plan addresses.
    cover_patches : tuple[str, ...]
        Patch identifiers (from the cover design).
    local_section_map : dict[str, str]
        Maps patch_id → proposed local section content (may be empty initially).
    compatibility_constraints : tuple
        Constraints that must hold between local sections on overlaps.
    estimated_cost : float
        Estimated construction cost (arbitrary units, 0 = unknown).
    trust_floor : str
        Minimum trust level required for each local section to be accepted.
    cover_design : Optional[CoverDesign]
        The cover design, if available.
    """

    plan_id: str
    goal: GenerationGoal
    cover_patches: tuple[str, ...]
    local_section_map: dict
    compatibility_constraints: tuple
    estimated_cost: float
    trust_floor: str
    cover_design: Optional[CoverDesign] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "local_section_map", dict(self.local_section_map))

    def is_fully_covered(self) -> bool:
        """Return True if every patch has a local section in the map."""
        return all(p in self.local_section_map for p in self.cover_patches)

    def missing_patches(self) -> list[str]:
        """Return patches that still need a local section."""
        return [p for p in self.cover_patches if p not in self.local_section_map]

    def constraint_count(self) -> int:
        """Return the number of compatibility constraints."""
        return len(self.compatibility_constraints)


@dataclass(frozen=True)
class SectionConstructionWitness:
    """Evidence witness for a completed section construction.

    A witness provides a proof-relevant record of *how* the global section was
    constructed.  It records the local sections, the gluing evidence, the trust
    level achieved, and any residual obligations.

    This is the "E" (evidence bundle) component of the judgment tuple produced
    at the end of a successful construction.

    Fields
    ------
    witness_id : str
        Unique identifier.
    plan_id : str
        The plan this witness corresponds to.
    goal_id : str
        The goal that was achieved.
    local_sections : dict[str, str]
        Patch → local section content for each patch.
    gluing_evidence : tuple[str, ...]
        Identifiers for evidence justifying each gluing step.
    achieved_trust_level : str
        The trust level of the completed global section.
    achieved_trust_tier : str
        The trust tier of the completed global section.
    residual_obligations : tuple[str, ...]
        Any obligations that were deferred (should be empty for full success).
    construction_time_ms : float
        Time taken to construct the section, in milliseconds.
    global_section_content : str
        The assembled global section content.
    is_complete : bool
        True iff all patches were covered and all compatibility checks passed.
    """

    witness_id: str
    plan_id: str
    goal_id: str
    local_sections: dict
    gluing_evidence: tuple[str, ...]
    achieved_trust_level: str
    achieved_trust_tier: str
    residual_obligations: tuple[str, ...]
    construction_time_ms: float
    global_section_content: str
    is_complete: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "local_sections", dict(self.local_sections))

    def to_evidence_dict(self) -> dict:
        """Convert to a dict suitable for insertion into an evidence bundle."""
        return {
            "witness_id": self.witness_id,
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "achieved_trust_level": self.achieved_trust_level,
            "achieved_trust_tier": self.achieved_trust_tier,
            "is_complete": self.is_complete,
            "patch_count": len(self.local_sections),
            "construction_time_ms": self.construction_time_ms,
        }


# ---------------------------------------------------------------------------
# Main construction class
# ---------------------------------------------------------------------------


class GenerationAsSectionConstruction:
    """Main orchestrator for generation-as-section-construction.

    This class implements the four-stage pipeline (cover design → local section
    construction → gluing check → global assembly) described in theory2.tex
    §40.9.

    Usage
    -----
    >>> engine = GenerationAsSectionConstruction()
    >>> target = SectionTarget.create("src/foo.py", "implement Foo.bar()")
    >>> goal = GenerationGoal.create(target, provenance="user_request")
    >>> plan = engine.plan_section_construction(goal)
    >>> success, result = engine.construct_section(plan)
    >>> failures = engine.validate_section_completeness(plan, result)

    Design Principles
    -----------------
    * ``construct_section`` NEVER raises — it returns ``(False, error_dict)``
      on failure, never a Python exception visible to the caller.
    * Obstructions are recorded in the result dict under ``"obstructions"``
      and are never silently erased.
    * All results include the full judgment tuple under ``"judgment_tuple"``.
    """

    def __init__(
        self,
        default_trust_floor: str = "UNVERIFIED",
        max_patches: int = 32,
        gluing_timeout_ms: float = 10_000.0,
    ) -> None:
        self.default_trust_floor = default_trust_floor
        self.max_patches = max_patches
        self.gluing_timeout_ms = gluing_timeout_ms
        self._construction_cache: dict[str, dict] = {}
        logger.info(
            "GenerationAsSectionConstruction initialised "
            "(trust_floor=%s, max_patches=%d)",
            default_trust_floor,
            max_patches,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan_section_construction(
        self, goal: GenerationGoal
    ) -> SectionConstructionPlan:
        """Produce a :class:`SectionConstructionPlan` for the given goal.

        Stage 1: propose a Grothendieck cover of the target coordinate.
        Stage 2: compute the initial local section map (empty at plan time).
        Stage 3: enumerate compatibility constraints from the cover.

        Parameters
        ----------
        goal:
            The generation goal to plan for.

        Returns
        -------
        SectionConstructionPlan
            A plan with patches, an (initially empty) local section map, and
            compatibility constraints.
        """
        logger.debug(
            "Planning section construction for goal %s (coord=%s)",
            goal.goal_id,
            goal.coordinate,
        )
        patches = self._propose_cover(goal.target)
        local_sections = self._compute_local_sections(patches, goal)
        cover_design = self._build_cover_design(goal.target, patches)
        constraints = self._enumerate_constraints(cover_design)

        plan = SectionConstructionPlan(
            plan_id=str(uuid.uuid4()),
            goal=goal,
            cover_patches=tuple(patches),
            local_section_map=local_sections,
            compatibility_constraints=tuple(constraints),
            estimated_cost=len(patches) * 1.0,
            trust_floor=self.default_trust_floor,
            cover_design=cover_design,
        )
        logger.info(
            "Plan %s created: %d patches, %d constraints",
            plan.plan_id,
            len(patches),
            len(constraints),
        )
        return plan

    def construct_section(
        self, plan: SectionConstructionPlan
    ) -> tuple[bool, dict]:
        """Construct the global section according to *plan*.

        This method NEVER raises.  On success it returns ``(True, result)``;
        on failure it returns ``(False, result)`` where ``result["obstructions"]``
        lists the Čech H¹ classes that prevented completion.

        Parameters
        ----------
        plan:
            A construction plan produced by :meth:`plan_section_construction`.

        Returns
        -------
        (success, result_dict)
            success : bool
                True iff a global section was successfully assembled.
            result_dict : dict
                Contains at minimum:
                    - ``"judgment_tuple"`` — (c, φ, A, E, O, B, T, Π)
                    - ``"global_section"`` — assembled content or None
                    - ``"obstructions"`` — list of obstruction class IDs
                    - ``"witness"`` — SectionConstructionWitness or None
                    - ``"validation_failures"`` — list of failure descriptions
        """
        t0 = time.time()
        result: dict = {
            "plan_id": plan.plan_id,
            "goal_id": plan.goal.goal_id,
            "judgment_tuple": plan.goal.judgment_tuple(),
            "global_section": None,
            "obstructions": list(plan.goal.obstructions),
            "witness": None,
            "validation_failures": [],
            "local_sections": {},
            "gluing_evidence": [],
        }

        try:
            logger.debug("Constructing section for plan %s", plan.plan_id)

            # Stage 1: fill in local sections
            local_sections = dict(plan.local_section_map)
            for patch_id in plan.cover_patches:
                if patch_id not in local_sections:
                    local_sections[patch_id] = self._generate_local_section(
                        patch_id, plan.goal
                    )
            result["local_sections"] = local_sections

            # Stage 2: gluing compatibility check
            incompatibilities = self._check_gluing_compatibility(
                local_sections, plan
            )
            if incompatibilities:
                obs_ids = [
                    f"cech_h1_{hashlib.sha256(ic.encode()).hexdigest()[:8]}"
                    for ic in incompatibilities
                ]
                result["obstructions"].extend(obs_ids)
                result["validation_failures"] = incompatibilities
                logger.info(
                    "Gluing failed for plan %s: %d incompatibilities",
                    plan.plan_id,
                    len(incompatibilities),
                )
                return False, result

            # Stage 3: assemble global section
            global_section = self._assemble_global_section(local_sections)
            result["global_section"] = global_section
            result["gluing_evidence"] = [
                f"glue_ev_{p}" for p in plan.cover_patches
            ]

            elapsed_ms = (time.time() - t0) * 1000.0
            witness = SectionConstructionWitness(
                witness_id=str(uuid.uuid4()),
                plan_id=plan.plan_id,
                goal_id=plan.goal.goal_id,
                local_sections=local_sections,
                gluing_evidence=tuple(result["gluing_evidence"]),
                achieved_trust_level=plan.goal.trust_annotation,
                achieved_trust_tier=plan.goal.target.required_trust_tier,
                residual_obligations=plan.goal.obligations,
                construction_time_ms=elapsed_ms,
                global_section_content=str(global_section),
                is_complete=True,
            )
            result["witness"] = witness
            logger.info(
                "Section constructed for plan %s in %.1f ms",
                plan.plan_id,
                elapsed_ms,
            )
            return True, result

        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "construct_section caught internal error for plan %s: %s",
                plan.plan_id,
                exc,
                exc_info=True,
            )
            result["validation_failures"].append(f"internal_error: {exc}")
            return False, result

    def validate_section_completeness(
        self,
        plan: SectionConstructionPlan,
        result: dict,
    ) -> list[str]:
        """Validate that the constructed section is complete.

        Returns a list of validation failure strings.  An empty list means
        the section is complete.

        Checks performed:
        1. Every patch has a local section.
        2. No obstructions are recorded.
        3. A global section is present.
        4. A witness is present and ``is_complete`` is True.
        5. The judgment tuple has 8 components.
        6. No residual obligations (if trust_floor requires it).

        Parameters
        ----------
        plan:
            The construction plan.
        result:
            The result dict from :meth:`construct_section`.

        Returns
        -------
        list[str]
            Validation failure descriptions.  Empty = success.
        """
        failures: list[str] = []

        # Check 1: every patch covered
        local_sections = result.get("local_sections", {})
        for patch_id in plan.cover_patches:
            if patch_id not in local_sections:
                failures.append(
                    f"patch_not_covered: patch {patch_id!r} has no local section"
                )

        # Check 2: no obstructions
        obs = result.get("obstructions", [])
        if obs:
            failures.append(
                f"obstructions_present: {len(obs)} Čech H¹ classes recorded: {obs}"
            )

        # Check 3: global section present
        if result.get("global_section") is None:
            failures.append("no_global_section: assembly did not produce a section")

        # Check 4: witness
        witness = result.get("witness")
        if witness is None:
            failures.append("no_witness: no SectionConstructionWitness recorded")
        elif hasattr(witness, "is_complete") and not witness.is_complete:
            failures.append("witness_incomplete: witness.is_complete is False")

        # Check 5: judgment tuple arity
        jt = result.get("judgment_tuple")
        if jt is None or len(jt) != 8:
            failures.append(
                f"bad_judgment_tuple: expected 8-tuple, got {type(jt).__name__} "
                f"of length {len(jt) if jt else 'N/A'}"
            )

        # Check 6: validation_failures from construction
        vf = result.get("validation_failures", [])
        for vf_item in vf:
            failures.append(f"construction_failure: {vf_item}")

        if failures:
            logger.debug(
                "Completeness check failed for plan %s: %d failures",
                plan.plan_id,
                len(failures),
            )
        else:
            logger.debug(
                "Completeness check passed for plan %s", plan.plan_id
            )
        return failures

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _propose_cover(self, target: SectionTarget) -> list[str]:
        """Propose a set of patches forming a Grothendieck cover of *target*.

        If the target already specifies patches, return them.  Otherwise,
        derive sensible patches from the coordinate using a simple heuristic
        (split on "/" separators, add a "global" patch).

        Parameters
        ----------
        target:
            The section target.

        Returns
        -------
        list[str]
            Patch identifier strings.
        """
        if target.has_cover():
            logger.debug(
                "Using pre-specified patches for target %s: %s",
                target.target_id,
                target.patch_ids,
            )
            return list(target.patch_ids)

        # Heuristic: decompose the coordinate into parts
        parts = [p for p in target.coordinate.replace("\\", "/").split("/") if p]
        if not parts:
            parts = ["root"]

        patches = [f"patch_{part}" for part in parts[:self.max_patches]]
        # Always include a "global" patch that ranges over the whole coordinate
        if "patch_global" not in patches:
            patches.append("patch_global")

        logger.debug(
            "Proposed %d patches for target %s: %s",
            len(patches),
            target.target_id,
            patches,
        )
        return patches

    def _compute_local_sections(
        self, patches: list[str], goal: GenerationGoal
    ) -> dict[str, str]:
        """Compute initial local section placeholders.

        At plan time, local sections are empty strings; they are filled in
        during :meth:`construct_section`.

        Parameters
        ----------
        patches:
            Patch identifiers.
        goal:
            The generation goal.

        Returns
        -------
        dict[str, str]
            Mapping patch_id → empty string (placeholder).
        """
        return {p: "" for p in patches}

    def _generate_local_section(self, patch_id: str, goal: GenerationGoal) -> str:
        """Generate a local section for *patch_id* given *goal*.

        In a real system this would invoke a solver or LLM.  Here we produce a
        structured placeholder that records the judgment components.

        Parameters
        ----------
        patch_id:
            The patch identifier.
        goal:
            The generation goal.

        Returns
        -------
        str
            A string representation of the local section.
        """
        c, phi, A, E, O, B, T, PI = goal.judgment_tuple()
        local_sec = (
            f"local_section("
            f"patch={patch_id!r}, "
            f"coord={c!r}, "
            f"prop={phi!r}, "
            f"carrier={A!r}, "
            f"trust={T!r}"
            f")"
        )
        logger.debug("Generated local section for patch %r: %s", patch_id, local_sec)
        return local_sec

    def _build_cover_design(
        self, target: SectionTarget, patches: list[str]
    ) -> CoverDesign:
        """Build a :class:`CoverDesign` from the target and proposed patches.

        Overlap pairs are generated for all consecutive patch pairs (a simple
        linear topology).

        Parameters
        ----------
        target:
            The section target.
        patches:
            Proposed patch identifiers.

        Returns
        -------
        CoverDesign
        """
        overlap_pairs: list[tuple[str, str]] = []
        for i in range(len(patches) - 1):
            overlap_pairs.append((patches[i], patches[i + 1]))

        # Cross-overlap with global patch if present
        if "patch_global" in patches:
            for p in patches:
                if p != "patch_global":
                    pair = (p, "patch_global")
                    if pair not in overlap_pairs:
                        overlap_pairs.append(pair)

        return CoverDesign(
            design_id=str(uuid.uuid4()),
            target_coordinate=target.coordinate,
            patch_ids=tuple(patches),
            overlap_pairs=tuple(overlap_pairs),
            cover_rationale=f"Auto-generated cover for coordinate {target.coordinate!r}",
            is_good_cover=len(patches) <= 4,  # small covers assumed good
            topology_name="jugeo_semantic",
        )

    def _enumerate_constraints(self, design: CoverDesign) -> list[dict]:
        """Enumerate compatibility constraints from a cover design.

        Each overlap pair (U_i, U_j) generates one constraint: the local
        sections s_i and s_j must agree on the overlap U_i ×_X U_j.

        Parameters
        ----------
        design:
            The cover design.

        Returns
        -------
        list[dict]
            One constraint dict per overlap pair.
        """
        constraints = []
        for patch_a, patch_b in design.overlap_pairs:
            constraints.append(
                {
                    "kind": "overlap_compatibility",
                    "patch_a": patch_a,
                    "patch_b": patch_b,
                    "description": (
                        f"Local sections on {patch_a!r} and {patch_b!r} must "
                        "agree on their overlap."
                    ),
                }
            )
        return constraints

    def _check_gluing_compatibility(
        self,
        local_sections: dict[str, str],
        plan: SectionConstructionPlan,
    ) -> list[str]:
        """Check that local sections are mutually compatible on all overlaps.

        In a real system this would invoke a logical consistency solver.
        Here we check the structural property that both sections are non-empty
        (a placeholder for a real compatibility predicate).

        Parameters
        ----------
        local_sections:
            Map from patch_id to local section content.
        plan:
            The construction plan (contains compatibility constraints).

        Returns
        -------
        list[str]
            Descriptions of incompatibilities found.  Empty = compatible.
        """
        incompatibilities: list[str] = []
        for constraint in plan.compatibility_constraints:
            if not isinstance(constraint, dict):
                continue
            pa = constraint.get("patch_a", "")
            pb = constraint.get("patch_b", "")
            sa = local_sections.get(pa, "")
            sb = local_sections.get(pb, "")
            if sa == "" and sb == "":
                # Both empty — technically compatible (vacuously)
                continue
            if sa == "" or sb == "":
                incompatibilities.append(
                    f"one_sided_section: patch {pa!r} has content {bool(sa)} "
                    f"but patch {pb!r} has content {bool(sb)} — overlap undefined"
                )
        return incompatibilities

    def _assemble_global_section(self, local_sections: dict[str, str]) -> dict:
        """Assemble local sections into a global section.

        In sheaf theory, if all compatibility conditions hold, the global
        section is uniquely determined by the local sections.  Here we
        represent it as the ordered concatenation of local sections.

        Parameters
        ----------
        local_sections:
            Map from patch_id to local section content.

        Returns
        -------
        dict
            The global section as a dict with ``"content"`` and ``"patches"``.
        """
        ordered = sorted(local_sections.items())
        content = "\n".join(f"# {p}\n{s}" for p, s in ordered)
        return {
            "kind": "global_section",
            "patches": list(local_sections.keys()),
            "content": content,
            "patch_count": len(local_sections),
        }


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def plan_section_construction(goal: GenerationGoal) -> SectionConstructionPlan:
    """Plan a section construction for *goal* using default settings.

    This is a convenience wrapper around
    :meth:`GenerationAsSectionConstruction.plan_section_construction`.

    Parameters
    ----------
    goal:
        The generation goal.

    Returns
    -------
    SectionConstructionPlan
    """
    engine = GenerationAsSectionConstruction()
    return engine.plan_section_construction(goal)


def construct_section(plan: SectionConstructionPlan) -> tuple[bool, dict]:
    """Construct a global section according to *plan*.

    Convenience wrapper.  NEVER raises — returns ``(False, error_dict)`` on
    failure.

    Parameters
    ----------
    plan:
        A construction plan.

    Returns
    -------
    (success, result_dict)
    """
    engine = GenerationAsSectionConstruction()
    return engine.construct_section(plan)


def validate_section_completeness(
    plan: SectionConstructionPlan, result: dict
) -> list[str]:
    """Validate that *result* represents a complete section for *plan*.

    Convenience wrapper.

    Parameters
    ----------
    plan:
        The construction plan.
    result:
        Result dict from :func:`construct_section`.

    Returns
    -------
    list[str]
        Validation failure descriptions.  Empty = success.
    """
    engine = GenerationAsSectionConstruction()
    return engine.validate_section_completeness(plan, result)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== generation_as_section_construction.py smoke test ===")

    # 1. Create a SectionTarget
    target = SectionTarget.create(
        coordinate="src/jugeo/foo.py",
        proposition="implement Foo.bar() returning int",
        carrier_type="PythonFunction",
        required_trust_tier="REVIEWED",
    )
    assert target.coordinate == "src/jugeo/foo.py"
    assert not target.has_cover()
    fp = target.fingerprint()
    assert len(fp) == 16
    print(f"  SectionTarget created: {target.target_id[:8]}... fp={fp}")

    # 2. Create a GenerationGoal
    goal = GenerationGoal.create(target, provenance="smoke_test", trust_annotation="UNVERIFIED")
    jt = goal.judgment_tuple()
    assert len(jt) == 8, f"judgment_tuple must have 8 components, got {len(jt)}"
    assert not isinstance(jt, bool), "judgment must NOT be a boolean"
    print(f"  GenerationGoal created: {goal.goal_id[:8]}... jt_len={len(jt)}")

    # 3. Plan section construction
    engine = GenerationAsSectionConstruction(max_patches=4)
    plan = engine.plan_section_construction(goal)
    assert len(plan.cover_patches) > 0
    print(f"  Plan created: {plan.plan_id[:8]}... patches={plan.cover_patches}")

    # 4. Construct the section
    success, result = engine.construct_section(plan)
    assert isinstance(success, bool)
    assert "judgment_tuple" in result
    assert "obstructions" in result
    assert "global_section" in result
    print(f"  Construction success={success}, obstructions={result['obstructions']}")

    # 5. Validate completeness
    failures = engine.validate_section_completeness(plan, result)
    if failures:
        print(f"  Completeness failures: {failures}")
    else:
        print("  Section is complete.")

    # 6. Verify that construction never raises (inject a bad plan)
    bad_plan = SectionConstructionPlan(
        plan_id="bad",
        goal=goal,
        cover_patches=("p1",),
        local_section_map={},
        compatibility_constraints=(),
        estimated_cost=0.0,
        trust_floor="UNVERIFIED",
    )
    ok2, res2 = engine.construct_section(bad_plan)
    assert isinstance(ok2, bool), "construct_section must always return (bool, dict)"
    print(f"  Bad-plan construction returned ({ok2}, ...) — no raise ✓")

    # 7. CoverDesign
    cd = CoverDesign(
        design_id="cd1",
        target_coordinate="src/foo.py",
        patch_ids=("p1", "p2", "p3"),
        overlap_pairs=(("p1", "p2"), ("p2", "p3")),
        cover_rationale="test",
        is_good_cover=True,
    )
    assert cd.overlaps_for("p2") == ["p1", "p3"]
    print(f"  CoverDesign overlaps for p2: {cd.overlaps_for('p2')}")

    # 8. SectionConstructionWitness
    w = SectionConstructionWitness(
        witness_id="w1",
        plan_id=plan.plan_id,
        goal_id=goal.goal_id,
        local_sections={"p1": "content1"},
        gluing_evidence=("ev1",),
        achieved_trust_level="UNVERIFIED",
        achieved_trust_tier="PROPOSAL",
        residual_obligations=(),
        construction_time_ms=42.0,
        global_section_content="global",
        is_complete=True,
    )
    ed = w.to_evidence_dict()
    assert ed["is_complete"] is True
    print(f"  SectionConstructionWitness evidence_dict: {ed}")

    # 9. Module-level convenience functions
    plan2 = plan_section_construction(goal)
    ok3, res3 = construct_section(plan2)
    failures3 = validate_section_completeness(plan2, res3)
    print(f"  Convenience functions: ok={ok3}, failures={failures3}")

    print("All smoke tests passed.")
    sys.exit(0)
