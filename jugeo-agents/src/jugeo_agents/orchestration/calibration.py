"""Trust Calibration — per-model, per-task empirical trust calibration.

Tracks how accurate each (model, task_type) pair is over time and converts
that empirical evidence into calibrated :class:`TrustLevel` values.  When
insufficient data exists, falls back to :class:`ModelTrustDefaults` which
maps model-name patterns to conservative priors.

Calibrations decay toward the prior over time via :class:`CalibrationDecay`
so that stale measurements don't dominate routing decisions indefinitely.
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from jugeo_agents.types import (
    AgentOutput,
    CalibrationRecord,
    FactualClaim,
    TrustLevel,
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

_Key = tuple[str, str]  # (model, task_type)


# ---------------------------------------------------------------------------
# 1. CalibrationProfile
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CalibrationProfile:
    """Aggregated statistics for a single (model, task_type) pair.

    Instances are produced by :meth:`CalibrationEngine.profile_for` and
    encapsulate all of the empirical evidence collected so far.
    """

    model: str
    task_type: str
    total_observations: int
    accuracy_rate: float
    hallucination_rate: float
    tool_reliability: float
    average_latency_ms: float
    trust_level: TrustLevel
    last_updated: float

    # -- helpers -------------------------------------------------------------

    @property
    def is_reliable(self) -> bool:
        """True when accuracy is above 80 % and hallucination below 10 %."""
        return self.accuracy_rate >= 0.80 and self.hallucination_rate < 0.10

    @property
    def key(self) -> _Key:
        return (self.model, self.task_type)

    def __str__(self) -> str:
        return (
            f"{self.model}/{self.task_type}: "
            f"acc={self.accuracy_rate:.1%} hall={self.hallucination_rate:.1%} "
            f"n={self.total_observations} trust={self.trust_level.name}"
        )


# ---------------------------------------------------------------------------
# 2. CalibrationReport
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CalibrationReport:
    """Snapshot report produced by :meth:`CalibrationEngine.report`."""

    models_calibrated: int
    task_types_seen: int
    best_performers: list[tuple[str, str, float]]   # (model, task, accuracy)
    worst_performers: list[tuple[str, str, float]]
    recommendations: list[str]

    def __str__(self) -> str:
        lines = [
            f"Calibration Report — {self.models_calibrated} model(s), "
            f"{self.task_types_seen} task type(s)",
            "",
        ]
        if self.best_performers:
            lines.append("Best performers:")
            for model, task, acc in self.best_performers:
                lines.append(f"  ✅ {model}/{task}: {acc:.1%}")
        if self.worst_performers:
            lines.append("Worst performers:")
            for model, task, acc in self.worst_performers:
                lines.append(f"  ⚠️  {model}/{task}: {acc:.1%}")
        if self.recommendations:
            lines.append("")
            lines.append("Recommendations:")
            for rec in self.recommendations:
                lines.append(f"  • {rec}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. ModelTrustDefaults — pattern-based prior trust for known models
# ---------------------------------------------------------------------------

class ModelTrustDefaults:
    """Default trust levels for known model families.

    When the :class:`CalibrationEngine` has fewer than *min_observations*
    records for a (model, task_type) pair it delegates here.  Patterns are
    matched against the lowered model name using :func:`re.search`.
    """

    # Ordered from most specific to least specific.
    _PATTERNS: list[tuple[str, TrustLevel]] = [
        # -- frontier ---------------------------------------------------
        (r"claude-opus",          TrustLevel.STRONG_MODEL_GENERATED),
        (r"gpt-4o(?!-mini)",      TrustLevel.STRONG_MODEL_GENERATED),
        (r"gpt-4-turbo",         TrustLevel.STRONG_MODEL_GENERATED),
        (r"gpt-4\.?5",           TrustLevel.STRONG_MODEL_GENERATED),
        (r"gemini-pro",          TrustLevel.STRONG_MODEL_GENERATED),
        (r"gemini-ultra",        TrustLevel.STRONG_MODEL_GENERATED),
        (r"gemini-2",            TrustLevel.STRONG_MODEL_GENERATED),
        (r"o1-preview",          TrustLevel.STRONG_MODEL_GENERATED),
        (r"o1-mini",             TrustLevel.STRONG_MODEL_GENERATED),
        (r"o3",                  TrustLevel.STRONG_MODEL_GENERATED),
        # -- mid-tier ---------------------------------------------------
        (r"claude-sonnet",       TrustLevel.STRONG_MODEL_GENERATED),
        (r"gpt-4o-mini",         TrustLevel.STRONG_MODEL_GENERATED),
        (r"claude-haiku",        TrustLevel.STRONG_MODEL_GENERATED),
        (r"gemini-flash",        TrustLevel.STRONG_MODEL_GENERATED),
        (r"command-r\+?",        TrustLevel.STRONG_MODEL_GENERATED),
        # -- small / weak -----------------------------------------------
        (r"gpt-3\.5",           TrustLevel.WEAK_MODEL_GENERATED),
        (r"llama-.*-8b",        TrustLevel.WEAK_MODEL_GENERATED),
        (r"llama-.*-7b",        TrustLevel.WEAK_MODEL_GENERATED),
        (r"mistral-7b",         TrustLevel.WEAK_MODEL_GENERATED),
        (r"phi-3-mini",         TrustLevel.WEAK_MODEL_GENERATED),
        (r"phi-2",              TrustLevel.WEAK_MODEL_GENERATED),
        (r"gemma-2b",           TrustLevel.WEAK_MODEL_GENERATED),
        (r"gemma-7b",           TrustLevel.WEAK_MODEL_GENERATED),
        # -- mid-sized open models that beat "small" thresholds ----------
        (r"llama-.*-70b",       TrustLevel.STRONG_MODEL_GENERATED),
        (r"mixtral",            TrustLevel.STRONG_MODEL_GENERATED),
        (r"llama-.*-405b",      TrustLevel.STRONG_MODEL_GENERATED),
    ]

    # Pre-compiled for speed.
    _COMPILED: list[tuple[re.Pattern[str], TrustLevel]] = [
        (re.compile(pat, re.IGNORECASE), lvl) for pat, lvl in _PATTERNS
    ]

    @classmethod
    def default_trust(cls, model: str) -> TrustLevel:
        """Return the default trust level for *model*.

        Matches the model name (case-insensitive) against known patterns.
        Returns :attr:`TrustLevel.UNGROUNDED_CLAIM` if nothing matches.
        """
        lowered = model.lower()
        for regex, trust in cls._COMPILED:
            if regex.search(lowered):
                return trust
        return TrustLevel.UNGROUNDED_CLAIM


# ---------------------------------------------------------------------------
# 4. CalibrationDecay — time-based confidence decay
# ---------------------------------------------------------------------------

class CalibrationDecay:
    """Exponential decay toward the prior for stale calibration data.

    Uses a half-life model: after *half_life_days* with no new observations
    the effective observation count is halved.  This means the calibrated
    trust level gradually falls back to :class:`ModelTrustDefaults`.
    """

    _SECONDS_PER_DAY: float = 86_400.0

    @classmethod
    def apply_decay(
        cls,
        profile: CalibrationProfile,
        now: float | None = None,
        half_life_days: float = 30.0,
    ) -> CalibrationProfile:
        """Return a new profile with decay applied.

        Parameters
        ----------
        profile:
            The source profile.
        now:
            Current epoch timestamp; defaults to ``time.time()``.
        half_life_days:
            The number of days after which effective observation count halves.
        """
        if now is None:
            now = time.time()

        elapsed_days = (now - profile.last_updated) / cls._SECONDS_PER_DAY
        if elapsed_days <= 0 or half_life_days <= 0:
            return profile

        # Exponential decay factor ∈ (0, 1]
        decay = math.pow(0.5, elapsed_days / half_life_days)

        # Effective observation count after decay
        effective_n = max(1, int(profile.total_observations * decay))

        # Blend accuracy toward 0.5 (maximum-entropy prior)
        blended_accuracy = profile.accuracy_rate * decay + 0.5 * (1.0 - decay)
        blended_hallucination = profile.hallucination_rate * decay
        blended_tool = profile.tool_reliability * decay + 0.5 * (1.0 - decay)

        # Re-derive trust from the decayed accuracy
        new_trust = _accuracy_to_trust(
            blended_accuracy,
            blended_hallucination,
            blended_tool,
            effective_n,
            profile.model,
        )

        return CalibrationProfile(
            model=profile.model,
            task_type=profile.task_type,
            total_observations=effective_n,
            accuracy_rate=blended_accuracy,
            hallucination_rate=blended_hallucination,
            tool_reliability=blended_tool,
            average_latency_ms=profile.average_latency_ms,
            trust_level=new_trust,
            last_updated=profile.last_updated,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _accuracy_to_trust(
    accuracy: float,
    hallucination_rate: float,
    tool_reliability: float,
    n_obs: int,
    model: str,
    min_observations: int = 10,
) -> TrustLevel:
    """Map empirical metrics to a :class:`TrustLevel`.

    The mapping is conservative: high hallucination rates drag trust down
    faster than high accuracy pulls it up.  If *n_obs* is below
    *min_observations* we return the model-family default.
    """
    if n_obs < min_observations:
        return ModelTrustDefaults.default_trust(model)

    # Hallucination dominates — any non-trivial hallucination rate caps trust.
    if hallucination_rate >= 0.30:
        return TrustLevel.SELF_CONTRADICTED
    if hallucination_rate >= 0.15:
        return TrustLevel.UNGROUNDED_CLAIM
    if hallucination_rate >= 0.05:
        return TrustLevel.WEAK_MODEL_GENERATED

    # High tool-verification rate can boost trust.
    if tool_reliability >= 0.80 and accuracy >= 0.95:
        return TrustLevel.TOOL_VERIFIED
    if tool_reliability >= 0.60 and accuracy >= 0.90:
        return TrustLevel.TOOL_EXECUTED

    # Pure accuracy tiers.
    if accuracy >= 0.95:
        return TrustLevel.CROSS_AGENT_CONFIRMED
    if accuracy >= 0.85:
        return TrustLevel.STRONG_MODEL_GENERATED
    if accuracy >= 0.70:
        return TrustLevel.WEAK_MODEL_GENERATED
    if accuracy >= 0.50:
        return TrustLevel.UNGROUNDED_CLAIM
    return TrustLevel.SELF_CONTRADICTED


# ---------------------------------------------------------------------------
# 5. CalibrationEngine
# ---------------------------------------------------------------------------

class CalibrationEngine:
    """Core calibration system that accumulates observations and produces
    per-model, per-task trust profiles.

    Usage::

        engine = CalibrationEngine(min_observations=10)
        engine.record(some_calibration_record)
        trust = engine.trust_for("gpt-4o", "summarization")
    """

    def __init__(self, min_observations: int = 10) -> None:
        self._min_observations = min_observations
        self._records: dict[_Key, list[CalibrationRecord]] = defaultdict(list)

    # -- recording ----------------------------------------------------------

    def record(self, record: CalibrationRecord) -> None:
        """Append a single calibration observation."""
        key: _Key = (record.model, record.task_type)
        self._records[key].append(record)

    def record_batch(self, records: list[CalibrationRecord]) -> None:
        """Append many observations at once."""
        for rec in records:
            self.record(rec)

    def record_from_output(
        self,
        output: AgentOutput,
        was_accurate: bool,
        was_hallucination: bool = False,
    ) -> None:
        """Create and record a :class:`CalibrationRecord` from an agent output.

        Convenience wrapper that extracts model, task_type, latency, and
        tool-verification status from the :class:`AgentOutput` directly.
        """
        was_tool_verified = output.trust >= TrustLevel.TOOL_VERIFIED
        task_type = output.subtask or output.role or "unknown"
        latency_ms = 0.0
        if "latency_ms" in output.metadata:
            latency_ms = float(output.metadata["latency_ms"])

        # Record one observation per claim, or a single record if no claims.
        claims = output.claims or [
            FactualClaim(text=output.output_text, source_agent=output.agent_id)
        ]
        for claim in claims:
            rec = CalibrationRecord(
                model=output.model,
                task_type=task_type,
                claim=claim,
                was_accurate=was_accurate,
                was_hallucination=was_hallucination,
                was_tool_verified=was_tool_verified,
                latency_ms=latency_ms,
            )
            self.record(rec)

    # -- querying -----------------------------------------------------------

    def trust_for(self, model: str, task_type: str) -> TrustLevel:
        """Return the calibrated trust level for *(model, task_type)*.

        Falls back to :meth:`ModelTrustDefaults.default_trust` when
        fewer than *min_observations* records are available.
        """
        key: _Key = (model, task_type)
        records = self._records.get(key, [])
        if len(records) < self._min_observations:
            return ModelTrustDefaults.default_trust(model)
        return self._compute_trust(records, model)

    def profile_for(self, model: str, task_type: str) -> CalibrationProfile:
        """Build a full :class:`CalibrationProfile` for *(model, task_type)*.

        Always returns a profile — even when insufficient data exists the
        profile will reflect the default trust and zero rates.
        """
        key: _Key = (model, task_type)
        records = self._records.get(key, [])
        return self._build_profile(model, task_type, records)

    def all_profiles(self) -> list[CalibrationProfile]:
        """Return profiles for every (model, task_type) pair observed."""
        return [
            self._build_profile(model, task_type, recs)
            for (model, task_type), recs in sorted(self._records.items())
        ]

    def best_model_for(self, task_type: str) -> str | None:
        """Return the model with the highest accuracy for *task_type*.

        Only considers pairs with at least *min_observations* data.
        Returns ``None`` when no qualifying model exists.
        """
        best_model: str | None = None
        best_accuracy: float = -1.0
        for (model, tt), recs in self._records.items():
            if tt != task_type:
                continue
            if len(recs) < self._min_observations:
                continue
            acc = self._accuracy(recs)
            if acc > best_accuracy:
                best_accuracy = acc
                best_model = model
        return best_model

    def worst_models(
        self, threshold: float = 0.5
    ) -> list[CalibrationProfile]:
        """Return profiles for all (model, task_type) pairs below *threshold*.

        Only includes pairs with at least *min_observations* records.
        """
        results: list[CalibrationProfile] = []
        for (model, task_type), recs in self._records.items():
            if len(recs) < self._min_observations:
                continue
            acc = self._accuracy(recs)
            if acc < threshold:
                results.append(self._build_profile(model, task_type, recs))
        results.sort(key=lambda p: p.accuracy_rate)
        return results

    def report(self, top_n: int = 5) -> CalibrationReport:
        """Produce a :class:`CalibrationReport` summarising the engine state.

        Parameters
        ----------
        top_n:
            Number of best/worst performers to include.
        """
        profiles = self.all_profiles()
        qualified = [
            p for p in profiles
            if p.total_observations >= self._min_observations
        ]

        models_calibrated = len({p.model for p in qualified})
        task_types_seen = len({p.task_type for p in profiles})

        sorted_by_acc = sorted(qualified, key=lambda p: p.accuracy_rate, reverse=True)
        best = [
            (p.model, p.task_type, p.accuracy_rate) for p in sorted_by_acc[:top_n]
        ]
        worst = [
            (p.model, p.task_type, p.accuracy_rate)
            for p in sorted_by_acc[-top_n:] if p.accuracy_rate < 0.8
        ]

        recommendations = self._generate_recommendations(profiles, qualified)

        return CalibrationReport(
            models_calibrated=models_calibrated,
            task_types_seen=task_types_seen,
            best_performers=best,
            worst_performers=worst,
            recommendations=recommendations,
        )

    def summary(self) -> str:
        """Human-readable summary of the calibration engine state."""
        profiles = self.all_profiles()
        if not profiles:
            return "CalibrationEngine: no observations recorded."

        qualified = [
            p for p in profiles
            if p.total_observations >= self._min_observations
        ]

        total_obs = sum(p.total_observations for p in profiles)
        lines = [
            f"CalibrationEngine: {total_obs} total observation(s) across "
            f"{len(profiles)} (model, task) pair(s).",
            f"  Pairs with ≥{self._min_observations} observations: {len(qualified)}",
        ]

        if qualified:
            avg_acc = sum(p.accuracy_rate for p in qualified) / len(qualified)
            avg_hall = sum(p.hallucination_rate for p in qualified) / len(qualified)
            lines.append(f"  Mean accuracy:       {avg_acc:.1%}")
            lines.append(f"  Mean hallucination:  {avg_hall:.1%}")

            best = max(qualified, key=lambda p: p.accuracy_rate)
            worst = min(qualified, key=lambda p: p.accuracy_rate)
            lines.append(f"  Best:  {best}")
            lines.append(f"  Worst: {worst}")

        return "\n".join(lines)

    # -- internal -----------------------------------------------------------

    @staticmethod
    def _accuracy(records: list[CalibrationRecord]) -> float:
        if not records:
            return 0.0
        return sum(1 for r in records if r.was_accurate) / len(records)

    @staticmethod
    def _hallucination_rate(records: list[CalibrationRecord]) -> float:
        if not records:
            return 0.0
        return sum(1 for r in records if r.was_hallucination) / len(records)

    @staticmethod
    def _tool_reliability(records: list[CalibrationRecord]) -> float:
        tool_records = [r for r in records if r.was_tool_verified]
        if not tool_records:
            return 0.0
        return sum(1 for r in tool_records if r.was_accurate) / len(tool_records)

    @staticmethod
    def _average_latency(records: list[CalibrationRecord]) -> float:
        latencies = [r.latency_ms for r in records if r.latency_ms > 0]
        if not latencies:
            return 0.0
        return sum(latencies) / len(latencies)

    def _compute_trust(
        self, records: list[CalibrationRecord], model: str
    ) -> TrustLevel:
        return _accuracy_to_trust(
            accuracy=self._accuracy(records),
            hallucination_rate=self._hallucination_rate(records),
            tool_reliability=self._tool_reliability(records),
            n_obs=len(records),
            model=model,
            min_observations=self._min_observations,
        )

    def _build_profile(
        self,
        model: str,
        task_type: str,
        records: list[CalibrationRecord],
    ) -> CalibrationProfile:
        n = len(records)
        if n == 0:
            return CalibrationProfile(
                model=model,
                task_type=task_type,
                total_observations=0,
                accuracy_rate=0.0,
                hallucination_rate=0.0,
                tool_reliability=0.0,
                average_latency_ms=0.0,
                trust_level=ModelTrustDefaults.default_trust(model),
                last_updated=time.time(),
            )

        acc = self._accuracy(records)
        hall = self._hallucination_rate(records)
        tool_rel = self._tool_reliability(records)
        avg_lat = self._average_latency(records)
        trust = self._compute_trust(records, model)
        last = max(r.timestamp for r in records)

        return CalibrationProfile(
            model=model,
            task_type=task_type,
            total_observations=n,
            accuracy_rate=acc,
            hallucination_rate=hall,
            tool_reliability=tool_rel,
            average_latency_ms=avg_lat,
            trust_level=trust,
            last_updated=last,
        )

    def _generate_recommendations(
        self,
        all_profiles: list[CalibrationProfile],
        qualified: list[CalibrationProfile],
    ) -> list[str]:
        """Produce actionable recommendations based on current calibration."""
        recs: list[str] = []

        # Flag under-observed pairs.
        under_observed = [
            p for p in all_profiles
            if p.total_observations < self._min_observations
            and p.total_observations > 0
        ]
        if under_observed:
            names = ", ".join(
                f"{p.model}/{p.task_type}" for p in under_observed[:5]
            )
            recs.append(
                f"Collect more observations for {names} "
                f"(need ≥{self._min_observations})."
            )

        # Flag high-hallucination models.
        hallucinators = [p for p in qualified if p.hallucination_rate >= 0.10]
        for p in hallucinators:
            recs.append(
                f"{p.model}/{p.task_type} has {p.hallucination_rate:.0%} "
                f"hallucination rate — consider adding tool verification."
            )

        # Flag low-accuracy models.
        low_accuracy = [p for p in qualified if p.accuracy_rate < 0.70]
        for p in low_accuracy:
            recs.append(
                f"{p.model}/{p.task_type} accuracy is only {p.accuracy_rate:.0%} "
                f"— consider replacing with a stronger model."
            )

        # Suggest best model per task type.
        task_types = {p.task_type for p in qualified}
        for tt in sorted(task_types):
            tt_profiles = [p for p in qualified if p.task_type == tt]
            if len(tt_profiles) >= 2:
                best = max(tt_profiles, key=lambda p: p.accuracy_rate)
                worst = min(tt_profiles, key=lambda p: p.accuracy_rate)
                if best.accuracy_rate - worst.accuracy_rate > 0.15:
                    recs.append(
                        f"For '{tt}' tasks, prefer {best.model} "
                        f"({best.accuracy_rate:.0%}) over {worst.model} "
                        f"({worst.accuracy_rate:.0%})."
                    )

        # Check for stale data.
        now = time.time()
        stale_days = 30.0
        stale_threshold = now - stale_days * 86_400
        stale = [p for p in qualified if p.last_updated < stale_threshold]
        if stale:
            names = ", ".join(f"{p.model}/{p.task_type}" for p in stale[:5])
            recs.append(
                f"Calibration data for {names} is older than "
                f"{stale_days:.0f} days — consider re-evaluating."
            )

        return recs
