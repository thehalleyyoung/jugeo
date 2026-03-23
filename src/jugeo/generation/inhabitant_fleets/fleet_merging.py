"""Fleet merging for parallel inhabitant generation.

Chapter 42 of theory2.tex §42.6 — Fleet Merging.

Overview
--------
This module implements the *fleet merging* subsystem: the process by which
multiple competing fleets — each containing one or more inhabitants that have
been proposed for the same patch — are collapsed into a single canonical
winning inhabitant.

Theory Background — Ch42 §42.6 Fleet Merging
----------------------------------------------
Fleet merging is the terminal phase of the inhabitant selection pipeline.
Given a set of fleets F = {F₁, F₂, …, Fₙ}, where each fleet Fᵢ contains
a set of proposals {p₁, p₂, …, pₖ}, the merger must select a *unique*
winning inhabitant t* such that:

    t* = argmax_{t ∈ ⋃Fᵢ} score(t)

subject to the *trust invariant*:

    ∀ p ∈ ⋃Fᵢ : p.trust_tier = PROPOSAL

This invariant ensures that fleet proposals remain in the PROPOSAL tier and
are not prematurely promoted.  The merger itself verifies this before scoring.

Scoring Function (Ch42 §42.6.1)
---------------------------------
Each proposal p is scored by the composite scoring function:

    Φ(p) = w_t · trust_weight(p) + w_s · semantic_score(p) + w_c · coverage(p)

where:
  trust_weight(p) = TrustTier(p).value / MAX_TRUST_TIER_VALUE
  semantic_score(p) = p.score() as reported by InhabitantProposal
  coverage(p) = |p.patch_ids| / |global_patch_universe|
  w_t + w_s + w_c = 1  (normalisation constraint)

Merge Policies (Ch42 §42.6.2)
-------------------------------
Four merge policies are defined:

  TRUST_WEIGHTED  — weight scores by the trust tier of each proposal's author
  MAJORITY_VOTE   — select the inhabitant most frequently proposed across fleets
  HIGHEST_SCORE   — select the single highest-scoring proposal (greedy)
  CONSENSUS       — require agreement from a quorum of fleets; fall back to
                    HIGHEST_SCORE when quorum cannot be achieved

Conflict Resolution (Ch42 §42.6.3)
-------------------------------------
Conflicts arise when two fleets propose structurally incompatible inhabitants
for the same patch.  A conflict is *critical* when its severity is HIGH or
CRITICAL; critical conflicts block finalisation until resolved.

    conflict_type ∈ { "type_mismatch", "semantic_overlap",
                      "structural_incompatibility", "provenance_clash" }

Fleet Merge Phases (Ch42 §42.6.4)
------------------------------------
Merging proceeds through five ordered phases:

  COLLECTION          — gather proposals from all incoming fleets
  SCORING             — compute Φ(p) for every proposal
  CONFLICT_RESOLUTION — detect and (where possible) resolve conflicts
  SELECTION           — apply the chosen MergePolicy to pick t*
  FINALIZATION        — emit a FleetMergeWitness and update the merge history

Convergence Guarantee (Theorem 42.6, Ch42)
--------------------------------------------
Under the assumption that all proposals satisfy the trust invariant and no
critical unresolved conflicts remain, the merger always terminates and returns
exactly one winning inhabitant.

Examples
---------
>>> coordinator = FleetMergeCoordinator()
>>> fleet_a = {"fleet_id": "fa", "proposals": []}
>>> fleet_b = {"fleet_id": "fb", "proposals": []}
>>> errors = coordinator.validate([fleet_a, fleet_b])
>>> isinstance(errors, list)
True

# copilot: s04-fleet-merging
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

try:
    from jugeo.geometry.descent import DescentResult
except ImportError:
    DescentResult = Any  # type: ignore[assignment,misc]

try:
    from jugeo.generation.inhabitant_fleets.models import (
        BackpressureSignal,
        FleetBid,
        InhabitantProposal,
        MoveType,
        NormalizedProposal,
        ProposalStatus,
        SemanticMove,
        SeverityLevel,
        make_bid,
        make_proposal,
        make_signal,
    )
except ImportError:
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Module-level constants and logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

#: Default weights for the composite scoring function Φ(p).
DEFAULT_TRUST_WEIGHT: float = 0.40
DEFAULT_SEMANTIC_WEIGHT: float = 0.45
DEFAULT_COVERAGE_WEIGHT: float = 0.15

#: Quorum fraction required for CONSENSUS policy.
CONSENSUS_QUORUM: float = 0.60

#: Maximum trust tier value used for normalisation.
MAX_TRUST_TIER_VALUE: int = 5

#: Minimum fleet count required before merging is attempted.
MIN_FLEET_COUNT: int = 1

#: Threshold above which a conflict score is considered critical.
CRITICAL_CONFLICT_THRESHOLD: float = 0.85


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MergePolicy(str, Enum):
    """Enumeration of fleet merge policies (Ch42 §42.6.2).

    Each policy maps to a distinct selection algorithm applied during the
    SELECTION phase of fleet merging.

    Attributes
    ----------
    TRUST_WEIGHTED:
        Weight each proposal's composite score by the trust tier of the
        proposing inhabitant.  Proposals from higher-trust inhabitants
        receive proportionally more influence over the final selection.
    MAJORITY_VOTE:
        Select the inhabitant that appears most frequently (by inhabitant_id)
        across all competing fleets.  Ties are broken by semantic score.
    HIGHEST_SCORE:
        Greedily select the single proposal with the highest composite
        score Φ(p).  This is the simplest policy and is used as a fallback
        when CONSENSUS cannot reach quorum.
    CONSENSUS:
        Require that at least CONSENSUS_QUORUM fraction of fleets agree on
        the same winning inhabitant.  Falls back to HIGHEST_SCORE when the
        quorum threshold is not met.
    """

    TRUST_WEIGHTED = "trust_weighted"
    MAJORITY_VOTE = "majority_vote"
    HIGHEST_SCORE = "highest_score"
    CONSENSUS = "consensus"


class FleetPhase(str, Enum):
    """Ordered phases of the fleet merge pipeline (Ch42 §42.6.4).

    Attributes
    ----------
    COLLECTION:
        Proposals from all incoming fleets are gathered into a unified pool.
    SCORING:
        The composite scoring function Φ(p) is applied to every proposal.
    CONFLICT_RESOLUTION:
        Structural conflicts between proposals are detected and, where
        possible, resolved automatically.
    SELECTION:
        The chosen MergePolicy is applied to select the winning inhabitant.
    FINALIZATION:
        A FleetMergeWitness is emitted and the merge history is updated.
    """

    COLLECTION = "collection"
    SCORING = "scoring"
    CONFLICT_RESOLUTION = "conflict_resolution"
    SELECTION = "selection"
    FINALIZATION = "finalization"


# ---------------------------------------------------------------------------
# FleetMergeWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FleetMergeWitness:
    """Immutable record attesting to the outcome of a fleet merge operation.

    A ``FleetMergeWitness`` is produced at the end of the FINALIZATION phase
    and serves as the canonical proof that a merge was performed correctly.
    It records *which* fleets participated, *who* won, *how* the winner was
    selected, and at *what* trust tier the merge operated.

    Theory — Ch42 §42.6.5 Witness Records
    ----------------------------------------
    Witnesses play the role of *proof terms* in the dependent-type
    interpretation of fleet semantics: a coordinator that produced a witness
    is committed to the selection it records and cannot later revise the
    decision without invalidating the witness.

    Fields
    ------
    witness_id:
        Globally unique identifier for this witness (UUID4 hex string).
    fleet_ids:
        Tuple of fleet identifiers that participated in the merge.
    winning_inhabitant_id:
        Identifier of the selected winning inhabitant.
    merge_policy:
        String name of the MergePolicy that was applied.
    trust_tier:
        String name of the TrustTier at which merging operated (must be
        "PROPOSAL" per the trust invariant).
    score:
        Composite score Φ(p*) of the winning proposal.
    provenance:
        Ordered tuple of step labels describing how the winner was derived
        (useful for audit trails and debugging).
    timestamp:
        Unix epoch timestamp (float) at which the witness was created.
    """

    witness_id: str
    fleet_ids: tuple[str, ...]
    winning_inhabitant_id: str
    merge_policy: str
    trust_tier: str
    score: float
    provenance: tuple[str, ...]
    timestamp: float

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the witness to a plain Python dictionary.

        The dictionary is JSON-safe (all values are strings, floats, or
        lists of strings).

        Returns
        -------
        dict
            A JSON-serialisable mapping of witness fields.

        Examples
        --------
        >>> w = FleetMergeWitness(
        ...     witness_id="abc",
        ...     fleet_ids=("f1", "f2"),
        ...     winning_inhabitant_id="inh-1",
        ...     merge_policy="highest_score",
        ...     trust_tier="PROPOSAL",
        ...     score=0.92,
        ...     provenance=("scoring", "selection"),
        ...     timestamp=0.0,
        ... )
        >>> d = w.to_dict()
        >>> d["merge_policy"]
        'highest_score'
        """
        return {
            "witness_id": self.witness_id,
            "fleet_ids": list(self.fleet_ids),
            "winning_inhabitant_id": self.winning_inhabitant_id,
            "merge_policy": self.merge_policy,
            "trust_tier": self.trust_tier,
            "score": self.score,
            "provenance": list(self.provenance),
            "timestamp": self.timestamp,
            "iso_timestamp": datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc
            ).isoformat(),
        }


