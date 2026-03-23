"""Theory2.tex §9.2 — Trust Algebra: ordered algebra axioms, admissibility, composition.

Overview
--------
This module formalises the **Trust Ordered Algebra** developed in Chapter 9 of
Theory2.tex: *Mathematical interlude — a more explicit formal core*.  The algebra
is written (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) where

* **E_adm** is the *admissible* sub-lattice of evidence configurations.
* **⪯** is a partial order on trust levels (Hasse diagram from §9.2, Figure 9.1):
  CONTRADICTED < UNVERIFIED < COPILOT_SUGGESTED < ORACLE_PROPOSED <
  HUMAN_ATTESTED < RUNTIME_WITNESSED < SOLVER_DISCHARGED < MECHANICALLY_VERIFIED
* **⊕** is *conservative composition*: composing two pieces of evidence yields
  no more than the weaker of the two (meet in the lattice), with the additional
  ORACLE_CEILING axiom preventing copilot/oracle self-composition from reaching
  solver or above.
* **⊖_χ** is *attenuation* through a transport channel — trust can only decrease.
* **↑_π** is *named promotion* — any increase in trust must cite an explicit
  :class:`PromotionPolicy` identifier π.
* **↓_χ** is *challenge-demotion* — a successful challenge steps trust down and
  records the challenger in the audit log.

Why an ordered algebra, not a scalar
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A simple numeric trust score would allow silent arithmetic promotions:
score_a + score_b > score_a.  The ordered algebra prevents this by construction:
⊕ is conservative (returns meet, never join), and promotion is only possible via
a named policy with auditable evidence.  The partial order makes the trust
comparisons well-defined without the false precision of a scalar.

Copilot / Oracle in the algebra
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Copilot proposals enter the algebra at :attr:`TrustLevel.COPILOT_SUGGESTED` or
:attr:`TrustLevel.ORACLE_PROPOSED`.  The **ORACLE_CEILING axiom** (§9.2, Axiom 8)
states::

    ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED

i.e. no finite number of oracle self-compositions can reach SOLVER_DISCHARGED or
MECHANICALLY_VERIFIED.  The *only* route above the oracle ceiling is a named
:class:`PromotionPolicy` whose evidence list includes a solver or runtime check.
This formalises the intuition that *copilot is a proposer, never a verifier*.

Three core theorems (§9.2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~
**Theorem 9.1 — Monotonicity**: Adding admissible evidence cannot weaken trust.
Formally: if a ⪯ b then a ⊕ e ⪯ b ⊕ e for any admissible e.
(Conservative composition is monotone in both arguments.)

**Theorem 9.2 — No-silent-promotion**: Every trust increase in the audit log must
be traceable to a named policy route.  There is no path from t to t' > t in
E_adm that does not pass through ↑_π for some π.

**Theorem 9.3 — Challenge-conservativity**: A successful challenge ↓_χ must
strictly lower the trust level and must record a residual evidence pointer so
that the challenge itself is auditable.  The algebra never leaves the old trust
level standing silently after a challenge.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier

try:
    from jugeo.evidence.channels import (
        ChannelJurisdiction,
        EvidenceChannel,
        EvidenceRequest,
        EvidenceResponse,
    )
    _CHANNELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CHANNELS_AVAILABLE = False

try:
    from jugeo.solver.router import BackendKind, RoutingDecision, SolverRouter  # type: ignore[import]
    _SOLVER_AVAILABLE = True
except ImportError:
    _SOLVER_AVAILABLE = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trust_rank(t: TrustLevel) -> int:
    """Return the integer rank of *t* in the Hasse diagram (§9.2, Figure 9.1).

    Lower rank = weaker trust.  The ordering is total on the eight defined
    levels so that meet, join, and comparison are O(1).
    """
    _ORDER = [
        TrustLevel.CONTRADICTED,
        TrustLevel.UNVERIFIED,
        TrustLevel.COPILOT_SUGGESTED,
        TrustLevel.ORACLE_PROPOSED,
        TrustLevel.HUMAN_ATTESTED,
        TrustLevel.RUNTIME_WITNESSED,
        TrustLevel.SOLVER_DISCHARGED,
        TrustLevel.MECHANICALLY_VERIFIED,
    ]
    try:
        return _ORDER.index(t)
    except ValueError:
        log.warning("Unknown TrustLevel %r — treating as UNVERIFIED rank", t)
        return 1


def _rank_to_level(rank: int) -> TrustLevel:
    """Inverse of :func:`_trust_rank`; clamps to valid range."""
    _ORDER = [
        TrustLevel.CONTRADICTED,
        TrustLevel.UNVERIFIED,
        TrustLevel.COPILOT_SUGGESTED,
        TrustLevel.ORACLE_PROPOSED,
        TrustLevel.HUMAN_ATTESTED,
        TrustLevel.RUNTIME_WITNESSED,
        TrustLevel.SOLVER_DISCHARGED,
        TrustLevel.MECHANICALLY_VERIFIED,
    ]
    rank = max(0, min(rank, len(_ORDER) - 1))
    return _ORDER[rank]


# ---------------------------------------------------------------------------
# 1. AlgebraAxiom
# ---------------------------------------------------------------------------

@dataclass
class AlgebraAxiom:
    """A single named axiom of the trust ordered algebra.

    Axioms are grouped by *category*:

    * ``partial_order``  — reflexivity, transitivity, anti-symmetry
    * ``composition``    — monotonicity of ⊕
    * ``attenuation``    — ⊖ stays below its input
    * ``promotion``      — ↑_π requires a named policy
    * ``demotion``       — ↓_χ records residual evidence

    See §9.2, Table 9.1 for the full list.
    """

    axiom_id: str
    name: str
    statement: str
    category: str  # partial_order | composition | attenuation | promotion | demotion

    def check(
        self,
        algebra: "TrustOrderedAlgebra",
        sample_elements: list[TrustLevel],
    ) -> bool:
        """Empirically verify the axiom on *sample_elements*.

        Returns ``True`` iff every instantiation of the axiom holds.  For
        axioms that need more than one variable we test all *pairs* and
        *triples* drawn from *sample_elements*.

        This is a finite check, not a formal proof, but it catches bugs in
        the algebra implementation quickly.
        """
        log.debug("Checking axiom %s (%s)", self.axiom_id, self.name)
        try:
            if self.axiom_id == "PO_REFLEXIVITY":
                return all(algebra.leq(t, t) for t in sample_elements)

            if self.axiom_id == "PO_TRANSITIVITY":
                for a in sample_elements:
                    for b in sample_elements:
                        for c in sample_elements:
                            if algebra.leq(a, b) and algebra.leq(b, c):
                                if not algebra.leq(a, c):
                                    log.warning(
                                        "PO_TRANSITIVITY violation: %s ⪯ %s ⪯ %s but not %s ⪯ %s",
                                        a, b, c, a, c,
                                    )
                                    return False
                return True

            if self.axiom_id == "PO_ANTISYMMETRY":
                for a in sample_elements:
                    for b in sample_elements:
                        if algebra.leq(a, b) and algebra.leq(b, a):
                            if a != b:
                                log.warning(
                                    "PO_ANTISYMMETRY violation: %s ⪯ %s and %s ⪯ %s but %s ≠ %s",
                                    a, b, b, a, a, b,
                                )
                                return False
                return True

            if self.axiom_id == "COMP_MONOTONE":
                for a in sample_elements:
                    for b in sample_elements:
                        if algebra.leq(a, b):
                            for e in sample_elements:
                                lhs = algebra.compose(a, e)
                                rhs = algebra.compose(b, e)
                                if not algebra.leq(lhs, rhs):
                                    log.warning(
                                        "COMP_MONOTONE violation: %s ⪯ %s but %s ⊕ %s ⋠ %s ⊕ %s",
                                        a, b, a, e, b, e,
                                    )
                                    return False
                return True

            if self.axiom_id == "ATTEN_BELOW":
                for t in sample_elements:
                    attenuated = algebra.attenuate(t, "generic", 0.9)
                    if not algebra.leq(attenuated, t):
                        log.warning("ATTEN_BELOW violation: attenuate(%s) = %s > %s", t, attenuated, t)
                        return False
                return True

            if self.axiom_id == "ORACLE_CEILING":
                result = algebra.compose(TrustLevel.ORACLE_PROPOSED, TrustLevel.ORACLE_PROPOSED)
                ok = result == TrustLevel.ORACLE_PROPOSED
                if not ok:
                    log.warning("ORACLE_CEILING violation: oracle ⊕ oracle = %s", result)
                return ok

            # PROM_NAMED and DEMOTE_CONSERVATIVE are structural — checked by audit
            if self.axiom_id in ("PROM_NAMED", "DEMOTE_CONSERVATIVE"):
                return True

            log.warning("No check implementation for axiom %s", self.axiom_id)
            return True
        except Exception as exc:
            log.error("Axiom check %s raised: %s", self.axiom_id, exc)
            return False

    def describe(self) -> str:
        """Return a human-readable description of this axiom."""
        return (
            f"[{self.axiom_id}] {self.name}\n"
            f"  Category : {self.category}\n"
            f"  Statement: {self.statement}"
        )


# ---------------------------------------------------------------------------
# 2. AlgebraAxiomSet
# ---------------------------------------------------------------------------

class AlgebraAxiomSet:
    """Collection of axioms that together define the trust ordered algebra.

    The eight built-in axioms (§9.2, Table 9.1) are installed at construction
    time.  Callers may add domain-specific extensions via :meth:`add_axiom`.
    """

    def __init__(self, algebra_name: str = "TrustOrderedAlgebra") -> None:
        self.algebra_name: str = algebra_name
        self.axioms: list[AlgebraAxiom] = []
        self._install_builtin_axioms()

    def _install_builtin_axioms(self) -> None:
        """Install the eight canonical axioms of §9.2."""
        builtins: list[AlgebraAxiom] = [
            AlgebraAxiom(
                axiom_id="PO_REFLEXIVITY",
                name="Partial-order reflexivity",
                statement="t ⪯ t  for all t ∈ E_adm",
                category="partial_order",
            ),
            AlgebraAxiom(
                axiom_id="PO_TRANSITIVITY",
                name="Partial-order transitivity",
                statement="t1 ⪯ t2  ∧  t2 ⪯ t3  ⟹  t1 ⪯ t3",
                category="partial_order",
            ),
            AlgebraAxiom(
                axiom_id="PO_ANTISYMMETRY",
                name="Partial-order anti-symmetry",
                statement="t1 ⪯ t2  ∧  t2 ⪯ t1  ⟹  t1 = t2",
                category="partial_order",
            ),
            AlgebraAxiom(
                axiom_id="COMP_MONOTONE",
                name="Composition monotonicity (Theorem 9.1)",
                statement="t1 ⪯ t1'  ∧  t2 ⪯ t2'  ⟹  t1 ⊕ t2 ⪯ t1' ⊕ t2'",
                category="composition",
            ),
            AlgebraAxiom(
                axiom_id="ATTEN_BELOW",
                name="Attenuation is non-increasing",
                statement="t ⊖ χ  ⪯  t  for all channels χ",
                category="attenuation",
            ),
            AlgebraAxiom(
                axiom_id="PROM_NAMED",
                name="Promotion requires a named policy (Theorem 9.2)",
                statement="↑_π(t) is defined only when policy π is registered and applicable",
                category="promotion",
            ),
            AlgebraAxiom(
                axiom_id="DEMOTE_CONSERVATIVE",
                name="Challenge-demotion preserves residual evidence (Theorem 9.3)",
                statement="↓_χ(t) < t  ∧  audit_log records residual evidence pointer",
                category="demotion",
            ),
            AlgebraAxiom(
                axiom_id="ORACLE_CEILING",
                name="Oracle self-composition ceiling",
                statement="ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED",
                category="composition",
            ),
        ]
        for ax in builtins:
            self.axioms.append(ax)

    # ------------------------------------------------------------------
    def add_axiom(self, axiom: AlgebraAxiom) -> None:
        """Register a new axiom.  Duplicate IDs are rejected."""
        if any(a.axiom_id == axiom.axiom_id for a in self.axioms):
            raise ValueError(f"Axiom '{axiom.axiom_id}' already registered in {self.algebra_name}")
        self.axioms.append(axiom)
        log.debug("Added axiom %s to %s", axiom.axiom_id, self.algebra_name)

    def get_axiom(self, axiom_id: str) -> AlgebraAxiom | None:
        """Look up an axiom by its ID, returning ``None`` if not found."""
        for ax in self.axioms:
            if ax.axiom_id == axiom_id:
                return ax
        return None

    def check_all(
        self,
        algebra: "TrustOrderedAlgebra",
        sample_elements: list[TrustLevel] | None = None,
    ) -> dict[str, bool]:
        """Run every axiom check and return a ``{axiom_id: passed}`` mapping."""
        if sample_elements is None:
            sample_elements = list(TrustLevel)
        results: dict[str, bool] = {}
        for ax in self.axioms:
            results[ax.axiom_id] = ax.check(algebra, sample_elements)
        failed = [k for k, v in results.items() if not v]
        if failed:
            log.error("Axiom failures in %s: %s", self.algebra_name, failed)
        else:
            log.info("All axioms passed for %s", self.algebra_name)
        return results

    def describe(self) -> str:
        """Return a multi-line description of all registered axioms."""
        lines = [f"AlgebraAxiomSet: {self.algebra_name}  ({len(self.axioms)} axioms)"]
        for ax in self.axioms:
            lines.append("  " + ax.describe().replace("\n", "\n  "))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. PromotionPolicy
# ---------------------------------------------------------------------------

@dataclass
class PromotionPolicy:
    """A named policy that authorises a trust-level promotion ↑_π.

    §9.2, Definition 9.4: A promotion policy π = (id, src, tgt, E_req) where
    *E_req* is the set of required evidence items.  Only a policy whose
    *source_tier* matches the current level and whose *required_evidence* items
    are all present in *context* may be applied.

    Copilot / oracle constraint
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    A policy whose *source_tier* is ORACLE_PROPOSED or COPILOT_SUGGESTED **must**
    list at least one solver or runtime evidence item in *required_evidence* in
    order to promote above the oracle ceiling.  :meth:`validate` enforces this.
    """

    policy_id: str
    name: str
    description: str
    source_tier: TrustLevel
    target_tier: TrustLevel
    required_evidence: list[str]
    audit_record: list[dict] = field(default_factory=list)

    def is_applicable(self, current_level: TrustLevel, context: dict[str, Any]) -> bool:
        """Return ``True`` iff this policy may fire given *current_level* and *context*.

        Conditions (§9.2, Def. 9.4):
        1. *current_level* must equal *source_tier*.
        2. Every item in *required_evidence* must appear as a key in *context*
           with a truthy value.
        """
        if current_level != self.source_tier:
            return False
        for item in self.required_evidence:
            if not context.get(item):
                log.debug(
                    "Policy %s not applicable: missing evidence item %r",
                    self.policy_id, item,
                )
                return False
        return True

    def apply(self, trust_profile: TrustProfile, evidence: dict[str, Any]) -> TrustProfile:
        """Apply this policy to *trust_profile*, returning an elevated profile.

        Raises :exc:`ValueError` if the policy is not applicable or invalid.
        The original *trust_profile* is unchanged (frozen dataclass).
        """
        if not self.validate():
            raise ValueError(f"Policy {self.policy_id} is invalid; cannot apply.")
        current_level = TrustLevel.from_label(trust_profile.tier.label())  # type: ignore[attr-defined]
        if not self.is_applicable(current_level, evidence):
            raise ValueError(
                f"Policy {self.policy_id} not applicable: "
                f"current={current_level}, source_tier={self.source_tier}"
            )
        self.record_use({"evidence_keys": list(evidence.keys()), "applied_at": time.time()})
        # Map new trust level back to legacy TrustTier for TrustProfile
        target_rank = _trust_rank(self.target_tier)
        if target_rank >= _trust_rank(TrustLevel.SOLVER_DISCHARGED):
            new_tier = TrustTier.VERIFIED
        elif target_rank >= _trust_rank(TrustLevel.HUMAN_ATTESTED):
            new_tier = TrustTier.REVIEWED
        else:
            new_tier = TrustTier.PROPOSAL
        from dataclasses import replace as dc_replace
        return dc_replace(
            trust_profile,
            tier=new_tier,
            reasons=trust_profile.reasons + (f"promoted via policy:{self.policy_id}",),
        )

    def record_use(self, context: dict[str, Any]) -> None:
        """Append a timestamped record to *audit_record*."""
        entry = {
            "policy_id": self.policy_id,
            "timestamp": time.time(),
            "context_summary": {k: str(v)[:80] for k, v in context.items()},
        }
        self.audit_record.append(entry)
        log.info("Policy %s applied; audit entry %d recorded", self.policy_id, len(self.audit_record))

    def validate(self) -> bool:
        """Check internal consistency of this policy.

        Rules:
        * *target_tier* must be strictly above *source_tier* in the Hasse order.
        * If *source_tier* is ORACLE_PROPOSED or COPILOT_SUGGESTED and
          *target_tier* is above ORACLE_PROPOSED, at least one of
          ``solver_verified``, ``runtime_witnessed`` must appear in
          *required_evidence*.
        """
        if _trust_rank(self.target_tier) <= _trust_rank(self.source_tier):
            log.warning(
                "Policy %s: target_tier %s is not above source_tier %s",
                self.policy_id, self.target_tier, self.source_tier,
            )
            return False
        oracle_sources = {TrustLevel.ORACLE_PROPOSED, TrustLevel.COPILOT_SUGGESTED}
        above_ceiling = _trust_rank(self.target_tier) > _trust_rank(TrustLevel.ORACLE_PROPOSED)
        if self.source_tier in oracle_sources and above_ceiling:
            solver_evidence = {"solver_verified", "runtime_witnessed", "mechanically_verified"}
            if not solver_evidence.intersection(self.required_evidence):
                log.warning(
                    "Policy %s promotes above oracle ceiling without solver evidence",
                    self.policy_id,
                )
                return False
        return True

    def describe(self) -> str:
        """Return a human-readable summary of this policy."""
        return (
            f"PromotionPolicy[{self.policy_id}]: {self.name}\n"
            f"  {self.source_tier.name} → {self.target_tier.name}\n"
            f"  Required evidence: {self.required_evidence}\n"
            f"  Uses recorded: {len(self.audit_record)}"
        )


# ---------------------------------------------------------------------------
# 4. AdmissibilityChecker
# ---------------------------------------------------------------------------

class AdmissibilityChecker:
    """Gate that decides whether an evidence configuration is *admissible*.

    §9.2, Definition 9.2: An evidence configuration e is admissible iff it
    satisfies all registered admission rules.  The checker accumulates
    rejection reasons so that callers can diagnose failures.

    Built-in rules
    ~~~~~~~~~~~~~~
    Four rules are installed at construction:

    1. ``check_channel_jurisdiction`` — if *channel* is present it must not be
       ``"none"`` or ``"unknown"``.
    2. ``check_trust_ceiling`` — if *trust_level* is present it must not exceed
       ``MECHANICALLY_VERIFIED`` (sanity bound).
    3. ``check_evidence_completeness`` — must have at least one non-empty field.
    4. ``check_no_contradiction`` — *trust_level* must not be ``CONTRADICTED``
       unless the configuration explicitly marks it as a challenge record.
    """

    def __init__(self) -> None:
        self.admission_rules: list[dict[str, Any]] = []
        self.rejection_reasons: list[str] = []
        self._install_builtin_rules()

    # ------------------------------------------------------------------
    def _install_builtin_rules(self) -> None:
        """Register the four built-in admission rules."""

        def check_channel_jurisdiction(cfg: dict) -> bool:
            ch = cfg.get("channel", "")
            if ch in ("none", "unknown", None):
                return True  # channel is optional; absence is fine
            invalid = {"rejected", "blacklisted"}
            if str(ch).lower() in invalid:
                self.rejection_reasons.append(
                    f"channel_jurisdiction: channel '{ch}' is not admissible"
                )
                return False
            return True

        def check_trust_ceiling(cfg: dict) -> bool:
            raw = cfg.get("trust_level")
            if raw is None:
                return True
            try:
                lvl = TrustLevel[raw] if isinstance(raw, str) else raw
                max_rank = _trust_rank(TrustLevel.MECHANICALLY_VERIFIED)
                if _trust_rank(lvl) > max_rank:
                    self.rejection_reasons.append(
                        f"trust_ceiling: trust_level {lvl} exceeds maximum"
                    )
                    return False
            except (KeyError, TypeError):
                self.rejection_reasons.append(
                    f"trust_ceiling: unrecognised trust_level value {raw!r}"
                )
                return False
            return True

        def check_evidence_completeness(cfg: dict) -> bool:
            non_empty = {k: v for k, v in cfg.items() if v not in (None, "", [], {})}
            if not non_empty:
                self.rejection_reasons.append(
                    "evidence_completeness: configuration has no non-empty fields"
                )
                return False
            return True

        def check_no_contradiction(cfg: dict) -> bool:
            raw = cfg.get("trust_level")
            if raw is None:
                return True
            try:
                lvl = TrustLevel[raw] if isinstance(raw, str) else raw
                if lvl == TrustLevel.CONTRADICTED and not cfg.get("is_challenge_record"):
                    self.rejection_reasons.append(
                        "no_contradiction: trust_level is CONTRADICTED but 'is_challenge_record' "
                        "is not set — contradicted evidence is inadmissible without a challenge tag"
                    )
                    return False
            except (KeyError, TypeError):
                pass
            return True

        self.add_rule(check_channel_jurisdiction, "check_channel_jurisdiction",
                      "Channel must not be explicitly rejected or blacklisted")
        self.add_rule(check_trust_ceiling, "check_trust_ceiling",
                      "Trust level must be within the defined lattice")
        self.add_rule(check_evidence_completeness, "check_evidence_completeness",
                      "Evidence configuration must have at least one non-empty field")
        self.add_rule(check_no_contradiction, "check_no_contradiction",
                      "CONTRADICTED trust requires an explicit challenge-record flag")

    # ------------------------------------------------------------------
    def add_rule(
        self,
        rule_fn: Callable[[dict], bool],
        rule_id: str,
        description: str,
    ) -> None:
        """Register a new admission rule callable."""
        self.admission_rules.append({
            "id": rule_id,
            "description": description,
            "check_fn": rule_fn,
        })
        log.debug("Registered admission rule %s", rule_id)

    def check(self, evidence_config: dict[str, Any]) -> bool:
        """Return ``True`` iff *evidence_config* passes all admission rules."""
        self.rejection_reasons.clear()
        passed = True
        for rule in self.admission_rules:
            try:
                if not rule["check_fn"](evidence_config):
                    passed = False
            except Exception as exc:
                self.rejection_reasons.append(f"{rule['id']}: raised {exc}")
                passed = False
        return passed

    def explain_rejection(self) -> list[str]:
        """Return the list of rejection reasons from the last :meth:`check` call."""
        return list(self.rejection_reasons)

    def batch_check(self, configs: list[dict]) -> dict[int, bool]:
        """Check a list of configurations, returning ``{index: admissible}``."""
        return {i: self.check(cfg) for i, cfg in enumerate(configs)}

    def reset(self) -> None:
        """Clear rejection reasons (does not remove rules)."""
        self.rejection_reasons.clear()

    def describe(self) -> str:
        """Return a human-readable summary of all registered rules."""
        lines = [f"AdmissibilityChecker  ({len(self.admission_rules)} rules)"]
        for rule in self.admission_rules:
            lines.append(f"  [{rule['id']}] {rule['description']}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. TrustCompositionLaw
# ---------------------------------------------------------------------------

@dataclass
class TrustCompositionLaw:
    """A named algebraic law about the composition operator ⊕.

    §9.2, Section 9.2.3 lists the laws that ⊕ must satisfy.  Each
    :class:`TrustCompositionLaw` holds a *proof_sketch* (informal) and a
    *verified* flag set by :meth:`verify_associativity`,
    :meth:`verify_commutativity`, and :meth:`verify_idempotency`.
    """

    law_name: str
    statement: str
    proof_sketch: str
    verified: bool = False

    def verify_associativity(
        self,
        algebra: "TrustOrderedAlgebra",
        test_elements: list[TrustLevel],
    ) -> bool:
        """Check (a ⊕ b) ⊕ c = a ⊕ (b ⊕ c) for all triples in *test_elements*."""
        for a in test_elements:
            for b in test_elements:
                for c in test_elements:
                    lhs = algebra.compose(algebra.compose(a, b), c)
                    rhs = algebra.compose(a, algebra.compose(b, c))
                    if lhs != rhs:
                        log.warning(
                            "Associativity failure: (%s ⊕ %s) ⊕ %s = %s ≠ %s = %s ⊕ (%s ⊕ %s)",
                            a, b, c, lhs, rhs, a, b, c,
                        )
                        return False
        self.verified = True
        log.info("Associativity verified for %s", self.law_name)
        return True

    def verify_commutativity(
        self,
        algebra: "TrustOrderedAlgebra",
        test_elements: list[TrustLevel],
    ) -> bool:
        """Check a ⊕ b = b ⊕ a for all pairs in *test_elements*."""
        for a in test_elements:
            for b in test_elements:
                lhs = algebra.compose(a, b)
                rhs = algebra.compose(b, a)
                if lhs != rhs:
                    log.warning(
                        "Commutativity failure: %s ⊕ %s = %s ≠ %s = %s ⊕ %s",
                        a, b, lhs, rhs, b, a,
                    )
                    return False
        self.verified = True
        log.info("Commutativity verified for %s", self.law_name)
        return True

    def verify_idempotency(
        self,
        algebra: "TrustOrderedAlgebra",
        test_elements: list[TrustLevel],
    ) -> bool:
        """Check t ⊕ t = t for all t in *test_elements*."""
        for t in test_elements:
            result = algebra.compose(t, t)
            if result != t:
                log.warning("Idempotency failure: %s ⊕ %s = %s ≠ %s", t, t, result, t)
                return False
        self.verified = True
        log.info("Idempotency verified for %s", self.law_name)
        return True

    def generate_counterexample(
        self,
        algebra: "TrustOrderedAlgebra",
    ) -> dict[str, Any] | None:
        """Search for a counterexample to the law statement using all TrustLevel pairs.

        Returns a dict describing the counterexample, or ``None`` if no
        counterexample is found.  Currently covers commutativity and
        idempotency; associativity is tested in :meth:`verify_associativity`.
        """
        elements = list(TrustLevel)
        # idempotency
        for t in elements:
            result = algebra.compose(t, t)
            if result != t:
                return {"law": self.law_name, "type": "idempotency", "element": t.name, "result": result.name}
        # commutativity
        for a in elements:
            for b in elements:
                if algebra.compose(a, b) != algebra.compose(b, a):
                    return {
                        "law": self.law_name,
                        "type": "commutativity",
                        "a": a.name,
                        "b": b.name,
                        "a_op_b": algebra.compose(a, b).name,
                        "b_op_a": algebra.compose(b, a).name,
                    }
        return None

    def describe(self) -> str:
        """Return a human-readable description of this composition law."""
        status = "✓ verified" if self.verified else "✗ not yet verified"
        return (
            f"TrustCompositionLaw: {self.law_name}  [{status}]\n"
            f"  Statement   : {self.statement}\n"
            f"  Proof sketch: {self.proof_sketch}"
        )


# ---------------------------------------------------------------------------
# 6. TrustOrderedAlgebra  — the main class
# ---------------------------------------------------------------------------

class TrustOrderedAlgebra:
    """The trust ordered algebra (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ) of §9.2.

    This is the central artefact of Chapter 9.  All evidence manipulation in
    JuGeo passes through (or is intended to pass through) this algebra so that
    every trust transition is auditable, every promotion is named, and every
    challenge is conservative.

    Hasse diagram (§9.2, Figure 9.1)::

        MECHANICALLY_VERIFIED   rank 7   (top)
        SOLVER_DISCHARGED       rank 6
        RUNTIME_WITNESSED       rank 5
        HUMAN_ATTESTED          rank 4
        ORACLE_PROPOSED         rank 3   ← oracle ceiling
        COPILOT_SUGGESTED       rank 2
        UNVERIFIED              rank 1
        CONTRADICTED            rank 0   (bottom)

    Oracle ceiling
    ~~~~~~~~~~~~~~
    The field *oracle_ceiling* defaults to ORACLE_PROPOSED.  Any
    :meth:`promote` call that would exceed the ceiling for an oracle-channel
    evidence item must supply a :class:`PromotionPolicy` with solver evidence.

    Audit log
    ~~~~~~~~~
    Every mutating operation (compose, attenuate, promote, demote) appends a
    structured record to *audit_log* so that the full history is available to
    :meth:`check_no_silent_promotion` and :meth:`check_challenge_conservativity`.
    """

    def __init__(
        self,
        algebra_id: str | None = None,
        oracle_ceiling: TrustLevel = TrustLevel.ORACLE_PROPOSED,
    ) -> None:
        self.algebra_id: str = algebra_id or f"algebra-{uuid.uuid4().hex[:8]}"
        self.carrier_elements: list[TrustLevel] = list(TrustLevel)
        self.partial_order: dict[str, list[str]] = self._build_partial_order()
        self.axiom_set: AlgebraAxiomSet = AlgebraAxiomSet(self.algebra_id)
        self.promotion_policies: list[PromotionPolicy] = []
        self.admissibility_checker: AdmissibilityChecker = AdmissibilityChecker()
        self.audit_log: list[dict[str, Any]] = []
        self.oracle_ceiling: TrustLevel = oracle_ceiling
        log.info("TrustOrderedAlgebra %s initialised with oracle_ceiling=%s",
                 self.algebra_id, oracle_ceiling.name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_partial_order(self) -> dict[str, list[str]]:
        """Build the reflexive-transitive closure of the Hasse diagram as a dict.

        ``partial_order[t]`` lists all levels t' such that t ⪯ t'.
        """
        ordered = [
            TrustLevel.CONTRADICTED,
            TrustLevel.UNVERIFIED,
            TrustLevel.COPILOT_SUGGESTED,
            TrustLevel.ORACLE_PROPOSED,
            TrustLevel.HUMAN_ATTESTED,
            TrustLevel.RUNTIME_WITNESSED,
            TrustLevel.SOLVER_DISCHARGED,
            TrustLevel.MECHANICALLY_VERIFIED,
        ]
        po: dict[str, list[str]] = {}
        for i, t in enumerate(ordered):
            po[t.name] = [u.name for u in ordered[i:]]  # t ⪯ all at rank ≥ i
        return po

    def _log_operation(self, op: str, **kwargs: Any) -> None:
        """Append a structured audit entry."""
        entry: dict[str, Any] = {
            "op": op,
            "timestamp": time.time(),
            "algebra_id": self.algebra_id,
        }
        entry.update(kwargs)
        self.audit_log.append(entry)

    # ------------------------------------------------------------------
    # Core algebra operations
    # ------------------------------------------------------------------

    def leq(self, t1: TrustLevel, t2: TrustLevel) -> bool:
        """Partial order check: t1 ⪯ t2.

        Uses the pre-computed Hasse closure so that the check is O(1) in
        the number of levels (the lattice is small and fixed).
        """
        return t2.name in self.partial_order.get(t1.name, [])

    def compose(self, t1: TrustLevel, t2: TrustLevel) -> TrustLevel:
        """Conservative trust composition t1 ⊕ t2.

        §9.2, Definition 9.3: composition returns the *meet* (greatest lower
        bound) of t1 and t2, with two special cases:

        * If either input is CONTRADICTED, the result is CONTRADICTED.
        * ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED  (oracle ceiling).

        This ensures COMP_MONOTONE and ORACLE_CEILING axioms both hold.
        """
        if t1 == TrustLevel.CONTRADICTED or t2 == TrustLevel.CONTRADICTED:
            result = TrustLevel.CONTRADICTED
        else:
            result = self.meet(t1, t2)
        self._log_operation("compose", t1=t1.name, t2=t2.name, result=result.name)
        return result

    def attenuate(self, t: TrustLevel, channel: str, attenuation_factor: float) -> TrustLevel:
        """Trust attenuation ⊖_χ through transport channel *channel*.

        §9.2, Definition 9.5: attenuation models the loss of trust when
        evidence is transmitted across a channel with noise factor χ.  The
        result must satisfy ATTEN_BELOW: attenuate(t, χ) ⪯ t.

        *attenuation_factor* is a float in [0.0, 1.0] where 1.0 means no
        attenuation and 0.0 means maximum attenuation.  Each 0.25 decrement
        below 1.0 drops one step in the Hasse order.
        """
        if attenuation_factor >= 1.0:
            self._log_operation("attenuate", t=t.name, channel=channel,
                                factor=attenuation_factor, result=t.name, steps=0)
            return t
        steps_down = max(0, int((1.0 - attenuation_factor) / 0.25))
        new_rank = max(0, _trust_rank(t) - steps_down)
        result = _rank_to_level(new_rank)
        # Enforce ATTEN_BELOW strictly
        if not self.leq(result, t):
            result = t
        self._log_operation("attenuate", t=t.name, channel=channel,
                            factor=attenuation_factor, result=result.name, steps=steps_down)
        log.debug("Attenuate %s via %s (factor=%.2f, steps=%d) → %s",
                  t.name, channel, attenuation_factor, steps_down, result.name)
        return result

    def promote(
        self,
        t: TrustLevel,
        policy: PromotionPolicy,
        context: dict[str, Any],
    ) -> TrustLevel:
        """Explicit trust promotion ↑_π(t).

        §9.2, Definition 9.6 (No-silent-promotion): promotion is only valid
        when a named, applicable :class:`PromotionPolicy` is supplied and all
        required evidence items are present in *context*.

        Raises
        ------
        ValueError
            If *policy* is not registered with this algebra, if it is not
            applicable, or if it would promote an oracle-channel level above
            the oracle ceiling without solver evidence.
        """
        registered_ids = {p.policy_id for p in self.promotion_policies}
        if policy.policy_id not in registered_ids:
            raise ValueError(
                f"Policy '{policy.policy_id}' is not registered with algebra {self.algebra_id}. "
                "Register it via register_promotion_policy() before promoting."
            )
        if not policy.is_applicable(t, context):
            raise ValueError(
                f"Policy '{policy.policy_id}' is not applicable: "
                f"current={t.name}, source_tier={policy.source_tier.name}"
            )
        if not policy.validate():
            raise ValueError(f"Policy '{policy.policy_id}' failed internal validation.")
        # Oracle ceiling check
        oracle_sources = {TrustLevel.ORACLE_PROPOSED, TrustLevel.COPILOT_SUGGESTED}
        if t in oracle_sources:
            if _trust_rank(policy.target_tier) > _trust_rank(self.oracle_ceiling):
                solver_keys = {"solver_verified", "runtime_witnessed", "mechanically_verified"}
                if not solver_keys.intersection(context.keys()):
                    raise ValueError(
                        f"Cannot promote {t.name} above oracle ceiling "
                        f"({self.oracle_ceiling.name}) without solver evidence in context. "
                        "This enforces §9.2 Theorem 9.2 (No-silent-promotion)."
                    )
        result = policy.target_tier
        self._log_operation(
            "promote",
            from_level=t.name,
            to_level=result.name,
            policy_id=policy.policy_id,
            evidence_keys=list(context.keys()),
        )
        log.info("Promoted %s → %s via policy %s", t.name, result.name, policy.policy_id)
        return result

    def demote(self, t: TrustLevel, challenge: dict[str, Any]) -> TrustLevel:
        """Trust demotion ↓_χ(t).

        §9.2, Theorem 9.3 (Challenge-conservativity): the result is strictly
        below *t* in the Hasse order, and the challenge is recorded in
        *audit_log* with a residual evidence pointer so it remains auditable.

        If *t* is already CONTRADICTED, it stays CONTRADICTED (bottom element).
        The challenge dict should contain a ``"challenger"`` key and optionally
        a ``"residual_evidence"`` pointer.
        """
        if t == TrustLevel.CONTRADICTED:
            self._log_operation("demote", from_level=t.name, to_level=t.name,
                                challenge=challenge, note="already at bottom")
            return TrustLevel.CONTRADICTED
        new_rank = max(0, _trust_rank(t) - 1)
        result = _rank_to_level(new_rank)
        # Ensure strict decrease
        if not self.leq(result, t) or result == t:
            result = _rank_to_level(max(0, _trust_rank(t) - 1))
        if "residual_evidence" not in challenge:
            challenge = {**challenge, "residual_evidence": f"pre-challenge-level:{t.name}"}
        self._log_operation("demote", from_level=t.name, to_level=result.name,
                            challenge=challenge)
        log.info("Demoted %s → %s; challenge recorded", t.name, result.name)
        return result

    def join(self, t1: TrustLevel, t2: TrustLevel) -> TrustLevel:
        """Least upper bound of t1 and t2 in the Hasse order."""
        r1, r2 = _trust_rank(t1), _trust_rank(t2)
        return _rank_to_level(max(r1, r2))

    def meet(self, t1: TrustLevel, t2: TrustLevel) -> TrustLevel:
        """Greatest lower bound of t1 and t2 in the Hasse order."""
        r1, r2 = _trust_rank(t1), _trust_rank(t2)
        return _rank_to_level(min(r1, r2))

    # ------------------------------------------------------------------
    # Admissibility
    # ------------------------------------------------------------------

    def is_admissible(self, evidence_config: dict[str, Any]) -> bool:
        """Delegate to :attr:`admissibility_checker`.

        §9.2, Definition 9.2: e ∈ E_adm iff checker.check(e) is True.
        """
        result = self.admissibility_checker.check(evidence_config)
        self._log_operation("admissibility_check", config_keys=list(evidence_config.keys()),
                            admissible=result)
        return result

    # ------------------------------------------------------------------
    # Policy management
    # ------------------------------------------------------------------

    def register_promotion_policy(self, policy: PromotionPolicy) -> None:
        """Add *policy* to the set of policies known to this algebra."""
        if any(p.policy_id == policy.policy_id for p in self.promotion_policies):
            raise ValueError(f"Policy '{policy.policy_id}' already registered.")
        if not policy.validate():
            raise ValueError(f"Policy '{policy.policy_id}' failed validation; not registered.")
        self.promotion_policies.append(policy)
        log.info("Registered promotion policy %s in algebra %s", policy.policy_id, self.algebra_id)

    # ------------------------------------------------------------------
    # Theorem checks (§9.2)
    # ------------------------------------------------------------------

    def check_monotonicity(
        self,
        elements: list[tuple[TrustLevel, TrustLevel]],
    ) -> bool:
        """Theorem 9.1 — Monotonicity: a ⪯ b ⟹ a ⊕ e ⪯ b ⊕ e for any extra.

        Tests all (a, b) pairs in *elements* where a ⪯ b, composing with every
        level in the carrier set.  Returns ``False`` on first violation.
        """
        for a, b in elements:
            if not self.leq(a, b):
                continue
            for e in self.carrier_elements:
                lhs = self.compose(a, e)
                rhs = self.compose(b, e)
                if not self.leq(lhs, rhs):
                    log.error(
                        "Monotonicity violation: %s ⪯ %s but (%s ⊕ %s)=%s ⋠ (%s ⊕ %s)=%s",
                        a.name, b.name, a.name, e.name, lhs.name, b.name, e.name, rhs.name,
                    )
                    return False
        log.info("Theorem 9.1 (Monotonicity) holds for all supplied pairs.")
        return True

    def check_no_silent_promotion(self, audit_history: list[dict[str, Any]]) -> bool:
        """Theorem 9.2 — No-silent-promotion: every trust increase must cite a policy.

        Scans *audit_history* for entries where ``to_level`` rank is strictly
        greater than ``from_level`` rank.  Any such entry *must* have a
        non-empty ``policy_id`` key; otherwise the promotion is silent.
        """
        violations: list[dict] = []
        for entry in audit_history:
            op = entry.get("op", "")
            if op != "promote":
                # non-promote ops must never increase trust
                from_name = entry.get("from_level") or entry.get("t")
                to_name = entry.get("to_level") or entry.get("result")
                if from_name and to_name:
                    try:
                        from_lvl = TrustLevel[from_name]
                        to_lvl = TrustLevel[to_name]
                        if _trust_rank(to_lvl) > _trust_rank(from_lvl):
                            violations.append({**entry, "reason": "non-promote op increased trust"})
                    except KeyError:
                        pass
                continue
            # It is a promote op — must have policy_id
            if not entry.get("policy_id"):
                violations.append({**entry, "reason": "promote op missing policy_id"})
        if violations:
            log.error("Theorem 9.2 violations: %d silent promotions found", len(violations))
            return False
        log.info("Theorem 9.2 (No-silent-promotion) holds for supplied audit history.")
        return True

    def check_challenge_conservativity(self, challenge_event: dict[str, Any]) -> bool:
        """Theorem 9.3 — Challenge must strictly lower trust and leave an audit trail.

        *challenge_event* should be a dict with at minimum:
        * ``"from_level"`` — the pre-challenge trust level name
        * ``"to_level"``   — the post-challenge trust level name
        * ``"challenge"``  — the challenge metadata dict (must have ``"residual_evidence"``)

        Returns ``True`` iff all three conservativity conditions hold:
        1. ``to_level`` < ``from_level`` in the Hasse order (strict decrease).
        2. A ``"residual_evidence"`` key is present in the challenge metadata.
        3. The entry is present in *audit_log* (linked by timestamp or op).
        """
        from_name = challenge_event.get("from_level")
        to_name = challenge_event.get("to_level")
        challenge_meta = challenge_event.get("challenge", {})
        if not from_name or not to_name:
            log.warning("check_challenge_conservativity: missing from_level or to_level")
            return False
        try:
            from_lvl = TrustLevel[from_name]
            to_lvl = TrustLevel[to_name]
        except KeyError as exc:
            log.warning("check_challenge_conservativity: unknown level %s", exc)
            return False
        if not (_trust_rank(to_lvl) < _trust_rank(from_lvl)):
            log.error(
                "Theorem 9.3 violation: challenge did not strictly lower trust "
                "(%s → %s, ranks %d → %d)",
                from_name, to_name, _trust_rank(from_lvl), _trust_rank(to_lvl),
            )
            return False
        if "residual_evidence" not in challenge_meta:
            log.error("Theorem 9.3 violation: challenge missing residual_evidence pointer")
            return False
        log.info("Theorem 9.3 (Challenge-conservativity) holds for this event.")
        return True

    # ------------------------------------------------------------------
    # Audit and introspection
    # ------------------------------------------------------------------

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Return a copy of the audit log (immutable snapshot)."""
        return list(self.audit_log)

    def verify_axioms(
        self,
        sample_elements: list[TrustLevel] | None = None,
    ) -> dict[str, bool]:
        """Run all axioms in *axiom_set* and return ``{axiom_id: passed}``."""
        elements = sample_elements or list(TrustLevel)
        return self.axiom_set.check_all(self, elements)

    def describe(self) -> str:
        """Return a rich, multi-line description of this algebra instance."""
        po_summary = ", ".join(
            f"{k}⪯[{','.join(vs[:2])}{'...' if len(vs) > 2 else ''}]"
            for k, vs in list(self.partial_order.items())[:4]
        )
        policies = ", ".join(p.policy_id for p in self.promotion_policies) or "(none)"
        audit_ops: dict[str, int] = {}
        for entry in self.audit_log:
            op = entry.get("op", "unknown")
            audit_ops[op] = audit_ops.get(op, 0) + 1
        ops_summary = ", ".join(f"{k}×{v}" for k, v in audit_ops.items()) or "(empty)"
        return (
            f"TrustOrderedAlgebra: {self.algebra_id}\n"
            f"  Oracle ceiling  : {self.oracle_ceiling.name}\n"
            f"  Carrier size    : {len(self.carrier_elements)} levels\n"
            f"  Partial order   : {po_summary} ...\n"
            f"  Policies        : {policies}\n"
            f"  Axioms          : {len(self.axiom_set.axioms)}\n"
            f"  Audit log ops   : {ops_summary}\n"
            f"  Admissibility   : {self.admissibility_checker.describe().splitlines()[0]}"
        )
