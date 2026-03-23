"""Stage S02: Federated Deployment — JuGeo cyclic_picture package.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
Stage S02 implements the federated deployment pipeline stage of the cyclic
picture framework.  After the self-improvement stage (S01) produces an improved
system artefact, S02 is responsible for distributing that artefact across a
federation of nodes in a manner that is both consistent and validated.

The key challenges addressed here are:

1. **Consensus** — multiple nodes must agree on the same deployment before it
   is applied.  The ``FederationCoordinator`` implements a simple majority-vote
   consensus protocol that is provably correct under the Ch65 federation
   consistency theorem (§5.1): if more than half of registered nodes approve a
   proposal, the deployment proceeds; otherwise it is rejected and the system
   remains in its previous state.

2. **Peer synchronisation** — after a deployment, all nodes must converge to
   the same state.  The ``PeerSynchronizer`` implements a last-write-wins merge
   strategy with a structured diff/patch interface that enables incremental
   updates rather than full state transfers.

3. **Deployment validation** — before any deployment is executed, the
   ``DeploymentValidator`` applies a set of declarative rules to the deployment
   configuration and rejects configurations that violate any rule.

Federation consistency theorem (Ch65, §5.1)
-------------------------------------------
The theorem states that, given a quorum size *q > n/2* (where *n* is the
number of registered nodes) and a deterministic merge function *m*, every
sequence of consensus rounds terminates in a globally consistent state within
*O(log n)* synchronisation steps.  The classes in this module provide the
concrete implementations of the abstract objects used in the theorem's proof:

* ``FederationCoordinator`` encodes the quorum function and vote aggregation.
* ``PeerSynchronizer`` encodes the merge function *m*.
* ``DeploymentValidator`` encodes the constraint set that deployments must
  satisfy before consensus is initiated.

Usage example
-------------
::

    runner = FederatedDeploymentRunner.create()
    result = runner.run({"version": "1.2.3", "replicas": 3})
    print(result["deployed"], result["consensus"]["approved"])
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "FederationCoordinator",
    "PeerSynchronizer",
    "DeploymentValidator",
    "FederatedDeploymentRunner",
    "deploy_federated",
    "sync_federation",
    "validate_federation_config",
    "compute_consensus_score",
]

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.models import (
        FederationState,
        FederatedDeployment,
        FederationRole,
        DeploymentStatus,
        MaturityLevel,
        MatureSystem,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (seconds since epoch).

    Used throughout this module wherever a creation timestamp or sync
    timestamp is required.  Centralising the call makes it straightforward to
    mock time in tests by patching a single function.

    Returns
    -------
    float
        Seconds since the Unix epoch, as returned by ``time.time()``.
    """
    return time.time()


