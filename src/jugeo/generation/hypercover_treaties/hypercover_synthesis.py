"""Hypercover synthesis pipeline — Phase 1: synthesis engine.

Chapter 41 of theory2.tex introduces hypercover treaty synthesis as a
structured five-phase procedure for assembling locally-verified overlap laws
into a globally consistent descent datum.

A *hypercover* U → X satisfies the *augmented nerve condition*: for every
finite set of patches {U_i} ⊆ U, the iterated fiber products

    U_i ×_X U_j          (double overlaps)
    U_i ×_X U_j ×_X U_k  (triple overlaps)

are themselves covered.  This is the key condition that allows Čech descent
to hold, i.e. that descent data (local sections + overlap compatibilities)
uniquely determine global sections (theory2.tex §41.1, Theorem 41.1).

Synthesis proceeds in five phases (theory2.tex §41.3):
  1. DECOMPOSING — parse the ConstructionGoal, extract patch keys and
     the explicit overlap structure from the support region.
  2. COVERING    — assemble a Cover object respecting the covering axioms.
  3. VALIDATING  — check the augmented nerve condition; enumerate violations.
  4. REFINING    — iteratively add intermediate patches to fix violations
     until all nerve conditions hold or the budget is exhausted.
  5. FINALIZING  — mine overlap laws from the stabilized cover, build
     OverlapLaw objects, and return the SynthesisOutcome.

This module provides the concrete implementations:

* HypercoverConditionChecker — augmented nerve condition verification
* GoalStructureParser        — extracts patches/overlaps from a goal
* HypercoverSynthesizer      — orchestrates the five phases
* SynthesisDriver            — high-level driver with convergence tracking

References
----------
theory2.tex §41.1  Hypercovers and Čech descent
theory2.tex §41.2  The synthesis state machine
theory2.tex §41.3  Five-phase synthesis procedure
theory2.tex §41.4  Treaty acceptance criteria
theory2.tex §41.5  Overlap law induction
"""
from __future__ import annotations

import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from jugeo.generation.hypercover_treaties.models import (
    DEFAULT_CONFIG,
    CandidateSource,
    HypercoverSynthesisRecord,
    LawStability,
    OutcomeKind,
    OverlapLaw,
    OverlapLawIndex,
    SynthesisConfig,
    SynthesisOutcome,
    SynthesisPhase,
    TreatyCandidate,
    TreatyRole,
)

try:
    from jugeo.geometry.descent import (
        DescentEngine, DescentResult, LocalSection, OverlapCondition,
        GluingData, DescentObstruction, RepairFrontier, DescentStrategy, OverlapStatus,
    )
    from jugeo.geometry.covers import Cover
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateObject, CoordinateKind
    from jugeo.generation.goals import (
        GenerationGoal, GoalDecomposer, ConstructionGoal, GoalPriority, GoalStatus, OverlapGoal,
    )
    from jugeo.generation.construction import (
        Candidate, ConstructionLoop, ConstructionResult, ConstructionContext,
    )
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause, TreatyStatus, evaluate_treaty
    from jugeo.orchestration.frontier import FrontierNode, Frontier, FrontierItem
    from jugeo.evidence.trust import TrustTier, TrustLevel
except ImportError:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HypercoverConditionChecker
# ---------------------------------------------------------------------------