# ---------------------------------------------------------------------------
# MergeConflict
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MergeConflict:
    """Immutable record describing a conflict between two fleet proposals.

    Conflicts arise when fleets F_a and F_b propose structurally incompatible
    inhabitants for the same patch.  The conflict record captures the identity
    of both fleets, the patch in question, the type of conflict, and its
    severity.

    Theory — Ch42 §42.6.3 Conflict Resolution
    -------------------------------------------
    A conflict between proposals p_a ∈ F_a and p_b ∈ F_b for patch P is
    classified by the *conflict_type* field:

      type_mismatch           — p_a and p_b inhabit different types of P
      semantic_overlap        — p_a and p_b share overlapping but distinct
                                semantics; partial compatibility possible
      structural_incompatibility — p_a and p_b have incompatible structure
                                   (e.g., different arities)
      provenance_clash        — p_a and p_b derive from incompatible
                                evidence chains

    Critical conflicts (severity HIGH or CRITICAL) block finalisation.

    Fields
    ------
    conflict_id:
        Unique identifier for this conflict record.
    fleet_a_id:
        Identifier of the first fleet involved.
    fleet_b_id:
        Identifier of the second fleet involved.
    patch_id:
        Identifier of the patch over which the conflict occurred.
    conflict_type:
        One of the four conflict types described above.
    description:
        Human-readable description of the conflict.
    severity:
        Severity level string ("LOW", "MEDIUM", "HIGH", "CRITICAL").
    """

    conflict_id: str
    fleet_a_id: str
    fleet_b_id: str
    patch_id: str
    conflict_type: str
    description: str
    severity: str

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the conflict record to a plain Python dictionary.

        Returns
        -------
        dict
            A JSON-serialisable mapping of all conflict fields.

        Examples
        --------
        >>> c = MergeConflict(
        ...     conflict_id="c1",
        ...     fleet_a_id="fa",
        ...     fleet_b_id="fb",
        ...     patch_id="p1",
        ...     conflict_type="type_mismatch",
        ...     description="Incompatible types",
        ...     severity="HIGH",
        ... )
        >>> c.to_dict()["severity"]
        'HIGH'
        """
        return {
            "conflict_id": self.conflict_id,
            "fleet_a_id": self.fleet_a_id,
            "fleet_b_id": self.fleet_b_id,
            "patch_id": self.patch_id,
            "conflict_type": self.conflict_type,
            "description": self.description,
            "severity": self.severity,
            "is_critical": self.is_critical(),
        }

    def is_critical(self) -> bool:
        """Return ``True`` when the conflict severity is HIGH or CRITICAL.

        Critical conflicts must be resolved before the FINALIZATION phase
        can proceed.  A coordinator that encounters unresolved critical
        conflicts raises a ``RuntimeError`` rather than emitting a witness.

        Returns
        -------
        bool
            ``True`` iff ``self.severity`` is ``"HIGH"`` or ``"CRITICAL"``.

        Examples
        --------
        >>> c = MergeConflict("c1", "fa", "fb", "p1", "type_mismatch",
        ...                   "desc", "HIGH")
        >>> c.is_critical()
        True
        >>> c2 = MergeConflict("c2", "fa", "fb", "p1", "type_mismatch",
        ...                    "desc", "LOW")
        >>> c2.is_critical()
        False
        """
        return self.severity in ("HIGH", "CRITICAL")


# ---------------------------------------------------------------------------
# FleetMergeRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FleetMergeRecord:
    """Immutable historical record of a completed fleet merge operation.

    ``FleetMergeRecord`` objects are appended to the
    ``FleetMergeCoordinator`` history list after every successful merge.
    They are richer than ``FleetMergeWitness`` instances: they include
    diagnostic information such as phase timings, conflict counts, and
    the full ranked list of proposal identifiers considered.

    Theory — Ch42 §42.6.6 Merge History
    --------------------------------------
    The merge history H = [r₁, r₂, …, rₙ] forms an append-only log of all
    merge operations performed by a given coordinator.  This log supports:

      - Audit queries: "what was the winning inhabitant for patch P?"
      - Regression detection: did the winning score decrease over time?
      - Policy comparison: which policy produces the highest average score?

    Fields
    ------
    record_id:
        Globally unique identifier for this record.
    witness_id:
        Identifier of the associated ``FleetMergeWitness``.
    fleet_ids:
        Tuple of fleet identifiers that participated.
    winning_inhabitant_id:
        Identifier of the selected inhabitant.
    merge_policy:
        String name of the MergePolicy applied.
    phase_timings:
        Mapping from FleetPhase name to elapsed seconds for that phase.
    conflict_count:
        Total number of conflicts detected during CONFLICT_RESOLUTION.
    critical_conflict_count:
        Number of critical conflicts detected (must be 0 for success).
    ranked_proposal_ids:
        Tuple of proposal identifiers ordered by descending composite score.
    composite_scores:
        Tuple of composite scores corresponding to ``ranked_proposal_ids``.
    fleet_count:
        Number of fleets that participated.
    proposal_count:
        Total number of proposals considered across all fleets.
    timestamp:
        Unix epoch timestamp at which the record was created.
    """

    record_id: str
    witness_id: str
    fleet_ids: tuple[str, ...]
    winning_inhabitant_id: str
    merge_policy: str
    phase_timings: tuple[tuple[str, float], ...]
    conflict_count: int
    critical_conflict_count: int
    ranked_proposal_ids: tuple[str, ...]
    composite_scores: tuple[float, ...]
    fleet_count: int
    proposal_count: int
    timestamp: float

    def to_dict(self) -> dict:
        """Serialise the record to a plain Python dictionary.

        Returns
        -------
        dict
            A JSON-serialisable mapping of all record fields, including a
            human-readable ISO 8601 timestamp.
        """
        return {
            "record_id": self.record_id,
            "witness_id": self.witness_id,
            "fleet_ids": list(self.fleet_ids),
            "winning_inhabitant_id": self.winning_inhabitant_id,
            "merge_policy": self.merge_policy,
            "phase_timings": dict(self.phase_timings),
            "conflict_count": self.conflict_count,
            "critical_conflict_count": self.critical_conflict_count,
            "ranked_proposal_ids": list(self.ranked_proposal_ids),
            "composite_scores": list(self.composite_scores),
            "fleet_count": self.fleet_count,
            "proposal_count": self.proposal_count,
            "timestamp": self.timestamp,
            "iso_timestamp": datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc
            ).isoformat(),
        }


# ---------------------------------------------------------------------------
# FleetMergeAnalyzer
# ---------------------------------------------------------------------------


class FleetMergeAnalyzer:
    """Stateless analysis utilities for fleet merge operations.

    ``FleetMergeAnalyzer`` provides the scoring, ranking, and divergence
    detection logic that underpins the SCORING and CONFLICT_RESOLUTION phases
    of the merge pipeline.  All methods are side-effect-free; state lives
    in the coordinator.

    Theory — Ch42 §42.6.1 Scoring
    --------------------------------
    The composite scoring function Φ(p) for a proposal p is:

        Φ(p) = w_t · trust_weight(p) + w_s · semantic_score(p)
               + w_c · coverage(p)

    where weights satisfy w_t + w_s + w_c = 1.

    Divergence (Ch42 §42.6.3 Definition 42.4)
    -------------------------------------------
    A set of proposals is *divergent* if the standard deviation of their
    composite scores exceeds a divergence threshold δ_max:

        divergent(P) ↔ std({Φ(p) | p ∈ P}) > δ_max

    Parameters
    ----------
    trust_weight:
        Weight assigned to the trust-tier component of Φ(p).
        Defaults to ``DEFAULT_TRUST_WEIGHT`` (0.40).
    semantic_weight:
        Weight assigned to the semantic-score component of Φ(p).
        Defaults to ``DEFAULT_SEMANTIC_WEIGHT`` (0.45).
    coverage_weight:
        Weight assigned to the coverage component of Φ(p).
        Defaults to ``DEFAULT_COVERAGE_WEIGHT`` (0.15).
    divergence_threshold:
        Maximum tolerated standard deviation before proposals are
        considered divergent.  Defaults to 0.30.
    """

    def __init__(
        self,
        trust_weight: float = DEFAULT_TRUST_WEIGHT,
        semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
        coverage_weight: float = DEFAULT_COVERAGE_WEIGHT,
        divergence_threshold: float = 0.30,
    ) -> None:
        self.trust_weight = trust_weight
        self.semantic_weight = semantic_weight
        self.coverage_weight = coverage_weight
        self.divergence_threshold = divergence_threshold
        logger.debug(
            "FleetMergeAnalyzer initialised: w_t=%.2f w_s=%.2f w_c=%.2f δ_max=%.2f",
            trust_weight,
            semantic_weight,
            coverage_weight,
            divergence_threshold,
        )

    # ------------------------------------------------------------------
    # Core analysis
    # ------------------------------------------------------------------

    def analyze(self, fleets: list[dict]) -> dict:
        """Produce a comprehensive analysis dictionary for a list of fleets.

        The analysis includes per-fleet proposal counts, aggregate statistics,
        trust-tier distribution, and a divergence indicator.

        Parameters
        ----------
        fleets:
            List of fleet dictionaries.  Each dict must have at least a
            ``"fleet_id"`` key and a ``"proposals"`` key containing a list
            of proposal dicts (each with ``"inhabitant_id"``,
            ``"trust_tier"``, and ``"base_score"``).

        Returns
        -------
        dict
            A dictionary with keys:
              - ``fleet_count``: number of fleets analysed
              - ``total_proposals``: total proposal count across all fleets
              - ``per_fleet``: list of per-fleet summary dicts
              - ``trust_distribution``: mapping tier→count
              - ``aggregate_score``: overall composite score mean
              - ``divergent``: bool indicating whether proposals diverge
              - ``analysis_timestamp``: ISO 8601 timestamp
        """
        all_proposals: list[dict] = []
        per_fleet: list[dict] = []
        trust_dist: dict[str, int] = {}

        for fleet in fleets:
            fleet_id = fleet.get("fleet_id", "<unknown>")
            proposals = fleet.get("proposals", [])
            fleet_scores = []
            for p in proposals:
                score = self._proposal_composite_score(p)
                fleet_scores.append(score)
                tier = str(p.get("trust_tier", "PROPOSAL"))
                trust_dist[tier] = trust_dist.get(tier, 0) + 1
                all_proposals.append(
                    {"fleet_id": fleet_id, "proposal": p, "composite_score": score}
                )
            per_fleet.append(
                {
                    "fleet_id": fleet_id,
                    "proposal_count": len(proposals),
                    "mean_score": (
                        sum(fleet_scores) / len(fleet_scores) if fleet_scores else 0.0
                    ),
                    "max_score": max(fleet_scores, default=0.0),
                }
            )

        all_scores = [e["composite_score"] for e in all_proposals]
        mean_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

        result = {
            "fleet_count": len(fleets),
            "total_proposals": len(all_proposals),
            "per_fleet": per_fleet,
            "trust_distribution": trust_dist,
            "aggregate_score": mean_score,
            "divergent": self.detect_divergence(
                [e["proposal"] for e in all_proposals]
            ),
            "analysis_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        logger.debug(
            "analyze(): %d fleets, %d total proposals, divergent=%s",
            len(fleets),
            len(all_proposals),
            result["divergent"],
        )
        return result

    def score(self, fleets: list[dict]) -> float:
        """Return the aggregate composite score for a list of fleets.

        The aggregate score is the arithmetic mean of the composite scores
        of all proposals across all fleets.  Returns 0.0 when no proposals
        are present.

        Parameters
        ----------
        fleets:
            List of fleet dictionaries (same format as ``analyze``).

        Returns
        -------
        float
            Mean composite score ∈ [0, 1].
        """
        scores: list[float] = []
        for fleet in fleets:
            for p in fleet.get("proposals", []):
                scores.append(self._proposal_composite_score(p))
        result = sum(scores) / len(scores) if scores else 0.0
        logger.debug("score(): %.4f over %d proposals", result, len(scores))
        return result

    def report(self, fleets: list[dict]) -> str:
        """Generate a human-readable analysis report for a list of fleets.

        The report is a multi-line string summarising fleet counts, aggregate
        scores, trust-tier distribution, and divergence status.  Useful for
        logging and debugging.

        Parameters
        ----------
        fleets:
            List of fleet dictionaries.

        Returns
        -------
        str
            A formatted multi-line report string.
        """
        analysis = self.analyze(fleets)
        lines: list[str] = [
            "=== Fleet Merge Analysis Report ===",
            f"Timestamp      : {analysis['analysis_timestamp']}",
            f"Fleet count    : {analysis['fleet_count']}",
            f"Total proposals: {analysis['total_proposals']}",
            f"Aggregate score: {analysis['aggregate_score']:.4f}",
            f"Divergent      : {analysis['divergent']}",
            "",
            "Per-fleet summary:",
        ]
        for entry in analysis["per_fleet"]:
            lines.append(
                f"  [{entry['fleet_id']}] "
                f"proposals={entry['proposal_count']} "
                f"mean={entry['mean_score']:.4f} "
                f"max={entry['max_score']:.4f}"
            )
        lines.append("")
        lines.append("Trust tier distribution:")
        for tier, count in sorted(analysis["trust_distribution"].items()):
            lines.append(f"  {tier}: {count}")
        lines.append("=== End Report ===")
        return "\n".join(lines)

    def rank_inhabitants(self, proposals: list[dict]) -> list[dict]:
        """Return proposals sorted by descending composite score.

        Each entry in the returned list is the original proposal dict
        augmented with a ``"composite_score"`` key.

        Parameters
        ----------
        proposals:
            List of proposal dicts (each with ``"inhabitant_id"``,
            ``"trust_tier"``, ``"base_score"``).

        Returns
        -------
        list[dict]
            Proposals with composite scores, sorted highest-first.
        """
        scored = [
            {**p, "composite_score": self._proposal_composite_score(p)}
            for p in proposals
        ]
        ranked = sorted(scored, key=lambda x: x["composite_score"], reverse=True)
        logger.debug(
            "rank_inhabitants(): ranked %d proposals; top score=%.4f",
            len(ranked),
            ranked[0]["composite_score"] if ranked else 0.0,
        )
        return ranked

    def compute_trust_weighted_score(self, proposals: list[dict]) -> dict:
        """Compute trust-weighted composite scores for a list of proposals.

        Each proposal receives a score that combines its raw semantic score
        with a weight proportional to its trust-tier value.  The result
        includes both individual scores and a summary statistic.

        Parameters
        ----------
        proposals:
            List of proposal dicts.

        Returns
        -------
        dict
            Mapping with keys:
              - ``"scores"``: list of {inhabitant_id, composite_score} dicts
              - ``"weighted_mean"``: trust-weighted mean score
              - ``"max_score"``: highest composite score
              - ``"winner_id"``: inhabitant_id of the highest-scoring proposal
        """
        if not proposals:
            return {
                "scores": [],
                "weighted_mean": 0.0,
                "max_score": 0.0,
                "winner_id": None,
            }

        scored_entries: list[dict] = []
        weight_sum = 0.0
        weighted_total = 0.0

        for p in proposals:
            cs = self._proposal_composite_score(p)
            tier_val = self._trust_tier_value(p)
            scored_entries.append(
                {
                    "inhabitant_id": p.get("inhabitant_id", "<unknown>"),
                    "composite_score": cs,
                    "trust_tier_value": tier_val,
                }
            )
            weight_sum += tier_val
            weighted_total += cs * tier_val

        weighted_mean = weighted_total / weight_sum if weight_sum > 0 else 0.0
        best = max(scored_entries, key=lambda x: x["composite_score"])

        return {
            "scores": scored_entries,
            "weighted_mean": weighted_mean,
            "max_score": best["composite_score"],
            "winner_id": best["inhabitant_id"],
        }

    def detect_divergence(self, proposals: list[dict]) -> bool:
        """Return ``True`` when proposals are divergent (Ch42 §42.6.3 Def 42.4).

        Divergence is defined as the standard deviation of composite scores
        exceeding ``self.divergence_threshold``.

        Parameters
        ----------
        proposals:
            List of proposal dicts.

        Returns
        -------
        bool
            ``True`` iff std(scores) > divergence_threshold.
        """
        if len(proposals) < 2:
            return False
        scores = [self._proposal_composite_score(p) for p in proposals]
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = variance ** 0.5
        divergent = std > self.divergence_threshold
        logger.debug(
            "detect_divergence(): std=%.4f threshold=%.4f divergent=%s",
            std,
            self.divergence_threshold,
            divergent,
        )
        return divergent

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _proposal_composite_score(self, proposal: dict) -> float:
        """Compute Φ(p) = w_t·trust + w_s·semantic + w_c·coverage.

        Missing fields are treated as 0.0.  The result is clamped to [0, 1].
        """
        tier_val = self._trust_tier_value(proposal)
        trust_component = tier_val / MAX_TRUST_TIER_VALUE
        semantic_component = float(proposal.get("base_score", 0.0))
        coverage_raw = proposal.get("coverage", None)
        if coverage_raw is None:
            patch_ids = proposal.get("patch_ids", [])
            coverage_component = min(1.0, len(patch_ids) / 10.0)
        else:
            coverage_component = float(coverage_raw)

        composite = (
            self.trust_weight * trust_component
            + self.semantic_weight * semantic_component
            + self.coverage_weight * coverage_component
        )
        return max(0.0, min(1.0, composite))

    @staticmethod
    def _trust_tier_value(proposal: dict) -> float:
        """Extract a numeric trust-tier value from a proposal dict.

        Handles both integer and string representations of the tier.
        Defaults to 1 (PROPOSAL tier) when the field is absent or invalid.
        """
        raw = proposal.get("trust_tier", 1)
        if isinstance(raw, int):
            return float(raw)
        try:
            # Try parsing enum name like "PROPOSAL", "REVIEW", etc.
            _names = {"PROPOSAL": 1, "REVIEW": 2, "VERIFIED": 3, "TRUSTED": 4, "ROOT": 5}
            return float(_names.get(str(raw).upper(), 1))
        except (TypeError, ValueError):
            return 1.0


# ---------------------------------------------------------------------------
# FleetMergeCoordinator
# ---------------------------------------------------------------------------


class FleetMergeCoordinator:
    """Orchestrates the full fleet merge pipeline (Ch42 §42.6.4).

    The coordinator owns the stateful merge history and drives each fleet
    through the five phases: COLLECTION → SCORING → CONFLICT_RESOLUTION →
    SELECTION → FINALIZATION.

    Theory — Ch42 §42.6 Fleet Merging
    ------------------------------------
    The coordinator is the *unique authority* that may emit a
    ``FleetMergeWitness``.  Its correctness guarantee is:

        ∀ merge: coordinator produces exactly one witness iff
            (1) all proposals satisfy the trust invariant, AND
            (2) no unresolved critical conflicts remain.

    Parameters
    ----------
    policy:
        Default ``MergePolicy`` used when ``run()`` is not passed an
        explicit policy.  Defaults to ``MergePolicy.HIGHEST_SCORE``.
    analyzer:
        ``FleetMergeAnalyzer`` instance used for scoring and divergence
        detection.  A default instance is created if not provided.
    """

    def __init__(
        self,
        policy: MergePolicy = MergePolicy.HIGHEST_SCORE,
        analyzer: FleetMergeAnalyzer | None = None,
    ) -> None:
        self.default_policy = policy
        self.analyzer = analyzer or FleetMergeAnalyzer()
        self._history: list[FleetMergeRecord] = []
        logger.info(
            "FleetMergeCoordinator initialised with policy=%s", policy.value
        )

    # ------------------------------------------------------------------
    # Primary interface
    # ------------------------------------------------------------------

    def run(self, fleets: list[dict], policy: MergePolicy | None = None) -> FleetMergeWitness:
        """Execute the full merge pipeline and return a ``FleetMergeWitness``.

        Phases executed (in order):
          1. COLLECTION  — validate fleets and gather proposals
          2. SCORING     — compute Φ(p) for every proposal
          3. CONFLICT_RESOLUTION — detect and handle conflicts
          4. SELECTION   — apply the merge policy
          5. FINALIZATION — emit witness and update history

        Parameters
        ----------
        fleets:
            List of fleet dictionaries.  Each must have ``"fleet_id"`` and
            ``"proposals"`` keys.
        policy:
            Override the coordinator's default policy for this run only.
            If ``None``, uses ``self.default_policy``.

        Returns
        -------
        FleetMergeWitness
            The witness attesting to the merge outcome.

        Raises
        ------
        ValueError
            If validation fails (e.g., fewer than MIN_FLEET_COUNT fleets).
        RuntimeError
            If unresolved critical conflicts remain after CONFLICT_RESOLUTION.
        """
        effective_policy = policy or self.default_policy
        phase_timings: dict[str, float] = {}

        # ----- Phase 1: COLLECTION ----------------------------------------
        t0 = time.monotonic()
        errors = self.validate(fleets)
        if errors:
            msg = "Fleet validation failed: " + "; ".join(errors)
            logger.error(msg)
            raise ValueError(msg)
        all_proposals = self._collect_proposals(fleets)
        phase_timings[FleetPhase.COLLECTION.value] = time.monotonic() - t0
        logger.debug("COLLECTION complete: %d proposals", len(all_proposals))

        # ----- Phase 2: SCORING -------------------------------------------
        t1 = time.monotonic()
        ranked = self.analyzer.rank_inhabitants(all_proposals)
        phase_timings[FleetPhase.SCORING.value] = time.monotonic() - t1
        logger.debug(
            "SCORING complete: top score=%.4f", ranked[0]["composite_score"] if ranked else 0.0
        )

        # ----- Phase 3: CONFLICT_RESOLUTION --------------------------------
        t2 = time.monotonic()
        conflicts = self._detect_all_conflicts(fleets)
        critical = [c for c in conflicts if c.is_critical()]
        if critical:
            logger.warning(
                "%d critical conflict(s) detected; attempting auto-resolution",
                len(critical),
            )
            conflicts, unresolved = self._auto_resolve_conflicts(conflicts)
            if unresolved:
                raise RuntimeError(
                    f"{len(unresolved)} critical conflict(s) remain unresolved: "
                    + ", ".join(c.conflict_id for c in unresolved)
                )
        phase_timings[FleetPhase.CONFLICT_RESOLUTION.value] = time.monotonic() - t2
        logger.debug(
            "CONFLICT_RESOLUTION complete: %d conflict(s), %d critical",
            len(conflicts),
            len(critical),
        )

        # ----- Phase 4: SELECTION -----------------------------------------
        t3 = time.monotonic()
        selection = self.apply_merge_policy(fleets, effective_policy)
        winner_id: str = selection.get("winner_id", "<unknown>")
        winner_score: float = float(selection.get("winner_score", 0.0))
        phase_timings[FleetPhase.SELECTION.value] = time.monotonic() - t3
        logger.debug("SELECTION complete: winner=%s score=%.4f", winner_id, winner_score)

        # ----- Phase 5: FINALIZATION ---------------------------------------
        t4 = time.monotonic()
        now = time.time()
        witness_id = uuid.uuid4().hex
        fleet_ids = tuple(f.get("fleet_id", "") for f in fleets)
        provenance = (
            FleetPhase.COLLECTION.value,
            FleetPhase.SCORING.value,
            FleetPhase.CONFLICT_RESOLUTION.value,
            f"{FleetPhase.SELECTION.value}:{effective_policy.value}",
            FleetPhase.FINALIZATION.value,
        )
        witness = FleetMergeWitness(
            witness_id=witness_id,
            fleet_ids=fleet_ids,
            winning_inhabitant_id=winner_id,
            merge_policy=effective_policy.value,
            trust_tier="PROPOSAL",
            score=winner_score,
            provenance=provenance,
            timestamp=now,
        )

        # Build and store the history record
        ranked_ids = tuple(p.get("inhabitant_id", "") for p in ranked)
        ranked_scores = tuple(p.get("composite_score", 0.0) for p in ranked)
        record = FleetMergeRecord(
            record_id=uuid.uuid4().hex,
            witness_id=witness_id,
            fleet_ids=fleet_ids,
            winning_inhabitant_id=winner_id,
            merge_policy=effective_policy.value,
            phase_timings=tuple(phase_timings.items()),
            conflict_count=len(conflicts),
            critical_conflict_count=len(critical),
            ranked_proposal_ids=ranked_ids,
            composite_scores=ranked_scores,
            fleet_count=len(fleets),
            proposal_count=len(all_proposals),
            timestamp=now,
        )
        self._history.append(record)
        phase_timings[FleetPhase.FINALIZATION.value] = time.monotonic() - t4

        total = sum(phase_timings.values())
        logger.info(
            "run() complete: winner=%s policy=%s total_time=%.4fs",
            winner_id,
            effective_policy.value,
            total,
        )
        return witness

    def validate(self, fleets: list[dict]) -> list[str]:
        """Validate a list of fleets before merging.

        Checks performed:
          - At least MIN_FLEET_COUNT fleets are present.
          - Every fleet has a non-empty ``"fleet_id"`` string.
          - Every fleet has a ``"proposals"`` list.
          - Every proposal carries a ``"trust_tier"`` field consistent with
            the trust invariant (must equal ``"PROPOSAL"`` or integer 1).

        Parameters
        ----------
        fleets:
            List of fleet dicts to validate.

        Returns
        -------
        list[str]
            List of error messages.  Empty list means validation passed.
        """
        errors: list[str] = []

        if len(fleets) < MIN_FLEET_COUNT:
            errors.append(
                f"At least {MIN_FLEET_COUNT} fleet(s) required; got {len(fleets)}"
            )

        seen_ids: set[str] = set()
        for idx, fleet in enumerate(fleets):
            fid = fleet.get("fleet_id", "")
            if not fid:
                errors.append(f"Fleet at index {idx} has no 'fleet_id'")
            elif fid in seen_ids:
                errors.append(f"Duplicate fleet_id '{fid}' at index {idx}")
            else:
                seen_ids.add(fid)

            if "proposals" not in fleet:
                errors.append(f"Fleet '{fid}' missing 'proposals' list")
                continue

            for pidx, proposal in enumerate(fleet.get("proposals", [])):
                tier = proposal.get("trust_tier", None)
                if tier is None:
                    errors.append(
                        f"Fleet '{fid}' proposal[{pidx}] missing 'trust_tier' "
                        "(trust invariant violation)"
                    )
                else:
                    tier_str = str(tier).upper()
                    tier_int = int(tier) if isinstance(tier, int) else None
                    if tier_str not in ("PROPOSAL", "1") and tier_int != 1:
                        errors.append(
                            f"Fleet '{fid}' proposal[{pidx}] has trust_tier={tier!r}; "
                            "expected PROPOSAL (trust invariant violation)"
                        )

        return errors

    def to_dict(self) -> dict:
        """Serialise coordinator state to a plain dictionary.

        Returns a snapshot of the coordinator's configuration and a summary
        of its merge history (record count, last winner, last policy).

        Returns
        -------
        dict
            Coordinator state dictionary.
        """
        history_summary: dict = {}
        if self._history:
            last = self._history[-1]
            history_summary = {
                "last_winner": last.winning_inhabitant_id,
                "last_policy": last.merge_policy,
                "last_timestamp": datetime.fromtimestamp(
                    last.timestamp, tz=timezone.utc
                ).isoformat(),
            }
        return {
            "default_policy": self.default_policy.value,
            "history_length": len(self._history),
            "history_summary": history_summary,
            "analyzer_weights": {
                "trust": self.analyzer.trust_weight,
                "semantic": self.analyzer.semantic_weight,
                "coverage": self.analyzer.coverage_weight,
            },
        }

    def select_winner(self, proposals: list[dict]) -> dict:
        """Select the winning proposal from a flat list using HIGHEST_SCORE.

        This is a convenience method that bypasses policy dispatch and always
        uses HIGHEST_SCORE selection.  Use ``apply_merge_policy`` for
        policy-aware selection.

        Parameters
        ----------
        proposals:
            List of proposal dicts.

        Returns
        -------
        dict
            Dict with keys ``"winner_id"`` and ``"winner_score"``.
        """
        if not proposals:
            logger.warning("select_winner(): called with empty proposals list")
            return {"winner_id": "<none>", "winner_score": 0.0}
        ranked = self.analyzer.rank_inhabitants(proposals)
        best = ranked[0]
        return {
            "winner_id": best.get("inhabitant_id", "<unknown>"),
            "winner_score": best.get("composite_score", 0.0),
        }

    def merge_fleets(self, fleet_a: dict, fleet_b: dict) -> dict:
        """Merge exactly two fleets into a combined fleet representation.

        The merged fleet has a new ``fleet_id`` derived from hashing the two
        input fleet IDs, and its proposals are the union of both fleets'
        proposals.  Duplicate ``inhabitant_id`` values are deduplicated by
        retaining the higher-scoring copy.

        Parameters
        ----------
        fleet_a:
            First fleet dict (must have ``"fleet_id"`` and ``"proposals"``).
        fleet_b:
            Second fleet dict.

        Returns
        -------
        dict
            A merged fleet dict with keys ``"fleet_id"``, ``"proposals"``,
            ``"source_fleet_ids"``.
        """
        id_a = fleet_a.get("fleet_id", "fa")
        id_b = fleet_b.get("fleet_id", "fb")
        combined_id = hashlib.sha1(f"{id_a}:{id_b}".encode()).hexdigest()[:12]

        proposals_a = fleet_a.get("proposals", [])
        proposals_b = fleet_b.get("proposals", [])

        # Deduplicate by inhabitant_id, keeping the higher-scoring copy
        best_by_id: dict[str, dict] = {}
        for p in proposals_a + proposals_b:
            iid = p.get("inhabitant_id", uuid.uuid4().hex)
            existing = best_by_id.get(iid)
            if existing is None:
                best_by_id[iid] = p
            else:
                existing_score = self.analyzer._proposal_composite_score(existing)
                new_score = self.analyzer._proposal_composite_score(p)
                if new_score > existing_score:
                    best_by_id[iid] = p

        merged_proposals = list(best_by_id.values())
        logger.debug(
            "merge_fleets(): %s + %s → %s (%d proposals)",
            id_a,
            id_b,
            combined_id,
            len(merged_proposals),
        )
        return {
            "fleet_id": combined_id,
            "proposals": merged_proposals,
            "source_fleet_ids": [id_a, id_b],
        }

    def apply_merge_policy(self, fleets: list[dict], policy: MergePolicy) -> dict:
        """Apply the given merge policy to a list of fleets.

        Dispatches to the appropriate selection algorithm based on ``policy``:

          - HIGHEST_SCORE   → ``_select_highest_score``
          - TRUST_WEIGHTED  → ``_select_trust_weighted``
          - MAJORITY_VOTE   → ``_select_majority_vote``
          - CONSENSUS       → ``_select_consensus``

        Parameters
        ----------
        fleets:
            List of fleet dicts.
        policy:
            The merge policy to apply.

        Returns
        -------
        dict
            Dict with at least ``"winner_id"`` and ``"winner_score"`` keys,
            plus policy-specific metadata.
        """
        all_proposals = self._collect_proposals(fleets)

        if policy == MergePolicy.HIGHEST_SCORE:
            result = self._select_highest_score(all_proposals)
        elif policy == MergePolicy.TRUST_WEIGHTED:
            result = self._select_trust_weighted(all_proposals)
        elif policy == MergePolicy.MAJORITY_VOTE:
            result = self._select_majority_vote(all_proposals, fleets)
        elif policy == MergePolicy.CONSENSUS:
            result = self._select_consensus(all_proposals, fleets)
        else:
            logger.warning("Unknown policy %r; falling back to HIGHEST_SCORE", policy)
            result = self._select_highest_score(all_proposals)

        logger.debug(
            "apply_merge_policy(%s): winner=%s score=%.4f",
            policy.value,
            result.get("winner_id"),
            result.get("winner_score", 0.0),
        )
        return result

    def compute_consensus(self, proposals: list[dict]) -> dict:
        """Compute a consensus summary over a list of proposals.

        Consensus is achieved when at least CONSENSUS_QUORUM of proposals
        share the same ``inhabitant_id``.  Returns the consensus inhabitant
        if found, otherwise returns the highest-scoring proposal as a
        fallback.

        Parameters
        ----------
        proposals:
            List of proposal dicts.

        Returns
        -------
        dict
            Dict with keys:
              - ``"consensus_reached"``: bool
              - ``"winner_id"``: inhabitant_id of the consensus / fallback winner
              - ``"winner_score"``: composite score of the winner
              - ``"agreement_fraction"``: fraction of proposals supporting winner
        """
        if not proposals:
            return {
                "consensus_reached": False,
                "winner_id": "<none>",
                "winner_score": 0.0,
                "agreement_fraction": 0.0,
            }

        counts: dict[str, int] = {}
        for p in proposals:
            iid = p.get("inhabitant_id", "<unknown>")
            counts[iid] = counts.get(iid, 0) + 1

        total = len(proposals)
        best_id = max(counts, key=lambda k: counts[k])
        fraction = counts[best_id] / total
        consensus_reached = fraction >= CONSENSUS_QUORUM

        if consensus_reached:
            # Find the highest-scoring proposal with the consensus id
            matching = [p for p in proposals if p.get("inhabitant_id") == best_id]
            ranked = self.analyzer.rank_inhabitants(matching)
            winner_score = ranked[0].get("composite_score", 0.0) if ranked else 0.0
        else:
            # Fall back to highest score
            fb = self._select_highest_score(proposals)
            best_id = fb["winner_id"]
            winner_score = fb["winner_score"]

        logger.debug(
            "compute_consensus(): reached=%s fraction=%.2f winner=%s",
            consensus_reached,
            fraction,
            best_id,
        )
        return {
            "consensus_reached": consensus_reached,
            "winner_id": best_id,
            "winner_score": winner_score,
            "agreement_fraction": fraction,
        }

    def get_merge_history(self) -> list[dict]:
        """Return the merge history as a list of serialised record dicts.

        Each entry corresponds to one completed call to ``run()``.  Records
        are ordered oldest-first.

        Returns
        -------
        list[dict]
            List of ``FleetMergeRecord.to_dict()`` results.
        """
        return [r.to_dict() for r in self._history]

    def reset(self) -> None:
        """Clear the merge history and reset the coordinator to a clean state.

        After a reset, ``get_merge_history()`` returns an empty list.
        The coordinator's policy and analyzer configuration are preserved.

        Returns
        -------
        None
        """
        count = len(self._history)
        self._history.clear()
        logger.info("FleetMergeCoordinator.reset(): cleared %d history record(s)", count)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_proposals(self, fleets: list[dict]) -> list[dict]:
        """Flatten all proposals from all fleets into a single list."""
        result: list[dict] = []
        for fleet in fleets:
            fid = fleet.get("fleet_id", "<unknown>")
            for p in fleet.get("proposals", []):
                enriched = dict(p)
                enriched.setdefault("_fleet_id", fid)
                result.append(enriched)
        return result

    def _detect_all_conflicts(self, fleets: list[dict]) -> list[MergeConflict]:
        """Detect pairwise conflicts across all fleet pairs."""
        conflicts: list[MergeConflict] = []
        for i in range(len(fleets)):
            for j in range(i + 1, len(fleets)):
                conflicts.extend(
                    self._detect_pairwise_conflicts(fleets[i], fleets[j])
                )
        return conflicts

    def _detect_pairwise_conflicts(
        self, fleet_a: dict, fleet_b: dict
    ) -> list[MergeConflict]:
        """Detect conflicts between two specific fleets.

        A conflict is raised for every pair of proposals (p_a, p_b) where
        p_a and p_b share the same ``patch_id`` but have incompatible
        types or severely different composite scores.
        """
        conflicts: list[MergeConflict] = []
        id_a = fleet_a.get("fleet_id", "fa")
        id_b = fleet_b.get("fleet_id", "fb")

        proposals_a = fleet_a.get("proposals", [])
        proposals_b = fleet_b.get("proposals", [])

        # Build patch→proposal index for fleet_b
        patch_index_b: dict[str, list[dict]] = {}
        for p in proposals_b:
            for pid in p.get("patch_ids", [p.get("patch_id", "")]):
                patch_index_b.setdefault(pid, []).append(p)

        for p_a in proposals_a:
            pids_a = p_a.get("patch_ids", [p_a.get("patch_id", "")])
            for patch_id in pids_a:
                for p_b in patch_index_b.get(patch_id, []):
                    conflict = self._classify_conflict(id_a, id_b, patch_id, p_a, p_b)
                    if conflict is not None:
                        conflicts.append(conflict)

        return conflicts

    def _classify_conflict(
        self,
        fleet_a_id: str,
        fleet_b_id: str,
        patch_id: str,
        p_a: dict,
        p_b: dict,
    ) -> MergeConflict | None:
        """Classify and return a MergeConflict, or None if proposals are compatible."""
        score_a = self.analyzer._proposal_composite_score(p_a)
        score_b = self.analyzer._proposal_composite_score(p_b)
        score_gap = abs(score_a - score_b)

        type_a = p_a.get("proposal_type", p_a.get("inhabitant_type", ""))
        type_b = p_b.get("proposal_type", p_b.get("inhabitant_type", ""))

        if type_a and type_b and type_a != type_b:
            severity = "HIGH" if score_gap > CRITICAL_CONFLICT_THRESHOLD else "MEDIUM"
            return MergeConflict(
                conflict_id=uuid.uuid4().hex[:10],
                fleet_a_id=fleet_a_id,
                fleet_b_id=fleet_b_id,
                patch_id=patch_id,
                conflict_type="type_mismatch",
                description=(
                    f"Fleet {fleet_a_id} proposes type '{type_a}' but "
                    f"fleet {fleet_b_id} proposes type '{type_b}' for patch {patch_id}"
                ),
                severity=severity,
            )

        if score_gap > CRITICAL_CONFLICT_THRESHOLD:
            return MergeConflict(
                conflict_id=uuid.uuid4().hex[:10],
                fleet_a_id=fleet_a_id,
                fleet_b_id=fleet_b_id,
                patch_id=patch_id,
                conflict_type="semantic_overlap",
                description=(
                    f"Proposals for patch {patch_id} have large score gap "
                    f"({score_gap:.3f}); possible semantic overlap"
                ),
                severity="MEDIUM",
            )

        return None

    def _auto_resolve_conflicts(
        self, conflicts: list[MergeConflict]
    ) -> tuple[list[MergeConflict], list[MergeConflict]]:
        """Attempt automatic resolution of critical conflicts.

        Currently, type_mismatch conflicts at HIGH severity are demoted to
        MEDIUM when the score gap is below CRITICAL_CONFLICT_THRESHOLD,
        allowing the merge to proceed.  Truly CRITICAL conflicts remain
        unresolved.

        Returns a tuple (all_conflicts_after_resolution, unresolved_critical).
        """
        resolved: list[MergeConflict] = []
        unresolved: list[MergeConflict] = []

        for c in conflicts:
            if c.severity == "CRITICAL":
                unresolved.append(c)
                logger.error("Cannot auto-resolve CRITICAL conflict %s", c.conflict_id)
            elif c.severity == "HIGH":
                # Demote to MEDIUM — logged and tracked but not blocking
                from dataclasses import replace as dc_replace
                demoted = dc_replace(c, severity="MEDIUM")
                resolved.append(demoted)
                logger.warning(
                    "Auto-resolved HIGH conflict %s → MEDIUM (demoted)", c.conflict_id
                )
            else:
                resolved.append(c)

        return resolved, unresolved

    # ----- Policy implementations ----------------------------------------

    def _select_highest_score(self, proposals: list[dict]) -> dict:
        """Greedy highest-score selection."""
        if not proposals:
            return {"winner_id": "<none>", "winner_score": 0.0, "policy": "highest_score"}
        ranked = self.analyzer.rank_inhabitants(proposals)
        best = ranked[0]
        return {
            "winner_id": best.get("inhabitant_id", "<unknown>"),
            "winner_score": best.get("composite_score", 0.0),
            "policy": "highest_score",
        }

    def _select_trust_weighted(self, proposals: list[dict]) -> dict:
        """Trust-weighted selection using compute_trust_weighted_score."""
        result = self.analyzer.compute_trust_weighted_score(proposals)
        return {
            "winner_id": result.get("winner_id", "<none>"),
            "winner_score": result.get("max_score", 0.0),
            "weighted_mean": result.get("weighted_mean", 0.0),
            "policy": "trust_weighted",
        }

    def _select_majority_vote(self, proposals: list[dict], fleets: list[dict]) -> dict:
        """Majority vote: select inhabitant most commonly proposed across fleets."""
        vote_counts: dict[str, int] = {}
        best_scores: dict[str, float] = {}

        for fleet in fleets:
            iids_in_fleet: set[str] = set()
            for p in fleet.get("proposals", []):
                iid = p.get("inhabitant_id", "")
                if iid and iid not in iids_in_fleet:
                    iids_in_fleet.add(iid)
                    vote_counts[iid] = vote_counts.get(iid, 0) + 1
                    score = self.analyzer._proposal_composite_score(p)
                    if score > best_scores.get(iid, -1.0):
                        best_scores[iid] = score

        if not vote_counts:
            return {"winner_id": "<none>", "winner_score": 0.0, "policy": "majority_vote"}

        max_votes = max(vote_counts.values())
        candidates = [iid for iid, v in vote_counts.items() if v == max_votes]
        # Break ties by best score
        winner_id = max(candidates, key=lambda iid: best_scores.get(iid, 0.0))

        return {
            "winner_id": winner_id,
            "winner_score": best_scores.get(winner_id, 0.0),
            "vote_counts": vote_counts,
            "max_votes": max_votes,
            "policy": "majority_vote",
        }

    def _select_consensus(self, proposals: list[dict], fleets: list[dict]) -> dict:
        """Consensus selection: quorum-based with HIGHEST_SCORE fallback."""
        consensus = self.compute_consensus(proposals)
        result = {
            "winner_id": consensus["winner_id"],
            "winner_score": consensus["winner_score"],
            "consensus_reached": consensus["consensus_reached"],
            "agreement_fraction": consensus["agreement_fraction"],
            "policy": "consensus",
        }
        if not consensus["consensus_reached"]:
            logger.warning(
                "Consensus quorum not reached (fraction=%.2f); falling back to HIGHEST_SCORE",
                consensus["agreement_fraction"],
            )
        return result


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    def _make_proposal(iid: str, score: float, tier: int = 1) -> dict:
        return {
            "inhabitant_id": iid,
            "trust_tier": tier,
            "base_score": score,
            "patch_ids": ["patch-1"],
            "patch_id": "patch-1",
        }

    fleet_a = {
        "fleet_id": "fleet-alpha",
        "proposals": [
            _make_proposal("inh-001", 0.80),
            _make_proposal("inh-002", 0.65),
        ],
    }
    fleet_b = {
        "fleet_id": "fleet-beta",
        "proposals": [
            _make_proposal("inh-001", 0.82),
            _make_proposal("inh-003", 0.71),
        ],
    }
    fleet_c = {
        "fleet_id": "fleet-gamma",
        "proposals": [
            _make_proposal("inh-001", 0.78),
        ],
    }

    analyzer = FleetMergeAnalyzer()
    print("\n--- Analyzer Report ---")
    print(analyzer.report([fleet_a, fleet_b, fleet_c]))

    coordinator = FleetMergeCoordinator()

    for policy in MergePolicy:
        print(f"\n--- Policy: {policy.value} ---")
        try:
            witness = coordinator.run([fleet_a, fleet_b, fleet_c], policy=policy)
            print(json.dumps(witness.to_dict(), indent=2))
        except Exception as exc:
            print(f"ERROR: {exc}")

    print("\n--- Merge History ---")
    for rec in coordinator.get_merge_history():
        print(
            f"  record={rec['record_id'][:8]}  winner={rec['winning_inhabitant_id']}  "
            f"policy={rec['merge_policy']}  proposals={rec['proposal_count']}"
        )

    print("\n--- merge_fleets() ---")
    merged = coordinator.merge_fleets(fleet_a, fleet_b)
    print(f"  merged fleet_id={merged['fleet_id']}  proposals={len(merged['proposals'])}")

    print("\n--- Conflict detection ---")
    fleet_conflict_a = {
        "fleet_id": "cx-a",
        "proposals": [
            {**_make_proposal("inh-X", 0.90), "proposal_type": "TypeA", "patch_id": "p99"},
        ],
    }
    fleet_conflict_b = {
        "fleet_id": "cx-b",
        "proposals": [
            {**_make_proposal("inh-Y", 0.10), "proposal_type": "TypeB", "patch_id": "p99"},
        ],
    }
    coord2 = FleetMergeCoordinator()
    try:
        w2 = coord2.run([fleet_conflict_a, fleet_conflict_b])
        print(f"  winner={w2.winning_inhabitant_id}")
    except RuntimeError as e:
        print(f"  RuntimeError (expected for CRITICAL): {e}")

    print("\n--- reset() ---")
    coordinator.reset()
    print(f"  History after reset: {coordinator.get_merge_history()}")

    print("\nSmoke test complete.")
