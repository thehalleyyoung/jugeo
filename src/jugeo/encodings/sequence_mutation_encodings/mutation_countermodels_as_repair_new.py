"""Mutation countermodels as repair guides: transforming Z3 countermodels for mutation obligations
into actionable repair guidance.

When a mutation obligation cannot be discharged (Z3 finds a countermodel), the countermodel
encodes a concrete execution scenario where the obligation is violated. This module extracts
structured repair guides from those countermodels — guiding the LLM oracle or human developer
to fix the mutation.

Invariants:
- Judgments are tuples (c, φ, A, E, O, B, T, Π) — never booleans
- Trust is an ordered algebra — never a float
- Obstructions are Čech H¹ cohomology classes

# copilot: mutation countermodels as repair guides — Z3 countermodels become actionable fix guidance
"""

from __future__ import annotations

import abc
import collections
import contextlib
import copy
import dataclasses
import enum
import functools
import hashlib
import itertools
import json
import logging
import math
import os
import pathlib
import re
import sys
import time
import uuid
import weakref
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    Generator,
    Iterable,
    Iterator,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)

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


log: logging.Logger = logging.getLogger(__name__)

REPAIR_KIND_COSTS: Dict[str, int] = {
    "ADD_GUARD": 2,
    "STRENGTHEN_PRECONDITION": 3,
    "WEAKEN_POSTCONDITION": 4,
    "CHANGE_MUTATION_ORDER": 6,
    "INTRODUCE_COPY": 5,
    "ADD_LOCK": 8,
    "REFACTOR_SCOPE": 10,
    "DELEGATE_TO_ORACLE": 1,
}

GUIDE_CONFIDENCE_THRESHOLDS: Dict[str, float] = {
    "LOW": 0.0,
    "MEDIUM": 0.4,
    "HIGH": 0.7,
    "CONCLUSIVE": 0.9,
}

MAX_GUIDES_PER_VIOLATION: int = 5


class TrustTier(IntEnum):
    """Ordered trust algebra for repair-guide judgments."""

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        """Lattice join (least upper bound)."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        """Lattice meet (greatest lower bound)."""
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        """Increment trust by one level, clamped at PROOF_BACKED."""
        return TrustTier(min(self.value + 1, 5))

    def demote(self) -> TrustTier:
        """Decrement trust by one level, clamped at PROPOSAL."""
        return TrustTier(max(self.value - 1, 1))

    def is_admissible(self, threshold: TrustTier) -> bool:
        """Return True iff self meets or exceeds threshold."""
        return self.value >= threshold.value


class RepairKind(str, Enum):
    """The class of repair action recommended by a repair guide."""

    ADD_GUARD = "add_guard"
    STRENGTHEN_PRECONDITION = "strengthen_precondition"
    WEAKEN_POSTCONDITION = "weaken_postcondition"
    CHANGE_MUTATION_ORDER = "change_mutation_order"
    INTRODUCE_COPY = "introduce_copy"
    ADD_LOCK = "add_lock"
    REFACTOR_SCOPE = "refactor_scope"
    DELEGATE_TO_ORACLE = "delegate_to_oracle"


class GuideConfidence(str, Enum):
    """How confident the system is that a repair guide will fix the violation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CONCLUSIVE = "conclusive"


class CountermodelInterpretation(str, Enum):
    """How the countermodel should be interpreted."""

    CONCRETE_VIOLATION = "concrete_violation"
    POTENTIAL_VIOLATION = "potential_violation"
    SPURIOUS = "spurious"
    UNDERDETERMINED = "underdetermined"


class RepairPriority(str, Enum):
    """Priority level for applying a repair guide."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    Fields: context, formula, assumptions, evidence, obligations, burden, trust, provenance.
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
    """A Čech H¹ cohomology class obstructing global consistency.

    Fields: cover_id, cocycle, cohomology_class, description.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        """Return True iff the obstruction is trivial."""
        return len(self.cocycle) == 0


@dataclass(frozen=True)
class MutationCountermodel:
    """A Z3 countermodel witnessing violation of a mutation obligation.

    Fields:
        model_id: Unique identifier for this countermodel.
        obligation_id: Which mutation obligation was violated.
        variable_assignments: Dict mapping variable names to concrete values.
        path_condition: The path condition under which the violation occurs.
        violated_formula: The formula that was falsified.
        interpretation: How this countermodel should be interpreted.
        trust: Trust level for this countermodel.
    """

    model_id: str
    obligation_id: str
    variable_assignments: dict
    path_condition: str
    violated_formula: str
    interpretation: CountermodelInterpretation
    trust: TrustTier


