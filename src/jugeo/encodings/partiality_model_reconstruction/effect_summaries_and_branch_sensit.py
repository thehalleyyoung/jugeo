"""
Effect summaries tracking branch-sensitive partiality across control flow.

Control flow branches create branch-sensitive partiality: a function may be total on
one branch and partial on another. This module models effect summaries for each branch
and aggregates them into a global effect model.

# copilot:
"""

from __future__ import annotations

import time
import hashlib
import itertools
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from functools import reduce
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

# ---------------------------------------------------------------------------
# JuGeo import pattern with fallback stubs
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

    def raise_with_scope(  # type: ignore[no-redef]
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
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TrustTier — epistemic tier for judgments carried by effect summaries
# ---------------------------------------------------------------------------
class TrustTier(IntEnum):
    """
    Ordered confidence levels attached to every judgment in this module.

    The lattice is totally ordered::

        PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED

    Higher tiers are stronger: a PROOF_BACKED claim overrides a PROPOSAL claim
    about the same fact.  Operations ``join`` and ``meet`` compute the
    lattice-theoretic least upper bound and greatest lower bound respectively.
    ``promote`` advances a tier by one step; ``demote`` retreats by one step.
    """

    PROPOSAL = 1
    """A heuristic or unreviewed machine-generated claim."""

    REVIEWED = 2
    """Passed human or tooling review but not formally verified."""

    VERIFIED = 3
    """Formally or statically verified (e.g. by a type-checker)."""

    RUNTIME_WITNESSED = 4
    """Observed at runtime with sufficient coverage."""

    PROOF_BACKED = 5
    """Discharged by an automated or interactive theorem prover."""

    # ------------------------------------------------------------------
    def join(self, other: TrustTier) -> TrustTier:
        """Return the least upper bound (highest confidence) of *self* and *other*."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Return the greatest lower bound (lowest confidence) of *self* and *other*."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Advance trust by one step, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        """Retreat trust by one step, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

    def is_admissible(self, threshold: TrustTier) -> bool:
        """Return True iff self satisfies *threshold* in the trust ordering (self ≽ threshold)."""
        return self.value >= threshold.value

    def is_at_least(self, threshold: TrustTier) -> bool:
        """True iff this tier satisfies *threshold* in the ordering ⪯ (alias for is_admissible)."""
        return self.value >= threshold.value


# ---------------------------------------------------------------------------
# Judgment — epistemic wrapper for every logical claim
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Judgment:
    """
    An epistemic record wrapping a logical claim.

    Every ``EffectSummary``, ``BranchSensitiveEffect``, and ``EffectObligation``
    carries exactly one ``Judgment`` so that the full provenance chain — from the
    heuristic classifier to formal proof — is preserved.

    Attributes
    ----------
    context:
        Human-readable description of the reasoning context.
    formula:
        The logical formula (or informal claim) this judgment asserts.
    assumptions:
        Propositions assumed when deriving this judgment.
    evidence:
        Identifiers of evidence items that support this claim.
    obligations:
        Residual proof obligations left open by this judgment.
    burden:
        Party responsible for discharging open obligations.
    trust:
        Epistemic tier of this judgment.
    provenance:
        Opaque provenance token (e.g. tool name, commit SHA).
    """

    context: str = ""
    formula: str = ""
    assumptions: Tuple[str, ...] = ()
    evidence: Tuple[str, ...] = ()
    obligations: Tuple[str, ...] = ()
    burden: str = "unassigned"
    trust: TrustTier = TrustTier.PROPOSAL
    provenance: Optional[str] = None

    def with_trust(self, tier: TrustTier) -> Judgment:
        """Return a copy of this judgment at a different trust tier."""
        return replace(self, trust=tier)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "context": self.context,
            "formula": self.formula,
            "assumptions": list(self.assumptions),
            "evidence": list(self.evidence),
            "obligations": list(self.obligations),
            "burden": self.burden,
            "trust": self.trust.name,
            "provenance": self.provenance,
        }