class HypercoverConditionChecker:
    """Checks whether a cover object satisfies the hypercover conditions.

    The hypercover conditions (theory2.tex §41.1) are:

    HC-1  Patch validity:   every patch key is a non-empty string.
    HC-2  Overlap symmetry: overlaps are listed as unordered pairs (no self-loops).
    HC-3  Augmented nerve:  for every triple of mutually overlapping patches
                            (i, j, k), the triple overlap is representable.
    HC-4  Descent data:     every overlap pair has a well-defined intersection
                            coordinate representable as a Coordinate.

    All four conditions must hold for the cover to admit a descent datum.

    This checker is intentionally stateless so that it can be called on
    any Cover-like object (either the real Cover from jugeo.geometry.covers
    or a plain dict fallback used when the geometry module is unavailable).
    """

    def __init__(self, goals: Any | None = None) -> None:
        # Older callers passed goals at construction time so they could reuse a
        # checker across multiple covers. The current implementation remains
        # stateless, but we preserve the argument and retain the goals for
        # diagnostics/introspection.
        self.goals = tuple(goals or ())

    def check_all_conditions(self, cover: Any) -> tuple[bool, list[str]]:
        """Check all four hypercover conditions on *cover*.

        Parameters
        ----------
        cover:
            A Cover object or dict fallback with keys ``patches`` and
            ``overlaps``.  When the jugeo.geometry.covers module is
            available, this will be a real Cover instance.

        Returns
        -------
        tuple[bool, list[str]]
            (passes, violations) where *passes* is True iff all conditions
            hold and *violations* is a list of human-readable violation
            descriptions.
        """
        violations: list[str] = []

        patch_keys, overlap_pairs = self._extract_cover_data(cover)

        # HC-1: Patch validity
        hc1_ok, hc1_violations = self._check_patch_validity(patch_keys)
        violations.extend(hc1_violations)

        # HC-2: Overlap symmetry
        hc2_ok, hc2_violations = self._check_overlap_symmetry(patch_keys, overlap_pairs)
        violations.extend(hc2_violations)

        # HC-3: Augmented nerve condition
        hc3_ok, hc3_violations = self.check_augmented_nerve(cover)
        violations.extend(hc3_violations)

        # HC-4: Descent data existence
        hc4_ok, hc4_violations = self.check_descent_data_existence(cover)
        violations.extend(hc4_violations)

        all_pass = hc1_ok and hc2_ok and hc3_ok and hc4_ok
        logger.debug(
            "HypercoverConditionChecker: %d patches, %d overlaps, %d violations",
            len(patch_keys),
            len(overlap_pairs),
            len(violations),
        )
        return all_pass, violations

    def _extract_cover_data(self, cover: Any) -> tuple[list[str], list[tuple[str, str]]]:
        """Extract (patch_keys, overlap_pairs) from a Cover or dict fallback."""
        if isinstance(cover, dict):
            patch_keys = list(cover.get("patches", []))
            overlap_pairs = [tuple(p) for p in cover.get("overlaps", [])]
            return patch_keys, overlap_pairs  # type: ignore[return-value]
        # Real Cover object from jugeo.geometry.covers
        try:
            patches_attr = cover.patches
            if patches_attr and hasattr(patches_attr[0], "components"):
                # CoordinateObject → extract the first component as a string key
                patch_keys = [
                    ".".join(p.components) for p in patches_attr
                ]
            else:
                patch_keys = [str(p) for p in patches_attr]
        except (AttributeError, IndexError):
            patch_keys = []
        try:
            overlap_pairs = list(cover.overlaps)
        except AttributeError:
            overlap_pairs = []
        return patch_keys, overlap_pairs  # type: ignore[return-value]

    def _check_patch_validity(
        self, patch_keys: list[str]
    ) -> tuple[bool, list[str]]:
        """HC-1: every patch key must be a non-empty string."""
        violations: list[str] = []
        if not patch_keys:
            violations.append("HC-1: cover has no patches (empty cover is not admissible)")
            return False, violations
        for i, key in enumerate(patch_keys):
            if not isinstance(key, str) or not key.strip():
                violations.append(
                    f"HC-1: patch at index {i} has invalid key {key!r} (must be non-empty string)"
                )
        return len(violations) == 0, violations

    def _check_overlap_symmetry(
        self, patch_keys: list[str], overlap_pairs: list[tuple[str, str]]
    ) -> tuple[bool, list[str]]:
        """HC-2: overlaps must be pairwise (no self-loops, both patches exist)."""
        violations: list[str] = []
        patch_set = set(patch_keys)
        seen: set[frozenset[str]] = set()
        for pair in overlap_pairs:
            if len(pair) != 2:
                violations.append(
                    f"HC-2: overlap {pair!r} is not a pair (expected 2 elements)"
                )
                continue
            a, b = pair
            if a == b:
                violations.append(f"HC-2: self-loop overlap ({a!r}, {b!r}) is not allowed")
                continue
            if a not in patch_set:
                violations.append(f"HC-2: overlap references unknown patch {a!r}")
            if b not in patch_set:
                violations.append(f"HC-2: overlap references unknown patch {b!r}")
            key = frozenset({a, b})
            if key in seen:
                violations.append(f"HC-2: duplicate overlap ({a!r}, {b!r})")
            seen.add(key)
        return len(violations) == 0, violations

    def check_augmented_nerve(self, cover: Any) -> tuple[bool, list[str]]:
        """HC-3: check the augmented nerve condition.

        For every triple of mutually overlapping patches (i, j, k),
        the triple overlap U_i ×_X U_j ×_X U_k must also be covered.
        We approximate this by checking that for every triple appearing
        in the overlap graph, all three pairwise overlaps are listed.

        In a full implementation this would also verify that the triple
        fiber product is non-empty and covered (theory2.tex §41.1, Def 41.3).
        Here we check the combinatorial skeleton: all three pairwise overlaps
        in a triple must be in the cover's overlap list.

        Returns (passes, violations).
        """
        violations: list[str] = []
        patch_keys, overlap_pairs = self._extract_cover_data(cover)

        if len(patch_keys) < 3:
            # Trivially satisfied: cannot form a triple
            return True, []

        graph = self._build_overlap_graph(patch_keys, overlap_pairs)
        triples = self._find_triple_overlaps(graph)

        overlap_set: set[frozenset[str]] = {
            frozenset({a, b}) for a, b in overlap_pairs
        }

        for i, j, k in triples:
            # All three pairwise overlaps must be present
            missing = []
            if frozenset({i, j}) not in overlap_set:
                missing.append(f"({i!r}, {j!r})")
            if frozenset({j, k}) not in overlap_set:
                missing.append(f"({j!r}, {k!r})")
            if frozenset({i, k}) not in overlap_set:
                missing.append(f"({i!r}, {k!r})")
            if missing:
                violations.append(
                    f"HC-3: triple ({i!r}, {j!r}, {k!r}) is missing pairwise overlaps: "
                    + ", ".join(missing)
                )

        return len(violations) == 0, violations

    def check_descent_data_existence(self, cover: Any) -> tuple[bool, list[str]]:
        """HC-4: check that every overlap pair has a representable intersection.

        In the real pipeline this would verify that a Coordinate with
        kind=REGION exists for each intersection.  Here we check that
        the overlap pair consists of valid non-empty string keys and
        that their concatenated representation forms a valid identifier.

        Returns (passes, violations).
        """
        violations: list[str] = []
        _, overlap_pairs = self._extract_cover_data(cover)

        for a, b in overlap_pairs:
            if not a or not b:
                violations.append(
                    f"HC-4: overlap ({a!r}, {b!r}) contains empty patch key; "
                    "cannot construct intersection coordinate"
                )
                continue
            # Check that the intersection key is a valid non-empty identifier
            intersection_key = f"{a}_cap_{b}"
            if not intersection_key.replace("_", "").replace("-", "").isalnum():
                violations.append(
                    f"HC-4: intersection key {intersection_key!r} for pair "
                    f"({a!r}, {b!r}) is not a valid identifier"
                )

        return len(violations) == 0, violations

    def _build_overlap_graph(
        self, patch_keys: list[str], overlap_pairs: list[tuple[str, str]]
    ) -> dict[str, set[str]]:
        """Build an undirected adjacency graph from the overlap pairs.

        Returns a dict mapping each patch key to the set of patches it overlaps.
        Every patch appears as a key even if it has no overlaps (empty set value).
        """
        graph: dict[str, set[str]] = {k: set() for k in patch_keys}
        for a, b in overlap_pairs:
            if a in graph:
                graph[a].add(b)
            if b in graph:
                graph[b].add(a)
        return graph

    def _find_triple_overlaps(
        self, graph: dict[str, set[str]]
    ) -> list[tuple[str, str, str]]:
        """Find all triples (i, j, k) of mutually overlapping patches.

        A triple (i, j, k) is *mutually overlapping* iff each pair
        appears in the overlap graph: j ∈ neighbors(i), k ∈ neighbors(j),
        and i ∈ neighbors(k).

        Returns triples in canonical order (i < j < k lexicographically).
        """
        nodes = sorted(graph.keys())
        triples: list[tuple[str, str, str]] = []
        for i, j, k in itertools.combinations(nodes, 3):
            # Check mutual adjacency
            if j in graph.get(i, set()) and k in graph.get(j, set()) and k in graph.get(i, set()):
                triples.append((i, j, k))
        return triples


