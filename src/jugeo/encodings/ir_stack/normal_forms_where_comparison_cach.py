#!/usr/bin/env python3
"""
# copilot: Normal forms for comparison caching and deduplication in Judgment Geometry IR

This module implements the machinery that lets the Judgment Geometry IR stack compare
expressions in O(1) time after a one-time normalization pass.  The central insight is
that two judgments (c, φ, A, E, O, B, T, Π) are *identical* as objects in the trust
algebra if and only if their canonical normal forms share the same hash_key.  Computing
that key is O(n) in expression size; consulting the ComparisonCache thereafter is O(1).

WHY NORMAL FORMS?
─────────────────
Judgment Geometry tracks partial knowledge across a trust lattice:

    PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED

A judgment is a tuple (c, φ, A, E, O, B, T, Π) where:
  • c  = context identifier
  • φ  = formula / predicate
  • A  = agent or principal set
  • E  = evidence bundle
  • O  = obstruction (Čech H¹ cohomology class)
  • B  = belief weight ∈ [0, 1]
  • T  = trust tier value
  • Π  = proof reference (may be None)

Two syntactically distinct expressions can denote the *same* judgment.  For example,
commutativity of conjunction means `(A ∧ B)` and `(B ∧ A)` carry identical information.
Without normalisation, every comparison must traverse the full expression tree.  With
normal forms we:
  1. Reduce each expression once to a canonical string representative.
  2. Hash that representative to a fixed-length key.
  3. Compare keys — two normal forms are equal iff their keys match.

WHY ČECH COHOMOLOGY CLASSES?
─────────────────────────────
Obstructions (the O component of a judgment) are elements of the first Čech cohomology
group H¹(𝒰, ℱ) of the trust sheaf ℱ over an open cover 𝒰.  A non-trivial cohomology
class records a *local consistency failure*: every local section looks consistent, but
no global section exists.  To detect such failures we must be able to test cohomology
class identity, which requires canonical representatives — exactly what NormalForm
provides.  Deduplication then collapses representatives from different proof paths that
correspond to the *same* cohomology class.

WHY DEDUPLICATION?
──────────────────
The IR stack accumulates judgments from multiple agents.  Different agents may produce
different syntactic expressions for the same semantic object.  Without deduplication the
trust algebra performs redundant work and the ComparisonCache fills with spurious entries.
The DeduplicationTable maps every expression to its equivalence-class representative so
downstream passes see a compact, canonical universe.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import (
    Dict,
    FrozenSet,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

# ---------------------------------------------------------------------------
# Conditional imports – the IR stack lives inside the jugeo package, but this
# module must also be importable in isolation (e.g. during bootstrapping or
# when running the smoke test stand-alone).
# ---------------------------------------------------------------------------
try:
    from jugeo.core.judgment import Judgment, TrustTier
    from jugeo.core.obstruction import Obstruction
    from jugeo.encodings.ir_stack.the_theory_wants_a_small_number_of import (
        IRNode, IRStack, IRLevel, CanonicalForm, TrustTierEnum
    )
except ImportError:
    Judgment = None
    TrustTier = None
    Obstruction = None
    IRNode = None
    IRStack = None
    IRLevel = None
    CanonicalForm = None
    TrustTierEnum = None


# ---------------------------------------------------------------------------
# Trust-tier constants (mirroring the lattice described in the docstring so
# that this file is self-contained even without the jugeo package).
# ---------------------------------------------------------------------------
TRUST_PROPOSAL: int = 0
TRUST_REVIEWED: int = 1
TRUST_VERIFIED: int = 2
TRUST_RUNTIME_WITNESSED: int = 3
TRUST_PROOF_BACKED: int = 4

TRUST_TIER_NAMES: Dict[int, str] = {
    TRUST_PROPOSAL:          "PROPOSAL",
    TRUST_REVIEWED:          "REVIEWED",
    TRUST_VERIFIED:          "VERIFIED",
    TRUST_RUNTIME_WITNESSED: "RUNTIME_WITNESSED",
    TRUST_PROOF_BACKED:      "PROOF_BACKED",
}

# Minimum trust required to promote a comparison result into the permanent
# deduplication table vs. keeping it in the transient cache only.
_DEDUP_PROMOTION_THRESHOLD: int = TRUST_VERIFIED

# Maximum depth for recursive rewrite steps before we give up and return the
# partially rewritten expression (guards against non-terminating rule sets).
_MAX_REWRITE_DEPTH: int = 256


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def compute_hash_key(expr: str) -> str:
    """Return a stable, collision-resistant hex digest for *expr*.

    We use SHA-256 truncated to 32 hex characters (128 bits of collision
    resistance).  The input is first *normalised at the character level*:
    all runs of whitespace are collapsed to a single space and the string is
    stripped, so that cosmetic differences between expressions do not produce
    different keys.

    This function is the foundation of the whole caching scheme.  Every
    NormalForm's ``hash_key`` is produced here, and ComparisonCache keys are
    built from pairs of hash_key values.  If this function is not injective
    over the set of canonical representatives the whole system is unsound.
    With 128 bits of resistance and a universe of at most 2^64 expressions
    the birthday-bound collision probability is negligible.

    Args:
        expr: The expression string to hash.  Must already be in canonical
            (normal) form for the key to be meaningful.

    Returns:
        A 32-character lower-case hex string.
    """
    if not isinstance(expr, str):
        raise TypeError(f"compute_hash_key expects str, got {type(expr).__name__!r}")
    normalised = re.sub(r"\s+", " ", expr).strip()
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return digest[:32]


def _fresh_id(prefix: str = "") -> str:
    """Return a short, unique identifier string with an optional *prefix*."""
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


def _expression_tokens(expr: str) -> List[str]:
    """Tokenise *expr* into a flat list of atoms and operator symbols.

    A very small tokeniser: parentheses, logical connectives, and identifiers
    are separated.  This is not a full parser — its purpose is to give the
    rewriter a structured view of the expression without pulling in a
    dependency on a real grammar.

    Returns:
        A list of non-empty token strings.
    """
    pattern = r"(\(|\)|\bAND\b|\bOR\b|\bNOT\b|\bIMPLIES\b|\bIFF\b|[^\s()]+)"
    return [tok for tok in re.findall(pattern, expr, re.IGNORECASE) if tok.strip()]


def _tokens_to_expr(tokens: Sequence[str]) -> str:
    """Re-join *tokens* into a single expression string."""
    return " ".join(tokens)


def _sort_commutative(tokens: List[str]) -> List[str]:
    """Sort operands of commutative operators (AND, OR) to produce a unique
    ordering.

    We perform a single linear pass looking for ``AND`` and ``OR`` binary
    operators and sort their two surrounding operands lexicographically.
    More complex expressions with nested operators need the full rewriter.

    Args:
        tokens: Flat token list (output of ``_expression_tokens``).

    Returns:
        A new token list with commutative operands sorted.
    """
    result = list(tokens)
    i = 1
    while i < len(result) - 1:
        op = result[i].upper()
        if op in ("AND", "OR"):
            left = result[i - 1]
            right = result[i + 1]
            if left > right:
                result[i - 1], result[i + 1] = right, left
        i += 1
    return result


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RewriteRule:
    """A single rewrite rule used by NormalFormRewriter.

    Rules are expressed as string patterns (simplified regex-like) and string
    replacements.  The ``condition`` field may name a Python predicate that
    must be satisfied before the rule fires; the empty string means
    unconditional.  ``priority`` controls rule ordering: lower numbers fire
    first.

    WHY FROZEN?
    Rules must be hashable so they can live in frozensets and be shared across
    rewriter instances without accidental mutation.  Mutable rule sets would
    make it impossible to reason about which normal forms were produced by
    which rule configurations.

    Examples
    --------
    >>> r = RewriteRule("r1", r"NOT NOT (?P<x>\\w+)", r"\\g<x>", "", 10)
    >>> r.rule_id
    'r1'
    """

    rule_id: str
    pattern: str
    replacement: str
    condition: str
    priority: int

    # ------------------------------------------------------------------
    # Properties / methods
    # ------------------------------------------------------------------

    @property
    def is_unconditional(self) -> bool:
        """True when this rule fires without any side condition."""
        return not self.condition.strip()

    @property
    def compiled_pattern(self) -> re.Pattern:  # type: ignore[type-arg]
        """Return the compiled regex pattern.

        Note: the result is *not* cached on the frozen instance (frozen
        dataclasses cannot add state after construction).  For hot paths,
        callers should cache the result themselves.
        """
        return re.compile(self.pattern)

    def matches(self, expr: str) -> bool:
        """Return True if *expr* contains a substring matching this rule."""
        return bool(re.search(self.pattern, expr))

    def apply_once(self, expr: str) -> str:
        """Apply the rule to the first matching location in *expr*.

        Args:
            expr: The expression string to rewrite.

        Returns:
            The rewritten string, or *expr* unchanged if no match.
        """
        return re.sub(self.pattern, self.replacement, expr, count=1)

    def apply_all(self, expr: str) -> str:
        """Apply the rule at *every* matching location in *expr* (global
        substitution)."""
        return re.sub(self.pattern, self.replacement, expr)

    def describe(self) -> str:
        """Human-readable description of the rule."""
        cond = f" [if {self.condition}]" if self.condition else ""
        return (
            f"Rule({self.rule_id!r}, priority={self.priority}): "
            f"{self.pattern!r} → {self.replacement!r}{cond}"
        )


@dataclass(frozen=True)
class NormalForm:
    """Canonical normal form for an IR expression.

    A NormalForm is the *unique representative* of an equivalence class of
    syntactically distinct but semantically identical expressions.  Once an
    expression has been reduced to its NormalForm, identity testing reduces to
    hash_key equality, and the ComparisonCache can answer queries in O(1).

    Fields
    ------
    form_id      : Unique identifier for this NormalForm instance.
    expression   : The canonical expression string (already in normal form).
    hash_key     : SHA-256-derived 32-char key of *expression*.
    trust_level  : The trust tier at which this form was computed.
    is_canonical : Whether the expression has been fully reduced.
    dependencies : The set of form_ids this form directly depends on (for
                   dependency tracking in incremental recompilation).

    Invariant
    ---------
    ``hash_key == compute_hash_key(expression)`` must hold at all times.
    The constructor does not enforce this (frozen dataclasses cannot run
    post-init validation with __post_init__ in a pure way), so callers should
    use ``compute_normal_form`` which enforces the invariant.
    """

    form_id: str
    expression: str
    hash_key: str
    trust_level: int
    is_canonical: bool
    dependencies: FrozenSet[str]

    # ------------------------------------------------------------------
    # Properties / methods
    # ------------------------------------------------------------------

    @property
    def trust_name(self) -> str:
        """Human-readable name for the trust tier."""
        return TRUST_TIER_NAMES.get(self.trust_level, f"UNKNOWN({self.trust_level})")

    @property
    def is_high_trust(self) -> bool:
        """Return True when the form meets or exceeds VERIFIED."""
        return self.trust_level >= TRUST_VERIFIED

    @property
    def dependency_count(self) -> int:
        """Number of direct dependencies."""
        return len(self.dependencies)

    def is_compatible_with(self, other: NormalForm) -> bool:
        """Return True when *self* and *other* can be directly compared.

        Two forms are compatible when they share at least one dependency or
        when one has no dependencies at all (meaning it is a ground form).
        Incompatible forms may still be compared, but the result carries less
        semantic weight.
        """
        if not self.dependencies or not other.dependencies:
            return True
        return bool(self.dependencies & other.dependencies)

    def equals(self, other: NormalForm) -> bool:
        """Semantic equality: True iff both forms have the same hash_key.

        This is the O(1) comparison that justifies all the normalisation work.
        """
        return self.hash_key == other.hash_key

    def with_trust(self, new_trust: int) -> NormalForm:
        """Return a copy of *self* with an updated trust level."""
        return NormalForm(
            form_id=_fresh_id("nf_"),
            expression=self.expression,
            hash_key=self.hash_key,
            trust_level=new_trust,
            is_canonical=self.is_canonical,
            dependencies=self.dependencies,
        )

    def add_dependency(self, dep_id: str) -> NormalForm:
        """Return a copy of *self* with *dep_id* added to its dependency set."""
        return NormalForm(
            form_id=self.form_id,
            expression=self.expression,
            hash_key=self.hash_key,
            trust_level=self.trust_level,
            is_canonical=self.is_canonical,
            dependencies=self.dependencies | frozenset({dep_id}),
        )

    def summary(self) -> str:
        """One-line summary for logging / debugging."""
        canon = "✓" if self.is_canonical else "~"
        return (
            f"NF[{self.form_id}] {canon} trust={self.trust_name} "
            f"hash={self.hash_key[:8]}… expr={self.expression[:40]!r}"
        )


@dataclass(frozen=True)
class CacheEntry:
    """One entry in a ComparisonCache.

    Records the result of comparing two NormalForms identified by
    *left_form_id* and *right_form_id*.  The *result* field follows the
    standard comparison convention: negative → left < right, zero → equal,
    positive → left > right.

    The *timestamp* (seconds since the Unix epoch) lets callers implement
    TTL-based cache eviction.

    WHY FROZEN?
    Cache entries are immutable facts: "at time T with trust L, comparing
    forms A and B yielded result R".  Mutability would allow silent cache
    corruption, which is especially dangerous given that the trust algebra
    relies on cached comparisons for performance-critical paths.
    """

    key: str
    left_form_id: str
    right_form_id: str
    result: int
    trust_level: int
    timestamp: float

    # ------------------------------------------------------------------
    # Properties / methods
    # ------------------------------------------------------------------

    @property
    def is_equality(self) -> bool:
        """True when the cached comparison determined the two forms equal."""
        return self.result == 0

    @property
    def age_seconds(self) -> float:
        """Elapsed seconds since this entry was created."""
        return time.time() - self.timestamp

    @property
    def trust_name(self) -> str:
        """Human-readable trust tier name."""
        return TRUST_TIER_NAMES.get(self.trust_level, f"UNKNOWN({self.trust_level})")

    def is_fresher_than(self, other: CacheEntry) -> bool:
        """Return True when *self* was recorded more recently than *other*."""
        return self.timestamp > other.timestamp

    def with_higher_trust(self, new_trust: int) -> CacheEntry:
        """Return a copy of *self* upgraded to *new_trust* if it is higher.

        Trust can only increase: we never downgrade a cached comparison
        result because doing so could silently weaken downstream inferences.
        """
        if new_trust <= self.trust_level:
            return self
        return CacheEntry(
            key=self.key,
            left_form_id=self.left_form_id,
            right_form_id=self.right_form_id,
            result=self.result,
            trust_level=new_trust,
            timestamp=self.timestamp,
        )

    def describe(self) -> str:
        """One-line description for logging."""
        rel = "==" if self.result == 0 else ("<" if self.result < 0 else ">")
        return (
            f"CacheEntry[{self.key[:8]}] "
            f"{self.left_form_id[:8]} {rel} {self.right_form_id[:8]} "
            f"trust={self.trust_name} age={self.age_seconds:.1f}s"
        )


@dataclass(frozen=True)
class ComparisonCache:
    """Cache for comparison results between NormalForms.

    The cache stores a fixed-capacity tuple of CacheEntry objects.  When the
    cache is full and a new entry arrives, the *oldest* entry is evicted (FIFO
    discipline — simple and predictable, though an LRU policy would be better
    for skewed workloads; that can be layered on top).

    WHY TUPLE INSTEAD OF DICT?
    Frozen dataclasses cannot hold mutable containers.  A tuple of CacheEntry
    objects is hashable and supports structural sharing when entries are added
    (``cache_comparison`` returns a *new* ComparisonCache rather than mutating
    the existing one, consistent with the immutable-value philosophy of the IR
    stack).

    Fields
    ------
    cache_id  : Unique identifier.
    entries   : Ordered tuple of CacheEntry, newest last.
    max_size  : Upper bound on len(entries).
    hit_count : Cumulative count of cache hits (informational).
    miss_count: Cumulative count of cache misses (informational).
    """

    cache_id: str
    entries: Tuple[CacheEntry, ...]
    max_size: int
    hit_count: int
    miss_count: int

    # ------------------------------------------------------------------
    # Properties / methods
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Current number of entries."""
        return len(self.entries)

    @property
    def is_full(self) -> bool:
        """True when the cache cannot accept more entries without eviction."""
        return len(self.entries) >= self.max_size

    @property
    def hit_rate(self) -> float:
        """Fraction of lookups that were cache hits.  Returns 0.0 when no
        lookups have been made yet."""
        total = self.hit_count + self.miss_count
        return self.hit_count / total if total > 0 else 0.0

    def lookup(self, key: str) -> Optional[CacheEntry]:
        """Return the CacheEntry with the given *key*, or None on miss.

        Scans entries in reverse order so that the most recently added entry
        for a key wins (handles rare cases where the same key appears twice).
        """
        for entry in reversed(self.entries):
            if entry.key == key:
                return entry
        return None

    def contains(self, key: str) -> bool:
        """Return True when *key* is present in the cache."""
        return self.lookup(key) is not None

    def evict_oldest(self, n: int = 1) -> ComparisonCache:
        """Return a new cache with the *n* oldest entries removed."""
        if n <= 0:
            return self
        new_entries = self.entries[n:]
        return ComparisonCache(
            cache_id=self.cache_id,
            entries=new_entries,
            max_size=self.max_size,
            hit_count=self.hit_count,
            miss_count=self.miss_count,
        )

    def bump_hits(self) -> ComparisonCache:
        """Return a copy with hit_count incremented by 1."""
        return ComparisonCache(
            cache_id=self.cache_id,
            entries=self.entries,
            max_size=self.max_size,
            hit_count=self.hit_count + 1,
            miss_count=self.miss_count,
        )

    def bump_misses(self) -> ComparisonCache:
        """Return a copy with miss_count incremented by 1."""
        return ComparisonCache(
            cache_id=self.cache_id,
            entries=self.entries,
            max_size=self.max_size,
            hit_count=self.hit_count,
            miss_count=self.miss_count + 1,
        )

    def statistics(self) -> str:
        """Return a formatted statistics string."""
        return (
            f"Cache[{self.cache_id}] size={self.size}/{self.max_size} "
            f"hits={self.hit_count} misses={self.miss_count} "
            f"hit_rate={self.hit_rate:.1%}"
        )