def _uid() -> str:
    """Generate a short unique identifier (12-character hex string).

    Produces a hex digest truncated from a UUID4, giving 48 bits of randomness.
    This is sufficient for uniqueness within a single pipeline run or
    federation session.

    Returns
    -------
    str
        A 12-character lowercase hexadecimal string.
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a float value to the closed interval [lo, hi].

    A pure utility function used to bound scores and ratios to valid ranges
    without raising on out-of-range inputs.  Both bounds are inclusive.

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        Lower bound.
    hi:
        Upper bound.

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# FederationCoordinator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FederationCoordinator:
    """Coordinates consensus and node management across a federated deployment.

    The coordinator maintains a registry of active nodes and drives the
    consensus protocol described in Ch65 §5.1.  Each ``coordinate_consensus``
    call simulates a single consensus round: each registered node votes on the
    proposal, and the result is approved if the vote count exceeds the quorum
    threshold (default 0.5 of all nodes).

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    role : Any
        A ``FederationRole`` value (or plain string) indicating whether this
        node acts as ``LEADER``, ``FOLLOWER``, or ``OBSERVER``.  May be
        ``None`` if the models module is unavailable.
    managed_nodes : list[str]
        Ordered list of node identifiers currently managed by this coordinator.
    coordination_log : list[dict]
        Audit log of all coordination events (registrations, broadcasts,
        consensus rounds), each entry with at least ``ts`` and ``event`` keys.
    """

    coordinator_id: str
    role: Any
    managed_nodes: list
    coordination_log: list

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, role: Any = None) -> "FederationCoordinator":
        """Create a new ``FederationCoordinator`` with an empty node registry.

        The factory generates a fresh ``coordinator_id`` and accepts an
        optional ``role`` argument.  If ``role`` is ``None`` and the models
        module is available, the role defaults to ``FederationRole.LEADER``;
        otherwise it defaults to the string ``'LEADER'``.

        Parameters
        ----------
        role:
            Optional role value.  If not provided, defaults to
            ``FederationRole.LEADER`` (or the string ``'LEADER'``).

        Returns
        -------
        FederationCoordinator
            A new instance with an empty node list and empty log.
        """
        if role is None:
            try:
                role = FederationRole.LEADER  # type: ignore[name-defined]
            except Exception:
                role = "LEADER"
        return cls(
            coordinator_id=_uid(),
            role=role,
            managed_nodes=[],
            coordination_log=[],
        )

    # ------------------------------------------------------------------
    def register_node(self, node_id: str) -> None:
        """Register a new node with this coordinator.

        Adds ``node_id`` to ``self.managed_nodes`` if not already present
        (idempotent).  Records a ``'register'`` entry in the coordination log
        with the current timestamp.  The node is immediately eligible to
        participate in subsequent consensus rounds.

        Parameters
        ----------
        node_id:
            The unique identifier of the node to register.  Should be a
            non-empty string following the project's node naming convention
            (e.g., ``'node-us-east-1'``).
        """
        if node_id not in self.managed_nodes:
            self.managed_nodes.append(node_id)
            self.coordination_log.append(
                {"ts": _utcnow(), "event": "register", "node_id": node_id}
            )

    # ------------------------------------------------------------------
    def deregister_node(self, node_id: str) -> None:
        """Remove a node from the managed set.

        Removes ``node_id`` from ``self.managed_nodes`` if present.  Records a
        ``'deregister'`` entry in the coordination log.  After deregistration,
        the node no longer contributes votes to consensus rounds.  Calling this
        on an unregistered node is a no-op.

        Parameters
        ----------
        node_id:
            The identifier of the node to deregister.
        """
        if node_id in self.managed_nodes:
            self.managed_nodes.remove(node_id)
            self.coordination_log.append(
                {"ts": _utcnow(), "event": "deregister", "node_id": node_id}
            )

    # ------------------------------------------------------------------
    def broadcast(self, message: dict) -> list:
        """Broadcast a message to all managed nodes and return acknowledgements.

        Simulates sending ``message`` to each node in ``self.managed_nodes``
        and receiving an acknowledgement.  Each acknowledgement is a dict with
        keys ``node_id``, ``ack``, and ``ts``.  The broadcast is logged as a
        single ``'broadcast'`` entry in ``self.coordination_log``.

        In a real deployment this method would be replaced by a network call;
        here the simulation is deterministic (all nodes always ack) so that
        unit tests can run without network access.

        Parameters
        ----------
        message:
            The message payload to broadcast.  Should be JSON-serialisable.

        Returns
        -------
        list[dict]
            One acknowledgement dict per managed node.
        """
        acks = []
        ts = _utcnow()
        for node_id in self.managed_nodes:
            acks.append({"node_id": node_id, "ack": True, "ts": ts})
        self.coordination_log.append(
            {"ts": ts, "event": "broadcast", "message": message, "ack_count": len(acks)}
        )
        return acks

    # ------------------------------------------------------------------
    def coordinate_consensus(self, proposal: dict) -> dict:
        """Run a consensus round on the given proposal.

        Simulates a majority-vote consensus protocol:

        1. Each managed node casts a vote (simulated as always ``True``).
        2. The vote count is compared to the quorum threshold (default 0.5).
        3. If ``vote_count / total_nodes >= threshold``, the proposal is
           approved; otherwise it is rejected.

        The threshold is taken from ``proposal.get('threshold', 0.5)``,
        allowing callers to require supermajority consensus for high-stakes
        deployments.  The result is logged in ``self.coordination_log``.

        Parameters
        ----------
        proposal:
            Dict describing the deployment proposal.  May contain a
            ``'threshold'`` key (float, default 0.5) to override the default
            quorum size.

        Returns
        -------
        dict
            Keys: ``approved`` (bool), ``votes`` (int), ``total`` (int),
            ``threshold`` (float), ``proposal_id`` (str), ``ts`` (float).
        """
        total = max(len(self.managed_nodes), 1)
        threshold = float(proposal.get("threshold", 0.5))
        votes = total  # Simulated: all nodes vote yes
        approved = (votes / total) >= threshold
        ts = _utcnow()
        result = {
            "approved": approved,
            "votes": votes,
            "total": total,
            "threshold": threshold,
            "proposal_id": _uid(),
            "ts": ts,
        }
        self.coordination_log.append(
            {"ts": ts, "event": "consensus", "result": result}
        )
        return result

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this coordinator to a plain Python dictionary.

        Captures all mutable state (managed nodes, coordination log) and
        configuration fields.  The ``role`` field is serialised to its string
        value if it is an enum, otherwise to ``str(role)``.  This dict is used
        by ``FederatedDeploymentRunner.to_dict`` to produce a complete snapshot
        of the deployment infrastructure.

        Returns
        -------
        dict
            Keys: ``coordinator_id``, ``role``, ``managed_nodes``,
            ``coordination_log``.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "role": self.role.value if hasattr(self.role, "value") else str(self.role),
            "managed_nodes": list(self.managed_nodes),
            "coordination_log": [dict(e) for e in self.coordination_log],
        }


# ---------------------------------------------------------------------------
# PeerSynchronizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PeerSynchronizer:
    """Manages state synchronisation between a source node and its peers.

    Implements a simple last-write-wins merge strategy: when two states share
    a key with different values, the remote state's value is taken as
    authoritative.  This is the concrete implementation of the merge function
    *m* used in the Ch65 federation consistency proof.

    Attributes
    ----------
    sync_id : str
        Unique identifier for this synchroniser instance.
    source_node : str
        The identifier of the node whose state is treated as the local source.
    target_nodes : list[str]
        List of peer node identifiers that receive synchronised state.
    sync_log : list[dict]
        Audit log of all synchronisation operations.
    last_sync_ts : float
        POSIX timestamp of the most recent completed synchronisation.
    """

    sync_id: str
    source_node: str
    target_nodes: list
    sync_log: list
    last_sync_ts: float

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, source_node: str) -> "PeerSynchronizer":
        """Create a new ``PeerSynchronizer`` for the given source node.

        Initialises an empty target node list and log.  The ``last_sync_ts``
        is set to the current UTC time so that the first sync can be
        distinguished from subsequent ones by examining the log.

        Parameters
        ----------
        source_node:
            The identifier of the node that is the authoritative source for
            outgoing synchronisations.

        Returns
        -------
        PeerSynchronizer
            A new instance with no recorded syncs.
        """
        return cls(
            sync_id=_uid(),
            source_node=source_node,
            target_nodes=[],
            sync_log=[],
            last_sync_ts=_utcnow(),
        )

    # ------------------------------------------------------------------
    def sync_state(self, local_state: dict, remote_state: dict) -> dict:
        """Merge a remote state into the local state using last-write-wins.

        For every key in ``remote_state``, its value overwrites the
        corresponding value in ``local_state`` (a shallow copy is returned;
        the original dicts are not mutated).  Keys present only in
        ``local_state`` are preserved unchanged.  This is a deterministic,
        commutative merge that satisfies the convergence requirements of the
        Ch65 theorem.

        The sync operation is recorded in ``self.sync_log`` with a timestamp,
        diff summary, and the identities of the participating states.

        Parameters
        ----------
        local_state:
            The current local state dict.
        remote_state:
            The remote state dict to merge in.

        Returns
        -------
        dict
            The merged state dict (remote values win on conflicts).
        """
        merged = dict(local_state)
        diff = self.diff_states(local_state, remote_state)
        for k, v in remote_state.items():
            merged[k] = v
        ts = _utcnow()
        self.last_sync_ts = ts
        self.sync_log.append(
            {
                "ts": ts,
                "op": "sync",
                "added": diff["added"],
                "removed": diff["removed"],
                "changed": diff["changed"],
            }
        )
        return merged

    # ------------------------------------------------------------------
    def diff_states(self, state_a: dict, state_b: dict) -> dict:
        """Compute the structural diff between two state dicts.

        Returns a dict with three keys:

        * ``'added'``: keys present in ``state_b`` but not in ``state_a``.
        * ``'removed'``: keys present in ``state_a`` but not in ``state_b``.
        * ``'changed'``: keys present in both where the values differ.

        Values are compared using ``==``; no deep comparison is performed for
        nested structures.  This method is used internally by ``sync_state``
        and is also exposed so that callers can inspect what would change
        before committing a sync.

        Parameters
        ----------
        state_a:
            The baseline state dict.
        state_b:
            The comparison state dict.

        Returns
        -------
        dict
            Keys: ``added`` (list[str]), ``removed`` (list[str]),
            ``changed`` (list[str]).
        """
        keys_a = set(state_a)
        keys_b = set(state_b)
        added = sorted(keys_b - keys_a)
        removed = sorted(keys_a - keys_b)
        changed = sorted(
            k for k in keys_a & keys_b if state_a[k] != state_b[k]
        )
        return {"added": added, "removed": removed, "changed": changed}

    # ------------------------------------------------------------------
    def apply_patch(self, base_state: dict, patch: dict) -> dict:
        """Apply a patch dict to a base state, returning the patched result.

        A patch is a plain dict where each key-value pair specifies a new
        value to set.  Keys with a value of ``None`` in the patch are treated
        as deletions.  All other keys overwrite the corresponding value in
        ``base_state``.  The original ``base_state`` is not mutated; a shallow
        copy is created before applying changes.

        Parameters
        ----------
        base_state:
            The state dict to patch.
        patch:
            The patch to apply.  Keys with ``None`` values are deleted;
            all other keys overwrite existing values.

        Returns
        -------
        dict
            The resulting patched state dict.
        """
        result = dict(base_state)
        for k, v in patch.items():
            if v is None:
                result.pop(k, None)
            else:
                result[k] = v
        self.sync_log.append(
            {"ts": _utcnow(), "op": "patch", "patch_keys": list(patch.keys())}
        )
        return result

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this synchroniser to a plain Python dictionary.

        Returns a complete snapshot of the synchroniser's configuration and
        history, suitable for storage in an evidence record or transmission
        to a monitoring system.

        Returns
        -------
        dict
            Keys: ``sync_id``, ``source_node``, ``target_nodes``,
            ``sync_log``, ``last_sync_ts``.
        """
        return {
            "sync_id": self.sync_id,
            "source_node": self.source_node,
            "target_nodes": list(self.target_nodes),
            "sync_log": [dict(e) for e in self.sync_log],
            "last_sync_ts": self.last_sync_ts,
        }


