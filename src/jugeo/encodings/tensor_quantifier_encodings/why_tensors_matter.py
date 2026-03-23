"""
Why Tensors Require Special Z3 Encoding Treatment
==================================================
Chapter 30 §1 of theory2.tex — JuGeo formal verification system.

Naive encoding of multi-dimensional tensors in Z3 fails for several reasons:

1. **Dimension structure is lost**: Using a flat ``Array(Int, T)`` discards the
   fact that the tensor has multiple independent dimensions.  Out-of-bounds
   reasoning becomes impossible without explicit bounds variables.

2. **Shape constraints need dedicated variables**: The shape (n0, n1, ...) must
   be represented as first-class Z3 Int variables so the solver can reason about
   their values (e.g., broadcast compatibility, reshape validity).

3. **Index validity is a quantified property**: Asserting "all accesses are
   in bounds" requires universal quantification over all index tuples, which
   lands in the undecidable theory of arrays + arithmetic in general.  The
   JuGeo approach restricts to *finite* extents and *affine* index functions,
   where the condition reduces to a finite conjunction in QF_LIA.

4. **Strides and layout affect aliasing**: Two tensor views of the same buffer
   may overlap; stride encoding captures this precisely without quantifiers.

5. **Mixed-rank problems**: When tensor rank is not statically known, we need
   parametric shapes.  JuGeo encodes rank as a bounded integer parameter and
   uses a universally quantified dimension array — but only where decidability
   can be guaranteed by restricting to QF_LIA after finite unrolling.

6. **Broadcasting semantics** require per-dimension case splits (dim = 1 vs
   dim = other) that must be encoded carefully to avoid non-linear arithmetic.

This module provides motivational examples, a primer encoder, and module-level
explanatory functions corresponding to §30.1 of the theory document.

copilot notes: The TensorEncodingPrimer class is intended for tutorial use;
the production encoding path is in models.py and affine_normal_form_encoder.py.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "TensorMotivationExamples",
    "TensorEncodingPrimer",
    "why_arrays_of_arrays",
    "qf_lia_decidability_argument",
    "affine_index_normal_form",
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


def _z3_implies(a: Any, b: Any) -> Any:
    """Create a Z3 Implies or a stub string."""
    if _Z3_AVAILABLE:
        return _z3.Implies(a, b)
    return f"Implies({a}, {b})"


def _z3_int_sort() -> Any:
    """Return Z3 IntSort or a stub string."""
    if _Z3_AVAILABLE:
        return _z3.IntSort()
    return "IntSort()"


def _z3_array_sort(dom: Any, rng: Any) -> Any:
    """Return Z3 ArraySort or a stub string."""
    if _Z3_AVAILABLE:
        return _z3.ArraySort(dom, rng)
    return f"ArraySort({dom}, {rng})"


def _z3_const(name: str, sort: Any) -> Any:
    """Declare a Z3 constant of a given sort, or return a stub string."""
    if _Z3_AVAILABLE:
        return _z3.Const(name, sort)
    return f"Const({name}, {sort})"


def _z3_forall(bound: list[Any], body: Any) -> Any:
    """Create a Z3 ForAll or stub string."""
    if _Z3_AVAILABLE:
        return _z3.ForAll(bound, body)
    return f"ForAll({bound}, {body})"


# ---------------------------------------------------------------------------
# TensorMotivationExamples
# ---------------------------------------------------------------------------


class TensorMotivationExamples:
    """Static examples illustrating why naive tensor encodings fail in Z3.

    Each method either shows a failing approach or demonstrates the correct
    JuGeo encoding.  These examples correspond directly to §30.1 of theory2.tex.

    copilot notes: All examples are self-contained and safe to call without
    a Z3 installation (they return stub strings when z3 is unavailable).
    """

    def naive_encoding_fails(self) -> tuple[str, str]:
        """Show how naive flat-array encoding loses dimension structure.

        A flat ``Array(Int, Int)`` in Z3 cannot express that a 2D tensor has
        dimensions (n0, n1) because the domain is a single uninterpreted integer.
        Out-of-bounds indices are not distinguishable from valid ones.

        Returns:
            A 2-tuple (explanation_text, smtlib2_example) where the SMT-LIB2
            snippet demonstrates the problem.
        """
        explanation = (
            "Naive encoding: declare A as Array(Int, Int) and hope the solver "
            "constrains indices automatically.  This fails because:\n"
            "  1. The array has no inherent bounds — any integer index is 'valid'.\n"
            "  2. The 2D structure (row, col) is invisible to the solver.\n"
            "  3. Out-of-bounds accesses cannot be detected.\n"
            "  4. Shape constraints (e.g., n0 > 0) must be asserted separately,\n"
            "     but the connection between the array and the shape is lost.\n"
            "  5. Broadcast compatibility between two such arrays is undecidable\n"
            "     without explicit shape variables."
        )

        smtlib2 = """\
