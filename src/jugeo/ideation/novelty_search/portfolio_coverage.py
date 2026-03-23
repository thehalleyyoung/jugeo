"""Portfolio coverage analysis for novelty_search – theory2.tex Ch57.

Estimates, analyzes, and optimizes coverage of the idea space by an
IdeaPortfolio. Detects gaps, measures density, and guides exploration.

Module layout::

    CoverageConfig    – coverage computation settings
    CoverageEstimator – estimates coverage of idea portfolios
    GapDetector       – identifies under-covered regions
    CoverageOptimizer – selects ideas that maximize coverage gain
    DensityAnalyzer   – analyzes density distribution across domains
    CoverageReport    – dataclass capturing a full coverage snapshot
    CoverageReporter  – generates human-readable coverage reports

Background
----------
Coverage is a multi-dimensional concept.  We measure it along three axes:

1. **Domain coverage** – do the ideas span a broad set of
   ``target_area`` domains, or are they concentrated in one area?

2. **Purpose coverage** – are there ideas at every level of alignment
   with the stated research purpose, or is there a gap near the goal?

3. **Trust coverage** – does the portfolio include ideas at multiple
   epistemic levels (from speculative to mechanically verified)?

A portfolio with high coverage in all three dimensions is more robust: it
has both high-confidence foundation stones and exploratory speculative
directions, and it addresses many facets of the research goal.

References
----------
* theory2.tex §57.2 "Coverage Metrics for Idea Portfolios"
* theory2.tex §57.5 "Gap Detection and Exploration Guidance"
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.evidence.trust import TrustLevel
from jugeo.ideation.ideas import (
    Idea,
    IdeaPortfolio,
    GainProfile,
    ValidationPath,
    TrustStatus,
    IdeaEvaluator,
)
from jugeo.ideation.novelty import (
    NoveltyScore,
    NoveltySearcher,
    TheoremPortfolio,
    PurposeAlignmentChecker,
    NoveltyOptimizer,
    NoveltyHistory,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_EPSILON: float = 1e-9
_ALL_TRUST_LEVELS: list[TrustLevel] = list(TrustLevel)
_MIN_DOMAIN_TOKEN_LEN: int = 2

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *value* clamped to the closed interval ``[lo, hi]``.

    Parameters
    ----------
    value:
        The floating-point value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to ``0.0``.
    hi:
        Upper bound (inclusive).  Defaults to ``1.0``.

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string with timezone info."""
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Split *text* into a frozen set of lower-cased alpha tokens.

    Stop words and single-character tokens are removed.

    Parameters
    ----------
    text:
        Arbitrary natural-language or technical text.

    Returns
    -------
    frozenset[str]
        Immutable set of cleaned tokens.
    """
    _STOP = frozenset(
        {
            "a", "an", "the", "of", "in", "to", "for", "and", "or", "is",
            "are", "be", "by", "on", "as", "at", "it", "its", "that", "this",
            "with", "from", "we", "our", "their", "if", "then", "so", "such",
            "not", "can", "may", "all", "any", "via", "per", "also",
        }
    )
    raw = re.findall(r"[a-zA-Z]+", text.lower())
    return frozenset(t for t in raw if t not in _STOP and len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute the Jaccard similarity coefficient between two token sets.

    Returns ``1.0`` when both sets are empty (two empty descriptions are
    treated as identical).

    Parameters
    ----------
    a, b:
        Token frozensets.

    Returns
    -------
    float
        ``|a ∩ b| / |a ∪ b|`` in ``[0.0, 1.0]``.
    """
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _domain_of(idea: Idea) -> str:
    """Extract the top-level domain token from an idea's ``target_area``.

    The domain is the first dot-separated component of ``target_area``,
    lower-cased and stripped of whitespace.

    Parameters
    ----------
    idea:
        The idea to classify.

    Returns
    -------
    str
        Domain string, e.g. ``"algebra"`` from ``"algebra.ring_theory"``.
        Returns ``"unknown"`` if ``target_area`` is empty.
    """
    area = (idea.target_area or "").strip()
    if not area:
        return "unknown"
    first_component = area.split(".")[0].strip().lower()
    return first_component if first_component else "unknown"


def _entropy(counts: list[int]) -> float:
    """Compute the Shannon entropy of a discrete distribution.

    The distribution is defined by *counts*, which may be unnormalised
    (they will be normalised internally).

    Parameters
    ----------
    counts:
        List of non-negative integer counts.  Zero counts are ignored.

    Returns
    -------
    float
        Shannon entropy in nats (base-e logarithm).  Returns ``0.0`` for
        empty or all-zero count lists.
    """
    total = sum(counts)
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log(p)
    return h


# ---------------------------------------------------------------------------
# CoverageConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageConfig:
    """Immutable configuration bundle for coverage computations.

    Attributes
    ----------
    min_coverage_density:
        Minimum density considered acceptable.  Regions below this
        threshold are flagged as gaps.
    target_density:
        Desired density level to aim for during optimisation.
    gap_threshold:
        Proportion below which a domain is declared under-covered.
    max_cluster_size:
        Maximum number of ideas per domain before overcrowding is reported.
    domain_weight:
        Fractional contribution of domain diversity to overall coverage.
    purpose_weight:
        Fractional contribution of purpose alignment to overall coverage.
    trust_weight:
        Fractional contribution of trust-level diversity to overall coverage.
    """

    min_coverage_density: float = 0.3
    target_density: float = 0.7
    gap_threshold: float = 0.3
    max_cluster_size: int = 5
    domain_weight: float = 0.4
    purpose_weight: float = 0.3
    trust_weight: float = 0.3

    def __post_init__(self) -> None:
        # Clamp float fields.
        object.__setattr__(
            self, "min_coverage_density", _clamp(self.min_coverage_density)
        )
        object.__setattr__(self, "target_density", _clamp(self.target_density))
        object.__setattr__(self, "gap_threshold", _clamp(self.gap_threshold))
        # Validate cluster size.
        if self.max_cluster_size < 1:
            object.__setattr__(self, "max_cluster_size", 1)
        # Validate weight sum.
        total = self.domain_weight + self.purpose_weight + self.trust_weight
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"CoverageConfig weights must sum to ~1.0 (got {total:.4f}). "
                "Use normalize_weights() to auto-correct."
            )

    @property
    def is_strict(self) -> bool:
        """True when ``min_coverage_density >= 0.5``, indicating tight requirements."""
        return self.min_coverage_density >= 0.5

    @property
    def coverage_range(self) -> tuple[float, float]:
        """Return ``(min_coverage_density, target_density)``."""
        return (self.min_coverage_density, self.target_density)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "min_coverage_density": self.min_coverage_density,
            "target_density": self.target_density,
            "gap_threshold": self.gap_threshold,
            "max_cluster_size": self.max_cluster_size,
            "domain_weight": self.domain_weight,
            "purpose_weight": self.purpose_weight,
            "trust_weight": self.trust_weight,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CoverageConfig":
        """Deserialise from a dictionary produced by ``to_dict``."""
        return cls(
            min_coverage_density=float(d.get("min_coverage_density", 0.3)),
            target_density=float(d.get("target_density", 0.7)),
            gap_threshold=float(d.get("gap_threshold", 0.3)),
            max_cluster_size=int(d.get("max_cluster_size", 5)),
            domain_weight=float(d.get("domain_weight", 0.4)),
            purpose_weight=float(d.get("purpose_weight", 0.3)),
            trust_weight=float(d.get("trust_weight", 0.3)),
        )

    def normalize_weights(self) -> "CoverageConfig":
        """Return a new config with ``domain_weight + purpose_weight + trust_weight == 1.0``.

        Proportional re-normalisation is used: each weight is divided by
        the current total, preserving their ratios.
        """
        total = self.domain_weight + self.purpose_weight + self.trust_weight
        if total < _EPSILON:
            third = 1.0 / 3.0
            return CoverageConfig(
                min_coverage_density=self.min_coverage_density,
                target_density=self.target_density,
                gap_threshold=self.gap_threshold,
                max_cluster_size=self.max_cluster_size,
                domain_weight=third,
                purpose_weight=third,
                trust_weight=third,
            )
        return CoverageConfig(
            min_coverage_density=self.min_coverage_density,
            target_density=self.target_density,
            gap_threshold=self.gap_threshold,
            max_cluster_size=self.max_cluster_size,
            domain_weight=self.domain_weight / total,
            purpose_weight=self.purpose_weight / total,
            trust_weight=self.trust_weight / total,
        )


