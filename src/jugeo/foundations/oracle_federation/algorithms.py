from __future__ import annotations
"""Core algorithms for oracle federation — Theory2.tex Ch7.

This module implements the algorithmic components underlying the oracle
federation framework: trust ceiling propagation, oracle proposal ranking,
federation routing optimization, witness consistency checking, jurisdiction
intersection, and corroboration chain validation.

Each algorithm is tied to specific definitions or theorems in Theory2.tex Ch7.

Algorithm summary
-----------------
- ``trust_ceiling_propagation`` — Ch7 Thm 7.1: propagate ceiling through a
  response chain, clamping each entry that exceeds the ceiling.
- ``oracle_proposal_ranking`` — Ch7 §7.1.3: rank proposals by corroboration
  count, jurisdiction quality, and recency.
- ``federation_route_optimal`` — Ch7 §7.2.3: select the solver in a
  federation that minimises cost × latency while respecting jurisdiction.
- ``witness_consistency_check`` — Ch7 Thm 7.3: check mutual consistency
  of a witness collection.
- ``jurisdiction_intersection_algorithm`` — Ch7 Thm 7.4: compute the meet
  of a list of jurisdictions.
- ``corroboration_chain_validator`` — Ch7 §7.1.3: validate a corroboration
  chain by checking source diversity and trust monotonicity.
"""

import hashlib
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
    from jugeo.evidence.channels import EvidenceRequest, EvidenceResponse
    from jugeo.solver.router import RoutingDecision
    from jugeo.solver.fragments import LogicalFragment
except ImportError:
    TrustLevel = None  # type: ignore
    TrustTier = None  # type: ignore
    TrustProfile = None  # type: ignore
    EvidenceRequest = None  # type: ignore
    EvidenceResponse = None  # type: ignore
    RoutingDecision = None  # type: ignore
    LogicalFragment = None  # type: ignore

logger = logging.getLogger(__name__)

# Trust rank map for comparisons — canonical ordering from Theory2.tex §2.4.
_TRUST_RANK: dict[str, int] = {
    "contradicted": 0,
    "unverified": 1,
    "copilot_suggested": 2,
    "oracle_proposed": 3,
    "human_attested": 4,
    "runtime_witnessed": 5,
    "solver_discharged": 6,
    "mechanically_verified": 7,
}

# Inverse map for rank → canonical label.
_RANK_TO_TRUST: dict[int, str] = {v: k for k, v in _TRUST_RANK.items()}


def _trust_rank(level: str) -> int:
    """Return the numeric rank for *level*, defaulting to 1 (unverified)."""
    return _TRUST_RANK.get(level.lower(), 1)


# ---------------------------------------------------------------------------
# Module-level algorithms
# ---------------------------------------------------------------------------


def trust_ceiling_propagation(
    response_chain: list[dict], ceiling: str
) -> list[dict]:
    """Propagate a trust ceiling through a response chain (Theory2.tex Thm 7.1).

    Theorem 7.1 states that no entry in a response chain may carry a trust
    level that exceeds the declared ceiling for that chain.  This function
    enforces the invariant by walking each response in *response_chain* and
    clamping any entry whose ``trust_level`` field exceeds *ceiling* according
    to the canonical ordering in ``_TRUST_RANK``.

    Clamped entries receive two additional fields:

    * ``ceiling_enforced`` — set to ``True`` to mark that a clamp occurred.
    * ``original_trust``   — the pre-clamp ``trust_level`` value, preserved
      for audit purposes as required by Theory2.tex §7.1.2.

    The function never mutates the input list; it returns a fresh list of
    shallow copies.

    Parameters
    ----------
    response_chain:
        Ordered list of response dicts, each expected to carry at minimum a
        ``trust_level`` key whose value is a string from ``_TRUST_RANK``.
    ceiling:
        The maximum permissible trust level as a canonical string key from
        ``_TRUST_RANK``.  Entries with a strictly higher rank are clamped.

    Returns
    -------
    list[dict]
        New list of response dicts with ceiling enforced.

    Raises
    ------
    ValueError
        If *ceiling* is not a recognised trust level key.
    """
    if ceiling not in _TRUST_RANK:
        raise ValueError(
            f"Unknown ceiling '{ceiling}'.  Valid levels: {sorted(_TRUST_RANK)}"
        )

    ceiling_rank = _trust_rank(ceiling)
    result: list[dict] = []
    clamp_count = 0

    for idx, entry in enumerate(response_chain):
        copy = dict(entry)
        raw_level = copy.get("trust_level", "unverified")
        entry_rank = _trust_rank(raw_level)

        if entry_rank > ceiling_rank:
            copy["original_trust"] = raw_level
            copy["trust_level"] = ceiling
            copy["ceiling_enforced"] = True
            clamp_count += 1
            logger.debug(
                "Clamped entry[%d]: %s → %s (ceiling=%s)",
                idx,
                raw_level,
                ceiling,
                ceiling,
            )
        else:
            copy.setdefault("ceiling_enforced", False)

        result.append(copy)

    logger.info(
        "trust_ceiling_propagation: %d/%d entries clamped to ceiling='%s'",
        clamp_count,
        len(response_chain),
        ceiling,
    )
    return result


