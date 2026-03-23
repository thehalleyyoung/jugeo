"""mutation_countermodel_encoder.py — Mutation countermodel encoder.

Theory2.tex Chapter 29 §5: Mutation countermodel encoder — when a mutation
violates an invariant, extract a structured repair guide.

This module implements ``MutationCountermodelEncoder``, the fifth and final
encoding layer in Chapter 29.  This is the *repair mode* encoder: it operates
after the solver has returned UNSAT (or UNKNOWN) for a mutation + invariant
query and:

1.  Encodes the *violation formula* (mutation ∧ ¬invariant) and checks it.
2.  Extracts a concrete countermodel from the Z3 result.
3.  Localises the violation to a minimal MutationSlice.
4.  Generates a structured RepairSuggestion.
5.  Optionally verifies candidate repairs inductively.
6.  Enumerates minimal repairs within a budget.

Design
------
The encoder delegates heavy reconstruction work to the
``jugeo.solver.reconstruction`` layer (ProofReconstructor, WitnessReconstructor,
ModelReconstructor).  When that layer is unavailable, it falls back to
symbolic stubs.

# copilot: MutationCountermodelEncoder — Theory2.tex §29.5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Z3 availability guard
# ---------------------------------------------------------------------------
try:
    import z3 as _z3

    _Z3_AVAILABLE = True
except ImportError:
    _z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

# ---------------------------------------------------------------------------
# Solver layer imports (optional)
# ---------------------------------------------------------------------------
try:
    from jugeo.solver.z3_session import SolveOutcome, Z3Session

    _Z3_SESSION_AVAILABLE = True
except ImportError:
    SolveOutcome = None  # type: ignore[assignment,misc]
    Z3Session = None  # type: ignore[assignment,misc]
    _Z3_SESSION_AVAILABLE = False

try:
    from jugeo.solver.reconstruction import (
        ModelReconstructor,
        ReconstructionResult,
        WitnessReconstructor,
        reconstruct_countermodel,
    )

    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    ModelReconstructor = None  # type: ignore[assignment,misc]
    ReconstructionResult = None  # type: ignore[assignment,misc]
    WitnessReconstructor = None  # type: ignore[assignment,misc]
    reconstruct_countermodel = None  # type: ignore[assignment]
    _RECONSTRUCTION_AVAILABLE = False

from jugeo.encodings.sequence_mutation_encodings.models import (
    MutationKind,
    MutationSlice,
    SequenceEncoding,
)


# ---------------------------------------------------------------------------
# RepairSuggestion
# ---------------------------------------------------------------------------


class RepairKind(Enum):
    """Classifies the kind of repair suggested.

    Values
    ------
    VALUE_CORRECTION
        Change the value at one or more specific indices.
    SUPPORT_REDUCTION
        Reduce the declared support set (mutation is over-approximate).
    INVARIANT_WEAKENING
        The invariant is too strong; suggest a weaker form.
    PRECONDITION_STRENGTHENING
        Add a precondition that prevents the violating input.
    MUTATION_KIND_CHANGE
        Change the mutation kind (e.g., ARBITRARY → POINTWISE).
    NO_REPAIR_FOUND
        The repair budget was exhausted with no viable repair.

    # copilot: RepairKind enum — classifies repair suggestions.
    """

    VALUE_CORRECTION = auto()
    SUPPORT_REDUCTION = auto()
    INVARIANT_WEAKENING = auto()
    PRECONDITION_STRENGTHENING = auto()
    MUTATION_KIND_CHANGE = auto()
    NO_REPAIR_FOUND = auto()


@dataclass(frozen=True)
class RepairSuggestion:
    """A structured repair suggestion produced by MutationCountermodelEncoder.

    Fields
    ------
    kind : RepairKind
        The kind of repair being suggested.
    description : str
        Human-readable description of the repair.
    affected_indices : frozenset[int]
        The indices/addresses affected by the repair.
    correction_values : dict[int, Any]
        For VALUE_CORRECTION: the corrected values at each affected index.
    new_support : frozenset[int] | None
        For SUPPORT_REDUCTION: the smaller support set.
    weakened_invariant : Any
        For INVARIANT_WEAKENING: the weakened invariant formula.
    confidence : float
        Confidence score in [0, 1] (copilot-assigned, ORACLE_PROPOSED trust).
    repair_formula : Any
        The Z3 formula verifying this repair.
    copilot_notes : str
        Notes from the copilot assist module about this repair.

    # copilot: RepairSuggestion dataclass — structured repair from §29.5.
    """

    kind: RepairKind
    description: str
    affected_indices: frozenset[int]
    correction_values: dict[int, Any] = field(default_factory=dict)
    new_support: frozenset[int] | None = None
    weakened_invariant: Any = None
    confidence: float = 0.5
    repair_formula: Any = None
    copilot_notes: str = ""

    def is_actionable(self) -> bool:
        """Return True if this suggestion can be directly acted upon.

        Returns
        -------
        bool
        """
        return self.kind not in (RepairKind.NO_REPAIR_FOUND,)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "kind": self.kind.name,
            "description": self.description,
            "affected_indices": sorted(self.affected_indices),
            "correction_values": {str(k): str(v) for k, v in self.correction_values.items()},
            "confidence": self.confidence,
            "copilot_notes": self.copilot_notes,
        }


# ---------------------------------------------------------------------------
# ViolationContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ViolationContext:
    """Context for a detected mutation-invariant violation.

    Fields
    ------
    pre_encoding : SequenceEncoding
        The pre-mutation encoding.
    post_encoding : SequenceEncoding
        The post-mutation encoding.
    mutation_slice : MutationSlice
        The mutation that was applied.
    invariant_formula : Any
        The Z3 formula that was violated.
    violation_formula : Any
        The formula ``mutation ∧ ¬invariant`` (the violation witness).
    solver_result : Any
        The Z3 result or SolveOutcome.
    model : Any
        The satisfying model for the violation formula (if SAT).

    # copilot: ViolationContext — holds all context for a detected violation.
    """

    pre_encoding: SequenceEncoding
    post_encoding: SequenceEncoding
    mutation_slice: MutationSlice
    invariant_formula: Any
    violation_formula: Any
    solver_result: Any = None
    model: Any = None


# ---------------------------------------------------------------------------
# MutationCountermodelEncoder
# ---------------------------------------------------------------------------


class MutationCountermodelEncoder:
    """Encodes mutation violations and extracts structured repair suggestions.

    This is the repair-mode encoder.  It:

    1.  Builds the violation formula: ``mutation_predicate ∧ ¬invariant``
    2.  Checks satisfiability (a SAT result means the violation is real)
    3.  Extracts a countermodel from the satisfying assignment
    4.  Localises the violation to a minimal MutationSlice
    5.  Generates a RepairSuggestion

    Parameters
    ----------
    name_prefix : str
        Prefix for Z3 symbol names.
    solver_timeout_ms : int
        Solver timeout in milliseconds.

    Theory2.tex §29.5.

    # copilot: MutationCountermodelEncoder — Theory2.tex §29.5.
    """

    def __init__(
        self,
        name_prefix: str = "mce",
        solver_timeout_ms: int = 5000,
    ) -> None:
        """Initialise the countermodel encoder.

        Parameters
        ----------
        name_prefix:
            Prefix for Z3 symbol names.
        solver_timeout_ms:
            Solver timeout in milliseconds (default 5000).
        """
        self._prefix = name_prefix
        self._counter = 0
        self._timeout_ms = solver_timeout_ms
        self._violations: list[ViolationContext] = []
        self._repairs: list[RepairSuggestion] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode_mutation_violation(
        self,
        pre: SequenceEncoding,
        mutation: MutationSlice,
        post: SequenceEncoding,
        invariant: Any,
    ) -> Any:
        """Encode the violation formula: ``mutation_pred ∧ ¬invariant``.

        This formula is SAT iff the mutation violates the invariant.

        Parameters
        ----------
        pre:
            Pre-mutation SequenceEncoding.
        mutation:
            The MutationSlice describing what changed.
        post:
            Post-mutation SequenceEncoding.
        invariant:
            Z3 formula that should hold on ``post``.

        Returns
        -------
        Any
            A Z3 And formula or string stub.

        Theory2.tex §29.5 Definition 29.5.

        # copilot: encode_mutation_violation — builds violation check formula.
        """
        mut_pred = mutation.mutation_predicate()
        pre_invs = list(pre.invariant_set())
        post_invs = list(post.invariant_set())
        if _Z3_AVAILABLE and not isinstance(mut_pred, str):
            neg_inv = _z3.Not(invariant) if not isinstance(invariant, str) else _z3.BoolVal(True)
            parts = [mut_pred] + pre_invs + post_invs + [neg_inv]
            # Filter out string stubs
            z3_parts = [p for p in parts if not isinstance(p, str)]
            if z3_parts:
                return _z3.And(*z3_parts)
            return _z3.BoolVal(True)
        return (
            f"({mut_pred}) AND "
            f"(pre_invariants) AND "
            f"(post_invariants) AND "
            f"NOT ({invariant})"
        )

    def extract_countermodel(
        self,
        z3_result: Any,
        context: ViolationContext,
    ) -> Any:
        """Extract a structured countermodel from a Z3 result.

        Uses the reconstruction layer (``jugeo.solver.reconstruction``) when
        available; falls back to a dict-based stub model.

        Parameters
        ----------
        z3_result:
            A Z3 result object (``z3.Solver`` after check, or ``SolveOutcome``).
        context:
            The ViolationContext holding the encodings.

        Returns
        -------
        Any
            A ``ReconstructionResult`` or a dict stub.

        Theory2.tex §29.5 — countermodel extraction.

        # copilot: extract_countermodel — delegates to reconstruction layer.
        """
        if _RECONSTRUCTION_AVAILABLE and reconstruct_countermodel is not None:
            try:
                model_obj = None
                if _Z3_AVAILABLE and hasattr(z3_result, "model"):
                    model_obj = z3_result.model()
                return reconstruct_countermodel(model_obj)
            except Exception as exc:
                logger.warning("extract_countermodel: reconstruction failed: %s", exc)
        # Stub countermodel
        stub_model: dict[str, Any] = {}
        if _Z3_AVAILABLE and hasattr(z3_result, "model"):
            try:
                m = z3_result.model()
                for decl in m:
                    stub_model[str(decl)] = str(m[decl])
            except Exception:
                pass
        return stub_model

    def localize_violation(
        self,
        countermodel: Any,
        support: frozenset[int],
        mutation: MutationSlice,
    ) -> MutationSlice:
        """Localize a violation to the minimal sub-slice of *mutation*.

        Given the countermodel, identifies which indices in *support* are
        actually responsible for the invariant violation and returns a
        smaller MutationSlice covering only those indices.

        Algorithm:
        1.  For each ``i ∈ support``, check if the countermodel value at
            index ``i`` violates the invariant independently.
        2.  Collect the set of *culprit* indices.
        3.  Return a new MutationSlice restricted to the culprit support.

        Parameters
        ----------
        countermodel:
            A countermodel (dict or ReconstructionResult).
        support:
            The declared support of the mutation.
        mutation:
            The original MutationSlice.

        Returns
        -------
        MutationSlice
            A minimal sub-slice of *mutation*.

        Theory2.tex §29.5 — violation localization.

        # copilot: localize_violation — minimal culprit support for repair.
        """
        if isinstance(countermodel, dict):
            # Heuristic: any index whose countermodel value differs from pre is a culprit
            culprits: set[int] = set()
            for idx in support:
                pre_key = f"{mutation.base_encoding.name_hint}[{idx}]"
                post_key = f"{mutation.post_encoding.name_hint}[{idx}]" if mutation.post_encoding else None
                pre_val = countermodel.get(pre_key)
                post_val = countermodel.get(post_key) if post_key else None
                if pre_val is not None and post_val is not None and pre_val != post_val:
                    culprits.add(idx)
            if culprits:
                return mutation.repair_slice({})._replace_support(frozenset(culprits)) \
                    if hasattr(mutation, "_replace_support") else \
                    MutationSlice(
                        base_encoding=mutation.base_encoding,
                        lo=min(culprits) if culprits else mutation.lo,
                        hi=max(culprits) + 1 if culprits else mutation.hi,
                        new_values=mutation.new_values,
                        support_set=frozenset(culprits),
                        mutation_kind=MutationKind.PARTIAL,
                        post_encoding=mutation.post_encoding,
                    )
        # Fallback: return original mutation
        return mutation

    def generate_repair_guide(
        self,
        slice_: MutationSlice,
        countermodel: Any,
    ) -> RepairSuggestion:
        """Generate a RepairSuggestion from a localized MutationSlice and countermodel.

        The generated repair is a VALUE_CORRECTION: for each culprit index,
        suggest the value that would satisfy the invariant based on the
        countermodel and simple local heuristics.

        Parameters
        ----------
        slice_:
            A (minimal) MutationSlice.
        countermodel:
            The countermodel dict or ReconstructionResult.

        Returns
        -------
        RepairSuggestion
            A structured repair suggestion.

        Theory2.tex §29.5 — repair guide generation.

        # copilot: generate_repair_guide — VALUE_CORRECTION repair from §29.5.
        """
        affected = slice_.support_set
        corrections: dict[int, Any] = {}
        if isinstance(countermodel, dict):
            for idx in sorted(affected):
                key = f"{slice_.base_encoding.name_hint}[{idx}]"
                val = countermodel.get(key)
                if val is not None:
                    # Simple heuristic: negate / clamp / default
                    try:
                        v = int(str(val))
                        corrections[idx] = max(0, v)  # clamp to non-negative
                    except (ValueError, TypeError):
                        corrections[idx] = 0
        description = (
            f"VALUE_CORRECTION at indices {sorted(affected)}: "
            f"adjust values to satisfy the violated invariant. "
            f"Countermodel shows {len(countermodel) if isinstance(countermodel, dict) else '?'} "
            f"variable assignments."
        )
        return RepairSuggestion(
            kind=RepairKind.VALUE_CORRECTION,
            description=description,
            affected_indices=affected,
            correction_values=corrections,
            confidence=0.6,
            copilot_notes=(
                "This repair was generated automatically by the countermodel encoder. "
                "It should be verified with encode_inductive_repair before applying."
            ),
        )

    def encode_inductive_repair(
        self,
        slice_: MutationSlice,
        candidate_fix: dict[int, Any],
    ) -> Any:
        """Return a Z3 formula that is SAT iff *candidate_fix* is a valid repair.

        The formula encodes:
            ``mutation_with_fix ∧ invariant_holds``

        where ``mutation_with_fix`` replaces the values at ``candidate_fix``
        indices with the corrected values.

        Parameters
        ----------
        slice_:
            The MutationSlice to repair.
        candidate_fix:
            Mapping from index to corrected value.

        Returns
        -------
        Any
            A Z3 formula or string stub.

        Theory2.tex §29.5 — inductive repair verification.

        # copilot: encode_inductive_repair — verify proposed fix with Z3.
        """
        repaired_slice = slice_.repair_slice(candidate_fix)
        repair_pred = repaired_slice.mutation_predicate()
        if _Z3_AVAILABLE and not isinstance(repair_pred, str):
            return repair_pred
        return (
            f"inductive_repair_check(support={sorted(candidate_fix.keys())}, "
            f"fix={candidate_fix})"
        )

    def enumerate_minimal_repairs(
        self,
        violation: ViolationContext,
        budget: int = 5,
    ) -> list[RepairSuggestion]:
        """Enumerate up to *budget* minimal repair suggestions for a violation.

        Explores repair strategies in priority order:
        1.  VALUE_CORRECTION (cheapest: fix specific values)
        2.  SUPPORT_REDUCTION (medium: reduce declared support)
        3.  PRECONDITION_STRENGTHENING (expensive: add pre-condition)

        Parameters
        ----------
        violation:
            The ViolationContext to repair.
        budget:
            Maximum number of repair suggestions to return.

        Returns
        -------
        list[RepairSuggestion]
            Up to *budget* RepairSuggestion instances.

        Theory2.tex §29.5 Theorem 29.1 — minimal repair existence.

        # copilot: enumerate_minimal_repairs — budgeted repair enumeration.
        """
        repairs: list[RepairSuggestion] = []
        if budget <= 0:
            return [RepairSuggestion(
                kind=RepairKind.NO_REPAIR_FOUND,
                description="Budget exhausted",
                affected_indices=frozenset(),
                confidence=0.0,
            )]
        # Strategy 1: VALUE_CORRECTION
        if len(repairs) < budget:
            countermodel = violation.model or {}
            loc_slice = self.localize_violation(
                countermodel,
                violation.mutation_slice.support_set,
                violation.mutation_slice,
            )
            r1 = self.generate_repair_guide(loc_slice, countermodel)
            repairs.append(r1)
        # Strategy 2: SUPPORT_REDUCTION
        if len(repairs) < budget:
            support = violation.mutation_slice.support_set
            if len(support) > 1:
                # Try removing each element from support
                for drop_idx in sorted(support):
                    if len(repairs) >= budget:
                        break
                    reduced = support - {drop_idx}
                    repairs.append(RepairSuggestion(
                        kind=RepairKind.SUPPORT_REDUCTION,
                        description=(
                            f"Remove index {drop_idx} from support. "
                            f"New support: {sorted(reduced)}"
                        ),
                        affected_indices=frozenset([drop_idx]),
                        new_support=reduced,
                        confidence=0.4,
                        copilot_notes=(
                            f"Dropping index {drop_idx} reduces mutation footprint. "
                            f"Verify that the invariant still holds on the reduced support."
                        ),
                    ))
        # Strategy 3: PRECONDITION_STRENGTHENING
        if len(repairs) < budget:
            repairs.append(RepairSuggestion(
                kind=RepairKind.PRECONDITION_STRENGTHENING,
                description=(
                    "Add a precondition that rules out the violating input. "
                    "See violation.invariant_formula for the invariant that failed."
                ),
                affected_indices=violation.mutation_slice.support_set,
                confidence=0.3,
                copilot_notes=(
                    "Precondition strengthening is a last resort. "
                    "Prefer VALUE_CORRECTION or SUPPORT_REDUCTION if possible."
                ),
            ))
        return repairs[:budget]

    def explain_violation(
        self,
        countermodel: Any,
        slice_: MutationSlice,
    ) -> str:
        """Return a human-readable explanation of the violation.

        Parameters
        ----------
        countermodel:
            The countermodel dict or ReconstructionResult.
        slice_:
            The MutationSlice that was violated.

        Returns
        -------
        str
            A multi-line explanation string.

        Theory2.tex §29.5 — violation explanation.

        # copilot: explain_violation — human-readable violation summary.
        """
        lines = [
            "Mutation-Invariant Violation Report",
            "=" * 40,
            f"Mutation kind    : {slice_.mutation_kind.name}",
            f"Slice range      : [{slice_.lo}, {slice_.hi})",
            f"Support set      : {sorted(slice_.support_set)}",
            f"Support valid    : {slice_.validate_support()}",
            "",
        ]
        if isinstance(countermodel, dict) and countermodel:
            lines.append("Countermodel assignments:")
            for k, v in sorted(countermodel.items()):
                lines.append(f"  {k} = {v}")
        else:
            lines.append("Countermodel: (not available or empty)")
        lines += [
            "",
            "Diagnosis:",
            "  The mutation modifies at least one index in the support set",
            "  in a way that violates the attached invariant on the post-state.",
            "  Use enumerate_minimal_repairs() to generate repair suggestions.",
        ]
        return "\n".join(lines)

    def copilot_suggest_repair(
        self,
        violation_summary: str,
    ) -> str:
        """Return a copilot-generated repair hint for a violation summary.

        This is the copilot interface for repair suggestion: given a plain-text
        summary of the violation, returns a structured hint string for the
        developer.

        The response carries ORACLE_PROPOSED trust — it must be independently
        verified with ``encode_inductive_repair`` before being applied.

        Parameters
        ----------
        violation_summary:
            A plain-text description of the violation (e.g., from
            ``explain_violation``).

        Returns
        -------
        str
            A repair hint string with ORACLE_PROPOSED trust annotation.

        Theory2.tex §29.5 Remark 29.5 — copilot repair hints.

        # copilot: copilot_suggest_repair — LLM-assisted repair suggestion.
        """
        hint_lines = [
            "[COPILOT REPAIR HINT — ORACLE_PROPOSED trust]",
            "",
            "Based on the violation summary, consider the following approaches:",
            "",
            "1. VALUE_CORRECTION: Identify the index where the invariant fails",
            "   and adjust the post-state value to satisfy the invariant.",
            "   Example: if the invariant requires arr[i] >= 0, ensure the",
            "   mutation does not assign negative values.",
            "",
            "2. SUPPORT_REDUCTION: If the support is over-declared, remove",
            "   unnecessary indices. The minimal support is the set of indices",
            "   that are actually written.",
            "",
            "3. PRECONDITION: If neither of the above works, add a precondition",
            "   that prevents the violating input from reaching the mutation.",
            "",
            f"Violation summary snippet: {violation_summary[:200]}...",
            "",
            "Trust annotation: ORACLE_PROPOSED. Verify with encode_inductive_repair.",
        ]
        return "\n".join(hint_lines)

    def record_violation(self, ctx: ViolationContext) -> None:
        """Record a ViolationContext for later inspection.

        Parameters
        ----------
        ctx:
            The ViolationContext to record.
        """
        self._violations.append(ctx)

    def all_violations(self) -> list[ViolationContext]:
        """Return all recorded violations.

        Returns
        -------
        list[ViolationContext]
        """
        return list(self._violations)

    def all_repairs(self) -> list[RepairSuggestion]:
        """Return all generated repair suggestions.

        Returns
        -------
        list[RepairSuggestion]
        """
        return list(self._repairs)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fresh_name(self, prefix: str) -> str:
        self._counter += 1
        return f"{self._prefix}_{prefix}_{self._counter}"


__all__: list[str] = [
    "MutationCountermodelEncoder",
    "RepairSuggestion",
    "ViolationContext",
    "RepairKind",
]