# ---------------------------------------------------------------------------
# CoverageEstimator
# ---------------------------------------------------------------------------


class CoverageEstimator:
    """Estimates multi-dimensional coverage of an idea portfolio.

    Coverage is assessed along three independent axes — domain, purpose,
    and trust — and then combined into an overall scalar.

    Parameters
    ----------
    config:
        Coverage configuration.  Defaults to ``CoverageConfig()``.
    """

    def __init__(self, config: CoverageConfig | None = None) -> None:
        self._config: CoverageConfig = config or CoverageConfig()

    def domain_distribution(self, ideas: Sequence[Idea]) -> dict[str, int]:
        """Count how many ideas belong to each top-level domain.

        Parameters
        ----------
        ideas:
            Ideas to classify.

        Returns
        -------
        dict[str, int]
            ``domain -> count`` mapping, sorted by count descending.
        """
        counts: dict[str, int] = defaultdict(int)
        for idea in ideas:
            counts[_domain_of(idea)] += 1
        return dict(sorted(counts.items(), key=lambda t: t[1], reverse=True))

    def purpose_distribution(
        self, ideas: Sequence[Idea], purpose: str
    ) -> dict[str, float]:
        """Compute each idea's Jaccard alignment with *purpose*.

        Parameters
        ----------
        ideas:
            Ideas to score.
        purpose:
            Free-text research purpose.

        Returns
        -------
        dict[str, float]
            ``idea_id -> alignment_score`` in ``[0.0, 1.0]``.
        """
        if not purpose.strip():
            return {idea.idea_id: 0.0 for idea in ideas}
        purpose_tokens = _tokenize(purpose)
        result: dict[str, float] = {}
        for idea in ideas:
            idea_tokens = _tokenize(
                " ".join(
                    [
                        idea.title or "",
                        idea.purpose or "",
                        idea.hypothesis or "",
                        idea.target_area or "",
                    ]
                )
            )
            result[idea.idea_id] = _jaccard(idea_tokens, purpose_tokens)
        return result

    def trust_distribution(self, ideas: Sequence[Idea]) -> dict[str, int]:
        """Count ideas at each trust level.

        Parameters
        ----------
        ideas:
            Ideas to classify by trust.

        Returns
        -------
        dict[str, int]
            ``trust_level_name -> count`` for each represented trust level.
        """
        counts: dict[str, int] = defaultdict(int)
        for idea in ideas:
            ts = idea.trust_status
            if hasattr(ts, "level") and hasattr(ts.level, "name"):
                name = ts.level.name
            elif hasattr(ts, "name"):
                name = ts.name
            else:
                name = str(ts)
            counts[name] += 1
        return dict(counts)

    def coverage_density(self, ideas: Sequence[Idea]) -> float:
        """Compute a normalised coverage density over domains.

        Density measures how evenly ideas are spread across distinct domains:
        ``n_domains / (n_ideas + 1)``, clamped to ``[0.0, 1.0]``.  A
        single-domain portfolio has density near ``0``, while a portfolio
        with one idea per domain approaches ``1``.

        Parameters
        ----------
        ideas:
            Ideas to evaluate.

        Returns
        -------
        float
            Coverage density in ``[0.0, 1.0]``.
        """
        idea_list = list(ideas)
        n = len(idea_list)
        if n == 0:
            return 0.0
        n_domains = len({_domain_of(idea) for idea in idea_list})
        return _clamp(n_domains / (n + 1))

    def uniformity_score(self, ideas: Sequence[Idea]) -> float:
        """Measure how uniformly ideas are distributed across domains.

        Uses normalised Shannon entropy: ``H / H_max`` where ``H_max`` is
        the entropy of a perfectly uniform distribution over the same number
        of domains.

        Parameters
        ----------
        ideas:
            Ideas to evaluate.

        Returns
        -------
        float
            Uniformity score in ``[0.0, 1.0]``.  ``1.0`` = perfectly uniform.
        """
        dist = self.domain_distribution(ideas)
        if not dist:
            return 0.0
        counts = list(dist.values())
        h = _entropy(counts)
        n_domains = len(counts)
        if n_domains <= 1:
            return 0.0
        h_max = math.log(n_domains)
        if h_max < _EPSILON:
            return 0.0
        return _clamp(h / h_max)

    def estimate(self, ideas: Sequence[Idea], purpose: str = "") -> dict[str, float]:
        """Compute coverage estimates along all three axes plus overall.

        Parameters
        ----------
        ideas:
            Ideas to analyse.
        purpose:
            Optional research purpose for purpose coverage.

        Returns
        -------
        dict[str, float]
            Keys: ``domain_coverage``, ``purpose_coverage``,
            ``trust_coverage``, ``overall_coverage``.
        """
        idea_list = list(ideas)
        n = len(idea_list)
        if n == 0:
            return {
                "domain_coverage": 0.0,
                "purpose_coverage": 0.0,
                "trust_coverage": 0.0,
                "overall_coverage": 0.0,
            }

        # Domain coverage: normalised count of distinct domains.
        n_domains = len({_domain_of(i) for i in idea_list})
        # Use logarithmic scaling: diminishing returns for large domain counts.
        domain_cov = _clamp(math.log1p(n_domains) / math.log1p(max(n, 1)))

        # Purpose coverage: fraction of ideas with alignment > 0 (if purpose given).
        if purpose.strip():
            pur_dist = self.purpose_distribution(idea_list, purpose)
            aligned_count = sum(1 for v in pur_dist.values() if v > 0.05)
            purpose_cov = _clamp(aligned_count / n)
        else:
            # No purpose specified: treat as neutral coverage.
            purpose_cov = 0.5

        # Trust coverage: fraction of TrustLevel members represented.
        trust_dist = self.trust_distribution(idea_list)
        n_levels = len(_ALL_TRUST_LEVELS)
        represented = len(trust_dist)
        trust_cov = _clamp(represented / max(n_levels, 1))

        overall = (
            self._config.domain_weight * domain_cov
            + self._config.purpose_weight * purpose_cov
            + self._config.trust_weight * trust_cov
        )

        return {
            "domain_coverage": domain_cov,
            "purpose_coverage": purpose_cov,
            "trust_coverage": trust_cov,
            "overall_coverage": _clamp(overall),
        }

    def compute_full_coverage(
        self, portfolio: IdeaPortfolio, purpose: str = ""
    ) -> "CoverageReport":
        """Run a complete coverage analysis and package results in a report.

        Parameters
        ----------
        portfolio:
            The idea portfolio to analyse.
        purpose:
            Optional research purpose.

        Returns
        -------
        CoverageReport
            Fully populated coverage report.
        """
        ideas: list[Idea] = list(portfolio.ideas) if hasattr(portfolio, "ideas") else []
        cov = self.estimate(ideas, purpose)
        dom_dist = self.domain_distribution(ideas)
        uni = self.uniformity_score(ideas)
        density = self.coverage_density(ideas)

        gap_detector = GapDetector(self._config)
        gap_regions = gap_detector.detect_gaps(ideas, purpose)

        report = CoverageReport(
            purpose=purpose,
            total_ideas=len(ideas),
            domain_coverage=cov["domain_coverage"],
            purpose_coverage=cov["purpose_coverage"],
            trust_coverage=cov["trust_coverage"],
            overall_coverage=cov["overall_coverage"],
            gap_regions=gap_regions,
            domain_distribution=dom_dist,
            uniformity_score=uni,
            density=density,
        )
        # Generate recommendations.
        reporter = CoverageReporter(self._config)
        report.recommendations = reporter.recommendations(report)
        return report

    def marginal_gain(
        self, new_idea: Idea, existing: Sequence[Idea], purpose: str = ""
    ) -> float:
        """Estimate how much *new_idea* increases overall coverage.

        Computes coverage with and without *new_idea* and returns the
        positive difference.

        Parameters
        ----------
        new_idea:
            The candidate idea to evaluate.
        existing:
            Currently selected ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        float
            Coverage gain in ``[0.0, 1.0]``.
        """
        existing_list = list(existing)
        cov_before = self.estimate(existing_list, purpose)["overall_coverage"]
        combined = existing_list + [new_idea]
        cov_after = self.estimate(combined, purpose)["overall_coverage"]
        return _clamp(cov_after - cov_before)