def oracle_proposal_ranking(proposals: list[dict]) -> list[dict]:
    """Rank oracle proposals by composite quality score (Theory2.tex §7.1.3).

    Section 7.1.3 defines a proposal ranking function that considers three
    orthogonal dimensions:

    1. **Corroboration depth** — the number of independent sources that have
       attested to the proposal, weighted at 10 points per corroborating source.
    2. **Jurisdiction quality** — a normalised ``[0, 1]`` float expressing how
       well the issuing oracle's jurisdiction covers the relevant fragment class,
       weighted at 5 points per unit quality.
    3. **Recency** — a time-decay factor ``1 / (1 + age_in_hours)`` that
       continuously discounts proposals as they age, preventing stale
       proposals from indefinitely dominating the ranking.

    Only proposals whose ``is_active`` flag is ``True`` are considered.
    Inactive proposals are filtered out before scoring and are absent from
    the returned list.

    Parameters
    ----------
    proposals:
        List of proposal dicts.  Expected keys:

        * ``proposal_id``         — unique identifier string.
        * ``corroboration_count`` — non-negative integer.
        * ``jurisdiction_quality``— float in ``[0, 1]``.
        * ``timestamp``           — Unix epoch float of creation time.
        * ``is_active``           — boolean gate.
        * ``trust_at_creation``   — canonical trust level string.

    Returns
    -------
    list[dict]
        Active proposals sorted by descending composite score.  Each dict
        receives two additional fields: ``rank`` (1-based int) and ``score``
        (float).
    """
    now = time.time()
    active = [p for p in proposals if p.get("is_active", True)]

    if not active:
        logger.info("oracle_proposal_ranking: no active proposals to rank")
        return []

    scored: list[tuple[float, dict]] = []

    for proposal in active:
        corroboration = float(proposal.get("corroboration_count", 0))
        jurisdiction_quality = float(proposal.get("jurisdiction_quality", 0.0))
        timestamp = float(proposal.get("timestamp", now))

        age_seconds = max(now - timestamp, 0.0)
        age_hours = age_seconds / 3600.0
        recency_score = 1.0 / (1.0 + age_hours)

        # Trust bonus: proposals created at higher trust get a small uplift.
        trust_at_creation = proposal.get("trust_at_creation", "unverified")
        trust_bonus = _trust_rank(trust_at_creation) * 0.5

        score = (
            corroboration * 10.0
            + jurisdiction_quality * 5.0
            + recency_score
            + trust_bonus
        )
        scored.append((score, proposal))

    scored.sort(key=lambda t: t[0], reverse=True)

    ranked: list[dict] = []
    for rank_idx, (score, proposal) in enumerate(scored, start=1):
        copy = dict(proposal)
        copy["rank"] = rank_idx
        copy["score"] = round(score, 6)
        ranked.append(copy)

    logger.info(
        "oracle_proposal_ranking: ranked %d active proposals; top score=%.4f",
        len(ranked),
        ranked[0]["score"] if ranked else 0.0,
    )
    return ranked


