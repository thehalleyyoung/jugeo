"""heap_slice_encoder.py — Heap-slice encoder.

Theory2.tex Chapter 29 §4: Heap-slice encoder — localized heap summaries with
support-bounded frame axioms.

This module implements ``HeapSliceEncoder``, the fourth encoding layer in
Chapter 29.  The key concepts are:

*   **Heap**: an array ``Array(Addr, Cell)`` where ``Addr = IntSort`` and
    ``Cell`` is a Z3 datatype.
*   **HeapSlice**: a heap summary restricted to a finite support set ``S``.
    Only addresses in ``S`` are characterised; all others are declared equal
    to the pre-state by the frame axiom.
*   **Frame axiom**: ``∀ addr ∉ S: post_heap[addr] = pre_heap[addr]``
*   **Footprint**: the minimal support needed for a mutation — the set of
    addresses that the mutation provably reads or writes.

Correctness guarantee
---------------------
A HeapSlice encoding is *sound* iff the frame axiom is discharged by the
solver.  The encoder generates the frame axiom automatically; the caller
is responsible for adding it to the Z3 solver query.

Fragment discipline
-------------------
Heap encodings with integer addresses and enumerated cell types typically
fall in ``QF_AUFLIA``.  Complex cell sorts (e.g., recursive datatypes) may
push the fragment to ``QF_AUFBV`` or beyond.

# copilot: HeapSliceEncoder — Theory2.tex §29.4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
# Fragment imports (optional)
# ---------------------------------------------------------------------------
try:
    from jugeo.solver.fragments import Fragment

    _FRAGMENTS_AVAILABLE = True
except ImportError:
    Fragment = None  # type: ignore[assignment,misc]
    _FRAGMENTS_AVAILABLE = False

from jugeo.encodings.sequence_mutation_encodings.models import HeapSlice


# ---------------------------------------------------------------------------
# EncodedHeapSlice result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EncodedHeapSlice:
    """Result of encoding a heap slice via HeapSliceEncoder.

    Fields
    ------
    heap_slice : HeapSlice
        The HeapSlice model.
    frame_axiom : Any
        The Z3 frame axiom formula (must be added to the solver).
    support_axioms : tuple[Any, ...]
        Z3 formulas asserting the values at each support address.
    pre_heap : Any
        The Z3 array for the pre-state heap.
    post_heap : Any
        The Z3 array for the post-state heap.
    cell_sort : Any
        The Z3 sort for heap cells.
    name : str
        Debugging name.
    footprint_size : int
        Size of the support set.

    # copilot: EncodedHeapSlice result dataclass — HeapSliceEncoder output.
    """

    heap_slice: HeapSlice
    frame_axiom: Any
    support_axioms: tuple[Any, ...]
    pre_heap: Any
    post_heap: Any
    cell_sort: Any
    name: str = "heap"
    footprint_size: int = 0

    def all_constraints(self) -> list[Any]:
        """Return all constraints: frame axiom + support axioms.

        Returns
        -------
        list[Any]
        """
        constraints = list(self.support_axioms)
        if self.frame_axiom is not None:
            constraints.append(self.frame_axiom)
        return constraints

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "footprint_size": self.footprint_size,
            "num_support_axioms": len(self.support_axioms),
            "support": sorted(self.heap_slice.support_addresses),
        }


# ---------------------------------------------------------------------------
# HeapSliceEncoder
# ---------------------------------------------------------------------------


class HeapSliceEncoder:
    """Encodes localized heap summaries with automatic frame axiom generation.

    This encoder implements Chapter 29 §4.  It maintains a *heap registry*
    mapping heap name strings to Z3 array variables, so that multiple slices
    over the same logical heap can share array variables.

    Parameters
    ----------
    name_prefix : str
        Prefix for all generated Z3 symbol names.
    addr_sort : Any
        Z3 sort for heap addresses (default IntSort).

    Theory2.tex §29.4.

    # copilot: HeapSliceEncoder — Theory2.tex §29.4.
    """

    def __init__(
        self,
        name_prefix: str = "hse",
        addr_sort: Any = None,
    ) -> None:
        """Initialise the heap-slice encoder.

        Parameters
        ----------
        name_prefix:
            Prefix for Z3 symbol names.
        addr_sort:
            Z3 sort for addresses.  Defaults to IntSort.
        """
        self._prefix = name_prefix
        self._counter = 0
        self._heap_registry: dict[str, Any] = {}
        if _Z3_AVAILABLE:
            self._addr_sort = addr_sort or _z3.IntSort()
        else:
            self._addr_sort = addr_sort or "Int"
        self._slices: list[EncodedHeapSlice] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode_heap_slice(
        self,
        heap_arr: Any,
        support_set: frozenset[int],
        cell_sort: Any,
        name: str | None = None,
    ) -> EncodedHeapSlice:
        """Encode a heap slice: a heap summary restricted to *support_set*.

        Creates:
        *   ``pre_heap`` array (the input *heap_arr*)
        *   ``post_heap`` array (fresh Z3 array for the post-state)
        *   ``frame_axiom``: ``∀ addr ∉ support_set: post_heap[addr] = pre_heap[addr]``

        Parameters
        ----------
        heap_arr:
            A Z3 ``Array(Addr, Cell)`` for the pre-state heap.
        support_set:
            The finite set of addresses in this slice.
        cell_sort:
            Z3 sort for heap cells.
        name:
            Optional name for this slice.

        Returns
        -------
        EncodedHeapSlice
            The encoding result.

        Theory2.tex §29.4 Definition 29.4.

        # copilot: encode_heap_slice — heap summary with frame axiom.
        """
        slice_name = name or self._fresh_name("slice")
        if _Z3_AVAILABLE:
            z3_csort = self._resolve_cell_sort(cell_sort)
            post_arr = _z3.Array(f"{slice_name}_post", self._addr_sort, z3_csort)
            fa = self.encode_frame_axiom(heap_arr, post_arr, support_set)
            support_axs: list[Any] = []
            hs = HeapSlice(
                support_addresses=support_set,
                cell_sort=z3_csort,
                read_map=heap_arr,
                write_map=post_arr,
                frame_invariant=fa,
                name_hint=slice_name,
            )
            result = EncodedHeapSlice(
                heap_slice=hs,
                frame_axiom=fa,
                support_axioms=tuple(support_axs),
                pre_heap=heap_arr,
                post_heap=post_arr,
                cell_sort=z3_csort,
                name=slice_name,
                footprint_size=len(support_set),
            )
        else:
            post_arr = f"{slice_name}_post:Array(Addr,Cell)"
            fa = (
                f"ForAll addr not in {sorted(support_set)}: "
                f"{slice_name}_post[addr]={slice_name}_pre[addr]"
            )
            hs = HeapSlice(
                support_addresses=support_set,
                cell_sort=cell_sort,
                read_map=heap_arr,
                write_map=post_arr,
                frame_invariant=fa,
                name_hint=slice_name,
            )
            result = EncodedHeapSlice(
                heap_slice=hs,
                frame_axiom=fa,
                support_axioms=(),
                pre_heap=heap_arr,
                post_heap=post_arr,
                cell_sort=cell_sort,
                name=slice_name,
                footprint_size=len(support_set),
            )
        self._slices.append(result)
        return result

    def encode_frame_axiom(
        self,
        pre: Any,
        post: Any,
        support: frozenset[int],
    ) -> Any:
        """Encode the frame axiom for a mutation with the given support.

        Returns:
            ``∀ addr: addr ∉ support → post[addr] = pre[addr]``

        Parameters
        ----------
        pre:
            Z3 array for the pre-state heap.
        post:
            Z3 array for the post-state heap.
        support:
            The set of addresses that may differ between pre and post.

        Returns
        -------
        Any
            A Z3 ForAll formula or string stub.

        Theory2.tex §29.4 — frame axiom definition.

        # copilot: encode_frame_axiom — the central correctness guarantee of §29.4.
        """
        if _Z3_AVAILABLE and not isinstance(pre, str):
            addr = _z3.Int("_frame_addr")
            if support:
                in_support = _z3.Or(*[addr == _z3.IntVal(a) for a in support])
            else:
                in_support = _z3.BoolVal(False)
            return _z3.ForAll(
                [addr],
                _z3.Implies(
                    _z3.Not(in_support),
                    _z3.Select(post, addr) == _z3.Select(pre, addr),
                ),
            )
        return (
            f"ForAll addr not in {sorted(support)}: "
            f"post[addr] = pre[addr]"
        )

    def encode_read(
        self,
        heap: Any,
        addr: Any,
        cell_sort: Any,
    ) -> Any:
        """Encode a heap read: return ``heap[addr]``.

        Parameters
        ----------
        heap:
            A Z3 array expression.
        addr:
            The address to read (Z3 IntExpr or Python int).
        cell_sort:
            The expected Z3 sort for the cell value.

        Returns
        -------
        Any
            ``z3.Select(heap, addr)`` or a string stub.

        Theory2.tex §29.4 — heap read.

        # copilot: encode_read — heap array select.
        """
        if _Z3_AVAILABLE and not isinstance(heap, str):
            z3_addr = _z3.IntVal(addr) if isinstance(addr, int) else addr
            return _z3.Select(heap, z3_addr)
        return f"heap[{addr}]"

    def encode_write(
        self,
        heap: Any,
        addr: Any,
        val: Any,
        cell_sort: Any,
    ) -> tuple[Any, Any]:
        """Encode a heap write: return ``(new_heap, frame_axiom)``.

        Produces a fresh array ``new_heap`` such that:
        *   ``new_heap[addr] = val``
        *   ``∀ other ≠ addr: new_heap[other] = heap[other]``

        The frame axiom is included in the return value.

        Parameters
        ----------
        heap:
            The pre-write heap array.
        addr:
            The address to write (Z3 IntExpr or Python int).
        val:
            The value to write (Z3 expression).
        cell_sort:
            Z3 sort for cell values.

        Returns
        -------
        tuple[Any, Any]
            ``(new_heap, write_frame_axiom)``

        Theory2.tex §29.4 — heap write.

        # copilot: encode_write — heap array store with single-cell frame.
        """
        if _Z3_AVAILABLE and not isinstance(heap, str):
            z3_addr = _z3.IntVal(addr) if isinstance(addr, int) else addr
            new_heap = _z3.Store(heap, z3_addr, val)
            addr_int = addr if isinstance(addr, int) else -1
            fa = self.encode_frame_axiom(
                heap,
                new_heap,
                frozenset([addr_int]) if addr_int >= 0 else frozenset(),
            )
            return new_heap, fa
        new_heap = f"Store(heap, {addr}, {val})"
        fa = f"ForAll a != {addr}: new_heap[a] = heap[a]"
        return new_heap, fa

    def encode_disjoint_slices(
        self,
        slice1: EncodedHeapSlice,
        slice2: EncodedHeapSlice,
    ) -> Any:
        """Return a Z3 formula asserting that two slices have disjoint support.

        Parameters
        ----------
        slice1:
            First encoded heap slice.
        slice2:
            Second encoded heap slice.

        Returns
        -------
        Any
            A Z3 Bool formula (True iff supports are disjoint) or string stub.

        Theory2.tex §29.4 — disjoint heap slices.

        # copilot: encode_disjoint_slices — support disjointness check.
        """
        overlap = slice1.heap_slice.support_addresses & slice2.heap_slice.support_addresses
        if _Z3_AVAILABLE:
            return _z3.BoolVal(len(overlap) == 0)
        return (
            f"disjoint({sorted(slice1.heap_slice.support_addresses)}, "
            f"{sorted(slice2.heap_slice.support_addresses)})"
        )

    def encode_slice_merge(
        self,
        slice1: EncodedHeapSlice,
        slice2: EncodedHeapSlice,
        heap: Any,
    ) -> EncodedHeapSlice:
        """Merge two disjoint heap slices into one.

        Precondition: the two slices must have disjoint support (checked at runtime).

        Parameters
        ----------
        slice1:
            First encoded heap slice.
        slice2:
            Second encoded heap slice.
        heap:
            The base heap array (pre-state).

        Returns
        -------
        EncodedHeapSlice
            The merged slice.

        Theory2.tex §29.4 Lemma 29.2 — merge of disjoint slices.

        # copilot: encode_slice_merge — Theory2.tex §29.4 Lemma 29.2.
        """
        merged_support = (
            slice1.heap_slice.support_addresses | slice2.heap_slice.support_addresses
        )
        overlap = (
            slice1.heap_slice.support_addresses & slice2.heap_slice.support_addresses
        )
        if overlap:
            logger.warning(
                "encode_slice_merge: overlapping support %s — result may be unsound",
                sorted(overlap),
            )
        merged_hs = slice1.heap_slice.merge_slices(slice2.heap_slice)
        merged_fa = self.encode_frame_axiom(heap, merged_hs.write_map, merged_support)
        return EncodedHeapSlice(
            heap_slice=merged_hs,
            frame_axiom=merged_fa,
            support_axioms=slice1.support_axioms + slice2.support_axioms,
            pre_heap=heap,
            post_heap=merged_hs.write_map,
            cell_sort=slice1.cell_sort,
            name=f"{slice1.name}_merged_{slice2.name}",
            footprint_size=len(merged_support),
        )

    def encode_footprint_bound(
        self,
        mutation_spec: dict[int, Any],
        support: frozenset[int],
    ) -> Any:
        """Encode a footprint-bound formula: all writes are within *support*.

        For each address in ``mutation_spec``, asserts ``addr ∈ support``.
        This encodes the contract: "this mutation only touches the declared
        support set."

        Parameters
        ----------
        mutation_spec:
            Mapping from address to new value.
        support:
            The declared support set.

        Returns
        -------
        Any
            A Z3 And formula or string stub.

        Theory2.tex §29.4 — footprint bound.

        # copilot: encode_footprint_bound — mutation stays within support.
        """
        violations = [addr for addr in mutation_spec if addr not in support]
        if _Z3_AVAILABLE:
            if violations:
                return _z3.BoolVal(False)
            return _z3.BoolVal(True)
        if violations:
            return f"VIOLATION: addresses {violations} not in support {sorted(support)}"
        return f"footprint_ok: all writes in support {sorted(support)}"

    def encode_heap_equivalence(
        self,
        h1: Any,
        h2: Any,
        support: frozenset[int],
    ) -> Any:
        """Encode that two heaps agree on all addresses in *support*.

        Returns ``∀ addr ∈ support: h1[addr] = h2[addr]``

        Parameters
        ----------
        h1:
            First Z3 heap array.
        h2:
            Second Z3 heap array.
        support:
            The set of addresses to check.

        Returns
        -------
        Any
            A Z3 formula or string stub.

        Theory2.tex §29.4 — heap slice equivalence.

        # copilot: encode_heap_equivalence — slice-level heap equality.
        """
        if _Z3_AVAILABLE and not isinstance(h1, str) and not isinstance(h2, str):
            if not support:
                return _z3.BoolVal(True)
            constraints = [
                _z3.Select(h1, _z3.IntVal(a)) == _z3.Select(h2, _z3.IntVal(a))
                for a in sorted(support)
            ]
            return _z3.And(*constraints)
        return (
            f"ForAll addr in {sorted(support)}: h1[addr] = h2[addr]"
        )

    def copilot_suggest_footprint(
        self,
        mutation_ast: dict[str, Any],
    ) -> frozenset[int]:
        """Suggest a footprint (support set) for a mutation, given its AST.

        This is the *copilot* interface for footprint estimation.  It inspects
        the mutation AST and heuristically estimates the set of addresses that
        the mutation touches.

        Heuristics (applied in order):
        1.  If ``mutation_ast`` contains a ``"writes"`` key listing addresses,
            return those directly.
        2.  If ``mutation_ast`` contains a ``"range"`` key ``(lo, hi)``, return
            ``frozenset(range(lo, hi))``.
        3.  If ``mutation_ast`` contains a ``"single_write"`` key, return a
            singleton set.
        4.  If none of the above, return an empty frozenset with a warning.

        Parameters
        ----------
        mutation_ast:
            A dict describing the mutation.  Recognised keys:
            - ``"writes"``: list[int] — explicit write addresses
            - ``"range"``: tuple[int, int] — (lo, hi) range
            - ``"single_write"``: int — single address
            - ``"reads"``: list[int] — read addresses (included in footprint)

        Returns
        -------
        frozenset[int]
            The estimated footprint.

        Theory2.tex §29.4 Remark 29.4 — copilot footprint suggestion.

        # copilot: copilot_suggest_footprint — heuristic footprint for HeapSlice.
        """
        footprint: set[int] = set()
        if "writes" in mutation_ast:
            try:
                footprint.update(int(a) for a in mutation_ast["writes"])
            except (TypeError, ValueError):
                pass
        if "single_write" in mutation_ast:
            try:
                footprint.add(int(mutation_ast["single_write"]))
            except (TypeError, ValueError):
                pass
        if "range" in mutation_ast:
            try:
                lo, hi = mutation_ast["range"]
                footprint.update(range(int(lo), int(hi)))
            except (TypeError, ValueError):
                pass
        if "reads" in mutation_ast:
            try:
                footprint.update(int(a) for a in mutation_ast["reads"])
            except (TypeError, ValueError):
                pass
        if not footprint:
            logger.warning(
                "copilot_suggest_footprint: no footprint heuristics matched in AST %s",
                mutation_ast,
            )
        return frozenset(footprint)

    def register_heap(self, heap_name: str, heap_arr: Any) -> None:
        """Register a named heap array in the internal registry.

        This allows multiple slices to reference the same heap by name.

        Parameters
        ----------
        heap_name:
            Logical name for the heap.
        heap_arr:
            The Z3 array variable.
        """
        self._heap_registry[heap_name] = heap_arr

    def get_heap(self, heap_name: str) -> Any | None:
        """Retrieve a registered heap array by name.

        Parameters
        ----------
        heap_name:
            Logical name for the heap.

        Returns
        -------
        Any or None
        """
        return self._heap_registry.get(heap_name)

    def make_heap_array(self, name: str, cell_sort: Any) -> Any:
        """Create a fresh Z3 heap array variable.

        Parameters
        ----------
        name:
            Z3 symbol name for the array.
        cell_sort:
            Z3 sort for heap cells.

        Returns
        -------
        Any
            A Z3 Array(Addr, Cell) or string stub.
        """
        if _Z3_AVAILABLE:
            z3_csort = self._resolve_cell_sort(cell_sort)
            arr = _z3.Array(name, self._addr_sort, z3_csort)
            self._heap_registry[name] = arr
            return arr
        stub = f"{name}:Array(Addr,{cell_sort})"
        self._heap_registry[name] = stub
        return stub

    def recent_slices(self) -> list[EncodedHeapSlice]:
        """Return all slices encoded by this encoder instance.

        Returns
        -------
        list[EncodedHeapSlice]
        """
        return list(self._slices)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fresh_name(self, prefix: str) -> str:
        self._counter += 1
        return f"{self._prefix}_{prefix}_{self._counter}"

    def _resolve_cell_sort(self, cell_sort: Any) -> Any:
        if not _Z3_AVAILABLE:
            return cell_sort
        if isinstance(cell_sort, str):
            mapping: dict[str, Any] = {
                "int": _z3.IntSort(),
                "bool": _z3.BoolSort(),
                "real": _z3.RealSort(),
                "cell": _z3.IntSort(),  # default cell = Int
            }
            return mapping.get(cell_sort.lower(), _z3.IntSort())
        if hasattr(cell_sort, "kind"):
            return cell_sort
        return _z3.IntSort()


__all__: list[str] = [
    "HeapSliceEncoder",
    "EncodedHeapSlice",
]