# ---------------------------------------------------------------------------
# GapDetector
# ---------------------------------------------------------------------------


class GapDetector:
    """Identifies under-covered regions in an idea portfolio.

    Gaps are defined as areas — domains, purpose ranges, or trust levels —
    that are either absent from the portfolio or present at a density below
    ``CoverageConfig.gap_threshold``.

    Parameters
    ----------
    config:
        Coverage configuration.  Defaults to ``CoverageConfig()``.
    """

    def __init__(self, config: CoverageConfig | None = None) -> None:
        self._config: CoverageConfig = config or CoverageConfig()

    def domain_gaps(
        self,
        ideas: Sequence[Idea],
        all_domains: Sequence[str] | None = None,
    ) -> list[str]:
        """Identify domains that are absent or under-represented.

        If *all_domains* is provided, any domain in that list with zero
        ideas is flagged.  Regardless, any domain with fewer than 2 ideas
        is reported as under-covered when the portfolio size permits it.

        Parameters
        ----------
        ideas:
            Current portfolio ideas.
        all_domains:
            Optional exhaustive domain vocabulary.

        Returns
        -------
        list[str]
            Human-readable descriptions of each gap, e.g.
            ``"domain 'algebraic-geometry' has 1/5 ideas (under-covered)"``.
        """
        dist = Counter(_domain_of(i) for i in ideas)
        gaps: list[str] = []
        n = len(list(ideas))

        if all_domains:
            for dom in all_domains:
                if dist[dom] == 0:
                    gaps.append(f"domain '{dom}' is absent from the portfolio")

        # Flag domains present but with low relative representation.
        for dom, count in dist.items():
            proportion = count / max(n, 1)
            if proportion < self._config.gap_threshold and n >= 3:
                gaps.append(
                    f"domain '{dom}' has {count}/{n} ideas "
                    f"(below gap_threshold {self._config.gap_threshold:.2f})"
                )
        return gaps

    def purpose_gaps(self, ideas: Sequence[Idea], purpose: str) -> list[str]:
        """Identify purpose-aligned areas where coverage is lacking.

        An idea with alignment ``< 0.1`` is treated as unaligned.  If fewer
        than half of the ideas are aligned with *purpose*, a gap is reported.
        Also checks for ideas that cover the purpose at a high level (> 0.5)
        and flags if there are none.

        Parameters
        ----------
        ideas:
            Current portfolio ideas.
        purpose:
            Research purpose to evaluate against.

        Returns
        -------
        list[str]
            Descriptions of purpose gaps.
        """
        if not purpose.strip():
            return []
        purpose_tokens = _tokenize(purpose)
        gaps: list[str] = []
        idea_list = list(ideas)
        n = len(idea_list)
        if n == 0:
            return ["Portfolio is empty — no purpose coverage at all"]

        alignments: list[float] = []
        for idea in idea_list:
            idea_tokens = _tokenize(
                " ".join(
                    [
                        idea.title or "",
                        idea.purpose or "",
                        idea.hypothesis or "",
                        idea.target_area or "",
                    ]
                )
            )
            alignments.append(_jaccard(idea_tokens, purpose_tokens))

        aligned_count = sum(1 for a in alignments if a > 0.1)
        strongly_aligned = sum(1 for a in alignments if a > 0.5)

        if aligned_count < n / 2:
            gaps.append(
                f"Fewer than half of ideas ({aligned_count}/{n}) align with purpose; "
                "consider adding purpose-focused ideas"
            )
        if strongly_aligned == 0:
            gaps.append(
                "No ideas strongly aligned with purpose (alignment > 0.5); "
                "high-alignment ideas are missing"
            )
        return gaps

    def trust_gaps(self, ideas: Sequence[Idea]) -> list[TrustStatus]:
        """Identify trust levels not represented in the portfolio.

        Parameters
        ----------
        ideas:
            Current portfolio ideas.

        Returns
        -------
        list[TrustStatus]
            Trust levels (as ``TrustLevel`` enum members wrapped in a
            lightweight placeholder) that are absent from the portfolio.

        Note
        ----
        Returns ``TrustLevel`` enum members directly since we cannot
        construct arbitrary ``TrustStatus`` instances generically.
        """
        represented: set[str] = set()
        for idea in ideas:
            ts = idea.trust_status
            if hasattr(ts, "level") and hasattr(ts.level, "name"):
                represented.add(ts.level.name)
            elif hasattr(ts, "name"):
                represented.add(ts.name)
            else:
                represented.add(str(ts))
        missing = [
            level for level in _ALL_TRUST_LEVELS if level.name not in represented
        ]
        return missing  # type: ignore[return-value]

    def density_gaps(self, ideas: Sequence[Idea]) -> list[str]:
        """Identify domains with a density below the configured threshold.

        Parameters
        ----------
        ideas:
            Current portfolio ideas.

        Returns
        -------
        list[str]
            Human-readable density gap descriptions.
        """
        idea_list = list(ideas)
        n = len(idea_list)
        if n == 0:
            return ["Portfolio is empty — no density anywhere"]
        dist = Counter(_domain_of(i) for i in idea_list)
        max_count = max(dist.values(), default=1)
        gaps: list[str] = []
        for dom, count in dist.items():
            # Normalise by max count to get relative density.
            rel_density = count / max_count
            if rel_density < self._config.gap_threshold:
                gaps.append(
                    f"domain '{dom}' has relative density {rel_density:.3f} "
                    f"(below threshold {self._config.gap_threshold:.2f})"
                )
        return gaps

    def gap_score(self, ideas: Sequence[Idea], purpose: str = "") -> float:
        """Compute a scalar gap score for the portfolio.

        A score of ``0.0`` means no gaps; ``1.0`` means completely
        uncovered.  The score is derived from the number of gap types
        present, normalised by the maximum possible number of gaps.

        Parameters
        ----------
        ideas:
            Portfolio ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        float
            Gap score in ``[0.0, 1.0]``.
        """
        dom_gaps = self.domain_gaps(ideas)
        pur_gaps = self.purpose_gaps(ideas, purpose)
        tru_gaps = self.trust_gaps(ideas)
        den_gaps = self.density_gaps(ideas)

        total_gaps = len(dom_gaps) + len(pur_gaps) + len(tru_gaps) + len(den_gaps)
        # Normalise: treat 20 or more as the maximum (fully gapped).
        max_gaps = 20
        return _clamp(total_gaps / max_gaps)

    def detect_gaps(self, ideas: Sequence[Idea], purpose: str = "") -> list[str]:
        """Return all detected gaps as human-readable strings.

        Combines domain gaps, purpose gaps, density gaps, and notes about
        missing trust levels.

        Parameters
        ----------
        ideas:
            Portfolio ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        list[str]
            List of gap descriptions.  Empty if no gaps detected.
        """
        all_gaps: list[str] = []
        all_gaps.extend(self.domain_gaps(ideas))
        all_gaps.extend(self.purpose_gaps(ideas, purpose))
        all_gaps.extend(self.density_gaps(ideas))
        missing_trust = self.trust_gaps(ideas)
        for level in missing_trust:
            all_gaps.append(f"trust level '{level.name}' is absent from the portfolio")
        return all_gaps

    def prioritized_gaps(
        self, ideas: Sequence[Idea], purpose: str = ""
    ) -> list[tuple[str, float]]:
        """Return gaps with priority scores, sorted by descending priority.

        Priority is a heuristic: purpose gaps receive the highest base
        score (0.9), trust gaps are next (0.7), domain gaps are 0.5, and
        density gaps are 0.3.  Ties are broken alphabetically by description.

        Parameters
        ----------
        ideas:
            Portfolio ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        list[tuple[str, float]]
            ``(description, priority)`` pairs sorted by descending priority.
        """
        results: list[tuple[str, float]] = []
        for desc in self.purpose_gaps(ideas, purpose):
            results.append((desc, 0.9))
        for level in self.trust_gaps(ideas):
            results.append((f"trust level '{level.name}' absent", 0.7))
        for desc in self.domain_gaps(ideas):
            results.append((desc, 0.5))
        for desc in self.density_gaps(ideas):
            results.append((desc, 0.3))
        results.sort(key=lambda t: (-t[1], t[0]))
        return results


