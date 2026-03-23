from __future__ import annotations
"""Bandit-style allocation across heterogeneous evidence channels. theory2.tex Ch47 §2. # copilot:

This module implements multi-armed bandit allocation strategies for distributing
compute budgets across heterogeneous evidence channels in the jugeo verification
framework. Each channel (solver, agent, verifier, sampler) is modelled as a
bandit arm with prior beliefs over reward distributions. The allocator selects
arms according to a configurable policy (UCB1, Thompson sampling, or
epsilon-greedy) and updates beliefs as rewards are observed.

Key abstractions:
  - BanditArm: immutable description of an evidence channel / arm
  - ArmStats: mutable per-arm statistics accumulator
  - BanditPolicy: frozen strategy configuration with arm-selection logic
  - AllocationRecord: immutable record of a single pull event
  - BanditAllocator: mutable allocator combining arms, stats, and policy
  - BanditAllocationCoordinator: high-level coordinator managing channels
  - BanditAllocationAnalyzer: cumulative regret and convergence analysis
  - BanditAllocationWitness: immutable certificate of allocation reliability
"""

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import math
import time
import uuid
import random
import logging
import dataclasses
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_UCB_C: float = 1.414
"""Default exploration constant for UCB1.

Value sqrt(2) ≈ 1.414 is the theoretically motivated choice from the original
UCB1 paper (Auer et al., 2002).  Larger values favour exploration; smaller
values lean towards exploitation of the current best arm.
"""

DEFAULT_EPSILON: float = 0.1
"""Default exploration probability for epsilon-greedy strategy.

With epsilon=0.1, the policy explores a random arm 10% of the time and
exploits the empirically best arm 90% of the time.  A typical decay schedule
would reduce epsilon over time, but this module uses a fixed value for
simplicity.
"""

MIN_PULLS_BEFORE_EXPLOIT: int = 3
"""Minimum number of pulls each arm must accumulate before exploitation begins.

During the warm-up phase all arms are pulled at least this many times to
ensure the policy has enough information for reliable arm selection.  Pulling
every arm MIN_PULLS_BEFORE_EXPLOIT times before any exploitation is a common
heuristic in practical bandit deployments.
"""

__all__ = [
    "BanditArm",
    "ArmStats",
    "BanditPolicy",
    "AllocationRecord",
    "BanditAllocator",
    "BanditAllocationCoordinator",
    "BanditAllocationAnalyzer",
    "BanditAllocationWitness",
    # Fleet-semantics-aware heterogeneous phase allocation
    "HeterogeneousPhase",
    "PhaseReward",
    "AllocationPolicy",
    "BanditAllocation",
    "allocate_with_bandit",
    "update_bandit_arm",
    "select_next_phase",
    "compute_phase_reward",
]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid arm types
# ---------------------------------------------------------------------------
_VALID_ARM_TYPES = frozenset({"solver", "agent", "verifier", "sampler"})

# ---------------------------------------------------------------------------
# Valid policy strategies
# ---------------------------------------------------------------------------
_VALID_STRATEGIES = frozenset({"ucb1", "thompson", "epsilon_greedy"})


