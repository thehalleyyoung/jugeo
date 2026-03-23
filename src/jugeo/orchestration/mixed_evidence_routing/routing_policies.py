"""Routing policies for the mixed-evidence routing layer.

This module implements the routing-policy machinery described in
*theory2.tex* Ch 45 §45.7 ("Routing Policies").  That section establishes
the theoretical framework for declarative, priority-ordered rules that
override or augment the default channel-selection logic of the
:class:`~jugeo.orchestration.mixed_evidence_routing.channel_selection.ChannelSelector`.

Overview (§45.7.1)
-------------------
A *routing policy* is a triple ``(conditions, actions, priority)`` where:

- **conditions** are predicates on the routing request dictionary.  All
  conditions must evaluate to ``True`` for the policy to *match*.
- **actions** are transformations applied to the request when a policy
  matches, such as forcing a specific channel, raising a trust requirement,
  escalating priority, or rejecting the request outright.
- **priority** is a ranked label (CRITICAL > HIGH > MEDIUM > LOW >
  BACKGROUND) that determines which policies fire first when multiple
  policies match the same request.

The text characterises policies as a *stratified rewriting system*: the
engine processes policies in priority order and each action may modify the
request seen by lower-priority policies.  §45.7.3 proves that if the action
set is *confluent* (i.e., no two enabled policies assign contradictory values
to the same request key) the rewriting terminates and the final request state
is unique regardless of the order in which actions of equal priority are
applied.

Conflict detection (§45.7.5)
------------------------------
Two policies *conflict* when:

1. **Contradictory actions** — both match under the same conditions but one
   sets a key to value A while the other sets it to value B, with A ≠ B.
2. **Overlapping conditions** — both policies test the same request key
   against a shared value, making it impossible to satisfy one without the
   other also matching.

The :class:`PolicyConflictDetector` implements a lightweight syntactic check
that flags likely conflicts without requiring a full SAT encoding.

Key responsibilities
--------------------
- Define ``PolicyPriority`` ranks and provide an integer ordering.
- Model *conditions* (:class:`PolicyCondition`) and *actions*
  (:class:`PolicyAction`) as immutable, serialisable value objects.
- Aggregate conditions and actions into named :class:`RoutingPolicy` objects.
- Detect conflicts between registered policies via
  :class:`PolicyConflictDetector`.
- Evaluate and apply ordered policies through :class:`PolicyEngine`.
- Provide a :class:`PolicyCoordinator` that ships five meaningful default
  policies covering the most common operational scenarios.
- Audit every policy application through :class:`PolicyWitness`.

Design notes
------------
All data-holding classes are implemented as ``@dataclass(frozen=True,
slots=True)`` (immutable) or ``@dataclass(slots=True)`` (mutable for
accumulators such as the engine log and witness events list).  Callables
stored in :class:`PolicyCondition` are excluded from equality and hashing via
``field(hash=False, compare=False)``.

The ``predicate`` field on :class:`PolicyCondition` is a plain Python
callable ``(dict) -> bool`` so that conditions can be expressed as lambdas,
named functions, or instances of predicate classes.  Serialisation
(:meth:`PolicyCondition.to_dict`) omits the callable and stores a string
description instead.

References
----------
- theory2.tex Ch 45 §45.7 "Routing Policies"
- theory2.tex Ch 45 §45.7.1 "Policy Structure and Semantics"
- theory2.tex Ch 45 §45.7.3 "Confluence of the Rewriting System"
- theory2.tex Ch 45 §45.7.5 "Conflict Detection and Resolution"
- theory2.tex Ch 45 §3 "Jurisdiction and Channel Selection"
- :mod:`jugeo.orchestration.mixed_evidence_routing.models`
- :mod:`jugeo.orchestration.mixed_evidence_routing.channel_selection`
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Models import (required — must succeed)
# ---------------------------------------------------------------------------

from jugeo.orchestration.mixed_evidence_routing.models import (
    EvidenceChannel,
    EscalationUrgency,
    RoutingDecision,
    RoutingHistory,
    ChannelStats,
)

# ---------------------------------------------------------------------------
# Optional upstream imports — guarded with try/except so that the module
# loads cleanly even when sister packages have not been installed yet.
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra  # type: ignore[import]

    _TRUST_AVAILABLE = True
except Exception:
    _TRUST_AVAILABLE = False

    class TrustLevel(str, enum.Enum):  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustLevel."""

        MECHANICALLY_VERIFIED = "mechanically_verified"
        SOLVER_DISCHARGED = "solver_discharged"
        RUNTIME_WITNESSED = "runtime_witnessed"
        HUMAN_ATTESTED = "human_attested"
        ORACLE_PROPOSED = "oracle_proposed"
        COPILOT_SUGGESTED = "copilot_suggested"
        UNVERIFIED = "unverified"
        CONTRADICTED = "contradicted"

    class TrustAlgebra:  # type: ignore[no-redef]
        """Stub for jugeo.evidence.trust.TrustAlgebra."""


try:
    from jugeo.orchestration.controller import OrchestratorState, Orchestrator  # type: ignore[import]

    _CONTROLLER_AVAILABLE = True
except Exception:
    _CONTROLLER_AVAILABLE = False

    class OrchestratorState:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.controller.OrchestratorState."""

    class Orchestrator:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.controller.Orchestrator."""


try:
    from jugeo.orchestration.fleet import FleetMember, Fleet  # type: ignore[import]

    _FLEET_AVAILABLE = True
except Exception:
    _FLEET_AVAILABLE = False

    class FleetMember:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.fleet.FleetMember."""

    class Fleet:  # type: ignore[no-redef]
        """Stub for jugeo.orchestration.fleet.Fleet."""


try:
    from jugeo.orchestration.mixed_evidence_routing.channel_selection import (  # type: ignore[import]
        ChannelSelector,
        ChannelLoadBalancer,
    )

    _CHANNEL_SELECTOR_AVAILABLE = True
except Exception:
    _CHANNEL_SELECTOR_AVAILABLE = False

    class ChannelSelector:  # type: ignore[no-redef]
        """Stub for ChannelSelector."""

    class ChannelLoadBalancer:  # type: ignore[no-redef]
        """Stub for ChannelLoadBalancer."""


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PolicyPriority
# ---------------------------------------------------------------------------


class PolicyPriority(str, enum.Enum):
    """Ordered priority labels for routing policies.

    Policies with a higher priority rank are evaluated (and their actions
    applied) before policies with a lower priority rank.  The ranking
    follows the convention established in theory2.tex Ch 45 §45.7.1:

    ``CRITICAL > HIGH > MEDIUM > LOW > BACKGROUND``

    Attributes:
        CRITICAL: Reserved for safety- or compliance-critical overrides
                  (e.g., "never route legal claims to an LLM").
        HIGH: Operationally important but not safety-critical (e.g., solver
              preference for mathematical proofs).
        MEDIUM: Normal policy rules that adjust default routing behaviour.
        LOW: Advisory or soft-preference rules applied after all hard rules.
        BACKGROUND: Maintenance or telemetry actions that run last and must
                    not affect the routing outcome.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    BACKGROUND = "background"

    @classmethod
    def rank(cls, p: "PolicyPriority") -> int:
        """Return an integer rank for *p* (higher integer = higher priority).

        The mapping is:
        ``CRITICAL → 4``, ``HIGH → 3``, ``MEDIUM → 2``, ``LOW → 1``,
        ``BACKGROUND → 0``.

        Args:
            p: A :class:`PolicyPriority` member or a string value such as
               ``"critical"``.

        Returns:
            Integer rank in the range ``[0, 4]``.

        Raises:
            KeyError: If *p* is not a valid priority value.
        """
        _ranks = {
            cls.CRITICAL: 4,
            cls.HIGH: 3,
            cls.MEDIUM: 2,
            cls.LOW: 1,
            cls.BACKGROUND: 0,
        }
        if isinstance(p, str) and not isinstance(p, cls):
            p = cls(p)
        return _ranks[p]


