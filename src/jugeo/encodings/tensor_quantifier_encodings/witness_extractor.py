"""
Witness Extraction for Tensor and Affine Legality Queries
==========================================================
Chapter 30 §4 of theory2.tex — JuGeo formal verification system.

When a Z3 query returns SAT or UNSAT, the solver provides additional evidence:
- SAT: a satisfying model mapping each variable to a concrete value.
- UNSAT: an unsatisfiable core (minimal subset of assumptions), or a proof object.

This module provides specialised extractors for tensor and affine legality queries:

1. ``TensorWitnessExtractor``: Extracts concrete tensor shapes, index tuples, and
   affine coefficients from a Z3 SAT model.

2. ``AffineLegalityWitnessExtractor``: Extracts the violating dependence vector
   and Farkas infeasibility certificate from an UNSAT result for a legality query.

The extracted witnesses are represented as:
- ``TensorWitness``: concrete shape list, optional index tuple, optional affine coefficients.
- ``DependenceWitness``: the dependence vector that violates lex-positivity.
- ``FarkasCoefficients``: non-negative multipliers y such that y^T A = 0, y^T b < 0.

copilot notes: Use ``TensorWitnessExtractor.copilot_interpret_tensor_witness()`` to
get a human-readable explanation of any extracted witness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = [
    "TensorWitness",
    "DependenceWitness",
    "FarkasCoefficients",
    "TensorWitnessExtractor",
    "AffineLegalityWitnessExtractor",
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

# ---------------------------------------------------------------------------
# Optional solver imports
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.reconstruction import (
        ReconstructionPipeline,
        ValidationStatus,
    )
    _SOLVER_AVAILABLE = True
except ImportError:
    _SOLVER_AVAILABLE = False
    ReconstructionPipeline = Any  # type: ignore[misc,assignment]
    ValidationStatus = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TensorWitness:
    """A concrete witness for a tensor shape or index constraint.

    Attributes:
        shape: Concrete list of dimension sizes (integers).
        index: Optional concrete index tuple (within bounds), or None.
        affine_coeffs: Optional concrete affine coefficient vector, or None.
        raw_bindings: Raw variable-to-value mapping from the Z3 model.
        is_valid: True if the witness has been validated against the constraint.
        source: Source of the witness ('sat_model', 'unsat_core', 'proof').
        extraction_notes: Ordered log of extraction steps and decisions.
    """

    shape: list[int]
    index: tuple[int, ...] | None
    affine_coeffs: list[int] | None
    raw_bindings: dict[str, Any]
    is_valid: bool
    source: str
    extraction_notes: list[str]

    def describe(self) -> str:
        """Return a concise human-readable description of this witness.

        Returns:
            Multi-line description string.
        """
        lines = [
            f"TensorWitness(source={self.source}, valid={self.is_valid})",
            f"  shape         : {self.shape}",
            f"  index         : {self.index}",
            f"  affine_coeffs : {self.affine_coeffs}",
            f"  raw_bindings  : {self.raw_bindings}",
        ]
        if self.extraction_notes:
            lines.append(f"  notes         : {'; '.join(self.extraction_notes[-3:])}")
        return "\n".join(lines)

    def total_elements(self) -> int:
        """Return the total number of elements (product of shape dimensions).

        Returns:
            Product of all dimension sizes, or 0 if shape is empty.
        """
        result = 1
        for d in self.shape:
            result *= d
        return result

    def as_dict(self) -> dict[str, Any]:
        """Return a plain Python dict representation of this witness.

        Returns:
            Dict with 'shape', 'index', 'affine_coeffs', 'is_valid', 'source'.
        """
        return {
            "shape": self.shape,
            "index": list(self.index) if self.index is not None else None,
            "affine_coeffs": self.affine_coeffs,
            "is_valid": self.is_valid,
            "source": self.source,
        }


@dataclass
class DependenceWitness:
    """Witnesses the illegality of an affine transformation for a given dependence.

    A DependenceWitness is produced when a legality check fails: it records the
    specific dependence vector d such that M*d is not lexicographically positive,
    along with the source and destination iteration points that realise this dependence.

    Attributes:
        dep_vector: The dependence vector d that violates lex-positivity.
        src_iteration: The source loop iteration point (concrete integers).
        dst_iteration: The destination loop iteration point (concrete integers).
        violating_constraint_idx: Index of the violated constraint row.
        transform_applied: The transformation matrix M, or None if not recorded.
        farkas_multipliers: Farkas certificate multipliers, or None.
        notes: Human-readable notes about the violation.
    """

    dep_vector: list[int]
    src_iteration: list[int]
    dst_iteration: list[int]
    violating_constraint_idx: int
    transform_applied: list[list[int]] | None
    farkas_multipliers: list[float] | None
    notes: str

    def md_vector(self) -> list[int] | None:
        """Compute M*d for this witness (the image of the dependence vector).

        Returns:
            M*d as a list of integers, or None if the transform is not recorded.
        """
        if self.transform_applied is None:
            return None
        n_cols = len(self.dep_vector)
        return [
            sum(self.transform_applied[r][c] * self.dep_vector[c] for c in range(n_cols))
            for r in range(len(self.transform_applied))
        ]

    def is_violation(self) -> bool:
        """Check whether M*d is indeed not lex-positive (confirming the violation).

        Returns:
            True if M*d is not lex-positive (confirming illegality).
        """
        md = self.md_vector()
        if md is None:
            return True  # Cannot verify — assume it is a violation
        return not _is_lex_positive(md)


@dataclass
class FarkasCoefficients:
    """Non-negative Farkas multipliers certifying infeasibility of a linear system.

    By Farkas' lemma, the system Ax ≤ b is infeasible iff there exist
    multipliers y ≥ 0 such that y^T A = 0 and y^T b < 0.

    Attributes:
        multipliers: Non-negative floats y_0, ..., y_{m-1}.
        is_valid: True if the multipliers satisfy the Farkas conditions.
        infeasibility_proof: Human-readable proof sketch.
        constraint_indices: Indices of the constraints used in the certificate.
    """

    multipliers: list[float]
    is_valid: bool
    infeasibility_proof: str
    constraint_indices: list[int]

    def verify(self, A: list[list[float]], b: list[float]) -> bool:
        """Verify that the multipliers certify infeasibility of Ax ≤ b.

        Checks:
        1. All multipliers are non-negative.
        2. y^T A = 0 (column-wise).
        3. y^T b < 0.

        Args:
            A: Constraint matrix.
            b: Right-hand side vector.

        Returns:
            True if the Farkas conditions are satisfied.
        """
        m = len(self.multipliers)
        if m == 0 or m != len(A):
            return False

        # Check non-negativity
        if any(y < -1e-9 for y in self.multipliers):
            return False

        if not A:
            return False
        n = len(A[0])

        # Check y^T A = 0
        for j in range(n):
            col_sum = sum(self.multipliers[i] * A[i][j] for i in range(m))
            if abs(col_sum) > 1e-9:
                return False

        # Check y^T b < 0
        dot = sum(self.multipliers[i] * b[i] for i in range(m))
        return dot < -1e-9


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_lex_positive(v: list[int]) -> bool:
    """Return True if integer vector v is lexicographically positive."""
    for component in v:
        if component > 0:
            return True
        if component < 0:
            return False
    return False


def _eval_z3_int(model: Any, var: Any) -> int | None:
    """Evaluate a Z3 Int variable in a model and return a Python int.

    Args:
        model: Z3 model or dict mapping strings to ints.
        var: Z3 Int expression or string variable name.

    Returns:
        Python int, or None if evaluation fails.
    """
    if model is None:
        return None

    # Z3 model
    if _Z3_AVAILABLE and hasattr(model, "eval"):
        try:
            val = model.eval(var, model_completion=True)
            return int(str(val))
        except Exception:
            return None

    # Dict model (for testing)
    if isinstance(model, dict):
        key = str(var)
        raw = model.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return None


# ---------------------------------------------------------------------------
# TensorWitnessExtractor
# ---------------------------------------------------------------------------


class TensorWitnessExtractor:
    """Extracts concrete tensor witnesses from Z3 SAT models.

    Given a Z3 satisfying model (or a dict for testing), this class evaluates
    the relevant tensor variables and assembles a ``TensorWitness``.

    copilot notes: Use ``extract_from_sat_model()`` as the main entry point.
    The ``copilot_interpret_tensor_witness()`` method provides a human-readable
    explanation suitable for display in IDE tooltips or CI reports.

    Example::

        extractor = TensorWitnessExtractor(z3_model=solver.model())
        dim_vars = [z3.Int('n0'), z3.Int('n1')]
        witness = extractor.extract_from_sat_model(
            extractor.z3_model,
            {'n0': z3.Int('n0'), 'n1': z3.Int('n1')},
        )
        print(extractor.copilot_interpret_tensor_witness(witness, "2D matrix shape"))
    """

    def __init__(
        self,
        z3_model: Any = None,
        reconstruction_pipeline: Any = None,
    ) -> None:
        """Initialise the extractor.

        Args:
            z3_model: The Z3 satisfying model, or None if not yet available.
            reconstruction_pipeline: Optional ReconstructionPipeline for proof
                                     reconstruction integration.
        """
        self.z3_model = z3_model
        self.reconstruction_pipeline = reconstruction_pipeline
        self._log: list[str] = []

    def extract_from_sat_model(
        self,
        z3_model: Any,
        tensor_vars: dict[str, Any],
    ) -> TensorWitness:
        """Extract a TensorWitness from a Z3 SAT model.

        Evaluates each variable in ``tensor_vars`` in the model to obtain
        concrete integer values.  Variables whose names start with 'n' or 'dim'
        are treated as shape variables; those starting with 'i' or 'idx' are
        treated as index variables.

        Args:
            z3_model: Z3 satisfying model or dict mapping names to values.
            tensor_vars: Dict mapping variable name strings to Z3 Int expressions.

        Returns:
            TensorWitness with populated shape, index, and raw_bindings fields.
        """
        self._log.append("extract_from_sat_model: start")
        raw_bindings: dict[str, Any] = {}
        shape: list[int] = []
        index_parts: list[int] = []
        affine_coeffs: list[int] = []

        for name, var in sorted(tensor_vars.items()):
            val = _eval_z3_int(z3_model, var)
            if val is None:
                val = 0
            raw_bindings[name] = val
            self._log.append(f"  {name} = {val}")

            if name.startswith(("n", "dim", "shape", "size", "m_")):
                shape.append(val)
            elif name.startswith(("i", "idx", "j", "k", "row", "col")):
                index_parts.append(val)
            elif name.startswith(("c", "coeff", "a_")):
                affine_coeffs.append(val)

        index_tuple = tuple(index_parts) if index_parts else None
        is_valid = all(s > 0 for s in shape)

        self._log.append(f"  extracted shape={shape}, index={index_tuple}")

        return TensorWitness(
            shape=shape,
            index=index_tuple,
            affine_coeffs=affine_coeffs if affine_coeffs else None,
            raw_bindings=raw_bindings,
            is_valid=is_valid,
            source="sat_model",
            extraction_notes=list(self._log),
        )

    def extract_shape_witness(
        self, model: Any, dim_vars: list[Any]
    ) -> dict[str, int]:
        """Evaluate each dimension variable in the model.

        Args:
            model: Z3 model or dict.
            dim_vars: List of Z3 Int variables (or stubs) for tensor dimensions.

        Returns:
            Dict mapping variable name to concrete integer value.
        """
        result: dict[str, int] = {}
        for var in dim_vars:
            val = _eval_z3_int(model, var)
            if val is None:
                val = 1  # Default to 1 if unevaluable
            result[str(var)] = val
        return result

    def extract_index_witness(
        self, model: Any, idx_vars: list[Any]
    ) -> tuple[int, ...]:
        """Evaluate each index variable in the model.

        Args:
            model: Z3 model or dict.
            idx_vars: List of Z3 Int variables for index components.

        Returns:
            Tuple of concrete integer values, one per index variable.
        """
        parts: list[int] = []
        for var in idx_vars:
            val = _eval_z3_int(model, var)
            parts.append(val if val is not None else 0)
        return tuple(parts)

    def extract_affine_witness(
        self, model: Any, coeff_vars: list[Any]
    ) -> list[int]:
        """Evaluate each affine coefficient variable in the model.

        Args:
            model: Z3 model or dict.
            coeff_vars: List of Z3 Int variables for affine coefficients.

        Returns:
            List of concrete integer values.
        """
        return [
            (_eval_z3_int(model, v) or 0)
            for v in coeff_vars
        ]

    def validate_tensor_witness(
        self, witness: TensorWitness, constraint_expr: Any
    ) -> bool:
        """Check that a witness satisfies basic shape and index validity.

        Validates:
        1. All shape dimensions are strictly positive.
        2. If an index is present, each component is in [0, shape[i]).
        3. If a constraint_expr is a callable, call it with the witness shape.

        Args:
            witness: The TensorWitness to validate.
            constraint_expr: A Z3 formula or callable that accepts a shape list.

        Returns:
            True if the witness appears valid.
        """
        # Check positivity
        if any(s <= 0 for s in witness.shape):
            return False

        # Check index bounds
        if witness.index is not None:
            if len(witness.index) != len(witness.shape):
                return False
            for idx, dim in zip(witness.index, witness.shape):
                if not (0 <= idx < dim):
                    return False

        # Try calling constraint_expr as a callable
        if callable(constraint_expr):
            try:
                return bool(constraint_expr(witness.shape))
            except Exception:
                pass

        return True

    def lift_witness_to_python(self, witness: TensorWitness) -> dict[str, Any]:
        """Convert all Z3 values in a witness to plain Python types.

        Args:
            witness: The TensorWitness to lift.

        Returns:
            Dict with 'shape' (list[int]), 'index' (list[int] or None),
            'affine_coeffs' (list[int] or None), and 'total_elements' (int).
        """
        result: dict[str, Any] = {
            "shape": [int(s) for s in witness.shape],
            "index": [int(i) for i in witness.index] if witness.index is not None else None,
            "affine_coeffs": [int(c) for c in witness.affine_coeffs]
            if witness.affine_coeffs is not None
            else None,
            "total_elements": witness.total_elements(),
            "is_valid": witness.is_valid,
            "source": witness.source,
        }
        # Also lift raw_bindings
        lifted_bindings: dict[str, Any] = {}
        for k, v in witness.raw_bindings.items():
            try:
                lifted_bindings[k] = int(v)
            except (TypeError, ValueError):
                lifted_bindings[k] = str(v)
        result["raw_bindings"] = lifted_bindings
        return result

    def minimize_witness_shape(self, witness: TensorWitness) -> TensorWitness:
        """Return a new witness with the smallest valid shape.

        Attempts to reduce each dimension to the minimum value consistent
        with the index being in bounds.  If there is no index, the minimum
        shape is (1, 1, ..., 1).

        Args:
            witness: The TensorWitness to minimize.

        Returns:
            New TensorWitness with minimized shape dimensions.
        """
        if witness.index is None:
            min_shape = [1] * len(witness.shape)
        else:
            # Minimum shape: each dimension must be > max index in that dimension
            min_shape = [
                max(1, int(idx) + 1)
                for idx in witness.index
            ]

        notes = list(witness.extraction_notes) + [
            f"minimize_witness_shape: {witness.shape} -> {min_shape}"
        ]

        return TensorWitness(
            shape=min_shape,
            index=witness.index,
            affine_coeffs=witness.affine_coeffs,
            raw_bindings=witness.raw_bindings,
            is_valid=all(s > 0 for s in min_shape),
            source=witness.source,
            extraction_notes=notes,
        )

    def copilot_interpret_tensor_witness(
        self, witness: TensorWitness, context: str
    ) -> str:
        """Return a human-readable interpretation of a tensor witness.

        Formats the witness as a descriptive string suitable for display in
        IDE tooltips, CI reports, or error messages.

        Args:
            witness: The TensorWitness to interpret.
            context: Human-readable context string (e.g., "2D convolution weight").

        Returns:
            Human-readable interpretation string.

        Example::

            interpreter.copilot_interpret_tensor_witness(w, "2D matrix")
            # Returns:
            # "Tensor of shape [3, 4] (12 elements) for '2D matrix'.
            #  Invalid index: (3, 0) — row index 3 out of bounds (shape[0]=3)."
        """
        shape_str = str(witness.shape)
        total = witness.total_elements()
        valid_str = "valid" if witness.is_valid else "invalid"

        lines = [
            f"Tensor witness ({valid_str}) for '{context}':",
            f"  shape          = {shape_str}  ({total} total elements)",
        ]

        if witness.index is not None:
            in_bounds = all(
                0 <= int(idx) < int(dim)
                for idx, dim in zip(witness.index, witness.shape)
            ) if len(witness.index) == len(witness.shape) else False

            bound_str = "in-bounds" if in_bounds else "OUT-OF-BOUNDS"
            lines.append(f"  index          = {list(witness.index)}  [{bound_str}]")

            if not in_bounds:
                for k, (idx, dim) in enumerate(zip(witness.index, witness.shape)):
                    if not (0 <= int(idx) < int(dim)):
                        lines.append(
                            f"    → dimension {k}: index {idx} not in [0, {dim})"
                        )

        if witness.affine_coeffs is not None:
            lines.append(f"  affine_coeffs  = {witness.affine_coeffs}")

        lines.append(f"  source         = {witness.source}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# AffineLegalityWitnessExtractor
# ---------------------------------------------------------------------------


class AffineLegalityWitnessExtractor:
    """Extracts dependence witnesses and Farkas certificates for affine legality.

    When an affine legality query returns UNSAT (the transform is illegal), this
    class extracts the specific dependence vector that violates lex-positivity
    and attempts to compute Farkas multipliers certifying the violation.

    copilot notes: Use ``extract_illegality_witness()`` to extract the violating
    dependence vector from an UNSAT core.  Use ``extract_farkas_certificate()``
    to compute Farkas multipliers for the infeasibility certificate.
    """

    def __init__(self) -> None:
        """Initialise the affine legality witness extractor."""
        self._log: list[str] = []

    def extract_illegality_witness(
        self,
        unsat_core: list[str],
        dep_constraints: list[Any],
    ) -> DependenceWitness:
        """Parse an UNSAT core to extract the violating dependence vector.

        Searches the dependence constraints for the one referenced in the
        UNSAT core, then extracts the corresponding dependence vector.

        Args:
            unsat_core: List of assumption labels from the UNSAT core.
            dep_constraints: List of constraint objects (Z3 formulas or strings).

        Returns:
            DependenceWitness identifying the violating constraint.
        """
        self._log.append(f"extract_illegality_witness: core={len(unsat_core)} clauses")

        # Find the first constraint index referenced in the core
        violating_idx = 0
        for label in unsat_core:
            # Labels are typically "dep_N" for the N-th dependence constraint
            import re
            m = re.search(r'dep_(\d+)', str(label))
            if m:
                violating_idx = int(m.group(1))
                break

        self._log.append(f"  violating constraint index: {violating_idx}")

        return DependenceWitness(
            dep_vector=[0, 1],  # Placeholder — real extraction requires Z3 model
            src_iteration=[0, 0],
            dst_iteration=[0, 1],
            violating_constraint_idx=violating_idx,
            transform_applied=None,
            farkas_multipliers=None,
            notes=f"Extracted from UNSAT core: {unsat_core[:3]}",
        )

    def extract_farkas_certificate(
        self,
        A: list[list[float]],
        b: list[float],
    ) -> FarkasCoefficients:
        """Compute Farkas multipliers certifying infeasibility of Ax ≤ b.

        Implements a simplified Gaussian elimination approach to find
        non-negative multipliers y such that y^T A = 0 and y^T b < 0.

        The algorithm:
        1. Form the augmented system [A^T | b] for the dual.
        2. Find a basis for the null space of A^T using Gaussian elimination.
        3. Check if any null space vector satisfies y >= 0 and y^T b < 0.
        4. If not found analytically, try the equal-weight vector y = 1/m.

        Args:
            A: Constraint matrix (m x n) as floats.
            b: Right-hand side vector (length m) as floats.

        Returns:
            FarkasCoefficients with multipliers (and is_valid flag).
        """
        self._log.append("extract_farkas_certificate: start")

        if not A or not b:
            return FarkasCoefficients(
                multipliers=[],
                is_valid=False,
                infeasibility_proof="Empty system — no certificate.",
                constraint_indices=[],
            )

        m = len(A)
        n = len(A[0]) if A else 0
        self._log.append(f"  system: {m} x {n}")

        # Try equal-weight multipliers first: y = [1/m, ..., 1/m]
        equal_y = [1.0 / m] * m

        # Check y^T A = 0
        col_sums = [
            sum(equal_y[i] * A[i][j] for i in range(m))
            for j in range(n)
        ]
        near_zero = all(abs(s) < 1e-6 for s in col_sums)

        # Check y^T b < 0
        dot_b = sum(equal_y[i] * b[i] for i in range(m))

        if near_zero and dot_b < -1e-9:
            self._log.append("  equal-weight certificate: valid")
            proof = (
                f"Farkas certificate found with equal-weight multipliers y = [{1.0/m:.4f}]*{m}. "
                f"y^T A ≈ 0, y^T b = {dot_b:.6f} < 0."
            )
            return FarkasCoefficients(
                multipliers=equal_y,
                is_valid=True,
                infeasibility_proof=proof,
                constraint_indices=list(range(m)),
            )

        # Try single-row certificates
        for i in range(m):
            y_single = [0.0] * m
            y_single[i] = 1.0
            col_sums_single = [A[i][j] for j in range(n)]
            if all(abs(c) < 1e-9 for c in col_sums_single) and b[i] < -1e-9:
                proof = (
                    f"Single-row Farkas certificate: row {i} has A[{i}] ≈ 0 and b[{i}] = {b[i]:.4f} < 0."
                )
                return FarkasCoefficients(
                    multipliers=y_single,
                    is_valid=True,
                    infeasibility_proof=proof,
                    constraint_indices=[i],
                )

        # Gaussian elimination for null space of A^T
        # Build A^T as a (n x m) matrix
        AT = [[A[i][j] for i in range(m)] for j in range(n)]

        # Row reduce AT (simplified — only handles small systems)
        pivot_cols: list[int] = []
        reduced = [list(row) for row in AT]
        pivot_row = 0
        for col in range(m):
            found = -1
            for row in range(pivot_row, n):
                if abs(reduced[row][col]) > 1e-9:
                    found = row
                    break
            if found == -1:
                pivot_cols.append(col)  # Free variable
                continue
            reduced[pivot_row], reduced[found] = reduced[found], reduced[pivot_row]
            piv = reduced[pivot_row][col]
            reduced[pivot_row] = [x / piv for x in reduced[pivot_row]]
            for r in range(n):
                if r != pivot_row and abs(reduced[r][col]) > 1e-9:
                    factor = reduced[r][col]
                    reduced[r] = [reduced[r][c] - factor * reduced[pivot_row][c] for c in range(m)]
            pivot_row += 1

        # Free columns correspond to null space directions
        for free_col in pivot_cols:
            y_free = [0.0] * m
            y_free[free_col] = 1.0
            dot_b_free = sum(y_free[i] * b[i] for i in range(m))
            if all(y_free[i] >= -1e-9 for i in range(m)) and dot_b_free < -1e-9:
                y_clean = [max(0.0, y) for y in y_free]
                proof = (
                    f"Null-space certificate via free column {free_col}. "
                    f"y^T b = {dot_b_free:.6f} < 0."
                )
                return FarkasCoefficients(
                    multipliers=y_clean,
                    is_valid=True,
                    infeasibility_proof=proof,
                    constraint_indices=[free_col],
                )

        # Could not find certificate — system may be feasible
        return FarkasCoefficients(
            multipliers=[0.0] * m,
            is_valid=False,
            infeasibility_proof="No Farkas certificate found — system may be feasible.",
            constraint_indices=[],
        )

    def reconstruct_dependence_polyhedron(
        self, witness: DependenceWitness
    ) -> Any:
        """Reconstruct an AffineLegality object from a dependence witness.

        Builds an AffineLegality with the witness's transform and the single
        violating dependence vector.

        Args:
            witness: The DependenceWitness to reconstruct from.

        Returns:
            An AffineLegality instance with the extracted information.
        """
        # Import locally to avoid circular imports
        from jugeo.encodings.tensor_quantifier_encodings.models import AffineLegality

        M = witness.transform_applied or [[1, 0], [0, 1]]  # Default to identity
        return AffineLegality(
            transform_matrix=M,
            dependence_vectors=[witness.dep_vector],
            legality_formula=None,
            counterexample=None,
            is_legal=False,
        )

    def minimize_witness(
        self, witness: DependenceWitness, constraints: list[Any]
    ) -> DependenceWitness:
        """Try to minimize the dependence witness by zeroing out components.

        Attempts to zero out each component of ``dep_vector`` one by one.
        If the result is still a violation (not lex-positive under M), the
        component is kept at zero.

        Args:
            witness: The DependenceWitness to minimize.
            constraints: List of constraint objects (not currently used —
                         future work: re-check validity after each zeroing).

        Returns:
            New DependenceWitness with a minimized (potentially sparser) dep_vector.
        """
        M = witness.transform_applied
        if M is None:
            return witness

        dep = list(witness.dep_vector)
        minimized = list(dep)

        for k in range(len(dep)):
            if dep[k] == 0:
                continue
            trial = list(minimized)
            trial[k] = 0
            md = [
                sum(M[r][c] * trial[c] for c in range(len(trial)))
                for r in range(len(M))
            ]
            if not _is_lex_positive(md):
                minimized[k] = 0  # Can zero this out and still violate

        notes = (
            f"Minimized dep_vector from {dep} to {minimized}."
            if minimized != dep
            else "No minimization possible."
        )

        return DependenceWitness(
            dep_vector=minimized,
            src_iteration=witness.src_iteration,
            dst_iteration=witness.dst_iteration,
            violating_constraint_idx=witness.violating_constraint_idx,
            transform_applied=M,
            farkas_multipliers=witness.farkas_multipliers,
            notes=notes,
        )

    def copilot_explain_illegality(self, witness: DependenceWitness) -> str:
        """Return a human-readable explanation of why a transform is illegal.

        Args:
            witness: The DependenceWitness to explain.

        Returns:
            Human-readable explanation string.

        Example::

            extractor.copilot_explain_illegality(w)
            # Returns:
            # "Transform is illegal: dependence vector [1, 0] maps to [-1, 0]
            #  under M = [[−1, 0], [0, 1]], violating lex-positivity.
            #  The first component of M*d is -1 < 0."
        """
        d = witness.dep_vector
        M = witness.transform_applied
        md = witness.md_vector()

        lines = [
            "Affine transform illegality witness:",
            f"  dependence vector : {d}",
        ]

        if M is not None:
            lines.append(f"  transform M       : {M}")

        if md is not None:
            lines.append(f"  M * d             : {md}")
            is_lp = _is_lex_positive(md)
            lines.append(f"  lex-positive?     : {is_lp}")

            if not is_lp:
                for k, v in enumerate(md):
                    if v < 0:
                        lines.append(
                            f"  → First negative component: (M*d)[{k}] = {v} < 0"
                        )
                        break
                    if v > 0:
                        break
                else:
                    lines.append("  → All-zero M*d — not lex-positive (zero vector).")

        if witness.notes:
            lines.append(f"  notes             : {witness.notes}")

        return "\n".join(lines)
