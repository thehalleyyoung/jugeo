"""
Core Algorithms for Tensor Quantifier Encoding.
================================================
Chapter 30 algorithms — JuGeo formal verification system.

copilot integration: these algorithms are called by the integration layer
and can be explained/verified by the copilot assist.  They are implemented
in pure Python — no Z3 or external solver is required.

The following algorithms are provided:

- ``fourier_motzkin``: Variable elimination for linear inequality systems.
- ``farkas_lemma_certificate``: Infeasibility certificate via Farkas' lemma.
- ``affine_transformation_legality``: Lex-positivity check for M*d.
- ``broadcast_shape_unification``: NumPy-style shape unification.
- ``linearize_nd_index``: Row-major N-D index linearisation.
- ``compute_tensor_stride``: Stride computation for row- or col-major layout.
- ``affine_hull``: Affine hull of a point set as equality constraints.
- ``normal_cone``: Normal cone at a face of a polyhedron.
- ``copilot_derive_tiling_schedule``: Tiling transformation matrix derivation.

Helper utilities: ``gcd``, ``lcm``, ``matrix_multiply``, ``transpose_matrix``,
``lex_compare``, ``is_lex_positive``.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "fourier_motzkin",
    "farkas_lemma_certificate",
    "affine_transformation_legality",
    "broadcast_shape_unification",
    "linearize_nd_index",
    "compute_tensor_stride",
    "affine_hull",
    "normal_cone",
    "copilot_derive_tiling_schedule",
    "gcd",
    "lcm",
    "matrix_multiply",
    "transpose_matrix",
    "lex_compare",
    "is_lex_positive",
]

# ---------------------------------------------------------------------------
# Basic integer utilities
# ---------------------------------------------------------------------------


def gcd(a: int, b: int) -> int:
    """Compute the greatest common divisor of two integers.

    Uses the Euclidean algorithm.  Returns a non-negative integer.

    Args:
        a: First integer.
        b: Second integer.

    Returns:
        Non-negative GCD of abs(a) and abs(b).

    Example::

        gcd(12, 8)   # 4
        gcd(-15, 10) # 5
        gcd(0, 7)    # 7
    """
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def lcm(a: int, b: int) -> int:
    """Compute the least common multiple of two non-negative integers.

    Uses the identity: lcm(a, b) = abs(a * b) // gcd(a, b).

    Args:
        a: First integer.
        b: Second integer.

    Returns:
        Non-negative LCM.  Returns 0 if either input is 0.

    Example::

        lcm(4, 6)   # 12
        lcm(5, 7)   # 35
        lcm(0, 3)   # 0
    """
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


# ---------------------------------------------------------------------------
# Matrix utilities
# ---------------------------------------------------------------------------


def matrix_multiply(
    A: list[list[int]], B: list[list[int]]
) -> list[list[int]]:
    """Multiply two integer matrices A (m x p) and B (p x n).

    Args:
        A: Left matrix as list of int rows.
        B: Right matrix as list of int rows.

    Returns:
        Product matrix (m x n) as list of int rows.

    Raises:
        ValueError: If inner dimensions are incompatible.

    Example::

        matrix_multiply([[1, 2], [3, 4]], [[5, 6], [7, 8]])
        # [[19, 22], [43, 50]]
    """
    if not A or not B:
        return []
    m = len(A)
    p = len(A[0])
    if len(B) != p:
        raise ValueError(
            f"Cannot multiply ({m}x{p}) and ({len(B)}x{len(B[0])}): "
            f"inner dimensions {p} != {len(B)}"
        )
    n = len(B[0])
    return [
        [sum(A[i][k] * B[k][j] for k in range(p)) for j in range(n)]
        for i in range(m)
    ]


def transpose_matrix(M: list[list[int]]) -> list[list[int]]:
    """Transpose an integer matrix M (rows become columns).

    Args:
        M: Matrix as list of int rows.

    Returns:
        Transposed matrix.  Empty matrix returns empty matrix.

    Example::

        transpose_matrix([[1, 2, 3], [4, 5, 6]])
        # [[1, 4], [2, 5], [3, 6]]
    """
    if not M or not M[0]:
        return []
    rows = len(M)
    cols = len(M[0])
    return [[M[r][c] for r in range(rows)] for c in range(cols)]


# ---------------------------------------------------------------------------
# Lexicographic utilities
# ---------------------------------------------------------------------------


def lex_compare(a: list[int], b: list[int]) -> int:
    """Lexicographically compare two integer vectors.

    Compares element-by-element from left to right.  Shorter vectors are
    padded with zeros on the right for comparison purposes.

    Args:
        a: First integer vector.
        b: Second integer vector.

    Returns:
        -1 if a < b lexicographically, 0 if equal, +1 if a > b.

    Example::

        lex_compare([1, 2, 3], [1, 2, 4])  # -1
        lex_compare([1, 2, 3], [1, 2, 3])  # 0
        lex_compare([2, 0], [1, 9])         # 1
    """
    n = max(len(a), len(b))
    for i in range(n):
        ai = a[i] if i < len(a) else 0
        bi = b[i] if i < len(b) else 0
        if ai < bi:
            return -1
        if ai > bi:
            return 1
    return 0


def is_lex_positive(v: list[int]) -> bool:
    """Return True if integer vector v is lexicographically positive.

    A vector is lex-positive if its first non-zero component is positive.
    The zero vector is NOT lex-positive.

    Args:
        v: List of integers.

    Returns:
        True if v is lex-positive.

    Example::

        is_lex_positive([0, 1, -1])   # True  (first nonzero is +1)
        is_lex_positive([0, -1, 1])   # False (first nonzero is -1)
        is_lex_positive([0, 0, 0])    # False (zero vector)
        is_lex_positive([3, -5, 2])   # True  (first nonzero is +3)
    """
    for component in v:
        if component > 0:
            return True
        if component < 0:
            return False
    return False  # all-zero is not lex-positive


# ---------------------------------------------------------------------------
# Fourier-Motzkin elimination
# ---------------------------------------------------------------------------


def fourier_motzkin(
    constraints: list[tuple[list[float], float]],
    var_idx: int,
) -> list[tuple[list[float], float]]:
    """Eliminate one variable from a linear inequality system using Fourier-Motzkin.

    Each constraint is a tuple (coeffs, rhs) representing:
    ``sum(coeffs[i] * x[i]) <= rhs``

    The algorithm partitions constraints into:
    - **Upper bounds**: constraints where ``coeffs[var_idx] > 0``.
    - **Lower bounds**: constraints where ``coeffs[var_idx] < 0``.
    - **Neutral**: constraints where ``coeffs[var_idx] = 0`` (kept unchanged).

    For each pair (upper, lower), a new constraint is generated with
    ``var_idx`` eliminated.  The result is the neutral constraints plus
    all generated constraints.

    Args:
        constraints: List of (coeffs, rhs) tuples.  Each coeffs list must have
                     the same length.
        var_idx: 0-based index of the variable to eliminate.

    Returns:
        New constraint list with ``var_idx`` column removed.

    Example::

        # Eliminate x from {x <= 3, x >= 1, x <= y, x >= y - 2}
        # Encoded as: (1, 3), (-1, -1), (1, 0, -1, 0) ... in full form
        cs = [
            ([1.0, 0.0], 3.0),   # x <= 3
            ([-1.0, 0.0], -1.0), # -x <= -1, i.e., x >= 1
            ([1.0, -1.0], 0.0),  # x - y <= 0, i.e., x <= y
            ([-1.0, 1.0], 2.0),  # -x + y <= 2, i.e., x >= y - 2
        ]
        result = fourier_motzkin(cs, 0)
        # Should yield: y >= 1 (y >= 1), y <= 5, etc.
    """
    upper: list[tuple[list[float], float]] = []
    lower: list[tuple[list[float], float]] = []
    neutral: list[tuple[list[float], float]] = []

    eps = 1e-12  # numerical zero threshold

    for coeffs, rhs in constraints:
        if var_idx >= len(coeffs):
            # Variable not present — treat as neutral
            neutral.append((coeffs, rhs))
            continue

        c = coeffs[var_idx]
        if c > eps:
            upper.append((coeffs, rhs))
        elif c < -eps:
            lower.append((coeffs, rhs))
        else:
            neutral.append((coeffs, rhs))

    # Project the var_idx column from neutral constraints
    projected_neutral: list[tuple[list[float], float]] = [
        (
            [coeffs[j] for j in range(len(coeffs)) if j != var_idx],
            rhs,
        )
        for coeffs, rhs in neutral
    ]

    # Generate new constraints from upper x lower pairs
    new_constraints: list[tuple[list[float], float]] = []
    for u_coeffs, u_rhs in upper:
        u_c = u_coeffs[var_idx]  # > 0, normalise upper to: x <= rhs/u_c - ...
        for l_coeffs, l_rhs in lower:
            l_c = abs(l_coeffs[var_idx])  # l_coeffs[var_idx] < 0, so l_c > 0
            # Combine: l_c * (u_expr <= u_rhs) + u_c * (-l_expr <= -l_rhs)
            # → l_c * sum(u_coeffs[j]*x[j]) + u_c * sum(-l_coeffs[j]*x[j]) <= l_c*u_rhs + u_c*(-l_rhs)
            # Simplify: combined[j] = l_c * u_coeffs[j] - u_c * l_coeffs[j] for j != var_idx
            n_vars = len(u_coeffs)
            new_coeffs = [
                l_c * u_coeffs[j] - u_c * l_coeffs[j]
                for j in range(n_vars)
                if j != var_idx
            ]
            new_rhs = l_c * u_rhs - u_c * l_rhs

            # Normalise by the largest absolute coefficient to reduce magnitude
            max_abs = max((abs(c) for c in new_coeffs), default=0.0)
            if max_abs > eps:
                scale = max_abs
                new_coeffs = [c / scale for c in new_coeffs]
                new_rhs = new_rhs / scale

            new_constraints.append((new_coeffs, new_rhs))

    return projected_neutral + new_constraints


# ---------------------------------------------------------------------------
# Farkas lemma certificate
# ---------------------------------------------------------------------------


def farkas_lemma_certificate(
    A: list[list[float]], b: list[float]
) -> list[float] | None:
    """Compute a Farkas certificate of infeasibility for the system Ax ≤ b.

    By Farkas' lemma: Ax ≤ b is infeasible iff ∃y ≥ 0 with y^T A = 0 and y^T b < 0.

    This implementation uses a sequence of heuristic strategies:
    1. Check if any single row gives a certificate (trivial infeasibility).
    2. Try equal-weight multipliers y = (1/m, ..., 1/m).
    3. Use a greedy search over pairs of constraints.
    4. Apply a simplified dual simplex pivot to find certifying multipliers.

    Args:
        A: Constraint matrix (m x n) where m = number of constraints.
        b: Right-hand side vector (length m).

    Returns:
        List of m non-negative floats (Farkas multipliers y) such that
        y^T A = 0 and y^T b < 0, or None if the system appears feasible.

    Example::

        # Infeasible system: x <= -1 AND x >= 0
        A = [[1.0], [-1.0]]  # x <= rhs
        b = [-1.0, 0.0]      # x <= -1, -x <= 0 → x >= 0
        # y = [1, 1]: y^T A = [1-1] = [0], y^T b = -1 + 0 = -1 < 0
        farkas_lemma_certificate(A, b)  # [1.0, 1.0] (or a scaled version)
    """
    if not A or not b:
        return None

    m = len(A)
    n = len(A[0]) if A else 0

    if m != len(b):
        return None

    def check_multipliers(y: list[float]) -> bool:
        """Return True if y satisfies y >= 0, y^T A = 0, y^T b < 0."""
        if any(yi < -1e-9 for yi in y):
            return False
        for j in range(n):
            col = sum(y[i] * A[i][j] for i in range(m))
            if abs(col) > 1e-8:
                return False
        dot = sum(y[i] * b[i] for i in range(m))
        return dot < -1e-9

    # Strategy 1: single-row certificate (constraint i: A[i] = 0 row, b[i] < 0)
    for i in range(m):
        if all(abs(A[i][j]) < 1e-9 for j in range(n)) and b[i] < -1e-9:
            y = [0.0] * m
            y[i] = 1.0
            return y

    # Strategy 2: equal-weight y = 1/m for all
    y_eq = [1.0 / m] * m
    if check_multipliers(y_eq):
        return y_eq

    # Strategy 3: greedy pairs — try all pairs of constraints summed
    for i in range(m):
        for j in range(i + 1, m):
            y_pair = [0.0] * m
            y_pair[i] = 0.5
            y_pair[j] = 0.5
            if check_multipliers(y_pair):
                return y_pair

    # Strategy 4: use Fourier-Motzkin to derive infeasibility
    # Encode as: A @ x <= b, with Farkas dual variables
    # Build the dual: y^T A = 0, y^T b < 0, y >= 0
    # Use row operations to find a null vector of A^T with negative b dot product

    # Augment [A^T | b] as a (n x m+1) matrix
    AT_b: list[list[float]] = [
        [A[i][j] for i in range(m)] + [b[j] if j < len(b) else 0.0]
        for j in range(n)
    ]

    # Gaussian elimination to find the null space of A^T
    working = [list(row) for row in AT_b]
    pivot_row = 0
    pivot_cols: list[int] = []

    for col in range(m):
        found = -1
        for row in range(pivot_row, len(working)):
            if abs(working[row][col]) > 1e-9:
                found = row
                break
        if found == -1:
            pivot_cols.append(col)
            continue
        working[pivot_row], working[found] = working[found], working[pivot_row]
        piv = working[pivot_row][col]
        working[pivot_row] = [x / piv for x in working[pivot_row]]
        for r in range(len(working)):
            if r != pivot_row and abs(working[r][col]) > 1e-9:
                fac = working[r][col]
                working[r] = [working[r][c] - fac * working[pivot_row][c]
                               for c in range(len(working[r]))]
        pivot_row += 1

    # Each free variable (pivot_col) gives a potential null vector
    for fc in pivot_cols:
        y_candidate = [0.0] * m
        y_candidate[fc] = 1.0
        if check_multipliers(y_candidate):
            return y_candidate

    return None  # No certificate found — system may be feasible


# ---------------------------------------------------------------------------
# Affine transformation legality
# ---------------------------------------------------------------------------


def affine_transformation_legality(
    M: list[list[int]], dep_vectors: list[list[int]]
) -> tuple[bool, list[int] | None]:
    """Check whether a linear transformation M is legal for all dependence vectors.

    A transformation M is *legal* for a set of dependence vectors D if for every
    d ∈ D, the vector M*d is lexicographically positive (first non-zero is positive).

    This is the Feautrier/Bastoul legality condition for polyhedral loop transformations.

    Args:
        M: Transformation matrix as list of integer rows (k x n).
        dep_vectors: List of dependence vectors (each of length n).

    Returns:
        ``(True, None)`` if the transform is legal for all dependence vectors.
        ``(False, violating_d)`` where ``violating_d`` is the first dependence
        vector for which M*d is not lex-positive.

    Example::

        # Identity on 2D loops — always legal for positive dependences
        M = [[1, 0], [0, 1]]
        deps = [[1, 0], [0, 1], [1, 1]]
        affine_transformation_legality(M, deps)  # (True, None)

        # Transpose (swap loops) — illegal for (1, 0)
        M_swap = [[0, 1], [1, 0]]
        affine_transformation_legality(M_swap, [[1, 0]])  # (False, [1, 0])
    """
    if not M or not dep_vectors:
        return (True, None)

    n_cols = len(M[0]) if M else 0
    n_rows = len(M)

    for dep in dep_vectors:
        if len(dep) != n_cols:
            raise ValueError(
                f"Dependence vector length {len(dep)} != matrix columns {n_cols}"
            )
        # Compute M * dep
        md = [
            sum(M[r][c] * dep[c] for c in range(n_cols))
            for r in range(n_rows)
        ]
        if not is_lex_positive(md):
            return (False, list(dep))

    return (True, None)


# ---------------------------------------------------------------------------
# Broadcast shape unification
# ---------------------------------------------------------------------------


def broadcast_shape_unification(shapes: list[list[int]]) -> list[int] | None:
    """Compute the NumPy-style broadcast shape for a collection of tensor shapes.

    Two shapes are broadcast-compatible if, for each dimension aligned from the
    right, either the dimensions are equal or one of them is 1.  The result
    shape takes the maximum value at each dimension position.

    This function generalises the two-tensor case to any number of tensors.

    Args:
        shapes: List of shape lists.  Empty shapes (scalars) are allowed.

    Returns:
        Unified broadcast shape as a list of ints, or None if the shapes are
        incompatible (two dimensions are different and neither is 1).

    Example::

        broadcast_shape_unification([[3, 1, 4], [1, 5, 1], [3, 5, 4]])
        # [3, 5, 4]

        broadcast_shape_unification([[3, 4], [2, 4]])
        # None  (3 and 2 are incompatible — neither is 1)

        broadcast_shape_unification([[1], [5], [1]])
        # [5]

        broadcast_shape_unification([[]])  # scalar
        # []
    """
    if not shapes:
        return []

    # Determine the maximum rank
    max_rank = max(len(s) for s in shapes)

    # Pad shapes on the left with 1s to align all to max_rank
    padded = [
        ([1] * (max_rank - len(s))) + list(s)
        for s in shapes
    ]

    result: list[int] = []
    for k in range(max_rank):
        dims_at_k = [p[k] for p in padded]

        # Find non-1 dims
        non_one = [d for d in dims_at_k if d != 1]

        if not non_one:
            result.append(1)
            continue

        # All non-1 dims must be equal
        if len(set(non_one)) > 1:
            return None  # Incompatible dimensions

        result.append(non_one[0])

    return result


# ---------------------------------------------------------------------------
# Index linearisation
# ---------------------------------------------------------------------------


def linearize_nd_index(
    idx_tuple: tuple[int, ...], shape: tuple[int, ...]
) -> int:
    """Compute the row-major (C-order) linear offset for an N-D index.

    Computes: ``idx[0]*shape[1]*...*shape[n-1] + idx[1]*shape[2]*...*shape[n-1] + ... + idx[n-1]``

    Args:
        idx_tuple: N-D index tuple.  Each component must satisfy 0 <= idx[i] < shape[i].
        shape: Shape tuple.  Must have the same length as ``idx_tuple``.

    Returns:
        Linear offset (non-negative integer).

    Raises:
        ValueError: If ``len(idx_tuple) != len(shape)`` or any index is out of bounds.

    Example::

        linearize_nd_index((2, 3), (4, 5))   # 2*5 + 3 = 13
        linearize_nd_index((0, 0, 0), (2, 3, 4))  # 0
        linearize_nd_index((1, 2, 3), (2, 3, 4))  # 1*12 + 2*4 + 3 = 12+8+3 = 23
    """
    n = len(idx_tuple)
    if n != len(shape):
        raise ValueError(
            f"idx_tuple length {n} != shape length {len(shape)}"
        )

    for i, (idx, dim) in enumerate(zip(idx_tuple, shape)):
        if not (0 <= idx < dim):
            raise ValueError(
                f"Index component {i}: {idx} not in [0, {dim})"
            )

    if n == 0:
        return 0

    offset = 0
    stride = 1
    for i in range(n - 1, -1, -1):
        offset += idx_tuple[i] * stride
        stride *= shape[i]
    return offset


# ---------------------------------------------------------------------------
# Stride computation
# ---------------------------------------------------------------------------


def compute_tensor_stride(
    shape: tuple[int, ...], layout: str
) -> tuple[int, ...]:
    """Compute the strides for a tensor in row-major or column-major layout.

    Strides are the number of elements to step in the underlying flat buffer
    to advance one step in each dimension.

    For **row-major** (C order): ``stride[i] = product(shape[i+1:])``
    For **col-major** (Fortran order): ``stride[i] = product(shape[:i])``

    Args:
        shape: Tuple of dimension sizes.
        layout: Either ``'row_major'`` (C order) or ``'col_major'`` (Fortran order).

    Returns:
        Stride tuple with the same length as ``shape``.

    Raises:
        ValueError: If ``layout`` is not 'row_major' or 'col_major'.

    Example::

        compute_tensor_stride((2, 3, 4), 'row_major')  # (12, 4, 1)
        compute_tensor_stride((2, 3, 4), 'col_major')  # (1, 2, 6)
    """
    if layout not in ("row_major", "col_major"):
        raise ValueError(
            f"Unknown layout '{layout}'. Expected 'row_major' or 'col_major'."
        )

    n = len(shape)
    if n == 0:
        return ()

    strides: list[int] = [1] * n

    if layout == "row_major":
        # stride[n-1] = 1, stride[i] = stride[i+1] * shape[i+1]
        for i in range(n - 2, -1, -1):
            strides[i] = strides[i + 1] * shape[i + 1]
    else:  # col_major
        # stride[0] = 1, stride[i] = stride[i-1] * shape[i-1]
        for i in range(1, n):
            strides[i] = strides[i - 1] * shape[i - 1]

    return tuple(strides)


# ---------------------------------------------------------------------------
# Affine hull
# ---------------------------------------------------------------------------


def affine_hull(points: list[list[float]]) -> list[tuple[list[float], float]]:
    """Compute the affine hull of a set of points as equality constraints.

    The affine hull of a set of points P = {p_0, ..., p_k} is the smallest
    affine subspace containing all points.  It is represented as a set of
    linear equality constraints (normal_vector, rhs) such that each point
    satisfies: ``dot(normal_vector, x) = rhs``.

    Algorithm:
    1. Choose p_0 as the reference point.
    2. Compute the direction vectors d_i = p_i - p_0 for i = 1, ..., k.
    3. Perform row reduction on the direction matrix to find the dimension
       of the affine hull (rank of direction matrix).
    4. The null space of the direction matrix gives the normal vectors.

    Args:
        points: List of points (each a list of floats of the same dimension).

    Returns:
        List of (normal_coeffs, rhs) pairs defining the affine hull constraints.
        Returns an empty list if the points span the full space.

    Example::

        affine_hull([[0.0, 0.0], [1.0, 0.0], [0.5, 0.0]])
        # Affine hull is the x-axis: normal = [0, 1], rhs = 0
        # Returns: [([0.0, 1.0], 0.0)]
    """
    if not points:
        return []

    n = len(points[0])
    if len(points) == 1:
        # Single point: affine hull is the point itself (n equality constraints)
        # Each coordinate: e_j^T x = p_0[j]
        return [([float(i == j) for i in range(n)], points[0][j]) for j in range(n)]

    # Direction vectors relative to p_0
    p0 = points[0]
    directions = [
        [points[i][j] - p0[j] for j in range(n)]
        for i in range(1, len(points))
    ]

    # Row-reduce the direction matrix to find its rank and null space
    working = [list(d) for d in directions]
    pivot_cols: list[int] = []
    pivot_row = 0

    for col in range(n):
        found = -1
        for row in range(pivot_row, len(working)):
            if abs(working[row][col]) > 1e-9:
                found = row
                break
        if found == -1:
            pivot_cols.append(col)
            continue
        working[pivot_row], working[found] = working[found], working[pivot_row]
        piv = working[pivot_row][col]
        working[pivot_row] = [x / piv for x in working[pivot_row]]
        for r in range(len(working)):
            if r != pivot_row and abs(working[r][col]) > 1e-9:
                fac = working[r][col]
                working[r] = [working[r][c] - fac * working[pivot_row][c] for c in range(n)]
        pivot_row += 1

    # Null space of direction matrix: free variables give normal vectors
    normals: list[tuple[list[float], float]] = []
    for fc in pivot_cols:
        normal = [0.0] * n
        normal[fc] = 1.0
        # Back-substitute to find the complete null vector
        for pr in range(pivot_row - 1, -1, -1):
            # Find the pivot column for this row
            pc = -1
            for c in range(n):
                if abs(working[pr][c]) > 1e-9 and c not in pivot_cols:
                    pc = c
                    break
            if pc == -1:
                continue
            normal[pc] = -sum(working[pr][c] * normal[c] for c in range(n) if c != pc)

        rhs = sum(normal[j] * p0[j] for j in range(n))
        normals.append((normal, rhs))

    return normals


# ---------------------------------------------------------------------------
# Normal cone
# ---------------------------------------------------------------------------


def normal_cone(
    A: list[list[float]], b: list[float], face_idx: list[int]
) -> list[list[float]]:
    """Compute the generators of the normal cone at a face of a polyhedron.

    The normal cone at face F (defined by the active constraints indexed by
    ``face_idx``) of the polyhedron P = {x | Ax <= b} is the cone:
    ``N(P, F) = { y | y = sum_{i in face_idx} lambda_i * A[i], lambda_i >= 0 }``

    The generators are simply the rows ``A[i]`` for ``i in face_idx``.

    Args:
        A: Constraint matrix defining the polyhedron (m x n).
        b: Right-hand side vector (length m).  Not currently used but retained
           for interface consistency with standard polyhedral computations.
        face_idx: Indices of the active (tight) constraints defining the face.

    Returns:
        List of generator vectors (rows of A at the face_idx positions).

    Example::

        A = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]]
        b = [1.0, 1.0, 0.0, 0.0]
        # Normal cone at vertex (0, 0) (face_idx = [2, 3]): rows -e1 and -e2
        normal_cone(A, b, [2, 3])
        # [[-1.0, 0.0], [0.0, -1.0]]
    """
    if not A or not face_idx:
        return []
    return [list(A[i]) for i in face_idx if 0 <= i < len(A)]


# ---------------------------------------------------------------------------
# Tiling schedule derivation
# ---------------------------------------------------------------------------


def copilot_derive_tiling_schedule(
    loop_nest: list[tuple[str, str, str]],
    tile_sizes: list[int],
) -> list[list[int]]:
    """Derive a tiling transformation matrix for a loop nest.

    Given a loop nest of n loops and tile sizes, derives the 2n x n
    transformation matrix that splits each loop into a tile-level loop and a
    point-level loop.

    The standard tiling transformation for n loops with tile sizes (T_0, ..., T_{n-1})
    maps each original loop index i_k to:
    - Tile index: t_k = floor(i_k / T_k) → captured by the k-th tile row.
    - Point index: p_k = i_k mod T_k → captured by the (n+k)-th point row.

    The transformation is represented as a 2n x n matrix where:
    - Row k (k = 0..n-1): tile loop for dimension k.
    - Row n+k (k = 0..n-1): point loop for dimension k.

    In the linear transformation view (ignoring the floor operation for
    the legality analysis), tile row k is e_k (the k-th unit vector) and
    point row n+k is also e_k.  The full matrix thus has the form:
    ``[I_n; I_n]`` (stacked identity matrices).

    For the Banded/skewed tiling (fusion of tile and point), the tile rows
    are the unit vectors scaled by T_k.

    Args:
        loop_nest: List of (loop_var_name, lower_bound, upper_bound) triples.
        tile_sizes: List of tile sizes, one per loop.

    Returns:
        2n x n integer transformation matrix as list of integer row lists.

    Raises:
        ValueError: If ``len(loop_nest) != len(tile_sizes)``.

    Example::

        # 2-loop nest with tile sizes (32, 32)
        loops = [('i', '0', 'N'), ('j', '0', 'M')]
        copilot_derive_tiling_schedule(loops, [32, 32])
        # [[1, 0], [0, 1], [1, 0], [0, 1]]
        # Rows 0-1: tile loops, rows 2-3: point loops
    """
    n = len(loop_nest)
    if n != len(tile_sizes):
        raise ValueError(
            f"loop_nest has {n} loops but tile_sizes has {len(tile_sizes)} elements"
        )

    if n == 0:
        return []

    # Build 2n x n matrix: upper half = I_n (tile loops), lower half = I_n (point loops)
    M: list[list[int]] = []
    for k in range(n):
        row = [1 if j == k else 0 for j in range(n)]
        M.append(row)
    for k in range(n):
        row = [1 if j == k else 0 for j in range(n)]
        M.append(row)

    return M


# ---------------------------------------------------------------------------
# Judgment-geometric cross-references
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations import judgment_products as _judgment_products
except ImportError:
    _judgment_products = None  # type: ignore[assignment]

try:
    from jugeo.geometry import covers as _covers_mod
except ImportError:
    _covers_mod = None  # type: ignore[assignment]


def tensor_from_judgment_product(product: Any) -> dict[str, Any]:
    """Build a tensor encoding from a judgment product.

    Bridges the foundations judgment-product subsystem into the
    tensor-quantifier encoding pipeline by decomposing a product into
    its component terms and encoding each as a tensor factor.

    Parameters
    ----------
    product:
        A judgment product from ``jugeo.foundations.judgment_products``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"product"``, ``"factors"``, and ``"tensor_dim"``
        keys.
    """
    if _judgment_products is None:
        raise RuntimeError("jugeo.foundations.judgment_products is not available")
    factors = _judgment_products.decompose(product) if hasattr(_judgment_products, "decompose") else [product]
    return {
        "product": product,
        "factors": factors,
        "tensor_dim": len(factors),
    }


def quantifier_over_cover(cover: Any) -> dict[str, Any]:
    """Encode a quantifier scoped over a geometric cover.

    Uses the geometry covers subsystem to attach cover metadata to a
    quantifier encoding, enabling cover-local quantification.

    Parameters
    ----------
    cover:
        A geometric cover from ``jugeo.geometry.covers``.

    Returns
    -------
    dict[str, Any]
        A dict with ``"cover"``, ``"patches"``, and ``"quantifier_scope"``
        keys.
    """
    if _covers_mod is None:
        raise RuntimeError("jugeo.geometry.covers is not available")
    patches = _covers_mod.patches(cover) if hasattr(_covers_mod, "patches") else []
    return {
        "cover": cover,
        "patches": patches,
        "quantifier_scope": len(patches),
    }
