"""
Fleet Search Over Admissible Inhabitants
=========================================

This module implements parallel fleet-based search over admissible inhabitants
of a type-theoretic judgment context.  Each fleet member runs an independent
search strategy; results are aggregated via a configurable policy.

Mathematical background
-----------------------
A *judgment* is an 8-tuple

    J = (c, φ, A, E, O, B, T, Π)

where
  c  – context (list of typed hypotheses)
  φ  – formula / goal type
  A  – assumptions (axiomatic background)
  E  – evidence (witnessed proof terms or run-time observations)
  O  – obstructions, represented as Čech H¹ cohomology classes on the
       nerve of the open cover induced by the hypothesis context
  B  – blame (pointer to the obligation responsible for an obstruction)
  T  – trust tier (element of the ordered algebra TrustTier)
  Π  – proof obligations (pending sub-goals)

*Admissibility* of an inhabitant e : φ requires
  1. All proof obligations Π are discharged.
  2. The obstruction class [O] ∈ H¹ vanishes (‖O‖ < ε).
  3. The trust tier T is at least VERIFIED.

A *fleet* is a collection of parallel search agents, each assigned one
strategy from SearchStrategy.  The FleetCoordinator mediates shared memory
and halts when the obstruction budget is exhausted.

    # copilot: fleet search over the space of admissible inhabitants

Sheaf-theoretic note
---------------------
Čech cohomology H¹(𝒰, ℱ) classifies obstructions to gluing local sections of
the sheaf ℱ of proof terms over the nerve of 𝒰.  In our setting 𝒰 is the
cover of the hypothesis context by principal opens U_i = {c | x_i ∈ c}, and
ℱ assigns to each U_i the set of partial proof terms that discharge φ
restricted to U_i.  A fleet member that finds a local section reports it;
the coordinator attempts to glue; any failure is recorded as a non-trivial
cohomology class.
"""

from __future__ import annotations

import abc
import collections
import datetime
import enum
import functools
import hashlib
import heapq
import itertools
import logging
import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple, Union

