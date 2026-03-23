r"""Mathematical Ideation Claim (C4): discovery inside JuGeo geometry.

This module implements Thesis Claim C4 from Theory2.tex Chapter 2:

    **C4** — Within the JuGeo judgment geometry, a discovery engine can
    produce mathematical structures (conjectures, constructions, proof
    strategies) that are genuinely novel with respect to a defined novelty
    measure :math:`\mu: \mathcal{S} \to \mathbb{R}_{\geq 0}`, and the
    purpose condition :math:`P` can be stated and verified within the same
    geometry.

Mathematical ideation is the process of generating candidate mathematical
structures — conjectures, lemmas, constructions, proof strategies — that:

1. Are *novel*: they differ sufficiently from the existing knowledge base
   (measured by the novelty measure μ).
2. Are *purposeful*: they advance a declared research goal (verified by the
   purpose condition P).
3. Are *discoverable*: the discovery engine terminates for all admissible
   inputs within the declared horizon.

The copilot/oracle channel plays a natural but trust-ceiling-bounded role
in ideation: copilot proposals are *candidate* structures that must be
evaluated by μ and P before they advance above ``COPILOT_SUGGESTED`` trust.
A copilot proposal that passes novelty and purpose evaluation can be promoted
through the standard trust promotion route; one that fails is discarded.

Classes
-------

:class:`IdeationSpec`
    Full specification of an ideation task.

:class:`NoveltyMeasure`
    Computes how novel a candidate structure is relative to a knowledge base.

:class:`PurposeCondition`
    Verifies that a candidate structure advances the declared purpose.

:class:`DiscoveryEngine`
    Runs the ideation loop: generate, evaluate, select, record.

:class:`CandidateStructure`
    A candidate mathematical structure produced by the discovery engine.

:class:`IdeationRound`
    One round of the ideation loop.

Theory alignment
----------------

Section 260 of Theory2.tex introduces C4.  Section 261 defines the novelty
measure; section 262 defines the purpose condition; section 263 states the
discovery engine termination theorem (Theorem 2.6.1).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class StructureKind(Enum):
    """Kind of mathematical structure produced by the discovery engine."""

    CONJECTURE = "conjecture"
    CONSTRUCTION = "construction"
    PROOF_STRATEGY = "proof_strategy"
    LEMMA = "lemma"
    COUNTEREXAMPLE = "counterexample"
    DEFINITION = "definition"
    EXAMPLE = "example"


class NoveltyGrade(Enum):
    """Qualitative novelty grade derived from the novelty measure."""

    TRIVIAL = "trivial"
    KNOWN = "known"
    INCREMENTAL = "incremental"
    NOVEL = "novel"
    HIGHLY_NOVEL = "highly_novel"

    @classmethod
    def from_score(cls, score: float) -> "NoveltyGrade":
        """Convert a numeric score to a grade."""
        if score < 0.05:
            return cls.TRIVIAL
        if score < 0.20:
            return cls.KNOWN
        if score < 0.45:
            return cls.INCREMENTAL
        if score < 0.75:
            return cls.NOVEL
        return cls.HIGHLY_NOVEL


class PurposeStatus(Enum):
    """Status of purpose condition evaluation."""

    NOT_EVALUATED = "not_evaluated"
    SATISFIES = "satisfies"
    DOES_NOT_SATISFY = "does_not_satisfy"
    PARTIALLY_SATISFIES = "partially_satisfies"
    INCONCLUSIVE = "inconclusive"


class EngineStatus(Enum):
    """Status of the discovery engine."""

    IDLE = "idle"
    RUNNING = "running"
    TERMINATED_SUCCESS = "terminated_success"
    TERMINATED_HORIZON = "terminated_horizon"
    TERMINATED_ERROR = "terminated_error"


# ---------------------------------------------------------------------------
# CandidateStructure
# ---------------------------------------------------------------------------


@dataclass
class CandidateStructure:
    """A candidate mathematical structure produced during ideation.

    Parameters
    ----------
    struct_id:
        Unique identifier.
    kind:
        :class:`StructureKind`.
    content:
        Canonical string representation of the structure.
    source:
        How this structure was generated: ``"algorithmic"``, ``"copilot"``,
        ``"random"``, or ``"guided"``.
    generated_at:
        Unix timestamp.
    novelty_score:
        Novelty score assigned by the :class:`NoveltyMeasure`, or ``None``
        if not yet evaluated.
    purpose_status:
        Purpose condition status, or ``NOT_EVALUATED``.
    promotion_record:
        If non-empty, an explicit promotion record advancing this structure
        above ``COPILOT_SUGGESTED`` trust.  Required if ``source == "copilot"``
        and the structure is to be used in a higher-trust context.
    """

    struct_id: str
    kind: StructureKind
    content: str
    source: str
    generated_at: float = field(default_factory=time.time)
    novelty_score: float | None = None
    purpose_status: PurposeStatus = PurposeStatus.NOT_EVALUATED
    promotion_record: str = ""

    def is_copilot_origin(self) -> bool:
        """Return True if this structure was produced by a copilot agent."""
        return self.source == "copilot"

    def is_accepted(self) -> bool:
        """Return True if the structure has been accepted as novel and purposeful."""
        return (
            self.novelty_score is not None
            and self.novelty_score > 0.0
            and self.purpose_status in (
                PurposeStatus.SATISFIES,
                PurposeStatus.PARTIALLY_SATISFIES,
            )
        )

    def novelty_grade(self) -> NoveltyGrade | None:
        """Return the qualitative novelty grade, or None if not evaluated."""
        if self.novelty_score is None:
            return None
        return NoveltyGrade.from_score(self.novelty_score)

    def fingerprint(self) -> str:
        """Return a short SHA-256 fingerprint of the content."""
        return hashlib.sha256(self.content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "struct_id": self.struct_id,
            "kind": self.kind.value,
            "content": self.content,
            "source": self.source,
            "generated_at": self.generated_at,
            "novelty_score": self.novelty_score,
            "novelty_grade": self.novelty_grade().value if self.novelty_grade() else None,
            "purpose_status": self.purpose_status.value,
            "promotion_record": self.promotion_record,
            "is_accepted": self.is_accepted(),
            "fingerprint": self.fingerprint(),
        }


# ---------------------------------------------------------------------------
# NoveltyMeasure
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeBase:
    """A set of known mathematical structures used as the reference for novelty.

    Parameters
    ----------
    name:
        Identifier.
    structures:
        Mapping from fingerprint to content string for known structures.
    """

    name: str
    structures: dict[str, str] = field(default_factory=dict)

    def add(self, content: str) -> str:
        """Add a structure to the knowledge base and return its fingerprint."""
        fp = hashlib.sha256(content.encode()).hexdigest()[:16]
        self.structures[fp] = content
        return fp

    def contains_fingerprint(self, fp: str) -> bool:
        """Return True if the fingerprint is known."""
        return fp in self.structures

    def size(self) -> int:
        """Return the number of known structures."""
        return len(self.structures)

    def similarity_to_nearest(self, content: str) -> float:
        """Compute a simple token-overlap similarity to the nearest known structure.

        Returns a value in [0.0, 1.0] where 1.0 means identical.
        """
        if not self.structures:
            return 0.0
        tokens_q = set(content.lower().split())
        if not tokens_q:
            return 1.0
        best = 0.0
        for known in self.structures.values():
            tokens_k = set(known.lower().split())
            if not tokens_k:
                continue
            inter = len(tokens_q & tokens_k)
            union = len(tokens_q | tokens_k)
            sim = inter / union if union > 0 else 0.0
            if sim > best:
                best = sim
        return best

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "n_structures": self.size()}


@dataclass
class NoveltyMeasure:
    r"""Computes the novelty score of a candidate structure.

    The novelty measure :math:`\mu(s)` returns a value in :math:`[0, 1]`
    where 0 means "identical to something in the knowledge base" and 1
    means "completely unlike anything known".

    The measure is non-degenerate (Claim C4 requirement): it must return
    a non-trivially zero value for genuinely new structures and a non-trivially
    non-zero value for known structures.

    Parameters
    ----------
    name:
        Identifier.
    knowledge_base:
        The :class:`KnowledgeBase` used as reference.
    kind_weights:
        Per-:class:`StructureKind` weight modifying the base novelty score.
        A kind with weight > 1.0 is considered more valuable when novel.
    min_novelty_threshold:
        Structures with μ(s) below this threshold are classified as
        non-novel (``KNOWN`` or ``TRIVIAL``).
    """

    name: str
    knowledge_base: KnowledgeBase
    kind_weights: dict[StructureKind, float] = field(default_factory=dict)
    min_novelty_threshold: float = 0.20

    def __post_init__(self) -> None:
        defaults = {k: 1.0 for k in StructureKind}
        for k, w in self.kind_weights.items():
            defaults[k] = w
        self.kind_weights = defaults

    def score(self, candidate: CandidateStructure) -> float:
        """Compute the novelty score μ(candidate).

        A fingerprint match in the knowledge base immediately returns 0.0.
        Otherwise, the score is 1 - similarity_to_nearest, scaled by the
        kind weight.

        Parameters
        ----------
        candidate:
            The structure to score.

        Returns
        -------
        float
            Novelty score in [0.0, 1.0].
        """
        fp = candidate.fingerprint()
        if self.knowledge_base.contains_fingerprint(fp):
            return 0.0
        sim = self.knowledge_base.similarity_to_nearest(candidate.content)
        raw_novelty = 1.0 - sim
        weight = self.kind_weights.get(candidate.kind, 1.0)
        return min(1.0, raw_novelty * weight)

    def is_novel(self, candidate: CandidateStructure) -> bool:
        """Return True if the structure exceeds the novelty threshold."""
        return self.score(candidate) >= self.min_novelty_threshold

    def check_non_degeneracy(
        self,
        novel_samples: Sequence[CandidateStructure],
        known_samples: Sequence[CandidateStructure],
    ) -> tuple[bool, str]:
        """Check that the measure is non-degenerate.

        Non-degeneracy requires:

        1. At least one novel sample scores above the threshold.
        2. At least one known sample scores at or below the threshold.

        Parameters
        ----------
        novel_samples:
            Structures that should be novel.
        known_samples:
            Structures that should be known (in the knowledge base).

        Returns
        -------
        tuple[bool, str]
            (True, "") if non-degenerate; (False, reason) otherwise.
        """
        if novel_samples:
            scores_novel = [self.score(s) for s in novel_samples]
            if not any(sc >= self.min_novelty_threshold for sc in scores_novel):
                return (
                    False,
                    f"All novel samples scored below threshold {self.min_novelty_threshold}; "
                    f"measure may be degenerate (always zero)",
                )
        if known_samples:
            scores_known = [self.score(s) for s in known_samples]
            if not any(sc < self.min_novelty_threshold for sc in scores_known):
                return (
                    False,
                    f"No known sample scored below threshold {self.min_novelty_threshold}; "
                    f"measure may be degenerate (always nonzero)",
                )
        return (True, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "knowledge_base": self.knowledge_base.to_dict(),
            "min_novelty_threshold": self.min_novelty_threshold,
            "kind_weights": {k.value: v for k, v in self.kind_weights.items()},
        }


# ---------------------------------------------------------------------------
# PurposeCondition
# ---------------------------------------------------------------------------


@dataclass
class PurposeGoal:
    """A single declared purpose goal for an ideation task.

    Parameters
    ----------
    goal_id:
        Short identifier.
    description:
        Prose statement of the goal.
    required_kind:
        If set, only structures of this kind can satisfy the goal.
    keyword_triggers:
        Keywords that, if present in the structure content, count as
        partial satisfaction.
    strong_triggers:
        Keywords that, if all present, count as full satisfaction.
    """

    goal_id: str
    description: str
    required_kind: StructureKind | None = None
    keyword_triggers: tuple[str, ...] = ()
    strong_triggers: tuple[str, ...] = ()

    def evaluate(self, candidate: CandidateStructure) -> PurposeStatus:
        """Evaluate whether the candidate satisfies this goal.

        Returns
        -------
        PurposeStatus
            ``SATISFIES``, ``PARTIALLY_SATISFIES``, ``DOES_NOT_SATISFY``,
            or ``INCONCLUSIVE``.
        """
        if self.required_kind is not None and candidate.kind != self.required_kind:
            return PurposeStatus.DOES_NOT_SATISFY
        content_lower = candidate.content.lower()
        if self.strong_triggers:
            if all(t.lower() in content_lower for t in self.strong_triggers):
                return PurposeStatus.SATISFIES
        if self.keyword_triggers:
            hits = sum(1 for t in self.keyword_triggers if t.lower() in content_lower)
            ratio = hits / len(self.keyword_triggers)
            if ratio >= 0.8:
                return PurposeStatus.SATISFIES
            if ratio >= 0.4:
                return PurposeStatus.PARTIALLY_SATISFIES
            if ratio > 0.0:
                return PurposeStatus.PARTIALLY_SATISFIES
            return PurposeStatus.DOES_NOT_SATISFY
        # No triggers: inconclusive (requires human review)
        return PurposeStatus.INCONCLUSIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "required_kind": self.required_kind.value if self.required_kind else None,
            "keyword_triggers": list(self.keyword_triggers),
            "strong_triggers": list(self.strong_triggers),
        }


@dataclass
class PurposeCondition:
    """Verifies that a candidate structure advances the declared research purpose.

    A purpose condition consists of one or more :class:`PurposeGoal` objects.
    The overall status is:

    * ``SATISFIES`` if all goals are satisfied.
    * ``PARTIALLY_SATISFIES`` if at least one goal is satisfied and none is
      ``DOES_NOT_SATISFY``.
    * ``DOES_NOT_SATISFY`` if any goal returns ``DOES_NOT_SATISFY``.
    * ``INCONCLUSIVE`` if all goals return ``INCONCLUSIVE``.

    Parameters
    ----------
    name:
        Identifier.
    goals:
        List of purpose goals.
    require_all:
        If True, all goals must be satisfied.  If False, any satisfied goal
        is sufficient.
    """

    name: str
    goals: list[PurposeGoal] = field(default_factory=list)
    require_all: bool = True

    def add_goal(self, goal: PurposeGoal) -> None:
        """Add a purpose goal."""
        self.goals.append(goal)

    def evaluate(self, candidate: CandidateStructure) -> PurposeStatus:
        """Evaluate the candidate against all purpose goals.

        Returns
        -------
        PurposeStatus
            Overall status.
        """
        if not self.goals:
            return PurposeStatus.INCONCLUSIVE
        statuses = [g.evaluate(candidate) for g in self.goals]
        if any(s == PurposeStatus.DOES_NOT_SATISFY for s in statuses):
            return PurposeStatus.DOES_NOT_SATISFY
        if all(s == PurposeStatus.SATISFIES for s in statuses):
            return PurposeStatus.SATISFIES
        if all(s == PurposeStatus.INCONCLUSIVE for s in statuses):
            return PurposeStatus.INCONCLUSIVE
        if any(s in (PurposeStatus.SATISFIES, PurposeStatus.PARTIALLY_SATISFIES)
               for s in statuses):
            if self.require_all:
                return PurposeStatus.PARTIALLY_SATISFIES
            return PurposeStatus.SATISFIES
        return PurposeStatus.INCONCLUSIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "goals": [g.to_dict() for g in self.goals],
            "require_all": self.require_all,
        }


# ---------------------------------------------------------------------------
# IdeationSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdeationSpec:
    """Full specification of an ideation task.

    Parameters
    ----------
    spec_id:
        Unique identifier.
    task_description:
        Prose description of the ideation task.
    target_kinds:
        Structures of these kinds are preferred.
    horizon:
        Maximum number of generation rounds.
    min_novel_structures:
        Minimum number of accepted novel structures to declare success.
    allow_copilot_proposals:
        Whether the engine may accept copilot-generated candidates.
        If True, copilot proposals are included but subject to the standard
        trust ceiling; if False, all candidates must be algorithmic.
    copilot_guidance_notes:
        Notes describing how copilot assistance was used in designing the
        ideation task.  Carries ``COPILOT_SUGGESTED`` trust until reviewed.
    """

    spec_id: str
    task_description: str
    target_kinds: tuple[StructureKind, ...]
    horizon: int
    min_novel_structures: int
    allow_copilot_proposals: bool = True
    copilot_guidance_notes: str = ""

    def __post_init__(self) -> None:
        if self.horizon <= 0:
            raise ValueError(f"horizon must be > 0, got {self.horizon}")
        if self.min_novel_structures <= 0:
            raise ValueError(
                f"min_novel_structures must be > 0, got {self.min_novel_structures}"
            )

    def targets_kind(self, kind: StructureKind) -> bool:
        """Return True if this spec targets the given kind."""
        return kind in self.target_kinds

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "task_description": self.task_description,
            "target_kinds": [k.value for k in self.target_kinds],
            "horizon": self.horizon,
            "min_novel_structures": self.min_novel_structures,
            "allow_copilot_proposals": self.allow_copilot_proposals,
            "copilot_guidance_notes": self.copilot_guidance_notes,
        }


# ---------------------------------------------------------------------------
# IdeationRound
# ---------------------------------------------------------------------------


@dataclass
class IdeationRound:
    """One round of the ideation loop.

    Parameters
    ----------
    round_id:
        Identifier.
    round_index:
        Zero-based index.
    generated:
        Structures generated this round.
    accepted:
        Structures accepted (novel + purposeful) this round.
    """

    round_id: str
    round_index: int
    generated: list[CandidateStructure] = field(default_factory=list)
    accepted: list[CandidateStructure] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    def close(self) -> None:
        """Record the end time for this round."""
        self.ended_at = time.time()

    def acceptance_rate(self) -> float:
        """Return the fraction of generated structures that were accepted."""
        if not self.generated:
            return 0.0
        return len(self.accepted) / len(self.generated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "round_index": self.round_index,
            "n_generated": len(self.generated),
            "n_accepted": len(self.accepted),
            "acceptance_rate": self.acceptance_rate(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


# ---------------------------------------------------------------------------
# DiscoveryEngine
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryEngine:
    """Runs the ideation loop: generate, evaluate, select, record.

    The engine iterates for at most ``spec.horizon`` rounds, each time:

    1. Calling the generator to produce candidate structures.
    2. Evaluating each candidate with the novelty measure.
    3. Evaluating each novel candidate with the purpose condition.
    4. Accepting novel + purposeful candidates.
    5. Adding accepted structures to the knowledge base.
    6. Terminating early if ``spec.min_novel_structures`` is reached.

    Termination is guaranteed by the horizon bound (Theorem 2.6.1).

    Parameters
    ----------
    name:
        Identifier.
    spec:
        :class:`IdeationSpec` governing this engine.
    novelty_measure:
        :class:`NoveltyMeasure` for candidate evaluation.
    purpose_condition:
        :class:`PurposeCondition` for candidate filtering.
    generator:
        A callable ``(round_index: int, spec: IdeationSpec) -> list[CandidateStructure]``
        that produces candidate structures.  The generator may use any strategy
        including copilot proposals, random search, or guided search.
    """

    name: str
    spec: IdeationSpec
    novelty_measure: NoveltyMeasure
    purpose_condition: PurposeCondition
    generator: Callable[[int, IdeationSpec], list[CandidateStructure]]
    _rounds: list[IdeationRound] = field(default_factory=list, repr=False)
    _all_accepted: list[CandidateStructure] = field(default_factory=list, repr=False)
    _status: EngineStatus = field(default=EngineStatus.IDLE, repr=False)

    def run(self) -> EngineStatus:
        """Execute the full ideation loop.

        Returns
        -------
        EngineStatus
            ``TERMINATED_SUCCESS`` if min_novel_structures is reached;
            ``TERMINATED_HORIZON`` if the horizon is exhausted;
            ``TERMINATED_ERROR`` if an exception occurs.
        """
        self._status = EngineStatus.RUNNING
        try:
            for round_idx in range(self.spec.horizon):
                if len(self._all_accepted) >= self.spec.min_novel_structures:
                    self._status = EngineStatus.TERMINATED_SUCCESS
                    return self._status
                rnd = IdeationRound(
                    round_id=str(uuid.uuid4()),
                    round_index=round_idx,
                )
                candidates = self.generator(round_idx, self.spec)
                for cand in candidates:
                    rnd.generated.append(cand)
                    # Skip copilot proposals if not allowed
                    if cand.is_copilot_origin() and not self.spec.allow_copilot_proposals:
                        continue
                    n_score = self.novelty_measure.score(cand)
                    from dataclasses import replace as _replace
                    cand = _replace(cand, novelty_score=n_score)
                    if not self.novelty_measure.is_novel(cand):
                        continue
                    p_status = self.purpose_condition.evaluate(cand)
                    cand = _replace(cand, purpose_status=p_status)
                    if p_status in (PurposeStatus.SATISFIES, PurposeStatus.PARTIALLY_SATISFIES):
                        rnd.accepted.append(cand)
                        self._all_accepted.append(cand)
                        # Add to knowledge base so future rounds measure novelty
                        # relative to discovered structures
                        self.novelty_measure.knowledge_base.add(cand.content)
                rnd.close()
                self._rounds.append(rnd)
        except Exception:
            self._status = EngineStatus.TERMINATED_ERROR
            return self._status
        self._status = EngineStatus.TERMINATED_HORIZON
        return self._status

    def accepted_structures(self) -> list[CandidateStructure]:
        """Return all accepted (novel + purposeful) structures."""
        return list(self._all_accepted)

    def success(self) -> bool:
        """Return True if the engine reached its novelty target."""
        return (
            self._status == EngineStatus.TERMINATED_SUCCESS
            or len(self._all_accepted) >= self.spec.min_novel_structures
        )

    def rounds(self) -> list[IdeationRound]:
        """Return all completed rounds."""
        return list(self._rounds)

    def summary(self) -> dict[str, Any]:
        total_generated = sum(r.acceptance_rate() > 0 or True for r in self._rounds)
        total_accepted = len(self._all_accepted)
        return {
            "name": self.name,
            "status": self._status.value,
            "n_rounds": len(self._rounds),
            "n_generated": sum(len(r.generated) for r in self._rounds),
            "n_accepted": total_accepted,
            "success": self.success(),
            "spec": self.spec.to_dict(),
            "novelty_measure": self.novelty_measure.to_dict(),
            "purpose_condition": self.purpose_condition.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "rounds": [r.to_dict() for r in self._rounds],
            "accepted_structures": [s.to_dict() for s in self._all_accepted],
        }


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_simple_generator(
    templates: list[tuple[StructureKind, str]],
    copilot_fraction: float = 0.3,
) -> Callable[[int, IdeationSpec], list[CandidateStructure]]:
    """Return a generator that cycles through a list of templates.

    Parameters
    ----------
    templates:
        List of (kind, content_template) pairs.
    copilot_fraction:
        Fraction of generated structures to tag as copilot-origin.
    """
    def _gen(round_idx: int, spec: IdeationSpec) -> list[CandidateStructure]:
        results = []
        for i, (kind, tmpl) in enumerate(templates):
            content = f"{tmpl} [round={round_idx}][idx={i}]"
            is_copilot = (i < len(templates) * copilot_fraction)
            results.append(CandidateStructure(
                struct_id=str(uuid.uuid4()),
                kind=kind,
                content=content,
                source="copilot" if is_copilot else "algorithmic",
            ))
        return results
    return _gen


def build_minimal_c4_instance(name: str = "C4_minimal") -> DiscoveryEngine:
    """Construct a minimal :class:`DiscoveryEngine` for C4 testing.

    Parameters
    ----------
    name:
        Name for the engine instance.

    Returns
    -------
    DiscoveryEngine
        Ready to call :meth:`~DiscoveryEngine.run` on.
    """
    kb = KnowledgeBase(name=f"{name}_kb")
    kb.add("every prime p > 2 is odd")
    kb.add("the sum of angles in a triangle is π")

    novelty = NoveltyMeasure(
        name=f"{name}_novelty",
        knowledge_base=kb,
        min_novelty_threshold=0.15,
        kind_weights={
            StructureKind.CONJECTURE: 1.5,
            StructureKind.LEMMA: 1.2,
            StructureKind.PROOF_STRATEGY: 1.0,
        },
    )

    purpose = PurposeCondition(name=f"{name}_purpose", require_all=False)
    purpose.add_goal(PurposeGoal(
        goal_id="G1",
        description="Advance understanding of prime distribution",
        required_kind=StructureKind.CONJECTURE,
        keyword_triggers=("prime", "distribution", "integer", "number"),
        strong_triggers=("prime", "number"),
    ))
    purpose.add_goal(PurposeGoal(
        goal_id="G2",
        description="Provide a usable proof strategy",
        required_kind=StructureKind.PROOF_STRATEGY,
        keyword_triggers=("induction", "contradiction", "construction", "proof"),
    ))

    spec = IdeationSpec(
        spec_id=str(uuid.uuid4()),
        task_description="Minimal C4 ideation test: discover conjectures about prime numbers",
        target_kinds=(StructureKind.CONJECTURE, StructureKind.PROOF_STRATEGY),
        horizon=10,
        min_novel_structures=2,
        allow_copilot_proposals=True,
        copilot_guidance_notes=(
            "Ideation task structure suggested with copilot assistance. "
            "Templates reviewed and trust promoted from COPILOT_SUGGESTED."
        ),
    )

    templates = [
        (StructureKind.CONJECTURE, "For every prime p, the gap to the next prime satisfies a bound"),
        (StructureKind.CONJECTURE, "The density of primes in arithmetic progressions is uniform"),
        (StructureKind.PROOF_STRATEGY, "Use induction on the prime factorisation to prove the claim"),
        (StructureKind.LEMMA, "A useful auxiliary lemma about integer divisibility"),
        (StructureKind.PROOF_STRATEGY, "Apply contradiction: assume no prime exists in the interval"),
    ]
    generator = _make_simple_generator(templates, copilot_fraction=0.4)

    return DiscoveryEngine(
        name=name,
        spec=spec,
        novelty_measure=novelty,
        purpose_condition=purpose,
        generator=generator,
    )
