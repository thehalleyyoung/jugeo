"""NumPy contracts for JuGeo — array shape safety and dtype correctness.

Covers the most commonly used NumPy operations.  Register all contracts
by importing this module::

    import jugeo.contracts.numpy  # noqa: F401
"""

from jugeo.contracts.core import library_contract, requires, ensures


# ─── Array creation ──────────────────────────────────────────────

@library_contract("numpy.zeros", "Create zero-filled array")
@requires("all(s >= 0 for s in shape) if isinstance(shape, tuple) else shape >= 0")
@ensures("result.shape == (shape if isinstance(shape, tuple) else (shape,))")
def zeros(shape, dtype=None): ...


@library_contract("numpy.ones", "Create ones-filled array")
@requires("all(s >= 0 for s in shape) if isinstance(shape, tuple) else shape >= 0")
@ensures("result.shape == (shape if isinstance(shape, tuple) else (shape,))")
def ones(shape, dtype=None): ...


@library_contract("numpy.eye", "Identity matrix")
@requires("N >= 0")
@ensures("result.shape == (N, M if M is not None else N)")
def eye(N, M=None, dtype=None): ...


@library_contract("numpy.arange", "Evenly spaced values")
@requires("step != 0")
@ensures("result.ndim == 1")
def arange(start, stop=None, step=1, dtype=None): ...


@library_contract("numpy.linspace", "Evenly spaced values over interval")
@requires("num >= 0")
@ensures("result.ndim == 1")
@ensures("result.shape[0] == num")
def linspace(start, stop, num=50, dtype=None): ...


# ─── Linear algebra ──────────────────────────────────────────────

@library_contract("numpy.dot", "Dot product / matrix multiplication")
@requires("a.shape[-1] == b.shape[-2] if b.ndim >= 2 else a.shape[-1] == b.shape[0]")
@ensures("result.ndim == max(0, a.ndim + b.ndim - 2)")
def dot(a, b): ...


@library_contract("numpy.matmul", "Matrix multiplication (@ operator)")
@requires("a.ndim >= 1 and b.ndim >= 1")
@requires("a.shape[-1] == b.shape[-2] if b.ndim >= 2 else a.shape[-1] == b.shape[-1]")
@ensures("result is not None")
def matmul(a, b): ...


@library_contract("numpy.linalg.inv", "Matrix inverse")
@requires("a.ndim >= 2")
@requires("a.shape[-2] == a.shape[-1]")
@ensures("result.shape == a.shape")
def inv(a): ...


@library_contract("numpy.linalg.solve", "Solve linear system Ax = b")
@requires("a.ndim >= 2 and a.shape[-2] == a.shape[-1]")
@requires("b.shape[-2] == a.shape[-1] if b.ndim >= 2 else b.shape[0] == a.shape[-1]")
@ensures("result.shape == b.shape")
def solve(a, b): ...


@library_contract("numpy.linalg.eig", "Eigenvalue decomposition")
@requires("a.ndim >= 2 and a.shape[-2] == a.shape[-1]")
@ensures("len(result) == 2")
def eig(a): ...


@library_contract("numpy.linalg.svd", "Singular value decomposition")
@requires("a.ndim >= 2")
@ensures("len(result) == 3")
def svd(a, full_matrices=True): ...


@library_contract("numpy.linalg.det", "Determinant")
@requires("a.ndim >= 2 and a.shape[-2] == a.shape[-1]")
@ensures("result.ndim == a.ndim - 2")
def det(a): ...


@library_contract("numpy.linalg.norm", "Matrix/vector norm")
@requires("x.ndim >= 1")
@ensures("result is not None")
def norm(x, ord=None, axis=None): ...


# ─── Shape manipulation ──────────────────────────────────────────

@library_contract("numpy.reshape", "Reshape array")
@requires("all(s >= -1 for s in newshape)")
@ensures("result.size == a.size")
def reshape(a, newshape): ...


@library_contract("numpy.transpose", "Transpose array")
@ensures("result.size == a.size")
@ensures("result.ndim == a.ndim")
def transpose(a, axes=None): ...


@library_contract("numpy.concatenate", "Join arrays along axis")
@requires("len(arrays) >= 1")
@ensures("result.ndim == arrays[0].ndim")
def concatenate(arrays, axis=0): ...


@library_contract("numpy.stack", "Stack arrays along new axis")
@requires("len(arrays) >= 1")
@requires("all(a.shape == arrays[0].shape for a in arrays)")
@ensures("result.ndim == arrays[0].ndim + 1")
def stack(arrays, axis=0): ...


@library_contract("numpy.split", "Split array into sub-arrays")
@requires("a.ndim >= 1")
@ensures("all(s.ndim == a.ndim for s in result)")
def split(a, indices_or_sections, axis=0): ...


@library_contract("numpy.expand_dims", "Insert new axis")
@ensures("result.ndim == a.ndim + 1")
@ensures("result.size == a.size")
def expand_dims(a, axis): ...


# ─── Reductions ──────────────────────────────────────────────────

@library_contract("numpy.sum", "Sum of array elements")
@ensures("result.ndim <= a.ndim")
def sum(a, axis=None, keepdims=False): ...


@library_contract("numpy.mean", "Mean of array elements")
@ensures("result.ndim <= a.ndim")
def mean(a, axis=None, keepdims=False): ...


@library_contract("numpy.max", "Maximum of array elements")
@ensures("result.ndim <= a.ndim")
def max(a, axis=None, keepdims=False): ...


@library_contract("numpy.min", "Minimum of array elements")
@ensures("result.ndim <= a.ndim")
def min(a, axis=None, keepdims=False): ...


@library_contract("numpy.argmax", "Index of maximum")
@requires("a.size > 0")
@ensures("result.ndim <= a.ndim")
def argmax(a, axis=None): ...


# ─── Element-wise ────────────────────────────────────────────────

@library_contract("numpy.abs", "Absolute value (element-wise)")
@ensures("result.shape == x.shape")
def abs(x): ...


@library_contract("numpy.exp", "Exponential (element-wise)")
@ensures("result.shape == x.shape")
def exp(x): ...


@library_contract("numpy.log", "Natural logarithm (element-wise)")
@requires("all(x > 0)")
@ensures("result.shape == x.shape")
def log(x): ...


@library_contract("numpy.sqrt", "Square root (element-wise)")
@requires("all(x >= 0)")
@ensures("result.shape == x.shape")
def sqrt(x): ...


@library_contract("numpy.clip", "Clip values to range")
@ensures("result.shape == a.shape")
def clip(a, a_min, a_max): ...


if __name__ == "__main__":
    from jugeo.contracts import get_registry
    print(get_registry().summary())