; NAIVE (broken) encoding of a 2D tensor A of shape (n0, n1)
; Problem: no shape variables, no bounds, no layout info
(declare-fun A (Int) Int)          ; flat 1D array — dimension info gone
(declare-const n Int)              ; single dimension — we lost n0 and n1!
(assert (> n 0))                   ; shape constraint disconnected from A
; Now ask: is A[5] valid?  Z3 cannot answer without n0 and n1.
; (select A 5) is always well-defined — out-of-bounds is not detected.
(check-sat)  ; sat — but no meaningful guarantee
"""
        return (explanation, smtlib2)

    def correct_extent_encoding(self) -> tuple[str, str]:
        """Show the correct JuGeo extent encoding for a 2D tensor.

        The correct encoding declares separate Int variables for each dimension
        and asserts that they are positive.  Index validity is then expressible
        as a QF_LIA conjunction without any quantifiers.

        Returns:
            A 2-tuple (explanation_text, smtlib2_correct) where the SMT-LIB2
            snippet demonstrates the correct encoding.
        """
        explanation = (
            "Correct JuGeo encoding: declare one Int variable per dimension.\n"
            "  n0, n1 > 0 asserts shape validity.\n"
            "  For index (i, j): 0 <= i < n0 AND 0 <= j < n1 is QF_LIA.\n"
            "  Total size: total == n0 * n1 (nonlinear, but bounded after instantiation).\n"
            "  This encoding supports: shape compatibility, broadcast, reshape, and\n"
            "  affine index validity — all in QF_LIA after bounding the extent."
        )

        smtlib2 = """\
; CORRECT JuGeo encoding of a 2D tensor A of shape (n0, n1)
(declare-const n0 Int)             ; dimension 0
(declare-const n1 Int)             ; dimension 1
(declare-const total Int)          ; total element count
(declare-fun A (Int Int) Int)      ; 2-argument function for 2D access

; Shape validity
(assert (> n0 0))
(assert (> n1 0))
(assert (= total (* n0 n1)))

; Index validity for concrete index (i, j)
(declare-const i Int)
(declare-const j Int)
(assert (and (>= i 0) (< i n0)
             (>= j 0) (< j n1)))

; Row-major linearisation: linear_idx = i * n1 + j
(declare-const linear_idx Int)
(assert (= linear_idx (+ (* i n1) j)))

(check-sat)  ; sat — and every constraint is meaningful
"""
        return (explanation, smtlib2)

    def why_qf_lia_for_shapes(self) -> str:
        """Explain why QF_LIA is the right theory for tensor shape constraints.

        Returns:
            Multi-paragraph explanation of the decidability argument.
        """
        return (
            "WHY QF_LIA FOR TENSOR SHAPES\n"
            "==============================\n\n"
            "Tensor shape constraints are integer linear arithmetic (ILA) because:\n\n"
            "1. Dimension variables are integers: n0, n1, ..., n_{r-1} ∈ Z.\n\n"
            "2. Shape validity is a conjunction of linear inequalities: n_i > 0.\n\n"
            "3. Index validity 0 ≤ i_k < n_k for each k is a conjunction of\n"
            "   linear inequalities in i_k and n_k — no multiplication needed.\n\n"
            "4. Row-major linearisation i_0*n_1*...*n_{r-1} + ... involves products\n"
            "   of variables, but after substituting concrete dimension values (via\n"
            "   Fourier-Motzkin projection or model instantiation), these become\n"
            "   linear.\n\n"
            "5. Broadcast compatibility: dim_a[k] = dim_b[k] ∨ dim_a[k] = 1 ∨\n"
            "   dim_b[k] = 1 is a finite disjunction of linear equalities —\n"
            "   directly expressible in QF_LIA.\n\n"
            "QF_LIA (quantifier-free linear integer arithmetic) is DECIDABLE in\n"
            "exponential time (by Presburger's theorem, 1929).  Z3's DPLL(T) with\n"
            "the LIA theory solver handles practical instances very efficiently."
        )

    def affine_index_example(self) -> tuple[str, str]:
        """Show how an affine index (i*n + j) is encoded as a Z3 expression.

        Returns:
            A 2-tuple (explanation, smtlib2_snippet).
        """
        explanation = (
            "Affine index encoding:\n"
            "  For a 2D tensor of shape (m, n), the row-major index of (i, j) is:\n"
            "    linear = i * n + j\n"
            "  This is a bilinear expression (i * n), but if n is a concrete parameter\n"
            "  then it is linear in i.  JuGeo treats n as a symbolic Int variable and\n"
            "  uses the QF_NIA fragment for shape-parametric queries, or instantiates\n"
            "  n at a concrete value to reduce to QF_LIA."
        )

        smtlib2 = """\