@dataclass(frozen=True)
class RepairGuide:
    """An actionable guide for repairing a mutation obligation violation.

    Fields:
        guide_id: Unique identifier.
        obligation_id: Which obligation this guide addresses.
        countermodel_id: Which countermodel triggered this guide.
        kind: The class of repair.
        confidence: How confident we are this guide will work.
        priority: How urgently this guide should be applied.
        description: Human-readable description of the repair.
        suggested_code_delta: A code snippet or diff suggestion.
        estimated_cost: Estimated effort in abstract cost units.
        trust: Trust level for this guide.
    """

    guide_id: str
    obligation_id: str
    countermodel_id: str
    kind: RepairKind
    confidence: GuideConfidence
    priority: RepairPriority
    description: str
    suggested_code_delta: str
    estimated_cost: int
    trust: TrustTier


@dataclass(frozen=True)
class CountermodelAsGuide:
    """A bundle of repair guides derived from a single countermodel.

    Fields:
        cas_id: Unique identifier.
        countermodel: The source countermodel.
        guides: Tuple of RepairGuide objects (in ranking order).
        ranking: Tuple of integer ranks (parallel to guides).
        trust: Trust level for this bundle.
    """

    cas_id: str
    countermodel: MutationCountermodel
    guides: tuple
    ranking: tuple
    trust: TrustTier


@dataclass(frozen=True)
class RepairBundle:
    """A finalised repair bundle with a selected guide.

    Fields:
        bundle_id: Unique identifier.
        obligation_id: The obligation being repaired.
        cas: The CountermodelAsGuide that provided the options.
        selected_guide: The best guide, or None if none are admissible.
        trust: Trust level for this bundle.
    """

    bundle_id: str
    obligation_id: str
    cas: CountermodelAsGuide
    selected_guide: Optional[RepairGuide]
    trust: TrustTier


def _extract_variable_trace(model: MutationCountermodel) -> dict:
    """Extract a variable trace from a countermodel for diagnosis.

    Args:
        model: The countermodel to trace.

    Returns:
        A dict with keys 'assignments', 'path_conjuncts', 'violated'.
    """
    path_conjuncts = [c.strip() for c in model.path_condition.split("&&")]
    return {
        "assignments": dict(model.variable_assignments),
        "path_conjuncts": path_conjuncts,
        "violated": model.violated_formula,
        "model_id": model.model_id,
    }


def _suggest_guard_condition(violation_context: dict) -> str:
    """Suggest a guard condition that would prevent the violation.

    Args:
        violation_context: A dict from _extract_variable_trace.

    Returns:
        A Python guard expression string.
    """
    assignments = violation_context.get("assignments", {})
    guards: List[str] = []
    for var, val in assignments.items():
        if isinstance(val, (int, float)) and val == 0:
            guards.append(f"{var} != 0")
        elif isinstance(val, str) and val in ("None", "null"):
            guards.append(f"{var} is not None")
        elif isinstance(val, bool) and not val:
            guards.append(f"{var} is True")
        else:
            guards.append(f"isinstance({var}, type({val!r}))")
    if not guards:
        return "True  # no automatic guard identified"
    return " and ".join(guards)


def _estimate_refactor_cost(guide: RepairGuide) -> int:
    """Estimate the refactoring cost for a guide.

    Args:
        guide: The repair guide.

    Returns:
        An integer cost estimate.
    """
    base = REPAIR_KIND_COSTS.get(guide.kind.name, 5)
    confidence_multiplier = {
        GuideConfidence.CONCLUSIVE: 1,
        GuideConfidence.HIGH: 2,
        GuideConfidence.MEDIUM: 3,
        GuideConfidence.LOW: 5,
    }.get(guide.confidence, 3)
    return base * confidence_multiplier


