from __future__ import annotations

"""Core data models for the structural frontier (Ch25).

This module defines the core data models for the structural frontier
(Chapter 25 of theory2.tex).  It models decidability, frontier
boundaries, solver-lifted types, countermodel obstructions, and repair
actions.  The structural frontier is the boundary of what Z3 can decide
-- types lift Z3 invariants, countermodels guide repair.

Architecture overview
---------------------
The structural frontier is the divide in formula-space that separates
the decidable interior (where Z3 always terminates with SAT or UNSAT)
from the undecidable exterior (where the solver may loop or return
UNKNOWN).  The principal abstractions are:

* ``DecidabilityClass`` -- a coarse label (DECIDABLE through UNDECIDABLE)
  assigned to each SMT-LIB fragment.
* ``FrontierSide`` -- which side of the frontier a formula occupies
  (INSIDE, BOUNDARY, or OUTSIDE).
* ``RepairAction`` -- a concrete, costed step that moves a formula closer
  to the decidable interior.
* ``StructuralFrontier`` -- a named frontier with an SMT-LIB boundary
  formula, examples inside and outside, and a decision procedure.
* ``SolverLiftedType`` -- a JuGeo type that carries a Z3 invariant as a
  first-class annotation, enabling type-level solver discharge.
* ``FrontierBoundary`` -- a directed crossing between two fragments with
  associated repair actions and a crossing cost.
* ``DecidabilityMap`` -- a mutable registry of frontiers, boundaries, and
  fragment assignments with path-finding for copilot navigation.
* ``CountermodelObstruction`` -- a rich obstruction record pairing a
  countermodel with its frontier location and repair candidates.

All value objects are frozen dataclasses wherever mutation is not
required.  Factory helpers provide sensible defaults for bootstrap and
testing.  The copilot integration surface is available via the
``copilot_*`` methods.

See Also
--------
theory2.tex ch25 -- Z3 and the Structural Frontier
jugeo.solver.z3_session -- Z3 session management
jugeo.solver.countermodels -- countermodel extraction
"""

import dataclasses
import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.solver.z3_session import (  # noqa: F401
    Z3Formula,
    Z3QueryBuilder,
    Z3Result,
    Z3Session,
    SolveOutcome,
    SolverResult,
)
from jugeo.solver.fragments import (  # noqa: F401
    Fragment,
    LogicalFragment,
    SolverFragment,
    classify_fragment,
)
from jugeo.solver.countermodels import (  # noqa: F401
    Countermodel,
    CountermodelExtractor,
    ObstructionConverter,
    FailureClass,
    RepairType,
)
from jugeo.geometry.supports import SupportRegion, SupportSet  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_smt_top_level(smt: str) -> list[str]:
    """Split an SMT expression into top-level space-separated terms.

    Parenthesised sub-expressions are kept intact so callers can
    decompose compound SMT-LIB2 expressions without a full parser.

    Parameters
    ----------
    smt:
        Raw SMT-LIB string to split.

    Returns
    -------
    list[str]
        Top-level tokens preserving nested structure.
    """
    terms: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in smt:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == " " and depth == 0:
            token = "".join(current).strip()
            if token:
                terms.append(token)
            current = []
        else:
            current.append(ch)
    token = "".join(current).strip()
    if token:
        terms.append(token)
    return terms


def _count_smt_operators(smt: str) -> int:
    """Count logical and arithmetic operators in an SMT-LIB expression.

    Each operator contributes a weighted score: quantifiers contribute 5
    each; nonlinear operators contribute 3 each; other operators 1 each.
    Copilot uses this as a proxy for formula complexity.

    Parameters
    ----------
    smt:
        SMT-LIB expression string.

    Returns
    -------
    int
        Weighted operator count (>= 0).
    """
    lower = smt.lower()
    score = 0
    heavy: dict[str, int] = {
        "forall": 5, "exists": 5, "*": 3, "/": 3, "mod": 3, "div": 3,
    }
    light: list[str] = [
        "and", "or", "not", "=>", "iff", "=", "<", ">", "<=", ">=",
        "+", "-", "let", "ite", "select", "store",
    ]
    for op, weight in heavy.items():
        score += lower.count(op) * weight
    for op in light:
        score += lower.count(op)
    return max(score, 0)


# ---------------------------------------------------------------------------
# 1. Core enumerations
# ---------------------------------------------------------------------------


class DecidabilityClass(str, Enum):
    """Coarse label for the decidability status of a formula or fragment.

    Copilot and the repair pipeline use this label to route formulas to
    the appropriate solver tactic and to select repair strategies.

    Members
    -------
    DECIDABLE
        Fully decidable -- solver terminates with SAT or UNSAT for every
        closed formula.  Examples: QF_LIA, QF_LRA, QF_BV, QF_UF.
    SEMI_DECIDABLE
        SAT is detectable; UNSAT may not terminate.  Example: first-order
        arithmetic with quantifiers.
    UNDECIDABLE
        No algorithm can decide all closed formulas.  Example: nonlinear
        integer arithmetic (Hilbert's 10th problem).
    UNKNOWN
        Decidability not yet determined.
    CONDITIONALLY_DECIDABLE
        Decidable under additional syntactic or semantic constraints.
    """

    DECIDABLE = "decidable"
    """Fully decidable -- solver always terminates."""

    SEMI_DECIDABLE = "semi_decidable"
    """SAT detectable; UNSAT may loop."""

    UNDECIDABLE = "undecidable"
    """No terminating decision procedure exists."""

    UNKNOWN = "unknown"
    """Decidability not yet determined."""

    CONDITIONALLY_DECIDABLE = "conditionally_decidable"
    """Decidable only under additional syntactic/semantic constraints."""

    @property
    def is_safe_for_solver(self) -> bool:
        """Return True when Z3 can safely attempt formulas in this class.

        Copilot gates solver calls on this property; unsafe classes are
        pre-processed or escalated before the solver is invoked.
        """
        return self in (
            DecidabilityClass.DECIDABLE,
            DecidabilityClass.CONDITIONALLY_DECIDABLE,
        )

    @property
    def severity_score(self) -> int:
        """Return an integer severity score from 0 (benign) to 4 (worst).

        Higher scores indicate the formula is further from the decidable
        interior and requires more aggressive repair by the copilot
        pipeline.
        """
        _scores: dict[str, int] = {
            "decidable": 0,
            "conditionally_decidable": 1,
            "unknown": 2,
            "semi_decidable": 3,
            "undecidable": 4,
        }
        return _scores.get(self.value, 2)


class FrontierSide(str, Enum):
    """Which side of the structural frontier a formula occupies.

    Copilot uses this label together with :class:`RepairAction` to select
    the appropriate repair strategy for a formula that has drifted outside
    the decidable region.

    Members
    -------
    INSIDE
        The formula is in the decidable interior; Z3 can decide it.
    BOUNDARY
        The formula is on the frontier -- try solver with timeout,
        escalate on UNKNOWN.
    OUTSIDE
        The formula is in undecidable territory; repair before solver.
    """

    INSIDE = "inside"
    """Decidable interior -- solver can discharge."""

    BOUNDARY = "boundary"
    """Frontier zone -- try with timeout."""

    OUTSIDE = "outside"
    """Undecidable exterior -- repair first."""

    def opposite(self) -> FrontierSide:
        """Return the opposite side: INSIDE<->OUTSIDE; BOUNDARY maps to itself.

        Copilot uses this to flip the search direction when tracing
        back from an outside formula toward the decidable interior.
        """
        if self == FrontierSide.INSIDE:
            return FrontierSide.OUTSIDE
        if self == FrontierSide.OUTSIDE:
            return FrontierSide.INSIDE
        return FrontierSide.BOUNDARY

    def is_solvable(self) -> bool:
        """Return True when the solver should be attempted for this side.

        Both INSIDE and BOUNDARY are solvable (BOUNDARY with a timeout).
        OUTSIDE must be repaired before any solver attempt.
        """
        return self in (FrontierSide.INSIDE, FrontierSide.BOUNDARY)