# ===========================================================================
# Internal helpers
# ===========================================================================


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Convert *v* to a finite float, returning *default* on any failure.

    Args:
        v: Any value to attempt conversion on.
        default: Fallback value returned when conversion fails or the result
            is non-finite.

    Returns:
        A finite Python float.

    Examples:
        >>> _safe_float("2.718")
        2.718
        >>> _safe_float(None)
        0.0
    """
    if v is None:
        return default
    try:
        result = float(v)
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(v: Any, lo: Any, hi: Any) -> Any:
    """Clamp *v* to [*lo*, *hi*].

    Args:
        v: Value to clamp.
        lo: Lower bound (inclusive).
        hi: Upper bound (inclusive).

    Returns:
        Clamped value.
    """
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _make_id(prefix: str = "") -> str:
    """Generate a short collision-resistant identifier.

    Args:
        prefix: Optional prefix string.

    Returns:
        String of the form ``"{prefix}_{hex8}"`` or bare hex8.
    """
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}_{uid}" if prefix else uid


def _beta_mean(alpha: float, beta: float) -> float:
    """Return the mean of a Beta(alpha, beta) distribution.

    Args:
        alpha: Shape parameter alpha (must be positive).
        beta: Shape parameter beta (must be positive).

    Returns:
        Mean value alpha / (alpha + beta), or 0.5 if either parameter is
        non-positive.
    """
    alpha = max(1e-9, _safe_float(alpha, 1.0))
    beta = max(1e-9, _safe_float(beta, 1.0))
    return alpha / (alpha + beta)


def _beta_variance(alpha: float, beta: float) -> float:
    """Return the variance of a Beta(alpha, beta) distribution.

    Variance = (alpha * beta) / ((alpha + beta)^2 * (alpha + beta + 1))

    Args:
        alpha: Shape parameter alpha.
        beta: Shape parameter beta.

    Returns:
        Non-negative variance as a float.
    """
    alpha = max(1e-9, _safe_float(alpha, 1.0))
    beta = max(1e-9, _safe_float(beta, 1.0))
    denom = (alpha + beta) ** 2 * (alpha + beta + 1.0)
    return (alpha * beta) / max(1e-30, denom)


def _beta_ci95(alpha: float, beta: float) -> Tuple[float, float]:
    """Approximate the 95% credible interval for Beta(alpha, beta).

    Uses the normal approximation: mean ± 1.96 * std.  This is accurate
    when both alpha and beta are moderately large (> 5).  For small counts
    it is a rough guide only.

    Args:
        alpha: Shape parameter alpha.
        beta: Shape parameter beta.

    Returns:
        Tuple (lower, upper) both clipped to [0, 1].
    """
    mu = _beta_mean(alpha, beta)
    sigma = math.sqrt(_beta_variance(alpha, beta))
    lower = _clamp(mu - 1.96 * sigma, 0.0, 1.0)
    upper = _clamp(mu + 1.96 * sigma, 0.0, 1.0)
    return (lower, upper)


def _thompson_sample_beta(alpha: float, beta: float) -> float:
    """Draw a single Thompson sample from Beta(alpha, beta).

    Uses the standard relationship between Beta and Gamma variates:
    X ~ Beta(a, b) iff X = G_a / (G_a + G_b) where G_k ~ Gamma(k, 1).

    Falls back to random.betavariate for simplicity and correctness.

    Args:
        alpha: Shape parameter alpha (clipped to >= 0.5).
        beta: Shape parameter beta (clipped to >= 0.5).

    Returns:
        A float sample in (0, 1).
    """
    a = max(0.5, _safe_float(alpha, 1.0))
    b = max(0.5, _safe_float(beta, 1.0))
    try:
        return random.betavariate(a, b)
    except Exception:
        return _beta_mean(a, b)


def _ucb1_score(mean: float, pull_count: int, total_pulls: int, c: float) -> float:
    """Compute the UCB1 score for an arm.

    UCB1(i) = mean_i + c * sqrt(ln(N) / n_i)

    where N is the total number of pulls across all arms and n_i is the pull
    count for arm i.

    Args:
        mean: Empirical mean reward for this arm.
        pull_count: Number of times this arm has been pulled.
        total_pulls: Total pulls across all arms.
        c: Exploration constant.

    Returns:
        UCB1 score as a float.  Returns infinity if pull_count is 0 (forces
        exploration of unpulled arms).
    """
    if pull_count == 0:
        return float("inf")
    if total_pulls <= 1:
        return mean
    log_term = math.log(max(1, total_pulls))
    bonus = _safe_float(c) * math.sqrt(log_term / pull_count)
    return mean + bonus


# ===========================================================================
# BanditArm
# ===========================================================================


@dataclass(frozen=True, slots=True)
class BanditArm:
    """Immutable descriptor for a bandit arm representing an evidence channel.

    Each arm corresponds to a specific type of evidence-generating process in
    the jugeo verification pipeline.  Prior beliefs about the arm's reward
    distribution are encoded as Beta distribution shape parameters
    (prior_alpha, prior_beta), allowing Bayesian updates via Thompson sampling.

    Attributes:
        arm_id: Unique identifier, typically generated by :meth:`make`.
        channel_name: Human-readable name for the evidence channel, e.g.
            ``"z3_solver"`` or ``"gpt4_agent"``.
        arm_type: Category of evidence source.  One of ``"solver"``,
            ``"agent"``, ``"verifier"``, ``"sampler"``.
        prior_alpha: Alpha shape parameter of the Beta prior.  Values > 1
            encode a prior belief that the arm tends to succeed.
        prior_beta: Beta shape parameter of the Beta prior.  Values > 1
            encode a prior belief that the arm tends to fail.
        metadata: Arbitrary key-value annotations for downstream consumers.
    """

    arm_id: str
    channel_name: str
    arm_type: str
    prior_alpha: float
    prior_beta: float
    metadata: dict

    def __post_init__(self) -> None:
        """Validate fields after dataclass construction.

        Raises:
            ValueError: If arm_type is not one of the valid types, or if
                prior_alpha or prior_beta are non-positive.
        """
        if self.arm_type not in _VALID_ARM_TYPES:
            raise ValueError(
                f"arm_type must be one of {sorted(_VALID_ARM_TYPES)}, got '{self.arm_type}'"
            )
        if self.prior_alpha <= 0 or self.prior_beta <= 0:
            raise ValueError(
                f"prior_alpha and prior_beta must be positive, got "
                f"alpha={self.prior_alpha}, beta={self.prior_beta}"
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def expected_reward(self) -> float:
        """Return the prior expected reward for this arm.

        Computed as the mean of the Beta(prior_alpha, prior_beta) prior.

        Returns:
            Float in (0, 1) representing expected reward probability.

        Examples:
            >>> arm = BanditArm.make("test_solver", "solver")
            >>> 0.0 < arm.expected_reward() < 1.0
            True
        """
        return _beta_mean(self.prior_alpha, self.prior_beta)

    def confidence_interval(self) -> Tuple[float, float]:
        """Return the approximate 95% credible interval for the prior reward.

        Uses a normal approximation to the Beta distribution.  The returned
        interval reflects uncertainty under the prior; it narrows as posterior
        updates are applied via ArmStats.

        Returns:
            Tuple ``(lower, upper)`` with both values in [0, 1].

        Examples:
            >>> arm = BanditArm.make("ch", "solver")
            >>> lo, hi = arm.confidence_interval()
            >>> assert 0.0 <= lo <= hi <= 1.0
        """
        return _beta_ci95(self.prior_alpha, self.prior_beta)

    def to_dict(self) -> dict:
        """Serialise this arm to a plain Python dictionary.

        Returns:
            Dict with all fields in JSON-serialisable form.

        Examples:
            >>> arm = BanditArm.make("test", "agent")
            >>> d = arm.to_dict()
            >>> d["arm_type"]
            'agent'
        """
        lo, hi = self.confidence_interval()
        return {
            "arm_id": self.arm_id,
            "channel_name": self.channel_name,
            "arm_type": self.arm_type,
            "prior_alpha": self.prior_alpha,
            "prior_beta": self.prior_beta,
            "expected_reward": round(self.expected_reward(), 6),
            "confidence_interval_95": [round(lo, 6), round(hi, 6)],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def make(
        cls,
        channel_name: str,
        arm_type: str,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
    ) -> "BanditArm":
        """Convenience factory that creates a fresh BanditArm.

        Args:
            channel_name: Human-readable name for the channel.
            arm_type: One of ``"solver"``, ``"agent"``, ``"verifier"``,
                ``"sampler"``.
            prior_alpha: Alpha shape parameter of the Beta prior.  Defaults to
                1.0 (uniform prior).
            prior_beta: Beta shape parameter of the Beta prior.  Defaults to
                1.0 (uniform prior).

        Returns:
            A new BanditArm with a generated arm_id.

        Raises:
            ValueError: If arm_type is not valid or priors are non-positive.
        """
        return cls(
            arm_id=_make_id("arm"),
            channel_name=str(channel_name),
            arm_type=str(arm_type),
            prior_alpha=max(1e-9, _safe_float(prior_alpha, 1.0)),
            prior_beta=max(1e-9, _safe_float(prior_beta, 1.0)),
            metadata={},
        )


# ===========================================================================
# ArmStats
# ===========================================================================


@dataclass(slots=True)
class ArmStats:
    """Mutable per-arm statistics accumulator.

    Maintains running statistics for a single bandit arm, including pull
    count, cumulative reward, squared reward sum (for variance), and a full
    list of observed rewards for diagnostics.

    Attributes:
        arm_id: Identifier of the arm these stats belong to.
        pull_count: Number of times this arm has been pulled.
        reward_sum: Sum of all observed rewards.
        reward_sq_sum: Sum of squared observed rewards (for variance).
        rewards: Complete history of observed rewards as a list of floats.
        last_pull_at: POSIX timestamp of the most recent pull.
    """

    arm_id: str
    pull_count: int
    reward_sum: float
    reward_sq_sum: float
    rewards: list
    last_pull_at: float

    def record_reward(self, r: float) -> None:
        """Incorporate a new observed reward into the running statistics.

        Updates pull_count, reward_sum, reward_sq_sum, rewards list, and
        last_pull_at timestamp atomically.

        Args:
            r: Observed reward.  Typically in [0, 1] but not enforced here;
               callers are responsible for normalisation.
        """
        safe_r = _safe_float(r, 0.0)
        self.pull_count += 1
        self.reward_sum += safe_r
        self.reward_sq_sum += safe_r * safe_r
        self.rewards.append(safe_r)
        self.last_pull_at = time.time()
        _log.debug(
            "arm %s: recorded reward %.4f (pull #%d)", self.arm_id, safe_r, self.pull_count
        )

    def mean_reward(self) -> float:
        """Return the empirical mean reward over all pulls.

        Returns:
            Mean reward as a float, or 0.0 if no pulls have been recorded.
        """
        if self.pull_count == 0:
            return 0.0
        return self.reward_sum / self.pull_count

    def variance(self) -> float:
        """Return the sample variance of observed rewards.

        Uses Welford's formula: Var = E[X^2] - E[X]^2.

        Returns:
            Non-negative float variance.  Returns 0.0 if fewer than 2 pulls.
        """
        if self.pull_count < 2:
            return 0.0
        mean = self.mean_reward()
        mean_sq = self.reward_sq_sum / self.pull_count
        return max(0.0, mean_sq - mean * mean)

    def ucb_score(self, total_pulls: int, c: float = 1.41) -> float:
        """Compute the UCB1 score for this arm given total pull count.

        Args:
            total_pulls: Sum of pull counts across all arms.
            c: Exploration constant.  Defaults to 1.41 ≈ sqrt(2).

        Returns:
            UCB1 score as a float.  Returns infinity for unpulled arms.

        Examples:
            >>> s = ArmStats("a1", 5, 3.5, 2.7, [0.7, 0.8, 0.7, 0.6, 0.7], 0.0)
            >>> score = s.ucb_score(20, c=1.41)
            >>> score > 0
            True
        """
        return _ucb1_score(self.mean_reward(), self.pull_count, total_pulls, c)

    def thompson_sample(self) -> float:
        """Draw a Thompson sample from the posterior Beta distribution.

        The posterior is obtained by conjugate updating:
            posterior_alpha = 1 + n_successes
            posterior_beta  = 1 + n_failures

        where successes and failures are approximated by treating rewards
        above 0.5 as successes and the rest as failures.

        Returns:
            A float sample from the posterior, suitable for arm comparison.
        """
        n_success = sum(1 for r in self.rewards if r > 0.5)
        n_failure = max(0, self.pull_count - n_success)
        post_alpha = 1.0 + n_success
        post_beta = 1.0 + n_failure
        return _thompson_sample_beta(post_alpha, post_beta)

    def to_dict(self) -> dict:
        """Serialise this stats object to a plain Python dictionary.

        Returns:
            Dict suitable for JSON serialisation.
        """
        return {
            "arm_id": self.arm_id,
            "pull_count": self.pull_count,
            "reward_sum": round(self.reward_sum, 6),
            "reward_sq_sum": round(self.reward_sq_sum, 6),
            "mean_reward": round(self.mean_reward(), 6),
            "variance": round(self.variance(), 6),
            "last_pull_at": self.last_pull_at,
            "reward_count": len(self.rewards),
        }


# ===========================================================================
# BanditPolicy
# ===========================================================================


@dataclass(frozen=True, slots=True)
class BanditPolicy:
    """Frozen configuration of an arm-selection strategy.

    Encapsulates all hyperparameters needed to implement UCB1, Thompson
    sampling, or epsilon-greedy selection.  Being frozen allows policy objects
    to be shared safely across coordinator instances.

    Attributes:
        policy_id: Unique identifier for this policy instance.
        strategy: Selection strategy.  One of ``"ucb1"``, ``"thompson"``,
            ``"epsilon_greedy"``.
        epsilon: Exploration probability used by ``"epsilon_greedy"``.
            Ignored by UCB1 and Thompson strategies.
        c_param: Exploration constant used by UCB1.  Ignored by Thompson and
            epsilon-greedy.
        temperature: Softmax temperature used for tie-breaking in some
            experimental extensions.  Not used by the standard strategies.
        metadata: Arbitrary annotations.
    """

    policy_id: str
    strategy: str
    epsilon: float
    c_param: float
    temperature: float
    metadata: dict

    def __post_init__(self) -> None:
        """Validate fields after dataclass construction.

        Raises:
            ValueError: If strategy is not valid.
        """
        if self.strategy not in _VALID_STRATEGIES:
            raise ValueError(
                f"strategy must be one of {sorted(_VALID_STRATEGIES)}, got '{self.strategy}'"
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def select_arm(self, arms_stats: Dict[str, ArmStats]) -> str:
        """Select an arm to pull according to this policy.

        Implements a warm-up phase (all arms pulled at least
        MIN_PULLS_BEFORE_EXPLOIT times) followed by strategy-specific
        selection.

        Args:
            arms_stats: Mapping from arm_id to ArmStats.  Must be non-empty.

        Returns:
            The arm_id of the selected arm.

        Raises:
            ValueError: If *arms_stats* is empty.
        """
        if not arms_stats:
            raise ValueError("arms_stats is empty; cannot select an arm")

        # Warm-up: find any arm with too few pulls
        for arm_id, stats in arms_stats.items():
            if stats.pull_count < MIN_PULLS_BEFORE_EXPLOIT:
                _log.debug("policy %s: warm-up pull for arm %s", self.policy_id, arm_id)
                return arm_id

        total_pulls = sum(s.pull_count for s in arms_stats.values())

        if self.strategy == "ucb1":
            # Select arm with highest UCB1 score
            best_id = max(
                arms_stats.keys(),
                key=lambda aid: arms_stats[aid].ucb_score(total_pulls, self.c_param),
            )
            _log.debug("policy %s UCB1: selected arm %s", self.policy_id, best_id)
            return best_id

        elif self.strategy == "thompson":
            # Draw one Thompson sample per arm and select the highest
            samples = {aid: stats.thompson_sample() for aid, stats in arms_stats.items()}
            best_id = max(samples.keys(), key=lambda aid: samples[aid])
            _log.debug("policy %s Thompson: selected arm %s", self.policy_id, best_id)
            return best_id

        else:
            # epsilon_greedy
            if random.random() < self.epsilon:
                # Explore: choose uniformly at random
                best_id = random.choice(list(arms_stats.keys()))
                _log.debug("policy %s eps-greedy (explore): selected arm %s",
                           self.policy_id, best_id)
            else:
                # Exploit: choose arm with highest empirical mean
                best_id = max(
                    arms_stats.keys(),
                    key=lambda aid: arms_stats[aid].mean_reward(),
                )
                _log.debug("policy %s eps-greedy (exploit): selected arm %s",
                           self.policy_id, best_id)
            return best_id

    def to_dict(self) -> dict:
        """Serialise this policy to a plain Python dictionary.

        Returns:
            Dict with all fields in JSON-serialisable form.
        """
        return {
            "policy_id": self.policy_id,
            "strategy": self.strategy,
            "epsilon": self.epsilon,
            "c_param": self.c_param,
            "temperature": self.temperature,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def default(cls) -> "BanditPolicy":
        """Return the default policy (UCB1 with c = DEFAULT_UCB_C).

        Returns:
            A BanditPolicy configured for UCB1 with standard hyperparameters.
        """
        return cls(
            policy_id=_make_id("policy"),
            strategy="ucb1",
            epsilon=DEFAULT_EPSILON,
            c_param=DEFAULT_UCB_C,
            temperature=1.0,
            metadata={},
        )

    @classmethod
    def ucb1(cls, c: float = DEFAULT_UCB_C) -> "BanditPolicy":
        """Return a UCB1 policy with the given exploration constant.

        Args:
            c: Exploration constant.  Defaults to DEFAULT_UCB_C.

        Returns:
            A BanditPolicy configured for UCB1.
        """
        return cls(
            policy_id=_make_id("policy"),
            strategy="ucb1",
            epsilon=DEFAULT_EPSILON,
            c_param=max(0.01, _safe_float(c, DEFAULT_UCB_C)),
            temperature=1.0,
            metadata={},
        )

    @classmethod
    def thompson(cls) -> "BanditPolicy":
        """Return a Thompson-sampling policy.

        Returns:
            A BanditPolicy configured for Thompson sampling.
        """
        return cls(
            policy_id=_make_id("policy"),
            strategy="thompson",
            epsilon=DEFAULT_EPSILON,
            c_param=DEFAULT_UCB_C,
            temperature=1.0,
            metadata={},
        )


# ===========================================================================
# AllocationRecord
# ===========================================================================


@dataclass(frozen=True, slots=True)
class AllocationRecord:
    """Immutable record of a single bandit pull and its observed reward.

    Created by :meth:`BanditAllocator.pull` and updated by
    :meth:`BanditAllocator.observe_reward` (via a new record, since this is
    frozen).

    Attributes:
        record_id: Unique identifier.
        arm_id: Identifier of the pulled arm.
        channel_name: Human-readable channel name for convenience.
        tokens_allocated: Number of tokens allocated in this pull.
        reward_observed: Reward signal received after allocation.  Set to 0.0
            until :meth:`observe_reward` is called.
        timestamp: POSIX timestamp of the pull.
        metadata: Arbitrary annotations.
    """

    record_id: str
    arm_id: str
    channel_name: str
    tokens_allocated: int
    reward_observed: float
    timestamp: float
    metadata: dict

    def to_dict(self) -> dict:
        """Serialise this record to a plain Python dictionary.

        Returns:
            Dict with all fields in JSON-serialisable form.
        """
        return {
            "record_id": self.record_id,
            "arm_id": self.arm_id,
            "channel_name": self.channel_name,
            "tokens_allocated": self.tokens_allocated,
            "reward_observed": round(self.reward_observed, 6),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def make(
        cls,
        arm_id: str,
        channel_name: str,
        tokens_allocated: int,
    ) -> "AllocationRecord":
        """Create a new AllocationRecord for a pull event.

        reward_observed is initialised to 0.0; call observe_reward separately
        to update it (by creating a replacement record if needed, since this
        class is frozen).

        Args:
            arm_id: Identifier of the arm being pulled.
            channel_name: Human-readable channel name.
            tokens_allocated: Tokens committed to this pull.

        Returns:
            A new AllocationRecord with a generated record_id.
        """
        return cls(
            record_id=_make_id("record"),
            arm_id=str(arm_id),
            channel_name=str(channel_name),
            tokens_allocated=max(0, int(tokens_allocated)),
            reward_observed=0.0,
            timestamp=time.time(),
            metadata={},
        )


# ===========================================================================
# BanditAllocator
# ===========================================================================


@dataclass(slots=True)
class BanditAllocator:
    """Mutable multi-armed bandit allocator.

    Maintains a collection of arms, their statistics, and a policy for
    selecting which arm to pull next.  Allocation history is recorded for
    downstream analysis.

    Attributes:
        allocator_id: Unique identifier.
        arms: Mapping from arm_id to BanditArm.
        stats: Mapping from arm_id to ArmStats.
        policy: The arm-selection strategy.
        budget: Total token budget available to this allocator.
        total_pulls: Running count of all pulls made.
        allocation_history: List of AllocationRecord instances.
    """

    allocator_id: str
    arms: dict
    stats: dict
    policy: BanditPolicy
    budget: int
    total_pulls: int
    allocation_history: list

    def add_arm(self, arm: BanditArm) -> None:
        """Register a new arm with this allocator.

        Creates a fresh ArmStats entry for the arm.  If an arm with the same
        arm_id already exists it is silently replaced.

        Args:
            arm: The BanditArm to add.
        """
        self.arms[arm.arm_id] = arm
        self.stats[arm.arm_id] = ArmStats(
            arm_id=arm.arm_id,
            pull_count=0,
            reward_sum=0.0,
            reward_sq_sum=0.0,
            rewards=[],
            last_pull_at=0.0,
        )
        _log.debug("allocator %s: registered arm %s (%s)",
                   self.allocator_id, arm.arm_id, arm.channel_name)

    def pull(self, budget_fraction: float = 1.0) -> AllocationRecord:
        """Select an arm and create an allocation record.

        Uses the policy to select the arm, then allocates
        ``budget_fraction * budget`` tokens to that arm.

        Args:
            budget_fraction: Fraction of the total budget to allocate in this
                pull.  Clipped to [0, 1].

        Returns:
            An AllocationRecord describing the pull.

        Raises:
            RuntimeError: If no arms have been registered.
        """
        if not self.arms:
            raise RuntimeError(f"allocator {self.allocator_id}: no arms registered")

        fraction = _clamp(_safe_float(budget_fraction, 1.0), 0.0, 1.0)
        tokens = max(1, int(self.budget * fraction / max(1, len(self.arms))))

        arm_id = self.policy.select_arm(self.stats)
        arm = self.arms[arm_id]

        record = AllocationRecord.make(
            arm_id=arm_id,
            channel_name=arm.channel_name,
            tokens_allocated=tokens,
        )
        self.total_pulls += 1
        self.allocation_history.append(record)
        _log.debug(
            "allocator %s: pulled arm %s (%s), tokens=%d",
            self.allocator_id, arm_id, arm.channel_name, tokens,
        )
        return record

    def observe_reward(self, arm_id: str, reward: float) -> None:
        """Update the statistics for an arm with an observed reward.

        Args:
            arm_id: Identifier of the arm that generated the reward.
            reward: Observed reward value (typically in [0, 1]).

        Raises:
            KeyError: If arm_id is not registered with this allocator.
        """
        if arm_id not in self.stats:
            raise KeyError(f"allocator {self.allocator_id}: unknown arm_id '{arm_id}'")
        self.stats[arm_id].record_reward(reward)

    def best_arm(self) -> str:
        """Return the arm_id of the arm with the highest empirical mean reward.

        In the warm-up phase (some arms have fewer than MIN_PULLS_BEFORE_EXPLOIT
        pulls), returns the arm with the most pulls as a heuristic.

        Returns:
            arm_id string of the currently best arm.

        Raises:
            RuntimeError: If no arms are registered.
        """
        if not self.stats:
            raise RuntimeError("No arms registered")
        # Check warm-up: if any arm is under-pulled, return the most-pulled arm
        under_pulled = [aid for aid, s in self.stats.items()
                        if s.pull_count < MIN_PULLS_BEFORE_EXPLOIT]
        if under_pulled:
            # Return the arm with the fewest pulls to drive warm-up faster
            return min(self.stats.keys(), key=lambda aid: self.stats[aid].pull_count)
        return max(self.stats.keys(), key=lambda aid: self.stats[aid].mean_reward())

    def regret_estimate(self) -> float:
        """Estimate the cumulative regret relative to the best empirical arm.

        Regret is defined as the difference in cumulative reward between
        always choosing the best arm and the actual allocation decisions made.

        Returns:
            Non-negative float regret estimate.  Returns 0.0 if fewer than 2
            arms are registered or no pulls have been made.
        """
        if not self.stats or self.total_pulls == 0:
            return 0.0
        best_mean = max(s.mean_reward() for s in self.stats.values())
        total_regret = 0.0
        for arm_id, stats in self.stats.items():
            gap = best_mean - stats.mean_reward()
            total_regret += gap * stats.pull_count
        return max(0.0, total_regret)

    def to_dict(self) -> dict:
        """Serialise this allocator to a plain Python dictionary.

        Returns:
            Dict with all allocator state in JSON-serialisable form.
        """
        return {
            "allocator_id": self.allocator_id,
            "arm_count": len(self.arms),
            "total_pulls": self.total_pulls,
            "budget": self.budget,
            "best_arm": self.best_arm() if self.arms else None,
            "regret_estimate": round(self.regret_estimate(), 6),
            "arms": {aid: arm.to_dict() for aid, arm in self.arms.items()},
            "stats": {aid: s.to_dict() for aid, s in self.stats.items()},
            "policy": self.policy.to_dict(),
            "history_length": len(self.allocation_history),
        }


# ===========================================================================
# BanditAllocationCoordinator
# ===========================================================================


@dataclass(slots=True)
class BanditAllocationCoordinator:
    """High-level coordinator managing channel registration and allocation rounds.

    Wraps a BanditAllocator and provides named-channel management, reward
    update routing, and round tracking.

    Attributes:
        coordinator_id: Unique identifier.
        allocator: The underlying BanditAllocator.
        channel_registry: Mapping from channel_name to arm_id.
        phase: Current phase label, e.g. ``"warmup"`` or ``"exploit"``.
        round_count: Number of allocation rounds completed.
        total_budget: Total token budget available to this coordinator.
    """

    coordinator_id: str
    allocator: BanditAllocator
    channel_registry: dict
    phase: str
    round_count: int
    total_budget: int

    def register_channel(
        self,
        name: str,
        arm_type: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Register a new evidence channel as a bandit arm.

        Creates a BanditArm and adds it to the allocator.  Also records the
        channel_name -> arm_id mapping in the registry.

        Args:
            name: Human-readable channel name.
            arm_type: One of ``"solver"``, ``"agent"``, ``"verifier"``,
                ``"sampler"``.
            metadata: Optional metadata dict attached to the arm.

        Raises:
            ValueError: If arm_type is not valid.
        """
        arm = BanditArm.make(channel_name=name, arm_type=arm_type)
        if metadata:
            # BanditArm is frozen; we store metadata in the registry instead
            self.channel_registry[name] = {
                "arm_id": arm.arm_id,
                "metadata": dict(metadata),
            }
        else:
            self.channel_registry[name] = {"arm_id": arm.arm_id, "metadata": {}}
        self.allocator.add_arm(arm)
        _log.debug(
            "coordinator %s: registered channel '%s' -> arm %s",
            self.coordinator_id, name, arm.arm_id,
        )

    def run_round(self, available_budget: int) -> AllocationRecord:
        """Execute one allocation round, pulling a single arm.

        Adjusts the allocator budget to *available_budget* for this round,
        then delegates to :meth:`BanditAllocator.pull`.

        Args:
            available_budget: Tokens available for this round.

        Returns:
            The AllocationRecord produced by the pull.
        """
        self.allocator.budget = max(1, int(available_budget))
        record = self.allocator.pull(budget_fraction=1.0)
        self.round_count += 1
        # Determine phase based on warm-up progress
        all_pulled = all(
            s.pull_count >= MIN_PULLS_BEFORE_EXPLOIT
            for s in self.allocator.stats.values()
        )
        self.phase = "exploit" if all_pulled else "warmup"
        _log.debug(
            "coordinator %s round %d: pulled arm %s (phase=%s)",
            self.coordinator_id, self.round_count, record.arm_id, self.phase,
        )
        return record

    def update_reward(self, channel_name: str, reward: float) -> None:
        """Update the reward for the most recently pulled arm on *channel_name*.

        Routes the reward to the correct arm via the channel registry.

        Args:
            channel_name: Name of the channel that generated the reward.
            reward: Observed reward value in [0, 1].

        Raises:
            KeyError: If *channel_name* is not registered.
        """
        if channel_name not in self.channel_registry:
            raise KeyError(
                f"coordinator {self.coordinator_id}: unknown channel '{channel_name}'"
            )
        arm_id = self.channel_registry[channel_name]["arm_id"]
        self.allocator.observe_reward(arm_id, reward)

    def best_channel(self) -> str:
        """Return the name of the channel with the highest empirical mean reward.

        Returns:
            Channel name string.

        Raises:
            RuntimeError: If no channels are registered.
        """
        if not self.channel_registry:
            raise RuntimeError("No channels registered")
        best_arm_id = self.allocator.best_arm()
        # Reverse-lookup channel name
        for name, info in self.channel_registry.items():
            if info["arm_id"] == best_arm_id:
                return name
        # Fallback: return the first registered channel
        return next(iter(self.channel_registry.keys()))

    def allocation_summary(self) -> dict:
        """Return a concise summary of allocation state.

        Returns:
            Dict with coordinator_id, round_count, phase, best_channel,
            total_pulls, regret_estimate, and per-channel stats.
        """
        per_channel: Dict[str, dict] = {}
        for name, info in self.channel_registry.items():
            arm_id = info["arm_id"]
            stats = self.allocator.stats.get(arm_id)
            if stats:
                per_channel[name] = {
                    "arm_id": arm_id,
                    "pull_count": stats.pull_count,
                    "mean_reward": round(stats.mean_reward(), 4),
                    "variance": round(stats.variance(), 4),
                }
        return {
            "coordinator_id": self.coordinator_id,
            "round_count": self.round_count,
            "phase": self.phase,
            "best_channel": self.best_channel() if self.channel_registry else None,
            "total_pulls": self.allocator.total_pulls,
            "regret_estimate": round(self.allocator.regret_estimate(), 6),
            "channels": per_channel,
        }

    def to_dict(self) -> dict:
        """Serialise this coordinator to a plain Python dictionary.

        Returns:
            Dict with full coordinator state.
        """
        return {
            **self.allocation_summary(),
            "allocator": self.allocator.to_dict(),
            "channel_registry": {
                k: dict(v) for k, v in self.channel_registry.items()
            },
        }

    @classmethod
    def make(
        cls,
        total_budget: int,
        policy: Optional[BanditPolicy] = None,
    ) -> "BanditAllocationCoordinator":
        """Create a fresh coordinator with the given budget and policy.

        Args:
            total_budget: Total token budget available to the coordinator.
            policy: Arm-selection policy.  Defaults to UCB1 with
                DEFAULT_UCB_C if not provided.

        Returns:
            A fully initialised BanditAllocationCoordinator.
        """
        if policy is None:
            policy = BanditPolicy.default()
        allocator = BanditAllocator(
            allocator_id=_make_id("allocator"),
            arms={},
            stats={},
            policy=policy,
            budget=max(1, int(total_budget)),
            total_pulls=0,
            allocation_history=[],
        )
        return cls(
            coordinator_id=_make_id("bcoord"),
            allocator=allocator,
            channel_registry={},
            phase="warmup",
            round_count=0,
            total_budget=max(1, int(total_budget)),
        )


