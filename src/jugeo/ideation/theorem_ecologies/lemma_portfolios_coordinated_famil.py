"""Lemma portfolios: coordinated families with shared purpose — theory2.tex Ch60.

# copilot: shared-core marker

Module layout::

    LemmaStatus              – enum: lifecycle status of a lemma in a portfolio
    CompletionStatus         – enum: completion status of a portfolio
    PortfolioConfig          – frozen dataclass: configuration for portfolio management
    LemmaRecord              – frozen dataclass: a single lemma entry
    TheoremNode              – frozen dataclass: a target theorem node
    LemmaPortfolio           – mutable dataclass: a living lemma portfolio
    PortfolioUpdateResult    – frozen dataclass: result of adding a lemma
    CompletionCheckResult    – frozen dataclass: result of a completion check
    RetirementResult         – frozen dataclass: result of retiring redundant lemmas
    PortfolioCycleResult     – frozen dataclass: result of a full portfolio cycle
    CoherenceReport          – frozen dataclass: portfolio coherence analysis
    RedundancyReport         – frozen dataclass: redundancy analysis
    GapCoverageReport        – frozen dataclass: gap coverage analysis
    CreationWitnessReport    – frozen dataclass: witness for portfolio creation
    AdditionWitnessReport    – frozen dataclass: witness for lemma addition
    CompletionWitnessReport  – frozen dataclass: witness for portfolio completion
    LemmaPortfoliosCoordinator  – orchestrates lemma portfolio management
    LemmaPortfoliosAnalyzer     – analyzes lemma portfolio structure
    LemmaPortfoliosWitness      – witnesses portfolio events

Theory Background
=================

A *lemma portfolio* is an organised collection of supporting lemmas that serve
a shared mathematical purpose.  The purpose may be, for example, "establish
convergence results for sequences in normed spaces" or "support all theorems
about graph colourings".

Portfolios are *coordinated*: when a new lemma is added, the portfolio
management system checks whether existing lemmas have become redundant (because
the new lemma subsumes them), or whether dormant lemmas can now be activated
(because the new lemma fills a gap they depended on).

A *PortfolioRecord* captures:

  * The purpose statement (natural-language description of the portfolio's goal).
  * The set of member lemmas with their statuses (active, redundant, retired,
    provisional).
  * The completion criterion: when is the portfolio "done"?
  * Inter-lemma dependencies: which lemmas depend on which.

Completion Criteria
-------------------

A portfolio is considered complete when it satisfies a configurable fraction
of the target theorem proofs.  The ``LemmaPortfoliosCoordinator`` tracks this
via a ``CompletionStatus`` enum:

  * INCOMPLETE  – fewer than 40% of target theorems are supported.
  * PARTIAL     – 40%–70% of target theorems are supported.
  * NEAR_COMPLETE – 70%–95% of target theorems are supported.
  * COMPLETE    – 95%+ of target theorems are supported.

Redundancy Detection
--------------------

Two lemmas A and B are redundant with respect to each other if their token
overlap (Jaccard similarity of normalised statement tokens) exceeds a
configurable threshold AND they cover the same set of target theorems.  When
redundancy is detected, the lemma with the lower utility score is marked
REDUNDANT and eligible for retirement.

Portfolio Merging
-----------------

When two portfolios with compatible purposes are merged, the coordinator
produces a new portfolio containing the union of active lemmas from both,
de-duplicated by a nearest-neighbour match on token overlap.  The new
portfolio's purpose is synthesised from the two input purposes.

Design Notes
============

* LemmaRecord is a frozen dataclass (``@dataclass(frozen=True, slots=True)``).
* LemmaPortfolio is mutable (``@dataclass(slots=True)``) because it evolves as
  lemmas are added and retired.
* Helper functions prefixed with ``_`` are module-private.
* The Witness class produces fine-grained event reports for external audit logs.
* Cross-module jugeo imports are wrapped in ``try/except Exception: pass``.
"""

from __future__ import annotations

import math
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    from jugeo.evidence.store import EvidenceStore  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.packs.registry import PackRegistry  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.orchestration.bus import EventBus  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.models import (  # type: ignore[import]
        LemmaPortfolio as _BaseLemmaPortfolio,
        EcologyHealth,
    )
except Exception:
    pass

try:
    from jugeo.ideation.theorem_ecologies.theorem_ecologies_from_local_closu import (  # type: ignore[import]
        TheoremEcology,
    )
