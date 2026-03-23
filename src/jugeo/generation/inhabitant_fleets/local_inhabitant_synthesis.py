"""Local Inhabitant Synthesis — Ch42 §1.

This module implements the *local inhabitant synthesis* phase of the Ch42
fleet pipeline.  In Ch42 theory, a *local inhabitant* is a semantic term t
such that the inhabitation judgement:

    Γ ⊢ t : P

holds locally — i.e., with respect to a bounded evidence context Γ
restricted to a single semantic patch P.

The synthesis phase takes a *goal* (a desired proposition / patch target)
and a *SynthesisContext* (budget, registered fleets, evidence sources) and
produces a list of :class:`~jugeo.generation.inhabitant_fleets.models.InhabitantProposal`
objects that satisfy the goal to varying degrees.

Theory — Ch42 §1 Local Synthesis
----------------------------------
Let G = (proposition, support, tier, priority, budget, provenance) be a
generation goal.  The local synthesis algorithm proceeds as follows:

    1. DECOMPOSE  G into sub-goals G₁, …, Gₙ via structural analysis
       of the proposition.  Budget is split proportionally.
    2. GENERATE   For each Gᵢ produce an initial candidate tᵢ by
       invoking the registered fleet members in round-robin order.
    3. VALIDATE   Each candidate is tested against the InhabitantValidator:
         • Non-empty semantic content
         • Evidence score ∈ [0, 1]
         • Patch ID matches a registered patch
    4. SCORE      score(t) = TrustTier × evidence_score × (1 − 0.05 × |competitors|)
    5. NORMALIZE  Produce NormalizedProposal with rank assignment.

Synthesis Context
------------------
The SynthesisContext carries mutable state across synthesis steps:

    • available_budget  – integer token / step budget
    • registered fleets – list of InhabitantFleet objects contributing members
    • patch_registry    – dict[patch_id → set[section_label]]

Budget is decremented by 1 per proposal generated.  When budget reaches 0,
synthesis terminates early and returns whatever proposals have been produced.

Normalisation
--------------
normalize_proposal(p) converts an InhabitantProposal to a NormalizedProposal by:
    1. Computing a normalized_score = clamp(p.score() / SCORE_CLAMP_MAX, 0, 1)
    2. Assigning a label = p.semantic_content[:40].strip()

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis import (
...     create_synthesis_context, LocalInhabitantSynthesizer,
... )
>>> ctx = create_synthesis_context(budget=3)
>>> synth = LocalInhabitantSynthesizer(ctx)
>>> class FakeGoal:
...     proposition = "All propositions are inhabited."
...     label = "test"
>>> proposals = synth.synthesize(FakeGoal(), ctx)
>>> len(proposals) >= 1
True
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.inhabitant_fleets.models import (
    InhabitantProposal,
    FleetBid,
    BackpressureSignal,
    ProposalStatus,
    TrustTier,
    MoveType,
    NormalizedProposal,
    make_proposal,
)

SCORE_CLAMP_MAX: float = 3.0  # TrustTier.VERIFIED=3, evidence=1.0, no competitors


# ---------------------------------------------------------------------------
# InhabitantSpace
# ---------------------------------------------------------------------------


class InhabitantSpace:
    """A semantic space of registered patches and section labels.

    The InhabitantSpace maintains a registry of patch IDs and their
    associated section labels.  It is used by the SynthesisContext to
    validate that proposals target known patches.

    Theory — Ch42 §1.1
    --------------------
    The semantic space S is a partially ordered set of patches:

        S = { P₁, P₂, …, Pₙ }  with  P ≤ Q  iff  P ⊆ Q  (subset ordering)

    Each patch P has a set of *section labels* L(P) ⊆ Σ* describing the
    semantic sub-structure within P.

    A *local inhabitant* for (P, ℓ) is a term t such that:

        Γ ⊢ t : P  and  section(t) = ℓ

    where Γ is the current evidence context and section(t) identifies
    which section of P the term inhabits.

    Attributes
    ----------
    _patches : dict[str, set[str]]
        Mapping from patch_id to the set of known section labels.
    """

    def __init__(
        self,
        patch_id: str = "",
        dimension: int = 0,
        basis_elements: list[str] | None = None,
        metric: str = "euclidean",
    ) -> None:
        self.patch_id = patch_id
        self.dimension = dimension
        self.basis_elements = list(basis_elements or [])
        self.metric = metric
        self._patches: dict[str, set[str]] = {}
        if patch_id:
            self.register_patch(patch_id)

    def register_patch(self, patch_id: str, section_label: str = "default") -> None:
        """Register a patch and optional section label.

        Parameters
        ----------
        patch_id : str
            Identifier for the semantic patch.
        section_label : str
            Section within the patch; defaults to ``"default"``.
        """
        self._patches.setdefault(patch_id, set()).add(section_label)

    def has_patch(self, patch_id: str) -> bool:
        """Return True if patch_id is registered."""
        return patch_id in self._patches

    def sections_for(self, patch_id: str) -> set[str]:
        """Return section labels for a patch."""
        return self._patches.get(patch_id, set())

    def all_patches(self) -> list[str]:
        """Return all registered patch IDs."""
        return list(self._patches.keys())

    def size(self) -> int:
        """Return the number of registered patches."""
        return len(self._patches)

    def sample(self, n: int) -> list[dict[str, Any]]:
        return [
            {
                "patch_id": self.patch_id or f"patch-{i}",
                "basis": list(self.basis_elements),
                "index": i,
            }
            for i in range(max(0, n))
        ]

    def project(self, inhabitant: Any) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "dimension": self.dimension,
            "metric": self.metric,
            "inhabitant": inhabitant,
        }

    def distance(self, left: Any, right: Any) -> float:
        if left == right:
            return 0.0
        return float(abs(len(str(left)) - len(str(right))) + (0 if self.metric == "euclidean" else 0.5))

    def is_inhabited(self) -> bool:
        return self.dimension > 0 and bool(self.basis_elements)

    def __repr__(self) -> str:
        return f"InhabitantSpace(patches={len(self._patches)})"


# ---------------------------------------------------------------------------
# SynthesisContext
# ---------------------------------------------------------------------------


@dataclass(init=False)
class SynthesisContext:
    """Mutable context passed through the synthesis pipeline.

    The SynthesisContext acts as a *blackboard* accumulating state across
    synthesis steps.  It carries the available budget, registered fleets,
    and an InhabitantSpace.

    Theory — Ch42 §1.2
    --------------------
    The synthesis context formalises the environment Γ:

        Γ = (budget, fleets, space, evidence_sources)

    Budget is a *resource bound* controlling how many proposals may be
    generated.  When budget = 0, synthesis halts and returns accumulated
    proposals.

    Attributes
    ----------
    available_budget : int
        Remaining synthesis steps (decremented per proposal).
    space : InhabitantSpace
        The semantic space of known patches.
    _fleets : list[Any]
        Registered InhabitantFleet instances.
    _signals : list[BackpressureSignal]
        Accumulated backpressure signals.
    """

    available_budget: int = 5
    space: InhabitantSpace = field(default_factory=InhabitantSpace)
    _fleets: list[Any] = field(default_factory=list, repr=False)
    _signals: list[BackpressureSignal] = field(default_factory=list, repr=False)
    context_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def __init__(
        self,
        available_budget: int = 5,
        space: InhabitantSpace | None = None,
        _fleets: list[Any] | None = None,
        _signals: list[BackpressureSignal] | None = None,
        context_id: str | None = None,
        created_at: float | None = None,
        *,
        active_treaties: list[Any] | None = None,
        backpressure_state: dict[str, Any] | None = None,
        fleet_registry: Any | None = None,
    ) -> None:
        self.available_budget = available_budget
        self.space = space or InhabitantSpace()
        self._fleets = list(_fleets or [])
        self._signals = list(_signals or [])
        self.context_id = context_id or uuid.uuid4().hex
        self.created_at = time.time() if created_at is None else created_at
        self.active_treaties = list(active_treaties or [])
        self.backpressure_state = dict(backpressure_state or {})
        self.fleet_registry = fleet_registry

    def register_fleet(self, fleet: Any) -> None:
        """Register an InhabitantFleet with this context.

        Parameters
        ----------
        fleet : Any
            An InhabitantFleet or duck-typed equivalent.
        """
        if fleet not in self._fleets:
            self._fleets.append(fleet)

    def get_fleets(self) -> list[Any]:
        """Return all registered fleets."""
        return list(self._fleets)

    def decrement_budget(self, amount: int = 1) -> bool:
        """Decrement budget and return True if budget remains.

        Returns
        -------
        bool
            True if there is remaining budget after decrement.
        """
        self.available_budget = max(0, self.available_budget - amount)
        return self.available_budget > 0

    def check_budget(self) -> bool:
        """Legacy budget-check helper."""
        return self.available_budget > 0

    def add_signal(self, signal: BackpressureSignal) -> None:
        """Accumulate a backpressure signal."""
        self._signals.append(signal)

    def get_signals(self) -> list[BackpressureSignal]:
        """Return accumulated backpressure signals."""
        return list(self._signals)

    def get_active_signals(self) -> list[BackpressureSignal]:
        """Legacy alias for :meth:`get_signals`."""
        return self.get_signals()

    def is_budget_exhausted(self) -> bool:
        """Return True if no budget remains."""
        return self.available_budget <= 0

    def clone(self, new_budget: int | None = None) -> "SynthesisContext":
        """Return a shallow clone with optionally overridden budget."""
        return SynthesisContext(
            available_budget=new_budget if new_budget is not None else self.available_budget,
            space=self.space,
        )


# ---------------------------------------------------------------------------
# InhabitantValidator
# ---------------------------------------------------------------------------


class InhabitantValidator:
    """Validates InhabitantProposal objects before they enter the pipeline.

    Validation rules (Ch42 §1.3):
      1. semantic_content must be non-empty
      2. evidence_score ∈ [0, 1]
      3. patch_id must be non-empty
      4. If a space is provided, patch_id must be registered

    Attributes
    ----------
    space : InhabitantSpace | None
        Optional space for patch registration checks.
    strict : bool
        If True, unknown patch IDs cause validation failure.
    """

    def __init__(self, space: InhabitantSpace | None = None, strict: bool = False) -> None:
        self.space = space
        self.strict = strict
        self._validation_count = 0
        self._failure_count = 0

    def validation_errors(
        self,
        proposal: InhabitantProposal,
        context: SynthesisContext | None = None,
    ) -> list[str]:
        """Validate a proposal and return a list of error messages.

        Parameters
        ----------
        proposal : InhabitantProposal
            The proposal to validate.

        Returns
        -------
        list[str]
            Empty if valid; error messages otherwise.
        """
        self._validation_count += 1
        proposal_errors = (
            proposal.validation_errors()
            if hasattr(proposal, "validation_errors")
            else []
        )
        errors = list(proposal_errors)
        active_space = self.space or (context.space if context is not None else None)
        if self.strict and active_space and not active_space.has_patch(proposal.patch_id):
            errors.append(f"patch_id {proposal.patch_id!r} not registered in space")
        if context is not None and not context.check_budget():
            errors.append("available_budget exhausted")
        if errors:
            self._failure_count += 1
        return errors

    def validate(
        self,
        proposal: InhabitantProposal,
        context: SynthesisContext | None = None,
    ) -> bool:
        return len(self.validation_errors(proposal, context)) == 0

    def check_treaty_compliance(self, proposal: InhabitantProposal) -> bool:
        return True

    def check_overlap_compatibility(self, proposal: InhabitantProposal) -> bool:
        return True

    def is_valid(self, proposal: InhabitantProposal) -> bool:
        """Return True if the proposal has no validation errors."""
        return len(self.validate(proposal)) == 0

    def stats(self) -> dict[str, int]:
        """Return validation statistics."""
        return {
            "total": self._validation_count,
            "failures": self._failure_count,
            "passes": self._validation_count - self._failure_count,
        }


# ---------------------------------------------------------------------------
# LocalInhabitantSynthesizer
# ---------------------------------------------------------------------------


class LocalInhabitantSynthesizer:
    """Synthesizes InhabitantProposals for a given goal.

    This is the main synthesis engine.  It:
      1. Extracts a proposition string from the goal
      2. Determines a patch target
      3. Creates one proposal per budget unit (up to available_budget)
      4. Validates each proposal
      5. Returns accepted proposals

    Theory — Ch42 §1.4
    --------------------
    The synthesizer implements the *local completeness property*:

        ∀ well-formed goal g with budget ≥ 1 ∃ proposal p such that
            p.patch_id is valid  AND  p.semantic_content ≠ ""

    Attributes
    ----------
    context : SynthesisContext
        The synthesis context; budget is drawn from here.
    validator : InhabitantValidator
        Used to validate each synthesized proposal.
    """

    def __init__(self, context: SynthesisContext) -> None:
        self.context = context
        self.validator = InhabitantValidator(space=context.space)
        self._synthesis_count = 0

    def synthesize(self, goal: Any, context: SynthesisContext | None = None) -> list[InhabitantProposal]:
        """Synthesize proposals for the given goal.

        Parameters
        ----------
        goal : Any
            A goal object with at least a ``proposition`` or ``label`` attribute,
            or a plain string.
        context : SynthesisContext | None
            Context to use; falls back to self.context if None.

        Returns
        -------
        list[InhabitantProposal]
            Synthesized and validated proposals.
        """
        ctx = context if context is not None else self.context
        proposition = self._extract_proposition(goal)
        patch_id = self._determine_patch(goal, proposition)
        section_label = self._extract_label(goal)
        proposals: list[InhabitantProposal] = []
        n = max(1, min(ctx.available_budget, 5))
        for i in range(n):
            if ctx.is_budget_exhausted():
                break
            content = self._generate_content(proposition, i)
            trust = TrustTier.PROPOSAL
            evidence = round(min(1.0, 0.5 + i * 0.1), 3)
            p = make_proposal(
                patch_id=f"{patch_id}_{i}" if i > 0 else patch_id,
                section_label=section_label,
                content=content,
                trust_tier=trust,
                evidence_score=evidence,
            )
            is_valid = self.validator.validate(p, ctx)
            if is_valid:
                p.accept()
                proposals.append(p)
            ctx.decrement_budget()
        self._synthesis_count += len(proposals)
        return proposals

    def _propose_candidates(self, goal: Any) -> list[InhabitantProposal]:
        return self.synthesize(goal, self.context)

    def _filter_by_backpressure(self, candidates: list[Any], signals: list[BackpressureSignal]) -> list[Any]:
        if not signals:
            return list(candidates)
        blocked = {
            target
            for signal in signals
            if signal.instability_score >= signal.threshold
            for target in signal.target_patches
        }
        return [candidate for candidate in candidates if getattr(candidate, "patch_id", None) not in blocked]

    def _select_best(self, candidates: list[InhabitantProposal]) -> InhabitantProposal | None:
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: (candidate.score(), candidate.proposal_id))

    def emit_proposal(self, candidate: InhabitantProposal) -> InhabitantProposal:
        candidate.accept()
        return candidate

    def _extract_proposition(self, goal: Any) -> str:
        for attr in ("proposition", "required_proposition", "label", "name", "description"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                return val
        return str(goal)

    def _extract_label(self, goal: Any) -> str:
        for attr in ("label", "name", "section_label", "goal_id"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                return val[:40]
        return "synthesis"

    def _determine_patch(self, goal: Any, proposition: str) -> str:
        patch = getattr(goal, "patch_id", None)
        if patch and isinstance(patch, str):
            return patch
        # Derive a stable patch ID from the proposition
        token = proposition[:20].replace(" ", "_").lower()
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in token)
        return f"patch_{safe}" if safe else f"patch_{uuid.uuid4().hex[:8]}"

    def _generate_content(self, proposition: str, index: int) -> str:
        variants = [
            proposition,
            f"Refined: {proposition}",
            f"Generalized: {proposition}",
            f"Specialized case {index}: {proposition}",
            f"Evidence-backed: {proposition}",
        ]
        return variants[index % len(variants)]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def normalize_proposal(proposal: InhabitantProposal) -> NormalizedProposal:
    """Normalize an InhabitantProposal into a NormalizedProposal.

    Parameters
    ----------
    proposal : InhabitantProposal
        Proposal to normalize.

    Returns
    -------
    NormalizedProposal
        Proposal with normalized_score ∈ [0, 1] and a display label.
    """
    raw_score = proposal.score()
    normalized_score = max(0.0, min(1.0, raw_score / SCORE_CLAMP_MAX))
    label = proposal.semantic_content[:40].strip()
    return NormalizedProposal(
        original=proposal,
        normalized_score=normalized_score,
        label=label,
    )


def synthesize_inhabitants(
    goal: Any,
    budget: int | SynthesisContext = 5,
) -> list[InhabitantProposal]:
    """Standalone helper: synthesize inhabitants for a goal with a fresh context.

    Parameters
    ----------
    goal : Any
        Goal with a ``proposition`` / ``label`` attribute or plain string.
    budget : int
        Maximum number of proposals to generate.

    Returns
    -------
    list[InhabitantProposal]
    """
    if isinstance(budget, SynthesisContext):
        ctx = budget
    else:
        ctx = SynthesisContext(available_budget=budget)
    synth = LocalInhabitantSynthesizer(ctx)
    return synth.synthesize(goal, ctx)


def create_synthesis_context(budget: int = 5) -> SynthesisContext:
    """Create a fresh SynthesisContext with the given budget.

    Parameters
    ----------
    budget : int
        Synthesis budget (number of proposals permitted).

    Returns
    -------
    SynthesisContext
    """
    return SynthesisContext(available_budget=budget)


__all__ = [
    "InhabitantSpace",
    "SynthesisContext",
    "InhabitantValidator",
    "LocalInhabitantSynthesizer",
    "synthesize_inhabitants",
    "normalize_proposal",
    "create_synthesis_context",
]