# ===========================================================================
# BanditAllocationAnalyzer
# ===========================================================================


@dataclass(slots=True)
class BanditAllocationAnalyzer:
    """Cumulative regret and convergence analysis for bandit allocations.

    Records per-round snapshots and computes metrics such as cumulative
    regret, exploration ratio, and convergence indicator.

    Attributes:
        analyzer_id: Unique identifier.
        round_snapshots: List of snapshot dicts, one per recorded round.
        arm_count: Number of arms registered at analysis time.
    """

    analyzer_id: str
    round_snapshots: list
    arm_count: int

    def record_round(self, snapshot: dict) -> None:
        """Append a per-round snapshot.

        Args:
            snapshot: Dict describing the state of the coordinator at the end
                of this round.  Typically obtained from
                :meth:`BanditAllocationCoordinator.allocation_summary`.
        """
        self.round_snapshots.append(dict(snapshot))
        _log.debug("analyzer %s: recorded round #%d", self.analyzer_id, len(self.round_snapshots))

    def cumulative_regret(self) -> float:
        """Return the latest regret estimate from the most recent snapshot.

        Returns:
            Non-negative float.  Returns 0.0 if no snapshots have been
            recorded.
        """
        if not self.round_snapshots:
            return 0.0
        return _safe_float(self.round_snapshots[-1].get("regret_estimate", 0.0))

    def exploration_ratio(self) -> float:
        """Estimate the fraction of rounds spent in the warmup/exploration phase.

        Returns:
            Float in [0, 1].  1.0 means all rounds were exploratory.
        """
        if not self.round_snapshots:
            return 0.0
        warmup_rounds = sum(
            1 for s in self.round_snapshots if s.get("phase") == "warmup"
        )
        return warmup_rounds / len(self.round_snapshots)

    def convergence_indicator(self) -> float:
        """Compute a convergence indicator based on pull-count concentration.

        Measures how concentrated pulls are on the best arm.  A value near 1
        suggests the policy has converged; a value near 0 suggests it is still
        exploring.

        Returns:
            Float in [0, 1].
        """
        if not self.round_snapshots:
            return 0.0
        latest = self.round_snapshots[-1]
        channels = latest.get("channels", {})
        if not channels:
            return 0.0
        counts = [c.get("pull_count", 0) for c in channels.values()]
        total = sum(counts)
        if total == 0:
            return 0.0
        max_count = max(counts)
        # Concentration = max_pull / total_pulls
        return max_count / total

    def channel_ranking(self) -> list:
        """Return channels ranked by mean reward from the latest snapshot.

        Returns:
            List of (channel_name, mean_reward) tuples sorted descending by
            mean_reward.  Returns an empty list if no snapshots available.
        """
        if not self.round_snapshots:
            return []
        latest = self.round_snapshots[-1]
        channels = latest.get("channels", {})
        ranking = [
            (name, _safe_float(info.get("mean_reward", 0.0)))
            for name, info in channels.items()
        ]
        return sorted(ranking, key=lambda t: t[1], reverse=True)

    def report(self) -> dict:
        """Generate a comprehensive analysis report.

        Returns:
            Dict with all key analysis metrics.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "round_count": len(self.round_snapshots),
            "arm_count": self.arm_count,
            "cumulative_regret": round(self.cumulative_regret(), 6),
            "exploration_ratio": round(self.exploration_ratio(), 4),
            "convergence_indicator": round(self.convergence_indicator(), 4),
            "channel_ranking": self.channel_ranking(),
            "latest_snapshot": self.round_snapshots[-1] if self.round_snapshots else None,
        }

    def to_dict(self) -> dict:
        """Serialise the analyzer to a plain Python dictionary.

        Returns:
            Dict with analyzer state and full snapshot list.
        """
        return {
            **self.report(),
            "all_snapshots": list(self.round_snapshots),
        }


# ===========================================================================
# BanditAllocationWitness
# ===========================================================================


@dataclass(frozen=True, slots=True)
class BanditAllocationWitness:
    """Immutable certificate of allocation reliability for a bandit run.

    Issued by :meth:`issue` after a coordinator and analyzer have been
    operating for a sufficient number of rounds.  Certifies that the
    allocation strategy has converged and that regret is within acceptable
    bounds relative to cumulative reward.

    Attributes:
        witness_id: Unique identifier.
        arm_id: Identifier of the best arm at witness issuance time.
        channel_name: Human-readable name of the best channel.
        round_count: Number of allocation rounds at issuance.
        cumulative_reward: Sum of all observed rewards across all arms.
        regret_estimate: Estimated cumulative regret.
        timestamp: POSIX timestamp of issuance.
        evidence: Supporting evidence (analyzer report, coordinator summary).
    """

    witness_id: str
    arm_id: str
    channel_name: str
    round_count: int
    cumulative_reward: float
    regret_estimate: float
    timestamp: float
    evidence: dict

    def to_dict(self) -> dict:
        """Serialise this witness to a plain Python dictionary.

        Returns:
            Dict with all fields in JSON-serialisable form.
        """
        return {
            "witness_id": self.witness_id,
            "arm_id": self.arm_id,
            "channel_name": self.channel_name,
            "round_count": self.round_count,
            "cumulative_reward": round(self.cumulative_reward, 6),
            "regret_estimate": round(self.regret_estimate, 6),
            "timestamp": self.timestamp,
            "evidence": dict(self.evidence),
        }

    def is_reliable(self) -> bool:
        """Return True if regret is less than half of cumulative reward.

        A reliable allocation is one where the policy has not wasted too much
        budget on suboptimal arms relative to the total value extracted.

        Returns:
            True if ``regret_estimate < cumulative_reward * 0.5``, or if
            cumulative_reward is 0 and regret is also 0.

        Examples:
            >>> # A witness with low regret relative to reward is reliable
        """
        if self.cumulative_reward <= 0.0:
            return self.regret_estimate <= 0.0
        return self.regret_estimate < self.cumulative_reward * 0.5

    def certify_text(self) -> str:
        """Return a human-readable certification summary.

        Returns:
            A multi-line string describing the witness, suitable for terminal
            display or audit logs.
        """
        status = "RELIABLE" if self.is_reliable() else "UNRELIABLE"
        lines = [
            f"=== BanditAllocationWitness ({status}) ===",
            f"  witness_id       : {self.witness_id}",
            f"  arm_id           : {self.arm_id}",
            f"  channel_name     : {self.channel_name}",
            f"  round_count      : {self.round_count}",
            f"  cumulative_reward: {self.cumulative_reward:.4f}",
            f"  regret_estimate  : {self.regret_estimate:.4f}",
            f"  issued_at        : {self.timestamp:.3f}",
        ]
        return "\n".join(lines)

    @classmethod
    def issue(
        cls,
        coordinator: BanditAllocationCoordinator,
        analyzer: BanditAllocationAnalyzer,
    ) -> "BanditAllocationWitness":
        """Issue a witness certifying the current allocation state.

        Collects evidence from both the coordinator and analyzer, computes
        cumulative reward and regret, and packages everything into an immutable
        witness.

        Args:
            coordinator: The coordinator to certify.
            analyzer: The analyzer tracking the coordinator.

        Returns:
            A new, immutable BanditAllocationWitness.
        """
        # Compute cumulative reward across all arms
        cumulative_reward = sum(
            s.reward_sum for s in coordinator.allocator.stats.values()
        )
        regret = coordinator.allocator.regret_estimate()

        # Determine best arm and channel
        best_arm_id = coordinator.allocator.best_arm() if coordinator.allocator.arms else ""
        best_channel = ""
        if best_arm_id:
            for name, info in coordinator.channel_registry.items():
                if info["arm_id"] == best_arm_id:
                    best_channel = name
                    break

        evidence = {
            "coordinator_summary": coordinator.allocation_summary(),
            "analyzer_report": analyzer.report(),
            "policy": coordinator.allocator.policy.to_dict(),
        }

        return cls(
            witness_id=_make_id("bwitness"),
            arm_id=best_arm_id,
            channel_name=best_channel,
            round_count=coordinator.round_count,
            cumulative_reward=cumulative_reward,
            regret_estimate=regret,
            timestamp=time.time(),
            evidence=evidence,
        )


# ===========================================================================
# Heterogeneous phase and fleet-aligned allocation abstractions
#
# The classes below extend the core bandit machinery with fleet-semantics-aware
# abstractions.  They bridge the bandit allocation layer to the fleet competition
# protocol described in theory2.tex Ch46–47, encoding the judgment tuple
# (c, φ, A, E, O, B, T, Π) and the ordered trust algebra.
# ===========================================================================


class _PhaseKindFallback(Enum):
    """Fallback PhaseKind enum used when jugeo.orchestration.frontier_phases.models is unavailable."""

    EXPLORATION = auto()
    EXPLOITATION = auto()
    VERIFICATION = auto()
    STALLED = auto()
    CONVERGING = auto()
    TERMINAL = auto()


class _TrustTierFallback(Enum):
    """Fallback TrustTier enum used when jugeo.orchestration.fleet_competition is unavailable."""

    PROPOSAL = 0
    REVIEWED = 1
    VERIFIED = 2
    RUNTIME_WITNESSED = 3
    PROOF_BACKED = 4

    def numeric_weight(self) -> float:
        return self.value / (len(_TrustTierFallback) - 1)


try:
    from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import (  # type: ignore[import]
        TrustTier as _TrustTierImport,
    )
    _EffectiveTrustTier = _TrustTierImport
except Exception:
    _EffectiveTrustTier = _TrustTierFallback  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class HeterogeneousPhase:
    """Immutable descriptor of a heterogeneous frontier phase for bandit allocation.

    A *heterogeneous phase* is a named stage of the frontier search (e.g.
    exploration, exploitation, verification) that differs from other phases in
    its reward characteristics, resource requirements, and trust tier ceiling.
    The bandit allocator treats each phase as a distinct arm cluster.

    Attributes:
        phase_id: Unique identifier for this phase descriptor.
        name: Human-readable phase name.
        kind: Phase kind classification (exploration, exploitation, …).
        trust_tier_ceiling: Maximum trust tier achievable in this phase.
        expected_reward_range: ``(min_reward, max_reward)`` prior estimate.
        resource_cost: Estimated resource cost per allocation unit.
        is_terminal: Whether this phase is a terminal (converged) phase.
        metadata: Arbitrary key-value metadata.
    """

    phase_id: str
    name: str
    kind: Any  # PhaseKind or _PhaseKindFallback
    trust_tier_ceiling: Any  # TrustTier
    expected_reward_range: Tuple[float, float]
    resource_cost: float
    is_terminal: bool = False
    metadata: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    def prior_expected_reward(self) -> float:
        """Return the midpoint of ``expected_reward_range`` as a prior.

        Returns:
            Float in [0, 1].
        """
        lo, hi = self.expected_reward_range
        return _safe_float((lo + hi) / 2.0)

    # ------------------------------------------------------------------
    def trust_weight(self) -> float:
        """Return the numeric trust weight of the ceiling tier.

        Returns:
            Float in [0, 1].
        """
        t = self.trust_tier_ceiling
        if hasattr(t, "numeric_weight"):
            return t.numeric_weight()
        if hasattr(t, "value"):
            return _safe_float(t.value / 4.0)
        return 0.5

    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of validation errors (empty == valid).

        Returns:
            List of human-readable error strings.
        """
        errors: List[str] = []
        if not self.phase_id:
            errors.append("phase_id must be non-empty")
        if not self.name:
            errors.append("name must be non-empty")
        lo, hi = self.expected_reward_range
        if not (0.0 <= lo <= hi <= 1.0):
            errors.append(f"expected_reward_range ({lo}, {hi}) is invalid; need 0 ≤ lo ≤ hi ≤ 1")
        if self.resource_cost < 0.0:
            errors.append(f"resource_cost {self.resource_cost} must be non-negative")
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        kind_name = self.kind.name if hasattr(self.kind, "name") else str(self.kind)
        trust_name = (
            self.trust_tier_ceiling.name
            if hasattr(self.trust_tier_ceiling, "name")
            else str(self.trust_tier_ceiling)
        )
        return {
            "phase_id": self.phase_id,
            "name": self.name,
            "kind": kind_name,
            "trust_tier_ceiling": trust_name,
            "expected_reward_range": list(self.expected_reward_range),
            "prior_expected_reward": self.prior_expected_reward(),
            "resource_cost": self.resource_cost,
            "is_terminal": self.is_terminal,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class PhaseReward:
    """Immutable reward signal observed after a phase allocation.

    Carries the full judgment context so that reward signals are traceable
    back to the evidence and agent that produced them.

    Attributes:
        reward_id: Unique identifier.
        phase_id: The phase that was allocated.
        arm_id: The bandit arm that was pulled.
        raw_reward: Observed reward in [0, 1].
        judgment_context: Context string for the judgment tuple.
        judgment_proposition: Proposition string (what was achieved).
        agent_id: Fleet member / agent that produced this reward.
        evidence_ids: Tuple of evidence references supporting the reward.
        trust_tier: Trust tier at which the reward was observed.
        observed_at: Monotonic timestamp.
    """

    reward_id: str
    phase_id: str
    arm_id: str
    raw_reward: float
    judgment_context: str
    judgment_proposition: str
    agent_id: str
    evidence_ids: Tuple[str, ...]
    trust_tier: Any  # TrustTier
    observed_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def judgment_tuple(
        self,
    ) -> Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple, Tuple[str, ...], Any, Tuple]:
        """Return the canonical 8-tuple judgment ``(c, φ, A, E, O, B, T, Π)``.

        Returns:
            8-tuple encoding this reward as a judgment.
        """
        return (
            self.judgment_context,
            self.judgment_proposition,
            (self.agent_id,),
            self.evidence_ids,
            (),
            ("theory2.tex Ch47 §2",),
            self.trust_tier,
            (),
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        trust_name = (
            self.trust_tier.name if hasattr(self.trust_tier, "name") else str(self.trust_tier)
        )
        return {
            "reward_id": self.reward_id,
            "phase_id": self.phase_id,
            "arm_id": self.arm_id,
            "raw_reward": self.raw_reward,
            "judgment_context": self.judgment_context,
            "judgment_proposition": self.judgment_proposition,
            "agent_id": self.agent_id,
            "evidence_ids": list(self.evidence_ids),
            "trust_tier": trust_name,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class AllocationPolicy:
    """Immutable allocation policy configuration for heterogeneous phase bandit.

    Extends ``BanditPolicy`` with phase-specific configuration: trust tier
    bonuses, resource-aware exploration, and phase kind preferences.

    Attributes:
        policy_id: Unique identifier.
        base_strategy: Base bandit strategy name ("ucb1", "thompson", "epsilon_greedy").
        ucb_c: UCB exploration constant.
        epsilon: Epsilon-greedy exploration probability.
        trust_bonus_weight: Additional weight given to arms with higher trust tiers [0, 1].
        resource_penalty_weight: Penalty weight for high-cost arms [0, 1].
        prefer_phase_kinds: Tuple of preferred phase kind names (empty = no preference).
        warmup_pulls: Minimum pulls before exploitation begins.
    """

    policy_id: str
    base_strategy: str
    ucb_c: float
    epsilon: float
    trust_bonus_weight: float
    resource_penalty_weight: float
    prefer_phase_kinds: Tuple[str, ...]
    warmup_pulls: int = 3

    # ------------------------------------------------------------------
    @classmethod
    def default_ucb1(cls) -> "AllocationPolicy":
        """Create a sensible UCB1-based allocation policy.

        Returns:
            A new ``AllocationPolicy`` with UCB1 strategy defaults.
        """
        return cls(
            policy_id=_make_id("apol"),
            base_strategy="ucb1",
            ucb_c=DEFAULT_UCB_C,
            epsilon=DEFAULT_EPSILON,
            trust_bonus_weight=0.15,
            resource_penalty_weight=0.10,
            prefer_phase_kinds=(),
            warmup_pulls=MIN_PULLS_BEFORE_EXPLOIT,
        )

    # ------------------------------------------------------------------
    @classmethod
    def default_thompson(cls) -> "AllocationPolicy":
        """Create a Thompson-sampling allocation policy.

        Returns:
            A new ``AllocationPolicy`` with Thompson sampling defaults.
        """
        return cls(
            policy_id=_make_id("apol"),
            base_strategy="thompson",
            ucb_c=DEFAULT_UCB_C,
            epsilon=DEFAULT_EPSILON,
            trust_bonus_weight=0.20,
            resource_penalty_weight=0.05,
            prefer_phase_kinds=("VERIFICATION",),
            warmup_pulls=MIN_PULLS_BEFORE_EXPLOIT,
        )

    # ------------------------------------------------------------------
    def adjusted_score(
        self,
        base_score: float,
        trust_weight: float = 0.5,
        resource_cost: float = 0.0,
    ) -> float:
        """Return the policy-adjusted score for an arm.

        Incorporates trust tier bonus and resource cost penalty.

        Args:
            base_score: Base bandit score from UCB1/Thompson/epsilon-greedy.
            trust_weight: Numeric trust weight of the arm's phase in [0, 1].
            resource_cost: Resource cost of the arm's phase (non-negative).

        Returns:
            Adjusted float score.
        """
        bonus = self.trust_bonus_weight * trust_weight
        penalty = self.resource_penalty_weight * _safe_float(resource_cost / 100.0)
        return _safe_float(base_score + bonus - penalty)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "policy_id": self.policy_id,
            "base_strategy": self.base_strategy,
            "ucb_c": self.ucb_c,
            "epsilon": self.epsilon,
            "trust_bonus_weight": self.trust_bonus_weight,
            "resource_penalty_weight": self.resource_penalty_weight,
            "prefer_phase_kinds": list(self.prefer_phase_kinds),
            "warmup_pulls": self.warmup_pulls,
        }


@dataclass
class BanditAllocation:
    """Mutable record of an active bandit allocation across heterogeneous phases.

    A ``BanditAllocation`` manages a pool of ``HeterogeneousPhase`` objects,
    maps each to a ``BanditArm``, and applies an ``AllocationPolicy`` to select
    the next phase on each allocation step.  It accumulates ``PhaseReward``
    observations and exposes summary statistics.

    Attributes:
        allocation_id: Unique allocation identifier.
        phases: Dict mapping phase_id → ``HeterogeneousPhase``.
        arm_map: Dict mapping phase_id → arm_id.
        allocator: The underlying ``BanditAllocator``.
        policy: The ``AllocationPolicy`` driving allocation.
        reward_history: List of observed ``PhaseReward`` objects.
        total_budget: Total budget available for this allocation.
        budget_used: Budget consumed so far.
        created_at: Monotonic timestamp.
    """

    allocation_id: str
    phases: Dict[str, HeterogeneousPhase]
    arm_map: Dict[str, str]  # phase_id → arm_id
    allocator: BanditAllocator
    policy: AllocationPolicy
    reward_history: List[PhaseReward]
    total_budget: float
    budget_used: float = 0.0
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    @classmethod
    def make(
        cls,
        phases: List[HeterogeneousPhase],
        policy: Optional[AllocationPolicy] = None,
        total_budget: float = 10_000.0,
    ) -> "BanditAllocation":
        """Factory: create a ``BanditAllocation`` from a list of phases.

        Each phase is registered as a bandit arm with prior beliefs derived
        from the phase's ``expected_reward_range``.

        Args:
            phases: List of ``HeterogeneousPhase`` objects.
            policy: Optional ``AllocationPolicy``; defaults to UCB1.
            total_budget: Total budget for this allocation.

        Returns:
            A new ``BanditAllocation`` ready for use.
        """
        pol = policy or AllocationPolicy.default_ucb1()
        bp = BanditPolicy(
            policy_id=pol.policy_id,
            strategy=pol.base_strategy,
            c_param=pol.ucb_c,
            epsilon=pol.epsilon,
            temperature=1.0,
            metadata={},
        )
        arms: Dict[str, BanditArm] = {}
        arm_map: Dict[str, str] = {}
        for ph in phases:
            lo, hi = ph.expected_reward_range
            arm = BanditArm.make(
                channel_name=ph.name,
                arm_type="solver",
                prior_alpha=1.0 + lo * 3.0,
                prior_beta=1.0 + (1.0 - hi) * 3.0,
            )
            arms[arm.arm_id] = arm
            arm_map[ph.phase_id] = arm.arm_id

        alloc = BanditAllocator(
            allocator_id=_make_id("balloc"),
            arms=arms,
            stats={arm_id: ArmStats(arm_id, 0, 0.0, 0.0, [], 0.0) for arm_id in arms},
            policy=bp,
            budget=int(total_budget),
            total_pulls=0,
            allocation_history=[],
        )

        return cls(
            allocation_id=_make_id("ba"),
            phases={ph.phase_id: ph for ph in phases},
            arm_map=arm_map,
            allocator=alloc,
            policy=pol,
            reward_history=[],
            total_budget=total_budget,
        )

    # ------------------------------------------------------------------
    def _phase_for_arm(self, arm_id: str) -> Optional[HeterogeneousPhase]:
        """Return the phase corresponding to *arm_id*, or ``None``.

        Args:
            arm_id: Bandit arm ID to look up.

        Returns:
            ``HeterogeneousPhase`` or ``None``.
        """
        for phase_id, aid in self.arm_map.items():
            if aid == arm_id:
                return self.phases.get(phase_id)
        return None

    # ------------------------------------------------------------------
    def select_phase(self, available_budget: float = 1_000.0) -> Optional[HeterogeneousPhase]:
        """Select the next phase to allocate budget to.

        Uses the underlying ``BanditAllocator`` to select an arm, then applies
        the ``AllocationPolicy`` trust and resource adjustments.

        Args:
            available_budget: Budget available for this step.

        Returns:
            The selected ``HeterogeneousPhase``, or ``None`` if no arms are registered.
        """
        if not self.allocator.arms:
            return None

        # Apply policy-adjusted scores to arm selection
        arm_scores: Dict[str, float] = {}
        total_pulls = sum(self.allocator.stats[a].pull_count for a in self.allocator.arms)

        for arm_id, arm in self.allocator.arms.items():
            stats = self.allocator.stats[arm_id]
            # Warm-up: pull arms that haven't reached warmup_pulls
            if stats.pull_count < self.policy.warmup_pulls:
                arm_scores[arm_id] = float("inf")
                continue

            # Base bandit score
            if self.policy.base_strategy == "ucb1":
                base = _safe_float(stats.mean_reward()) + self.policy.ucb_c * math.sqrt(
                    math.log(max(1, total_pulls)) / max(1, stats.pull_count)
                )
            elif self.policy.base_strategy == "thompson":
                base = _safe_float(stats.thompson_sample())
            else:  # epsilon_greedy
                if random.random() < self.policy.epsilon:
                    base = float("inf")
                else:
                    base = _safe_float(stats.mean_reward())

            ph = self._phase_for_arm(arm_id)
            trust_w = ph.trust_weight() if ph else 0.5
            res_cost = ph.resource_cost if ph else 0.0
            arm_scores[arm_id] = self.policy.adjusted_score(base, trust_w, res_cost)

        if not arm_scores:
            return None

        best_arm_id = max(arm_scores, key=lambda a: arm_scores[a])
        return self._phase_for_arm(best_arm_id)

    # ------------------------------------------------------------------
    def record_reward(self, phase: HeterogeneousPhase, raw_reward: float) -> PhaseReward:
        """Record an observed reward for *phase* and update bandit statistics.

        Args:
            phase: The phase for which a reward was observed.
            raw_reward: The observed reward in [0, 1].

        Returns:
            The newly created ``PhaseReward``.
        """
        arm_id = self.arm_map.get(phase.phase_id, "")
        clamped = _clamp(raw_reward, 0.0, 1.0)

        # Update bandit stats
        if arm_id and arm_id in self.allocator.stats:
            self.allocator.stats[arm_id].record_reward(clamped)
            self.budget_used += phase.resource_cost

        pr = PhaseReward(
            reward_id=_make_id("preward"),
            phase_id=phase.phase_id,
            arm_id=arm_id,
            raw_reward=clamped,
            judgment_context=phase.phase_id,
            judgment_proposition=f"Phase {phase.name} achieved reward {clamped:.3f}",
            agent_id=self.allocation_id,
            evidence_ids=(arm_id,),
            trust_tier=phase.trust_tier_ceiling,
        )
        self.reward_history.append(pr)
        return pr

    # ------------------------------------------------------------------
    def mean_reward(self) -> float:
        """Return the mean reward across all observed PhaseReward objects.

        Returns:
            Float in [0, 1]; 0.0 if no rewards have been observed.
        """
        if not self.reward_history:
            return 0.0
        return _safe_float(sum(r.raw_reward for r in self.reward_history) / len(self.reward_history))

    # ------------------------------------------------------------------
    def best_phase(self) -> Optional[HeterogeneousPhase]:
        """Return the phase with the highest mean observed reward.

        Returns:
            ``HeterogeneousPhase`` or ``None`` if no rewards have been observed.
        """
        if not self.reward_history:
            return None
        totals: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        for r in self.reward_history:
            totals[r.phase_id] = totals.get(r.phase_id, 0.0) + r.raw_reward
            counts[r.phase_id] = counts.get(r.phase_id, 0) + 1
        best_pid = max(totals, key=lambda pid: totals[pid] / max(1, counts.get(pid, 1)))
        return self.phases.get(best_pid)

    # ------------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        """Return a summary dict of the current allocation state.

        Returns:
            Dict with budget, reward, and arm statistics.
        """
        return {
            "allocation_id": self.allocation_id,
            "total_budget": self.total_budget,
            "budget_used": self.budget_used,
            "budget_remaining": max(0.0, self.total_budget - self.budget_used),
            "mean_reward": self.mean_reward(),
            "total_rewards_observed": len(self.reward_history),
            "phase_count": len(self.phases),
        }


# ===========================================================================
# Module-level entry-point functions for heterogeneous phase bandit allocation
# ===========================================================================


def allocate_with_bandit(
    phases: List[HeterogeneousPhase],
    n_steps: int = 10,
    policy: Optional[AllocationPolicy] = None,
    total_budget: float = 10_000.0,
    reward_fn: Optional[Any] = None,
) -> Tuple[BanditAllocation, List[PhaseReward]]:
    """Run *n_steps* of bandit allocation across *phases* and collect rewards.

    Convenience function that creates a ``BanditAllocation``, runs *n_steps*
    of phase selection, calls *reward_fn* (or uses the phase prior) for each
    selected phase, and returns the allocation and reward history.

    Args:
        phases: List of ``HeterogeneousPhase`` objects.
        n_steps: Number of allocation steps to run.
        policy: Optional ``AllocationPolicy``; defaults to UCB1.
        total_budget: Total budget for the allocation.
        reward_fn: Optional callable ``(phase) → float`` for simulated rewards.
            Defaults to using the phase's ``prior_expected_reward()`` with noise.

    Returns:
        A ``(BanditAllocation, rewards)`` tuple where *rewards* is the list
        of ``PhaseReward`` objects observed during the run.
    """
    allocation = BanditAllocation.make(phases=phases, policy=policy, total_budget=total_budget)
    rewards: List[PhaseReward] = []

    for _ in range(n_steps):
        phase = allocation.select_phase(available_budget=total_budget / max(1, n_steps))
        if phase is None:
            break
        if reward_fn is not None:
            try:
                raw = _safe_float(reward_fn(phase))
            except Exception:
                raw = phase.prior_expected_reward()
        else:
            noise = (random.random() - 0.5) * 0.1
            raw = _clamp(phase.prior_expected_reward() + noise, 0.0, 1.0)
        pr = allocation.record_reward(phase, raw)
        rewards.append(pr)

    return allocation, rewards


def update_bandit_arm(
    allocation: BanditAllocation,
    phase_id: str,
    reward: float,
) -> Optional[PhaseReward]:
    """Update the bandit arm for *phase_id* with *reward* in *allocation*.

    Convenience function that looks up the phase, clamps the reward, and
    calls ``allocation.record_reward``.

    Args:
        allocation: The ``BanditAllocation`` to update.
        phase_id: ID of the phase whose arm should be updated.
        reward: Observed reward in [0, 1].

    Returns:
        The new ``PhaseReward`` record, or ``None`` if *phase_id* is unknown.
    """
    phase = allocation.phases.get(phase_id)
    if phase is None:
        _log.warning("update_bandit_arm: unknown phase_id %s", phase_id)
        return None
    return allocation.record_reward(phase, reward)


def select_next_phase(
    allocation: BanditAllocation,
    available_budget: float = 1_000.0,
) -> Optional[HeterogeneousPhase]:
    """Select the next phase to execute using the bandit policy in *allocation*.

    Convenience wrapper around ``BanditAllocation.select_phase``.

    Args:
        allocation: The ``BanditAllocation`` to use for selection.
        available_budget: Budget available for this step.

    Returns:
        The selected ``HeterogeneousPhase``, or ``None`` if no arms are registered.
    """
    return allocation.select_phase(available_budget=available_budget)


def compute_phase_reward(
    phase: HeterogeneousPhase,
    evidence_quality: float = 0.5,
    coverage_achieved: float = 0.5,
    trust_weight_override: Optional[float] = None,
) -> float:
    """Compute a synthetic reward signal for *phase* given observed quality.

    Combines evidence quality, coverage achieved, and trust tier weight into
    a single reward signal in [0, 1].  Useful for testing and simulation.

    Args:
        phase: The ``HeterogeneousPhase`` that was executed.
        evidence_quality: Quality of evidence produced in [0, 1].
        coverage_achieved: Fraction of target domain covered in [0, 1].
        trust_weight_override: Optional override for the trust weight in [0, 1].

    Returns:
        A reward signal in [0, 1].
    """
    trust_w = (
        trust_weight_override
        if trust_weight_override is not None
        else phase.trust_weight()
    )
    reward = (
        0.40 * _clamp(evidence_quality, 0.0, 1.0)
        + 0.40 * _clamp(coverage_achieved, 0.0, 1.0)
        + 0.20 * _clamp(trust_w, 0.0, 1.0)
    )
    return _clamp(reward, 0.0, 1.0)


# ===========================================================================
# Guarded optional imports from the broader jugeo ecosystem
# ===========================================================================

try:
    from jugeo.orchestration.frontier_phases.models import (  # type: ignore[import]
        PhaseKind,
        TransitionTrigger,
        PhaseDescriptor,
        PhaseTransitionRecord,
        PhaseHistory,
        StallDetector,
        ConvergenceCertificate,
        PhaseHealthStatus,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier import (  # type: ignore[import]
        Frontier,
        FrontierNode,
        FrontierHistory,
        PhaseTransition,
        BackpressureController,
        FrontierBudget,
        FrontierDiversity,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (  # type: ignore[import]
        OrchestratorState,
        SemanticMove,
        ConvergenceMonitor,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import (  # type: ignore[import]
        TrustLevel,
        TrustAlgebra,
        TrustProfile,
    )
except Exception:
    pass


# ===========================================================================
# Smoke test / entry point
# ===========================================================================

if __name__ == "__main__":
    import pprint

    print("Running s02 smoke test …")

    # 1. Create a policy and coordinator
    policy = BanditPolicy.ucb1(c=1.414)
    coordinator = BanditAllocationCoordinator.make(
        total_budget=10_000,
        policy=policy,
    )
    print(f"  [1] BanditAllocationCoordinator created: {coordinator.coordinator_id}")

    # 2. Register 3 channels: solver, agent, verifier
    coordinator.register_channel("z3_solver", "solver", metadata={"version": "4.12"})
    coordinator.register_channel("gpt4_agent", "agent", metadata={"model": "gpt-4"})
    coordinator.register_channel("lean_verifier", "verifier", metadata={"lean": "4"})
    assert len(coordinator.channel_registry) == 3
    print(f"  [2] Registered 3 channels: {list(coordinator.channel_registry.keys())}")

    # 3. Create analyzer
    analyzer = BanditAllocationAnalyzer(
        analyzer_id=_make_id("analyzer"),
        round_snapshots=[],
        arm_count=3,
    )

    # 4. Run 5 allocation rounds with simulated rewards
    simulated_rewards = {
        "z3_solver": [0.8, 0.7, 0.85, 0.9, 0.75],
        "gpt4_agent": [0.6, 0.65, 0.7, 0.55, 0.6],
        "lean_verifier": [0.9, 0.88, 0.92, 0.85, 0.91],
    }
    reward_iters = {name: iter(rewards) for name, rewards in simulated_rewards.items()}

    for round_idx in range(5):
        record = coordinator.run_round(available_budget=2_000)
        # Simulate a reward for the channel that was just pulled
        channel_name = record.channel_name
        try:
            reward = next(reward_iters[channel_name])
        except (StopIteration, KeyError):
            reward = 0.5
        coordinator.update_reward(channel_name, reward)
        snapshot = coordinator.allocation_summary()
        analyzer.record_round(snapshot)
        print(
            f"  [4.{round_idx+1}] round={coordinator.round_count}, "
            f"arm={record.arm_id}, channel={channel_name}, reward={reward:.2f}, "
            f"phase={coordinator.phase}"
        )

    # 5. Verify best channel
    best = coordinator.best_channel()
    print(f"  [5] Best channel after 5 rounds: {best}")

    # 6. Check analyzer metrics
    report = analyzer.report()
    print(f"  [6] Analyzer: regret={report['cumulative_regret']:.4f}, "
          f"exploration_ratio={report['exploration_ratio']:.4f}, "
          f"convergence={report['convergence_indicator']:.4f}")
    print(f"       Channel ranking: {report['channel_ranking']}")

    # 7. Issue witness and print
    witness = BanditAllocationWitness.issue(coordinator, analyzer)
    print("  [7] BanditAllocationWitness issued:")
    pprint.pprint(witness.to_dict(), indent=4)
    print(witness.certify_text())
    print(f"       is_reliable: {witness.is_reliable()}")

    # 8. Verify BanditArm methods
    arm_sample = BanditArm.make("test_sampler", "sampler", prior_alpha=2.0, prior_beta=3.0)
    expected_r = arm_sample.expected_reward()
    ci = arm_sample.confidence_interval()
    assert 0.0 < expected_r < 1.0
    assert 0.0 <= ci[0] <= ci[1] <= 1.0
    print(f"  [8] BanditArm: expected_reward={expected_r:.4f}, CI={ci}")

    # 9. Verify AllocationRecord factory
    rec = AllocationRecord.make("arm_x", "test_channel", 500)
    assert rec.tokens_allocated == 500
    assert rec.reward_observed == 0.0
    d = rec.to_dict()
    assert d["channel_name"] == "test_channel"
    print(f"  [9] AllocationRecord verified: {rec.record_id}")

    # 10. Verify ArmStats methods
    stats = ArmStats("arm_s", 0, 0.0, 0.0, [], 0.0)
    for r in [0.8, 0.6, 0.9, 0.7, 0.85]:
        stats.record_reward(r)
    assert stats.pull_count == 5
    assert stats.mean_reward() > 0.0
    assert stats.variance() >= 0.0
    ts = stats.thompson_sample()
    assert 0.0 < ts < 1.0
    print(f"  [10] ArmStats: mean={stats.mean_reward():.4f}, "
          f"variance={stats.variance():.4f}, thompson_sample={ts:.4f}")

    print("\ns02 smoke test passed")

    # ===========================================================================
    # Heterogeneous phase bandit allocation smoke test (extended)
    # ===========================================================================

    print("\n=== Heterogeneous phase bandit allocation smoke test ===\n")

    # Build phases with trust tiers
    tier = _EffectiveTrustTier
    phase_configs = [
        ("exploration", _PhaseKindFallback.EXPLORATION, tier.PROPOSAL, (0.3, 0.6), 10.0),
        ("exploitation", _PhaseKindFallback.EXPLOITATION, tier.REVIEWED, (0.5, 0.8), 20.0),
        ("verification", _PhaseKindFallback.VERIFICATION, tier.PROOF_BACKED, (0.7, 0.95), 50.0),
    ]
    phases_list: List[HeterogeneousPhase] = []
    for name, kind, trust, reward_range, cost in phase_configs:
        ph = HeterogeneousPhase(
            phase_id=_make_id("ph"),
            name=name,
            kind=kind,
            trust_tier_ceiling=trust,
            expected_reward_range=reward_range,
            resource_cost=cost,
        )
        errs = ph.validate()
        assert not errs, f"Phase {name} validation failed: {errs}"
        phases_list.append(ph)

    print(f"  Created {len(phases_list)} heterogeneous phases")

    # AllocationPolicy
    alloc_policy = AllocationPolicy.default_ucb1()
    print(f"  AllocationPolicy: strategy={alloc_policy.base_strategy}, trust_bonus={alloc_policy.trust_bonus_weight}")
    print(f"  AllocationPolicy dict: {alloc_policy.to_dict()}")

    # BanditAllocation.make
    ba = BanditAllocation.make(phases=phases_list, policy=alloc_policy, total_budget=5_000.0)
    print(f"  BanditAllocation created: {ba.allocation_id}")

    # Run 10 steps of allocation
    for step in range(10):
        phase = select_next_phase(ba, available_budget=500.0)
        if phase is None:
            print(f"  Step {step}: no phase selected")
            continue
        reward_val = compute_phase_reward(
            phase,
            evidence_quality=0.6 + step * 0.02,
            coverage_achieved=0.5 + step * 0.03,
        )
        pr = update_bandit_arm(ba, phase.phase_id, reward_val)
        assert pr is not None
        tup = pr.judgment_tuple()
        assert len(tup) == 8, "PhaseReward judgment tuple must be 8-tuple"
        print(
            f"  Step {step+1:2d}: phase={phase.name:12s}  reward={reward_val:.3f}  "
            f"trust={phase.trust_weight():.2f}  budget_used={ba.budget_used:.1f}"
        )

    summary = ba.summary()
    print(f"\n  Allocation summary: {summary}")
    best_ph = ba.best_phase()
    if best_ph:
        print(f"  Best phase: {best_ph.name}  (trust_tier={best_ph.trust_tier_ceiling})")

    # allocate_with_bandit convenience function
    alloc2, rewards2 = allocate_with_bandit(
        phases=phases_list,
        n_steps=8,
        policy=AllocationPolicy.default_thompson(),
        total_budget=4_000.0,
    )
    print(f"\n  allocate_with_bandit returned {len(rewards2)} rewards")
    print(f"  Mean reward: {alloc2.mean_reward():.4f}")

    print("\nExtended s02 smoke test passed.")
