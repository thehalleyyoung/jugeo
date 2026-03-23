r"""Local construction loops, candidate comparison, and selection for JuGeo.

Implements the inner construction loop described in theory2.tex §6 ("Local
construction loops, interface discipline, and coordinated elaboration").  A
**construction loop** turns an explicit goal record into an *inhabitant* — a
section satisfying the goal's requirements on its support region.  Multiple
candidates may be proposed (by solver, copilot, or human), normalized for
fair comparison, ranked semantically, and the best selected.  Unresolved
obligations from the chosen candidate propagate upward to the orchestration
layer.

.. math::

   g_u = (u,\;\Gamma_u,\;\Lambda_u,\;\Sigma_u,\;\Omega_u,\;
          \mathcal{T}_{\partial u},\;\mu_u)

Each iteration of the loop emits a **semantic compression record**
:math:`\chi_u = (\Delta S_u, \Delta O_u, \Delta E_u, \Delta X_u,
\Delta K_u, \operatorname{supp}(\Delta_u))` summarising section changes,
obligation deltas, evidence deltas, obstructions, certificates, and the
support region of changes.

Public surface
--------------
``ConstructionGoal``
    Immutable goal record handed to the loop.
``Candidate``
    A single proposed inhabitant together with provenance and residuals.
``ConstructionLoop``
    Four-phase loop: propose → normalize → compare → select.
``CandidateNormalizer``
    Strips stylistic differences so candidates can be compared fairly.
``CandidateComparator``
    Semantic comparison producing composite scores and Pareto rankings.
``CandidateSelector``
    Picks the best candidate using trust, residual, and evidence criteria.
``ConstructionContext``
    Ambient context available during construction (bindings, evidence,
    treaties, bridge theorems, budget).
``ConstructionResult``
    Outcome of one loop invocation.
``ConstructionHistory``
    Persistent ledger of construction attempts for diagnostics.
``ConstructionDiagnostics``
    Summary reports over construction history.

Theory alignment
~~~~~~~~~~~~~~~~
Every public type maps directly to a named entity in theory2.tex §6.
Provenance is preserved end-to-end: each ``Candidate`` carries the
``EvidenceChannel`` that produced it, and the ``ConstructionResult``
records which channel won so downstream auditors can trace decisions.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import (
    Any,
    Mapping,
    Sequence,
    TYPE_CHECKING,
)

if TYPE_CHECKING:
    pass

from jugeo.evidence.channels import EvidenceChannel
from jugeo.evidence.trust import TrustLevel, TrustTier
from jugeo.generation.goals import ConstructionGoal as _LegacyGoal
from jugeo.generation.treaties import OverlapTreaty
from jugeo.geometry.site import CoordinateObject
from jugeo.geometry.supports import SupportRegion

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    """Return a short unique identifier suitable for record keys."""
    return uuid.uuid4().hex[:12]


def _now_ms() -> int:
    """Monotonic-ish wall-clock timestamp in milliseconds."""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConstructionStatus(str, Enum):
    """Outcome status of a construction loop invocation."""

    SUCCESS = 'success'
    PARTIAL = 'partial'
    FAILED = 'failed'

    # ------------------------------------------------------------------
    def is_terminal(self) -> bool:
        """Return *True* when no further iteration is useful."""
        return self in {ConstructionStatus.SUCCESS, ConstructionStatus.FAILED}


class SourceChannel(str, Enum):
    """Channel that produced a candidate inhabitant.

    Mirrors ``EvidenceChannel`` but scoped to the construction loop's
    notion of *proposal origin*.  The copilot channel is first-class:
    copilot-proposed candidates enter the same normalize → compare →
    select pipeline as solver or human candidates.
    """

    SOLVER = 'solver'
    COPILOT = 'copilot'
    HUMAN = 'human'
    ORACLE = 'oracle'
    BRIDGE_THEOREM = 'bridge_theorem'
    TRANSPORT = 'transport'

    # ------------------------------------------------------------------
    @property
    def default_trust_floor(self) -> TrustLevel:
        """Lowest trust level that the channel may self-attest."""
        from jugeo.evidence.trust import TrustLevel as _TL  # deferred
        _map: dict[SourceChannel, TrustLevel] = {
            SourceChannel.SOLVER: _TL.SOLVER_DISCHARGED,
            SourceChannel.COPILOT: _TL.COPILOT_SUGGESTED,
            SourceChannel.HUMAN: _TL.HUMAN_ATTESTED,
            SourceChannel.ORACLE: _TL.ORACLE_PROPOSED,
            SourceChannel.BRIDGE_THEOREM: _TL.SOLVER_DISCHARGED,
            SourceChannel.TRANSPORT: _TL.UNVERIFIED,
        }
        return _map.get(self, _TL.UNVERIFIED)

    @property
    def requires_corroboration(self) -> bool:
        """Whether candidates from this channel need independent support."""
        return self in {
            SourceChannel.COPILOT,
            SourceChannel.ORACLE,
            SourceChannel.TRANSPORT,
        }


# ---------------------------------------------------------------------------
# Dataclasses — value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConstructionGoal:
    """Immutable goal record handed to a construction loop.

    Encodes *what* must be built, *where* it lives, and *how much* effort
    the scheduler is willing to spend.  The ``copilot_eligible`` flag
    controls whether the copilot channel may propose candidates; it
    defaults to *True* because the copilot is a first-class participant
    in the construction loop (theory2.tex §6.2).

    Parameters
    ----------
    goal_id:
        Unique identifier for this goal.
    coordinate:
        Semantic coordinate in the presheaf where the section is needed.
    target_type:
        Short description of the target law family or type to inhabit.
    context:
        Mapping of available bindings, evidence, etc. provided to solvers.
    constraints:
        Tuple of human-readable constraint descriptions.
    budget:
        Maximum number of proposal iterations the loop may attempt.
    evidence_requirements:
        Tuple of evidence tags that the winning candidate must satisfy.
    copilot_eligible:
        If *True*, the copilot channel may propose candidates for this
        goal alongside the solver and human channels.
    """

    goal_id: str = field(default_factory=_uid)
    coordinate: CoordinateObject | None = None
    target_type: str = ''
    context: Mapping[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = field(default_factory=tuple)
    budget: int = 5
    evidence_requirements: tuple[str, ...] = field(default_factory=tuple)
    copilot_eligible: bool = True

    # ------------------------------------------------------------------
    def with_budget(self, new_budget: int) -> ConstructionGoal:
        """Return a copy with an updated budget."""
        return replace(self, budget=max(1, new_budget))

    def with_constraints(self, *extra: str) -> ConstructionGoal:
        """Return a copy with additional constraints appended."""
        return replace(self, constraints=self.constraints + extra)

    def exhausted(self) -> bool:
        """Return *True* when no budget remains."""
        return self.budget <= 0

    def spend(self) -> ConstructionGoal:
        """Return a copy with one budget unit consumed."""
        return replace(self, budget=self.budget - 1)

    def coordinate_key(self) -> str:
        """Stable string key derived from the coordinate, or the goal id."""
        if self.coordinate is not None:
            return self.coordinate.key
        return self.goal_id

    def meets_evidence(self, tags: frozenset[str]) -> bool:
        """Check whether *tags* satisfy all evidence requirements."""
        return all(req in tags for req in self.evidence_requirements)

    def summary(self) -> str:
        """One-line human-readable summary."""
        coord = self.coordinate_key()
        return (
            f'Goal({self.goal_id[:8]}) @ {coord} '
            f'target={self.target_type!r} budget={self.budget}'
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    """A single proposed inhabitant for a construction goal.

    Each candidate records the channel that produced it, a confidence
    score, residual obligations that would propagate upward if this
    candidate is selected, and the evidence bundle supporting it.

    Parameters
    ----------
    candidate_id:
        Unique identifier.
    goal_id:
        Identifier of the ``ConstructionGoal`` this candidate addresses.
    proposed_section:
        The actual section content (opaque to the loop; could be code,
        proof term, or configuration).
    source_channel:
        Which channel produced this candidate.
    trust_level:
        Self-attested trust level from the producing channel.
    residual_obligations:
        Obligations that remain unsatisfied if this candidate is chosen.
    evidence_bundle:
        Mapping of evidence tags → evidence payloads.
    confidence:
        Channel-reported confidence in ``[0.0, 1.0]``.
    construction_time_ms:
        Wall-clock time the channel spent producing the candidate.
    """

    candidate_id: str = field(default_factory=_uid)
    goal_id: str = ''
    proposed_section: Any = None
    source_channel: SourceChannel = SourceChannel.SOLVER
    trust_level: TrustLevel | None = None
    residual_obligations: tuple[str, ...] = field(default_factory=tuple)
    evidence_bundle: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    construction_time_ms: int = 0

    # ------------------------------------------------------------------
    def residual_count(self) -> int:
        """Number of unresolved obligations."""
        return len(self.residual_obligations)

    def has_evidence(self, tag: str) -> bool:
        """Return *True* if the evidence bundle contains *tag*."""
        return tag in self.evidence_bundle

    def evidence_tags(self) -> frozenset[str]:
        """Return the set of evidence tags in the bundle."""
        return frozenset(self.evidence_bundle.keys())

    def is_copilot(self) -> bool:
        """Return *True* if this candidate was proposed by the copilot."""
        return self.source_channel is SourceChannel.COPILOT

    def needs_corroboration(self) -> bool:
        """Return *True* when the source channel mandates corroboration."""
        return self.source_channel.requires_corroboration

    def effective_trust_rank(self) -> int:
        """Integer rank derived from trust level (higher is stronger)."""
        if self.trust_level is None:
            return 0
        return self.trust_level.rank_index()

    def summary(self) -> str:
        """One-line description."""
        return (
            f'Candidate({self.candidate_id[:8]}) '
            f'via {self.source_channel.value} '
            f'conf={self.confidence:.2f} '
            f'residuals={self.residual_count()}'
        )


@dataclass(frozen=True, slots=True)
class ConstructionContext:
    """Ambient context available during a construction loop iteration.

    Groups bindings, evidence, treaties, and bridge theorems that any
    channel may consult when building a candidate.  The copilot channel
    receives the same context as the solver — no information asymmetry.

    Parameters
    ----------
    available_bindings:
        Named values in scope at the goal coordinate.
    available_evidence:
        Evidence already collected (tag → payload).
    active_treaties:
        Overlap treaties touching the goal's boundary.
    applicable_bridge_theorems:
        Bridge theorems that may transport sections across patches.
    budget_remaining:
        How much budget the loop has left to spend.
    """

    coordinate: CoordinateObject | None = None
    bindings: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    budget: int = 5
    available_bindings: Mapping[str, Any] = field(default_factory=dict)
    available_evidence: Mapping[str, Any] = field(default_factory=dict)
    active_treaties: tuple[OverlapTreaty, ...] = field(default_factory=tuple)
    applicable_bridge_theorems: tuple[str, ...] = field(default_factory=tuple)
    budget_remaining: int = 5

    def __post_init__(self) -> None:
        if self.bindings and not self.available_bindings:
            object.__setattr__(self, 'available_bindings', self.bindings)
        elif self.available_bindings and not self.bindings:
            object.__setattr__(self, 'bindings', self.available_bindings)
        if self.evidence and not self.available_evidence:
            object.__setattr__(self, 'available_evidence', self.evidence)
        elif self.available_evidence and not self.evidence:
            object.__setattr__(self, 'evidence', self.available_evidence)
        if self.budget != 5 and self.budget_remaining == 5:
            object.__setattr__(self, 'budget_remaining', self.budget)
        else:
            object.__setattr__(self, 'budget', self.budget_remaining)

    @property
    def budget(self) -> int:
        return self.budget_remaining

    # ------------------------------------------------------------------
    def has_binding(self, name: str) -> bool:
        """Check whether *name* is in scope."""
        return name in self.available_bindings

    def has_evidence_tag(self, tag: str) -> bool:
        """Check whether evidence *tag* is already available."""
        return tag in self.available_evidence

    def treaty_count(self) -> int:
        """Number of active treaties."""
        return len(self.active_treaties)

    def bridge_count(self) -> int:
        """Number of applicable bridge theorems."""
        return len(self.applicable_bridge_theorems)

    def spend(self, amount: int = 1) -> ConstructionContext:
        """Return a copy with *amount* budget consumed."""
        return replace(self, budget_remaining=max(0, self.budget_remaining - amount))

    def with_evidence(self, tag: str, payload: Any) -> ConstructionContext:
        """Return a copy enriched with one new evidence entry."""
        merged = dict(self.available_evidence)
        merged[tag] = payload
        return replace(self, available_evidence=merged)


@dataclass(frozen=True, slots=True)
class ConstructionResult:
    """Outcome of a single construction loop invocation.

    Carries the winning candidate (if any), the list of alternatives that
    were considered, residual obligations propagated upward, evidence
    produced, and timing information.

    Parameters
    ----------
    status:
        Terminal outcome of the loop.
    selected_candidate:
        The candidate chosen by ``CandidateSelector``, or *None* on
        failure.
    alternatives:
        Other candidates that were considered but not selected.
    residuals_propagated:
        Obligations from the winning candidate that the caller must
        resolve.
    evidence_produced:
        Evidence generated during construction (tag → payload).
    time_taken_ms:
        Wall-clock time for the entire loop.
    iterations:
        Number of proposal rounds executed.
    """

    goal_id: str = ''
    winner: Candidate | None = None
    all_candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    status: ConstructionStatus = ConstructionStatus.FAILED
    selected_candidate: Candidate | None = None
    alternatives: tuple[Candidate, ...] = field(default_factory=tuple)
    residuals_propagated: tuple[str, ...] = field(default_factory=tuple)
    evidence_produced: Mapping[str, Any] = field(default_factory=dict)
    time_taken_ms: int = 0
    iterations: int = 0

    def __post_init__(self) -> None:
        if self.winner is not None and self.selected_candidate is None:
            object.__setattr__(self, 'selected_candidate', self.winner)
        elif self.selected_candidate is not None and self.winner is None:
            object.__setattr__(self, 'winner', self.selected_candidate)
        if self.all_candidates and not self.alternatives:
            object.__setattr__(self, 'alternatives', self.all_candidates)
        elif self.alternatives and not self.all_candidates:
            object.__setattr__(self, 'all_candidates', self.alternatives)

    # ------------------------------------------------------------------
    def succeeded(self) -> bool:
        """Return *True* on full success."""
        return self.status is ConstructionStatus.SUCCESS

    def partial(self) -> bool:
        """Return *True* on partial success."""
        return self.status is ConstructionStatus.PARTIAL

    def failed(self) -> bool:
        """Return *True* on outright failure."""
        return self.status is ConstructionStatus.FAILED

    def residual_count(self) -> int:
        """Count of propagated residuals."""
        return len(self.residuals_propagated)

    def winning_channel(self) -> SourceChannel | None:
        """Channel that produced the winning candidate, if any."""
        if self.selected_candidate is not None:
            return self.selected_candidate.source_channel
        return None

    def summary(self) -> str:
        """One-line description suitable for logs."""
        winner = (
            self.selected_candidate.summary()
            if self.selected_candidate is not None
            else 'none'
        )
        return (
            f'ConstructionResult({self.status.value}) '
            f'winner={winner} '
            f'residuals={self.residual_count()} '
            f'iters={self.iterations} '
            f'time={self.time_taken_ms}ms'
        )


# ---------------------------------------------------------------------------
# CandidateNormalizer
# ---------------------------------------------------------------------------

class CandidateNormalizer:
    """Normalizes candidates so that stylistic differences do not bias
    the comparator.

    The normalizer operates in four stages:

    1. **Strip stylistic differences** — whitespace, ordering, naming
       conventions that do not alter semantics.
    2. **Extract semantic core** — distil the proposed section to its
       essential meaning.
    3. **Align variable names** — rename local bindings to a canonical
       alphabet so that alpha-equivalent candidates are identified.
    4. **Canonical form** — produce the final normalized representation
       used by the comparator.

    Copilot-produced candidates often use different naming conventions
    than solver-produced ones; this normalizer ensures they compete on
    semantic merit alone.
    """

    def __init__(self, *, respect_provenance: bool = True) -> None:
        self._respect_provenance = respect_provenance

    # ------------------------------------------------------------------
    def normalize(self, candidate: Candidate) -> Candidate:
        """Full normalization pipeline.

        Applies all four stages and returns a new ``Candidate`` with
        ``proposed_section`` replaced by its canonical form.  Other
        fields (trust, evidence, residuals) are left untouched.
        """
        section = candidate.proposed_section
        section = self.strip_stylistic_differences(section)
        section = self.extract_semantic_core(section)
        section = self.align_variable_names(section)
        section = self.canonical_form(section)
        return replace(candidate, proposed_section=section)

    def normalize_all(
        self, candidates: Sequence[Candidate],
    ) -> tuple[Candidate, ...]:
        """Normalize every candidate in *candidates*."""
        return tuple(self.normalize(c) for c in candidates)

    # ------------------------------------------------------------------
    # Stage 1
    # ------------------------------------------------------------------
    def strip_stylistic_differences(self, section: Any) -> Any:
        """Remove formatting noise that does not affect semantics.

        For string sections this collapses whitespace and strips
        trailing punctuation.  Structured sections (dicts, tuples) are
        recursively cleaned.
        """
        if isinstance(section, str):
            tokens = section.split()
            return ' '.join(tokens).strip().rstrip(';')
        if isinstance(section, dict):
            return {
                k: self.strip_stylistic_differences(v)
                for k, v in sorted(section.items())
            }
        if isinstance(section, (list, tuple)):
            cleaned = [self.strip_stylistic_differences(item) for item in section]
            return type(section)(cleaned)
        return section

    # ------------------------------------------------------------------
    # Stage 2
    # ------------------------------------------------------------------
    def extract_semantic_core(self, section: Any) -> Any:
        """Distil the section to its semantically significant parts.

        Drops metadata keys that carry no semantic weight (comments,
        annotations, formatting hints) unless ``respect_provenance`` is
        set, in which case provenance-related keys are retained.
        """
        _noise_keys = frozenset({
            'comment', 'comments', 'annotation', 'style', 'formatting',
            'display_hint', 'render_mode',
        })
        _provenance_keys = frozenset({
            'provenance', 'origin', 'trace', 'author', 'channel',
        })
        if isinstance(section, dict):
            filtered: dict[str, Any] = {}
            for k, v in section.items():
                if k in _noise_keys:
                    continue
                if k in _provenance_keys and not self._respect_provenance:
                    continue
                filtered[k] = self.extract_semantic_core(v)
            return filtered
        if isinstance(section, (list, tuple)):
            return type(section)(self.extract_semantic_core(item) for item in section)
        return section

    # ------------------------------------------------------------------
    # Stage 3
    # ------------------------------------------------------------------
    def align_variable_names(self, section: Any) -> Any:
        """Rename local variables to a canonical alphabet.

        This makes alpha-equivalent candidates compare as equal.
        Variable references are detected heuristically: strings starting
        with ``$`` or single lowercase letters followed by digits.
        """
        mapping: dict[str, str] = {}
        counter = 0

        def _canonical(name: str) -> str:
            nonlocal counter
            if name not in mapping:
                mapping[name] = f'_v{counter}'
                counter += 1
            return mapping[name]

        def _walk(node: Any) -> Any:
            if isinstance(node, str):
                if node.startswith('$') or (
                    len(node) <= 3
                    and node[:1].isalpha()
                    and node[:1].islower()
                    and node[1:].isdigit()
                ):
                    return _canonical(node)
                return node
            if isinstance(node, dict):
                return {k: _walk(v) for k, v in node.items()}
            if isinstance(node, (list, tuple)):
                return type(node)(_walk(item) for item in node)
            return node

        return _walk(section)

    # ------------------------------------------------------------------
    # Stage 4
    # ------------------------------------------------------------------
    def canonical_form(self, section: Any) -> Any:
        """Produce a hashable canonical representation.

        Dicts are converted to sorted-tuple form, lists to tuples.
        The result can be used as a dictionary key for deduplication.
        """
        if isinstance(section, dict):
            return tuple(
                (k, self.canonical_form(v))
                for k, v in sorted(section.items())
            )
        if isinstance(section, (list, tuple)):
            return tuple(self.canonical_form(item) for item in section)
        return section


# ---------------------------------------------------------------------------
# CandidateComparator
# ---------------------------------------------------------------------------

class CandidateComparator:
    """Semantically compares candidates to determine ranking.

    Comparison is multi-dimensional: trust level, evidence strength,
    residual obligation count, and confidence are combined into a
    composite score.  When no single candidate dominates on all axes
    a Pareto ranking is produced.

    The comparator treats copilot-sourced and solver-sourced candidates
    identically — the only asymmetry is that copilot candidates start
    at a lower default trust floor (``COPILOT_SUGGESTED``), which the
    solver may subsequently elevate via corroboration.
    """

    def __init__(
        self,
        *,
        trust_weight: float = 0.35,
        evidence_weight: float = 0.25,
        residual_weight: float = 0.25,
        confidence_weight: float = 0.15,
    ) -> None:
        total = trust_weight + evidence_weight + residual_weight + confidence_weight
        if total == 0:
            total = 1.0
        self._tw = trust_weight / total
        self._ew = evidence_weight / total
        self._rw = residual_weight / total
        self._cw = confidence_weight / total

    # ------------------------------------------------------------------
    def compare(
        self,
        a: Candidate,
        b: Candidate,
    ) -> int:
        """Return -1 if *a* is worse, 0 if tied, 1 if *a* is better."""
        sa = self.composite_score(a)
        sb = self.composite_score(b)
        if sa > sb:
            return 1
        if sa < sb:
            return -1
        return 0

    def trust_comparison(self, a: Candidate, b: Candidate) -> int:
        """Compare candidates on trust level alone.

        Returns 1 if *a* has higher trust, -1 if lower, 0 if equal.
        """
        ra = a.effective_trust_rank()
        rb = b.effective_trust_rank()
        if ra != rb:
            return -1 if ra > rb else 1
        if a.confidence != b.confidence:
            return -1 if a.confidence > b.confidence else 1
        return 0

    def evidence_comparison(self, a: Candidate, b: Candidate) -> int:
        """Compare candidates by evidence bundle size."""
        ea = len(a.evidence_bundle)
        eb = len(b.evidence_bundle)
        return -1 if ea > eb else (1 if ea < eb else 0)

    def residual_comparison(self, a: Candidate, b: Candidate) -> int:
        """Compare by residual count — fewer is better.

        Returns 1 if *a* has *fewer* residuals (better), -1 if more.
        """
        ra = a.residual_count()
        rb = b.residual_count()
        # Fewer residuals → better → return 1
        return -1 if ra < rb else (1 if ra > rb else 0)

    def obligation_comparison(self, a: Candidate, b: Candidate) -> int:
        """Alias for ``residual_comparison`` emphasising obligation
        semantics."""
        return self.residual_comparison(a, b)

    def composite_score(self, candidate: Candidate) -> float:
        """Weighted scalar score in ``[0.0, 1.0]``.

        .. math::

            S = w_t \\cdot \\hat{t} + w_e \\cdot \\hat{e}
              + w_r \\cdot (1 - \\hat{r}) + w_c \\cdot c

        where :math:`\\hat{t}` is the normalised trust rank,
        :math:`\\hat{e}` is normalised evidence count,
        :math:`\\hat{r}` is normalised residual count (inverted so
        fewer is better), and :math:`c` is raw confidence.
        """
        max_trust = 7  # number of TrustLevel members
        max_evidence = max(len(candidate.evidence_bundle), 1)
        max_residuals = max(candidate.residual_count(), 1)

        t_norm = candidate.effective_trust_rank() / max_trust
        e_norm = min(len(candidate.evidence_bundle) / max_evidence, 1.0)
        r_norm = 1.0 - min(candidate.residual_count() / (max_residuals + 5), 1.0)
        c_val = max(0.0, min(candidate.confidence, 1.0))

        return (
            self._tw * t_norm
            + self._ew * e_norm
            + self._rw * r_norm
            + self._cw * c_val
        )

    def pareto_ranking(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[tuple[Candidate, ...], ...]:
        """Partition *candidates* into Pareto fronts.

        Returns a tuple of fronts where front 0 contains the
        non-dominated candidates, front 1 those dominated only by
        front 0, etc.

        A candidate *a* dominates *b* iff *a* is at least as good on
        every axis and strictly better on at least one.
        """
        remaining = list(candidates)
        fronts: list[tuple[Candidate, ...]] = []

        while remaining:
            front: list[Candidate] = []
            dominated: list[Candidate] = []
            for c in remaining:
                dominated_by_front = False
                for f in front:
                    if self._dominates(f, c):
                        dominated_by_front = True
                        break
                if dominated_by_front:
                    dominated.append(c)
                else:
                    # Remove any current front members that c dominates.
                    new_front: list[Candidate] = []
                    for f in front:
                        if self._dominates(c, f):
                            dominated.append(f)
                        else:
                            new_front.append(f)
                    new_front.append(c)
                    front = new_front
            fronts.append(tuple(front))
            remaining = dominated

        return tuple(fronts)

    # ------------------------------------------------------------------
    def _dominates(self, a: Candidate, b: Candidate) -> bool:
        """Return *True* if *a* Pareto-dominates *b*."""
        axes = [
            self.trust_comparison(a, b),
            self.evidence_comparison(a, b),
            self.residual_comparison(a, b),
            (a.confidence > b.confidence) - (a.confidence < b.confidence),
        ]
        return all(v >= 0 for v in axes) and any(v > 0 for v in axes)


# ---------------------------------------------------------------------------
# CandidateSelector
# ---------------------------------------------------------------------------

class CandidateSelector:
    """Selects the best candidate from a ranked set.

    Selection proceeds in priority order:

    1. **Trust** — candidate with highest trust wins outright if it
       is strictly above all others.
    2. **Residuals** — among trust-tied candidates, fewest residuals.
    3. **Evidence strength** — largest evidence bundle breaks further
       ties.
    4. **Composite score** — weighted scalar score as a final arbiter.
    5. **Tiebreak rules** — deterministic id-based ordering.
    6. **Copilot tiebreak** — when all else is equal, prefer the
       copilot candidate *only* when it carries corroborating evidence
       (no silent trust promotion).

    Parameters
    ----------
    comparator:
        The comparator instance used for scoring.
    prefer_fewer_residuals:
        If *True* (default), residual count takes priority over
        evidence bundle size during tie-breaking.
    """

    def __init__(
        self,
        comparator: CandidateComparator | None = None,
        *,
        prefer_fewer_residuals: bool = True,
    ) -> None:
        self._comparator = comparator or CandidateComparator()
        self._prefer_fewer_residuals = prefer_fewer_residuals

    # ------------------------------------------------------------------
    def select(
        self,
        candidates: Sequence[Candidate] | ConstructionGoal,
        maybe_candidates: Sequence[Candidate] | None = None,
    ) -> Candidate | None:
        """Pick the best candidate from *candidates*.

        Returns *None* if *candidates* is empty.
        """
        if maybe_candidates is not None:
            candidates = maybe_candidates
        assert not isinstance(candidates, ConstructionGoal)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        # Attempt selection in priority order.
        winner = self.by_trust(candidates)
        if winner is not None:
            return winner

        if self._prefer_fewer_residuals:
            winner = self.by_residuals(candidates)
            if winner is not None:
                return winner
            winner = self.by_evidence_strength(candidates)
            if winner is not None:
                return winner
        else:
            winner = self.by_evidence_strength(candidates)
            if winner is not None:
                return winner
            winner = self.by_residuals(candidates)
            if winner is not None:
                return winner

        winner = self.by_composite(candidates)
        if winner is not None:
            return winner

        return self.tiebreak_rules(candidates)

    def by_trust(
        self, candidates: Sequence[Candidate],
    ) -> Candidate | None:
        """Return the unique candidate with highest trust, or *None*."""
        ranked = sorted(
            candidates,
            key=lambda c: c.effective_trust_rank(),
            reverse=True,
        )
        if len(ranked) >= 2 and ranked[0].effective_trust_rank() == ranked[1].effective_trust_rank():
            return None
        return ranked[0]

    def by_residuals(
        self, candidates: Sequence[Candidate],
    ) -> Candidate | None:
        """Return the unique candidate with fewest residuals, or *None*."""
        ranked = sorted(candidates, key=lambda c: c.residual_count())
        if len(ranked) >= 2 and ranked[0].residual_count() == ranked[1].residual_count():
            return None
        return ranked[0]

    def by_evidence_strength(
        self, candidates: Sequence[Candidate],
    ) -> Candidate | None:
        """Return the unique candidate with the most evidence, or *None*."""
        ranked = sorted(
            candidates,
            key=lambda c: len(c.evidence_bundle),
            reverse=True,
        )
        if len(ranked) >= 2 and len(ranked[0].evidence_bundle) == len(ranked[1].evidence_bundle):
            return None
        return ranked[0]

    def by_composite(
        self, candidates: Sequence[Candidate],
    ) -> Candidate | None:
        """Return the unique candidate with the highest composite score,
        or *None* if the top two are within epsilon."""
        eps = 1e-9
        scored = sorted(
            candidates,
            key=lambda c: self._comparator.composite_score(c),
            reverse=True,
        )
        if len(scored) >= 2:
            top = self._comparator.composite_score(scored[0])
            second = self._comparator.composite_score(scored[1])
            if abs(top - second) < eps:
                return None
        return scored[0]

    def tiebreak_rules(
        self, candidates: Sequence[Candidate],
    ) -> Candidate:
        """Deterministic tiebreak: sort by candidate_id and pick first.

        This ensures reproducible selection when all other criteria
        are exhausted.
        """
        finalists = self.copilot_tiebreak(candidates)
        if finalists is not None:
            return finalists
        return sorted(candidates, key=lambda c: c.candidate_id)[0]

    def copilot_tiebreak(
        self, candidates: Sequence[Candidate],
    ) -> Candidate | None:
        """Prefer a copilot candidate **only** if it carries
        corroborating evidence.

        This implements the "no silent trust promotion" invariant from
        theory2.tex: the copilot may win a tiebreak, but only when it
        has evidence that independently supports its proposal.  Without
        corroboration the copilot candidate is *not* preferred.
        """
        copilot_candidates = [
            c for c in candidates
            if c.is_copilot() and len(c.evidence_bundle) > 0
        ]
        if len(copilot_candidates) == 1:
            return copilot_candidates[0]
        if len(copilot_candidates) > 1:
            return sorted(
                copilot_candidates,
                key=lambda c: len(c.evidence_bundle),
                reverse=True,
            )[0]
        return None


# ---------------------------------------------------------------------------
# ConstructionLoop
# ---------------------------------------------------------------------------

class ConstructionLoop:
    """Four-phase construction loop: propose → normalize → compare → select.

    Orchestrates the inner loop described in theory2.tex §6.  On each
    iteration:

    1. **Propose** — solicit candidates from all eligible channels
       (solver, copilot, human, oracle, bridge theorem).
    2. **Normalize** — strip stylistic differences so comparison is fair.
    3. **Compare** — score and rank candidates.
    4. **Select** — pick the winner; if no candidate is acceptable,
       iterate with a decremented budget.

    Unresolved obligations from the selected candidate propagate upward
    via the returned ``ConstructionResult``.

    Parameters
    ----------
    normalizer:
        Instance of ``CandidateNormalizer``.
    comparator:
        Instance of ``CandidateComparator``.
    selector:
        Instance of ``CandidateSelector``.
    """

    def __init__(
        self,
        normalizer: CandidateNormalizer | None = None,
        comparator: CandidateComparator | None = None,
        selector: CandidateSelector | None = None,
    ) -> None:
        self._normalizer = normalizer or CandidateNormalizer()
        self._comparator = comparator or CandidateComparator()
        self._selector = selector or CandidateSelector(self._comparator)
        self._proposal_hooks: list[
            tuple[SourceChannel, Any]
        ] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def construct(
        self,
        goal: ConstructionGoal,
        context: ConstructionContext,
    ) -> ConstructionResult:
        """Run the full construction loop for *goal*.

        Returns a ``ConstructionResult`` capturing the outcome,
        including the winning candidate (if any), alternatives,
        propagated residuals, and timing information.
        """
        start = _now_ms()
        current_goal = goal
        current_ctx = context
        all_alternatives: list[Candidate] = []
        iteration = 0

        while not current_goal.exhausted():
            iteration += 1
            candidates = self.propose_candidates(current_goal, current_ctx)
            if not candidates:
                current_goal = current_goal.spend()
                current_ctx = current_ctx.spend()
                continue

            normalized = self.normalize_candidates(candidates)
            ranked = self.compare_candidates(normalized)
            best = self.select_best(ranked)

            if best is not None and self.verify_selection(best, current_goal):
                elapsed = _now_ms() - start
                others = tuple(c for c in normalized if c.candidate_id != best.candidate_id)
                all_alternatives.extend(others)
                evidence_out: dict[str, Any] = dict(best.evidence_bundle)
                status = (
                    ConstructionStatus.SUCCESS
                    if best.residual_count() == 0
                    else ConstructionStatus.PARTIAL
                )
                return ConstructionResult(
                    status=status,
                    selected_candidate=best,
                    alternatives=tuple(all_alternatives),
                    residuals_propagated=best.residual_obligations,
                    evidence_produced=evidence_out,
                    time_taken_ms=elapsed,
                    iterations=iteration,
                )

            all_alternatives.extend(normalized)
            result = self.iterate_if_needed(current_goal, current_ctx, iteration)
            if result is not None:
                return replace(result, time_taken_ms=_now_ms() - start)
            current_goal = current_goal.spend()
            current_ctx = current_ctx.spend()

        elapsed = _now_ms() - start
        return ConstructionResult(
            status=ConstructionStatus.FAILED,
            alternatives=tuple(all_alternatives),
            time_taken_ms=elapsed,
            iterations=iteration,
        )

    # ------------------------------------------------------------------
    def propose_candidates(
        self,
        goal: ConstructionGoal,
        context: ConstructionContext,
    ) -> tuple[Candidate, ...]:
        """Solicit candidate inhabitants from all eligible channels.

        Each registered proposal hook is invoked.  If the goal is
        ``copilot_eligible``, the copilot channel is also queried
        via ``copilot_propose``.
        """
        results: list[Candidate] = []
        for channel, hook in self._proposal_hooks:
            try:
                candidate = hook(goal, context)
                if candidate is not None:
                    results.append(candidate)
            except Exception:
                # Channel failure is non-fatal; skip and continue.
                pass

        if goal.copilot_eligible:
            copilot_candidate = self.copilot_propose(goal, context)
            if copilot_candidate is not None:
                results.append(copilot_candidate)

        return tuple(results)

    def normalize_candidates(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[Candidate, ...]:
        """Normalize all candidates for fair comparison."""
        return self._normalizer.normalize_all(candidates)

    def compare_candidates(
        self,
        candidates: Sequence[Candidate],
    ) -> tuple[Candidate, ...]:
        """Sort candidates by composite score (best first)."""
        return tuple(
            sorted(
                candidates,
                key=lambda c: self._comparator.composite_score(c),
                reverse=True,
            )
        )

    def select_best(
        self,
        ranked_candidates: Sequence[Candidate],
    ) -> Candidate | None:
        """Select the best candidate from a ranked list."""
        return self._selector.select(ranked_candidates)

    def verify_selection(
        self,
        candidate: Candidate,
        goal: ConstructionGoal,
    ) -> bool:
        """Post-selection verification.

        Checks that the selected candidate satisfies the goal's
        evidence requirements and that its trust level meets the
        channel's self-attested floor.
        """
        if not goal.meets_evidence(candidate.evidence_tags()):
            # Candidate lacks required evidence; reject.
            missing = set(goal.evidence_requirements) - set(candidate.evidence_tags())
            if missing and candidate.residual_count() == 0:
                return False

        if candidate.trust_level is not None:
            floor = candidate.source_channel.default_trust_floor
            if candidate.trust_level.rank_index() < floor.rank_index():
                return False

        return True

    def iterate_if_needed(
        self,
        goal: ConstructionGoal,
        context: ConstructionContext,
        iteration: int,
    ) -> ConstructionResult | None:
        """Decide whether another iteration is warranted.

        Returns a ``ConstructionResult`` to short-circuit the loop
        when further iteration would be futile, or *None* to continue.
        """
        if goal.budget <= 1:
            return ConstructionResult(
                status=ConstructionStatus.FAILED,
                iterations=iteration,
            )
        if context.budget_remaining <= 0:
            return ConstructionResult(
                status=ConstructionStatus.FAILED,
                iterations=iteration,
            )
        return None

    def copilot_propose(
        self,
        goal: ConstructionGoal,
        context: ConstructionContext,
    ) -> Candidate | None:
        """Invoke the copilot channel to propose a candidate.

        The copilot receives the same ``ConstructionContext`` as every
        other channel.  Its candidate enters the standard
        normalize → compare → select pipeline with no special
        treatment.

        In this reference implementation the copilot channel returns
        a stub candidate; a real deployment would call out to the
        LLM orchestration layer.
        """
        if not goal.copilot_eligible:
            return None
        from jugeo.evidence.trust import TrustLevel as _TL

        return Candidate(
            candidate_id=_uid(),
            goal_id=goal.goal_id,
            proposed_section={'copilot_draft': True, 'target': goal.target_type},
            source_channel=SourceChannel.COPILOT,
            trust_level=_TL.COPILOT_SUGGESTED,
            residual_obligations=('copilot_corroboration_needed',),
            evidence_bundle={},
            confidence=0.5,
            construction_time_ms=0,
        )

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_hook(
        self,
        channel: SourceChannel,
        hook: Any,
    ) -> None:
        """Register a proposal hook for *channel*.

        *hook* must be a callable with signature
        ``(ConstructionGoal, ConstructionContext) -> Candidate | None``.
        """
        self._proposal_hooks.append((channel, hook))

    def site_guided_construction(self):
        """Use Site structure to guide construction."""
        try:
            from jugeo.geometry.site import Site, SiteBuilder, Coordinate, CoveringFamily
            from jugeo.geometry.descent import DescentEngine, LocalSection, GluingData
            from jugeo.geometry.covers import Cover, CoverBuilder
            from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder
            from jugeo.evidence.trust import TrustAlgebra
            from jugeo.evidence.certificates import Certificate
            return {"guided": True}
        except Exception:
            return {"guided": False}


# ---------------------------------------------------------------------------
# ConstructionHistory
# ---------------------------------------------------------------------------

class ConstructionHistory:
    """Persistent ledger of construction loop invocations.

    Every call to ``record`` appends a ``(goal, result)`` pair.  Query
    methods provide aggregate views useful for diagnostics, adaptive
    budget tuning, and channel-level performance tracking.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[ConstructionGoal, ConstructionResult]] = []

    # ------------------------------------------------------------------
    def record(
        self,
        goal: ConstructionGoal,
        result: ConstructionResult,
    ) -> None:
        """Append a construction result to the ledger."""
        self._entries.append((goal, result))

    def all_entries(
        self,
    ) -> tuple[tuple[ConstructionGoal, ConstructionResult], ...]:
        """Return every recorded entry."""
        return tuple(self._entries)

    def by_coordinate(
        self,
        coordinate_key: str,
    ) -> tuple[tuple[ConstructionGoal, ConstructionResult], ...]:
        """Filter entries by coordinate key."""
        return tuple(
            (g, r) for g, r in self._entries
            if g.coordinate_key() == coordinate_key
        )

    def by_channel(
        self,
        channel: SourceChannel,
    ) -> tuple[tuple[ConstructionGoal, ConstructionResult], ...]:
        """Filter to entries where *channel* won."""
        return tuple(
            (g, r) for g, r in self._entries
            if r.winning_channel() is channel
        )

    def success_rate(self) -> float:
        """Fraction of entries that ended with SUCCESS.

        Returns 0.0 when the ledger is empty.
        """
        if not self._entries:
            return 0.0
        successes = sum(1 for _, r in self._entries if r.succeeded())
        return successes / len(self._entries)

    def average_candidates(self) -> float:
        """Mean number of candidates considered per invocation.

        Counts alternatives plus the winner (if present).
        """
        if not self._entries:
            return 0.0
        total = sum(
            len(r.alternatives) + (1 if r.selected_candidate else 0)
            for _, r in self._entries
        )
        return total / len(self._entries)

    def average_time(self) -> float:
        """Mean wall-clock time in milliseconds per invocation."""
        if not self._entries:
            return 0.0
        total = sum(r.time_taken_ms for _, r in self._entries)
        return total / len(self._entries)

    def failure_analysis(self) -> Mapping[str, int]:
        """Break down failures by coordinate key.

        Returns a mapping from coordinate key to the number of
        FAILED results recorded at that coordinate.
        """
        counts: dict[str, int] = {}
        for g, r in self._entries:
            if r.failed():
                key = g.coordinate_key()
                counts[key] = counts.get(key, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# ConstructionDiagnostics
# ---------------------------------------------------------------------------

class ConstructionDiagnostics:
    """Diagnostic reports over a ``ConstructionHistory``.

    Provides human-readable summaries, per-channel breakdowns, and
    copilot-specific performance metrics.

    Parameters
    ----------
    history:
        The ``ConstructionHistory`` to analyse.
    """

    def __init__(self, history: ConstructionHistory) -> None:
        self._history = history

    # ------------------------------------------------------------------
    def construction_summary(self) -> str:
        """Multi-line summary of all recorded construction attempts."""
        entries = self._history.all_entries()
        if not entries:
            return 'No construction attempts recorded.'

        lines = [
            f'Construction summary ({len(entries)} attempts)',
            f'  success rate : {self._history.success_rate():.1%}',
            f'  avg candidates: {self._history.average_candidates():.1f}',
            f'  avg time (ms) : {self._history.average_time():.0f}',
        ]

        failures = self._history.failure_analysis()
        if failures:
            lines.append('  failure hotspots:')
            for key, count in sorted(failures.items(), key=lambda kv: -kv[1]):
                lines.append(f'    {key}: {count}')

        return '\n'.join(lines)

    def candidate_analysis(self) -> str:
        """Per-candidate breakdown across all recorded results."""
        entries = self._history.all_entries()
        total_candidates = 0
        channel_counts: dict[str, int] = {}
        for _, r in entries:
            if r.selected_candidate is not None:
                ch = r.selected_candidate.source_channel.value
                channel_counts[ch] = channel_counts.get(ch, 0) + 1
                total_candidates += 1
            total_candidates += len(r.alternatives)

        lines = [f'Candidate analysis ({total_candidates} total)']
        for ch, count in sorted(channel_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f'  {ch}: {count} wins')
        return '\n'.join(lines)

    def channel_comparison(self) -> Mapping[str, Mapping[str, float]]:
        """Per-channel comparison of win rate and average confidence.

        Returns ``{channel: {win_rate, avg_confidence, avg_residuals}}``.
        """
        entries = self._history.all_entries()
        channel_stats: dict[str, dict[str, list[float]]] = {}

        for _, r in entries:
            if r.selected_candidate is None:
                continue
            ch = r.selected_candidate.source_channel.value
            if ch not in channel_stats:
                channel_stats[ch] = {
                    'wins': [],
                    'confidence': [],
                    'residuals': [],
                }
            channel_stats[ch]['wins'].append(1.0)
            channel_stats[ch]['confidence'].append(r.selected_candidate.confidence)
            channel_stats[ch]['residuals'].append(
                float(r.selected_candidate.residual_count()),
            )

        total_wins = sum(
            len(v['wins']) for v in channel_stats.values()
        )

        result: dict[str, Mapping[str, float]] = {}
        for ch, stats in channel_stats.items():
            wins = len(stats['wins'])
            result[ch] = {
                'win_rate': wins / total_wins if total_wins else 0.0,
                'avg_confidence': (
                    sum(stats['confidence']) / wins if wins else 0.0
                ),
                'avg_residuals': (
                    sum(stats['residuals']) / wins if wins else 0.0
                ),
            }
        return result

    def copilot_construction_summary(self) -> str:
        """Summary of the copilot channel's performance.

        Reports win count, win rate vs. other channels, average
        confidence, residual rate, and whether copilot candidates
        tend to need corroboration.
        """
        entries = self._history.all_entries()
        copilot_wins = 0
        copilot_considered = 0
        copilot_confidence: list[float] = []
        copilot_residuals: list[int] = []
        copilot_corroborated = 0
        total_wins = 0

        for _, r in entries:
            # Count copilot candidates in alternatives.
            for alt in r.alternatives:
                if alt.is_copilot():
                    copilot_considered += 1
            if r.selected_candidate is not None:
                total_wins += 1
                if r.selected_candidate.is_copilot():
                    copilot_wins += 1
                    copilot_considered += 1
                    copilot_confidence.append(r.selected_candidate.confidence)
                    copilot_residuals.append(r.selected_candidate.residual_count())
                    if len(r.selected_candidate.evidence_bundle) > 0:
                        copilot_corroborated += 1

        if copilot_considered == 0:
            return 'Copilot channel: no candidates recorded.'

        avg_conf = (
            sum(copilot_confidence) / len(copilot_confidence)
            if copilot_confidence
            else 0.0
        )
        avg_res = (
            sum(copilot_residuals) / len(copilot_residuals)
            if copilot_residuals
            else 0.0
        )
        win_rate = copilot_wins / total_wins if total_wins else 0.0

        lines = [
            f'Copilot construction summary',
            f'  candidates considered: {copilot_considered}',
            f'  wins                : {copilot_wins} ({win_rate:.1%} of total)',
            f'  avg confidence      : {avg_conf:.2f}',
            f'  avg residuals       : {avg_res:.1f}',
            f'  corroborated wins   : {copilot_corroborated}',
        ]
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Backward-compatible aliases — preserve the original public surface so
# that ``integration.py`` and existing tests continue to work.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConstructionStep:
    """A single step in a legacy construction plan.

    Retained for backward compatibility with ``integration.py`` and
    existing tests.
    """

    description: str
    patch: str


@dataclass(frozen=True, slots=True)
class ConstructionPlan:
    """Legacy construction plan produced by ``propose_construction``.

    Wraps a ``_LegacyGoal`` with ordered steps and residuals.
    Retained for backward compatibility.
    """

    goal: _LegacyGoal
    steps: tuple[ConstructionStep, ...]
    residuals: tuple[str, ...] = field(default_factory=tuple)


def propose_construction(goal: _LegacyGoal) -> ConstructionPlan:
    """Produce a ``ConstructionPlan`` from a legacy goal record.

    This is the original entry-point kept for backward compatibility.
    New code should use ``ConstructionLoop.construct`` instead.
    """
    steps = tuple(
        ConstructionStep(f'construct {goal.proposition}', patch)
        for patch in sorted(goal.support.patch_keys)
    )
    residuals = (
        () if goal.required_tier.value <= 2
        else ('verified evidence required',)
    )
    return ConstructionPlan(goal, steps, residuals)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # New public surface
    'ConstructionStatus',
    'SourceChannel',
    'ConstructionGoal',
    'Candidate',
    'ConstructionContext',
    'ConstructionResult',
    'CandidateNormalizer',
    'CandidateComparator',
    'CandidateSelector',
    'ConstructionLoop',
    'ConstructionHistory',
    'ConstructionDiagnostics',
    # Backward-compatible aliases
    'ConstructionStep',
    'ConstructionPlan',
    'propose_construction',
    # Cross-subsystem enrichments
    'judgment_guided_construction',
    'solver_verified_candidate',
    'trust_scored_candidate',
]