; Affine index (i*n + j) as a Z3 / SMT-LIB2 expression
(declare-const m Int)   ; number of rows
(declare-const n Int)   ; number of columns
(declare-const i Int)   ; row index
(declare-const j Int)   ; column index
(declare-const lin Int) ; linear offset

(assert (> m 0))
(assert (> n 0))
(assert (and (>= i 0) (< i m)))
(assert (and (>= j 0) (< j n)))

; Row-major linearisation
(assert (= lin (+ (* i n) j)))

; Claim: lin is in [0, m*n - 1]
(assert (not (and (>= lin 0) (< lin (* m n)))))
(check-sat)  ; unsat — the linearisation is always in bounds given valid i, j
"""
        return (explanation, smtlib2)

    def mixed_rank_problem(self) -> str:
        """Explain the challenges of encoding tensors with unknown rank.

        Returns:
            Multi-paragraph explanation string.
        """
        return (
            "THE MIXED-RANK PROBLEM\n"
            "======================\n\n"
            "When tensor rank is not statically known, the encoding faces\n"
            "fundamental challenges:\n\n"
            "1. The number of dimension variables is unknown — we cannot declare\n"
            "   a fixed set of n0, n1, ..., n_{r-1} without knowing r.\n\n"
            "2. Index validity ∀k ∈ [0,r). 0 ≤ idx[k] < dim[k] requires\n"
            "   quantification over k, taking us out of QF_LIA.\n\n"
            "3. Linearisation i_0*dim_1*...*dim_{r-1} + ... involves a variable\n"
            "   number of multiplications, making it non-linear in general.\n\n"
            "JuGeo's solution: treat rank r as a bounded parameter (r ≤ MAX_RANK)\n"
            "and unroll quantification over dimensions into a fixed-size conjunction.\n"
            "This is sound for any fixed upper bound and covers all practical tensors\n"
            "(MAX_RANK = 8 covers all standard deep learning frameworks)."
        )

    def stride_encoding_motivation(self) -> str:
        """Explain why strides matter for non-contiguous tensor views.

        Returns:
            Explanation string covering transpose, slice, and view operations.
        """
        return (
            "WHY STRIDES MATTER FOR ENCODING\n"
            "================================\n\n"
            "A tensor view is characterised not just by its shape but also by its\n"
            "strides — the number of elements to skip in the underlying buffer to\n"
            "advance one step in each dimension.\n\n"
            "Examples of non-contiguous views:\n\n"
            "  Transpose: shape (m, n) with strides (1, m) instead of (n, 1).\n"
            "    → Two elements that differ only in their row index are adjacent\n"
            "      in memory, but two elements that differ only in column are m\n"
            "      apart.\n\n"
            "  Column slice: A[:, 2] has shape (m,) with stride (n,).\n"
            "    → Adjacent elements in the slice are n apart in the buffer.\n\n"
            "  Diagonal: shape (min(m,n),) with stride (n+1,).\n\n"
            "Aliasing analysis requires knowing whether two tensor views overlap\n"
            "in the underlying buffer.  This is expressible in QF_LIA given\n"
            "strides, offsets, and shapes:\n\n"
            "  overlap iff ∃i,j: base_A + sum(stride_A[k]*i[k]) =\n"
            "                     base_B + sum(stride_B[k]*j[k])\n\n"
            "This is a reachability query in integer linear arithmetic."
        )


# ---------------------------------------------------------------------------
# TensorEncodingPrimer
# ---------------------------------------------------------------------------


class TensorEncodingPrimer:
    """Tutorial encoder that demonstrates correct tensor encoding techniques.

    This class provides step-by-step encoding methods for 1D, 2D, and N-D
    tensors, index validity, linearisation, and broadcasting.  It is intended
    as educational documentation and as a reference implementation.

    copilot notes: These methods produce valid Z3 formulas (or stub strings)
    that can be asserted directly into a Z3 Solver.  The production path uses
    TensorExtent.to_z3_formula() which is more general.

    Example::

        primer = TensorEncodingPrimer()
        arr, n, axiom = primer.encode_1d_tensor(10)
        # arr is a Z3 Array, n is a Z3 Int, axiom is a Z3 formula
    """

    def __init__(self) -> None:
        """Initialise the primer with an optional Z3 context."""
        self._var_counter: int = 0

    def _fresh(self, prefix: str = "x") -> str:
        """Generate a fresh variable name.

        Args:
            prefix: Name prefix.

        Returns:
            A fresh name like 'x_0', 'x_1', etc.
        """
        name = f"{prefix}_{self._var_counter}"
        self._var_counter += 1
        return name

    def encode_1d_tensor(
        self, n: int | str
    ) -> tuple[Any, Any, Any]:
        """Encode a 1D tensor of length n.

        Declares:
        - ``arr``: an Array(Int, Int) representing the tensor data.
        - ``len_var``: a Z3 Int variable for the length.
        - ``bounds_axiom``: a Z3 formula asserting len_var > 0.

        Args:
            n: Either a concrete integer length or a string variable name.

        Returns:
            A 3-tuple (arr_var, len_var, bounds_axiom).
        """
        arr_name = self._fresh("arr1d")
        len_name = str(n) if isinstance(n, str) else self._fresh("len")

        if _Z3_AVAILABLE:
            int_sort = _z3.IntSort()
            arr_sort = _z3.ArraySort(int_sort, int_sort)
            arr_var = _z3.Const(arr_name, arr_sort)
            len_var = _z3.Int(len_name)
            if isinstance(n, int):
                bounds_axiom = _z3.And(len_var == n, len_var > 0)
            else:
                bounds_axiom = len_var > 0
        else:
            arr_var = f"Array1D({arr_name})"
            len_var = f"Int({len_name})"
            if isinstance(n, int):
                bounds_axiom = f"And({len_name} == {n}, {len_name} > 0)"
            else:
                bounds_axiom = f"({len_name} > 0)"

        return (arr_var, len_var, bounds_axiom)

    def encode_2d_tensor(
        self, m: int | str, n: int | str
    ) -> tuple[Any, Any, Any, list[Any]]:
        """Encode a 2D tensor of shape (m, n).

        Declares:
        - ``arr``: an Array(Int, Array(Int, Int)) for 2D access.
        - ``m_var``: Z3 Int for the number of rows.
        - ``n_var``: Z3 Int for the number of columns.
        - ``bounds_axioms``: List of Z3 formulas asserting m > 0, n > 0.

        Args:
            m: Row count (concrete int or string variable name).
            n: Column count (concrete int or string variable name).

        Returns:
            4-tuple (arr_var, m_var, n_var, bounds_axioms).
        """
        m_name = str(m) if isinstance(m, str) else self._fresh("m")
        n_name = str(n) if isinstance(n, str) else self._fresh("n")
        arr_name = self._fresh("arr2d")

        if _Z3_AVAILABLE:
            int_sort = _z3.IntSort()
            inner_sort = _z3.ArraySort(int_sort, int_sort)
            outer_sort = _z3.ArraySort(int_sort, inner_sort)
            arr_var = _z3.Const(arr_name, outer_sort)
            m_var = _z3.Int(m_name)
            n_var = _z3.Int(n_name)
            axioms: list[Any] = [m_var > 0, n_var > 0]
            if isinstance(m, int):
                axioms.append(m_var == m)
            if isinstance(n, int):
                axioms.append(n_var == n)
        else:
            arr_var = f"Array2D({arr_name})"
            m_var = f"Int({m_name})"
            n_var = f"Int({n_name})"
            axioms = [f"({m_name} > 0)", f"({n_name} > 0)"]

        return (arr_var, m_var, n_var, axioms)

    def encode_nd_tensor(
        self, shape_vars: list[Any], elem_sort: Any
    ) -> Any:
        """Encode an N-dimensional tensor using nested array sorts.

        For rank r, creates a nested sort:
        ``Array(Int, Array(Int, ... Array(Int, elem_sort) ...))``.

        Args:
            shape_vars: List of Z3 Int variables (or stubs) for each dimension.
            elem_sort: The element sort (e.g., IntSort() or RealSort()).

        Returns:
            A Z3 constant of the nested array sort, or a stub string.
        """
        rank = len(shape_vars)
        if rank == 0:
            return f"Scalar({elem_sort})"

        if _Z3_AVAILABLE:
            int_sort = _z3.IntSort()
            current_sort = elem_sort
            for _ in range(rank):
                current_sort = _z3.ArraySort(int_sort, current_sort)
            arr_name = self._fresh(f"arr{rank}d")
            return _z3.Const(arr_name, current_sort)
        else:
            layers = " -> ".join(["Int"] * rank + [str(elem_sort)])
            return f"NestedArray({layers})"

    def encode_index_validity(
        self, idx_vars: list[Any], shape_vars: list[Any]
    ) -> Any:
        """Encode the conjunction asserting each index is within its dimension bound.

        Produces: ``∧_k (0 ≤ idx_vars[k] ∧ idx_vars[k] < shape_vars[k])``

        Args:
            idx_vars: Z3 Int variables (or stubs) for each index component.
            shape_vars: Z3 Int variables (or stubs) for each dimension.

        Returns:
            Z3 And formula (or stub string).
        """
        if len(idx_vars) != len(shape_vars):
            raise ValueError(
                f"idx_vars length {len(idx_vars)} != shape_vars length {len(shape_vars)}"
            )
        conjuncts: list[Any] = []
        for idx, dim in zip(idx_vars, shape_vars):
            if _Z3_AVAILABLE:
                conjuncts.append(0 <= idx)
                conjuncts.append(idx < dim)
            else:
                conjuncts.append(f"(0 <= {idx})")
                conjuncts.append(f"({idx} < {dim})")
        return _z3_and(*conjuncts) if conjuncts else (True if _Z3_AVAILABLE else "True")

    def encode_row_major_linearization(
        self, idx_vars: list[Any], shape_vars: list[Any]
    ) -> Any:
        """Encode row-major (C-order) index linearisation.

        Computes: ``idx[0]*dim[1]*...*dim[r-1] + idx[1]*dim[2]*...*dim[r-1] + ... + idx[r-1]``

        This encoding uses only multiplication and addition, which is in QF_NIA
        (non-linear integer arithmetic) when shapes are symbolic.  After
        instantiating concrete shape values the result reduces to QF_LIA.

        Args:
            idx_vars: List of Z3 Int variables for the index components.
            shape_vars: List of Z3 Int variables for the dimension sizes.

        Returns:
            Z3 arithmetic expression for the linear offset (or stub string).
        """
        r = len(idx_vars)
        if r != len(shape_vars):
            raise ValueError("idx_vars and shape_vars must have the same length")
        if r == 0:
            return _z3.IntVal(0) if _Z3_AVAILABLE else "0"

        # Build result = idx[0]*prod(shape[1:]) + idx[1]*prod(shape[2:]) + ... + idx[r-1]
        result: Any = idx_vars[0]
        for k in range(1, r):
            # multiply by shape[k]
            if _Z3_AVAILABLE:
                result = result * shape_vars[k]
            else:
                result = f"({result} * {shape_vars[k]})"
            # add idx[k]
            if _Z3_AVAILABLE:
                result = result + idx_vars[k]
            else:
                result = f"({result} + {idx_vars[k]})"
        return result

    def encode_column_major_linearization(
        self, idx_vars: list[Any], shape_vars: list[Any]
    ) -> Any:
        """Encode column-major (Fortran-order) index linearisation.

        Computes: ``idx[0] + dim[0]*idx[1] + dim[0]*dim[1]*idx[2] + ...``

        Args:
            idx_vars: List of Z3 Int variables for the index components.
            shape_vars: List of Z3 Int variables for the dimension sizes.

        Returns:
            Z3 arithmetic expression for the linear offset (or stub string).
        """
        r = len(idx_vars)
        if r != len(shape_vars):
            raise ValueError("idx_vars and shape_vars must have the same length")
        if r == 0:
            return _z3.IntVal(0) if _Z3_AVAILABLE else "0"

        # result = idx[r-1]
        # for k = r-2 down to 0: result = result * shape[k] + idx[k]
        result: Any = idx_vars[-1]
        for k in range(r - 2, -1, -1):
            if _Z3_AVAILABLE:
                result = result * shape_vars[k]
                result = result + idx_vars[k]
            else:
                result = f"(({result} * {shape_vars[k]}) + {idx_vars[k]})"
        return result

    def encode_broadcast_semantics(
        self, shape_a: list[Any], shape_b: list[Any]
    ) -> Any:
        """Encode NumPy broadcast compatibility between two shapes.

        Pads the shorter shape on the left with 1s, then for each aligned
        dimension k asserts: ``dim_a[k] = dim_b[k] ∨ dim_a[k] = 1 ∨ dim_b[k] = 1``.

        Args:
            shape_a: List of Z3 Int expressions for tensor A's dimensions.
            shape_b: List of Z3 Int expressions for tensor B's dimensions.

        Returns:
            Z3 And formula (or stub string) encoding broadcast compatibility.
        """
        n = max(len(shape_a), len(shape_b))
        one: Any = _z3.IntVal(1) if _Z3_AVAILABLE else "1"
        a_padded = ([one] * (n - len(shape_a))) + list(shape_a)
        b_padded = ([one] * (n - len(shape_b))) + list(shape_b)

        conjuncts: list[Any] = []
        for a_dim, b_dim in zip(a_padded, b_padded):
            if _Z3_AVAILABLE:
                compat = _z3.Or(a_dim == b_dim, a_dim == one, b_dim == one)
            else:
                compat = f"({a_dim} == {b_dim} Or {a_dim} == 1 Or {b_dim} == 1)"
            conjuncts.append(compat)

        return _z3_and(*conjuncts) if conjuncts else (True if _Z3_AVAILABLE else "True")

    def copilot_explain_encoding_choice(self, tensor_rank: int) -> str:
        """Return an encoding recommendation based on tensor rank.

        For each rank value, explains which encoding pattern is most appropriate
        and why.

        Args:
            tensor_rank: The rank (number of dimensions) of the tensor.

        Returns:
            Recommendation string.
        """
        if tensor_rank == 0:
            return (
                "Rank 0 (scalar): No encoding needed. A scalar is a single Z3 Int or Real "
                "variable.  Shape is trivially valid."
            )
        if tensor_rank == 1:
            return (
                "Rank 1 (vector): Use encode_1d_tensor(n). Declare Array(Int, Int) and a "
                "single Int length variable n > 0. Index validity: 0 <= i < n (QF_LIA)."
            )
        if tensor_rank == 2:
            return (
                "Rank 2 (matrix): Use encode_2d_tensor(m, n). Declare two Int variables "
                "m > 0, n > 0. For index (i, j): QF_LIA bounds check. Row-major linear "
                "offset: i*n + j (nonlinear in m, n — instantiate or use QF_NIA)."
            )
        if tensor_rank <= 4:
            return (
                f"Rank {tensor_rank}: Declare {tensor_rank} Int dimension variables. "
                "Use encode_nd_tensor() with a nested array sort. Index validity is a "
                f"{2 * tensor_rank}-conjunct QF_LIA formula. Linearisation is QF_NIA "
                "when shapes are symbolic; reduce to QF_LIA by bounding shape values."
            )
        return (
            f"Rank {tensor_rank} (high-rank tensor): Consider encoding the shape as a "
            "Z3 Array(Int, Int) mapping dimension index to size, with a quantified bound. "
            "Use MAX_RANK = 8 as an upper bound and unroll the dimension quantifier. "
            "This stays in QF_LIA after unrolling and is decidable."
        )


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def why_arrays_of_arrays(rank: int) -> str:
    """Explain the arrays-of-arrays encoding for tensors of a given rank.

    For rank r, a tensor is encoded as an r-times-nested array type:
    ``Array(Int, Array(Int, ... Array(Int, ElemSort) ...))``

    This preserves dimension structure and allows per-dimension bounds
    checking at the type level.

    Args:
        rank: The tensor rank (number of dimensions).

    Returns:
        Multi-paragraph explanation string.
    """
    if rank <= 0:
        return "Rank 0: scalar — no array encoding needed."

    layers = []
    sort = "ElemSort"
    for k in range(rank):
        sort = f"Array(Int_{k}, {sort})"
        layers.append(sort)

    return (
        f"WHY ARRAYS-OF-ARRAYS FOR RANK-{rank} TENSORS\n"
        + "=" * 50 + "\n\n"
        "The arrays-of-arrays encoding wraps the element sort in r layers of\n"
        f"array sorts.  For rank {rank}, the type hierarchy is:\n\n"
        + "\n".join(f"  Layer {k+1}: {s}" for k, s in enumerate(layers))
        + "\n\n"
        "Advantages:\n"
        "  1. Each 'dimension access' corresponds to one array select operation.\n"
        "  2. Per-dimension bounds are independent Int constraints.\n"
        "  3. The Z3 array theory (read/write axioms) applies at each layer.\n"
        "  4. Row-major linearisation is derived, not primitive.\n\n"
        "Disadvantages:\n"
        "  1. The sort is complex; Z3's array theory solver works harder.\n"
        "  2. For high ranks (> 4), a single 1D array with an explicit\n"
        "     linearisation formula may be more efficient.\n"
        "  3. Broadcasting and reshape require cross-layer reasoning."
    )


def qf_lia_decidability_argument() -> str:
    """Formal argument for the decidability of tensor shape constraints in QF_LIA.

    Returns:
        Multi-paragraph formal argument string.
    """
    return (
        "DECIDABILITY OF TENSOR SHAPE CONSTRAINTS IN QF_LIA\n"
        "====================================================\n\n"
        "Theorem (Presburger 1929): The first-order theory of (Z, +, <) — Presburger\n"
        "arithmetic — is decidable.  QF_LIA (quantifier-free linear integer arithmetic)\n"
        "is a fragment of Presburger arithmetic and is therefore decidable.\n\n"
        "Claim: All tensor shape constraints in JuGeo Chapter 30 are in QF_LIA.\n\n"
        "Proof sketch:\n"
        "  1. Dimension variables n_0, ..., n_{r-1} range over Z.\n"
        "  2. Shape validity n_i > 0 is a linear inequality.\n"
        "  3. Index validity 0 ≤ i_k < n_k is a conjunction of linear inequalities.\n"
        "  4. Broadcast compatibility: each dim condition is a disjunction of\n"
        "     linear equalities (a = b, a = 1, b = 1) — expressible in QF_LIA.\n"
        "  5. Reshape validity: total = n_0 * n_1 * ... * n_{r-1} is nonlinear,\n"
        "     but after fixing the shape to a concrete tuple (via model instantiation\n"
        "     or Fourier-Motzkin projection), the product becomes a linear constant.\n"
        "  6. Affine index legality M*d ≻ 0 for integer vectors d is a finite\n"
        "     conjunction of linear inequalities over integer variables — QF_LIA. □\n\n"
        "Complexity: QF_LIA is NP-complete.  In practice, Z3's DPLL(T) with the\n"
        "built-in LIA solver solves typical tensor shape queries in milliseconds."
    )


def affine_index_normal_form(
    coeffs: list[int], vars_: list[str], const: int
) -> str:
    """Pretty-print an affine index expression in normal form.

    Produces the string representation of the affine expression:
    ``coeffs[0]*vars_[0] + coeffs[1]*vars_[1] + ... + const``

    Negative coefficients are handled with subtraction.  Zero coefficients
    are omitted.  The constant term is omitted if zero.

    Args:
        coeffs: List of integer coefficients, one per variable.
        vars_: List of variable name strings.
        const: Integer constant term.

    Returns:
        Normalised affine expression string.

    Example::

        affine_index_normal_form([1, 4, -2], ['i', 'j', 'k'], 3)
        # Returns "i + 4*j - 2*k + 3"
    """
    if len(coeffs) != len(vars_):
        raise ValueError(f"coeffs ({len(coeffs)}) and vars_ ({len(vars_)}) must match")

    terms: list[str] = []
    for c, v in zip(coeffs, vars_):
        if c == 0:
            continue
        if c == 1:
            terms.append(v)
        elif c == -1:
            terms.append(f"-{v}")
        else:
            terms.append(f"{c}*{v}")

    if const != 0:
        terms.append(str(const))

    if not terms:
        return "0"

    # Join with " + " but replace " + -" with " - "
    result = " + ".join(terms)
    result = result.replace(" + -", " - ")
    return result
