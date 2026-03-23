"""structured_data_encoder.py — Structured-data encoder for sequences.

Theory2.tex Chapter 29 §1: Structured-data encoder — lists, tuples, and
ordered dicts as typed Z3 arrays.

This module implements ``StructuredDataEncoder``, the first of five encoding
layers in Chapter 29.  The encoder maps Python sequences to the Z3 theory of
arrays (QF_AUFLIA fragment):

*   ``list[T]``     →  ``Array(IntSort, T_sort)`` + ``IntSort`` length variable
*   ``tuple[T...]`` →  heterogeneous Z3 datatype array (product sort)
*   ``dict[K, V]``  →  passed to ``FiniteMapEncoder`` (see s03)

Key encoding invariants
-----------------------
1.  **Index bounds**: ``0 ≤ i < length → array[i] ∈ T_sort``
2.  **Out-of-bounds default**: ``(i < 0 ∨ i ≥ length) → array[i] = default``
3.  **Length non-negativity**: ``length ≥ 0``

These three invariants make the encoding *faithful*: the Z3 array completely
characterises the Python sequence.

Fragment discipline
-------------------
Outputs are tagged ``QF_AUFLIA`` (quantifier-free array / linear integer
arithmetic) unless nested encodings are used, in which case the fragment may
escalate to ``SEQUENCES`` or ``QF_ABV``.

# copilot: StructuredDataEncoder — Theory2.tex §29.1.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator, Sequence

if TYPE_CHECKING:
    from jugeo.solver.fragments import EncodingStrategy, SolverFragment

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
# Solver fragment imports (optional)
# ---------------------------------------------------------------------------
try:
    from jugeo.solver.fragments import (
        EncodingStrategy,
        Fragment,
        FragmentSignature,
    )

    _FRAGMENTS_AVAILABLE = True
except ImportError:
    EncodingStrategy = None  # type: ignore[assignment,misc]
    Fragment = None  # type: ignore[assignment,misc]
    FragmentSignature = None  # type: ignore[assignment,misc]
    _FRAGMENTS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Local model imports
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.models import SequenceEncoding


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EncodedList:
    """Result of encoding a Python list as a Z3 array.

    Fields
    ------
    array_var : Any
        The Z3 array ``Array(IntSort, elem_sort)``.
    length_var : Any
        The Z3 integer variable equal to ``len(original_list)``.
    elem_sort : Any
        The Z3 sort for array elements.
    axioms : tuple[Any, ...]
        Z3 formulas defining the concrete values in the array.
    invariants : tuple[Any, ...]
        Z3 formulas for index-bounds and out-of-bounds behaviour.
    sequence_encoding : SequenceEncoding
        The corresponding SequenceEncoding model.
    name : str
        Debugging name for this encoded list.
    original_length : int
        The length of the original Python list.

    # copilot: EncodedList result type for StructuredDataEncoder.encode_list.
    """

    array_var: Any
    length_var: Any
    elem_sort: Any
    axioms: tuple[Any, ...]
    invariants: tuple[Any, ...]
    sequence_encoding: SequenceEncoding
    name: str = "list"
    original_length: int = 0

    def all_constraints(self) -> list[Any]:
        """Return the union of axioms and invariants.

        Returns
        -------
        list[Any]
        """
        return list(self.axioms) + list(self.invariants)

    def to_solver_add(self, solver: Any) -> None:
        """Add all constraints to a Z3 solver instance.

        Parameters
        ----------
        solver:
            A ``z3.Solver`` or compatible object with an ``add()`` method.
        """
        for c in self.all_constraints():
            solver.add(c)


@dataclass(frozen=True)
class EncodedTuple:
    """Result of encoding a Python tuple as a Z3 datatype array.

    A tuple ``(v0: T0, v1: T1, …)`` is encoded as a Z3 datatype with one
    constructor ``mk_tuple`` and projection functions ``proj_0``, ``proj_1``, …

    Fields
    ------
    datatype_sort : Any
        The Z3 datatype sort for the tuple.
    tuple_var : Any
        The Z3 constant of type ``datatype_sort``.
    proj_fns : tuple[Any, ...]
        Projection functions (``proj_0``, ``proj_1``, …).
    axioms : tuple[Any, ...]
        Z3 formulas fixing the concrete values.
    elem_sorts : tuple[Any, ...]
        The Z3 sorts for each element of the tuple.
    name : str
        Debugging name.
    arity : int
        Number of tuple elements.

    # copilot: EncodedTuple result type for StructuredDataEncoder.encode_tuple.
    """

    datatype_sort: Any
    tuple_var: Any
    proj_fns: tuple[Any, ...]
    axioms: tuple[Any, ...]
    elem_sorts: tuple[Any, ...]
    name: str = "tup"
    arity: int = 0

    def project(self, index: int) -> Any:
        """Return the Z3 expression for the element at *index*.

        Parameters
        ----------
        index:
            Zero-based index into the tuple.

        Returns
        -------
        Any
            A Z3 expression or string stub.
        """
        if index < 0 or index >= len(self.proj_fns):
            raise IndexError(f"Tuple index {index} out of range [0, {len(self.proj_fns)})")
        fn = self.proj_fns[index]
        if _Z3_AVAILABLE and callable(fn) and self.tuple_var is not None:
            return fn(self.tuple_var)
        return f"{self.name}.proj_{index}"


# ---------------------------------------------------------------------------
# StructuredDataEncoder
# ---------------------------------------------------------------------------


class StructuredDataEncoder:
    """Encodes Python sequences as typed Z3 arrays.

    This is the primary encoder for Chapter 29 §1.  It handles:

    *   Homogeneous lists (``list[T]``) → ``Array(IntSort, T_sort)``
    *   Heterogeneous tuples → Z3 datatype
    *   Nested sequences (arrays of arrays)

    The encoder is *stateless* in the sense that each call produces a fresh
    set of Z3 symbols.  An internal counter ensures name uniqueness.

    Parameters
    ----------
    name_prefix : str
        Prefix for all generated Z3 symbol names (default ``"sde"``).
    fresh_counter_start : int
        Starting value for the fresh name counter (default 0).

    Theory2.tex §29.1.

    # copilot: StructuredDataEncoder — Theory2.tex §29.1.
    """

    def __init__(
        self,
        name_prefix: str = "sde",
        fresh_counter_start: int = 0,
    ) -> None:
        """Initialise the encoder.

        Parameters
        ----------
        name_prefix:
            Prefix for all Z3 symbol names generated by this encoder.
        fresh_counter_start:
            Initial value for the internal fresh counter.
        """
        self._prefix = name_prefix
        self._counter = fresh_counter_start
        self._encodings: list[EncodedList | EncodedTuple] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode_list(
        self,
        lst: list[Any],
        elem_sort: Any,
    ) -> EncodedList:
        """Encode a Python list as a Z3 array with explicit length.

        Given ``lst = [v0, v1, …, vₙ]`` and an element sort ``S``, produces:

        *   ``arr : Array(IntSort, S)``  with  ``arr[i] = vᵢ`` for all valid i
        *   ``length : IntSort``  with  ``length = len(lst)``
        *   index-bound invariants  ``length ≥ 0`` and ``∀ i<0 ∨ i≥length: arr[i]=default``
        *   value axioms  ``arr[0] = v0, arr[1] = v1, …``

        Parameters
        ----------
        lst:
            The Python list to encode.
        elem_sort:
            The Z3 sort for list elements.  Either a ``z3.SortRef`` or a string
            like ``"Int"``, ``"Bool"``, ``"Real"``.

        Returns
        -------
        EncodedList
            The encoding result with array variable, length variable, axioms,
            and a ``SequenceEncoding`` model.

        Theory2.tex §29.1 Definition 29.1.

        # copilot: encode_list — maps list to Z3 Array(IntSort, elem_sort).
        """
        name = self._fresh_name("arr")
        n = len(lst)
        if _Z3_AVAILABLE:
            z3_elem_sort = self._resolve_sort(elem_sort)
            arr_var = _z3.Array(name, _z3.IntSort(), z3_elem_sort)
            len_var = _z3.Int(f"{name}_len")
            # Value axioms: arr[i] = v_i for each concrete value
            axioms: list[Any] = []
            for i, v in enumerate(lst):
                z3_val = self._python_to_z3(v, z3_elem_sort)
                axioms.append(_z3.Select(arr_var, _z3.IntVal(i)) == z3_val)
            # Length axiom
            axioms.append(len_var == _z3.IntVal(n))
            # Invariants
            default_val = self._default_for_sort(z3_elem_sort)
            invs: list[Any] = self._build_invariants(arr_var, len_var, z3_elem_sort, default_val, name)
            seq_enc = SequenceEncoding(
                sort_name=str(elem_sort),
                element_sort=z3_elem_sort,
                length_var=len_var,
                array_var=arr_var,
                index_invariants=tuple(invs),
                default_value=default_val,
                name_hint=name,
            )
            result = EncodedList(
                array_var=arr_var,
                length_var=len_var,
                elem_sort=z3_elem_sort,
                axioms=tuple(axioms),
                invariants=tuple(invs),
                sequence_encoding=seq_enc,
                name=name,
                original_length=n,
            )
        else:
            # Symbolic stub — Z3 not available
            arr_var = f"{name}:Array(Int,{elem_sort})"
            len_var = f"{name}_len:Int={n}"
            axioms_s = tuple(f"{name}[{i}]={v}" for i, v in enumerate(lst))
            invs_s = (
                f"{name}_len>=0",
                f"ForAll i<0 OR i>={n}: {name}[i]=default",
            )
            seq_enc = SequenceEncoding(
                sort_name=str(elem_sort),
                element_sort=elem_sort,
                length_var=len_var,
                array_var=arr_var,
                index_invariants=invs_s,
                name_hint=name,
            )
            result = EncodedList(
                array_var=arr_var,
                length_var=len_var,
                elem_sort=elem_sort,
                axioms=axioms_s,
                invariants=invs_s,
                sequence_encoding=seq_enc,
                name=name,
                original_length=n,
            )
        self._encodings.append(result)
        return result

    def encode_tuple(
        self,
        tup: tuple[Any, ...],
        sorts: Sequence[Any],
    ) -> EncodedTuple:
        """Encode a Python tuple as a Z3 algebraic datatype.

        A tuple ``(v0: S0, v1: S1, …, vₖ: Sₖ)`` is encoded as a fresh Z3
        datatype with:

        *   A single constructor ``mk_{name}(s0: S0, s1: S1, …)``
        *   Projection functions ``proj_0``, ``proj_1``, …
        *   Axioms fixing each component to its concrete value.

        Parameters
        ----------
        tup:
            The Python tuple to encode.
        sorts:
            A sequence of Z3 sorts (or sort name strings), one per element.

        Returns
        -------
        EncodedTuple
            The encoding result.

        Theory2.tex §29.1 — heterogeneous sequences.

        # copilot: encode_tuple — maps tuple to Z3 algebraic datatype.
        """
        if len(tup) != len(sorts):
            raise ValueError(
                f"encode_tuple: len(tup)={len(tup)} != len(sorts)={len(sorts)}"
            )
        name = self._fresh_name("tup")
        arity = len(tup)
        if _Z3_AVAILABLE:
            z3_sorts = [self._resolve_sort(s) for s in sorts]
            # Build Z3 Datatype
            dt = _z3.Datatype(name)
            fields = [(f"proj_{i}", z3_sorts[i]) for i in range(arity)]
            dt.declare(f"mk_{name}", *fields)
            dt_sort = dt.create()
            # Constructor and projections
            ctor = dt_sort.constructor(0)
            projs = [dt_sort.accessor(0, i) for i in range(arity)]
            tup_const = _z3.Const(f"{name}_val", dt_sort)
            # Axioms
            axioms: list[Any] = []
            for i, (v, sort) in enumerate(zip(tup, z3_sorts)):
                z3_val = self._python_to_z3(v, sort)
                axioms.append(projs[i](tup_const) == z3_val)
            result = EncodedTuple(
                datatype_sort=dt_sort,
                tuple_var=tup_const,
                proj_fns=tuple(projs),
                axioms=tuple(axioms),
                elem_sorts=tuple(z3_sorts),
                name=name,
                arity=arity,
            )
        else:
            # Symbolic stub
            projs_s = tuple(f"proj_{i}" for i in range(arity))
            axioms_s = tuple(f"{name}.proj_{i}={v}" for i, v in enumerate(tup))
            result = EncodedTuple(
                datatype_sort=f"Datatype<{name}>",
                tuple_var=f"{name}_val",
                proj_fns=projs_s,
                axioms=axioms_s,
                elem_sorts=tuple(str(s) for s in sorts),
                name=name,
                arity=arity,
            )
        self._encodings.append(result)
        return result

    def decode_array(
        self,
        z3_arr: Any,
        length_model: Any,
        elem_decoder: Callable[[Any], Any] | None = None,
    ) -> list[Any]:
        """Decode a Z3 array from a satisfying model into a Python list.

        Evaluates ``z3_arr[0]``, …, ``z3_arr[n-1]`` where ``n`` is the
        integer value of ``length_model``.

        Parameters
        ----------
        z3_arr:
            A Z3 array expression, or a Python dict mapping index → value
            (for stub mode).
        length_model:
            Either a Z3 model value (``z3.IntNumRef``) or a Python integer.
        elem_decoder:
            Optional function to convert each Z3 element value to Python.
            Defaults to ``int`` for integer sorts.

        Returns
        -------
        list[Any]
            The decoded Python list.

        Theory2.tex §29.1 — decoding reconstructs the original sequence.

        # copilot: decode_array — reconstructs Python list from Z3 model.
        """
        n = self._extract_int(length_model)
        if n < 0 or n > 10_000:
            logger.warning("decode_array: suspicious length %d, clamping to 0", n)
            n = 0
        result: list[Any] = []
        for i in range(n):
            if _Z3_AVAILABLE and hasattr(z3_arr, "sort"):
                try:
                    elem = _z3.simplify(_z3.Select(z3_arr, _z3.IntVal(i)))
                    if elem_decoder is not None:
                        result.append(elem_decoder(elem))
                    else:
                        result.append(self._z3_to_python(elem))
                except Exception as exc:
                    logger.warning("decode_array[%d]: %s", i, exc)
                    result.append(None)
            elif isinstance(z3_arr, dict):
                result.append(z3_arr.get(i))
            else:
                result.append(None)
        return result

    def assert_index_bounds(
        self,
        arr: Any,
        length: Any,
    ) -> list[Any]:
        """Return Z3 constraints asserting index-bound invariants for *arr*.

        The constraints are:
        1.  ``length ≥ 0``
        2.  ``∀ i: i ≥ length → arr[i] = default``  (conceptual; we return
            a symbolic placeholder when quantifiers are expensive)

        Parameters
        ----------
        arr:
            A Z3 array expression or string stub.
        length:
            A Z3 integer expression or Python int.

        Returns
        -------
        list[Any]
            A list of Z3 constraint formulas (or string stubs).

        Theory2.tex §29.1 — index-bound invariants.

        # copilot: assert_index_bounds — generates index-safety constraints.
        """
        constraints: list[Any] = []
        if _Z3_AVAILABLE and not isinstance(length, str):
            constraints.append(length >= 0)
            i = _z3.Int("_ibound_i")
            oob_placeholder = _z3.ForAll(
                [i],
                _z3.Implies(
                    _z3.Or(i < 0, i >= length),
                    _z3.BoolVal(True),  # placeholder; caller supplies default equality
                ),
            )
            constraints.append(oob_placeholder)
        else:
            constraints.append(f"{length} >= 0")
            constraints.append(f"ForAll i: (i<0 OR i>={length}) => out_of_bounds")
        return constraints

    def assert_element_type(
        self,
        arr: Any,
        length: Any,
        elem_pred: Callable[[Any], Any] | Any,
    ) -> Any:
        """Return a Z3 formula asserting *elem_pred* holds on every element.

        The constraint is:
            ``∀ i ∈ [0, length): elem_pred(arr[i])``

        Parameters
        ----------
        arr:
            A Z3 array expression.
        length:
            A Z3 integer expression (upper bound).
        elem_pred:
            A callable ``(elem: Z3Expr) → Z3BoolExpr`` or a Z3 lambda.

        Returns
        -------
        Any
            A Z3 ForAll formula or string stub.

        Theory2.tex §29.1 — element type membership invariant.

        # copilot: assert_element_type — universal element predicate.
        """
        if _Z3_AVAILABLE and not isinstance(arr, str):
            i = _z3.Int("_etype_i")
            in_range = _z3.And(i >= 0, i < length)
            elem = _z3.Select(arr, i)
            if callable(elem_pred):
                body = elem_pred(elem)
            else:
                body = elem_pred
            return _z3.ForAll([i], _z3.Implies(in_range, body))
        return f"ForAll i in [0,{length}): elem_pred(arr[i])"

    def lift_operation(
        self,
        python_op: Callable[[Any], Any],
        arr: Any,
        length: Any,
    ) -> Any:
        """Lift a Python operation to a Z3 formula over the encoded array.

        Applies ``python_op`` to each concrete index and conjoins the results.
        This is useful for encoding operations like "double all elements":
        ``python_op = lambda x: 2*x`` becomes ``arr_post[i] = 2 * arr_pre[i]``.

        Parameters
        ----------
        python_op:
            A Python callable ``(z3_elem) → Z3Expr``.
        arr:
            A Z3 array expression.
        length:
            The length of the array (Python int or Z3 IntVal).

        Returns
        -------
        Any
            A Z3 formula encoding the lifted operation.

        Theory2.tex §29.1 — lifting Python operations to Z3.

        # copilot: lift_operation — lifts a Python fn to Z3 array predicate.
        """
        n = self._extract_int(length)
        if _Z3_AVAILABLE and not isinstance(arr, str) and n >= 0:
            constraints = []
            for i in range(n):
                elem = _z3.Select(arr, _z3.IntVal(i))
                try:
                    result_expr = python_op(elem)
                    constraints.append(result_expr)
                except Exception as exc:
                    logger.warning("lift_operation[%d]: %s", i, exc)
            if constraints:
                return _z3.And(*constraints)
            return _z3.BoolVal(True)
        parts = []
        for i in range(max(0, n)):
            try:
                parts.append(str(python_op(f"arr[{i}]")))
            except Exception:
                parts.append(f"op(arr[{i}])")
        return " AND ".join(parts) if parts else "True"

    def encode_nested(
        self,
        nested_seq: list[list[Any]],
        depth: int,
        elem_sort: Any = None,
    ) -> EncodedList:
        """Encode a nested sequence (list of lists) as a nested Z3 array.

        A 2D list ``[[v00, v01, …], [v10, v11, …], …]`` is encoded as
        ``Array(IntSort, Array(IntSort, elem_sort))``.  Deeper nesting adds
        more array layers.

        Parameters
        ----------
        nested_seq:
            The nested Python sequence.
        depth:
            The nesting depth (1 = flat list, 2 = list of lists, etc.).
        elem_sort:
            The Z3 sort for the innermost elements.  If None, defaults to IntSort.

        Returns
        -------
        EncodedList
            An EncodedList wrapping a nested Z3 array.

        Theory2.tex §29.1 — nested sequence encoding.

        # copilot: encode_nested — nested arrays for multi-dimensional sequences.
        """
        if depth <= 0:
            raise ValueError(f"encode_nested: depth must be >= 1, got {depth}")
        if depth == 1:
            flat: list[Any] = []
            for item in nested_seq:
                if isinstance(item, list):
                    flat.extend(item)
                else:
                    flat.append(item)
            sort = elem_sort or "Int"
            return self.encode_list(flat, sort)
        # Build nested sort: Array(Int, Array(Int, ... elem_sort ...))
        name = self._fresh_name(f"nested{depth}")
        if _Z3_AVAILABLE:
            inner_sort = self._resolve_sort(elem_sort or "Int")
            for _ in range(depth - 1):
                inner_sort = _z3.ArraySort(_z3.IntSort(), inner_sort)
            outer_arr = _z3.Array(name, _z3.IntSort(), inner_sort)
            outer_len = _z3.Int(f"{name}_len")
            axioms: list[Any] = [outer_len == _z3.IntVal(len(nested_seq))]
            for i, sub in enumerate(nested_seq):
                if isinstance(sub, (list, tuple)) and depth == 2:
                    sub_name = self._fresh_name(f"sub_{i}")
                    sub_sort = self._resolve_sort(elem_sort or "Int")
                    sub_arr = _z3.Array(sub_name, _z3.IntSort(), sub_sort)
                    for j, v in enumerate(sub):
                        z3v = self._python_to_z3(v, sub_sort)
                        axioms.append(_z3.Select(sub_arr, _z3.IntVal(j)) == z3v)
                    axioms.append(
                        _z3.Select(outer_arr, _z3.IntVal(i)) == sub_arr
                    )
            invs = self._build_invariants(outer_arr, outer_len, inner_sort, None, name)
            seq_enc = SequenceEncoding(
                sort_name=f"Array(Int,{'Array(Int,' * (depth-1)}{elem_sort or 'Int'}{')'*(depth-1)})",
                element_sort=inner_sort,
                length_var=outer_len,
                array_var=outer_arr,
                index_invariants=tuple(invs),
                name_hint=name,
            )
            return EncodedList(
                array_var=outer_arr,
                length_var=outer_len,
                elem_sort=inner_sort,
                axioms=tuple(axioms),
                invariants=tuple(invs),
                sequence_encoding=seq_enc,
                name=name,
                original_length=len(nested_seq),
            )
        # Stub mode
        return self.encode_list(
            [str(sub) for sub in nested_seq],
            elem_sort or "Int",
        )

    def copilot_suggest_encoding(
        self,
        seq_type_hint: str | type,
    ) -> str:
        """Suggest an encoding strategy for a given Python sequence type hint.

        This is the *copilot interface* for encoding suggestions: given a type
        hint string or type object, returns a recommended encoding strategy name
        from the ``EncodingStrategy`` enumeration or a descriptive string.

        The suggestions follow Theory2.tex §29.1 Table 29.1:

        +-------------------+-------------------------------------------+
        | Python type       | Recommended encoding strategy             |
        +===================+===========================================+
        | list[int]         | ARRAY_INT (Array(Int, Int))               |
        | list[bool]        | ARRAY_BOOL (Array(Int, Bool))             |
        | list[str]         | SEQUENCES (Z3 sequence theory)            |
        | tuple[*Ts]        | DATATYPE (product sort)                   |
        | list[list[T]]     | NESTED_ARRAY (nested array encoding)      |
        | set[int]          | ARRAY_BOOL (characteristic function)      |
        | dict[K, V]        | PARTIAL_FN (finite map, see s03)          |
        +-------------------+-------------------------------------------+

        Parameters
        ----------
        seq_type_hint:
            A string like ``"list[int]"`` or a Python type like ``list``.

        Returns
        -------
        str
            A recommended encoding strategy identifier.

        # copilot: copilot_suggest_encoding — Theory2.tex §29.1 Table 29.1.
        """
        hint = str(seq_type_hint).lower().replace(" ", "")
        if "dict" in hint:
            return "PARTIAL_FN (use FiniteMapEncoder, see finite_map_encoder)"
        if "set[" in hint:
            return "ARRAY_BOOL (characteristic function Array(K, Bool))"
        if "tuple[" in hint:
            return "DATATYPE (Z3 algebraic datatype product sort via encode_tuple)"
        if "list[list" in hint or "list[tuple" in hint:
            return "NESTED_ARRAY (use encode_nested with depth=2)"
        if "list[str" in hint or "list[bytes" in hint:
            return "SEQUENCES (Z3 sequence theory; Fragment = SEQUENCES)"
        if "list[bool" in hint:
            return "ARRAY_BOOL (Array(IntSort, BoolSort))"
        if "list[float" in hint or "list[real" in hint:
            return "ARRAY_REAL (Array(IntSort, RealSort); Fragment = QF_AUFLIA→QF_AUFLRA)"
        if "list[" in hint:
            return "ARRAY_INT (Array(IntSort, IntSort); Fragment = QF_AUFLIA)"
        if _FRAGMENTS_AVAILABLE and EncodingStrategy is not None:
            try:
                return str(EncodingStrategy.DEFAULT)
            except Exception:
                pass
        return "ARRAY_INT (default: Array(IntSort, IntSort); Fragment = QF_AUFLIA)"

    def recent_encodings(self) -> list[EncodedList | EncodedTuple]:
        """Return a list of all encodings produced by this encoder instance.

        Returns
        -------
        list[EncodedList | EncodedTuple]
        """
        return list(self._encodings)

    def reset(self) -> None:
        """Clear the internal encoding history and reset the name counter.

        Does not affect any Z3 solver state.
        """
        self._encodings.clear()
        self._counter = 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fresh_name(self, prefix: str) -> str:
        """Return a fresh symbol name.

        Parameters
        ----------
        prefix:
            Name prefix.

        Returns
        -------
        str
        """
        self._counter += 1
        return f"{self._prefix}_{prefix}_{self._counter}"

    def _resolve_sort(self, sort_hint: Any) -> Any:
        """Resolve a sort hint (string or Z3 sort) to a Z3 sort.

        Parameters
        ----------
        sort_hint:
            A string like ``"Int"``, ``"Bool"``, ``"Real"`` or a ``z3.SortRef``.

        Returns
        -------
        Any
            A Z3 sort object.
        """
        if not _Z3_AVAILABLE:
            return sort_hint
        if isinstance(sort_hint, str):
            mapping = {
                "int": _z3.IntSort(),
                "integer": _z3.IntSort(),
                "bool": _z3.BoolSort(),
                "boolean": _z3.BoolSort(),
                "real": _z3.RealSort(),
                "float": _z3.RealSort(),
            }
            return mapping.get(sort_hint.lower(), _z3.IntSort())
        if hasattr(sort_hint, "kind"):
            return sort_hint
        return _z3.IntSort()

    def _default_for_sort(self, sort: Any) -> Any:
        """Return a canonical default value for a Z3 sort.

        Parameters
        ----------
        sort:
            A Z3 sort object.

        Returns
        -------
        Any
            A Z3 expression for the default value.
        """
        if not _Z3_AVAILABLE:
            return 0
        try:
            if _z3.is_bv_sort(sort):
                return _z3.BitVecVal(0, sort.size())
            if sort == _z3.BoolSort():
                return _z3.BoolVal(False)
            if sort == _z3.RealSort():
                return _z3.RealVal(0)
        except Exception:
            pass
        return _z3.IntVal(0)

    def _python_to_z3(self, v: Any, sort: Any) -> Any:
        """Convert a Python value to a Z3 literal of the given sort.

        Parameters
        ----------
        v:
            A Python value (int, bool, float, str).
        sort:
            The target Z3 sort.

        Returns
        -------
        Any
            A Z3 literal expression.
        """
        if not _Z3_AVAILABLE:
            return v
        try:
            if sort == _z3.BoolSort():
                return _z3.BoolVal(bool(v))
            if sort == _z3.RealSort():
                return _z3.RealVal(v)
            return _z3.IntVal(int(v))
        except Exception:
            return _z3.IntVal(0)

    def _build_invariants(
        self,
        arr: Any,
        length: Any,
        sort: Any,
        default_val: Any,
        name: str,
    ) -> list[Any]:
        """Build the standard index-bound invariants for an array encoding.

        Parameters
        ----------
        arr:
            The Z3 array variable.
        length:
            The Z3 length variable.
        sort:
            The element sort.
        default_val:
            The default value for out-of-bounds accesses.
        name:
            Name hint for Z3 quantifier variables.

        Returns
        -------
        list[Any]
        """
        if not _Z3_AVAILABLE:
            return [f"{name}_len>=0"]
        invs: list[Any] = [length >= 0]
        if default_val is not None:
            i = _z3.Int(f"_inv_i_{name}")
            oob = _z3.Or(i < 0, i >= length)
            invs.append(
                _z3.ForAll(
                    [i],
                    _z3.Implies(oob, _z3.Select(arr, i) == default_val),
                )
            )
        return invs

    def _extract_int(self, val: Any) -> int:
        """Extract a Python integer from a Z3 integer value or Python int.

        Parameters
        ----------
        val:
            A Z3 integer value or Python int.

        Returns
        -------
        int
            The integer value, or 0 on failure.
        """
        if isinstance(val, int):
            return val
        if _Z3_AVAILABLE:
            try:
                if hasattr(val, "as_long"):
                    return int(val.as_long())
                return int(str(val))
            except Exception:
                pass
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    def _z3_to_python(self, val: Any) -> Any:
        """Convert a Z3 value to a Python value.

        Parameters
        ----------
        val:
            A Z3 expression (typically from ``z3.simplify``).

        Returns
        -------
        Any
            A Python int, bool, or float.
        """
        if not _Z3_AVAILABLE:
            return val
        try:
            if _z3.is_true(val):
                return True
            if _z3.is_false(val):
                return False
            if hasattr(val, "as_long"):
                return int(val.as_long())
            if hasattr(val, "as_fraction"):
                n, d = val.as_fraction()
                return float(n) / float(d)
            return str(val)
        except Exception:
            return None


__all__: list[str] = [
    "StructuredDataEncoder",
    "EncodedList",
    "EncodedTuple",
]