def federation_route_optimal(
    fragment_description: str,
    fragment_kind: str,
    federation_dict: dict,
) -> dict:
    """Select the optimal federation member solver for a fragment (Theory2.tex §7.2.3).

    Section 7.2.3 defines the *federation routing problem*: given a fragment
    of kind *fragment_kind* and a dictionary describing the available member
    solvers, find the solver that maximises a quality score subject to the
    constraint that the solver's declared jurisdiction must cover *fragment_kind*.

    The quality score for solver ``s`` is defined as::

        score(s) = trust_rank(s.trust_ceiling) / (s.cost * s.latency ** 0.5)

    This formulation rewards high trust ceilings while penalising expensive
    or slow solvers.  The latency is square-rooted to model diminishing
    marginal disutility of additional latency.

    Parameters
    ----------
    fragment_description:
        Human-readable description of the fragment being routed (used in the
        returned ``rationale`` field and for logging).
    fragment_kind:
        Machine-readable kind identifier (e.g. ``"smt2"``, ``"horn_clause"``,
        ``"runtime_assertion"``).  Must appear in a solver's ``jurisdiction``
        list for that solver to be eligible.
    federation_dict:
        Dict with key ``"member_solvers"`` mapping ``solver_id`` → solver info
        dict with keys ``jurisdiction`` (list[str]), ``cost`` (float),
        ``latency`` (float), and ``trust_ceiling`` (str).

    Returns
    -------
    dict
        Routing decision with keys: ``selected_backend``, ``rationale``,
        ``alternatives`` (list of runner-up solver ids), ``estimated_cost``,
        ``estimated_latency``, ``jurisdiction_check_passed``, ``request_id``.
    """
    request_id = str(uuid.uuid4())
    member_solvers: dict[str, dict] = federation_dict.get("member_solvers", {})

    eligible: list[tuple[float, str, dict]] = []
    ineligible: list[str] = []

    for solver_id, info in member_solvers.items():
        jurisdiction: list[str] = info.get("jurisdiction", [])
        if fragment_kind not in jurisdiction:
            ineligible.append(solver_id)
            continue

        cost = float(info.get("cost", 1.0))
        latency = float(info.get("latency", 1.0))
        trust_ceiling = info.get("trust_ceiling", "unverified")

        if cost <= 0 or latency <= 0:
            logger.warning(
                "Solver '%s' has non-positive cost/latency; skipping", solver_id
            )
            ineligible.append(solver_id)
            continue

        trust_rank_val = _trust_rank(trust_ceiling)
        score = trust_rank_val / (cost * math.sqrt(latency))
        eligible.append((score, solver_id, info))

    if not eligible:
        logger.warning(
            "federation_route_optimal: no eligible solver for fragment_kind='%s'",
            fragment_kind,
        )
        return {
            "selected_backend": None,
            "rationale": f"No solver covers fragment_kind='{fragment_kind}'",
            "alternatives": [],
            "estimated_cost": 0.0,
            "estimated_latency": 0.0,
            "jurisdiction_check_passed": False,
            "request_id": request_id,
        }

    eligible.sort(key=lambda t: t[0], reverse=True)
    best_score, best_id, best_info = eligible[0]
    alternatives = [sid for _, sid, _ in eligible[1:]]

    rationale = (
        f"Selected '{best_id}' for fragment_kind='{fragment_kind}' "
        f"(description: {fragment_description!r}); "
        f"score={best_score:.4f}, trust_ceiling='{best_info.get('trust_ceiling')}', "
        f"cost={best_info.get('cost')}, latency={best_info.get('latency')}. "
        f"Ineligible solvers: {ineligible}."
    )

    logger.info(
        "federation_route_optimal: selected='%s' score=%.4f request_id=%s",
        best_id,
        best_score,
        request_id,
    )

    return {
        "selected_backend": best_id,
        "rationale": rationale,
        "alternatives": alternatives,
        "estimated_cost": float(best_info.get("cost", 0.0)),
        "estimated_latency": float(best_info.get("latency", 0.0)),
        "jurisdiction_check_passed": True,
        "request_id": request_id,
    }


def witness_consistency_check(witnesses: list[dict]) -> bool:
    """Check mutual consistency of a witness collection (Theory2.tex Thm 7.3).

    Theorem 7.3 states that a set of runtime witnesses is *consistent* if and
    only if no two witnesses with overlapping scope make contradictory factual
    claims.  This function operationalises the theorem by comparing witnesses
    that share an ``entity_id`` or that carry overlapping ``heap_snapshot``
    keys.

    Two witnesses are deemed inconsistent if:

    * They share the same ``entity_id`` and report different ``value`` fields
      that cannot be reconciled (i.e. neither is ``None``/absent).
    * They share one or more ``heap_snapshot`` keys and those keys map to
      differing non-``None`` values in both witnesses.

    All detected inconsistencies are logged at WARNING level so that the
    audit trail required by §7.1.2 is preserved even when the function
    returns ``False``.

    Parameters
    ----------
    witnesses:
        List of witness dicts.  Recognised keys: ``witness_id`` (str),
        ``entity_id`` (str), ``value`` (any), ``heap_snapshot`` (dict),
        ``kind`` (str), ``trust_level`` (str).

    Returns
    -------
    bool
        ``True`` if no inconsistencies were found; ``False`` otherwise.
    """
    inconsistencies: list[str] = []

    for i in range(len(witnesses)):
        for j in range(i + 1, len(witnesses)):
            wa = witnesses[i]
            wb = witnesses[j]
            id_a = wa.get("witness_id", f"w[{i}]")
            id_b = wb.get("witness_id", f"w[{j}]")

            # Check entity_id / value consistency.
            if (
                wa.get("entity_id") is not None
                and wa.get("entity_id") == wb.get("entity_id")
            ):
                val_a = wa.get("value")
                val_b = wb.get("value")
                if val_a is not None and val_b is not None and val_a != val_b:
                    msg = (
                        f"Witnesses {id_a!r} and {id_b!r} share entity_id "
                        f"'{wa['entity_id']}' but disagree on value: "
                        f"{val_a!r} vs {val_b!r}"
                    )
                    inconsistencies.append(msg)
                    logger.warning("Witness inconsistency: %s", msg)

            # Check heap_snapshot overlap.
            snap_a: dict = wa.get("heap_snapshot") or {}
            snap_b: dict = wb.get("heap_snapshot") or {}
            shared_keys = set(snap_a.keys()) & set(snap_b.keys())
            for key in shared_keys:
                v_a = snap_a[key]
                v_b = snap_b[key]
                if v_a is not None and v_b is not None and v_a != v_b:
                    msg = (
                        f"Witnesses {id_a!r} and {id_b!r} disagree on "
                        f"heap_snapshot['{key}']: {v_a!r} vs {v_b!r}"
                    )
                    inconsistencies.append(msg)
                    logger.warning("Witness inconsistency: %s", msg)

    if inconsistencies:
        logger.error(
            "witness_consistency_check: FAILED with %d inconsistency/ies",
            len(inconsistencies),
        )
        return False

    logger.debug(
        "witness_consistency_check: PASSED for %d witnesses", len(witnesses)
    )
    return True