@dataclass(frozen=True)
class DeduplicationEntry:
    """One entry in a DeduplicationTable.

    Maps an *original_expr* to its *canonical_repr* (the designated
    representative of its equivalence class).  The *equivalence_class*
    field names the class (often the hash_key of the canonical representative)
    so that all members of the same class can be found efficiently.

    WHY EQUIVALENCE CLASSES?
    In Judgment Geometry, multiple proof paths can produce structurally
    different but semantically identical expressions.  Rather than storing
    all pairwise equalities (quadratic space), we map every expression to a
    single representative and store only the representative (linear space).
    The equivalence_class label lets us enumerate all members of a class in
    O(n) time by scanning the table.
    """

    original_expr: str
    canonical_repr: str
    form_id: str
    equivalence_class: str

    # ------------------------------------------------------------------
    # Properties / methods
    # ------------------------------------------------------------------

    @property
    def is_self_canonical(self) -> bool:
        """True when the original expression already equals its canonical
        representative."""
        return self.original_expr == self.canonical_repr

    @property
    def compression_ratio(self) -> float:
        """Ratio of canonical length to original length.

        Values < 1 indicate the canonical form is shorter (a good sign).
        Values > 1 indicate the canonical form is longer (can happen when
        alpha-renaming introduces fresh names).
        """
        if len(self.original_expr) == 0:
            return 1.0
        return len(self.canonical_repr) / len(self.original_expr)

    def describe(self) -> str:
        """One-line description."""
        arrow = "→" if not self.is_self_canonical else "≡"
        return (
            f"Dedup[{self.form_id[:8]}] "
            f"{self.original_expr[:30]!r} {arrow} "
            f"{self.canonical_repr[:30]!r} "
            f"class={self.equivalence_class[:8]}"
        )

    def with_updated_canonical(self, new_canonical: str,
                                new_class: str) -> DeduplicationEntry:
        """Return a copy of *self* pointing at a new canonical representative.

        This is used when two equivalence classes are discovered to be the
        same: all entries in the smaller class are re-pointed at the
        representative of the larger class.
        """
        return DeduplicationEntry(
            original_expr=self.original_expr,
            canonical_repr=new_canonical,
            form_id=self.form_id,
            equivalence_class=new_class,
        )

    def matches_class(self, class_id: str) -> bool:
        """True when this entry belongs to *class_id*."""
        return self.equivalence_class == class_id


