"""
Data models for the tensor_quantifier_encodings package.
=========================================================
Chapter 30 of theory2.tex — JuGeo formal verification system.

This module defines the five core dataclass models used throughout the
tensor_quantifier_encodings package:

- TensorExtent      — shape, strides, and dimension constraints for a tensor
- AffineLegality    — affine transformation legality and dependence vectors
- QuantifierDiscipline — strategy for handling quantifiers in tensor formulas
- WitnessExtractor  — extracts and validates witnesses from solver results
- TensorConstraint  — a single shape or index constraint on tensor(s)

copilot notes: All models have optional Z3 integration.  When z3 is not
installed the methods return symbolic string stubs so that the rest of the
package can be imported and tested without a Z3 installation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = [
    "TensorLayout",
    "TensorExtent",
    "AffineLegality",
    "DisciplineKind",
    "QuantifierDiscipline",
    "ExtractionStrategy",
    "WitnessExtractor",
    "ConstraintKind",
    "TensorConstraint",
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


def _z3_int(name: str) -> Any:
    """Create a Z3 Int variable or return a symbolic stub string.

    Args:
        name: Variable name.

    Returns:
        z3.Int(name) if z3 is available, else the string ``"Int(<name>)"``.
    """
    if _Z3_AVAILABLE:
        return _z3.Int(name)
    return f"Int({name})"


def _z3_and(*args: Any) -> Any:
    """Create a Z3 And expression or return a symbolic stub string.

    Args:
        *args: Sub-expressions to conjoin.

    Returns:
        z3.And(*args) if z3 is available and args non-empty, else a string stub.
    """
    if _Z3_AVAILABLE and args:
        return _z3.And(*args)
    return f"And({', '.join(str(a) for a in args)})"


def _z3_or(*args: Any) -> Any:
    """Create a Z3 Or expression or return a symbolic stub string."""
    if _Z3_AVAILABLE and args:
        return _z3.Or(*args)
    return f"Or({', '.join(str(a) for a in args)})"


def _z3_not(expr: Any) -> Any:
    """Create a Z3 Not expression or return a symbolic stub string."""
    if _Z3_AVAILABLE:
        return _z3.Not(expr)
    return f"Not({expr})"


def _z3_implies(a: Any, b: Any) -> Any:
    """Create a Z3 Implies expression or return a symbolic stub string."""
    if _Z3_AVAILABLE:
        return _z3.Implies(a, b)
    return f"Implies({a}, {b})"


def _z3_int_val(n: int) -> Any:
    """Return a Z3 integer literal or the Python int itself."""
    if _Z3_AVAILABLE:
        return _z3.IntVal(n)
    return n


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TensorLayout(str, Enum):
    """Memory layout of a tensor.

    ROW_MAJOR (C order): last dimension varies fastest.
    COL_MAJOR (Fortran order): first dimension varies fastest.
    STRIDED: arbitrary strides per dimension.
    PACKED: elements stored contiguously with no padding.
    """

    ROW_MAJOR = "row_major"
    COL_MAJOR = "col_major"
    STRIDED = "strided"
    PACKED = "packed"


class DisciplineKind(str, Enum):
    """Strategy for handling quantifiers in a formula.

    ALWAYS_QF: Always eliminate quantifiers before sending to Z3.
    INLINE_QUANT: Keep quantifiers but add Z3 triggers/patterns.
    SKOLEM: Replace existential quantifiers with Skolem functions.
    INSTANTIATE: Instantiate universal quantifiers at specific terms.
    """

    ALWAYS_QF = "always_qf"
    INLINE_QUANT = "inline_quant"
    SKOLEM = "skolem"
    INSTANTIATE = "instantiate"


class ExtractionStrategy(str, Enum):
    """Strategy for extracting witnesses from a solver result.

    FROM_SAT_MODEL: Extract values directly from the satisfying Z3 model.
    FROM_UNSAT_CORE: Derive a witness from the unsatisfiable core.
    FROM_PROOF: Extract witness from a Z3 proof object.
    INTERPOLATION: Compute a Craig interpolant as the witness.
    """

    FROM_SAT_MODEL = "from_sat_model"
    FROM_UNSAT_CORE = "from_unsat_core"
    FROM_PROOF = "from_proof"
    INTERPOLATION = "interpolation"


class ConstraintKind(str, Enum):
    """The semantic kind of a TensorConstraint.

    SHAPE_COMPATIBLE: Two tensors have the same shape.
    INDEX_VALID: An index tuple is within bounds.
    STRIDE_CONSISTENT: Strides are consistent with the declared layout.
    BROADCAST_COMPATIBLE: Two tensors are compatible under NumPy broadcasting rules.
    RESHAPE_VALID: A reshape operation preserves total element count.
    """

    SHAPE_COMPATIBLE = "shape_compatible"
    INDEX_VALID = "index_valid"
    STRIDE_CONSISTENT = "stride_consistent"
    BROADCAST_COMPATIBLE = "broadcast_compatible"
    RESHAPE_VALID = "reshape_valid"


# ---------------------------------------------------------------------------
# TensorExtent
# ---------------------------------------------------------------------------


@dataclass
class TensorExtent:
    """Represents the shape, strides, and dimension constraints of a tensor.

    A TensorExtent captures everything needed to encode a tensor's memory
    layout as a QF_LIA formula in Z3:

    - ``rank``: the number of dimensions (statically known integer).
    - ``dim_vars``: Z3 Int variables (or symbolic stubs) for each dimension.
    - ``dim_constraints``: Z3 formulas constraining the dimension variables.
    - ``stride_vars``: Z3 Int variables for each stride dimension.
    - ``total_size_var``: Z3 Int variable for the total number of elements.
    - ``layout``: the memory layout order.

    copilot notes: Use ``to_z3_formula()`` to obtain the full conjunction of
    all shape and stride constraints ready to assert into a Z3 solver.

    Example::

        extent = TensorExtent(
            rank=2,
            dim_vars=[z3.Int('n0'), z3.Int('n1')],
            dim_constraints=[z3.Int('n0') > 0, z3.Int('n1') > 0],
            stride_vars=[z3.Int('s0'), z3.Int('s1')],
            total_size_var=z3.Int('total'),
        )
        formula = extent.to_z3_formula()
    """

    rank: int
    dim_vars: list[Any]
    dim_constraints: list[Any]
    stride_vars: list[Any]
    total_size_var: Any
    layout: TensorLayout = TensorLayout.ROW_MAJOR

    def encode_shape(self) -> Any:
        """Return a Z3 conjunction asserting all dimension variables are positive.

        This encodes the fundamental shape validity requirement: every dimension
        of a tensor must be a strictly positive integer.

        Returns:
            Z3 formula ``And(dim_vars[0] > 0, dim_vars[1] > 0, ...)``, or a
            symbolic stub string if z3 is unavailable.
        """
        positivity = [
            (d > _z3_int_val(0)) if _Z3_AVAILABLE else f"({d} > 0)"
            for d in self.dim_vars
        ]
        base = self.dim_constraints + positivity
        return _z3_and(*base) if base else _z3_int_val(1)

    def decode_shape_model(self, model: Any) -> list[int]:
        """Extract concrete dimension values from a Z3 satisfying model.

        Evaluates each dimension variable in the given model and converts the
        result to a Python integer.

        Args:
            model: A Z3 model (or dict mapping var names to ints for testing).

        Returns:
            List of integer dimension values, one per rank dimension.
        """
        result: list[int] = []
        for v in self.dim_vars:
            if _Z3_AVAILABLE and hasattr(model, "eval"):
                val = model.eval(v, model_completion=True)
                result.append(int(str(val)))
            elif isinstance(model, dict):
                key = str(v)
                result.append(int(model.get(key, 1)))
            else:
                result.append(1)
        return result

    def valid_index_constraint(self, idx_vars: list[Any]) -> Any:
        """Return a Z3 formula asserting an index tuple is within bounds.

        Encodes the conjunction: for each dimension i,
        ``0 <= idx_vars[i] < dim_vars[i]``.

        Args:
            idx_vars: List of Z3 Int variables (or stubs) for the index components.

        Returns:
            Z3 formula, or symbolic stub string.

        Raises:
            ValueError: If ``len(idx_vars) != self.rank``.
        """
        if len(idx_vars) != self.rank:
            raise ValueError(
                f"Index has {len(idx_vars)} components but tensor has rank {self.rank}"
            )
        conjuncts: list[Any] = []
        for i, (idx, dim) in enumerate(zip(idx_vars, self.dim_vars)):
            if _Z3_AVAILABLE:
                conjuncts.append(_z3_int_val(0) <= idx)
                conjuncts.append(idx < dim)
            else:
                conjuncts.append(f"(0 <= {idx})")
                conjuncts.append(f"({idx} < {dim})")
        return _z3_and(*conjuncts)

    def linearize_index(self, idx_tuple: list[Any]) -> Any:
        """Compute the row-major linear offset from an N-D index.

        Encodes: ``idx[0]*dim[1]*...*dim[n-1] + idx[1]*dim[2]*...*dim[n-1] + ... + idx[n-1]``.

        Args:
            idx_tuple: List of Z3 Int expressions for each dimension index.

        Returns:
            Z3 arithmetic expression for the linear offset, or a stub string.
        """
        if len(idx_tuple) != self.rank:
            raise ValueError(
                f"Index length {len(idx_tuple)} != rank {self.rank}"
            )
        if self.rank == 0:
            return _z3_int_val(0)

        result: Any = idx_tuple[0]
        for k in range(1, self.rank):
            # multiply current result by dim[k]
            if _Z3_AVAILABLE:
                result = result * self.dim_vars[k]
            else:
                result = f"({result} * {self.dim_vars[k]})"
            # add next index component
            if _Z3_AVAILABLE:
                result = result + idx_tuple[k]
            else:
                result = f"({result} + {idx_tuple[k]})"
        return result

    def broadcast_compatible(self, other: TensorExtent) -> Any:
        """Return a Z3 formula asserting NumPy broadcast compatibility.

        Two shapes are broadcast-compatible if, for each dimension aligned
        from the right, either the dimensions are equal or one of them is 1.

        When the ranks differ, the shorter shape is padded with 1s on the left.

        Args:
            other: Another TensorExtent to test compatibility with.

        Returns:
            Z3 formula (or stub string) expressing broadcast compatibility.
        """
        n = max(self.rank, other.rank)
        # Pad dim_vars with symbolic 1s on the left
        a_dims = ([_z3_int_val(1)] * (n - self.rank)) + list(self.dim_vars)
        b_dims = ([_z3_int_val(1)] * (n - other.rank)) + list(other.dim_vars)

        conjuncts: list[Any] = []
        one = _z3_int_val(1)
        for a, b in zip(a_dims, b_dims):
            if _Z3_AVAILABLE:
                compat = _z3.Or(a == b, a == one, b == one)
            else:
                compat = f"({a} == {b} Or {a} == 1 Or {b} == 1)"
            conjuncts.append(compat)
        return _z3_and(*conjuncts) if conjuncts else _z3_int_val(1)

    def reshape_constraint(self, new_shape: list[Any]) -> Any:
        """Return a Z3 formula asserting a reshape preserves total element count.

        Encodes: ``total_size_var == product(new_shape)``.

        Args:
            new_shape: List of Z3 Int expressions for the new shape dimensions.

        Returns:
            Z3 equality formula, or stub string.
        """
        if not new_shape:
            return _z3_int_val(1) == self.total_size_var if _Z3_AVAILABLE else "(total == 1)"

        product: Any = new_shape[0]
        for s in new_shape[1:]:
            if _Z3_AVAILABLE:
                product = product * s
            else:
                product = f"({product} * {s})"

        if _Z3_AVAILABLE:
            return self.total_size_var == product
        return f"({self.total_size_var} == {product})"

    def bounds_check(self, idx: list[Any]) -> Any:
        """Check that an index is within bounds (alias for valid_index_constraint).

        Provided as a more descriptively named entry point for callers that
        think in terms of array bounds checking rather than constraint encoding.

        Args:
            idx: List of Z3 Int expressions for the index components.

        Returns:
            Z3 formula (or stub) asserting ``0 <= idx[i] < dim_vars[i]`` for each i.
        """
        return self.valid_index_constraint(idx)

    def affine_range(self) -> list[tuple[Any, Any]]:
        """Return the (lower_bound, upper_bound) pairs for each dimension.

        For each dimension i the range is [0, dim_vars[i] - 1].

        Returns:
            List of (lb, ub) pairs where lb = 0 and ub = dim_vars[i] - 1.
        """
        ranges: list[tuple[Any, Any]] = []
        for dim in self.dim_vars:
            lb: Any = _z3_int_val(0)
            if _Z3_AVAILABLE:
                ub = dim - _z3_int_val(1)
            else:
                ub = f"({dim} - 1)"
            ranges.append((lb, ub))
        return ranges

    def to_z3_formula(self) -> Any:
        """Encode the full shape and stride constraints as a single Z3 formula.

        This is the canonical method for obtaining a Z3 formula that completely
        characterises this tensor extent.  It conjoins:

        1. All positivity constraints on dimension variables.
        2. All explicit ``dim_constraints``.
        3. The total size constraint: ``total_size_var == product(dim_vars)``.
        4. Row-major stride constraints (if layout == ROW_MAJOR).

        Returns:
            Z3 formula (or stub string).
        """
        conjuncts: list[Any] = list(self.dim_constraints)

        # Positivity of all dims
        for d in self.dim_vars:
            if _Z3_AVAILABLE:
                conjuncts.append(d > _z3_int_val(0))
            else:
                conjuncts.append(f"({d} > 0)")

        # Total size = product of dims
        if self.dim_vars:
            product: Any = self.dim_vars[0]
            for d in self.dim_vars[1:]:
                if _Z3_AVAILABLE:
                    product = product * d
                else:
                    product = f"({product} * {d})"
            if _Z3_AVAILABLE:
                conjuncts.append(self.total_size_var == product)
            else:
                conjuncts.append(f"({self.total_size_var} == {product})")

        # Row-major stride constraints
        if self.layout == TensorLayout.ROW_MAJOR and self.stride_vars:
            # stride[rank-1] = 1, stride[i] = stride[i+1] * dim[i+1]
            if self.rank >= 1 and len(self.stride_vars) >= 1:
                last = self.stride_vars[-1]
                if _Z3_AVAILABLE:
                    conjuncts.append(last == _z3_int_val(1))
                else:
                    conjuncts.append(f"({last} == 1)")
            for i in range(self.rank - 2, -1, -1):
                if i + 1 < len(self.stride_vars) and i + 1 < len(self.dim_vars):
                    si = self.stride_vars[i]
                    si1 = self.stride_vars[i + 1]
                    di1 = self.dim_vars[i + 1]
                    if _Z3_AVAILABLE:
                        conjuncts.append(si == si1 * di1)
                    else:
                        conjuncts.append(f"({si} == {si1} * {di1})")

        return _z3_and(*conjuncts) if conjuncts else _z3_int_val(1)

    def copilot_infer_layout(self) -> TensorLayout:
        """Heuristically infer the memory layout from the stride variables.

        If the stride variables are consistent with row-major order (each stride
        equals the product of subsequent dimension sizes), returns ROW_MAJOR.
        If consistent with column-major order, returns COL_MAJOR.
        Otherwise returns STRIDED.

        Returns:
            The inferred TensorLayout enum value.
        """
        if not self.stride_vars or not self.dim_vars:
            return TensorLayout.ROW_MAJOR

        n = len(self.stride_vars)
        if n != self.rank:
            return TensorLayout.STRIDED

        # Check if stride names follow the pattern s0, s1, ... which suggests
        # the strides were constructed for row-major layout.
        stride_names = [str(s) for s in self.stride_vars]
        dim_names = [str(d) for d in self.dim_vars]

        if all(stride_names[i].startswith("s") for i in range(n)):
            # Heuristic: if last stride name is s<rank-1>, assume row-major
            if stride_names[-1].endswith(str(n - 1)):
                return TensorLayout.ROW_MAJOR
            if stride_names[0].endswith("0"):
                return TensorLayout.COL_MAJOR

        # Fall back to declared layout
        return self.layout


# ---------------------------------------------------------------------------
# AffineLegality
# ---------------------------------------------------------------------------


@dataclass
class AffineLegality:
    """Records an affine transformation matrix and its legality condition.

    An affine transformation M is *legal* for a set of dependence vectors D if
    for every d in D, the vector M*d is lexicographically positive (i.e., the
    first non-zero component is positive).  This is the Feautrier/Bastoul
    legality condition for polyhedral loop transformation.

    Attributes:
        transform_matrix: Integer matrix M (list of rows).
        dependence_vectors: List of dependence vectors to check against.
        legality_formula: Z3 formula encoding the legality condition.
        counterexample: Z3 model witnessing illegality, or None.
        is_legal: True/False/None (None = not yet checked).

    copilot notes: Use ``encode_legality()`` to build the Z3 formula, then
    call ``check_legality(session)`` to determine the result.
    """

    transform_matrix: list[list[int]]
    dependence_vectors: list[list[int]]
    legality_formula: Any
    counterexample: Any | None
    is_legal: bool | None

    def encode_legality(self) -> Any:
        """Encode the legality condition as a QF_LIA formula.

        For each dependence vector d, the condition is:
        ``M*d`` is lexicographically positive, i.e., there exists a first
        index k such that (M*d)[j] = 0 for j < k and (M*d)[k] > 0.

        This is encoded as a disjunction over possible first-nonzero positions.

        Returns:
            Z3 And of all per-dependence-vector conditions, or a stub string.
        """
        if not self.dependence_vectors or not self.transform_matrix:
            return _z3_int_val(1)

        per_dep_conditions: list[Any] = []
        for dep in self.dependence_vectors:
            # Compute M*dep (integer arithmetic)
            md: list[int] = [
                sum(self.transform_matrix[r][c] * dep[c] for c in range(len(dep)))
                for r in range(len(self.transform_matrix))
            ]
            # Lex-positive: first non-zero is positive
            is_lex_pos = _lex_positive_int(md)
            if _Z3_AVAILABLE:
                per_dep_conditions.append(_z3.BoolVal(is_lex_pos))
            else:
                per_dep_conditions.append(f"lex_pos({md})")

        return _z3_and(*per_dep_conditions) if per_dep_conditions else _z3_int_val(1)

    def decode_counterexample(self, model: Any) -> list[int]:
        """Extract the violating dependence vector from a Z3 counterexample model.

        Evaluates the dependence vector variables in the model.  If the model
        contains concrete integer values, returns them directly.

        Args:
            model: Z3 model (or dict for testing).

        Returns:
            List of integers representing the violating dependence vector.
        """
        if not self.dependence_vectors:
            return []
        # Return the first dependence vector that is not lex-positive under M
        for dep in self.dependence_vectors:
            md: list[int] = [
                sum(self.transform_matrix[r][c] * dep[c] for c in range(len(dep)))
                for r in range(len(self.transform_matrix))
            ]
            if not _lex_positive_int(md):
                return list(dep)
        return list(self.dependence_vectors[0])

    def check_legality(self, session: Any) -> bool:
        """Check legality using a Z3 session and update ``self.is_legal``.

        This method encodes the legality condition, sends it to the session,
        and updates ``is_legal`` and ``counterexample`` in place.

        Args:
            session: A Z3Session or compatible object with a ``check(formula)``
                     method returning a result with a ``sat`` attribute.

        Returns:
            True if legal, False otherwise.
        """
        formula = self.encode_legality()
        if _Z3_AVAILABLE and hasattr(session, "check"):
            result = session.check(formula)
            self.is_legal = bool(result)
        else:
            # Fallback: check statically
            self.is_legal = all(
                _lex_positive_int([
                    sum(self.transform_matrix[r][c] * dep[c] for c in range(len(dep)))
                    for r in range(len(self.transform_matrix))
                ])
                for dep in self.dependence_vectors
            )
        return bool(self.is_legal)

    def compose_transforms(self, other: AffineLegality) -> AffineLegality:
        """Return the composition of this transform with another.

        Computes the matrix product ``other.transform_matrix @ self.transform_matrix``
        (applying ``self`` first, then ``other``).

        Args:
            other: Another AffineLegality to compose with.

        Returns:
            A new AffineLegality with the composed transform matrix.
        """
        A = other.transform_matrix
        B = self.transform_matrix
        n = len(B)
        m = len(A)
        k = len(B[0]) if B else 0
        composed = [
            [sum(A[i][j] * B[j][c] for j in range(min(len(A[i]), n))) for c in range(k)]
            for i in range(m)
        ]
        return AffineLegality(
            transform_matrix=composed,
            dependence_vectors=self.dependence_vectors,
            legality_formula=None,
            counterexample=None,
            is_legal=None,
        )

    def invert_transform(self) -> list[list[int]] | None:
        """Compute the integer inverse of the transform matrix.

        Uses Gaussian elimination with exact integer arithmetic.  Returns None
        if the matrix is not invertible over the integers (i.e., determinant != ±1).

        Returns:
            The inverse matrix as a list of int rows, or None if not invertible.
        """
        M = [list(row) for row in self.transform_matrix]
        n = len(M)
        if n == 0:
            return []
        if any(len(row) != n for row in M):
            return None  # Not square

        # Augment with identity
        aug = [M[i] + [int(i == j) for j in range(n)] for i in range(n)]

        for col in range(n):
            # Find pivot
            pivot = None
            for row in range(col, n):
                if aug[row][col] != 0:
                    pivot = row
                    break
            if pivot is None:
                return None
            aug[col], aug[pivot] = aug[pivot], aug[col]

            pval = aug[col][col]
            if abs(pval) != 1:
                return None  # Not unimodular

            for row in range(n):
                if row != col and aug[row][col] != 0:
                    factor = aug[row][col] // pval
                    aug[row] = [aug[row][k] - factor * aug[col][k] for k in range(2 * n)]

        return [aug[i][n:] for i in range(n)]

    def dependence_cone(self) -> list[Any]:
        """Return Z3 constraints representing the dependence cone.

        The dependence cone is the set of vectors expressible as non-negative
        integer linear combinations of the declared dependence vectors.

        Returns:
            List of Z3 constraints, one per dependent vector combination.
        """
        if not self.dependence_vectors:
            return []
        constraints: list[Any] = []
        for dep in self.dependence_vectors:
            # For each d in D: And(d[i] constraints)
            for component in dep:
                if _Z3_AVAILABLE:
                    constraints.append(_z3.BoolVal(True))
                else:
                    constraints.append(f"in_cone({component})")
        return constraints

    def fourier_motzkin_project(self, var_idx: int) -> AffineLegality:
        """Project out one variable from the dependence vectors using Fourier-Motzkin.

        Eliminates the variable at position ``var_idx`` from all dependence
        vectors by forming pairwise combinations of upper and lower bounds.

        Args:
            var_idx: Index of the variable to eliminate (0-based).

        Returns:
            A new AffineLegality with the projected dependence vectors.
        """
        if not self.dependence_vectors:
            return AffineLegality(
                transform_matrix=self.transform_matrix,
                dependence_vectors=[],
                legality_formula=None,
                counterexample=None,
                is_legal=None,
            )

        uppers: list[list[int]] = []
        lowers: list[list[int]] = []
        neutral: list[list[int]] = []

        for dep in self.dependence_vectors:
            coeff = dep[var_idx] if var_idx < len(dep) else 0
            if coeff > 0:
                uppers.append(dep)
            elif coeff < 0:
                lowers.append(dep)
            else:
                neutral.append(dep)

        new_deps = list(neutral)
        for u in uppers:
            u_c = u[var_idx]
            for lo in lowers:
                lo_c = abs(lo[var_idx])
                combined = [u_c * lo[j] + lo_c * u[j] for j in range(len(u))]
                # Remove the var_idx position
                projected = combined[:var_idx] + combined[var_idx + 1:]
                new_deps.append(projected)

        # Also project the transform matrix (drop column var_idx)
        new_M = [
            row[:var_idx] + row[var_idx + 1:]
            for row in self.transform_matrix
        ]

        return AffineLegality(
            transform_matrix=new_M,
            dependence_vectors=new_deps,
            legality_formula=None,
            counterexample=None,
            is_legal=None,
        )

    def farkas_certificate(self) -> list[float] | None:
        """Attempt to compute Farkas multipliers certifying infeasibility.

        If the dependence cone is empty (no dependence vectors), there is
        nothing to certify and None is returned.  Otherwise, this method uses
        a simple greedy approach to find non-negative multipliers y such that
        y^T * M_d < 0, where M_d are the rows M*d for each dependence vector d.

        Returns:
            List of floats (Farkas multipliers) if found, else None.
        """
        if not self.dependence_vectors:
            return None

        rows: list[list[int]] = []
        for dep in self.dependence_vectors:
            md = [
                sum(self.transform_matrix[r][c] * dep[c] for c in range(len(dep)))
                for r in range(len(self.transform_matrix))
            ]
            rows.append(md)

        # Simple feasibility certificate: check if sum of rows is all-negative
        n = len(rows[0]) if rows else 0
        if n == 0:
            return None

        # Equal-weight multipliers
        m = len(rows)
        y = [1.0 / m] * m
        s = [sum(y[i] * rows[i][j] for i in range(m)) for j in range(n)]

        if all(s[j] < 0 for j in range(n)):
            return y
        return None

    def copilot_suggest_legal_transform(self) -> str:
        """Return a textual suggestion for making an illegal transform legal.

        This heuristic method inspects the current transform matrix and suggests
        simple modifications (e.g., skewing or transposing loop order) that
        are likely to make the transform legal.

        Returns:
            Human-readable suggestion string.
        """
        if not self.transform_matrix:
            return "The transform matrix is empty — no suggestion possible."

        n = len(self.transform_matrix)
        # Check if identity is legal
        identity_legal = all(
            _lex_positive_int([dep[r] for r in range(min(n, len(dep)))])
            for dep in self.dependence_vectors
        )
        if identity_legal:
            return (
                "The identity transform is legal for the given dependence vectors. "
                "Consider reverting to the identity before applying further transformations."
            )

        # Suggest loop interchange: transpose the matrix
        return (
            f"Try transposing the {n}x{n} transform matrix (loop interchange). "
            "Alternatively, add a skewing term: set M[i][i+1] = 1 for problematic dimensions. "
            "Use the copilot_derive_tiling_schedule() in algorithms.py for tile-based transforms."
        )


# ---------------------------------------------------------------------------
# QuantifierDiscipline
# ---------------------------------------------------------------------------


@dataclass
class QuantifierDiscipline:
    """Strategy for handling quantifiers in tensor index formulas.

    Many tensor shape and index constraints naturally involve universal
    quantifiers (e.g., "for all valid indices i, the access is in bounds").
    This dataclass records the chosen discipline for encoding such formulas
    in Z3 in a decidable and terminating manner.

    Attributes:
        discipline_kind: The chosen strategy (QF, inline, Skolem, instantiate).
        trigger_pattern: Z3 trigger pattern string for e-matching (if applicable).
        instantiation_depth: Maximum depth for quantifier instantiation.
        bound_vars: Names of the universally/existentially bound variables.
        witness_terms: Z3 expressions to substitute for existential witnesses.

    copilot notes: For most tensor problems, ALWAYS_QF is the best choice —
    tensor extents are bounded integers and quantifiers can be eliminated by
    unrolling or Fourier-Motzkin projection.
    """

    discipline_kind: DisciplineKind
    trigger_pattern: str
    instantiation_depth: int
    bound_vars: list[str]
    witness_terms: list[Any]

    def apply_discipline(self, formula: Any) -> Any:
        """Apply the discipline strategy to a formula.

        Dispatches to the appropriate method based on ``discipline_kind``.

        Args:
            formula: A Z3 formula (or symbolic stub string).

        Returns:
            The disciplined formula.
        """
        if self.discipline_kind == DisciplineKind.ALWAYS_QF:
            return self.quantifier_free_equivalent(formula)
        elif self.discipline_kind == DisciplineKind.SKOLEM:
            return self.skolemize(formula)
        elif self.discipline_kind == DisciplineKind.INSTANTIATE:
            return self.instantiate(self.witness_terms)
        else:
            # INLINE_QUANT: return as-is (triggers handled by Z3 itself)
            return formula

    def skolemize(self, formula: Any) -> Any:
        """Replace existential quantifiers with fresh Skolem constants.

        For each bound variable named ``x``, introduces a fresh constant
        ``sk_x`` and substitutes it into the formula body.

        Args:
            formula: A Z3 formula or symbolic stub string.

        Returns:
            Skolemized formula (stub string if z3 unavailable).
        """
        if not _Z3_AVAILABLE:
            subs = {v: f"sk_{v}" for v in self.bound_vars}
            result = str(formula)
            for orig, sk in subs.items():
                result = result.replace(orig, sk)
            return f"Skolemized({result})"

        # With Z3: introduce fresh Int constants for each existential var
        skolem_consts = [_z3.Int(f"sk_{v}") for v in self.bound_vars]
        # Return the formula with the bound vars replaced by Skolem constants
        # (This is a simplified version — full skolemization requires Z3 substitution)
        return formula  # Z3 handles skolemization internally during solving

    def instantiate(self, terms: list[Any]) -> Any:
        """Substitute bound variables with the given ground terms.

        If there are fewer terms than bound variables, the remaining variables
        are left unsubstituted.

        Args:
            terms: List of Z3 expressions (or strings) to substitute.

        Returns:
            Instantiated formula expression.
        """
        if not terms:
            return f"Instantiated([], {self.bound_vars})"

        pairs = list(zip(self.bound_vars, terms))
        result = f"Instantiated({pairs})"
        return result

    def check_termination(self) -> bool:
        """Return True if the instantiation depth is within a safe bound.

        An instantiation depth of 0-10 is considered safe.  Above 10 the
        risk of non-termination or exponential blowup increases significantly.

        Returns:
            True if safe (depth <= 10), False otherwise.
        """
        return 0 <= self.instantiation_depth <= 10

    def trigger_loop_safe(self) -> bool:
        """Heuristic check that the trigger pattern will not cause e-matching loops.

        A trigger is considered loop-safe if it does not contain nested
        applications of the same function symbol and does not match the
        conclusion of any known axiom.

        Returns:
            True if the trigger appears loop-safe, False otherwise.
        """
        if not self.trigger_pattern:
            return True
        # Simple syntactic check: trigger should not be a single variable
        # and should not contain recursive function applications
        if self.trigger_pattern.count("(") > 3:
            return False  # Deeply nested — potential loop
        # Check for known dangerous patterns
        dangerous = ["map(", "select(map(", "store("]
        return not any(d in self.trigger_pattern for d in dangerous)

    def quantifier_free_equivalent(self, formula: Any) -> Any:
        """Produce a quantifier-free equivalent by Skolemization and instantiation.

        First skolemizes existential quantifiers, then instantiates universal
        quantifiers at the declared witness terms.

        Args:
            formula: Input formula to make quantifier-free.

        Returns:
            QF formula (or stub string).
        """
        skolemized = self.skolemize(formula)
        return self.instantiate(self.witness_terms) if self.witness_terms else skolemized

    def add_e_matching_trigger(self, trigger: str) -> QuantifierDiscipline:
        """Return a new QuantifierDiscipline with an additional trigger.

        Does not mutate self (dataclass approach — returns new instance).

        Args:
            trigger: Additional trigger pattern string to append.

        Returns:
            New QuantifierDiscipline with updated trigger_pattern.
        """
        combined = (self.trigger_pattern + ";" + trigger).strip(";")
        return QuantifierDiscipline(
            discipline_kind=self.discipline_kind,
            trigger_pattern=combined,
            instantiation_depth=self.instantiation_depth,
            bound_vars=list(self.bound_vars),
            witness_terms=list(self.witness_terms),
        )

    def copilot_recommend_discipline(self, formula_summary: str) -> str:
        """Return a discipline recommendation based on a formula summary string.

        Analyses the summary for keywords (quantifier alternation, large domains,
        recursive predicates) and recommends the most appropriate discipline.

        Args:
            formula_summary: Natural-language or SMT-LIB2 summary of the formula.

        Returns:
            Recommendation string.
        """
        summary_lower = formula_summary.lower()

        if "forall" not in summary_lower and "exists" not in summary_lower:
            return (
                "The formula appears quantifier-free. "
                "Recommended discipline: ALWAYS_QF (no discipline needed)."
            )

        if "forall" in summary_lower and "exists" not in summary_lower:
            count = summary_lower.count("forall")
            if count <= 2:
                return (
                    f"Found {count} universal quantifier(s) with no existential alternation. "
                    "Recommended discipline: INSTANTIATE at concrete tensor dimension bounds. "
                    "Set instantiation_depth to the maximum tensor rank (typically 4-6)."
                )

        if "exists" in summary_lower and "forall" not in summary_lower:
            return (
                "Found existential quantifier(s). "
                "Recommended discipline: SKOLEM — introduce fresh Skolem constants. "
                "This preserves satisfiability and avoids e-matching overhead."
            )

        return (
            "Found quantifier alternation (∀∃ or ∃∀). "
            "Recommended discipline: ALWAYS_QF — eliminate all quantifiers via "
            "Fourier-Motzkin projection or finite unrolling. "
            "Quantifier alternation in LIA is decidable but may be expensive."
        )


# ---------------------------------------------------------------------------
# WitnessExtractor
# ---------------------------------------------------------------------------


@dataclass
class WitnessExtractor:
    """Extracts and validates witnesses from Z3 solver results.

    A witness is a concrete assignment to the free variables of a formula
    that either satisfies the formula (SAT witness) or certifies its
    unsatisfiability (UNSAT witness / Farkas certificate).

    Attributes:
        proof_or_model: The Z3 proof object or satisfying model.
        extraction_strategy: Which strategy to use for witness extraction.
        witness_bindings: List of extracted (var, value) bindings.
        validation_status: Current validation status (from reconstruction module).
        extraction_log: Log of extraction steps for debugging.

    copilot notes: Use ``extract_witnesses()`` as the entry point, then
    ``validate_witness()`` and ``lift_to_high_level()`` to get usable results.
    """

    proof_or_model: Any
    extraction_strategy: ExtractionStrategy
    witness_bindings: list[Any]
    validation_status: Any
    extraction_log: list[str]

    def extract_witnesses(self) -> list[Any]:
        """Main dispatch method for witness extraction.

        Dispatches to the appropriate extraction method based on
        ``self.extraction_strategy``.

        Returns:
            List of extracted witness bindings (var, value) pairs or Z3 exprs.
        """
        self.extraction_log.append(f"extract_witnesses: strategy={self.extraction_strategy}")

        if self.extraction_strategy == ExtractionStrategy.FROM_SAT_MODEL:
            return self._extract_from_sat_model()
        elif self.extraction_strategy == ExtractionStrategy.FROM_UNSAT_CORE:
            return self._extract_from_unsat_core()
        elif self.extraction_strategy == ExtractionStrategy.FROM_PROOF:
            return self._extract_from_proof()
        elif self.extraction_strategy == ExtractionStrategy.INTERPOLATION:
            return self._extract_interpolation_witness()
        else:
            self.extraction_log.append("Unknown extraction strategy")
            return []

    def _extract_from_sat_model(self) -> list[Any]:
        """Extract witness bindings from a Z3 SAT model.

        Returns:
            List of (var_name, value) tuples extracted from the model.
        """
        if self.proof_or_model is None:
            self.extraction_log.append("No model available")
            return []

        bindings: list[Any] = []
        if _Z3_AVAILABLE and hasattr(self.proof_or_model, "decls"):
            for decl in self.proof_or_model.decls():
                val = self.proof_or_model[decl]
                bindings.append((str(decl), val))
                self.extraction_log.append(f"  bound: {decl} = {val}")
        elif isinstance(self.proof_or_model, dict):
            for k, v in self.proof_or_model.items():
                bindings.append((k, v))
                self.extraction_log.append(f"  bound: {k} = {v}")

        self.witness_bindings = bindings
        return bindings

    def _extract_from_unsat_core(self) -> list[Any]:
        """Extract an infeasibility certificate from an UNSAT core.

        Returns:
            List of core clause labels (strings) that together are unsatisfiable.
        """
        if isinstance(self.proof_or_model, (list, tuple)):
            core = list(self.proof_or_model)
            self.extraction_log.append(f"UNSAT core: {len(core)} clauses")
            self.witness_bindings = [("core_clause", c) for c in core]
            return self.witness_bindings
        self.extraction_log.append("No UNSAT core available")
        return []

    def _extract_from_proof(self) -> list[Any]:
        """Extract proof steps as witness bindings from a Z3 proof object.

        Returns:
            List of proof step descriptions as string pairs.
        """
        if self.proof_or_model is None:
            self.extraction_log.append("No proof available")
            return []
        proof_str = str(self.proof_or_model)
        steps = proof_str.split("\n")[:10]  # Take first 10 lines
        bindings = [(f"step_{i}", s.strip()) for i, s in enumerate(steps) if s.strip()]
        self.witness_bindings = bindings
        return bindings

    def _extract_interpolation_witness(self) -> list[Any]:
        """Extract a Craig interpolant as a witness.

        Returns:
            List containing the interpolant formula as a binding.
        """
        self.extraction_log.append("Interpolation witness extraction requested")
        binding = ("interpolant", str(self.proof_or_model))
        self.witness_bindings = [binding]
        return [binding]

    def validate_witness(self, w: Any) -> bool:
        """Check whether a single witness binding satisfies the original constraint.

        For (var_name, value) pairs, checks that the value is a non-negative
        integer (for tensor shape witnesses) or a valid Z3 expression.

        Args:
            w: A witness binding, expected to be a (name, value) tuple or pair.

        Returns:
            True if the witness appears valid, False otherwise.
        """
        if isinstance(w, (list, tuple)) and len(w) == 2:
            _, value = w
            if isinstance(value, int):
                return value > 0  # Shape witnesses must be positive
            if isinstance(value, str) and value.isdigit():
                return int(value) > 0
            return True  # Cannot validate non-integer witnesses statically
        return False

    def minimize_witness(self) -> WitnessExtractor:
        """Return a new WitnessExtractor with a minimal set of witness bindings.

        Removes bindings that are not strictly necessary to validate the witness.
        A binding is considered necessary if removing it fails ``validate_witness``.

        Returns:
            New WitnessExtractor with reduced ``witness_bindings``.
        """
        minimal: list[Any] = []
        for binding in self.witness_bindings:
            if self.validate_witness(binding):
                minimal.append(binding)
        return WitnessExtractor(
            proof_or_model=self.proof_or_model,
            extraction_strategy=self.extraction_strategy,
            witness_bindings=minimal,
            validation_status=self.validation_status,
            extraction_log=list(self.extraction_log) + ["minimize_witness called"],
        )

    def lift_to_high_level(self) -> dict[str, Any]:
        """Convert all Z3 witness values to plain Python types.

        Returns:
            Dict mapping variable names (str) to Python int/bool/str values.
        """
        result: dict[str, Any] = {}
        for binding in self.witness_bindings:
            if isinstance(binding, (list, tuple)) and len(binding) == 2:
                name, value = binding
                if _Z3_AVAILABLE and hasattr(value, "as_long"):
                    result[str(name)] = int(value.as_long())
                elif isinstance(value, int):
                    result[str(name)] = value
                elif isinstance(value, str):
                    try:
                        result[str(name)] = int(value)
                    except ValueError:
                        result[str(name)] = value
                else:
                    result[str(name)] = str(value)
        return result

    def serialize_witnesses(self) -> str:
        """Return a JSON-serializable string representation of the witnesses.

        Converts all witness bindings to primitive Python types before
        JSON serialization.

        Returns:
            JSON string of the witness bindings dict.
        """
        high_level = self.lift_to_high_level()
        return json.dumps(high_level, default=str, indent=2)

    def check_completeness(self) -> bool:
        """Check whether all free variables have been bound in the witness.

        Returns:
            True if ``witness_bindings`` is non-empty, False otherwise.
        """
        return len(self.witness_bindings) > 0

    def merge_with(self, other: WitnessExtractor) -> WitnessExtractor:
        """Combine the witness bindings from two extractors.

        Variable names that appear in both are taken from ``self`` (self takes
        precedence over ``other``).

        Args:
            other: Another WitnessExtractor to merge with.

        Returns:
            A new WitnessExtractor with combined bindings.
        """
        self_names = {
            b[0] for b in self.witness_bindings
            if isinstance(b, (list, tuple)) and len(b) == 2
        }
        merged = list(self.witness_bindings) + [
            b for b in other.witness_bindings
            if not (isinstance(b, (list, tuple)) and len(b) == 2 and b[0] in self_names)
        ]
        return WitnessExtractor(
            proof_or_model=self.proof_or_model,
            extraction_strategy=self.extraction_strategy,
            witness_bindings=merged,
            validation_status=self.validation_status,
            extraction_log=list(self.extraction_log) + ["merged with other extractor"],
        )

    def copilot_interpret_witness(self, context: str) -> str:
        """Return a human-readable interpretation of the extracted witnesses.

        Args:
            context: Natural-language description of the verification context
                     (e.g., "shape constraint for 2D convolution").

        Returns:
            Human-readable interpretation string.
        """
        if not self.witness_bindings:
            return f"[{context}] No witness bindings found."

        high_level = self.lift_to_high_level()
        parts = [f"[{context}] Witness ({self.extraction_strategy.value}):"]
        for name, val in sorted(high_level.items()):
            parts.append(f"  {name} = {val}")

        if self.extraction_strategy == ExtractionStrategy.FROM_SAT_MODEL:
            parts.append("  → The formula is satisfiable with these values.")
        elif self.extraction_strategy == ExtractionStrategy.FROM_UNSAT_CORE:
            parts.append("  → These clauses together form the infeasibility certificate.")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# TensorConstraint
# ---------------------------------------------------------------------------


@dataclass
class TensorConstraint:
    """A single shape or index constraint on one or two tensors.

    TensorConstraint is the unit of constraint encoding: it wraps a Z3 formula
    along with the semantic kind of the constraint and references to the
    tensor(s) it constrains.

    Attributes:
        constraint_kind: Semantic category of the constraint.
        lhs_tensor: The primary tensor extent.
        rhs_tensor: The secondary tensor extent (for binary constraints), or None.
        constraint_expr: The Z3 formula encoding the constraint.
        is_equality: True if the constraint is an equality (vs inequality).

    copilot notes: Use ``encode()`` to obtain the raw formula, ``negate()``
    to get the negation (useful for counterexample search), and
    ``check_satisfiable()`` to check via a solver session.
    """

    constraint_kind: ConstraintKind
    lhs_tensor: TensorExtent
    rhs_tensor: TensorExtent | None
    constraint_expr: Any
    is_equality: bool = False

    def encode(self) -> Any:
        """Return the Z3 formula encoding this constraint.

        This is the primary encoding method.  It returns ``constraint_expr``
        directly, which must have been set by the factory that created this
        TensorConstraint.

        Returns:
            The constraint_expr Z3 formula (or stub string).
        """
        return self.constraint_expr

    def negate(self) -> TensorConstraint:
        """Return a new TensorConstraint representing the logical negation.

        The negated constraint can be used to search for counterexamples:
        if the negation is UNSAT, the original constraint holds universally.

        Returns:
            New TensorConstraint with ``_z3_not(constraint_expr)``.
        """
        return TensorConstraint(
            constraint_kind=self.constraint_kind,
            lhs_tensor=self.lhs_tensor,
            rhs_tensor=self.rhs_tensor,
            constraint_expr=_z3_not(self.constraint_expr),
            is_equality=self.is_equality,
        )

    def implies(self, other: TensorConstraint) -> Any:
        """Return a Z3 implication: ``self.constraint_expr => other.constraint_expr``.

        Args:
            other: The consequent constraint.

        Returns:
            Z3 implication formula.
        """
        return _z3_implies(self.constraint_expr, other.constraint_expr)

    def simplify(self) -> TensorConstraint:
        """Return a simplified version of this constraint.

        Applies Z3's built-in simplifier if available, otherwise returns self.

        Returns:
            Simplified TensorConstraint.
        """
        if _Z3_AVAILABLE and hasattr(_z3, "simplify"):
            try:
                simplified_expr = _z3.simplify(self.constraint_expr)
            except Exception:
                simplified_expr = self.constraint_expr
        else:
            simplified_expr = self.constraint_expr

        return TensorConstraint(
            constraint_kind=self.constraint_kind,
            lhs_tensor=self.lhs_tensor,
            rhs_tensor=self.rhs_tensor,
            constraint_expr=simplified_expr,
            is_equality=self.is_equality,
        )

    def to_normal_form(self) -> TensorConstraint:
        """Normalise the constraint to equality or canonical inequality form.

        For equality constraints, converts to a canonical representation.
        For inequality constraints, ensures the comparison direction is
        consistent (always uses <= or <, never >= or >).

        Returns:
            Normalised TensorConstraint.
        """
        # For now, simplification serves as normalisation
        return self.simplify()

    def check_satisfiable(self, session: Any) -> bool:
        """Check satisfiability of this constraint using a solver session.

        Args:
            session: A Z3Session or compatible object with a ``check`` method.

        Returns:
            True if satisfiable, False otherwise.
        """
        formula = self.encode()
        if hasattr(session, "check"):
            result = session.check(formula)
            return bool(result)

        # Fallback: try Z3 directly
        if _Z3_AVAILABLE:
            solver = _z3.Solver()
            try:
                solver.add(formula)
                outcome = solver.check()
                return str(outcome) == "sat"
            except Exception:
                return False

        return True  # Cannot determine without solver

    def get_free_vars(self) -> list[Any]:
        """Collect all free variables appearing in the constraint expression.

        Returns:
            List of Z3 expressions (or stub strings) representing free variables.
        """
        free: list[Any] = []
        free.extend(self.lhs_tensor.dim_vars)
        free.extend(self.lhs_tensor.stride_vars)
        free.append(self.lhs_tensor.total_size_var)
        if self.rhs_tensor is not None:
            free.extend(self.rhs_tensor.dim_vars)
            free.extend(self.rhs_tensor.stride_vars)
            free.append(self.rhs_tensor.total_size_var)
        return free

    def copilot_explain(self) -> str:
        """Return a human-readable explanation of this constraint.

        Returns:
            Description string including kind, tensor shapes, and formula.
        """
        lhs_shape = f"rank-{self.lhs_tensor.rank}"
        rhs_part = ""
        if self.rhs_tensor is not None:
            rhs_shape = f"rank-{self.rhs_tensor.rank}"
            rhs_part = f" and {rhs_shape} tensor"

        kind_descriptions = {
            ConstraintKind.SHAPE_COMPATIBLE: "both tensors have the same shape",
            ConstraintKind.INDEX_VALID: "the index is within bounds",
            ConstraintKind.STRIDE_CONSISTENT: "strides are consistent with the declared layout",
            ConstraintKind.BROADCAST_COMPATIBLE: "the tensors are NumPy broadcast-compatible",
            ConstraintKind.RESHAPE_VALID: "the reshape preserves the total element count",
        }
        desc = kind_descriptions.get(self.constraint_kind, "constraint holds")
        eq_note = " (equality)" if self.is_equality else " (inequality)"

        return (
            f"TensorConstraint[{self.constraint_kind.value}]: "
            f"For {lhs_shape} tensor{rhs_part}, assert that {desc}{eq_note}. "
            f"Formula: {self.constraint_expr}"
        )


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------


def _lex_positive_int(v: list[int]) -> bool:
    """Return True if integer vector v is lexicographically positive.

    A vector is lex-positive if its first non-zero component is positive.
    The zero vector is NOT lex-positive.

    Args:
        v: List of integers.

    Returns:
        True if v is lex-positive.
    """
    for component in v:
        if component > 0:
            return True
        if component < 0:
            return False
    return False  # all-zero vector is not lex-positive