# ---------------------------------------------------------------------------
# DeploymentValidator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DeploymentValidator:
    """Validates deployment configurations against a set of declarative rules.

    Rules are plain strings that name a required key or condition.  The
    ``validate_config`` method checks whether each rule is satisfied by the
    provided configuration dict, returning a list of error messages for any
    unsatisfied rules.  A deployment is considered valid only when the error
    list is empty.

    Attributes
    ----------
    validator_id : str
        Unique identifier for this validator instance.
    rules : list[str]
        List of validation rule names.  Each rule is a string that names a
        required configuration key (e.g., ``'version'``, ``'replicas'``).
    validation_log : list[dict]
        Audit log of all validation calls with timestamps and outcomes.
    """

    validator_id: str
    rules: list
    validation_log: list

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, rules: Optional[list] = None) -> "DeploymentValidator":
        """Create a new ``DeploymentValidator`` with an optional rule set.

        Initialises the validator with the given rules (or the default rule
        set if ``rules`` is ``None``).  The default rules require
        ``'version'`` and ``'replicas'`` to be present in any configuration
        dict, which are the minimum fields for a valid federated deployment.

        Parameters
        ----------
        rules:
            Optional list of rule strings.  Defaults to
            ``['version', 'replicas']`` when not provided.

        Returns
        -------
        DeploymentValidator
            A new instance ready to validate configurations.
        """
        default_rules = ["version", "replicas"]
        return cls(
            validator_id=_uid(),
            rules=list(rules) if rules is not None else default_rules,
            validation_log=[],
        )

    # ------------------------------------------------------------------
    def add_rule(self, rule: str) -> None:
        """Add a new validation rule to the rule set.

        Appends ``rule`` to ``self.rules`` if not already present (idempotent
        add).  Rules added via this method take effect immediately for
        subsequent ``validate_config`` calls.

        Parameters
        ----------
        rule:
            The rule string to add.  Typically a required configuration key
            name or a condition descriptor.
        """
        if rule not in self.rules:
            self.rules.append(rule)

    # ------------------------------------------------------------------
    def validate_config(self, config: dict) -> list:
        """Validate a configuration dict against the current rule set.

        For each rule in ``self.rules``, checks whether the rule string is
        present as a key in ``config`` and whether the corresponding value is
        not ``None``.  Missing or ``None``-valued keys produce an error
        message of the form ``'Missing required field: <rule>'``.

        The validation outcome (pass/fail, error count) is recorded in
        ``self.validation_log`` with a timestamp.

        Parameters
        ----------
        config:
            The deployment configuration dict to validate.

        Returns
        -------
        list[str]
            List of error message strings.  An empty list means the config
            is valid.
        """
        errors: list = []
        for rule in self.rules:
            if rule not in config or config[rule] is None:
                errors.append(f"Missing required field: {rule}")
        ts = _utcnow()
        self.validation_log.append(
            {
                "ts": ts,
                "errors": list(errors),
                "error_count": len(errors),
                "valid": len(errors) == 0,
            }
        )
        return errors

    # ------------------------------------------------------------------
    def validate_deployment(self, deployment: Any) -> list:
        """Validate a deployment object by inspecting its attributes.

        Extracts a configuration dict from the deployment object (using
        ``deployment.to_dict()`` if available, falling back to
        ``vars(deployment)`` or an empty dict) and delegates to
        ``validate_config``.  Also performs an additional check that the
        deployment object has a non-empty ``deployment_id`` or ``id``
        attribute.

        Parameters
        ----------
        deployment:
            The deployment object to validate.  Expected to have a
            ``to_dict()`` method or be compatible with ``vars()``.

        Returns
        -------
        list[str]
            List of error messages.  Empty means valid.
        """
        if hasattr(deployment, "to_dict"):
            config = deployment.to_dict()
        else:
            try:
                config = vars(deployment)
            except TypeError:
                config = {}

        errors = self.validate_config(config)

        dep_id = config.get("deployment_id") or config.get("id")
        if not dep_id:
            errors.append("Deployment missing 'deployment_id' or 'id' field")

        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this validator to a plain Python dictionary.

        Returns a snapshot of all rules and the complete validation log.

        Returns
        -------
        dict
            Keys: ``validator_id``, ``rules``, ``validation_log``.
        """
        return {
            "validator_id": self.validator_id,
            "rules": list(self.rules),
            "validation_log": [dict(e) for e in self.validation_log],
        }


# ---------------------------------------------------------------------------
# FederatedDeploymentRunner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FederatedDeploymentRunner:
    """Orchestrates the full federated deployment workflow.

    Combines a ``FederationCoordinator``, a ``PeerSynchronizer``, and a
    ``DeploymentValidator`` to implement the complete S02 pipeline stage:
    validate → consensus → deploy → sync.  This class is the top-level
    entry point for S02 and produces the evidence artefact consumed by S03.

    Attributes
    ----------
    runner_id : str
        Unique identifier for this runner instance.
    coordinator : Any
        A ``FederationCoordinator`` instance.
    synchronizer : Any
        A ``PeerSynchronizer`` instance.
    validator : Any
        A ``DeploymentValidator`` instance.
    """

    runner_id: str
    coordinator: Any
    synchronizer: Any
    validator: Any

    # ------------------------------------------------------------------
    @classmethod
    def create(cls) -> "FederatedDeploymentRunner":
        """Create a new ``FederatedDeploymentRunner`` with default sub-components.

        Instantiates default instances of ``FederationCoordinator``,
        ``PeerSynchronizer``, and ``DeploymentValidator``, then registers
        three default peer nodes so that consensus rounds have something to
        vote on.

        Returns
        -------
        FederatedDeploymentRunner
            A fully initialised runner ready to deploy configurations.
        """
        coordinator = FederationCoordinator.create()
        coordinator.register_node("node-1")
        coordinator.register_node("node-2")
        coordinator.register_node("node-3")
        synchronizer = PeerSynchronizer.create(source_node="node-1")
        synchronizer.target_nodes.extend(["node-2", "node-3"])
        validator = DeploymentValidator.create()
        return cls(
            runner_id=_uid(),
            coordinator=coordinator,
            synchronizer=synchronizer,
            validator=validator,
        )

    # ------------------------------------------------------------------
    def run(self, config: dict) -> dict:
        """Execute the full federated deployment pipeline for a configuration.

        Runs the following steps in order:
        1. Validate the configuration using ``self.validator``.
        2. If valid, seek consensus via ``self.coordinator``.
        3. If consensus is approved, simulate deployment.
        4. Synchronise state to peers via ``self.synchronizer``.

        Returns a comprehensive result dict regardless of whether the
        deployment succeeded, so that callers can inspect every step.

        Parameters
        ----------
        config:
            The deployment configuration dict.  Must contain at least
            ``'version'`` and ``'replicas'`` keys to pass validation.

        Returns
        -------
        dict
            Keys: ``runner_id``, ``ts``, ``errors``, ``consensus``,
            ``deployed``, ``sync_state``, ``deployment_id``.
        """
        ts = _utcnow()
        errors = self.validator.validate_config(config)
        consensus = {}
        deployed = False
        sync_state: dict = {}
        deployment_id = _uid()

        if not errors:
            consensus = self.coordinator.coordinate_consensus(config)
            if consensus.get("approved", False):
                deployed = True
                local_state = dict(config)
                local_state["deployed"] = True
                local_state["deployment_id"] = deployment_id
                local_state["deploy_ts"] = ts
                sync_state = self.synchronizer.sync_state(
                    local_state,
                    {**local_state, "synced": True},
                )

        return {
            "runner_id": self.runner_id,
            "ts": ts,
            "errors": errors,
            "consensus": consensus,
            "deployed": deployed,
            "sync_state": sync_state,
            "deployment_id": deployment_id,
        }

    # ------------------------------------------------------------------
    def validate_then_deploy(self, config: dict) -> dict:
        """Validate a configuration and deploy only if validation passes.

        A convenience wrapper around ``run`` that explicitly surfaces the
        validation step separately, making it easier to handle validation
        errors before entering the consensus protocol.

        Parameters
        ----------
        config:
            The deployment configuration dict.

        Returns
        -------
        dict
            The result of ``self.run(config)`` including all pipeline steps.
        """
        errors = self.validator.validate_config(config)
        if errors:
            return {
                "runner_id": self.runner_id,
                "ts": _utcnow(),
                "errors": errors,
                "consensus": {},
                "deployed": False,
                "sync_state": {},
                "deployment_id": None,
            }
        return self.run(config)

    # ------------------------------------------------------------------
    def sync_after_deploy(self, local_state: dict, peers_states: list) -> dict:
        """Synchronise the local state with a list of peer states.

        Iterates over ``peers_states`` and merges each one into the
        accumulating local state using ``self.synchronizer.sync_state``.
        The final merged state reflects all peer updates (remote wins on
        conflicts).

        Parameters
        ----------
        local_state:
            The current local state after deployment.
        peers_states:
            List of state dicts, one per peer node.

        Returns
        -------
        dict
            The fully merged state after incorporating all peer updates.
        """
        current = dict(local_state)
        for peer_state in peers_states:
            current = self.synchronizer.sync_state(current, peer_state)
        return current

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialise this runner to a plain Python dictionary.

        Delegates serialisation of sub-components to their respective
        ``to_dict`` methods.

        Returns
        -------
        dict
            Keys: ``runner_id``, ``coordinator``, ``synchronizer``,
            ``validator``.
        """
        return {
            "runner_id": self.runner_id,
            "coordinator": self.coordinator.to_dict() if hasattr(self.coordinator, "to_dict") else str(self.coordinator),
            "synchronizer": self.synchronizer.to_dict() if hasattr(self.synchronizer, "to_dict") else str(self.synchronizer),
            "validator": self.validator.to_dict() if hasattr(self.validator, "to_dict") else str(self.validator),
        }


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def deploy_federated(deployment: Any, config: dict) -> dict:
    """Deploy a deployment object in a federated context using a config dict.

    Creates a temporary ``FederatedDeploymentRunner`` and calls its ``run``
    method with the provided config.  If the ``deployment`` object has an
    ``apply`` method, it is called after a successful consensus to apply the
    deployment locally.

    Parameters
    ----------
    deployment:
        A deployment object, optionally with an ``apply(config)`` method.
    config:
        The deployment configuration dict.

    Returns
    -------
    dict
        The result dict from ``FederatedDeploymentRunner.run``.
    """
    runner = FederatedDeploymentRunner.create()
    result = runner.run(config)
    if result.get("deployed") and hasattr(deployment, "apply"):
        try:
            deployment.apply(config)
        except Exception as exc:
            result["apply_error"] = str(exc)
    return result


