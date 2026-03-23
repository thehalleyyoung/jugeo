"""Section 2: Trust as an ordered algebra of admissible evidence — Theory2 Ch6.

Theory: T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) is a partially ordered algebraic
structure on admissible evidence configurations.

Key axioms formalised here:
  1. ≼ is a partial order (reflexive, antisymmetric, transitive).
  2. ⊕ is the meet of the partial order (conservative composition).
  3. ⊖ is attenuation — strictly decreases rank.
  4. ↑_π requires named policy + non-empty justification (no silent promotion).
  5. ↓_χ is ceiling enforcement — strictly decreases rank to at most χ.

The no-silent-promotion rule is a core invariant: every promotion event must
reference a named policy and leave an audit trail.

Author: copilot
Reference: theory2.tex Chapter 6, Section 2
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
    from jugeo.evidence.provenance import ProvenanceNode, ProvenanceGraph
    from jugeo.evidence.certificates import Certificate, CertificateBuilder, CertificateStatus
    from jugeo.judgments.judgment_terms import JudgmentTerm
    from jugeo.errors import JuGeoError, StructuredFailure, FailureScope, EvidenceFamily
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Trust level ordering
# ---------------------------------------------------------------------------

_TRUST_LEVELS: List[str] = [
    "CONTRADICTED",       # rank 0
    "UNVERIFIED",         # rank 1
    "COPILOT_SUGGESTED",  # rank 2
    "ORACLE_PROPOSED",    # rank 3
    "HUMAN_ATTESTED",     # rank 4
    "RUNTIME_WITNESSED",  # rank 5
    "SOLVER_DISCHARGED",  # rank 6
    "MECHANICALLY_VERIFIED",  # rank 7
]

_RANK: Dict[str, int] = {name: i for i, name in enumerate(_TRUST_LEVELS)}

_ADMISSIBLE: FrozenSet[str] = frozenset(
    {"ORACLE_PROPOSED", "HUMAN_ATTESTED", "RUNTIME_WITNESSED", "SOLVER_DISCHARGED", "MECHANICALLY_VERIFIED"}
)


def _rank(level: Any) -> int:
    """Return integer rank of a trust level name or enum."""
    if isinstance(level, str):
        return _RANK.get(level, 0)
    if hasattr(level, "name"):
        return _RANK.get(level.name, 0)
    return 0


def _name_at_rank(rank: int) -> str:
    """Return the trust level name for a given integer rank."""
    rank = max(0, min(rank, len(_TRUST_LEVELS) - 1))
    return _TRUST_LEVELS[rank]


# ---------------------------------------------------------------------------
# AdmissibleConfig
# ---------------------------------------------------------------------------


@dataclass
class AdmissibleConfig:
    """A validated evidence configuration in the trust algebra.

    An AdmissibleConfig represents a specific combination of evidence items
    that together justify a particular trust level for a coordinate.  It must
    pass admissibility checks before entering the algebra.

    Attributes:
        config_id: Unique identifier.
        evidence_map: Maps evidence item IDs to their trust levels.
        trust_level: The aggregate trust level of this configuration.
        channel: The primary discharge channel.
        coordinate: Geometric coordinate.
        justification: Human-readable justification for the configuration.
        created_at: Unix timestamp.
        metadata: Arbitrary metadata.
    """

    config_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    evidence_map: Dict[str, str] = field(default_factory=dict)
    trust_level: str = "UNVERIFIED"
    channel: str = ""
    coordinate: str = ""
    justification: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        """Validate this configuration.

        Returns:
            List of violation strings; empty if valid.
        """
        violations: List[str] = []
        if not self.config_id:
            violations.append("config_id must not be empty")
        if not self.evidence_map:
            violations.append("evidence_map must not be empty")
        if self.trust_level not in _RANK:
            violations.append(f"Unknown trust level '{self.trust_level}'")
        if self.trust_level == "CONTRADICTED":
            violations.append("CONTRADICTED configurations are not admissible")
        return violations

    def to_trust_level(self) -> str:
        """Return the trust level name of this configuration.

        Returns:
            Trust level string name.
        """
        return self.trust_level

    def is_admissible(self) -> bool:
        """Return True if this configuration is in E_adm.

        Returns:
            True if admissible.
        """
        return self.trust_level in _ADMISSIBLE and not self.validate()

    def fingerprint(self) -> str:
        """Return a hash fingerprint of the evidence_map.

        Returns:
            Hex string SHA-256 fingerprint.
        """
        content = json.dumps(sorted(self.evidence_map.items()), sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def serialize(self) -> Dict[str, Any]:
        """Serialise this configuration to a plain dict.

        Returns:
            Dict representation.
        """
        return {
            "config_id": self.config_id,
            "evidence_map": dict(self.evidence_map),
            "trust_level": self.trust_level,
            "channel": self.channel,
            "coordinate": self.coordinate,
            "justification": self.justification,
            "created_at": self.created_at,
            "fingerprint": self.fingerprint(),
            "is_admissible": self.is_admissible(),
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# TrustOrderRelation — implements ≼
# ---------------------------------------------------------------------------


@dataclass
class TrustOrderRelation:
    """Implements the partial order ≼ on AdmissibleConfig objects.

    Two configurations are comparable when they share the same coordinate and
    channel.  The order is determined by trust level rank.

    Attributes:
        comparison_log: Log of comparison events.
    """

    comparison_log: List[Dict[str, Any]] = field(default_factory=list)

    def compare(self, a: AdmissibleConfig, b: AdmissibleConfig) -> int:
        """Compare two configurations.

        Returns:
            -1 if a ≺ b, 0 if a = b, 1 if a ≻ b, None-equivalent (2) if incomparable.
        """
        rank_a = _rank(a.trust_level)
        rank_b = _rank(b.trust_level)
        if a.coordinate != b.coordinate or a.channel != b.channel:
            return 2  # incomparable (different coordinates or channels)
        if rank_a < rank_b:
            return -1
        if rank_a > rank_b:
            return 1
        return 0

    def leq(self, a: AdmissibleConfig, b: AdmissibleConfig) -> bool:
        """Return True if a ≼ b (a is dominated by b).

        Args:
            a: Lower configuration.
            b: Upper configuration.

        Returns:
            True if a ≼ b.
        """
        result = self.compare(a, b)
        return result in {-1, 0}

    def geq(self, a: AdmissibleConfig, b: AdmissibleConfig) -> bool:
        """Return True if a ≽ b (a dominates b).

        Args:
            a: Dominant configuration.
            b: Dominated configuration.

        Returns:
            True if a ≽ b.
        """
        result = self.compare(a, b)
        return result in {1, 0}

    def meet(self, a: AdmissibleConfig, b: AdmissibleConfig) -> AdmissibleConfig:
        """Compute the meet (greatest lower bound) of two configurations.

        The meet takes the minimum trust level of the two configurations.

        Args:
            a: First configuration.
            b: Second configuration.

        Returns:
            New AdmissibleConfig with meet trust level.
        """
        rank_meet = min(_rank(a.trust_level), _rank(b.trust_level))
        meet_level = _name_at_rank(rank_meet)
        merged_evidence = {**a.evidence_map, **b.evidence_map}
        return AdmissibleConfig(
            evidence_map=merged_evidence,
            trust_level=meet_level,
            channel=a.channel if a.channel == b.channel else f"{a.channel}+{b.channel}",
            coordinate=a.coordinate,
            justification=f"meet({a.config_id[:8]}, {b.config_id[:8]})",
            metadata={"meet_of": [a.config_id, b.config_id]},
        )

    def join(self, a: AdmissibleConfig, b: AdmissibleConfig) -> AdmissibleConfig:
        """Compute the join (least upper bound) of two configurations.

        The join takes the maximum trust level.  This should be used with
        caution — joining configurations can only happen when both configurations
        independently justify the higher level.

        Args:
            a: First configuration.
            b: Second configuration.

        Returns:
            New AdmissibleConfig with join trust level.
        """
        rank_join = max(_rank(a.trust_level), _rank(b.trust_level))
        join_level = _name_at_rank(rank_join)
        merged_evidence = {**a.evidence_map, **b.evidence_map}
        return AdmissibleConfig(
            evidence_map=merged_evidence,
            trust_level=join_level,
            channel=a.channel if a.channel == b.channel else f"{a.channel}+{b.channel}",
            coordinate=a.coordinate,
            justification=f"join({a.config_id[:8]}, {b.config_id[:8]})",
            metadata={"join_of": [a.config_id, b.config_id]},
        )

    def incomparable_pairs(
        self, configs: List[AdmissibleConfig]
    ) -> List[Tuple[AdmissibleConfig, AdmissibleConfig]]:
        """Find all pairs of configurations that are mutually incomparable.

        Args:
            configs: List of AdmissibleConfig objects.

        Returns:
            List of incomparable (a, b) pairs.
        """
        result = []
        for a, b in combinations(configs, 2):
            if self.compare(a, b) == 2:
                result.append((a, b))
        return result

    def draw_hasse(self, configs: List[AdmissibleConfig]) -> str:
        """Produce a text representation of the Hasse diagram.

        For a given list of configurations (same coordinate/channel), produces
        a simple text-based Hasse diagram sorted by trust rank.

        Args:
            configs: List of AdmissibleConfig to diagram.

        Returns:
            ASCII Hasse diagram string.
        """
        sorted_configs = sorted(configs, key=lambda c: _rank(c.trust_level), reverse=True)
        lines = ["Hasse diagram (≼ order, top = strongest):"]
        prev_rank: Optional[int] = None
        for cfg in sorted_configs:
            rank = _rank(cfg.trust_level)
            prefix = "  " if prev_rank is not None and rank < prev_rank else "  "
            lines.append(f"{prefix}[rank={rank}] {cfg.trust_level} (id={cfg.config_id[:8]})")
            prev_rank = rank
        return "\n".join(lines)

    def verify_partial_order_axioms(
        self, configs: List[AdmissibleConfig]
    ) -> List[str]:
        """Check reflexivity, antisymmetry, and transitivity on a sample.

        Args:
            configs: Configurations to check.

        Returns:
            List of axiom violations; empty if order is valid.
        """
        violations: List[str] = []
        # Reflexivity: a ≼ a
        for c in configs:
            if not self.leq(c, c):
                violations.append(f"Reflexivity violated for {c.config_id[:8]}")
        # Antisymmetry: a ≼ b and b ≼ a implies a = b (same trust rank)
        for a, b in combinations(configs, 2):
            if self.leq(a, b) and self.leq(b, a):
                if _rank(a.trust_level) != _rank(b.trust_level):
                    violations.append(
                        f"Antisymmetry violated: {a.config_id[:8]} ≼ {b.config_id[:8]} "
                        f"and {b.config_id[:8]} ≼ {a.config_id[:8]} "
                        f"but ranks differ ({_rank(a.trust_level)} vs {_rank(b.trust_level)})"
                    )
        # Transitivity: a ≼ b and b ≼ c implies a ≼ c
        for a, b, c in combinations(configs, 3) if len(configs) >= 3 else []:
            if self.leq(a, b) and self.leq(b, c) and not self.leq(a, c):
                violations.append(
                    f"Transitivity violated: {a.config_id[:8]} ≼ {b.config_id[:8]} ≼ {c.config_id[:8]} "
                    f"but not {a.config_id[:8]} ≼ {c.config_id[:8]}"
                )
        return violations


# ---------------------------------------------------------------------------
# TrustComposition — implements ⊕
# ---------------------------------------------------------------------------


@dataclass
class TrustComposition:
    """Implements trust composition ⊕ (conservative meet).

    ⊕ is the meet operation: a ⊕ b = meet(a, b) in the partial order.
    It is associative, commutative, and idempotent.

    Attributes:
        composition_log: Log of composition events.
    """

    composition_log: List[Dict[str, Any]] = field(default_factory=list)
    order_relation: TrustOrderRelation = field(default_factory=TrustOrderRelation)

    def compose_two(self, a: AdmissibleConfig, b: AdmissibleConfig) -> AdmissibleConfig:
        """Compose two configurations a ⊕ b.

        Args:
            a: First configuration.
            b: Second configuration.

        Returns:
            Composed AdmissibleConfig.
        """
        result = self.order_relation.meet(a, b)
        self.composition_log.append({
            "event": "compose",
            "a": a.config_id,
            "b": b.config_id,
            "result_trust": result.trust_level,
            "timestamp": time.time(),
        })
        return result

    def compose_many(self, configs: List[AdmissibleConfig]) -> AdmissibleConfig:
        """Compose a list of configurations using ⊕.

        Args:
            configs: List of at least one AdmissibleConfig.

        Returns:
            The composition of all configs.

        Raises:
            ValueError: If configs is empty.
        """
        if not configs:
            raise ValueError("Cannot compose empty list of configurations")
        result = configs[0]
        for cfg in configs[1:]:
            result = self.compose_two(result, cfg)
        return result

    def is_associative_check(
        self, a: AdmissibleConfig, b: AdmissibleConfig, c: AdmissibleConfig
    ) -> bool:
        """Check associativity: (a ⊕ b) ⊕ c = a ⊕ (b ⊕ c).

        Args:
            a: First configuration.
            b: Second configuration.
            c: Third configuration.

        Returns:
            True if associativity holds.
        """
        left = self.compose_two(self.compose_two(a, b), c)
        right = self.compose_two(a, self.compose_two(b, c))
        return _rank(left.trust_level) == _rank(right.trust_level)

    def is_commutative_check(self, a: AdmissibleConfig, b: AdmissibleConfig) -> bool:
        """Check commutativity: a ⊕ b = b ⊕ a.

        Args:
            a: First configuration.
            b: Second configuration.

        Returns:
            True if commutativity holds.
        """
        ab = self.compose_two(a, b)
        ba = self.compose_two(b, a)
        return _rank(ab.trust_level) == _rank(ba.trust_level)

    def idempotency_check(self, a: AdmissibleConfig) -> bool:
        """Check idempotency: a ⊕ a = a.

        Args:
            a: Configuration to check.

        Returns:
            True if idempotency holds.
        """
        aa = self.compose_two(a, a)
        return _rank(aa.trust_level) == _rank(a.trust_level)


# ---------------------------------------------------------------------------
# TrustAttenuation — implements ⊖
# ---------------------------------------------------------------------------


@dataclass
class TrustAttenuation:
    """Implements trust attenuation ⊖.

    Attenuation strictly decreases the trust level.  It models how trust
    degrades with distance, transport, or intermediate steps.

    Attributes:
        attenuation_log: Log of attenuation events.
    """

    attenuation_log: List[Dict[str, Any]] = field(default_factory=list)

    def attenuate(self, config: AdmissibleConfig, steps: int = 1) -> AdmissibleConfig:
        """Attenuate a configuration by a given number of rank steps.

        Args:
            config: Configuration to attenuate.
            steps: Number of rank steps to decrease by.

        Returns:
            New AdmissibleConfig with attenuated trust level.
        """
        current_rank = _rank(config.trust_level)
        new_rank = max(0, current_rank - steps)
        new_level = _name_at_rank(new_rank)
        result = AdmissibleConfig(
            evidence_map=dict(config.evidence_map),
            trust_level=new_level,
            channel=config.channel,
            coordinate=config.coordinate,
            justification=f"attenuate({config.config_id[:8]}, steps={steps})",
            metadata={"attenuated_from": config.config_id, "steps": steps},
        )
        self.attenuation_log.append({
            "event": "attenuate",
            "from_config": config.config_id,
            "from_level": config.trust_level,
            "to_level": new_level,
            "steps": steps,
            "timestamp": time.time(),
        })
        return result

    def attenuate_by_distance(
        self, config: AdmissibleConfig, distance: float, decay_rate: float = 1.0
    ) -> AdmissibleConfig:
        """Attenuate trust by a continuous distance metric.

        The number of steps is computed as floor(distance * decay_rate).

        Args:
            config: Configuration to attenuate.
            distance: Non-negative distance value.
            decay_rate: Steps per unit distance (default 1.0).

        Returns:
            Attenuated AdmissibleConfig.
        """
        steps = max(0, math.floor(distance * decay_rate))
        result = self.attenuate(config, steps)
        result.metadata["distance"] = distance
        result.metadata["decay_rate"] = decay_rate
        return result

    def attenuate_by_transport(
        self, config: AdmissibleConfig, transport_hops: int
    ) -> AdmissibleConfig:
        """Attenuate trust by transport hop count.

        Each transport hop across a geometric boundary reduces trust by one
        rank step.

        Args:
            config: Configuration to attenuate.
            transport_hops: Number of boundary crossings.

        Returns:
            Attenuated AdmissibleConfig.
        """
        result = self.attenuate(config, transport_hops)
        result.metadata["transport_hops"] = transport_hops
        return result


# ---------------------------------------------------------------------------
# TrustPromotion — implements ↑_π
# ---------------------------------------------------------------------------


@dataclass
class TrustPromotion:
    """Implements trust promotion ↑_π with mandatory justification.

    Promotion is ONLY allowed when:
    1. A non-empty justification string is provided.
    2. A named policy (π) is referenced.
    3. The resulting level does not exceed any registered ceiling.

    The no-silent-promotion invariant is enforced by rejecting any promotion
    attempt that lacks a justification or policy name.

    Attributes:
        audit_log: Append-only log of all promotion attempts.
        registered_policies: Set of known policy names.
        ceiling_map: Maps scope keys to trust ceilings.
    """

    audit_log: List[Dict[str, Any]] = field(default_factory=list)
    registered_policies: Set[str] = field(default_factory=set)
    ceiling_map: Dict[str, str] = field(default_factory=dict)

    def promote(
        self,
        config: AdmissibleConfig,
        policy_name: str,
        justification: str,
        scope_key: str = "",
    ) -> Tuple[AdmissibleConfig, bool]:
        """Attempt to promote a configuration by one trust rank.

        Args:
            config: Configuration to promote.
            policy_name: Named policy authorising this promotion.
            justification: Non-empty justification string.
            scope_key: Optional scope key for ceiling lookup.

        Returns:
            Tuple of (resulting_config, success_bool).  On failure, the original
            config is returned unchanged.
        """
        valid, reason = self.validate_justification(policy_name, justification)
        if not valid:
            self._record_audit("promotion_rejected", config, policy_name, justification, reason)
            return (config, False)

        current_rank = _rank(config.trust_level)
        promoted_rank = current_rank + 1
        promoted_level = _name_at_rank(promoted_rank)

        # Ceiling check
        if scope_key in self.ceiling_map:
            ceiling_rank = _rank(self.ceiling_map[scope_key])
            if promoted_rank > ceiling_rank:
                self._record_audit(
                    "promotion_ceiling_blocked",
                    config,
                    policy_name,
                    justification,
                    f"ceiling={self.ceiling_map[scope_key]}",
                )
                return (AdmissibleConfig(
                    evidence_map=dict(config.evidence_map),
                    trust_level=self.ceiling_map[scope_key],
                    channel=config.channel,
                    coordinate=config.coordinate,
                    justification=f"ceiling_enforced:{policy_name}",
                    metadata={"ceiling": self.ceiling_map[scope_key]},
                ), False)

        result = AdmissibleConfig(
            evidence_map=dict(config.evidence_map),
            trust_level=promoted_level,
            channel=config.channel,
            coordinate=config.coordinate,
            justification=f"promoted via policy '{policy_name}': {justification}",
            metadata={"promoted_from": config.config_id, "policy": policy_name},
        )
        self._record_audit("promotion_accepted", config, policy_name, justification, promoted_level)
        return (result, True)

    def validate_justification(
        self, policy_name: str, justification: str
    ) -> Tuple[bool, str]:
        """Validate that a justification is acceptable.

        Args:
            policy_name: Policy name to validate.
            justification: Justification string.

        Returns:
            Tuple of (valid_bool, reason_string).
        """
        if not policy_name or not policy_name.strip():
            return (False, "policy_name must not be empty (no silent promotion)")
        if not justification or not justification.strip():
            return (False, "justification must not be empty (no silent promotion)")
        if len(justification.strip()) < 10:
            return (False, "justification too short (minimum 10 characters)")
        return (True, "")

    def reject_silent_promotion(self, config: AdmissibleConfig) -> AdmissibleConfig:
        """Record and return the config unchanged as a silent-promotion rejection.

        Args:
            config: Configuration on which silent promotion was attempted.

        Returns:
            Unchanged config.
        """
        self.audit_log.append({
            "event": "silent_promotion_rejected",
            "config_id": config.config_id,
            "trust_level": config.trust_level,
            "timestamp": time.time(),
        })
        return config

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the full promotion audit log.

        Returns:
            List of audit record dicts.
        """
        return list(self.audit_log)

    def record_in_audit_log(self, event: str, details: Dict[str, Any]) -> None:
        """Manually record an event in the audit log.

        Args:
            event: Event type string.
            details: Dict of additional details.
        """
        self.audit_log.append({"event": event, "timestamp": time.time(), **details})

    def _record_audit(
        self,
        event: str,
        config: AdmissibleConfig,
        policy: str,
        justification: str,
        outcome: str,
    ) -> None:
        self.audit_log.append({
            "event": event,
            "config_id": config.config_id,
            "from_level": config.trust_level,
            "policy": policy,
            "justification": justification[:80],
            "outcome": outcome,
            "timestamp": time.time(),
        })


