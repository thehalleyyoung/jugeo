"""
Affine Normal Form Encoder for Polyhedral Constraints
======================================================
Chapter 30 §2 of theory2.tex — JuGeo formal verification system.

This module encodes affine constraints — the bread-and-butter of polyhedral
compilation — as Z3 QF_LIA formulas.  Affine constraints arise in:

  - Loop bound analysis: lb(p) ≤ i ≤ ub(p) where bounds are affine in parameters.
  - Dependence analysis: the dependence polyhedron Ax ≤ b captures all iteration
    pairs (i_src, i_dst) where one iteration writes data read by another.
  - Tiling legality: a transformation matrix M is legal iff M*d ≻_lex 0 for all
    dependence vectors d in the dependence polyhedron.
  - Parametric polyhedra: {x ∈ Z^n | A*x ≤ B*p + c} for parameter vector p.

The ``AffineNormalFormEncoder`` class is the core encoder.  It provides methods
for encoding individual constraints, constraint systems, loop bounds, dependence
conditions, and legality conditions as Z3 formulas.

Module-level pure Python helpers (``gcd``, ``gcd_list``, ``matrix_vector_multiply``)
are provided for preprocessing coefficient systems before encoding.

copilot notes: Use ``encode_legality_condition()`` to check whether a polyhedral
loop transformation is legal.  Use ``fourier_motzkin_eliminate()`` to project out
variables before encoding (reduces to a smaller QF_LIA formula).
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "AffineNormalFormEncoder",
    "gcd",
    "gcd_list",
    "matrix_vector_multiply",
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
    """Create a Z3 And expression or return a symbolic stub string."""
    if _Z3_AVAILABLE and args:
        return _z3.And(*args)
    return f"And({', '.join(str(a) for a in args)})"


def _z3_or(*args: Any) -> Any:
    """Create a Z3 Or expression or return a symbolic stub string."""
    if _Z3_AVAILABLE and args:
        return _z3.Or(*args)
    return f"Or({', '.join(str(a) for a in args)})"


def _z3_int_val(n: int) -> Any:
    """Return a Z3 integer literal or the Python int itself."""
    if _Z3_AVAILABLE:
        return _z3.IntVal(n)
    return n


# ---------------------------------------------------------------------------
# Pure Python helpers
# ---------------------------------------------------------------------------


def gcd(a: int, b: int) -> int:
    """Compute the greatest common divisor of two integers using the Euclidean algorithm.

    Returns a non-negative integer.  If both inputs are zero, returns 0.
    The sign of the inputs is ignored (returns non-negative GCD).

    Args:
        a: First integer.
        b: Second integer.

    Returns:
        Non-negative GCD of abs(a) and abs(b).

    Example::

        gcd(12, 8)   # 4
        gcd(-6, 9)   # 3
        gcd(0, 5)    # 5
    """
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def gcd_list(nums: list[int]) -> int:
    """Compute the GCD of a list of integers.

    Uses the identity: gcd(a, b, c, ...) = gcd(a, gcd(b, gcd(c, ...))).

    Args:
        nums: List of integers.  Must be non-empty.

    Returns:
        Non-negative GCD of all elements.

    Raises:
        ValueError: If ``nums`` is empty.

    Example::

        gcd_list([12, 8, 4])   # 4
        gcd_list([7])          # 7
        gcd_list([0, 0, 6])    # 6
    """
    if not nums:
        raise ValueError("gcd_list requires a non-empty list")
    result = abs(nums[0])
    for n in nums[1:]:
        result = gcd(result, abs(n))
    return result


def matrix_vector_multiply(M: list[list[int]], v: list[int]) -> list[int]:
    """Multiply an integer matrix M by an integer column vector v.

    Computes ``M @ v`` where M is (rows x cols) and v has length cols.

    Args:
        M: Matrix as list of row lists.  Each row must have the same length.
        v: Column vector as list of integers.

    Returns:
        Result vector as list of integers, length == len(M).

    Raises:
        ValueError: If the dimensions are incompatible.

    Example::

        matrix_vector_multiply([[1, 2], [3, 4]], [1, 1])  # [3, 7]
        matrix_vector_multiply([[1, 0], [0, 1]], [5, 3])  # [5, 3]
    """
    if not M:
        return []
    n_cols = len(M[0])
    if len(v) != n_cols:
        raise ValueError(
            f"Matrix has {n_cols} columns but vector has {len(v)} elements"
        )
    return [sum(M[i][j] * v[j] for j in range(n_cols)) for i in range(len(M))]


# ---------------------------------------------------------------------------
# AffineNormalFormEncoder
# ---------------------------------------------------------------------------


class AffineNormalFormEncoder:
    """Encodes affine constraints as Z3 QF_LIA formulas.

    Affine constraints arise in polyhedral compilation:
    - Loop bounds: lb(p) ≤ i ≤ ub(p) where lb, ub are affine in parameters p.
    - Dependence conditions: ∃λ: A_src*i_src + b_src = A_dst*i_dst + b_dst + λ*d.
    - Tiling legality: M*d > 0 for all dependence vectors d.
    - Parametric polyhedra: {x ∈ Z^n | Ax ≤ b(p)} for parameter vector p.

    Each method returns a Z3 formula (or a symbolic stub string when z3 is not
    installed).  The formulas are in the QF_LIA fragment unless stated otherwise.

    copilot notes: The most commonly used methods are:
    - ``encode_affine_system()`` for a full inequality system Ax ≤ b.
    - ``encode_legality_condition()`` for M*d ≻ 0 checks.
    - ``fourier_motzkin_eliminate()`` for variable elimination.

    Example::

        encoder = AffineNormalFormEncoder()
        i, j = [z3.Int('i'), z3.Int('j')]
        formula = encoder.encode_affine_constraint([1, -1], [i, j], 0, 'leq')
        # Encodes: i - j <= 0, i.e., i <= j
    """

    def __init__(self, z3_ctx: Any = None) -> None:
        """Initialise the encoder with an optional Z3 context.

        Args:
            z3_ctx: Optional Z3 context object.  If None, uses the default context.
        """
        self._ctx = z3_ctx
        self._smt2_assertions: list[str] = []

    def encode_affine_constraint(
        self,
        coeffs: list[int],
        vars_: list[Any],
        rhs: int,
        kind: str,
    ) -> Any:
        """Encode a single affine constraint as a Z3 formula.

        Builds the expression: ``sum(coeffs[i] * vars_[i]) <kind> rhs``.

        Args:
            coeffs: Integer coefficients for each variable.
            vars_: Z3 Int expressions (or stubs) for each variable.
            rhs: Right-hand side integer constant.
            kind: Comparison operator — one of ``'leq'``, ``'geq'``, ``'eq'``,
                  ``'lt'``, ``'gt'``.

        Returns:
            Z3 formula (or stub string).

        Raises:
            ValueError: If ``kind`` is not a recognized comparison operator.
            ValueError: If ``len(coeffs) != len(vars_)``.
        """
        valid_kinds = {"leq", "geq", "eq", "lt", "gt"}
        if kind not in valid_kinds:
            raise ValueError(f"Unknown kind '{kind}'. Expected one of {valid_kinds}")
        if len(coeffs) != len(vars_):
            raise ValueError(
                f"len(coeffs)={len(coeffs)} != len(vars_)={len(vars_)}"
            )

        if not coeffs:
            # Empty sum = 0
            zero_cmp = {
                "leq": 0 <= rhs,
                "geq": 0 >= rhs,
                "eq": 0 == rhs,
                "lt": 0 < rhs,
                "gt": 0 > rhs,
            }[kind]
            return _z3.BoolVal(zero_cmp) if _Z3_AVAILABLE else str(zero_cmp)

        # Build the linear sum
        if _Z3_AVAILABLE:
            lhs: Any = _z3.IntVal(0)
            for c, v in zip(coeffs, vars_):
                lhs = lhs + _z3.IntVal(c) * v
            rhs_expr = _z3.IntVal(rhs)
            if kind == "leq":
                return lhs <= rhs_expr
            elif kind == "geq":
                return lhs >= rhs_expr
            elif kind == "eq":
                return lhs == rhs_expr
            elif kind == "lt":
                return lhs < rhs_expr
            else:  # gt
                return lhs > rhs_expr
        else:
            terms = " + ".join(
                f"{c}*{v}" if c != 1 else str(v)
                for c, v in zip(coeffs, vars_)
                if c != 0
            ) or "0"
            op = {"leq": "<=", "geq": ">=", "eq": "==", "lt": "<", "gt": ">"}[kind]
            return f"({terms} {op} {rhs})"

    def encode_affine_system(
        self,
        A: list[list[int]],
        b: list[int],
        vars_: list[Any],
    ) -> Any:
        """Encode a system of affine inequalities Ax ≤ b as a Z3 formula.

        Each row i of A and corresponding element b[i] produces one constraint:
        ``sum(A[i][j] * vars_[j]) <= b[i]``.

        Args:
            A: Constraint matrix (rows x vars).
            b: Right-hand side vector (length == len(A)).
            vars_: Z3 Int variables or stubs (length == len(A[0])).

        Returns:
            Z3 And of all row constraints (or stub string).

        Raises:
            ValueError: If dimensions are inconsistent.
        """
        if not A:
            return _z3.BoolVal(True) if _Z3_AVAILABLE else "True"
        if len(A) != len(b):
            raise ValueError(f"A has {len(A)} rows but b has {len(b)} elements")

        conjuncts = [
            self.encode_affine_constraint(A[i], vars_, b[i], "leq")
            for i in range(len(A))
        ]
        return _z3_and(*conjuncts)

    def encode_parametric_polyhedron(
        self,
        A_body: list[list[int]],
        A_param: list[list[int]],
        b: list[int],
        param_vars: list[Any],
        body_vars: list[Any],
    ) -> Any:
        """Encode a parametric polyhedron ``A_body * x + A_param * p ≤ b``.

        The parametric polyhedron is the set of body variable vectors x that
        satisfy the constraint for given parameter vector p.

        Args:
            A_body: Matrix of coefficients for the body variables x.
            A_param: Matrix of coefficients for the parameter variables p.
            b: Right-hand side vector.
            param_vars: Z3 Int variables (or stubs) for the parameters p.
            body_vars: Z3 Int variables (or stubs) for the body variables x.

        Returns:
            Z3 And formula (or stub string) expressing the polyhedron.
        """
        n_rows = len(A_body)
        if not n_rows:
            return _z3.BoolVal(True) if _Z3_AVAILABLE else "True"
        if len(A_param) != n_rows or len(b) != n_rows:
            raise ValueError("A_body, A_param, and b must have the same number of rows")

        conjuncts: list[Any] = []
        for i in range(n_rows):
            # Build lhs = A_body[i] * x + A_param[i] * p
            if _Z3_AVAILABLE:
                lhs: Any = _z3.IntVal(0)
                for c, v in zip(A_body[i], body_vars):
                    lhs = lhs + _z3.IntVal(c) * v
                for c, p in zip(A_param[i], param_vars):
                    lhs = lhs + _z3.IntVal(c) * p
                conjuncts.append(lhs <= _z3.IntVal(b[i]))
            else:
                body_terms = " + ".join(
                    f"{c}*{v}" for c, v in zip(A_body[i], body_vars) if c != 0
                ) or "0"
                param_terms = " + ".join(
                    f"{c}*{p}" for c, p in zip(A_param[i], param_vars) if c != 0
                ) or "0"
                conjuncts.append(f"({body_terms} + {param_terms} <= {b[i]})")

        return _z3_and(*conjuncts)

    def encode_loop_bounds(
        self,
        loop_var: Any,
        lb_coeffs: list[int],
        lb_vars: list[Any],
        lb_const: int,
        ub_coeffs: list[int],
        ub_vars: list[Any],
        ub_const: int,
    ) -> Any:
        """Encode loop bounds: ``lb_affine ≤ loop_var ≤ ub_affine``.

        The lower bound is: ``sum(lb_coeffs[i] * lb_vars[i]) + lb_const``.
        The upper bound is: ``sum(ub_coeffs[i] * ub_vars[i]) + ub_const``.

        Args:
            loop_var: Z3 Int variable (or stub) for the loop iteration variable.
            lb_coeffs: Coefficients for the lower bound affine expression.
            lb_vars: Variables in the lower bound affine expression.
            lb_const: Constant term in the lower bound.
            ub_coeffs: Coefficients for the upper bound affine expression.
            ub_vars: Variables in the upper bound affine expression.
            ub_const: Constant term in the upper bound.

        Returns:
            Z3 formula ``lb ≤ loop_var ≤ ub`` (or stub string).
        """
        # Build lb = sum(lb_coeffs[i] * lb_vars[i]) + lb_const
        if _Z3_AVAILABLE:
            lb: Any = _z3.IntVal(lb_const)
            for c, v in zip(lb_coeffs, lb_vars):
                lb = lb + _z3.IntVal(c) * v
            ub: Any = _z3.IntVal(ub_const)
            for c, v in zip(ub_coeffs, ub_vars):
                ub = ub + _z3.IntVal(c) * v
            return _z3.And(lb <= loop_var, loop_var <= ub)
        else:
            lb_terms = " + ".join(
                f"{c}*{v}" for c, v in zip(lb_coeffs, lb_vars)
            )
            lb_str = f"({lb_terms} + {lb_const})" if lb_terms else str(lb_const)
            ub_terms = " + ".join(
                f"{c}*{v}" for c, v in zip(ub_coeffs, ub_vars)
            )
            ub_str = f"({ub_terms} + {ub_const})" if ub_terms else str(ub_const)
            return f"({lb_str} <= {loop_var} And {loop_var} <= {ub_str})"

    def encode_dependence_constraint(
        self,
        src_idx: list[Any],
        dst_idx: list[Any],
        dep_vector: list[int],
    ) -> Any:
        """Encode the dependence constraint ``dst_idx - src_idx = dep_vector``.

        This asserts that the difference between the destination and source
        iteration vectors equals a concrete dependence vector d.

        Args:
            src_idx: Z3 Int expressions for the source iteration indices.
            dst_idx: Z3 Int expressions for the destination iteration indices.
            dep_vector: Integer dependence vector d.

        Returns:
            Z3 And of element-wise equality constraints (or stub string).
        """
        if len(src_idx) != len(dst_idx) or len(src_idx) != len(dep_vector):
            raise ValueError("src_idx, dst_idx, and dep_vector must have the same length")

        conjuncts: list[Any] = []
        for s, d, delta in zip(src_idx, dst_idx, dep_vector):
            if _Z3_AVAILABLE:
                conjuncts.append(d - s == _z3.IntVal(delta))
            else:
                conjuncts.append(f"({d} - {s} == {delta})")
        return _z3_and(*conjuncts)

    def encode_legality_condition(
        self,
        transform_M: list[list[int]],
        dep_vectors: list[list[int]],
    ) -> Any:
        """Encode the legality condition: for each d, M*d is lex-positive.

        A transformation matrix M is legal for a set of dependence vectors D
        if for every d ∈ D, the vector M*d is lexicographically positive:
        its first non-zero component is strictly positive.

        This is encoded as a conjunction over all d:
        ``Or(Md[0] > 0, And(Md[0] = 0, Md[1] > 0), ..., And(Md[0]=0, ..., Md[k-1]=0, Md[k]>0))``.

        Args:
            transform_M: The transformation matrix M (rows x dims).
            dep_vectors: List of dependence vectors to check.

        Returns:
            Z3 And formula (or stub string) encoding all lex-positivity conditions.
        """
        if not dep_vectors or not transform_M:
            return _z3.BoolVal(True) if _Z3_AVAILABLE else "True"

        all_conditions: list[Any] = []
        for dep in dep_vectors:
            # Compute M * dep (integer arithmetic)
            md = matrix_vector_multiply(transform_M, dep)
            lex_cond = self.lex_positive_encoding(
                [_z3.IntVal(c) if _Z3_AVAILABLE else c for c in md]
            )
            all_conditions.append(lex_cond)

        return _z3_and(*all_conditions)

    def fourier_motzkin_eliminate(
        self,
        constraints: list[tuple[list[int], list[Any], int, str]],
        var_idx: int,
    ) -> list[tuple[list[int], list[Any], int, str]]:
        """Eliminate one variable using Fourier-Motzkin projection.

        Each constraint is a tuple (coeffs, vars_, rhs, kind) representing:
        ``sum(coeffs[i] * vars_[i]) <kind> rhs``.

        The Fourier-Motzkin algorithm partitions constraints into:
        - Upper bounds (coeff[var_idx] > 0 after normalisation to ≤).
        - Lower bounds (coeff[var_idx] < 0).
        - Neutral (coeff[var_idx] = 0).

        New constraints are generated for each (upper, lower) pair.

        Args:
            constraints: List of (coeffs, vars_, rhs, kind) tuples.
            var_idx: Index of the variable to eliminate (0-based).

        Returns:
            Projected list of constraints with ``var_idx`` column removed.
        """
        upper: list[tuple[list[int], list[Any], int, str]] = []
        lower: list[tuple[list[int], list[Any], int, str]] = []
        neutral: list[tuple[list[int], list[Any], int, str]] = []

        for coeffs, vars_, rhs, kind in constraints:
            if var_idx >= len(coeffs):
                neutral.append((coeffs, vars_, rhs, kind))
                continue

            # Normalise to <= form
            c = coeffs[var_idx]
            if kind == "geq":
                c = -c
            elif kind == "gt":
                c = -c

            if c > 0:
                upper.append((coeffs, vars_, rhs, kind))
            elif c < 0:
                lower.append((coeffs, vars_, rhs, kind))
            else:
                neutral.append((coeffs, vars_, rhs, kind))

        # Project the var_idx column from neutral
        projected_neutral = [
            (
                coeffs[:var_idx] + coeffs[var_idx + 1:],
                vars_[:var_idx] + vars_[var_idx + 1:],
                rhs,
                kind,
            )
            for coeffs, vars_, rhs, kind in neutral
        ]

        # Generate new constraints from upper x lower pairs
        new_constraints: list[tuple[list[int], list[Any], int, str]] = []
        for u_coeffs, u_vars, u_rhs, _u_kind in upper:
            u_c = u_coeffs[var_idx]
            for l_coeffs, l_vars, l_rhs, _l_kind in lower:
                l_c = abs(l_coeffs[var_idx])
                # Eliminate x: l_c * (u_expr) + u_c * (-l_expr) <= l_c*u_rhs + u_c*(-l_rhs)
                new_coeffs = [
                    l_c * u_coeffs[j] + u_c * (-l_coeffs[j])
                    for j in range(len(u_coeffs))
                    if j != var_idx
                ]
                new_vars = [
                    v for k, v in enumerate(u_vars) if k != var_idx
                ]
                new_rhs = l_c * u_rhs - u_c * l_rhs
                # Normalise by GCD
                all_coeffs_and_rhs = new_coeffs + [new_rhs]
                nonzero = [abs(x) for x in all_coeffs_and_rhs if x != 0]
                if nonzero:
                    g = gcd_list(nonzero)
                    if g > 1:
                        new_coeffs = [c // g for c in new_coeffs]
                        new_rhs = new_rhs // g
                new_constraints.append((new_coeffs, new_vars, new_rhs, "leq"))

        return projected_neutral + new_constraints

    def normalize_affine_constraint(
        self,
        coeffs: list[int],
        vars_: list[Any],
        rhs: int,
    ) -> tuple[list[int], list[Any], int]:
        """Normalize an affine constraint by dividing all coefficients by their GCD.

        After normalisation, the GCD of all coefficients and the RHS constant is 1.
        This reduces the magnitudes of coefficients and simplifies the constraint.

        Args:
            coeffs: Coefficient list.
            vars_: Variable list.
            rhs: Right-hand side constant.

        Returns:
            3-tuple (normalised_coeffs, vars_, normalised_rhs).
        """
        all_vals = [abs(c) for c in coeffs if c != 0]
        if rhs != 0:
            all_vals.append(abs(rhs))

        if not all_vals:
            return (coeffs, vars_, rhs)

        g = gcd_list(all_vals)
        if g <= 1:
            return (coeffs, vars_, rhs)

        return ([c // g for c in coeffs], vars_, rhs // g)

    def to_smtlib2(self, constraints: list[Any]) -> str:
        """Format a list of Z3 constraints as an SMT-LIB2 string.

        The output is suitable for writing to a ``.smt2`` file or passing to
        another SMT solver.  Each constraint becomes an ``(assert ...)`` command.

        Args:
            constraints: List of Z3 formulas (or stub strings).

        Returns:
            SMT-LIB2 formatted string with all assertions.
        """
        lines = [
            "(set-logic QF_LIA)",
            "(set-option :produce-models true)",
        ]
        for i, c in enumerate(constraints):
            if _Z3_AVAILABLE and hasattr(c, "sexpr"):
                lines.append(f"(assert {c.sexpr()})")
            else:
                lines.append(f"(assert {c})  ; constraint {i}")
        lines.append("(check-sat)")
        lines.append("(get-model)")
        return "\n".join(lines)

    def copilot_simplify_affine_system(
        self,
        system: list[tuple[list[int], list[Any], int, str]],
    ) -> list[tuple[list[int], list[Any], int, str]]:
        """Remove redundant constraints from an affine system.

        A constraint (coeffs_A, vars, rhs_A, 'leq') is dominated by
        (coeffs_B, vars, rhs_B, 'leq') if coeffs_A == coeffs_B and rhs_A >= rhs_B
        (the second constraint is tighter).  This method removes dominated constraints.

        Also normalises all constraints by GCD before comparison.

        Args:
            system: List of (coeffs, vars_, rhs, kind) tuples.

        Returns:
            Simplified list with dominated constraints removed.

        copilot notes: This is a simple syntactic dominance check.  For a full
        redundancy check, use a linear programming oracle.
        """
        normalised: list[tuple[list[int], list[Any], int, str]] = []
        for coeffs, vars_, rhs, kind in system:
            norm_coeffs, norm_vars, norm_rhs = self.normalize_affine_constraint(
                coeffs, vars_, rhs
            )
            normalised.append((norm_coeffs, norm_vars, norm_rhs, kind))

        # Remove dominated constraints: keep only the tightest for each coefficient pattern
        seen: dict[str, int] = {}  # key -> tightest_rhs index
        result_indices: list[int] = []

        for i, (coeffs, _vars, rhs, kind) in enumerate(normalised):
            key = str(coeffs) + "|" + kind
            if key not in seen:
                seen[key] = i
                result_indices.append(i)
            else:
                prev_idx = seen[key]
                prev_rhs = normalised[prev_idx][2]
                if kind == "leq" and rhs < prev_rhs:
                    # This constraint is tighter — replace
                    result_indices = [j for j in result_indices if j != prev_idx]
                    result_indices.append(i)
                    seen[key] = i
                elif kind == "geq" and rhs > prev_rhs:
                    result_indices = [j for j in result_indices if j != prev_idx]
                    result_indices.append(i)
                    seen[key] = i

        return [system[i] for i in sorted(result_indices)]

    def encode_farkas_infeasibility(
        self,
        A: list[list[int]],
        b: list[int],
    ) -> Any:
        """Encode the Farkas dual: ∃y ≥ 0: y^T A = 0 ∧ y^T b < 0.

        By Farkas' lemma, the system Ax ≤ b is infeasible if and only if
        there exist multipliers y ≥ 0 such that y^T A = 0 and y^T b < 0.
        This method encodes the existence of such multipliers as a QF_LIA formula.

        The multipliers are declared as Z3 Int variables y_0, ..., y_{m-1} ≥ 0,
        and the Farkas conditions are asserted.

        Args:
            A: Constraint matrix (m rows, n columns).
            b: Right-hand side vector (length m).

        Returns:
            Z3 formula (or stub string) asserting Farkas infeasibility.
        """
        if not A:
            return _z3.BoolVal(False) if _Z3_AVAILABLE else "False"

        m = len(A)
        n = len(A[0]) if A else 0

        if _Z3_AVAILABLE:
            y_vars = [_z3.Int(f"farkas_y_{i}") for i in range(m)]
            conditions: list[Any] = []
            # y >= 0
            for y in y_vars:
                conditions.append(y >= 0)
            # y^T A = 0 (column-wise)
            for j in range(n):
                col_sum = _z3.IntVal(0)
                for i in range(m):
                    col_sum = col_sum + y_vars[i] * _z3.IntVal(A[i][j])
                conditions.append(col_sum == 0)
            # y^T b < 0
            dot_product = _z3.IntVal(0)
            for i in range(m):
                dot_product = dot_product + y_vars[i] * _z3.IntVal(b[i])
            conditions.append(dot_product < 0)
            return _z3.And(conditions)
        else:
            y_vars = [f"farkas_y_{i}" for i in range(m)]
            parts = [f"({y} >= 0)" for y in y_vars]
            for j in range(n):
                col_terms = " + ".join(f"{A[i][j]}*{y_vars[i]}" for i in range(m))
                parts.append(f"({col_terms} == 0)")
            dot = " + ".join(f"{b[i]}*{y_vars[i]}" for i in range(m))
            parts.append(f"({dot} < 0)")
            return f"And({', '.join(parts)})"

    def lex_positive_encoding(self, vec: list[Any]) -> Any:
        """Encode lexicographic positivity of an integer vector.

        The vector v is lex-positive if the first non-zero component is positive.
        Encoding:
          ``v[0] > 0``
          ``Or``
          ``(v[0] = 0 And v[1] > 0)``
          ``Or``
          ``(v[0] = 0 And v[1] = 0 And v[2] > 0)``
          ...

        Args:
            vec: List of Z3 Int expressions (or stubs) for the vector components.

        Returns:
            Z3 Or formula (or stub string) encoding lex-positivity.
        """
        if not vec:
            return _z3.BoolVal(False) if _Z3_AVAILABLE else "False"

        zero: Any = _z3.IntVal(0) if _Z3_AVAILABLE else 0
        alternatives: list[Any] = []

        for k in range(len(vec)):
            # (vec[0]=0 And ... And vec[k-1]=0 And vec[k]>0)
            prefix_zeros: list[Any] = []
            for j in range(k):
                if _Z3_AVAILABLE:
                    prefix_zeros.append(vec[j] == zero)
                else:
                    prefix_zeros.append(f"({vec[j]} == 0)")

            if _Z3_AVAILABLE:
                positive_k = vec[k] > zero
            else:
                positive_k = f"({vec[k]} > 0)"

            if prefix_zeros:
                alt = _z3_and(*(prefix_zeros + [positive_k]))
            else:
                alt = positive_k
            alternatives.append(alt)

        return _z3_or(*alternatives) if len(alternatives) > 1 else alternatives[0]