# ---------------------------------------------------------------------------
# CechObstruction — topological obstruction to global consistency
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CechObstruction:
    """
    A Čech-cohomology obstruction arising when local partial data cannot be
    consistently patched into a global section.

    In the context of effect summaries this occurs when two branches ascribe
    contradictory effects to the same variable or when type constraints from
    different analysis passes are incompatible.

    Attributes
    ----------
    cover_id:
        Identifier of the open cover (set of analysis patches) where the
        obstruction was detected.
    cocycle:
        The 1-cocycle that failed to be a coboundary; represented as a
        string encoding of the mismatching transition maps.
    cohomology_class:
        String label for the cohomology class (e.g. ``"H^1(X, F) != 0"``).
    description:
        Human-readable explanation of what the obstruction means.
    """

    cover_id: str
    cocycle: FrozenSet[str]
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """
        Return ``True`` when the obstruction is trivially removable.

        A trivial obstruction has an empty cocycle set — no mismatching
        transition maps — so the local data can be globally patched.
        """
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# EffectKind — taxonomy of computational side-effects
# ---------------------------------------------------------------------------
class EffectKind(str, Enum):
    """
    Taxonomy of computational side-effects recognised by the JuGeo effect analyser.

    Each member represents a distinct *effect kind*; a block may have zero or
    more of these.  The ``is_pure`` property is ``True`` only for
    :attr:`PURE` and :attr:`NONE_RETURN`.  The ``is_side_effecting`` property
    is the complement of ``is_pure``.

    .. note::
        ``NONE_RETURN`` is treated as *not* a side-effect in the strict sense
        (it does not touch external state) but is tracked separately because
        it affects the totality of the function contract.
    """

    IO = "io"
    """Generic I/O — catch-all for unclassified I/O operations."""

    MUTATION = "mutation"
    """Writes to a variable (local or global)."""

    EXCEPTION = "exception"
    """May raise an exception."""

    NONE_RETURN = "none_return"
    """May return ``None`` (partiality marker)."""

    PURE = "pure"
    """Provably free of side-effects."""

    LOGGING = "logging"
    """Writes to a log channel (weaker than IO)."""

    NETWORK = "network"
    """Performs network I/O (TCP/UDP/HTTP)."""

    FILESYSTEM = "filesystem"
    """Reads from or writes to the filesystem."""

    DATABASE = "database"
    """Interacts with a database system."""

    UNKNOWN = "unknown"
    """Effect kind could not be determined."""

    # ------------------------------------------------------------------
    @property
    def is_pure(self) -> bool:
        """``True`` iff this kind represents absence of side-effects."""
        return self in (EffectKind.PURE, EffectKind.NONE_RETURN)

    @property
    def is_side_effecting(self) -> bool:
        """``True`` iff this kind represents a genuine side-effect."""
        return not self.is_pure


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PURE_EFFECTS: FrozenSet[EffectKind] = frozenset({EffectKind.PURE, EffectKind.NONE_RETURN})
"""Effect kinds that do not alter external state."""

SIDE_EFFECTING_EFFECTS: FrozenSet[EffectKind] = frozenset(
    k for k in EffectKind if k not in PURE_EFFECTS
)
"""All effect kinds that alter external state or exhibit observable behaviour."""

IO_EFFECTS: FrozenSet[EffectKind] = frozenset(
    {EffectKind.IO, EffectKind.NETWORK, EffectKind.FILESYSTEM, EffectKind.DATABASE}
)
"""Effect kinds that involve I/O subsystems."""

EFFECT_SEVERITY_MAP: Dict[EffectKind, int] = {
    EffectKind.PURE: 1,
    EffectKind.NONE_RETURN: 2,
    EffectKind.LOGGING: 3,
    EffectKind.MUTATION: 4,
    EffectKind.IO: 5,
    EffectKind.EXCEPTION: 6,
    EffectKind.FILESYSTEM: 7,
    EffectKind.NETWORK: 8,
    EffectKind.DATABASE: 9,
    EffectKind.UNKNOWN: 10,
}
"""
Severity score for each effect kind on a scale of 1 (benign) to 10 (most impactful).

Used by :meth:`EffectSummary.dominant_effect` to pick the most impactful effect.
"""

# Map from severity → kind (inverse of EFFECT_SEVERITY_MAP, used internally)
_SEVERITY_TO_KIND: Dict[int, EffectKind] = {v: k for k, v in EFFECT_SEVERITY_MAP.items()}


# ---------------------------------------------------------------------------
# Helper — build a default Judgment for internal use
# ---------------------------------------------------------------------------
def _make_judgment(
    context: str,
    formula: str,
    trust: TrustTier = TrustTier.PROPOSAL,
    provenance: Optional[str] = None,
) -> Judgment:
    """Return a minimally-populated :class:`Judgment`."""
    return Judgment(
        context=context,
        formula=formula,
        trust=trust,
        provenance=provenance or f"s04:{uuid.uuid4().hex[:8]}",
    )


