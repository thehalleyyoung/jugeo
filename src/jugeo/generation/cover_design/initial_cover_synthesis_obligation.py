"""
Initial cover synthesis — generating first candidate covers with their proof obligations.

This module implements the initial synthesis phase of cover design in JuGeo. It is
responsible for generating the first candidate covers for a given target geometry,
attaching proof obligations to each proposal, and organising those proposals into
a structured synthesis result ready for downstream verification and discharge.

The design follows a judgment-based architecture: every candidate cover is paired
with an explicit set of obligations (liveness, non-overlap, alignment, size bounds,
etc.) that must be discharged before the cover is accepted.  Trust is tracked via
the ``TrustTier`` lattice so that evidence from solvers, runtime witnesses, and human
oracles can be combined in a principled way.

Key concepts
------------
- **Cover proposal** – a set of candidate elements together with their obligations.
- **Synthesis strategy** – a policy that translates a target size and boundary list
  into a concrete set of cover-element dictionaries.
- **Proof obligation** – a single named property that must be verified for a cover
  proposal to be accepted.
- **Trust tier** – a five-level lattice that tracks the epistemic status of every
  piece of evidence attached to a judgment.

# copilot: initial-cover-synthesis-obligation

Usage example (see also ``__main__`` block below)::

    strategy = SynthesisStrategy(
        strategy_id="s1",
        strategy_name="uniform",
        prefer_boundary_alignment=True,
        max_elements=8,
        min_element_size=16,
        overlap_allowed=True,
        size_based=True,
    )
    target = {"id": "t1", "size": 128, "boundaries": [32, 64, 96]}
    synthesis = synthesize_initial_cover(target, strategy, target["boundaries"])
    obligations = generate_cover_obligations(synthesis.best_proposal())
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
import itertools
import functools
import collections
import abc
import re
import math

# ---------------------------------------------------------------------------
# Optional jugeo imports
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification,
        FailureScope,
        JuGeoError,
        StructuredFailure,
        raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False

    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"
        ENCODING = "encoding"
        UNKNOWN = "unknown"

    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"
        DESCENT_OBSTRUCTION = "descent_obstruction"
        UNCLASSIFIED = "unclassified"

    class JuGeoError(RuntimeError):  # type: ignore[no-redef]
        pass

    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None:
            self.message = message

    def raise_with_scope(  # type: ignore[misc]
        code: str,
        *,
        message: str,
        provenance: Any = None,
        **kw: Any,
    ) -> None:
        raise JuGeoError(f"[{code}] {message}")


try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind,
        JudgmentStatus,
        PropositionKind,
        ProvenanceSource,
        TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False

    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"
        BEHAVIORAL = "behavioral"
        RELATIONAL = "relational"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"
        RUNTIME_WITNESS = "runtime_witness"
        ORACLE_PROPOSAL = "oracle_proposal"

    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"
        RUNTIME = "runtime"
        ORACLE = "oracle"
        HUMAN = "human"


# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust tier lattice
# ---------------------------------------------------------------------------


class TrustTier(IntEnum):
    """Five-level trust lattice for cover evidence.

    The lattice is totally ordered: PROPOSAL < REVIEWED < VERIFIED <
    RUNTIME_WITNESSED < PROOF_BACKED.  The ``join`` and ``meet`` operations
    implement the usual least-upper-bound / greatest-lower-bound operators on
    this chain.
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
# Shared frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """A judgment (c, phi, A, E, O, B, T, Pi) — NEVER a boolean.

    Following the JuGeo judgment-tuple convention every judgment carries:
    - *context*   – the syntactic context (e.g. cover id, target id) in which
                    the formula was asserted.
    - *formula*   – the proposition being judged.
    - *assumptions* – background facts treated as granted for this judgment.
    - *evidence*  – a tuple of evidence items supporting the judgment.
    - *obligations* – proof obligations that remain open.
    - *burden*    – the party responsible for discharging obligations.
    - *trust*     – a ``TrustTier`` value summarising epistemic status.
    - *provenance* – origin metadata (solver, oracle, human, runtime).
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
    """A Čech H¹ cohomology class witnessing descent failure.

    When the gluing conditions for a cover candidate fail — i.e. the local
    sections on pairwise intersections do not agree on triple intersections —
    a 1-cocycle in the Čech complex is non-trivial.  This dataclass records
    that obstruction so that it can be reported and possibly resolved by
    refining the cover.

    Attributes
    ----------
    cover_id:
        Identifier of the cover proposal that produced the obstruction.
    cocycle:
        Frozenset of (i, j) index pairs encoding the non-trivial 1-cocycle.
    cohomology_class:
        A string label for the cohomology class (e.g. ``"[c_01 + c_12 - c_02]"``).
    description:
        Human-readable summary of what the obstruction means geometrically.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return ``True`` iff the cocycle is the trivial (zero) class."""
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverCandidate:
    """A single element of a cover proposal with overlap metadata.

    A cover candidate is a contiguous interval ``[start, end)`` of a
    linearised target, together with bookkeeping about how much it overlaps
    with its left and right neighbours.

    Attributes
    ----------
    candidate_id:
        Unique identifier.
    start:
        Inclusive start index in the target sequence.
    end:
        Exclusive end index in the target sequence.
    label:
        Short human-readable label (e.g. ``"U_3"``).
    overlap_left:
        Number of positions overlapping with the preceding candidate.
    overlap_right:
        Number of positions overlapping with the following candidate.
    confidence:
        Float in ``[0.0, 1.0]`` reflecting how well this element fits the
        strategy heuristics.
    """

    candidate_id: str
    start: int
    end: int
    label: str
    overlap_left: int
    overlap_right: int
    confidence: float

    def size(self) -> int:
        """Return the number of positions covered by this candidate."""
        return max(0, self.end - self.start)

    def overlaps_with(self, other: "CoverCandidate") -> bool:
        """Return ``True`` if this candidate and *other* share at least one position."""
        return self.start < other.end and other.start < self.end

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "candidate_id": self.candidate_id,
            "start": self.start,
            "end": self.end,
            "label": self.label,
            "overlap_left": self.overlap_left,
            "overlap_right": self.overlap_right,
            "confidence": self.confidence,
            "size": self.size(),
        }


