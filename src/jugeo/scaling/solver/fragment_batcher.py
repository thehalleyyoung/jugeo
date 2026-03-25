"""Fragment-based query batching for the solver scaling layer.

:class:`FragmentClassifier` inspects raw SMT-LIB 2 text and assigns each
query to a :class:`~jugeo.scaling.solver.models.SolverFragment` bucket so
that the session pool can route it to a specialised solver instance.

:class:`QueryBatcher` accumulates pending queries and groups them into
:class:`~jugeo.scaling.solver.models.QueryBatch` objects, one batch per
fragment, up to a configurable batch size.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from typing import Any

from jugeo.scaling.solver.models import (
    QueryBatch,
    SolverFragment,
    SolverQuery,
)


# ---------------------------------------------------------------------------
# Fragment classifier
# ---------------------------------------------------------------------------

class FragmentClassifier:
    """Classify a raw SMT-LIB 2 query into a :class:`SolverFragment`.

    The classification is a *best-effort* heuristic based on keyword and
    pattern matching — it does not require a full SMT-LIB parser.
    """

    # Keywords that imply specific theories.
    _BV_KEYWORDS = frozenset({
        "bvadd", "bvsub", "bvmul", "bvudiv", "bvurem", "bvshl", "bvlshr",
        "bvashr", "bvor", "bvand", "bvxor", "bvnot", "bvneg", "bvult",
        "bvule", "bvugt", "bvuge", "bvslt", "bvsle", "bvsgt", "bvsge",
        "concat", "extract", "bvcomp", "(_ BitVec",
    })

    _ARRAY_KEYWORDS = frozenset({
        "select", "store", "Array",
    })

    _UF_KEYWORDS = frozenset({
        "declare-fun", "apply",
    })

    # Arithmetic operators (not just sort names) that indicate LIA/LRA usage.
    _LIA_OPS = frozenset({"div", "mod"})
    _LRA_OPS = frozenset({"to_real", "to_int"})

    _LIA_KEYWORDS = frozenset({
        "div", "mod", "Int",
    })

    _LRA_KEYWORDS = frozenset({
        "Real", "/",
    })

    _HORN_KEYWORDS = frozenset({
        "CHC", "horn", "(rule", "(query",
    })

    _QUANTIFIER_RE = re.compile(r'\b(forall|exists)\b')

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def classify(self, smt_text: str) -> SolverFragment:
        """Return the best-matching :class:`SolverFragment` for *smt_text*."""
        has_quant = self._has_quantifiers(smt_text)
        has_arrays = self._has_arrays(smt_text)
        has_bv = self._has_bitvectors(smt_text)
        theory = self._detect_theory(smt_text)
        is_horn = self._has_horn(smt_text)

        if is_horn:
            return SolverFragment.HORN

        # Count how many theories are present — if more than one, it's MIXED.
        active_theories: list[str] = []
        if has_bv:
            active_theories.append("BV")
        if has_arrays:
            active_theories.append("ARRAY")
        if theory in ("LIA", "LRA", "UF"):
            active_theories.append(theory)

        if has_quant:
            return SolverFragment.QUANTIFIED

        if has_bv:
            return SolverFragment.QF_BV

        # Arrays + arithmetic → QF_AUFLIA (takes priority over MIXED).
        if has_arrays and theory in ("LIA", "UF", ""):
            return SolverFragment.QF_AUFLIA

        if has_arrays and theory == "LRA":
            # Arrays with real arithmetic — treat as MIXED.
            return SolverFragment.MIXED

        if len(active_theories) > 1:
            return SolverFragment.MIXED

        if theory == "UF":
            return SolverFragment.QF_UF

        if theory == "LIA":
            return SolverFragment.QF_LIA

        if theory == "LRA":
            return SolverFragment.QF_LRA

        if theory:
            return SolverFragment.MIXED

        return SolverFragment.UNKNOWN

    def batch_classify(
        self, queries: list[SolverQuery]
    ) -> dict[SolverFragment, list[SolverQuery]]:
        """Group *queries* by their :class:`SolverFragment`.

        Returns a dict mapping each fragment to the list of queries that belong
        to it.  Queries are classified on the fly if their fragment field is
        ``UNKNOWN``.
        """
        groups: dict[SolverFragment, list[SolverQuery]] = defaultdict(list)
        for q in queries:
            fragment = q.fragment
            if fragment is SolverFragment.UNKNOWN:
                fragment = self.classify(q.smt_text)
            groups[fragment].append(q)
        return dict(groups)

    # ---------------------------------------------------------------------------
    # Detection helpers
    # ---------------------------------------------------------------------------

    def _has_quantifiers(self, text: str) -> bool:
        """Return True if *text* contains quantifier keywords."""
        return bool(self._QUANTIFIER_RE.search(text))

    def _has_arrays(self, text: str) -> bool:
        """Return True if *text* uses array operations."""
        for kw in self._ARRAY_KEYWORDS:
            if kw in text:
                return True
        # Also check sort declarations: (Array Int Int)
        if re.search(r'\bArray\b', text):
            return True
        return False

    def _has_bitvectors(self, text: str) -> bool:
        """Return True if *text* uses bit-vector operations."""
        for kw in self._BV_KEYWORDS:
            if kw in text:
                return True
        return False

    def _has_horn(self, text: str) -> bool:
        """Return True if *text* appears to be a CHC / Horn query."""
        for kw in self._HORN_KEYWORDS:
            if kw.lower() in text.lower():
                return True
        return False

    def _detect_theory(self, text: str) -> str:
        """Return the primary arithmetic / logic theory name or ''."""
        has_real = bool(re.search(r'\bReal\b', text))
        # Only flag LIA if arithmetic operators are applied in prefix position
        # (immediately after an opening paren), not inside identifier names.
        has_arith_ops = bool(re.search(r'\(\s*(div|mod|\+|-|\*|>=|<=|>|<)\s', text))
        has_int = bool(re.search(r'\bInt\b', text)) and has_arith_ops
        has_uf = self._has_uf(text)
        has_div_mod = bool(re.search(r'\(\s*(div|mod)\s', text))

        if has_real and has_int:
            return "MIXED"
        if has_real:
            return "LRA"
        if has_int or has_div_mod:
            return "LIA"
        if has_uf:
            return "UF"
        return ""

    def _has_uf(self, text: str) -> bool:
        """Return True if *text* declares uninterpreted functions."""
        return bool(re.search(r'\(declare-fun\b', text))


# ---------------------------------------------------------------------------
# Query batcher
# ---------------------------------------------------------------------------

class QueryBatcher:
    """Accumulate :class:`SolverQuery` objects and flush them as
    :class:`QueryBatch` objects grouped by fragment.

    Flushing creates one batch per fragment, each containing at most
    *batch_size* queries.  If a fragment has more pending queries than
    *batch_size*, multiple batches are created.
    """

    def __init__(self, batch_size: int = 50) -> None:
        self._batch_size = batch_size
        self._classifier = FragmentClassifier()
        # fragment → list of pending queries
        self._pending: dict[SolverFragment, list[SolverQuery]] = defaultdict(list)
        self._total_added = 0
        self._total_flushed = 0

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def add_query(self, query: SolverQuery) -> None:
        """Add *query* to the pending queue, classifying it if needed."""
        fragment = query.fragment
        if fragment is SolverFragment.UNKNOWN:
            fragment = self._classifier.classify(query.smt_text)
        self._pending[fragment].append(query)
        self._total_added += 1

    def flush(self) -> list[QueryBatch]:
        """Create :class:`QueryBatch` objects from all pending queries.

        Clears the pending queue.  Returns a list of batches, one (or more,
        if the fragment has > *batch_size* queries) per fragment.
        """
        batches: list[QueryBatch] = []
        for fragment, queries in self._pending.items():
            for batch in self._create_batches(fragment, queries):
                batches.append(batch)
                self._total_flushed += len(batch.queries)
        self._pending.clear()
        return batches

    def pending_count(self) -> int:
        """Return the total number of pending (unflushed) queries."""
        return sum(len(qs) for qs in self._pending.values())

    def pending_by_fragment(self) -> dict[str, int]:
        """Return a mapping of fragment name → pending count."""
        return {
            fragment.value: len(queries)
            for fragment, queries in self._pending.items()
            if queries
        }

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _create_batches(
        self, fragment: SolverFragment, queries: list[SolverQuery]
    ) -> list[QueryBatch]:
        """Split *queries* into :class:`QueryBatch` objects of at most
        *batch_size* queries each."""
        batches: list[QueryBatch] = []
        for start in range(0, len(queries), self._batch_size):
            chunk = queries[start: start + self._batch_size]
            batches.append(self._create_batch(fragment, chunk))
        return batches

    def _create_batch(
        self, fragment: SolverFragment, queries: list[SolverQuery]
    ) -> QueryBatch:
        """Create a single :class:`QueryBatch` for *queries*."""
        return QueryBatch.create(fragment=fragment, queries=queries)

    # ---------------------------------------------------------------------------
    # Statistics
    # ---------------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        return {
            "batch_size": self._batch_size,
            "total_added": self._total_added,
            "total_flushed": self._total_flushed,
            "pending_count": self.pending_count(),
            "pending_by_fragment": self.pending_by_fragment(),
        }