def extract_repair_guide(
    countermodel: MutationCountermodel,
    context: dict,
) -> RepairGuide:
    """Extract a single best-effort repair guide from a countermodel.

    Args:
        countermodel: The Z3 countermodel for the violated obligation.
        context: Additional context (e.g. 'mutation_kind', 'function_name').

    Returns:
        A RepairGuide with the recommended action.
    """
    trace = _extract_variable_trace(countermodel)
    guard_str = _suggest_guard_condition(trace)

    has_none = any(
        v in ("None", "null", None)
        for v in countermodel.variable_assignments.values()
    )
    has_zero = any(
        v == 0 or v == "0"
        for v in countermodel.variable_assignments.values()
    )

    if has_none:
        kind = RepairKind.ADD_GUARD
        confidence = GuideConfidence.HIGH
        description = (
            f"Add a None-guard before the mutation at obligation "
            f"{countermodel.obligation_id}."
        )
        delta = f"if {guard_str}:\n    # original mutation here\n    pass"
    elif has_zero:
        kind = RepairKind.STRENGTHEN_PRECONDITION
        confidence = GuideConfidence.MEDIUM
        description = (
            f"Strengthen the precondition for {countermodel.obligation_id} "
            f"to exclude zero values."
        )
        delta = f"assert {guard_str}, 'Precondition violated'"
    else:
        kind = RepairKind.DELEGATE_TO_ORACLE
        confidence = GuideConfidence.LOW
        description = (
            f"Unable to automatically classify repair for "
            f"{countermodel.obligation_id}. Delegating to oracle."
        )
        delta = f"# TODO: oracle should inspect model {countermodel.model_id}"

    mutation_kind = context.get("mutation_kind", "unknown")
    priority = {
        "delete": RepairPriority.CRITICAL,
        "assign": RepairPriority.HIGH,
        "augmented_assign": RepairPriority.NORMAL,
    }.get(str(mutation_kind), RepairPriority.NORMAL)

    guide_id = f"guide_{hashlib.sha1(countermodel.model_id.encode()).hexdigest()[:10]}"
    cost = REPAIR_KIND_COSTS.get(kind.name, 5)

    return RepairGuide(
        guide_id=guide_id,
        obligation_id=countermodel.obligation_id,
        countermodel_id=countermodel.model_id,
        kind=kind,
        confidence=confidence,
        priority=priority,
        description=description,
        suggested_code_delta=delta,
        estimated_cost=cost,
        trust=countermodel.trust,
    )


def build_repair_from_countermodel(
    model: MutationCountermodel,
    obligation: Any,
) -> CountermodelAsGuide:
    """Build a full set of repair guides from a countermodel.

    Args:
        model: The countermodel.
        obligation: The mutation obligation that was violated.

    Returns:
        A CountermodelAsGuide with ranked guides.
    """
    if hasattr(obligation, "mutation_kind"):
        mut_kind = obligation.mutation_kind
    elif isinstance(obligation, dict):
        mut_kind = obligation.get("mutation_kind", "unknown")
    else:
        mut_kind = "unknown"

    context = {"mutation_kind": mut_kind}
    primary_guide = extract_repair_guide(model, context)

    alternatives: List[RepairGuide] = [primary_guide]

    for rk in [
        RepairKind.INTRODUCE_COPY,
        RepairKind.WEAKEN_POSTCONDITION,
        RepairKind.CHANGE_MUTATION_ORDER,
    ]:
        if len(alternatives) >= MAX_GUIDES_PER_VIOLATION:
            break
        alt_id = f"guide_alt_{rk.name}_{hashlib.md5(model.model_id.encode()).hexdigest()[:6]}"
        alt = RepairGuide(
            guide_id=alt_id,
            obligation_id=model.obligation_id,
            countermodel_id=model.model_id,
            kind=rk,
            confidence=GuideConfidence.LOW,
            priority=RepairPriority.DEFERRED,
            description=f"Alternative repair: {rk.value} for {model.obligation_id}",
            suggested_code_delta=f"# {rk.value}: see model {model.model_id}",
            estimated_cost=REPAIR_KIND_COSTS.get(rk.name, 5),
            trust=model.trust.demote(),
        )
        alternatives.append(alt)

    ranked_guides = rank_repair_guides(tuple(alternatives), context)
    ranking = tuple(range(len(ranked_guides)))

    cas_id = f"cas_{hashlib.sha1(model.model_id.encode()).hexdigest()[:10]}"
    return CountermodelAsGuide(
        cas_id=cas_id,
        countermodel=model,
        guides=ranked_guides,
        ranking=ranking,
        trust=model.trust,
    )