@dataclass(frozen=True)
class CoverSynthesisObligation:
    """A single proof obligation attached to a cover proposal.

    Each obligation records *what* must be verified (``obligation_type`` and
    ``description``), *how urgent* it is (``priority`` — higher is more
    critical), and whether it has already been discharged together with the
    evidence that was used.

    Attributes
    ----------
    obligation_id:
        Unique identifier generated at construction time.
    cover_id:
        The cover proposal this obligation belongs to.
    obligation_type:
        One of ``"liveness"``, ``"non_overlap"``, ``"alignment"``,
        ``"size_bound"``, ``"completeness"``, or ``"coherence"``.
    description:
        Human-readable statement of what must hold.
    priority:
        Integer priority; obligations with ``priority >= 8`` are considered
        critical and must be discharged before the proposal can be accepted.
    discharged:
        ``True`` once supporting evidence has been supplied.
    evidence:
        Tuple of evidence strings or dicts accumulated via ``discharge()``.
    """

    obligation_id: str
    cover_id: str
    obligation_type: str
    description: str
    priority: int
    discharged: bool
    evidence: tuple

    def discharge(self, new_evidence: Any) -> "CoverSynthesisObligation":
        """Return a new obligation with *new_evidence* appended and ``discharged=True``.

        Parameters
        ----------
        new_evidence:
            Any serialisable evidence item (string, dict, etc.).

        Returns
        -------
        CoverSynthesisObligation
            A new frozen instance identical to ``self`` except for the updated
            ``evidence`` tuple and ``discharged=True``.
        """
        updated_evidence = self.evidence + (new_evidence,)
        log.debug(
            "Discharging obligation %s (type=%s) with evidence %r",
            self.obligation_id,
            self.obligation_type,
            new_evidence,
        )
        return replace(self, evidence=updated_evidence, discharged=True)

    def is_critical(self) -> bool:
        """Return ``True`` if this obligation has priority >= 8."""
        return self.priority >= 8

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "obligation_id": self.obligation_id,
            "cover_id": self.cover_id,
            "obligation_type": self.obligation_type,
            "description": self.description,
            "priority": self.priority,
            "discharged": self.discharged,
            "evidence": list(self.evidence),
            "is_critical": self.is_critical(),
        }


