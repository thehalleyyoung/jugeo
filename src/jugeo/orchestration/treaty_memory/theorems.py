from __future__ import annotations

"""
theorems.py — Theorem statements and verifiable invariants for treaty_memory.

Reference: theory2.tex Ch48 – "Treaty synthesis, negotiation memory, and archival semantics"

# copilot: This module encodes formal invariants that the treaty_memory subsystem must
# satisfy at all observable states.  Every theorem is self-contained: it carries its own
# check(), counterexample(), and verify() methods so CI pipelines and runtime monitors
# can invoke them uniformly.  The FalsificationSuite performs randomised adversarial
# testing (property-based, poor-man's QuickCheck style) without depending on hypothesis.

Design
------
* Theorems are numbered after the chapter sections in theory2.tex (48.1 – 48.5).
* TheoremResult / FalsificationReport are frozen dataclasses — safe to cache and hash.
* TreatyMemoryTheoremSchema is the central registry; import it and call verify_all().
* FalsificationSuite seeds a PRNG for reproducible adversarial state generation.
* All jugeo.* imports are guarded so the module is importable in isolation.
"""

import logging
import math
import random
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ─── Optional jugeo imports ─────────────────────────────────────────────────

try:
    from jugeo.orchestration.treaty_memory.core import TreatyMemory  # type: ignore
except ImportError:
    TreatyMemory: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.law import LawCandidate  # type: ignore
except ImportError:
    LawCandidate: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.archive import SemanticArchive  # type: ignore
except ImportError:
    SemanticArchive: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.capital import CapitalLedger  # type: ignore
except ImportError:
    CapitalLedger: Any = None  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.interface import InterfaceRegistry  # type: ignore
except ImportError:
    InterfaceRegistry: Any = None  # type: ignore[assignment,misc]

# ─── Module metadata ─────────────────────────────────────────────────────────

__all__ = [
    # dataclasses
    "TheoremResult",
    "FalsificationReport",
    # registry
    "TreatyMemoryTheoremSchema",
    # theorems
    "Theorem48_1_MemoryMonotonicity",
    "Theorem48_2_LawStability",
    "Theorem48_3_ArchiveCompression",
    "Theorem48_4_CapitalNonNegativity",
    "Theorem48_5_InterfaceDiscoveryCompleteness",
    # falsification
    "FalsificationSuite",
    # helpers
    "make_theorem_state",
    "assert_theorem",
    "batch_verify",
]

log = logging.getLogger(__name__)

# ─── Internal constants ───────────────────────────────────────────────────────

# Floating-point tolerance used when comparing real-valued invariants.
_FLOAT_EPS: float = 1e-9

# Maximum number of falsification attempts before declaring a theorem "not falsified".
_DEFAULT_FALSIFICATION_ATTEMPTS: int = 1_000

# Maximum episode count used when generating adversarial states.
_MAX_ADVERSARIAL_EPISODES: int = 500

# Minimum legal episode count; a memory with zero episodes is considered empty (valid).
_MIN_EPISODE_COUNT: int = 0

# Confidence is always in [0, 1].  Values outside this range are bugs.
_CONFIDENCE_LO: float = 0.0
_CONFIDENCE_HI: float = 1.0

# Capital balances must be ≥ this floor.  The theory requires non-negativity.
_CAPITAL_FLOOR: float = 0.0

# Compression ratio threshold: archive may be at most this fraction of raw history.
# The constant is intentionally loose (1.0 = archive ≤ raw) to avoid false positives.
_ARCHIVE_COMPRESSION_MAX_RATIO: float = 1.0

# State dict keys used by every theorem.  Consistent naming prevents typos.
_KEY_EPISODES_BEFORE = "episodes_before"
_KEY_EPISODES_AFTER = "episodes_after"
_KEY_LAW_SUPPORT = "law_support"
_KEY_LAW_REFUTATION = "law_refutation"
_KEY_LAW_CONFIDENCE = "law_confidence"
_KEY_RAW_HISTORY_SIZE = "raw_history_size"
_KEY_ARCHIVE_SIZE = "archive_size"
_KEY_CAPITAL_BALANCES = "capital_balances"
_KEY_CHECKED_INTERFACES = "checked_interfaces"
_KEY_DISCOVERED_INTERFACES = "discovered_interfaces"

# Logging prefix for all theorem activity so it is easy to grep in production logs.
_LOG_PREFIX = "treaty_memory.theorems"


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TheoremResult:
    """Immutable result record produced by a single theorem verification run.

    Attributes
    ----------
    theorem_id:
        Stable UUID-based identifier for this particular check invocation.
    name:
        Human-readable name of the theorem (e.g. ``"Theorem48_1_MemoryMonotonicity"``).
    holds:
        ``True`` iff the theorem was satisfied by the provided state.
    witness:
        If *holds* is ``False``, a minimal counterexample dict sufficient to reproduce the
        violation; ``None`` otherwise.
    checked_at:
        POSIX timestamp (``time.time()``) at the moment the check was completed.
    details:
        Free-text description of the outcome, suitable for logging or display.
    """

    theorem_id: str
    name: str
    holds: bool
    witness: dict | None
    checked_at: float
    details: str

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """Return a one-line summary suitable for log output."""
        status = "HOLDS" if self.holds else "VIOLATED"
        return f"[{_LOG_PREFIX}] {self.name} {status} @ {self.checked_at:.3f}"

    def as_dict(self) -> dict:
        """Serialise to a plain dict (JSON-safe values only)."""
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "holds": self.holds,
            "witness": self.witness,
            "checked_at": self.checked_at,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class FalsificationReport:
    """Immutable report produced after a falsification campaign against a theorem.

    Attributes
    ----------
    report_id:
        Unique identifier for this falsification run.
    theorem_name:
        Name of the theorem that was attacked.
    n_attempts:
        Number of adversarial states that were tried.
    falsified:
        ``True`` iff at least one adversarial state caused the theorem to fail.
    counterexample:
        The first state that falsified the theorem, or ``None``.
    run_at:
        POSIX timestamp when the campaign finished.
    """

    report_id: str
    theorem_name: str
    n_attempts: int
    falsified: bool
    counterexample: dict | None
    run_at: float

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """Return a one-line summary suitable for CI output."""
        outcome = "FALSIFIED" if self.falsified else "NOT FALSIFIED"
        return (
            f"[{_LOG_PREFIX}] FalsificationReport {self.theorem_name} "
            f"{outcome} after {self.n_attempts} attempts @ {self.run_at:.3f}"
        )

    def as_dict(self) -> dict:
        """Serialise to a plain dict."""
        return {
            "report_id": self.report_id,
            "theorem_name": self.theorem_name,
            "n_attempts": self.n_attempts,
            "falsified": self.falsified,
            "counterexample": self.counterexample,
            "run_at": self.run_at,
        }