# ---------------------------------------------------------------------------
# EffectSummary
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EffectSummary:
    """
    A summary of the side-effects of a single basic block or expression.

    Effect summaries are the atomic unit of effect analysis.  They record
    *which* effect kinds are present, whether the block may raise, whether
    it may return ``None``, and which global variables it reads and writes.

    Summaries form a join-semilattice under :meth:`merge_with`; the bottom
    element has ``effects=()``, ``may_raise=()``, ``may_return_none=False``,
    and empty global sets.

    Attributes
    ----------
    summary_id:
        Unique identifier for this summary.
    block_description:
        Human-readable label for the block being summarised.
    effects:
        Tuple of :class:`EffectKind` values observed in this block.
    may_raise:
        Names of exception types this block may raise.
    may_return_none:
        Whether the block may return ``None`` (a partiality flag).
    reads_globals:
        Global variable names read by this block.
    writes_globals:
        Global variable names written by this block.
    judgment:
        Epistemic status of this summary.
    """

    summary_id: str
    block_description: str
    effects: Tuple[EffectKind, ...]
    may_raise: Tuple[str, ...]
    may_return_none: bool
    reads_globals: FrozenSet[str]
    writes_globals: FrozenSet[str]
    judgment: Judgment

    # ------------------------------------------------------------------
    def is_pure(self) -> bool:
        """
        Return ``True`` iff this block has no side-effects.

        A block is pure when its ``effects`` tuple contains only
        ``EffectKind.PURE`` (or is empty) and it neither may raise nor
        writes any global variables.
        """
        side_effects = [e for e in self.effects if e.is_side_effecting]
        return (
            len(side_effects) == 0
            and len(self.may_raise) == 0
            and len(self.writes_globals) == 0
        )

    def is_total(self) -> bool:
        """
        Return ``True`` iff this block is total.

        A block is total when it neither may return ``None`` nor may raise
        an exception.  Totality is a necessary condition for the block to
        satisfy a tight postcondition.
        """
        return not self.may_return_none and len(self.may_raise) == 0

    def has_effect(self, kind: EffectKind) -> bool:
        """Return ``True`` iff ``kind`` is present in ``self.effects``."""
        return kind in self.effects

    def dominant_effect(self) -> EffectKind:
        """
        Return the most impactful :class:`EffectKind` in this summary.

        Dominance is determined by :data:`EFFECT_SEVERITY_MAP`.  If no
        effects are recorded the result is :attr:`EffectKind.PURE`.
        """
        if not self.effects:
            return EffectKind.PURE
        return max(self.effects, key=lambda e: EFFECT_SEVERITY_MAP.get(e, 0))

    def merge_with(self, other: EffectSummary) -> EffectSummary:
        """
        Return the semilattice join (union) of *self* and *other*.

        The merged summary captures the union of all effects, exception
        types, global reads, and global writes.  The resulting ``summary_id``
        is a fresh UUID; the ``judgment`` uses the *meet* (lower) trust of
        the two inputs so the merged claim is no stronger than the weakest
        component.

        This operation is commutative and associative.
        """
        merged_effects = tuple(set(self.effects) | set(other.effects))
        merged_raises = tuple(set(self.may_raise) | set(other.may_raise))
        merged_reads = self.reads_globals | other.reads_globals
        merged_writes = self.writes_globals | other.writes_globals
        merged_trust = self.judgment.trust.meet(other.judgment.trust)
        merged_judgment = _make_judgment(
            context=f"merge({self.block_description!r}, {other.block_description!r})",
            formula="merged effect summary",
            trust=merged_trust,
        )
        return EffectSummary(
            summary_id=uuid.uuid4().hex,
            block_description=f"merge({self.block_description}, {other.block_description})",
            effects=merged_effects,
            may_raise=merged_raises,
            may_return_none=self.may_return_none or other.may_return_none,
            reads_globals=merged_reads,
            writes_globals=merged_writes,
            judgment=merged_judgment,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "summary_id": self.summary_id,
            "block_description": self.block_description,
            "effects": [e.value for e in self.effects],
            "may_raise": list(self.may_raise),
            "may_return_none": self.may_return_none,
            "reads_globals": sorted(self.reads_globals),
            "writes_globals": sorted(self.writes_globals),
            "judgment": self.judgment.to_dict(),
            "is_pure": self.is_pure(),
            "is_total": self.is_total(),
            "dominant_effect": self.dominant_effect().value,
        }


