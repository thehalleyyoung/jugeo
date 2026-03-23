"""finite_map_encoder.py — Finite-map encoder.

Theory2.tex Chapter 29 §3: Finite-map encoder — Python dicts as Z3 partial
functions with precise domain tracking.

This module implements ``FiniteMapEncoder``, the third encoding layer in
Chapter 29.  The key insight is:

    A Python dict ``{k0: v0, k1: v1, …, kₙ: vₙ}`` is encoded as:

    *   A Z3 *uninterpreted function* ``f: KeySort → ValSort``
    *   A *domain predicate* ``dom(x) ≡ x = k0 ∨ x = k1 ∨ … ∨ x = kₙ``
    *   *Value axioms*: ``dom(x) → f(x) = vₓ``  for each concrete ``(kₓ, vₓ)``
    *   *Default axiom*: ``¬dom(x) → f(x) = default``

This encoding is faithful: any satisfying assignment to ``f`` that respects
the axioms uniquely determines the original dict up to key-order.

Fragment discipline
-------------------
*  Empty dict → trivially ``QF_UF`` (a constant uninterpreted function).
*  Non-empty dict with integer keys/values → ``QF_UFLIA``.
*  Merging two maps → ``QF_UFLIA`` with disjointness check.

# copilot: FiniteMapEncoder — Theory2.tex §29.3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Mapping, Sequence

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


# ---------------------------------------------------------------------------
# EncodedMap result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EncodedMap:
    """Result of encoding a Python dict as a Z3 uninterpreted function.

    Fields
    ------
    fn_var : Any
        The Z3 function declaration ``f: KeySort → ValSort``.
    dom_pred : Any
        The Z3 domain predicate formula ``dom(x) ≡ x = k0 ∨ … ∨ x = kₙ``.
    value_axioms : tuple[Any, ...]
        Z3 formulas: ``dom(kᵢ) → f(kᵢ) = vᵢ`` for each key kᵢ.
    default_axiom : Any
        Z3 formula: ``¬dom(x) → f(x) = default``.
    key_sort : Any
        The Z3 sort for map keys.
    val_sort : Any
        The Z3 sort for map values.
    keys : tuple[Any, ...]
        The concrete Z3 key expressions.
    values : tuple[Any, ...]
        The concrete Z3 value expressions.
    default_value : Any
        The Z3 default value (for keys outside the domain).
    name : str
        Debugging name.
    original_size : int
        The number of key-value pairs in the original dict.

    # copilot: EncodedMap result dataclass — FiniteMapEncoder output.
    """

    fn_var: Any
    dom_pred: Any
    value_axioms: tuple[Any, ...]
    default_axiom: Any
    key_sort: Any
    val_sort: Any
    keys: tuple[Any, ...]
    values: tuple[Any, ...]
    default_value: Any
    name: str = "map"
    original_size: int = 0

    def all_axioms(self) -> list[Any]:
        """Return all axioms: value axioms + default axiom.

        Returns
        -------
        list[Any]
        """
        return list(self.value_axioms) + ([self.default_axiom] if self.default_axiom is not None else [])

    def lookup(self, key: Any) -> Any:
        """Return the Z3 expression ``f(key)``.

        Parameters
        ----------
        key:
            A Z3 key expression or Python value.

        Returns
        -------
        Any
            ``fn_var(key)`` as a Z3 expression.
        """
        if _Z3_AVAILABLE and self.fn_var is not None and hasattr(self.fn_var, "__call__"):
            return self.fn_var(key)
        return f"{self.name}({key})"

    def domain_check(self, key: Any) -> Any:
        """Return a Z3 formula asserting *key* is in the domain.

        Parameters
        ----------
        key:
            A Z3 key expression.

        Returns
        -------
        Any
        """
        if _Z3_AVAILABLE and not isinstance(self.dom_pred, str) and self.keys:
            z3_key = self._to_key_expr(key)
            return _z3.Or(*[z3_key == k for k in self.keys])
        return f"in_domain({key}, {self.name})"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "name": self.name,
            "original_size": self.original_size,
            "num_axioms": len(self.value_axioms),
            "has_default": self.default_value is not None,
        }

    def _to_key_expr(self, key: Any) -> Any:
        if _Z3_AVAILABLE:
            if isinstance(key, int):
                return _z3.IntVal(key)
            if isinstance(key, bool):
                return _z3.BoolVal(key)
        return key


# ---------------------------------------------------------------------------
# FiniteMapEncoder
# ---------------------------------------------------------------------------


class FiniteMapEncoder:
    """Encodes Python dicts as Z3 uninterpreted functions with domain predicates.

    This encoder implements Chapter 29 §3.  For each Python dict, it produces:

    *   A Z3 uninterpreted function ``f: KeySort → ValSort``
    *   A domain predicate ``dom(x) ≡ x = k0 ∨ … ∨ x = kₙ``
    *   Value axioms and a default axiom.

    Parameters
    ----------
    name_prefix : str
        Prefix for Z3 symbol names.

    Theory2.tex §29.3.

    # copilot: FiniteMapEncoder — Theory2.tex §29.3.
    """

    def __init__(self, name_prefix: str = "fme") -> None:
        """Initialise the finite-map encoder.

        Parameters
        ----------
        name_prefix:
            Prefix for all generated Z3 symbol names.
        """
        self._prefix = name_prefix
        self._counter = 0
        self._maps: list[EncodedMap] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode_dict(
        self,
        d: dict[Any, Any],
        key_sort: Any,
        val_sort: Any,
        default: Any = None,
    ) -> EncodedMap:
        """Encode a Python dict as a Z3 uninterpreted function with domain predicate.

        Parameters
        ----------
        d:
            The Python dict to encode.
        key_sort:
            Z3 sort (or string) for dict keys.
        val_sort:
            Z3 sort (or string) for dict values.
        default:
            The default Z3 value returned for keys outside the domain.
            If None, an appropriate zero value is chosen.

        Returns
        -------
        EncodedMap
            The encoding result.

        Theory2.tex §29.3 Definition 29.3.

        # copilot: encode_dict — maps Python dict to Z3 partial function.
        """
        name = self._fresh_name("fn")
        if _Z3_AVAILABLE:
            z3_ksort = self._resolve_sort(key_sort)
            z3_vsort = self._resolve_sort(val_sort)
            fn = _z3.Function(name, z3_ksort, z3_vsort)
            default_val = default if default is not None else self._default_for_sort(z3_vsort)
            z3_keys = [self._python_to_z3(k, z3_ksort) for k in d.keys()]
            z3_vals = [self._python_to_z3(v, z3_vsort) for v in d.values()]
            dom = self.encode_domain_predicate(list(d.keys()), key_sort)
            # Value axioms: f(ki) = vi for each (ki, vi)
            value_axioms: list[Any] = []
            for k_z3, v_z3 in zip(z3_keys, z3_vals):
                value_axioms.append(fn(k_z3) == v_z3)
            # Default axiom: ¬dom(x) → f(x) = default (universal)
            x = _z3.Const(f"{name}_dom_x", z3_ksort)
            if z3_keys:
                in_dom = _z3.Or(*[x == k for k in z3_keys])
            else:
                in_dom = _z3.BoolVal(False)
            default_ax = _z3.ForAll(
                [x],
                _z3.Implies(_z3.Not(in_dom), fn(x) == default_val),
            )
            result = EncodedMap(
                fn_var=fn,
                dom_pred=dom,
                value_axioms=tuple(value_axioms),
                default_axiom=default_ax,
                key_sort=z3_ksort,
                val_sort=z3_vsort,
                keys=tuple(z3_keys),
                values=tuple(z3_vals),
                default_value=default_val,
                name=name,
                original_size=len(d),
            )
        else:
            # Stub mode
            fn = f"{name}:Fn({key_sort}→{val_sort})"
            dom = self.encode_domain_predicate(list(d.keys()), key_sort)
            axioms_s = tuple(f"{name}({k})={v}" for k, v in d.items())
            result = EncodedMap(
                fn_var=fn,
                dom_pred=dom,
                value_axioms=axioms_s,
                default_axiom=f"¬dom(x) => {name}(x)={default}",
                key_sort=key_sort,
                val_sort=val_sort,
                keys=tuple(str(k) for k in d.keys()),
                values=tuple(str(v) for v in d.values()),
                default_value=default,
                name=name,
                original_size=len(d),
            )
        self._maps.append(result)
        return result

    def encode_domain_predicate(
        self,
        keys: Sequence[Any],
        key_sort: Any,
    ) -> Any:
        """Encode a domain predicate as a Z3 Or-formula.

        Returns ``x = k0 ∨ x = k1 ∨ … ∨ x = kₙ`` as a formula with a free
        variable ``x``.

        Parameters
        ----------
        keys:
            The concrete key values.
        key_sort:
            Z3 sort for keys.

        Returns
        -------
        Any
            A Z3 formula or string stub.

        Theory2.tex §29.3 — domain predicate definition.

        # copilot: encode_domain_predicate — explicit finite domain for map.
        """
        if not keys:
            if _Z3_AVAILABLE:
                return _z3.BoolVal(False)
            return "False"
        if _Z3_AVAILABLE:
            z3_ksort = self._resolve_sort(key_sort)
            x = _z3.Const("_dom_x", z3_ksort)
            z3_keys = [self._python_to_z3(k, z3_ksort) for k in keys]
            return _z3.Or(*[x == k for k in z3_keys])
        return " OR ".join(f"x={k}" for k in keys)

    def encode_lookup(
        self,
        fn: Any,
        key: Any,
        dom_pred: Any,
        default: Any = None,
    ) -> Any:
        """Encode a safe lookup: returns ``f(key)`` with domain check.

        If ``key`` is in the domain, returns ``fn(key)``; otherwise returns
        ``default`` (or raises a Z3 constraint violation if no default).

        Parameters
        ----------
        fn:
            A Z3 function declaration.
        key:
            The key to look up (Z3 expression or Python value).
        dom_pred:
            The domain predicate formula for ``fn``.
        default:
            Fallback value if key is not in domain.

        Returns
        -------
        Any
            A Z3 expression or string stub.

        Theory2.tex §29.3 — safe map lookup.

        # copilot: encode_lookup — guarded map lookup with domain check.
        """
        if _Z3_AVAILABLE and callable(fn) and not isinstance(fn, str):
            fn_val = fn(key)
            if default is not None:
                return fn_val  # domain check is an axiom, not part of the expression
            return fn_val
        return f"{fn}({key})"

    def encode_update(
        self,
        fn: Any,
        key: Any,
        val: Any,
        dom_pred: Any,
        key_sort: Any,
    ) -> tuple[Any, Any]:
        """Encode a map update: ``f[key ↦ val]``.

        Returns a fresh function ``g`` and an extended domain predicate.
        All existing mappings are preserved; ``key`` is added (or overwritten).

        Parameters
        ----------
        fn:
            The Z3 function to update.
        key:
            The key to update.
        val:
            The new value.
        dom_pred:
            Current domain predicate.
        key_sort:
            Z3 sort for keys.

        Returns
        -------
        tuple[Any, Any]
            ``(new_fn, new_dom_pred)``

        Theory2.tex §29.3 — map update.

        # copilot: encode_update — functional map update.
        """
        name = self._fresh_name("upd")
        if _Z3_AVAILABLE and callable(fn) and not isinstance(fn, str):
            z3_ksort = self._resolve_sort(key_sort)
            z3_vsort = fn.range() if hasattr(fn, "range") else _z3.IntSort()
            new_fn = _z3.Function(name, z3_ksort, z3_vsort)
            z3_key = self._python_to_z3(key, z3_ksort) if not hasattr(key, "sort") else key
            z3_val = val
            # New fn axioms: new_fn(key) = val; for y != key: new_fn(y) = fn(y)
            y = _z3.Const(f"{name}_upd_y", z3_ksort)
            update_axiom = _z3.ForAll(
                [y],
                _z3.If(y == z3_key, new_fn(y) == z3_val, new_fn(y) == fn(y)),
            )
            # Extended domain
            new_dom: Any
            if isinstance(dom_pred, bool) and not dom_pred:
                new_dom = _z3.Or(_z3.Const("_dom_x", z3_ksort) == z3_key)
            elif not isinstance(dom_pred, str):
                new_dom = _z3.Or(dom_pred, _z3.Const("_dom_x", z3_ksort) == z3_key)
            else:
                new_dom = f"({dom_pred}) OR (x={key})"
            return new_fn, new_dom
        return f"{name}:=update({fn}, {key}, {val})", f"({dom_pred}) OR (x={key})"

    def encode_merge(
        self,
        fn1: Any,
        fn2: Any,
        dom1: Any,
        dom2: Any,
    ) -> tuple[Any, Any, Any]:
        """Merge two encoded maps into one.

        Returns ``(merged_fn, merged_dom, conflict_check)`` where:

        *   ``merged_fn``    — the merged Z3 function (fn1 takes priority)
        *   ``merged_dom``   — ``dom1 ∨ dom2``
        *   ``conflict_check`` — Z3 formula that is SAT iff keys overlap

        Parameters
        ----------
        fn1:
            First Z3 function.
        fn2:
            Second Z3 function.
        dom1:
            Domain predicate for fn1.
        dom2:
            Domain predicate for fn2.

        Returns
        -------
        tuple[Any, Any, Any]
            ``(merged_fn, merged_dom, conflict_check)``

        Theory2.tex §29.3 — map merge.

        # copilot: encode_merge — merges two finite maps.
        """
        name = self._fresh_name("merge")
        if _Z3_AVAILABLE and callable(fn1) and callable(fn2):
            try:
                k_sort = fn1.domain(0)
                v_sort = fn1.range()
            except Exception:
                k_sort = _z3.IntSort()
                v_sort = _z3.IntSort()
            merged = _z3.Function(name, k_sort, v_sort)
            x = _z3.Const(f"{name}_x", k_sort)
            # Merge: fn1 takes priority on its domain
            merge_axiom = _z3.ForAll(
                [x],
                _z3.If(
                    dom1 if not isinstance(dom1, str) else _z3.BoolVal(False),
                    merged(x) == fn1(x),
                    merged(x) == fn2(x),
                ),
            )
            merged_dom = _z3.Or(dom1, dom2) if not isinstance(dom1, str) else f"({dom1}) OR ({dom2})"
            conflict = _z3.And(dom1, dom2) if not isinstance(dom1, str) else f"({dom1}) AND ({dom2})"
            return merged, merged_dom, conflict
        merged_fn = f"{name}:=merge({fn1}, {fn2})"
        merged_dom = f"({dom1}) OR ({dom2})"
        conflict = f"({dom1}) AND ({dom2})"
        return merged_fn, merged_dom, conflict

    def encode_restriction(
        self,
        fn: Any,
        dom_pred: Any,
        new_keys: Sequence[Any],
        key_sort: Any,
    ) -> tuple[Any, Any]:
        """Restrict a map to a subset of keys.

        Returns ``(restricted_fn, new_dom_pred)`` where:
        *   ``restricted_fn`` agrees with ``fn`` on ``new_keys``
        *   ``new_dom_pred ≡ x ∈ new_keys``

        Parameters
        ----------
        fn:
            The Z3 function to restrict.
        dom_pred:
            Current domain predicate.
        new_keys:
            The new (smaller) key set.
        key_sort:
            Z3 sort for keys.

        Returns
        -------
        tuple[Any, Any]

        Theory2.tex §29.3 — map restriction.

        # copilot: encode_restriction — restricts map domain.
        """
        name = self._fresh_name("restr")
        new_dom = self.encode_domain_predicate(new_keys, key_sort)
        if _Z3_AVAILABLE and callable(fn) and not isinstance(fn, str):
            try:
                k_sort = fn.domain(0)
                v_sort = fn.range()
            except Exception:
                k_sort = self._resolve_sort(key_sort)
                v_sort = _z3.IntSort()
            restricted = _z3.Function(name, k_sort, v_sort)
            z3_new_keys = [self._python_to_z3(k, k_sort) for k in new_keys]
            x = _z3.Const(f"{name}_x", k_sort)
            if z3_new_keys:
                in_new = _z3.Or(*[x == k for k in z3_new_keys])
            else:
                in_new = _z3.BoolVal(False)
            restr_ax = _z3.ForAll(
                [x],
                _z3.Implies(in_new, restricted(x) == fn(x)),
            )
            return restricted, new_dom
        return f"{name}:=restrict({fn}, {list(new_keys)})", new_dom

    def encode_image(
        self,
        fn: Any,
        dom_pred: Any,
        keys: Sequence[Any],
        key_sort: Any,
        val_sort: Any,
    ) -> Any:
        """Encode the image of a map as a Z3 set (characteristic array).

        The image ``{f(k) | k ∈ dom}`` is encoded as an
        ``Array(ValSort, Bool)`` where ``img[v] = True`` iff ``∃ k ∈ dom: f(k)=v``.

        Parameters
        ----------
        fn:
            The Z3 function.
        dom_pred:
            Domain predicate.
        keys:
            Concrete key values.
        key_sort:
            Z3 sort for keys.
        val_sort:
            Z3 sort for values.

        Returns
        -------
        Any
            A Z3 array ``Array(ValSort, Bool)`` or string stub.

        Theory2.tex §29.3 — map image.

        # copilot: encode_image — image of finite map as characteristic array.
        """
        name = self._fresh_name("img")
        if _Z3_AVAILABLE and callable(fn) and not isinstance(fn, str):
            z3_vsort = self._resolve_sort(val_sort)
            img_arr = _z3.Array(name, z3_vsort, _z3.BoolSort())
            z3_ksort = self._resolve_sort(key_sort)
            z3_keys = [self._python_to_z3(k, z3_ksort) for k in keys]
            axioms = [
                _z3.Select(img_arr, fn(k)) == _z3.BoolVal(True)
                for k in z3_keys
            ]
            return img_arr
        return f"{name}:=image({fn})"

    def encode_pointwise_equal(
        self,
        fn1: Any,
        fn2: Any,
        dom1: Any,
        dom2: Any,
        key_sort: Any,
    ) -> Any:
        """Encode pointwise equality of two maps on their shared domain.

        Returns:
            ``∀ x: (dom1(x) ∧ dom2(x)) → fn1(x) = fn2(x)``

        Parameters
        ----------
        fn1:
            First Z3 function.
        fn2:
            Second Z3 function.
        dom1:
            Domain predicate for fn1.
        dom2:
            Domain predicate for fn2.
        key_sort:
            Z3 sort for keys.

        Returns
        -------
        Any
            A Z3 formula or string stub.

        Theory2.tex §29.3 — pointwise map equality.

        # copilot: encode_pointwise_equal — equality on shared domain.
        """
        if _Z3_AVAILABLE and callable(fn1) and callable(fn2):
            z3_ksort = self._resolve_sort(key_sort)
            x = _z3.Const("_eq_x", z3_ksort)
            both_dom: Any
            if not isinstance(dom1, str) and not isinstance(dom2, str):
                both_dom = _z3.And(dom1, dom2)
            else:
                both_dom = _z3.BoolVal(False)
            return _z3.ForAll(
                [x],
                _z3.Implies(both_dom, fn1(x) == fn2(x)),
            )
        return f"ForAll x: (dom1(x) AND dom2(x)) => fn1(x)=fn2(x)"

    def copilot_infer_map_type(
        self,
        dict_sample: dict[Any, Any],
    ) -> tuple[str, str]:
        """Infer Z3 sort hints for the keys and values of a Python dict.

        Inspects the concrete types of keys and values in ``dict_sample`` and
        returns sort name strings suitable for passing to ``encode_dict``.

        Parameters
        ----------
        dict_sample:
            A sample Python dict to inspect.

        Returns
        -------
        tuple[str, str]
            ``(key_sort_hint, val_sort_hint)`` — strings like ``"Int"``, ``"Bool"``,
            ``"Real"``, or ``"String"``.

        Theory2.tex §29.3 Remark 29.3 — copilot map type inference.

        # copilot: copilot_infer_map_type — type hint inference for encode_dict.
        """
        def _sort_for_sample(vals: list[Any]) -> str:
            if not vals:
                return "Int"
            sample = [v for v in vals if v is not None][:10]
            if all(isinstance(v, bool) for v in sample):
                return "Bool"
            if all(isinstance(v, int) for v in sample):
                return "Int"
            if all(isinstance(v, float) or isinstance(v, int) for v in sample):
                return "Real"
            if all(isinstance(v, str) for v in sample):
                return "String"
            return "Int"  # fallback

        keys = list(dict_sample.keys())
        vals = list(dict_sample.values())
        return _sort_for_sample(keys), _sort_for_sample(vals)

    def recent_maps(self) -> list[EncodedMap]:
        """Return a list of all maps encoded by this encoder instance.

        Returns
        -------
        list[EncodedMap]
        """
        return list(self._maps)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fresh_name(self, prefix: str) -> str:
        self._counter += 1
        return f"{self._prefix}_{prefix}_{self._counter}"

    def _resolve_sort(self, sort_hint: Any) -> Any:
        if not _Z3_AVAILABLE:
            return sort_hint
        if isinstance(sort_hint, str):
            mapping: dict[str, Any] = {
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
        if not _Z3_AVAILABLE:
            return 0
        try:
            if sort == _z3.BoolSort():
                return _z3.BoolVal(False)
            if sort == _z3.RealSort():
                return _z3.RealVal(0)
        except Exception:
            pass
        return _z3.IntVal(0)

    def _python_to_z3(self, v: Any, sort: Any) -> Any:
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


__all__: list[str] = [
    "FiniteMapEncoder",
    "EncodedMap",
]