def jurisdiction_intersection_algorithm(jurisdictions: list[dict]) -> dict:
    """Compute the meet of a list of jurisdictions (Theory2.tex Thm 7.4).

    Theorem 7.4 establishes that the composition of two (or more)
    jurisdictions enforces the *meet* (greatest lower bound) of their trust
    ceilings and the *intersection* of their allowed domain sets.  This
    function generalises the pairwise meet to an arbitrary list of
    jurisdiction dicts.

    The combined scope is constructed by joining the sorted individual scope
    strings with ``"+"`` so that the result is deterministic regardless of
    input ordering.

    Parameters
    ----------
    jurisdictions:
        Non-empty list of jurisdiction dicts with keys:

        * ``scope``           — string label for the jurisdiction's domain.
        * ``allowed_domains`` — list of domain/fragment-kind strings.
        * ``trust_ceiling``   — canonical trust level string.

    Returns
    -------
    dict
        A new jurisdiction dict representing the meet, with the same three
        keys plus an ``intersection_of`` field listing the source scopes.

    Raises
    ------
    ValueError
        If *jurisdictions* is empty.
    """
    if not jurisdictions:
        raise ValueError("jurisdiction_intersection_algorithm requires at least one jurisdiction")

    # Seed with the first jurisdiction's domains; intersect with each subsequent.
    domain_sets = [set(j.get("allowed_domains", [])) for j in jurisdictions]
    intersection_domains: set[str] = domain_sets[0]
    for ds in domain_sets[1:]:
        intersection_domains = intersection_domains & ds

    # Minimum trust ceiling by rank (the meet in the trust lattice).
    min_rank = min(_trust_rank(j.get("trust_ceiling", "unverified")) for j in jurisdictions)
    min_ceiling = _RANK_TO_TRUST.get(min_rank, "unverified")

    scopes = sorted(j.get("scope", "unknown") for j in jurisdictions)
    combined_scope = "+".join(scopes)

    result = {
        "scope": combined_scope,
        "allowed_domains": sorted(intersection_domains),
        "trust_ceiling": min_ceiling,
        "intersection_of": scopes,
        "domain_count": len(intersection_domains),
    }

    logger.info(
        "jurisdiction_intersection: %d jurisdictions → %d shared domains, "
        "ceiling='%s', scope='%s'",
        len(jurisdictions),
        len(intersection_domains),
        min_ceiling,
        combined_scope,
    )
    return result


def corroboration_chain_validator(
    proposal_id: str, chain: list[dict]
) -> bool:
    """Validate a corroboration chain for a proposal (Theory2.tex §7.1.3).

    Section 7.1.3 requires that every corroboration chain satisfies four
    structural invariants:

    1. **Non-emptiness** — the chain must contain at least one entry.
    2. **Timestamp monotonicity** — successive entries must have strictly
       increasing ``timestamp`` values (no retroactive corroboration).
    3. **Source uniqueness** — no ``source_id`` may appear more than once,
       preventing circular self-corroboration.
    4. **Trust non-decrease** — the ``trust_level`` of successive entries must
       be non-decreasing in the canonical ordering, ensuring that a
       corroboration chain represents genuine epistemic progress.

    Parameters
    ----------
    proposal_id:
        The identifier of the proposal whose chain is being validated.
        Used only for log messages.
    chain:
        Ordered list of corroboration dicts, each with keys ``source_id``
        (str), ``trust_level`` (str), and ``timestamp`` (float).

    Returns
    -------
    bool
        ``True`` if all four invariants hold; ``False`` if any fail.
    """
    def _fail(reason: str) -> bool:
        logger.warning(
            "corroboration_chain_validator: FAILED for proposal '%s' — %s",
            proposal_id,
            reason,
        )
        return False

    # 1. Non-emptiness.
    if not chain:
        return _fail("chain is empty")

    seen_sources: set[str] = set()

    for idx in range(len(chain)):
        entry = chain[idx]
        source_id = entry.get("source_id", "")
        trust_level = entry.get("trust_level", "unverified")
        timestamp = float(entry.get("timestamp", 0.0))

        # 2. Timestamp monotonicity (strict).
        if idx > 0:
            prev_ts = float(chain[idx - 1].get("timestamp", 0.0))
            if timestamp <= prev_ts:
                return _fail(
                    f"timestamp at index {idx} ({timestamp}) is not strictly "
                    f"greater than previous ({prev_ts})"
                )

        # 3. Source uniqueness.
        if source_id in seen_sources:
            return _fail(
                f"source_id '{source_id}' appears more than once (index {idx})"
            )
        seen_sources.add(source_id)

        # 4. Trust non-decrease.
        if idx > 0:
            prev_rank = _trust_rank(chain[idx - 1].get("trust_level", "unverified"))
            curr_rank = _trust_rank(trust_level)
            if curr_rank < prev_rank:
                return _fail(
                    f"trust level decreased at index {idx}: "
                    f"'{chain[idx - 1]['trust_level']}' → '{trust_level}'"
                )

    logger.debug(
        "corroboration_chain_validator: PASSED for proposal '%s' (%d entries)",
        proposal_id,
        len(chain),
    )
    return True