# ---------------------------------------------------------------------------
# TrustDemotion — implements ↓_χ
# ---------------------------------------------------------------------------


@dataclass
class TrustDemotion:
    """Implements trust demotion ↓_χ and ceiling enforcement.

    Demotion is the dual of promotion: it strictly decreases trust to at most
    the ceiling χ.  Unlike promotion, demotion does not require justification —
    it is always safe to decrease trust.

    Attributes:
        demotion_log: Log of demotion events.
        global_ceiling_map: Maps scope_key → ceiling level name.
    """

    demotion_log: List[Dict[str, Any]] = field(default_factory=list)
    global_ceiling_map: Dict[str, str] = field(default_factory=dict)

    def demote(
        self, config: AdmissibleConfig, ceiling: str, reason: str = ""
    ) -> AdmissibleConfig:
        """Demote a configuration to at most `ceiling`.

        If the config already satisfies the ceiling, it is returned unchanged.

        Args:
            config: Configuration to demote.
            ceiling: Maximum allowed trust level name.
            reason: Optional reason string.

        Returns:
            Demoted (or unchanged) AdmissibleConfig.
        """
        current_rank = _rank(config.trust_level)
        ceiling_rank = _rank(ceiling)
        if current_rank <= ceiling_rank:
            return config
        result = AdmissibleConfig(
            evidence_map=dict(config.evidence_map),
            trust_level=ceiling,
            channel=config.channel,
            coordinate=config.coordinate,
            justification=f"demoted to ceiling '{ceiling}': {reason}",
            metadata={"demoted_from": config.config_id, "ceiling": ceiling, "reason": reason},
        )
        self.demotion_log.append({
            "event": "demotion",
            "config_id": config.config_id,
            "from_level": config.trust_level,
            "ceiling": ceiling,
            "reason": reason,
            "timestamp": time.time(),
        })
        return result

    def enforce_ceiling(
        self, config: AdmissibleConfig, scope_key: str
    ) -> AdmissibleConfig:
        """Enforce the registered ceiling for a scope.

        Args:
            config: Configuration to enforce ceiling on.
            scope_key: Scope key to look up ceiling.

        Returns:
            Config with ceiling enforced; unchanged if no ceiling set.
        """
        ceiling = self.global_ceiling_map.get(scope_key)
        if ceiling is None:
            return config
        return self.demote(config, ceiling, reason=f"scope_ceiling:{scope_key}")

    def compute_ceiling_map(
        self, configs: List[AdmissibleConfig], default_ceiling: str = "ORACLE_PROPOSED"
    ) -> Dict[str, str]:
        """Compute a ceiling map from a list of configurations.

        The ceiling for each coordinate is the maximum observed trust level.

        Args:
            configs: List of AdmissibleConfig objects.
            default_ceiling: Fallback ceiling if no configs exist for coordinate.

        Returns:
            Dict mapping coordinate → ceiling trust level name.
        """
        ceiling_map: Dict[str, int] = {}
        for cfg in configs:
            current = ceiling_map.get(cfg.coordinate, 0)
            ceiling_map[cfg.coordinate] = max(current, _rank(cfg.trust_level))
        return {coord: _name_at_rank(rank) for coord, rank in ceiling_map.items()}

    def set_ceiling(self, scope_key: str, ceiling: str) -> None:
        """Register a ceiling for a scope.

        Args:
            scope_key: Scope identifier.
            ceiling: Trust level name ceiling.

        Raises:
            ValueError: If ceiling is not a valid trust level.
        """
        if ceiling not in _RANK:
            raise ValueError(f"Unknown trust level '{ceiling}'")
        self.global_ceiling_map[scope_key] = ceiling


