r"""Core dataclass models for the local_construction sub-package.

Theory (theory2.tex §39 — Local construction loops):
    Chapter 39 of theory2.tex develops the theory of *local construction
    loops* — the innermost tier of the JuGeo three-tier generation
    architecture.  Where the outer :class:`ConstructionLoop` (Chapter 38)
    manages the full goal DAG, a local loop is scoped to a *single* goal
    g : GenerationGoal and iterates over candidate sections until one is
    accepted or the budget is exhausted.

    §39.4 specifies the loop state machine:

        PENDING  ──initialize──►  RUNNING
        RUNNING  ──succeed──────►  SUCCEEDED
        RUNNING  ──fail──────────►  FAILED
        RUNNING  ──stall─────────►  STALLED

    §39.5 defines *interface discipline*: the contract ∂u that a section
    s_u must satisfy at its boundary.  Two sections s_u and s_v are
    compatible iff their interface disciplines D_u and D_v can be reconciled
    by the *negotiation* operation D_u ⊓ D_v.

    §39.6 introduces *coordinated elaboration*: a set of loops running in
    parallel that must stay consistent at synchronisation points.  The
    coordination graph G = (V, E) has an edge (u, v) iff coordinate u
    directly depends on coordinate v.

    §39.7 defines the *candidate set* of a goal as the multi-set of proposed
    inhabitants, equipped with a partial Pareto order based on three
    objectives: trust score, residual obligation count, and evidence density.

    copilot: models-marker

Usage::

    from jugeo.generation.local_construction.models import (
        LocalConstructionLoop,
        InterfaceDiscipline,
        CoordinatedElaboration,
        CandidateSet,
        LoopStatus,
        StrictnessLevel,
        GenerationMethod,
    )
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.generation.construction import ConstructionContext
from jugeo.generation.goals import GenerationGoal

__all__ = [
    # Enums
    "LoopStatus",
    "StrictnessLevel",
    "GenerationMethod",
    # Exceptions
    "LocalConstructionError",
    "InterfaceBreachError",
    "BudgetExhaustedError",
    "ConvergenceFailureError",
    # Dataclasses
    "LocalConstructionLoop",
    "InterfaceDiscipline",
    "CoordinatedElaboration",
    "CandidateSet",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LoopStatus(str, Enum):
    """State-machine statuses for a :class:`LocalConstructionLoop`.

    Maps to the five states defined in theory2.tex §39.4 Definition 39.4.1.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALLED = "stalled"


class StrictnessLevel(str, Enum):
    """Strictness level for an :class:`InterfaceDiscipline`.

    Corresponds to the three negotiation regimes of theory2.tex §39.5
    Definition 39.5.3.

    * ``STRICT`` — all required exports/imports must be exactly satisfied.
    * ``LENIENT`` — missing exports are tolerated if a default is available.
    * ``NEGOTIABLE`` — disciplines may be weakened during conflict resolution.
    """

    STRICT = "strict"
    LENIENT = "lenient"
    NEGOTIABLE = "negotiable"


class GenerationMethod(str, Enum):
    """Method used to generate candidates in a :class:`CandidateSet`.

    Corresponds to theory2.tex §39.7 Definition 39.7.2.
    """

    SOLVER = "solver"
    COPILOT = "copilot"
    HUMAN = "human"
    ENUMERATION = "enumeration"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LocalConstructionError(Exception):
    """Base exception for the local_construction package.

    Raised when a local construction loop encounters an unrecoverable error
    that is not more specifically typed by a subclass.
    """


class InterfaceBreachError(LocalConstructionError):
    """Raised when a section violates its :class:`InterfaceDiscipline`.

    Corresponds to theory2.tex §39.5 Condition 39.5.1: a section s_u
    *breaches* its interface discipline D_u iff there exists an export
    required by D_u that s_u does not provide.
    """


class BudgetExhaustedError(LocalConstructionError):
    """Raised when a loop's budget drops to zero before convergence.

    Corresponds to theory2.tex §39.4 Condition 39.4.3.
    """


class ConvergenceFailureError(LocalConstructionError):
    """Raised when a loop exceeds its iteration limit without converging.

    Corresponds to theory2.tex §39.4 Condition 39.4.4.
    """


# ---------------------------------------------------------------------------
# LocalConstructionLoop
# ---------------------------------------------------------------------------