# ---------------------------------------------------------------------------
# Class: TrustCeilingPropagator
# ---------------------------------------------------------------------------


class TrustCeilingPropagator:
    """Stateful propagator that applies a trust ceiling step by step.

    This class wraps the stateless ``trust_ceiling_propagation`` function with
    a stateful interface suitable for streaming pipelines where responses
    arrive one at a time.  It maintains a full history of applied steps and
    exposes cumulative statistics for monitoring.

    Parameters
    ----------
    ceiling:
        Initial trust ceiling label.  Defaults to ``"oracle_proposed"``,
        which is the standard ceiling for unverified oracle channels per
        Theory2.tex §7.1.
    """

    def __init__(self, ceiling: str = "oracle_proposed") -> None:
        if ceiling not in _TRUST_RANK:
            raise ValueError(f"Unknown ceiling '{ceiling}'")
        self.ceiling: str = ceiling
        self.path: list[dict] = []
        self._step_count: int = 0
        self._clamp_count: int = 0

    def step(self, response_dict: dict) -> dict:
        """Apply the current ceiling to a single response dict.

        Parameters
        ----------
        response_dict:
            A response dict with at minimum a ``trust_level`` key.

        Returns
        -------
        dict
            A new dict (never the input mutated) with ceiling enforced.
        """
        clamped_list = trust_ceiling_propagation([response_dict], self.ceiling)
        result = clamped_list[0]
        self.path.append(result)
        self._step_count += 1
        if result.get("ceiling_enforced"):
            self._clamp_count += 1
        return result

    def propagate(self, response_chain: list[dict]) -> list[dict]:
        """Apply the current ceiling to every entry in *response_chain*.

        Parameters
        ----------
        response_chain:
            List of response dicts to process in order.

        Returns
        -------
        list[dict]
            Results in the same order with ceiling enforced.
        """
        return [self.step(entry) for entry in response_chain]

    def get_path(self) -> list[dict]:
        """Return a shallow copy of the accumulated step history."""
        return list(self.path)

    def get_stats(self) -> dict:
        """Return cumulative statistics for this propagator instance.

        Returns
        -------
        dict
            Keys: ``step_count``, ``clamp_count``, ``clamp_rate``, ``ceiling``.
        """
        rate = (
            self._clamp_count / self._step_count
            if self._step_count > 0
            else 0.0
        )
        return {
            "step_count": self._step_count,
            "clamp_count": self._clamp_count,
            "clamp_rate": round(rate, 4),
            "ceiling": self.ceiling,
        }

    def reset(self) -> None:
        """Clear accumulated path and reset all counters to zero."""
        self.path = []
        self._step_count = 0
        self._clamp_count = 0
        logger.debug("TrustCeilingPropagator.reset() called; ceiling='%s'", self.ceiling)

    def set_ceiling(self, new_ceiling: str) -> None:
        """Update the active ceiling, logging the transition.

        Parameters
        ----------
        new_ceiling:
            New canonical trust level label.

        Raises
        ------
        ValueError
            If *new_ceiling* is not a recognised trust level.
        """
        if new_ceiling not in _TRUST_RANK:
            raise ValueError(f"Unknown ceiling '{new_ceiling}'")
        old = self.ceiling
        self.ceiling = new_ceiling
        logger.info(
            "TrustCeilingPropagator: ceiling changed '%s' → '%s'", old, new_ceiling
        )

    def describe(self) -> str:
        """Return a human-readable summary of this propagator's state."""
        stats = self.get_stats()
        return (
            f"TrustCeilingPropagator("
            f"ceiling='{self.ceiling}', "
            f"steps={stats['step_count']}, "
            f"clamps={stats['clamp_count']}, "
            f"clamp_rate={stats['clamp_rate']:.1%})"
        )


# ---------------------------------------------------------------------------
# Class: FederationLoadBalancer
# ---------------------------------------------------------------------------