# ─── Helper: state factory ────────────────────────────────────────────────────


def make_theorem_state(
    *,
    episodes_before: int = 0,
    episodes_after: int = 0,
    law_support: float = 1.0,
    law_refutation: float = 0.0,
    law_confidence: float | None = None,
    raw_history_size: int = 100,
    archive_size: int = 80,
    capital_balances: list[float] | None = None,
    checked_interfaces: set[str] | None = None,
    discovered_interfaces: set[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Construct a canonical theorem-state dict with sensible defaults.

    This function is the single source of truth for the shape of the *state*
    dict consumed by all theorem ``check()`` / ``verify()`` methods.  Tests and
    the :class:`FalsificationSuite` both use it so that the schema stays in sync.

    Parameters
    ----------
    episodes_before:
        Episode count before a memory-update operation.
    episodes_after:
        Episode count after a memory-update operation.
    law_support:
        Number of observations supporting the law candidate (non-negative real).
    law_refutation:
        Number of observations refuting the law candidate (non-negative real).
    law_confidence:
        Explicit confidence override.  If ``None`` it is computed from support /
        (support + refutation), defaulting to ``1.0`` if the denominator is zero.
    raw_history_size:
        Byte- or item-count of the uncompressed episode history.
    archive_size:
        Byte- or item-count of the semantic archive derived from history.
    capital_balances:
        List of per-agent semantic capital balances.  All must be ≥ 0.
    checked_interfaces:
        Set of interface identifiers that were explicitly checked.
    discovered_interfaces:
        Set of interface identifiers that were discovered during treaty synthesis.
    extra:
        Arbitrary additional keys merged into the returned dict.

    Returns
    -------
    dict
        A state dict with all canonical keys populated.
    """
    if law_confidence is None:
        denom = law_support + law_refutation
        law_confidence = (law_support / denom) if denom > _FLOAT_EPS else 1.0

    state: dict = {
        _KEY_EPISODES_BEFORE: episodes_before,
        _KEY_EPISODES_AFTER: episodes_after,
        _KEY_LAW_SUPPORT: law_support,
        _KEY_LAW_REFUTATION: law_refutation,
        _KEY_LAW_CONFIDENCE: law_confidence,
        _KEY_RAW_HISTORY_SIZE: raw_history_size,
        _KEY_ARCHIVE_SIZE: archive_size,
        _KEY_CAPITAL_BALANCES: list(capital_balances) if capital_balances else [1.0],
        _KEY_CHECKED_INTERFACES: set(checked_interfaces) if checked_interfaces else set(),
        _KEY_DISCOVERED_INTERFACES: set(discovered_interfaces) if discovered_interfaces else set(),
    }
    if extra:
        state.update(extra)
    return state


# ─── Helper: assertion wrapper ────────────────────────────────────────────────


def assert_theorem(theorem: Any, state: dict) -> None:
    """Assert that *theorem* holds on *state*, raising ``AssertionError`` otherwise.

    Intended for use in unit-test ``assert`` chains.  The error message includes
    the counterexample dict so failures are immediately actionable.

    Parameters
    ----------
    theorem:
        Any theorem object exposing a ``verify(state) -> TheoremResult`` method.
    state:
        The theorem-state dict to verify.

    Raises
    ------
    AssertionError
        When the theorem is violated.  The message contains the counterexample.
    """
    result: TheoremResult = theorem.verify(state)
    if not result.holds:
        raise AssertionError(
            f"Theorem {result.name} violated.\n"
            f"Details: {result.details}\n"
            f"Witness: {result.witness}"
        )


# ─── Helper: batch verifier ───────────────────────────────────────────────────


def batch_verify(theorems: list[Any], state: dict) -> list[TheoremResult]:
    """Run every theorem in *theorems* against *state* and return all results.

    Parameters
    ----------
    theorems:
        Iterable of theorem objects, each with a ``verify(state) -> TheoremResult``
        method.
    state:
        The shared theorem-state dict.

    Returns
    -------
    list[TheoremResult]
        One result per theorem, in the same order as *theorems*.
    """
    results: list[TheoremResult] = []
    for thm in theorems:
        result = thm.verify(state)
        log.debug(result.summary())
        results.append(result)
    return results


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _new_id() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def _now() -> float:
    """Return the current POSIX timestamp."""
    return time.time()


def _confidence_from_counts(support: float, refutation: float) -> float:
    """Compute Bayesian-style confidence from support and refutation counts.

    ``confidence = support / (support + refutation)``

    Edge cases:
    * Both zero → ``1.0`` (vacuous truth; no evidence of refutation).
    * Negative values are clamped to zero before computation.
    """
    s = max(support, 0.0)
    r = max(refutation, 0.0)
    denom = s + r
    return (s / denom) if denom > _FLOAT_EPS else 1.0


def _is_valid_confidence(value: float) -> bool:
    """Return ``True`` iff *value* is a valid confidence in ``[0, 1]``."""
    return _CONFIDENCE_LO - _FLOAT_EPS <= value <= _CONFIDENCE_HI + _FLOAT_EPS


def _summarise_balances(balances: list[float]) -> str:
    """Return a compact statistical summary of a list of capital balances."""
    if not balances:
        return "[] (empty)"
    mn = min(balances)
    mx = max(balances)
    mean = statistics.mean(balances)
    return f"n={len(balances)} min={mn:.4f} max={mx:.4f} mean={mean:.4f}"


def _set_is_superset(superset: set, subset: set) -> bool:
    """Return ``True`` iff *superset* ⊇ *subset*."""
    return subset.issubset(superset)


# ─── Theorem 48.1 – Memory Monotonicity ──────────────────────────────────────


class Theorem48_1_MemoryMonotonicity:
    """**Theorem 48.1 – Memory Monotonicity** (theory2.tex §48.1)

    *Statement*: For any treaty memory update operation, the number of stored
    episodes after the operation is greater than or equal to the number before.
    Formally, if ``M`` is the memory before and ``M'`` after:

        ``|M'.episodes| ≥ |M.episodes|``

    This captures the archival principle that episodes are **never silently
    discarded** — they may be compressed into the semantic archive but the raw
    count is a non-decreasing quantity across the lifetime of the memory.

    Scope
    -----
    * Applies only to normal update paths.  Explicit ``reset()`` operations are
      outside this theorem's scope and must be modelled separately.
    """

    NAME: str = "Theorem48_1_MemoryMonotonicity"
    DESCRIPTION: str = (
        "Memory grows monotonically: episodes_after >= episodes_before "
        "for any normal update operation."
    )

    # ------------------------------------------------------------------
    def check(self, state: dict) -> bool:
        """Return ``True`` iff episodes did not decrease.

        Parameters
        ----------
        state:
            Must contain ``"episodes_before"`` and ``"episodes_after"`` keys.
        """
        before: int = state.get(_KEY_EPISODES_BEFORE, 0)
        after: int = state.get(_KEY_EPISODES_AFTER, 0)
        return after >= before

    def counterexample(self, state: dict) -> dict | None:
        """Return a minimal counterexample dict if the theorem is violated, else ``None``."""
        if self.check(state):
            return None
        before = state.get(_KEY_EPISODES_BEFORE, 0)
        after = state.get(_KEY_EPISODES_AFTER, 0)
        return {
            "description": "episodes decreased after update",
            _KEY_EPISODES_BEFORE: before,
            _KEY_EPISODES_AFTER: after,
            "deficit": before - after,
        }

    def verify(self, state: dict) -> TheoremResult:
        """Run the full verification and return a :class:`TheoremResult`.

        Parameters
        ----------
        state:
            Theorem-state dict (use :func:`make_theorem_state` to construct one).
        """
        holds = self.check(state)
        witness = None if holds else self.counterexample(state)
        before = state.get(_KEY_EPISODES_BEFORE, 0)
        after = state.get(_KEY_EPISODES_AFTER, 0)
        details = (
            f"episodes_before={before}, episodes_after={after} → "
            + ("monotone ✓" if holds else f"DECREASED by {before - after} ✗")
        )
        return TheoremResult(
            theorem_id=_new_id(),
            name=self.NAME,
            holds=holds,
            witness=witness,
            checked_at=_now(),
            details=details,
        )


# ─── Theorem 48.2 – Law Stability ────────────────────────────────────────────


class Theorem48_2_LawStability:
    """**Theorem 48.2 – Law Stability** (theory2.tex §48.2)

    *Statement*: A law candidate's stored confidence value must equal the ratio
    ``support / (support + refutation)`` (clamped to ``[0, 1]``).  Specifically:

        ``|confidence - support/(support+refutation)| ≤ ε``

    where ``ε = 1e-9``.

    Rationale
    ---------
    The confidence score drives treaty negotiation decisions.  An inconsistency
    between the stored value and the derivable value from raw counts indicates a
    bookkeeping bug that could corrupt policy choices downstream.

    Scope
    -----
    Applies to every law candidate that has been persisted to the memory store.
    Newly-created candidates with zero evidence are exempt (confidence defaults
    to ``1.0`` by convention — vacuous truth).
    """

    NAME: str = "Theorem48_2_LawStability"
    DESCRIPTION: str = (
        "A law candidate's confidence equals support/(support+refutation) "
        "within floating-point tolerance."
    )

    # ------------------------------------------------------------------
    def check(self, state: dict) -> bool:
        """Return ``True`` iff the stored confidence matches the computed value."""
        support: float = state.get(_KEY_LAW_SUPPORT, 1.0)
        refutation: float = state.get(_KEY_LAW_REFUTATION, 0.0)
        stored: float = state.get(_KEY_LAW_CONFIDENCE, 1.0)
        expected = _confidence_from_counts(support, refutation)
        return abs(stored - expected) <= _FLOAT_EPS

    def counterexample(self, state: dict) -> dict | None:
        """Return a minimal counterexample if the theorem is violated."""
        if self.check(state):
            return None
        support = state.get(_KEY_LAW_SUPPORT, 1.0)
        refutation = state.get(_KEY_LAW_REFUTATION, 0.0)
        stored = state.get(_KEY_LAW_CONFIDENCE, 1.0)
        expected = _confidence_from_counts(support, refutation)
        return {
            "description": "stored confidence diverges from recomputed value",
            _KEY_LAW_SUPPORT: support,
            _KEY_LAW_REFUTATION: refutation,
            "stored_confidence": stored,
            "expected_confidence": expected,
            "delta": abs(stored - expected),
        }

    def verify(self, state: dict) -> TheoremResult:
        """Run the full verification and return a :class:`TheoremResult`."""
        holds = self.check(state)
        witness = None if holds else self.counterexample(state)
        support = state.get(_KEY_LAW_SUPPORT, 1.0)
        refutation = state.get(_KEY_LAW_REFUTATION, 0.0)
        stored = state.get(_KEY_LAW_CONFIDENCE, 1.0)
        expected = _confidence_from_counts(support, refutation)
        details = (
            f"support={support}, refutation={refutation}, "
            f"stored={stored:.9f}, expected={expected:.9f}, "
            f"delta={abs(stored - expected):.2e} → "
            + ("stable ✓" if holds else "DIVERGED ✗")
        )
        return TheoremResult(
            theorem_id=_new_id(),
            name=self.NAME,
            holds=holds,
            witness=witness,
            checked_at=_now(),
            details=details,
        )


# ─── Theorem 48.3 – Archive Compression ──────────────────────────────────────


class Theorem48_3_ArchiveCompression:
    """**Theorem 48.3 – Archive Compression** (theory2.tex §48.3)

    *Statement*: The semantic archive is never larger than the raw episode
    history it was derived from:

        ``archive_size ≤ raw_history_size``

    Rationale
    ---------
    The archive is a *lossy or lossless compression* of raw history.  If the
    archive exceeds the raw history in size, the compression process has
    introduced overhead (e.g. redundant indices, inflated metadata) that
    violates the design contract.

    Scope
    -----
    Both sizes are measured in the same unit (bytes, tokens, or item counts —
    the theorem is unit-agnostic as long as the same unit is used for both).
    """

    NAME: str = "Theorem48_3_ArchiveCompression"
    DESCRIPTION: str = (
        "The semantic archive size is ≤ the raw history size "
        "(compression does not inflate)."
    )

    # ------------------------------------------------------------------
    def check(self, state: dict) -> bool:
        """Return ``True`` iff archive_size ≤ raw_history_size."""
        raw: int = state.get(_KEY_RAW_HISTORY_SIZE, 0)
        archive: int = state.get(_KEY_ARCHIVE_SIZE, 0)
        # Guard: raw_history_size == 0 means no history yet; archive must also be 0.
        if raw == 0:
            return archive == 0
        return archive <= raw * _ARCHIVE_COMPRESSION_MAX_RATIO

    def counterexample(self, state: dict) -> dict | None:
        """Return a minimal counterexample if the theorem is violated."""
        if self.check(state):
            return None
        raw = state.get(_KEY_RAW_HISTORY_SIZE, 0)
        archive = state.get(_KEY_ARCHIVE_SIZE, 0)
        return {
            "description": "archive exceeds raw history",
            _KEY_RAW_HISTORY_SIZE: raw,
            _KEY_ARCHIVE_SIZE: archive,
            "excess": archive - raw,
            "ratio": (archive / raw) if raw > 0 else float("inf"),
        }

    def verify(self, state: dict) -> TheoremResult:
        """Run the full verification and return a :class:`TheoremResult`."""
        holds = self.check(state)
        witness = None if holds else self.counterexample(state)
        raw = state.get(_KEY_RAW_HISTORY_SIZE, 0)
        archive = state.get(_KEY_ARCHIVE_SIZE, 0)
        ratio = (archive / raw) if raw > 0 else (0.0 if archive == 0 else float("inf"))
        details = (
            f"raw={raw}, archive={archive}, ratio={ratio:.4f} → "
            + ("compressed ✓" if holds else "OVER-SIZED ✗")
        )
        return TheoremResult(
            theorem_id=_new_id(),
            name=self.NAME,
            holds=holds,
            witness=witness,
            checked_at=_now(),
            details=details,
        )


# ─── Theorem 48.4 – Capital Non-Negativity ───────────────────────────────────


class Theorem48_4_CapitalNonNegativity:
    """**Theorem 48.4 – Capital Non-Negativity** (theory2.tex §48.4)

    *Statement*: All per-agent semantic capital balances are non-negative:

        ``∀ b ∈ capital_balances : b ≥ 0``

    Rationale
    ---------
    Semantic capital represents an agent's accumulated treaty credit.  A
    negative balance has no defined interpretation in the theory and indicates a
    bookkeeping invariant has been broken (e.g. a debit was applied without a
    prior credit or a sufficient-funds check).

    Scope
    -----
    Applies to every balance in the ledger at rest, i.e. after any atomic
    transaction is committed.  Mid-transaction state is explicitly excluded.
    """

    NAME: str = "Theorem48_4_CapitalNonNegativity"
    DESCRIPTION: str = (
        "All semantic capital balances are non-negative (≥ 0) after any "
        "committed transaction."
    )

    # ------------------------------------------------------------------
    def check(self, state: dict) -> bool:
        """Return ``True`` iff every balance is ≥ ``_CAPITAL_FLOOR``."""
        balances: list[float] = state.get(_KEY_CAPITAL_BALANCES, [])
        return all(b >= _CAPITAL_FLOOR - _FLOAT_EPS for b in balances)

    def counterexample(self, state: dict) -> dict | None:
        """Return a minimal counterexample listing all negative balances."""
        if self.check(state):
            return None
        balances: list[float] = state.get(_KEY_CAPITAL_BALANCES, [])
        negatives = [(i, b) for i, b in enumerate(balances) if b < _CAPITAL_FLOOR - _FLOAT_EPS]
        return {
            "description": "one or more capital balances are negative",
            "negative_entries": [{"index": i, "balance": b} for i, b in negatives],
            "n_violations": len(negatives),
        }

    def verify(self, state: dict) -> TheoremResult:
        """Run the full verification and return a :class:`TheoremResult`."""
        holds = self.check(state)
        witness = None if holds else self.counterexample(state)
        balances: list[float] = state.get(_KEY_CAPITAL_BALANCES, [])
        summary = _summarise_balances(balances)
        n_neg = sum(1 for b in balances if b < _CAPITAL_FLOOR - _FLOAT_EPS)
        details = (
            f"balances: {summary}, negatives={n_neg} → "
            + ("non-negative ✓" if holds else f"{n_neg} NEGATIVE ✗")
        )
        return TheoremResult(
            theorem_id=_new_id(),
            name=self.NAME,
            holds=holds,
            witness=witness,
            checked_at=_now(),
            details=details,
        )


# ─── Theorem 48.5 – Interface Discovery Completeness ─────────────────────────


class Theorem48_5_InterfaceDiscoveryCompleteness:
    """**Theorem 48.5 – Interface Discovery Completeness** (theory2.tex §48.5)

    *Statement*: The set of interfaces discovered during treaty synthesis is a
    superset of the set that was explicitly checked:

        ``discovered_interfaces ⊇ checked_interfaces``

    Rationale
    ---------
    The treaty synthesiser promises to include every interface it explicitly
    verifies in its discovery output.  If a checked interface is absent from the
    discovery set, the synthesiser has silently dropped evidence, which
    compromises treaty correctness proofs that rely on the discovery record.

    Scope
    -----
    Applies at the end of a full synthesis pass.  Incremental / streaming passes
    may temporarily violate this until the pass is complete.
    """

    NAME: str = "Theorem48_5_InterfaceDiscoveryCompleteness"
    DESCRIPTION: str = (
        "discovered_interfaces ⊇ checked_interfaces: no checked interface "
        "is missing from the discovery output."
    )

    # ------------------------------------------------------------------
    def check(self, state: dict) -> bool:
        """Return ``True`` iff every checked interface is in the discovered set."""
        checked: set = state.get(_KEY_CHECKED_INTERFACES, set())
        discovered: set = state.get(_KEY_DISCOVERED_INTERFACES, set())
        return _set_is_superset(discovered, checked)

    def counterexample(self, state: dict) -> dict | None:
        """Return the missing interfaces as a counterexample, or ``None``."""
        if self.check(state):
            return None
        checked: set = state.get(_KEY_CHECKED_INTERFACES, set())
        discovered: set = state.get(_KEY_DISCOVERED_INTERFACES, set())
        missing = checked - discovered
        return {
            "description": "checked interfaces absent from discovery output",
            "missing_interfaces": sorted(missing),
            "n_missing": len(missing),
            "checked_count": len(checked),
            "discovered_count": len(discovered),
        }

    def verify(self, state: dict) -> TheoremResult:
        """Run the full verification and return a :class:`TheoremResult`."""
        holds = self.check(state)
        witness = None if holds else self.counterexample(state)
        checked: set = state.get(_KEY_CHECKED_INTERFACES, set())
        discovered: set = state.get(_KEY_DISCOVERED_INTERFACES, set())
        missing = checked - discovered
        details = (
            f"checked={len(checked)}, discovered={len(discovered)}, "
            f"missing={len(missing)} → "
            + ("complete ✓" if holds else f"{len(missing)} MISSING ✗")
        )
        return TheoremResult(
            theorem_id=_new_id(),
            name=self.NAME,
            holds=holds,
            witness=witness,
            checked_at=_now(),
            details=details,
        )


# ─── Theorem Registry ─────────────────────────────────────────────────────────


class TreatyMemoryTheoremSchema:
    """Central registry for all Ch48 treaty-memory theorems.

    Usage
    -----
    ::

        schema = TreatyMemoryTheoremSchema()
        state  = make_theorem_state(episodes_before=3, episodes_after=5, ...)
        results = schema.verify_all(state)
        for r in results:
            print(r.summary())

    The registry owns one instance of each theorem class.  Callers that need
    custom instances (e.g. with patched tolerances) should instantiate the
    theorem classes directly.
    """

    def __init__(self) -> None:
        """Instantiate all theorems and index them by name."""
        self._theorems: dict[str, Any] = {
            Theorem48_1_MemoryMonotonicity.NAME: Theorem48_1_MemoryMonotonicity(),
            Theorem48_2_LawStability.NAME: Theorem48_2_LawStability(),
            Theorem48_3_ArchiveCompression.NAME: Theorem48_3_ArchiveCompression(),
            Theorem48_4_CapitalNonNegativity.NAME: Theorem48_4_CapitalNonNegativity(),
            Theorem48_5_InterfaceDiscoveryCompleteness.NAME: Theorem48_5_InterfaceDiscoveryCompleteness(),
        }
        log.debug("[%s] TreatyMemoryTheoremSchema initialised with %d theorems", _LOG_PREFIX, len(self._theorems))

    # ------------------------------------------------------------------
    def list_theorems(self) -> list[str]:
        """Return the sorted list of registered theorem names."""
        return sorted(self._theorems.keys())

    def get_theorem(self, name: str) -> Any:
        """Return the theorem instance for *name*.

        Raises
        ------
        KeyError
            If *name* is not registered.
        """
        if name not in self._theorems:
            raise KeyError(
                f"Unknown theorem {name!r}. "
                f"Available: {self.list_theorems()}"
            )
        return self._theorems[name]

    def verify_all(self, state: dict) -> list[TheoremResult]:
        """Verify every registered theorem against *state*.

        Parameters
        ----------
        state:
            A theorem-state dict (see :func:`make_theorem_state`).

        Returns
        -------
        list[TheoremResult]
            Results in alphabetical theorem-name order.
        """
        results: list[TheoremResult] = []
        for name in self.list_theorems():
            thm = self._theorems[name]
            result = thm.verify(state)
            log.info(result.summary())
            results.append(result)
        return results

    # ------------------------------------------------------------------
    def count_violations(self, state: dict) -> int:
        """Return the number of theorems violated by *state*."""
        return sum(1 for r in self.verify_all(state) if not r.holds)

    def all_hold(self, state: dict) -> bool:
        """Return ``True`` iff every theorem holds on *state*."""
        return self.count_violations(state) == 0

    def violated_names(self, state: dict) -> list[str]:
        """Return the names of all violated theorems, or an empty list."""
        return [r.name for r in self.verify_all(state) if not r.holds]


# ─── Falsification Suite ──────────────────────────────────────────────────────


@dataclass(slots=True)
class FalsificationSuite:
    """Randomised falsification campaign for Ch48 theorems.

    This class performs a poor-man's property-based testing loop.  For each
    theorem it generates up to *n_attempts* adversarial states and checks
    whether the theorem is violated.

    The RNG is seeded deterministically so that CI runs are reproducible.  Pass
    a different *seed* value to explore a different region of the state space.

    Parameters
    ----------
    seed:
        Integer seed for Python's ``random`` module.

    Example
    -------
    ::

        suite = FalsificationSuite(seed=42)
        reports = suite.run_all(n_attempts=500)
        for r in reports:
            print(r.summary())
    """

    seed: int
    # Private fields populated in __post_init__; declared here so slots work.
    _rng: random.Random = field(init=False, repr=False, compare=False)
    _schema: TreatyMemoryTheoremSchema = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Initialise the RNG and build the theorem registry."""
        self._rng = random.Random(self.seed)
        self._schema = TreatyMemoryTheoremSchema()
        log.debug("[%s] FalsificationSuite seeded with %d", _LOG_PREFIX, self.seed)

    # ------------------------------------------------------------------
    # Adversarial state generators
    # ------------------------------------------------------------------

    def _adversarial_monotonicity(self) -> dict:
        """Generate a state that attempts to violate Theorem 48.1."""
        before = self._rng.randint(1, _MAX_ADVERSARIAL_EPISODES)
        # Adversarial: after < before (decrease)
        after = self._rng.randint(0, before - 1)
        return make_theorem_state(episodes_before=before, episodes_after=after)

    def _adversarial_law_stability(self) -> dict:
        """Generate a state that attempts to violate Theorem 48.2."""
        support = self._rng.uniform(0.1, 100.0)
        refutation = self._rng.uniform(0.1, 100.0)
        expected = _confidence_from_counts(support, refutation)
        # Adversarial: stored value deliberately deviates by > ε
        delta = self._rng.uniform(0.01, 0.5) * self._rng.choice([-1, 1])
        stored = max(0.0, min(1.0, expected + delta))
        return make_theorem_state(
            law_support=support,
            law_refutation=refutation,
            law_confidence=stored,
        )

    def _adversarial_archive_compression(self) -> dict:
        """Generate a state that attempts to violate Theorem 48.3."""
        raw = self._rng.randint(1, 10_000)
        # Adversarial: archive > raw
        archive = raw + self._rng.randint(1, 500)
        return make_theorem_state(raw_history_size=raw, archive_size=archive)

    def _adversarial_capital_nonnegativity(self) -> dict:
        """Generate a state that attempts to violate Theorem 48.4."""
        n = self._rng.randint(1, 20)
        balances = [self._rng.uniform(-10.0, 10.0) for _ in range(n)]
        # Guarantee at least one negative to make the adversarial attempt meaningful.
        balances[0] = -abs(self._rng.uniform(0.01, 5.0))
        return make_theorem_state(capital_balances=balances)

    def _adversarial_interface_completeness(self) -> dict:
        """Generate a state that attempts to violate Theorem 48.5."""
        n_interfaces = self._rng.randint(3, 20)
        all_ifaces = {f"iface_{i}" for i in range(n_interfaces)}
        checked = set(self._rng.sample(sorted(all_ifaces), k=self._rng.randint(1, n_interfaces)))
        # Adversarial: discovered is a strict subset of checked (missing some)
        if checked:
            remove_k = self._rng.randint(1, len(checked))
            discovered = checked - set(self._rng.sample(sorted(checked), k=remove_k))
        else:
            discovered = set()
        return make_theorem_state(
            checked_interfaces=checked,
            discovered_interfaces=discovered,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_adversarial_state(self, theorem_name: str) -> dict:
        """Return one adversarially-constructed state targeting *theorem_name*.

        Parameters
        ----------
        theorem_name:
            The ``NAME`` attribute of the theorem to attack.

        Raises
        ------
        ValueError
            If the theorem name is unrecognised.
        """
        dispatch: dict[str, Any] = {
            Theorem48_1_MemoryMonotonicity.NAME: self._adversarial_monotonicity,
            Theorem48_2_LawStability.NAME: self._adversarial_law_stability,
            Theorem48_3_ArchiveCompression.NAME: self._adversarial_archive_compression,
            Theorem48_4_CapitalNonNegativity.NAME: self._adversarial_capital_nonnegativity,
            Theorem48_5_InterfaceDiscoveryCompleteness.NAME: self._adversarial_interface_completeness,
        }
        if theorem_name not in dispatch:
            raise ValueError(
                f"No adversarial generator for {theorem_name!r}. "
                f"Known: {sorted(dispatch)}"
            )
        return dispatch[theorem_name]()

    def run_falsification(
        self,
        theorem: Any,
        n_attempts: int = _DEFAULT_FALSIFICATION_ATTEMPTS,
    ) -> FalsificationReport:
        """Run a falsification campaign against *theorem*.

        Parameters
        ----------
        theorem:
            A theorem object exposing ``verify(state) -> TheoremResult``.
        n_attempts:
            Number of adversarial states to try.

        Returns
        -------
        FalsificationReport
            Summary of the campaign, including the first counterexample found (if any).
        """
        name: str = getattr(theorem, "NAME", repr(theorem))
        log.debug("[%s] Starting falsification of %s (%d attempts)", _LOG_PREFIX, name, n_attempts)
        first_counterexample: dict | None = None
        falsified = False

        for attempt in range(n_attempts):
            try:
                state = self.generate_adversarial_state(name)
            except ValueError:
                # No specialised generator; fall back to a random benign state.
                state = make_theorem_state(
                    episodes_before=self._rng.randint(0, 100),
                    episodes_after=self._rng.randint(0, 100),
                )
            result: TheoremResult = theorem.verify(state)
            if not result.holds:
                falsified = True
                first_counterexample = result.witness
                log.warning(
                    "[%s] Theorem %s FALSIFIED on attempt %d: %s",
                    _LOG_PREFIX,
                    name,
                    attempt + 1,
                    result.details,
                )
                break

        return FalsificationReport(
            report_id=_new_id(),
            theorem_name=name,
            n_attempts=n_attempts if not falsified else (attempt + 1),  # type: ignore[possibly-undefined]
            falsified=falsified,
            counterexample=first_counterexample,
            run_at=_now(),
        )

    def run_all(
        self,
        n_attempts: int = _DEFAULT_FALSIFICATION_ATTEMPTS,
    ) -> list[FalsificationReport]:
        """Run falsification campaigns for every registered theorem.

        Parameters
        ----------
        n_attempts:
            Per-theorem attempt budget.

        Returns
        -------
        list[FalsificationReport]
            One report per theorem in alphabetical order.
        """
        reports: list[FalsificationReport] = []
        for name in self._schema.list_theorems():
            thm = self._schema.get_theorem(name)
            report = self.run_falsification(thm, n_attempts=n_attempts)
            log.info(report.summary())
            reports.append(report)
        return reports


# ─── Additional invariant helpers ─────────────────────────────────────────────


def check_episode_delta(before: int, after: int) -> bool:
    """Return ``True`` iff the episode transition is monotone.

    Thin wrapper around :class:`Theorem48_1_MemoryMonotonicity` suitable for
    use as a guard in update code paths.
    """
    return after >= before


def check_confidence_in_range(confidence: float) -> bool:
    """Return ``True`` iff *confidence* is in the valid ``[0, 1]`` range."""
    return _is_valid_confidence(confidence)


def check_no_negative_balances(balances: list[float]) -> bool:
    """Return ``True`` iff all values in *balances* are non-negative."""
    return all(b >= _CAPITAL_FLOOR - _FLOAT_EPS for b in balances)


def check_superset(discovered: set, checked: set) -> bool:
    """Return ``True`` iff *discovered* ⊇ *checked*."""
    return _set_is_superset(discovered, checked)


def compute_compression_ratio(raw: int, archive: int) -> float:
    """Return ``archive / raw``.  Returns ``0.0`` if *raw* is zero and *archive* is zero,
    or ``+inf`` if *raw* is zero but *archive* is positive (pathological case).
    """
    if raw == 0:
        return 0.0 if archive == 0 else math.inf
    return archive / raw


def summarise_results(results: list[TheoremResult]) -> dict:
    """Return a high-level summary dict over a list of :class:`TheoremResult` objects.

    Useful for structured logging and monitoring dashboards.
    """
    total = len(results)
    held = sum(1 for r in results if r.holds)
    violated = total - held
    violated_names = [r.name for r in results if not r.holds]
    return {
        "total": total,
        "held": held,
        "violated": violated,
        "violated_names": violated_names,
        "all_hold": violated == 0,
    }


def summarise_falsification_reports(reports: list[FalsificationReport]) -> dict:
    """Return a summary dict over a list of :class:`FalsificationReport` objects."""
    total = len(reports)
    falsified = sum(1 for r in reports if r.falsified)
    safe = total - falsified
    falsified_names = [r.theorem_name for r in reports if r.falsified]
    total_attempts = sum(r.n_attempts for r in reports)
    return {
        "total_theorems": total,
        "falsified": falsified,
        "safe": safe,
        "falsified_names": falsified_names,
        "total_attempts": total_attempts,
    }


def invariant_check_all(state: dict) -> bool:
    """Quick boolean check: return ``True`` iff all five Ch48 theorems hold on *state*.

    Equivalent to ``TreatyMemoryTheoremSchema().all_hold(state)`` but avoids
    constructing the schema object if you already have a state dict handy.
    """
    schema = TreatyMemoryTheoremSchema()
    return schema.all_hold(state)


def format_result_table(results: list[TheoremResult]) -> str:
    """Format *results* as a plain-text ASCII table for terminal output.

    Example output::

        Theorem                                        Status   Details
        ─────────────────────────────────────────────────────────────────
        Theorem48_1_MemoryMonotonicity                 HOLDS    ...
        Theorem48_2_LawStability                       HOLDS    ...

    """
    col_w = 50
    header = f"{'Theorem':<{col_w}} {'Status':<10} Details"
    sep = "─" * (col_w + 60)
    lines = [header, sep]
    for r in results:
        status = "HOLDS" if r.holds else "VIOLATED"
        details_short = r.details[:60] + ("…" if len(r.details) > 60 else "")
        lines.append(f"{r.name:<{col_w}} {status:<10} {details_short}")
    return "\n".join(lines)


# ─── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    print(f"[smoke] {__file__}")

    # ── Build a known-good state ──────────────────────────────────────────────
    good_state = make_theorem_state(
        episodes_before=10,
        episodes_after=15,
        law_support=8.0,
        law_refutation=2.0,
        # law_confidence is derived automatically → 0.8
        raw_history_size=1000,
        archive_size=600,
        capital_balances=[10.0, 5.5, 0.0, 22.3],
        checked_interfaces={"iface_A", "iface_B"},
        discovered_interfaces={"iface_A", "iface_B", "iface_C"},
    )

    schema = TreatyMemoryTheoremSchema()
    results = schema.verify_all(good_state)
    assert all(r.holds for r in results), (
        "Expected all theorems to hold on good_state:\n"
        + "\n".join(r.summary() for r in results if not r.holds)
    )
    print("[smoke] All 5 theorems hold on good_state ✓")
    print(format_result_table(results))

    # ── Build known-bad states and check each theorem is violated ─────────────
    # T48.1 violation: episodes decrease
    bad_mono = make_theorem_state(episodes_before=20, episodes_after=5)
    t1 = Theorem48_1_MemoryMonotonicity()
    r1 = t1.verify(bad_mono)
    assert not r1.holds, "Expected T48.1 to be violated"
    print(f"[smoke] T48.1 violation detected: {r1.witness} ✓")

    # T48.2 violation: confidence does not match counts
    bad_law = make_theorem_state(law_support=7.0, law_refutation=3.0, law_confidence=0.99)
    t2 = Theorem48_2_LawStability()
    r2 = t2.verify(bad_law)
    assert not r2.holds, "Expected T48.2 to be violated"
    print(f"[smoke] T48.2 violation detected: delta={r2.witness['delta']:.2e} ✓")

    # T48.3 violation: archive bigger than raw history
    bad_arch = make_theorem_state(raw_history_size=100, archive_size=200)
    t3 = Theorem48_3_ArchiveCompression()
    r3 = t3.verify(bad_arch)
    assert not r3.holds, "Expected T48.3 to be violated"
    print(f"[smoke] T48.3 violation detected: excess={r3.witness['excess']} ✓")

    # T48.4 violation: negative balance
    bad_cap = make_theorem_state(capital_balances=[5.0, -1.0, 3.0])
    t4 = Theorem48_4_CapitalNonNegativity()
    r4 = t4.verify(bad_cap)
    assert not r4.holds, "Expected T48.4 to be violated"
    print(f"[smoke] T48.4 violation detected: {r4.witness['negative_entries']} ✓")

    # T48.5 violation: checked not subset of discovered
    bad_iface = make_theorem_state(
        checked_interfaces={"A", "B", "C"},
        discovered_interfaces={"A", "B"},
    )
    t5 = Theorem48_5_InterfaceDiscoveryCompleteness()
    r5 = t5.verify(bad_iface)
    assert not r5.holds, "Expected T48.5 to be violated"
    print(f"[smoke] T48.5 violation detected: missing={r5.witness['missing_interfaces']} ✓")

    # ── assert_theorem helper ─────────────────────────────────────────────────
    try:
        assert_theorem(t1, bad_mono)
        print("[smoke] ERROR: assert_theorem should have raised")
        sys.exit(1)
    except AssertionError:
        print("[smoke] assert_theorem raises correctly on violation ✓")

    assert_theorem(t1, good_state)
    print("[smoke] assert_theorem passes on good_state ✓")

    # ── batch_verify ──────────────────────────────────────────────────────────
    all_theorems = [t1, t2, t3, t4, t5]
    batch = batch_verify(all_theorems, good_state)
    assert all(r.holds for r in batch), "batch_verify should all hold on good_state"
    print(f"[smoke] batch_verify returned {len(batch)} results, all hold ✓")

    # ── FalsificationSuite ────────────────────────────────────────────────────
    suite = FalsificationSuite(seed=0)
    # Each adversarial generator is designed to produce violating states, so
    # every theorem should be falsified within a small number of attempts.
    reports = suite.run_all(n_attempts=50)
    summary = summarise_falsification_reports(reports)
    print(f"[smoke] Falsification summary: {summary}")
    assert summary["falsified"] == 5, (
        f"Expected all 5 theorems falsified, got {summary['falsified_names']}"
    )
    print("[smoke] All 5 theorems falsified by adversarial suite ✓")

    # ── Compression ratio helper ──────────────────────────────────────────────
    ratio = compute_compression_ratio(1000, 600)
    assert abs(ratio - 0.6) < _FLOAT_EPS * 10
    ratio_inf = compute_compression_ratio(0, 5)
    assert math.isinf(ratio_inf)
    print("[smoke] compute_compression_ratio ✓")

    # ── invariant_check_all ───────────────────────────────────────────────────
    assert invariant_check_all(good_state), "good_state should pass invariant_check_all"
    assert not invariant_check_all(bad_mono), "bad_mono should fail invariant_check_all"
    print("[smoke] invariant_check_all ✓")

    print("[smoke] PASS")
