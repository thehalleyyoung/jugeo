"""
Criteria for good semantic decomposition -- covers that respect meaning boundaries.

# copilot:

This module defines the formal criteria used to evaluate whether a cover
decomposition respects semantic meaning boundaries.  In the JuGeo framework,
a *cover* is an open covering of the target semantic space, and a *good*
cover is one where:

  1. Every cover element aligns with a coherent semantic region (coherence).
  2. Cover elements overlap only where necessary for gluing (overlap control).
  3. The union of all cover elements truly covers the entire target (coverage).
  4. Section boundaries coincide with detected semantic boundaries (alignment).

Theory reminder
---------------
Judgments = tuples (c, phi, A, E, O, B, T, Pi) -- NEVER booleans.
Trust = ordered algebra T=(E_adm, leq, oplus, ominus, up_pi, down_chi) NEVER a float.
TrustTier: PROPOSAL -> REVIEWED -> VERIFIED -> RUNTIME_WITNESSED -> PROOF_BACKED.
Obstructions = Cech H1 cohomology classes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict, replace
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple, Union
import itertools
import functools
import collections
import abc
import re
import math

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JuGeo imports with fallback stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
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
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust tiers forming a bounded lattice.

    The lattice ordering is:
        PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED

    Join is max, meet is min -- the standard total-order lattice.
    """
    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: "TrustTier") -> "TrustTier":
        """Lattice join (least upper bound)."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Lattice meet (greatest lower bound)."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> "TrustTier":
        """Promote to next tier if possible."""
        next_val = min(self.value + 1, TrustTier.PROOF_BACKED.value)
        return TrustTier(next_val)

    def demote(self) -> "TrustTier":
        """Demote to previous tier if possible."""
        prev_val = max(self.value - 1, TrustTier.PROPOSAL.value)
        return TrustTier(prev_val)

# ---------------------------------------------------------------------------
# Core shared dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, phi, A, E, O, B, T, Pi) -- NEVER a boolean."""
    context: Any; formula: Any; assumptions: tuple; evidence: tuple
    obligations: tuple; burden: Any; trust: TrustTier; provenance: Any


@dataclass(frozen=True)
class CechObstruction:
    """A Cech H1 cohomology class witnessing descent failure."""
    cover_id: str; cocycle: frozenset; cohomology_class: str; description: str
    def is_trivial(self) -> bool: return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
DEFAULT_MIN_SECTION_SIZE: int = 32
DEFAULT_MAX_SECTION_SIZE: int = 4096
DEFAULT_MAX_OVERLAP_FRACTION: float = 0.25
BOUNDARY_HEURISTIC_PATTERNS: Tuple[str, ...] = (
    r"\n\n", r"^(def |class )", r"^#{1,4}\s",
    r"^//\s*[-=]{3,}", r"^\s*\*{3,}", r"^[A-Z][A-Z ]{4,}$",
)
COHERENCE_KEYWORDS: FrozenSet[str] = frozenset({
    "function", "class", "module", "return", "import", "def", "if", "for",
    "while", "try", "except", "with", "assert", "yield", "lambda",
})


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticDecompositionCriteria:
    """A structured set of criteria for evaluating whether a cover
    decomposition respects semantic meaning boundaries.

    Each criterion carries a *weight* used in weighted scoring, a
    *minimum_score* threshold below which the criterion is violated,
    and a *strict* flag that turns any violation into a hard failure.

    Attributes
    ----------
    name : str
        Human-readable criterion name.
    description : str
        Detailed description of what the criterion measures.
    criteria_id : str
        Unique identifier.
    weight : float
        Relative importance in [0, 1].
    minimum_score : float
        Lowest acceptable score.
    strict : bool
        Hard error on failure when True.
    """
    name: str
    description: str
    criteria_id: str
    weight: float
    minimum_score: float
    strict: bool

    def validate(self) -> bool:
        """Return True iff all field invariants hold.

        Checks weight in [0,1], minimum_score in [0,1], non-empty name/id.
        """
        if not (0.0 <= self.weight <= 1.0):
            logger.warning("Criterion %s weight out of range: %f", self.criteria_id, self.weight)
            return False
        if not (0.0 <= self.minimum_score <= 1.0):
            logger.warning("Criterion %s min_score out of range", self.criteria_id)
            return False
        if not self.name or not self.criteria_id:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {"name": self.name, "description": self.description,
                "criteria_id": self.criteria_id, "weight": self.weight,
                "minimum_score": self.minimum_score, "strict": self.strict}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SemanticDecompositionCriteria":
        """Deserialise from a plain dictionary."""
        return cls(name=d["name"], description=d.get("description", ""),
                   criteria_id=d["criteria_id"], weight=float(d.get("weight", 1.0)),
                   minimum_score=float(d.get("minimum_score", 0.0)),
                   strict=bool(d.get("strict", False)))

    def merge(self, other: "SemanticDecompositionCriteria") -> "SemanticDecompositionCriteria":
        """Merge two criteria by averaging weights/scores.

        The merged criterion inherits strict=True if *either* source is strict.
        """
        merged_id = hashlib.sha256((self.criteria_id + other.criteria_id).encode()).hexdigest()[:16]
        return SemanticDecompositionCriteria(
            name=f"{self.name}+{other.name}",
            description=f"Merged: {self.description} | {other.description}",
            criteria_id=merged_id, weight=(self.weight + other.weight) / 2.0,
            minimum_score=max(self.minimum_score, other.minimum_score),
            strict=self.strict or other.strict)

    def is_satisfied_by(self, score: float) -> bool:
        """Return whether *score* satisfies this criterion."""
        return score >= self.minimum_score