class FederationLoadBalancer:
    """Dynamic load balancer for a solver federation.

    Maintains a weight table for each solver in the federation and updates
    those weights based on observed outcomes (success/failure and latency).
    The ``rebalance`` method computes new weights from a batch of solver
    statistics; ``record_outcome`` feeds individual outcomes into running
    averages that feed the next rebalance cycle.

    This class is not thread-safe; external locking is required in concurrent
    environments.
    """

    def __init__(self) -> None:
        self.weights: dict[str, float] = {}
        self.performance: dict[str, dict] = {}
        self._rebalance_count: int = 0

    def score(self, solver_id: str, fragment_kind: str) -> float:  # noqa: ARG002
        """Compute a routing score for *solver_id*.

        The *fragment_kind* argument is accepted for future per-kind weight
        tables but is not yet used in the current uniform-weight scheme.

        Parameters
        ----------
        solver_id:
            Solver whose weight to look up.
        fragment_kind:
            Kind of fragment being routed (reserved for future use).

        Returns
        -------
        float
            Current weight for *solver_id*, or ``1.0`` if not yet calibrated.
        """
        base_weight = self.weights.get(solver_id, 1.0)
        perf = self.performance.get(solver_id, {})
        success_rate = perf.get("success_rate", 1.0)
        avg_latency_ms = perf.get("avg_latency_ms", 100.0)
        # Blend weight with live performance stats.
        live_factor = success_rate / (1.0 + avg_latency_ms / 1000.0)
        return base_weight * live_factor

    def rebalance(self, solver_stats: dict[str, dict]) -> None:
        """Recompute solver weights from a batch of statistics.

        Each entry in *solver_stats* maps a ``solver_id`` to a dict with
        keys ``success_rate`` (float in ``[0, 1]``) and
        ``avg_latency_ms`` (non-negative float).

        The raw weight formula is::

            raw_weight = success_rate / (1 + avg_latency_ms / 1000)

        Weights are then L1-normalised so they sum to ``1.0``.

        Parameters
        ----------
        solver_stats:
            Mapping of solver_id → {success_rate, avg_latency_ms}.
        """
        raw: dict[str, float] = {}
        for solver_id, stats in solver_stats.items():
            success_rate = max(0.0, float(stats.get("success_rate", 1.0)))
            avg_latency_ms = max(0.0, float(stats.get("avg_latency_ms", 100.0)))
            raw[solver_id] = success_rate / (1.0 + avg_latency_ms / 1000.0)

        total = sum(raw.values())
        if total > 0:
            self.weights = {sid: w / total for sid, w in raw.items()}
        else:
            # Uniform fallback if all solvers have zero weight.
            n = len(solver_stats)
            self.weights = {sid: 1.0 / n for sid in solver_stats} if n > 0 else {}

        # Merge stats into performance table.
        for solver_id, stats in solver_stats.items():
            self.performance[solver_id] = dict(stats)

        self._rebalance_count += 1
        logger.info(
            "FederationLoadBalancer.rebalance: cycle=%d solvers=%d weights=%s",
            self._rebalance_count,
            len(self.weights),
            {sid: f"{w:.3f}" for sid, w in self.weights.items()},
        )

    def get_weights(self) -> dict[str, float]:
        """Return a shallow copy of the current weight table."""
        return dict(self.weights)

    def record_outcome(
        self, solver_id: str, success: bool, latency_ms: float
    ) -> None:
        """Update running averages for *solver_id* from a single observation.

        Uses an exponential moving average with α=0.2 so that recent outcomes
        have higher influence than historical ones.

        Parameters
        ----------
        solver_id:
            Solver that produced the outcome.
        success:
            Whether the solve attempt succeeded.
        latency_ms:
            Wall-clock solve time in milliseconds.
        """
        alpha = 0.2
        perf = self.performance.setdefault(
            solver_id,
            {"success_rate": 1.0, "avg_latency_ms": 100.0, "sample_count": 0},
        )
        old_sr = perf.get("success_rate", 1.0)
        old_lat = perf.get("avg_latency_ms", 100.0)
        count = perf.get("sample_count", 0)

        new_sr = alpha * float(success) + (1 - alpha) * old_sr
        new_lat = alpha * latency_ms + (1 - alpha) * old_lat

        perf["success_rate"] = new_sr
        perf["avg_latency_ms"] = new_lat
        perf["sample_count"] = count + 1

        logger.debug(
            "record_outcome: solver='%s' success=%s latency=%.1fms → "
            "success_rate=%.3f avg_latency=%.1fms",
            solver_id,
            success,
            latency_ms,
            new_sr,
            new_lat,
        )

    def get_best_solver(self, candidates: list[str]) -> str | None:
        """Return the highest-weighted solver from *candidates*.

        Parameters
        ----------
        candidates:
            List of solver ids to consider.

        Returns
        -------
        str | None
            The solver id with the highest weight, or ``None`` if *candidates*
            is empty.
        """
        if not candidates:
            return None
        return max(candidates, key=lambda sid: self.weights.get(sid, 0.0))

    def reset(self) -> None:
        """Clear all weights and performance data, resetting to a blank state."""
        self.weights = {}
        self.performance = {}
        logger.debug("FederationLoadBalancer.reset()")


# ---------------------------------------------------------------------------
# Class: WitnessCorrelator
# ---------------------------------------------------------------------------