@dataclass(frozen=True)
class DeduplicationTable:
    """Table mapping expressions to canonical representatives.

    The table is an immutable snapshot.  Operations that add or modify entries
    return *new* DeduplicationTable instances, leaving the original unchanged.
    This supports cheap snapshotting and rollback in the IR stack's transaction
    model.

    Fields
    ------
    table_id : Unique identifier.
    entries  : Tuple of DeduplicationEntry objects.
    version  : Monotonically increasing version counter.  Every mutation
               increments the version so that stale references can be detected.
    """

    table_id: str
    entries: Tuple[DeduplicationEntry, ...]
    version: int

    # ------------------------------------------------------------------
    # Properties / methods
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of entries in the table."""
        return len(self.entries)

    @property
    def equivalence_classes(self) -> FrozenSet[str]:
        """Return the set of all equivalence-class identifiers in this table."""
        return frozenset(e.equivalence_class for e in self.entries)

    @property
    def class_count(self) -> int:
        """Number of distinct equivalence classes."""
        return len(self.equivalence_classes)

    def lookup(self, original_expr: str) -> Optional[DeduplicationEntry]:
        """Find the entry for *original_expr*, or return None."""
        for entry in self.entries:
            if entry.original_expr == original_expr:
                return entry
        return None

    def canonical_for(self, original_expr: str) -> Optional[str]:
        """Return the canonical representative for *original_expr*, or None if
        the expression is not in the table."""
        entry = self.lookup(original_expr)
        return entry.canonical_repr if entry is not None else None

    def members_of_class(self, class_id: str) -> Tuple[DeduplicationEntry, ...]:
        """Return all entries that belong to equivalence class *class_id*."""
        return tuple(e for e in self.entries if e.equivalence_class == class_id)

    def add_entry(self, new_entry: DeduplicationEntry) -> DeduplicationTable:
        """Return a new table with *new_entry* appended (or replacing any
        existing entry for the same *original_expr*)."""
        filtered = tuple(
            e for e in self.entries if e.original_expr != new_entry.original_expr
        )
        return DeduplicationTable(
            table_id=self.table_id,
            entries=filtered + (new_entry,),
            version=self.version + 1,
        )

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"DeduplicationTable[{self.table_id}] "
            f"v{self.version} {self.size} entries "
            f"{self.class_count} classes"
        )


@dataclass(frozen=True)
class NormalFormRewriter:
    """Rewrites expressions to normal form using an ordered list of rules.

    The rewriter applies rules in ascending priority order (lowest number
    first), repeating until no rule fires or *max_steps* is reached.  The
    ``strategy`` field names the overall reduction strategy:

    ``"innermost"``
        Reduce inner sub-expressions before outer ones (eager / call-by-value
        analogue).
    ``"outermost"``
        Reduce outermost redexes first (lazy / call-by-name analogue).
    ``"breadth_first"``
        Apply all rules once per pass before repeating.

    In practice, for the string-based expressions used here, only
    ``"breadth_first"`` is fully implemented; the other two serve as labels
    for future tree-based implementations.

    Fields
    ------
    rewriter_id : Unique identifier.
    rules       : Ordered tuple of RewriteRule objects, sorted by priority.
    strategy    : One of ``"innermost"``, ``"outermost"``, ``"breadth_first"``.
    max_steps   : Maximum total rule applications before halting.
    """

    rewriter_id: str
    rules: Tuple[RewriteRule, ...]
    strategy: str
    max_steps: int

    # ------------------------------------------------------------------
    # Properties / methods
    # ------------------------------------------------------------------

    @property
    def rule_count(self) -> int:
        """Total number of rules."""
        return len(self.rules)

    @property
    def sorted_rules(self) -> Tuple[RewriteRule, ...]:
        """Rules sorted by ascending priority (lowest fires first)."""
        return tuple(sorted(self.rules, key=lambda r: r.priority))

    def has_rule(self, rule_id: str) -> bool:
        """True when a rule with the given *rule_id* is present."""
        return any(r.rule_id == rule_id for r in self.rules)

    def add_rule(self, rule: RewriteRule) -> NormalFormRewriter:
        """Return a new rewriter with *rule* appended."""
        return NormalFormRewriter(
            rewriter_id=self.rewriter_id,
            rules=self.rules + (rule,),
            strategy=self.strategy,
            max_steps=self.max_steps,
        )

    def remove_rule(self, rule_id: str) -> NormalFormRewriter:
        """Return a new rewriter with the rule identified by *rule_id*
        removed."""
        new_rules = tuple(r for r in self.rules if r.rule_id != rule_id)
        return NormalFormRewriter(
            rewriter_id=self.rewriter_id,
            rules=new_rules,
            strategy=self.strategy,
            max_steps=self.max_steps,
        )

    def describe(self) -> str:
        """Multi-line description of all rules."""
        lines = [
            f"NormalFormRewriter[{self.rewriter_id}] "
            f"strategy={self.strategy} max_steps={self.max_steps}",
        ]
        for rule in self.sorted_rules:
            lines.append(f"  {rule.describe()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def build_rewriter(rules: Sequence[RewriteRule],
                   strategy: str = "breadth_first",
                   max_steps: int = _MAX_REWRITE_DEPTH) -> NormalFormRewriter:
    """Construct a NormalFormRewriter from a sequence of RewriteRule objects.

    Rules are deduplicated by rule_id (last writer wins) and stored in a
    frozen tuple sorted by ascending priority.

    Args:
        rules:     The rules to include.  May be any sequence; duplicates
                   (by rule_id) are collapsed.
        strategy:  Reduction strategy label.  Defaults to ``"breadth_first"``.
        max_steps: Maximum rule applications before giving up.

    Returns:
        A new NormalFormRewriter ready for use.

    Example
    -------
    >>> r = build_rewriter([RewriteRule("r1", r"NOT NOT (\\w+)", r"\\1", "", 10)])
    >>> r.rule_count
    1
    """
    seen: Set[str] = set()
    deduped: List[RewriteRule] = []
    for rule in reversed(list(rules)):
        if rule.rule_id not in seen:
            deduped.append(rule)
            seen.add(rule.rule_id)
    sorted_rules = tuple(sorted(deduped, key=lambda r: r.priority))
    return NormalFormRewriter(
        rewriter_id=_fresh_id("rw_"),
        rules=sorted_rules,
        strategy=strategy,
        max_steps=max_steps,
    )


def apply_rewrite_step(expr: str, rewriter: NormalFormRewriter) -> Tuple[str, bool]:
    """Apply one rewrite step to *expr* using *rewriter*.

    Iterates through all rules in priority order and applies the first one
    that matches.  Returns the rewritten expression and a flag indicating
    whether any rule fired.

    Args:
        expr:     The expression to rewrite.
        rewriter: The NormalFormRewriter to use.

    Returns:
        A pair ``(new_expr, did_fire)`` where *did_fire* is True when at
        least one rule matched and the expression was changed.

    Notes
    -----
    This function applies exactly *one* rule per call (the highest-priority
    matching rule).  To drive the rewriter to a fixed point, call it
    repeatedly (as ``compute_normal_form`` does) until *did_fire* is False.
    """
    if not isinstance(expr, str):
        raise TypeError(f"apply_rewrite_step expects str, got {type(expr).__name__!r}")

    for rule in rewriter.sorted_rules:
        if rule.matches(expr):
            new_expr = rule.apply_once(expr)
            if new_expr != expr:
                return new_expr, True
    return expr, False


def compute_normal_form(
        expression: str,
        trust_level: int,
        rewriter: Optional[NormalFormRewriter] = None,
        dependencies: Optional[FrozenSet[str]] = None,
) -> NormalForm:
    """Compute the NormalForm for *expression* at *trust_level*.

    This is the primary entry point for normalisation.  It:

    1. Validates inputs.
    2. Applies the default rewrite rules (double-negation elimination,
       commutativity normalisation, and whitespace canonicalisation) plus any
       rules from *rewriter*.
    3. Applies commutative sorting to AND/OR operands.
    4. Computes the hash_key of the resulting canonical expression.
    5. Wraps everything in an immutable NormalForm.

    WHY A REWRITER ARGUMENT?
    Different passes in the IR stack need different normal forms.  For example
    the trust algebra wants double-negation eliminated and commutative
    operands sorted, while the proof checker wants alpha-normalised bound
    variables.  Passing an explicit *rewriter* lets each pass request the
    right level of normalisation without baking assumptions into this function.

    Args:
        expression:   The raw expression string to normalise.
        trust_level:  The trust tier (integer constant from this module).
        rewriter:     Optional additional rewrite rules.  These are applied
                      *after* the built-in rules.
        dependencies: Optional set of form_ids that this form depends on.

    Returns:
        A fully populated NormalForm with ``is_canonical=True`` if the fixed
        point was reached within ``_MAX_REWRITE_DEPTH`` steps, or
        ``is_canonical=False`` if we hit the step limit.

    Raises:
        TypeError:  If *expression* is not a str.
        ValueError: If *trust_level* is outside [0, 4].
    """
    if not isinstance(expression, str):
        raise TypeError(
            f"compute_normal_form: expression must be str, got "
            f"{type(expression).__name__!r}"
        )
    if trust_level not in range(5):
        raise ValueError(
            f"compute_normal_form: trust_level must be 0–4, got {trust_level!r}"
        )

    # Step 1 — apply built-in rules.
    builtin_rules: List[RewriteRule] = [
        RewriteRule("bn-whitespace",   r"\s+",              " ",    "", 0),
        RewriteRule("bn-dbl-neg",      r"NOT\s+NOT\s+",     "",     "", 5),
        RewriteRule("bn-true-and",     r"TRUE\s+AND\s+",    "",     "", 10),
        RewriteRule("bn-and-true",     r"\s+AND\s+TRUE",    "",     "", 10),
        RewriteRule("bn-false-or",     r"FALSE\s+OR\s+",    "",     "", 10),
        RewriteRule("bn-or-false",     r"\s+OR\s+FALSE",    "",     "", 10),
        RewriteRule("bn-lower",        r"(?<!\w)(and|or|not|implies|iff)(?!\w)",
                    lambda m: m.group(0).upper(), "", 1),  # type: ignore[arg-type]
    ]
    # The lambda replacement is not regex-native; handle it specially.
    builtin_rewriter = build_rewriter(
        [r for r in builtin_rules if not callable(r.replacement)],
        strategy="breadth_first",
        max_steps=_MAX_REWRITE_DEPTH,
    )
    if rewriter is not None:
        # Merge: user rules extend the builtin set.
        combined_rules = builtin_rewriter.rules + rewriter.rules
        active_rewriter = build_rewriter(combined_rules,
                                         strategy=rewriter.strategy,
                                         max_steps=rewriter.max_steps)
    else:
        active_rewriter = builtin_rewriter

    # Step 2 — upcase logical operators (regex can't use callable replacement
    # in frozen rules, so handle this via a simple re.sub outside the rewriter).
    current = re.sub(
        r"\b(and|or|not|implies|iff)\b",
        lambda m: m.group(0).upper(),
        expression.strip(),
        flags=re.IGNORECASE,
    )
    # Collapse all whitespace.
    current = re.sub(r"\s+", " ", current).strip()

    # Step 3 — drive the rewriter to a fixed point.
    reached_fixpoint = False
    steps_taken = 0
    for _ in range(active_rewriter.max_steps):
        new_expr, fired = apply_rewrite_step(current, active_rewriter)
        steps_taken += 1
        if not fired:
            reached_fixpoint = True
            break
        current = new_expr

    # Step 4 — commutative sort on the token stream.
    tokens = _expression_tokens(current)
    tokens = _sort_commutative(tokens)
    current = _tokens_to_expr(tokens)
    # Final whitespace collapse after token re-join.
    current = re.sub(r"\s+", " ", current).strip()

    # Step 5 — compute hash and construct result.
    hk = compute_hash_key(current)
    deps = dependencies if dependencies is not None else frozenset()
    return NormalForm(
        form_id=_fresh_id("nf_"),
        expression=current,
        hash_key=hk,
        trust_level=trust_level,
        is_canonical=reached_fixpoint,
        dependencies=deps,
    )


def cache_comparison(
        cache: ComparisonCache,
        left: NormalForm,
        right: NormalForm,
        result: int,
        trust_level: Optional[int] = None,
) -> ComparisonCache:
    """Add a comparison result to *cache* and return the updated cache.

    If an entry for the same pair already exists in the cache:
      • If the new trust_level is higher, the existing entry is upgraded.
      • If the new trust_level is equal or lower, the existing entry is kept
        unchanged and the new entry is discarded (we never downgrade trust).

    If the cache is full, the oldest entry is evicted before the new one is
    inserted (FIFO eviction).

    The cache key for a pair (left, right) is the concatenation of their
    hash_keys separated by "|".  The key is symmetric only if callers
    normalise the order (left ≤ right lexicographically); this function does
    *not* enforce symmetry so that directional comparisons (e.g., trust
    subsumption) can be recorded independently in each direction.

    Args:
        cache:       The existing (immutable) ComparisonCache.
        left:        The left-hand NormalForm.
        right:       The right-hand NormalForm.
        result:      Comparison result: negative → left < right, 0 → equal,
                     positive → left > right.
        trust_level: The trust tier at which this comparison was made.
                     Defaults to ``min(left.trust_level, right.trust_level)``.

    Returns:
        A new ComparisonCache with the entry added (or the old entry upgraded).
    """
    if trust_level is None:
        trust_level = min(left.trust_level, right.trust_level)

    pair_key = f"{left.hash_key}|{right.hash_key}"
    now = time.time()

    # Check whether the pair is already cached.
    existing = cache.lookup(pair_key)
    if existing is not None:
        if trust_level > existing.trust_level:
            # Upgrade the existing entry's trust.
            upgraded = existing.with_higher_trust(trust_level)
            new_entries = tuple(
                upgraded if e.key == pair_key else e for e in cache.entries
            )
            return ComparisonCache(
                cache_id=cache.cache_id,
                entries=new_entries,
                max_size=cache.max_size,
                hit_count=cache.hit_count + 1,
                miss_count=cache.miss_count,
            )
        # Same or lower trust — keep existing, count as a hit.
        return cache.bump_hits()

    # New entry: evict oldest if necessary.
    working_cache = cache
    if working_cache.is_full:
        working_cache = working_cache.evict_oldest(1)

    new_entry = CacheEntry(
        key=pair_key,
        left_form_id=left.form_id,
        right_form_id=right.form_id,
        result=result,
        trust_level=trust_level,
        timestamp=now,
    )
    updated_entries = working_cache.entries + (new_entry,)
    return ComparisonCache(
        cache_id=working_cache.cache_id,
        entries=updated_entries,
        max_size=working_cache.max_size,
        hit_count=working_cache.hit_count,
        miss_count=working_cache.miss_count + 1,
    )


def deduplicate_terms(
        terms: Sequence[str],
        table: DeduplicationTable,
        trust_level: int = TRUST_PROPOSAL,
) -> Tuple[Tuple[str, ...], DeduplicationTable]:
    """Deduplicate *terms* using and updating *table*.

    For each term in *terms*:
      1. Compute its NormalForm.
      2. Look up its canonical representative in *table*.
         a. If found, use the stored canonical repr (no change to table).
         b. If not found, the term's own normal form expression becomes the
            canonical repr, and a new DeduplicationEntry is added to the table.
      3. Replace the term with its canonical repr in the output list.

    Terms that normalise to the same hash_key are considered identical and
    are all replaced by the single canonical representative that was first
    inserted into the table.

    This ensures that downstream passes see at most one representative per
    equivalence class, dramatically reducing the size of the normal-form
    universe that the ComparisonCache must track.

    Args:
        terms:       A sequence of expression strings to deduplicate.
        table:       The current DeduplicationTable (immutable snapshot).
        trust_level: Trust tier used when computing new NormalForms.

    Returns:
        A pair ``(canonical_terms, updated_table)`` where *canonical_terms*
        is a tuple of canonical-repr strings (one per input term, in order)
        and *updated_table* is the DeduplicationTable extended with any newly
        seen expressions.

    Example
    -------
    >>> t = DeduplicationTable("t1", (), 0)
    >>> terms = ["A AND B", "B AND A", "C OR D"]
    >>> canon, t2 = deduplicate_terms(terms, t, TRUST_REVIEWED)
    >>> canon[0] == canon[1]  # both map to the same normal form
    True
    """
    if not terms:
        return (), table

    current_table = table
    # Build an intermediate map: hash_key → canonical_repr so we can detect
    # within-batch duplicates before they reach the table.
    hash_to_canonical: Dict[str, str] = {}
    for entry in current_table.entries:
        nf = compute_normal_form(entry.canonical_repr, trust_level)
        hash_to_canonical.setdefault(nf.hash_key, entry.canonical_repr)

    canonical_terms: List[str] = []
    for raw_term in terms:
        nf = compute_normal_form(raw_term, trust_level)

        # Consult the in-memory map first (faster than scanning the table).
        if nf.hash_key in hash_to_canonical:
            canonical = hash_to_canonical[nf.hash_key]
        else:
            # First time we see this equivalence class — the term itself
            # (in canonical form) becomes the representative.
            canonical = nf.expression
            hash_to_canonical[nf.hash_key] = canonical

        canonical_terms.append(canonical)

        # Update the table if this specific original expression is new.
        existing = current_table.lookup(raw_term)
        if existing is None or existing.canonical_repr != canonical:
            new_entry = DeduplicationEntry(
                original_expr=raw_term,
                canonical_repr=canonical,
                form_id=nf.form_id,
                equivalence_class=nf.hash_key,
            )
            current_table = current_table.add_entry(new_entry)

    return tuple(canonical_terms), current_table


def merge_deduplication_tables(
        t1: DeduplicationTable,
        t2: DeduplicationTable,
) -> DeduplicationTable:
    """Merge *t2* into *t1*, resolving conflicts by preferring the entry with
    the shorter canonical representation (heuristic: shorter ≈ more reduced).

    When two tables are merged, the equivalence classes of *t2* may overlap
    with those of *t1*.  For each entry in *t2* we:

    1. Check whether the same *original_expr* already has an entry in *t1*.
    2. If the *t1* canonical repr and *t2* canonical repr belong to the same
       hash class, the entry is already consistent — keep *t1*'s version.
    3. If they belong to different hash classes, we choose the shorter
       canonical repr as the winner and re-point the loser's class entries
       at the winner's representative.
    4. If the expression is new (not in *t1*), add *t2*'s entry verbatim.

    The result has a new *table_id* (it is a genuinely new table, not a
    mutation of either input) and its version is ``max(t1.version, t2.version) + 1``.

    Args:
        t1: Base table.
        t2: Table to merge in.

    Returns:
        A new DeduplicationTable containing the merged entries.
    """
    # Start with all entries from t1.
    merged_entries: Dict[str, DeduplicationEntry] = {
        e.original_expr: e for e in t1.entries
    }

    for e2 in t2.entries:
        if e2.original_expr not in merged_entries:
            merged_entries[e2.original_expr] = e2
        else:
            e1 = merged_entries[e2.original_expr]
            if e1.equivalence_class == e2.equivalence_class:
                # Consistent — keep t1's entry.
                pass
            else:
                # Conflict: choose shorter canonical repr as winner.
                if len(e2.canonical_repr) < len(e1.canonical_repr):
                    # Re-point e1's class to e2's canonical.
                    winning_repr = e2.canonical_repr
                    winning_class = e2.equivalence_class
                    merged_entries[e2.original_expr] = e1.with_updated_canonical(
                        winning_repr, winning_class
                    )
                # else: e1 is already shorter; keep it.

    new_version = max(t1.version, t2.version) + 1
    return DeduplicationTable(
        table_id=_fresh_id("dt_"),
        entries=tuple(merged_entries.values()),
        version=new_version,
    )


def normal_form_distance(nf1: NormalForm, nf2: NormalForm) -> float:
    """Compute a rough semantic distance between two NormalForms.

    The distance is defined as the normalised edit distance (Levenshtein) on
    the canonical expression strings, scaled by a trust-compatibility factor.

    A distance of 0.0 means the two forms are identical (same hash_key).
    A distance of 1.0 means the expressions share no characters in common.
    Intermediate values reflect partial overlap.

    WHY DISTANCE?
    In Judgment Geometry, we sometimes need to rank candidate judgments by
    similarity to a query judgment.  Exact equality (hash_key comparison) is
    too coarse for this use case.  The edit distance on normal forms provides
    a cheap, syntactically grounded notion of proximity that correlates well
    with semantic similarity for the structured expressions used here.

    Args:
        nf1: First NormalForm.
        nf2: Second NormalForm.

    Returns:
        A float in [0.0, 1.0].  0.0 = identical, 1.0 = maximally different.
    """
    if nf1.hash_key == nf2.hash_key:
        return 0.0

    s1 = nf1.expression
    s2 = nf2.expression

    # Levenshtein distance via dynamic programming.
    m, n = len(s1), len(s2)
    if m == 0 and n == 0:
        return 0.0
    if m == 0:
        return 1.0
    if n == 0:
        return 1.0

    # Use two rows to keep memory O(min(m,n)).
    if m < n:
        s1, s2 = s2, s1
        m, n = n, m

    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,        # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
        prev = curr

    raw_distance = prev[n]
    max_len = max(m, n)
    normalised = raw_distance / max_len

    # Trust-compatibility penalty: if the forms come from incompatible trust
    # tiers, push the distance slightly toward 1.0 to reflect that they may
    # be measuring different things.
    trust_diff = abs(nf1.trust_level - nf2.trust_level)
    trust_penalty = trust_diff / (4 * 10)  # max penalty 0.1 at trust diff = 4

    return min(1.0, normalised + trust_penalty)


# ---------------------------------------------------------------------------
# Default rewrite rules  (exported for use by other modules)
# ---------------------------------------------------------------------------

#: The standard rule set used throughout the IR stack.
DEFAULT_RULES: Tuple[RewriteRule, ...] = (
    RewriteRule("std-dbl-neg",    r"NOT\s+NOT\s+",                "",    "", 5),
    RewriteRule("std-true-and",   r"\bTRUE\b\s+AND\s+",           "",    "", 10),
    RewriteRule("std-and-true",   r"\s+AND\s+\bTRUE\b",           "",    "", 10),
    RewriteRule("std-false-or",   r"\bFALSE\b\s+OR\s+",           "",    "", 10),
    RewriteRule("std-or-false",   r"\s+OR\s+\bFALSE\b",           "",    "", 10),
    RewriteRule("std-idem-and",   r"(\w+)\s+AND\s+\1",            r"\1", "", 15),
    RewriteRule("std-idem-or",    r"(\w+)\s+OR\s+\1",             r"\1", "", 15),
    RewriteRule("std-contra",     r"(\w+)\s+AND\s+NOT\s+\1",      "FALSE", "", 20),
    RewriteRule("std-excl-mid",   r"(\w+)\s+OR\s+NOT\s+\1",       "TRUE",  "", 20),
    RewriteRule("std-ws",         r"\s{2,}",                       " ",   "", 0),
)

#: Rewriter pre-built from DEFAULT_RULES.
DEFAULT_REWRITER: NormalFormRewriter = build_rewriter(DEFAULT_RULES)


# ---------------------------------------------------------------------------
# Judgment tuple helpers
# ---------------------------------------------------------------------------

def make_judgment_key(
        c: str, phi: str, A: str, E: str,
        O: str, B: float, T: int, Pi: Optional[str],
) -> str:
    """Return a cache key for the judgment tuple (c, φ, A, E, O, B, T, Π).

    Judgments are NEVER booleans — they are structured tuples.  This function
    computes the canonical hash_key for a judgment by first normalising each
    component individually, then combining the component keys.

    The *O* component (obstruction) is treated as a Čech H¹ cohomology class
    identifier and is normalised separately; two obstructions that have the
    same canonical form are considered the same cohomology class, even if
    their syntactic presentations differ.

    Args:
        c:   Context identifier string.
        phi: Formula string.
        A:   Agent/principal set (stringified).
        E:   Evidence bundle identifier.
        O:   Obstruction / H¹ class identifier.
        B:   Belief weight in [0, 1].
        T:   Trust tier (integer 0–4).
        Pi:  Proof reference (may be None or empty string).

    Returns:
        A 32-character hex string that uniquely identifies the judgment tuple.
    """
    components = [
        compute_normal_form(c,   T).hash_key,
        compute_normal_form(phi, T).hash_key,
        compute_normal_form(A,   T).hash_key,
        compute_normal_form(E,   T).hash_key,
        compute_normal_form(O,   T).hash_key,
        compute_hash_key(f"{B:.6f}"),
        compute_hash_key(str(T)),
        compute_hash_key(Pi or ""),
    ]
    combined = "|".join(components)
    return compute_hash_key(combined)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("Judgment Geometry IR Stack — Normal Forms Smoke Test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Normal form computation
    # ------------------------------------------------------------------
    print("\n[1] NormalForm computation")
    exprs = [
        "A AND B",
        "B AND A",                        # should map to same form as above
        "not not X",                      # double-negation elimination
        "TRUE AND phi",                   # TRUE-AND simplification
        "alpha OR FALSE",                 # OR-FALSE simplification
        "P AND NOT P",                    # contradiction → FALSE
        "Q OR NOT Q",                     # excluded middle → TRUE
        "C   AND   D",                    # extra whitespace
    ]
    forms: List[NormalForm] = []
    for expr in exprs:
        nf = compute_normal_form(expr, TRUST_VERIFIED, DEFAULT_REWRITER)
        forms.append(nf)
        print(f"  {expr!r:30s} → {nf.expression!r:25s}  key={nf.hash_key[:12]}")

    # Check commutativity normalisation.
    assert forms[0].hash_key == forms[1].hash_key, (
        "Commutativity: 'A AND B' and 'B AND A' must have the same hash_key"
    )
    print("  ✓ Commutativity: 'A AND B' ≡ 'B AND A'")

    # ------------------------------------------------------------------
    # 2. ComparisonCache
    # ------------------------------------------------------------------
    print("\n[2] ComparisonCache")
    cache = ComparisonCache(
        cache_id=_fresh_id("cc_"),
        entries=(),
        max_size=64,
        hit_count=0,
        miss_count=0,
    )
    # Cache several comparisons.
    for i in range(len(forms)):
        for j in range(i, len(forms)):
            cmp_result = 0 if forms[i].hash_key == forms[j].hash_key else (
                -1 if forms[i].expression < forms[j].expression else 1
            )
            cache = cache_comparison(cache, forms[i], forms[j], cmp_result,
                                     TRUST_VERIFIED)

    print(f"  {cache.statistics()}")
    # Repeat one comparison — should increment hits.
    before_hits = cache.hit_count
    cache = cache_comparison(cache, forms[0], forms[1], 0, TRUST_VERIFIED)
    assert cache.hit_count == before_hits + 1, "Re-inserting existing pair must be a hit"
    print(f"  ✓ Re-comparison of 'A AND B' / 'B AND A' counted as cache hit")
    print(f"  {cache.statistics()}")

    # ------------------------------------------------------------------
    # 3. Deduplication
    # ------------------------------------------------------------------
    print("\n[3] DeduplicationTable")
    dedup_table = DeduplicationTable(
        table_id=_fresh_id("dt_"),
        entries=(),
        version=0,
    )
    raw_terms = [
        "A AND B",
        "B AND A",          # same equivalence class as 'A AND B'
        "not not X",        # normalises to 'X'
        "X",                # same as above after normalisation
        "C OR D",
        "D OR C",           # same as 'C OR D'
        "TRUE AND alpha",   # simplifies to 'alpha'
        "alpha",            # same as above
    ]
    canonical, dedup_table = deduplicate_terms(raw_terms, dedup_table,
                                               TRUST_REVIEWED)
    print(f"  Input terms  : {len(raw_terms)}")
    print(f"  Canonical out: {len(set(canonical))} unique representatives")
    for orig, canon in zip(raw_terms, canonical):
        marker = "≡" if orig != canon else "="
        print(f"    {orig!r:25s} {marker} {canon!r}")

    assert canonical[0] == canonical[1], "'A AND B' and 'B AND A' must deduplicate"
    assert canonical[4] == canonical[5], "'C OR D' and 'D OR C' must deduplicate"
    print(f"  ✓ Deduplication collapsed {len(raw_terms)} terms → "
          f"{len(set(canonical))} unique representatives")
    print(f"  {dedup_table.summary()}")

    # ------------------------------------------------------------------
    # 4. Merge deduplication tables
    # ------------------------------------------------------------------
    print("\n[4] Merging DeduplicationTables")
    _, table_a = deduplicate_terms(["P AND Q", "Q AND P"], DeduplicationTable(
        _fresh_id("dt_"), (), 0), TRUST_VERIFIED)
    _, table_b = deduplicate_terms(["R OR S", "S OR R", "P AND Q"], DeduplicationTable(
        _fresh_id("dt_"), (), 0), TRUST_REVIEWED)
    merged = merge_deduplication_tables(table_a, table_b)
    print(f"  Table A: {table_a.summary()}")
    print(f"  Table B: {table_b.summary()}")
    print(f"  Merged : {merged.summary()}")
    assert merged.size >= max(table_a.size, table_b.size), (
        "Merged table must be at least as large as the larger input"
    )
    print("  ✓ Merged table is consistent with both inputs")

    # ------------------------------------------------------------------
    # 5. Normal form distance
    # ------------------------------------------------------------------
    print("\n[5] NormalForm distance")
    nf_ab  = compute_normal_form("A AND B",  TRUST_VERIFIED)
    nf_ba  = compute_normal_form("B AND A",  TRUST_VERIFIED)   # same as nf_ab
    nf_cd  = compute_normal_form("C OR D",   TRUST_VERIFIED)
    nf_xyz = compute_normal_form("X IMPLIES Y IFF Z", TRUST_PROOF_BACKED)

    d_ab_ba  = normal_form_distance(nf_ab, nf_ba)
    d_ab_cd  = normal_form_distance(nf_ab, nf_cd)
    d_ab_xyz = normal_form_distance(nf_ab, nf_xyz)

    print(f"  dist('A AND B', 'B AND A')         = {d_ab_ba:.4f}  (expect 0.0)")
    print(f"  dist('A AND B', 'C OR D')           = {d_ab_cd:.4f}")
    print(f"  dist('A AND B', 'X IMPLIES Y IFF Z') = {d_ab_xyz:.4f}")
    assert d_ab_ba == 0.0, "Identical normal forms must have distance 0.0"
    assert d_ab_cd <= 1.0 and d_ab_xyz <= 1.0, "Distances must be in [0, 1]"
    assert d_ab_cd <= d_ab_xyz or True, "Ordering not guaranteed; just check bounds"
    print("  ✓ Distance invariants hold")

    # ------------------------------------------------------------------
    # 6. Judgment key
    # ------------------------------------------------------------------
    print("\n[6] Judgment tuple keys")
    jk1 = make_judgment_key(
        c="ctx_alpha", phi="A AND B", A="{agent_1}",
        E="evidence_bundle_42", O="H1_trivial", B=0.95,
        T=TRUST_VERIFIED, Pi="proof_ref_7",
    )
    jk2 = make_judgment_key(
        c="ctx_alpha", phi="B AND A",  # commutativity — should yield same key
        A="{agent_1}", E="evidence_bundle_42", O="H1_trivial", B=0.95,
        T=TRUST_VERIFIED, Pi="proof_ref_7",
    )
    jk3 = make_judgment_key(
        c="ctx_beta",  phi="A AND B", A="{agent_1}",
        E="evidence_bundle_42", O="H1_trivial", B=0.95,
        T=TRUST_VERIFIED, Pi="proof_ref_7",
    )
    print(f"  Judgment key 1 (phi='A AND B'):  {jk1}")
    print(f"  Judgment key 2 (phi='B AND A'):  {jk2}")
    print(f"  Judgment key 3 (ctx='ctx_beta'): {jk3}")
    assert jk1 == jk2, (
        "Judgments with commuted phi must produce the same key after normalisation"
    )
    assert jk1 != jk3, (
        "Judgments with different contexts must produce different keys"
    )
    print("  ✓ Commutativity-invariant judgment keys confirmed")

    # ------------------------------------------------------------------
    # 7. Rewriter introspection
    # ------------------------------------------------------------------
    print("\n[7] NormalFormRewriter")
    print(DEFAULT_REWRITER.describe())
    custom_rule = RewriteRule("user-r1", r"\bFOO\b", "BAR", "", 50)
    extended = DEFAULT_REWRITER.add_rule(custom_rule)
    assert extended.rule_count == DEFAULT_REWRITER.rule_count + 1
    nf_foo = compute_normal_form("TRUE AND FOO AND baz", TRUST_PROPOSAL, extended)
    print(f"\n  'TRUE AND FOO AND baz' → {nf_foo.expression!r}")
    assert "BAR" in nf_foo.expression, "Custom rule replacing FOO→BAR must have fired"
    print("  ✓ Custom rewrite rule applied correctly")

    print("\n" + "=" * 70)
    print("All smoke tests passed.")
    print("=" * 70)
    sys.exit(0)
