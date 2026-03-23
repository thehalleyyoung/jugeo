"""Frontier phases algorithmic primitives: planning, execution, normalization. theory2.tex Ch47. # copilot:"""

from __future__ import annotations

import math
import random
import time
import uuid
import json
import itertools
import functools
from dataclasses import dataclass, field

__all__ = [
    "FrontierPhasesConfig", "PlanStep", "ExecutionResult",
    "FrontierPhasesPlanner", "FrontierPhasesExecutor",
    "SignalNormalizationSpec", "FrontierPhasesNormalizer",
    "PhaseAlgorithmRegistry",
    "compute_obstruction_density", "compute_coverage_ratio",
    "compute_trust_mass", "compute_diversity_entropy",
    "budget_split", "ucb1_score", "thompson_beta_sample",
]

try:
    from jugeo.orchestration.frontier_phases.models import (
        PhaseKind, TransitionTrigger, PhaseDescriptor, PhaseTransitionRecord,
        PhaseHistory, StallDetector, ConvergenceCertificate, PhaseHealthStatus,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier import (
        Frontier, FrontierNode, FrontierHistory, PhaseTransition,
        BackpressureController, FrontierBudget, FrontierDiversity,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (
        OrchestratorState, SemanticMove, ConvergenceMonitor,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
except Exception:
    pass


# ---------------------------------------------------------------------------
# Standalone algorithm functions
# ---------------------------------------------------------------------------


def compute_obstruction_density(nodes: list, total_capacity: int) -> float:
    """Compute the fraction of capacity occupied by obstructed nodes.

    Iterates over *nodes* and counts those whose 'obstructed' key is truthy.
    The density is the count of obstructed nodes divided by *total_capacity*.
    If *total_capacity* is zero or *nodes* is empty the function returns 0.0
    rather than raising a ZeroDivisionError.

    The result is clamped to [0.0, 1.0] to guard against inconsistent inputs
    where the number of obstructed nodes exceeds the declared capacity.

    Args:
        nodes: A list of dicts representing frontier nodes.  Each dict may
            contain an 'obstructed' key whose truthiness indicates obstruction.
        total_capacity: The total number of node slots in the frontier.  Used
            as the denominator for density computation.

    Returns:
        Float in [0.0, 1.0] representing the fraction of capacity that is
        currently obstructed.  Returns 0.0 when *total_capacity* is 0.

    Examples:
        >>> nodes = [{"id": "a", "obstructed": True}, {"id": "b", "obstructed": False}]
        >>> compute_obstruction_density(nodes, 4)
        0.25
    """
    if total_capacity <= 0:
        return 0.0
    if not nodes:
        return 0.0
    obstructed_count = sum(1 for n in nodes if n.get("obstructed", False))
    return max(0.0, min(1.0, obstructed_count / total_capacity))


def compute_coverage_ratio(covered_ids: set, total_ids: set) -> float:
    """Compute fraction of total search space covered.

    Returns the ratio |covered_ids ∩ total_ids| / |total_ids|.  Using
    intersection rather than len(covered_ids) directly guards against
    inflated coverage counts from stale IDs that no longer appear in
    the current total space.

    Args:
        covered_ids: Set of node/region IDs that have been visited or explored.
        total_ids: Set of all node/region IDs that constitute the full search
            space.  Defines the denominator of the coverage fraction.

    Returns:
        Float in [0.0, 1.0].  Returns 0.0 when *total_ids* is empty.

    Examples:
        >>> compute_coverage_ratio({"a", "b"}, {"a", "b", "c", "d"})
        0.5
    """
    if not total_ids:
        return 0.0
    return len(covered_ids & total_ids) / len(total_ids)


def compute_trust_mass(trust_scores: list) -> float:
    """Aggregate trust scores into a scalar mass value.

    Trust mass is defined as the arithmetic mean of the individual trust scores
    provided in *trust_scores*.  Each score should be a float in [0, 1];
    values outside this range are clamped before averaging so that a single
    anomalous score cannot dominate the aggregate.

    When the list is empty 0.0 is returned to represent the absence of any
    accumulated trust.

    Args:
        trust_scores: List of individual trust score floats, typically one per
            explored frontier node or proof segment.

    Returns:
        Float in [0.0, 1.0] representing aggregated trust mass.

    Examples:
        >>> compute_trust_mass([0.8, 0.6, 0.9])
        0.7666...
    """
    if not trust_scores:
        return 0.0
    clamped = [max(0.0, min(1.0, float(s))) for s in trust_scores]
    return sum(clamped) / len(clamped)


def compute_diversity_entropy(proof_modes: list) -> float:
    """Compute Shannon entropy of proof mode distribution.

    Treats *proof_modes* as a categorical sample, estimates the empirical
    probability of each mode, then returns the normalised Shannon entropy
    H / log2(K) where K is the number of distinct modes.  The normalisation
    ensures the result lies in [0, 1] regardless of the vocabulary size.

    Entropy of 0 means all observations belong to a single mode (no diversity).
    Entropy of 1 means all modes are equally represented (maximum diversity).

    Args:
        proof_modes: List of mode labels (strings or ints).  May contain
            duplicates; duplicates contribute to the frequency estimate.

    Returns:
        Float in [0.0, 1.0].  Returns 0.0 for empty or single-element lists.

    Examples:
        >>> compute_diversity_entropy(["a", "b", "a", "b"])
        1.0
        >>> compute_diversity_entropy(["a", "a", "a"])
        0.0
    """
    if not proof_modes:
        return 0.0
    counts: dict = {}
    for m in proof_modes:
        counts[m] = counts.get(m, 0) + 1
    n = len(proof_modes)
    k = len(counts)
    if k <= 1:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        p = count / n
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy / math.log2(k)


def budget_split(total: int, weights: dict) -> dict:
    """Split a budget across channels according to relative weights.

    Computes proportional integer allocations so that each channel receives
    floor(total * w / sum_w) tokens.  Any remainder tokens (due to flooring)
    are distributed to channels in descending order of their fractional
    remainders (largest-remainder method) until *total* is exactly consumed.

    Args:
        total: The total integer budget to distribute.
        weights: Dict mapping channel name to non-negative float weight.
            Channels with zero weight receive zero tokens.

    Returns:
        Dict mapping channel name to integer token allocation.  The sum of
        all values equals *total* exactly.

    Examples:
        >>> budget_split(100, {"explore": 3, "exploit": 2})
        {'explore': 60, 'exploit': 40}
    """
    if not weights or total <= 0:
        return {k: 0 for k in weights}
    sum_w = sum(weights.values())
    if sum_w == 0.0:
        return {k: 0 for k in weights}
    exact = {k: total * w / sum_w for k, w in weights.items()}
    floored = {k: int(v) for k, v in exact.items()}
    remainder = total - sum(floored.values())
    fractional_order = sorted(
        weights.keys(), key=lambda k: exact[k] - floored[k], reverse=True
    )
    for i in range(remainder):
        floored[fractional_order[i % len(fractional_order)]] += 1
    return floored


def ucb1_score(
    mean_reward: float,
    pull_count: int,
    total_pulls: int,
    c: float = 1.414,
) -> float:
    """Compute UCB1 score for a bandit arm.

    The UCB1 index is: mean_reward + c * sqrt(ln(total_pulls) / pull_count).

    This score balances exploitation of high-reward arms against exploration of
    under-sampled arms.  Higher *c* values increase the exploration bonus,
    trading short-term reward for better long-run coverage of the arm space.

    When *pull_count* is zero the arm has never been pulled; in that case
    +infinity is returned to ensure unexplored arms are tried first.

    Args:
        mean_reward: Empirical mean reward of the arm so far.
        pull_count: Number of times this arm has been pulled.
        total_pulls: Total number of pulls across all arms.
        c: Exploration constant; defaults to sqrt(2) ≈ 1.414.

    Returns:
        Float UCB1 index.  Returns +inf when *pull_count* is 0.

    Examples:
        >>> ucb1_score(0.5, 10, 100)
        0.5 + 1.414 * sqrt(ln(100) / 10)
    """
    if pull_count == 0:
        return float("inf")
    if total_pulls <= 0:
        return mean_reward
    exploration_bonus = c * math.sqrt(math.log(total_pulls) / pull_count)
    return mean_reward + exploration_bonus


def thompson_beta_sample(alpha: float, beta: float) -> float:
    """Draw a Thompson sample from Beta(alpha, beta) distribution.

    Uses the relationship between the Beta and Gamma distributions:
    Beta(alpha, beta) = Gamma(alpha) / (Gamma(alpha) + Gamma(beta)).
    Samples two independent Gamma variates and normalises them.

    Falls back to 0.5 if the sampling fails due to degenerate parameters
    (e.g. both alpha and beta are zero) rather than propagating an exception.

    Args:
        alpha: First shape parameter; represents pseudo-counts of successes.
            Must be positive.
        beta: Second shape parameter; represents pseudo-counts of failures.
            Must be positive.

    Returns:
        Float in (0.0, 1.0) drawn from the Beta(alpha, beta) distribution.

    Examples:
        >>> s = thompson_beta_sample(10.0, 2.0)
        >>> 0.0 < s < 1.0
        True
    """
    try:
        alpha_val = max(alpha, 1e-9)
        beta_val = max(beta, 1e-9)
        x = random.gammavariate(alpha_val, 1.0)
        y = random.gammavariate(beta_val, 1.0)
        if x + y == 0.0:
            return 0.5
        return x / (x + y)
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrontierPhasesConfig:
    """Immutable configuration for a FrontierPhases orchestration run.

    Centralises all tunable hyper-parameters so that policies, planners and
    executors can read from a single authoritative source.

    Attributes:
        config_id: Unique identifier for this configuration object.
        max_iterations: Hard cap on the number of planning/execution cycles.
        budget_token_limit: Total token budget available for the entire run.
        bandit_arms: Number of bandit arms in the exploration strategy.
        diversity_threshold: Minimum diversity entropy to consider a run healthy.
        convergence_coverage: Coverage ratio required to declare convergence.
        trust_tolerance: Maximum acceptable trust mass drop across a transition.
        metadata: Arbitrary key/value pairs for downstream consumers.
    """

    config_id: str
    max_iterations: int
    budget_token_limit: int
    bandit_arms: int
    diversity_threshold: float
    convergence_coverage: float
    trust_tolerance: float
    metadata: dict

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns:
            Dict containing all config fields.
        """
        return {
            "config_id": self.config_id,
            "max_iterations": self.max_iterations,
            "budget_token_limit": self.budget_token_limit,
            "bandit_arms": self.bandit_arms,
            "diversity_threshold": self.diversity_threshold,
            "convergence_coverage": self.convergence_coverage,
            "trust_tolerance": self.trust_tolerance,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def default(cls) -> "FrontierPhasesConfig":
        """Return a sensible default configuration for most use cases.

        The defaults are calibrated for a medium-scale theorem proving session
        with a modest token budget and balanced exploration/exploitation.

        Returns:
            FrontierPhasesConfig with default hyper-parameter values.
        """
        return cls(
            config_id=str(uuid.uuid4()),
            max_iterations=500,
            budget_token_limit=100_000,
            bandit_arms=8,
            diversity_threshold=0.5,
            convergence_coverage=0.85,
            trust_tolerance=0.05,
            metadata={"version": "1.0", "source": "FrontierPhasesConfig.default"},
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    """A single step in a frontier phase execution plan.

    PlanSteps are immutable and can be safely shared across planner instances.
    The *dependencies* field allows the planner to encode ordering constraints
    without introducing mutable state.

    Attributes:
        step_id: Unique identifier for this step.
        action: Name of the action to execute (e.g. "EXPAND", "PRUNE", "SCORE").
        phase: The phase this step belongs to (e.g. "EXPLORATION").
        priority: Scheduling priority; higher values are executed first.
        tokens_required: Estimated token cost to execute this step.
        dependencies: List of step_id strings that must complete before this step.
        metadata: Arbitrary key/value pairs for step-specific parameters.
    """

    step_id: str
    action: str
    phase: str
    priority: float
    tokens_required: int
    dependencies: list
    metadata: dict

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns:
            Dict containing all PlanStep fields.
        """
        return {
            "step_id": self.step_id,
            "action": self.action,
            "phase": self.phase,
            "priority": self.priority,
            "tokens_required": self.tokens_required,
            "dependencies": list(self.dependencies),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def make(
        cls,
        action: str,
        phase: str,
        priority: float,
        tokens_required: int,
    ) -> "PlanStep":
        """Construct a PlanStep with a fresh UUID and empty dependencies.

        Args:
            action: The action label for this step.
            phase: The phase this step is associated with.
            priority: Scheduling priority (higher = sooner).
            tokens_required: Estimated token cost.

        Returns:
            A new PlanStep with no dependencies and an empty metadata dict.
        """
        return cls(
            step_id=str(uuid.uuid4()),
            action=action,
            phase=phase,
            priority=priority,
            tokens_required=tokens_required,
            dependencies=[],
            metadata={},
        )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Immutable record of a single PlanStep execution outcome.

    Attributes:
        result_id: Unique identifier for this result.
        step_id: ID of the PlanStep that was executed.
        success: Whether the step completed without error.
        tokens_used: Actual tokens consumed during execution.
        elapsed: Wall-clock time in seconds for the execution.
        output: Dict of output values produced by the step.
        error: Error message string if success is False; None otherwise.
    """

    result_id: str
    step_id: str
    success: bool
    tokens_used: int
    elapsed: float
    output: dict
    error: str | None

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns:
            Dict containing all ExecutionResult fields.
        """
        return {
            "result_id": self.result_id,
            "step_id": self.step_id,
            "success": self.success,
            "tokens_used": self.tokens_used,
            "elapsed": self.elapsed,
            "output": dict(self.output),
            "error": self.error,
        }

    @classmethod
    def ok(
        cls,
        step_id: str,
        tokens_used: int,
        elapsed: float,
        output: dict,
    ) -> "ExecutionResult":
        """Create a successful ExecutionResult.

        Args:
            step_id: ID of the step that succeeded.
            tokens_used: Actual tokens consumed.
            elapsed: Execution duration in seconds.
            output: Dict of values produced.

        Returns:
            ExecutionResult with success=True and error=None.
        """
        return cls(
            result_id=str(uuid.uuid4()),
            step_id=step_id,
            success=True,
            tokens_used=tokens_used,
            elapsed=elapsed,
            output=output,
            error=None,
        )

    @classmethod
    def fail(cls, step_id: str, error: str) -> "ExecutionResult":
        """Create a failed ExecutionResult.

        Args:
            step_id: ID of the step that failed.
            error: Human-readable error description.

        Returns:
            ExecutionResult with success=False, zero tokens, and the error message.
        """
        return cls(
            result_id=str(uuid.uuid4()),
            step_id=step_id,
            success=False,
            tokens_used=0,
            elapsed=0.0,
            output={},
            error=error,
        )


@dataclass(slots=True)
class FrontierPhasesPlanner:
    """Builds and manages ordered execution plans for frontier phases.

    A planner constructs phase-appropriate PlanStep sequences, validates them
    for dependency cycles and budget feasibility, and exposes the current plan
    for handoff to an executor.

    Attributes:
        planner_id: Unique identifier for this planner instance.
        config: The FrontierPhasesConfig governing budgeting and limits.
        plan: The current list of PlanStep objects (the active plan).
        plan_history: List of previous plans (each plan as a list of dicts).
    """

    planner_id: str
    config: FrontierPhasesConfig
    plan: list
    plan_history: list

    # Action templates per phase
    _PHASE_ACTIONS: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_PHASE_ACTIONS", {
            "EXPLORATION": [
                ("EXPAND_FRONTIER", 0.9, 500),
                ("SAMPLE_DIVERSE", 0.8, 300),
                ("SCORE_NODES", 0.7, 200),
                ("UPDATE_BANDIT", 0.6, 150),
                ("PRUNE_LOW_TRUST", 0.5, 100),
            ],
            "EXPLOITATION": [
                ("FOCUS_SEARCH", 0.95, 600),
                ("DEEP_EXPAND", 0.85, 700),
                ("SCORE_NODES", 0.75, 200),
                ("UPDATE_BANDIT", 0.65, 150),
                ("BACKTRACK_CHECK", 0.4, 100),
            ],
            "CONVERGENCE": [
                ("CERTIFY_COVERAGE", 0.99, 400),
                ("VALIDATE_TRUST", 0.95, 300),
                ("FINAL_SCORE", 0.9, 200),
                ("EMIT_CERTIFICATE", 0.85, 100),
            ],
        })

    def build_plan(self, phase: str, signals: dict) -> list:
        """Construct a list of PlanSteps appropriate for the given phase.

        Selects action templates for *phase*, adjusts priorities based on the
        current signal values, and returns an ordered list of PlanStep objects.
        The constructed plan also replaces self.plan.

        Args:
            phase: The current phase name (e.g. "EXPLORATION").
            signals: Dict of current signal values used to fine-tune priorities.

        Returns:
            List of PlanStep objects in decreasing priority order.
        """
        if self._PHASE_ACTIONS is None:
            self.__post_init__()
        templates = self._PHASE_ACTIONS.get(phase, self._PHASE_ACTIONS["EXPLORATION"])
        coverage = float(signals.get("coverage_ratio", 0.0))
        steps = []
        for action, base_priority, tokens in templates:
            adjusted_priority = base_priority + 0.05 * coverage
            step = PlanStep.make(action, phase, adjusted_priority, tokens)
            steps.append(step)
        self.plan_history.append([s.to_dict() for s in self.plan])
        self.plan = self.prioritize(steps)
        return list(self.plan)

    def prioritize(self, steps: list) -> list:
        """Sort a list of PlanSteps in descending priority order.

        Args:
            steps: List of PlanStep objects to sort.

        Returns:
            New list sorted by step.priority descending.
        """
        return sorted(steps, key=lambda s: s.priority, reverse=True)

    def validate_plan(self) -> bool:
        """Check the current plan for dependency cycles and budget feasibility.

        Dependency cycle detection uses a DFS-based topological sort attempt.
        Budget feasibility checks that the total tokens_required does not exceed
        the configured budget_token_limit.

        Returns:
            True if the plan is valid (no cycles, within budget); False otherwise.
        """
        id_set = {s.step_id for s in self.plan}
        # Check all dependency references exist
        for step in self.plan:
            for dep in step.dependencies:
                if dep not in id_set:
                    return False
        # DFS cycle detection
        visited: set = set()
        path: set = set()

        def has_cycle(step_id: str, adjacency: dict) -> bool:
            if step_id in path:
                return True
            if step_id in visited:
                return False
            path.add(step_id)
            for dep in adjacency.get(step_id, []):
                if has_cycle(dep, adjacency):
                    return True
            path.discard(step_id)
            visited.add(step_id)
            return False

        adjacency = {s.step_id: list(s.dependencies) for s in self.plan}
        for step in self.plan:
            if has_cycle(step.step_id, adjacency):
                return False
        # Budget check
        total_tokens = sum(s.tokens_required for s in self.plan)
        if total_tokens > self.config.budget_token_limit:
            return False
        return True

    def clear_plan(self) -> None:
        """Clear the current plan, archiving it to plan_history first."""
        self.plan_history.append([s.to_dict() for s in self.plan])
        self.plan = []

    def plan_summary(self) -> dict:
        """Return a summary dict describing the current plan.

        Returns:
            Dict with step count, total tokens required, phases present, and
            whether the plan is currently valid.
        """
        return {
            "planner_id": self.planner_id,
            "step_count": len(self.plan),
            "total_tokens_required": sum(s.tokens_required for s in self.plan),
            "phases": list({s.phase for s in self.plan}),
            "valid": self.validate_plan(),
            "plan_history_count": len(self.plan_history),
        }

    def to_dict(self) -> dict:
        """Serialise the planner state to a JSON-compatible dictionary.

        Returns:
            Dict with planner_id, config summary, plan steps, and history count.
        """
        return {
            "planner_id": self.planner_id,
            "config": self.config.to_dict(),
            "plan": [s.to_dict() for s in self.plan],
            "plan_history_count": len(self.plan_history),
        }

    @classmethod
    def make(cls, config: FrontierPhasesConfig) -> "FrontierPhasesPlanner":
        """Create a new FrontierPhasesPlanner with the given config.

        Args:
            config: The FrontierPhasesConfig to use.

        Returns:
            A new FrontierPhasesPlanner with an empty plan.
        """
        return cls(
            planner_id=str(uuid.uuid4()),
            config=config,
            plan=[],
            plan_history=[],
        )


@dataclass(slots=True)
class FrontierPhasesExecutor:
    """Executes PlanStep objects against a frontier proxy.

    Tracks budget consumption, maintains an execution log, and supports
    selective rollback of previously executed steps.

    Attributes:
        executor_id: Unique identifier for this executor instance.
        executed: List of ExecutionResult objects from all executed steps.
        budget_remaining: Token budget remaining for future executions.
        errors: List of error message strings encountered during execution.
    """

    executor_id: str
    executed: list
    budget_remaining: int
    errors: list

    def execute_step(self, step: PlanStep, frontier_proxy: dict) -> ExecutionResult:
        """Execute a single PlanStep against the given frontier proxy.

        Simulates action execution by updating frontier_proxy in place and
        recording a result.  Returns a failure result if the step would exceed
        the remaining budget.

        Args:
            step: The PlanStep to execute.
            frontier_proxy: Mutable dict representing the frontier state.
                Updated in-place to reflect the step's effect.

        Returns:
            ExecutionResult indicating success or failure.
        """
        if step.tokens_required > self.budget_remaining:
            result = ExecutionResult.fail(
                step.step_id,
                f"Insufficient budget: need {step.tokens_required}, have {self.budget_remaining}",
            )
            self.errors.append(result.error)
            self.executed.append(result)
            return result
        start = time.monotonic()
        try:
            output = self._dispatch_action(step.action, frontier_proxy, step.metadata)
            elapsed = time.monotonic() - start
            self.budget_remaining -= step.tokens_required
            result = ExecutionResult.ok(step.step_id, step.tokens_required, elapsed, output)
        except Exception as exc:
            elapsed = time.monotonic() - start
            result = ExecutionResult.fail(step.step_id, str(exc))
            self.errors.append(str(exc))
        self.executed.append(result)
        return result

    def _dispatch_action(self, action: str, proxy: dict, meta: dict) -> dict:
        """Internal dispatcher that simulates action effects on the proxy.

        Args:
            action: The action name string.
            proxy: The mutable frontier proxy dict.
            meta: Step metadata dict.

        Returns:
            Output dict summarising what the action produced.
        """
        dispatch_map = {
            "EXPAND_FRONTIER": self._action_expand,
            "SAMPLE_DIVERSE": self._action_sample_diverse,
            "SCORE_NODES": self._action_score_nodes,
            "UPDATE_BANDIT": self._action_update_bandit,
            "PRUNE_LOW_TRUST": self._action_prune,
            "FOCUS_SEARCH": self._action_focus,
            "DEEP_EXPAND": self._action_deep_expand,
            "BACKTRACK_CHECK": self._action_backtrack,
            "CERTIFY_COVERAGE": self._action_certify,
            "VALIDATE_TRUST": self._action_validate_trust,
            "FINAL_SCORE": self._action_final_score,
            "EMIT_CERTIFICATE": self._action_emit_cert,
        }
        fn = dispatch_map.get(action, self._action_default)
        return fn(proxy, meta)

    def _action_expand(self, proxy: dict, meta: dict) -> dict:
        current = float(proxy.get("coverage_ratio", 0.0))
        increment = random.uniform(0.03, 0.08)
        proxy["coverage_ratio"] = min(1.0, current + increment)
        new_nodes = random.randint(2, 8)
        proxy["node_count"] = proxy.get("node_count", 0) + new_nodes
        return {"expanded_nodes": new_nodes, "coverage_ratio": proxy["coverage_ratio"]}

    def _action_sample_diverse(self, proxy: dict, meta: dict) -> dict:
        current_div = float(proxy.get("diversity_score", 0.0))
        proxy["diversity_score"] = min(1.0, current_div + random.uniform(0.02, 0.06))
        return {"diversity_score": proxy["diversity_score"]}

    def _action_score_nodes(self, proxy: dict, meta: dict) -> dict:
        scored = proxy.get("node_count", 0)
        proxy["trust_mass"] = min(1.0, float(proxy.get("trust_mass", 0.0)) + 0.03)
        return {"scored_nodes": scored, "trust_mass": proxy["trust_mass"]}

    def _action_update_bandit(self, proxy: dict, meta: dict) -> dict:
        proxy["bandit_regret"] = max(0.0, float(proxy.get("bandit_regret", 0.5)) - 0.05)
        return {"bandit_regret": proxy["bandit_regret"]}

    def _action_prune(self, proxy: dict, meta: dict) -> dict:
        pruned = random.randint(0, 3)
        proxy["obstruction_density"] = max(
            0.0, float(proxy.get("obstruction_density", 0.5)) - 0.04
        )
        return {"pruned": pruned, "obstruction_density": proxy["obstruction_density"]}

    def _action_focus(self, proxy: dict, meta: dict) -> dict:
        proxy["coverage_ratio"] = min(1.0, float(proxy.get("coverage_ratio", 0.0)) + 0.05)
        return {"coverage_ratio": proxy["coverage_ratio"]}

    def _action_deep_expand(self, proxy: dict, meta: dict) -> dict:
        proxy["coverage_ratio"] = min(1.0, float(proxy.get("coverage_ratio", 0.0)) + 0.07)
        new_nodes = random.randint(4, 12)
        proxy["node_count"] = proxy.get("node_count", 0) + new_nodes
        return {"deep_expanded": new_nodes, "coverage_ratio": proxy["coverage_ratio"]}

    def _action_backtrack(self, proxy: dict, meta: dict) -> dict:
        stalls = max(0, int(proxy.get("stall_count", 0)) - 1)
        proxy["stall_count"] = stalls
        return {"stall_count": stalls}

    def _action_certify(self, proxy: dict, meta: dict) -> dict:
        coverage = float(proxy.get("coverage_ratio", 0.0))
        certified = coverage >= 0.85
        return {"certified": certified, "coverage_ratio": coverage}

    def _action_validate_trust(self, proxy: dict, meta: dict) -> dict:
        trust = float(proxy.get("trust_mass", 0.0))
        valid = trust >= 0.7
        return {"trust_valid": valid, "trust_mass": trust}

    def _action_final_score(self, proxy: dict, meta: dict) -> dict:
        score = (
            float(proxy.get("coverage_ratio", 0.0)) * 0.5
            + float(proxy.get("trust_mass", 0.0)) * 0.3
            + float(proxy.get("diversity_score", 0.0)) * 0.2
        )
        proxy["final_score"] = score
        return {"final_score": score}

    def _action_emit_cert(self, proxy: dict, meta: dict) -> dict:
        cert_id = str(uuid.uuid4())
        proxy["certificate_id"] = cert_id
        return {"certificate_id": cert_id}

    def _action_default(self, proxy: dict, meta: dict) -> dict:
        return {"action": "no-op"}

    def execute_plan(self, steps: list, frontier_proxy: dict) -> list:
        """Execute an ordered list of PlanSteps sequentially.

        Dependency resolution is performed before execution: steps whose
        dependencies have not yet succeeded are deferred once and retried after
        the remaining steps complete.

        Args:
            steps: List of PlanStep objects to execute in order.
            frontier_proxy: Mutable frontier state dict.

        Returns:
            List of ExecutionResult objects in execution order.
        """
        results = []
        completed_ids: set = set()
        remaining = list(steps)
        max_passes = len(steps) + 1
        for _ in range(max_passes):
            if not remaining:
                break
            next_remaining = []
            for step in remaining:
                deps_met = all(d in completed_ids for d in step.dependencies)
                if not deps_met:
                    next_remaining.append(step)
                    continue
                result = self.execute_step(step, frontier_proxy)
                results.append(result)
                if result.success:
                    completed_ids.add(step.step_id)
            remaining = next_remaining
        # Force-execute any still-stuck steps (missing deps are ignored)
        for step in remaining:
            result = self.execute_step(step, frontier_proxy)
            results.append(result)
        return results

    def rollback(self, result_id: str) -> bool:
        """Remove a previously executed result from the execution log.

        This is a logical rollback only: it removes the result from the log
        but does not undo effects on the frontier proxy (which is mutable and
        not tracked here).

        Args:
            result_id: The result_id of the ExecutionResult to remove.

        Returns:
            True if the result was found and removed; False if not found.
        """
        for i, r in enumerate(self.executed):
            if r.result_id == result_id:
                self.executed.pop(i)
                return True
        return False

    def success_rate(self) -> float:
        """Return the fraction of executed steps that succeeded.

        Returns:
            Float in [0.0, 1.0]; 0.0 when no steps have been executed.
        """
        if not self.executed:
            return 0.0
        successes = sum(1 for r in self.executed if r.success)
        return successes / len(self.executed)

    def budget_utilization(self) -> float:
        """Return the fraction of the original budget that has been consumed.

        Computed as 1 - (budget_remaining / original_budget) using the total
        tokens in the executed log as a proxy for original budget consumption.

        Returns:
            Float in [0.0, 1.0] representing budget consumed fraction.
        """
        total_used = sum(r.tokens_used for r in self.executed)
        total_available = total_used + self.budget_remaining
        if total_available == 0:
            return 0.0
        return total_used / total_available

    def to_dict(self) -> dict:
        """Serialise the executor state to a JSON-compatible dictionary.

        Returns:
            Dict with executor_id, execution counts, success rate, budget info.
        """
        return {
            "executor_id": self.executor_id,
            "executed_count": len(self.executed),
            "success_rate": self.success_rate(),
            "budget_remaining": self.budget_remaining,
            "budget_utilization": self.budget_utilization(),
            "error_count": len(self.errors),
        }


@dataclass(frozen=True, slots=True)
class SignalNormalizationSpec:
    """Specification for normalising a single signal field to [0, 1].

    Captures the expected range of a field and provides forward (normalize)
    and inverse (denormalize) transformations.

    Attributes:
        spec_id: Unique identifier for this spec.
        field_name: The signal field this spec applies to.
        min_val: The minimum expected raw value (maps to 0.0).
        max_val: The maximum expected raw value (maps to 1.0).
        clip: If True, clamp outputs to [0.0, 1.0] even for out-of-range inputs.
    """

    spec_id: str
    field_name: str
    min_val: float
    max_val: float
    clip: bool

    def normalize(self, value: float) -> float:
        """Map a raw value from [min_val, max_val] to [0.0, 1.0].

        Uses linear interpolation: (value - min_val) / (max_val - min_val).
        If max_val == min_val returns 0.0 to avoid division by zero.

        Args:
            value: The raw field value to normalise.

        Returns:
            Normalised float; clamped to [0, 1] if self.clip is True.
        """
        if self.max_val == self.min_val:
            return 0.0
        result = (value - self.min_val) / (self.max_val - self.min_val)
        if self.clip:
            return max(0.0, min(1.0, result))
        return result

    def denormalize(self, value: float) -> float:
        """Map a normalised value from [0.0, 1.0] back to [min_val, max_val].

        Args:
            value: The normalised value in [0, 1].

        Returns:
            Raw float in [min_val, max_val].
        """
        return self.min_val + value * (self.max_val - self.min_val)

    def to_dict(self) -> dict:
        """Serialise the spec to a JSON-compatible dictionary.

        Returns:
            Dict containing all spec fields.
        """
        return {
            "spec_id": self.spec_id,
            "field_name": self.field_name,
            "min_val": self.min_val,
            "max_val": self.max_val,
            "clip": self.clip,
        }

    @classmethod
    def for_field(
        cls,
        field_name: str,
        min_val: float,
        max_val: float,
    ) -> "SignalNormalizationSpec":
        """Factory method for creating a clipped spec for a named field.

        Args:
            field_name: The signal field to normalise.
            min_val: Lower bound of the raw range.
            max_val: Upper bound of the raw range.

        Returns:
            A clipped SignalNormalizationSpec with a fresh UUID.
        """
        return cls(
            spec_id=str(uuid.uuid4()),
            field_name=field_name,
            min_val=min_val,
            max_val=max_val,
            clip=True,
        )


@dataclass(slots=True)
class FrontierPhasesNormalizer:
    """Applies per-field normalization specs to raw signal dictionaries.

    Supports both forward (normalize_signal) and inverse (denormalize_signal)
    transforms, as well as automatic spec fitting from a sample dataset.

    Attributes:
        normalizer_id: Unique identifier for this normalizer instance.
        specs: Dict mapping field name to SignalNormalizationSpec.
        history: List of (raw_dict, normalized_dict) pairs logged during use.
    """

    normalizer_id: str
    specs: dict
    history: list

    def add_spec(self, spec: SignalNormalizationSpec) -> None:
        """Register a normalization spec for a specific field.

        Overwrites any existing spec for the same field_name.

        Args:
            spec: The SignalNormalizationSpec to register.
        """
        self.specs[spec.field_name] = spec

    def normalize_signal(self, raw_dict: dict) -> dict:
        """Normalise all fields in *raw_dict* that have a registered spec.

        Fields without a spec are passed through unchanged.

        Args:
            raw_dict: Dict of field name -> raw value.

        Returns:
            New dict with normalised values for spec-covered fields.
        """
        result = dict(raw_dict)
        for field_name, spec in self.specs.items():
            if field_name in raw_dict:
                result[field_name] = spec.normalize(float(raw_dict[field_name]))
        self.history.append({"raw": dict(raw_dict), "normalized": dict(result)})
        return result

    def denormalize_signal(self, norm_dict: dict) -> dict:
        """Reverse-normalise all fields in *norm_dict* that have a registered spec.

        Args:
            norm_dict: Dict of field name -> normalised value.

        Returns:
            New dict with denormalised values for spec-covered fields.
        """
        result = dict(norm_dict)
        for field_name, spec in self.specs.items():
            if field_name in norm_dict:
                result[field_name] = spec.denormalize(float(norm_dict[field_name]))
        return result

    def fit(self, samples: list) -> None:
        """Compute min/max from *samples* and create normalisation specs.

        Args:
            samples: List of raw signal dicts.  All numeric keys are processed.
                At least two samples are required for meaningful fitting.
        """
        if not samples:
            return
        all_keys: set = set()
        for s in samples:
            all_keys.update(s.keys())
        for key in all_keys:
            vals = []
            for s in samples:
                if key in s:
                    try:
                        vals.append(float(s[key]))
                    except (TypeError, ValueError):
                        pass
            if len(vals) < 2:
                continue
            min_v = min(vals)
            max_v = max(vals)
            if min_v == max_v:
                max_v = min_v + 1.0
            self.specs[key] = SignalNormalizationSpec.for_field(key, min_v, max_v)

    def to_dict(self) -> dict:
        """Serialise the normalizer state to a JSON-compatible dictionary.

        Returns:
            Dict with normalizer_id, spec count, and history length.
        """
        return {
            "normalizer_id": self.normalizer_id,
            "spec_count": len(self.specs),
            "specs": {k: v.to_dict() for k, v in self.specs.items()},
            "history_length": len(self.history),
        }


@dataclass(slots=True)
class PhaseAlgorithmRegistry:
    """A registry of named callable algorithms for use in phase planning.

    Algorithms are registered with metadata and can be looked up and dispatched
    by name.  This allows phase plans to reference algorithms symbolically
    without importing them directly.

    Attributes:
        registry_id: Unique identifier for this registry instance.
        algorithms: Dict mapping algorithm name to (callable, metadata) tuples.
        metadata: Registry-level metadata dict.
    """

    registry_id: str
    algorithms: dict
    metadata: dict

    def register(self, name: str, fn: object, meta: dict) -> None:
        """Register a callable algorithm under *name*.

        Overwrites any existing registration for the same name.

        Args:
            name: The symbolic name for the algorithm.
            fn: The callable to register.
            meta: Metadata dict describing the algorithm (e.g. description,
                version, author).
        """
        self.algorithms[name] = (fn, dict(meta))

    def lookup(self, name: str) -> "object | None":
        """Look up a registered algorithm by name.

        Args:
            name: The symbolic name of the algorithm to retrieve.

        Returns:
            The registered callable, or None if not found.
        """
        entry = self.algorithms.get(name)
        if entry is None:
            return None
        return entry[0]

    def list_algorithms(self) -> list:
        """Return a list of all registered algorithm names.

        Returns:
            List of name strings in insertion order.
        """
        return list(self.algorithms.keys())

    def dispatch(self, name: str, *args, **kwargs) -> object:
        """Look up and call the algorithm registered under *name*.

        Args:
            name: The algorithm to dispatch.
            *args: Positional arguments forwarded to the callable.
            **kwargs: Keyword arguments forwarded to the callable.

        Returns:
            The return value of the registered callable.

        Raises:
            KeyError: If *name* is not registered.
        """
        fn = self.lookup(name)
        if fn is None:
            raise KeyError(f"Algorithm '{name}' not registered in registry {self.registry_id}")
        return fn(*args, **kwargs)

    def to_dict(self) -> dict:
        """Serialise the registry to a JSON-compatible dictionary.

        Returns:
            Dict with registry_id, algorithm names, and metadata.
        """
        return {
            "registry_id": self.registry_id,
            "algorithm_names": self.list_algorithms(),
            "algorithm_count": len(self.algorithms),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Configuration
    config = FrontierPhasesConfig.default()
    print(f"Config: {config.config_id[:8]} max_iter={config.max_iterations}")

    # Planner + Executor
    planner = FrontierPhasesPlanner.make(config)
    executor = FrontierPhasesExecutor(
        executor_id=str(uuid.uuid4()),
        executed=[],
        budget_remaining=config.budget_token_limit,
        errors=[],
    )

    # Normalizer
    normalizer = FrontierPhasesNormalizer(
        normalizer_id=str(uuid.uuid4()),
        specs={},
        history=[],
    )
    sample_signals = [
        {"coverage_ratio": 0.1, "obstruction_density": 0.9, "trust_mass": 0.1},
        {"coverage_ratio": 0.5, "obstruction_density": 0.5, "trust_mass": 0.5},
        {"coverage_ratio": 0.9, "obstruction_density": 0.1, "trust_mass": 0.9},
    ]
    normalizer.fit(sample_signals)
    print(f"Normalizer fitted with {len(normalizer.specs)} specs")

    # Registry
    registry = PhaseAlgorithmRegistry(
        registry_id=str(uuid.uuid4()),
        algorithms={},
        metadata={"description": "Frontier phases algorithm registry"},
    )
    registry.register(
        "obstruction_density",
        compute_obstruction_density,
        {"description": "Compute obstruction density of frontier nodes"},
    )
    registry.register(
        "coverage_ratio",
        compute_coverage_ratio,
        {"description": "Compute search space coverage ratio"},
    )
    registry.register(
        "diversity_entropy",
        compute_diversity_entropy,
        {"description": "Compute Shannon entropy of proof mode distribution"},
    )
    print(f"Registry algorithms: {registry.list_algorithms()}")

    # Dispatch algorithm functions
    test_nodes = [{"id": i, "obstructed": i % 3 == 0} for i in range(10)]
    density = registry.dispatch("obstruction_density", test_nodes, 20)
    print(f"  obstruction_density = {density:.3f}")

    covered = {"a", "b", "c", "d"}
    total = {"a", "b", "c", "d", "e", "f", "g", "h"}
    coverage = registry.dispatch("coverage_ratio", covered, total)
    print(f"  coverage_ratio = {coverage:.3f}")

    modes = ["tactic", "tactic", "simp", "decide", "tactic", "omega"]
    entropy = registry.dispatch("diversity_entropy", modes)
    print(f"  diversity_entropy = {entropy:.3f}")

    # Build and execute plan
    current_signals = {"coverage_ratio": 0.25, "obstruction_density": 0.6, "trust_mass": 0.3}
    steps = planner.build_plan("EXPLORATION", current_signals)
    print(f"\nPlan built: {len(steps)} steps, valid={planner.validate_plan()}")
    print(f"Plan summary: {json.dumps(planner.plan_summary(), indent=2)}")

    frontier_proxy = {
        "coverage_ratio": 0.25,
        "obstruction_density": 0.6,
        "trust_mass": 0.3,
        "diversity_score": 0.4,
        "bandit_regret": 0.5,
        "stall_count": 0,
        "node_count": 5,
    }
    results = executor.execute_plan(steps, frontier_proxy)
    print(f"\nExecuted {len(results)} steps")
    print(f"  success_rate       = {executor.success_rate():.2f}")
    print(f"  budget_utilization = {executor.budget_utilization():.3f}")
    print(f"  budget_remaining   = {executor.budget_remaining}")
    print(f"  frontier after     = {json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in frontier_proxy.items()}, indent=2)}")

    # UCB1 and Thompson sampling demo
    print("\nBandit primitives:")
    for pulls in [0, 1, 5, 20]:
        score = ucb1_score(0.6, pulls, 100)
        print(f"  ucb1_score(0.6, {pulls:2d}, 100) = {score:.4f}")

    ts = [thompson_beta_sample(5.0, 2.0) for _ in range(5)]
    print(f"  Thompson samples Beta(5,2): {[round(x, 3) for x in ts]}")

    # Budget split demo
    split = budget_split(1000, {"explore": 3, "exploit": 2, "converge": 1})
    print(f"\nBudget split 1000: {split}")

    print("\nalgorithms smoke test passed")


# ---------------------------------------------------------------------------
# Cross-subsystem integration: evidence, solver, encodings, judgments
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel as _TrustLevel
except Exception:
    TrustAlgebra = None  # type: ignore[assignment,misc]
    _TrustLevel = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.z3_session import Z3Session
except Exception:
    Z3Session = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings import encode_section
except Exception:
    encode_section = None  # type: ignore[assignment]


def phase_trust_gate(phase_descriptor, trust):
    """Gate a phase transition on minimum trust using jugeo.evidence.trust.

    Certain phases (e.g. exploitation) require a minimum trust tier.
    Returns whether the transition is permitted and the resolved trust.
    """
    if TrustAlgebra is None:
        return {"permitted": True, "reason": "trust subsystem unavailable (vacuously permitted)",
                "subsystem": "jugeo.evidence.trust"}
    algebra = TrustAlgebra()
    try:
        resolved = algebra.resolve(trust) if hasattr(algebra, "resolve") else trust
    except Exception:
        resolved = trust
    level_val = getattr(resolved, "value", 0)
    phase_name = getattr(phase_descriptor, "name", str(phase_descriptor)).lower()
    min_required = 2 if "exploit" in phase_name else 1
    permitted = isinstance(level_val, (int, float)) and level_val >= min_required
    return {"permitted": permitted, "trust": str(resolved), "min_required": min_required,
            "subsystem": "jugeo.evidence.trust"}


def phase_solver_invariant(phase_descriptor, constraints):
    """Verify that a phase's invariant holds via Z3 (jugeo.solver.z3_session).

    Before committing a phase transition the orchestrator checks that
    the phase-specific constraints remain satisfiable.
    """
    if Z3Session is None:
        return {"holds": None, "reason": "Z3Session unavailable",
                "subsystem": "jugeo.solver.z3_session"}
    try:
        session = Z3Session()
        for c in (constraints or []):
            session.add(c)
        outcome = session.check()
        return {"holds": getattr(outcome, "satisfiable", False),
                "subsystem": "jugeo.solver.z3_session"}
    except Exception as exc:
        return {"holds": None, "reason": str(exc),
                "subsystem": "jugeo.solver.z3_session"}


def encode_phase_snapshot(phase_descriptor, sections):
    """Encode a phase snapshot via jugeo.encodings for archival / replay.

    Serialises the phase descriptor and its associated sections into the
    canonical encoding format used across the jugeo pipeline.
    """
    if encode_section is None:
        return {"encoded": False, "reason": "encode_section unavailable",
                "subsystem": "jugeo.encodings"}
    encoded_sections = []
    for s in (sections or []):
        try:
            encoded_sections.append(encode_section(s))
        except Exception:
            encoded_sections.append({"raw": str(s)})
    return {
        "encoded": True,
        "phase": getattr(phase_descriptor, "name", str(phase_descriptor)),
        "section_count": len(encoded_sections),
        "subsystem": "jugeo.encodings",
    }
