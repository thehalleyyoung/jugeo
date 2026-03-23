"""Section 1 of the live_mutation Ch23 implementation: exec/eval as dynamic section
injection.  In sheaf-theoretic terms, executing a code string inserts a new *dynamic
section* at the caller's coordinate with proposal-tier trust.  Evaluating an expression
queries the current value of a section and returns an EvalResult bounded by the exec
context's support.  This module implements the four core components: ExecInjector
(manages exec-based section injection), EvalQuerier (manages eval-based section
queries), NamespaceTracker (tracks what gets added to the semantic space), and
DynamicTrustAssigner (assigns trust levels to dynamic sections based on provenance and
content analysis).  Theory alignment: Ch23 §1 of theory2.tex.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    from jugeo.python_runtime.live_mutation.models import (
        DynamicSection,
        EvalResult,
        ExecContext,
        MutationKind,
        new_context_id,
        new_result_id,
        new_section_id,
    )
except ImportError:  # pragma: no cover - stub for isolated runs
    DynamicSection = EvalResult = ExecContext = MutationKind = None  # type: ignore[assignment,misc]

    def new_section_id() -> str:
        return f"sec-{uuid.uuid4().hex[:12]}"

    def new_context_id() -> str:
        return f"ctx-{uuid.uuid4().hex[:12]}"

    def new_result_id() -> str:
        return f"res-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Trust-tier ordering for upgrade comparisons
# ---------------------------------------------------------------------------
_TIER_ORDER: dict[str, int] = {
    "PROPOSAL": 0,
    "CORROBORATED": 1,
    "VERIFIED": 2,
    "CERTIFIED": 3,
}


def _tier_rank(tier: str) -> int:
    """Return the numeric rank of *tier*, defaulting to 0 for unknown tiers."""
    return _TIER_ORDER.get(tier, 0)


# ---------------------------------------------------------------------------
# ExecInjector
# ---------------------------------------------------------------------------


@dataclass
class ExecInjector:
    """Manages exec-based dynamic section injection.

    When a code string is executed, a new DynamicSection is created and
    registered.  The injector tracks all injected sections, detects symbol
    conflicts, and assigns support coordinates.

    Attributes:
        _sections: Mapping from section_id to section-record dicts.
        _namespace_snapshots: Ordered list of namespace state snapshots taken
            after each injection.
        _injection_count: Running total of all injections (including those
            later invalidated).
        _conflict_log: List of human-readable conflict warning strings.
        support_coordinate: The sheaf coordinate prefix used for all sections
            produced by this injector.
    """

    _sections: dict[str, dict] = field(default_factory=dict)
    _namespace_snapshots: list[dict] = field(default_factory=list)
    _injection_count: int = 0
    _conflict_log: list[str] = field(default_factory=list)
    support_coordinate: str = "exec://dynamic"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject(
        self,
        source_code: str,
        context_id: str,
        global_ns: dict,
        local_ns: dict | None = None,
    ) -> dict:
        """Perform exec injection.

        Parses the source AST to extract all top-level defined names, records
        them, detects conflicts with the existing namespace held by previously
        injected sections, increments the injection count, appends a namespace
        snapshot, and returns a section-record dict.

        Args:
            source_code: Python source code string to inject.
            context_id: Identifier of the exec context (e.g. ``new_context_id()``).
            global_ns: The global namespace dict passed to ``exec``.
            local_ns: Optional local namespace dict.  If *None* the global
                namespace is used as the local namespace as well.

        Returns:
            A section-record dict with the keys ``section_id``,
            ``defined_names``, ``conflict_warnings``, ``trust_level``,
            ``support_coordinate``, ``context_id``, ``source_hash``,
            ``injected_at``, and ``invalidated``.
        """
        section_id = new_section_id()
        defined_names = self._extract_defined_names(source_code)
        conflict_warnings = self._build_conflict_warnings(section_id, defined_names)

        # Persist conflict entries
        for w in conflict_warnings:
            self._conflict_log.append(w)

        # Build the section record
        source_hash = hashlib.sha256(source_code.encode()).hexdigest()[:16]
        record: dict = {
            "section_id": section_id,
            "defined_names": sorted(defined_names),
            "conflict_warnings": conflict_warnings,
            "trust_level": "PROPOSAL",
            "support_coordinate": f"{self.support_coordinate}/{section_id}",
            "context_id": context_id,
            "source_hash": source_hash,
            "injected_at": time.time(),
            "invalidated": False,
        }
        self._sections[section_id] = record
        self._injection_count += 1

        # Snapshot: union of all defined names currently known
        snapshot_names: set[str] = set()
        for sec in self._sections.values():
            if not sec["invalidated"]:
                snapshot_names.update(sec["defined_names"])
        self._namespace_snapshots.append(
            {
                "after_injection": section_id,
                "snapshot_at": time.time(),
                "symbol_count": len(snapshot_names),
                "symbols": sorted(snapshot_names),
            }
        )

        return record

    def get_section(self, section_id: str) -> dict | None:
        """Return the section record for *section_id*, or *None* if not found.

        Args:
            section_id: The identifier returned by a prior :meth:`inject` call.

        Returns:
            The mutable section-record dict, or *None*.
        """
        return self._sections.get(section_id)

    def list_sections(self) -> list[str]:
        """Return a sorted list of all injected section IDs (including invalidated).

        Returns:
            Alphabetically sorted list of section ID strings.
        """
        return sorted(self._sections.keys())

    def detect_conflicts(self, new_names: set[str]) -> list[str]:
        """Return symbol names from *new_names* already present in any active section.

        Args:
            new_names: Set of symbol names that would be introduced by a
                prospective new injection.

        Returns:
            Sorted list of conflicting symbol names.
        """
        existing: set[str] = set()
        for sec in self._sections.values():
            if not sec["invalidated"]:
                existing.update(sec["defined_names"])
        return sorted(new_names & existing)

    def invalidate_section(self, section_id: str) -> bool:
        """Mark a section as invalidated, removing its symbols from the active namespace.

        Args:
            section_id: The section to invalidate.

        Returns:
            *True* if the section was found and successfully marked; *False*
            if the section does not exist or was already invalidated.
        """
        rec = self._sections.get(section_id)
        if rec is None or rec["invalidated"]:
            return False
        rec["invalidated"] = True
        rec["invalidated_at"] = time.time()
        return True

    def active_sections(self) -> list[dict]:
        """Return all non-invalidated section records.

        Returns:
            List of section-record dicts, ordered by injection time.
        """
        return [
            s
            for s in sorted(self._sections.values(), key=lambda x: x["injected_at"])
            if not s["invalidated"]
        ]

    def injection_stats(self) -> dict:
        """Return a summary dict of injector statistics.

        Returns:
            Dict with ``total_injections``, ``active_count``,
            ``conflict_count``, and ``avg_symbols_per_injection``.
        """
        active = self.active_sections()
        total_symbols = sum(len(s["defined_names"]) for s in self._sections.values())
        avg = total_symbols / self._injection_count if self._injection_count else 0.0
        return {
            "total_injections": self._injection_count,
            "active_count": len(active),
            "conflict_count": len(self._conflict_log),
            "avg_symbols_per_injection": round(avg, 4),
        }

    def export_namespace(self) -> dict[str, str]:
        """Return a mapping of symbol name to owning section ID for active sections.

        When multiple active sections define the same symbol, the most-recently
        injected section wins (last writer wins semantics).

        Returns:
            Dict mapping ``symbol_name`` → ``section_id``.
        """
        result: dict[str, str] = {}
        for sec in sorted(self._sections.values(), key=lambda x: x["injected_at"]):
            if not sec["invalidated"]:
                for name in sec["defined_names"]:
                    result[name] = sec["section_id"]
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_defined_names(self, source_code: str) -> set[str]:
        """Parse *source_code* and return names defined at the top level."""
        defined: set[str] = set()
        try:
            tree = ast.parse(source_code, mode="exec")
        except SyntaxError:
            return defined
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    defined.add(node.target.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name.split(".")[0]
                    defined.add(name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    if name != "*":
                        defined.add(name)
        return defined

    def _build_conflict_warnings(
        self, section_id: str, new_names: set[str]
    ) -> list[str]:
        """Build a list of human-readable conflict warning strings."""
        conflicts = self.detect_conflicts(new_names)
        return [
            f"Section {section_id} redefines '{n}' already present in active namespace"
            for n in conflicts
        ]


# ---------------------------------------------------------------------------
# EvalQuerier
# ---------------------------------------------------------------------------


@dataclass
class EvalQuerier:
    """Manages eval-based section queries.

    Evaluating an expression queries the current semantic section value and
    returns an EvalResult with support bounded by the exec context.  The
    querier maintains a query log and tracks expression patterns.

    Attributes:
        _query_log: Ordered list of query-result-record dicts.
        _context_snapshots: Mapping from context_id to the last query record
            issued under that context.
        _error_count: Running total of queries that failed syntax validation.
        max_expression_length: Maximum allowed length (in characters) for an
            expression string; longer expressions are rejected as invalid.
    """

    _query_log: list[dict] = field(default_factory=list)
    _context_snapshots: dict[str, dict] = field(default_factory=dict)
    _error_count: int = 0
    max_expression_length: int = 4096

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(
        self,
        expression: str,
        context_id: str,
        trust_level: str = "PROPOSAL",
        support_keys: frozenset | None = None,
    ) -> dict:
        """Record a query attempt and validate the expression.

        Validates the expression length and attempts to parse it with
        :func:`ast.parse` to check syntax.  Returns a result-record dict
        regardless of whether the expression is syntactically valid.

        Args:
            expression: The Python expression string to query.
            context_id: Identifier of the exec context scoping this query.
            trust_level: Trust tier of the calling context.
            support_keys: Optional frozenset of namespace keys that bound the
                support of this eval result.

        Returns:
            A result-record dict with ``result_id``, ``expression``,
            ``context_id``, ``trust_level``, ``support_keys``,
            ``is_valid_syntax``, ``evaluated_at``, ``complexity``, and
            ``error`` (``None`` or an error message string).
        """
        result_id = new_result_id()
        evaluated_at = time.time()
        error: str | None = None
        is_valid = False

        # Length check
        if len(expression) > self.max_expression_length:
            error = (
                f"Expression exceeds max length ({len(expression)} > "
                f"{self.max_expression_length})"
            )
            self._error_count += 1
        else:
            # Syntax check
            try:
                ast.parse(expression, mode="eval")
                is_valid = True
            except SyntaxError as exc:
                error = f"SyntaxError: {exc}"
                self._error_count += 1

        complexity = self.expression_complexity(expression) if is_valid else 0

        record: dict = {
            "result_id": result_id,
            "expression": expression,
            "context_id": context_id,
            "trust_level": trust_level,
            "support_keys": list(support_keys) if support_keys is not None else [],
            "is_valid_syntax": is_valid,
            "evaluated_at": evaluated_at,
            "complexity": complexity,
            "error": error,
        }
        self._query_log.append(record)
        self._context_snapshots[context_id] = record
        return record

    def get_result(self, result_id: str) -> dict | None:
        """Return the query result for *result_id*, or *None* if not found.

        Args:
            result_id: The ``result_id`` value from a prior :meth:`query` call.

        Returns:
            The result-record dict, or *None*.
        """
        for record in self._query_log:
            if record["result_id"] == result_id:
                return record
        return None

    def query_history(self) -> list[dict]:
        """Return all query records ordered by ``evaluated_at`` ascending.

        Returns:
            List of result-record dicts.
        """
        return sorted(self._query_log, key=lambda r: r["evaluated_at"])

    def error_rate(self) -> float:
        """Return the fraction of queries that had syntax or length errors.

        Returns:
            Float in ``[0.0, 1.0]``; 0.0 if no queries have been made.
        """
        total = len(self._query_log)
        if total == 0:
            return 0.0
        return self._error_count / total

    def expression_complexity(self, expression: str) -> int:
        """Return the AST-node count of *expression* as a complexity proxy.

        Args:
            expression: A Python expression string.

        Returns:
            Non-negative integer node count; 0 on parse error.
        """
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError:
            return 0
        return sum(1 for _ in ast.walk(tree))

    def recent_queries(self, n: int = 10) -> list[dict]:
        """Return the *n* most recent query records (newest first).

        Args:
            n: Maximum number of records to return.

        Returns:
            List of result-record dicts, most recent first.
        """
        ordered = sorted(self._query_log, key=lambda r: r["evaluated_at"], reverse=True)
        return ordered[:n]

    def clear_history(self) -> int:
        """Clear the query log and reset the error count.

        Returns:
            The number of records that were cleared.
        """
        count = len(self._query_log)
        self._query_log.clear()
        self._context_snapshots.clear()
        self._error_count = 0
        return count

    def query_stats(self) -> dict:
        """Return a summary of querier statistics.

        Returns:
            Dict with ``total_queries``, ``error_count``, ``error_rate``,
            ``avg_expression_length``, and ``unique_expressions``.
        """
        total = len(self._query_log)
        avg_len = (
            sum(len(r["expression"]) for r in self._query_log) / total
            if total
            else 0.0
        )
        unique = len({r["expression"] for r in self._query_log})
        return {
            "total_queries": total,
            "error_count": self._error_count,
            "error_rate": round(self.error_rate(), 4),
            "avg_expression_length": round(avg_len, 2),
            "unique_expressions": unique,
        }


# ---------------------------------------------------------------------------
# NamespaceTracker
# ---------------------------------------------------------------------------


@dataclass
class NamespaceTracker:
    """Tracks what gets added to the semantic space when sections are injected.

    Maintains a layered view of the namespace, records provenance for each
    symbol, and detects shadowing and pollution.

    Attributes:
        _symbol_provenance: Maps symbol name → section_id that owns it.
        _shadow_log: Ordered log of shadowing events (dicts with
            ``symbol``, ``old_section``, ``new_section``, ``shadowed_at``).
        _layer_stack: Stack of namespace layers (each layer is a frozenset of
            symbol names).
        _total_additions: Cumulative count of symbol additions (including
            re-additions that shadow prior entries).
    """

    _symbol_provenance: dict[str, str] = field(default_factory=dict)
    _shadow_log: list[dict] = field(default_factory=list)
    _layer_stack: list[frozenset] = field(default_factory=list)
    _total_additions: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_injection(self, section_id: str, symbols: set[str]) -> list[str]:
        """Record that *section_id* defined *symbols*.

        Detects and logs shadows for any symbol already tracked under a
        different section.

        Args:
            section_id: The section performing the injection.
            symbols: Set of symbol names being introduced.

        Returns:
            Sorted list of symbol names that were shadowed by this injection.
        """
        shadowed: list[str] = []
        for sym in sorted(symbols):
            self._total_additions += 1
            if sym in self._symbol_provenance and self._symbol_provenance[sym] != section_id:
                old_section = self._symbol_provenance[sym]
                shadowed.append(sym)
                self._shadow_log.append(
                    {
                        "symbol": sym,
                        "old_section": old_section,
                        "new_section": section_id,
                        "shadowed_at": time.time(),
                    }
                )
            self._symbol_provenance[sym] = section_id
        return shadowed

    def revoke_section(self, section_id: str) -> set[str]:
        """Remove all symbols whose provenance is *section_id*.

        Args:
            section_id: The section being revoked.

        Returns:
            Set of symbol names that were removed from the provenance mapping.
        """
        revoked: set[str] = {
            sym for sym, owner in self._symbol_provenance.items() if owner == section_id
        }
        for sym in revoked:
            del self._symbol_provenance[sym]
        return revoked

    def push_layer(self, symbols: frozenset[str]) -> int:
        """Push a new namespace layer onto the layer stack.

        Args:
            symbols: The set of symbols forming the new layer.

        Returns:
            The new layer depth (1-indexed).
        """
        self._layer_stack.append(symbols)
        return len(self._layer_stack)

    def pop_layer(self) -> frozenset[str] | None:
        """Pop and return the top namespace layer.

        Returns:
            The popped frozenset of symbol names, or *None* if the stack is empty.
        """
        if not self._layer_stack:
            return None
        return self._layer_stack.pop()

    def symbol_owner(self, name: str) -> str | None:
        """Return the section_id that currently owns *name*.

        Args:
            name: The symbol name to look up.

        Returns:
            A section_id string, or *None* if the symbol is not tracked.
        """
        return self._symbol_provenance.get(name)

    def all_symbols(self) -> set[str]:
        """Return the complete set of currently tracked symbol names.

        Returns:
            Set of symbol name strings.
        """
        return set(self._symbol_provenance.keys())

    def shadow_report(self) -> list[dict]:
        """Return the shadow log as a list of event dicts.

        Returns:
            List of shadow-event dicts with keys ``symbol``, ``old_section``,
            ``new_section``, ``shadowed_at``.
        """
        return list(self._shadow_log)

    def pollution_score(self) -> float:
        """Return a 0.0–1.0 namespace pollution score.

        Pollution is defined as the ratio of *unique shadowed symbols* to the
        total number of tracked symbols.  A score of 0.0 indicates no
        shadowing; 1.0 would mean every tracked symbol has been shadowed at
        least once.

        Returns:
            Float in ``[0.0, 1.0]``; 0.0 if no symbols are tracked.
        """
        total = len(self._symbol_provenance)
        if total == 0:
            return 0.0
        shadowed_unique = len({e["symbol"] for e in self._shadow_log})
        return min(1.0, shadowed_unique / total)

    def tracker_stats(self) -> dict:
        """Return a summary of tracker statistics.

        Returns:
            Dict with ``total_symbols``, ``total_additions``,
            ``shadow_count``, ``layer_depth``, ``pollution_score``.
        """
        return {
            "total_symbols": len(self._symbol_provenance),
            "total_additions": self._total_additions,
            "shadow_count": len(self._shadow_log),
            "layer_depth": len(self._layer_stack),
            "pollution_score": round(self.pollution_score(), 4),
        }


# ---------------------------------------------------------------------------
# DynamicTrustAssigner
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS: tuple[str, ...] = (
    r"exec\s*\(",
    r"eval\s*\(",
    r"__import__\s*\(",
    r"os\.system\s*\(",
    r"subprocess\.",
    r"__builtins__",
)
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_PATTERNS))


@dataclass
class DynamicTrustAssigner:
    """Assigns trust levels to dynamically injected sections.

    Dynamic sections always start at ``PROPOSAL`` tier.  They can be upgraded
    by external corroboration.  This class computes a numeric trust score and
    maps it to a tier string.

    Trust-tier mapping:

    - Score 0–1 → ``PROPOSAL``
    - Score 2–3 → ``CORROBORATED``
    - Score 4–5 → ``VERIFIED``
    - Score 6+  → ``CERTIFIED``

    Attributes:
        _assignments: List of trust-assignment record dicts.
        _upgrade_log: List of trust-upgrade event dicts.
        default_tier: The tier used as the starting point for new sections
            before scoring (always ``"PROPOSAL"``).
    """

    _assignments: list[dict] = field(default_factory=list)
    _upgrade_log: list[dict] = field(default_factory=list)
    default_tier: str = "PROPOSAL"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assign_trust(
        self,
        section_id: str,
        source_code: str,
        context_id: str,
        corroboration_count: int = 0,
    ) -> str:
        """Compute and record the trust tier for a newly injected section.

        Scoring rubric:

        - **+1** if the source code has fewer than 50 lines.
        - **+2** if the source code contains no dangerous patterns (exec,
          eval, ``__import__``, ``os.system``).
        - **+1 per corroboration** up to a maximum of **+3**.

        Args:
            section_id: The section being assigned a trust tier.
            source_code: The Python source text of the section.
            context_id: The exec context in which the section was injected.
            corroboration_count: Non-negative integer count of external
                corroborations for this section.

        Returns:
            A trust-tier string: ``"PROPOSAL"``, ``"CORROBORATED"``,
            ``"VERIFIED"``, or ``"CERTIFIED"``.
        """
        score = self.trust_score(source_code, corroboration_count)
        tier = self._score_to_tier(score)
        record: dict = {
            "section_id": section_id,
            "context_id": context_id,
            "score": score,
            "tier": tier,
            "assigned_at": time.time(),
            "corroboration_count": corroboration_count,
            "has_dangerous_patterns": self.contains_dangerous_patterns(source_code),
        }
        self._assignments.append(record)
        return tier

    def upgrade_trust(self, section_id: str, new_tier: str, reason: str) -> bool:
        """Upgrade a section's trust tier if *new_tier* is strictly higher.

        Only the most recent assignment for *section_id* is upgraded.

        Args:
            section_id: The section to upgrade.
            new_tier: The desired new tier (must be strictly higher).
            reason: Human-readable justification for the upgrade.

        Returns:
            *True* if the upgrade was applied; *False* if the section was not
            found or if *new_tier* is not strictly higher than the current tier.
        """
        current = self.get_trust(section_id)
        if current is None:
            return False
        if _tier_rank(new_tier) <= _tier_rank(current):
            return False
        # Update the most recent assignment
        for rec in reversed(self._assignments):
            if rec["section_id"] == section_id:
                rec["tier"] = new_tier
                break
        self._upgrade_log.append(
            {
                "section_id": section_id,
                "old_tier": current,
                "new_tier": new_tier,
                "reason": reason,
                "upgraded_at": time.time(),
            }
        )
        return True

    def get_trust(self, section_id: str) -> str | None:
        """Return the current trust tier for *section_id*.

        Args:
            section_id: The section to look up.

        Returns:
            The tier string, or *None* if no assignment exists.
        """
        for rec in reversed(self._assignments):
            if rec["section_id"] == section_id:
                return rec["tier"]
        return None

    def trust_score(self, source_code: str, corroboration_count: int = 0) -> int:
        """Compute the numeric trust score for *source_code*.

        Args:
            source_code: Python source text.
            corroboration_count: Non-negative external corroboration count.

        Returns:
            Non-negative integer trust score.
        """
        score = 0
        lines = source_code.splitlines()
        if len(lines) < 50:
            score += 1
        if not self.contains_dangerous_patterns(source_code):
            score += 2
        score += min(corroboration_count, 3)
        return score

    def contains_dangerous_patterns(self, source_code: str) -> bool:
        """Return *True* if *source_code* contains dangerous execution patterns.

        Dangerous patterns include: ``exec(``, ``eval(``, ``__import__(``,
        ``os.system(``, ``subprocess.``, ``__builtins__``.

        Args:
            source_code: Python source text to inspect.

        Returns:
            Boolean indicating presence of dangerous patterns.
        """
        return bool(_DANGEROUS_RE.search(source_code))

    def assignment_history(self) -> list[dict]:
        """Return all trust-assignment records in chronological order.

        Returns:
            List of assignment-record dicts.
        """
        return list(self._assignments)

    def tier_distribution(self) -> dict[str, int]:
        """Return the count of sections per trust tier across all assignments.

        Only the *most recent* assignment per section is counted.

        Returns:
            Dict mapping tier-string → count.
        """
        latest: dict[str, str] = {}
        for rec in self._assignments:
            latest[rec["section_id"]] = rec["tier"]
        dist: dict[str, int] = {}
        for tier in latest.values():
            dist[tier] = dist.get(tier, 0) + 1
        return dist

    def assigner_stats(self) -> dict:
        """Return a summary of assigner statistics.

        Returns:
            Dict with ``total_assignments``, ``upgrades``,
            ``tier_distribution``.
        """
        return {
            "total_assignments": len(self._assignments),
            "upgrades": len(self._upgrade_log),
            "tier_distribution": self.tier_distribution(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_tier(score: int) -> str:
        """Map a numeric *score* to a tier string."""
        if score <= 1:
            return "PROPOSAL"
        if score <= 3:
            return "CORROBORATED"
        if score <= 5:
            return "VERIFIED"
        return "CERTIFIED"


# ---------------------------------------------------------------------------
# Module-level convenience factories
# ---------------------------------------------------------------------------


def make_exec_injector(support_coordinate: str = "exec://dynamic") -> ExecInjector:
    """Create a fresh :class:`ExecInjector` with the given support coordinate.

    Args:
        support_coordinate: Sheaf coordinate prefix for injected sections.

    Returns:
        A new :class:`ExecInjector` instance.
    """
    return ExecInjector(support_coordinate=support_coordinate)


def make_eval_querier(max_expression_length: int = 4096) -> EvalQuerier:
    """Create a fresh :class:`EvalQuerier` with the given expression length cap.

    Args:
        max_expression_length: Maximum allowed expression length in characters.

    Returns:
        A new :class:`EvalQuerier` instance.
    """
    return EvalQuerier(max_expression_length=max_expression_length)


def make_namespace_tracker() -> NamespaceTracker:
    """Create a fresh :class:`NamespaceTracker`.

    Returns:
        A new :class:`NamespaceTracker` instance.
    """
    return NamespaceTracker()


def make_dynamic_trust_assigner(default_tier: str = "PROPOSAL") -> DynamicTrustAssigner:
    """Create a fresh :class:`DynamicTrustAssigner` with the given default tier.

    Args:
        default_tier: Starting tier for newly assigned sections.  Should
            almost always be ``"PROPOSAL"``.

    Returns:
        A new :class:`DynamicTrustAssigner` instance.
    """
    return DynamicTrustAssigner(default_tier=default_tier)


__all__ = [
    "ExecInjector",
    "EvalQuerier",
    "NamespaceTracker",
    "DynamicTrustAssigner",
    "make_exec_injector",
    "make_eval_querier",
    "make_namespace_tracker",
    "make_dynamic_trust_assigner",
]

# copilot: exec/eval dynamic section injection for live_mutation Ch23 §1