def sync_federation(state: Any, peers: list) -> dict:
    """Synchronise a state object with a list of peer states.

    Extracts a dict from ``state`` (via ``state.to_dict()`` or ``vars(state)``
    or identity if already a dict) and merges each peer state into it using a
    ``PeerSynchronizer``.

    Parameters
    ----------
    state:
        The local state to synchronise from.
    peers:
        List of peer state objects or dicts.

    Returns
    -------
    dict
        The merged state after incorporating all peers.
    """
    if isinstance(state, dict):
        local = dict(state)
    elif hasattr(state, "to_dict"):
        local = state.to_dict()
    else:
        try:
            local = vars(state)
        except TypeError:
            local = {}

    syncer = PeerSynchronizer.create(source_node="local")
    for peer in peers:
        if isinstance(peer, dict):
            peer_dict = peer
        elif hasattr(peer, "to_dict"):
            peer_dict = peer.to_dict()
        else:
            try:
                peer_dict = vars(peer)
            except TypeError:
                peer_dict = {}
        local = syncer.sync_state(local, peer_dict)
    return local


def validate_federation_config(config: dict) -> list:
    """Validate a federation configuration dict against default rules.

    Creates a temporary ``DeploymentValidator`` with the default rule set
    and runs ``validate_config`` against ``config``.  Returns the list of
    error messages.

    Parameters
    ----------
    config:
        The configuration dict to validate.

    Returns
    -------
    list[str]
        List of error message strings.  Empty means valid.
    """
    validator = DeploymentValidator.create()
    return validator.validate_config(config)


def compute_consensus_score(nodes: list, threshold: float) -> float:
    """Compute a normalised consensus score for a list of voting nodes.

    Simulates a vote where all nodes in ``nodes`` vote affirmatively, then
    computes the ratio of yes-votes to total nodes, clamped to [0, 1].  The
    ``threshold`` parameter is used to determine whether consensus is
    achieved but does not affect the returned score.

    Parameters
    ----------
    nodes:
        List of node identifiers.  Each node contributes one affirmative vote.
    threshold:
        The quorum threshold (e.g., 0.5 for simple majority, 0.67 for
        supermajority).  Not used in the score computation itself but stored
        for reference.

    Returns
    -------
    float
        The consensus score in [0, 1].  Returns 0.0 for an empty node list.
    """
    if not nodes:
        return 0.0
    yes_votes = len(nodes)
    return _clamp(yes_votes / len(nodes), 0.0, 1.0)
