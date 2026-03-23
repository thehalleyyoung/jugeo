r"""Long-Horizon Orchestration Claim (C3): orchestration as semantic control.

This module implements Thesis Claim C3 from Theory2.tex Chapter 2:

    **C3** — Multi-agent task orchestration over long semantic horizons can
    be formalised as a control problem in JuGeo's judgment geometry: there
    exists a semantic Lyapunov function V such that the orchestrator's control
    law drives V(J_t) to zero along admissible trajectories.

The claim is a direct application of Lyapunov stability theory to the
*semantic* dynamics of JuGeo's judgment space.  Rather than tracking physical
state, the orchestrator tracks semantic state — the judgment tuple J_t at
each step t — and the control law selects the next agent action to decrease
V.

Key concepts
------------

*Semantic trajectory* — A finite sequence of judgment tuples
:math:`(J_0, J_1, \ldots, J_T)` produced by applying a sequence of agent
actions to an initial state.

*Semantic Lyapunov function* — A function :math:`V: \mathcal{J} \to \mathbb{R}_{\geq 0}`
such that :math:`V(J_t) \to 0` as the orchestrator converges to the goal.
The function measures "semantic distance to goal": trust gap, unresolved
obligations, and coverage deficit.

*Control law* — A mapping from the current semantic state to the next agent
action, chosen to decrease V.

*Convergence condition* — The formal condition that V is a valid Lyapunov
function for the orchestrator's dynamics.

Classes
-------

:class:`OrchestratorSpecification`
    Full specification of an orchestrator: agent set, task horizon, goal
    condition, and performance bounds.

:class:`ControlLawDefinition`
    Defines the control law: how actions are selected from semantic state.

:class:`ConvergenceCondition`
    Verifies that the Lyapunov function decreases along trajectories.

:class:`SemanticTrajectory`
    Represents a trajectory of judgment tuples under a control law.

:class:`LyapunovFunction`
    A computable Lyapunov function over judgment states.

All copilot-assisted components are tagged; the Lyapunov function definition
was initially sketched with copilot assistance and subsequently reviewed.

Theory alignment
----------------

Section 250 of Theory2.tex introduces C3.  Section 251 defines semantic
trajectories; section 252 states the Lyapunov convergence theorem; section 253
discusses the control law construction.  Theorem 2.5.1 (Convergence) is the
main theorem implemented by :class:`ConvergenceCondition`.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ConvergenceStatus(Enum):
    """Status of a convergence verification."""

    NOT_RUN = "not_run"
    CONVERGES = "converges"
    DIVERGES = "diverges"
    INCONCLUSIVE = "inconclusive"
    HORIZON_EXCEEDED = "horizon_exceeded"


class ActionKind(Enum):
    """Kind of orchestrator action."""

    DELEGATE = "delegate"
    CHALLENGE = "challenge"
    PROMOTE = "promote"
    DEMOTE = "demote"
    SPLIT = "split"
    MERGE = "merge"
    RESOLVE = "resolve"
    DEFER = "defer"


class GoalConditionKind(Enum):
    """Kind of goal condition for the orchestrator."""

    TRUST_THRESHOLD = "trust_threshold"
    OBLIGATION_CLEARED = "obligation_cleared"
    COVERAGE_COMPLETE = "coverage_complete"
    COMPOSITE = "composite"


class OrchestratorPolicy(Enum):
    """High-level policy governing the orchestrator's strategy."""

    GREEDY = "greedy"
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    ADVERSARIAL_AWARE = "adversarial_aware"


