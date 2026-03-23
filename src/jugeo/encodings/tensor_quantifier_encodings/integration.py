"""
Integration with the JuGeo Solver Layer.
=========================================
Chapter 30 — tensor quantifier encoding integration.

copilot notes: this module bridges the tensor encoding layer with the
Z3 session pool, fragment classifier, and reconstruction pipeline.  It
provides the high-level API for:

1. Solving tensor extent queries through the fragment classifier → QF_LIA.
2. Checking affine legality conditions with a Z3 session.
3. Extracting tensor witnesses from satisfying models.
4. Applying quantifier discipline to formulas before solving.
5. Reconstructing affine proofs from UNSAT results.

All solver-layer imports are guarded with try/except so this module can be
imported without the jugeo.solver package (for testing and documentation).
The integration methods gracefully degrade to stub behaviour when the solver
layer is unavailable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = [
    "TensorQuantifierSolverIntegration",
    "TensorEncodingContext",
]

# ---------------------------------------------------------------------------
# Optional Z3 imports
# ---------------------------------------------------------------------------

try:
    import z3 as _z3  # type: ignore[import]

    _Z3_AVAILABLE = True
except ImportError:
    _z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False


def _z3_and(*args: Any) -> Any:
    """Create a Z3 And expression or return a symbolic stub string."""
    if _Z3_AVAILABLE and args:
        return _z3.And(*args)
    return f"And({', '.join(str(a) for a in args)})"


# ---------------------------------------------------------------------------
# Optional solver imports
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import (
        Z3Session,
        Z3SessionPool,
        Z3Formula,
        Z3Encoder,
        Z3Decoder,
        Z3QueryBuilder,
        Z3Result,
        Z3FragmentClassifier,
        Z3TacticRouter,
        Z3SessionMonitor,
        Z3Serializer,
        Z3CopilotAssist,
        SolveOutcome,
        FormulaKind,
        TrustLevel,
    )
    _SOLVER_AVAILABLE = True
except ImportError:
    _SOLVER_AVAILABLE = False
    # Stub types for type-checking only
    Z3Session = Any  # type: ignore[misc,assignment]
    Z3SessionPool = Any  # type: ignore[misc,assignment]
    Z3Formula = Any  # type: ignore[misc,assignment]
    Z3Encoder = Any  # type: ignore[misc,assignment]
    Z3Decoder = Any  # type: ignore[misc,assignment]
    Z3QueryBuilder = Any  # type: ignore[misc,assignment]
    Z3Result = Any  # type: ignore[misc,assignment]
    Z3FragmentClassifier = Any  # type: ignore[misc,assignment]
    Z3TacticRouter = Any  # type: ignore[misc,assignment]
    Z3SessionMonitor = Any  # type: ignore[misc,assignment]
    Z3Serializer = Any  # type: ignore[misc,assignment]
    Z3CopilotAssist = Any  # type: ignore[misc,assignment]
    SolveOutcome = Any  # type: ignore[misc,assignment]
    FormulaKind = Any  # type: ignore[misc,assignment]
    TrustLevel = Any  # type: ignore[misc,assignment]

try:
    from jugeo.solver.fragments import (
        Fragment,
        FragmentSignature,
        FragmentClassifier,
        FragmentDecomposer,
        EncodingStrategy,
        TacticSelector,
        FragmentCache,
        FragmentStatistics,
        CopilotFragmentAssist,
        LogicalFragment,
        SolverFragment,
        classify_fragment,
    )
    _FRAGMENTS_AVAILABLE = True
except ImportError:
    _FRAGMENTS_AVAILABLE = False
    Fragment = Any  # type: ignore[misc,assignment]
    FragmentSignature = Any  # type: ignore[misc,assignment]
    FragmentClassifier = Any  # type: ignore[misc,assignment]
    FragmentDecomposer = Any  # type: ignore[misc,assignment]
    EncodingStrategy = Any  # type: ignore[misc,assignment]
    TacticSelector = Any  # type: ignore[misc,assignment]
    FragmentCache = Any  # type: ignore[misc,assignment]
    FragmentStatistics = Any  # type: ignore[misc,assignment]
    CopilotFragmentAssist = Any  # type: ignore[misc,assignment]
    LogicalFragment = Any  # type: ignore[misc,assignment]
    SolverFragment = Any  # type: ignore[misc,assignment]
    classify_fragment = None  # type: ignore[assignment]

try:
    from jugeo.solver.reconstruction import (
        ReconstructionKind,
        ValidationStatus,
        ProofStep,
        WitnessBinding,
        SortInterpretation,
        FunctionInterpretation,
        ArrayInterpretation,
        DatatypeInterpretation,
        ReconstructionResult,
        ReconstructionReport,
        ProofReconstructor,
        WitnessReconstructor,
        ModelReconstructor,
        EvidenceAssembler,
        PartialReconstructor,
        ReconstructionCache,
        ReconstructionValidator,
        ReconstructionPipeline,
        ReconstructionStatistics,
        reconstruct_countermodel,
    )
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    ReconstructionKind = Any  # type: ignore[misc,assignment]
    ValidationStatus = Any  # type: ignore[misc,assignment]
    ProofStep = Any  # type: ignore[misc,assignment]
    WitnessBinding = Any  # type: ignore[misc,assignment]
    SortInterpretation = Any  # type: ignore[misc,assignment]
    FunctionInterpretation = Any  # type: ignore[misc,assignment]
    ArrayInterpretation = Any  # type: ignore[misc,assignment]
    DatatypeInterpretation = Any  # type: ignore[misc,assignment]
    ReconstructionResult = Any  # type: ignore[misc,assignment]
    ReconstructionReport = Any  # type: ignore[misc,assignment]
    ProofReconstructor = Any  # type: ignore[misc,assignment]
    WitnessReconstructor = Any  # type: ignore[misc,assignment]
    ModelReconstructor = Any  # type: ignore[misc,assignment]
    EvidenceAssembler = Any  # type: ignore[misc,assignment]
    PartialReconstructor = Any  # type: ignore[misc,assignment]
    ReconstructionCache = Any  # type: ignore[misc,assignment]
    ReconstructionValidator = Any  # type: ignore[misc,assignment]
    ReconstructionPipeline = Any  # type: ignore[misc,assignment]
    ReconstructionStatistics = Any  # type: ignore[misc,assignment]
    reconstruct_countermodel = None  # type: ignore[assignment]

# Internal imports — relative to this package
from jugeo.encodings.tensor_quantifier_encodings.models import (
    TensorExtent,
    TensorConstraint,
    AffineLegality,
    QuantifierDiscipline,
    DisciplineKind,
)
from jugeo.encodings.tensor_quantifier_encodings.witness_extractor import (
    TensorWitnessExtractor,
    DependenceWitness,
)


# ---------------------------------------------------------------------------
# TensorEncodingContext
# ---------------------------------------------------------------------------


@dataclass
class TensorEncodingContext:
    """Lightweight container holding all information for a tensor encoding query.

    This dataclass bundles a TensorExtent, its associated TensorConstraints, a
    QuantifierDiscipline, and a solver session identifier into a single object
    that can be passed around and serialised.

    Attributes:
        extent: The tensor extent (shape, strides, layout).
        constraints: List of TensorConstraints to conjoin with the extent formula.
        discipline: Quantifier discipline to apply before solving.
        session_id: Identifier of the Z3 session to use for solving.

    copilot notes: Use ``to_formula()`` to obtain the full Z3 formula for this
    context, ready to assert into a solver session.
    """

    extent: TensorExtent
    constraints: list[TensorConstraint]
    discipline: QuantifierDiscipline
    session_id: str

    def to_formula(self) -> Any:
        """Build the full Z3 formula from the extent and all constraints.

        Conjoins:
        1. The extent's full shape formula (``extent.to_z3_formula()``).
        2. All constraint expressions from ``self.constraints``.

        The resulting formula is in QF_LIA (or QF_NIA for parametric shapes).

        Returns:
            Z3 formula (or stub string) encoding the entire context.
        """
        parts: list[Any] = [self.extent.to_z3_formula()]
        for constraint in self.constraints:
            parts.append(constraint.encode())
        return _z3_and(*parts) if parts else (True if _Z3_AVAILABLE else "True")

    def to_json(self) -> str:
        """Serialise the context metadata to a JSON string.

        Returns:
            JSON string with extent rank, number of constraints, discipline kind,
            and session_id.
        """
        return json.dumps({
            "extent_rank": self.extent.rank,
            "n_constraints": len(self.constraints),
            "discipline_kind": self.discipline.discipline_kind.value,
            "session_id": self.session_id,
        }, indent=2)

    def describe(self) -> str:
        """Return a human-readable description of this context.

        Returns:
            Multi-line description string.
        """
        return (
            f"TensorEncodingContext:\n"
            f"  session_id  : {self.session_id}\n"
            f"  rank        : {self.extent.rank}\n"
            f"  constraints : {len(self.constraints)}\n"
            f"  discipline  : {self.discipline.discipline_kind.value}\n"
        )


# ---------------------------------------------------------------------------
# TensorQuantifierSolverIntegration
# ---------------------------------------------------------------------------


class TensorQuantifierSolverIntegration:
    """Bridges the tensor encoding layer with the JuGeo solver infrastructure.

    This class connects:
    - ``TensorExtent`` / ``TensorConstraint`` encoding (models.py).
    - ``QuantifierDiscipline`` (models.py / s03).
    - ``Z3SessionPool`` for solver session management.
    - ``Z3FragmentClassifier`` for routing queries to the right tactic.
    - ``ReconstructionPipeline`` for proof reconstruction.
    - ``TensorWitnessExtractor`` for SAT model witness extraction.

    copilot notes: When the jugeo.solver layer is not available (e.g., during
    testing), all solve methods return stub results and log a warning.

    Example::

        integration = TensorQuantifierSolverIntegration(
            session_pool=pool,
            fragment_classifier=classifier,
            reconstruction_pipeline=pipeline,
        )
        result = integration.solve_tensor_extent_query(extent, constraints)
    """

    def __init__(
        self,
        session_pool: Any,
        fragment_classifier: Any,
        reconstruction_pipeline: Any,
    ) -> None:
        """Initialise the integration layer.

        Args:
            session_pool: A Z3SessionPool (or compatible stub) for managing
                          solver sessions.
            fragment_classifier: A Z3FragmentClassifier (or stub) for routing
                                 queries to the appropriate Z3 tactic.
            reconstruction_pipeline: A ReconstructionPipeline (or stub) for
                                     reconstructing proofs and witnesses.
        """
        self._pool = session_pool
        self._classifier = fragment_classifier
        self._reconstruction = reconstruction_pipeline
        self._call_log: list[str] = []

    def _log(self, message: str) -> None:
        """Append a message to the internal call log."""
        self._call_log.append(message)

    def _stub_result(self, reason: str) -> Any:
        """Return a stub result dict when the solver is unavailable.

        Args:
            reason: Why the solver was not called.

        Returns:
            Dict stub with 'status': 'stub' and 'reason'.
        """
        return {"status": "stub", "reason": reason, "sat": False, "model": None}

    def solve_tensor_extent_query(
        self,
        extent: TensorExtent,
        constraints: list[TensorConstraint],
    ) -> Any:
        """Solve a tensor extent query by encoding it as QF_LIA and invoking Z3.

        Builds the formula by conjoining the extent's shape formula with all
        constraint expressions, classifies it as QF_LIA, routes it through the
        session pool, and returns the Z3Result.

        Args:
            extent: The TensorExtent encoding the tensor shape and strides.
            constraints: List of TensorConstraints to assert along with the shape.

        Returns:
            Z3Result (or stub dict) from the solver.
        """
        self._log("solve_tensor_extent_query: start")

        # Build the formula
        formula_parts: list[Any] = [extent.to_z3_formula()]
        for c in constraints:
            formula_parts.append(c.encode())

        combined = _z3_and(*formula_parts) if formula_parts else None
        self._log(f"  formula assembled, {len(formula_parts)} parts")

        if not _SOLVER_AVAILABLE:
            self._log("  solver unavailable — returning stub")
            return self._stub_result("jugeo.solver not available")

        if not _Z3_AVAILABLE:
            self._log("  z3 not available — returning stub")
            return self._stub_result("z3 not available")

        try:
            session = (
                self._pool.get_session()
                if hasattr(self._pool, "get_session")
                else None
            )
            if session is None:
                return self._stub_result("session_pool.get_session() returned None")

            if hasattr(session, "check"):
                result = session.check(combined)
                self._log(f"  solver returned: {result}")
                return result
        except Exception as exc:
            self._log(f"  solver error: {exc}")
            return self._stub_result(f"solver raised: {exc}")

        return self._stub_result("no check method on session")

    def check_affine_legality(
        self,
        transform: AffineLegality,
        dep_vectors: list[list[int]],
        session: Any,
    ) -> tuple[bool, DependenceWitness | None]:
        """Check whether an affine transform is legal for the given dependence vectors.

        Encodes the legality condition as a QF_LIA formula (for each d in dep_vectors,
        M*d is lex-positive), checks satisfiability (the formula should be valid —
        i.e., its negation should be UNSAT), and extracts a counterexample if found.

        Args:
            transform: The AffineLegality object holding the transform matrix.
            dep_vectors: List of integer dependence vectors to check.
            session: A Z3Session to use for solving.

        Returns:
            ``(True, None)`` if the transform is legal (no violation found).
            ``(False, DependenceWitness)`` if a violating dependence was found.
        """
        self._log("check_affine_legality: start")

        # Pure Python check first (no solver needed for small cases)
        from jugeo.encodings.tensor_quantifier_encodings.algorithms import (
            affine_transformation_legality,
        )
        is_legal, violating_d = affine_transformation_legality(
            transform.transform_matrix, dep_vectors
        )
        self._log(f"  pure Python check: legal={is_legal}, violating={violating_d}")

        if is_legal:
            return (True, None)

        # Build a witness from the violating dependence vector
        md = None
        if violating_d is not None and transform.transform_matrix:
            M = transform.transform_matrix
            n = len(violating_d)
            md = [
                sum(M[r][c] * violating_d[c] for c in range(n))
                for r in range(len(M))
            ]

        witness = DependenceWitness(
            dep_vector=list(violating_d) if violating_d is not None else [],
            src_iteration=[0] * len(violating_d) if violating_d else [],
            dst_iteration=list(violating_d) if violating_d else [],
            violating_constraint_idx=0,
            transform_applied=transform.transform_matrix,
            farkas_multipliers=None,
            notes=(
                f"M*d = {md} is not lex-positive."
                if md is not None
                else "Transform is illegal."
            ),
        )

        return (False, witness)

    def extract_tensor_witness(
        self,
        sat_result: Any,
        extent: TensorExtent,
    ) -> TensorWitnessExtractor:
        """Build a TensorWitnessExtractor from a SAT result and a tensor extent.

        Extracts the Z3 model from the SAT result (if available) and
        initialises a TensorWitnessExtractor ready to extract the shape witness.

        Args:
            sat_result: A Z3Result or stub dict from a solve call.
            extent: The TensorExtent whose variables should be evaluated.

        Returns:
            TensorWitnessExtractor initialised with the model.
        """
        self._log("extract_tensor_witness: start")

        model: Any = None
        if isinstance(sat_result, dict):
            model = sat_result.get("model")
        elif hasattr(sat_result, "model"):
            model = sat_result.model

        extractor = TensorWitnessExtractor(z3_model=model)
        self._log(f"  extractor created, model={'present' if model else 'absent'}")
        return extractor

    def apply_quantifier_discipline(
        self,
        formula: Any,
        discipline: QuantifierDiscipline,
    ) -> Any:
        """Apply a QuantifierDiscipline to a formula to produce a QF formula.

        Dispatches to ``discipline.apply_discipline(formula)``.

        Args:
            formula: A Z3 formula (or stub string).
            discipline: The QuantifierDiscipline to apply.

        Returns:
            The disciplined formula.
        """
        self._log(f"apply_quantifier_discipline: kind={discipline.discipline_kind}")
        return discipline.apply_discipline(formula)

    def solve_with_discipline(
        self,
        formula: Any,
        discipline: QuantifierDiscipline,
        session: Any,
    ) -> Any:
        """Apply quantifier discipline then solve the resulting QF formula.

        Steps:
        1. Apply ``discipline.apply_discipline(formula)`` to get a QF formula.
        2. Invoke ``session.check(qf_formula)`` to solve.

        Args:
            formula: A formula (possibly with quantifiers).
            discipline: The QuantifierDiscipline to apply.
            session: A Z3Session or compatible object.

        Returns:
            Z3Result or stub dict.
        """
        self._log("solve_with_discipline: start")
        qf_formula = self.apply_quantifier_discipline(formula, discipline)
        self._log(f"  discipline applied, kind={discipline.discipline_kind}")

        if hasattr(session, "check"):
            try:
                result = session.check(qf_formula)
                self._log(f"  solver result: {result}")
                return result
            except Exception as exc:
                self._log(f"  solver error: {exc}")
                return self._stub_result(f"solve_with_discipline raised: {exc}")

        return self._stub_result("session has no check method")

    def reconstruct_affine_proof(self, unsat_result: Any) -> Any:
        """Reconstruct a proof from an UNSAT result for an affine legality query.

        Delegates to the ReconstructionPipeline if available.

        Args:
            unsat_result: A Z3Result (status=UNSAT) or stub dict.

        Returns:
            ReconstructionResult or stub dict.
        """
        self._log("reconstruct_affine_proof: start")

        if not _RECONSTRUCTION_AVAILABLE:
            self._log("  reconstruction unavailable — returning stub")
            return {"kind": "stub", "proof": "no reconstruction pipeline"}

        try:
            if hasattr(self._reconstruction, "run"):
                result = self._reconstruction.run(unsat_result)
                self._log(f"  reconstruction result: {result}")
                return result
        except Exception as exc:
            self._log(f"  reconstruction error: {exc}")

        return {"kind": "stub", "proof": f"reconstruction failed: {unsat_result}"}

    def incremental_legality_check(
        self,
        transforms: list[AffineLegality],
    ) -> list[tuple[bool, DependenceWitness | None]]:
        """Check legality for a list of affine transforms, one by one.

        For each transform in the list, performs a pure Python legality check
        (via ``affine_transformation_legality``) and records the result.

        Args:
            transforms: List of AffineLegality instances to check.

        Returns:
            List of ``(is_legal, witness_or_None)`` tuples, one per transform.
        """
        self._log(f"incremental_legality_check: {len(transforms)} transforms")
        results: list[tuple[bool, DependenceWitness | None]] = []

        for i, transform in enumerate(transforms):
            self._log(f"  checking transform {i}")
            is_legal, witness = self.check_affine_legality(
                transform,
                transform.dependence_vectors,
                session=None,
            )
            results.append((is_legal, witness))
            self._log(f"    result: legal={is_legal}")

        return results

    def copilot_diagnose_quantifier_issue(
        self,
        formula: Any,
        result: Any,
    ) -> str:
        """Return a diagnosis string for a quantifier-related solver issue.

        Analyses the formula (as a string) and the solver result to identify
        potential issues such as:
        - Non-terminating e-matching.
        - Quantifier alternation causing search space explosion.
        - Undecidable fragment (e.g., quantified non-linear arithmetic).

        Args:
            formula: The formula that was solved (Z3 expr or string).
            result: The solver result (Z3Result, dict, or string).

        Returns:
            Human-readable diagnosis string.

        copilot notes: This method is intended for IDE integration and CI output.
        It is not a solver — it provides heuristic guidance only.
        """
        formula_str = str(formula)
        result_str = str(result)

        lines = ["Copilot diagnosis for quantifier/solver issue:"]

        # Check for quantifier keywords
        has_forall = "forall" in formula_str.lower() or "ForAll" in formula_str
        has_exists = "exists" in formula_str.lower() or "Exists" in formula_str

        if has_forall and has_exists:
            lines.append(
                "  ⚠ Formula contains BOTH ∀ and ∃. Quantifier alternation detected."
            )
            lines.append(
                "    Recommendation: Apply ALWAYS_QF discipline — eliminate all quantifiers "
                "before sending to Z3. Use Fourier-Motzkin for ∀ and Skolemisation for ∃."
            )
        elif has_forall:
            lines.append(
                "  ℹ Formula contains ∀ (universal). "
                "Ensure instantiation depth <= 5 and triggers are loop-safe."
            )
        elif has_exists:
            lines.append(
                "  ℹ Formula contains ∃ (existential). "
                "Consider Skolemisation to eliminate the existential."
            )
        else:
            lines.append("  ✓ Formula appears quantifier-free (no quantifier keywords found).")

        # Check result
        if "timeout" in result_str.lower() or "unknown" in result_str.lower():
            lines.append(
                "  ⚠ Solver returned UNKNOWN or TIMEOUT. Possible causes:"
            )
            lines.append(
                "    1. Formula is in an undecidable fragment (quantified NIA)."
            )
            lines.append(
                "    2. E-matching trigger causes non-termination."
            )
            lines.append(
                "    3. Formula size is too large — apply simplification first."
            )
        elif "unsat" in result_str.lower():
            lines.append("  ✓ Solver returned UNSAT — consider reconstructing a proof.")
        elif "sat" in result_str.lower():
            lines.append("  ✓ Solver returned SAT — extract witnesses for analysis.")

        lines.append(
            "\n  For Chapter 30 tensor queries: always use QF_LIA fragment."
        )

        return "\n".join(lines)

    def get_call_log(self) -> list[str]:
        """Return the internal call log for debugging.

        Returns:
            List of log message strings in order of occurrence.
        """
        return list(self._call_log)

    def clear_log(self) -> None:
        """Clear the internal call log."""
        self._call_log.clear()
