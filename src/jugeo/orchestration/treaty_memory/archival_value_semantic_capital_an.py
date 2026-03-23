from __future__ import annotations

"""
theory2.tex Ch48 §5 – "Treaty synthesis, negotiation memory, and archival semantics"

# copilot: This module implements the archival-value and semantic-capital subsystem
# described in Chapter 48 §5 of theory2.tex.  It provides machinery for measuring
# the *ongoing worth* of archived negotiation entries, accruing semantic capital
# as treaties are synthesised, and recommending compression strategies when the
# corpus grows beyond sustainable size.

Design
------
The module is organised in five sections:

1. **Constants & configuration** – tuneable hyper-parameters that govern decay rates,
   compression thresholds, and capital-accrual rules.
2. **Core data-models** – frozen / mutable dataclasses that carry the domain objects
   across subsystem boundaries with zero implicit mutation.
3. **Account management** – ``SemanticCapitalAccount``, the live ledger that tracks
   deposits, withdrawals, and running balance of semantic capital.
4. **Analysis engine** – ``ArchivalValueAnalyzer``, which computes marginal values,
   compression gains, and depreciation forecasts from an account's history.
5. **Coordinator** – ``ArchivalValueCoordinator``, the façade used by the broader
   orchestration layer to drive the whole pipeline.

All public names are listed in ``__all__``.  Jugeo-internal imports are guarded
by ``try/except ImportError`` so that the module can be imported in isolation
(e.g. during unit testing) without requiring the full jugeo package tree.
"""

import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ─── Jugeo imports (guarded) ────────────────────────────────────────────────

try:
    from jugeo.orchestration.treaty_memory.index import TreatyMemoryIndex  # type: ignore[import]
except ImportError:
    TreatyMemoryIndex = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.treaty_memory.entry import NegotiationEntry  # type: ignore[import]
except ImportError:
    NegotiationEntry = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.episode import Episode  # type: ignore[import]
except ImportError:
    Episode = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.context import JugeoContext  # type: ignore[import]
except ImportError:
    JugeoContext = Any  # type: ignore[assignment,misc]

# ─── Module-level logger ────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─── Public API ─────────────────────────────────────────────────────────────

__all__ = [
    # data models
    "CapitalUnit",
    "CompressionStrategy",
    "ValueAnalysisReport",
    # account
    "SemanticCapitalAccount",
    # engine
    "ArchivalValueAnalyzer",
    # coordinator
    "ArchivalValueCoordinator",
    # helpers
    "compute_archival_value",
    "apply_compression",
    "semantic_capital_rate",
    "value_decay",
    "marginal_relevance_score",
    "normalise_capital_vector",
    "entropy_of_corpus",
    "compression_efficiency",
    "capital_weighted_centroid",
    "forecast_depreciation_schedule",
]

# ─── Section 1: Constants & Configuration ───────────────────────────────────

# Default exponential decay rate for archival value (per second).
# A value of 0.0 means no decay; 1.0 would halve every ~0.69 seconds.
# Calibrated against §48.3 empirical decay experiments (theory2.tex).
_DEFAULT_DECAY_RATE: float = 1.5e-6

# Minimum archival value beneath which an entry is eligible for compression.
# Entries with computed value < this threshold may be discarded or summarised.
_MIN_ARCHIVAL_VALUE: float = 0.05

# Maximum fraction of the corpus that may be compressed in a single pass.
# Prevents catastrophic information loss when the corpus is large.
_MAX_COMPRESSION_FRACTION: float = 0.40

# Capital-accrual rate baseline (semantic capital units per analysed entry).
# This is the *floor* rate; the actual rate is boosted by relevance signals.
_BASE_ACCRUAL_RATE: float = 1.0

# Boost multiplier applied to capital accrual when an entry is referenced
# by more than one downstream treaty node.
_MULTI_REFERENCE_BOOST: float = 1.75

# Penalty multiplier applied when an entry exhibits high lexical redundancy
# with previously archived material (overlap ratio > _REDUNDANCY_THRESHOLD).
_REDUNDANCY_PENALTY: float = 0.60

# Lexical-overlap ratio above which two entries are considered redundant.
_REDUNDANCY_THRESHOLD: float = 0.72

# Number of depreciation periods used in the default forecast window.
_DEFAULT_DEPRECIATION_PERIODS: int = 12

# Compression strategy policy names recognised by this module.
_POLICY_TRUNCATE: str = "truncate"
_POLICY_SUMMARISE: str = "summarise"
_POLICY_CLUSTER: str = "cluster"
_POLICY_DELTA_ENCODE: str = "delta_encode"

# Sentinel value for "no capital unit found" in account lookups.
_SENTINEL_EMPTY: float = 0.0

# Smoothing constant added to entropy denominators to prevent log(0).
_ENTROPY_EPSILON: float = 1e-9

# Maximum number of history entries retained by SemanticCapitalAccount
# before automatic ring-buffer truncation.  Set to 0 to disable.
_ACCOUNT_MAX_HISTORY: int = 10_000

# Version tag embedded in every ValueAnalysisReport for forward-compat.
_REPORT_SCHEMA_VERSION: str = "1.0.0"

# How many standard deviations above the mean marginal value qualifies
# an entry as "high-value" in the analysis report.
_HIGH_VALUE_SIGMA: float = 1.5