def rank_repair_guides(
    guides: Tuple[RepairGuide, ...],
    context: dict,
) -> Tuple[RepairGuide, ...]:
    """Rank repair guides by expected effectiveness.

    Args:
        guides: The guides to rank.
        context: Optional context for tie-breaking.

    Returns:
        A new tuple sorted from best to worst.
    """
    confidence_order = {
        GuideConfidence.CONCLUSIVE: 0,
        GuideConfidence.HIGH: 1,
        GuideConfidence.MEDIUM: 2,
        GuideConfidence.LOW: 3,
    }
    priority_order = {
        RepairPriority.CRITICAL: 0,
        RepairPriority.HIGH: 1,
        RepairPriority.NORMAL: 2,
        RepairPriority.LOW: 3,
        RepairPriority.DEFERRED: 4,
    }

    def key(g: RepairGuide) -> Tuple[int, int, int, str]:
        return (
            confidence_order.get(g.confidence, 9),
            priority_order.get(g.priority, 9),
            g.estimated_cost,
            g.kind.value,
        )

    return tuple(sorted(guides, key=key))


def classify_countermodel_pattern(
    model: MutationCountermodel,
) -> CountermodelInterpretation:
    """Classify how a countermodel should be interpreted.

    Args:
        model: The countermodel to classify.

    Returns:
        A CountermodelInterpretation value.
    """
    if not model.path_condition or model.path_condition.strip() == "false":
        return CountermodelInterpretation.SPURIOUS

    if len(model.variable_assignments) < 2:
        return CountermodelInterpretation.UNDERDETERMINED

    has_symbolic = any(
        isinstance(v, str) and "?" in v
        for v in model.variable_assignments.values()
    )
    if has_symbolic:
        return CountermodelInterpretation.POTENTIAL_VIOLATION

    return CountermodelInterpretation.CONCRETE_VIOLATION


def extract_minimal_counterexample(model: MutationCountermodel) -> dict:
    """Extract the minimal subset of variable assignments needed to exhibit the violation.

    Args:
        model: The full countermodel.

    Returns:
        A dict with only the relevant variable assignments.
    """
    minimal: Dict[str, Any] = {}
    formula = model.violated_formula

    for var, val in model.variable_assignments.items():
        if var in formula:
            minimal[var] = val

    if not minimal:
        minimal = dict(model.variable_assignments)

    return {
        "model_id": model.model_id,
        "minimal_assignments": minimal,
        "violated_formula": model.violated_formula,
        "explanation": (
            f"Minimal counterexample has {len(minimal)} of "
            f"{len(model.variable_assignments)} total assignments."
        ),
    }


def estimate_repair_cost(guide: RepairGuide) -> int:
    """Estimate the total cost of applying a repair guide.

    Args:
        guide: The repair guide.

    Returns:
        An integer cost estimate.
    """
    base = REPAIR_KIND_COSTS.get(guide.kind.name, 5)
    trust_mult = {
        TrustTier.PROOF_BACKED: 1,
        TrustTier.RUNTIME_WITNESSED: 1,
        TrustTier.VERIFIED: 2,
        TrustTier.REVIEWED: 3,
        TrustTier.PROPOSAL: 4,
    }.get(guide.trust, 2)
    return base * trust_mult


def build_repair_judgment(
    guide: RepairGuide,
    model: MutationCountermodel,
) -> Judgment:
    """Build a Judgment recording the validity of a repair guide.

    Args:
        guide: The repair guide.
        model: The countermodel that triggered the guide.

    Returns:
        A Judgment — never a boolean.
    """
    interpretation = classify_countermodel_pattern(model)
    is_concrete = interpretation == CountermodelInterpretation.CONCRETE_VIOLATION
    trust = guide.trust if is_concrete else guide.trust.demote()

    evidence = (
        {
            "kind": EvidenceItemKind.ORACLE_PROPOSAL.value,
            "guide_id": guide.guide_id,
            "kind_str": guide.kind.value,
            "confidence": guide.confidence.value,
            "model_interpretation": interpretation.value,
        },
    )
    return Judgment(
        context={
            "guide_id": guide.guide_id,
            "obligation_id": guide.obligation_id,
            "model_id": model.model_id,
        },
        formula=f"(repair-valid {guide.guide_id} {model.model_id})",
        assumptions=(model.path_condition,),
        evidence=evidence,
        obligations=() if is_concrete else (f"(verify-repair {guide.guide_id})",),
        burden="oracle" if is_concrete else "human",
        trust=trust,
        provenance=f"build_repair_judgment:{guide.guide_id}",
    )


def check_guide_applicability(
    guide: RepairGuide,
    code_context: dict,
) -> bool:
    """Check whether a repair guide is applicable in the given code context.

    Args:
        guide: The repair guide to check.
        code_context: Dict with 'function_name', 'defined_vars', 'language'.

    Returns:
        True if the guide is applicable.
    """
    language = code_context.get("language", "python")
    if language != "python":
        return False

    if guide.kind == RepairKind.ADD_LOCK:
        imports = code_context.get("imports", [])
        if "threading" not in imports and "asyncio" not in imports:
            return False

    return True