# ---------------------------------------------------------------------------
# Semantic state representation for the orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestratorState:
    """Snapshot of the orchestrator's view of judgment-space at time t.

    Parameters
    ----------
    step:
        Time step index (t).
    trust_gap:
        The gap between the current maximum trust level and the target trust
        level.  A value of 0.0 means the target has been reached.
    unresolved_obligations:
        Count of unresolved obligations in the active judgment set.
    coverage_deficit:
        Fractional deficit in evidence coverage, in [0.0, 1.0].
    active_agents:
        Names of agents currently active in the orchestration.
    pending_copilot_proposals:
        Count of copilot proposals awaiting explicit review/promotion.
    timestamp:
        Unix timestamp of this snapshot.
    """

    step: int
    trust_gap: float
    unresolved_obligations: int
    coverage_deficit: float
    active_agents: tuple[str, ...]
    pending_copilot_proposals: int = 0
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not 0.0 <= self.coverage_deficit <= 1.0:
            raise ValueError(
                f"coverage_deficit must be in [0.0, 1.0], got {self.coverage_deficit}"
            )
        if self.trust_gap < 0.0:
            raise ValueError(f"trust_gap must be ≥ 0.0, got {self.trust_gap}")

    def is_goal(
        self,
        *,
        trust_threshold: float = 0.0,
        max_obligations: int = 0,
        max_deficit: float = 0.0,
    ) -> bool:
        """Return True if this state satisfies the given goal conditions."""
        return (
            self.trust_gap <= trust_threshold
            and self.unresolved_obligations <= max_obligations
            and self.coverage_deficit <= max_deficit
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "trust_gap": self.trust_gap,
            "unresolved_obligations": self.unresolved_obligations,
            "coverage_deficit": self.coverage_deficit,
            "active_agents": list(self.active_agents),
            "pending_copilot_proposals": self.pending_copilot_proposals,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class OrchestratorAction:
    """An action taken by the orchestrator at one step.

    Parameters
    ----------
    action_id:
        Unique identifier.
    kind:
        :class:`ActionKind`.
    target_agent:
        Name of the agent targeted by this action.
    payload:
        Short description of the action payload.
    expected_trust_delta:
        Expected change in trust_gap after this action (negative = improvement).
    copilot_suggested:
        Whether this action was suggested by a copilot agent.  Copilot-
        suggested actions are advisory; the orchestrator must apply its own
        policy before executing them.
    """

    action_id: str
    kind: ActionKind
    target_agent: str
    payload: str
    expected_trust_delta: float
    copilot_suggested: bool = False

    def is_beneficial(self) -> bool:
        """Return True if the expected trust delta is negative (improvement)."""
        return self.expected_trust_delta < 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "target_agent": self.target_agent,
            "payload": self.payload,
            "expected_trust_delta": self.expected_trust_delta,
            "copilot_suggested": self.copilot_suggested,
        }


# ---------------------------------------------------------------------------
# LyapunovFunction
# ---------------------------------------------------------------------------


@dataclass
class LyapunovFunction:
    """A semantic Lyapunov function over orchestrator states.

    The Lyapunov function V measures the semantic distance of the current
    orchestrator state from the goal.  For C3, V must satisfy:

    1. **Non-negativity** — V(s) ≥ 0 for all s.
    2. **Zero at goal** — V(s) = 0 iff s is a goal state.
    3. **Decrease along trajectories** — V(s_{t+1}) < V(s_t) under the
       admissible control law (except at the goal).

    The default implementation is a weighted sum of semantic gaps.

    Parameters
    ----------
    name:
        Identifier for this Lyapunov function.
    trust_weight:
        Weight for the trust gap component.
    obligation_weight:
        Weight for the unresolved-obligation component.
    coverage_weight:
        Weight for the coverage-deficit component.
    copilot_weight:
        Additional penalty for pending copilot proposals awaiting review.
    goal_trust_threshold:
        Trust gap below which trust is considered resolved.
    goal_obligation_threshold:
        Obligation count at or below which obligations are considered resolved.
    goal_coverage_threshold:
        Coverage deficit at or below which coverage is considered complete.
    """

    name: str
    trust_weight: float = 1.0
    obligation_weight: float = 0.5
    coverage_weight: float = 0.75
    copilot_weight: float = 0.25
    goal_trust_threshold: float = 0.0
    goal_obligation_threshold: int = 0
    goal_coverage_threshold: float = 0.0

    def __call__(self, state: OrchestratorState) -> float:
        """Evaluate V(state).

        Returns
        -------
        float
            A non-negative real number; 0.0 at the goal state.
        """
        return (
            self.trust_weight * max(0.0, state.trust_gap - self.goal_trust_threshold)
            + self.obligation_weight * max(0, state.unresolved_obligations - self.goal_obligation_threshold)
            + self.coverage_weight * max(0.0, state.coverage_deficit - self.goal_coverage_threshold)
            + self.copilot_weight * state.pending_copilot_proposals
        )

    def is_zero_at_goal(
        self,
        goal_states: Sequence[OrchestratorState],
    ) -> bool:
        """Verify that V(s) = 0 for all given goal states.

        Parameters
        ----------
        goal_states:
            States that should be goal states.

        Returns
        -------
        bool
            True if V(s) ≈ 0 for all states in *goal_states*.
        """
        eps = 1e-9
        return all(abs(self(s)) < eps for s in goal_states)

    def is_positive_off_goal(
        self,
        non_goal_states: Sequence[OrchestratorState],
    ) -> bool:
        """Verify that V(s) > 0 for all non-goal states.

        Parameters
        ----------
        non_goal_states:
            States that should not be goal states.

        Returns
        -------
        bool
            True if V(s) > 0 for all states in *non_goal_states*.
        """
        return all(self(s) > 0.0 for s in non_goal_states)

    def delta(
        self, s_before: OrchestratorState, s_after: OrchestratorState
    ) -> float:
        """Return V(s_after) - V(s_before).

        A negative delta indicates progress toward the goal.
        """
        return self(s_after) - self(s_before)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trust_weight": self.trust_weight,
            "obligation_weight": self.obligation_weight,
            "coverage_weight": self.coverage_weight,
            "copilot_weight": self.copilot_weight,
        }