# ---------------------------------------------------------------------------
# CoverageOptimizer
# ---------------------------------------------------------------------------


class CoverageOptimizer:
    """Greedy selection of ideas that maximally increase portfolio coverage.

    All selection algorithms use a greedy marginal-gain strategy: at each
    step the candidate that adds the most coverage to the current selection
    is added.  This provides a ``(1 - 1/e)``-approximation guarantee for
    submodular coverage functions (see theory2.tex §57.3).

    Parameters
    ----------
    config:
        Coverage configuration.  Defaults to ``CoverageConfig()``.
    """

    def __init__(self, config: CoverageConfig | None = None) -> None:
        self._config: CoverageConfig = config or CoverageConfig()
        self._estimator: CoverageEstimator = CoverageEstimator(config)

    def _coverage_gain(
        self, new_idea: Idea, existing: Sequence[Idea], purpose: str = ""
    ) -> float:
        """Compute the marginal coverage gain of adding *new_idea* to *existing*.

        Parameters
        ----------
        new_idea:
            Candidate idea.
        existing:
            Currently selected ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        float
            Coverage gain (non-negative).
        """
        return self._estimator.marginal_gain(new_idea, existing, purpose)

    def marginal_gains(
        self,
        candidates: Sequence[Idea],
        existing: Sequence[Idea],
        purpose: str = "",
    ) -> list[tuple[Idea, float]]:
        """Compute marginal coverage gains for all *candidates*.

        Parameters
        ----------
        candidates:
            Ideas to evaluate.
        existing:
            Currently selected ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        list[tuple[Idea, float]]
            ``(idea, gain)`` pairs sorted by descending gain.
        """
        results = [
            (idea, self._coverage_gain(idea, existing, purpose))
            for idea in candidates
        ]
        results.sort(key=lambda t: t[1], reverse=True)
        return results

    def select_to_maximize_coverage(
        self,
        candidates: Sequence[Idea],
        existing: Sequence[Idea],
        k: int,
        purpose: str = "",
    ) -> list[Idea]:
        """Greedily select up to *k* ideas from *candidates* to maximise coverage.

        At each step the candidate with the highest marginal gain is
        added.  Selection stops when *k* ideas have been chosen or all
        candidates are exhausted.

        Parameters
        ----------
        candidates:
            Ideas to choose from.
        existing:
            Already-selected ideas that define the baseline coverage.
        k:
            Maximum number of ideas to select.
        purpose:
            Optional research purpose.

        Returns
        -------
        list[Idea]
            Ordered list of selected ideas (most impactful first).
        """
        if k <= 0:
            return []
        remaining = list(candidates)
        selected: list[Idea] = []
        current_existing = list(existing)

        while remaining and len(selected) < k:
            best_idea: Idea | None = None
            best_gain: float = -1.0
            for idea in remaining:
                gain = self._coverage_gain(idea, current_existing, purpose)
                if gain > best_gain:
                    best_gain = gain
                    best_idea = idea
            if best_idea is None:
                break
            selected.append(best_idea)
            current_existing.append(best_idea)
            remaining.remove(best_idea)

        return selected

    def optimize_budget_constrained(
        self,
        candidates: Sequence[Idea],
        existing: Sequence[Idea],
        budget: float,
        purpose: str = "",
    ) -> list[Idea]:
        """Select ideas within a cost budget to maximise coverage.

        Uses a greedy cost-effectiveness (gain/cost) ranking.  Ideas with
        zero or negative cost are assigned a cost of ``0.01`` to avoid
        division-by-zero.

        Parameters
        ----------
        candidates:
            Ideas to choose from.
        existing:
            Already-selected ideas.
        budget:
            Maximum total cost of selected ideas.
        purpose:
            Optional research purpose.

        Returns
        -------
        list[Idea]
            Selected ideas whose total cost does not exceed *budget*.
        """
        if budget <= 0:
            return []

        remaining = list(candidates)
        selected: list[Idea] = []
        current_existing = list(existing)
        spent = 0.0

        while remaining:
            best_idea: Idea | None = None
            best_effectiveness: float = -1.0
            for idea in remaining:
                cost = float(idea.predicted_gain.cost)
                if cost <= 0:
                    cost = 0.01
                if spent + cost > budget:
                    continue
                gain = self._coverage_gain(idea, current_existing, purpose)
                effectiveness = gain / cost
                if effectiveness > best_effectiveness:
                    best_effectiveness = effectiveness
                    best_idea = idea
            if best_idea is None:
                break
            cost = max(float(best_idea.predicted_gain.cost), 0.01)
            selected.append(best_idea)
            current_existing.append(best_idea)
            spent += cost
            remaining.remove(best_idea)

        return selected

    def reorder_by_coverage(
        self, ideas: Sequence[Idea], purpose: str = ""
    ) -> list[Idea]:
        """Reorder *ideas* so that high-coverage contributions come first.

        Uses greedy ordering: the first idea is the one with the highest
        stand-alone coverage, the second is the one that adds the most
        given the first, and so on.

        Parameters
        ----------
        ideas:
            Ideas to reorder.
        purpose:
            Optional research purpose.

        Returns
        -------
        list[Idea]
            Ideas in descending coverage-contribution order.
        """
        return self.select_to_maximize_coverage(ideas, [], len(list(ideas)), purpose)

    def remove_redundant(
        self, ideas: Sequence[Idea], redundancy_threshold: float = 0.9
    ) -> list[Idea]:
        """Remove ideas whose marginal gain falls below *redundancy_threshold*.

        An idea is considered redundant when its coverage contribution,
        after all ideas with higher marginal gain have been selected, falls
        below ``(1 - redundancy_threshold)`` of the maximum marginal gain.

        Parameters
        ----------
        ideas:
            Ideas to filter.
        redundancy_threshold:
            Fraction of max marginal gain below which ideas are removed.

        Returns
        -------
        list[Idea]
            Non-redundant ideas.
        """
        ordered = self.reorder_by_coverage(ideas)
        if not ordered:
            return []

        kept: list[Idea] = [ordered[0]]
        max_gain = self._coverage_gain(ordered[0], [], "")

        for idea in ordered[1:]:
            gain = self._coverage_gain(idea, kept, "")
            relative_gain = gain / max(max_gain, _EPSILON)
            if relative_gain >= (1.0 - redundancy_threshold):
                kept.append(idea)

        return kept