def summarize_repair_options(cas: CountermodelAsGuide) -> str:
    """Produce a human-readable summary of all repair options.

    Args:
        cas: The CountermodelAsGuide bundle.

    Returns:
        A multi-line string describing all guides.
    """
    lines = [
        f"Repair options for obligation: {cas.countermodel.obligation_id}",
        f"  Countermodel: {cas.countermodel.model_id}",
        f"  Interpretation: {cas.countermodel.interpretation.value}",
        f"  Violated: {cas.countermodel.violated_formula}",
        f"",
        f"  Guides ({len(cas.guides)} total):",
    ]
    for guide, rank in zip(cas.guides, cas.ranking):
        lines.append(
            f"    [{rank}] {guide.kind.value} "
            f"(confidence={guide.confidence.value}, "
            f"priority={guide.priority.value}, "
            f"cost={guide.estimated_cost})"
        )
        lines.append(f"        {guide.description}")
    return "\n".join(lines)


def generate_code_delta(guide: RepairGuide, source: str) -> str:
    """Generate a suggested code change based on a repair guide and source.

    Args:
        guide: The repair guide.
        source: The Python source code to modify.

    Returns:
        A string containing the modified source or a diff-style annotation.
    """
    obligation_hint = (
        guide.obligation_id.split("_")[1] if "_" in guide.obligation_id else guide.obligation_id
    )

    lines = source.splitlines()
    insertion_line = 0
    for i, line in enumerate(lines):
        if obligation_hint in line or guide.obligation_id in line:
            insertion_line = i
            break

    indent = re.match(r"^(\s*)", lines[insertion_line]).group(1) if lines else ""
    delta_lines = guide.suggested_code_delta.splitlines()
    indented_delta = [f"{indent}{dl}" for dl in delta_lines]

    before = lines[:insertion_line]
    after = lines[insertion_line:]
    result = before + [f"{indent}# REPAIR({guide.kind.value}):"] + indented_delta + after
    return "\n".join(result)