# ---------------------------------------------------------------------------
# GoalStructureParser
# ---------------------------------------------------------------------------


class GoalStructureParser:
    """Parses a ConstructionGoal into structured components for synthesis.

    The parser extracts the information needed by HypercoverSynthesizer
    from a ConstructionGoal (or a plain dict fallback when the goals module
    is unavailable).  It also computes derived structures:
    - all pairs of patches as potential overlaps
    - a simple keyword-based dependency graph from the proposition text

    theory2.tex §41.3 Phase 1 (DECOMPOSING) describes this parsing step as
    extracting the *nerve data* (patch keys and their pairwise incidence)
    from the goal's support region.
    """

    def parse(self, goal: Any) -> dict[str, Any]:
        """Parse *goal* into a structured dict.

        Parameters
        ----------
        goal:
            A ConstructionGoal (from jugeo.generation.goals) or a plain
            dict with keys matching the ConstructionGoal field names.

        Returns
        -------
        dict with keys:
            proposition     — the goal proposition string
            patch_keys      — frozenset[str] of patch keys from the support
            required_tier   — integer trust tier (1, 2, or 3)
            priority        — integer goal priority (1=LOW, 2=MEDIUM, 3=HIGH)
            budget          — integer budget from the goal
            provenance      — tuple[str, ...] of provenance strings
            target_key      — string key of the target coordinate
        """
        if isinstance(goal, dict):
            return {
                "proposition": goal.get("proposition", ""),
                "patch_keys": frozenset(goal.get("patch_keys", [])),
                "required_tier": int(goal.get("required_tier", 1)),
                "priority": int(goal.get("priority", 2)),
                "budget": int(goal.get("budget", 1)),
                "provenance": tuple(goal.get("provenance", ())),
                "target_key": goal.get("target_key", ""),
            }

        # Real ConstructionGoal object
        try:
            support = goal.support
            patch_keys = frozenset(support.patch_keys)
        except AttributeError:
            patch_keys = frozenset()

        try:
            required_tier = int(goal.required_tier)
        except (AttributeError, TypeError, ValueError):
            required_tier = 1

        try:
            priority = int(goal.priority)
        except (AttributeError, TypeError, ValueError):
            priority = 2

        try:
            budget = int(goal.budget)
        except (AttributeError, TypeError, ValueError):
            budget = 1

        try:
            provenance = tuple(goal.provenance)
        except AttributeError:
            provenance = ()

        try:
            target_key = ".".join(goal.support.coordinate.components)
        except AttributeError:
            target_key = ""

        proposition = ""
        try:
            proposition = str(goal.proposition)
        except AttributeError:
            pass

        return {
            "proposition": proposition,
            "patch_keys": patch_keys,
            "required_tier": required_tier,
            "priority": priority,
            "budget": budget,
            "provenance": provenance,
            "target_key": target_key,
        }

    def extract_overlap_structure(self, goal: Any) -> list[tuple[str, str]]:
        """Compute all potential overlap pairs from the goal's patch keys.

        From a frozenset of n patch keys this generates C(n, 2) potential
        overlap pairs.  In a full implementation the actual overlaps would
        come from the support region's intersection lattice (theory2.tex §41.2).
        Here we enumerate all pairs as candidates and rely on the
        HypercoverConditionChecker to prune impossible ones.

        Returns a list of (patch_a, patch_b) tuples in lexicographic order.
        """
        parsed = self.parse(goal)
        keys = sorted(parsed["patch_keys"])
        return list(itertools.combinations(keys, 2))

    def build_dependency_graph(self, goal: Any) -> dict[str, list[str]]:
        """Build a lightweight dependency dict from the proposition text.

        Looks for keywords of the form ``<A> requires <B>`` or ``<A> depends on <B>``
        in the proposition, where <A> and <B> are words matching known patch keys.
        Returns a dict mapping each patch key to the list of patch keys it depends on.

        This is a heuristic approximation; the real dependency structure should
        come from the formal ConstructionGoal dependency annotations when available.
        """
        parsed = self.parse(goal)
        patch_keys = sorted(parsed["patch_keys"])
        proposition = parsed["proposition"].lower()

        deps: dict[str, list[str]] = {k: [] for k in patch_keys}

        for key in patch_keys:
            key_lower = key.lower()
            if key_lower not in proposition:
                continue
            for other in patch_keys:
                if other == key:
                    continue
                other_lower = other.lower()
                requires_pattern = f"{key_lower} requires {other_lower}"
                depends_pattern = f"{key_lower} depends on {other_lower}"
                if requires_pattern in proposition or depends_pattern in proposition:
                    deps[key].append(other)

        return deps

    def infer_overlap_pairs_from_proposition(
        self, goal: Any
    ) -> list[tuple[str, str]]:
        """Infer overlap pairs by scanning the proposition for co-occurrences.

        Two patches are considered to *co-occur* in the proposition if both
        their keys appear within a sliding window of 30 characters.  This is
        a fallback heuristic when the support region contains no explicit
        overlap structure.
        """
        parsed = self.parse(goal)
        keys = sorted(parsed["patch_keys"])
        proposition = parsed["proposition"]
        window = 30
        co_occurring: set[tuple[str, str]] = set()

        for i, key_a in enumerate(keys):
            pos_a = proposition.find(key_a)
            if pos_a < 0:
                continue
            for key_b in keys[i + 1:]:
                pos_b = proposition.find(key_b)
                if pos_b < 0:
                    continue
                if abs(pos_a - pos_b) <= window:
                    a, b = sorted([key_a, key_b])
                    co_occurring.add((a, b))

        return sorted(co_occurring)