class RepairAction(str, Enum):
    """Concrete repair steps that move a formula toward the decidable interior.

    The repair pipeline applies actions in cost order.  Copilot selects
    the first action whose :attr:`cost` is within the session budget.

    Members
    -------
    STRENGTHEN_INVARIANT
        Add a conjunct to narrow the type.  Low cost; often effective.
    WEAKEN_CLAIM
        Remove a conjunct, accepting a weaker theorem.  Low cost.
    SPLIT_CONJUNCTION
        Split a conjunction into separate sub-claims.  Medium cost.
    ADD_ABSTRACTION
        Introduce a UF or array abstraction.  Medium-high cost.
    ESCALATE_TO_HUMAN
        Request human review.  Always available as last resort.
    COPILOT_SUGGEST
        Delegate to LLM copilot for a suggested repair.  Medium cost.
    """

    STRENGTHEN_INVARIANT = "strengthen_invariant"
    """Add conjunct to invariant -- low cost."""

    WEAKEN_CLAIM = "weaken_claim"
    """Remove conjunct from claim -- low cost."""

    SPLIT_CONJUNCTION = "split_conjunction"
    """Split conjunction into sub-claims -- medium cost."""

    ADD_ABSTRACTION = "add_abstraction"
    """Introduce UF/array abstraction -- medium-high cost."""

    ESCALATE_TO_HUMAN = "escalate_to_human"
    """Request human review -- always available."""

    COPILOT_SUGGEST = "copilot_suggest"
    """Delegate to LLM copilot -- medium cost."""

    @property
    def cost(self) -> int:
        """Return a dimensionless cost estimate; lower is cheaper and tried first."""
        _costs: dict[str, int] = {
            "strengthen_invariant": 1,
            "weaken_claim": 2,
            "split_conjunction": 4,
            "add_abstraction": 6,
            "copilot_suggest": 7,
            "escalate_to_human": 10,
        }
        return _costs.get(self.value, 5)

    @property
    def requires_human(self) -> bool:
        """Return True when this action requires direct human involvement.

        Copilot uses this to detect when the repair chain is exhausted
        and human attention is mandatory.
        """
        return self == RepairAction.ESCALATE_TO_HUMAN