@dataclass(frozen=True)
class CoverQualityScore:
    """Multi-dimensional quality score for a cover design.

    Attributes
    ----------
    coverage : float
        Fraction of the target that is covered, in [0, 1].
    overlap_penalty : float
        Penalty for excessive overlap, in [0, 1].
    boundary_alignment : float
        How well section boundaries align with semantic boundaries.
    coherence : float
        Average intra-section semantic coherence.
    """
    coverage: float
    overlap_penalty: float
    boundary_alignment: float
    coherence: float

    def total_score(self) -> float:
        """Weighted total: (coverage + boundary_alignment + coherence)/3 - overlap_penalty."""
        raw = (self.coverage + self.boundary_alignment + self.coherence) / 3.0 - self.overlap_penalty
        return max(0.0, min(1.0, raw))

    def is_passing(self, threshold: float = 0.5) -> bool:
        """Return True iff total_score() >= threshold."""
        return self.total_score() >= threshold

    def to_dict(self) -> Dict[str, Any]:
        """Serialise including computed total."""
        return {"coverage": self.coverage, "overlap_penalty": self.overlap_penalty,
                "boundary_alignment": self.boundary_alignment, "coherence": self.coherence,
                "total_score": self.total_score()}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CoverQualityScore":
        """Deserialise from dictionary."""
        return cls(coverage=float(d["coverage"]), overlap_penalty=float(d["overlap_penalty"]),
                   boundary_alignment=float(d["boundary_alignment"]), coherence=float(d["coherence"]))

    def weighted_combination(self, weights: Dict[str, float]) -> float:
        """Compute custom weighted combination of score dimensions.

        Parameters
        ----------
        weights : dict
            Mapping from dimension name to weight.

        Returns
        -------
        float
        """
        tw = 0.0; tv = 0.0
        dv = {"coverage": self.coverage, "overlap_penalty": self.overlap_penalty,
              "boundary_alignment": self.boundary_alignment, "coherence": self.coherence}
        for dim, w in weights.items():
            if dim in dv:
                tw += w; tv += w * dv[dim]
        return tv / tw if tw else 0.0


@dataclass(frozen=True)
class SemanticBoundary:
    """A detected boundary point where semantics shift significantly.

    Attributes
    ----------
    position : int
        Character offset of the boundary in the source text.
    boundary_type : str
        Category: 'paragraph_break', 'function_def', 'heading', etc.
    confidence : float
        Detection confidence in [0, 1].
    left_context : str
        Snippet of text immediately to the left.
    right_context : str
        Snippet of text immediately to the right.
    markers : frozenset
        Set of string markers that triggered this boundary.
    """
    position: int
    boundary_type: str
    confidence: float
    left_context: str
    right_context: str
    markers: frozenset

    def is_strong(self) -> bool:
        """A boundary is strong when confidence >= 0.7."""
        return self.confidence >= 0.7

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {"position": self.position, "boundary_type": self.boundary_type,
                "confidence": self.confidence, "left_context": self.left_context,
                "right_context": self.right_context, "markers": sorted(self.markers)}