# ---------------------------------------------------------------------------
# DensityAnalyzer
# ---------------------------------------------------------------------------


class DensityAnalyzer:
    """Analyzes the density distribution of ideas across domains.

    Density here refers to how many ideas occupy each domain relative to
    the total portfolio size, and whether that distribution is balanced.

    Parameters
    ----------
    config:
        Coverage configuration.  Defaults to ``CoverageConfig()``.
    """

    def __init__(self, config: CoverageConfig | None = None) -> None:
        self._config: CoverageConfig = config or CoverageConfig()

    def cluster_sizes(self, ideas: Sequence[Idea]) -> dict[str, int]:
        """Return the number of ideas per domain.

        Parameters
        ----------
        ideas:
            Portfolio ideas.

        Returns
        -------
        dict[str, int]
            ``domain -> count``.
        """
        counts: dict[str, int] = defaultdict(int)
        for idea in ideas:
            counts[_domain_of(idea)] += 1
        return dict(counts)

    def analyze_domain_density(self, ideas: Sequence[Idea]) -> dict[str, float]:
        """Compute normalised density for each domain.

        Density for domain ``d`` is ``count(d) / n_total``.

        Parameters
        ----------
        ideas:
            Portfolio ideas.

        Returns
        -------
        dict[str, float]
            ``domain -> density`` in ``[0.0, 1.0]``.
        """
        idea_list = list(ideas)
        n = len(idea_list)
        if n == 0:
            return {}
        sizes = self.cluster_sizes(idea_list)
        return {dom: count / n for dom, count in sizes.items()}

    def analyze_purpose_density(
        self, ideas: Sequence[Idea], purpose: str
    ) -> float:
        """Estimate the overall purpose-aligned density of the portfolio.

        Returns the fraction of ideas with purpose alignment above a 0.1
        threshold, normalised to ``[0.0, 1.0]``.

        Parameters
        ----------
        ideas:
            Portfolio ideas.
        purpose:
            Research purpose.

        Returns
        -------
        float
            Purpose density in ``[0.0, 1.0]``.
        """
        idea_list = list(ideas)
        n = len(idea_list)
        if n == 0 or not purpose.strip():
            return 0.0
        purpose_tokens = _tokenize(purpose)
        aligned = 0
        for idea in idea_list:
            idea_tokens = _tokenize(
                " ".join(
                    [
                        idea.title or "",
                        idea.purpose or "",
                        idea.hypothesis or "",
                        idea.target_area or "",
                    ]
                )
            )
            if _jaccard(idea_tokens, purpose_tokens) > 0.1:
                aligned += 1
        return _clamp(aligned / n)

    def overcrowded_domains(self, ideas: Sequence[Idea]) -> list[str]:
        """Return domains with more than ``max_cluster_size`` ideas.

        Parameters
        ----------
        ideas:
            Portfolio ideas.

        Returns
        -------
        list[str]
            Domain names that are overcrowded.
        """
        sizes = self.cluster_sizes(ideas)
        return [
            dom
            for dom, count in sizes.items()
            if count > self._config.max_cluster_size
        ]

    def undercrowded_domains(self, ideas: Sequence[Idea]) -> list[str]:
        """Return domains with fewer than 2 ideas.

        Parameters
        ----------
        ideas:
            Portfolio ideas.

        Returns
        -------
        list[str]
            Domain names that are undercrowded.
        """
        sizes = self.cluster_sizes(ideas)
        return [dom for dom, count in sizes.items() if count < 2]

    def density_heatmap(self, ideas: Sequence[Idea]) -> dict[str, float]:
        """Build a ``[0, 1]``-normalised density heatmap over domains.

        The maximum-density domain maps to ``1.0``; all others are scaled
        proportionally.

        Parameters
        ----------
        ideas:
            Portfolio ideas.

        Returns
        -------
        dict[str, float]
            ``domain -> normalised_density``.
        """
        raw = self.analyze_domain_density(ideas)
        if not raw:
            return {}
        max_density = max(raw.values())
        if max_density < _EPSILON:
            return {dom: 0.0 for dom in raw}
        return {dom: d / max_density for dom, d in raw.items()}

    def entropy(self, ideas: Sequence[Idea]) -> float:
        """Shannon entropy of the domain distribution.

        Higher entropy means ideas are more evenly spread across domains.

        Parameters
        ----------
        ideas:
            Portfolio ideas.

        Returns
        -------
        float
            Shannon entropy in nats (base-*e*).
        """
        sizes = self.cluster_sizes(ideas)
        return _entropy(list(sizes.values()))

    def recommend_balance(self, ideas: Sequence[Idea]) -> list[str]:
        """Generate human-readable balancing recommendations.

        Inspects overcrowded and undercrowded domains and suggests actions.

        Parameters
        ----------
        ideas:
            Portfolio ideas.

        Returns
        -------
        list[str]
            List of actionable recommendation strings.
        """
        recs: list[str] = []
        overcrowded = self.overcrowded_domains(ideas)
        undercrowded = self.undercrowded_domains(ideas)
        heatmap = self.density_heatmap(ideas)

        for dom in overcrowded:
            recs.append(
                f"Domain '{dom}' has too many ideas "
                f"(>{self._config.max_cluster_size}). "
                "Consider pruning low-value ideas or redistributing effort."
            )
        for dom in undercrowded:
            recs.append(
                f"Domain '{dom}' has fewer than 2 ideas. "
                "Adding another idea here would improve robustness."
            )

        # Suggest the sparsest domain for expansion.
        if heatmap:
            sparsest = min(heatmap, key=lambda d: heatmap[d])
            if heatmap[sparsest] < self._config.min_coverage_density:
                recs.append(
                    f"Domain '{sparsest}' is the least dense "
                    f"(relative density {heatmap[sparsest]:.2f}). "
                    "Prioritise new ideas in this area."
                )

        if not recs:
            recs.append(
                "Domain distribution looks balanced. "
                "No immediate rebalancing needed."
            )
        return recs