# ---------------------------------------------------------------------------
# BranchSensitiveEffect
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BranchSensitiveEffect:
    """
    Records how effects differ across the two arms of a conditional branch.

    When a branch condition ``c`` is known, the ``true``-arm and ``false``-arm
    may exhibit entirely different effect profiles.  This data structure makes
    that asymmetry explicit and first-class so that downstream reasoning can
    track it without losing precision.

    Attributes
    ----------
    effect_id:
        Unique identifier for this record.
    condition:
        String representation of the branch condition (e.g. ``"x > 0"``).
    true_branch_effects:
        Effects present when the condition evaluates to ``True``.
    false_branch_effects:
        Effects present when the condition evaluates to ``False``.
    true_may_return_none:
        Whether the ``True``-branch may return ``None``.
    false_may_return_none:
        Whether the ``False``-branch may return ``None``.
    judgment:
        Epistemic status of this branch analysis.
    """

    effect_id: str
    condition: str
    true_branch_effects: Tuple[EffectKind, ...]
    false_branch_effects: Tuple[EffectKind, ...]
    true_may_return_none: bool
    false_may_return_none: bool
    judgment: Judgment

    # ------------------------------------------------------------------
    def effects_differ(self) -> bool:
        """
        Return ``True`` iff the effect profiles of the two arms are different.

        Comparison is performed on the *sorted* effect sets so that ordering
        within the tuples does not affect the result.
        """
        return sorted(e.value for e in self.true_branch_effects) != sorted(
            e.value for e in self.false_branch_effects
        )

    def is_branch_sensitive(self) -> bool:
        """Alias for :meth:`effects_differ`."""
        return self.effects_differ()

    def true_summary_sketch(self) -> str:
        """
        Return a one-line human-readable sketch of the ``True``-arm effects.

        The sketch includes the effect kinds and the partiality flag.
        """
        effect_names = ", ".join(e.value for e in self.true_branch_effects) or "pure"
        none_note = " [may→None]" if self.true_may_return_none else ""
        return f"if {self.condition}: effects=[{effect_names}]{none_note}"

    def false_summary_sketch(self) -> str:
        """
        Return a one-line human-readable sketch of the ``False``-arm effects.
        """
        effect_names = ", ".join(e.value for e in self.false_branch_effects) or "pure"
        none_note = " [may→None]" if self.false_may_return_none else ""
        return f"else: effects=[{effect_names}]{none_note}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "effect_id": self.effect_id,
            "condition": self.condition,
            "true_branch_effects": [e.value for e in self.true_branch_effects],
            "false_branch_effects": [e.value for e in self.false_branch_effects],
            "true_may_return_none": self.true_may_return_none,
            "false_may_return_none": self.false_may_return_none,
            "effects_differ": self.effects_differ(),
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# PartialBranchMap
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PartialBranchMap:
    """
    An n-way map from branch conditions to :class:`EffectSummary` objects.

    In a multi-way conditional (``if/elif/else`` or ``match``) each arm has
    its own effect summary.  The :class:`PartialBranchMap` aggregates these
    into a single object that supports worst-case analysis.

    Attributes
    ----------
    map_id:
        Unique identifier for this map.
    branches:
        A tuple of ``(condition_str, EffectSummary)`` pairs in source order.
    join_point_summary:
        Optional post-join effect summary (effect after the entire branch).
    judgment:
        Epistemic status of this map.
    """

    map_id: str
    branches: Tuple[Tuple[str, EffectSummary], ...]
    join_point_summary: Optional[EffectSummary]
    judgment: Judgment

    # ------------------------------------------------------------------
    def get_branch(self, condition: str) -> Optional[EffectSummary]:
        """Return the :class:`EffectSummary` for *condition*, or ``None``."""
        for cond, summary in self.branches:
            if cond == condition:
                return summary
        return None

    def all_conditions(self) -> List[str]:
        """Return all branch condition strings in source order."""
        return [cond for cond, _ in self.branches]

    def all_summaries(self) -> List[EffectSummary]:
        """Return all :class:`EffectSummary` objects in source order."""
        return [summary for _, summary in self.branches]

    def is_complete(self) -> bool:
        """
        Return ``True`` iff every branch in the map has an associated summary.

        Completeness is trivially satisfied since the branch list is a tuple
        of ``(condition, summary)`` pairs — every recorded branch has a
        summary.  The method is provided for interface compatibility.
        """
        return len(self.branches) > 0

    def worst_case_summary(self) -> EffectSummary:
        """
        Return the conservative join of all branch summaries.

        The worst-case summary has the *union* of all effects, all exception
        types, and all global accesses across every arm.  It represents what
        a caller must be prepared for when it cannot determine which branch
        will be taken at runtime.
        """
        summaries = self.all_summaries()
        if not summaries:
            wc_judgment = _make_judgment(
                "worst_case_summary", "empty branch map", TrustTier.PROPOSAL
            )
            return EffectSummary(
                summary_id=uuid.uuid4().hex,
                block_description="empty-map-worst-case",
                effects=(),
                may_raise=(),
                may_return_none=False,
                reads_globals=frozenset(),
                writes_globals=frozenset(),
                judgment=wc_judgment,
            )
        return reduce(lambda a, b: a.merge_with(b), summaries)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "map_id": self.map_id,
            "branches": [(cond, s.to_dict()) for cond, s in self.branches],
            "join_point_summary": (
                self.join_point_summary.to_dict()
                if self.join_point_summary is not None
                else None
            ),
            "judgment": self.judgment.to_dict(),
            "is_complete": self.is_complete(),
        }


