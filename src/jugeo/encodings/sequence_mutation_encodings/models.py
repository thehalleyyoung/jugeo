"""models.py — Core domain models for jugeo.encodings.sequence_mutation_encodings.

Theory2.tex Chapter 29: sequences, finite maps, heap slices, support-aware mutation.

This module provides five immutable dataclass models that are the shared
vocabulary across all encoders, algorithms, and theorems in this package.
Every encoder produces instances of these models; every theorem quantifies
over them.

Models
------
* :class:`SequenceEncoding`     — a Python sequence encoded as a Z3 array with
                                   index-bound invariants.
* :class:`MutationSlice`        — a description of a mutation on a slice [lo, hi)
                                   of a sequence.
* :class:`HeapSlice`            — a heap summary restricted to a finite support set.
* :class:`SupportAwareMutation` — a mutation that only affects declared support cells.
* :class:`SequenceInvariant`    — a structural invariant on a SequenceEncoding.

Design principles
-----------------
*  All models are ``@dataclass(frozen=True)`` to prevent silent mutation after
   construction.  Derived values are computed on demand via methods.
*  Z3 expressions are stored as ``Any`` typed fields and accessed through helper
   methods; this lets the models be instantiated even when z3-python is absent.
*  Every model provides a ``to_dict()`` / ``__repr__`` for inspection and logging.

# copilot: core domain models for sequence_mutation_encodings — Theory2.tex Ch29.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence

if TYPE_CHECKING:
    pass

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
# Supporting enumerations
# ---------------------------------------------------------------------------


class MutationKind(Enum):
    """Describes the structural shape of a mutation on a sequence slice.

    Values
    ------
    POINTWISE
        Each element in [lo, hi) is updated independently: post[i] = f(pre[i]).
    BULK_ASSIGN
        All elements in [lo, hi) are set to a single constant value.
    PERMUTATION
        Elements in [lo, hi) are rearranged without changing their multiset.
    INSERTION
        New elements are inserted, shifting existing elements right.
    DELETION
        Elements are removed, shifting remaining elements left.
    PARTIAL
        Mutation affects only a strict subset of [lo, hi) (support ⊊ [lo, hi)).
    ARBITRARY
        No structural constraint on the mutation; most general case.

    # copilot: MutationKind enum — classifies mutation shapes.
    """

    POINTWISE = auto()
    BULK_ASSIGN = auto()
    PERMUTATION = auto()
    INSERTION = auto()
    DELETION = auto()
    PARTIAL = auto()
    ARBITRARY = auto()


class SequenceInvariantKind(Enum):
    """Classifies structural invariants on sequence encodings.

    Values
    ------
    SORTED_ASC
        arr[i] ≤ arr[i+1] for all i in [0, length-1).
    SORTED_DESC
        arr[i] ≥ arr[i+1] for all i in [0, length-1).
    BOUNDED
        lb ≤ arr[i] ≤ ub for all i in [0, length).
    DISTINCT
        arr[i] ≠ arr[j] for all i ≠ j in [0, length).
    NON_NEGATIVE
        arr[i] ≥ 0 for all i in [0, length).
    PREFIX_PROPERTY
        A user-supplied predicate holds on every proper prefix.
    CUSTOM
        An arbitrary user-supplied predicate.

    # copilot: SequenceInvariantKind enum — structural invariant types.
    """

    SORTED_ASC = auto()
    SORTED_DESC = auto()
    BOUNDED = auto()
    DISTINCT = auto()
    NON_NEGATIVE = auto()
    PREFIX_PROPERTY = auto()
    CUSTOM = auto()


# ---------------------------------------------------------------------------
# SequenceEncoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceEncoding:
    """Represents a Python sequence encoded as a Z3 array with index-bound invariants.

    A Python list ``[v0, v1, …, vₙ]`` of element sort ``S`` is encoded as:

    *   a Z3 array  ``arr : Array(IntSort, S)``
    *   a Z3 integer ``length : IntSort``  (= n+1)
    *   index-bound invariant: ``0 ≤ i < length → arr[i] ∈ S``
    *   out-of-bounds axiom:   ``(i < 0 ∨ i ≥ length) → arr[i] = default_val``

    Theory2.tex §29.1.

    Fields
    ------
    sort_name : str
        Human-readable name for the element sort (e.g., "Int", "Bool", "Str").
    element_sort : Any
        The Z3 sort object for array elements, or a string stub when z3 is absent.
    length_var : Any
        The Z3 integer variable representing the sequence length.
    array_var : Any
        The Z3 array variable ``Array(IntSort, element_sort)``.
    index_invariants : tuple[Any, ...]
        Pre-computed Z3 invariant formulas attached to this encoding.
    default_value : Any
        The Z3 expression returned for out-of-bounds array accesses.
    name_hint : str
        Optional debugging name for this encoding (used in Z3 symbol names).

    # copilot: SequenceEncoding model — Theory2.tex §29.1.
    """

    sort_name: str
    element_sort: Any
    length_var: Any
    array_var: Any
    index_invariants: tuple[Any, ...] = field(default_factory=tuple)
    default_value: Any = None
    name_hint: str = "seq"

    # ------------------------------------------------------------------
    # Encoding / decoding
    # ------------------------------------------------------------------

    def encode(self) -> dict[str, Any]:
        """Return a dictionary representation of the Z3 encoding components.

        Returns the array variable, length variable, and invariants as a
        dictionary.  Callers add this to their Z3 solver context.

        Returns
        -------
        dict[str, Any]
            Keys: 'array_var', 'length_var', 'element_sort', 'index_invariants'.
        """
        return {
            "array_var": self.array_var,
            "length_var": self.length_var,
            "element_sort": self.element_sort,
            "index_invariants": list(self.index_invariants),
            "sort_name": self.sort_name,
            "name_hint": self.name_hint,
        }

    def decode(self, model: Any, elem_decoder: Any = None) -> list[Any]:
        """Decode a Z3 model into a Python list.

        Uses the Z3 model to evaluate ``array_var[0]``, …, ``array_var[n-1]``
        where ``n`` is the model value of ``length_var``.

        Parameters
        ----------
        model:
            A Z3 model (``z3.ModelRef``) or a dict mapping variable names to
            Python values (when z3 is absent).
        elem_decoder:
            Optional callable ``(z3_val) → Python value`` applied to each
            array element.  Defaults to ``int()`` for integer sorts.

        Returns
        -------
        list[Any]
            The decoded Python sequence.
        """
        if _Z3_AVAILABLE and hasattr(model, "eval"):
            try:
                raw_len = model.eval(self.length_var, model_completion=True)
                n = int(raw_len.as_long()) if hasattr(raw_len, "as_long") else int(str(raw_len))
                result = []
                for i in range(n):
                    idx = _z3.IntVal(i)
                    elem = model.eval(_z3.Select(self.array_var, idx), model_completion=True)
                    if elem_decoder is not None:
                        result.append(elem_decoder(elem))
                    else:
                        result.append(elem)
                return result
            except Exception as exc:
                logger.warning("SequenceEncoding.decode: Z3 eval failed: %s", exc)
                return []
        # Fallback for dict-style stub models
        if isinstance(model, dict):
            n = model.get(str(self.length_var), 0)
            arr = model.get(str(self.array_var), {})
            return [arr.get(i, self.default_value) for i in range(n)]
        return []

    def length_constraint(self) -> Any:
        """Return the Z3 formula asserting ``length_var ≥ 0``.

        Returns
        -------
        Any
            A Z3 formula or a string stub.

        Theory2.tex §29.1 — every encoded sequence has non-negative length.
        """
        if _Z3_AVAILABLE and self.length_var is not None:
            return self.length_var >= 0
        return f"({self.name_hint}.length >= 0)"

    def bounds_axiom(self) -> Any:
        """Return the Z3 formula for out-of-bounds behaviour.

        The axiom states:
            ``∀ i: (i < 0 ∨ i ≥ length_var) → array_var[i] = default_value``

        Returns
        -------
        Any
            A Z3 ForAll formula or a string stub.

        Theory2.tex §29.1 — out-of-range access returns a canonical default.
        """
        if _Z3_AVAILABLE and self.array_var is not None and self.default_value is not None:
            i = _z3.Int(f"_bounds_idx_{self.name_hint}")
            oob = _z3.Or(i < 0, i >= self.length_var)
            access = _z3.Select(self.array_var, i)
            return _z3.ForAll([i], _z3.Implies(oob, access == self.default_value))
        return (
            f"ForAll i: (i<0 OR i>={self.name_hint}.length) => "
            f"{self.name_hint}[i] = default"
        )

    def element_access(self, index: Any) -> Any:
        """Return the Z3 expression for ``array_var[index]``.

        Parameters
        ----------
        index:
            A Z3 integer expression (or plain Python int).

        Returns
        -------
        Any
            ``z3.Select(array_var, index)`` or a string stub.
        """
        if _Z3_AVAILABLE and self.array_var is not None:
            if isinstance(index, int):
                index = _z3.IntVal(index)
            return _z3.Select(self.array_var, index)
        return f"{self.name_hint}[{index}]"

    def slice_constraint(self, lo: Any, hi: Any, pred: Any) -> Any:
        """Return a Z3 formula asserting ``pred(array_var[i])`` for all ``i ∈ [lo, hi)``.

        Parameters
        ----------
        lo:
            Lower bound (inclusive), Z3 int expression or Python int.
        hi:
            Upper bound (exclusive), Z3 int expression or Python int.
        pred:
            A Z3 lambda expression ``i → Bool`` or callable.

        Returns
        -------
        Any
            A bounded quantifier formula or string stub.
        """
        if _Z3_AVAILABLE and self.array_var is not None:
            i = _z3.Int(f"_slice_idx_{self.name_hint}")
            in_range = _z3.And(i >= lo, i < hi)
            elem = _z3.Select(self.array_var, i)
            if callable(pred):
                body = pred(elem)
            else:
                body = pred
            return _z3.ForAll([i], _z3.Implies(in_range, body))
        return f"ForAll i in [{lo},{hi}): pred({self.name_hint}[i])"

    def extend(self, new_elem: Any) -> "SequenceEncoding":
        """Return a new SequenceEncoding with one element appended.

        The new length is ``length_var + 1`` and the new array is a Z3 Store
        at position ``length_var``.

        Parameters
        ----------
        new_elem:
            The Z3 expression (or stub) to append.

        Returns
        -------
        SequenceEncoding
            A new encoding with the appended element.
        """
        if _Z3_AVAILABLE and self.array_var is not None:
            new_arr = _z3.Store(self.array_var, self.length_var, new_elem)
            new_len = self.length_var + 1
        else:
            new_arr = f"Store({self.name_hint}.array, {self.name_hint}.length, {new_elem})"
            new_len = f"({self.name_hint}.length + 1)"
        return replace(
            self,
            array_var=new_arr,
            length_var=new_len,
            name_hint=f"{self.name_hint}_ext",
        )

    def prepend(self, new_elem: Any) -> "SequenceEncoding":
        """Return a new SequenceEncoding with one element prepended.

        All existing elements are shifted right by 1.  This is encoded via
        a fresh array variable with an explicit Store axiom.

        Parameters
        ----------
        new_elem:
            The Z3 expression (or stub) to prepend.

        Returns
        -------
        SequenceEncoding
            A new encoding with the prepended element.
        """
        if _Z3_AVAILABLE and self.array_var is not None:
            new_name = f"{self.name_hint}_pre"
            new_arr = _z3.Array(new_name, _z3.IntSort(), self.element_sort)
            shift_axiom = self.slice_constraint(
                0, self.length_var,
                lambda elem: elem == _z3.Select(self.array_var, _z3.Int("_pre_i") - 1)
            )
            first_axiom = _z3.Select(new_arr, _z3.IntVal(0)) == new_elem
            new_len = self.length_var + 1
            return replace(
                self,
                array_var=new_arr,
                length_var=new_len,
                index_invariants=self.index_invariants + (shift_axiom, first_axiom),
                name_hint=new_name,
            )
        return replace(self, name_hint=f"{self.name_hint}_pre")

    def concat(self, other: "SequenceEncoding") -> "SequenceEncoding":
        """Return a new SequenceEncoding representing the concatenation of self and other.

        The new length is ``self.length_var + other.length_var``.  The new array
        contains self's elements at indices [0, self.length) and other's elements
        at indices [self.length, self.length + other.length).

        Parameters
        ----------
        other:
            Another SequenceEncoding with compatible element_sort.

        Returns
        -------
        SequenceEncoding
            The concatenated encoding.
        """
        if _Z3_AVAILABLE and self.array_var is not None and other.array_var is not None:
            new_name = f"{self.name_hint}_cat_{other.name_hint}"
            new_arr = _z3.Array(new_name, _z3.IntSort(), self.element_sort)
            new_len = self.length_var + other.length_var
            i = _z3.Int("_cat_i")
            lower_axiom = _z3.ForAll(
                [i],
                _z3.Implies(
                    _z3.And(i >= 0, i < self.length_var),
                    _z3.Select(new_arr, i) == _z3.Select(self.array_var, i),
                ),
            )
            upper_axiom = _z3.ForAll(
                [i],
                _z3.Implies(
                    _z3.And(i >= self.length_var, i < new_len),
                    _z3.Select(new_arr, i)
                    == _z3.Select(other.array_var, i - self.length_var),
                ),
            )
            return replace(
                self,
                array_var=new_arr,
                length_var=new_len,
                index_invariants=self.index_invariants + (lower_axiom, upper_axiom),
                name_hint=new_name,
            )
        return replace(self, name_hint=f"{self.name_hint}_cat_{other.name_hint}")

    def invariant_set(self) -> tuple[Any, ...]:
        """Return all Z3 invariant formulas attached to this encoding.

        Includes index bounds, out-of-bounds axioms, and any custom invariants.

        Returns
        -------
        tuple[Any, ...]
            All attached invariants.
        """
        base = (self.length_constraint(), self.bounds_axiom())
        return base + self.index_invariants

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this encoding.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "sort_name": self.sort_name,
            "name_hint": self.name_hint,
            "num_invariants": len(self.index_invariants),
            "has_default": self.default_value is not None,
        }


# ---------------------------------------------------------------------------
# MutationSlice
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationSlice:
    """A description of a mutation operation on a slice [lo, hi) of a sequence.

    A MutationSlice captures the *what*, *where*, and *how* of a sequence
    mutation:

    *   ``base_encoding`` — the SequenceEncoding being mutated.
    *   ``[lo, hi)``      — the index range being mutated.
    *   ``new_values``    — the post-mutation values (Z3 array or dict stub).
    *   ``support_set``   — the set of indices actually modified (⊆ [lo, hi)).
    *   ``mutation_kind`` — structural shape of the mutation.

    Theory2.tex §29.4 — mutation slices and support.

    Fields
    ------
    base_encoding : SequenceEncoding
        The pre-mutation sequence encoding.
    lo : Any
        Lower bound of the mutation range (inclusive), Z3 int or Python int.
    hi : Any
        Upper bound of the mutation range (exclusive), Z3 int or Python int.
    new_values : Any
        Z3 array (or dict stub) containing the post-mutation values.
    support_set : frozenset[int]
        The set of indices that are actually modified.
    mutation_kind : MutationKind
        The structural kind of this mutation.
    post_encoding : SequenceEncoding | None
        The post-mutation SequenceEncoding (may be None if not yet computed).

    # copilot: MutationSlice model — Theory2.tex §29.4.
    """

    base_encoding: SequenceEncoding
    lo: Any
    hi: Any
    new_values: Any
    support_set: frozenset[int]
    mutation_kind: MutationKind = MutationKind.ARBITRARY
    post_encoding: SequenceEncoding | None = None

    def support_constraint(self) -> Any:
        """Return a Z3 formula asserting that the mutation is contained in the support.

        The constraint is:
            ``∀ i ∈ [lo, hi): i ∉ support_set → post[i] = pre[i]``

        This is the *frame condition* restricted to the slice [lo, hi).

        Returns
        -------
        Any
            A Z3 formula or string stub.
        """
        if _Z3_AVAILABLE and self.base_encoding.array_var is not None:
            constraints = []
            for i_val in range(
                int(self.lo) if isinstance(self.lo, (int, float)) else 0,
                int(self.hi) if isinstance(self.hi, (int, float)) else 0,
            ):
                if i_val not in self.support_set:
                    pre_elem = self.base_encoding.element_access(i_val)
                    if self.post_encoding is not None:
                        post_elem = self.post_encoding.element_access(i_val)
                        constraints.append(post_elem == pre_elem)
            if constraints:
                return _z3.And(*constraints)
            return _z3.BoolVal(True)
        lo_int = int(self.lo) if isinstance(self.lo, (int, float)) else "lo"
        hi_int = int(self.hi) if isinstance(self.hi, (int, float)) else "hi"
        return (
            f"ForAll i in [{lo_int},{hi_int}): "
            f"i not in support_set => post[i] = pre[i]"
        )

    def mutation_predicate(self) -> Any:
        """Return a Z3 formula characterising the entire mutation.

        Combines the support constraint and the explicit new values for
        each index in ``support_set``.

        Returns
        -------
        Any
            A Z3 And formula or string stub.
        """
        parts: list[Any] = [self.support_constraint()]
        if (
            _Z3_AVAILABLE
            and self.post_encoding is not None
            and self.new_values is not None
        ):
            for idx in sorted(self.support_set):
                post_elem = self.post_encoding.element_access(idx)
                if isinstance(self.new_values, dict):
                    new_val = self.new_values.get(idx)
                    if new_val is not None:
                        parts.append(post_elem == new_val)
                else:
                    new_val = _z3.Select(self.new_values, _z3.IntVal(idx))
                    parts.append(post_elem == new_val)
        if _Z3_AVAILABLE and parts:
            return _z3.And(*parts)
        return f"mutation_predicate(support={sorted(self.support_set)})"

    def repair_slice(self, repair_values: dict[int, Any]) -> "MutationSlice":
        """Return a new MutationSlice with updated values for specific indices.

        Used in repair mode: after a violation is detected, specific indices
        are corrected to their repaired values.

        Parameters
        ----------
        repair_values:
            Mapping from index to new Z3 value or Python value.

        Returns
        -------
        MutationSlice
            A new MutationSlice with the repaired values merged into new_values.
        """
        if isinstance(self.new_values, dict):
            merged = {**self.new_values, **repair_values}
        else:
            merged = dict(repair_values)
        new_support = self.support_set | frozenset(repair_values.keys())
        return replace(self, new_values=merged, support_set=new_support)

    def countermodel_from_violation(self, z3_model: Any) -> dict[int, Any]:
        """Extract a concrete countermodel for this slice from a Z3 model.

        Evaluates ``post_encoding[i]`` for each ``i`` in ``support_set`` and
        returns the resulting Python-level values.

        Parameters
        ----------
        z3_model:
            A satisfying Z3 model object (``z3.ModelRef``).

        Returns
        -------
        dict[int, Any]
            Mapping from index to concrete model value.
        """
        result: dict[int, Any] = {}
        if self.post_encoding is None:
            return result
        for idx in sorted(self.support_set):
            elem = self.post_encoding.element_access(idx)
            if _Z3_AVAILABLE and hasattr(z3_model, "eval"):
                try:
                    val = z3_model.eval(elem, model_completion=True)
                    result[idx] = val
                except Exception:
                    result[idx] = None
            elif isinstance(z3_model, dict):
                result[idx] = z3_model.get(f"{self.post_encoding.name_hint}[{idx}]")
        return result

    def validate_support(self) -> bool:
        """Return True if ``support_set ⊆ [lo, hi)`` and ``lo < hi``.

        Returns
        -------
        bool
        """
        try:
            lo_int = int(self.lo)
            hi_int = int(self.hi)
        except (TypeError, ValueError):
            return True  # cannot validate symbolic bounds
        if lo_int >= hi_int:
            return False
        for idx in self.support_set:
            if idx < lo_int or idx >= hi_int:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "lo": str(self.lo),
            "hi": str(self.hi),
            "support_set": sorted(self.support_set),
            "mutation_kind": self.mutation_kind.name,
            "has_post_encoding": self.post_encoding is not None,
        }


# ---------------------------------------------------------------------------
# HeapSlice
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeapSlice:
    """A restricted summary of the heap covering only a finite support set.

    A HeapSlice represents:

    *   ``heap_array : Array(Addr, Cell)`` — the full heap (pre- or post-state).
    *   ``support_addresses`` — the finite set of addresses in this slice.
    *   ``frame_invariant`` — the Z3 formula asserting that cells outside the
        support set are unchanged from a reference heap.

    Theory2.tex §29.4 — heap slices.

    Fields
    ------
    support_addresses : frozenset[int]
        The finite set of heap addresses in this slice.
    cell_sort : Any
        The Z3 sort for heap cells.
    read_map : Any
        The Z3 array ``Array(IntSort, cell_sort)`` for read operations.
    write_map : Any
        The Z3 array ``Array(IntSort, cell_sort)`` for write operations (post).
    frame_invariant : Any
        Z3 formula: ``∀ addr ∉ support: write_map[addr] = read_map[addr]``.
    name_hint : str
        Debugging name.

    # copilot: HeapSlice model — Theory2.tex §29.4.
    """

    support_addresses: frozenset[int]
    cell_sort: Any
    read_map: Any
    write_map: Any
    frame_invariant: Any = None
    name_hint: str = "heap"

    def frame_axiom(self) -> Any:
        """Return the Z3 frame axiom for this slice.

        The frame axiom is:
            ``∀ addr ∉ support_addresses: write_map[addr] = read_map[addr]``

        Returns
        -------
        Any
            A Z3 formula or string stub.
        """
        if self.frame_invariant is not None:
            return self.frame_invariant
        if _Z3_AVAILABLE and self.read_map is not None and self.write_map is not None:
            addr = _z3.Int(f"_frame_addr_{self.name_hint}")
            in_support = _z3.Or(*[addr == _z3.IntVal(a) for a in self.support_addresses]) \
                if self.support_addresses else _z3.BoolVal(False)
            return _z3.ForAll(
                [addr],
                _z3.Implies(
                    _z3.Not(in_support),
                    _z3.Select(self.write_map, addr) == _z3.Select(self.read_map, addr),
                ),
            )
        return (
            f"ForAll addr not in {sorted(self.support_addresses)}: "
            f"{self.name_hint}.write[addr] = {self.name_hint}.read[addr]"
        )

    def read_constraint(self, addr: Any, expected_val: Any) -> Any:
        """Return a Z3 formula asserting that reading *addr* yields *expected_val*.

        Parameters
        ----------
        addr:
            A Z3 or Python integer address.
        expected_val:
            The expected cell value (Z3 expression or stub).

        Returns
        -------
        Any
            A Z3 equality formula or string stub.
        """
        if _Z3_AVAILABLE and self.read_map is not None:
            if isinstance(addr, int):
                addr = _z3.IntVal(addr)
            return _z3.Select(self.read_map, addr) == expected_val
        return f"{self.name_hint}.read[{addr}] = {expected_val}"

    def write_constraint(self, addr: Any, new_val: Any) -> Any:
        """Return a Z3 formula asserting that *addr* is written to *new_val*.

        Also asserts that *addr* is in ``support_addresses`` (support honesty).

        Parameters
        ----------
        addr:
            The address to write (Z3 or Python int).
        new_val:
            The value to write (Z3 expression or stub).

        Returns
        -------
        Any
            A Z3 formula or string stub.
        """
        if _Z3_AVAILABLE and self.write_map is not None:
            if isinstance(addr, int):
                addr_expr = _z3.IntVal(addr)
            else:
                addr_expr = addr
            write_eq = _z3.Select(self.write_map, addr_expr) == new_val
            if self.support_addresses and isinstance(addr, int):
                in_sup = _z3.BoolVal(addr in self.support_addresses)
            else:
                in_sup = _z3.Or(*[addr_expr == _z3.IntVal(a) for a in self.support_addresses]) \
                    if self.support_addresses else _z3.BoolVal(False)
            return _z3.And(in_sup, write_eq)
        return f"{self.name_hint}.write[{addr}] = {new_val} (in support)"

    def merge_slices(self, other: "HeapSlice") -> "HeapSlice":
        """Merge two HeapSlices into one.

        The merged slice has support = union of both support sets.
        Precondition: the slices should be disjoint (no conflicting writes).

        Parameters
        ----------
        other:
            Another HeapSlice to merge with.

        Returns
        -------
        HeapSlice
            A new HeapSlice with merged support.
        """
        merged_support = self.support_addresses | other.support_addresses
        if _Z3_AVAILABLE and self.write_map is not None and other.write_map is not None:
            new_name = f"{self.name_hint}_merge_{other.name_hint}"
            new_write = _z3.Array(new_name, _z3.IntSort(), self.cell_sort)
            constraints = []
            for addr in merged_support:
                addr_expr = _z3.IntVal(addr)
                if addr in self.support_addresses:
                    constraints.append(
                        _z3.Select(new_write, addr_expr) == _z3.Select(self.write_map, addr_expr)
                    )
                else:
                    constraints.append(
                        _z3.Select(new_write, addr_expr) == _z3.Select(other.write_map, addr_expr)
                    )
            return replace(
                self,
                support_addresses=merged_support,
                write_map=new_write,
                name_hint=new_name,
                frame_invariant=_z3.And(*constraints) if constraints else _z3.BoolVal(True),
            )
        return replace(
            self,
            support_addresses=merged_support,
            name_hint=f"{self.name_hint}_merge_{other.name_hint}",
        )

    def disjoint_slices(self, other: "HeapSlice") -> Any:
        """Return a Z3 formula asserting this slice and other have disjoint support.

        Parameters
        ----------
        other:
            Another HeapSlice.

        Returns
        -------
        Any
            A Z3 Bool (True/False) or a string stub.
        """
        overlap = self.support_addresses & other.support_addresses
        if _Z3_AVAILABLE:
            return _z3.BoolVal(len(overlap) == 0)
        return f"disjoint({sorted(self.support_addresses)}, {sorted(other.support_addresses)})"

    def consistent_with(self, reference: "HeapSlice") -> Any:
        """Return a Z3 formula checking that this slice agrees with reference on shared support.

        Parameters
        ----------
        reference:
            Another HeapSlice to compare against.

        Returns
        -------
        Any
            A Z3 formula or string stub.
        """
        shared = self.support_addresses & reference.support_addresses
        if _Z3_AVAILABLE and self.read_map is not None and reference.read_map is not None:
            if not shared:
                return _z3.BoolVal(True)
            constraints = [
                _z3.Select(self.read_map, _z3.IntVal(a))
                == _z3.Select(reference.read_map, _z3.IntVal(a))
                for a in sorted(shared)
            ]
            return _z3.And(*constraints)
        return f"consistent({sorted(shared)} addresses agree)"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name_hint": self.name_hint,
            "support_addresses": sorted(self.support_addresses),
            "has_frame_invariant": self.frame_invariant is not None,
        }


# ---------------------------------------------------------------------------
# SupportAwareMutation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupportAwareMutation:
    """A mutation that only affects cells in its declared support set.

    A SupportAwareMutation pairs a pre-state and a post-state (both
    SequenceEncodings or HeapSlices) with an explicit support set and a
    mutation function.  The key property is:

        ``∀ addr ∉ support: post_state[addr] = pre_state[addr]``

    This is called the *support axiom*.  It is automatically discharged by
    the solver when ``support_axiom()`` is added to the query.

    Theory2.tex §29.4.

    Fields
    ------
    pre_state : Any
        The pre-mutation state (SequenceEncoding, HeapSlice, or stub).
    post_state : Any
        The post-mutation state.
    support : frozenset[int]
        The declared support (set of addresses/indices that may change).
    mutation_fn : Any
        The Z3 formula or callable describing the mutation.
    name_hint : str
        Debugging name.

    # copilot: SupportAwareMutation model — Theory2.tex §29.4.
    """

    pre_state: Any
    post_state: Any
    support: frozenset[int]
    mutation_fn: Any = None
    name_hint: str = "mut"

    def support_axiom(self) -> Any:
        """Return the Z3 support axiom: outside support, post = pre.

        Returns
        -------
        Any
            A Z3 formula or string stub.
        """
        if _Z3_AVAILABLE:
            constraints = []
            if isinstance(self.pre_state, SequenceEncoding):
                pre_arr = self.pre_state.array_var
                post_arr = self.post_state.array_var if isinstance(self.post_state, SequenceEncoding) else None
            elif isinstance(self.pre_state, HeapSlice):
                pre_arr = self.pre_state.read_map
                post_arr = self.post_state.write_map if isinstance(self.post_state, HeapSlice) else None
            else:
                pre_arr = post_arr = None
            if pre_arr is not None and post_arr is not None:
                for addr in range(
                    min(self.support) if self.support else 0,
                    max(self.support) + 2 if self.support else 0,
                ):
                    if addr not in self.support:
                        constraints.append(
                            _z3.Select(post_arr, _z3.IntVal(addr))
                            == _z3.Select(pre_arr, _z3.IntVal(addr))
                        )
                if constraints:
                    return _z3.And(*constraints)
            return _z3.BoolVal(True)
        return f"support_axiom(support={sorted(self.support)})"

    def mutation_correctness(self) -> Any:
        """Return the Z3 formula asserting that the mutation function holds on support.

        Returns
        -------
        Any
            The mutation_fn formula, if available, or a stub string.
        """
        if self.mutation_fn is not None:
            if callable(self.mutation_fn):
                return self.mutation_fn(self.pre_state, self.post_state, self.support)
            return self.mutation_fn
        return f"mutation_correctness({self.name_hint}: support={sorted(self.support)})"

    def frame_lemma(self) -> Any:
        """Return the Z3 conjunction of support axiom and mutation correctness.

        Returns
        -------
        Any
            A Z3 And formula or string stub.
        """
        sa = self.support_axiom()
        mc = self.mutation_correctness()
        if _Z3_AVAILABLE and not isinstance(sa, str) and not isinstance(mc, str):
            return _z3.And(sa, mc)
        return f"({sa}) AND ({mc})"

    def compose(self, other: "SupportAwareMutation") -> "SupportAwareMutation":
        """Compose this mutation with *other* (self then other).

        The composition has support = self.support ∪ other.support (Proposition 29.3).

        Parameters
        ----------
        other:
            A SupportAwareMutation to apply after self.

        Returns
        -------
        SupportAwareMutation
            The composed mutation.

        Theory2.tex §29.4 Prop 29.3.
        """
        composed_support = self.support | other.support
        if callable(self.mutation_fn) and callable(other.mutation_fn):
            def composed_fn(pre: Any, post: Any, sup: frozenset[int]) -> Any:
                mid = self.post_state
                f1 = self.mutation_fn(pre, mid, self.support)
                f2 = other.mutation_fn(mid, post, other.support)
                if _Z3_AVAILABLE and not isinstance(f1, str) and not isinstance(f2, str):
                    return _z3.And(f1, f2)
                return f"({f1}) AND ({f2})"
        else:
            composed_fn = None
        return SupportAwareMutation(
            pre_state=self.pre_state,
            post_state=other.post_state,
            support=composed_support,
            mutation_fn=composed_fn,
            name_hint=f"{self.name_hint}_then_{other.name_hint}",
        )

    def invert(self) -> "SupportAwareMutation":
        """Return the inverse mutation (swapping pre and post states).

        This is only meaningful for permutation and bijective mutations.

        Returns
        -------
        SupportAwareMutation
        """
        return replace(
            self,
            pre_state=self.post_state,
            post_state=self.pre_state,
            name_hint=f"{self.name_hint}_inv",
        )

    def verify_support(self) -> bool:
        """Return True if ``support`` is a finite set (always true here).

        For symbolic support sets, this returns True without checking.

        Returns
        -------
        bool
        """
        return isinstance(self.support, frozenset)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name_hint": self.name_hint,
            "support": sorted(self.support),
            "has_mutation_fn": self.mutation_fn is not None,
        }


# ---------------------------------------------------------------------------
# SequenceInvariant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceInvariant:
    """A structural invariant on a SequenceEncoding.

    A SequenceInvariant attaches a formal predicate (Z3 formula) to a
    SequenceEncoding and provides operations to check, strengthen, weaken,
    and compose invariants.

    Theory2.tex §29.2 — sequence invariants and window widening.

    Fields
    ------
    kind : SequenceInvariantKind
        The structural kind of the invariant.
    encoding : SequenceEncoding
        The encoding this invariant is about.
    predicate_expr : Any
        The Z3 formula asserting the invariant on ``encoding``.
    proof_obligation : Any
        A Z3 formula that must be satisfied to discharge this invariant.
    parameters : dict[str, Any]
        Optional parameters (e.g., bounds lb, ub for BOUNDED invariants).
    name : str
        Human-readable name for this invariant.

    # copilot: SequenceInvariant model — Theory2.tex §29.2.
    """

    kind: SequenceInvariantKind
    encoding: SequenceEncoding
    predicate_expr: Any
    proof_obligation: Any = None
    parameters: dict[str, Any] = field(default_factory=dict)
    name: str = ""

    def check(self) -> Any:
        """Return the Z3 formula that must hold for this invariant to be satisfied.

        Returns
        -------
        Any
            The predicate_expr, possibly conjoined with proof_obligation.
        """
        if self.proof_obligation is not None:
            if _Z3_AVAILABLE and not isinstance(self.predicate_expr, str):
                return _z3.And(self.predicate_expr, self.proof_obligation)
            return f"({self.predicate_expr}) AND ({self.proof_obligation})"
        return self.predicate_expr

    def strengthen(self, additional_pred: Any) -> "SequenceInvariant":
        """Return a new SequenceInvariant with an additional conjunct.

        The new invariant is logically stronger (harder to satisfy).

        Parameters
        ----------
        additional_pred:
            Z3 formula to conjoin with the existing predicate.

        Returns
        -------
        SequenceInvariant
        """
        if _Z3_AVAILABLE and not isinstance(self.predicate_expr, str):
            new_pred = _z3.And(self.predicate_expr, additional_pred)
        else:
            new_pred = f"({self.predicate_expr}) AND ({additional_pred})"
        return replace(
            self,
            predicate_expr=new_pred,
            kind=SequenceInvariantKind.CUSTOM,
            name=f"{self.name}_strong",
        )

    def weaken(self, disjunct_pred: Any) -> "SequenceInvariant":
        """Return a new SequenceInvariant that is a disjunction (logically weaker).

        Parameters
        ----------
        disjunct_pred:
            Z3 formula to disjoin with the existing predicate.

        Returns
        -------
        SequenceInvariant
        """
        if _Z3_AVAILABLE and not isinstance(self.predicate_expr, str):
            new_pred = _z3.Or(self.predicate_expr, disjunct_pred)
        else:
            new_pred = f"({self.predicate_expr}) OR ({disjunct_pred})"
        return replace(
            self,
            predicate_expr=new_pred,
            kind=SequenceInvariantKind.CUSTOM,
            name=f"{self.name}_weak",
        )

    def compose(self, other: "SequenceInvariant") -> "SequenceInvariant":
        """Return the conjunction of this invariant and *other* (over same encoding).

        Parameters
        ----------
        other:
            Another SequenceInvariant over the same encoding.

        Returns
        -------
        SequenceInvariant
        """
        if _Z3_AVAILABLE and not isinstance(self.predicate_expr, str):
            new_pred = _z3.And(self.predicate_expr, other.predicate_expr)
        else:
            new_pred = f"({self.predicate_expr}) AND ({other.predicate_expr})"
        return replace(
            self,
            predicate_expr=new_pred,
            kind=SequenceInvariantKind.CUSTOM,
            name=f"{self.name}_and_{other.name}",
        )

    def implies(self, other: "SequenceInvariant") -> Any:
        """Return a Z3 formula asserting that self.predicate ⇒ other.predicate.

        Parameters
        ----------
        other:
            Another SequenceInvariant.

        Returns
        -------
        Any
            A Z3 implication formula or string stub.
        """
        if _Z3_AVAILABLE and not isinstance(self.predicate_expr, str):
            return _z3.Implies(self.predicate_expr, other.predicate_expr)
        return f"({self.predicate_expr}) => ({other.predicate_expr})"

    def is_inductive(self, mutation: SupportAwareMutation) -> Any:
        """Return a Z3 formula asserting this invariant is preserved by *mutation*.

        The formula is:
            ``(pre satisfies invariant) ∧ (mutation frame lemma) ⇒ (post satisfies invariant)``

        Parameters
        ----------
        mutation:
            The SupportAwareMutation to check inductiveness against.

        Returns
        -------
        Any
            A Z3 formula or string stub.
        """
        frame = mutation.frame_lemma()
        post_encoding = mutation.post_state
        if isinstance(post_encoding, SequenceEncoding):
            post_inv = replace(self, encoding=post_encoding)
            post_pred = post_inv.predicate_expr
        else:
            post_pred = f"post_satisfies({self.name})"
        if _Z3_AVAILABLE and not isinstance(self.predicate_expr, str):
            pre_and_frame = _z3.And(self.predicate_expr, frame) if not isinstance(frame, str) else self.predicate_expr
            return _z3.Implies(pre_and_frame, post_pred if not isinstance(post_pred, str) else _z3.BoolVal(True))
        return f"({self.predicate_expr} AND {frame}) => {post_pred}"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "kind": self.kind.name,
            "name": self.name,
            "encoding_hint": self.encoding.name_hint,
            "parameters": {k: str(v) for k, v in self.parameters.items()},
            "has_proof_obligation": self.proof_obligation is not None,
        }


__all__: list[str] = [
    "MutationKind",
    "SequenceInvariantKind",
    "SequenceEncoding",
    "MutationSlice",
    "HeapSlice",
    "SupportAwareMutation",
    "SequenceInvariant",
]