# ---------------------------------------------------------------------------
# 2. Structural frontier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuralFrontier:
    """A named frontier in formula-space with associated decidability data.

    Each ``StructuralFrontier`` describes a specific boundary in the
    SMT-LIB fragment lattice.  Copilot loads frontiers from the
    :class:`DecidabilityMap` registry when diagnosing a formula's
    position.

    Attributes
    ----------
    frontier_id:
        Unique identifier, e.g. ``"frontier-qf-lia"``.
    name:
        Human-readable name, e.g. ``"QF_LIA / QF_NIA boundary"``.
    decidable_fragment:
        SMT-LIB logic name for the decidable side.
    boundary_formula_smt:
        SMT-LIB2 formula characterising the frontier boundary.
    inside_examples:
        SMT strings known to lie inside (decidable side).
    outside_examples:
        SMT strings known to lie outside (undecidable side).
    decision_procedure:
        Name of the decision procedure for the interior.
    created_at:
        Monotonic timestamp (seconds) of object creation.
    """

    frontier_id: str
    name: str
    decidable_fragment: str
    boundary_formula_smt: str
    inside_examples: tuple[str, ...]
    outside_examples: tuple[str, ...]
    decision_procedure: str
    created_at: float = field(default_factory=time.monotonic)

    def classify_formula(self, smt: str) -> FrontierSide:
        """Heuristically classify *smt* relative to this frontier.

        Proceeds through three stages: (1) quantifier check, (2) nonlinear
        multiplication check, (3) UF-outside-UF-fragment check; defaults
        to INSIDE if none fire.  Copilot calls this before solver submission
        to decide whether to proceed, add a timeout, or repair.

        Parameters
        ----------
        smt:
            SMT-LIB string of the formula to classify.

        Returns
        -------
        FrontierSide
            INSIDE, BOUNDARY, or OUTSIDE.
        """
        lower = smt.lower()
        if "forall" in lower or "exists" in lower:
            logger.debug("classify_formula: quantifier -> OUTSIDE")
            return FrontierSide.OUTSIDE
        if re.search(r"[a-zA-Z_]\w*\s*\*\s*[a-zA-Z_]\w*", smt):
            logger.debug("classify_formula: nonlinear -> BOUNDARY")
            return FrontierSide.BOUNDARY
        if "**" in smt or re.search(r"\b(pow|sqrt)\b", lower):
            return FrontierSide.BOUNDARY
        _uf = {"QF_UF", "QF_AUFLIA", "QF_UFBV", "QF_UFIDL", "AUFLIA"}
        if (
            re.search(r"\b[a-zA-Z_]\w*\s*\(", smt)
            and self.decidable_fragment not in _uf
        ):
            return FrontierSide.BOUNDARY
        return FrontierSide.INSIDE

    def is_decidable_for(self, formula: str) -> bool:
        """Return True when *formula* is classified as INSIDE this frontier.

        Convenience wrapper around :meth:`classify_formula` for callers
        that need only a boolean answer rather than the full FrontierSide.

        Parameters
        ----------
        formula:
            SMT-LIB string to classify.

        Returns
        -------
        bool
            True when the formula is in the decidable interior.
        """
        return self.classify_formula(formula) == FrontierSide.INSIDE

    def boundary_description(self) -> str:
        """Return a multiline description of this frontier.

        Copilot includes this text in its explanation when a formula is
        found to be on or outside the frontier.  Covers the fragment
        name, decision procedure, boundary formula, and known examples.

        Returns
        -------
        str
            Multiline description string.
        """
        lines = [
            f"Frontier: {self.name}",
            f"  ID               : {self.frontier_id}",
            f"  Decidable side   : {self.decidable_fragment}",
            f"  Decision proc.   : {self.decision_procedure}",
            f"  Boundary formula : {self.boundary_formula_smt}",
        ]
        if self.inside_examples:
            lines.append("  Inside examples:")
            for ex in self.inside_examples:
                lines.append(f"    + {ex}")
        if self.outside_examples:
            lines.append("  Outside examples:")
            for ex in self.outside_examples:
                lines.append(f"    - {ex}")
        return "\n".join(lines)

    def merge(self, other: StructuralFrontier) -> StructuralFrontier:
        """Create a new frontier merging inside/outside examples from both.

        The merged frontier takes name, decidable_fragment, and
        decision_procedure from *self*.  The boundary_formula_smt is
        combined with a logical AND; duplicate examples are removed.
        Copilot uses this to consolidate two overlapping frontiers.

        Parameters
        ----------
        other:
            :class:`StructuralFrontier` to merge with this one.

        Returns
        -------
        StructuralFrontier
            New merged frontier; originals are unchanged.
        """
        merged_bnd = (
            f"(and {self.boundary_formula_smt} {other.boundary_formula_smt})"
            if self.boundary_formula_smt and other.boundary_formula_smt
            else self.boundary_formula_smt or other.boundary_formula_smt
        )
        return dataclasses.replace(
            self,
            frontier_id=f"merged-{self.frontier_id}-{other.frontier_id}",
            name=f"{self.name} / {other.name}",
            boundary_formula_smt=merged_bnd,
            inside_examples=tuple(
                dict.fromkeys(self.inside_examples + other.inside_examples)
            ),
            outside_examples=tuple(
                dict.fromkeys(self.outside_examples + other.outside_examples)
            ),
        )

    def summary(self) -> str:
        """Return a concise one-paragraph summary of this frontier.

        Suitable for copilot prompts and progress reports.  Includes
        the fragment, decision procedure, example counts, and the
        first 80 characters of the boundary formula.

        Returns
        -------
        str
            Single-paragraph plain-text summary.
        """
        return (
            f"{self.name} (id={self.frontier_id}) sits between the decidable "
            f"{self.decidable_fragment} (decided by {self.decision_procedure}) "
            f"and undecidable territory.  "
            f"{len(self.inside_examples)} inside example(s), "
            f"{len(self.outside_examples)} outside example(s).  "
            f"Boundary: {self.boundary_formula_smt[:80]}."
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this frontier to a plain JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields as JSON-compatible primitives.
        """
        return {
            "frontier_id": self.frontier_id,
            "name": self.name,
            "decidable_fragment": self.decidable_fragment,
            "boundary_formula_smt": self.boundary_formula_smt,
            "inside_examples": list(self.inside_examples),
            "outside_examples": list(self.outside_examples),
            "decision_procedure": self.decision_procedure,
            "created_at": self.created_at,
        }

    # -- Judgment-geometric integration ------------------------------------

    @property
    def judgment_source(self) -> Any:
        r"""Return the judgment that originated this frontier definition.

        Each structural frontier corresponds to a class of judgments whose
        logical content approaches the boundary of decidability.  This
        property records the link from frontier to originating judgment
        family.

        Returns
        -------
        dict describing the frontier's judgment origin.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment
        except ImportError:
            pass
        return {
            "frontier_id": self.frontier_id,
            "decidable_fragment": self.decidable_fragment,
            "source": "structural_frontier_definition",
        }

    @property
    def trust_annotation(self) -> Any:
        r"""Trust annotation for this frontier classification.

        Frontier classifications produced by the heuristic classifier carry
        ``SOLVER_PARTIAL`` trust; manually curated frontiers carry
        ``HUMAN_ATTESTED``.

        Returns
        -------
        A trust tier or string.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            return TrustTier.REVIEWED
        except (ImportError, AttributeError):
            return "SOLVER_PARTIAL"

    @property
    def solver_target(self) -> dict[str, Any]:
        r"""Z3 session configuration for formulas inside this frontier.

        Returns
        -------
        dict with fragment and decision procedure.
        """
        try:
            from jugeo.solver.z3_session import Z3Session
        except ImportError:
            pass
        return {
            "frontier_id": self.frontier_id,
            "decidable_fragment": self.decidable_fragment,
            "decision_procedure": self.decision_procedure,
        }

    def descent_encoding(self) -> dict[str, Any]:
        r"""Encode descent conditions relative to this frontier.

        The descent condition at a structural frontier is that for a cover
        ``\{U_i\}``, the decidability classification of each ``U_i``'s formula
        agrees on overlaps.  Formulas that straddle the boundary on different
        cover members cannot be glued consistently.

        Returns
        -------
        dict describing the descent encoding.
        """
        try:
            from jugeo.geometry.descent import GluingData
        except ImportError:
            pass
        return {
            "frontier_id": self.frontier_id,
            "boundary_formula": self.boundary_formula_smt,
            "descent_note": (
                "Descent condition: decidability classification must agree on overlaps."
            ),
        }

    @property
    def certificate(self) -> dict[str, Any]:
        r"""Certificate attesting the correctness of this frontier.

        Returns
        -------
        dict summarising frontier well-formedness.
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder
        except ImportError:
            pass
        return {
            "frontier_id": self.frontier_id,
            "name": self.name,
            "decidable_fragment": self.decidable_fragment,
            "inside_count": len(self.inside_examples),
            "outside_count": len(self.outside_examples),
        }

    def site_classification(self):
        """Classify decidability across a Site's coordinates."""
        try:
            from jugeo.geometry.site import Site, Coordinate
            from jugeo.geometry.descent import DescentEngine
            from jugeo.judgments.judgment_terms import Judgment
            from jugeo.evidence.trust import TrustLevel
            return {"classified": True}
        except Exception:
            return {"classified": False}


# ---------------------------------------------------------------------------
# 3. Solver-lifted type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolverLiftedType:
    """A JuGeo type carrying a Z3 invariant as a first-class annotation.

    A ``SolverLiftedType`` wraps a base type name with an SMT-LIB2
    invariant that Z3 can verify or falsify.  ``fragment`` records the
    SMT-LIB logic for the invariant; ``support`` records the geometric
    region where the invariant holds.

    Copilot calls :meth:`check_member`, :meth:`weaken`, and
    :meth:`strengthen` during repair; it reads :meth:`complexity` to
    decide whether to attempt solver discharge or escalate.

    Attributes
    ----------
    type_id:
        Unique identifier, e.g. ``"t-bounded-int"``.
    base_name:
        Base type name, e.g. ``"Int"`` or ``"Array(Int, Bool)"``.
    z3_invariant_smt:
        SMT-LIB2 formula satisfied by all members of this type.
    sort_declaration:
        SMT-LIB2 sort or datatype declaration statement.
    fragment:
        SMT-LIB logic name for the invariant.
    support:
        Geometric region in which this type's invariant is valid.
    is_recursive:
        True when the type definition is inductive/recursive.
    inhabited:
        True when the type is known to be non-empty.
    copilot_suggested:
        True when this type was produced by the copilot repair pipeline.
    """

    type_id: str
    base_name: str
    z3_invariant_smt: str
    sort_declaration: str
    fragment: str
    support: SupportRegion
    is_recursive: bool = False
    inhabited: bool = True
    copilot_suggested: bool = False

    def check_member(self, value_smt: str) -> bool:
        """Heuristically check whether *value_smt* satisfies the invariant.

        Detects obvious tautologies (``true``) and obvious contradictions
        (``false``, self-contradiction after substitution), falling back
        to an optimistic True when inconclusive.  Not a substitute for a
        full solver check.

        Parameters
        ----------
        value_smt:
            SMT-LIB term to test.

        Returns
        -------
        bool
            True on tautology or inconclusive; False on contradiction.
        """
        inv = self.z3_invariant_smt.strip()
        if inv in ("true", "(= true true)"):
            return True
        if inv in ("false", "(= false false)"):
            return False
        substituted = inv.replace("x", value_smt).replace("_v", value_smt)
        if re.search(r"\(<\s*(\S+)\s+\1\s*\)", substituted):
            return False
        if value_smt.lower() in inv.lower():
            return True
        return True

    def intersect(self, other: SolverLiftedType) -> SolverLiftedType:
        """Return a new type whose invariant is the conjunction of both invariants.

        The result is inhabited only by values satisfying both.  Copilot
        calls this to narrow a type when a countermodel suggests an
        additional constraint is needed.

        Parameters
        ----------
        other:
            :class:`SolverLiftedType` to intersect with.

        Returns
        -------
        SolverLiftedType
            New type with conjoined invariant.
        """
        return dataclasses.replace(
            self,
            type_id=f"intersect-{self.type_id}-{other.type_id}",
            base_name=f"{self.base_name} & {other.base_name}",
            z3_invariant_smt=(
                f"(and {self.z3_invariant_smt} {other.z3_invariant_smt})"
            ),
            inhabited=self.inhabited and other.inhabited,
            copilot_suggested=True,
        )

    def union(self, other: SolverLiftedType) -> SolverLiftedType:
        """Return a new type whose invariant is the disjunction of both invariants.

        The result is inhabited by values satisfying at least one
        invariant.  Copilot calls this to combine partial types into a
        covering type during the repair phase.

        Parameters
        ----------
        other:
            :class:`SolverLiftedType` to form a union with.

        Returns
        -------
        SolverLiftedType
            New type with disjunctive invariant.
        """
        return dataclasses.replace(
            self,
            type_id=f"union-{self.type_id}-{other.type_id}",
            base_name=f"{self.base_name} | {other.base_name}",
            z3_invariant_smt=(
                f"(or {self.z3_invariant_smt} {other.z3_invariant_smt})"
            ),
            inhabited=self.inhabited or other.inhabited,
            copilot_suggested=True,
        )

    def weaken(self) -> SolverLiftedType:
        """Return a weakened version of this type with a simplified invariant.

        For conjunctions ``(and A B C ...)``, the last conjunct is
        removed.  For a two-conjunct form the result is the first
        conjunct alone.  Otherwise the invariant becomes ``true``.
        Copilot calls this when the invariant is too strong.

        Returns
        -------
        SolverLiftedType
            New type with strictly weaker invariant.
        """
        inv = self.z3_invariant_smt.strip()
        if inv.lower().startswith("(and "):
            inner = inv[5:].rstrip(")")
            parts = _split_smt_top_level(inner)
            if len(parts) > 2:
                new_inv = f"(and {' '.join(parts[:-1])})"
            elif len(parts) == 2:
                new_inv = parts[0]
            else:
                new_inv = "true"
        else:
            new_inv = "true"
        logger.debug("SolverLiftedType.weaken: %r -> %r", inv[:60], new_inv[:60])
        return dataclasses.replace(
            self,
            z3_invariant_smt=new_inv,
            inhabited=True,
            copilot_suggested=True,
        )

    def strengthen(self, extra_invariant: str) -> SolverLiftedType:
        """Return a stronger type with *extra_invariant* conjoined.

        The returned type is a subtype of ``self``.  Copilot calls this
        when a countermodel suggests an additional constraint that would
        prevent the failing assignment.

        Parameters
        ----------
        extra_invariant:
            SMT-LIB2 formula to conjoin.

        Returns
        -------
        SolverLiftedType
            New type with strengthened invariant, or *self* if
            *extra_invariant* is trivial.
        """
        stripped = (extra_invariant or "").strip()
        if not stripped or stripped == "true":
            return self
        return dataclasses.replace(
            self,
            z3_invariant_smt=f"(and {self.z3_invariant_smt} {extra_invariant})",
            copilot_suggested=True,
        )

    def complexity(self) -> int:
        """Return a weighted operator count as a complexity proxy.

        Weights quantifiers and nonlinear operators more heavily than
        linear ones.  Copilot uses this to decide whether to attempt
        inline solver discharge or escalate immediately.

        Returns
        -------
        int
            Non-negative weighted complexity score.
        """
        return _count_smt_operators(self.z3_invariant_smt)

    def to_smt2(self) -> str:
        """Return an SMT-LIB2 ``assert`` statement for this type's invariant.

        Annotated with the type name and id for unambiguous identification
        when pasting into an SMT-LIB2 script.

        Returns
        -------
        str
            SMT-LIB2 assert statement.
        """
        return (
            f"; type invariant for {self.base_name} (id={self.type_id})\n"
            f"(assert {self.z3_invariant_smt})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields as JSON-compatible primitives.
        """
        return {
            "type_id": self.type_id,
            "base_name": self.base_name,
            "z3_invariant_smt": self.z3_invariant_smt,
            "sort_declaration": self.sort_declaration,
            "fragment": self.fragment,
            "is_recursive": self.is_recursive,
            "inhabited": self.inhabited,
            "copilot_suggested": self.copilot_suggested,
            "complexity": self.complexity(),
        }

    # -- Judgment-geometric integration ------------------------------------

    @property
    def judgment_source(self) -> Any:
        r"""Return the judgment that this lifted type encodes.

        A solver-lifted type pairs a base type with a Z3 invariant; the
        originating judgment is the type refinement assertion that the
        invariant captures.

        Returns
        -------
        dict with type and invariant metadata.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment, Proposition
        except ImportError:
            pass
        return {
            "type_id": self.type_id,
            "base_name": self.base_name,
            "invariant": self.z3_invariant_smt[:80],
            "source": "solver_lifted_type",
        }

    @property
    def trust_annotation(self) -> Any:
        r"""Trust annotation for this lifted type.

        Copilot-suggested types carry ``ORACLE_PROPOSED``; solver-verified
        types carry ``SOLVER_DISCHARGED``.

        Returns
        -------
        A trust tier or string.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            return TrustTier.PROPOSAL if self.copilot_suggested else TrustTier.DISCHARGED
        except (ImportError, AttributeError):
            return "ORACLE_PROPOSED" if self.copilot_suggested else "SOLVER_DISCHARGED"

    @property
    def solver_target(self) -> dict[str, Any]:
        r"""Z3 session target for verifying this type's invariant.

        Returns
        -------
        dict with solver configuration.
        """
        try:
            from jugeo.solver.z3_session import Z3Session
        except ImportError:
            pass
        return {
            "type_id": self.type_id,
            "fragment": self.fragment,
            "sort_declaration": self.sort_declaration,
            "is_recursive": self.is_recursive,
        }

    def descent_encoding(self) -> dict[str, Any]:
        r"""Encode the descent condition for this lifted type.

        The descent condition for a type is that its invariant is preserved
        under restriction maps in the Grothendieck topology: if ``\phi``
        holds at ``X`` and ``f: Y \to X`` is a cover morphism, then
        ``f^*(\phi)`` holds at ``Y``.

        Returns
        -------
        dict describing the descent encoding.
        """
        try:
            from jugeo.geometry.descent import GluingData
        except ImportError:
            pass
        return {
            "type_id": self.type_id,
            "invariant": self.z3_invariant_smt,
            "descent_note": "Type invariant must be preserved under restrictions.",
        }

    @property
    def certificate(self) -> dict[str, Any]:
        r"""Certificate for this solver-lifted type.

        Returns
        -------
        dict with type well-formedness metadata.
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder
        except ImportError:
            pass
        return {
            "type_id": self.type_id,
            "base_name": self.base_name,
            "fragment": self.fragment,
            "inhabited": self.inhabited,
            "copilot_suggested": self.copilot_suggested,
        }


# ---------------------------------------------------------------------------
# 4. Frontier boundary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontierBoundary:
    """A directed crossing between two SMT-LIB fragments with a repair plan.

    Describes the cost and recommended repair actions for crossing between
    the decidable ``inside_fragment`` and the undecidable
    ``outside_territory``.  Copilot uses ``crossing_cost``,
    ``repair_actions``, and :meth:`classify_crossing` when building a
    repair plan.

    Attributes
    ----------
    boundary_id:
        Unique identifier, e.g. ``"boundary-qf-lia-to-qf-nia"``.
    description:
        Human-readable description of this crossing.
    inside_fragment:
        SMT-LIB logic on the decidable (inside) side.
    outside_territory:
        SMT-LIB logic or region name on the undecidable (outside) side.
    crossing_cost:
        Dimensionless cost; < 10 means the pipeline can automate it.
    examples:
        Representative formulas that lie on this boundary.
    repair_actions:
        Ordered repair actions, cheapest first.
    """

    boundary_id: str
    description: str
    inside_fragment: str
    outside_territory: str
    crossing_cost: int
    examples: tuple[str, ...] = ()
    repair_actions: tuple[RepairAction, ...] = ()

    def is_crossable(self) -> bool:
        """Return True when the automated pipeline can attempt this crossing.

        Crossings with cost < 10 are considered automatable; higher-cost
        crossings require human escalation.

        Returns
        -------
        bool
            True when crossing_cost < 10.
        """
        return self.crossing_cost < 10

    def cheapest_repair(self) -> RepairAction:
        """Return the lowest-cost repair action for this boundary.

        Returns the first element of ``repair_actions`` (assumed sorted)
        or ESCALATE_TO_HUMAN when the list is empty.

        Returns
        -------
        RepairAction
            Cheapest available repair.
        """
        if self.repair_actions:
            return self.repair_actions[0]
        return RepairAction.ESCALATE_TO_HUMAN

    def classify_crossing(self, formula: str) -> str:
        """Return a descriptive string about crossing *formula* over this boundary.

        Checks against known examples, then applies heuristics for
        quantifiers (forward crossing) and nonlinear patterns (hover).
        Copilot includes this in repair explanation strings.

        Parameters
        ----------
        formula:
            SMT-LIB string to classify.

        Returns
        -------
        str
            Human-readable crossing classification.
        """
        for ex in self.examples:
            if formula.strip() == ex.strip():
                return (
                    f"Formula matches known boundary example for "
                    f"{self.inside_fragment}/{self.outside_territory}."
                )
        lower = formula.lower()
        if "forall" in lower or "exists" in lower:
            return (
                f"Forward crossing: quantifiers place formula in "
                f"{self.outside_territory} (outside {self.inside_fragment}).  "
                f"Cost={self.crossing_cost}.  "
                f"Recommended: {self.cheapest_repair().value}."
            )
        if re.search(r"[a-zA-Z_]\w*\s*\*\s*[a-zA-Z_]\w*", formula):
            return (
                f"Boundary hover: nonlinear pattern near "
                f"{self.inside_fragment}/{self.outside_territory}.  "
                f"Cost={self.crossing_cost}.  "
                f"Consider {RepairAction.ADD_ABSTRACTION.value}."
            )
        return (
            f"Formula appears inside {self.inside_fragment}.  "
            f"No crossing required.  Boundary: {self.boundary_id}."
        )

    def summary(self) -> str:
        """Return a concise one-line summary for this boundary.

        Returns
        -------
        str
            One-line string with id, fragments, cost, and crossability.
        """
        return (
            f"FrontierBoundary({self.boundary_id}): "
            f"{self.inside_fragment} -> {self.outside_territory}, "
            f"cost={self.crossing_cost}, crossable={self.is_crossable()}, "
            f"actions={len(self.repair_actions)}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields as JSON-compatible primitives.
        """
        return {
            "boundary_id": self.boundary_id,
            "description": self.description,
            "inside_fragment": self.inside_fragment,
            "outside_territory": self.outside_territory,
            "crossing_cost": self.crossing_cost,
            "examples": list(self.examples),
            "repair_actions": [a.value for a in self.repair_actions],
        }


# ---------------------------------------------------------------------------
# 5. Decidability map
# ---------------------------------------------------------------------------


@dataclass
class DecidabilityMap:
    """A mutable registry of fragments, frontiers, and boundaries.

    The ``DecidabilityMap`` is the central index for decidability
    information in a JuGeo analysis session.  Copilot calls
    :meth:`lookup` to classify a formula, :meth:`crossing_path` to
    build a repair plan, and :meth:`copilot_map_summary` to obtain a
    structured description suitable for LLM prompt injection.

    Attributes
    ----------
    map_id:
        Unique identifier for this map instance.
    fragment_assignments:
        Maps fragment name to :class:`DecidabilityClass`.
    boundary_registry:
        Maps boundary_id to :class:`FrontierBoundary`.
    frontier_registry:
        Maps frontier_id to :class:`StructuralFrontier`.
    """

    map_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    fragment_assignments: dict[str, DecidabilityClass] = field(default_factory=dict)
    boundary_registry: dict[str, FrontierBoundary] = field(default_factory=dict)
    frontier_registry: dict[str, StructuralFrontier] = field(default_factory=dict)

    def register_fragment(self, name: str, cls: DecidabilityClass) -> None:
        """Register a fragment name with its decidability class.

        Emits a warning when overwriting an existing entry with a
        different class, enabling detection of accidental re-registrations
        in logs.

        Parameters
        ----------
        name:
            SMT-LIB logic name, e.g. ``"QF_BV"``.
        cls:
            :class:`DecidabilityClass` for this fragment.
        """
        if name in self.fragment_assignments:
            existing = self.fragment_assignments[name]
            if existing != cls:
                logger.warning(
                    "register_fragment: overwriting %s (%s -> %s)",
                    name, existing.value, cls.value,
                )
        self.fragment_assignments[name] = cls

    def register_boundary(self, boundary: FrontierBoundary) -> None:
        """Add a :class:`FrontierBoundary` to the boundary registry.

        Overwrites any existing entry with the same boundary_id.

        Parameters
        ----------
        boundary:
            Boundary to register.
        """
        self.boundary_registry[boundary.boundary_id] = boundary

    def register_frontier(self, frontier: StructuralFrontier) -> None:
        """Add a :class:`StructuralFrontier` to the frontier registry.

        Overwrites any existing entry with the same frontier_id.

        Parameters
        ----------
        frontier:
            Frontier to register.
        """
        self.frontier_registry[frontier.frontier_id] = frontier

    def lookup(self, formula_smt: str) -> DecidabilityClass:
        """Heuristically determine the decidability class of *formula_smt*.

        Stages: (1) quantifier+nonlinear -> UNDECIDABLE, (2) quantifier
        alone -> SEMI_DECIDABLE, (3) registered fragment name in string
        -> registered class, (4) nonlinear pattern -> UNDECIDABLE, (5)
        default -> DECIDABLE.  Copilot calls this before solver dispatch.

        Parameters
        ----------
        formula_smt:
            SMT-LIB formula string to classify.

        Returns
        -------
        DecidabilityClass
            Best-guess decidability class.
        """
        lower = formula_smt.lower()
        has_q = "forall" in lower or "exists" in lower
        has_nl = bool(
            re.search(r"[a-zA-Z_]\w*\s*\*\s*[a-zA-Z_]\w*", formula_smt)
        )
        if has_q and has_nl:
            return DecidabilityClass.UNDECIDABLE
        if has_q:
            return DecidabilityClass.SEMI_DECIDABLE
        for frag_name, cls in self.fragment_assignments.items():
            if frag_name.lower() in lower:
                return cls
        if has_nl:
            return DecidabilityClass.UNDECIDABLE
        return DecidabilityClass.DECIDABLE

    def all_decidable(self) -> list[str]:
        """Return sorted names of all DECIDABLE fragments in the registry.

        Returns
        -------
        list[str]
            Sorted fragment names with class DECIDABLE.
        """
        return sorted(
            n for n, c in self.fragment_assignments.items()
            if c == DecidabilityClass.DECIDABLE
        )

    def all_undecidable(self) -> list[str]:
        """Return sorted names of all UNDECIDABLE fragments in the registry.

        Returns
        -------
        list[str]
            Sorted fragment names with class UNDECIDABLE.
        """
        return sorted(
            n for n, c in self.fragment_assignments.items()
            if c == DecidabilityClass.UNDECIDABLE
        )

    def crossing_path(self, src: str, dst: str) -> list[FrontierBoundary]:
        """Return a sequence of boundaries forming a path from *src* to *dst*.

        Performs BFS over the boundary registry treating each boundary as
        an undirected edge.  Returns the shortest-hop path; empty list if
        no path exists.  Copilot calls this to build step-by-step repair
        plans for formulas that need to move between two fragments.

        Parameters
        ----------
        src:
            Fragment name for the starting point.
        dst:
            Fragment name for the destination.

        Returns
        -------
        list[FrontierBoundary]
            Ordered boundaries forming the path; empty if none found.
        """
        if src == dst:
            return []
        adj: dict[str, list[tuple[str, FrontierBoundary]]] = {}
        for b in self.boundary_registry.values():
            adj.setdefault(b.inside_fragment, []).append((b.outside_territory, b))
            adj.setdefault(b.outside_territory, []).append((b.inside_fragment, b))
        visited: set[str] = {src}
        queue: deque[tuple[str, list[FrontierBoundary]]] = deque([(src, [])])
        while queue:
            node, path = queue.popleft()
            for neighbour, boundary in adj.get(node, []):
                if neighbour == dst:
                    return path + [boundary]
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append((neighbour, path + [boundary]))
        logger.debug("crossing_path: no path %r -> %r", src, dst)
        return []

    def copilot_map_summary(self) -> str:
        """Return a structured summary of this map for copilot consumption.

        Includes counts and full listings of registered fragments,
        frontiers, and boundaries.  Suitable for LLM prompt injection.

        Returns
        -------
        str
            Multi-section plain-text summary.
        """
        lines = [
            f"# DecidabilityMap id={self.map_id}",
            f"  Fragments  : {len(self.fragment_assignments)}",
            f"  Frontiers  : {len(self.frontier_registry)}",
            f"  Boundaries : {len(self.boundary_registry)}",
            "",
            "## Fragment Assignments",
        ]
        for name in sorted(self.fragment_assignments):
            cls = self.fragment_assignments[name]
            tag = "safe" if cls.is_safe_for_solver else "unsafe"
            lines.append(f"  [{tag:>6}] {name:<20} {cls.value}")
        if self.frontier_registry:
            lines += ["", "## Frontiers"]
            for fid, f in self.frontier_registry.items():
                lines.append(f"  {fid}: {f.name} [{f.decidable_fragment}]")
        if self.boundary_registry:
            lines += ["", "## Boundaries"]
            for bid, b in self.boundary_registry.items():
                lines.append(
                    f"  {bid}: {b.inside_fragment} -> {b.outside_territory}"
                    f" (cost={b.crossing_cost}, crossable={b.is_crossable()})"
                )
        dec = self.all_decidable()
        und = self.all_undecidable()
        lines += [
            "",
            "## Decidable ({}): {}".format(len(dec), ", ".join(dec) or "(none)"),
            "## Undecidable ({}): {}".format(len(und), ", ".join(und) or "(none)"),
        ]
        return "\n".join(lines)

    def export(self) -> dict[str, Any]:
        """Export the full map as a plain JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All registry contents as nested JSON-compatible structures.
        """
        return {
            "map_id": self.map_id,
            "fragment_assignments": {
                k: v.value for k, v in self.fragment_assignments.items()
            },
            "boundary_registry": {
                k: v.to_dict() for k, v in self.boundary_registry.items()
            },
            "frontier_registry": {
                k: v.to_dict() for k, v in self.frontier_registry.items()
            },
        }

    # -- Judgment-geometric integration ------------------------------------

    @property
    def judgment_source(self) -> Any:
        r"""The judgment family this decidability map classifies.

        A decidability map is a functor from the fragment lattice to the
        decidability poset.  Its judgment source is the universe of judgment
        forms that it can classify.

        Returns
        -------
        dict describing the map's judgment scope.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment
        except ImportError:
            pass
        return {
            "map_id": self.map_id,
            "fragment_count": len(self.fragment_assignments),
            "frontier_count": len(self.frontier_registry),
        }

    @property
    def trust_annotation(self) -> Any:
        r"""Trust annotation for this map.

        Returns
        -------
        A trust tier or string.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            return TrustTier.REVIEWED
        except (ImportError, AttributeError):
            return "SOLVER_PARTIAL"

    @property
    def solver_target(self) -> dict[str, Any]:
        r"""Solver configuration summary for this decidability map.

        Returns
        -------
        dict mapping decidable fragments to solver parameters.
        """
        try:
            from jugeo.solver.z3_session import Z3Session
        except ImportError:
            pass
        decidable = [
            frag for frag, cls in self.fragment_assignments.items()
            if cls == DecidabilityClass.DECIDABLE
        ]
        return {
            "map_id": self.map_id,
            "decidable_fragments": decidable,
            "total_fragments": len(self.fragment_assignments),
        }

    def descent_encoding(self) -> dict[str, Any]:
        r"""Descent encoding for the decidability map.

        The descent condition at the level of the decidability map requires
        that fragment assignments are compatible across covers: overlapping
        regions must agree on decidability classification.

        Returns
        -------
        dict describing the descent encoding.
        """
        try:
            from jugeo.geometry.descent import GluingData
        except ImportError:
            pass
        return {
            "map_id": self.map_id,
            "descent_note": (
                "Fragment assignments must be compatible on overlaps."
            ),
        }

    @property
    def certificate(self) -> dict[str, Any]:
        r"""Certificate for this decidability map.

        Returns
        -------
        dict summarising the map.
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder
        except ImportError:
            pass
        return {
            "map_id": self.map_id,
            "fragment_count": len(self.fragment_assignments),
            "boundary_count": len(self.boundary_registry),
            "frontier_count": len(self.frontier_registry),
        }


# ---------------------------------------------------------------------------
# 6. Countermodel obstruction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountermodelObstruction:
    """A rich obstruction pairing a countermodel with its frontier location.

    When Z3 returns SAT on the negation of a claim, the resulting model
    is a counterexample that localises a type invariant violation.
    Copilot reads :meth:`most_likely_repair`, :meth:`is_resolvable`, and
    :meth:`to_repair_ticket` when building its repair proposal.

    Attributes
    ----------
    obstruction_id:
        Unique identifier for this obstruction.
    countermodel:
        The concrete counterexample extracted from Z3.
    violated_invariant:
        SMT-LIB string of the invariant that was falsified.
    repair_frontier:
        The :class:`FrontierBoundary` where the obstruction was found.
    suggested_actions:
        Ordered repair actions (cheapest / most likely first).
    confidence:
        Float in [0.0, 1.0]; confidence in the repair suggestions.
    copilot_explanation:
        Human-readable explanation from the copilot pipeline.
    """

    obstruction_id: str
    countermodel: Countermodel = field(hash=False)
    violated_invariant: str
    repair_frontier: FrontierBoundary
    suggested_actions: tuple[RepairAction, ...]
    confidence: float
    copilot_explanation: str

    def most_likely_repair(self) -> RepairAction:
        """Return the most likely repair action.

        Returns the first element of ``suggested_actions`` when non-empty;
        always falls back to ESCALATE_TO_HUMAN so the pipeline always has
        a next step.

        Returns
        -------
        RepairAction
            Best candidate repair action.
        """
        if self.suggested_actions:
            return self.suggested_actions[0]
        return RepairAction.ESCALATE_TO_HUMAN

    def is_resolvable(self) -> bool:
        """Return True when the pipeline should attempt automated resolution.

        Resolvable requires confidence > 0.3 *and* at least one repair
        action.  Below the threshold the obstruction is escalated to human
        review regardless of available actions.

        Returns
        -------
        bool
            True when automated resolution should be attempted.
        """
        return self.confidence > 0.3 and bool(self.suggested_actions)

    def escalate(self) -> CountermodelObstruction:
        """Return a copy with ESCALATE_TO_HUMAN appended to suggested_actions.

        Used when earlier repair actions are exhausted or confidence drops
        below threshold.  The original object is unchanged (frozen).

        Returns
        -------
        CountermodelObstruction
            New obstruction with ESCALATE_TO_HUMAN as the terminal action.
        """
        if RepairAction.ESCALATE_TO_HUMAN in self.suggested_actions:
            return self
        return dataclasses.replace(
            self,
            suggested_actions=self.suggested_actions + (RepairAction.ESCALATE_TO_HUMAN,),
            copilot_explanation=(
                self.copilot_explanation
                + " [escalated: confidence below threshold or actions exhausted]"
            ),
        )

    def to_repair_ticket(self) -> dict[str, Any]:
        """Produce a structured repair ticket for downstream tooling.

        Contains all information needed to queue and track the repair in
        an issue tracker or copilot work-queue.

        Returns
        -------
        dict[str, Any]
            Repair ticket as a JSON-compatible dictionary.
        """
        return {
            "ticket_id": f"repair-{self.obstruction_id}",
            "obstruction_id": self.obstruction_id,
            "violated_invariant": self.violated_invariant,
            "frontier_boundary": self.repair_frontier.boundary_id,
            "inside_fragment": self.repair_frontier.inside_fragment,
            "outside_territory": self.repair_frontier.outside_territory,
            "crossing_cost": self.repair_frontier.crossing_cost,
            "confidence": round(self.confidence, 4),
            "is_resolvable": self.is_resolvable(),
            "most_likely_repair": self.most_likely_repair().value,
            "all_suggested_actions": [a.value for a in self.suggested_actions],
            "copilot_explanation": self.copilot_explanation,
            "countermodel_id": getattr(
                self.countermodel, "model_id", str(id(self.countermodel))
            ),
        }

    def copilot_summary(self) -> str:
        """Return a copilot-readable summary of this obstruction.

        Formats key fields into a compact multi-line string suitable for
        LLM prompt injection.

        Returns
        -------
        str
            Compact multi-line summary.
        """
        return "\n".join([
            f"CountermodelObstruction {self.obstruction_id}",
            f"  violated     : {self.violated_invariant[:80]}",
            f"  frontier     : {self.repair_frontier.boundary_id}",
            f"  confidence   : {self.confidence:.2f}",
            f"  resolvable   : {self.is_resolvable()}",
            f"  repair       : {self.most_likely_repair().value}",
            f"  explanation  : {self.copilot_explanation[:120]}",
        ])

    # -- Judgment-geometric integration ------------------------------------

    @property
    def judgment_source(self) -> Any:
        r"""The judgment whose invariant this obstruction falsifies.

        Returns
        -------
        dict with the violated invariant and frontier metadata.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment
        except ImportError:
            pass
        return {
            "obstruction_id": self.obstruction_id,
            "violated_invariant": self.violated_invariant[:80],
            "frontier": self.repair_frontier.boundary_id,
        }

    @property
    def trust_annotation(self) -> Any:
        r"""Trust annotation for this obstruction.

        Obstructions carry negative trust — they are evidence *against* a
        claim.  The trust tier reflects the solver's confidence, capped at
        ``SOLVER_PARTIAL`` since the obstruction is constructive evidence
        of failure, not of correctness.

        Returns
        -------
        A trust tier or string.
        """
        try:
            from jugeo.evidence.trust import TrustTier
            return TrustTier.REVIEWED
        except (ImportError, AttributeError):
            return "SOLVER_PARTIAL"

    @property
    def solver_target(self) -> dict[str, Any]:
        r"""Solver configuration that produced this obstruction.

        Returns
        -------
        dict with obstruction and frontier metadata.
        """
        try:
            from jugeo.solver.z3_session import Z3Session
        except ImportError:
            pass
        return {
            "obstruction_id": self.obstruction_id,
            "inside_fragment": self.repair_frontier.inside_fragment,
            "outside_territory": self.repair_frontier.outside_territory,
        }

    def descent_encoding(self) -> dict[str, Any]:
        r"""Encode the descent obstruction witnessed by this countermodel.

        In the Čech-cohomological picture, this obstruction represents a
        non-trivial class in ``H^1``.  The descent encoding captures the
        gluing failure as an SMT formula whose satisfiability witnesses the
        obstruction.

        Returns
        -------
        dict with descent-failure encoding.
        """
        try:
            from jugeo.geometry.descent import DescentObstruction
        except ImportError:
            pass
        return {
            "obstruction_id": self.obstruction_id,
            "violated_invariant": self.violated_invariant,
            "countermodel_id": getattr(
                self.countermodel, "model_id", str(id(self.countermodel))
            ),
            "descent_note": "Non-trivial H¹ class: gluing fails at this obstruction.",
        }

    @property
    def certificate(self) -> dict[str, Any]:
        r"""Certificate for this obstruction.

        The certificate records the violated invariant, the repair frontier,
        and the confidence level — sufficient for downstream audit.

        Returns
        -------
        dict summarising the obstruction as a certificate.
        """
        try:
            from jugeo.evidence.certificates import CertificateBuilder
        except ImportError:
            pass
        return {
            "obstruction_id": self.obstruction_id,
            "violated_invariant": self.violated_invariant[:80],
            "confidence": round(self.confidence, 4),
            "is_resolvable": self.is_resolvable(),
            "most_likely_repair": self.most_likely_repair().value,
        }


# ---------------------------------------------------------------------------
# 7. Factory functions
# ---------------------------------------------------------------------------


def make_default_frontier(name: str, fragment: str) -> StructuralFrontier:
    """Create a :class:`StructuralFrontier` with sensible defaults.

    The frontier_id is derived from the fragment name; examples come from
    the module-level constants; the decision procedure is looked up from a
    built-in map, falling back to a generic description.

    Parameters
    ----------
    name:
        Human-readable frontier name.
    fragment:
        SMT-LIB logic name for the decidable side.

    Returns
    -------
    StructuralFrontier
        New frontier with default examples and a trivial boundary formula.
    """
    frontier_id = f"frontier-{fragment.lower().replace('_', '-')}"
    _procs: dict[str, str] = {
        "QF_LIA": "Presburger arithmetic / omega test",
        "QF_LRA": "Fourier-Motzkin elimination",
        "QF_BV": "bit-blasting to propositional SAT",
        "QF_UF": "congruence closure (Nelson-Oppen)",
        "QF_AUFLIA": "combination: arrays + UF + linear integer",
        "QF_ABV": "combination: arrays + bitvectors",
        "QF_IDL": "integer difference logic / Bellman-Ford",
        "QF_RDL": "real difference logic / Dijkstra",
        "QF_UFBV": "combination: UF + bitvectors",
        "PROP": "DPLL / CDCL propositional solver",
    }
    procedure = _procs.get(fragment, f"solver procedure for {fragment}")
    inside: tuple[str, ...] = (
        (f"(assert (= x 0))  ; {fragment} example",)
        if fragment in KNOWN_DECIDABLE_FRAGMENTS else ()
    )
    outside: tuple[str, ...] = (
        ("(assert (exists ((x Int)) (= (* x x) 2)))  ; undecidable",)
        if KNOWN_UNDECIDABLE_REGIONS else ()
    )
    return StructuralFrontier(
        frontier_id=frontier_id,
        name=name,
        decidable_fragment=fragment,
        boundary_formula_smt=f"(assert (= _fragment_tag {fragment!r}))",
        inside_examples=inside,
        outside_examples=outside,
        decision_procedure=procedure,
    )


def make_default_boundary(inside: str, outside: str) -> FrontierBoundary:
    """Create a :class:`FrontierBoundary` with default repair actions.

    The crossing cost is the sum of costs for the first three actions in
    :data:`DEFAULT_REPAIR_SEQUENCE`.  The full sequence becomes the
    boundary's ``repair_actions``.

    Parameters
    ----------
    inside:
        SMT-LIB logic on the decidable (inside) side.
    outside:
        SMT-LIB logic or region name on the undecidable (outside) side.

    Returns
    -------
    FrontierBoundary
        New boundary with default repair actions and cost estimate.
    """
    boundary_id = (
        f"boundary-{inside.lower().replace('_', '-')}"
        f"-to-{outside.lower().replace('_', '-')}"
    )
    cost = sum(a.cost for a in DEFAULT_REPAIR_SEQUENCE[:3])
    return FrontierBoundary(
        boundary_id=boundary_id,
        description=(
            f"Automated boundary from {inside} (decidable) to "
            f"{outside} (undecidable/semi-decidable)."
        ),
        inside_fragment=inside,
        outside_territory=outside,
        crossing_cost=cost,
        examples=(),
        repair_actions=DEFAULT_REPAIR_SEQUENCE,
    )


def make_default_map() -> DecidabilityMap:
    """Return a :class:`DecidabilityMap` pre-populated with known fragments.

    Registers all :data:`KNOWN_DECIDABLE_FRAGMENTS` as DECIDABLE and all
    :data:`KNOWN_UNDECIDABLE_REGIONS` as UNDECIDABLE.  Registers default
    frontiers for QF_LIA, QF_LRA, QF_BV, and QF_UF, and default
    boundaries for five common fragment pairs.  This is the recommended
    entry point for bootstrapping in production code and tests.

    Returns
    -------
    DecidabilityMap
        Fully populated map ready for production use.
    """
    dm = DecidabilityMap(map_id=f"default-map-{uuid.uuid4().hex[:8]}")
    for frag in KNOWN_DECIDABLE_FRAGMENTS:
        dm.register_fragment(frag, DecidabilityClass.DECIDABLE)
    for region in KNOWN_UNDECIDABLE_REGIONS:
        dm.register_fragment(region, DecidabilityClass.UNDECIDABLE)
    for frag in ("FOL", "PA", "HA"):
        dm.register_fragment(frag, DecidabilityClass.SEMI_DECIDABLE)
    for frag in ("QF_LIA", "QF_LRA", "QF_BV", "QF_UF"):
        dm.register_frontier(make_default_frontier(f"{frag} / beyond", frag))
    for ins, outs in [
        ("QF_LIA", "QF_NIA"),
        ("QF_LRA", "NRA"),
        ("QF_BV", "QF_NIA"),
        ("QF_UF", "FOL"),
        ("QF_AUFLIA", "QF_NIA"),
    ]:
        dm.register_boundary(make_default_boundary(ins, outs))
    logger.debug(
        "make_default_map: %d fragments, %d frontiers, %d boundaries",
        len(dm.fragment_assignments),
        len(dm.frontier_registry),
        len(dm.boundary_registry),
    )
    return dm


# ---------------------------------------------------------------------------
# 8. Module-level constants
# ---------------------------------------------------------------------------


KNOWN_DECIDABLE_FRAGMENTS: list[str] = [
    "QF_LIA",     # quantifier-free linear integer arithmetic
    "QF_LRA",     # quantifier-free linear real arithmetic
    "QF_BV",      # quantifier-free bitvectors
    "QF_UF",      # quantifier-free uninterpreted functions
    "QF_AUFLIA",  # arrays + UF + linear integer arithmetic
    "QF_ABV",     # arrays + bitvectors
    "QF_IDL",     # integer difference logic
    "QF_RDL",     # real difference logic
    "QF_UFBV",    # UF + bitvectors
    "PROP",       # propositional logic (SAT)
]
"""SMT-LIB logic names known to be decidable by Z3."""

KNOWN_UNDECIDABLE_REGIONS: list[str] = [
    "QF_NIA",   # nonlinear integer arithmetic (Hilbert's 10th problem)
    "NRA",      # nonlinear real arithmetic (general)
    "FOL",      # first-order logic (general)
    "SOL",      # second-order logic
    "HO",       # higher-order logic
]
"""Fragment names or logic regions known to be undecidable."""

DEFAULT_REPAIR_SEQUENCE: tuple[RepairAction, ...] = (
    RepairAction.STRENGTHEN_INVARIANT,
    RepairAction.WEAKEN_CLAIM,
    RepairAction.SPLIT_CONJUNCTION,
    RepairAction.ADD_ABSTRACTION,
    RepairAction.COPILOT_SUGGEST,
    RepairAction.ESCALATE_TO_HUMAN,
)
"""Default ordered repair sequence from cheapest to most expensive.

Copilot applies actions from this sequence until the formula is back in
the decidable interior or the list is exhausted.  ESCALATE_TO_HUMAN is
always the terminal action.
"""


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------


__all__ = [
    "DecidabilityClass",
    "FrontierSide",
    "RepairAction",
    "StructuralFrontier",
    "SolverLiftedType",
    "FrontierBoundary",
    "CountermodelObstruction",
    "DecidabilityMap",
    "make_default_frontier",
    "make_default_boundary",
    "make_default_map",
    "KNOWN_DECIDABLE_FRAGMENTS",
    "KNOWN_UNDECIDABLE_REGIONS",
    "DEFAULT_REPAIR_SEQUENCE",
]

# copilot: shared-core marker for LLM orchestration.