# ---------------------------------------------------------------------------
# PolicyCondition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyCondition:
    """An immutable predicate on a routing request dictionary.

    A :class:`PolicyCondition` wraps a Python callable
    ``predicate: (dict) -> bool`` together with identifying metadata.  The
    routing engine calls :meth:`evaluate` and treats ``True`` as "this
    condition is satisfied by the current request."

    All fields are immutable.  The ``predicate`` callable is excluded from
    ``__eq__`` and ``__hash__`` computations (via ``field(hash=False,
    compare=False)``) so that two conditions with identical *condition_id* are
    considered equal regardless of the specific function object.

    Attributes:
        condition_id: Stable unique identifier for this condition (e.g.
                      ``"cond-claim-kind-legal"``).
        name: Short human-readable name displayed in logs and reports.
        description: Longer explanation of what the condition tests.
        predicate: Callable ``(dict) -> bool``.  Receives the raw routing
                   request dictionary and returns ``True`` when the condition
                   is satisfied.  Must not raise exceptions; return ``False``
                   on error.
        metadata: Arbitrary key/value pairs for tooling (e.g. author, version,
                  source reference).
    """

    condition_id: str
    name: str
    description: str
    predicate: Callable[[dict], bool] = field(hash=False, compare=False)
    metadata: dict = field(default_factory=dict, hash=False, compare=False)

    def evaluate(self, request: dict) -> bool:
        """Evaluate the condition against *request*.

        Calls :attr:`predicate` with *request* and returns the boolean result.
        Any exception raised by the predicate is caught, logged at WARNING
        level, and treated as ``False`` so that a faulty predicate does not
        crash the policy engine.

        Args:
            request: The routing request dictionary.

        Returns:
            ``True`` if the condition is satisfied, ``False`` otherwise.
        """
        try:
            result = self.predicate(request)
            _log.debug(
                "Condition %s (%s) evaluated to %s",
                self.condition_id,
                self.name,
                result,
            )
            return bool(result)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Condition %s raised an exception during evaluation: %s; "
                "treating as False.",
                self.condition_id,
                exc,
            )
            return False

    def to_dict(self) -> dict:
        """Serialise the condition to a JSON-compatible dictionary.

        The ``predicate`` callable is replaced by a ``"predicate_desc"``
        string so that the result can be stored or transmitted.

        Returns:
            Dictionary with keys: ``condition_id``, ``name``, ``description``,
            ``predicate_desc``, ``metadata``.
        """
        return {
            "condition_id": self.condition_id,
            "name": self.name,
            "description": self.description,
            "predicate_desc": getattr(self.predicate, "__doc__", None)
            or getattr(self.predicate, "__name__", str(self.predicate)),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# PolicyAction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyAction:
    """An immutable action applied to a routing request when a policy fires.

    Actions transform the routing request by setting, modifying, or removing
    keys.  The :attr:`action_type` string determines the semantics of
    :meth:`apply`:

    - ``"force_channel"`` — set ``request["forced_channel"]`` to
      ``parameters["channel"]`` (an :class:`~jugeo.orchestration
      .mixed_evidence_routing.models.EvidenceChannel` value string).
    - ``"add_trust_requirement"`` — set ``request["trust_requirement"]`` to
      ``parameters["trust_level"]``.
    - ``"set_priority"`` — set ``request["priority"]`` to
      ``parameters["priority"]``.
    - ``"escalate"`` — set ``request["escalate"]`` to ``True`` and
      ``request["escalation_reason"]`` to ``parameters.get("reason", "policy")``.
    - ``"reject"`` — set ``request["rejected"]`` to ``True`` and
      ``request["rejection_reason"]`` to ``parameters.get("reason", "policy")``.
    - Any other type is treated as a generic key-value merge: each entry in
      ``parameters`` is written into the request.

    Attributes:
        action_id: Stable unique identifier for this action instance.
        action_type: One of the recognised type strings above.
        parameters: Type-specific parameters (see type descriptions above).
        metadata: Arbitrary key/value pairs for tooling.
    """

    action_id: str
    action_type: str
    parameters: dict = field(default_factory=dict, hash=False, compare=False)
    metadata: dict = field(default_factory=dict, hash=False, compare=False)

    def apply(self, request: dict) -> dict:
        """Apply this action to *request* and return the modified copy.

        The original *request* dictionary is **not** mutated; a shallow copy
        is made before modifications are applied.

        Args:
            request: The current routing request dictionary.

        Returns:
            A new dictionary that is a shallow copy of *request* with the
            action's modifications applied.

        Raises:
            No exceptions are raised; errors are logged at WARNING level.
        """
        result = dict(request)
        try:
            if self.action_type == "force_channel":
                channel_val = self.parameters.get("channel")
                result["forced_channel"] = channel_val
                _log.debug(
                    "Action %s: forced_channel set to %s", self.action_id, channel_val
                )
            elif self.action_type == "add_trust_requirement":
                trust_val = self.parameters.get("trust_level")
                result["trust_requirement"] = trust_val
                _log.debug(
                    "Action %s: trust_requirement set to %s",
                    self.action_id,
                    trust_val,
                )
            elif self.action_type == "set_priority":
                prio_val = self.parameters.get("priority")
                result["priority"] = prio_val
                _log.debug(
                    "Action %s: priority set to %s", self.action_id, prio_val
                )
            elif self.action_type == "escalate":
                reason = self.parameters.get("reason", "policy")
                result["escalate"] = True
                result["escalation_reason"] = reason
                _log.debug(
                    "Action %s: escalate=True, reason=%s", self.action_id, reason
                )
            elif self.action_type == "reject":
                reason = self.parameters.get("reason", "policy")
                result["rejected"] = True
                result["rejection_reason"] = reason
                _log.debug(
                    "Action %s: rejected=True, reason=%s", self.action_id, reason
                )
            else:
                # Generic merge — write all parameters into the request.
                result.update(self.parameters)
                _log.debug(
                    "Action %s (type=%s): merged %d parameter(s) into request",
                    self.action_id,
                    self.action_type,
                    len(self.parameters),
                )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Action %s failed during apply: %s; request unchanged.",
                self.action_id,
                exc,
            )
        return result

    def to_dict(self) -> dict:
        """Serialise the action to a JSON-compatible dictionary.

        Returns:
            Dictionary with keys: ``action_id``, ``action_type``,
            ``parameters``, ``metadata``.
        """
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "parameters": dict(self.parameters),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# RoutingPolicy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """A named, versioned routing policy composed of conditions and actions.

    A :class:`RoutingPolicy` matches a routing request when **all** of its
    :attr:`conditions` evaluate to ``True``.  If it matches, **all** of its
    :attr:`actions` are applied in order.

    Policies are immutable value objects.  The :attr:`policy_id` serves as
    the stable identity key used by the :class:`PolicyEngine` registry.

    Attributes:
        policy_id: Stable unique identifier (e.g. ``"force-human-for-legal"``).
        name: Short human-readable label.
        description: Detailed explanation of the policy's intent and the
                     theory2.tex section that motivates it.
        priority: :class:`PolicyPriority` rank.  Higher-priority policies are
                  evaluated and applied first.
        conditions: Tuple of :class:`PolicyCondition` objects.  All must be
                    satisfied for the policy to match.
        actions: Tuple of :class:`PolicyAction` objects applied in order when
                 the policy matches.
        enabled: When ``False`` the policy is registered but never evaluated.
        created_at: Unix timestamp of creation (set automatically if not
                    provided).
        metadata: Arbitrary key/value pairs.
    """

    policy_id: str
    name: str
    description: str
    priority: PolicyPriority
    conditions: tuple
    actions: tuple
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict, hash=False, compare=False)

    def matches(self, request: dict) -> bool:
        """Return ``True`` if every condition in :attr:`conditions` is satisfied.

        Conditions are evaluated in order.  The method short-circuits on the
        first ``False`` result for efficiency.  If :attr:`enabled` is
        ``False`` the method always returns ``False``.

        Args:
            request: The routing request dictionary.

        Returns:
            ``True`` only when the policy is enabled and all conditions match.
        """
        if not self.enabled:
            _log.debug("Policy %s is disabled; skipping.", self.policy_id)
            return False
        for condition in self.conditions:
            if not condition.evaluate(request):
                _log.debug(
                    "Policy %s: condition %s did not match.",
                    self.policy_id,
                    condition.condition_id,
                )
                return False
        _log.debug("Policy %s matched request.", self.policy_id)
        return True

    def apply(self, request: dict) -> dict:
        """Apply all actions to *request* sequentially and return the result.

        Each action receives the output of the previous action.  The original
        *request* is never mutated.

        Args:
            request: The routing request dictionary.

        Returns:
            Modified request dictionary after all actions have been applied.
        """
        current = dict(request)
        for action in self.actions:
            current = action.apply(current)
        _log.info(
            "Policy %s (%s) applied %d action(s) to request %s",
            self.policy_id,
            self.name,
            len(self.actions),
            request.get("request_id", "<unknown>"),
        )
        return current

    def to_dict(self) -> dict:
        """Serialise the policy to a JSON-compatible dictionary.

        Returns:
            Dictionary with all fields; ``conditions`` and ``actions`` are
            each serialised via their own ``to_dict`` methods.
        """
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority.value,
            "conditions": [c.to_dict() for c in self.conditions],
            "actions": [a.to_dict() for a in self.actions],
            "enabled": self.enabled,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RoutingPolicy":
        """Reconstruct a :class:`RoutingPolicy` from a serialised dictionary.

        Conditions and actions are reconstructed as objects with trivial
        always-True or no-op callables because the original Python callables
        cannot be deserialised from JSON.  Callers that need live predicates
        must re-register conditions after deserialisation.

        Args:
            d: Dictionary as produced by :meth:`to_dict`.

        Returns:
            A new :class:`RoutingPolicy` instance.
        """
        conditions = tuple(
            PolicyCondition(
                condition_id=c["condition_id"],
                name=c["name"],
                description=c["description"],
                predicate=lambda req, _desc=c.get("predicate_desc", ""): True,
                metadata=c.get("metadata", {}),
            )
            for c in d.get("conditions", [])
        )
        actions = tuple(
            PolicyAction(
                action_id=a["action_id"],
                action_type=a["action_type"],
                parameters=a.get("parameters", {}),
                metadata=a.get("metadata", {}),
            )
            for a in d.get("actions", [])
        )
        return cls(
            policy_id=d["policy_id"],
            name=d["name"],
            description=d.get("description", ""),
            priority=PolicyPriority(d["priority"]),
            conditions=conditions,
            actions=actions,
            enabled=d.get("enabled", True),
            created_at=d.get("created_at", time.time()),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# PolicyConflict
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyConflict:
    """An immutable record describing a detected conflict between two policies.

    Conflicts are detected by :class:`PolicyConflictDetector` and stored in
    its output list.  Each conflict has a *type* that categorises the nature
    of the incompatibility, as described in theory2.tex Ch 45 §45.7.5.

    Recognised ``conflict_type`` values:

    - ``"contradictory_actions"`` — the two policies both match under
      overlapping conditions yet their actions assign incompatible values to
      the same request key (e.g., one forces Z3 and the other forces HUMAN).
    - ``"overlapping_conditions"`` — both policies share at least one
      condition that tests the same key/value pair, causing both to fire
      whenever that condition holds.  This is not necessarily an error but
      may indicate an unintended interaction.
    - ``"redundant_action"`` — both policies perform the same action, which
      is wasteful but harmless.

    Attributes:
        conflict_id: Unique identifier for this conflict record.
        policy_a_id: ID of the first policy.
        policy_b_id: ID of the second policy.
        conflict_type: One of ``"contradictory_actions"``,
                       ``"overlapping_conditions"``, or ``"redundant_action"``.
        description: Human-readable explanation of the specific conflict.
        timestamp: Unix timestamp when the conflict was detected.
    """

    conflict_id: str
    policy_a_id: str
    policy_b_id: str
    conflict_type: str
    description: str
    timestamp: float

    def to_dict(self) -> dict:
        """Serialise the conflict record to a JSON-compatible dictionary.

        Returns:
            Dictionary with all fields.
        """
        return {
            "conflict_id": self.conflict_id,
            "policy_a_id": self.policy_a_id,
            "policy_b_id": self.policy_b_id,
            "conflict_type": self.conflict_type,
            "description": self.description,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# PolicyConflictDetector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PolicyConflictDetector:
    """Detects conflicts between registered routing policies.

    The detector performs a syntactic / structural analysis over the
    action and condition sets of every pair of policies.  It does **not**
    require executing policies; instead it inspects the ``action_type`` and
    ``parameters`` of each :class:`PolicyAction` and the ``condition_id``
    strings of each :class:`PolicyCondition`.

    This is a best-effort, conservative analysis: it may produce false
    positives (flagging non-conflicting policies) but will not miss true
    conflicts that are visible from the policy structure alone.  See
    theory2.tex Ch 45 §45.7.5 for the formal definition of conflict.

    Attributes:
        detector_id: Unique identifier for this detector instance.
    """

    detector_id: str

    def detect(self, policies: list[RoutingPolicy]) -> list[PolicyConflict]:
        """Analyse *policies* pairwise and return all detected conflicts.

        For each pair ``(p1, p2)`` of enabled policies the detector checks:

        1. Whether their conditions overlap (:meth:`_conditions_overlap`).
        2. Whether any pair of actions conflict
           (:meth:`_actions_conflict`).

        Only conflicts for pairs where conditions overlap AND actions conflict
        are reported as ``"contradictory_actions"``.  Pure condition overlap
        without action conflict is reported as ``"overlapping_conditions"``.

        Args:
            policies: List of :class:`RoutingPolicy` instances to analyse.

        Returns:
            List of :class:`PolicyConflict` records (may be empty).
        """
        conflicts: list[PolicyConflict] = []
        enabled = [p for p in policies if p.enabled]
        for i, p1 in enumerate(enabled):
            for p2 in enabled[i + 1 :]:
                overlap = self._conditions_overlap(p1, p2)
                action_conflict = any(
                    self._actions_conflict(a1, a2)
                    for a1 in p1.actions
                    for a2 in p2.actions
                )
                if overlap and action_conflict:
                    conflicts.append(
                        PolicyConflict(
                            conflict_id=str(uuid.uuid4()),
                            policy_a_id=p1.policy_id,
                            policy_b_id=p2.policy_id,
                            conflict_type="contradictory_actions",
                            description=(
                                f"Policies '{p1.name}' and '{p2.name}' share "
                                f"overlapping conditions and have contradictory "
                                f"actions that modify the same request keys."
                            ),
                            timestamp=time.time(),
                        )
                    )
                    _log.warning(
                        "Conflict detected between policies %s and %s "
                        "(contradictory_actions).",
                        p1.policy_id,
                        p2.policy_id,
                    )
                elif overlap:
                    conflicts.append(
                        PolicyConflict(
                            conflict_id=str(uuid.uuid4()),
                            policy_a_id=p1.policy_id,
                            policy_b_id=p2.policy_id,
                            conflict_type="overlapping_conditions",
                            description=(
                                f"Policies '{p1.name}' and '{p2.name}' share at "
                                f"least one condition with the same condition_id, "
                                f"meaning both may fire on the same request."
                            ),
                            timestamp=time.time(),
                        )
                    )
                    _log.debug(
                        "Overlapping conditions between policies %s and %s.",
                        p1.policy_id,
                        p2.policy_id,
                    )
        return conflicts

    def _actions_conflict(
        self, a: PolicyAction, b: PolicyAction
    ) -> bool:
        """Return ``True`` if actions *a* and *b* are contradictory.

        Two actions conflict when they both write to the same logical key with
        different values.  The key affected by each action type is determined
        by convention:

        - ``"force_channel"`` writes to ``"forced_channel"``
        - ``"add_trust_requirement"`` writes to ``"trust_requirement"``
        - ``"set_priority"`` writes to ``"priority"``
        - ``"escalate"`` and ``"reject"`` both write to a disposition key
          and conflict with each other.

        Args:
            a: First :class:`PolicyAction`.
            b: Second :class:`PolicyAction`.

        Returns:
            ``True`` if the two actions are structurally contradictory.
        """
        _key_for_type: dict[str, str] = {
            "force_channel": "forced_channel",
            "add_trust_requirement": "trust_requirement",
            "set_priority": "priority",
        }
        disposition_types = {"escalate", "reject"}

        if a.action_type in disposition_types and b.action_type in disposition_types:
            if a.action_type != b.action_type:
                return True

        if a.action_type in _key_for_type and b.action_type == a.action_type:
            key = _key_for_type[a.action_type]
            val_a = a.parameters.get(
                list(a.parameters.keys())[0] if a.parameters else "", None
            )
            val_b = b.parameters.get(
                list(b.parameters.keys())[0] if b.parameters else "", None
            )
            return val_a != val_b

        return False

    def _conditions_overlap(
        self, p1: RoutingPolicy, p2: RoutingPolicy
    ) -> bool:
        """Return ``True`` if *p1* and *p2* share at least one condition id.

        Two policies are considered to have overlapping conditions when they
        both contain a :class:`PolicyCondition` with the same
        ``condition_id``.  This is a conservative proxy for the semantic
        notion of condition overlap defined in theory2.tex §45.7.5.

        Args:
            p1: First :class:`RoutingPolicy`.
            p2: Second :class:`RoutingPolicy`.

        Returns:
            ``True`` if any ``condition_id`` appears in both policies.
        """
        ids_1 = {c.condition_id for c in p1.conditions}
        ids_2 = {c.condition_id for c in p2.conditions}
        return bool(ids_1 & ids_2)


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PolicyEngine:
    """Evaluates and applies an ordered set of routing policies to requests.

    The engine maintains a registry of :class:`RoutingPolicy` objects and
    applies them in decreasing priority order.  Each evaluation cycle:

    1. Filters to only enabled policies via :meth:`evaluate`.
    2. Sorts matching policies by :class:`PolicyPriority` rank (highest
       first).
    3. Applies each matching policy's actions sequentially via
       :meth:`apply_all`, threading the modified request through the chain.

    Every application is logged to :attr:`execution_log` (bounded to
    :attr:`max_log_size` entries).  The engine delegates conflict detection
    to :attr:`conflict_detector`.

    Attributes:
        engine_id: Unique identifier for this engine instance.
        policies: Mutable list of all registered :class:`RoutingPolicy`
                  objects (enabled and disabled).
        conflict_detector: :class:`PolicyConflictDetector` instance used by
                           :meth:`detect_conflicts`.
        execution_log: Rolling log of policy application events; each entry
                       is a dict with keys ``timestamp``, ``request_id``,
                       ``policy_id``, ``policy_name``, and ``actions_applied``.
        max_log_size: Maximum number of entries retained in
                      :attr:`execution_log`.  Older entries are dropped when
                      the limit is exceeded.
    """

    engine_id: str
    policies: list = field(default_factory=list)
    conflict_detector: PolicyConflictDetector = field(
        default_factory=lambda: PolicyConflictDetector(
            detector_id=str(uuid.uuid4())
        )
    )
    execution_log: list = field(default_factory=list)
    max_log_size: int = 1000

    def register(self, policy: RoutingPolicy) -> None:
        """Register *policy* with the engine.

        If a policy with the same :attr:`~RoutingPolicy.policy_id` already
        exists it is replaced.

        Args:
            policy: :class:`RoutingPolicy` to register.
        """
        existing_ids = [p.policy_id for p in self.policies]
        if policy.policy_id in existing_ids:
            idx = existing_ids.index(policy.policy_id)
            self.policies[idx] = policy
            _log.info("Policy %s replaced in engine %s.", policy.policy_id, self.engine_id)
        else:
            self.policies.append(policy)
            _log.info(
                "Policy %s (%s) registered in engine %s.",
                policy.policy_id,
                policy.name,
                self.engine_id,
            )

    def unregister(self, policy_id: str) -> bool:
        """Remove the policy with *policy_id* from the registry.

        Args:
            policy_id: ID of the policy to remove.

        Returns:
            ``True`` if the policy was found and removed, ``False`` if it did
            not exist in the registry.
        """
        before = len(self.policies)
        self.policies = [p for p in self.policies if p.policy_id != policy_id]
        removed = len(self.policies) < before
        if removed:
            _log.info("Policy %s unregistered from engine %s.", policy_id, self.engine_id)
        else:
            _log.warning(
                "Attempted to unregister unknown policy %s from engine %s.",
                policy_id,
                self.engine_id,
            )
        return removed

    def evaluate(self, request: dict) -> list[RoutingPolicy]:
        """Return all enabled policies that match *request*, sorted by priority.

        Policies are sorted so that the highest-priority (CRITICAL) policies
        appear first in the returned list.

        Args:
            request: The routing request dictionary.

        Returns:
            List of :class:`RoutingPolicy` objects that match *request*,
            ordered from highest to lowest priority.
        """
        matching = [p for p in self.policies if p.matches(request)]
        matching.sort(key=lambda p: PolicyPriority.rank(p.priority), reverse=True)
        _log.debug(
            "evaluate(): %d / %d policies matched request %s",
            len(matching),
            len(self.policies),
            request.get("request_id", "<unknown>"),
        )
        return matching

    def apply_all(self, request: dict) -> tuple[dict, list[str]]:
        """Evaluate and apply all matching policies to *request*.

        Policies are applied in priority order (highest first).  The modified
        request from one policy is fed into the next, producing a pipeline of
        transformations.

        Each application is appended to :attr:`execution_log`.  When the log
        exceeds :attr:`max_log_size` the oldest entry is dropped.

        Args:
            request: The routing request dictionary.

        Returns:
            A tuple ``(modified_request, applied_policy_ids)`` where
            *modified_request* is the final request after all policies and
            *applied_policy_ids* is an ordered list of the IDs of every policy
            that was applied.
        """
        matching = self.evaluate(request)
        current = dict(request)
        applied: list[str] = []
        for policy in matching:
            current = policy.apply(current)
            applied.append(policy.policy_id)
            entry: dict[str, Any] = {
                "timestamp": time.time(),
                "request_id": request.get("request_id", "<unknown>"),
                "policy_id": policy.policy_id,
                "policy_name": policy.name,
                "actions_applied": len(policy.actions),
            }
            self.execution_log.append(entry)
            if len(self.execution_log) > self.max_log_size:
                self.execution_log.pop(0)
        return current, applied

    def detect_conflicts(self) -> list[PolicyConflict]:
        """Delegate to :attr:`conflict_detector` and return any conflicts found.

        Returns:
            List of :class:`PolicyConflict` records.  Empty when no conflicts
            are detected.
        """
        return self.conflict_detector.detect(self.policies)

    def enabled_count(self) -> int:
        """Return the number of currently enabled policies.

        Returns:
            Non-negative integer.
        """
        return sum(1 for p in self.policies if p.enabled)

    def stats(self) -> dict:
        """Return a summary statistics dictionary for this engine.

        Returns:
            Dictionary with keys:

            - ``"engine_id"`` — the engine's ID string.
            - ``"total_policies"`` — total registered policies.
            - ``"enabled_policies"`` — count of enabled policies.
            - ``"log_entries"`` — current number of execution log entries.
            - ``"max_log_size"`` — configured log size limit.
            - ``"priority_breakdown"`` — dict mapping priority value string
              to count of registered policies at that priority.
        """
        priority_breakdown: dict[str, int] = {p.value: 0 for p in PolicyPriority}
        for pol in self.policies:
            priority_breakdown[pol.priority.value] += 1
        return {
            "engine_id": self.engine_id,
            "total_policies": len(self.policies),
            "enabled_policies": self.enabled_count(),
            "log_entries": len(self.execution_log),
            "max_log_size": self.max_log_size,
            "priority_breakdown": priority_breakdown,
        }


# ---------------------------------------------------------------------------
# PolicyCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PolicyCoordinator:
    """Top-level coordinator that manages the engine and a named policy library.

    The :class:`PolicyCoordinator` is the primary entry point for operator
    tooling.  It wraps a :class:`PolicyEngine` and maintains a named
    :attr:`policy_library` that maps ``policy_id`` strings to
    :class:`RoutingPolicy` objects.

    Calling :meth:`load_default_policies` populates the library with five
    default policies that cover the most common compliance and operational
    scenarios described in theory2.tex Ch 45 §45.7:

    1. **force_human_for_legal** — routes legal claims exclusively to the
       human expert channel.
    2. **require_solver_for_math** — forces mathematical proof claims to the
       Z3 solver channel.
    3. **trust_ceiling_copilot** — rejects requests that demand
       ``MECHANICALLY_VERIFIED`` trust but are tagged for the Copilot LLM.
    4. **escalate_on_timeout** — escalates any request that has already
       timed out so a human can intervene.
    5. **load_balance_z3** — falls back to the runtime witness channel when
       Z3 load exceeds 80 %.

    Attributes:
        coordinator_id: Unique identifier for this coordinator instance.
        engine: The :class:`PolicyEngine` managed by this coordinator.
        policy_library: Dict mapping ``policy_id`` → :class:`RoutingPolicy`.
    """

    coordinator_id: str
    engine: PolicyEngine = field(
        default_factory=lambda: PolicyEngine(engine_id=str(uuid.uuid4()))
    )
    policy_library: dict = field(default_factory=dict)

    def load_default_policies(self) -> None:
        """Create and register the five default routing policies.

        Each policy is built from :class:`PolicyCondition` and
        :class:`PolicyAction` objects with meaningful predicates and actions.
        Policies are added to both :attr:`policy_library` and the underlying
        :attr:`engine`.

        The five policies and their semantics are documented in the class
        docstring and in theory2.tex Ch 45 §45.7.
        """
        # ------------------------------------------------------------------
        # 1. force_human_for_legal
        # ------------------------------------------------------------------
        cond_legal = PolicyCondition(
            condition_id="cond-claim-kind-legal",
            name="claim_kind is legal",
            description=(
                "Evaluates to True when the routing request's 'claim_kind' "
                "field equals 'legal'.  Legal claims require human expert "
                "review (theory2.tex §45.7.2)."
            ),
            predicate=lambda req: req.get("claim_kind") == "legal",
            metadata={"source": "theory2.tex §45.7.2"},
        )
        act_force_human = PolicyAction(
            action_id="act-force-human-channel",
            action_type="force_channel",
            parameters={"channel": EvidenceChannel.HUMAN.value},
            metadata={"rationale": "Legal claims must be reviewed by a qualified human."},
        )
        policy_legal = RoutingPolicy(
            policy_id="force_human_for_legal",
            name="Force Human Channel for Legal Claims",
            description=(
                "Overrides channel selection to HUMAN for any request whose "
                "'claim_kind' is 'legal'.  This implements the mandatory human "
                "review requirement specified in theory2.tex Ch 45 §45.7.2."
            ),
            priority=PolicyPriority.CRITICAL,
            conditions=(cond_legal,),
            actions=(act_force_human,),
            enabled=True,
            metadata={"category": "compliance", "ref": "§45.7.2"},
        )

        # ------------------------------------------------------------------
        # 2. require_solver_for_math
        # ------------------------------------------------------------------
        cond_math = PolicyCondition(
            condition_id="cond-claim-kind-math-proof",
            name="claim_kind is mathematical_proof",
            description=(
                "Evaluates to True when 'claim_kind' equals 'mathematical_proof'. "
                "Such claims should be discharged by the Z3 solver for maximum "
                "trust (theory2.tex §45.7.2)."
            ),
            predicate=lambda req: req.get("claim_kind") == "mathematical_proof",
            metadata={"source": "theory2.tex §45.7.2"},
        )
        act_force_z3 = PolicyAction(
            action_id="act-force-z3-channel",
            action_type="force_channel",
            parameters={"channel": EvidenceChannel.Z3.value},
            metadata={"rationale": "Math proofs must be formally verified by Z3."},
        )
        act_trust_solver = PolicyAction(
            action_id="act-trust-solver-discharged",
            action_type="add_trust_requirement",
            parameters={"trust_level": TrustLevel.SOLVER_DISCHARGED.value
                        if hasattr(TrustLevel, "SOLVER_DISCHARGED") else "solver_discharged"},
            metadata={"rationale": "Require solver-discharged trust for math proofs."},
        )
        policy_math = RoutingPolicy(
            policy_id="require_solver_for_math",
            name="Require Z3 Solver for Mathematical Proofs",
            description=(
                "Forces the Z3 evidence channel for 'mathematical_proof' claims "
                "and sets the trust requirement to SOLVER_DISCHARGED.  "
                "Implements theory2.tex Ch 45 §45.7.2 formal verification policy."
            ),
            priority=PolicyPriority.HIGH,
            conditions=(cond_math,),
            actions=(act_force_z3, act_trust_solver),
            enabled=True,
            metadata={"category": "formal_verification", "ref": "§45.7.2"},
        )

        # ------------------------------------------------------------------
        # 3. trust_ceiling_copilot
        # ------------------------------------------------------------------
        cond_mech_verified = PolicyCondition(
            condition_id="cond-trust-req-mechanically-verified",
            name="trust_requirement is MECHANICALLY_VERIFIED",
            description=(
                "Evaluates to True when the request demands "
                "'mechanically_verified' trust level.  Copilot LLM cannot "
                "satisfy this trust ceiling (theory2.tex §45.7.4)."
            ),
            predicate=lambda req: req.get("trust_requirement") in (
                "mechanically_verified",
                TrustLevel.MECHANICALLY_VERIFIED.value
                if hasattr(TrustLevel, "MECHANICALLY_VERIFIED") else "mechanically_verified",
            ),
            metadata={"source": "theory2.tex §45.7.4"},
        )
        cond_copilot_channel = PolicyCondition(
            condition_id="cond-channel-is-copilot",
            name="requested channel or forced_channel is copilot_llm",
            description=(
                "Evaluates to True when the routing request targets the "
                "COPILOT_LLM channel, either via 'channel' or 'forced_channel'."
            ),
            predicate=lambda req: req.get("channel") in (
                EvidenceChannel.COPILOT_LLM.value, "copilot_llm"
            ) or req.get("forced_channel") in (
                EvidenceChannel.COPILOT_LLM.value, "copilot_llm"
            ),
            metadata={"source": "theory2.tex §45.7.4"},
        )
        act_reject_copilot = PolicyAction(
            action_id="act-reject-copilot-for-mech-verified",
            action_type="reject",
            parameters={
                "reason": (
                    "COPILOT_LLM channel cannot satisfy MECHANICALLY_VERIFIED "
                    "trust requirement (theory2.tex §45.7.4 trust ceiling)."
                )
            },
            metadata={"rationale": "Trust ceiling violation."},
        )
        policy_trust_ceiling = RoutingPolicy(
            policy_id="trust_ceiling_copilot",
            name="Reject Copilot When Mechanically Verified Trust Required",
            description=(
                "Rejects requests that demand MECHANICALLY_VERIFIED trust "
                "but are routed to the COPILOT_LLM channel.  The LLM cannot "
                "provide formal verification evidence (theory2.tex §45.7.4)."
            ),
            priority=PolicyPriority.CRITICAL,
            conditions=(cond_mech_verified, cond_copilot_channel),
            actions=(act_reject_copilot,),
            enabled=True,
            metadata={"category": "trust_enforcement", "ref": "§45.7.4"},
        )

        # ------------------------------------------------------------------
        # 4. escalate_on_timeout
        # ------------------------------------------------------------------
        cond_timed_out = PolicyCondition(
            condition_id="cond-timed-out-true",
            name="request has timed_out=True",
            description=(
                "Evaluates to True when the routing request contains "
                "'timed_out': True, indicating a previous routing attempt "
                "exceeded its deadline."
            ),
            predicate=lambda req: bool(req.get("timed_out", False)),
            metadata={"source": "theory2.tex §45.7.6"},
        )
        act_escalate_timeout = PolicyAction(
            action_id="act-escalate-on-timeout",
            action_type="escalate",
            parameters={
                "reason": "Previous routing attempt timed out; escalating to human."
            },
            metadata={"rationale": "Prevents infinite retry loops on timed-out requests."},
        )
        act_set_high_priority = PolicyAction(
            action_id="act-set-high-priority-on-timeout",
            action_type="set_priority",
            parameters={"priority": PolicyPriority.HIGH.value},
            metadata={"rationale": "Timed-out escalations deserve elevated queue priority."},
        )
        policy_timeout = RoutingPolicy(
            policy_id="escalate_on_timeout",
            name="Escalate Timed-Out Requests to Human",
            description=(
                "Escalates any request that has previously timed out to the "
                "human review queue and sets its priority to HIGH.  Prevents "
                "silent failures when solvers or LLMs are unresponsive "
                "(theory2.tex §45.7.6)."
            ),
            priority=PolicyPriority.HIGH,
            conditions=(cond_timed_out,),
            actions=(act_escalate_timeout, act_set_high_priority),
            enabled=True,
            metadata={"category": "resilience", "ref": "§45.7.6"},
        )

        # ------------------------------------------------------------------
        # 5. load_balance_z3
        # ------------------------------------------------------------------
        cond_z3_overloaded = PolicyCondition(
            condition_id="cond-z3-load-above-threshold",
            name="Z3 load factor exceeds 0.8",
            description=(
                "Evaluates to True when the request carries a 'z3_load' metric "
                "greater than 0.8, indicating Z3 is near capacity.  Falls back "
                "to the runtime witness channel (theory2.tex §45.7.7)."
            ),
            predicate=lambda req: float(req.get("z3_load", 0.0)) > 0.8,
            metadata={"source": "theory2.tex §45.7.7"},
        )
        act_fallback_witness = PolicyAction(
            action_id="act-fallback-to-runtime-witness",
            action_type="force_channel",
            parameters={"channel": EvidenceChannel.RUNTIME_WITNESS.value},
            metadata={"rationale": "Z3 overload: fallback to runtime witness."},
        )
        policy_z3_lb = RoutingPolicy(
            policy_id="load_balance_z3",
            name="Load-Balance Z3 by Falling Back to Runtime Witness",
            description=(
                "When the Z3 solver load exceeds 80 %, forces routing to the "
                "RUNTIME_WITNESS channel to preserve system throughput.  "
                "Implements the load-shedding policy in theory2.tex §45.7.7."
            ),
            priority=PolicyPriority.MEDIUM,
            conditions=(cond_z3_overloaded,),
            actions=(act_fallback_witness,),
            enabled=True,
            metadata={"category": "load_balancing", "ref": "§45.7.7"},
        )

        for policy in (
            policy_legal,
            policy_math,
            policy_trust_ceiling,
            policy_timeout,
            policy_z3_lb,
        ):
            self.policy_library[policy.policy_id] = policy
            self.engine.register(policy)
        _log.info(
            "PolicyCoordinator %s loaded %d default policies.",
            self.coordinator_id,
            len(self.policy_library),
        )

    def route_with_policy(self, request: dict) -> tuple[dict, list[str]]:
        """Apply all matching policies to *request* and return the result.

        This is the primary entry point for routing requests through the
        policy layer.  It delegates to :meth:`PolicyEngine.apply_all` and
        ensures the request carries a ``"request_id"`` if one is absent.

        Args:
            request: The routing request dictionary.  Should contain at
                     minimum a ``"claim_kind"`` key.

        Returns:
            Tuple ``(modified_request, applied_policy_ids)`` as returned by
            :meth:`PolicyEngine.apply_all`.
        """
        if "request_id" not in request:
            request = {**request, "request_id": str(uuid.uuid4())}
        modified, applied = self.engine.apply_all(request)
        _log.info(
            "route_with_policy: request %s → %d policy/policies applied: %s",
            request.get("request_id"),
            len(applied),
            applied,
        )
        return modified, applied

    def add_policy(self, policy: RoutingPolicy) -> None:
        """Add *policy* to the library and register it with the engine.

        Args:
            policy: The :class:`RoutingPolicy` to add.
        """
        self.policy_library[policy.policy_id] = policy
        self.engine.register(policy)
        _log.info(
            "PolicyCoordinator %s: policy %s added.", self.coordinator_id, policy.policy_id
        )

    def remove_policy(self, policy_id: str) -> bool:
        """Remove the policy with *policy_id* from both the library and engine.

        Args:
            policy_id: ID of the policy to remove.

        Returns:
            ``True`` if the policy existed and was removed, ``False`` otherwise.
        """
        in_library = policy_id in self.policy_library
        if in_library:
            del self.policy_library[policy_id]
        engine_removed = self.engine.unregister(policy_id)
        removed = in_library or engine_removed
        if removed:
            _log.info(
                "PolicyCoordinator %s: policy %s removed.",
                self.coordinator_id,
                policy_id,
            )
        return removed

    def compliance_check(self) -> dict:
        """Return a compliance summary for the current policy configuration.

        Runs conflict detection and collects per-policy status to produce an
        operational compliance report.

        Returns:
            Dictionary with keys:

            - ``"coordinator_id"``
            - ``"total_policies"``
            - ``"enabled_policies"``
            - ``"disabled_policies"``
            - ``"conflicts_detected"``
            - ``"conflicts"`` — list of conflict dicts
            - ``"engine_stats"`` — from :meth:`PolicyEngine.stats`
            - ``"policy_ids"`` — list of all policy IDs in the library
            - ``"compliance_status"`` — ``"ok"`` if no contradictory_actions
              conflicts, ``"warning"`` if overlapping conditions, ``"error"``
              if contradictory actions exist.
        """
        conflicts = self.engine.detect_conflicts()
        has_contradiction = any(
            c.conflict_type == "contradictory_actions" for c in conflicts
        )
        has_overlap = any(
            c.conflict_type == "overlapping_conditions" for c in conflicts
        )
        if has_contradiction:
            status = "error"
        elif has_overlap:
            status = "warning"
        else:
            status = "ok"

        total = len(self.policy_library)
        enabled = sum(1 for p in self.policy_library.values() if p.enabled)
        return {
            "coordinator_id": self.coordinator_id,
            "total_policies": total,
            "enabled_policies": enabled,
            "disabled_policies": total - enabled,
            "conflicts_detected": len(conflicts),
            "conflicts": [c.to_dict() for c in conflicts],
            "engine_stats": self.engine.stats(),
            "policy_ids": list(self.policy_library.keys()),
            "compliance_status": status,
        }

    def export(self) -> dict:
        """Export the full coordinator state as a JSON-compatible dictionary.

        Returns:
            Dictionary with keys ``"coordinator_id"``, ``"engine_id"``,
            ``"policy_library"`` (dict mapping id → serialised policy), and
            ``"compliance_summary"``.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "engine_id": self.engine.engine_id,
            "policy_library": {
                pid: p.to_dict() for pid, p in self.policy_library.items()
            },
            "compliance_summary": self.compliance_check(),
        }