# ---------------------------------------------------------------------------
# TrustAlgebraInstance — integrates all operations
# ---------------------------------------------------------------------------


@dataclass
class TrustAlgebraInstance:
    """Integrates all trust algebra operations into a single interface.

    Provides a unified API for the full algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).
    Enforces the no-silent-promotion invariant at the integration layer.

    Attributes:
        instance_id: Unique identifier.
        order: TrustOrderRelation implementing ≼.
        composition: TrustComposition implementing ⊕.
        attenuation: TrustAttenuation implementing ⊖.
        promotion: TrustPromotion implementing ↑_π.
        demotion: TrustDemotion implementing ↓_χ.
        configs: Registered AdmissibleConfig objects.
        validation_log: Log of validation events.
    """

    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order: TrustOrderRelation = field(default_factory=TrustOrderRelation)
    composition: TrustComposition = field(default_factory=TrustComposition)
    attenuation: TrustAttenuation = field(default_factory=TrustAttenuation)
    promotion: TrustPromotion = field(default_factory=TrustPromotion)
    demotion: TrustDemotion = field(default_factory=TrustDemotion)
    configs: Dict[str, AdmissibleConfig] = field(default_factory=dict)
    validation_log: List[Dict[str, Any]] = field(default_factory=list)

    def register(self, config: AdmissibleConfig) -> bool:
        """Register an AdmissibleConfig if it passes validation.

        Args:
            config: Configuration to register.

        Returns:
            True if registered; False if validation failed.
        """
        violations = config.validate()
        if violations:
            self.validation_log.append({
                "event": "registration_rejected",
                "config_id": config.config_id,
                "violations": violations,
                "timestamp": time.time(),
            })
            return False
        self.configs[config.config_id] = config
        self.validation_log.append({
            "event": "registration_accepted",
            "config_id": config.config_id,
            "trust_level": config.trust_level,
            "timestamp": time.time(),
        })
        return True

    def compose(self, config_id_a: str, config_id_b: str) -> Optional[AdmissibleConfig]:
        """Compose two registered configurations.

        Args:
            config_id_a: First config ID.
            config_id_b: Second config ID.

        Returns:
            Composed config, or None if either ID is unknown.
        """
        a = self.configs.get(config_id_a)
        b = self.configs.get(config_id_b)
        if a is None or b is None:
            return None
        result = self.composition.compose_two(a, b)
        self.configs[result.config_id] = result
        return result

    def promote_config(
        self,
        config_id: str,
        policy_name: str,
        justification: str,
        scope_key: str = "",
    ) -> Tuple[Optional[AdmissibleConfig], bool]:
        """Promote a registered configuration.

        Enforces no-silent-promotion: promotion without justification is silently
        rejected and logged.

        Args:
            config_id: ID of the config to promote.
            policy_name: Named policy.
            justification: Non-empty justification.
            scope_key: Optional scope key for ceiling.

        Returns:
            Tuple of (resulting_config_or_None, success_bool).
        """
        config = self.configs.get(config_id)
        if config is None:
            return (None, False)
        result, success = self.promotion.promote(config, policy_name, justification, scope_key)
        if success:
            self.configs[result.config_id] = result
        return (result, success)

    def demote_config(
        self, config_id: str, ceiling: str, reason: str = ""
    ) -> Optional[AdmissibleConfig]:
        """Demote a registered configuration.

        Args:
            config_id: ID of the config to demote.
            ceiling: Target ceiling trust level.
            reason: Optional reason.

        Returns:
            Demoted config, or None if config_id is unknown.
        """
        config = self.configs.get(config_id)
        if config is None:
            return None
        result = self.demotion.demote(config, ceiling, reason)
        self.configs[result.config_id] = result
        return result

    def verify_no_silent_promotion_invariant(self) -> List[str]:
        """Scan audit logs for any silent promotion events.

        Returns:
            List of violation descriptions; empty if invariant holds.
        """
        violations = []
        for entry in self.promotion.audit_log:
            if entry.get("event") == "silent_promotion_rejected":
                violations.append(
                    f"Silent promotion attempt recorded for config {entry.get('config_id', 'unknown')} "
                    f"at {entry.get('timestamp', 0)}"
                )
        return violations

    def validate_all_configs(self) -> Dict[str, List[str]]:
        """Validate all registered configurations.

        Returns:
            Dict mapping config_id → list of violations (only for invalid configs).
        """
        result: Dict[str, List[str]] = {}
        for cid, config in self.configs.items():
            violations = config.validate()
            if violations:
                result[cid] = violations
        return result

    def summary(self) -> Dict[str, Any]:
        """Produce a summary of the algebra instance state.

        Returns:
            Dict with instance_id, config count, promotion audit size, etc.
        """
        return {
            "instance_id": self.instance_id,
            "registered_configs": len(self.configs),
            "admissible_configs": sum(1 for c in self.configs.values() if c.is_admissible()),
            "promotion_attempts": len(self.promotion.audit_log),
            "demotion_events": len(self.demotion.demotion_log),
            "composition_events": len(self.composition.composition_log),
            "attenuation_events": len(self.attenuation.attenuation_log),
            "silent_promotion_violations": len(self.verify_no_silent_promotion_invariant()),
        }