@dataclass(frozen=True)
class CoverProposal:
    """A concrete cover proposal — a tuple of candidates plus obligations.

    A ``CoverProposal`` bundles together all the information needed to evaluate
    and eventually accept or reject a cover: the candidate elements themselves,
    the obligations that must be verified, a strategy-level quality estimate, and
    a trust tier summarising how well-evidenced the proposal is.

    Attributes
    ----------
    proposal_id:
        Unique identifier.
    cover_elements:
        Tuple of ``CoverCandidate`` instances making up this proposal.
    obligations:
        Tuple of ``CoverSynthesisObligation`` instances attached to this
        proposal.
    quality_estimate:
        Float in ``[0.0, 1.0]`` set by the synthesis strategy.
    strategy_id:
        Identifier of the ``SynthesisStrategy`` that produced this proposal.
    trust:
        Current ``TrustTier`` for this proposal.
    """

    proposal_id: str
    cover_elements: tuple
    obligations: tuple
    quality_estimate: float
    strategy_id: str
    trust: TrustTier

    def add_obligation(self, obligation: CoverSynthesisObligation) -> "CoverProposal":
        """Return a new proposal with *obligation* appended.

        Parameters
        ----------
        obligation:
            The ``CoverSynthesisObligation`` to attach.

        Returns
        -------
        CoverProposal
            A new frozen instance with the obligation added.
        """
        log.debug(
            "Adding obligation %s to proposal %s",
            obligation.obligation_id,
            self.proposal_id,
        )
        return replace(self, obligations=self.obligations + (obligation,))

    def is_fully_obligated(self) -> bool:
        """Return ``True`` iff every attached obligation has been discharged."""
        if not self.obligations:
            return False
        return all(ob.discharged for ob in self.obligations)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "proposal_id": self.proposal_id,
            "cover_elements": [e.to_dict() for e in self.cover_elements],
            "obligations": [o.to_dict() for o in self.obligations],
            "quality_estimate": self.quality_estimate,
            "strategy_id": self.strategy_id,
            "trust": self.trust.name,
            "num_elements": len(self.cover_elements),
            "num_obligations": len(self.obligations),
            "is_fully_obligated": self.is_fully_obligated(),
        }