# ---------------------------------------------------------------------------
# PolicyWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PolicyWitness:
    """Audit log for policy applications and conflict records.

    The :class:`PolicyWitness` maintains an append-only event log that
    records every policy application and every conflict detected during a
    routing session.  It also provides an :meth:`verify_invariants` method
    that scans the log for violations of expected operational invariants.

    Invariants checked by :meth:`verify_invariants`:

    1. **No request is both escalated and rejected** — a single request
       should not simultaneously carry ``escalate=True`` and
       ``rejected=True`` in its final state.
    2. **Legal claims always route to HUMAN** — any application event for
       a legal-claim request should result in ``forced_channel="human"``.
    3. **Math proof requests must not route to COPILOT_LLM** — if a
       mathematical_proof request is routed to copilot the invariant fires.

    Attributes:
        witness_id: Unique identifier for this witness instance.
        events: Mutable list of event dictionaries accumulated during the
                session.
        created_at: Unix timestamp of witness creation.
    """

    witness_id: str
    events: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def record_application(
        self,
        request_id: str,
        policies_applied: list[str],
        result: dict,
    ) -> None:
        """Record that *policies_applied* were applied to *request_id*.

        Args:
            request_id: Identifier of the routing request.
            policies_applied: Ordered list of policy IDs that were applied.
            result: The final modified request dictionary after all policies
                    were applied.
        """
        event = {
            "event_type": "policy_application",
            "timestamp": time.time(),
            "request_id": request_id,
            "policies_applied": list(policies_applied),
            "result_snapshot": {
                k: v
                for k, v in result.items()
                if k
                in (
                    "forced_channel",
                    "trust_requirement",
                    "priority",
                    "escalate",
                    "escalation_reason",
                    "rejected",
                    "rejection_reason",
                    "claim_kind",
                )
            },
        }
        self.events.append(event)
        _log.debug(
            "PolicyWitness %s: recorded application of %d policies to request %s.",
            self.witness_id,
            len(policies_applied),
            request_id,
        )

    def record_conflict(self, conflict: PolicyConflict) -> None:
        """Record a detected :class:`PolicyConflict` in the event log.

        Args:
            conflict: The conflict to record.
        """
        event = {
            "event_type": "conflict_detected",
            "timestamp": time.time(),
            "conflict": conflict.to_dict(),
        }
        self.events.append(event)
        _log.warning(
            "PolicyWitness %s: recorded conflict %s between policies %s and %s.",
            self.witness_id,
            conflict.conflict_id,
            conflict.policy_a_id,
            conflict.policy_b_id,
        )

    def verify_invariants(self) -> list[str]:
        """Scan the event log and return any invariant violations found.

        Checks the following invariants on every ``"policy_application"``
        event in :attr:`events`:

        1. A request must not be both escalated and rejected.
        2. A legal claim must have ``forced_channel == "human"`` in the
           result snapshot (if policies were applied).
        3. A mathematical_proof claim must not have
           ``forced_channel == "copilot_llm"``.

        Returns:
            A list of human-readable violation strings.  Empty list means
            all invariants hold.
        """
        violations: list[str] = []
        for event in self.events:
            if event.get("event_type") != "policy_application":
                continue
            snap = event.get("result_snapshot", {})
            rid = event.get("request_id", "<unknown>")

            escalated = snap.get("escalate", False)
            rejected = snap.get("rejected", False)
            if escalated and rejected:
                violations.append(
                    f"Request {rid}: both 'escalate' and 'rejected' are True — "
                    f"invariant 1 violated (escalate ∧ reject)."
                )

            claim_kind = snap.get("claim_kind")
            forced = snap.get("forced_channel")

            if claim_kind == "legal" and forced is not None and forced != "human":
                violations.append(
                    f"Request {rid}: legal claim routed to '{forced}' instead of "
                    f"'human' — invariant 2 violated."
                )

            if claim_kind == "mathematical_proof" and forced == "copilot_llm":
                violations.append(
                    f"Request {rid}: mathematical_proof claim routed to "
                    f"'copilot_llm' — invariant 3 violated."
                )

        _log.info(
            "PolicyWitness %s: verify_invariants found %d violation(s).",
            self.witness_id,
            len(violations),
        )
        return violations

    def to_dict(self) -> dict:
        """Serialise the witness to a JSON-compatible dictionary.

        Returns:
            Dictionary with ``witness_id``, ``created_at``, ``event_count``,
            and ``events``.
        """
        return {
            "witness_id": self.witness_id,
            "created_at": self.created_at,
            "event_count": len(self.events),
            "events": list(self.events),
        }


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_default_policy_engine() -> PolicyEngine:
    """Create a :class:`PolicyEngine` pre-loaded with the five default policies.

    This is a convenience factory that constructs a :class:`PolicyCoordinator`,
    loads default policies, and returns the inner :class:`PolicyEngine`.

    Returns:
        A :class:`PolicyEngine` with all default policies registered.
    """
    coordinator = make_default_policy_coordinator()
    return coordinator.engine


