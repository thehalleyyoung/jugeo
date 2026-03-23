"""sequence_window_encoder.py — Sequence-window encoder.

Theory2.tex Chapter 29 §2: Sequence-window encoder — window/slice predicates.

This module implements ``SequenceWindowEncoder``, the second encoding layer in
Chapter 29.  A *window predicate* has the form:

    ``∀ i ∈ [lo, hi): P(arr[i])``

and is encoded as a bounded universally quantified Z3 formula.

Key operations
--------------
*  **Window predicate**: quantify over a slice.
*  **Window conjunction**: apply multiple predicates to the same window.
*  **Sorted window**: ``arr[i] ≤ arr[i+1]`` for i in [lo, hi-1).
*  **Distinct window**: ``arr[i] ≠ arr[j]`` for i ≠ j in [lo, hi).
*  **Bounded window**: ``lb ≤ arr[i] ≤ ub`` for i in [lo, hi).
*  **Split window**: ``P([lo, mid)) ∧ P([mid, hi))  ↔  P([lo, hi))``
*  **Shift window**: replace ``arr`` with ``arr[·+offset]``.
*  **Copilot invariant guesser**: given sample arrays, suggest a predicate.

Fragment discipline
-------------------
*  Single-predicate windows (e.g., bounded, non-negative) typically stay in
   ``QF_AUFLIA`` when the predicate is quantifier-free.
*  Sorted and distinct windows require universal quantifiers and may push the
   query into ``AUFLIA`` (with quantifiers).

# copilot: SequenceWindowEncoder — Theory2.tex §29.2.
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

from jugeo.encodings.sequence_mutation_encodings.models import SequenceEncoding


# ---------------------------------------------------------------------------
# WindowPredicate result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowPredicate:
    """Represents an encoded window predicate over an array slice.

    A WindowPredicate is the result of encoding ``∀ i ∈ [lo, hi): P(arr[i])``.

    Fields
    ------
    formula : Any
        The Z3 ForAll formula (or string stub).
    arr : Any
        The Z3 array this predicate is about.
    lo : Any
        Lower bound (inclusive) of the window.
    hi : Any
        Upper bound (exclusive) of the window.
    pred_name : str
        Human-readable name for the predicate.
    fragment_hint : str
        Suggested Z3 fragment: ``"QF_AUFLIA"`` or ``"AUFLIA"``.

    # copilot: WindowPredicate dataclass — result of window encoding.
    """

    formula: Any
    arr: Any
    lo: Any
    hi: Any
    pred_name: str = "pred"
    fragment_hint: str = "QF_AUFLIA"

    def negate(self) -> "WindowPredicate":
        """Return a WindowPredicate whose formula is the negation of self.

        Returns
        -------
        WindowPredicate
        """
        if _Z3_AVAILABLE and not isinstance(self.formula, str):
            return WindowPredicate(
                formula=_z3.Not(self.formula),
                arr=self.arr,
                lo=self.lo,
                hi=self.hi,
                pred_name=f"not_{self.pred_name}",
                fragment_hint=self.fragment_hint,
            )
        return WindowPredicate(
            formula=f"NOT ({self.formula})",
            arr=self.arr,
            lo=self.lo,
            hi=self.hi,
            pred_name=f"not_{self.pred_name}",
            fragment_hint=self.fragment_hint,
        )

    def and_(self, other: "WindowPredicate") -> "WindowPredicate":
        """Return a WindowPredicate that is the conjunction of self and other.

        Parameters
        ----------
        other:
            Another WindowPredicate (typically over the same window).

        Returns
        -------
        WindowPredicate
        """
        if _Z3_AVAILABLE and not isinstance(self.formula, str):
            return WindowPredicate(
                formula=_z3.And(self.formula, other.formula),
                arr=self.arr,
                lo=self.lo,
                hi=self.hi,
                pred_name=f"{self.pred_name}_and_{other.pred_name}",
                fragment_hint=self.fragment_hint,
            )
        return WindowPredicate(
            formula=f"({self.formula}) AND ({other.formula})",
            arr=self.arr,
            lo=self.lo,
            hi=self.hi,
            pred_name=f"{self.pred_name}_and_{other.pred_name}",
            fragment_hint=self.fragment_hint,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "pred_name": self.pred_name,
            "lo": str(self.lo),
            "hi": str(self.hi),
            "fragment_hint": self.fragment_hint,
        }


# ---------------------------------------------------------------------------
# SequenceWindowEncoder
# ---------------------------------------------------------------------------


class SequenceWindowEncoder:
    """Encodes window/slice predicates over Z3 arrays.

    A *window predicate* is a formula of the form:
        ``∀ i ∈ [lo, hi): P(arr[i])``

    This encoder supports single predicates, conjunctions, sortedness,
    distinctness, and bounded-value constraints.

    Parameters
    ----------
    name_prefix : str
        Prefix for Z3 bound variables created by this encoder.
    use_bounded_quantifiers : bool
        When True, emit bounded ``∀ i ∈ [lo, hi)`` directly.  When False,
        emit unbounded ``∀ i: (lo ≤ i < hi) → P(arr[i])`` which is more
        compatible with QF fragments after quantifier elimination.

    Theory2.tex §29.2.

    # copilot: SequenceWindowEncoder — Theory2.tex §29.2.
    """

    def __init__(
        self,
        name_prefix: str = "win",
        use_bounded_quantifiers: bool = True,
    ) -> None:
        """Initialise the window encoder.

        Parameters
        ----------
        name_prefix:
            Prefix string for all Z3 bound variable names.
        use_bounded_quantifiers:
            Whether to emit bounded quantifiers (default True).
        """
        self._prefix = name_prefix
        self._bounded = use_bounded_quantifiers
        self._counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode_window_predicate(
        self,
        arr: Any,
        lo: Any,
        hi: Any,
        elem_pred: Callable[[Any], Any] | Any,
        pred_name: str = "P",
    ) -> WindowPredicate:
        """Encode ``∀ i ∈ [lo, hi): elem_pred(arr[i])`` as a Z3 formula.

        Parameters
        ----------
        arr:
            A Z3 array expression or string stub.
        lo:
            Lower bound (inclusive).  Z3 IntExpr or Python int.
        hi:
            Upper bound (exclusive).  Z3 IntExpr or Python int.
        elem_pred:
            A callable ``(Z3Expr) → Z3BoolExpr`` applied to each array element,
            or a Z3 lambda expression.
        pred_name:
            Human-readable name for the predicate (for debugging).

        Returns
        -------
        WindowPredicate
            The encoded window predicate.

        Theory2.tex §29.2 Definition 29.2.

        # copilot: encode_window_predicate — universal window quantification.
        """
        i_name = self._fresh_var("i")
        if _Z3_AVAILABLE and not isinstance(arr, str):
            i = _z3.Int(i_name)
            lo_z3 = _z3.IntVal(lo) if isinstance(lo, int) else lo
            hi_z3 = _z3.IntVal(hi) if isinstance(hi, int) else hi
            in_range = _z3.And(i >= lo_z3, i < hi_z3)
            elem = _z3.Select(arr, i)
            if callable(elem_pred):
                body = elem_pred(elem)
            else:
                body = elem_pred
            formula = _z3.ForAll([i], _z3.Implies(in_range, body))
            frag = "AUFLIA"
        else:
            formula = f"ForAll {i_name} in [{lo},{hi}): {pred_name}(arr[{i_name}])"
            frag = "AUFLIA"
        return WindowPredicate(
            formula=formula,
            arr=arr,
            lo=lo,
            hi=hi,
            pred_name=pred_name,
            fragment_hint=frag,
        )

    def encode_window_conjunction(
        self,
        arr: Any,
        lo: Any,
        hi: Any,
        preds: Sequence[Callable[[Any], Any]],
        pred_names: Sequence[str] | None = None,
    ) -> WindowPredicate:
        """Encode a conjunction of window predicates over the same slice.

        Returns ``∀ i ∈ [lo, hi): P0(arr[i]) ∧ P1(arr[i]) ∧ …``

        Parameters
        ----------
        arr:
            A Z3 array expression.
        lo:
            Lower bound (inclusive).
        hi:
            Upper bound (exclusive).
        preds:
            A sequence of callables, each ``(Z3Expr) → Z3BoolExpr``.
        pred_names:
            Optional names for each predicate (for debugging).

        Returns
        -------
        WindowPredicate
            The conjunction of all window predicates.

        Theory2.tex §29.2 — conjunction of window constraints.

        # copilot: encode_window_conjunction — multiple predicates on one window.
        """
        if pred_names is None:
            pred_names = [f"P{k}" for k in range(len(preds))]
        if not preds:
            formula: Any = _z3.BoolVal(True) if _Z3_AVAILABLE else "True"
            return WindowPredicate(formula=formula, arr=arr, lo=lo, hi=hi, pred_name="empty")
        i_name = self._fresh_var("conj_i")
        if _Z3_AVAILABLE and not isinstance(arr, str):
            i = _z3.Int(i_name)
            lo_z3 = _z3.IntVal(lo) if isinstance(lo, int) else lo
            hi_z3 = _z3.IntVal(hi) if isinstance(hi, int) else hi
            in_range = _z3.And(i >= lo_z3, i < hi_z3)
            elem = _z3.Select(arr, i)
            bodies = [p(elem) for p in preds]
            formula = _z3.ForAll([i], _z3.Implies(in_range, _z3.And(*bodies)))
        else:
            parts = " AND ".join(f"{n}(arr[{i_name}])" for n in pred_names)
            formula = f"ForAll {i_name} in [{lo},{hi}): {parts}"
        return WindowPredicate(
            formula=formula,
            arr=arr,
            lo=lo,
            hi=hi,
            pred_name="_and_".join(pred_names),
            fragment_hint="AUFLIA",
        )

    def encode_sorted_window(
        self,
        arr: Any,
        lo: Any,
        hi: Any,
        ascending: bool = True,
    ) -> WindowPredicate:
        """Encode the sortedness predicate on a window.

        Ascending:   ``∀ i ∈ [lo, hi-1): arr[i] ≤ arr[i+1]``
        Descending:  ``∀ i ∈ [lo, hi-1): arr[i] ≥ arr[i+1]``

        Parameters
        ----------
        arr:
            A Z3 array expression.
        lo:
            Lower bound (inclusive).
        hi:
            Upper bound (exclusive).
        ascending:
            If True, encode ascending sort; otherwise descending.

        Returns
        -------
        WindowPredicate
            The sortedness formula.

        Theory2.tex §29.2 — sorted window predicate.

        # copilot: encode_sorted_window — sortedness constraint over array slice.
        """
        i_name = self._fresh_var("sort_i")
        direction = "asc" if ascending else "desc"
        if _Z3_AVAILABLE and not isinstance(arr, str):
            i = _z3.Int(i_name)
            lo_z3 = _z3.IntVal(lo) if isinstance(lo, int) else lo
            hi_z3 = _z3.IntVal(hi) if isinstance(hi, int) else hi
            # i ranges over [lo, hi-1)
            in_range = _z3.And(i >= lo_z3, i < hi_z3 - 1)
            curr = _z3.Select(arr, i)
            nxt = _z3.Select(arr, i + 1)
            if ascending:
                body = curr <= nxt
            else:
                body = curr >= nxt
            formula = _z3.ForAll([i], _z3.Implies(in_range, body))
        else:
            op = "<=" if ascending else ">="
            formula = (
                f"ForAll {i_name} in [{lo},{hi}-1): "
                f"arr[{i_name}] {op} arr[{i_name}+1]"
            )
        return WindowPredicate(
            formula=formula,
            arr=arr,
            lo=lo,
            hi=hi,
            pred_name=f"sorted_{direction}",
            fragment_hint="AUFLIA",
        )

    def encode_distinct_window(
        self,
        arr: Any,
        lo: Any,
        hi: Any,
    ) -> WindowPredicate:
        """Encode the all-distinct predicate on a window.

        Returns ``∀ i, j ∈ [lo, hi): i ≠ j → arr[i] ≠ arr[j]``

        Parameters
        ----------
        arr:
            A Z3 array expression.
        lo:
            Lower bound (inclusive).
        hi:
            Upper bound (exclusive).

        Returns
        -------
        WindowPredicate
            The distinctness formula.

        Theory2.tex §29.2 — distinct-element window predicate.

        # copilot: encode_distinct_window — pairwise distinctness constraint.
        """
        i_name = self._fresh_var("dist_i")
        j_name = self._fresh_var("dist_j")
        if _Z3_AVAILABLE and not isinstance(arr, str):
            i = _z3.Int(i_name)
            j = _z3.Int(j_name)
            lo_z3 = _z3.IntVal(lo) if isinstance(lo, int) else lo
            hi_z3 = _z3.IntVal(hi) if isinstance(hi, int) else hi
            i_in = _z3.And(i >= lo_z3, i < hi_z3)
            j_in = _z3.And(j >= lo_z3, j < hi_z3)
            neq_idx = i != j
            neq_val = _z3.Select(arr, i) != _z3.Select(arr, j)
            formula = _z3.ForAll(
                [i, j],
                _z3.Implies(_z3.And(i_in, j_in, neq_idx), neq_val),
            )
        else:
            formula = (
                f"ForAll {i_name},{j_name} in [{lo},{hi}): "
                f"{i_name}!={j_name} => arr[{i_name}]!=arr[{j_name}]"
            )
        return WindowPredicate(
            formula=formula,
            arr=arr,
            lo=lo,
            hi=hi,
            pred_name="distinct",
            fragment_hint="AUFLIA",
        )

    def encode_bounded_window(
        self,
        arr: Any,
        lo: Any,
        hi: Any,
        lb: Any,
        ub: Any,
    ) -> WindowPredicate:
        """Encode the bounded-values predicate: ``lb ≤ arr[i] ≤ ub`` for ``i ∈ [lo, hi)``.

        Parameters
        ----------
        arr:
            A Z3 array expression.
        lo:
            Window lower bound (inclusive).
        hi:
            Window upper bound (exclusive).
        lb:
            Element lower bound (inclusive).
        ub:
            Element upper bound (inclusive).

        Returns
        -------
        WindowPredicate
            The bounded-values formula.

        Theory2.tex §29.2 — bounded window predicate.

        # copilot: encode_bounded_window — element range constraint.
        """
        def bounded_pred(elem: Any) -> Any:
            lb_z3 = _z3.IntVal(lb) if isinstance(lb, int) else lb
            ub_z3 = _z3.IntVal(ub) if isinstance(ub, int) else ub
            return _z3.And(elem >= lb_z3, elem <= ub_z3)

        i_name = self._fresh_var("bnd_i")
        if _Z3_AVAILABLE and not isinstance(arr, str):
            i = _z3.Int(i_name)
            lo_z3 = _z3.IntVal(lo) if isinstance(lo, int) else lo
            hi_z3 = _z3.IntVal(hi) if isinstance(hi, int) else hi
            lb_z3 = _z3.IntVal(lb) if isinstance(lb, int) else lb
            ub_z3 = _z3.IntVal(ub) if isinstance(ub, int) else ub
            in_range = _z3.And(i >= lo_z3, i < hi_z3)
            elem = _z3.Select(arr, i)
            body = _z3.And(elem >= lb_z3, elem <= ub_z3)
            formula = _z3.ForAll([i], _z3.Implies(in_range, body))
        else:
            formula = (
                f"ForAll {i_name} in [{lo},{hi}): "
                f"{lb} <= arr[{i_name}] <= {ub}"
            )
        return WindowPredicate(
            formula=formula,
            arr=arr,
            lo=lo,
            hi=hi,
            pred_name=f"bounded_{lb}_{ub}",
            fragment_hint="AUFLIA",
        )

    def split_window(
        self,
        arr: Any,
        mid: Any,
        lo: Any,
        hi: Any,
        pred: Callable[[Any], Any],
        pred_name: str = "P",
    ) -> WindowPredicate:
        """Split a window predicate at *mid* and return the conjunction.

        Returns:
            ``(∀ i ∈ [lo, mid): pred(arr[i])) ∧ (∀ i ∈ [mid, hi): pred(arr[i]))``

        This is logically equivalent to ``∀ i ∈ [lo, hi): pred(arr[i])``  when
        ``lo ≤ mid ≤ hi``.  The split form is useful for incremental reasoning.

        Parameters
        ----------
        arr:
            A Z3 array expression.
        mid:
            The split point.  Must satisfy lo ≤ mid ≤ hi.
        lo:
            Original window lower bound.
        hi:
            Original window upper bound.
        pred:
            A callable ``(Z3Expr) → Z3BoolExpr``.
        pred_name:
            Human-readable name for the predicate.

        Returns
        -------
        WindowPredicate
            Conjunction of the two sub-window predicates.

        Theory2.tex §29.2 Lemma 29.1 — window split.

        # copilot: split_window — Theory2.tex §29.2 Lemma 29.1.
        """
        left = self.encode_window_predicate(arr, lo, mid, pred, pred_name=f"{pred_name}_left")
        right = self.encode_window_predicate(arr, mid, hi, pred, pred_name=f"{pred_name}_right")
        return left.and_(right)

    def shift_window(
        self,
        arr: Any,
        offset: Any,
        lo: Any,
        hi: Any,
        pred: Callable[[Any], Any],
        pred_name: str = "P",
    ) -> WindowPredicate:
        """Encode a shifted window predicate.

        Returns ``∀ i ∈ [lo, hi): pred(arr[i + offset])``

        This is used when the underlying array has been shifted (e.g. after
        a prepend operation that shifts all elements right by 1).

        Parameters
        ----------
        arr:
            A Z3 array expression.
        offset:
            The shift offset (Z3 IntExpr or Python int).  Positive means shift
            indices to the right.
        lo:
            Window lower bound.
        hi:
            Window upper bound.
        pred:
            A callable ``(Z3Expr) → Z3BoolExpr``.
        pred_name:
            Human-readable predicate name.

        Returns
        -------
        WindowPredicate
            The shifted window predicate.

        Theory2.tex §29.2 — window shift.

        # copilot: shift_window — shifted window predicate.
        """
        i_name = self._fresh_var("shift_i")
        if _Z3_AVAILABLE and not isinstance(arr, str):
            i = _z3.Int(i_name)
            lo_z3 = _z3.IntVal(lo) if isinstance(lo, int) else lo
            hi_z3 = _z3.IntVal(hi) if isinstance(hi, int) else hi
            off_z3 = _z3.IntVal(offset) if isinstance(offset, int) else offset
            in_range = _z3.And(i >= lo_z3, i < hi_z3)
            elem = _z3.Select(arr, i + off_z3)
            body = pred(elem)
            formula = _z3.ForAll([i], _z3.Implies(in_range, body))
        else:
            formula = (
                f"ForAll {i_name} in [{lo},{hi}): "
                f"{pred_name}(arr[{i_name}+{offset}])"
            )
        return WindowPredicate(
            formula=formula,
            arr=arr,
            lo=lo,
            hi=hi,
            pred_name=f"{pred_name}_shifted_{offset}",
            fragment_hint="AUFLIA",
        )

    def encode_non_negative_window(
        self,
        arr: Any,
        lo: Any,
        hi: Any,
    ) -> WindowPredicate:
        """Encode ``arr[i] ≥ 0`` for all i in [lo, hi).

        Parameters
        ----------
        arr:
            A Z3 array expression.
        lo:
            Window lower bound.
        hi:
            Window upper bound.

        Returns
        -------
        WindowPredicate

        # copilot: encode_non_negative_window — non-negativity predicate.
        """
        return self.encode_bounded_window(arr, lo, hi, lb=0, ub=2**63)

    def encode_prefix_predicate(
        self,
        arr: Any,
        lo: Any,
        hi: Any,
        prefix_len: Any,
        prefix_pred: Callable[[Any], Any],
        suffix_pred: Callable[[Any], Any] | None = None,
    ) -> WindowPredicate:
        """Encode a two-part prefix/suffix predicate on a window.

        Returns:
            ``(∀ i ∈ [lo, lo+prefix_len): prefix_pred(arr[i])) ∧
              (∀ i ∈ [lo+prefix_len, hi): suffix_pred(arr[i]))``

        If ``suffix_pred`` is None, only the prefix part is encoded.

        Parameters
        ----------
        arr:
            A Z3 array expression.
        lo:
            Window lower bound.
        hi:
            Window upper bound.
        prefix_len:
            Length of the prefix sub-window.
        prefix_pred:
            Predicate for prefix elements.
        suffix_pred:
            Optional predicate for suffix elements.

        Returns
        -------
        WindowPredicate

        # copilot: encode_prefix_predicate — split window for prefix/suffix.
        """
        if _Z3_AVAILABLE and not isinstance(arr, str):
            lo_z3 = _z3.IntVal(lo) if isinstance(lo, int) else lo
            plen_z3 = _z3.IntVal(prefix_len) if isinstance(prefix_len, int) else prefix_len
            mid = lo_z3 + plen_z3
        else:
            mid = f"({lo}+{prefix_len})"
        prefix_wp = self.encode_window_predicate(arr, lo, mid, prefix_pred, pred_name="prefix")
        if suffix_pred is not None:
            suffix_wp = self.encode_window_predicate(arr, mid, hi, suffix_pred, pred_name="suffix")
            return prefix_wp.and_(suffix_wp)
        return prefix_wp

    def copilot_guess_window_invariant(
        self,
        arr_samples: Sequence[Sequence[Any]],
        lo: int,
        hi: int,
    ) -> str:
        """Suggest a window invariant based on sample array slices.

        Analyses the sample arrays to infer a likely predicate over [lo, hi).
        This is the *copilot inference* interface: it inspects the samples
        heuristically and returns a descriptive string of the suggested
        invariant.

        Heuristics applied (in order):
        1.  **Constant**: all slices have the same value at every position.
        2.  **Non-negative**: all elements ≥ 0.
        3.  **Bounded**: all elements lie in [min_val, max_val].
        4.  **Sorted ascending**: each slice is non-decreasing.
        5.  **Sorted descending**: each slice is non-increasing.
        6.  **Distinct**: each slice has pairwise distinct elements.

        Parameters
        ----------
        arr_samples:
            A list of concrete array samples (list of lists of comparables).
        lo:
            Window start (inclusive).
        hi:
            Window end (exclusive).
        Returns
        -------
        str
            A description of the suggested invariant for the copilot prompt.

        Theory2.tex §29.2 Remark 29.2 — copilot-assisted invariant suggestion.

        # copilot: copilot_guess_window_invariant — heuristic predicate inference.
        """
        if not arr_samples or lo >= hi:
            return f"encode_window_predicate(arr, {lo}, {hi}, lambda x: True)  # trivial window"
        slices: list[list[Any]] = []
        for sample in arr_samples:
            try:
                sl = list(sample[lo:hi])
                slices.append(sl)
            except (TypeError, IndexError):
                pass
        if not slices:
            return f"encode_window_predicate(arr, {lo}, {hi}, lambda x: True)  # empty samples"
        flat: list[Any] = [v for sl in slices for v in sl]
        # Heuristic 1: non-negative
        try:
            if all(v >= 0 for v in flat):
                min_v = min(flat)
                max_v = max(flat)
                if min_v == max_v:
                    return (
                        f"encode_bounded_window(arr, {lo}, {hi}, lb={min_v}, ub={max_v})  "
                        f"# constant window: all elements = {min_v}"
                    )
                return (
                    f"encode_bounded_window(arr, {lo}, {hi}, lb={min_v}, ub={max_v})  "
                    f"# bounded non-negative: [{min_v}, {max_v}]"
                )
        except TypeError:
            pass
        # Heuristic 2: sorted ascending
        try:
            if all(
                all(sl[k] <= sl[k + 1] for k in range(len(sl) - 1))
                for sl in slices
                if len(sl) > 1
            ):
                return f"encode_sorted_window(arr, {lo}, {hi}, ascending=True)  # sorted ascending"
        except TypeError:
            pass
        # Heuristic 3: sorted descending
        try:
            if all(
                all(sl[k] >= sl[k + 1] for k in range(len(sl) - 1))
                for sl in slices
                if len(sl) > 1
            ):
                return f"encode_sorted_window(arr, {lo}, {hi}, ascending=False)  # sorted descending"
        except TypeError:
            pass
        # Heuristic 4: distinct
        try:
            if all(len(set(sl)) == len(sl) for sl in slices):
                return f"encode_distinct_window(arr, {lo}, {hi})  # all-distinct elements"
        except TypeError:
            pass
        return (
            f"encode_window_predicate(arr, {lo}, {hi}, lambda x: <custom_pred>)  "
            f"# no simple invariant detected from {len(arr_samples)} samples"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fresh_var(self, prefix: str) -> str:
        """Return a fresh Z3 bound variable name.

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


__all__: list[str] = [
    "SequenceWindowEncoder",
    "WindowPredicate",
]