@dataclass(frozen=True)
class DecompositionPolicy:
    """Specifies trade-off preferences for decomposition.

    Controls whether the algorithm should prefer coverage (ensuring
    every byte is covered even if overlap is high) or minimal overlap.

    Attributes
    ----------
    policy_id : str
    prefer_coverage : bool
    max_overlap_fraction : float
    min_section_size : int
    max_section_size : int
    require_full_coverage : bool
    """
    policy_id: str
    prefer_coverage: bool
    max_overlap_fraction: float
    min_section_size: int
    max_section_size: int
    require_full_coverage: bool

    def is_valid(self) -> bool:
        """Check invariants."""
        if not self.policy_id:
            return False
        if not (0.0 <= self.max_overlap_fraction <= 1.0):
            return False
        if self.min_section_size <= 0:
            return False
        if self.max_section_size < self.min_section_size:
            return False
        return True

    def apply(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter/adjust sections to conform to this policy.

        Drops small sections, splits large ones at midpoint, trims excess overlap.
        """
        result: List[Dict[str, Any]] = []
        for sec in sections:
            size = sec["end"] - sec["start"]
            if size < self.min_section_size:
                continue
            if size > self.max_section_size:
                mid = sec["start"] + size // 2
                result.append({**sec, "end": mid})
                result.append({**sec, "start": mid})
            else:
                result.append(sec)
        trimmed: List[Dict[str, Any]] = []
        for i, sec in enumerate(result):
            if i > 0:
                prev = trimmed[-1]
                overlap = max(0, prev["end"] - sec["start"])
                sec_size = sec["end"] - sec["start"]
                if sec_size > 0 and overlap / sec_size > self.max_overlap_fraction:
                    new_start = prev["end"] - int(sec_size * self.max_overlap_fraction)
                    sec = {**sec, "start": max(sec["start"], new_start)}
            trimmed.append(sec)
        return trimmed


@dataclass(frozen=True)
class CriteriaEvaluator:
    """Evaluates a proposed cover against a list of criteria.

    Attributes
    ----------
    criteria : tuple of SemanticDecompositionCriteria
    policy : DecompositionPolicy
    """
    criteria: tuple
    policy: DecompositionPolicy

    def evaluate(self, cover: List[Dict[str, Any]]) -> CoverQualityScore:
        """Evaluate cover, returning CoverQualityScore."""
        return score_decomposition(cover, list(self.criteria), self.policy)

    def score_all(self, cover: List[Dict[str, Any]]) -> Dict[str, float]:
        """Return per-criterion scores."""
        scores: Dict[str, float] = {}
        ts = _total_size_of_cover(cover)
        for c in self.criteria:
            nl = c.name.lower()
            if "coverage" in nl:
                scores[c.criteria_id] = _compute_coverage(cover, ts)
            elif "overlap" in nl:
                scores[c.criteria_id] = 1.0 - _compute_overlap_penalty(cover, ts)
            elif "boundary" in nl:
                scores[c.criteria_id] = _compute_boundary_alignment(cover)
            elif "coherence" in nl:
                scores[c.criteria_id] = _compute_coherence(cover)
            else:
                scores[c.criteria_id] = 0.5
        return scores

    def find_violations(self, cover: List[Dict[str, Any]]) -> List[SemanticDecompositionCriteria]:
        """Return criteria violated by this cover."""
        scores = self.score_all(cover)
        return [c for c in self.criteria if not c.is_satisfied_by(scores.get(c.criteria_id, 0.0))]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _total_size_of_cover(cover: List[Dict[str, Any]]) -> int:
    """Total span from min start to max end."""
    if not cover:
        return 0
    return max(s["end"] for s in cover) - min(s["start"] for s in cover)


def _compute_coverage(cover: List[Dict[str, Any]], total_size: int) -> float:
    """Coverage via interval-union merge."""
    if total_size <= 0 or not cover:
        return 0.0
    intervals = sorted([(s["start"], s["end"]) for s in cover])
    merged: List[Tuple[int, int]] = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return min(1.0, sum(e - s for s, e in merged) / total_size)


def _compute_overlap_penalty(cover: List[Dict[str, Any]], total_size: int) -> float:
    """Overlap penalty: sum of adjacent intersections / total_size."""
    if total_size <= 0 or len(cover) < 2:
        return 0.0
    sc = sorted(cover, key=lambda s: s["start"])
    ot = sum(max(0, sc[i]["end"] - sc[i + 1]["start"]) for i in range(len(sc) - 1))
    return min(1.0, ot / total_size)


def _compute_boundary_alignment(cover: List[Dict[str, Any]]) -> float:
    """Score section boundary alignment with detected semantic boundaries."""
    if not cover:
        return 0.0
    aligned = 0; total_edges = 0
    for sec in cover:
        content = sec.get("content", "")
        if not content:
            continue
        bds = find_semantic_boundaries(content, min_confidence=0.3)
        positions = {b.position for b in bds}
        total_edges += 2
        if 0 in positions or not positions:
            aligned += 1
        if len(content) in positions or len(content) - 1 in positions or not positions:
            aligned += 1
    return aligned / total_edges if total_edges else 1.0


def _compute_coherence(cover: List[Dict[str, Any]]) -> float:
    """Average keyword-density coherence across sections."""
    if not cover:
        return 0.0
    scores: List[float] = []
    for sec in cover:
        content = sec.get("content", "")
        words = content.lower().split() if content else []
        if not words:
            scores.append(0.0); continue
        density = sum(1 for w in words if w in COHERENCE_KEYWORDS) / len(words)
        if density < 0.01: scores.append(0.2)
        elif density < 0.05: scores.append(0.5)
        elif density <= 0.25: scores.append(1.0)
        elif density <= 0.5: scores.append(0.7)
        else: scores.append(0.4)
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def score_decomposition(
    cover: List[Dict[str, Any]],
    criteria: List[SemanticDecompositionCriteria],
    policy: DecompositionPolicy,
) -> CoverQualityScore:
    """Score a cover decomposition against criteria and policy.

    Takes a cover (list of sections with start/end/content/label) and returns
    a CoverQualityScore.  Computes coverage, overlap penalty, boundary
    alignment, and coherence dimensions.

    Parameters
    ----------
    cover : list of dict
        Each dict has 'start', 'end', 'content', 'label'.
    criteria : list of SemanticDecompositionCriteria
    policy : DecompositionPolicy

    Returns
    -------
    CoverQualityScore
    """
    logger.info("Scoring decomposition: %d sections, %d criteria", len(cover), len(criteria))
    adj = policy.apply(cover) if policy.is_valid() else cover
    ts = _total_size_of_cover(adj)
    return CoverQualityScore(
        coverage=max(0.0, min(1.0, _compute_coverage(adj, ts))),
        overlap_penalty=max(0.0, min(1.0, _compute_overlap_penalty(adj, ts))),
        boundary_alignment=max(0.0, min(1.0, _compute_boundary_alignment(adj))),
        coherence=max(0.0, min(1.0, _compute_coherence(adj))))


def find_semantic_boundaries(text: str, min_confidence: float = 0.5) -> List[SemanticBoundary]:
    """Detect semantic boundaries in text/code using heuristics.

    Uses paragraph breaks, function/class definitions, markdown headings,
    and ALL-CAPS section headings as boundary indicators.

    Parameters
    ----------
    text : str
        Source text to analyse.
    min_confidence : float
        Discard boundaries below this confidence threshold.

    Returns
    -------
    list of SemanticBoundary
        Sorted by position ascending.
    """
    bds: List[SemanticBoundary] = []
    cr = 30
    # Paragraph breaks
    idx = 0
    while True:
        pos = text.find("\n\n", idx)
        if pos == -1:
            break
        bds.append(SemanticBoundary(position=pos, boundary_type="paragraph_break",
                   confidence=0.8, left_context=text[max(0, pos - cr):pos],
                   right_context=text[pos + 2:pos + 2 + cr], markers=frozenset({"para_break"})))
        idx = pos + 2
    # Line-based patterns
    ls = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if re.match(r"^(def |class )", stripped):
            bds.append(SemanticBoundary(position=ls, boundary_type="function_or_class_def",
                       confidence=0.9, left_context=text[max(0, ls - cr):ls],
                       right_context=text[ls:ls + cr], markers=frozenset({"def_or_class"})))
        elif re.match(r"^#{1,4}\s", stripped):
            bds.append(SemanticBoundary(position=ls, boundary_type="heading",
                       confidence=0.75, left_context=text[max(0, ls - cr):ls],
                       right_context=text[ls:ls + cr], markers=frozenset({"heading"})))
        elif re.match(r"^[A-Z][A-Z ]{4,}$", stripped) and len(stripped) < 60:
            bds.append(SemanticBoundary(position=ls, boundary_type="caps_heading",
                       confidence=0.6, left_context=text[max(0, ls - cr):ls],
                       right_context=text[ls:ls + cr], markers=frozenset({"caps_heading"})))
        ls += len(line) + 1
    bds = [b for b in bds if b.confidence >= min_confidence]
    bds.sort(key=lambda b: b.position)
    # Deduplicate nearby boundaries
    deduped: List[SemanticBoundary] = []
    for b in bds:
        if deduped and abs(b.position - deduped[-1].position) < 5:
            if b.confidence > deduped[-1].confidence:
                deduped[-1] = b
        else:
            deduped.append(b)
    return deduped


def optimize_cover_design(
    cover: List[Dict[str, Any]],
    criteria: List[SemanticDecompositionCriteria],
    policy: DecompositionPolicy,
    max_iterations: int = 50,
) -> Tuple[List[Dict[str, Any]], CoverQualityScore]:
    """Improve cover via greedy hill-climbing.

    At each iteration the algorithm scores the current cover, then tries
    three mutations (split largest, merge smallest adjacent, shift a
    boundary) and keeps the best improvement.

    Parameters
    ----------
    cover : list of dict
    criteria : list of SemanticDecompositionCriteria
    policy : DecompositionPolicy
    max_iterations : int

    Returns
    -------
    tuple of (optimised cover, final CoverQualityScore)
    """
    logger.info("Optimising cover design for up to %d iterations", max_iterations)
    cc = [dict(s) for s in cover]
    cs = score_decomposition(cc, criteria, policy)
    for iteration in range(max_iterations):
        bc, bs = cc, cs
        # Try splitting the largest section at its midpoint
        if cc:
            li = max(range(len(cc)), key=lambda i: cc[i]["end"] - cc[i]["start"])
            sec = cc[li]; mid = (sec["start"] + sec["end"]) // 2
            if sec["start"] < mid < sec["end"]:
                cand = cc[:li] + [{**sec, "end": mid, "label": sec.get("label", "") + "_L"},
                                  {**sec, "start": mid, "label": sec.get("label", "") + "_R"}] + cc[li+1:]
                ns = score_decomposition(cand, criteria, policy)
                if ns.total_score() > bs.total_score():
                    bc, bs = cand, ns
        # Try merging the two smallest adjacent sections
        if len(cc) >= 2:
            si = sorted(range(len(cc)), key=lambda i: cc[i]["start"])
            best_pair = None; min_ps = float("inf")
            for j in range(len(si) - 1):
                ai, bi = si[j], si[j+1]
                ps = (cc[ai]["end"] - cc[ai]["start"]) + (cc[bi]["end"] - cc[bi]["start"])
                if ps < min_ps:
                    min_ps = ps; best_pair = (ai, bi)
            if best_pair is not None:
                ai, bi = best_pair
                ms = {"start": min(cc[ai]["start"], cc[bi]["start"]),
                      "end": max(cc[ai]["end"], cc[bi]["end"]),
                      "content": cc[ai].get("content", "") + cc[bi].get("content", ""),
                      "label": cc[ai].get("label", "") + "_M"}
                cand = [s for i, s in enumerate(cc) if i not in best_pair] + [ms]
                ns = score_decomposition(cand, criteria, policy)
                if ns.total_score() > bs.total_score():
                    bc, bs = cand, ns
        # Try shifting each boundary by +/-10
        for si in range(len(cc)):
            for delta in (-10, 10):
                cand = [dict(s) for s in cc]
                cand[si]["end"] += delta
                if cand[si]["end"] <= cand[si]["start"]:
                    continue
                ns = score_decomposition(cand, criteria, policy)
                if ns.total_score() > bs.total_score():
                    bc, bs = cand, ns
        if bs.total_score() <= cs.total_score():
            logger.debug("Converged at iteration %d", iteration)
            break
        cc, cs = bc, bs
    logger.info("Optimisation done: final=%.4f", cs.total_score())
    return cc, cs


def evaluate_criteria(
    cover: List[Dict[str, Any]],
    criteria: List[SemanticDecompositionCriteria],
    policy: DecompositionPolicy,
) -> Dict[str, Any]:
    """Apply all criteria to a cover and return a structured report.

    Returns a dict with keys: passed, failed, warnings, scores,
    overall_quality, overall_judgment (a Judgment object).
    """
    ev = CriteriaEvaluator(criteria=tuple(criteria), policy=policy)
    pcs = ev.score_all(cover)
    viol = ev.find_violations(cover)
    ov = ev.evaluate(cover)
    passed = [c.criteria_id for c in criteria if c.is_satisfied_by(pcs.get(c.criteria_id, 0.0))]
    failed = [c.criteria_id for c in criteria if not c.is_satisfied_by(pcs.get(c.criteria_id, 0.0))]
    warnings = [f"{'STRICT ' if c.strict else ''}criterion '{c.name}' failed: "
                f"{pcs.get(c.criteria_id, 0.0):.3f} < {c.minimum_score:.3f}"
                for c in criteria if not c.is_satisfied_by(pcs.get(c.criteria_id, 0.0))]
    trust = TrustTier.VERIFIED if not failed else TrustTier.PROPOSAL
    jdg = Judgment(
        context="cover_evaluation",
        formula=f"evaluate({len(cover)} sections, {len(criteria)} criteria)",
        assumptions=tuple(c.criteria_id for c in criteria),
        evidence=tuple(pcs.items()),
        obligations=tuple(v.criteria_id for v in viol),
        burden=len(failed), trust=trust,
        provenance="semantic_decomposition_criteria_co")
    return {"passed": passed, "failed": failed, "warnings": warnings,
            "scores": pcs, "overall_quality": ov.to_dict(), "overall_judgment": jdg}


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    print("=== semantic_decomposition_criteria_co smoke test ===")
    pol = DecompositionPolicy(policy_id="tp", prefer_coverage=True,
        max_overlap_fraction=0.2, min_section_size=5, max_section_size=500,
        require_full_coverage=True)
    assert pol.is_valid()
    cc = SemanticDecompositionCriteria(name="coverage", description="frac", criteria_id="cc",
         weight=0.4, minimum_score=0.3, strict=True)
    co = SemanticDecompositionCriteria(name="overlap", description="olap", criteria_id="co",
         weight=0.2, minimum_score=0.2, strict=False)
    cb = SemanticDecompositionCriteria(name="boundary", description="bnd", criteria_id="cb",
         weight=0.2, minimum_score=0.2, strict=False)
    ch = SemanticDecompositionCriteria(name="coherence", description="coh", criteria_id="ch",
         weight=0.2, minimum_score=0.2, strict=False)
    ac = [cc, co, cb, ch]
    for c in ac:
        assert c.validate()
        assert SemanticDecompositionCriteria.from_dict(c.to_dict()).name == c.name
    mg = cc.merge(co); assert mg.strict
    txt = "def hello():\n    print('hi')\n\nclass Foo:\n    pass\n\ndef bar():\n    return 42\n"
    cov = [{"start": 0, "end": 30, "content": txt[:30], "label": "s1"},
           {"start": 25, "end": 60, "content": txt[25:60], "label": "s2"},
           {"start": 55, "end": len(txt), "content": txt[55:], "label": "s3"}]
    qs = score_decomposition(cov, ac, pol)
    print(f"Score: {qs.to_dict()}")
    assert 0.0 <= qs.total_score() <= 1.0
    bds = find_semantic_boundaries(txt)
    print(f"Boundaries: {len(bds)}")
    oc, os_ = optimize_cover_design(cov, ac, pol, max_iterations=3)
    print(f"Optimised: {os_.total_score():.4f}")
    rpt = evaluate_criteria(cov, ac, pol)
    assert isinstance(rpt["overall_judgment"], Judgment)
    obs = CechObstruction(cover_id="t", cocycle=frozenset(), cohomology_class="0", description="triv")
    assert obs.is_trivial()
    t1 = TrustTier.PROPOSAL; t2 = TrustTier.VERIFIED
    assert t1.join(t2) == TrustTier.VERIFIED
    assert t1.meet(t2) == TrustTier.PROPOSAL
    assert t1.promote() == TrustTier.REVIEWED
    assert TrustTier.PROOF_BACKED.promote() == TrustTier.PROOF_BACKED
    assert TrustTier.PROPOSAL.demote() == TrustTier.PROPOSAL
    wc = qs.weighted_combination({"coverage": 2.0, "coherence": 1.0})
    print(f"Weighted: {wc:.4f}")
    qs_d = qs.to_dict()
    qs2 = CoverQualityScore.from_dict(qs_d)
    assert abs(qs2.total_score() - qs.total_score()) < 1e-9
    print("=== All smoke tests passed ===")