# ---------------------------------------------------------------------------
# EffectObligation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EffectObligation:
    """
    A proof or handling obligation generated from an :class:`EffectSummary`.

    Obligations arise when a block exhibits a side-effect that must be
    explicitly handled by the calling context.  For example, a block that
    may raise ``ValueError`` generates an obligation to catch or propagate
    that exception.

    Attributes
    ----------
    obligation_id:
        Unique identifier.
    effect:
        The :class:`EffectKind` that gives rise to this obligation.
    description:
        Human-readable description of what must be done.
    must_handle:
        Whether the obligation is *mandatory* (True) or advisory (False).
    handling_strategy:
        Optional string describing the chosen handling strategy once
        the obligation has been discharged.
    judgment:
        Epistemic status.
    """

    obligation_id: str
    effect: EffectKind
    description: str
    must_handle: bool
    handling_strategy: Optional[str]
    judgment: Judgment

    # ------------------------------------------------------------------
    def discharge(self, strategy: str) -> EffectObligation:
        """
        Return a copy of this obligation with *strategy* recorded.

        Discharging records the handling strategy and promotes the
        trust tier by one step to reflect that the obligation has been
        addressed.
        """
        promoted_judgment = replace(
            self.judgment, trust=self.judgment.trust.promote()
        )
        return replace(
            self,
            handling_strategy=strategy,
            judgment=promoted_judgment,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "obligation_id": self.obligation_id,
            "effect": self.effect.value,
            "description": self.description,
            "must_handle": self.must_handle,
            "handling_strategy": self.handling_strategy,
            "judgment": self.judgment.to_dict(),
        }