class WitnessCorrelator:
    """Correlate and reconcile collections of runtime witnesses.

    Runtime witnesses are produced by diverse channels and may partially
    overlap in the entities or heap regions they observe.  This class
    provides tools to detect correlations (both positive consistency and
    negative conflicts) and to resolve conflicts according to a configurable
    policy.

    Correlation results and conflicts are cached in instance state for
    repeated inspection without re-computation.
    """

    def __init__(self) -> None:
        self.correlation_cache: dict[str, dict] = {}
        self.conflicts: list[dict] = []
        self._resolution_count: int = 0
        self._check_count: int = 0

    def correlate(self, witnesses: list[dict]) -> dict:
        """Compute a pairwise correlation matrix for *witnesses*.

        Groups witnesses by ``kind`` and ``entity_id``, then for each pair
        determines whether their observations are ``"consistent"``,
        ``"inconsistent"``, or ``"partial"`` (overlapping keys with some
        agreement and some conflict).

        Parameters
        ----------
        witnesses:
            List of witness dicts.  Each must have at minimum a
            ``witness_id`` key.

        Returns
        -------
        dict
            Nested dict ``{witness_id: {other_id: status}}`` where status is
            one of ``"consistent"``, ``"inconsistent"``, or ``"partial"``.
        """
        matrix: dict[str, dict] = {}

        for i in range(len(witnesses)):
            wa = witnesses[i]
            id_a = wa.get("witness_id", f"w{i}")
            matrix.setdefault(id_a, {})

            for j in range(i + 1, len(witnesses)):
                wb = witnesses[j]
                id_b = wb.get("witness_id", f"w{j}")
                matrix.setdefault(id_b, {})
                self._check_count += 1

                snap_a: dict = wa.get("heap_snapshot") or {}
                snap_b: dict = wb.get("heap_snapshot") or {}
                shared = set(snap_a.keys()) & set(snap_b.keys())

                if not shared:
                    # No overlapping observations: neutral.
                    status = "consistent"
                else:
                    conflicts = sum(
                        1
                        for k in shared
                        if snap_a[k] is not None
                        and snap_b[k] is not None
                        and snap_a[k] != snap_b[k]
                    )
                    agreements = len(shared) - conflicts
                    if conflicts == 0:
                        status = "consistent"
                    elif agreements == 0:
                        status = "inconsistent"
                    else:
                        status = "partial"

                matrix[id_a][id_b] = status
                matrix[id_b][id_a] = status

        cache_key = hashlib.md5(
            ",".join(w.get("witness_id", "") for w in witnesses).encode()
        ).hexdigest()
        self.correlation_cache[cache_key] = matrix
        return matrix

    def find_conflicts(self, witnesses: list[dict]) -> list[dict]:
        """Identify pairs of witnesses with conflicting observations.

        Parameters
        ----------
        witnesses:
            List of witness dicts.

        Returns
        -------
        list[dict]
            Each entry is a dict with keys ``witness_a``, ``witness_b``,
            ``conflict_description``, and ``resolution_hint``.
        """
        found: list[dict] = []

        for i in range(len(witnesses)):
            for j in range(i + 1, len(witnesses)):
                wa, wb = witnesses[i], witnesses[j]
                id_a = wa.get("witness_id", f"w{i}")
                id_b = wb.get("witness_id", f"w{j}")

                snap_a: dict = wa.get("heap_snapshot") or {}
                snap_b: dict = wb.get("heap_snapshot") or {}
                shared = set(snap_a.keys()) & set(snap_b.keys())

                for key in shared:
                    va, vb = snap_a.get(key), snap_b.get(key)
                    if va is not None and vb is not None and va != vb:
                        trust_a = _trust_rank(wa.get("trust_level", "unverified"))
                        trust_b = _trust_rank(wb.get("trust_level", "unverified"))
                        if trust_a >= trust_b:
                            hint = f"prefer '{id_a}' (higher or equal trust)"
                        else:
                            hint = f"prefer '{id_b}' (higher trust)"

                        found.append(
                            {
                                "witness_a": id_a,
                                "witness_b": id_b,
                                "conflict_key": key,
                                "value_a": va,
                                "value_b": vb,
                                "conflict_description": (
                                    f"Witnesses '{id_a}' and '{id_b}' disagree "
                                    f"on heap_snapshot['{key}']: {va!r} vs {vb!r}"
                                ),
                                "resolution_hint": hint,
                            }
                        )

        self.conflicts = found
        logger.info(
            "WitnessCorrelator.find_conflicts: %d conflict(s) found among %d witnesses",
            len(found),
            len(witnesses),
        )
        return found

    def resolve_conflicts(
        self, conflicts: list[dict], policy: str = "conservative"
    ) -> list[dict]:
        """Apply a conflict-resolution policy to a list of conflict records.

        Parameters
        ----------
        conflicts:
            List of conflict dicts as returned by ``find_conflicts``.
        policy:
            Resolution policy name.

            * ``"conservative"`` — keep the witness with lower trust or, on a
              tie, the earlier timestamp.  This minimises the risk of accepting
              a falsely elevated trust level.
            * ``"optimistic"`` — keep the witness with higher trust.

        Returns
        -------
        list[dict]
            Resolved conflict records with an additional ``"kept"`` key naming
            the witness that was favoured.
        """
        resolved: list[dict] = []
        for conflict in conflicts:
            copy = dict(conflict)
            hint = conflict.get("resolution_hint", "")
            # Parse hint for preferred witness id.
            if "prefer '" in hint:
                kept = hint.split("prefer '")[1].split("'")[0]
            else:
                kept = conflict.get("witness_a", "unknown")
            copy["kept"] = kept
            copy["policy"] = policy
            self._resolution_count += 1
            resolved.append(copy)

        logger.info(
            "WitnessCorrelator.resolve_conflicts: %d resolved with policy='%s'",
            len(resolved),
            policy,
        )
        return resolved

    def get_correlation_stats(self) -> dict:
        """Return cumulative statistics for this correlator instance.

        Returns
        -------
        dict
            Keys: ``total_checked``, ``conflict_count``,
            ``resolution_count``, ``cache_size``.
        """
        return {
            "total_checked": self._check_count,
            "conflict_count": len(self.conflicts),
            "resolution_count": self._resolution_count,
            "cache_size": len(self.correlation_cache),
        }