# ---------------------------------------------------------------------------
# SemanticTrajectory
# ---------------------------------------------------------------------------


@dataclass
class SemanticTrajectory:
    """A trajectory of orchestrator states under a control law.

    Parameters
    ----------
    trajectory_id:
        Unique identifier.
    states:
        Ordered list of :class:`OrchestratorState` objects (index = time step).
    actions:
        Actions taken at each step (len = len(states) - 1).
    """

    trajectory_id: str
    states: list[OrchestratorState] = field(default_factory=list)
    actions: list[OrchestratorAction] = field(default_factory=list)

    def length(self) -> int:
        """Return the number of states in the trajectory."""
        return len(self.states)

    def append(self, state: OrchestratorState, action: OrchestratorAction | None = None) -> None:
        """Append a state (and optionally the action that produced it)."""
        self.states.append(state)
        if action is not None:
            self.actions.append(action)

    def lyapunov_sequence(self, V: LyapunovFunction) -> list[float]:
        """Return the sequence of Lyapunov values along this trajectory."""
        return [V(s) for s in self.states]

    def is_lyapunov_decreasing(self, V: LyapunovFunction, strict: bool = False) -> bool:
        """Return True if V is non-increasing (or strictly decreasing) along the trajectory.

        Parameters
        ----------
        V:
            The :class:`LyapunovFunction` to evaluate.
        strict:
            If True, require strict decrease at every non-goal step.

        Returns
        -------
        bool
            True if the Lyapunov condition is satisfied.
        """
        seq = self.lyapunov_sequence(V)
        if len(seq) < 2:
            return True
        for i in range(len(seq) - 1):
            if strict:
                if seq[i + 1] >= seq[i] and seq[i] > 1e-9:
                    return False
            else:
                if seq[i + 1] > seq[i]:
                    return False
        return True

    def final_state(self) -> OrchestratorState | None:
        """Return the last state in the trajectory."""
        return self.states[-1] if self.states else None

    def convergence_step(
        self,
        V: LyapunovFunction,
        threshold: float = 1e-6,
    ) -> int | None:
        """Return the first step at which V(s_t) ≤ threshold, or None."""
        for i, s in enumerate(self.states):
            if V(s) <= threshold:
                return i
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "length": self.length(),
            "states": [s.to_dict() for s in self.states],
            "actions": [a.to_dict() for a in self.actions],
        }


# ---------------------------------------------------------------------------
# ControlLawDefinition
# ---------------------------------------------------------------------------