@dataclass(frozen=True)
class SynthesisStrategy:
    """A policy for translating a target size and boundaries into cover elements.

    Attributes
    ----------
    strategy_id:
        Unique identifier.
    strategy_name:
        Human-readable name (e.g. ``"uniform"``, ``"boundary_aligned"``).
    prefer_boundary_alignment:
        If ``True``, cover element boundaries are snapped to the provided
        boundary list when possible.
    max_elements:
        Upper bound on the number of elements that may be generated.
    min_element_size:
        Minimum size (in positions) that any element must have.
    overlap_allowed:
        If ``True`` adjacent elements may overlap.
    size_based:
        If ``True`` element sizes are computed proportionally from
        ``target_size``; otherwise they follow the boundary list directly.
    """

    strategy_id: str
    strategy_name: str
    prefer_boundary_alignment: bool
    max_elements: int
    min_element_size: int
    overlap_allowed: bool
    size_based: bool

    def apply(
        self,
        target_size: int,
        boundaries: List[int],
    ) -> List[Dict[str, Any]]:
        """Generate a list of cover-element dictionaries for a target.

        The algorithm works in two phases:

        1. **Candidate positions** – if ``size_based`` is ``True`` the target
           is divided into ``max_elements`` equal-width windows; otherwise the
           boundary list is used directly to delimit windows.
        2. **Overlap injection** – if ``overlap_allowed`` is ``True`` each
           window is extended by ``min_element_size // 4`` positions on each
           side (clamped to ``[0, target_size)``).

        Parameters
        ----------
        target_size:
            Total number of positions in the target.
        boundaries:
            Sorted list of interior boundary positions.

        Returns
        -------
        list of dict
            Each dict has keys ``start``, ``end``, ``label``, ``overlap_left``,
            ``overlap_right``, and ``confidence``.
        """
        if target_size <= 0:
            log.warning("apply() called with non-positive target_size=%d", target_size)
            return []

        overlap = self.min_element_size // 4 if self.overlap_allowed else 0
        elements: List[Dict[str, Any]] = []

        if self.size_based or not boundaries:
            n = min(self.max_elements, max(1, target_size // max(1, self.min_element_size)))
            window = target_size / n
            positions = [round(i * window) for i in range(n + 1)]
        else:
            positions = sorted({0} | set(boundaries) | {target_size})
            if len(positions) - 1 > self.max_elements:
                step = max(1, len(positions) // self.max_elements)
                positions = positions[::step]
                if positions[-1] != target_size:
                    positions.append(target_size)

        if self.prefer_boundary_alignment and boundaries:
            snapped: List[int] = []
            for p in positions:
                nearest = min(boundaries, key=lambda b: abs(b - p))
                if abs(nearest - p) <= self.min_element_size // 2 and nearest not in snapped:
                    snapped.append(nearest)
                elif p not in snapped:
                    snapped.append(p)
            positions = sorted(set(snapped) | {0, target_size})

        for idx in range(len(positions) - 1):
            raw_start = positions[idx]
            raw_end = positions[idx + 1]
            if raw_end - raw_start < self.min_element_size and idx < len(positions) - 2:
                log.debug("Skipping undersized window [%d, %d)", raw_start, raw_end)
                continue
            ol = min(overlap, raw_start)
            or_ = min(overlap, target_size - raw_end)
            confidence = _element_confidence(raw_start, raw_end, target_size, boundaries)
            elements.append(
                {
                    "start": raw_start - ol,
                    "end": raw_end + or_,
                    "label": f"U_{idx}",
                    "overlap_left": ol,
                    "overlap_right": or_,
                    "confidence": confidence,
                }
            )

        log.debug(
            "Strategy '%s' generated %d elements for target_size=%d",
            self.strategy_name,
            len(elements),
            target_size,
        )
        return elements

    def is_valid(self) -> bool:
        """Return ``True`` iff all strategy parameters are self-consistent."""
        if self.max_elements <= 0:
            return False
        if self.min_element_size <= 0:
            return False
        if not re.match(r"^[A-Za-z0-9_\-]+$", self.strategy_id):
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "prefer_boundary_alignment": self.prefer_boundary_alignment,
            "max_elements": self.max_elements,
            "min_element_size": self.min_element_size,
            "overlap_allowed": self.overlap_allowed,
            "size_based": self.size_based,
        }


@dataclass(frozen=True)
class InitialCoverSynthesis:
    """The result of an initial cover synthesis run.

    Collects all proposals generated in a single synthesis pass together with
    metadata about which strategy was used and when the run happened.

    Attributes
    ----------
    synthesis_id:
        Unique identifier for this synthesis run.
    target_id:
        Identifier of the target geometry being covered.
    target_size:
        Size of the target (number of positions).
    strategy_used:
        ``strategy_id`` of the ``SynthesisStrategy`` that was applied.
    generated_at:
        UNIX timestamp of when synthesis was performed.
    proposals:
        Tuple of ``CoverProposal`` instances generated during this run.
    """

    synthesis_id: str
    target_id: str
    target_size: int
    strategy_used: str
    generated_at: float
    proposals: tuple

    def add_proposal(self, proposal: CoverProposal) -> "InitialCoverSynthesis":
        """Return a new synthesis with *proposal* appended.

        Parameters
        ----------
        proposal:
            The ``CoverProposal`` to add.

        Returns
        -------
        InitialCoverSynthesis
            A new frozen instance with the proposal added.
        """
        log.debug(
            "Adding proposal %s to synthesis %s (total=%d)",
            proposal.proposal_id,
            self.synthesis_id,
            len(self.proposals) + 1,
        )
        return replace(self, proposals=self.proposals + (proposal,))

    def best_proposal(self) -> Optional[CoverProposal]:
        """Return the proposal with the highest ``quality_estimate``.

        Returns ``None`` if there are no proposals.
        """
        if not self.proposals:
            return None
        return max(self.proposals, key=lambda p: p.quality_estimate)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        best = self.best_proposal()
        return {
            "synthesis_id": self.synthesis_id,
            "target_id": self.target_id,
            "target_size": self.target_size,
            "strategy_used": self.strategy_used,
            "generated_at": self.generated_at,
            "proposals": [p.to_dict() for p in self.proposals],
            "num_proposals": len(self.proposals),
            "best_proposal_id": best.proposal_id if best else None,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of the synthesis run."""
        best = self.best_proposal()
        best_q = f"{best.quality_estimate:.3f}" if best else "n/a"
        return (
            f"Synthesis {self.synthesis_id[:8]} | target={self.target_id} "
            f"size={self.target_size} | strategy={self.strategy_used} | "
            f"proposals={len(self.proposals)} | best_quality={best_q}"
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _stable_id(prefix: str, *parts: Any) -> str:
    """Generate a short stable ID by hashing *parts* and prefixing with *prefix*."""
    raw = json.dumps(parts, sort_keys=True, default=str).encode()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _element_confidence(
    start: int,
    end: int,
    target_size: int,
    boundaries: List[int],
) -> float:
    """Estimate confidence for a single cover element.

    Confidence is boosted when element boundaries coincide with known
    boundaries (suggesting the element is well-aligned) and reduced when
    the element is very small relative to the mean window size.

    Parameters
    ----------
    start, end:
        Positions of the element.
    target_size:
        Total positions in the target.
    boundaries:
        Known boundary positions.

    Returns
    -------
    float
        Value in ``[0.0, 1.0]``.
    """
    size = max(0, end - start)
    if target_size <= 0:
        return 0.0
    size_score = min(1.0, size / max(1, target_size))
    alignment_bonus = 0.0
    for b in boundaries:
        if abs(b - start) <= 2 or abs(b - end) <= 2:
            alignment_bonus = min(0.3, alignment_bonus + 0.15)
    raw = 0.5 + 0.2 * size_score + alignment_bonus
    return min(1.0, round(raw, 4))


def _make_obligation(
    cover_id: str,
    obligation_type: str,
    description: str,
    priority: int,
) -> CoverSynthesisObligation:
    """Construct a fresh undischarged ``CoverSynthesisObligation``."""
    oid = _stable_id("obl", cover_id, obligation_type, description[:32])
    return CoverSynthesisObligation(
        obligation_id=oid,
        cover_id=cover_id,
        obligation_type=obligation_type,
        description=description,
        priority=priority,
        discharged=False,
        evidence=(),
    )


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def synthesize_initial_cover(
    target: Dict[str, Any],
    strategy: SynthesisStrategy,
    boundaries: List[int],
) -> InitialCoverSynthesis:
    """Generate the initial cover synthesis for *target* using *strategy*.

    This is the main entry point for cover synthesis.  It:

    1. Validates the strategy.
    2. Applies the strategy to obtain a list of raw element dicts.
    3. Wraps each element in a ``CoverCandidate``.
    4. Groups the candidates into a single ``CoverProposal`` (the strategy may
       later be extended to produce multiple proposals).
    5. Attaches base proof obligations (liveness, completeness, coherence).
    6. Returns a fully populated ``InitialCoverSynthesis``.

    Parameters
    ----------
    target:
        Dict with at least ``"id"`` and ``"size"`` keys.  May also carry
        ``"boundaries"`` (which will be merged with the explicit *boundaries*
        argument) and ``"label"`` for logging.
    strategy:
        The ``SynthesisStrategy`` to apply.
    boundaries:
        Sorted list of interior boundary positions.  Will be merged with any
        boundaries present in *target*.

    Returns
    -------
    InitialCoverSynthesis
        A populated synthesis object.  May have zero proposals if the strategy
        produces no elements (e.g. ``target_size=0``).

    Raises
    ------
    JuGeoError
        If the strategy fails its validity check.
    """
    t0 = time.monotonic()
    target_id: str = str(target.get("id", "unknown"))
    target_size: int = int(target.get("size", 0))

    if not strategy.is_valid():
        raise_with_scope(
            "SYNTH_INVALID_STRATEGY",
            message=f"Strategy {strategy.strategy_id!r} failed validity check",
            provenance=strategy.to_dict(),
        )

    # Merge boundary sources.
    merged_boundaries = sorted(set(boundaries) | set(target.get("boundaries", [])))

    log.info(
        "Synthesizing cover for target=%s size=%d strategy=%s boundaries=%d",
        target_id,
        target_size,
        strategy.strategy_id,
        len(merged_boundaries),
    )

    raw_elements = strategy.apply(target_size, merged_boundaries)

    candidates: List[CoverCandidate] = []
    for i, el in enumerate(raw_elements):
        cid = _stable_id("cand", target_id, strategy.strategy_id, i, el["start"], el["end"])
        candidates.append(
            CoverCandidate(
                candidate_id=cid,
                start=el["start"],
                end=el["end"],
                label=el.get("label", f"U_{i}"),
                overlap_left=el.get("overlap_left", 0),
                overlap_right=el.get("overlap_right", 0),
                confidence=el.get("confidence", 0.5),
            )
        )

    synthesis_id = _stable_id("syn", target_id, strategy.strategy_id, time.time())

    if not candidates:
        log.warning("No candidates generated for target=%s", target_id)
        empty_synthesis = InitialCoverSynthesis(
            synthesis_id=synthesis_id,
            target_id=target_id,
            target_size=target_size,
            strategy_used=strategy.strategy_id,
            generated_at=time.time(),
            proposals=(),
        )
        log.debug("Synthesis %s completed in %.3fs (empty)", synthesis_id, time.monotonic() - t0)
        return empty_synthesis

    # Compute per-proposal quality estimate as weighted mean confidence.
    quality = float(
        sum(c.confidence for c in candidates) / len(candidates)
    )
    # Adjust for coverage completeness.
    covered_positions: set = set()
    for c in candidates:
        covered_positions.update(range(c.start, c.end))
    coverage_ratio = len(covered_positions) / max(1, target_size)
    quality = round(0.7 * quality + 0.3 * coverage_ratio, 4)

    proposal_id = _stable_id("prop", synthesis_id, strategy.strategy_id)
    proposal = CoverProposal(
        proposal_id=proposal_id,
        cover_elements=tuple(candidates),
        obligations=(),
        quality_estimate=quality,
        strategy_id=strategy.strategy_id,
        trust=TrustTier.PROPOSAL,
    )

    # Attach base obligations generated automatically.
    for ob in generate_cover_obligations(proposal):
        proposal = proposal.add_obligation(ob)

    synthesis = InitialCoverSynthesis(
        synthesis_id=synthesis_id,
        target_id=target_id,
        target_size=target_size,
        strategy_used=strategy.strategy_id,
        generated_at=time.time(),
        proposals=(proposal,),
    )

    elapsed = time.monotonic() - t0
    log.info("Synthesis %s: %s  (%.3fs)", synthesis_id[:8], synthesis.summary(), elapsed)
    return synthesis


def generate_cover_obligations(
    proposal: CoverProposal,
) -> List[CoverSynthesisObligation]:
    """Generate the standard set of proof obligations for *proposal*.

    The obligations generated are:

    ``liveness``
        Every position in the target must be covered by at least one element.
        Priority 10 (critical).

    ``non_overlap_excess``
        Adjacent elements must not overlap by more than the permitted maximum.
        Priority 7.

    ``min_element_size``
        Each element must meet the minimum size requirement.
        Priority 8 (critical).

    ``completeness``
        The union of all elements must span the full target range.
        Priority 9 (critical).

    ``coherence``
        Elements must be sorted in non-decreasing order of ``start``.
        Priority 6.

    ``no_duplicate_label``
        No two elements may share the same label.
        Priority 5.

    Parameters
    ----------
    proposal:
        The ``CoverProposal`` for which obligations should be generated.

    Returns
    -------
    list of CoverSynthesisObligation
        All obligations.  They are returned in decreasing priority order so
        that the caller can attach them in priority order.
    """
    obligations: List[CoverSynthesisObligation] = []
    cid = proposal.proposal_id

    obligations.append(
        _make_obligation(
            cid,
            "liveness",
            "Every position in the target must be covered by at least one element.",
            10,
        )
    )
    obligations.append(
        _make_obligation(
            cid,
            "completeness",
            "The union of all candidate elements spans the complete target range [0, target_size).",
            9,
        )
    )
    obligations.append(
        _make_obligation(
            cid,
            "min_element_size",
            "Each element must satisfy size() >= strategy.min_element_size.",
            8,
        )
    )
    obligations.append(
        _make_obligation(
            cid,
            "non_overlap_excess",
            "No two adjacent elements may overlap by more than (min_element_size // 2) positions.",
            7,
        )
    )
    obligations.append(
        _make_obligation(
            cid,
            "coherence",
            "Cover elements are ordered by non-decreasing start position.",
            6,
        )
    )
    obligations.append(
        _make_obligation(
            cid,
            "no_duplicate_label",
            "All element labels within the proposal are distinct.",
            5,
        )
    )

    obligations.sort(key=lambda o: o.priority, reverse=True)
    log.debug(
        "Generated %d obligations for proposal %s",
        len(obligations),
        proposal.proposal_id[:8],
    )
    return obligations


def evaluate_cover_proposal(
    proposal: CoverProposal,
    criteria: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate *proposal* against a list of evaluation criteria.

    Each criterion is a dict with the following keys:

    ``name`` (str)
        Short identifier for the criterion (e.g. ``"coverage_ratio"``).
    ``weight`` (float)
        Relative importance in ``[0.0, 1.0]``.  Weights need not sum to 1.
    ``threshold`` (float, optional)
        Minimum value required to pass.  Defaults to ``0.0``.

    The evaluation computes a weighted score based on the following
    built-in metrics:

    - ``coverage_ratio`` – proportion of a nominal target size covered.
    - ``mean_confidence`` – mean element confidence.
    - ``obligation_discharge_ratio`` – fraction of obligations discharged.
    - ``trust_level`` – ``TrustTier`` value normalised to ``[0, 1]``.
    - ``element_count_penalty`` – penalty for having too many elements.

    Parameters
    ----------
    proposal:
        The ``CoverProposal`` to evaluate.
    criteria:
        List of criterion dicts (see above).

    Returns
    -------
    dict
        Keys: ``"scores"`` (per-criterion), ``"weighted_total"`` (float),
        ``"passed"`` (bool), ``"failures"`` (list of criterion names that
        fell below threshold).
    """
    if not proposal.cover_elements:
        return {
            "scores": {},
            "weighted_total": 0.0,
            "passed": False,
            "failures": ["no_elements"],
        }

    # Compute raw metrics.
    num_elems = len(proposal.cover_elements)
    confidences = [e.confidence for e in proposal.cover_elements]
    mean_conf = sum(confidences) / num_elems

    all_positions: set = set()
    for e in proposal.cover_elements:
        all_positions.update(range(e.start, e.end))
    max_end = max(e.end for e in proposal.cover_elements)
    coverage_ratio = len(all_positions) / max(1, max_end)

    num_obligated = sum(1 for o in proposal.obligations if o.discharged)
    discharge_ratio = (
        num_obligated / len(proposal.obligations) if proposal.obligations else 1.0
    )

    trust_norm = (proposal.trust.value - TrustTier.PROPOSAL.value) / (
        TrustTier.PROOF_BACKED.value - TrustTier.PROPOSAL.value
    )

    # Element count penalty: 0 penalty for <= 8, scales up after that.
    count_penalty = max(0.0, 1.0 - max(0, num_elems - 8) * 0.05)

    raw_metrics: Dict[str, float] = {
        "coverage_ratio": coverage_ratio,
        "mean_confidence": mean_conf,
        "obligation_discharge_ratio": discharge_ratio,
        "trust_level": trust_norm,
        "element_count_penalty": count_penalty,
    }

    scores: Dict[str, float] = {}
    failures: List[str] = []
    total_weight = 0.0
    weighted_sum = 0.0

    for crit in criteria:
        name = crit.get("name", "")
        weight = float(crit.get("weight", 1.0))
        threshold = float(crit.get("threshold", 0.0))
        value = raw_metrics.get(name, proposal.quality_estimate)
        scores[name] = value
        weighted_sum += weight * value
        total_weight += weight
        if value < threshold:
            failures.append(name)
            log.debug(
                "Proposal %s failed criterion '%s': %.4f < %.4f",
                proposal.proposal_id[:8],
                name,
                value,
                threshold,
            )

    weighted_total = weighted_sum / max(1e-9, total_weight)
    passed = len(failures) == 0 and weighted_total >= 0.5

    log.info(
        "Evaluated proposal %s: total=%.4f passed=%s failures=%s",
        proposal.proposal_id[:8],
        weighted_total,
        passed,
        failures,
    )
    return {
        "scores": scores,
        "weighted_total": round(weighted_total, 5),
        "passed": passed,
        "failures": failures,
        "raw_metrics": raw_metrics,
    }


def select_cover_strategy(
    target: Dict[str, Any],
    available_strategies: List[SynthesisStrategy],
) -> SynthesisStrategy:
    """Select the best strategy from *available_strategies* for *target*.

    The selection heuristic scores each strategy as follows:

    - **Base score**: ``1.0``.
    - **Boundary alignment bonus** (+0.2): if the target has boundaries and
      the strategy ``prefer_boundary_alignment`` is ``True``.
    - **Size compatibility** (+0.0 – +0.3): computed as
      ``1 – |ideal_n – max_elements| / max(1, ideal_n)`` where
      ``ideal_n = target_size // min_element_size``.
    - **Overlap bonus** (+0.1): if the target has more than 3 boundaries and
      the strategy allows overlap.

    The strategy with the highest score is returned.  In case of a tie the
    first one in *available_strategies* wins.

    Parameters
    ----------
    target:
        Dict with at least ``"size"`` and optionally ``"boundaries"``.
    available_strategies:
        Non-empty list of ``SynthesisStrategy`` instances.

    Returns
    -------
    SynthesisStrategy
        The selected strategy.

    Raises
    ------
    JuGeoError
        If *available_strategies* is empty.
    """
    if not available_strategies:
        raise_with_scope(
            "SYNTH_NO_STRATEGIES",
            message="available_strategies is empty; cannot select a strategy",
        )

    valid_strategies = [s for s in available_strategies if s.is_valid()]
    if not valid_strategies:
        log.warning("No valid strategies found; falling back to first available")
        valid_strategies = available_strategies

    target_size: int = int(target.get("size", 0))
    boundaries: List[int] = list(target.get("boundaries", []))
    has_boundaries = bool(boundaries)
    num_boundaries = len(boundaries)

    best_strategy = valid_strategies[0]
    best_score = -math.inf

    for strat in valid_strategies:
        score = 1.0
        if has_boundaries and strat.prefer_boundary_alignment:
            score += 0.2
        ideal_n = max(1, target_size // max(1, strat.min_element_size))
        size_compat = max(0.0, 1.0 - abs(ideal_n - strat.max_elements) / ideal_n)
        score += 0.3 * size_compat
        if num_boundaries > 3 and strat.overlap_allowed:
            score += 0.1
        log.debug("Strategy %s score=%.4f", strat.strategy_id, score)
        if score > best_score:
            best_score = score
            best_strategy = strat

    log.info(
        "Selected strategy %s (score=%.4f) for target_size=%d",
        best_strategy.strategy_id,
        best_score,
        target_size,
    )
    return best_strategy


# ---------------------------------------------------------------------------
# Smoke test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s | %(message)s",
    )

    print("=" * 70)
    print("JuGeo — Initial Cover Synthesis Obligation (smoke test)")
    print("=" * 70)

    # --- 1. Demonstrate TrustTier lattice operations ---
    t1 = TrustTier.PROPOSAL
    t2 = TrustTier.VERIFIED
    print(f"\nTrustTier.join({t1.name}, {t2.name}) = {t1.join(t2).name}")
    print(f"TrustTier.meet({t1.name}, {t2.name}) = {t1.meet(t2).name}")
    print(f"TrustTier.PROPOSAL.promote() = {TrustTier.PROPOSAL.promote().name}")
    print(f"TrustTier.PROOF_BACKED.demote() = {TrustTier.PROOF_BACKED.demote().name}")

    # --- 2. Build strategies ---
    uniform_strategy = SynthesisStrategy(
        strategy_id="uniform_v1",
        strategy_name="uniform",
        prefer_boundary_alignment=False,
        max_elements=6,
        min_element_size=16,
        overlap_allowed=False,
        size_based=True,
    )
    boundary_strategy = SynthesisStrategy(
        strategy_id="boundary_v1",
        strategy_name="boundary_aligned",
        prefer_boundary_alignment=True,
        max_elements=8,
        min_element_size=12,
        overlap_allowed=True,
        size_based=False,
    )
    print(f"\nStrategies constructed: {uniform_strategy.strategy_name}, {boundary_strategy.strategy_name}")
    print(f"uniform valid: {uniform_strategy.is_valid()}")
    print(f"boundary valid: {boundary_strategy.is_valid()}")

    # --- 3. Target definition ---
    target = {
        "id": "tgt_example_001",
        "size": 128,
        "boundaries": [20, 40, 60, 80, 100],
    }

    # --- 4. Strategy selection ---
    selected = select_cover_strategy(target, [uniform_strategy, boundary_strategy])
    print(f"\nSelected strategy: {selected.strategy_name} ({selected.strategy_id})")

    # --- 5. Synthesize cover ---
    synthesis = synthesize_initial_cover(target, selected, target["boundaries"])
    print(f"\nSynthesis summary:\n  {synthesis.summary()}")

    # --- 6. Inspect best proposal ---
    best = synthesis.best_proposal()
    if best:
        print(f"\nBest proposal ID: {best.proposal_id[:12]}...")
        print(f"  Elements ({len(best.cover_elements)}):")
        for elem in best.cover_elements:
            print(
                f"    [{elem.label}] start={elem.start} end={elem.end} "
                f"size={elem.size()} conf={elem.confidence:.4f}"
            )
        print(f"  Obligations ({len(best.obligations)}):")
        for ob in best.obligations:
            crit_marker = " [CRITICAL]" if ob.is_critical() else ""
            print(
                f"    {ob.obligation_type} priority={ob.priority} "
                f"discharged={ob.discharged}{crit_marker}"
            )

    # --- 7. Evaluate against criteria ---
    criteria = [
        {"name": "coverage_ratio",              "weight": 2.0, "threshold": 0.9},
        {"name": "mean_confidence",             "weight": 1.5, "threshold": 0.4},
        {"name": "obligation_discharge_ratio",  "weight": 1.0, "threshold": 0.0},
        {"name": "trust_level",                 "weight": 0.5, "threshold": 0.0},
        {"name": "element_count_penalty",       "weight": 0.8, "threshold": 0.5},
    ]
    if best:
        result = evaluate_cover_proposal(best, criteria)
        print(f"\nEvaluation result:")
        print(f"  weighted_total = {result['weighted_total']:.5f}")
        print(f"  passed         = {result['passed']}")
        print(f"  failures       = {result['failures']}")
        for k, v in result["scores"].items():
            print(f"    {k}: {v:.4f}")

    # --- 8. Discharge an obligation and re-evaluate ---
    if best and best.obligations:
        first_ob = best.obligations[0]
        discharged_ob = first_ob.discharge({"source": "smoke_test", "verified_by": "manual"})
        updated_obligations = (discharged_ob,) + best.obligations[1:]
        updated_proposal = CoverProposal(
            proposal_id=best.proposal_id,
            cover_elements=best.cover_elements,
            obligations=updated_obligations,
            quality_estimate=best.quality_estimate,
            strategy_id=best.strategy_id,
            trust=best.trust.promote(),
        )
        print(f"\nAfter discharging first obligation:")
        print(f"  Obligation '{first_ob.obligation_type}' discharged: {discharged_ob.discharged}")
        print(f"  Trust promoted: {best.trust.name} -> {updated_proposal.trust.name}")
        print(f"  is_fully_obligated: {updated_proposal.is_fully_obligated()}")

    # --- 9. CechObstruction demo ---
    obstruction = CechObstruction(
        cover_id=best.proposal_id if best else "demo",
        cocycle=frozenset({(0, 1), (1, 2)}),
        cohomology_class="[c_01 + c_12 - c_02]",
        description="Transition functions disagree on triple intersection U_0 ∩ U_1 ∩ U_2.",
    )
    print(f"\nCechObstruction:")
    print(f"  trivial: {obstruction.is_trivial()}")
    print(f"  class:   {obstruction.cohomology_class}")
    trivial = CechObstruction(
        cover_id="demo",
        cocycle=frozenset(),
        cohomology_class="0",
        description="No obstruction.",
    )
    print(f"  trivial (empty cocycle): {trivial.is_trivial()}")

    # --- 10. Serialisation round-trip ---
    if best:
        d = synthesis.to_dict()
        serialised = json.dumps(d, indent=2, default=str)
        print(f"\nSerialised synthesis JSON (first 300 chars):\n{serialised[:300]}...")

    print("\n" + "=" * 70)
    print("Smoke test complete.")
    print("=" * 70)