# ---------------------------------------------------------------------------
# EffectAnalyzer
# ---------------------------------------------------------------------------
class EffectAnalyzer:
    """
    Stateful analyser that builds :class:`EffectSummary` and
    :class:`PartialBranchMap` objects from structured descriptions.

    The analyser accumulates statistics about how many summaries have been
    produced, how many branches have been analysed, and how many obligations
    have been generated.

    Parameters
    ----------
    trust:
        Default :class:`TrustTier` to attach to all produced judgments.
    """

    def __init__(self, trust: TrustTier = TrustTier.PROPOSAL) -> None:
        self._trust = trust
        self._n_blocks_analyzed: int = 0
        self._n_branches_analyzed: int = 0
        self._n_obligations_generated: int = 0

    # ------------------------------------------------------------------
    def analyze_block(
        self,
        block_desc: str,
        declared_effects: List[str],
    ) -> EffectSummary:
        """
        Produce an :class:`EffectSummary` from a block description and a
        list of declared effect strings.

        Parameters
        ----------
        block_desc:
            Human-readable label for the block.
        declared_effects:
            List of string tokens representing declared effects.  Each token
            is passed through :func:`classify_effect` to obtain a
            :class:`EffectKind`.

        Returns
        -------
        EffectSummary
            The produced summary.
        """
        self._n_blocks_analyzed += 1
        effect_kinds = tuple(dict.fromkeys(classify_effect(e) for e in declared_effects))
        may_raise: Tuple[str, ...] = ()
        may_return_none = False
        writes_globals: FrozenSet[str] = frozenset()
        reads_globals: FrozenSet[str] = frozenset()
        if EffectKind.EXCEPTION in effect_kinds:
            may_raise = ("Exception",)
        if EffectKind.NONE_RETURN in effect_kinds:
            may_return_none = True
        judgment = _make_judgment(
            context=f"analyze_block({block_desc!r})",
            formula=f"block has effects {[e.value for e in effect_kinds]}",
            trust=self._trust,
        )
        return EffectSummary(
            summary_id=uuid.uuid4().hex,
            block_description=block_desc,
            effects=effect_kinds,
            may_raise=may_raise,
            may_return_none=may_return_none,
            reads_globals=reads_globals,
            writes_globals=writes_globals,
            judgment=judgment,
        )

    # ------------------------------------------------------------------
    def analyze_branches(
        self,
        branch_specs: List[Dict[str, Any]],
    ) -> PartialBranchMap:
        """
        Produce a :class:`PartialBranchMap` from a list of branch specification
        dictionaries.

        Parameters
        ----------
        branch_specs:
            Each dict must contain:

            * ``"condition"`` (str) – the branch condition.
            * ``"effects"`` (list[str]) – declared effect tokens.
            * ``"may_return_none"`` (bool, optional) – partiality flag.
            * ``"may_raise"`` (list[str], optional) – exception type names.

        Returns
        -------
        PartialBranchMap
        """
        branches: List[Tuple[str, EffectSummary]] = []
        for spec in branch_specs:
            condition = spec.get("condition", "unknown")
            effect_strings = spec.get("effects", [])
            may_return_none = spec.get("may_return_none", False)
            may_raise = tuple(spec.get("may_raise", []))
            reads_globals = frozenset(spec.get("reads_globals", []))
            writes_globals = frozenset(spec.get("writes_globals", []))
            effect_kinds = tuple(dict.fromkeys(classify_effect(e) for e in effect_strings))
            j = _make_judgment(
                context=f"branch({condition!r})",
                formula=f"branch effects: {[e.value for e in effect_kinds]}",
                trust=self._trust,
            )
            summary = EffectSummary(
                summary_id=uuid.uuid4().hex,
                block_description=f"branch:{condition}",
                effects=effect_kinds,
                may_raise=may_raise,
                may_return_none=may_return_none,
                reads_globals=reads_globals,
                writes_globals=writes_globals,
                judgment=j,
            )
            branches.append((condition, summary))
            self._n_branches_analyzed += 1

        map_judgment = _make_judgment(
            "analyze_branches",
            f"partial branch map with {len(branches)} branches",
            self._trust,
        )
        return PartialBranchMap(
            map_id=uuid.uuid4().hex,
            branches=tuple(branches),
            join_point_summary=None,
            judgment=map_judgment,
        )

    # ------------------------------------------------------------------
    def generate_obligations(
        self,
        summary: EffectSummary,
    ) -> List[EffectObligation]:
        """
        Generate :class:`EffectObligation` objects for every non-pure effect
        in *summary*.

        Parameters
        ----------
        summary:
            The effect summary to analyse.

        Returns
        -------
        list[EffectObligation]
            One obligation per significant effect kind plus one for each
            exception type that *may_raise* lists.
        """
        obligations: List[EffectObligation] = []
        for kind in summary.effects:
            if kind in PURE_EFFECTS:
                continue
            severity = EFFECT_SEVERITY_MAP.get(kind, 5)
            must = severity >= 6
            j = _make_judgment(
                f"obligation({kind.value})",
                f"obligation for effect {kind.value} in {summary.block_description!r}",
                self._trust,
            )
            ob = EffectObligation(
                obligation_id=uuid.uuid4().hex,
                effect=kind,
                description=f"Handle {kind.value} effect in '{summary.block_description}'",
                must_handle=must,
                handling_strategy=None,
                judgment=j,
            )
            obligations.append(ob)
            self._n_obligations_generated += 1

        for exc_name in summary.may_raise:
            j = _make_judgment(
                f"obligation(exception:{exc_name})",
                f"catch or propagate {exc_name}",
                self._trust,
            )
            ob = EffectObligation(
                obligation_id=uuid.uuid4().hex,
                effect=EffectKind.EXCEPTION,
                description=f"Catch or propagate {exc_name} raised by '{summary.block_description}'",
                must_handle=True,
                handling_strategy=None,
                judgment=j,
            )
            obligations.append(ob)
            self._n_obligations_generated += 1

        return obligations

    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, int]:
        """Return accumulated analysis statistics."""
        return {
            "blocks_analyzed": self._n_blocks_analyzed,
            "branches_analyzed": self._n_branches_analyzed,
            "obligations_generated": self._n_obligations_generated,
        }


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def classify_effect(effect_description: str) -> EffectKind:
    """
    Map a textual effect description to an :class:`EffectKind`.

    This is a heuristic classifier that matches common Python idioms and
    library names.  The matching is case-insensitive and uses substring
    search rather than parsing.

    Rules (applied in order; first match wins):

    * ``"print"``, ``"sys.stdout"``, ``"logging"`` → :attr:`EffectKind.LOGGING`
    * ``"open"``, ``"os.path"``, ``"pathlib"`` → :attr:`EffectKind.FILESYSTEM`
    * ``"requests"``, ``"http"``, ``"socket"``, ``"urllib"`` → :attr:`EffectKind.NETWORK`
    * ``"db."``, ``"cursor"``, ``"execute"``, ``"commit"``, ``"rollback"`` → :attr:`EffectKind.DATABASE`
    * ``"raise"`` → :attr:`EffectKind.EXCEPTION`
    * ``"return none"`` → :attr:`EffectKind.NONE_RETURN`
    * ``"pure"``, ``"math."`` → :attr:`EffectKind.PURE`
    * Assignment patterns (``" ="`` or starts with word and ``"="`` present) → :attr:`EffectKind.MUTATION`
    * Default → :attr:`EffectKind.IO`

    Parameters
    ----------
    effect_description:
        A string describing the effect to classify.

    Returns
    -------
    EffectKind
        The matched effect kind.
    """
    desc = effect_description.lower().strip()

    # Explicit pure markers
    if desc in ("pure", "no_effect", "no effect", ""):
        return EffectKind.PURE

    # Logging
    if any(tok in desc for tok in ("print(", "sys.stdout", "logging.", "log.")):
        return EffectKind.LOGGING

    # Filesystem
    if any(tok in desc for tok in ("open(", "os.path", "pathlib", "shutil", ".read(", ".write(")):
        return EffectKind.FILESYSTEM

    # Network
    if any(tok in desc for tok in ("requests.", "http.", "socket.", "urllib.", "aiohttp", "httpx")):
        return EffectKind.NETWORK

    # Database
    if any(tok in desc for tok in ("db.", "cursor.", ".execute(", ".commit(", ".rollback(", "session.")):
        return EffectKind.DATABASE

    # Exception
    if desc.startswith("raise") or "raise " in desc:
        return EffectKind.EXCEPTION

    # None return
    if "return none" in desc or desc == "none_return":
        return EffectKind.NONE_RETURN

    # Pure / math
    if desc.startswith("pure") or "math." in desc or desc == "computation":
        return EffectKind.PURE

    # Mutation (assignment)
    if " = " in desc or (len(desc) > 1 and "=" in desc and "==" not in desc):
        return EffectKind.MUTATION

    # Default: generic IO
    return EffectKind.IO