class CountermodelDatabase:
    """Stores countermodels for reuse and deduplication."""

    def __init__(self) -> None:
        """Initialise an empty countermodel database."""
        self._by_id: Dict[str, MutationCountermodel] = {}
        self._by_obligation: Dict[str, List[str]] = collections.defaultdict(list)
        self._created_at: float = time.time()

    def store(self, model: MutationCountermodel) -> None:
        """Store a countermodel."""
        self._by_id[model.model_id] = model
        self._by_obligation[model.obligation_id].append(model.model_id)
        log.debug("CountermodelDatabase: stored %s", model.model_id)

    def get(self, model_id: str) -> Optional[MutationCountermodel]:
        """Retrieve a countermodel by ID."""
        return self._by_id.get(model_id)

    def for_obligation(self, obligation_id: str) -> Tuple[MutationCountermodel, ...]:
        """Return all countermodels for a given obligation ID."""
        return tuple(
            self._by_id[mid]
            for mid in self._by_obligation.get(obligation_id, [])
            if mid in self._by_id
        )

    def all(self) -> Tuple[MutationCountermodel, ...]:
        """Return all stored countermodels."""
        return tuple(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def __repr__(self) -> str:
        return f"CountermodelDatabase(count={len(self)})"


class RepairHistory:
    """Tracks which repairs have been attempted and their outcomes."""

    def __init__(self) -> None:
        """Initialise an empty repair history."""
        self._attempts: List[Dict[str, Any]] = []

    def record_attempt(
        self,
        guide: RepairGuide,
        outcome: str,
        notes: str = "",
    ) -> None:
        """Record a repair attempt.

        Args:
            guide: The guide that was applied.
            outcome: One of 'success', 'failure', 'partial', 'skipped'.
            notes: Optional notes about the attempt.
        """
        self._attempts.append(
            {
                "guide_id": guide.guide_id,
                "obligation_id": guide.obligation_id,
                "kind": guide.kind.value,
                "outcome": outcome,
                "notes": notes,
                "timestamp": time.time(),
            }
        )

    def was_attempted(self, guide_id: str) -> bool:
        """Return True if the guide with guide_id was previously attempted."""
        return any(a["guide_id"] == guide_id for a in self._attempts)

    def successful_guides(self) -> Tuple[str, ...]:
        """Return guide IDs that were applied successfully."""
        return tuple(a["guide_id"] for a in self._attempts if a["outcome"] == "success")

    def to_json(self) -> str:
        """Serialise the history to JSON."""
        return json.dumps(self._attempts, indent=2)

    def __len__(self) -> int:
        return len(self._attempts)

    def __repr__(self) -> str:
        return f"RepairHistory(attempts={len(self)})"


class RepairGuideRanker:
    """Ranks repair guides by expected effectiveness given context."""

    def __init__(self, history: Optional[RepairHistory] = None) -> None:
        """Initialise the ranker with an optional repair history."""
        self._history = history or RepairHistory()
        self._success_cache: Dict[str, float] = {}

    def _success_rate(self, kind: RepairKind) -> float:
        """Compute the empirical success rate for a repair kind from history."""
        if kind.value in self._success_cache:
            return self._success_cache[kind.value]

        successes = sum(
            1 for a in self._history._attempts
            if a["kind"] == kind.value and a["outcome"] == "success"
        )
        total = sum(
            1 for a in self._history._attempts
            if a["kind"] == kind.value
        )
        rate = successes / total if total > 0 else 0.5
        self._success_cache[kind.value] = rate
        return rate

    def rank(
        self,
        guides: Tuple[RepairGuide, ...],
        code_context: dict,
    ) -> Tuple[RepairGuide, ...]:
        """Rank guides using a combined score.

        Args:
            guides: Guides to rank.
            code_context: Context for applicability filtering.

        Returns:
            Sorted guides (best first).
        """
        confidence_weights = {
            GuideConfidence.CONCLUSIVE: 1.0,
            GuideConfidence.HIGH: 0.8,
            GuideConfidence.MEDIUM: 0.5,
            GuideConfidence.LOW: 0.2,
        }

        def score(g: RepairGuide) -> float:
            if not check_guide_applicability(g, code_context):
                return -math.inf
            sr = self._success_rate(g.kind)
            cw = confidence_weights.get(g.confidence, 0.5)
            cost = max(g.estimated_cost, 1)
            return (sr * cw) / cost

        return tuple(sorted(guides, key=score, reverse=True))

    def __repr__(self) -> str:
        return f"RepairGuideRanker(history_size={len(self._history)})"


class MutationCountermodelsRepairGuidesCoordinator:
    """Coordinates the countermodel-to-guide pipeline."""

    def __init__(
        self,
        trust: TrustTier = TrustTier.PROPOSAL,
        history: Optional[RepairHistory] = None,
    ) -> None:
        """Initialise the coordinator."""
        self._trust = trust
        self._db = CountermodelDatabase()
        self._history = history or RepairHistory()
        self._ranker = RepairGuideRanker(self._history)
        self._bundles: List[RepairBundle] = []

    def coordinate_repair_extraction(
        self,
        models: Tuple[MutationCountermodel, ...],
        obligations: Tuple[Any, ...],
        code_context: dict,
    ) -> Tuple[RepairBundle, ...]:
        """Run the full pipeline for a batch of countermodels.

        Args:
            models: Countermodels from Z3.
            obligations: Corresponding mutation obligations.
            code_context: Code context for applicability.

        Returns:
            A tuple of RepairBundle objects.
        """
        bundles: List[RepairBundle] = []
        for model in models:
            self._db.store(model)
            matching_ob = next(
                (ob for ob in obligations if getattr(ob, "obligation_id", None) == model.obligation_id),
                None,
            )
            cas = build_repair_from_countermodel(model, matching_ob or {})
            ranked_guides = self._ranker.rank(cas.guides, code_context)
            selected = ranked_guides[0] if ranked_guides else None
            bundle = self.emit_repair_bundle(cas, selected)
            bundles.append(bundle)
            self._bundles.append(bundle)

        return tuple(bundles)

    def process_countermodel(
        self,
        model: MutationCountermodel,
        obligation: Any,
    ) -> CountermodelAsGuide:
        """Process a single countermodel into a CountermodelAsGuide."""
        self._db.store(model)
        return build_repair_from_countermodel(model, obligation)

    def select_best_guide(
        self,
        cas: CountermodelAsGuide,
        code_context: dict,
    ) -> Optional[RepairGuide]:
        """Select the best repair guide from a CountermodelAsGuide."""
        ranked = self._ranker.rank(cas.guides, code_context)
        return ranked[0] if ranked else None

    def emit_repair_bundle(
        self,
        cas: CountermodelAsGuide,
        selected: Optional[RepairGuide],
    ) -> RepairBundle:
        """Emit a RepairBundle from a CountermodelAsGuide and selected guide."""
        bundle_id = f"bundle_{hashlib.sha1(cas.cas_id.encode()).hexdigest()[:10]}"
        trust = selected.trust if selected else cas.trust.demote()
        return RepairBundle(
            bundle_id=bundle_id,
            obligation_id=cas.countermodel.obligation_id,
            cas=cas,
            selected_guide=selected,
            trust=trust,
        )

    def __repr__(self) -> str:
        return (
            f"MutationCountermodelsRepairGuidesCoordinator("
            f"db={len(self._db)}, bundles={len(self._bundles)})"
        )


class MutationCountermodelsRepairGuidesAnalyzer:
    """Analyses countermodels to extract violation insights."""

    def __init__(self) -> None:
        """Initialise the analyser."""
        self._cache: Dict[str, CountermodelInterpretation] = {}

    def analyze_violation_pattern(self, model: MutationCountermodel) -> Dict[str, Any]:
        """Analyse the violation pattern in a countermodel."""
        trace = _extract_variable_trace(model)
        pattern = classify_countermodel_pattern(model)
        return {
            "pattern": pattern.value,
            "variables": list(trace["assignments"].keys()),
            "path_depth": len(trace["path_conjuncts"]),
            "is_ground": pattern == CountermodelInterpretation.CONCRETE_VIOLATION,
        }

    def classify_countermodel(self, model: MutationCountermodel) -> CountermodelInterpretation:
        """Classify a countermodel, using cache."""
        if model.model_id not in self._cache:
            self._cache[model.model_id] = classify_countermodel_pattern(model)
        return self._cache[model.model_id]

    def extract_minimal_violation(self, model: MutationCountermodel) -> dict:
        """Extract the minimal counterexample."""
        return extract_minimal_counterexample(model)

    def compute_repair_scope(self, model: MutationCountermodel) -> Tuple[str, ...]:
        """Compute the scope of files/functions that need repair."""
        obligation_prefix = (
            model.obligation_id.split("_")[1] if "_" in model.obligation_id else ""
        )
        scopes: List[str] = []
        if obligation_prefix:
            scopes.append(f"function:{obligation_prefix}")
        for var in model.variable_assignments:
            if "." in var:
                module = var.split(".")[0]
                scopes.append(f"module:{module}")
        return tuple(set(scopes))

    def __repr__(self) -> str:
        return f"MutationCountermodelsRepairGuidesAnalyzer(cached={len(self._cache)})"


class MutationCountermodelsRepairGuidesWitness:
    """Witnesses repair guide applicability and completeness."""

    def __init__(self, trust: TrustTier = TrustTier.REVIEWED) -> None:
        """Initialise the witness producer."""
        self._trust = trust

    def witness_guide_validity(
        self,
        guide: RepairGuide,
        model: MutationCountermodel,
    ) -> Judgment:
        """Produce a witness that a repair guide is valid for the countermodel."""
        interp = classify_countermodel_pattern(model)
        evidence = (
            {
                "kind": EvidenceItemKind.ORACLE_PROPOSAL.value,
                "guide_id": guide.guide_id,
                "model_id": model.model_id,
                "interpretation": interp.value,
            },
        )
        trust = (
            self._trust
            if interp == CountermodelInterpretation.CONCRETE_VIOLATION
            else self._trust.demote()
        )
        return Judgment(
            context={"guide_id": guide.guide_id, "model_id": model.model_id},
            formula=f"(guide-valid {guide.guide_id})",
            assumptions=(f"(interpretation {interp.value})",),
            evidence=evidence,
            obligations=(),
            burden="oracle",
            trust=trust,
            provenance="witness_guide_validity",
        )

    def witness_repair_completeness(self, bundle: RepairBundle) -> Judgment:
        """Witness that a repair bundle is complete (covers the violation)."""
        n_guides = len(bundle.cas.guides)
        has_selected = bundle.selected_guide is not None
        trust = self._trust if has_selected else self._trust.demote()
        evidence = (
            {
                "kind": EvidenceItemKind.ORACLE_PROPOSAL.value,
                "bundle_id": bundle.bundle_id,
                "n_guides": n_guides,
                "selected": bundle.selected_guide.guide_id if has_selected else None,
            },
        )
        return Judgment(
            context={"bundle_id": bundle.bundle_id},
            formula=f"(bundle-complete {bundle.bundle_id})",
            assumptions=(),
            evidence=evidence,
            obligations=() if has_selected else (f"(select-guide {bundle.bundle_id})",),
            burden="none" if has_selected else "oracle",
            trust=trust,
            provenance="witness_repair_completeness",
        )

    def emit_repair_witness(
        self,
        bundles: Tuple[RepairBundle, ...],
    ) -> Tuple[Judgment, ...]:
        """Emit witnesses for a batch of repair bundles."""
        return tuple(self.witness_repair_completeness(b) for b in bundles)

    def __repr__(self) -> str:
        return f"MutationCountermodelsRepairGuidesWitness(trust={self._trust.name})"


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log.info("=== s05 smoke test: mutation countermodels as repair guides ===")

    t1 = TrustTier.PROPOSAL
    t3 = TrustTier.VERIFIED
    assert t1.join(t3) == TrustTier.VERIFIED
    assert t3.meet(t1) == TrustTier.PROPOSAL
    assert t3.promote() == TrustTier.RUNTIME_WITNESSED
    assert t1.demote() == TrustTier.PROPOSAL
    assert not t1.is_admissible(TrustTier.VERIFIED)
    log.info("TrustTier: OK")

    model = MutationCountermodel(
        model_id="mdl_abc123",
        obligation_id="mut_assign_foo_0_aabbcc",
        variable_assignments={"x": 0, "y": None, "z": 42},
        path_condition="(> x 0) && (is-defined y)",
        violated_formula="(= y_after 100)",
        interpretation=CountermodelInterpretation.CONCRETE_VIOLATION,
        trust=TrustTier.REVIEWED,
    )

    interp = classify_countermodel_pattern(model)
    assert interp == CountermodelInterpretation.CONCRETE_VIOLATION
    log.info("classify_countermodel_pattern: %s", interp.value)

    minimal = extract_minimal_counterexample(model)
    log.info("minimal: %s", minimal)

    guide = extract_repair_guide(model, {"mutation_kind": "assign"})
    assert guide.kind in (
        RepairKind.ADD_GUARD,
        RepairKind.STRENGTHEN_PRECONDITION,
        RepairKind.DELEGATE_TO_ORACLE,
    )
    log.info("extract_repair_guide: kind=%s", guide.kind.value)

    cas = build_repair_from_countermodel(
        model, {"obligation_id": model.obligation_id, "mutation_kind": "assign"}
    )
    assert len(cas.guides) > 0
    log.info("build_repair_from_countermodel: %d guides", len(cas.guides))

    ranked = rank_repair_guides(cas.guides, {"function_name": "foo"})
    log.info("rank_repair_guides: first=%s", ranked[0].kind.value if ranked else "none")

    cost = estimate_repair_cost(guide)
    assert cost > 0
    log.info("estimate_repair_cost: %d", cost)

    j = build_repair_judgment(guide, model)
    assert isinstance(j, Judgment)
    log.info("build_repair_judgment: trust=%s", j.trust.name)

    summary_str = summarize_repair_options(cas)
    assert "Repair options" in summary_str
    log.info("summarize_repair_options: OK")

    source = "def foo():\n    x = x + 1\n    return x\n"
    delta = generate_code_delta(guide, source)
    log.info("generate_code_delta: OK")

    db = CountermodelDatabase()
    db.store(model)
    assert db.get("mdl_abc123") is model
    log.info("CountermodelDatabase: OK")

    history = RepairHistory()
    history.record_attempt(guide, "success", "test")
    assert history.was_attempted(guide.guide_id)
    log.info("RepairHistory: OK")

    coord = MutationCountermodelsRepairGuidesCoordinator(trust=TrustTier.PROPOSAL)
    bundle = coord.emit_repair_bundle(cas, guide)
    assert isinstance(bundle, RepairBundle)
    log.info("Coordinator: OK, bundle_id=%s", bundle.bundle_id)

    analyser = MutationCountermodelsRepairGuidesAnalyzer()
    pattern = analyser.analyze_violation_pattern(model)
    log.info("Analyser pattern: %s", pattern)

    witness = MutationCountermodelsRepairGuidesWitness(TrustTier.REVIEWED)
    wj = witness.witness_guide_validity(guide, model)
    assert isinstance(wj, Judgment)
    log.info("witness_guide_validity: trust=%s", wj.trust.name)

    cech = CechObstruction(
        cover_id="cov_repair",
        cocycle=frozenset({"(guide_a, guide_b, conflict)"}),
        cohomology_class="[conflict] in H^1",
        description="Conflicting repair guides cannot be simultaneously applied",
    )
    assert not cech.is_trivial()
    log.info("CechObstruction: OK")

    log.info("=== s05 smoke test: ALL PASSED ===")