@dataclass
class ControlLawDefinition:
    """Defines the orchestrator's control law.

    The control law π(s) selects the next action to take from state s.
    A sound control law must:

    1. Always select an action (totality).
    2. Never exceed agent authority (admissibility).
    3. Prefer actions that decrease V (optimality w.r.t. Lyapunov).

    Parameters
    ----------
    name:
        Identifier for this control law.
    policy:
        :class:`OrchestratorPolicy` governing the strategy.
    lyapunov_fn:
        The :class:`LyapunovFunction` used to guide action selection.
    available_actions:
        Sequence of candidate actions.  In practice these are generated
        dynamically; here they are provided for testability.
    copilot_advisory_enabled:
        Whether copilot-suggested actions are considered.  If True, copilot
        suggestions are included in the candidate set but are subject to the
        orchestrator's policy filter before execution.
    """

    name: str
    policy: OrchestratorPolicy
    lyapunov_fn: LyapunovFunction
    available_actions: list[OrchestratorAction] = field(default_factory=list)
    copilot_advisory_enabled: bool = True

    def select_action(
        self, state: OrchestratorState
    ) -> OrchestratorAction | None:
        """Select the best action for the given state.

        Selection strategy depends on :attr:`policy`:

        * ``GREEDY`` — pick the action with the largest expected trust delta
          decrease (most negative expected_trust_delta).
        * ``CONSERVATIVE`` — pick the action with the smallest expected
          change magnitude to avoid over-committing.
        * ``BALANCED`` — pick the action whose expected delta is closest to
          ``-V(state) / 2`` (half-way step).
        * ``ADVERSARIAL_AWARE`` — like GREEDY but ignores copilot-suggested
          actions.

        Parameters
        ----------
        state:
            Current orchestrator state.

        Returns
        -------
        OrchestratorAction | None
            The selected action, or ``None`` if no candidates are available.
        """
        candidates = self.available_actions
        if not candidates:
            return None
        if self.policy == OrchestratorPolicy.ADVERSARIAL_AWARE:
            candidates = [a for a in candidates if not a.copilot_suggested]
        elif not self.copilot_advisory_enabled:
            candidates = [a for a in candidates if not a.copilot_suggested]
        if not candidates:
            return None

        v_now = self.lyapunov_fn(state)
        if self.policy in (OrchestratorPolicy.GREEDY, OrchestratorPolicy.ADVERSARIAL_AWARE):
            return min(candidates, key=lambda a: a.expected_trust_delta)
        elif self.policy == OrchestratorPolicy.CONSERVATIVE:
            return min(candidates, key=lambda a: abs(a.expected_trust_delta))
        else:  # BALANCED
            target_delta = -v_now / 2.0 if v_now > 0 else -0.1
            return min(
                candidates,
                key=lambda a: abs(a.expected_trust_delta - target_delta),
            )

    def admissibility_check(self, action: OrchestratorAction) -> list[str]:
        """Check that an action is admissible under this control law.

        Returns
        -------
        list[str]
            Empty if admissible; list of error messages otherwise.
        """
        errors: list[str] = []
        if action.copilot_suggested and not self.copilot_advisory_enabled:
            errors.append(
                f"Copilot-suggested action {action.action_id!r} is disabled "
                f"in policy {self.policy.value!r}"
            )
        if action.expected_trust_delta > 1.0:
            errors.append(
                f"Action {action.action_id!r} has unexpectedly large positive delta "
                f"{action.expected_trust_delta:.3f}; may be misspecified"
            )
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "policy": self.policy.value,
            "lyapunov_fn": self.lyapunov_fn.to_dict(),
            "n_available_actions": len(self.available_actions),
            "copilot_advisory_enabled": self.copilot_advisory_enabled,
        }


# ---------------------------------------------------------------------------
# ConvergenceCondition
# ---------------------------------------------------------------------------