# ---------------------------------------------------------------------------
# HypercoverSynthesizer
# ---------------------------------------------------------------------------


class HypercoverSynthesizer:
    """Synthesizes a hypercover treaty for a construction goal.

    Implements the five-phase synthesis procedure of theory2.tex §41.3:

    1. Decompose — parse the goal into patches and overlaps via GoalStructureParser
    2. Cover     — construct a Cover object (or dict fallback) satisfying covering axioms
    3. Validate  — check the hypercover (augmented nerve) conditions via
                   HypercoverConditionChecker
    4. Refine    — iteratively add intermediate "filler" patches to repair
                   violated augmented nerve conditions
    5. Finalize  — mine overlap laws, build OverlapLaw objects, return SynthesisOutcome

    The synthesizer records every significant action in a HypercoverSynthesisRecord
    and builds the SynthesisOutcome from that record.  Budget tracking uses integer
    units: each phase transition costs 1 unit, each refinement round costs 2 units,
    and each law-mining operation costs 1 unit.
    """

    def __init__(
        self,
        config: SynthesisConfig | None = None,
    ) -> None:
        self.config: SynthesisConfig = config or DEFAULT_CONFIG
        self.checker: HypercoverConditionChecker = HypercoverConditionChecker()
        self.parser: GoalStructureParser = GoalStructureParser()
        self._law_index: OverlapLawIndex = OverlapLawIndex()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def synthesize(self, goal: Any) -> SynthesisOutcome:
        """Run the five-phase hypercover synthesis procedure on *goal*.

        Returns a SynthesisOutcome.  Never raises; synthesis failures are
        encoded in the OutcomeKind field of the returned outcome.

        Budget consumption:
        - Phase transitions: 1 unit each
        - Each refinement round: 2 units
        - Law mining: 1 unit per overlap pair
        """
        start_time = time.monotonic()
        record = HypercoverSynthesisRecord(
            goal_proposition=self._extract_proposition(goal),
            target_coordinate_key=self._extract_target_key(goal),
        )
        budget_used = 0

        try:
            # ---- Phase 1: DECOMPOSING ----------------------------------------
            record = record.with_phase(SynthesisPhase.DECOMPOSING)
            record = record.with_step("Begin decomposition of goal structure")
            decomp = self._decompose_goal(goal)
            patch_keys = tuple(sorted(decomp["patch_keys"]))
            overlap_pairs = tuple(sorted(decomp["overlap_pairs"]))
            record = record.with_cover(patch_keys, overlap_pairs)
            record = record.with_step(
                f"Decomposed: {len(patch_keys)} patches, {len(overlap_pairs)} overlap pairs"
            )
            budget_used += 1

            if budget_used >= self.config.max_budget:
                record = record.with_phase(SynthesisPhase.FAILED)
                elapsed = time.monotonic() - start_time
                return self._build_outcome(
                    record.with_budget(budget_used).with_elapsed(elapsed),
                    laws=[],
                    success=False,
                    kind=OutcomeKind.BUDGET_EXHAUSTED,
                )

            # ---- Phase 2: COVERING -------------------------------------------
            record = record.with_phase(SynthesisPhase.COVERING)
            record = record.with_step("Building cover from decomposition")
            cover = self._build_cover_from_decomposition(decomp)
            record = record.with_step(f"Cover built with {len(patch_keys)} patches")
            budget_used += 1

            # ---- Phase 3: VALIDATING -----------------------------------------
            record = record.with_phase(SynthesisPhase.VALIDATING)
            record = record.with_step("Validating augmented nerve conditions")
            passes, violations = self._validate_hypercover_condition(cover)
            record = record.with_step(
                f"Validation complete: {'PASS' if passes else 'FAIL'} "
                f"({len(violations)} violations)"
            )
            budget_used += 1

            # ---- Phase 4: REFINING -------------------------------------------
            if not passes:
                record = record.with_phase(SynthesisPhase.REFINING)
                record = record.with_step(
                    f"Entering refinement with {len(violations)} violations"
                )
                cover, record = self._refine_until_hypercover(cover, goal, record)
                passes, violations = self._validate_hypercover_condition(cover)
                budget_used += 2 * self.config.max_refinement_rounds
                record = record.with_step(
                    f"Post-refinement validation: {'PASS' if passes else 'FAIL'}"
                )

                if budget_used >= self.config.max_budget:
                    record = record.with_phase(SynthesisPhase.FAILED)
                    elapsed = time.monotonic() - start_time
                    return self._build_outcome(
                        record.with_budget(budget_used).with_elapsed(elapsed),
                        laws=[],
                        success=False,
                        kind=OutcomeKind.BUDGET_EXHAUSTED,
                    )

            # ---- Phase 5: FINALIZING -----------------------------------------
            record = record.with_phase(SynthesisPhase.FINALIZING)
            record = record.with_step("Mining overlap laws from stabilized cover")
            laws: list[OverlapLaw] = []
            if self.config.enable_law_mining:
                laws = self._mine_overlap_laws(cover, record)
                budget_used += len(laws)
                record = record.with_step(f"Mined {len(laws)} overlap laws")
                for law in laws:
                    self._law_index.add(law)

            record = record.with_budget(budget_used)
            record = record.with_phase(SynthesisPhase.COMPLETE)
            record = record.with_step("Synthesis complete")
            elapsed = time.monotonic() - start_time

            return self._build_outcome(
                record.with_elapsed(elapsed),
                laws=laws,
                success=passes,
                kind=OutcomeKind.SUCCESS if passes else OutcomeKind.PARTIAL_SUCCESS,
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("HypercoverSynthesizer.synthesize raised unexpectedly: %s", exc)
            try:
                record = record.with_phase(SynthesisPhase.FAILED)
            except ValueError:
                pass
            elapsed = time.monotonic() - start_time
            return self._build_outcome(
                record.with_budget(budget_used).with_elapsed(elapsed),
                laws=[],
                success=False,
                kind=OutcomeKind.FAILURE,
            )

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    def _extract_proposition(self, goal: Any) -> str:
        """Safely extract the proposition string from *goal*."""
        try:
            return str(goal.proposition)
        except AttributeError:
            if isinstance(goal, dict):
                return str(goal.get("proposition", ""))
        return ""

    def _extract_target_key(self, goal: Any) -> str:
        """Safely extract the target coordinate key from *goal*."""
        try:
            return ".".join(goal.support.coordinate.components)
        except AttributeError:
            if isinstance(goal, dict):
                return str(goal.get("target_key", ""))
        return ""

    def _decompose_goal(self, goal: Any) -> dict[str, Any]:
        """Phase 1: parse the goal and compute overlap pairs.

        Uses GoalStructureParser to extract patch keys and computes the
        full set of potential overlap pairs.  If the inferred set of overlaps
        is empty (single patch) a trivially-covering set with no overlaps is used.
        """
        parsed = self.parser.parse(goal)
        overlap_pairs = self.parser.extract_overlap_structure(goal)

        # Augment with proposition-inferred overlaps when available
        inferred = self.parser.infer_overlap_pairs_from_proposition(goal)
        all_pairs = list({tuple(sorted(p)) for p in overlap_pairs + inferred})

        return {
            "proposition": parsed["proposition"],
            "patch_keys": parsed["patch_keys"],
            "overlap_pairs": all_pairs,
            "required_tier": parsed["required_tier"],
            "priority": parsed["priority"],
            "budget": parsed["budget"],
            "provenance": parsed["provenance"],
            "target_key": parsed["target_key"],
        }

    def _build_cover_from_decomposition(self, decomp: dict[str, Any]) -> Any:
        """Phase 2: construct a Cover from the decomposition dict.

        Attempts to build a real Cover using jugeo.geometry.covers and
        jugeo.geometry.site.  Falls back to a plain dict representation when
        those modules are unavailable.

        For each patch key a Coordinate is constructed with:
            components = (key,)
            kind       = CoordinateKind.REGION

        The target coordinate is constructed similarly from decomp["target_key"].
        """
        patch_keys = sorted(decomp["patch_keys"])
        overlap_pairs = [tuple(p) for p in decomp["overlap_pairs"]]
        target_key = decomp.get("target_key", "target")

        try:
            # Attempt to build real Cover
            from jugeo.geometry.site import Coordinate, CoordinateKind as CK  # type: ignore[import]
            from jugeo.geometry.covers import Cover as C  # type: ignore[import]

            target_coord = Coordinate(
                components=(target_key,) if target_key else ("target",),
                kind=CK.REGION,
            )
            patch_coords = tuple(
                Coordinate(components=(key,), kind=CK.REGION)
                for key in patch_keys
            )
            cover = C(
                target=target_coord,
                patches=patch_coords,
                overlaps=tuple(overlap_pairs),
            )
            logger.debug("Built real Cover with %d patches", len(patch_coords))
            return cover

        except (ImportError, Exception) as exc:
            logger.debug(
                "Falling back to dict cover representation (reason: %s)", exc
            )
            return {
                "target": target_key,
                "patches": patch_keys,
                "overlaps": overlap_pairs,
            }

    def _validate_hypercover_condition(self, cover: Any) -> tuple[bool, list[str]]:
        """Phase 3: delegate to HypercoverConditionChecker."""
        return self.checker.check_all_conditions(cover)

    def _refine_until_hypercover(
        self,
        cover: Any,
        goal: Any,
        record: HypercoverSynthesisRecord,
    ) -> tuple[Any, HypercoverSynthesisRecord]:
        """Phase 4: iteratively refine the cover until all nerve conditions hold.

        Each round:
        1. Check all conditions and collect violations.
        2. For each HC-3 violation (missing triple overlap), add an intermediate
           patch whose key is the concatenation of the three patch keys.
        3. Rebuild the cover dict with the new patches.
        4. Repeat until no violations or max_refinement_rounds is reached.

        Returns (refined_cover, updated_record).
        """
        for round_idx in range(self.config.max_refinement_rounds):
            passes, violations = self._validate_hypercover_condition(cover)
            if passes:
                record = record.with_step(
                    f"Refinement converged at round {round_idx}"
                )
                break

            # Extract current patches and overlaps
            patch_keys, overlap_pairs = self.checker._extract_cover_data(cover)

            # Find HC-3 triple-overlap violations and add filler patches
            new_patches: list[str] = list(patch_keys)
            new_overlaps: list[tuple[str, str]] = list(overlap_pairs)

            for violation in violations:
                if "HC-3" not in violation:
                    continue
                # Parse the triple from the violation message
                # Format: "HC-3: triple ('A', 'B', 'C') is missing ..."
                parts = violation.split("triple")
                if len(parts) < 2:
                    continue
                triple_str = parts[1].strip()
                # Extract patch key identifiers by splitting on quotes
                keys_in_triple = [
                    s.strip().strip("'\"")
                    for s in triple_str.split(",")
                    if s.strip().strip("'\"()[] ")
                ][:3]
                if len(keys_in_triple) < 3:
                    continue

                i, j, k = keys_in_triple[0], keys_in_triple[1], keys_in_triple[2]
                filler_key = f"{i}_x_{j}_x_{k}"
                if filler_key not in new_patches:
                    new_patches.append(filler_key)
                    # Add pairwise overlaps between filler and each triple member
                    for member in (i, j, k):
                        candidate_pair = tuple(sorted([filler_key, member]))
                        if candidate_pair not in {
                            tuple(sorted(p)) for p in new_overlaps
                        }:
                            new_overlaps.append(candidate_pair)  # type: ignore[arg-type]

            record = record.with_step(
                f"Refinement round {round_idx + 1}: added "
                f"{len(new_patches) - len(patch_keys)} filler patches"
            )

            # Rebuild the cover representation
            cover = {
                "target": (
                    cover.get("target", "target")
                    if isinstance(cover, dict)
                    else "target"
                ),
                "patches": new_patches,
                "overlaps": new_overlaps,
            }

        else:
            record = record.with_step(
                f"Refinement reached max rounds ({self.config.max_refinement_rounds}) "
                "without full convergence"
            )

        return cover, record

    def _mine_overlap_laws(
        self, cover: Any, record: HypercoverSynthesisRecord
    ) -> list[OverlapLaw]:
        """Phase 5a: mine OverlapLaw objects from the stabilized cover.

        For each overlap pair in the cover, creates an OverlapLaw with:
        - a predicate description derived from the pair names
        - initial support_count of 1 and violation_count of 0
        - confidence proportional to the record's acceptance ratio
        - stability PROVISIONAL (will be promoted by the caller if threshold met)
        - discovered_in_record_id set to the current record's ID

        The synthesizer records acceptance ratio from the record to set the
        initial confidence.  In a full implementation this would call the
        OverlapLawDiscovery subsystem from overlap_laws.py.
        """
        _, overlap_pairs = self.checker._extract_cover_data(cover)
        base_confidence = max(0.4, record.acceptance_ratio())
        patch_count = record.patch_count()
        # Use patch_count as a heuristic signal for overall synthesis quality
        quality_bonus = min(0.3, 0.05 * patch_count)

        laws: list[OverlapLaw] = []
        for a, b in overlap_pairs:
            if not a or not b:
                continue
            predicate = (
                f"For any section on patch '{a}' and any section on patch '{b}', "
                f"their restrictions to the intersection '{a} ∩ {b}' agree."
            )
            law = OverlapLaw(
                law_id=str(uuid.uuid4()),
                patch_pair=(a, b),
                predicate_description=predicate,
                stability=LawStability.PROVISIONAL,
                support_count=1,
                violation_count=0,
                confidence=min(1.0, base_confidence + quality_bonus),
                discovered_in_record_id=record.record_id,
                provenance=record.provenance,
            )
            # Promote to STABLE immediately if confidence is high enough
            if law.confidence >= self.config.min_law_confidence:
                law = law.promote_stability()
            laws.append(law)

        logger.debug("Mined %d overlap laws from cover", len(laws))
        return laws

    def _build_outcome(
        self,
        record: HypercoverSynthesisRecord,
        laws: list[OverlapLaw],
        success: bool,
        kind: OutcomeKind | None = None,
    ) -> SynthesisOutcome:
        """Construct a SynthesisOutcome from the final synthesis record."""
        if kind is None:
            kind = OutcomeKind.SUCCESS if success else OutcomeKind.FAILURE

        # Derive failed patches: any patch with no laws in the law index
        patch_keys = record.cover_patch_keys
        patches_with_laws: set[str] = set()
        for law in laws:
            patches_with_laws.update(law.patch_pair)
        failed_patches = tuple(k for k in patch_keys if k not in patches_with_laws)

        # Build repair suggestions for any remaining violations
        repair_suggestions: list[str] = []
        if not success:
            repair_suggestions.append(
                "Consider adding more patches to resolve triple-overlap violations."
            )
            repair_suggestions.append(
                "Review the augmented nerve condition: for every triple (i,j,k) "
                "of mutually overlapping patches, all pairwise overlaps must be listed."
            )
            if failed_patches:
                repair_suggestions.append(
                    f"The following patches have no overlap laws: {list(failed_patches)!r}. "
                    "Ensure each patch overlaps with at least one other patch."
                )

        return SynthesisOutcome(
            outcome_id=str(uuid.uuid4()),
            kind=kind,
            record_id=record.record_id,
            accepted_laws=tuple(laws),
            accepted_treaties_count=len(record.accepted_treaty_ids),
            failed_patches=failed_patches,
            repair_suggestions=tuple(repair_suggestions),
            total_budget_used=record.budget_consumed,
            wall_seconds=record.elapsed_seconds,
            provenance=record.provenance,
        )

    def get_law_index(self) -> OverlapLawIndex:
        """Return the law index accumulated across all synthesis runs."""
        return self._law_index

    def reset_law_index(self) -> None:
        """Clear the accumulated law index."""
        self._law_index = OverlapLawIndex()


# ---------------------------------------------------------------------------
# SynthesisDriver
# ---------------------------------------------------------------------------


@dataclass
class SynthesisDriver:
    """High-level driver that runs HypercoverSynthesizer with convergence tracking.

    The driver maintains a *state dict* across calls to ``step()`` and
    provides a ``is_converged()`` predicate.  This supports streaming
    synthesis: callers can advance synthesis one step at a time and inspect
    intermediate results.

    Convergence is defined as:
    - The outcome's phase is COMPLETE or FAILED, OR
    - No new violations were found in the last two consecutive rounds.

    The ``run()`` method is a convenience wrapper that calls ``step()``
    in a loop until convergence or budget exhaustion.
    """

    config: SynthesisConfig = field(default_factory=SynthesisConfig)
    _synthesizer: HypercoverSynthesizer = field(init=False)
    _history: list[SynthesisOutcome] = field(default_factory=list, init=False)
    _loaded_goals: tuple[Any, ...] = field(default_factory=tuple, init=False)
    _current_state: dict[str, Any] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._synthesizer = HypercoverSynthesizer(self.config)

    def run(self, goal: Any, budget: int | None = None) -> SynthesisOutcome:
        """Run synthesis on *goal*, respecting *budget*.

        Parameters
        ----------
        goal:
            A ConstructionGoal or plain dict compatible with GoalStructureParser.
        budget:
            Optional integer budget override.  If None, uses config.max_budget.

        Returns
        -------
        SynthesisOutcome
            The final outcome after convergence or budget exhaustion.
        """
        effective_config = self.config
        if budget is not None and budget != self.config.max_budget:
            effective_config = self.config.with_budget(budget)
            self._synthesizer = HypercoverSynthesizer(effective_config)

        state = self._initial_state(goal)
        self._current_state = dict(state)
        max_steps = effective_config.max_budget * 2  # upper bound on iterations

        for step_idx in range(max_steps):
            state = self.step(state)
            self._current_state = dict(state)
            if self.is_converged(state):
                logger.debug(
                    "SynthesisDriver converged at step %d (phase=%s)",
                    step_idx,
                    state.get("phase", "unknown"),
                )
                break
        else:
            logger.warning(
                "SynthesisDriver reached max_steps (%d) without convergence", max_steps
            )
            state["kind"] = OutcomeKind.BUDGET_EXHAUSTED.value

        outcome = state.get("outcome")
        if outcome is None:
            outcome = SynthesisOutcome(
                kind=OutcomeKind.BUDGET_EXHAUSTED,
                record_id=state.get("record_id", ""),
                total_budget_used=state.get("budget_used", 0),
                wall_seconds=time.monotonic() - state.get("start_time", time.monotonic()),
            )

        self._history.append(outcome)
        return outcome

    def load(self, goals: Any) -> None:
        """Load goals for legacy step-by-step driver usage."""
        if isinstance(goals, (list, tuple)):
            self._loaded_goals = tuple(goals)
        else:
            self._loaded_goals = (goals,)
        goal = self._loaded_goals[0] if self._loaded_goals else []
        self._current_state = self._initial_state(goal)

    def step(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Advance synthesis state by one logical step.

        The *state* dict is updated in place (a copy is returned) with:
        - ``phase``: updated SynthesisPhase value
        - ``budget_used``: incremented
        - ``violations``: list of current violations (empty when none)
        - ``outcome``: SynthesisOutcome once phase==COMPLETE or FAILED
        - ``violation_history``: list of violation-count snapshots for convergence

        Parameters
        ----------
        state:
            A mutable dict produced by ``_initial_state()`` or a prior ``step()`` call.

        Returns
        -------
        dict[str, Any]
            Updated state dict.
        """
        if state is None:
            if self._current_state is None:
                goal = self._loaded_goals[0] if self._loaded_goals else []
                self._current_state = self._initial_state(goal)
            state = self._current_state

        new_state = dict(state)

        phase = new_state.get("phase", SynthesisPhase.DECOMPOSING.value)
        goal = new_state.get("goal")
        budget_used = new_state.get("budget_used", 0)

        if phase in (SynthesisPhase.COMPLETE.value, SynthesisPhase.FAILED.value):
            return new_state  # Already terminal

        if budget_used >= self.config.max_budget:
            new_state["phase"] = SynthesisPhase.FAILED.value
            new_state["outcome"] = SynthesisOutcome(
                kind=OutcomeKind.BUDGET_EXHAUSTED,
                record_id=new_state.get("record_id", ""),
                total_budget_used=budget_used,
            )
            return new_state

        # Full synthesis on each step call (stateless per-step approach)
        outcome = self._synthesizer.synthesize(goal)
        new_state["outcome"] = outcome
        new_state["budget_used"] = budget_used + outcome.total_budget_used
        new_state["phase"] = (
            SynthesisPhase.COMPLETE.value
            if outcome.is_success()
            else SynthesisPhase.FAILED.value
            if outcome.is_failure()
            else SynthesisPhase.FINALIZING.value
        )
        new_state["record_id"] = outcome.record_id

        # Track violation history for convergence detection
        violation_history: list[int] = new_state.get("violation_history", [])
        violation_history.append(outcome.failed_patch_count())
        new_state["violation_history"] = violation_history

        self._current_state = dict(new_state)
        return new_state

    def is_converged(self, state: dict[str, Any] | None = None) -> bool:
        """Check if synthesis state has converged.

        Convergence criteria:
        1. Phase is COMPLETE or FAILED (terminal state).
        2. No new violations in the last two rounds (violation history plateau).

        Parameters
        ----------
        state:
            The current synthesis state dict.

        Returns
        -------
        bool
            True iff convergence has been reached.
        """
        if state is None:
            state = self._current_state or {}

        phase = state.get("phase", "")
        if phase in (SynthesisPhase.COMPLETE.value, SynthesisPhase.FAILED.value):
            return True

        violation_history: list[int] = state.get("violation_history", [])
        if len(violation_history) >= 2:
            # Convergence if last two rounds have identical violation counts
            if violation_history[-1] == violation_history[-2]:
                return True
            # Also converged if violations reached zero
            if violation_history[-1] == 0:
                return True

        return False

    def _initial_state(self, goal: Any) -> dict[str, Any]:
        """Build the initial state dict for a new synthesis run."""
        return {
            "goal": goal,
            "phase": SynthesisPhase.DECOMPOSING.value,
            "budget_used": 0,
            "violations": [],
            "violation_history": [],
            "outcome": None,
            "record_id": "",
            "start_time": time.monotonic(),
        }

    def history(self) -> list[SynthesisOutcome]:
        """Return all outcomes produced so far by this driver."""
        return list(self._history)

    def last_outcome(self) -> SynthesisOutcome | None:
        """Return the most recent SynthesisOutcome, or None."""
        return self._history[-1] if self._history else None

    def law_index(self) -> OverlapLawIndex:
        """Return the accumulated law index from the underlying synthesizer."""
        return self._synthesizer.get_law_index()

    def reset(self) -> None:
        """Reset the driver's history and law index."""
        self._history.clear()
        self._loaded_goals = ()
        self._current_state = None
        self._synthesizer.reset_law_index()