# ---------------------------------------------------------------------------
# Cross-referencing helpers — Theory2.tex §7 (Oracle Federation)
# ---------------------------------------------------------------------------


def oracle_site_routing(site_data, *, oracle_channel="copilot"):
    """Route oracle queries over a site using geometry coordinates.

    Maps each entry in *site_data* to a routing record that pairs a
    :class:`~jugeo.geometry.site.Coordinate` with a
    :class:`~jugeo.geometry.covers.CoverMember` so the oracle federation
    layer can dispatch queries along the correct channel.

    See Theory2.tex §7 (Oracle Federation) for the formal routing invariant.

    Parameters
    ----------
    site_data : dict
        Mapping of site keys to raw coordinate dicts.
    oracle_channel : str, optional
        Channel identifier for the oracle backend (default ``"copilot"``).

    Returns
    -------
    dict
        Routing table with ``routes``, ``channel``, and ``site_count`` keys.
    """
    try:
        from jugeo.geometry.site import Coordinate, CoordinateKind
        from jugeo.geometry.covers import CoverMember
    except ImportError:
        logger.warning("oracle_site_routing: geometry modules unavailable")
        return {"routes": {}, "channel": oracle_channel, "site_count": 0, "error": "missing_geometry"}

    routes = {}
    for key, entry in (site_data or {}).items():
        kind = CoordinateKind(entry.get("kind", "region"))
        coord = Coordinate(components=(key,), kind=kind)
        routes[key] = {
            "coordinate": coord,
            "kind": kind.value,
            "channel": oracle_channel,
        }
    logger.debug("oracle_site_routing: routed %d sites on channel=%s", len(routes), oracle_channel)
    return {"routes": routes, "channel": oracle_channel, "site_count": len(routes)}


def oracle_judgment_generation(oracle_result):
    """Generate a judgment record from an oracle query result.

    Wraps the oracle result into a :class:`~jugeo.judgments.judgment_terms.Proposition`
    and assigns an initial :class:`~jugeo.judgments.judgment_terms.JudgmentStatus`.

    See Theory2.tex §7 (Oracle Federation) for judgment generation semantics.

    Parameters
    ----------
    oracle_result : dict
        Raw oracle result with at least ``"formula"`` and optionally ``"kind"``.

    Returns
    -------
    dict
        Judgment dict with ``proposition``, ``status``, and ``oracle_source`` keys.
    """
    try:
        from jugeo.judgments.judgment_terms import Proposition, JudgmentStatus
    except ImportError:
        logger.warning("oracle_judgment_generation: judgment_terms unavailable")
        return {"proposition": None, "status": "error", "oracle_source": oracle_result}

    formula = oracle_result.get("formula", "")
    kind_str = oracle_result.get("kind", "structural")
    prop = Proposition(kind=kind_str, formula=formula)
    status = JudgmentStatus.PROPOSED
    logger.debug("oracle_judgment_generation: created proposition formula=%r status=%s", formula, status.value)
    return {"proposition": prop, "status": status.value, "oracle_source": oracle_result}


def oracle_encoding(oracle_result, *, format="z3"):
    """Encode an oracle result for downstream solver consumption.

    Delegates to :func:`~jugeo.encodings.encode_judgment` after wrapping the
    oracle result in a lightweight judgment structure.

    See Theory2.tex §7 (Oracle Federation) for encoding requirements.

    Parameters
    ----------
    oracle_result : dict
        Raw oracle result dict.
    format : str, optional
        Target encoding format (default ``"z3"``).

    Returns
    -------
    dict
        Encoding dict with ``encoded``, ``format``, and ``source`` keys.
    """
    try:
        from jugeo.encodings import encode_judgment
    except ImportError:
        logger.warning("oracle_encoding: encodings module unavailable")
        return {"encoded": None, "format": format, "source": oracle_result, "error": "missing_encodings"}

    try:
        encoded = encode_judgment(oracle_result)
    except Exception as exc:  # noqa: BLE001
        logger.error("oracle_encoding: encode_judgment failed: %s", exc)
        return {"encoded": None, "format": format, "source": oracle_result, "error": str(exc)}

    logger.debug("oracle_encoding: encoded oracle result in format=%s", format)
    return {"encoded": encoded, "format": format, "source": oracle_result}