@dataclass
class LocalConstructionLoop:
    """The inner construction loop for a single local goal.

    Implements the loop automaton described in theory2.tex §39.4.  A loop
    is initialised from a :class:`~jugeo.generation.goals.GenerationGoal`,
    iterates by proposing and verifying candidates, and terminates when one
    of the convergence criteria is met.

    Attributes:
        loop_id: Unique identifier for this loop instance.
        goal_id: Identifier of the goal being resolved.
        coordinate_id: Coordinate u ∈ Coord this loop is scoped to.
        max_iterations: Maximum number of proposal/verify cycles (§39.4.4).
        current_iteration: Number of cycles completed so far.
        status: Current state-machine status string.
        candidate_history: Tuple of serialised candidate dicts, one per
            accepted proposal (may include rejected ones for audit).
        selected_candidate_id: ID of the chosen candidate, or ``None``.
        verification_record: Tuple of verification result dicts accumulated
            across all iterations.
        budget_remaining: Fraction of the initial budget not yet consumed.
    """

    loop_id: str
    goal_id: str
    coordinate_id: str
    max_iterations: int = 20
    current_iteration: int = 0
    status: str = LoopStatus.PENDING.value
    candidate_history: tuple = field(default_factory=tuple)
    selected_candidate_id: str | None = None
    verification_record: tuple = field(default_factory=tuple)
    budget_remaining: float = 1.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, goal: GenerationGoal) -> None:
        """Populate loop state from *goal* and transition to RUNNING.

        Sets :attr:`goal_id`, :attr:`coordinate_id`, and
        :attr:`budget_remaining` from the goal, then transitions the loop
        status to ``RUNNING`` (§39.4 Definition 39.4.1 transition 1).

        Args:
            goal: The :class:`~jugeo.generation.goals.GenerationGoal` whose
                local section this loop will construct.

        Raises:
            LocalConstructionError: If the loop is already in a terminal
                state (SUCCEEDED, FAILED, STALLED).
        """
        if self.status in (
            LoopStatus.SUCCEEDED.value,
            LoopStatus.FAILED.value,
            LoopStatus.STALLED.value,
        ):
            raise LocalConstructionError(
                f"Cannot initialize loop '{self.loop_id}' in terminal "
                f"status '{self.status}'"
            )
        self.goal_id = goal.goal_id
        self.coordinate_id = goal.coordinate_id
        self.budget_remaining = goal.budget
        self.status = LoopStatus.RUNNING.value
        self.current_iteration = 0
        self.candidate_history = tuple()
        self.selected_candidate_id = None
        self.verification_record = tuple()
        logger.debug(
            "LocalConstructionLoop.initialize: loop '%s' running for goal '%s'",
            self.loop_id, self.goal_id,
        )

    # ------------------------------------------------------------------
    # Candidate proposal and selection
    # ------------------------------------------------------------------

    def propose_candidates(self, context: ConstructionContext) -> list[dict]:
        """Generate candidate proposals for the current iteration.

        Uses heuristics derived from *context* bindings and evidence to
        produce a ranked list of candidate proposal dicts.  In a full
        implementation this would delegate to solvers and copilot channels;
        here it produces structured placeholder proposals reflecting the
        available budget and binding density.

        The proposals follow the schema required by :meth:`select_best` and
        :meth:`verify_candidate`:

        .. code-block:: python

            {
                "candidate_id": str,
                "goal_id":      str,
                "score":        float,      # preliminary composite score
                "source":       str,        # generation channel
                "trust_score":  float,
                "residual_count": int,
                "evidence_density": float,
                "payload":      dict,
            }

        Args:
            context: The :class:`~jugeo.generation.construction.ConstructionContext`
                providing bindings, evidence, and budget.

        Returns:
            List of candidate proposal dicts, sorted descending by
            preliminary score.

        Raises:
            BudgetExhaustedError: If :attr:`budget_remaining` is zero or
                negative before any proposals are generated.
        """
        if self.budget_remaining <= 0.0:
            raise BudgetExhaustedError(
                f"Loop '{self.loop_id}': budget exhausted before proposing "
                f"(iteration {self.current_iteration})"
            )

        binding_density = len(context.bindings) / max(1, self.max_iterations)
        evidence_density = len(context.evidence) / max(1, len(context.evidence) + 5)

        # Produce a small set of synthetic candidates with varied parameters.
        proposals: list[dict] = []
        num_proposals = max(2, min(5, int(self.budget_remaining * 10)))

        for i in range(num_proposals):
            cid = f"{self.loop_id}_c{self.current_iteration}_{i}"
            # Vary trust and residuals across proposals
            trust = min(1.0, evidence_density + binding_density * (1 - i * 0.1))
            residuals = max(0, i)  # first candidate has fewest residuals
            score = trust * 0.6 + (1.0 / (residuals + 1)) * 0.4
            proposals.append({
                "candidate_id": cid,
                "goal_id": self.goal_id,
                "score": round(score, 4),
                "source": GenerationMethod.SOLVER.value if i == 0
                          else GenerationMethod.ENUMERATION.value,
                "trust_score": round(trust, 4),
                "residual_count": residuals,
                "evidence_density": round(evidence_density, 4),
                "payload": {
                    "coordinate_id": self.coordinate_id,
                    "context_id": context.context_id,
                    "iteration": self.current_iteration,
                },
            })

        proposals.sort(key=lambda c: c["score"], reverse=True)
        logger.debug(
            "propose_candidates: loop '%s' iter %d — generated %d proposals",
            self.loop_id, self.current_iteration, len(proposals),
        )
        return proposals

    def select_best(self, candidates: list[dict]) -> str | None:
        """Multi-criterion selection over *candidates*.

        Implements the selection criterion of theory2.tex §39.7 Definition
        39.7.4: a candidate c* dominates c if it is at least as good on all
        three objectives (trust score, residual count, evidence density) and
        strictly better on at least one.

        This method applies a weighted composite score rather than strict
        Pareto dominance, balancing:

        * trust_score       weight 0.50
        * 1/(residual_count+1) weight 0.30
        * evidence_density  weight 0.20

        Args:
            candidates: List of candidate dicts as returned by
                :meth:`propose_candidates`.

        Returns:
            The ``"candidate_id"`` string of the best candidate, or ``None``
            if *candidates* is empty.
        """
        if not candidates:
            logger.debug("select_best: empty candidate list for loop '%s'", self.loop_id)
            return None

        def composite(c: dict) -> float:
            trust = float(c.get("trust_score", 0.0))
            residuals = int(c.get("residual_count", 0))
            evidence = float(c.get("evidence_density", 0.0))
            return trust * 0.50 + (1.0 / (residuals + 1)) * 0.30 + evidence * 0.20

        best = max(candidates, key=composite)
        logger.debug(
            "select_best: loop '%s' — selected '%s' (score=%.4f)",
            self.loop_id, best["candidate_id"], composite(best),
        )
        return best["candidate_id"]

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_candidate(
        self,
        candidate: dict,
        context: ConstructionContext,
    ) -> tuple[bool, list[str], dict]:
        """Verify that *candidate* satisfies the loop's obligations.

        Checks, in order:
        1. Budget sufficiency (§39.4.3): candidate cost ≤ budget_remaining.
        2. Treaty compatibility (§39.5): candidate treaty_id matches context.
        3. Obligation count (§39.4.2): residual_count ≤ threshold.

        On success, consumes a fraction of the budget proportional to the
        candidate's residual count.

        Args:
            candidate: A candidate dict as produced by :meth:`propose_candidates`.
            context: The active :class:`~jugeo.generation.construction.ConstructionContext`.

        Returns:
            A triple ``(ok, residuals, evidence_map)`` where:
            * ``ok`` is True iff all checks pass.
            * ``residuals`` is the list of unresolved obligation strings.
            * ``evidence_map`` is a dict of verification evidence.
        """
        ok = True
        residuals: list[str] = []
        evidence_map: dict[str, Any] = {
            "candidate_id": candidate.get("candidate_id"),
            "loop_id": self.loop_id,
            "iteration": self.current_iteration,
            "checks": {},
        }

        # 1. Budget check
        cost = 1.0 / max(1, self.max_iterations)
        if self.budget_remaining < cost:
            ok = False
            residuals.append(f"budget_insufficient:{self.budget_remaining:.4f}<{cost:.4f}")
            evidence_map["checks"]["budget"] = "FAIL"
        else:
            evidence_map["checks"]["budget"] = "OK"

        # 2. Treaty compatibility
        candidate_treaty = candidate.get("payload", {}).get("treaty_id", "")
        context_treaty = context.treaty_id or ""
        if context_treaty and candidate_treaty and context_treaty != candidate_treaty:
            ok = False
            residuals.append(
                f"treaty_mismatch:{candidate_treaty}!={context_treaty}"
            )
            evidence_map["checks"]["treaty"] = "FAIL"
        else:
            evidence_map["checks"]["treaty"] = "OK"

        # 3. Obligation count threshold
        residual_count = int(candidate.get("residual_count", 0))
        threshold = max(1, self.max_iterations // 4)
        if residual_count > threshold:
            ok = False
            residuals.append(
                f"too_many_residuals:{residual_count}>{threshold}"
            )
            evidence_map["checks"]["obligations"] = "FAIL"
        else:
            evidence_map["checks"]["obligations"] = "OK"
            # Extend with obligation strings from candidate payload
            for i in range(residual_count):
                residuals.append(f"obligation_{i}_of_{self.goal_id}")

        if ok:
            # Consume budget proportional to residuals resolved
            consumed = cost * max(1, residual_count)
            self.budget_remaining = max(0.0, self.budget_remaining - consumed)
            evidence_map["budget_consumed"] = consumed
            evidence_map["budget_remaining"] = self.budget_remaining
        else:
            evidence_map["budget_consumed"] = 0.0
            evidence_map["budget_remaining"] = self.budget_remaining

        evidence_map["ok"] = ok
        evidence_map["residual_count"] = len(residuals)
        logger.debug(
            "verify_candidate: loop '%s' candidate '%s' — ok=%s residuals=%d",
            self.loop_id, candidate.get("candidate_id"), ok, len(residuals),
        )
        return ok, residuals, evidence_map

    # ------------------------------------------------------------------
    # Obligation propagation
    # ------------------------------------------------------------------

    def propagate_obligations(self, result: dict) -> list[str]:
        """Extract obligations from *result* that must propagate upward.

        Implements the obligation lift operation of theory2.tex §39.4
        Proposition 39.4.5: obligations that cannot be resolved locally are
        lifted to the enclosing :class:`ConstructionLoop`.

        An obligation propagates iff it is tagged with the prefix
        ``"obligation_"`` in the result's ``"residuals"`` list.

        Args:
            result: A dict containing at least a ``"residuals"`` key whose
                value is a list of obligation strings.

        Returns:
            List of obligation strings that should be lifted to the outer
            loop.
        """
        raw_residuals: list[str] = result.get("residuals", [])
        propagated = [
            r for r in raw_residuals
            if r.startswith("obligation_") or r.startswith("treaty_")
        ]
        logger.debug(
            "propagate_obligations: loop '%s' — %d/%d residuals propagate",
            self.loop_id, len(propagated), len(raw_residuals),
        )
        return propagated

    # ------------------------------------------------------------------
    # Iteration control
    # ------------------------------------------------------------------

    def advance_iteration(self) -> None:
        """Increment :attr:`current_iteration` and check terminal conditions.

        After incrementing, checks for:
        * iteration limit exceeded  → transition to STALLED (§39.4.4)
        * budget exhausted          → transition to FAILED  (§39.4.3)

        Raises:
            ConvergenceFailureError: If ``current_iteration`` exceeds
                ``max_iterations`` after incrementing.
            BudgetExhaustedError: If ``budget_remaining`` falls to zero.
        """
        self.current_iteration += 1
        logger.debug(
            "advance_iteration: loop '%s' iter %d/%d budget %.4f",
            self.loop_id, self.current_iteration, self.max_iterations,
            self.budget_remaining,
        )
        if self.current_iteration >= self.max_iterations:
            self.status = LoopStatus.STALLED.value
            raise ConvergenceFailureError(
                f"Loop '{self.loop_id}' stalled after {self.current_iteration} "
                f"iterations (max={self.max_iterations})"
            )
        if self.budget_remaining <= 0.0:
            self.status = LoopStatus.FAILED.value
            raise BudgetExhaustedError(
                f"Loop '{self.loop_id}' budget exhausted at iteration "
                f"{self.current_iteration}"
            )

    def is_converged(self) -> bool:
        """Return True iff the loop has reached a terminal state.

        A loop is *converged* (§39.4 Definition 39.4.2) iff its status is one
        of SUCCEEDED, FAILED, or STALLED.

        Returns:
            bool.
        """
        return self.status in (
            LoopStatus.SUCCEEDED.value,
            LoopStatus.FAILED.value,
            LoopStatus.STALLED.value,
        )

    # ------------------------------------------------------------------
    # Obligation collection
    # ------------------------------------------------------------------

    def get_residual_obligations(self) -> list[str]:
        """Collect all unresolved obligations from :attr:`verification_record`.

        Iterates over the accumulated verification records and returns the
        union of all residual strings that did not result in success.

        Returns:
            Deduplicated list of unresolved obligation strings.
        """
        seen: set[str] = set()
        result: list[str] = []
        for record in self.verification_record:
            if not record.get("ok", False):
                for r in record.get("residuals", []):
                    if r not in seen:
                        seen.add(r)
                        result.append(r)
        return result

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a full serialisation of the loop state.

        Returns:
            Dict with all fields; tuples serialised as lists.
        """
        return {
            "loop_id": self.loop_id,
            "goal_id": self.goal_id,
            "coordinate_id": self.coordinate_id,
            "max_iterations": self.max_iterations,
            "current_iteration": self.current_iteration,
            "status": self.status,
            "candidate_history": list(self.candidate_history),
            "selected_candidate_id": self.selected_candidate_id,
            "verification_record": list(self.verification_record),
            "budget_remaining": self.budget_remaining,
        }

    def summary(self) -> str:
        """Return a human-readable one-paragraph summary of the loop.

        Returns:
            Multi-line string suitable for terminal output.
        """
        lines = [
            f"LocalConstructionLoop '{self.loop_id}'",
            f"  Goal        : {self.goal_id}",
            f"  Coordinate  : {self.coordinate_id}",
            f"  Status      : {self.status}",
            f"  Iteration   : {self.current_iteration}/{self.max_iterations}",
            f"  Budget left : {self.budget_remaining:.4f}",
            f"  Selected    : {self.selected_candidate_id or '—'}",
            f"  Candidates  : {len(self.candidate_history)} in history",
            f"  Verifications: {len(self.verification_record)} records",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# InterfaceDiscipline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterfaceDiscipline:
    """The interface contract that a local section must satisfy at ∂u.

    Implements Definition 39.5.1 from theory2.tex: the interface discipline
    D_u = (E_u, I_u, L_u, τ, σ) where E_u are required exports, I_u required
    imports, L_u overlap laws, τ the treaty identifier, and σ the strictness.

    Being ``frozen=True`` reflects the fact that an interface discipline is a
    *specification* object — it is immutable once declared.

    Attributes:
        discipline_id: Unique identifier for this discipline instance.
        coordinate_id: The coordinate u whose boundary this governs.
        boundary_coordinates: frozenset of coordinates v such that ∂u ∩ ∂v ≠ ∅.
        required_exports: Symbols that s_u must export to its neighbours.
        required_imports: Symbols that s_u must be able to import.
        overlap_laws: Laws that must hold on every pairwise overlap ∂u ∩ ∂v.
        treaty_id: Identifier of the treaty τ governing this boundary.
        strictness_level: One of ``"strict"``, ``"lenient"``, ``"negotiable"``.
    """

    discipline_id: str
    coordinate_id: str
    boundary_coordinates: frozenset[str]
    required_exports: tuple[str, ...]
    required_imports: tuple[str, ...]
    overlap_laws: tuple[str, ...]
    treaty_id: str
    strictness_level: str = StrictnessLevel.STRICT.value

    # ------------------------------------------------------------------
    # Compliance checks
    # ------------------------------------------------------------------

    def check_section_compliance(self, section: dict) -> bool:
        """Check that *section* provides all required exports.

        A section is *compliant* (§39.5 Condition 39.5.1) iff for every
        e ∈ required_exports, the key ``e`` appears in section's
        ``"exports"`` dict.

        Args:
            section: A dict with at least an ``"exports"`` key mapping
                symbol names to values.

        Returns:
            True iff all required exports are present.

        Raises:
            InterfaceBreachError: When ``strictness_level == "strict"`` and
                the section is not compliant.
        """
        exports: dict = section.get("exports", {})
        missing = [e for e in self.required_exports if e not in exports]
        if missing:
            msg = (
                f"Section missing required exports {missing} "
                f"for discipline '{self.discipline_id}'"
            )
            if self.strictness_level == StrictnessLevel.STRICT.value:
                raise InterfaceBreachError(msg)
            logger.warning("check_section_compliance: %s (tolerated, level=%s)",
                           msg, self.strictness_level)
            return False
        return True

    def get_export_signature(self) -> dict[str, str]:
        """Return a map from each required export name to a type hint string.

        In the absence of a real type inference engine, returns placeholder
        type hints inferred from the export name conventions.

        Returns:
            Dict mapping export name → type hint string.
        """
        def infer_type(name: str) -> str:
            if name.endswith("_id"):
                return "str"
            if name.endswith("_count") or name.endswith("_index"):
                return "int"
            if name.endswith("_score") or name.endswith("_ratio"):
                return "float"
            if name.endswith("_flag") or name.startswith("is_") or name.startswith("has_"):
                return "bool"
            if name.endswith("_list") or name.endswith("s"):
                return "list[Any]"
            return "Any"

        return {e: infer_type(e) for e in self.required_exports}

    def get_import_requirements(self) -> dict[str, str]:
        """Return a map from each required import name to a source hint.

        Returns:
            Dict mapping import name → source hint string (e.g. module path).
        """
        def infer_source(name: str) -> str:
            if "goal" in name:
                return "jugeo.generation.goals"
            if "context" in name:
                return "jugeo.generation.construction"
            return "jugeo.generation.local_construction"

        return {i: infer_source(i) for i in self.required_imports}

    def validate_overlap_law(
        self,
        law: str,
        section_a: dict,
        section_b: dict,
    ) -> bool:
        """Check that *law* holds between *section_a* and *section_b*.

        Overlap laws (§39.5 Definition 39.5.2) are strings of the form
        ``"key_a == key_b"`` or ``"key_a <= key_b"``.  This method parses
        and evaluates simple equality and inequality laws.

        Args:
            law: The law string, e.g. ``"treaty_id == treaty_id"``.
            section_a: The first section dict.
            section_b: The second section dict.

        Returns:
            True if the law is satisfied or cannot be evaluated (lenient
            fallback).
        """
        try:
            parts = law.split()
            if len(parts) != 3:
                return True  # cannot evaluate; pass leniently
            key_a, op, key_b = parts
            val_a = section_a.get(key_a)
            val_b = section_b.get(key_b)
            if val_a is None or val_b is None:
                return self.strictness_level != StrictnessLevel.STRICT.value
            if op == "==":
                return val_a == val_b
            if op == "<=":
                return val_a <= val_b  # type: ignore[operator]
            if op == ">=":
                return val_a >= val_b  # type: ignore[operator]
            return True
        except Exception:
            logger.debug("validate_overlap_law: evaluation error for law '%s'", law)
            return self.strictness_level != StrictnessLevel.STRICT.value

    def negotiate_with(self, other: InterfaceDiscipline) -> InterfaceDiscipline:
        """Produce a merged discipline by lenient reconciliation of *self* and *other*.

        Implements the negotiation operation D_u ⊓ D_v from theory2.tex §39.5
        Definition 39.5.4.  The result takes:
        * The union of required_exports and required_imports.
        * The intersection of overlap_laws (laws both agree on).
        * The weaker strictness level.
        * A fresh discipline_id.

        Args:
            other: The :class:`InterfaceDiscipline` to merge with.

        Returns:
            A new :class:`InterfaceDiscipline` instance.
        """
        strictness_order = {
            StrictnessLevel.STRICT.value: 2,
            StrictnessLevel.LENIENT.value: 1,
            StrictnessLevel.NEGOTIABLE.value: 0,
        }
        weaker = min(
            self.strictness_level,
            other.strictness_level,
            key=lambda s: strictness_order.get(s, 0),
        )
        merged_exports = tuple(
            sorted(set(self.required_exports) | set(other.required_exports))
        )
        merged_imports = tuple(
            sorted(set(self.required_imports) | set(other.required_imports))
        )
        common_laws = tuple(
            sorted(set(self.overlap_laws) & set(other.overlap_laws))
        )
        merged_boundary = self.boundary_coordinates | other.boundary_coordinates
        return InterfaceDiscipline(
            discipline_id=f"negotiated_{uuid.uuid4().hex[:8]}",
            coordinate_id=self.coordinate_id,
            boundary_coordinates=merged_boundary,
            required_exports=merged_exports,
            required_imports=merged_imports,
            overlap_laws=common_laws,
            treaty_id=self.treaty_id if self.treaty_id == other.treaty_id else "",
            strictness_level=weaker,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns:
            Dict with all fields; frozensets serialised as sorted lists.
        """
        return {
            "discipline_id": self.discipline_id,
            "coordinate_id": self.coordinate_id,
            "boundary_coordinates": sorted(self.boundary_coordinates),
            "required_exports": list(self.required_exports),
            "required_imports": list(self.required_imports),
            "overlap_laws": list(self.overlap_laws),
            "treaty_id": self.treaty_id,
            "strictness_level": self.strictness_level,
        }

    def summary(self) -> str:
        """Return a compact human-readable summary.

        Returns:
            Multi-line string.
        """
        lines = [
            f"InterfaceDiscipline '{self.discipline_id}'",
            f"  Coordinate  : {self.coordinate_id}",
            f"  Strictness  : {self.strictness_level}",
            f"  Treaty      : {self.treaty_id or '—'}",
            f"  Exports req : {', '.join(self.required_exports) or '—'}",
            f"  Imports req : {', '.join(self.required_imports) or '—'}",
            f"  Overlap laws: {len(self.overlap_laws)}",
            f"  Boundary    : {', '.join(sorted(self.boundary_coordinates)) or '—'}",
        ]
        return "\n".join(lines)

    def is_satisfiable(self) -> bool:
        """Check that required_exports and required_imports do not conflict.

        A discipline is *satisfiable* (§39.5 Proposition 39.5.3) iff no
        symbol appears in both required_exports and required_imports (a
        section cannot simultaneously export and import the same name under
        STRICT mode).

        Returns:
            True iff the discipline is satisfiable.
        """
        if self.strictness_level != StrictnessLevel.STRICT.value:
            return True  # lenient/negotiable modes allow overlap
        conflicts = set(self.required_exports) & set(self.required_imports)
        if conflicts:
            logger.warning(
                "InterfaceDiscipline '%s': unsatisfiable — conflicts %s",
                self.discipline_id, conflicts,
            )
            return False
        return True

    def compute_compliance_score(self, section: dict) -> float:
        """Compute the fraction of required exports that *section* satisfies.

        Args:
            section: A dict with an ``"exports"`` key.

        Returns:
            Float in [0.0, 1.0]; 1.0 means full compliance.
        """
        if not self.required_exports:
            return 1.0
        exports: dict = section.get("exports", {})
        satisfied = sum(1 for e in self.required_exports if e in exports)
        return satisfied / len(self.required_exports)


# ---------------------------------------------------------------------------
# CoordinatedElaboration
# ---------------------------------------------------------------------------


@dataclass
class CoordinatedElaboration:
    """Parallel construction of multiple sections with coordination.

    Implements the coordinated elaboration framework of theory2.tex §39.6.
    A :class:`CoordinatedElaboration` manages a set of
    :class:`LocalConstructionLoop` instances running in (simulated) parallel
    and enforces consistency at synchronisation points.

    Attributes:
        elaboration_id: Unique identifier.
        participating_loops: Tuple of loop_id strings registered so far.
        coordination_graph: Maps each loop_id to the frozenset of loop_ids it
            directly depends on (edges in the dependency DAG, §39.6 Def 39.6.1).
        interface_states: Maps loop_id → current interface compliance string
            (``"satisfied"``, ``"pending"``, ``"breached"``).
        conflict_log: Tuple of conflict dicts accumulated during resolution.
        synchronization_points: Tuple of loop_ids that are designated
            synchronisation boundaries (§39.6 Def 39.6.2).
        status: Overall elaboration status string.
    """

    elaboration_id: str
    participating_loops: tuple[str, ...] = field(default_factory=tuple)
    coordination_graph: dict[str, frozenset[str]] = field(default_factory=dict)
    interface_states: dict[str, str] = field(default_factory=dict)
    conflict_log: tuple = field(default_factory=tuple)
    synchronization_points: tuple[str, ...] = field(default_factory=tuple)
    status: str = "initializing"

    # The live loop objects indexed by loop_id; not serialised.
    _loops: dict[str, LocalConstructionLoop] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_loop(self, loop: LocalConstructionLoop) -> None:
        """Add *loop* to the elaboration and update the coordination graph.

        If the loop's :attr:`~LocalConstructionLoop.coordinate_id` already
        appears in the graph (via another loop), the new loop is added as an
        independent node with no edges.

        Args:
            loop: The :class:`LocalConstructionLoop` to register.
        """
        self._loops[loop.loop_id] = loop
        self.participating_loops = (*self.participating_loops, loop.loop_id)
        if loop.loop_id not in self.coordination_graph:
            self.coordination_graph[loop.loop_id] = frozenset()
        self.interface_states[loop.loop_id] = "pending"
        if self.status == "initializing" and len(self._loops) >= 1:
            self.status = "running"
        logger.debug(
            "register_loop: elaboration '%s' now has %d loops",
            self.elaboration_id, len(self._loops),
        )

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------

    def synchronize_interfaces(self) -> dict[str, str]:
        """Update :attr:`interface_states` based on current loop statuses.

        A loop's interface is:
        * ``"satisfied"`` if the loop has SUCCEEDED.
        * ``"breached"`` if the loop has FAILED or STALLED.
        * ``"pending"`` otherwise.

        Returns:
            Updated :attr:`interface_states` dict.
        """
        for loop_id, loop in self._loops.items():
            if loop.status == LoopStatus.SUCCEEDED.value:
                self.interface_states[loop_id] = "satisfied"
            elif loop.status in (LoopStatus.FAILED.value, LoopStatus.STALLED.value):
                self.interface_states[loop_id] = "breached"
            else:
                self.interface_states[loop_id] = "pending"
        logger.debug(
            "synchronize_interfaces: elaboration '%s' states updated",
            self.elaboration_id,
        )
        return dict(self.interface_states)

    # ------------------------------------------------------------------
    # Conflict detection and resolution
    # ------------------------------------------------------------------

    def detect_conflicts(self) -> list[dict]:
        """Find loops with incompatible interface states.

        Two loops conflict iff they share an edge in :attr:`coordination_graph`
        and at least one has ``"breached"`` status while the other has
        ``"satisfied"``.

        Returns:
            List of conflict dicts, each with keys ``"loop_a"``, ``"loop_b"``,
            ``"reason"``.
        """
        conflicts: list[dict] = []
        seen_pairs: set[frozenset] = set()
        for loop_id, deps in self.coordination_graph.items():
            for dep_id in deps:
                pair: frozenset = frozenset({loop_id, dep_id})
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                state_a = self.interface_states.get(loop_id, "pending")
                state_b = self.interface_states.get(dep_id, "pending")
                if (state_a == "breached" and state_b == "satisfied") or \
                   (state_b == "breached" and state_a == "satisfied"):
                    conflicts.append({
                        "loop_a": loop_id,
                        "loop_b": dep_id,
                        "state_a": state_a,
                        "state_b": state_b,
                        "reason": "interface_incompatibility",
                    })
        logger.debug(
            "detect_conflicts: elaboration '%s' found %d conflict(s)",
            self.elaboration_id, len(conflicts),
        )
        return conflicts

    def resolve_conflict(self, conflict: dict) -> bool:
        """Attempt to resolve *conflict* by weakening strictness.

        The resolution strategy (§39.6 Proposition 39.6.3) is:
        1. Identify the breached loop.
        2. Reset its interface state to ``"pending"``.
        3. Log the resolution attempt.

        Args:
            conflict: A conflict dict as returned by :meth:`detect_conflicts`.

        Returns:
            True iff the resolution was applied (always True in this
            implementation; a real solver would verify the result).
        """
        breached = (
            conflict["loop_a"]
            if self.interface_states.get(conflict["loop_a"]) == "breached"
            else conflict["loop_b"]
        )
        self.interface_states[breached] = "pending"
        resolution = {
            "conflict": conflict,
            "resolution": "reset_to_pending",
            "resolved_loop": breached,
            "timestamp": time.time(),
        }
        self.conflict_log = (*self.conflict_log, resolution)
        logger.debug(
            "resolve_conflict: elaboration '%s' reset loop '%s' to pending",
            self.elaboration_id, breached,
        )
        return True

    # ------------------------------------------------------------------
    # Advancement
    # ------------------------------------------------------------------

    def advance_all_loops(self) -> int:
        """Advance every RUNNING loop by one iteration.

        Catches :class:`ConvergenceFailureError` and
        :class:`BudgetExhaustedError` and marks the affected loop terminal
        without propagating the exception.

        Returns:
            Number of loops successfully advanced.
        """
        advanced = 0
        for loop in self._loops.values():
            if loop.status == LoopStatus.RUNNING.value:
                try:
                    loop.advance_iteration()
                    advanced += 1
                except (ConvergenceFailureError, BudgetExhaustedError) as exc:
                    logger.info(
                        "advance_all_loops: loop '%s' terminated: %s",
                        loop.loop_id, exc,
                    )
        return advanced

    # ------------------------------------------------------------------
    # Status and progress
    # ------------------------------------------------------------------

    def get_coordination_status(self) -> dict[str, Any]:
        """Return a comprehensive status dictionary.

        Returns:
            Dict with keys ``"elaboration_id"``, ``"status"``,
            ``"loop_count"``, ``"interface_states"``, ``"conflict_count"``,
            ``"synchronization_points"``, ``"global_progress"``.
        """
        return {
            "elaboration_id": self.elaboration_id,
            "status": self.status,
            "loop_count": len(self._loops),
            "interface_states": dict(self.interface_states),
            "conflict_count": len(self.conflict_log),
            "synchronization_points": list(self.synchronization_points),
            "global_progress": self.compute_global_progress(),
        }

    def compute_global_progress(self) -> float:
        """Compute a 0.0–1.0 progress score across all loops.

        Progress of each loop is:
        * 1.0 for SUCCEEDED
        * 0.5 for STALLED or FAILED (partially processed)
        * current_iteration / max_iterations for RUNNING
        * 0.0 for PENDING

        Returns:
            Mean progress value in [0.0, 1.0], or 0.0 if no loops.
        """
        if not self._loops:
            return 0.0
        total = 0.0
        for loop in self._loops.values():
            if loop.status == LoopStatus.SUCCEEDED.value:
                total += 1.0
            elif loop.status in (LoopStatus.FAILED.value, LoopStatus.STALLED.value):
                total += 0.5
            elif loop.status == LoopStatus.RUNNING.value:
                total += loop.current_iteration / max(1, loop.max_iterations)
        return total / len(self._loops)

    def abort_conflicted_loops(self) -> list[str]:
        """Abort all loops with ``"breached"`` interface state.

        Sets their status to FAILED and returns their IDs.

        Returns:
            List of loop_ids that were aborted.
        """
        aborted: list[str] = []
        for loop_id, state in self.interface_states.items():
            if state == "breached":
                loop = self._loops.get(loop_id)
                if loop and not loop.is_converged():
                    loop.status = LoopStatus.FAILED.value
                    aborted.append(loop_id)
        if aborted:
            logger.info(
                "abort_conflicted_loops: elaboration '%s' aborted %d loop(s)",
                self.elaboration_id, len(aborted),
            )
        return aborted

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary.

        Returns:
            Dict with all public fields; frozensets serialised as sorted
            lists, tuples as lists.
        """
        return {
            "elaboration_id": self.elaboration_id,
            "participating_loops": list(self.participating_loops),
            "coordination_graph": {
                k: sorted(v) for k, v in self.coordination_graph.items()
            },
            "interface_states": dict(self.interface_states),
            "conflict_log": list(self.conflict_log),
            "synchronization_points": list(self.synchronization_points),
            "status": self.status,
        }

    def summary(self) -> str:
        """Return a human-readable summary.

        Returns:
            Multi-line string.
        """
        lines = [
            f"CoordinatedElaboration '{self.elaboration_id}'",
            f"  Status    : {self.status}",
            f"  Loops     : {len(self._loops)} registered",
            f"  Progress  : {self.compute_global_progress():.1%}",
            f"  Conflicts : {len(self.conflict_log)} logged",
            f"  Sync pts  : {len(self.synchronization_points)}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CandidateSet
# ---------------------------------------------------------------------------


@dataclass
class CandidateSet:
    """A scored set of candidate inhabitants for a local goal.

    Implements the candidate set structure of theory2.tex §39.7.  The set
    is equipped with a Pareto order based on three objectives defined in
    §39.7 Definition 39.7.3:
    * trust_score          (maximise)
    * residual_count       (minimise)
    * evidence_density     (maximise)

    Attributes:
        set_id: Unique identifier.
        goal_id: Identifier of the goal being served.
        candidates: Tuple of candidate dicts.
        generation_method: Method used to generate the candidates.
        generated_at: Unix timestamp of generation.
        scored: Whether :meth:`score_all` has been called.
        scores: Dict mapping candidate_id → composite score.
        dominated_ids: frozenset of candidate_ids that are Pareto-dominated.
    """

    set_id: str
    goal_id: str
    candidates: tuple = field(default_factory=tuple)
    generation_method: str = GenerationMethod.SOLVER.value
    generated_at: float = field(default_factory=time.time)
    scored: bool = False
    scores: dict[str, float] = field(default_factory=dict)
    dominated_ids: frozenset[str] = field(default_factory=frozenset)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_candidate(self, c: dict) -> None:
        """Append *c* to :attr:`candidates` and invalidate scores.

        Args:
            c: A candidate dict; must contain ``"candidate_id"``.
        """
        self.candidates = (*self.candidates, c)
        self.scored = False
        self.scores = {}
        self.dominated_ids = frozenset()
        logger.debug(
            "CandidateSet.add_candidate: set '%s' now has %d candidate(s)",
            self.set_id, len(self.candidates),
        )

    # ------------------------------------------------------------------
    # Pareto operations
    # ------------------------------------------------------------------

    def _dominates(self, c_a: dict, c_b: dict) -> bool:
        """Return True if c_a Pareto-dominates c_b.

        c_a dominates c_b iff:
        * trust(c_a) >= trust(c_b)
        * residuals(c_a) <= residuals(c_b)
        * evidence(c_a) >= evidence(c_b)
        * at least one inequality is strict.
        """
        t_a = float(c_a.get("trust_score", 0.0))
        t_b = float(c_b.get("trust_score", 0.0))
        r_a = int(c_a.get("residual_count", 0))
        r_b = int(c_b.get("residual_count", 0))
        e_a = float(c_a.get("evidence_density", 0.0))
        e_b = float(c_b.get("evidence_density", 0.0))
        at_least_as_good = (t_a >= t_b) and (r_a <= r_b) and (e_a >= e_b)
        strictly_better = (t_a > t_b) or (r_a < r_b) or (e_a > e_b)
        return at_least_as_good and strictly_better

    def remove_dominated(self) -> int:
        """Remove Pareto-dominated candidates and update :attr:`dominated_ids`.

        Returns:
            Number of candidates removed.
        """
        candidates_list = list(self.candidates)
        dominated: set[str] = set()
        for i, c_a in enumerate(candidates_list):
            for j, c_b in enumerate(candidates_list):
                if i == j:
                    continue
                if self._dominates(c_b, c_a):
                    dominated.add(c_a.get("candidate_id", str(i)))
                    break
        self.dominated_ids = frozenset(dominated)
        before = len(self.candidates)
        self.candidates = tuple(
            c for c in self.candidates
            if c.get("candidate_id") not in dominated
        )
        removed = before - len(self.candidates)
        logger.debug(
            "remove_dominated: set '%s' removed %d dominated candidate(s)",
            self.set_id, removed,
        )
        return removed

    def get_pareto_front(self) -> list[dict]:
        """Return non-dominated candidates.

        Returns:
            List of candidate dicts not in :attr:`dominated_ids`.
        """
        return [
            c for c in self.candidates
            if c.get("candidate_id") not in self.dominated_ids
        ]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_all(self, context: ConstructionContext) -> None:
        """Compute composite scores for all candidates.

        Scoring formula (§39.7 Definition 39.7.4):
        score = 0.50 * trust + 0.30 * 1/(residuals+1) + 0.20 * evidence

        A budget bonus of +0.05 is applied when the candidate's evidence
        density exceeds the context's average evidence density.

        Args:
            context: The active :class:`~jugeo.generation.construction.ConstructionContext`.
        """
        avg_evidence = len(context.evidence) / max(1, len(context.evidence) + 5)
        for c in self.candidates:
            cid = c.get("candidate_id", "unknown")
            trust = float(c.get("trust_score", 0.0))
            residuals = int(c.get("residual_count", 0))
            evidence = float(c.get("evidence_density", 0.0))
            score = trust * 0.50 + (1.0 / (residuals + 1)) * 0.30 + evidence * 0.20
            if evidence > avg_evidence:
                score = min(1.0, score + 0.05)
            self.scores[cid] = round(score, 6)
        self.scored = True
        logger.debug(
            "score_all: set '%s' scored %d candidates",
            self.set_id, len(self.scores),
        )

    def rank_by_score(self) -> list[dict]:
        """Sort candidates by descending composite score.

        Returns:
            List of candidate dicts in descending score order.
        """
        if not self.scored:
            logger.warning("rank_by_score: set '%s' has not been scored", self.set_id)
        return sorted(
            self.candidates,
            key=lambda c: self.scores.get(c.get("candidate_id", ""), 0.0),
            reverse=True,
        )

    def get_top_k(self, k: int) -> list[dict]:
        """Return the top-k candidates by composite score.

        Args:
            k: Number of candidates to return.

        Returns:
            List of at most *k* candidate dicts.
        """
        return self.rank_by_score()[:k]

    def filter_by_trust(self, min_trust: float) -> list[dict]:
        """Filter candidates whose trust_score meets the minimum.

        Args:
            min_trust: Minimum trust score threshold (inclusive).

        Returns:
            List of candidate dicts with trust_score >= min_trust.
        """
        return [
            c for c in self.candidates
            if float(c.get("trust_score", 0.0)) >= min_trust
        ]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary.

        Returns:
            Dict with all fields; tuples as lists, frozensets as sorted lists.
        """
        return {
            "set_id": self.set_id,
            "goal_id": self.goal_id,
            "candidates": list(self.candidates),
            "generation_method": self.generation_method,
            "generated_at": self.generated_at,
            "scored": self.scored,
            "scores": dict(self.scores),
            "dominated_ids": sorted(self.dominated_ids),
        }

    def summary(self) -> str:
        """Return a human-readable summary.

        Returns:
            Multi-line string.
        """
        top_score = max(self.scores.values(), default=0.0)
        lines = [
            f"CandidateSet '{self.set_id}'",
            f"  Goal         : {self.goal_id}",
            f"  Method       : {self.generation_method}",
            f"  Candidates   : {len(self.candidates)}",
            f"  Scored       : {self.scored}",
            f"  Top score    : {top_score:.4f}",
            f"  Dominated    : {len(self.dominated_ids)}",
            f"  Pareto front : {len(self.get_pareto_front())}",
        ]
        return "\n".join(lines)

    def merge_with(self, other_set: CandidateSet) -> CandidateSet:
        """Return a new :class:`CandidateSet` that is the union of both sets.

        The merged set inherits the ``goal_id`` of *self*, takes method
        ``"enumeration"`` to signal multi-source origin, and inherits scores
        from both sets where available.

        Args:
            other_set: The other :class:`CandidateSet` to merge with.

        Returns:
            A new :class:`CandidateSet` containing all candidates from both.
        """
        merged_candidates = self.candidates + other_set.candidates
        merged_scores = {**other_set.scores, **self.scores}  # self takes priority
        new_set = CandidateSet(
            set_id=f"merged_{uuid.uuid4().hex[:8]}",
            goal_id=self.goal_id,
            candidates=merged_candidates,
            generation_method=GenerationMethod.ENUMERATION.value,
            generated_at=time.time(),
            scored=bool(merged_scores),
            scores=merged_scores,
            dominated_ids=frozenset(),
        )
        logger.debug(
            "merge_with: merged set has %d candidates", len(merged_candidates)
        )
        return new_set