def build_effect_summary(
    block_descriptor: Dict[str, Any],
    trust: TrustTier = TrustTier.PROPOSAL,
) -> EffectSummary:
    """
    Construct an :class:`EffectSummary` from a block descriptor dictionary.

    Parameters
    ----------
    block_descriptor:
        Dictionary with keys:

        * ``"description"`` (str) – human-readable block label.
        * ``"effects"`` (list[str]) – declared effect tokens.
        * ``"may_raise"`` (list[str]) – exception type names.
        * ``"may_return_none"`` (bool) – partiality flag.
        * ``"reads_globals"`` (list[str]) – global names read.
        * ``"writes_globals"`` (list[str]) – global names written.
    trust:
        Trust tier for the produced judgment.

    Returns
    -------
    EffectSummary
    """
    description = block_descriptor.get("description", "unknown")
    effect_tokens = block_descriptor.get("effects", [])
    may_raise = tuple(block_descriptor.get("may_raise", []))
    may_return_none = bool(block_descriptor.get("may_return_none", False))
    reads_globals: FrozenSet[str] = frozenset(block_descriptor.get("reads_globals", []))
    writes_globals: FrozenSet[str] = frozenset(block_descriptor.get("writes_globals", []))

    effect_kinds = tuple(dict.fromkeys(classify_effect(e) for e in effect_tokens))
    judgment = _make_judgment(
        context=f"build_effect_summary({description!r})",
        formula=f"effects={[e.value for e in effect_kinds]}",
        trust=trust,
    )
    return EffectSummary(
        summary_id=uuid.uuid4().hex,
        block_description=description,
        effects=effect_kinds,
        may_raise=may_raise,
        may_return_none=may_return_none,
        reads_globals=reads_globals,
        writes_globals=writes_globals,
        judgment=judgment,
    )


def branch_sensitive_analysis(
    branches: List[Dict[str, Any]],
    trust: TrustTier = TrustTier.PROPOSAL,
) -> PartialBranchMap:
    """
    Produce a :class:`PartialBranchMap` from a list of branch specification
    dictionaries, each describing one arm of a multi-way conditional.

    Parameters
    ----------
    branches:
        List of dicts, each with:

        * ``"condition"`` (str) – branch guard.
        * ``"effects"`` (list[str]) – effect tokens for this arm.
        * ``"may_return_none"`` (bool, optional).
        * ``"may_raise"`` (list[str], optional).
        * ``"reads_globals"`` (list[str], optional).
        * ``"writes_globals"`` (list[str], optional).
    trust:
        Trust tier for all produced judgments.

    Returns
    -------
    PartialBranchMap
    """
    analyser = EffectAnalyzer(trust=trust)
    return analyser.analyze_branches(branches)