@dataclass
class ConvergenceCondition:
    """Verifies the Lyapunov convergence condition for Claim C3.

    A convergence condition consists of:

    1. A :class:`LyapunovFunction` V.
    2. A :class:`ControlLawDefinition` π.
    3. A :class:`SemanticTrajectory` generated by applying π.
    4. Verification that V is non-increasing along the trajectory and
       that V → 0 within the declared horizon bound.

    Parameters
    ----------
    name:
        Identifier for this condition instance.
    lyapunov_fn:
        The Lyapunov function to verify.
    control_law:
        The control law being verified.
    horizon:
        Maximum number of steps before convergence must occur.
    tolerance:
        V value below which convergence is declared.
    """

    name: str
    lyapunov_fn: LyapunovFunction
    control_law: ControlLawDefinition
    horizon: int = 100
    tolerance: float = 1e-6
    _last_result: ConvergenceStatus = field(
        default=ConvergenceStatus.NOT_RUN, repr=False
    )
    _last_trajectory: SemanticTrajectory | None = field(default=None, repr=False)

    def verify_on_trajectory(
        self, trajectory: SemanticTrajectory
    ) -> ConvergenceStatus:
        """Verify the Lyapunov condition on a given trajectory.

        Checks:

        1. V is non-increasing at every step.
        2. V reaches the tolerance within the declared horizon.

        Parameters
        ----------
        trajectory:
            The semantic trajectory to evaluate.

        Returns
        -------
        ConvergenceStatus
            ``CONVERGES``, ``DIVERGES``, ``HORIZON_EXCEEDED``, or
            ``INCONCLUSIVE``.
        """
        self._last_trajectory = trajectory
        if not trajectory.states:
            self._last_result = ConvergenceStatus.INCONCLUSIVE
            return self._last_result
        if not trajectory.is_lyapunov_decreasing(self.lyapunov_fn):
            self._last_result = ConvergenceStatus.DIVERGES
            return self._last_result
        conv_step = trajectory.convergence_step(self.lyapunov_fn, self.tolerance)
        if conv_step is None:
            if trajectory.length() >= self.horizon:
                self._last_result = ConvergenceStatus.HORIZON_EXCEEDED
            else:
                self._last_result = ConvergenceStatus.INCONCLUSIVE
            return self._last_result
        self._last_result = ConvergenceStatus.CONVERGES
        return self._last_result

    def simulate_and_verify(
        self,
        initial_state: OrchestratorState,
        transition_fn: Callable[[OrchestratorState, OrchestratorAction], OrchestratorState],
    ) -> tuple[ConvergenceStatus, SemanticTrajectory]:
        """Simulate the orchestrator and verify convergence.

        Applies the control law at each step, appending states to a new
        trajectory, until the goal is reached or the horizon is exceeded.

        Parameters
        ----------
        initial_state:
            Starting state for the simulation.
        transition_fn:
            A callable ``(state, action) -> next_state`` modelling the
            environment's response to the orchestrator's action.

        Returns
        -------
        tuple[ConvergenceStatus, SemanticTrajectory]
            The convergence status and the trajectory produced.
        """
        traj_id = str(uuid.uuid4())
        traj = SemanticTrajectory(trajectory_id=traj_id)
        traj.append(initial_state)
        state = initial_state
        for _ in range(self.horizon):
            if self.lyapunov_fn(state) <= self.tolerance:
                break
            action = self.control_law.select_action(state)
            if action is None:
                break
            next_state = transition_fn(state, action)
            traj.append(next_state, action)
            state = next_state
        status = self.verify_on_trajectory(traj)
        return status, traj

    def report(self) -> dict[str, Any]:
        """Return a structured report of the last verification."""
        traj_summary: dict[str, Any] = {}
        if self._last_trajectory is not None:
            traj = self._last_trajectory
            seq = traj.lyapunov_sequence(self.lyapunov_fn)
            traj_summary = {
                "length": traj.length(),
                "initial_V": seq[0] if seq else None,
                "final_V": seq[-1] if seq else None,
                "min_V": min(seq) if seq else None,
                "convergence_step": traj.convergence_step(
                    self.lyapunov_fn, self.tolerance
                ),
                "lyapunov_decreasing": traj.is_lyapunov_decreasing(self.lyapunov_fn),
            }
        return {
            "name": self.name,
            "status": self._last_result.value,
            "horizon": self.horizon,
            "tolerance": self.tolerance,
            "lyapunov_fn": self.lyapunov_fn.to_dict(),
            "control_law": self.control_law.to_dict(),
            "trajectory_summary": traj_summary,
        }