# ─── Section 2: Core Data Models ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CapitalUnit:
    """A single indivisible quantum of semantic capital.

    Capital units are immutable once created.  They are deposited into a
    :class:`SemanticCapitalAccount` and may later be withdrawn by their
    ``unit_id``.

    Attributes
    ----------
    unit_id:
        Globally-unique identifier for this unit (UUID4 string).
    source_entry_id:
        The identifier of the :class:`NegotiationEntry` that generated this
        unit of capital.
    capital_type:
        A short label categorising the capital (e.g. ``"relevance"``,
        ``"novelty"``, ``"synthesis"``).
    amount:
        Non-negative floating-point magnitude of the capital unit.
    accrued_at:
        UNIX timestamp (seconds) at which the unit was created.
    """

    unit_id: str
    source_entry_id: str
    capital_type: str
    amount: float
    accrued_at: float

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError(f"CapitalUnit.amount must be ≥ 0, got {self.amount!r}")
        if not self.unit_id:
            raise ValueError("CapitalUnit.unit_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class CompressionStrategy:
    """Immutable descriptor for a corpus-compression strategy.

    Compression strategies are data-only objects; actual compression logic
    lives in :func:`apply_compression` and
    :meth:`ArchivalValueAnalyzer.compression_gain`.

    Attributes
    ----------
    strategy_id:
        Unique identifier for this strategy instance.
    name:
        Human-readable name (e.g. ``"aggressive_truncation"``).
    target_ratio:
        Desired corpus size after compression, expressed as a fraction of
        the original size (0 < target_ratio ≤ 1).
    min_value_threshold:
        Entries whose archival value falls below this threshold are
        candidates for compression/removal.
    policy:
        One of :data:`_POLICY_TRUNCATE`, :data:`_POLICY_SUMMARISE`,
        :data:`_POLICY_CLUSTER`, or :data:`_POLICY_DELTA_ENCODE`.
    """

    strategy_id: str
    name: str
    target_ratio: float
    min_value_threshold: float
    policy: str

    def __post_init__(self) -> None:
        if not (0.0 < self.target_ratio <= 1.0):
            raise ValueError(
                f"target_ratio must be in (0, 1], got {self.target_ratio!r}"
            )
        known = {_POLICY_TRUNCATE, _POLICY_SUMMARISE, _POLICY_CLUSTER, _POLICY_DELTA_ENCODE}
        if self.policy not in known:
            raise ValueError(f"Unknown compression policy {self.policy!r}; must be one of {known}")


@dataclass(frozen=True, slots=True)
class ValueAnalysisReport:
    """Immutable snapshot of an archival-value analysis run.

    Produced by :meth:`ArchivalValueAnalyzer.analyze` and consumed by the
    coordinator and any downstream reporting components.

    Attributes
    ----------
    report_id:
        UUID4 string identifying this specific report.
    total_capital:
        Sum of all capital amounts held in the analysed account at the
        time of the report.
    marginal_values:
        Tuple of ``(entry_id, marginal_value)`` pairs, sorted descending
        by marginal value.
    compression_recommendations:
        Tuple of ``(entry_id, reason)`` pairs for entries recommended for
        compression.
    depreciation_forecast:
        Tuple of projected total capital values for each future period in
        the forecast window.
    generated_at:
        UNIX timestamp at which this report was generated.
    schema_version:
        Report schema version tag for forward-compatibility.
    """

    report_id: str
    total_capital: float
    marginal_values: tuple
    compression_recommendations: tuple
    depreciation_forecast: tuple
    generated_at: float
    schema_version: str = _REPORT_SCHEMA_VERSION

    def summary(self) -> str:
        """Return a compact human-readable summary of this report."""
        n_rec = len(self.compression_recommendations)
        return (
            f"ValueAnalysisReport(id={self.report_id[:8]}… "
            f"capital={self.total_capital:.4f} "
            f"recommendations={n_rec} "
            f"schema={self.schema_version})"
        )


# ─── Section 3: Account Management ──────────────────────────────────────────


@dataclass(slots=True)
class SemanticCapitalAccount:
    """Mutable ledger that tracks the accumulation and expenditure of semantic capital.

    The account maintains an ordered history of :class:`CapitalUnit` deposits
    and a fast-lookup dict for withdrawal by ``unit_id``.  The running balance
    is recomputed lazily from the live unit pool, so it always reflects the
    current state even after partial withdrawals.

    Parameters
    ----------
    account_id:
        Optional explicit identifier; defaults to a fresh UUID4.
    max_history:
        Maximum number of history events to retain.  Older events are
        silently dropped when the limit is reached.  Set to ``0`` to retain
        all history indefinitely (memory-unbounded).
    """

    account_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    max_history: int = field(default=_ACCOUNT_MAX_HISTORY)

    # Internal state – not part of the public interface.
    _units: dict = field(default_factory=dict, repr=False)
    _history: list = field(default_factory=list, repr=False)

    # ── deposits & withdrawals ───────────────────────────────────────────

    def deposit(self, unit: CapitalUnit) -> None:
        """Deposit *unit* into the account.

        Deposits are idempotent with respect to ``unit_id``: re-depositing
        a unit with the same ``unit_id`` overwrites the previous entry and
        appends a new history event.

        Parameters
        ----------
        unit:
            The :class:`CapitalUnit` to add.
        """
        self._units[unit.unit_id] = unit
        self._record_event("deposit", unit)
        log.debug(
            "SemanticCapitalAccount(%s): deposited %s (+%.4f)",
            self.account_id[:8],
            unit.unit_id[:8],
            unit.amount,
        )

    def withdraw(self, unit_id: str) -> CapitalUnit | None:
        """Remove the unit identified by *unit_id* and return it.

        Returns ``None`` if no matching unit exists.  A ``"withdrawal"``
        event is appended to history regardless.

        Parameters
        ----------
        unit_id:
            The :class:`CapitalUnit.unit_id` to remove.
        """
        unit = self._units.pop(unit_id, None)
        if unit is not None:
            self._record_event("withdrawal", unit)
            log.debug(
                "SemanticCapitalAccount(%s): withdrew %s (-%.4f)",
                self.account_id[:8],
                unit_id[:8],
                unit.amount,
            )
        return unit

    # ── balance & inspection ─────────────────────────────────────────────

    def balance(self) -> float:
        """Return the current total balance (sum of all live unit amounts)."""
        if not self._units:
            return _SENTINEL_EMPTY
        return math.fsum(u.amount for u in self._units.values())

    def history(self) -> list[CapitalUnit]:
        """Return a copy of the chronological deposit/withdrawal history.

        Modifying the returned list does not affect the account state.
        """
        return list(self._history)

    def live_units(self) -> list[CapitalUnit]:
        """Return all currently live (non-withdrawn) :class:`CapitalUnit` objects."""
        return list(self._units.values())

    def audit(self) -> dict:
        """Return a structured audit snapshot of the account.

        The snapshot includes balance, unit count, capital breakdown by type,
        oldest and newest accrual timestamps, and history length.

        Returns
        -------
        dict
            Keys: ``account_id``, ``balance``, ``unit_count``,
            ``by_type``, ``oldest_accrual``, ``newest_accrual``,
            ``history_length``.
        """
        units = self.live_units()
        by_type: dict[str, float] = {}
        for u in units:
            by_type[u.capital_type] = by_type.get(u.capital_type, 0.0) + u.amount

        accrual_times = [u.accrued_at for u in units] or [0.0]
        return {
            "account_id": self.account_id,
            "balance": self.balance(),
            "unit_count": len(units),
            "by_type": by_type,
            "oldest_accrual": min(accrual_times),
            "newest_accrual": max(accrual_times),
            "history_length": len(self._history),
        }

    # ── internal helpers ─────────────────────────────────────────────────

    def _record_event(self, event_type: str, unit: CapitalUnit) -> None:
        """Append *unit* to the event history, evicting old entries if needed."""
        self._history.append((event_type, unit))
        if self.max_history and len(self._history) > self.max_history:
            # Drop the oldest quarter of the history to amortise cost.
            trim = self.max_history // 4
            self._history = self._history[trim:]


# ─── Section 4: Analysis Engine ─────────────────────────────────────────────


class ArchivalValueAnalyzer:
    """Stateless engine for computing archival value metrics.

    All methods are pure functions over their arguments; the class carries
    no mutable state.  This design allows the analyzer to be shared across
    coordinator instances without synchronisation concerns.

    Usage
    -----
    ::

        analyzer = ArchivalValueAnalyzer()
        report = analyzer.analyze(account, index)
    """

    # ── primary analysis ─────────────────────────────────────────────────

    def analyze(
        self,
        account: SemanticCapitalAccount,
        index: Any,
        *,
        periods: int = _DEFAULT_DEPRECIATION_PERIODS,
        strategy: CompressionStrategy | None = None,
    ) -> ValueAnalysisReport:
        """Run a full archival-value analysis and return a :class:`ValueAnalysisReport`.

        Parameters
        ----------
        account:
            The :class:`SemanticCapitalAccount` to analyse.
        index:
            The treaty-memory index (used to look up entry metadata).
            May be ``None`` or a stub when called outside the full runtime.
        periods:
            Number of future periods to include in the depreciation forecast.
        strategy:
            Optional :class:`CompressionStrategy` to use when generating
            compression recommendations.  If ``None``, a default truncation
            strategy is synthesised.

        Returns
        -------
        ValueAnalysisReport
        """
        units = account.live_units()
        total = account.balance()

        # --- marginal values ---
        mv_pairs = self._compute_marginal_values(units, index)

        # --- compression recommendations ---
        if strategy is None:
            strategy = _default_strategy()
        recs = self._generate_recommendations(mv_pairs, strategy)

        # --- depreciation forecast ---
        forecast = self.depreciation_curve(account, periods)

        return ValueAnalysisReport(
            report_id=str(uuid.uuid4()),
            total_capital=total,
            marginal_values=tuple(mv_pairs),
            compression_recommendations=tuple(recs),
            depreciation_forecast=tuple(forecast),
            generated_at=time.time(),
        )

    # ── marginal value ───────────────────────────────────────────────────

    def marginal_value(self, entry: dict) -> float:
        """Compute the marginal archival value of a single entry dict.

        The marginal value is a composite score that weighs:

        * **Recency** – entries accrued more recently retain more value.
        * **Reference count** – entries cited by many downstream nodes are
          more valuable.
        * **Novelty** – entries with low lexical overlap to the existing
          corpus score higher.

        Parameters
        ----------
        entry:
            A dict with optional keys ``"timestamp"``, ``"ref_count"``,
            ``"novelty_score"``, ``"importance"``.

        Returns
        -------
        float
            Non-negative marginal value score.
        """
        now = time.time()
        ts = float(entry.get("timestamp", now))
        elapsed = max(now - ts, 0.0)
        recency = value_decay(1.0, elapsed, _DEFAULT_DECAY_RATE)

        ref_count = float(entry.get("ref_count", 1))
        ref_boost = math.log1p(ref_count)

        novelty = float(entry.get("novelty_score", 0.5))
        importance = float(entry.get("importance", 1.0))

        score = recency * ref_boost * novelty * importance
        return max(score, 0.0)

    # ── compression gain ─────────────────────────────────────────────────

    def compression_gain(
        self,
        entries: list[dict],
        strategy: CompressionStrategy,
    ) -> float:
        """Estimate the net information gain from applying *strategy* to *entries*.

        Gain is defined as the fraction of total capital that would be
        released (freed) by removing low-value entries, multiplied by a
        policy-dependent efficiency factor.

        Parameters
        ----------
        entries:
            List of entry dicts (same schema as accepted by
            :meth:`marginal_value`).
        strategy:
            The :class:`CompressionStrategy` to evaluate.

        Returns
        -------
        float
            Estimated gain in [0, 1].
        """
        if not entries:
            return 0.0

        values = [self.marginal_value(e) for e in entries]
        below = sum(1 for v in values if v < strategy.min_value_threshold)
        raw_gain = below / len(entries)
        clipped = min(raw_gain, _MAX_COMPRESSION_FRACTION)

        # Policy efficiency: delta-encode preserves more, truncate loses more.
        efficiency = compression_efficiency(strategy.policy)
        return clipped * efficiency

    # ── depreciation curve ───────────────────────────────────────────────

    def depreciation_curve(
        self,
        account: SemanticCapitalAccount,
        periods: int,
    ) -> list[float]:
        """Project the account balance over *periods* future time-steps.

        Each period represents one abstract "negotiation epoch".  The model
        assumes exponential decay with the global decay rate, applied to the
        *current* balance at the start of each period.

        Parameters
        ----------
        account:
            Source of the current balance and accrual history.
        periods:
            Number of forward epochs to simulate.

        Returns
        -------
        list[float]
            List of length *periods* with projected balances (period 1 first).
        """
        current = account.balance()
        epoch_seconds = 3600.0  # one epoch ≈ one hour of wall-clock time

        forecast: list[float] = []
        for p in range(1, periods + 1):
            elapsed = epoch_seconds * p
            projected = value_decay(current, elapsed, _DEFAULT_DECAY_RATE)
            forecast.append(projected)
        return forecast

    # ── private helpers ──────────────────────────────────────────────────

    def _compute_marginal_values(
        self, units: list[CapitalUnit], index: Any
    ) -> list[tuple[str, float]]:
        """Map each live capital unit to its marginal value."""
        pairs: list[tuple[str, float]] = []
        for unit in units:
            # Try to retrieve rich entry metadata from the index.
            entry: dict = {}
            if index is not None and hasattr(index, "get_entry"):
                try:
                    raw = index.get_entry(unit.source_entry_id)
                    if raw is not None:
                        entry = dict(raw)
                except Exception:
                    pass
            # Enrich with capital-unit metadata.
            entry.setdefault("timestamp", unit.accrued_at)
            entry.setdefault("importance", unit.amount)
            mv = self.marginal_value(entry)
            pairs.append((unit.unit_id, mv))
        # Sort descending by marginal value.
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs

    def _generate_recommendations(
        self,
        mv_pairs: list[tuple[str, float]],
        strategy: CompressionStrategy,
    ) -> list[tuple[str, str]]:
        """Return compression recommendations based on marginal-value scores."""
        recs: list[tuple[str, str]] = []
        if not mv_pairs:
            return recs

        values = [v for _, v in mv_pairs]
        mean_v = statistics.mean(values)
        stdev_v = statistics.pstdev(values) if len(values) > 1 else 0.0
        high_threshold = mean_v + _HIGH_VALUE_SIGMA * stdev_v

        for uid, mv in mv_pairs:
            if mv < strategy.min_value_threshold:
                recs.append((uid, f"below_min_threshold(value={mv:.4f})"))
            elif mv < mean_v - stdev_v:
                recs.append((uid, f"low_relative_value(value={mv:.4f},mean={mean_v:.4f})"))

        log.debug(
            "Generated %d compression recommendations (mean_mv=%.4f, high_threshold=%.4f)",
            len(recs),
            mean_v,
            high_threshold,
        )
        return recs


# ─── Section 5: Coordinator ─────────────────────────────────────────────────


class ArchivalValueCoordinator:
    """High-level façade that drives the archival-value pipeline.

    The coordinator wires together a :class:`SemanticCapitalAccount`, an
    :class:`ArchivalValueAnalyzer`, and the treaty-memory index.  It exposes
    a small, stable interface consumed by the orchestration layer.

    Parameters
    ----------
    account_id:
        Optional explicit account identifier; defaults to a fresh UUID4.

    Examples
    --------
    ::

        coordinator = ArchivalValueCoordinator()
        unit = coordinator.assess_entry({"importance": 0.9, "novelty_score": 0.8})
        coordinator.accrue_capital("ep-001", 2.5, "synthesis")
        report = coordinator.report()
    """

    def __init__(self, account_id: str | None = None) -> None:
        self._account = SemanticCapitalAccount(
            account_id=account_id or str(uuid.uuid4())
        )
        self._analyzer = ArchivalValueAnalyzer()
        self._index: Any = None  # injected later via attach_index()
        self._compression_log: list[dict] = []
        log.info(
            "ArchivalValueCoordinator initialised (account=%s)",
            self._account.account_id[:8],
        )

    # ── public interface ─────────────────────────────────────────────────

    def attach_index(self, index: Any) -> None:
        """Attach a treaty-memory index for entry-metadata lookups.

        Parameters
        ----------
        index:
            Any object that responds to ``index.get_entry(entry_id) -> dict``.
        """
        self._index = index
        log.debug("Index attached: %r", index)

    def assess_entry(self, entry: dict) -> CapitalUnit:
        """Assess *entry* and deposit the resulting :class:`CapitalUnit`.

        The entry's archival value is computed, converted to a capital unit,
        and immediately deposited into the internal account.

        Parameters
        ----------
        entry:
            Entry metadata dict.  Recognised keys: ``"entry_id"``,
            ``"timestamp"``, ``"ref_count"``, ``"novelty_score"``,
            ``"importance"``, ``"capital_type"``.

        Returns
        -------
        CapitalUnit
            The newly created and deposited unit.
        """
        entry_id = str(entry.get("entry_id", uuid.uuid4()))
        capital_type = str(entry.get("capital_type", "relevance"))
        rate = semantic_capital_rate(entry)
        amount = compute_archival_value(entry, {}) * rate

        unit = CapitalUnit(
            unit_id=str(uuid.uuid4()),
            source_entry_id=entry_id,
            capital_type=capital_type,
            amount=max(amount, 0.0),
            accrued_at=time.time(),
        )
        self._account.deposit(unit)
        return unit

    def run_compression(
        self,
        strategy: CompressionStrategy,
        index: Any = None,
    ) -> int:
        """Execute a compression pass using *strategy*.

        Entries flagged by the analysis engine are withdrawn from the
        account and logged.  The number of withdrawn units is returned.

        Parameters
        ----------
        strategy:
            The :class:`CompressionStrategy` to apply.
        index:
            Optional index override; falls back to the attached index.

        Returns
        -------
        int
            Number of capital units removed during this pass.
        """
        effective_index = index or self._index
        report = self._analyzer.analyze(
            self._account, effective_index, strategy=strategy
        )

        removed = 0
        for unit_id, reason in report.compression_recommendations:
            withdrawn = self._account.withdraw(unit_id)
            if withdrawn is not None:
                removed += 1
                self._compression_log.append(
                    {
                        "unit_id": unit_id,
                        "reason": reason,
                        "timestamp": time.time(),
                        "amount": withdrawn.amount,
                    }
                )

        log.info(
            "Compression pass complete: strategy=%r removed=%d remaining_balance=%.4f",
            strategy.name,
            removed,
            self._account.balance(),
        )
        return removed

    def accrue_capital(
        self,
        episode_id: str,
        amount: float,
        capital_type: str = "synthesis",
    ) -> CapitalUnit:
        """Directly accrue *amount* of *capital_type* capital for *episode_id*.

        Useful when the orchestration layer has already computed the accrual
        amount externally and just needs to record it.

        Parameters
        ----------
        episode_id:
            Source episode identifier.
        amount:
            Non-negative capital amount to accrue.
        capital_type:
            Category label for the capital.

        Returns
        -------
        CapitalUnit
            The deposited unit.
        """
        unit = CapitalUnit(
            unit_id=str(uuid.uuid4()),
            source_entry_id=episode_id,
            capital_type=capital_type,
            amount=max(float(amount), 0.0),
            accrued_at=time.time(),
        )
        self._account.deposit(unit)
        return unit

    def report(self) -> dict:
        """Generate a comprehensive status report.

        Returns
        -------
        dict
            Keys: ``"account_audit"``, ``"analysis_report"``,
            ``"compression_log_size"``.
        """
        audit = self._account.audit()
        analysis = self._analyzer.analyze(self._account, self._index)
        return {
            "account_audit": audit,
            "analysis_report": {
                "report_id": analysis.report_id,
                "total_capital": analysis.total_capital,
                "n_marginal_values": len(analysis.marginal_values),
                "n_recommendations": len(analysis.compression_recommendations),
                "depreciation_forecast": list(analysis.depreciation_forecast),
                "generated_at": analysis.generated_at,
                "schema_version": analysis.schema_version,
            },
            "compression_log_size": len(self._compression_log),
        }


# ─── Section 6: Helper Functions ────────────────────────────────────────────


def compute_archival_value(entry: dict, context: dict) -> float:
    """Compute the archival value of *entry* in the given *context*.

    The archival value is a scalar in [0, ∞) that represents how much
    information the entry contributes to the treaty-memory corpus.  Higher
    values indicate entries that should be retained longer and compressed last.

    The computation weighs four signals:

    1. **Temporal recency** – recent entries are worth more.
    2. **Structural importance** – entries marked important score higher.
    3. **Novelty** – entries with low overlap to the existing corpus score higher.
    4. **Reference density** – entries cited by many other entries score higher.

    Parameters
    ----------
    entry:
        Dict with optional keys ``"timestamp"``, ``"importance"``,
        ``"novelty_score"``, ``"ref_count"``.
    context:
        Optional context dict; currently unused but reserved for future
        corpus-level signals.

    Returns
    -------
    float
        Non-negative archival value.
    """
    now = time.time()
    ts = float(entry.get("timestamp", now))
    elapsed = max(now - ts, 0.0)

    recency = value_decay(1.0, elapsed, _DEFAULT_DECAY_RATE)
    importance = float(entry.get("importance", 1.0))
    novelty = float(entry.get("novelty_score", 0.5))
    ref_count = float(entry.get("ref_count", 1))

    # Reference density uses a log scale to avoid dominance by high-traffic entries.
    ref_density = math.log1p(ref_count)

    # Weighted combination (weights are intentionally exposed as constants
    # so that downstream calibration experiments can tune them easily).
    _W_RECENCY = 0.30
    _W_IMPORTANCE = 0.35
    _W_NOVELTY = 0.20
    _W_REF = 0.15

    value = (
        _W_RECENCY * recency
        + _W_IMPORTANCE * importance
        + _W_NOVELTY * novelty
        + _W_REF * ref_density
    )
    return max(value, 0.0)


def apply_compression(
    entries: list[dict],
    strategy: CompressionStrategy,
) -> list[dict]:
    """Apply *strategy* to *entries* and return the compressed list.

    Entries whose computed archival value falls below
    ``strategy.min_value_threshold`` are removed (``truncate``),
    replaced by a stub summary (``summarise``), or clustered
    (``cluster`` / ``delta_encode``).

    Parameters
    ----------
    entries:
        List of entry dicts.
    strategy:
        The :class:`CompressionStrategy` to apply.

    Returns
    -------
    list[dict]
        The compressed entry list.  May be shorter than *entries*.
    """
    if not entries:
        return []

    analyzer = ArchivalValueAnalyzer()
    surviving: list[dict] = []

    for entry in entries:
        mv = analyzer.marginal_value(entry)
        if mv >= strategy.min_value_threshold:
            surviving.append(entry)
        else:
            # Policy-dependent handling of low-value entries.
            if strategy.policy == _POLICY_SUMMARISE:
                stub = {
                    "entry_id": entry.get("entry_id", str(uuid.uuid4())),
                    "summary_stub": True,
                    "original_value": mv,
                    "timestamp": entry.get("timestamp", time.time()),
                }
                surviving.append(stub)
            elif strategy.policy in (_POLICY_CLUSTER, _POLICY_DELTA_ENCODE):
                # For clustering/delta-encoding keep a lightweight reference.
                stub = {
                    "entry_id": entry.get("entry_id", str(uuid.uuid4())),
                    "compressed": True,
                    "policy": strategy.policy,
                    "original_value": mv,
                }
                surviving.append(stub)
            # _POLICY_TRUNCATE: discard silently (entry not appended).

    log.debug(
        "apply_compression: %d → %d entries (policy=%s)",
        len(entries),
        len(surviving),
        strategy.policy,
    )
    return surviving


def semantic_capital_rate(episode: dict) -> float:
    """Compute the semantic-capital accrual rate for *episode*.

    The rate is the number of capital units earned per unit of archival
    value.  It starts at :data:`_BASE_ACCRUAL_RATE` and is modified by:

    * A boost if the episode is referenced by multiple downstream nodes.
    * A penalty if the episode exhibits high lexical overlap with previous
      episodes.

    Parameters
    ----------
    episode:
        Dict with optional keys ``"ref_count"``, ``"overlap_ratio"``.

    Returns
    -------
    float
        Accrual rate ≥ 0.
    """
    rate = _BASE_ACCRUAL_RATE
    ref_count = float(episode.get("ref_count", 1))
    if ref_count > 1:
        rate *= _MULTI_REFERENCE_BOOST

    overlap = float(episode.get("overlap_ratio", 0.0))
    if overlap > _REDUNDANCY_THRESHOLD:
        rate *= _REDUNDANCY_PENALTY

    return max(rate, 0.0)


def value_decay(initial: float, elapsed: float, decay_rate: float) -> float:
    """Compute the decayed value after *elapsed* seconds.

    Uses a standard exponential-decay model::

        decayed = initial × exp(−decay_rate × elapsed)

    Parameters
    ----------
    initial:
        Starting value.
    elapsed:
        Time elapsed in seconds (must be ≥ 0).
    decay_rate:
        Non-negative decay constant (per second).

    Returns
    -------
    float
        Decayed value ≥ 0.
    """
    if elapsed < 0:
        raise ValueError(f"elapsed must be ≥ 0, got {elapsed!r}")
    if decay_rate < 0:
        raise ValueError(f"decay_rate must be ≥ 0, got {decay_rate!r}")
    return initial * math.exp(-decay_rate * elapsed)


def marginal_relevance_score(
    candidate: dict,
    already_selected: list[dict],
    lambda_: float = 0.5,
) -> float:
    """Compute a Maximal Marginal Relevance (MMR) style score.

    This implements a simplified version of the MMR criterion often used in
    summarisation: a candidate entry is rewarded for high intrinsic value
    and penalised for similarity to already-selected entries.

    Parameters
    ----------
    candidate:
        The entry being evaluated.
    already_selected:
        Entries already committed to the retained set.
    lambda_:
        Trade-off parameter in [0, 1].  Higher values favour relevance;
        lower values favour diversity.

    Returns
    -------
    float
        MMR score.
    """
    analyzer = ArchivalValueAnalyzer()
    relevance = analyzer.marginal_value(candidate)

    if not already_selected:
        return relevance

    # Approximate similarity as overlap of novelty scores (cheap proxy).
    cand_novelty = float(candidate.get("novelty_score", 0.5))
    max_sim = max(
        1.0 - abs(cand_novelty - float(s.get("novelty_score", 0.5)))
        for s in already_selected
    )
    return lambda_ * relevance - (1.0 - lambda_) * max_sim


def normalise_capital_vector(units: list[CapitalUnit]) -> list[float]:
    """Normalise a list of capital unit amounts to the unit simplex.

    Returns a probability-like vector summing to 1.0.  If all amounts are
    zero the uniform distribution is returned.

    Parameters
    ----------
    units:
        List of :class:`CapitalUnit` objects.

    Returns
    -------
    list[float]
        Normalised amounts, same order as *units*.
    """
    amounts = [u.amount for u in units]
    total = math.fsum(amounts)
    if total == 0.0:
        n = len(amounts) or 1
        return [1.0 / n] * len(amounts)
    return [a / total for a in amounts]


def entropy_of_corpus(units: list[CapitalUnit]) -> float:
    """Compute the Shannon entropy of the capital distribution.

    Higher entropy indicates a more diverse corpus; lower entropy indicates
    concentration of capital in a few high-value entries.

    Parameters
    ----------
    units:
        List of :class:`CapitalUnit` objects.

    Returns
    -------
    float
        Shannon entropy in bits (base-2 logarithm).
    """
    probs = normalise_capital_vector(units)
    return -math.fsum(
        p * math.log2(p + _ENTROPY_EPSILON) for p in probs if p > 0
    )


def compression_efficiency(policy: str) -> float:
    """Return the information-retention efficiency factor for *policy*.

    Efficiency is defined as the fraction of semantic capital that is
    *preserved* (not lost) when the policy is applied to a low-value entry.

    Parameters
    ----------
    policy:
        One of the ``_POLICY_*`` constants.

    Returns
    -------
    float
        Efficiency in (0, 1].
    """
    _EFFICIENCY_MAP: dict[str, float] = {
        _POLICY_TRUNCATE: 0.50,
        _POLICY_SUMMARISE: 0.75,
        _POLICY_CLUSTER: 0.85,
        _POLICY_DELTA_ENCODE: 0.92,
    }
    return _EFFICIENCY_MAP.get(policy, 0.60)


def capital_weighted_centroid(units: list[CapitalUnit]) -> dict[str, float]:
    """Compute a capital-weighted centroid over the per-type distribution.

    The centroid is a dict mapping each ``capital_type`` to its normalised
    share of the total balance.  This is useful for monitoring whether the
    account's capital is well-diversified across types.

    Parameters
    ----------
    units:
        List of :class:`CapitalUnit` objects.

    Returns
    -------
    dict[str, float]
        Map from capital type to normalised weight in [0, 1].
    """
    type_sums: dict[str, float] = {}
    for u in units:
        type_sums[u.capital_type] = type_sums.get(u.capital_type, 0.0) + u.amount

    total = math.fsum(type_sums.values())
    if total == 0.0:
        return {k: 0.0 for k in type_sums}
    return {k: v / total for k, v in type_sums.items()}


def forecast_depreciation_schedule(
    initial_balance: float,
    periods: int,
    epoch_seconds: float = 3600.0,
    decay_rate: float = _DEFAULT_DECAY_RATE,
) -> list[dict]:
    """Build a detailed depreciation schedule for *initial_balance*.

    Each period entry contains the period number, the projected balance,
    the absolute loss relative to the previous period, and the cumulative
    loss from period 0.

    Parameters
    ----------
    initial_balance:
        Starting capital balance.
    periods:
        Number of periods to forecast.
    epoch_seconds:
        Wall-clock seconds per epoch.
    decay_rate:
        Exponential decay constant.

    Returns
    -------
    list[dict]
        One dict per period with keys ``"period"``, ``"balance"``,
        ``"period_loss"``, ``"cumulative_loss"``.
    """
    schedule: list[dict] = []
    prev_balance = initial_balance
    for p in range(1, periods + 1):
        elapsed = epoch_seconds * p
        balance = value_decay(initial_balance, elapsed, decay_rate)
        period_loss = prev_balance - balance
        cumulative_loss = initial_balance - balance
        schedule.append(
            {
                "period": p,
                "balance": balance,
                "period_loss": period_loss,
                "cumulative_loss": cumulative_loss,
            }
        )
        prev_balance = balance
    return schedule


# ─── Section 7: Private Utilities ───────────────────────────────────────────


def _default_strategy() -> CompressionStrategy:
    """Return a sensible default :class:`CompressionStrategy`."""
    return CompressionStrategy(
        strategy_id=str(uuid.uuid4()),
        name="default_truncation",
        target_ratio=0.70,
        min_value_threshold=_MIN_ARCHIVAL_VALUE,
        policy=_POLICY_TRUNCATE,
    )


def _make_test_entry(
    *,
    entry_id: str | None = None,
    importance: float = 1.0,
    novelty_score: float = 0.5,
    ref_count: int = 1,
    timestamp: float | None = None,
) -> dict:
    """Create a synthetic entry dict for testing purposes."""
    return {
        "entry_id": entry_id or str(uuid.uuid4()),
        "importance": importance,
        "novelty_score": novelty_score,
        "ref_count": ref_count,
        "timestamp": timestamp or time.time(),
    }


def _summarise_report(report: ValueAnalysisReport) -> str:
    """Format a concise human-readable summary of a :class:`ValueAnalysisReport`."""
    lines = [
        f"  report_id          : {report.report_id}",
        f"  total_capital      : {report.total_capital:.6f}",
        f"  marginal_values    : {len(report.marginal_values)} entries",
        f"  recommendations    : {len(report.compression_recommendations)} entries",
        f"  depreciation[0]    : {report.depreciation_forecast[0]:.6f}" if report.depreciation_forecast else "  depreciation       : (empty)",
        f"  schema_version     : {report.schema_version}",
    ]
    return "\n".join(lines)


# ─── Section 8: Module-Level Smoke Test ─────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    print(f"[smoke] {__file__}")

    # ── 1. Basic capital unit creation ───────────────────────────────────
    unit_a = CapitalUnit(
        unit_id=str(uuid.uuid4()),
        source_entry_id="entry-001",
        capital_type="relevance",
        amount=3.14,
        accrued_at=time.time(),
    )
    assert unit_a.amount == 3.14, "CapitalUnit amount mismatch"
    print("[smoke] CapitalUnit OK")

    # ── 2. Account deposit / withdraw / balance ───────────────────────────
    account = SemanticCapitalAccount()
    account.deposit(unit_a)
    assert abs(account.balance() - 3.14) < 1e-9, "Balance after deposit wrong"
    withdrawn = account.withdraw(unit_a.unit_id)
    assert withdrawn is not None, "Withdraw returned None unexpectedly"
    assert account.balance() == 0.0, "Balance should be 0 after full withdrawal"
    print("[smoke] SemanticCapitalAccount OK")

    # ── 3. Compression strategy validation ───────────────────────────────
    strat = CompressionStrategy(
        strategy_id=str(uuid.uuid4()),
        name="test_strategy",
        target_ratio=0.5,
        min_value_threshold=0.1,
        policy=_POLICY_SUMMARISE,
    )
    assert strat.policy == _POLICY_SUMMARISE, "Policy mismatch"
    print("[smoke] CompressionStrategy OK")

    # ── 4. Value decay ────────────────────────────────────────────────────
    decayed = value_decay(100.0, 1_000_000.0, _DEFAULT_DECAY_RATE)
    assert 0.0 < decayed < 100.0, "Decay result out of range"
    no_decay = value_decay(100.0, 0.0, _DEFAULT_DECAY_RATE)
    assert abs(no_decay - 100.0) < 1e-9, "Zero-elapsed decay should be identity"
    print("[smoke] value_decay OK")

    # ── 5. compute_archival_value ─────────────────────────────────────────
    entry = _make_test_entry(importance=0.8, novelty_score=0.6, ref_count=3)
    av = compute_archival_value(entry, {})
    assert av > 0.0, "Archival value should be positive"
    print(f"[smoke] compute_archival_value={av:.4f} OK")

    # ── 6. semantic_capital_rate ──────────────────────────────────────────
    rate_solo = semantic_capital_rate({"ref_count": 1, "overlap_ratio": 0.0})
    rate_multi = semantic_capital_rate({"ref_count": 5, "overlap_ratio": 0.0})
    rate_redundant = semantic_capital_rate({"ref_count": 1, "overlap_ratio": 0.9})
    assert rate_multi > rate_solo, "Multi-ref should boost rate"
    assert rate_redundant < rate_solo, "High overlap should penalise rate"
    print("[smoke] semantic_capital_rate OK")

    # ── 7. ArchivalValueAnalyzer.marginal_value ───────────────────────────
    analyzer = ArchivalValueAnalyzer()
    mv = analyzer.marginal_value(_make_test_entry(importance=1.0, novelty_score=1.0, ref_count=10))
    assert mv > 0.0, "Marginal value should be positive"
    print(f"[smoke] ArchivalValueAnalyzer.marginal_value={mv:.4f} OK")

    # ── 8. Depreciation curve ─────────────────────────────────────────────
    account2 = SemanticCapitalAccount()
    for i in range(5):
        account2.deposit(
            CapitalUnit(
                unit_id=str(uuid.uuid4()),
                source_entry_id=f"entry-{i:03d}",
                capital_type="synthesis",
                amount=float(i + 1),
                accrued_at=time.time(),
            )
        )
    curve = analyzer.depreciation_curve(account2, periods=6)
    assert len(curve) == 6, "Curve length mismatch"
    assert all(c >= 0 for c in curve), "Curve must be non-negative"
    print(f"[smoke] depreciation_curve[0]={curve[0]:.6f} OK")

    # ── 9. Full analysis report ───────────────────────────────────────────
    report = analyzer.analyze(account2, None, strategy=strat)
    assert report.total_capital > 0.0, "Report capital should be positive"
    print("[smoke] ValueAnalysisReport:")
    print(_summarise_report(report))

    # ── 10. ArchivalValueCoordinator ─────────────────────────────────────
    coordinator = ArchivalValueCoordinator()
    for i in range(8):
        coordinator.assess_entry(
            _make_test_entry(
                importance=0.5 + i * 0.05,
                novelty_score=0.3 + i * 0.08,
                ref_count=i + 1,
            )
        )
    coordinator.accrue_capital("ep-999", 10.0, "treaty")
    full_report = coordinator.report()
    assert full_report["account_audit"]["unit_count"] == 9, "Expected 9 units in account"
    print(f"[smoke] coordinator.report balance={full_report['account_audit']['balance']:.4f} OK")

    removed = coordinator.run_compression(strat)
    print(f"[smoke] coordinator.run_compression removed={removed} OK")

    # ── 11. apply_compression helper ─────────────────────────────────────
    test_entries = [_make_test_entry(importance=i * 0.1) for i in range(10)]
    compressed = apply_compression(test_entries, strat)
    assert len(compressed) <= len(test_entries), "Compression should not grow the list"
    print(f"[smoke] apply_compression {len(test_entries)}→{len(compressed)} entries OK")

    # ── 12. Entropy & centroid ────────────────────────────────────────────
    all_units = account2.live_units()
    if all_units:
        ent = entropy_of_corpus(all_units)
        centroid = capital_weighted_centroid(all_units)
        assert ent >= 0.0, "Entropy must be non-negative"
        assert abs(sum(centroid.values()) - 1.0) < 1e-9, "Centroid must sum to 1"
        print(f"[smoke] entropy={ent:.4f} centroid_types={list(centroid)} OK")

    # ── 13. Depreciation schedule ─────────────────────────────────────────
    sched = forecast_depreciation_schedule(100.0, 4)
    assert len(sched) == 4, "Schedule length mismatch"
    assert sched[-1]["cumulative_loss"] > sched[0]["cumulative_loss"], "Cumulative loss should grow"
    print(f"[smoke] forecast_depreciation_schedule periods=4 OK")

    # ── 14. MMR score ─────────────────────────────────────────────────────
    cand = _make_test_entry(novelty_score=0.9)
    sel = [_make_test_entry(novelty_score=0.1)]
    mmr = marginal_relevance_score(cand, sel, lambda_=0.7)
    print(f"[smoke] marginal_relevance_score={mmr:.4f} OK")

    print("[smoke] PASS")
    sys.exit(0)
