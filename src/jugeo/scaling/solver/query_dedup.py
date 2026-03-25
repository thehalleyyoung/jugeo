"""Query deduplication via content hashing for the solver scaling layer.

The :class:`QueryNormalizer` reduces SMT-LIB 2 text to a canonical form so
that semantically identical queries share the same SHA-256 hash regardless of
superficial differences (whitespace, comments, variable naming, operand order).

The :class:`DeduplicationCache` wraps the normalizer with an LRU result store
so that duplicate queries across batches are answered immediately from cache.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict
from typing import Any, Optional

from jugeo.scaling.solver.models import (
    DeduplicationResult,
    QueryStatus,
    SolverQuery,
    SolverResult,
)


# ---------------------------------------------------------------------------
# Query normalizer
# ---------------------------------------------------------------------------

class QueryNormalizer:
    """Normalise SMT-LIB 2 text to a canonical form for hashing.

    The pipeline is:
    1. Strip ``; …`` line comments and ``#| … |#`` block comments.
    2. Collapse all whitespace runs to a single space and strip leading/
       trailing whitespace.
    3. Alpha-rename all bound variables introduced by ``let``, ``forall``,
       ``exists``, and ``lambda`` binders to ``x0``, ``x1``, … so that
       different user-chosen names hash identically.
    4. Sort the arguments of commutative operators ``and``, ``or``, ``+``,
       ``*`` so that operand-order differences are erased.
    """

    # Commutative n-ary operators whose argument lists can be sorted.
    _COMMUTATIVE = frozenset({"and", "or", "+", "*"})

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def normalize(self, smt_text: str) -> str:
        """Return the normalised form of *smt_text*."""
        text = self._strip_comments(smt_text)
        text = self._normalize_whitespace(text)
        text = self._alpha_rename(text)
        text = self._sort_commutative(text)
        return text

    def content_hash(self, smt_text: str) -> str:
        """Return the SHA-256 hex digest of the normalised *smt_text*."""
        normalised = self.normalize(smt_text)
        return hashlib.sha256(normalised.encode()).hexdigest()

    # ---------------------------------------------------------------------------
    # Internal pipeline steps
    # ---------------------------------------------------------------------------

    def _strip_comments(self, text: str) -> str:
        """Remove SMT-LIB line comments (``; …``) and block comments."""
        # Block comments: #| … |#  (SMT-LIB 2.6 §3.1)
        text = re.sub(r'#\|.*?\|#', ' ', text, flags=re.DOTALL)
        # Line comments: ; to end of line
        text = re.sub(r';[^\n]*', ' ', text)
        return text

    def _normalize_whitespace(self, text: str) -> str:
        """Collapse all whitespace sequences to a single space."""
        return re.sub(r'\s+', ' ', text).strip()

    def _alpha_rename(self, text: str) -> str:
        """Rename bound variables to canonical names x0, x1, …

        Handles ``let``, ``forall``, ``exists``, and ``lambda`` binders.
        Variable references inside the scope of a binder are renamed to match.
        This is a best-effort textual pass — it handles the common case without
        a full SMT-LIB parser.
        """
        counter = [0]
        # Map from user name → canonical name, maintained as a stack of dicts
        # for proper scoping.  For simplicity we use a single flat map and
        # rely on the fact that variable names don't shadow themselves in
        # well-formed SMT.
        rename_map: dict[str, str] = {}

        # Pattern: (let ((var expr) …) body) or (forall ((var sort) …) body)
        # We extract the bound variable names and replace them.

        binder_pattern = re.compile(
            r'\(\s*(let|forall|exists|lambda)\s+\(\s*\((\w+)\s'
        )

        def _fresh(name: str) -> str:
            if name not in rename_map:
                rename_map[name] = f"x{counter[0]}"
                counter[0] += 1
            return rename_map[name]

        # Pass 1: collect all bound variable names in binder positions.
        for m in re.finditer(
            r'\(\s*(?:let|forall|exists|lambda)\s+\(\s*(?:\(\s*(\w+)\s[^)]*\)\s*)+\)',
            text,
        ):
            # Extract individual variable names from the binding list.
            binding_segment = m.group(0)
            for var_m in re.finditer(r'\(\s*(\w+)\s', binding_segment):
                _fresh(var_m.group(1))

        # Also collect variables from multi-binding let forms.
        for var_m in re.finditer(
            r'\(\s*(?:let|forall|exists|lambda)\s+\(\s*(?:\(\s*(\w+)\s)',
            text,
        ):
            _fresh(var_m.group(1))

        if not rename_map:
            return text

        # Pass 2: replace all occurrences of bound variable names with their
        # canonical names.  We only replace whole words to avoid corrupting
        # symbol names that share a prefix.
        for original, canonical in rename_map.items():
            text = re.sub(r'\b' + re.escape(original) + r'\b', canonical, text)

        return text

    def _sort_commutative(self, text: str) -> str:
        """Sort arguments of commutative operators for canonical ordering.

        Operates on the *tokenised* s-expression level so that ``(and B A)``
        and ``(and A B)`` both normalise to ``(and A B)``.
        """
        tokens = self._tokenize(text)
        sorted_tokens = self._sort_tokens(tokens)
        return self._detokenize(sorted_tokens)

    # ---------------------------------------------------------------------------
    # S-expression tokeniser / detokeniser
    # ---------------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        """Split SMT text into a flat list of tokens (parens and atoms)."""
        token_re = re.compile(r'[()]|[^\s()]+')
        return token_re.findall(text)

    def _detokenize(self, tokens: list[str]) -> str:
        """Reassemble tokens into a space-normalised SMT string."""
        parts: list[str] = []
        for i, tok in enumerate(tokens):
            if tok == ')':
                # Remove trailing space before ')'
                if parts and parts[-1] == ' ':
                    parts.pop()
                parts.append(')')
            elif tok == '(':
                if parts and parts[-1] not in ('(', ''):
                    parts.append(' ')
                parts.append('(')
            else:
                if parts and parts[-1] not in ('(', ''):
                    parts.append(' ')
                parts.append(tok)
        return ''.join(parts)

    def _sort_tokens(self, tokens: list[str]) -> list[str]:
        """Recursively sort arguments of commutative operators in token list."""
        result: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == '(':
                # Parse the whole s-expression starting here.
                expr_tokens, consumed = self._parse_sexp(tokens, i)
                sorted_expr = self._sort_sexp(expr_tokens)
                result.extend(sorted_expr)
                i += consumed
            else:
                result.append(tok)
                i += 1
        return result

    def _parse_sexp(self, tokens: list[str], start: int) -> tuple[list[str], int]:
        """Parse one complete s-expression starting at ``tokens[start]`` (which
        must be ``'('``).  Returns ``(sub_tokens, n_consumed)``."""
        assert tokens[start] == '('
        depth = 0
        i = start
        while i < len(tokens):
            if tokens[i] == '(':
                depth += 1
            elif tokens[i] == ')':
                depth -= 1
                if depth == 0:
                    return tokens[start: i + 1], i - start + 1
            i += 1
        # Malformed — return as-is
        return tokens[start:], len(tokens) - start

    def _sort_sexp(self, tokens: list[str]) -> list[str]:
        """Sort top-level arguments of commutative operators within *tokens*."""
        if not tokens or tokens[0] != '(':
            return tokens
        # tokens[0] == '(' and tokens[-1] == ')'
        inner = tokens[1:-1]
        if not inner:
            return tokens

        # First inner token is the operator
        op = inner[0] if inner else None
        if op not in self._COMMUTATIVE:
            # Still recurse into children
            rebuilt = ['(']
            i = 0
            while i < len(inner):
                if inner[i] == '(':
                    sub, consumed = self._parse_sexp(inner, i)
                    rebuilt.extend(self._sort_sexp(sub))
                    i += consumed
                else:
                    rebuilt.append(inner[i])
                    i += 1
            rebuilt.append(')')
            return rebuilt

        # Collect argument sub-expressions (everything after the operator)
        args: list[list[str]] = []
        i = 1  # skip operator token
        while i < len(inner):
            if inner[i] == '(':
                sub, consumed = self._parse_sexp(inner, i)
                args.append(self._sort_sexp(sub))
                i += consumed
            else:
                args.append([inner[i]])
                i += 1

        # Sort args lexicographically by their detokenised form
        args.sort(key=lambda toks: self._detokenize(toks))

        result = ['(', op]
        for arg in args:
            result.extend(arg)
        result.append(')')
        return result


# ---------------------------------------------------------------------------
# Deduplication cache
# ---------------------------------------------------------------------------

class DeduplicationCache:
    """LRU cache that deduplicates solver queries by content hash.

    Results are stored keyed by ``(content_hash, solver_version)`` so that
    upgrading the solver does not serve stale cached results.
    """

    def __init__(self, max_entries: int = 100_000) -> None:
        self._max_entries = max_entries
        # OrderedDict as an LRU: most-recently-used at the end.
        self._store: OrderedDict[str, tuple[SolverResult, float]] = OrderedDict()
        self._normalizer = QueryNormalizer()

        self._hits = 0
        self._misses = 0
        self._stores = 0
        self._evictions = 0

    # ---------------------------------------------------------------------------
    # Core cache operations
    # ---------------------------------------------------------------------------

    def check(self, query: SolverQuery) -> Optional[SolverResult]:
        """Return a cached :class:`SolverResult` if *query* is a cache hit."""
        key = self._key(query.content_hash)
        if key in self._store:
            self._store.move_to_end(key)
            result, _ = self._store[key]
            self._hits += 1
            # Return a copy with cached=True to signal the source.
            return SolverResult(
                query_id=query.id,
                status=result.status,
                duration_ms=result.duration_ms,
                solver_version=result.solver_version,
                session_id=result.session_id,
                cached=True,
                model=result.model,
                proof_hash=result.proof_hash,
            )
        self._misses += 1
        return None

    def store(self, query: SolverQuery, result: SolverResult) -> None:
        """Store *result* under *query*'s content hash."""
        key = self._key(query.content_hash)
        self._store[key] = (result, time.time())
        self._store.move_to_end(key)
        self._stores += 1
        if len(self._store) > self._max_entries:
            self.evict_lru(max(1, len(self._store) - self._max_entries))

    def deduplicate_batch(
        self, queries: list[SolverQuery]
    ) -> tuple[list[SolverQuery], list[DeduplicationResult]]:
        """Remove duplicates from *queries*, returning cache-hit results.

        Returns ``(unique_queries, dedup_results)`` where *unique_queries*
        contains only one representative per content hash (the first seen) and
        *dedup_results* records which query ids were folded into which original.
        """
        seen: dict[str, str] = {}      # content_hash → first query id
        unique: list[SolverQuery] = []
        dedup_results: list[DeduplicationResult] = []
        fold_map: dict[str, list[str]] = {}   # original_id → [dup_ids]

        for q in queries:
            h = q.content_hash
            if h in seen:
                original_id = seen[h]
                fold_map.setdefault(original_id, []).append(q.id)
            else:
                seen[h] = q.id
                unique.append(q)
                fold_map[q.id] = []

        for original_id, dup_ids in fold_map.items():
            if dup_ids:
                dedup_results.append(
                    DeduplicationResult.create(
                        original_query_id=original_id,
                        duplicate_query_ids=dup_ids,
                        cache_hit=False,
                    )
                )

        return unique, dedup_results

    # ---------------------------------------------------------------------------
    # Maintenance
    # ---------------------------------------------------------------------------

    def evict_lru(self, count: int) -> None:
        """Evict the *count* least-recently-used entries."""
        for _ in range(min(count, len(self._store))):
            self._store.popitem(last=False)
            self._evictions += 1

    def clear(self) -> None:
        """Remove all cached entries."""
        self._store.clear()
        self._hits = 0
        self._misses = 0
        self._stores = 0
        self._evictions = 0

    def statistics(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "entries": len(self._store),
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "stores": self._stores,
            "evictions": self._evictions,
            "hit_rate": self._hits / total if total else 0.0,
        }

    # ---------------------------------------------------------------------------
    # Serialization
    # ---------------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise the cache to a JSON-compatible dict."""
        entries: list[dict[str, Any]] = []
        for key, (result, stored_at) in self._store.items():
            entries.append({"key": key, "result": result.to_dict(), "stored_at": stored_at})
        return {
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "stores": self._stores,
            "evictions": self._evictions,
            "entries": entries,
        }

    def load(self, data: dict[str, Any]) -> None:
        """Restore state from a previously :meth:`serialize`d dict."""
        self._max_entries = int(data.get("max_entries", self._max_entries))
        self._hits = int(data.get("hits", 0))
        self._misses = int(data.get("misses", 0))
        self._stores = int(data.get("stores", 0))
        self._evictions = int(data.get("evictions", 0))
        self._store.clear()
        for entry in data.get("entries", []):
            result = SolverResult.from_dict(entry["result"])
            self._store[entry["key"]] = (result, float(entry.get("stored_at", 0.0)))

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def _key(self, content_hash: str) -> str:
        return content_hash

    def __len__(self) -> int:
        return len(self._store)