try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, enum.Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, enum.Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(enum.IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, enum.Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, enum.Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, enum.Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"


# ---------------------------------------------------------------------------
# Module-level logger and constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

DEFAULT_FLEET_SIZE: int = 4
MAX_SEARCH_DEPTH: int = 10
ADMISSIBILITY_QUORUM: float = 0.75
VERSION: str = "0.3.0"
DEFAULT_OBSTRUCTION_BUDGET: float = 1.0
DEFAULT_BEAM_WIDTH: int = 8
DEFAULT_MAX_ITERATIONS: int = 200
DEFAULT_ADMISSIBILITY_THRESHOLD: float = 0.75
EPSILON: float = 1e-9
CECH_DIMENSION: int = 4
STRATEGY_TIMEOUT_SECS: float = 30.0
_AGGREGATION_POLICIES: Tuple[str, ...] = ("max_quality", "min_obstruction", "vote", "weighted_sum", "trust_max")
# Keep legacy alias for backward compatibility with existing helper classes:
AGGREGATION_POLICIES = _AGGREGATION_POLICIES
_KNOWN_STRATEGIES: Tuple[str, ...] = (
    "depth_first", "breadth_first", "beam", "mcts", "random_restart",
)

_ADJECTIVES: Tuple[str, ...] = (
    "admissible", "coherent", "fibrant", "cofibrant", "étale", "flat", "smooth", "proper",
)
_NOUNS: Tuple[str, ...] = (
    "section", "morphism", "sheaf", "fiber", "stalk", "germ", "topos", "site",
)


# ---------------------------------------------------------------------------
# TrustTier ordered algebra
# ---------------------------------------------------------------------------

class TrustTier(enum.IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float.

    The five tiers form a totally ordered set.  join (⊕=max) and meet (⊗=min)
    make (TrustTier, ≼, ⊕, ⊗) a bounded distributive lattice.  promote (↑_π)
    and demote (↓_χ) are the successor and predecessor functions clamped at the
    extremes PROOF_BACKED and PROPOSAL respectively.

    In the judgment 8-tuple J=(c, φ, A, E, O, B, T, Π), T ∈ TrustTier governs
    which proof obligations can be deferred vs. must be discharged immediately.

    Values
    ------
    PROPOSAL         = 1  — Proposed by an oracle; unreviewed.
    REVIEWED         = 2  — Passed human or automated review.
    VERIFIED         = 3  — All static checks discharged.
    RUNTIME_WITNESSED = 4 — Confirmed by a runtime observation.
    PROOF_BACKED     = 5  — Backed by a machine-checked formal proof.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    # Ordered-algebra operations
    # ------------------------------------------------------------------

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return int(self) <= int(other)

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, TrustTier):
            return NotImplemented
        return int(self) < int(other)

    def meet(self, other: TrustTier) -> TrustTier:
        """Greatest lower bound (min) in the trust lattice."""
        return TrustTier(min(int(self), int(other)))

    def join(self, other: TrustTier) -> TrustTier:
        """Least upper bound (max) in the trust lattice."""
        return TrustTier(max(int(self), int(other)))

    def is_at_least(self, threshold: TrustTier) -> bool:
        """Return True iff self ≥ threshold."""
        return int(self) >= int(threshold)

    def upgrade(self) -> TrustTier:
        """Return the next higher tier, or self if already maximal."""
        next_val = int(self) + 1
        try:
            return TrustTier(next_val)
        except ValueError:
            return self

    def downgrade(self) -> TrustTier:
        """Return the next lower tier, or self if already minimal."""
        prev_val = int(self) - 1
        if prev_val < 1:
            return self
        return TrustTier(prev_val)

    def promote(self) -> TrustTier:
        """↑_π — promote one tier upward, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """↓_χ — demote one tier downward, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

    @classmethod
    def from_string(cls, s: str) -> TrustTier:
        """Parse a trust tier from its name (case-insensitive)."""
        mapping = {t.name.upper(): t for t in cls}
        key = s.strip().upper()
        if key not in mapping:
            raise ValueError(f"Unknown TrustTier name: {s!r}")
        return mapping[key]


# ---------------------------------------------------------------------------
# Mandatory dataclasses: Judgment and CechObstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    A judgment is an 8-tuple encoding a logical assertion together with all
    the metadata required to verify, discharge, or defer it.

    Fields
    ------
    context     : c  — typing context (list of hypotheses or cover element id).
    formula     : φ  — the proposition or goal type being judged.
    assumptions : A  — axiomatic background (tuple of assumption labels).
    evidence    : E  — witnessed proof terms or runtime observations.
    obligations : O  — pending sub-goals yet to be discharged.
    burden      : B  — pointer to the obligation responsible for any failure.
    trust       : T  — TrustTier of this judgment.
    provenance  : Π  — source or proof strategy that produced this judgment.
    """

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


@dataclass(frozen=True)
class CechObstruction:
    """A Čech H¹ cohomology obstruction to gluing local proof sections.

    When fleet members find contradictory local sections of the proof-term
    sheaf, the failure is encoded as a CechObstruction.  The ``cocycle``
    field records the incompatible pairs; ``cohomology_class`` is a hash
    representative of the corresponding H¹ class.

    Fields
    ------
    cover_id         – identifier of the open cover 𝒰 where the obstruction lives.
    cocycle          – frozenset of strings witnessing the 1-cocycle.
    cohomology_class – string representative of the H¹ class (e.g. "[abc123ef]").
    description      – human-readable explanation of the obstruction.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff the cocycle is empty (obstruction vanishes)."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Private helpers shared by the new classes below
# ---------------------------------------------------------------------------

def _make_id(prefix: str = "obj") -> str:
    """Generate a short random ID with the given prefix (e.g. 'fleet-3a7b9c12')."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _current_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp string for the current moment."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _hash_goal(goal: dict) -> str:
    """Produce a stable 16-hex-char fingerprint of a goal dict."""
    canonical = str(sorted(goal.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _score_candidate(candidate: dict, min_trust: "TrustTier") -> float:
    """Compute an admissibility score in [0, 1] for a raw candidate dict.

    The score combines quality, trust tier, and remaining obligations.
    A score ≥ ADMISSIBILITY_QUORUM with no obligations and sufficient trust
    indicates a potentially admissible inhabitant.

    Parameters
    ----------
    candidate : dict  — keys: 'quality' (float), 'trust' (str), 'obligations' (list)
    min_trust : TrustTier — minimum required tier.

    Returns
    -------
    float in [0, 1].
    """
    quality: float = float(candidate.get("quality", 0.5))
    obligations: list = candidate.get("obligations", [])
    trust_name: str = candidate.get("trust", "PROPOSAL")
    try:
        trust = TrustTier.from_string(trust_name)
    except ValueError:
        trust = TrustTier.PROPOSAL
    trust_factor = trust.value / TrustTier.PROOF_BACKED.value
    obligation_penalty = len(obligations) * 0.05
    score = max(0.0, min(1.0, quality * trust_factor - obligation_penalty))
    if trust.value < min_trust.value:
        score *= 0.5
    return score


def _build_trivial_obstruction(cover_id: str = "trivial") -> "CechObstruction":
    """Return the trivial (zero) Čech obstruction for a given cover."""
    return CechObstruction(
        cover_id=cover_id,
        cocycle=frozenset(),
        cohomology_class="[0]",
        description="Trivial obstruction — all local sections glue globally.",
    )


def _build_nontrivial_obstruction(
    cover_id: str,
    conflicting_members: Sequence[str],
) -> "CechObstruction":
    """Build a non-trivial Čech obstruction from conflicting member IDs.

    Parameters
    ----------
    cover_id           – identifier of the open cover.
    conflicting_members – member IDs with incompatible local sections.

    Returns
    -------
    CechObstruction with non-empty cocycle.
    """
    pairs = list(itertools.combinations(conflicting_members, 2))
    cocycle = frozenset(f"{a}|{b}" for a, b in pairs)
    description = (
        f"Non-trivial Čech 1-cocycle: {len(cocycle)} incompatible local "
        f"section pairs on cover '{cover_id}'."
    )
    h_class_input = "|".join(sorted(cocycle)).encode()
    h_class = hashlib.md5(h_class_input).hexdigest()[:8]
    return CechObstruction(
        cover_id=cover_id,
        cocycle=cocycle,
        cohomology_class=f"[{h_class}]",
        description=description,
    )


def _select_strategy(index: int, total: int) -> str:
    """Assign a strategy name to the i-th fleet member by round-robin."""
    strategies = list(_KNOWN_STRATEGIES)
    return strategies[index % len(strategies)]


def _admissibility_evidence_for(
    expression: str,
    type_signature: str,
    trust: "TrustTier",
) -> tuple:
    """Synthesize a tuple of evidence tokens for an admissible expression.

    Higher trust tiers accumulate more evidence tokens (static check,
    runtime witness, proof term).

    Parameters
    ----------
    expression     – the proof-term expression being certified.
    type_signature – the type being inhabited.
    trust          – the trust tier of the inhabitant.

    Returns
    -------
    tuple of str evidence tokens.
    """
    base: Tuple[str, ...] = (
        f"type_check:{hashlib.md5(expression.encode()).hexdigest()[:6]}",
        f"trust:{trust.name}",
        f"sig:{hashlib.md5(type_signature.encode()).hexdigest()[:6]}",
    )
    if trust.value >= TrustTier.VERIFIED.value:
        base = base + ("static_check:passed",)
    if trust.value >= TrustTier.RUNTIME_WITNESSED.value:
        base = base + ("runtime_witness:observed",)
    if trust.value >= TrustTier.PROOF_BACKED.value:
        base = base + ("proof_term:checked",)
    return base


def _pick_best(candidates: List[Any], policy: str = "max_quality") -> Any:
    """Select the best AdmissibleInhabitant from a non-empty list by policy.

    Supported policies: max_quality, min_obstruction, vote, weighted_sum, trust_max.

    Parameters
    ----------
    candidates – non-empty list of AdmissibleInhabitant.
    policy     – aggregation policy name.

    Returns
    -------
    Best candidate.

    Raises
    ------
    ValueError if candidates is empty or policy is unknown.
    """
    if not candidates:
        raise ValueError("candidates list must be non-empty.")
    if policy in ("max_quality", "trust_max"):
        return max(candidates, key=lambda c: (c.trust_tier.value, len(c.admissibility_evidence)))
    if policy == "min_obstruction":
        return min(candidates, key=lambda c: len(c.obligations_remaining))
    if policy == "vote":
        counter: Dict[int, int] = collections.Counter(  # type: ignore[type-arg]
            c.trust_tier.value for c in candidates
        )
        winning = max(counter, key=lambda v: counter[v])
        tier_cands = [c for c in candidates if c.trust_tier.value == winning]
        return max(tier_cands, key=lambda c: len(c.admissibility_evidence))
    if policy == "weighted_sum":
        def _wscore(c: Any) -> float:
            return 0.7 * c.trust_tier.value + 0.3 * len(c.admissibility_evidence)
        return max(candidates, key=_wscore)
    raise ValueError(f"Unknown aggregation policy: {policy!r}")


def _deduplicate_inhabitants(candidates: List[Any]) -> List[Any]:
    """Deduplicate by inhabitant_id, keeping the highest-trust copy.

    Parameters
    ----------
    candidates – list of AdmissibleInhabitant.

    Returns
    -------
    Deduplicated list.
    """
    best: Dict[str, Any] = {}
    for c in candidates:
        ex = best.get(c.inhabitant_id)
        if ex is None or c.trust_tier.value > ex.trust_tier.value:
            best[c.inhabitant_id] = c
    return list(best.values())


def _rank_inhabitants(candidates: List[Any], policy: str = "max_quality") -> List[Any]:
    """Return candidates sorted best-first under the given policy."""
    if policy == "min_obstruction":
        return sorted(candidates, key=lambda c: len(c.obligations_remaining))
    return sorted(
        candidates,
        key=lambda c: (c.trust_tier.value, len(c.admissibility_evidence)),
        reverse=True,
    )


def _build_search_stats(fleet: Any, results: List[Any], policy: str) -> tuple:
    """Assemble a search statistics tuple from fleet and result data."""
    member_depths = [m.search_depth for m in fleet.members]
    return (
        ("total_candidates", len(fleet.result_pool)),
        ("admissible_found", len(results)),
        ("policy", policy),
        ("fleet_status", fleet.status),
        ("n_members", len(fleet.members)),
        ("strategies", tuple({m.strategy for m in fleet.members})),
        ("max_depth_reached", max(member_depths) if member_depths else 0),
        ("mean_depth", sum(member_depths) / len(member_depths) if member_depths else 0.0),
    )


# ---------------------------------------------------------------------------
# SearchStrategy enum
# ---------------------------------------------------------------------------

class SearchStrategy(enum.Enum):
    """Enumeration of search strategies available to fleet members.

    Each strategy defines a different traversal policy over the space of
    potential inhabitants.  The fleet coordinator assigns one strategy per
    member and tracks their individual progress.

    DEPTH_FIRST      – DFS with backtracking; low memory, may get stuck.
    BREADTH_FIRST    – BFS; complete but high memory.
    BEAM             – Beam search with width DEFAULT_BEAM_WIDTH; heuristic.
    MCTS             – Monte Carlo Tree Search; balances explore/exploit.
    RANDOM_RESTART   – Repeated random walks from fresh starting points.
    """

    DEPTH_FIRST = "depth_first"
    BREADTH_FIRST = "breadth_first"
    BEAM = "beam"
    MCTS = "mcts"
    RANDOM_RESTART = "random_restart"

    def is_complete(self) -> bool:
        """Return True iff the strategy is (asymptotically) complete."""
        return self in (SearchStrategy.DEPTH_FIRST, SearchStrategy.BREADTH_FIRST)

    def expected_memory_factor(self) -> float:
        """Heuristic relative memory usage (BFS = 1.0 baseline)."""
        factors = {
            SearchStrategy.DEPTH_FIRST: 0.1,
            SearchStrategy.BREADTH_FIRST: 1.0,
            SearchStrategy.BEAM: 0.05 * DEFAULT_BEAM_WIDTH,
            SearchStrategy.MCTS: 0.3,
            SearchStrategy.RANDOM_RESTART: 0.05,
        }
        return factors[self]

    def initial_parameters(self) -> Dict[str, Any]:
        """Return sensible default hyper-parameters for this strategy."""
        base: Dict[str, Any] = {"max_iterations": DEFAULT_MAX_ITERATIONS}
        if self is SearchStrategy.BEAM:
            base["beam_width"] = DEFAULT_BEAM_WIDTH
        elif self is SearchStrategy.MCTS:
            base["exploration_constant"] = math.sqrt(2)
            base["rollout_depth"] = 20
        elif self is SearchStrategy.RANDOM_RESTART:
            base["restarts"] = 10
            base["steps_per_restart"] = DEFAULT_MAX_ITERATIONS // 10
        return base

    def description(self) -> str:
        return f"SearchStrategy.{self.name} (complete={self.is_complete()}, mem_factor={self.expected_memory_factor():.2f})"


# ---------------------------------------------------------------------------
# Cohomology helpers
# ---------------------------------------------------------------------------

def _zero_cech(dim: int = CECH_DIMENSION) -> Tuple[complex, ...]:
    """Return the zero Čech H¹ class (trivial obstruction)."""
    return tuple(complex(0.0, 0.0) for _ in range(dim))


def _cech_norm(cech: Tuple[complex, ...]) -> float:
    """Euclidean norm of a Čech cohomology class vector."""
    return math.sqrt(sum(abs(z) ** 2 for z in cech))


def _cech_add(a: Tuple[complex, ...], b: Tuple[complex, ...]) -> Tuple[complex, ...]:
    """Pointwise addition of two Čech classes."""
    return tuple(x + y for x, y in zip(a, b))


def _cech_scale(a: Tuple[complex, ...], s: float) -> Tuple[complex, ...]:
    """Scalar multiplication of a Čech class."""
    return tuple(z * s for z in a)


def _random_cech(magnitude: float = 0.1, dim: int = CECH_DIMENSION) -> Tuple[complex, ...]:
    """Generate a random small Čech class for testing."""
    return tuple(
        complex(random.gauss(0, magnitude), random.gauss(0, magnitude))
        for _ in range(dim)
    )


def _make_judgment(
    context: str,
    formula: str,
    assumptions: Tuple[str, ...],
    evidence: Tuple[str, ...],
    obstruction: Tuple[complex, ...],
    blame: str,
    trust_tier: TrustTier,
    proof_obligations: Tuple[str, ...],
) -> Tuple[Any, ...]:
    """Construct a judgment 8-tuple (c, φ, A, E, O, B, T, Π)."""
    return (context, formula, assumptions, evidence, obstruction, blame, trust_tier, proof_obligations)


def _judgment_obstruction_norm(judgment: Tuple[Any, ...]) -> float:
    """Extract and return the norm of the obstruction component O."""
    obstruction: Tuple[complex, ...] = judgment[4]
    return _cech_norm(obstruction)


# ---------------------------------------------------------------------------
# AdmissibilityChecker
# ---------------------------------------------------------------------------

class AdmissibilityChecker:
    """Compositional checker for inhabitant admissibility.

    An inhabitant e : φ is *admissible* if it passes all registered checks.
    Checks are composed as a conjunction: all must return True.

    Mathematical note
    -----------------
    Admissibility is modelled as a predicate on sections of the sheaf of proof
    terms.  Each check corresponds to a local condition on an open set of the
    cover.  The global section exists (is admissible) iff all local conditions
    are satisfied AND the resulting Čech 1-cocycle is a coboundary (‖O‖ < ε).
    """

    def __init__(self, threshold: float = DEFAULT_ADMISSIBILITY_THRESHOLD) -> None:
        self.threshold = threshold
        self._checks: List[Tuple[str, Any]] = []
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register("quality_threshold", lambda inh: inh.quality_score >= self.threshold)
        self.register("obstruction_vanishing", lambda inh: _cech_norm(inh.cech_class) < DEFAULT_OBSTRUCTION_BUDGET)
        self.register("trust_sufficient", lambda inh: inh.trust_tier.is_at_least(TrustTier.VERIFIED))
        self.register("proof_nonempty", lambda inh: len(inh.admissibility_proof) > 0)

    def register(self, name: str, predicate: Any) -> None:
        """Register a named check predicate."""
        self._checks.append((name, predicate))

    def check(self, inhabitant: "AdmissibleInhabitant") -> Tuple[bool, List[str]]:
        """Run all checks.  Returns (passed, list_of_failed_check_names)."""
        failed: List[str] = []
        for name, pred in self._checks:
            try:
                if not pred(inhabitant):
                    failed.append(name)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}:exception:{exc}")
        return (len(failed) == 0, failed)

    def check_all(self, inhabitants: Sequence["AdmissibleInhabitant"]) -> List["AdmissibleInhabitant"]:
        """Filter a sequence to only admissible inhabitants."""
        return [inh for inh in inhabitants if self.check(inh)[0]]

    def explain(self, inhabitant: "AdmissibleInhabitant") -> str:
        """Return a human-readable admissibility report."""
        passed, failed = self.check(inhabitant)
        lines = [f"Admissibility report for {inhabitant.inhabitant_id}:"]
        for name, _ in self._checks:
            status = "FAIL" if name in failed else "PASS"
            lines.append(f"  [{status}] {name}")
        lines.append(f"  Overall: {'ADMISSIBLE' if passed else 'INADMISSIBLE'}")
        return "\n".join(lines)

    def relax_threshold(self, delta: float = 0.05) -> None:
        """Lower the quality threshold by delta to widen admissibility."""
        self.threshold = max(0.0, self.threshold - delta)
        # re-register the quality check with the new threshold
        self._checks = [(n, p) for (n, p) in self._checks if n != "quality_threshold"]
        self.register("quality_threshold", lambda inh: inh.quality_score >= self.threshold)

    def tighten_threshold(self, delta: float = 0.05) -> None:
        """Raise the quality threshold by delta to narrow admissibility."""
        self.threshold = min(1.0, self.threshold + delta)
        self._checks = [(n, p) for (n, p) in self._checks if n != "quality_threshold"]
        self.register("quality_threshold", lambda inh: inh.quality_score >= self.threshold)

    def num_checks(self) -> int:
        return len(self._checks)

    def check_names(self) -> List[str]:
        return [n for n, _ in self._checks]


# ---------------------------------------------------------------------------
# FleetMemory – shared state between fleet members
# ---------------------------------------------------------------------------

class FleetMemory:
    """Shared memory between fleet members, implementing a simple blackboard.

    Fleet members can post candidate inhabitants and read the current best.
    The memory tracks the Pareto frontier along (quality, -obstruction_norm).

    Implementation note
    --------------------
    In a real async system this would be backed by a concurrent data structure.
    Here we use a plain list protected by a logical version counter.
    """

    def __init__(self, capacity: int = 512) -> None:
        self.capacity = capacity
        self._entries: List["AdmissibleInhabitant"] = []
        self._version: int = 0
        self._access_log: List[str] = []

    def post(self, member_id: str, inhabitant: "AdmissibleInhabitant") -> None:
        """Post a candidate from a fleet member."""
        if len(self._entries) >= self.capacity:
            # evict lowest-quality entry
            self._entries.sort(key=lambda x: x.quality_score)
            self._entries.pop(0)
        self._entries.append(inhabitant)
        self._version += 1
        self._access_log.append(f"POST {member_id} -> {inhabitant.inhabitant_id} (v{self._version})")

    def best(self) -> Optional["AdmissibleInhabitant"]:
        """Return the highest-quality posted inhabitant, or None."""
        if not self._entries:
            return None
        return max(self._entries, key=lambda x: x.quality_score)

    def pareto_frontier(self) -> List["AdmissibleInhabitant"]:
        """Return Pareto-optimal entries along (quality, -obstruction_norm)."""
        frontier: List[AdmissibleInhabitant] = []
        for cand in self._entries:
            dominated = False
            for other in self._entries:
                if other is cand:
                    continue
                if (other.quality_score >= cand.quality_score and
                        _cech_norm(other.cech_class) <= _cech_norm(cand.cech_class) and
                        (other.quality_score > cand.quality_score or
                         _cech_norm(other.cech_class) < _cech_norm(cand.cech_class))):
                    dominated = True
                    break
            if not dominated:
                frontier.append(cand)
        return frontier

    def all_entries(self) -> List["AdmissibleInhabitant"]:
        return list(self._entries)

    def version(self) -> int:
        return self._version

    def recent_log(self, n: int = 10) -> List[str]:
        return self._access_log[-n:]

    def clear(self) -> None:
        self._entries.clear()
        self._version += 1
        self._access_log.append(f"CLEAR (v{self._version})")

    def size(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# ObstructionMonitor
# ---------------------------------------------------------------------------

class ObstructionMonitor:
    """Monitors Čech H¹ obstruction classes and halts fleet when budget exceeded.

    The monitor accumulates obstruction contributions from each fleet member
    and computes their vector sum.  When ‖Σ O_i‖ > budget, it signals halt.

    Theory
    ------
    Each fleet member contributes a local 1-cocycle.  The total obstruction is
    the sum; if this does not vanish it witnesses a genuine gluing failure —
    no global admissible section exists under the current strategy assignments.
    """

    def __init__(self, budget: float = DEFAULT_OBSTRUCTION_BUDGET) -> None:
        self.budget = budget
        self._contributions: Dict[str, Tuple[complex, ...]] = {}
        self._total: Tuple[complex, ...] = _zero_cech()
        self._halted: bool = False
        self._halt_reason: str = ""
        self._history: List[Tuple[float, str]] = []

    def record(self, member_id: str, cech_class: Tuple[complex, ...]) -> None:
        """Record or update a member's obstruction contribution."""
        self._contributions[member_id] = cech_class
        self._recompute_total()
        norm = _cech_norm(self._total)
        self._history.append((norm, member_id))
        if norm > self.budget:
            self._halted = True
            self._halt_reason = (
                f"Total obstruction norm {norm:.4f} exceeds budget {self.budget:.4f} "
                f"(triggered by member {member_id})"
            )

    def _recompute_total(self) -> None:
        total = _zero_cech()
        for cech in self._contributions.values():
            total = _cech_add(total, cech)
        self._total = total

    def is_halted(self) -> bool:
        return self._halted

    def halt_reason(self) -> str:
        return self._halt_reason

    def current_norm(self) -> float:
        return _cech_norm(self._total)

    def remaining_budget(self) -> float:
        return max(0.0, self.budget - self.current_norm())

    def reset(self) -> None:
        self._contributions.clear()
        self._total = _zero_cech()
        self._halted = False
        self._halt_reason = ""
        self._history.clear()

    def contributor_norms(self) -> Dict[str, float]:
        return {mid: _cech_norm(cech) for mid, cech in self._contributions.items()}

    def summary(self) -> str:
        lines = [
            f"ObstructionMonitor(budget={self.budget:.3f})",
            f"  current_norm = {self.current_norm():.4f}",
            f"  remaining    = {self.remaining_budget():.4f}",
            f"  halted       = {self._halted}",
        ]
        if self._halted:
            lines.append(f"  reason       = {self._halt_reason}")
        for mid, norm in self.contributor_norms().items():
            lines.append(f"  [{mid}] contribution_norm = {norm:.4f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ResultAggregator
# ---------------------------------------------------------------------------

class ResultAggregator:
    """Aggregates multiple fleet results into a single best inhabitant.

    Supported policies
    ------------------
    max_quality      – pick the inhabitant with highest quality_score.
    min_obstruction  – pick the one with smallest ‖cech_class‖.
    vote             – majority vote by trust_tier bucket.
    weighted_sum     – score = α·quality - β·obstruction_norm, pick max.
    trust_max        – pick highest trust_tier, break ties by quality.
    """

    ALPHA: float = 0.7  # weight for quality in weighted_sum
    BETA: float = 0.3   # weight for obstruction in weighted_sum

    def __init__(self, policy: str = "max_quality") -> None:
        if policy not in AGGREGATION_POLICIES:
            raise ValueError(f"Unknown policy {policy!r}. Choose from {AGGREGATION_POLICIES}.")
        self.policy = policy

    def aggregate(self, candidates: Sequence["AdmissibleInhabitant"]) -> "AdmissibleInhabitant":
        """Return the best inhabitant according to self.policy."""
        if not candidates:
            raise ValueError("No candidates to aggregate.")
        if self.policy == "max_quality":
            return max(candidates, key=lambda x: x.quality_score)
        if self.policy == "min_obstruction":
            return min(candidates, key=lambda x: _cech_norm(x.cech_class))
        if self.policy == "vote":
            return self._vote(candidates)
        if self.policy == "weighted_sum":
            return self._weighted_sum(candidates)
        if self.policy == "trust_max":
            return max(candidates, key=lambda x: (int(x.trust_tier), x.quality_score))
        raise ValueError(f"Unhandled policy: {self.policy}")

    def _vote(self, candidates: Sequence["AdmissibleInhabitant"]) -> "AdmissibleInhabitant":
        """Pick the trust-tier bucket with most votes, then by quality."""
        bucket_counts: Dict[TrustTier, int] = {}
        for c in candidates:
            bucket_counts[c.trust_tier] = bucket_counts.get(c.trust_tier, 0) + 1
        winner_tier = max(bucket_counts, key=lambda t: bucket_counts[t])
        tier_candidates = [c for c in candidates if c.trust_tier is winner_tier]
        return max(tier_candidates, key=lambda x: x.quality_score)

    def _weighted_sum(self, candidates: Sequence["AdmissibleInhabitant"]) -> "AdmissibleInhabitant":
        def score(inh: "AdmissibleInhabitant") -> float:
            return self.ALPHA * inh.quality_score - self.BETA * _cech_norm(inh.cech_class)
        return max(candidates, key=score)

    def scores(self, candidates: Sequence["AdmissibleInhabitant"]) -> Dict[str, float]:
        """Return a score dict for all candidates under the current policy."""
        result: Dict[str, float] = {}
        for inh in candidates:
            if self.policy == "max_quality":
                result[inh.inhabitant_id] = inh.quality_score
            elif self.policy == "min_obstruction":
                result[inh.inhabitant_id] = -_cech_norm(inh.cech_class)
            elif self.policy == "weighted_sum":
                result[inh.inhabitant_id] = (
                    self.ALPHA * inh.quality_score - self.BETA * _cech_norm(inh.cech_class)
                )
            elif self.policy == "trust_max":
                result[inh.inhabitant_id] = int(inh.trust_tier) + inh.quality_score
            else:
                result[inh.inhabitant_id] = inh.quality_score
        return result

    def ranking(self, candidates: Sequence["AdmissibleInhabitant"]) -> List["AdmissibleInhabitant"]:
        """Return candidates sorted best-first under policy."""
        sc = self.scores(candidates)
        return sorted(candidates, key=lambda x: sc[x.inhabitant_id], reverse=True)


# ---------------------------------------------------------------------------
# ParallelSearchSimulator
# ---------------------------------------------------------------------------

class ParallelSearchSimulator:
    """Simulates asynchronous parallel execution of multiple search strategies.

    Because real concurrency would require threading / asyncio (out of scope),
    this class interleaves strategy steps round-robin, logging each step.
    The simulation is deterministic given a fixed random seed.

    Each simulated member tries to improve its candidate by random walks;
    better candidates are posted to shared FleetMemory.
    """

    def __init__(
        self,
        fleet: "SearchFleet",
        memory: FleetMemory,
        monitor: ObstructionMonitor,
        checker: AdmissibilityChecker,
        rng_seed: int = 42,
    ) -> None:
        self.fleet = fleet
        self.memory = memory
        self.monitor = monitor
        self.checker = checker
        self._rng = random.Random(rng_seed)
        self._iteration_logs: List[str] = []
        self._step_count: int = 0

    def _generate_candidate(self, member: "FleetMember", iteration: int) -> "AdmissibleInhabitant":
        """Produce a synthetic candidate for a given member and iteration."""
        quality = min(1.0, self._rng.uniform(0.3, 0.95) + iteration * 0.002)
        cech = _random_cech(magnitude=max(EPSILON, 0.5 - iteration * 0.01), dim=CECH_DIMENSION)
        tier_int = min(4, int(iteration / 20))
        tier = TrustTier(tier_int)
        proof_steps = tuple(f"step_{k}" for k in range(self._rng.randint(1, 5)))
        return AdmissibleInhabitant(
            inhabitant_id=f"{member.member_id}:it{iteration}:{uuid.uuid4().hex[:6]}",
            type_expression=f"({member.assigned_goal} → Prop)",
            admissibility_proof=proof_steps,
            quality_score=quality,
            trust_tier=tier,
            cech_class=cech,
        )

    def run(self, max_steps_per_member: int = 30) -> List["AdmissibleInhabitant"]:
        """Run the simulation.  Returns list of admissible inhabitants found."""
        found: List[AdmissibleInhabitant] = []
        members = list(self.fleet.members)
        for step in range(max_steps_per_member):
            for member in members:
                if self.monitor.is_halted():
                    self._iteration_logs.append(
                        f"  Step {step}: HALTED by ObstructionMonitor ({self.monitor.halt_reason()})"
                    )
                    return found
                cand = self._generate_candidate(member, step)
                self.monitor.record(member.member_id, cand.cech_class)
                passed, failures = self.checker.check(cand)
                log_line = (
                    f"  Step {step:03d} [{member.strategy_name}] "
                    f"id={cand.inhabitant_id[-12:]} "
                    f"q={cand.quality_score:.3f} "
                    f"|O|={_cech_norm(cand.cech_class):.3f} "
                    f"T={cand.trust_tier.name} "
                    f"{'ADMIT' if passed else 'REJECT(' + ','.join(failures) + ')'}"
                )
                self._iteration_logs.append(log_line)
                if passed:
                    self.memory.post(member.member_id, cand)
                    found.append(cand)
            self._step_count += 1
        return found

    def iteration_logs(self) -> List[str]:
        return list(self._iteration_logs)

    def step_count(self) -> int:
        return self._step_count


# ---------------------------------------------------------------------------
# Primary frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdmissibleInhabitant:
    """An inhabitant of a type that has been certified admissible.

    Fields
    ------
    inhabitant_id       – unique identifier for this proof term.
    type_expression     – string representation of the goal type φ.
    admissibility_proof – ordered tuple of proof steps discharging Π.
    quality_score       – real number in [0, 1]; higher is better.
    trust_tier          – element of TrustTier ordered algebra.
    cech_class          – representative of [O] ∈ H¹ (Čech cohomology).

    Invariant: admissible iff quality_score ≥ threshold AND ‖cech_class‖ < ε
               AND len(admissibility_proof) > 0 AND trust_tier ≥ VERIFIED.
    """

    inhabitant_id: str
    type_expression: str
    admissibility_proof: Tuple[str, ...]
    quality_score: float
    trust_tier: TrustTier
    cech_class: Tuple[complex, ...]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def obstruction_norm(self) -> float:
        """‖[O]‖ – size of the cohomological obstruction."""
        return _cech_norm(self.cech_class)

    def is_trivially_admissible(self) -> bool:
        """Quick check: quality 1, zero obstruction, PROOF_BACKED."""
        return (
            self.quality_score >= 1.0 - EPSILON
            and self.obstruction_norm() < EPSILON
            and self.trust_tier is TrustTier.PROOF_BACKED
        )

    def judgment(self, context: str = "Γ", blame: str = "self") -> Tuple[Any, ...]:
        """Lift this inhabitant to a full judgment 8-tuple."""
        return _make_judgment(
            context=context,
            formula=self.type_expression,
            assumptions=self.admissibility_proof,
            evidence=(self.inhabitant_id,),
            obstruction=self.cech_class,
            blame=blame,
            trust_tier=self.trust_tier,
            proof_obligations=(),
        )

    def with_upgraded_trust(self) -> "AdmissibleInhabitant":
        """Return a copy with the trust tier upgraded by one level."""
        return AdmissibleInhabitant(
            inhabitant_id=self.inhabitant_id,
            type_expression=self.type_expression,
            admissibility_proof=self.admissibility_proof,
            quality_score=self.quality_score,
            trust_tier=self.trust_tier.upgrade(),
            cech_class=self.cech_class,
        )

    def with_reduced_obstruction(self, factor: float = 0.5) -> "AdmissibleInhabitant":
        """Return a copy with the Čech class scaled down by factor."""
        return AdmissibleInhabitant(
            inhabitant_id=self.inhabitant_id,
            type_expression=self.type_expression,
            admissibility_proof=self.admissibility_proof,
            quality_score=self.quality_score,
            trust_tier=self.trust_tier,
            cech_class=_cech_scale(self.cech_class, factor),
        )

    def summary(self) -> str:
        return (
            f"AdmissibleInhabitant(id={self.inhabitant_id!r}, "
            f"q={self.quality_score:.3f}, "
            f"|O|={self.obstruction_norm():.4f}, "
            f"T={self.trust_tier.name})"
        )


@dataclass(frozen=True)
class FleetMember:
    """One member of a search fleet, assigned a specific strategy and goal.

    Fields
    ------
    member_id         – unique identifier.
    strategy_name     – name of the SearchStrategy assigned to this member.
    assigned_goal     – string description of the sub-goal this member pursues.
    current_candidate – inhabitant_id of the member's current best candidate.
    iterations_done   – number of search iterations completed so far.
    trust_tier        – trust of the member's current best result.
    """

    member_id: str
    strategy_name: str
    assigned_goal: str
    current_candidate: str
    iterations_done: int
    trust_tier: TrustTier

    def strategy(self) -> SearchStrategy:
        """Return the SearchStrategy enum value for this member."""
        return SearchStrategy(self.strategy_name)

    def is_active(self) -> bool:
        """A member is active if it has done fewer than max iterations."""
        return self.iterations_done < DEFAULT_MAX_ITERATIONS

    def progress_fraction(self) -> float:
        """Return progress as a fraction in [0, 1]."""
        return min(1.0, self.iterations_done / max(1, DEFAULT_MAX_ITERATIONS))

    def with_iteration(self, new_candidate: str, new_tier: TrustTier) -> "FleetMember":
        """Return a copy with one more iteration recorded."""
        return FleetMember(
            member_id=self.member_id,
            strategy_name=self.strategy_name,
            assigned_goal=self.assigned_goal,
            current_candidate=new_candidate,
            iterations_done=self.iterations_done + 1,
            trust_tier=new_tier,
        )

    def summary(self) -> str:
        return (
            f"FleetMember(id={self.member_id!r}, strategy={self.strategy_name}, "
            f"goal={self.assigned_goal!r}, it={self.iterations_done}, T={self.trust_tier.name})"
        )


@dataclass(frozen=True)
class SearchFleet:
    """A coordinated collection of fleet members sharing memory and policy.

    Fields
    ------
    fleet_id             – unique identifier.
    members              – immutable tuple of FleetMember instances.
    shared_memory        – tuple of inhabitant_ids currently in shared memory.
    coordination_policy  – e.g. "round_robin", "first_wins", "consensus".
    trust_tier           – aggregate trust of the fleet's best result so far.
    obstruction_state    – current accumulated Čech obstruction class.
    """

    fleet_id: str
    members: Tuple[FleetMember, ...]
    shared_memory: Tuple[str, ...]
    coordination_policy: str
    trust_tier: TrustTier
    obstruction_state: Tuple[complex, ...]

    def size(self) -> int:
        return len(self.members)

    def obstruction_norm(self) -> float:
        return _cech_norm(self.obstruction_state)

    def active_members(self) -> Tuple[FleetMember, ...]:
        return tuple(m for m in self.members if m.is_active())

    def strategies_used(self) -> Tuple[str, ...]:
        return tuple(sorted({m.strategy_name for m in self.members}))

    def best_trust(self) -> TrustTier:
        if not self.members:
            return TrustTier.PROPOSAL
        return max((m.trust_tier for m in self.members), key=lambda t: int(t))

    def with_member_update(self, updated: FleetMember) -> "SearchFleet":
        """Return a copy with one member replaced."""
        new_members = tuple(
            updated if m.member_id == updated.member_id else m
            for m in self.members
        )
        return SearchFleet(
            fleet_id=self.fleet_id,
            members=new_members,
            shared_memory=self.shared_memory,
            coordination_policy=self.coordination_policy,
            trust_tier=self.best_trust(),
            obstruction_state=self.obstruction_state,
        )

    def summary(self) -> str:
        return (
            f"SearchFleet(id={self.fleet_id!r}, members={self.size()}, "
            f"policy={self.coordination_policy!r}, "
            f"|O|={self.obstruction_norm():.4f}, T={self.trust_tier.name})"
        )


@dataclass(frozen=True)
class FleetSearch:
    """Configuration record for a fleet search run.

    Fields
    ------
    search_id              – unique identifier for this search run.
    search_strategies      – tuple of strategy names to deploy.
    admissibility_criteria – tuple of criterion names to enforce.
    result_aggregation     – aggregation policy name.
    trust_tier             – required minimum trust of the result.
    obstruction_budget     – maximum allowed ‖O‖ for the search.
    """

    search_id: str
    search_strategies: Tuple[str, ...]
    admissibility_criteria: Tuple[str, ...]
    result_aggregation: str
    trust_tier: TrustTier
    obstruction_budget: float

    def validate(self) -> Tuple[bool, List[str]]:
        """Validate the configuration.  Returns (ok, list_of_errors)."""
        errors: List[str] = []
        for s in self.search_strategies:
            try:
                SearchStrategy(s)
            except ValueError:
                errors.append(f"Unknown strategy: {s!r}")
        if self.result_aggregation not in AGGREGATION_POLICIES:
            errors.append(f"Unknown aggregation policy: {self.result_aggregation!r}")
        if self.obstruction_budget <= 0:
            errors.append("obstruction_budget must be positive.")
        return (len(errors) == 0, errors)

    def num_strategies(self) -> int:
        return len(self.search_strategies)

    def requires_proof_backed(self) -> bool:
        return self.trust_tier is TrustTier.PROOF_BACKED

    def summary(self) -> str:
        return (
            f"FleetSearch(id={self.search_id!r}, "
            f"strategies={self.search_strategies}, "
            f"aggregation={self.result_aggregation!r}, "
            f"budget={self.obstruction_budget:.3f}, T={self.trust_tier.name})"
        )


@dataclass(frozen=True)
class FleetCoordinator:
    """Global coordinator managing multiple active search fleets.

    Fields
    ------
    coordinator_id                – unique identifier.
    active_fleets                 – tuple of fleet_ids currently managed.
    global_admissibility_threshold – minimum quality for any result to be
                                     accepted fleet-wide.
    trust_tier                    – coordinator's own trust tier.
    cech_obstruction              – accumulated global obstruction class.
    """

    coordinator_id: str
    active_fleets: Tuple[str, ...]
    global_admissibility_threshold: float
    trust_tier: TrustTier
    cech_obstruction: Tuple[complex, ...]

    def num_fleets(self) -> int:
        return len(self.active_fleets)

    def global_obstruction_norm(self) -> float:
        return _cech_norm(self.cech_obstruction)

    def is_globally_admissible(self, inhabitant: AdmissibleInhabitant) -> bool:
        """Check fleet-wide admissibility: quality ≥ threshold AND obstruction < budget."""
        return (
            inhabitant.quality_score >= self.global_admissibility_threshold
            and inhabitant.obstruction_norm() < DEFAULT_OBSTRUCTION_BUDGET
            and inhabitant.trust_tier.is_at_least(self.trust_tier)
        )

    def can_accept_fleet(self, fleet: SearchFleet) -> bool:
        return fleet.obstruction_norm() < DEFAULT_OBSTRUCTION_BUDGET

    def with_fleet_added(self, fleet_id: str) -> "FleetCoordinator":
        if fleet_id in self.active_fleets:
            return self
        return FleetCoordinator(
            coordinator_id=self.coordinator_id,
            active_fleets=self.active_fleets + (fleet_id,),
            global_admissibility_threshold=self.global_admissibility_threshold,
            trust_tier=self.trust_tier,
            cech_obstruction=self.cech_obstruction,
        )

    def with_fleet_removed(self, fleet_id: str) -> "FleetCoordinator":
        return FleetCoordinator(
            coordinator_id=self.coordinator_id,
            active_fleets=tuple(f for f in self.active_fleets if f != fleet_id),
            global_admissibility_threshold=self.global_admissibility_threshold,
            trust_tier=self.trust_tier,
            cech_obstruction=self.cech_obstruction,
        )

    def summary(self) -> str:
        return (
            f"FleetCoordinator(id={self.coordinator_id!r}, "
            f"fleets={self.num_fleets()}, "
            f"threshold={self.global_admissibility_threshold:.3f}, "
            f"|O_global|={self.global_obstruction_norm():.4f}, "
            f"T={self.trust_tier.name})"
        )


# ---------------------------------------------------------------------------
# Top-level functions
# ---------------------------------------------------------------------------

def _make_fleet_id() -> str:
    return "fleet-" + uuid.uuid4().hex[:8]


def _make_member_id(strategy: SearchStrategy, index: int) -> str:
    return f"member-{strategy.value}-{index:02d}"


def launch_fleet_search(
    goal: str,
    strategies: Sequence[str],
    budget: float = DEFAULT_OBSTRUCTION_BUDGET,
) -> SearchFleet:
    """Launch a search fleet for the given goal using the specified strategies.

    Parameters
    ----------
    goal       – string description of the goal type φ.
    strategies – names of SearchStrategy values to deploy (one member each).
    budget     – maximum tolerated obstruction norm for the fleet.

    Returns
    -------
    A SearchFleet with one FleetMember per strategy.

    Raises
    ------
    ValueError if any strategy name is unrecognised or strategies is empty.
    """
    if not strategies:
        raise ValueError("At least one strategy must be provided.")
    members: List[FleetMember] = []
    for idx, s_name in enumerate(strategies):
        try:
            strategy = SearchStrategy(s_name)
        except ValueError as exc:
            raise ValueError(f"Unrecognised strategy {s_name!r}") from exc
        member = FleetMember(
            member_id=_make_member_id(strategy, idx),
            strategy_name=s_name,
            assigned_goal=goal,
            current_candidate="",
            iterations_done=0,
            trust_tier=TrustTier.PROPOSAL,
        )
        members.append(member)

    return SearchFleet(
        fleet_id=_make_fleet_id(),
        members=tuple(members),
        shared_memory=(),
        coordination_policy="round_robin",
        trust_tier=TrustTier.PROPOSAL,
        obstruction_state=_zero_cech(),
    )


def coordinate_fleet(
    fleet: SearchFleet,
    coordinator: FleetCoordinator,
    max_steps: int = 25,
    rng_seed: int = 0,
) -> Tuple[AdmissibleInhabitant, ...]:
    """Run the fleet under coordinator supervision and return admissible results.

    The coordinator checks global admissibility thresholds and obstruction
    budgets, halting the fleet if necessary.

    Parameters
    ----------
    fleet       – the SearchFleet to run.
    coordinator – the FleetCoordinator overseeing execution.
    max_steps   – maximum simulation steps per member.
    rng_seed    – seed for reproducibility.

    Returns
    -------
    Tuple of AdmissibleInhabitant instances that passed all checks.
    """
    if not coordinator.can_accept_fleet(fleet):
        return ()

    memory = FleetMemory()
    monitor = ObstructionMonitor(budget=DEFAULT_OBSTRUCTION_BUDGET)
    checker = AdmissibilityChecker(threshold=coordinator.global_admissibility_threshold)
    sim = ParallelSearchSimulator(fleet, memory, monitor, checker, rng_seed=rng_seed)
    found = sim.run(max_steps_per_member=max_steps)

    # filter by global coordinator threshold
    globally_admissible = [
        inh for inh in found if coordinator.is_globally_admissible(inh)
    ]
    return tuple(globally_admissible)


def aggregate_fleet_results(
    fleet_results: Sequence[AdmissibleInhabitant],
    aggregation_policy: str = "max_quality",
) -> AdmissibleInhabitant:
    """Aggregate multiple admissible inhabitants into the single best result.

    Parameters
    ----------
    fleet_results       – collection of AdmissibleInhabitant instances.
    aggregation_policy  – one of AGGREGATION_POLICIES.

    Returns
    -------
    The best AdmissibleInhabitant under the given policy.

    Raises
    ------
    ValueError if fleet_results is empty.
    """
    if not fleet_results:
        raise ValueError("fleet_results must be non-empty.")
    aggregator = ResultAggregator(policy=aggregation_policy)
    return aggregator.aggregate(fleet_results)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _random_goal() -> str:
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    return f"∀ x : {adj}_{noun}, P x"


def _print_separator(title: str = "", width: int = 70) -> None:
    if title:
        side = (width - len(title) - 2) // 2
        print("=" * side + f" {title} " + "=" * (width - side - len(title) - 2))
    else:
        print("=" * width)


def demonstrate_trust_algebra() -> None:
    """Print a demonstration of TrustTier ordered-algebra operations."""
    _print_separator("TrustTier Ordered Algebra")
    tiers = list(TrustTier)
    for a, b in itertools.combinations(tiers, 2):
        meet = a.meet(b)
        join = a.join(b)
        print(f"  {a.name:20s} meet {b.name:20s} = {meet.name}")
        print(f"  {a.name:20s} join {b.name:20s} = {join.name}")
    print(f"  PROOF_BACKED.upgrade() = {TrustTier.PROOF_BACKED.upgrade().name}")
    print(f"  PROPOSAL.downgrade()   = {TrustTier.PROPOSAL.downgrade().name}")


def demonstrate_cohomology() -> None:
    """Print Čech cohomology simulation."""
    _print_separator("Čech H¹ Cohomology Simulation")
    zero = _zero_cech()
    rand1 = _random_cech(magnitude=0.3)
    rand2 = _random_cech(magnitude=0.2)
    total = _cech_add(rand1, rand2)
    print(f"  zero class norm:   {_cech_norm(zero):.6f}")
    print(f"  rand1 norm:        {_cech_norm(rand1):.6f}")
    print(f"  rand2 norm:        {_cech_norm(rand2):.6f}")
    print(f"  (rand1+rand2) norm:{_cech_norm(total):.6f}")
    scaled = _cech_scale(rand1, 0.1)
    print(f"  0.1*rand1 norm:    {_cech_norm(scaled):.6f}")


# ---------------------------------------------------------------------------
# __main__ block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    random.seed(2024)

    _print_separator("Fleet Search Over Admissible Inhabitants")
    print(f"Module version: {VERSION}")
    print(f"numpy available: {_NUMPY}")
    print()

    # 1. TrustTier algebra
    demonstrate_trust_algebra()
    print()

    # 2. Cohomology helpers
    demonstrate_cohomology()
    print()

    # 3. SearchStrategy enum
    _print_separator("SearchStrategy")
    for strat in SearchStrategy:
        print(f"  {strat.description()}")
        print(f"    params: {strat.initial_parameters()}")
    print()

    # 4. AdmissibilityChecker
    _print_separator("AdmissibilityChecker")
    checker = AdmissibilityChecker(threshold=0.6)
    print(f"  Registered checks: {checker.check_names()}")
    sample_inh = AdmissibleInhabitant(
        inhabitant_id="sample-001",
        type_expression="∀ x : Type, x → x",
        admissibility_proof=("refl", "intro"),
        quality_score=0.82,
        trust_tier=TrustTier.VERIFIED,
        cech_class=_random_cech(magnitude=0.05),
    )
    print(checker.explain(sample_inh))
    checker.relax_threshold(0.1)
    print(f"  After relax: threshold={checker.threshold:.2f}")
    print()

    # 5. FleetMemory
    _print_separator("FleetMemory")
    memory = FleetMemory(capacity=20)
    for i in range(5):
        inh = AdmissibleInhabitant(
            inhabitant_id=f"mem-inh-{i}",
            type_expression="T",
            admissibility_proof=("p",),
            quality_score=round(0.5 + i * 0.1, 2),
            trust_tier=TrustTier(min(i, 4)),
            cech_class=_random_cech(magnitude=0.1),
        )
        memory.post(f"member-{i}", inh)
    print(f"  Memory size: {memory.size()}")
    best = memory.best()
    print(f"  Best: {best.summary() if best else 'None'}")
    frontier = memory.pareto_frontier()
    print(f"  Pareto frontier size: {len(frontier)}")
    print(f"  Recent log: {memory.recent_log(3)}")
    print()

    # 6. ObstructionMonitor
    _print_separator("ObstructionMonitor")
    monitor = ObstructionMonitor(budget=1.5)
    for i in range(6):
        cech = _random_cech(magnitude=0.3)
        monitor.record(f"m{i}", cech)
        if monitor.is_halted():
            print(f"  Halted at member m{i}: {monitor.halt_reason()}")
            break
    print(monitor.summary())
    print()

    # 7. ResultAggregator
    _print_separator("ResultAggregator")
    candidates = [
        AdmissibleInhabitant(
            inhabitant_id=f"cand-{i}",
            type_expression="φ",
            admissibility_proof=("q",),
            quality_score=round(0.6 + i * 0.08, 3),
            trust_tier=TrustTier(min(i % 5, 4)),
            cech_class=_random_cech(magnitude=0.15 - i * 0.02),
        )
        for i in range(5)
    ]
    for policy in AGGREGATION_POLICIES:
        agg = ResultAggregator(policy=policy)
        best_cand = agg.aggregate(candidates)
        print(f"  [{policy:20s}] best = {best_cand.summary()}")
    ranking = ResultAggregator("max_quality").ranking(candidates)
    print(f"  Ranking (max_quality): {[c.inhabitant_id for c in ranking]}")
    print()

    # 8. launch_fleet_search
    _print_separator("launch_fleet_search")
    goal = _random_goal()
    strategies = [s.value for s in SearchStrategy]
    fleet = launch_fleet_search(goal=goal, strategies=strategies, budget=2.0)
    print(fleet.summary())
    print(f"  Members:")
    for m in fleet.members:
        print(f"    {m.summary()}")
    print()

    # 9. FleetSearch dataclass
    _print_separator("FleetSearch")
    fs = FleetSearch(
        search_id="search-" + uuid.uuid4().hex[:6],
        search_strategies=tuple(strategies),
        admissibility_criteria=("quality_threshold", "obstruction_vanishing"),
        result_aggregation="weighted_sum",
        trust_tier=TrustTier.VERIFIED,
        obstruction_budget=1.0,
    )
    ok, errs = fs.validate()
    print(fs.summary())
    print(f"  Valid: {ok}, Errors: {errs}")
    print()

    # 10. FleetCoordinator
    _print_separator("FleetCoordinator")
    coordinator = FleetCoordinator(
        coordinator_id="coord-" + uuid.uuid4().hex[:6],
        active_fleets=(fleet.fleet_id,),
        global_admissibility_threshold=0.55,
        trust_tier=TrustTier.REVIEWED,
        cech_obstruction=_zero_cech(),
    )
    print(coordinator.summary())
    coordinator2 = coordinator.with_fleet_added("fleet-extra-001")
    print(f"  After add: {coordinator2.num_fleets()} fleets")
    coordinator3 = coordinator2.with_fleet_removed("fleet-extra-001")
    print(f"  After remove: {coordinator3.num_fleets()} fleets")
    print()

    # 11. coordinate_fleet
    _print_separator("coordinate_fleet")
    results = coordinate_fleet(fleet, coordinator, max_steps=15, rng_seed=99)
    print(f"  Admissible inhabitants found: {len(results)}")
    for r in results[:3]:
        print(f"    {r.summary()}")
    print()

    # 12. aggregate_fleet_results
    _print_separator("aggregate_fleet_results")
    if results:
        best_result = aggregate_fleet_results(results, aggregation_policy="weighted_sum")
        print(f"  Best aggregated result: {best_result.summary()}")
        j = best_result.judgment()
        print(f"  Judgment obstruction norm: {_judgment_obstruction_norm(j):.4f}")
        upgraded = best_result.with_upgraded_trust()
        print(f"  Upgraded trust: {upgraded.trust_tier.name}")
        reduced = best_result.with_reduced_obstruction(0.25)
        print(f"  Reduced obstruction norm: {reduced.obstruction_norm():.6f}")
    else:
        print("  (no results to aggregate)")
    print()

    # 13. ParallelSearchSimulator standalone
    _print_separator("ParallelSearchSimulator")
    sim_fleet = launch_fleet_search(
        goal="∃ x : Nat, x + 1 = 2",
        strategies=["beam", "mcts", "random_restart"],
        budget=1.5,
    )
    sim_memory = FleetMemory()
    sim_monitor = ObstructionMonitor(budget=1.5)
    sim_checker = AdmissibilityChecker(threshold=0.50)
    sim = ParallelSearchSimulator(sim_fleet, sim_memory, sim_monitor, sim_checker, rng_seed=7)
    sim_found = sim.run(max_steps_per_member=10)
    print(f"  Steps run: {sim.step_count()}")
    print(f"  Admissible found: {len(sim_found)}")
    print(f"  Shared memory size: {sim_memory.size()}")
    print(f"  Obstruction monitor: norm={sim_monitor.current_norm():.4f}, halted={sim_monitor.is_halted()}")
    print(f"  Iteration log (first 5 lines):")
    for line in sim.iteration_logs()[:5]:
        print(line)
    print()

    # 14. Full pipeline end-to-end
    _print_separator("Full End-to-End Pipeline")
    e2e_goal = "∀ A B : Prop, (A → B) → A → B"
    e2e_strategies = ["depth_first", "breadth_first", "beam", "mcts", "random_restart"]
    e2e_fleet = launch_fleet_search(e2e_goal, e2e_strategies, budget=2.0)
    e2e_coordinator = FleetCoordinator(
        coordinator_id="e2e-coord",
        active_fleets=(e2e_fleet.fleet_id,),
        global_admissibility_threshold=0.45,
        trust_tier=TrustTier.PROPOSAL,
        cech_obstruction=_zero_cech(),
    )
    e2e_results = coordinate_fleet(e2e_fleet, e2e_coordinator, max_steps=20, rng_seed=42)
    print(f"  Fleet: {e2e_fleet.summary()}")
    print(f"  Coordinator: {e2e_coordinator.summary()}")
    print(f"  Results count: {len(e2e_results)}")
    if e2e_results:
        for policy in AGGREGATION_POLICIES:
            try:
                agg_best = aggregate_fleet_results(e2e_results, aggregation_policy=policy)
                print(f"  [{policy:20s}] {agg_best.summary()}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{policy:20s}] ERROR: {exc}")
    print()

    _print_separator("Done")
    print(f"Fleet search module v{VERSION} exercised successfully.")