def merge_effect_summaries(
    s1: EffectSummary,
    s2: EffectSummary,
    trust: TrustTier = TrustTier.PROPOSAL,
) -> EffectSummary:
    """
    Return the semilattice join (union) of two :class:`EffectSummary` objects.

    The merged summary has:

    * ``effects`` = union of both effect sets.
    * ``may_raise`` = union of both exception type sets.
    * ``may_return_none`` = logical OR.
    * ``reads_globals`` / ``writes_globals`` = set unions.
    * ``judgment`` at the supplied *trust* tier.

    This function is a convenience wrapper around :meth:`EffectSummary.merge_with`
    that also allows specifying a trust override for the merged judgment.

    Parameters
    ----------
    s1, s2:
        The two summaries to merge.
    trust:
        Override trust for the produced judgment.

    Returns
    -------
    EffectSummary
    """
    merged = s1.merge_with(s2)
    new_judgment = replace(merged.judgment, trust=trust)
    return replace(merged, judgment=new_judgment)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    print("=" * 70)
    print("s04 — Effect Summaries & Branch-Sensitive Partiality — smoke test")
    print("=" * 70)

    # ── 1. Pure function summary ──────────────────────────────────────────
    pure_j = _make_judgment("smoke_test", "pure function", TrustTier.REVIEWED)
    pure_summary = EffectSummary(
        summary_id=uuid.uuid4().hex,
        block_description="math_helper(x)",
        effects=(EffectKind.PURE,),
        may_raise=(),
        may_return_none=False,
        reads_globals=frozenset(),
        writes_globals=frozenset(),
        judgment=pure_j,
    )
    print(f"\n[1] Pure summary: is_pure={pure_summary.is_pure()}, is_total={pure_summary.is_total()}")
    assert pure_summary.is_pure(), "Expected pure summary to be pure"
    assert pure_summary.is_total(), "Expected pure summary to be total"

    # ── 2. IO function summary ────────────────────────────────────────────
    io_j = _make_judgment("smoke_test", "io function", TrustTier.REVIEWED)
    io_summary = EffectSummary(
        summary_id=uuid.uuid4().hex,
        block_description="write_report(data)",
        effects=(EffectKind.IO, EffectKind.FILESYSTEM, EffectKind.EXCEPTION),
        may_raise=("IOError", "PermissionError"),
        may_return_none=True,
        reads_globals=frozenset({"CONFIG"}),
        writes_globals=frozenset({"LAST_WRITE_TS"}),
        judgment=io_j,
    )
    print(f"[2] IO summary: is_pure={io_summary.is_pure()}, dominant={io_summary.dominant_effect().value}")
    assert not io_summary.is_pure(), "IO summary should not be pure"
    assert io_summary.dominant_effect() == EffectKind.FILESYSTEM

    # ── 3. Merge ──────────────────────────────────────────────────────────
    merged = merge_effect_summaries(pure_summary, io_summary, TrustTier.REVIEWED)
    print(f"[3] Merged: effects={[e.value for e in merged.effects]}, may_raise={merged.may_raise}")
    assert EffectKind.FILESYSTEM in merged.effects

    # ── 4. BranchSensitiveEffect ──────────────────────────────────────────
    bse_j = _make_judgment("smoke_test", "branch x>0", TrustTier.PROPOSAL)
    bse = BranchSensitiveEffect(
        effect_id=uuid.uuid4().hex,
        condition="x > 0",
        true_branch_effects=(EffectKind.DATABASE,),
        false_branch_effects=(EffectKind.PURE,),
        true_may_return_none=False,
        false_may_return_none=True,
        judgment=bse_j,
    )
    print(f"[4] Branch 'x>0': effects_differ={bse.effects_differ()}")
    print(f"    true: {bse.true_summary_sketch()}")
    print(f"    false: {bse.false_summary_sketch()}")
    assert bse.effects_differ(), "Expected branches to have different effects"

    # ── 5. branch_sensitive_analysis ─────────────────────────────────────
    specs = [
        {"condition": "mode == 'write'", "effects": ["db.execute"], "may_return_none": False, "may_raise": ["IntegrityError"]},
        {"condition": "mode == 'read'", "effects": ["db.cursor"], "may_return_none": True, "may_raise": []},
        {"condition": "mode == 'noop'", "effects": ["pure"], "may_return_none": False, "may_raise": []},
    ]
    pbm = branch_sensitive_analysis(specs, TrustTier.REVIEWED)
    print(f"\n[5] PartialBranchMap: {len(pbm.branches)} branches, complete={pbm.is_complete()}")
    assert len(pbm.branches) == 3

    # ── 6. worst_case_summary ─────────────────────────────────────────────
    wcs = pbm.worst_case_summary()
    print(f"[6] worst_case_summary effects: {[e.value for e in wcs.effects]}")
    assert EffectKind.DATABASE in wcs.effects

    # ── 7. classify_effect ────────────────────────────────────────────────
    classify_cases = [
        ("print(x)", EffectKind.LOGGING),
        ("open('f.txt')", EffectKind.FILESYSTEM),
        ("requests.get(url)", EffectKind.NETWORK),
        ("db.execute(sql)", EffectKind.DATABASE),
        ("raise ValueError('bad')", EffectKind.EXCEPTION),
        ("return None", EffectKind.NONE_RETURN),
        ("math.sqrt(x)", EffectKind.PURE),
        ("x = 5", EffectKind.MUTATION),
    ]
    print("\n[7] classify_effect results:")
    for desc, expected in classify_cases:
        result = classify_effect(desc)
        mark = "✓" if result == expected else "✗"
        print(f"    {mark} classify_effect({desc!r}) = {result.value} (expected {expected.value})")

    # ── 8. build_effect_summary from descriptor ───────────────────────────
    descriptor = {
        "description": "process_payment(order)",
        "effects": ["db.execute", "requests.post", "raise ValueError"],
        "may_raise": ["PaymentError", "NetworkError"],
        "may_return_none": False,
        "reads_globals": ["PAYMENT_GATEWAY_URL"],
        "writes_globals": ["ORDER_LOG"],
    }
    built = build_effect_summary(descriptor, TrustTier.REVIEWED)
    print(f"\n[8] Built summary: {built.block_description}, effects={[e.value for e in built.effects]}")
    assert EffectKind.DATABASE in built.effects or EffectKind.NETWORK in built.effects

    # ── 9. Generate obligations ───────────────────────────────────────────
    analyser = EffectAnalyzer(trust=TrustTier.REVIEWED)
    obligations = analyser.generate_obligations(built)
    print(f"[9] Generated {len(obligations)} obligations:")
    for ob in obligations:
        print(f"    - [{ob.effect.value}] {ob.description[:60]} (must_handle={ob.must_handle})")

    # ── 10. effects_differ ───────────────────────────────────────────────
    print(f"\n[10] effects_differ() for 'x > 0' branch: {bse.effects_differ()}")

    # ── Stats ─────────────────────────────────────────────────────────────
    stats = analyser.get_stats()
    print(f"\nAnalyser stats: {stats}")

    print("\n✓ smoke test passed")