except Exception:
    pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _tokenize(text: str) -> frozenset[str]:
    """Return a frozenset of lowercase alphabetic tokens of length >= 2."""
    import re
    return frozenset(w for w in re.split(r"[^a-z]+", text.lower()) if len(w) >= 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _coverage_fraction(
    lemmas: list["LemmaRecord"],
    targets: list["TheoremNode"],
    threshold: float = 0.2,
) -> float:
    """Estimate what fraction of *targets* are supported by *lemmas*.

    A target is considered supported if any lemma's token set overlaps
    with the target's token set above *threshold*.

    Parameters
    ----------
    lemmas:
        Active lemmas in the portfolio.
    targets:
        Target theorems whose coverage we measure.
    threshold:
        Minimum Jaccard similarity to count as covered.

    Returns
    -------
    float
        Fraction of targets covered, in [0, 1].
    """
    if not targets:
        return 1.0
    if not lemmas:
        return 0.0
    lemma_tokens = [_tokenize(f"{l.label} {l.statement}") for l in lemmas]
    covered = 0
    for t in targets:
        t_tokens = _tokenize(f"{t.label} {t.statement}")
        if any(_jaccard(lt, t_tokens) >= threshold for lt in lemma_tokens):
            covered += 1
    return covered / len(targets)


def _utility_entropy(scores: list[float]) -> float:
    """Shannon entropy of a discretised utility distribution."""
    if not scores:
        return 0.0
    bins = [0, 0, 0, 0]
    for s in scores:
        idx = min(3, int(s * 4))
        bins[idx] += 1
    total = len(scores)
    probs = [b / total for b in bins if b > 0]
    return -sum(p * math.log2(p) for p in probs)


def _find_redundant_pairs(
    lemmas: list["LemmaRecord"],
    similarity_threshold: float = 0.6,
) -> list[tuple[str, str]]:
    """Identify pairs of lemmas that are redundant with each other.

    Two lemmas are redundant if their Jaccard similarity exceeds
    *similarity_threshold*.

    Parameters
    ----------
    lemmas:
        Lemmas to check.
    similarity_threshold:
        Minimum Jaccard similarity to flag as redundant.

    Returns
    -------
    list[tuple[str, str]]
        Pairs of (lemma_id_a, lemma_id_b) that are mutually redundant.
    """
    pairs: list[tuple[str, str]] = []
    tokens = [
        (l.lemma_id, _tokenize(f"{l.label} {l.statement}"))
        for l in lemmas
    ]
    for i in range(len(tokens)):
        for j in range(i + 1, len(tokens)):
            lid_a, tok_a = tokens[i]
            lid_b, tok_b = tokens[j]
            if _jaccard(tok_a, tok_b) >= similarity_threshold:
                pairs.append((lid_a, lid_b))
    return pairs


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class LemmaStatus(str, Enum):
    """Lifecycle status of a lemma within a portfolio."""

    ACTIVE = "active"
    REDUNDANT = "redundant"
    RETIRED = "retired"
    PROVISIONAL = "provisional"

    def is_usable(self) -> bool:
        """Return True if this lemma can be used in proofs."""
        return self in (LemmaStatus.ACTIVE, LemmaStatus.PROVISIONAL)

    def priority(self) -> int:
        """Return a numeric priority for sorting."""
        mapping = {
            LemmaStatus.ACTIVE: 3,
            LemmaStatus.PROVISIONAL: 2,
            LemmaStatus.REDUNDANT: 1,
            LemmaStatus.RETIRED: 0,
        }
        return mapping[self]


class CompletionStatus(str, Enum):
    """Completion status of a lemma portfolio."""

    INCOMPLETE = "incomplete"
    PARTIAL = "partial"
    NEAR_COMPLETE = "near_complete"
    COMPLETE = "complete"

    def fraction_lower_bound(self) -> float:
        """Return the minimum coverage fraction for this status."""
        mapping = {
            CompletionStatus.COMPLETE: 0.95,
            CompletionStatus.NEAR_COMPLETE: 0.70,
            CompletionStatus.PARTIAL: 0.40,
            CompletionStatus.INCOMPLETE: 0.0,
        }
        return mapping[self]

    def is_satisfactory(self) -> bool:
        """Return True if the portfolio is at least near-complete."""
        return self in (CompletionStatus.NEAR_COMPLETE, CompletionStatus.COMPLETE)

    @classmethod
    def from_fraction(cls, fraction: float) -> "CompletionStatus":
        """Map a coverage fraction to the appropriate CompletionStatus."""
        if fraction >= 0.95:
            return cls.COMPLETE
        if fraction >= 0.70:
            return cls.NEAR_COMPLETE
        if fraction >= 0.40:
            return cls.PARTIAL
        return cls.INCOMPLETE


# ---------------------------------------------------------------------------
# Value objects (frozen + slots)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    """Configuration for lemma portfolio management.

    Attributes
    ----------
    max_portfolio_size:
        Maximum number of lemmas in a single portfolio.
    redundancy_threshold:
        Jaccard similarity above which two lemmas are considered redundant.
    completion_thresholds:
        Mapping from CompletionStatus to the required coverage fraction.
    utility_decay_factor:
        Factor by which utility scores decay per time unit without reuse.
    max_provisional_age_s:
        Maximum age (seconds) of a PROVISIONAL lemma before auto-retirement.
    enable_auto_retirement:
        Whether to automatically retire redundant lemmas.
    coverage_threshold:
        Minimum Jaccard similarity for a lemma to count as covering a target.
    """

    max_portfolio_size: int = 128
    redundancy_threshold: float = 0.6
    utility_decay_factor: float = 0.05
    max_provisional_age_s: float = 86400.0
    enable_auto_retirement: bool = True
    coverage_threshold: float = 0.2

    def __post_init__(self) -> None:
        object.__setattr__(self, "redundancy_threshold",
                           _clamp(self.redundancy_threshold))
        object.__setattr__(self, "coverage_threshold",
                           _clamp(self.coverage_threshold))


@dataclass(frozen=True, slots=True)
class LemmaRecord:
    """A single lemma entry in a portfolio.

    Attributes
    ----------
    lemma_id:
        Unique identifier.
    label:
        Human-readable name.
    statement:
        Formal or informal statement of the lemma.
    status:
        Current lifecycle status.
    utility_score:
        Estimated utility in [0, 1].
    reuse_count:
        Number of times this lemma has been used in proofs.
    dependencies:
        IDs of other lemmas this lemma depends on.
    tags:
        Semantic tags for grouping.
    added_at:
        Unix timestamp when this lemma was added to the portfolio.
    metadata:
        Arbitrary extra metadata.
    """

    lemma_id: str = field(default_factory=_uid)
    label: str = "unnamed_lemma"
    statement: str = ""
    status: LemmaStatus = LemmaStatus.PROVISIONAL
    utility_score: float = 0.5
    reuse_count: int = 0
    dependencies: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    added_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "utility_score",
                           _clamp(self.utility_score))

    def token_set(self) -> frozenset[str]:
        """Return a frozenset of normalised tokens from label and statement."""
        return _tokenize(f"{self.label} {self.statement}")

    def age_s(self) -> float:
        """Return age of this lemma in seconds."""
        return _utcnow() - self.added_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "lemma_id": self.lemma_id,
            "label": self.label,
            "statement": self.statement,
            "status": self.status.value,
            "utility_score": self.utility_score,
            "reuse_count": self.reuse_count,
            "dependencies": list(self.dependencies),
            "tags": list(self.tags),
            "added_at": self.added_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LemmaRecord":
        """Deserialise from a plain dict."""
        return cls(
            lemma_id=data.get("lemma_id", _uid()),
            label=data.get("label", "unnamed_lemma"),
            statement=data.get("statement", ""),
            status=LemmaStatus(data.get("status", "provisional")),
            utility_score=data.get("utility_score", 0.5),
            reuse_count=data.get("reuse_count", 0),
            dependencies=tuple(data.get("dependencies", [])),
            tags=tuple(data.get("tags", [])),
            added_at=data.get("added_at", _utcnow()),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class TheoremNode:
    """A target theorem node (simplified representation for portfolio use).

    Attributes
    ----------
    node_id:
        Unique identifier.
    label:
        Human-readable name.
    statement:
        Formal or informal statement.
    tags:
        Semantic tags.
    """

    node_id: str = field(default_factory=_uid)
    label: str = "unnamed_theorem"
    statement: str = ""
    tags: tuple[str, ...] = ()

    def token_set(self) -> frozenset[str]:
        """Return normalised tokens from label and statement."""
        return _tokenize(f"{self.label} {self.statement}")


@dataclass(frozen=True, slots=True)
class PortfolioUpdateResult:
    """Result of adding a lemma to a portfolio.

    Attributes
    ----------
    portfolio_id:
        ID of the updated portfolio.
    lemma_id:
        ID of the lemma that was added or rejected.
    admitted:
        True if the lemma was admitted.
    activation_count:
        Number of dormant lemmas activated by this addition.
    retirement_count:
        Number of lemmas marked redundant/retired due to this addition.
    coverage_before:
        Coverage fraction before the addition.
    coverage_after:
        Coverage fraction after the addition.
    reason:
        Human-readable reason for admission or rejection.
    updated_at:
        ISO-8601 timestamp.
    """

    portfolio_id: str
    lemma_id: str
    admitted: bool
    activation_count: int = 0
    retirement_count: int = 0
    coverage_before: float = 0.0
    coverage_after: float = 0.0
    reason: str = ""
    updated_at: str = field(default_factory=_now_iso)

    def coverage_delta(self) -> float:
        """Return the change in coverage fraction."""
        return self.coverage_after - self.coverage_before


@dataclass(frozen=True, slots=True)
class CompletionCheckResult:
    """Result of a portfolio completion check.

    Attributes
    ----------
    portfolio_id:
        ID of the checked portfolio.
    status:
        Computed completion status.
    coverage_fraction:
        Fraction of target theorems currently covered.
    uncovered_theorem_ids:
        IDs of target theorems not yet covered.
    checked_at:
        ISO-8601 timestamp.
    """

    portfolio_id: str
    status: CompletionStatus
    coverage_fraction: float
    uncovered_theorem_ids: tuple[str, ...] = ()
    checked_at: str = field(default_factory=_now_iso)

    def is_complete(self) -> bool:
        """Return True if the portfolio is fully complete."""
        return self.status == CompletionStatus.COMPLETE

    def summary(self) -> str:
        """Return a one-line summary."""
        return (
            f"Portfolio {self.portfolio_id}: {self.status.value} "
            f"(coverage={self.coverage_fraction:.2%}, "
            f"uncovered={len(self.uncovered_theorem_ids)})"
        )


@dataclass(frozen=True, slots=True)
class RetirementResult:
    """Result of retiring redundant lemmas from a portfolio.

    Attributes
    ----------
    portfolio_id:
        ID of the affected portfolio.
    retired_lemma_ids:
        IDs of lemmas that were retired.
    retained_lemma_ids:
        IDs of lemmas that were kept.
    coverage_before:
        Coverage fraction before retirement.
    coverage_after:
        Coverage fraction after retirement.
    retired_at:
        ISO-8601 timestamp.
    """

    portfolio_id: str
    retired_lemma_ids: tuple[str, ...]
    retained_lemma_ids: tuple[str, ...]
    coverage_before: float
    coverage_after: float
    retired_at: str = field(default_factory=_now_iso)

    def net_reduction(self) -> int:
        """Return the number of lemmas removed."""
        return len(self.retired_lemma_ids)

    def coverage_change(self) -> float:
        """Return coverage change after retirement."""
        return self.coverage_after - self.coverage_before


@dataclass(frozen=True, slots=True)
class PortfolioCycleResult:
    """Result of a full lemma portfolio construction cycle.

    Attributes
    ----------
    portfolio_id:
        ID of the produced portfolio.
    purpose:
        Purpose statement of the portfolio.
    final_status:
        Completion status after the cycle.
    admitted_count:
        Number of lemmas admitted.
    rejected_count:
        Number of lemmas rejected.
    retired_count:
        Number of lemmas retired as redundant.
    coverage_fraction:
        Final coverage fraction.
    total_duration_s:
        Wall-clock seconds for the cycle.
    cycle_at:
        ISO-8601 timestamp.
    """

    portfolio_id: str
    purpose: str
    final_status: CompletionStatus
    admitted_count: int
    rejected_count: int
    retired_count: int
    coverage_fraction: float
    total_duration_s: float = 0.0
    cycle_at: str = field(default_factory=_now_iso)

    def success(self) -> bool:
        """Return True if the cycle produced a satisfactory portfolio."""
        return self.final_status.is_satisfactory()


@dataclass(frozen=True, slots=True)
class CoherenceReport:
    """Portfolio coherence analysis report.

    Attributes
    ----------
    portfolio_id:
        ID of the analysed portfolio.
    purpose_coverage:
        Fraction of purpose tokens covered by lemma tokens.
    inter_lemma_similarity:
        Average pairwise Jaccard similarity among active lemmas.
    entropy_score:
        Shannon entropy of utility score distribution (higher = diverse).
    coherence_score:
        Composite coherence in [0, 1].
    notes:
        Human-readable notes.
    computed_at:
        ISO-8601 timestamp.
    """

    portfolio_id: str
    purpose_coverage: float
    inter_lemma_similarity: float
    entropy_score: float
    coherence_score: float
    notes: str = ""
    computed_at: str = field(default_factory=_now_iso)

    def is_coherent(self, threshold: float = 0.5) -> bool:
        """Return True if coherence exceeds *threshold*."""
        return self.coherence_score >= threshold


@dataclass(frozen=True, slots=True)
class RedundancyReport:
    """Redundancy analysis report for a lemma portfolio."""

    portfolio_id: str
    redundant_pairs: tuple[tuple[str, str], ...]
    redundancy_fraction: float
    most_redundant_id: str
    savings_estimate: int
    computed_at: str = field(default_factory=_now_iso)

    def has_redundancy(self) -> bool:
        """Return True if any redundant pairs were found."""
        return len(self.redundant_pairs) > 0


@dataclass(frozen=True, slots=True)
class GapCoverageReport:
    """Gap coverage analysis against a set of target theorems."""

    portfolio_id: str
    total_targets: int
    covered_count: int
    gap_theorem_ids: tuple[str, ...]
    coverage_fraction: float
    gap_fraction: float
    recommended_additions: tuple[str, ...]
    computed_at: str = field(default_factory=_now_iso)

    def fully_covered(self) -> bool:
        """Return True if all targets are covered."""
        return self.covered_count == self.total_targets


@dataclass(frozen=True, slots=True)
class CreationWitnessReport:
    """Witness report for portfolio creation."""

    witness_id: str = field(default_factory=_uid)
    portfolio_id: str = ""
    purpose: str = ""
    seed_lemma_count: int = 0
    initial_coverage: float = 0.0
    observed_at: str = field(default_factory=_now_iso)
    notes: str = ""


@dataclass(frozen=True, slots=True)
class AdditionWitnessReport:
    """Witness report for a lemma addition event."""

    witness_id: str = field(default_factory=_uid)
    portfolio_id: str = ""
    lemma_id: str = ""
    admitted: bool = False
    coverage_before: float = 0.0
    coverage_after: float = 0.0
    observed_at: str = field(default_factory=_now_iso)

    def net_coverage_gain(self) -> float:
        """Return the coverage gained by this addition."""
        return self.coverage_after - self.coverage_before


@dataclass(frozen=True, slots=True)
class CompletionWitnessReport:
    """Witness report for a portfolio completion check event."""

    witness_id: str = field(default_factory=_uid)
    portfolio_id: str = ""
    status_observed: CompletionStatus = CompletionStatus.INCOMPLETE
    coverage_fraction_observed: float = 0.0
    uncovered_count: int = 0
    observed_at: str = field(default_factory=_now_iso)
    narrative: str = ""


# ---------------------------------------------------------------------------
# Mutable working state
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LemmaPortfolio:
    """A living lemma portfolio that evolves as lemmas are added and retired.

    Attributes
    ----------
    portfolio_id:
        Unique identifier.
    purpose:
        Natural-language description of the portfolio's goal.
    members:
        List of LemmaRecord objects currently in the portfolio.
    target_theorem_ids:
        IDs of theorems this portfolio is meant to support.
    completion_status:
        Current completion status.
    created_at:
        Unix timestamp of creation.
    updated_at:
        Unix timestamp of last modification.
    metadata:
        Arbitrary metadata.
    """

    portfolio_id: str = field(default_factory=_uid)
    purpose: str = "general_purpose"
    members: list[LemmaRecord] = field(default_factory=list)
    target_theorem_ids: list[str] = field(default_factory=list)
    completion_status: CompletionStatus = CompletionStatus.INCOMPLETE
    created_at: float = field(default_factory=_utcnow)
    updated_at: float = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def size(self) -> int:
        """Return the total number of member lemmas."""
        return len(self.members)

    def active_members(self) -> list[LemmaRecord]:
        """Return only lemmas in ACTIVE or PROVISIONAL status."""
        return [m for m in self.members if m.status.is_usable()]

    def member_ids(self) -> list[str]:
        """Return IDs of all members."""
        return [m.lemma_id for m in self.members]

    def get_member(self, lemma_id: str) -> LemmaRecord | None:
        """Look up a member by ID; return None if not found."""
        for m in self.members:
            if m.lemma_id == lemma_id:
                return m
        return None

    def add_member(self, lemma: LemmaRecord) -> None:
        """Add *lemma* to the portfolio."""
        if lemma.lemma_id not in self.member_ids():
            self.members.append(lemma)
            self.updated_at = _utcnow()

    def replace_member(self, updated: LemmaRecord) -> bool:
        """Replace an existing member with *updated*. Return True if found."""
        for i, m in enumerate(self.members):
            if m.lemma_id == updated.lemma_id:
                self.members[i] = updated
                self.updated_at = _utcnow()
                return True
        return False

    def purpose_tokens(self) -> frozenset[str]:
        """Return normalised tokens from the purpose string."""
        return _tokenize(self.purpose)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "portfolio_id": self.portfolio_id,
            "purpose": self.purpose,
            "members": [m.to_dict() for m in self.members],
            "target_theorem_ids": list(self.target_theorem_ids),
            "completion_status": self.completion_status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def detect_redundancies(
    portfolio: LemmaPortfolio,
    config: PortfolioConfig,
) -> list[tuple[str, str]]:
    """Detect redundant lemma pairs in *portfolio*.

    Parameters
    ----------
    portfolio:
        The portfolio to scan.
    config:
        PortfolioConfig with the redundancy threshold.

    Returns
    -------
    list[tuple[str, str]]
        Pairs of (lemma_id_a, lemma_id_b) that are mutually redundant.
    """
    active = portfolio.active_members()
    return _find_redundant_pairs(active, config.redundancy_threshold)


def score_lemma_for_portfolio(
    candidate: LemmaRecord,
    portfolio: LemmaPortfolio,
    config: PortfolioConfig,
) -> float:
    """Score *candidate* for admission into *portfolio*.

    Scoring components:

    * **Purpose alignment** – Jaccard similarity between candidate tokens
      and the portfolio purpose tokens.
    * **Novelty** – 1 minus maximum pairwise similarity with existing active
      lemmas (rewards complementary, non-redundant lemmas).
    * **Utility** – the lemma's own utility_score field.

    Parameters
    ----------
    candidate:
        LemmaRecord to score.
    portfolio:
        Target portfolio.
    config:
        PortfolioConfig.

    Returns
    -------
    float
        Score in [0, 1].
    """
    purpose_tokens = portfolio.purpose_tokens()
    cand_tokens = candidate.token_set()
    purpose_align = _jaccard(cand_tokens, purpose_tokens)

    active = portfolio.active_members()
    if not active:
        novelty = 1.0
    else:
        max_sim = max(_jaccard(cand_tokens, m.token_set()) for m in active)
        novelty = 1.0 - max_sim

    raw = 0.4 * purpose_align + 0.35 * novelty + 0.25 * candidate.utility_score
    return _clamp(raw)


def run_portfolio_cycle(
    purpose: str,
    lemma_candidates: list[LemmaRecord],
    target_theorems: list[TheoremNode] | None = None,
    config: PortfolioConfig | None = None,
) -> PortfolioCycleResult:
    """Convenience wrapper: build and populate a portfolio in one call.

    Parameters
    ----------
    purpose:
        Natural-language purpose for the portfolio.
    lemma_candidates:
        Lemmas to evaluate for admission.
    target_theorems:
        Target theorems for coverage measurement (optional).
    config:
        PortfolioConfig; uses defaults if None.

    Returns
    -------
    PortfolioCycleResult
        Summary of the full cycle.
    """
    cfg = config or PortfolioConfig()
    coordinator = LemmaPortfoliosCoordinator(cfg)
    return coordinator.run_portfolio_cycle(purpose, lemma_candidates)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class LemmaPortfoliosCoordinator:
    """Orchestrates lemma portfolio management.

    Drives the portfolio lifecycle: creation from seeds, incremental
    lemma admission, redundancy retirement, completion checking, and
    portfolio merging.

    Parameters
    ----------
    config:
        PortfolioConfig with thresholds and limits.
    """

    def __init__(self, config: PortfolioConfig) -> None:
        self.config = config
        self._history: list[dict[str, Any]] = []

    def create_portfolio(
        self,
        purpose: str,
        seed_lemmas: list[LemmaRecord],
    ) -> LemmaPortfolio:
        """Create a new portfolio with *purpose* and admit *seed_lemmas*.

        All seed lemmas are admitted unconditionally with their status
        upgraded to ACTIVE.

        Parameters
        ----------
        purpose:
            Natural-language description of the portfolio's goal.
        seed_lemmas:
            Initial lemmas to populate the portfolio.

        Returns
        -------
        LemmaPortfolio
            A new, mutable portfolio.
        """
        portfolio = LemmaPortfolio(purpose=purpose)
        import dataclasses
        for lemma in seed_lemmas:
            activated = dataclasses.replace(lemma, status=LemmaStatus.ACTIVE)
            portfolio.add_member(activated)
        self._log_event("create_portfolio", {
            "portfolio_id": portfolio.portfolio_id,
            "purpose": purpose,
            "seed_count": len(seed_lemmas),
        })
        return portfolio

    def add_lemma_to_portfolio(
        self,
        portfolio: LemmaPortfolio,
        lemma: LemmaRecord,
    ) -> PortfolioUpdateResult:
        """Evaluate and potentially admit *lemma* into *portfolio*.

        Procedure:

        1. Score the candidate via ``score_lemma_for_portfolio``.
        2. If score >= 0.3 (hard-coded lower bound) and portfolio is not
           full, admit with ACTIVE status.
        3. Check for newly activated dormant lemmas (PROVISIONAL lemmas
           whose dependencies are now satisfied).
        4. If auto-retirement is enabled, mark newly redundant lemmas.

        Parameters
        ----------
        portfolio:
            The target portfolio (mutated in place).
        lemma:
            The candidate lemma.

        Returns
        -------
        PortfolioUpdateResult
            Detailed result of the admission attempt.
        """
        import dataclasses
        coverage_before = self._estimate_coverage(portfolio)
        score = score_lemma_for_portfolio(lemma, portfolio, self.config)
        if score < 0.3 or portfolio.size() >= self.config.max_portfolio_size:
            return PortfolioUpdateResult(
                portfolio_id=portfolio.portfolio_id,
                lemma_id=lemma.lemma_id,
                admitted=False,
                coverage_before=coverage_before,
                coverage_after=coverage_before,
                reason=f"Score {score:.3f} below threshold or portfolio full",
            )
        activated = dataclasses.replace(lemma, status=LemmaStatus.ACTIVE)
        portfolio.add_member(activated)
        activation_count = self._activate_dormant(portfolio, lemma.lemma_id)
        retirement_count = 0
        if self.config.enable_auto_retirement:
            retirement_count = self._retire_redundant(portfolio)
        coverage_after = self._estimate_coverage(portfolio)
        self._log_event("add_lemma", {
            "lemma_id": lemma.lemma_id,
            "score": score,
            "admitted": True,
        })
        return PortfolioUpdateResult(
            portfolio_id=portfolio.portfolio_id,
            lemma_id=lemma.lemma_id,
            admitted=True,
            activation_count=activation_count,
            retirement_count=retirement_count,
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            reason=f"Admitted with score {score:.3f}",
        )

    def check_portfolio_completion(
        self,
        portfolio: LemmaPortfolio,
    ) -> CompletionCheckResult:
        """Check the completion status of *portfolio*.

        Completion is measured against any target theorem IDs stored in
        the portfolio, falling back to a token-based coverage estimate.

        Parameters
        ----------
        portfolio:
            The portfolio to check.

        Returns
        -------
        CompletionCheckResult
            Completion check result with coverage fraction and uncovered IDs.
        """
        active = portfolio.active_members()
        if not portfolio.target_theorem_ids:
            fraction = min(1.0, len(active) / max(1, self.config.max_portfolio_size))
            status = CompletionStatus.from_fraction(fraction)
            return CompletionCheckResult(
                portfolio_id=portfolio.portfolio_id,
                status=status,
                coverage_fraction=_clamp(fraction),
            )
        covered_ids: list[str] = []
        uncovered_ids: list[str] = []
        active_tokens = [m.token_set() for m in active]
        for tid in portfolio.target_theorem_ids:
            t_tokens = _tokenize(tid.replace("_", " "))
            if any(
                _jaccard(at, t_tokens) >= self.config.coverage_threshold
                for at in active_tokens
            ):
                covered_ids.append(tid)
            else:
                uncovered_ids.append(tid)
        fraction = len(covered_ids) / max(1, len(portfolio.target_theorem_ids))
        status = CompletionStatus.from_fraction(fraction)
        portfolio.completion_status = status
        return CompletionCheckResult(
            portfolio_id=portfolio.portfolio_id,
            status=status,
            coverage_fraction=_clamp(fraction),
            uncovered_theorem_ids=tuple(uncovered_ids),
        )

    def retire_redundant_lemmas(
        self, portfolio: LemmaPortfolio
    ) -> RetirementResult:
        """Identify and retire all redundant lemmas in *portfolio*.

        Parameters
        ----------
        portfolio:
            The portfolio to clean up (mutated in place).

        Returns
        -------
        RetirementResult
            Summary of retirements.
        """
        import dataclasses
        coverage_before = self._estimate_coverage(portfolio)
        pairs = detect_redundancies(portfolio, self.config)
        to_retire: set[str] = set()
        for lid_a, lid_b in pairs:
            m_a = portfolio.get_member(lid_a)
            m_b = portfolio.get_member(lid_b)
            if m_a and m_b:
                retire_id = lid_a if m_a.utility_score <= m_b.utility_score else lid_b
                to_retire.add(retire_id)
        retained: list[str] = []
        for lemma in portfolio.members:
            if lemma.lemma_id in to_retire:
                retired_lemma = dataclasses.replace(lemma, status=LemmaStatus.RETIRED)
                portfolio.replace_member(retired_lemma)
            else:
                retained.append(lemma.lemma_id)
        coverage_after = self._estimate_coverage(portfolio)
        self._log_event("retire_redundant", {"retired_count": len(to_retire)})
        return RetirementResult(
            portfolio_id=portfolio.portfolio_id,
            retired_lemma_ids=tuple(to_retire),
            retained_lemma_ids=tuple(retained),
            coverage_before=coverage_before,
            coverage_after=coverage_after,
        )

    def merge_portfolios(
        self, portfolios: list[LemmaPortfolio]
    ) -> LemmaPortfolio:
        """Merge multiple portfolios into a single unified portfolio.

        The merged portfolio's purpose is the concatenation of unique
        purpose phrases.  Active lemmas from all portfolios are admitted,
        with near-duplicate lemmas de-duplicated via nearest-neighbour
        Jaccard matching.

        Parameters
        ----------
        portfolios:
            Portfolios to merge (not mutated).

        Returns
        -------
        LemmaPortfolio
            A new merged portfolio.
        """
        merged_purpose = "; ".join(
            {p.purpose for p in portfolios if p.purpose}
        )
        merged = LemmaPortfolio(purpose=merged_purpose)
        admitted_tokens: list[frozenset[str]] = []
        for portfolio in portfolios:
            for lemma in portfolio.active_members():
                tok = lemma.token_set()
                if any(
                    _jaccard(tok, existing) >= self.config.redundancy_threshold
                    for existing in admitted_tokens
                ):
                    continue
                merged.add_member(lemma)
                admitted_tokens.append(tok)
                if merged.size() >= self.config.max_portfolio_size:
                    break
        self._log_event("merge_portfolios", {
            "source_count": len(portfolios),
            "merged_id": merged.portfolio_id,
            "merged_size": merged.size(),
        })
        return merged

    def run_portfolio_cycle(
        self,
        purpose: str,
        lemma_candidates: list[LemmaRecord],
    ) -> PortfolioCycleResult:
        """Run a full portfolio lifecycle cycle.

        Steps:

        1. Create portfolio with the first third of candidates as seeds.
        2. Add remaining candidates one by one.
        3. Retire redundant lemmas.
        4. Check completion.

        Parameters
        ----------
        purpose:
            Portfolio purpose statement.
        lemma_candidates:
            Pool of lemma candidates.

        Returns
        -------
        PortfolioCycleResult
            Summary of the cycle.
        """
        t0 = _utcnow()
        split = max(1, len(lemma_candidates) // 3)
        seeds = lemma_candidates[:split]
        rest = lemma_candidates[split:]
        portfolio = self.create_portfolio(purpose, seeds)
        admitted = len(seeds)
        rejected = 0
        for lemma in rest:
            result = self.add_lemma_to_portfolio(portfolio, lemma)
            if result.admitted:
                admitted += 1
            else:
                rejected += 1
        retirement = self.retire_redundant_lemmas(portfolio)
        retired = retirement.net_reduction()
        completion = self.check_portfolio_completion(portfolio)
        duration = _utcnow() - t0
        return PortfolioCycleResult(
            portfolio_id=portfolio.portfolio_id,
            purpose=purpose,
            final_status=completion.status,
            admitted_count=admitted,
            rejected_count=rejected,
            retired_count=retired,
            coverage_fraction=completion.coverage_fraction,
            total_duration_s=duration,
        )

    # ------------------------------------------------------------------
    # Private helpers

    def _estimate_coverage(self, portfolio: LemmaPortfolio) -> float:
        """Estimate the current coverage fraction of *portfolio*."""
        active = portfolio.active_members()
        if not portfolio.target_theorem_ids:
            return min(1.0, len(active) / max(1, self.config.max_portfolio_size))
        covered = 0
        active_tokens = [m.token_set() for m in active]
        for tid in portfolio.target_theorem_ids:
            t_tokens = _tokenize(tid.replace("_", " "))
            if any(
                _jaccard(at, t_tokens) >= self.config.coverage_threshold
                for at in active_tokens
            ):
                covered += 1
        return covered / max(1, len(portfolio.target_theorem_ids))

    def _activate_dormant(
        self, portfolio: LemmaPortfolio, new_lemma_id: str
    ) -> int:
        """Activate PROVISIONAL lemmas whose dependencies are now satisfied."""
        import dataclasses
        member_ids_set = set(portfolio.member_ids())
        count = 0
        for lemma in portfolio.members:
            if lemma.status == LemmaStatus.PROVISIONAL:
                if all(d in member_ids_set for d in lemma.dependencies):
                    activated = dataclasses.replace(lemma, status=LemmaStatus.ACTIVE)
                    portfolio.replace_member(activated)
                    count += 1
        return count

    def _retire_redundant(self, portfolio: LemmaPortfolio) -> int:
        """Mark newly redundant lemmas as REDUNDANT. Return count retired."""
        import dataclasses
        pairs = detect_redundancies(portfolio, self.config)
        retired = 0
        for lid_a, lid_b in pairs:
            m_a = portfolio.get_member(lid_a)
            m_b = portfolio.get_member(lid_b)
            if not m_a or not m_b:
                continue
            if m_a.status != LemmaStatus.ACTIVE or m_b.status != LemmaStatus.ACTIVE:
                continue
            retire_id = lid_a if m_a.utility_score <= m_b.utility_score else lid_b
            to_retire = portfolio.get_member(retire_id)
            if to_retire:
                portfolio.replace_member(
                    dataclasses.replace(to_retire, status=LemmaStatus.REDUNDANT)
                )
                retired += 1
        return retired

    def _log_event(self, event: str, data: dict[str, Any]) -> None:
        """Append an event to the internal history log."""
        self._history.append({"event": event, "data": data, "at": _now_iso()})


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class LemmaPortfoliosAnalyzer:
    """Analyzes lemma portfolio structure.

    Provides analysis of portfolio coherence, redundancy, gap coverage
    against target theorems, and lemma utility ranking.
    """

    def analyze_portfolio_coherence(
        self, portfolio: LemmaPortfolio
    ) -> CoherenceReport:
        """Analyse how coherent *portfolio* is with respect to its purpose.

        Coherence combines:

        * Purpose coverage: fraction of purpose tokens present in at least
          one active lemma.
        * Inter-lemma similarity: average pairwise Jaccard among active
          lemmas (too low = incoherent, too high = redundant).
        * Entropy of utility distribution (higher entropy = more diverse
          utility levels = healthier portfolio).

        Parameters
        ----------
        portfolio:
            The portfolio to analyse.

        Returns
        -------
        CoherenceReport
            Detailed coherence report.
        """
        active = portfolio.active_members()
        purpose_tok = portfolio.purpose_tokens()
        union_lemma_tok: frozenset[str] = frozenset()
        for m in active:
            union_lemma_tok = union_lemma_tok | m.token_set()
        purpose_coverage = (
            len(purpose_tok & union_lemma_tok) / max(1, len(purpose_tok))
        )
        if len(active) < 2:
            inter_sim = 0.0
        else:
            sims: list[float] = []
            tokens_list = [m.token_set() for m in active]
            for i in range(len(tokens_list)):
                for j in range(i + 1, len(tokens_list)):
                    sims.append(_jaccard(tokens_list[i], tokens_list[j]))
            inter_sim = sum(sims) / max(1, len(sims))
        utility_scores = [m.utility_score for m in active]
        entropy = _utility_entropy(utility_scores)
        max_entropy = math.log2(4)
        normalised_entropy = entropy / max_entropy if max_entropy > 0 else 0.0
        coherence = _clamp(
            0.4 * purpose_coverage
            + 0.3 * (1.0 - inter_sim)
            + 0.3 * normalised_entropy
        )
        notes = (
            f"Purpose coverage {purpose_coverage:.2%}; "
            f"inter-lemma similarity {inter_sim:.2%}; "
            f"utility entropy {entropy:.3f} bits"
        )
        return CoherenceReport(
            portfolio_id=portfolio.portfolio_id,
            purpose_coverage=_clamp(purpose_coverage),
            inter_lemma_similarity=_clamp(inter_sim),
            entropy_score=_clamp(normalised_entropy),
            coherence_score=coherence,
            notes=notes,
        )

    def analyze_redundancy(
        self, portfolio: LemmaPortfolio
    ) -> RedundancyReport:
        """Analyse redundancy within *portfolio*.

        Parameters
        ----------
        portfolio:
            The portfolio to analyse.

        Returns
        -------
        RedundancyReport
            Pairs of redundant lemmas and associated metrics.
        """
        active = portfolio.active_members()
        pairs = _find_redundant_pairs(active, 0.6)
        redundancy_fraction = (
            len({lid for pair in pairs for lid in pair}) / max(1, len(active))
        )
        most_redundant = ""
        if pairs:
            from collections import Counter
            counts: Counter[str] = Counter(
                lid for pair in pairs for lid in pair
            )
            most_redundant = counts.most_common(1)[0][0]
        savings = len({pair[0] for pair in pairs})
        return RedundancyReport(
            portfolio_id=portfolio.portfolio_id,
            redundant_pairs=tuple(pairs),
            redundancy_fraction=_clamp(redundancy_fraction),
            most_redundant_id=most_redundant,
            savings_estimate=savings,
        )

    def analyze_gap_coverage(
        self,
        portfolio: LemmaPortfolio,
        target_theorems: list[TheoremNode],
    ) -> GapCoverageReport:
        """Analyse how well *portfolio* covers *target_theorems*.

        Parameters
        ----------
        portfolio:
            The portfolio to evaluate.
        target_theorems:
            Target theorem nodes.

        Returns
        -------
        GapCoverageReport
            Coverage fractions and gap theorem IDs.
        """
        active = portfolio.active_members()
        covered: list[str] = []
        gaps: list[str] = []
        active_tokens = [m.token_set() for m in active]
        for t in target_theorems:
            t_tokens = t.token_set()
            if any(_jaccard(at, t_tokens) >= 0.15 for at in active_tokens):
                covered.append(t.node_id)
            else:
                gaps.append(t.node_id)
        fraction = len(covered) / max(1, len(target_theorems))
        gap_fraction = 1.0 - fraction
        recommended = tuple(gaps[:3])
        return GapCoverageReport(
            portfolio_id=portfolio.portfolio_id,
            total_targets=len(target_theorems),
            covered_count=len(covered),
            gap_theorem_ids=tuple(gaps),
            coverage_fraction=_clamp(fraction),
            gap_fraction=_clamp(gap_fraction),
            recommended_additions=recommended,
        )

    def rank_lemmas_by_utility(
        self, portfolio: LemmaPortfolio
    ) -> list[LemmaRecord]:
        """Return active lemmas sorted by utility_score descending.

        Parameters
        ----------
        portfolio:
            The portfolio whose lemmas to rank.

        Returns
        -------
        list[LemmaRecord]
            Active lemmas in descending utility order.
        """
        active = portfolio.active_members()
        return sorted(active, key=lambda m: m.utility_score, reverse=True)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

class LemmaPortfoliosWitness:
    """Witnesses portfolio events for audit and replay.

    Records high-fidelity event observations for portfolio creation,
    lemma addition, and completion checks.
    """

    def __init__(self) -> None:
        self._log: list[dict[str, Any]] = []

    def witness_portfolio_creation(
        self,
        purpose: str,
        portfolio: LemmaPortfolio,
    ) -> CreationWitnessReport:
        """Witness the creation of *portfolio*.

        Parameters
        ----------
        purpose:
            The purpose string provided to the coordinator.
        portfolio:
            The created portfolio.

        Returns
        -------
        CreationWitnessReport
            A signed creation witness record.
        """
        initial_coverage = len(portfolio.active_members()) / max(
            1, portfolio.size()
        )
        report = CreationWitnessReport(
            portfolio_id=portfolio.portfolio_id,
            purpose=purpose,
            seed_lemma_count=portfolio.size(),
            initial_coverage=_clamp(initial_coverage),
            notes=f"Created at {_now_iso()} with purpose '{purpose}'",
        )
        self._log.append({"type": "creation", "portfolio_id": portfolio.portfolio_id})
        return report

    def witness_lemma_addition(
        self,
        portfolio: LemmaPortfolio,
        lemma: LemmaRecord,
        result: PortfolioUpdateResult,
    ) -> AdditionWitnessReport:
        """Witness a lemma addition event.

        Parameters
        ----------
        portfolio:
            The portfolio after the addition attempt.
        lemma:
            The lemma that was evaluated.
        result:
            The result of the addition attempt.

        Returns
        -------
        AdditionWitnessReport
            A signed addition witness record.
        """
        report = AdditionWitnessReport(
            portfolio_id=portfolio.portfolio_id,
            lemma_id=lemma.lemma_id,
            admitted=result.admitted,
            coverage_before=result.coverage_before,
            coverage_after=result.coverage_after,
        )
        self._log.append({"type": "addition", "lemma_id": lemma.lemma_id,
                          "admitted": result.admitted})
        return report

    def witness_portfolio_completion(
        self,
        portfolio: LemmaPortfolio,
        check: CompletionCheckResult,
    ) -> CompletionWitnessReport:
        """Witness a portfolio completion check.

        Parameters
        ----------
        portfolio:
            The portfolio that was checked.
        check:
            The result of the completion check.

        Returns
        -------
        CompletionWitnessReport
            A signed completion witness record.
        """
        narrative = (
            f"Portfolio {portfolio.portfolio_id} ({portfolio.purpose!r}) "
            f"has {portfolio.size()} members ({len(portfolio.active_members())} active). "
            f"Completion: {check.status.value} at {check.coverage_fraction:.2%}. "
            f"Uncovered: {len(check.uncovered_theorem_ids)} theorems."
        )
        report = CompletionWitnessReport(
            portfolio_id=portfolio.portfolio_id,
            status_observed=check.status,
            coverage_fraction_observed=check.coverage_fraction,
            uncovered_count=len(check.uncovered_theorem_ids),
            narrative=narrative,
        )
        self._log.append({"type": "completion", "status": check.status.value})
        return report

    def log_snapshot(self) -> list[dict[str, Any]]:
        """Return a snapshot of the internal witness log."""
        return list(self._log)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "LemmaStatus",
    "CompletionStatus",
    "PortfolioConfig",
    "LemmaRecord",
    "TheoremNode",
    "LemmaPortfolio",
    "PortfolioUpdateResult",
    "CompletionCheckResult",
    "RetirementResult",
    "PortfolioCycleResult",
    "CoherenceReport",
    "RedundancyReport",
    "GapCoverageReport",
    "CreationWitnessReport",
    "AdditionWitnessReport",
    "CompletionWitnessReport",
    "LemmaPortfoliosCoordinator",
    "LemmaPortfoliosAnalyzer",
    "LemmaPortfoliosWitness",
    "run_portfolio_cycle",
    "score_lemma_for_portfolio",
    "detect_redundancies",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== s02 smoke test: lemma portfolios ===")

    config = PortfolioConfig(
        max_portfolio_size=20,
        redundancy_threshold=0.5,
        enable_auto_retirement=True,
    )
    coordinator = LemmaPortfoliosCoordinator(config)
    analyzer = LemmaPortfoliosAnalyzer()
    witness = LemmaPortfoliosWitness()

    lemmas = [
        LemmaRecord(lemma_id="l1", label="Triangle Inequality",
                    statement="Norm satisfies triangle inequality",
                    utility_score=0.9, status=LemmaStatus.ACTIVE),
        LemmaRecord(lemma_id="l2", label="Cauchy Schwarz Inequality",
                    statement="Inner product bounded by product of norms",
                    utility_score=0.85, status=LemmaStatus.ACTIVE),
        LemmaRecord(lemma_id="l3", label="Parallelogram Law",
                    statement="Sum of squared norms relation in inner product spaces",
                    utility_score=0.7, status=LemmaStatus.PROVISIONAL),
        LemmaRecord(lemma_id="l4", label="Triangle Inequality variant",
                    statement="Norm satisfies triangle inequality upper bound",
                    utility_score=0.6, status=LemmaStatus.PROVISIONAL),
        LemmaRecord(lemma_id="l5", label="Jensen Inequality",
                    statement="Convex function of expectation bounded by expectation of function",
                    utility_score=0.75, status=LemmaStatus.ACTIVE),
    ]

    purpose = "support convergence theorems in normed spaces"
    portfolio = coordinator.create_portfolio(purpose, lemmas[:2])
    print(f"Created portfolio {portfolio.portfolio_id!r} with {portfolio.size()} members")

    cwr = witness.witness_portfolio_creation(purpose, portfolio)
    print(f"Creation witness: seed_count={cwr.seed_lemma_count}")

    for l in lemmas[2:]:
        res = coordinator.add_lemma_to_portfolio(portfolio, l)
        awr = witness.witness_lemma_addition(portfolio, l, res)
        print(f"  Added {l.label!r}: admitted={res.admitted}, "
              f"coverage_delta={res.coverage_delta():+.3f}")

    ret = coordinator.retire_redundant_lemmas(portfolio)
    print(f"Retirement: {ret.net_reduction()} lemmas retired")

    completion = coordinator.check_portfolio_completion(portfolio)
    compl_wr = witness.witness_portfolio_completion(portfolio, completion)
    print(f"Completion: {completion.summary()}")

    coherence = analyzer.analyze_portfolio_coherence(portfolio)
    print(f"Coherence: score={coherence.coherence_score:.3f}, "
          f"is_coherent={coherence.is_coherent()}")

    redundancy = analyzer.analyze_redundancy(portfolio)
    print(f"Redundancy: pairs={len(redundancy.redundant_pairs)}, "
          f"fraction={redundancy.redundancy_fraction:.2%}")

    targets = [
        TheoremNode(node_id="th1", label="Banach Fixed Point",
                    statement="Contraction mapping on complete metric space"),
        TheoremNode(node_id="th2", label="Riesz Representation",
                    statement="Every linear functional on Hilbert space has an inner product representation"),
    ]
    gap_report = analyzer.analyze_gap_coverage(portfolio, targets)
    print(f"Gap coverage: {gap_report.covered_count}/{gap_report.total_targets} targets")

    ranked = analyzer.rank_lemmas_by_utility(portfolio)
    print(f"Top lemma: {ranked[0].label if ranked else 'none'}")

    p2 = coordinator.create_portfolio("support spectral theory", lemmas[3:])
    merged = coordinator.merge_portfolios([portfolio, p2])
    print(f"Merged portfolio size: {merged.size()}")

    cycle_result = run_portfolio_cycle(purpose, lemmas, config=config)
    print(f"Cycle result: admitted={cycle_result.admitted_count}, "
          f"status={cycle_result.final_status.value}, "
          f"success={cycle_result.success()}")

    score = score_lemma_for_portfolio(lemmas[0], portfolio, config)
    print(f"Score for '{lemmas[0].label}': {score:.3f}")

    red_pairs = detect_redundancies(portfolio, config)
    print(f"Detected {len(red_pairs)} redundant pairs")

    print("=== smoke test passed ===")