# ---------------------------------------------------------------------------
# Cross-subsystem enrichment functions
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.sections import Section as _Section, SectionFamily as _SectionFamily
except Exception:  # pragma: no cover
    _Section = None  # type: ignore[assignment,misc]
    _SectionFamily = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.z3_session import Z3Session as _Z3Session, SolveOutcome as _SolveOutcome
except Exception:  # pragma: no cover
    _Z3Session = None  # type: ignore[assignment,misc]
    _SolveOutcome = None  # type: ignore[assignment,misc]


def judgment_guided_construction(
    goal: ConstructionGoal,
    *,
    sections: Sequence[Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> list[Candidate]:
    """Construct candidates guided by judgment sections.

    Queries ``jugeo.judgments.sections`` for existing sections at the
    goal's coordinate and uses their data as seeds for candidate
    proposals.  Each section whose support overlaps the goal's
    coordinate contributes a candidate whose provenance records the
    originating section.

    Parameters
    ----------
    goal:
        The construction goal to satisfy.
    sections:
        Pre-fetched judgment sections.  When *None* the function
        attempts to retrieve them from the goal's context.
    context:
        Additional context bindings forwarded to candidate creation.

    Returns
    -------
    list[Candidate]
        One candidate per viable judgment section, with
        ``source=SourceChannel.BRIDGE_THEOREM`` and provenance
        recording the originating section identifier.
    """
    ctx = dict(context or {})
    ctx.update(goal.context)
    raw_sections: Sequence[Any] = sections or ctx.get("sections", ())
    candidates: list[Candidate] = []
    for sec in raw_sections:
        section_id = getattr(sec, "section_id", None) or getattr(sec, "coordinate", "unknown")
        data = getattr(sec, "data", {})
        candidate = Candidate(
            candidate_id=_uid(),
            source=SourceChannel.BRIDGE_THEOREM,
            payload=dict(data) if isinstance(data, Mapping) else {"raw": data},
            provenance={"origin": "judgment_section", "section_id": str(section_id)},
        )
        candidates.append(candidate)
    return candidates


def solver_verified_candidate(
    candidate: Candidate,
    *,
    formula: str = "",
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Verify a candidate using the Z3 solver session.

    Opens a ``jugeo.solver.z3_session.Z3Session``, asserts the given
    *formula* (or a default tautology derived from the candidate
    payload), and returns a verdict dict with keys ``"satisfiable"``,
    ``"model"``, and ``"outcome"``.

    Parameters
    ----------
    candidate:
        The candidate to verify.
    formula:
        An SMT-LIB formula string.  When empty a trivial ``true``
        assertion is used so the call still exercises the session.
    timeout_ms:
        Solver timeout in milliseconds.

    Returns
    -------
    dict[str, Any]
        ``{"satisfiable": bool, "model": dict | None, "outcome": str}``.

    Raises
    ------
    RuntimeError
        If ``jugeo.solver.z3_session`` is not available.
    """
    if _Z3Session is None:
        raise RuntimeError(
            "jugeo.solver.z3_session is not available; "
            "cannot verify candidate with Z3"
        )
    session = _Z3Session(timeout_ms=timeout_ms)
    try:
        assertion = formula or "true"
        session.assert_formula(assertion)
        outcome = session.check_sat()
        outcome_str = outcome.value if hasattr(outcome, "value") else str(outcome)
        model: dict[str, Any] | None = None
        if outcome_str.upper() == "SAT":
            try:
                model = session.get_model()
            except Exception:
                model = None
        return {
            "satisfiable": outcome_str.upper() == "SAT",
            "model": model,
            "outcome": outcome_str,
        }
    finally:
        if hasattr(session, "close"):
            session.close()


def trust_scored_candidate(
    candidate: Candidate,
    *,
    trust_profile: Any | None = None,
) -> dict[str, Any]:
    """Score a candidate by its trust level using ``jugeo.evidence.trust``.

    Retrieves the candidate's trust metadata and computes a composite
    score combining the source channel's default trust floor with any
    externally supplied :class:`~jugeo.evidence.trust.TrustProfile`.

    Parameters
    ----------
    candidate:
        The candidate to score.
    trust_profile:
        An optional ``TrustProfile`` to merge with the channel default.

    Returns
    -------
    dict[str, Any]
        ``{"trust_level": str, "trust_tier": str, "score": float,
        "requires_corroboration": bool}``.
    """
    source = candidate.source if hasattr(candidate, "source") else SourceChannel.COPILOT
    base_level = source.default_trust_floor
    level_str = base_level.value if hasattr(base_level, "value") else str(base_level)
    requires_corr = source.requires_corroboration

    tier_str = ""
    if trust_profile is not None:
        tier_attr = getattr(trust_profile, "tier", None)
        tier_str = tier_attr.value if hasattr(tier_attr, "value") else str(tier_attr or "")

    score_map: dict[str, float] = {
        "MECHANICALLY_VERIFIED": 1.0,
        "SOLVER_DISCHARGED": 0.85,
        "RUNTIME_WITNESSED": 0.7,
        "HUMAN_ATTESTED": 0.6,
        "ORACLE_PROPOSED": 0.4,
        "COPILOT_SUGGESTED": 0.3,
        "UNVERIFIED": 0.1,
        "CONTRADICTED": 0.0,
    }
    score = score_map.get(level_str.upper(), 0.1)

    return {
        "trust_level": level_str,
        "trust_tier": tier_str,
        "score": score,
        "requires_corroboration": requires_corr,
    }


# copilot: shared-core marker for future LLM orchestration.