def make_default_policy_coordinator() -> PolicyCoordinator:
    """Create a :class:`PolicyCoordinator` pre-loaded with default policies.

    Constructs a :class:`PolicyCoordinator` with a fresh :class:`PolicyEngine`
    and :class:`PolicyConflictDetector`, then calls
    :meth:`~PolicyCoordinator.load_default_policies`.

    Returns:
        A fully initialised :class:`PolicyCoordinator`.
    """
    coordinator = PolicyCoordinator(
        coordinator_id=str(uuid.uuid4()),
        engine=PolicyEngine(
            engine_id=str(uuid.uuid4()),
            conflict_detector=PolicyConflictDetector(
                detector_id=str(uuid.uuid4())
            ),
        ),
    )
    coordinator.load_default_policies()
    return coordinator


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PolicyPriority",
    "PolicyCondition",
    "PolicyAction",
    "RoutingPolicy",
    "PolicyConflict",
    "PolicyConflictDetector",
    "PolicyEngine",
    "PolicyCoordinator",
    "PolicyWitness",
    "make_default_policy_engine",
    "make_default_policy_coordinator",
]

# ---------------------------------------------------------------------------
# Module-level smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 70)
    print("routing_policies.py — smoke test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Create coordinator and load default policies
    # ------------------------------------------------------------------
    coordinator = make_default_policy_coordinator()
    witness = PolicyWitness(
        witness_id=str(uuid.uuid4()),
        created_at=time.time(),
    )
    print(f"\nCoordinator ID : {coordinator.coordinator_id}")
    print(f"Engine ID      : {coordinator.engine.engine_id}")
    print(f"Policies loaded: {len(coordinator.policy_library)}")
    print(f"  {list(coordinator.policy_library.keys())}")

    # ------------------------------------------------------------------
    # 2. Route four representative requests
    # ------------------------------------------------------------------
    requests = [
        {
            "request_id": str(uuid.uuid4()),
            "claim_kind": "legal",
            "description": "Contract interpretation dispute",
        },
        {
            "request_id": str(uuid.uuid4()),
            "claim_kind": "mathematical_proof",
            "description": "Proof of commutativity of addition",
        },
        {
            "request_id": str(uuid.uuid4()),
            "claim_kind": "runtime_assertion",
            "description": "Assertion check on sorted list invariant",
            "timed_out": True,
        },
        {
            "request_id": str(uuid.uuid4()),
            "claim_kind": "natural_language",
            "description": "Summarise the code review findings",
        },
    ]

    print("\n" + "-" * 70)
    print("Routing four requests through the policy engine:")
    print("-" * 70)

    for req in requests:
        modified, applied = coordinator.route_with_policy(req)
        rid = req["request_id"]
        print(f"\nRequest: {req.get('description')!r}")
        print(f"  request_id   : {rid}")
        print(f"  claim_kind   : {req.get('claim_kind')}")
        if applied:
            print(f"  Policies applied ({len(applied)}): {applied}")
        else:
            print("  Policies applied: (none)")
        interesting = {
            k: modified[k]
            for k in (
                "forced_channel",
                "trust_requirement",
                "priority",
                "escalate",
                "escalation_reason",
                "rejected",
                "rejection_reason",
            )
            if k in modified
        }
        if interesting:
            print(f"  Result modifications:")
            for k, v in interesting.items():
                print(f"    {k}: {v}")
        else:
            print("  Result modifications: (none)")

        witness.record_application(rid, applied, modified)

    # ------------------------------------------------------------------
    # 3. Detect conflicts
    # ------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("Conflict detection:")
    print("-" * 70)
    conflicts = coordinator.engine.detect_conflicts()
    if conflicts:
        print(f"  {len(conflicts)} conflict(s) found:")
        for c in conflicts:
            print(
                f"    [{c.conflict_type}] {c.policy_a_id} ↔ {c.policy_b_id}: "
                f"{c.description[:80]}…"
            )
            witness.record_conflict(c)
    else:
        print("  No conflicts detected.")

    # ------------------------------------------------------------------
    # 4. Compliance check
    # ------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("Compliance check summary:")
    print("-" * 70)
    compliance = coordinator.compliance_check()
    print(f"  Status           : {compliance['compliance_status'].upper()}")
    print(f"  Total policies   : {compliance['total_policies']}")
    print(f"  Enabled policies : {compliance['enabled_policies']}")
    print(f"  Disabled policies: {compliance['disabled_policies']}")
    print(f"  Conflicts found  : {compliance['conflicts_detected']}")
    print(f"  Priority breakdown:")
    for prio, count in compliance["engine_stats"]["priority_breakdown"].items():
        if count:
            print(f"    {prio}: {count}")

    # ------------------------------------------------------------------
    # 5. Witness invariant check
    # ------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("Witness invariant verification:")
    print("-" * 70)
    violations = witness.verify_invariants()
    if violations:
        print(f"  {len(violations)} invariant violation(s):")
        for v in violations:
            print(f"    ✗ {v}")
    else:
        print("  All invariants hold — no violations detected.")

    # ------------------------------------------------------------------
    # 6. Engine stats
    # ------------------------------------------------------------------
    print("\n" + "-" * 70)
    print("Engine stats:")
    print("-" * 70)
    pprint.pprint(coordinator.engine.stats(), indent=4)

    print("\n" + "=" * 70)
    print("Smoke test complete.")
    print("=" * 70)