# ---------------------------------------------------------------------------
# OrchestratorSpecification
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorSpecification:
    """Full specification of an orchestrator for Claim C3 verification.

    Brings together the control law, Lyapunov function, convergence condition,
    agent set, and performance bounds into a single verifiable specification.

    Parameters
    ----------
    spec_id:
        Unique identifier.
    name:
        Human-readable name.
    agents:
        Names of agents participating in the orchestration.
    task_description:
        Prose description of the task being orchestrated.
    control_law:
        The :class:`ControlLawDefinition` for this orchestrator.
    convergence_condition:
        The :class:`ConvergenceCondition` to verify.
    horizon_bound:
        Maximum number of orchestration steps.
    trust_target:
        Target trust level name that the orchestrator should achieve.
    copilot_advisory_notes:
        Notes from copilot-assisted design of this orchestrator specification.
        Carries ``COPILOT_SUGGESTED`` trust until reviewed.
    """

    spec_id: str
    name: str
    agents: tuple[str, ...]
    task_description: str
    control_law: ControlLawDefinition
    convergence_condition: ConvergenceCondition
    horizon_bound: int
    trust_target: str
    copilot_advisory_notes: str = ""
    created_at: float = field(default_factory=time.time)

    def is_single_agent(self) -> bool:
        """Return True if only one agent is specified."""
        return len(self.agents) == 1

    def validate(self) -> list[str]:
        """Validate the specification for internal consistency.

        Returns
        -------
        list[str]
            Empty if valid; list of error descriptions otherwise.
        """
        errors: list[str] = []
        if not self.agents:
            errors.append("OrchestratorSpecification must have at least one agent")
        if self.horizon_bound <= 0:
            errors.append(
                f"horizon_bound must be > 0, got {self.horizon_bound}"
            )
        if self.convergence_condition.horizon > self.horizon_bound:
            errors.append(
                "ConvergenceCondition horizon exceeds OrchestratorSpecification horizon_bound"
            )
        return errors

    def run(
        self,
        initial_state: OrchestratorState,
        transition_fn: Callable[[OrchestratorState, OrchestratorAction], OrchestratorState],
    ) -> tuple[ConvergenceStatus, SemanticTrajectory]:
        """Execute the orchestrator from an initial state and verify convergence.

        Parameters
        ----------
        initial_state:
            Starting state.
        transition_fn:
            Environment transition function.

        Returns
        -------
        tuple[ConvergenceStatus, SemanticTrajectory]
        """
        return self.convergence_condition.simulate_and_verify(
            initial_state, transition_fn
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "name": self.name,
            "agents": list(self.agents),
            "task_description": self.task_description,
            "control_law": self.control_law.to_dict(),
            "convergence_condition_report": self.convergence_condition.report(),
            "horizon_bound": self.horizon_bound,
            "trust_target": self.trust_target,
            "copilot_advisory_notes": self.copilot_advisory_notes,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _default_transition(
    state: OrchestratorState,
    action: OrchestratorAction,
) -> OrchestratorState:
    """Default transition function for testing.

    Applies the action's expected trust delta and reduces obligations/coverage
    by a fixed decrement.  Copilot-suggested actions are applied at half weight.
    """
    weight = 0.5 if action.copilot_suggested else 1.0
    new_trust_gap = max(0.0, state.trust_gap + weight * action.expected_trust_delta)
    new_obligations = max(
        0,
        state.unresolved_obligations - (1 if action.kind == ActionKind.RESOLVE else 0),
    )
    new_coverage = max(
        0.0,
        state.coverage_deficit - 0.1 * weight,
    )
    new_copilot = max(
        0,
        state.pending_copilot_proposals
        - (1 if action.kind == ActionKind.PROMOTE else 0),
    )
    return OrchestratorState(
        step=state.step + 1,
        trust_gap=new_trust_gap,
        unresolved_obligations=new_obligations,
        coverage_deficit=new_coverage,
        active_agents=state.active_agents,
        pending_copilot_proposals=new_copilot,
    )


def build_minimal_c3_instance(
    name: str = "C3_minimal",
    horizon: int = 20,
) -> OrchestratorSpecification:
    """Construct a minimal :class:`OrchestratorSpecification` for C3 testing.

    Parameters
    ----------
    name:
        Name for the specification.
    horizon:
        Maximum orchestration steps.

    Returns
    -------
    OrchestratorSpecification
        Ready to call :meth:`~OrchestratorSpecification.run` on.
    """
    lyap = LyapunovFunction(name=f"{name}_V")
    actions = [
        OrchestratorAction(
            action_id=str(uuid.uuid4()),
            kind=ActionKind.PROMOTE,
            target_agent="agent_0",
            payload="promote trust",
            expected_trust_delta=-0.3,
        ),
        OrchestratorAction(
            action_id=str(uuid.uuid4()),
            kind=ActionKind.RESOLVE,
            target_agent="agent_0",
            payload="resolve obligation",
            expected_trust_delta=-0.1,
        ),
        OrchestratorAction(
            action_id=str(uuid.uuid4()),
            kind=ActionKind.DELEGATE,
            target_agent="agent_1",
            payload="delegate sub-task",
            expected_trust_delta=-0.2,
            copilot_suggested=True,
        ),
    ]
    law = ControlLawDefinition(
        name=f"{name}_law",
        policy=OrchestratorPolicy.GREEDY,
        lyapunov_fn=lyap,
        available_actions=actions,
        copilot_advisory_enabled=True,
    )
    cond = ConvergenceCondition(
        name=f"{name}_convergence",
        lyapunov_fn=lyap,
        control_law=law,
        horizon=horizon,
        tolerance=1e-6,
    )
    return OrchestratorSpecification(
        spec_id=str(uuid.uuid4()),
        name=name,
        agents=("agent_0", "agent_1"),
        task_description="Minimal C3 test orchestration",
        control_law=law,
        convergence_condition=cond,
        horizon_bound=horizon,
        trust_target="SOLVER_DISCHARGED",
        copilot_advisory_notes=(
            "Control law structure initially sketched with copilot assistance. "
            "Reviewed and trust ceiling enforced."
        ),
    )