# ---------------------------------------------------------------------------
# CoverageReport
# ---------------------------------------------------------------------------


@dataclass
class CoverageReport:
    """Mutable dataclass capturing a full coverage analysis snapshot.

    Built by ``CoverageEstimator.compute_full_coverage`` or
    ``CoverageReporter.generate_report``.

    Attributes
    ----------
    report_id:
        Unique identifier for this report.
    purpose:
        Research purpose used when generating the report.
    timestamp:
        ISO-8601 UTC timestamp of report creation.
    total_ideas:
        Number of ideas analysed.
    domain_coverage:
        Domain-axis coverage score in ``[0.0, 1.0]``.
    purpose_coverage:
        Purpose-axis coverage score in ``[0.0, 1.0]``.
    trust_coverage:
        Trust-axis coverage score in ``[0.0, 1.0]``.
    overall_coverage:
        Weighted overall coverage score in ``[0.0, 1.0]``.
    gap_regions:
        List of human-readable gap descriptions.
    domain_distribution:
        ``domain -> count`` mapping.
    uniformity_score:
        Entropy-based uniformity in ``[0.0, 1.0]``.
    density:
        Normalised coverage density in ``[0.0, 1.0]``.
    recommendations:
        Actionable recommendation strings.
    """

    report_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    purpose: str = ""
    timestamp: str = field(default_factory=_now_iso)
    total_ideas: int = 0
    domain_coverage: float = 0.0
    purpose_coverage: float = 0.0
    trust_coverage: float = 0.0
    overall_coverage: float = 0.0
    gap_regions: list[str] = field(default_factory=list)
    domain_distribution: dict[str, int] = field(default_factory=dict)
    uniformity_score: float = 0.0
    density: float = 0.0
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "report_id": self.report_id,
            "purpose": self.purpose,
            "timestamp": self.timestamp,
            "total_ideas": self.total_ideas,
            "domain_coverage": self.domain_coverage,
            "purpose_coverage": self.purpose_coverage,
            "trust_coverage": self.trust_coverage,
            "overall_coverage": self.overall_coverage,
            "gap_regions": list(self.gap_regions),
            "domain_distribution": dict(self.domain_distribution),
            "uniformity_score": self.uniformity_score,
            "density": self.density,
            "recommendations": list(self.recommendations),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CoverageReport":
        """Deserialise from a dictionary produced by ``to_dict``."""
        return cls(
            report_id=d.get("report_id", str(uuid.uuid4())),
            purpose=d.get("purpose", ""),
            timestamp=d.get("timestamp", _now_iso()),
            total_ideas=int(d.get("total_ideas", 0)),
            domain_coverage=float(d.get("domain_coverage", 0.0)),
            purpose_coverage=float(d.get("purpose_coverage", 0.0)),
            trust_coverage=float(d.get("trust_coverage", 0.0)),
            overall_coverage=float(d.get("overall_coverage", 0.0)),
            gap_regions=list(d.get("gap_regions", [])),
            domain_distribution=dict(d.get("domain_distribution", {})),
            uniformity_score=float(d.get("uniformity_score", 0.0)),
            density=float(d.get("density", 0.0)),
            recommendations=list(d.get("recommendations", [])),
        )

    def to_json(self) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "CoverageReport":
        """Deserialise from a JSON string produced by ``to_json``."""
        return cls.from_dict(json.loads(s))

    def summary(self) -> str:
        """Return a one-line summary of the report.

        Returns
        -------
        str
            Summary string including overall coverage and gap count.
        """
        return (
            f"CoverageReport[{self.report_id[:8]}] "
            f"ideas={self.total_ideas} "
            f"coverage={self.overall_coverage:.2f} "
            f"gaps={len(self.gap_regions)} "
            f"ts={self.timestamp}"
        )

    def describe(self) -> str:
        """Return a multi-line human-readable description.

        Returns
        -------
        str
            Formatted report description.
        """
        lines = [
            f"Coverage Report  [{self.report_id}]",
            f"  Timestamp       : {self.timestamp}",
            f"  Purpose         : {self.purpose[:60]!r}{'...' if len(self.purpose) > 60 else ''}",
            f"  Total ideas     : {self.total_ideas}",
            f"  Domain coverage : {self.domain_coverage:.3f}",
            f"  Purpose coverage: {self.purpose_coverage:.3f}",
            f"  Trust coverage  : {self.trust_coverage:.3f}",
            f"  Overall coverage: {self.overall_coverage:.3f}",
            f"  Uniformity      : {self.uniformity_score:.3f}",
            f"  Density         : {self.density:.3f}",
            f"  Gap regions     : {len(self.gap_regions)}",
        ]
        if self.gap_regions:
            for gap in self.gap_regions[:5]:
                lines.append(f"    - {gap}")
            if len(self.gap_regions) > 5:
                lines.append(f"    ... (+{len(self.gap_regions) - 5} more)")
        if self.recommendations:
            lines.append("  Recommendations :")
            for rec in self.recommendations[:3]:
                lines.append(f"    * {rec}")
            if len(self.recommendations) > 3:
                lines.append(f"    ... (+{len(self.recommendations) - 3} more)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CoverageReporter
# ---------------------------------------------------------------------------


class CoverageReporter:
    """Generates human-readable and machine-readable coverage reports.

    Parameters
    ----------
    config:
        Coverage configuration.  Defaults to ``CoverageConfig()``.
    """

    def __init__(self, config: CoverageConfig | None = None) -> None:
        self._config: CoverageConfig = config or CoverageConfig()
        self._estimator: CoverageEstimator = CoverageEstimator(config)
        self._gap_detector: GapDetector = GapDetector(config)
        self._density: DensityAnalyzer = DensityAnalyzer(config)

    def generate_report(
        self, ideas: Sequence[Idea], purpose: str = ""
    ) -> CoverageReport:
        """Run a full coverage analysis and return a ``CoverageReport``.

        Parameters
        ----------
        ideas:
            Ideas to analyse.
        purpose:
            Optional research purpose.

        Returns
        -------
        CoverageReport
            Fully populated report.
        """
        idea_list = list(ideas)
        cov = self._estimator.estimate(idea_list, purpose)
        dom_dist = self._estimator.domain_distribution(idea_list)
        uni = self._estimator.uniformity_score(idea_list)
        density = self._estimator.coverage_density(idea_list)
        gaps = self._gap_detector.detect_gaps(idea_list, purpose)

        report = CoverageReport(
            purpose=purpose,
            total_ideas=len(idea_list),
            domain_coverage=cov["domain_coverage"],
            purpose_coverage=cov["purpose_coverage"],
            trust_coverage=cov["trust_coverage"],
            overall_coverage=cov["overall_coverage"],
            gap_regions=gaps,
            domain_distribution=dom_dist,
            uniformity_score=uni,
            density=density,
        )
        report.recommendations = self.recommendations(report)
        return report

    def recommendations(self, report: CoverageReport) -> list[str]:
        """Generate actionable recommendations from a coverage report.

        Parameters
        ----------
        report:
            A ``CoverageReport`` to analyse.

        Returns
        -------
        list[str]
            Ordered list of recommendation strings.
        """
        recs: list[str] = []

        if report.overall_coverage < self._config.min_coverage_density:
            recs.append(
                f"Overall coverage ({report.overall_coverage:.2f}) is below the minimum "
                f"threshold ({self._config.min_coverage_density:.2f}). "
                "Add more diverse ideas to improve coverage."
            )

        if report.domain_coverage < 0.4:
            recs.append(
                "Domain coverage is low. Consider exploring ideas in underrepresented "
                "mathematical domains to broaden the portfolio."
            )

        if report.purpose_coverage < 0.4 and report.purpose:
            recs.append(
                "Purpose coverage is low. Add ideas that more directly address "
                f"the stated purpose: {report.purpose[:60]!r}."
            )

        if report.trust_coverage < 0.3:
            recs.append(
                "Trust coverage is low — most ideas share a similar epistemic level. "
                "Including ideas at diverse trust levels (from speculative to verified) "
                "improves resilience."
            )

        if report.uniformity_score < 0.4 and report.total_ideas >= 3:
            recs.append(
                f"Domain distribution is skewed (uniformity={report.uniformity_score:.2f}). "
                "Some domains are overcrowded while others are sparse."
            )

        if report.gap_regions:
            top_gap = report.gap_regions[0]
            recs.append(f"Most urgent gap: {top_gap}")

        if not recs:
            recs.append(
                "Coverage looks healthy across all dimensions. "
                "Continue adding ideas to maintain diversity."
            )

        return recs

    def text_report(self, report: CoverageReport) -> str:
        """Generate a detailed multi-line text report.

        Parameters
        ----------
        report:
            The coverage report to render.

        Returns
        -------
        str
            Formatted plain-text report.
        """
        bar_len = 30

        def _bar(value: float) -> str:
            filled = int(value * bar_len)
            return "[" + "█" * filled + "░" * (bar_len - filled) + f"] {value:.2%}"

        lines = [
            "=" * 60,
            "  JUGEO COVERAGE REPORT",
            "=" * 60,
            f"  Report ID : {report.report_id}",
            f"  Timestamp : {report.timestamp}",
            f"  Purpose   : {report.purpose[:55]!r}{'...' if len(report.purpose) > 55 else ''}",
            f"  Ideas     : {report.total_ideas}",
            "-" * 60,
            "  COVERAGE SCORES",
            f"  Domain    : {_bar(report.domain_coverage)}",
            f"  Purpose   : {_bar(report.purpose_coverage)}",
            f"  Trust     : {_bar(report.trust_coverage)}",
            f"  OVERALL   : {_bar(report.overall_coverage)}",
            "-" * 60,
            "  DISTRIBUTION METRICS",
            f"  Uniformity: {report.uniformity_score:.4f}",
            f"  Density   : {report.density:.4f}",
        ]
        if report.domain_distribution:
            lines.append("  Domain breakdown:")
            for dom, cnt in sorted(
                report.domain_distribution.items(), key=lambda t: -t[1]
            )[:8]:
                lines.append(f"    {dom:<25} {cnt:>4} idea(s)")
        if report.gap_regions:
            lines.append("-" * 60)
            lines.append(f"  GAPS DETECTED ({len(report.gap_regions)})")
            for gap in report.gap_regions[:10]:
                lines.append(f"  ⚠  {gap}")
            if len(report.gap_regions) > 10:
                lines.append(f"  ... and {len(report.gap_regions) - 10} more")
        if report.recommendations:
            lines.append("-" * 60)
            lines.append("  RECOMMENDATIONS")
            for i, rec in enumerate(report.recommendations, 1):
                lines.append(f"  {i}. {rec}")
        lines.append("=" * 60)
        return "\n".join(lines)

    def compare_reports(
        self, before: CoverageReport, after: CoverageReport
    ) -> str:
        """Generate a delta report comparing two coverage snapshots.

        Parameters
        ----------
        before:
            Earlier coverage report.
        after:
            Later coverage report.

        Returns
        -------
        str
            Human-readable delta report.
        """
        def _delta(b: float, a: float) -> str:
            diff = a - b
            arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "─")
            return f"{b:.3f} → {a:.3f}  {arrow} {abs(diff):.3f}"

        lines = [
            "=== COVERAGE DELTA REPORT ===",
            f"Before : {before.report_id[:8]} @ {before.timestamp}",
            f"After  : {after.report_id[:8]} @ {after.timestamp}",
            f"Ideas  : {before.total_ideas} → {after.total_ideas}",
            "",
            "Scores:",
            f"  Domain    : {_delta(before.domain_coverage, after.domain_coverage)}",
            f"  Purpose   : {_delta(before.purpose_coverage, after.purpose_coverage)}",
            f"  Trust     : {_delta(before.trust_coverage, after.trust_coverage)}",
            f"  Overall   : {_delta(before.overall_coverage, after.overall_coverage)}",
            "",
            f"Gaps before: {len(before.gap_regions)} | after: {len(after.gap_regions)}",
        ]
        new_gaps = set(after.gap_regions) - set(before.gap_regions)
        resolved_gaps = set(before.gap_regions) - set(after.gap_regions)
        if resolved_gaps:
            lines.append("Resolved gaps:")
            for g in sorted(resolved_gaps)[:5]:
                lines.append(f"  ✓  {g}")
        if new_gaps:
            lines.append("New gaps:")
            for g in sorted(new_gaps)[:5]:
                lines.append(f"  ✗  {g}")
        return "\n".join(lines)

    def summary_line(self, report: CoverageReport) -> str:
        """Return a concise one-line summary of *report*.

        Parameters
        ----------
        report:
            The report to summarise.

        Returns
        -------
        str
            Single-line string.
        """
        gap_flag = "⚠" if report.gap_regions else "✓"
        return (
            f"{gap_flag} coverage={report.overall_coverage:.2f} "
            f"(dom={report.domain_coverage:.2f} "
            f"pur={report.purpose_coverage:.2f} "
            f"trust={report.trust_coverage:.2f}) "
            f"ideas={report.total_ideas} gaps={len(report.gap_regions)}"
        )

    def copilot_summary(self, report: CoverageReport) -> str:
        """Produce a concise paragraph suitable for copilot consumption.

        Parameters
        ----------
        report:
            The coverage report.

        Returns
        -------
        str
            A 2-4 sentence natural language summary.
        """
        health = (
            "healthy"
            if report.overall_coverage >= self._config.target_density
            else "below target"
        )
        top_domain = (
            max(report.domain_distribution, key=report.domain_distribution.get)
            if report.domain_distribution
            else "unknown"
        )
        gap_note = (
            f"There are {len(report.gap_regions)} gap(s) detected, the most urgent being: "
            f"{report.gap_regions[0]!r}."
            if report.gap_regions
            else "No critical gaps detected."
        )
        rec_note = (
            f"Top recommendation: {report.recommendations[0]}"
            if report.recommendations
            else ""
        )
        return (
            f"The portfolio of {report.total_ideas} idea(s) has {health} overall coverage "
            f"of {report.overall_coverage:.1%}. "
            f"The dominant domain is '{top_domain}' and domain uniformity is "
            f"{report.uniformity_score:.2f}. "
            f"{gap_note} "
            f"{rec_note}"
        ).strip()

    def trend_analysis(
        self, reports: Sequence[CoverageReport]
    ) -> dict[str, Any]:
        """Analyse coverage trends across a sequence of reports.

        Computes statistics (min, max, mean, final value, trend direction)
        for the main coverage axes over time.

        Parameters
        ----------
        reports:
            Sequence of coverage reports in chronological order.

        Returns
        -------
        dict[str, Any]
            Trend statistics keyed by metric name.  Each metric entry
            contains ``min``, ``max``, ``mean``, ``final``, ``trend``
            (``"improving"``, ``"declining"``, or ``"stable"``).
        """
        report_list = list(reports)
        if not report_list:
            return {}

        axes = {
            "overall_coverage": [r.overall_coverage for r in report_list],
            "domain_coverage": [r.domain_coverage for r in report_list],
            "purpose_coverage": [r.purpose_coverage for r in report_list],
            "trust_coverage": [r.trust_coverage for r in report_list],
            "uniformity_score": [r.uniformity_score for r in report_list],
            "gap_count": [float(len(r.gap_regions)) for r in report_list],
        }

        result: dict[str, Any] = {}
        for metric, values in axes.items():
            if not values:
                continue
            mean_val = sum(values) / len(values)
            # Determine trend using simple linear regression slope sign.
            n = len(values)
            if n >= 2:
                xs = list(range(n))
                x_mean = (n - 1) / 2.0
                num = sum((xs[i] - x_mean) * (values[i] - mean_val) for i in range(n))
                denom = sum((xs[i] - x_mean) ** 2 for i in range(n))
                slope = num / max(denom, _EPSILON)
                if slope > 0.01:
                    trend = "improving"
                elif slope < -0.01:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "stable"

            result[metric] = {
                "min": min(values),
                "max": max(values),
                "mean": mean_val,
                "final": values[-1],
                "trend": trend,
                "n_reports": n,
            }

        return result
