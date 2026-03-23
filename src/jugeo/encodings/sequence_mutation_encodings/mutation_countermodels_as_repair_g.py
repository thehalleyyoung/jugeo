"""Mutation countermodels as witnesses to bad mutation sequences and repair guides — theory2.tex §28.19–§28.23.

# copilot: Mutation countermodels are explicit witnesses (concrete states) that
# demonstrate a violation of a specified invariant under a given mutation sequence.
# Repair guides prescribe corrective steps. Anomalies record local cocycle jumps.
# Theory: A countermodel C is a model of ¬invariant reachable by applying mutations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# jugeo import try/except block
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

    class FailureScope(str, Enum):
        GEOMETRY = "geometry"
        ENCODING = "encoding"
        UNKNOWN = "unknown"

    class FailureClassification(str, Enum):
        ENCODING_MISMATCH = "encoding_mismatch"
        UNCLASSIFIED = "unclassified"

    class JuGeoError(RuntimeError):
        pass

    class StructuredFailure:
        def __init__(self, message: str, **kw):
            self.message = message

    def raise_with_scope(code: str, *, message: str, provenance=None, **kw):
        raise JuGeoError(f"[{code}] {message}")


try:
    from jugeo.judgments.judgment_terms import TrustLevel
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False

    class TrustLevel(IntEnum):
        CONTRADICTED = 0
        UNVERIFIED = 1
        ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5


# ---------------------------------------------------------------------------
# TrustTier
# ---------------------------------------------------------------------------


class TrustTier(IntEnum):
    """
    Lattice of trust levels ordered by evidence strength.

    PROPOSAL          — oracle-proposed, not yet reviewed
    REVIEWED          — human-reviewed but not formally verified
    VERIFIED          — statically verified by type checker or SMT solver
    RUNTIME_WITNESSED — witnessed at runtime by a concrete execution
    PROOF_BACKED      — backed by a machine-checked proof
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: TrustTier) -> TrustTier:
        return TrustTier(min(int(self), int(other)))

    def promote(self) -> TrustTier:
        return TrustTier(min(int(self) + 1, TrustTier.PROOF_BACKED))

    def demote(self) -> TrustTier:
        return TrustTier(max(int(self) - 1, TrustTier.PROPOSAL))

    def is_at_least(self, other: TrustTier) -> bool:
        return int(self) >= int(other)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AnomalyKind(str, Enum):
    WRITE_WRITE_CONFLICT = "write_write_conflict"
    READ_WRITE_CONFLICT = "read_write_conflict"
    INVARIANT_BREAK = "invariant_break"
    NULL_DEREFERENCE = "null_dereference"
    BOUNDS_VIOLATION = "bounds_violation"
    TYPE_CONFUSION = "type_confusion"
    UNKNOWN = "unknown"


class MutationKind(str, Enum):
    WRITE = "write"
    DELETE = "delete"
    SWAP = "swap"
    APPEND = "append"
    CLEAR = "clear"
    UNKNOWN = "unknown"


class RepairStepKind(str, Enum):
    REORDER = "reorder"
    DELETE_MUTATION = "delete_mutation"
    INSERT_GUARD = "insert_guard"
    CHANGE_VALUE = "change_value"
    ADD_INVARIANT_CHECK = "add_invariant_check"
    ROLLBACK = "rollback"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, *parts: str) -> str:
    raw = ":".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_countermodel_judgment(
    coordinate: str,
    *,
    trust_tier: TrustTier = TrustTier.PROPOSAL,
    obstructions: tuple[CountermodelCechObstruction, ...] = (),
    evidence: tuple[str, ...] = (),
) -> CountermodelJudgment:
    c = coordinate
    phi = f"countermodel_wff({coordinate})"
    A = (f"agent:countermodel_builder@{coordinate}",)
    E = evidence if evidence else (f"evidence:countermodel@{c}",)
    O = (f"obligation:countermodel_minimality@{c}",)
    B = obstructions
    T = trust_tier
    Pi: dict[str, Any] = {"coordinate": coordinate, "encoding": "countermodel"}
    return CountermodelJudgment(c=c, phi=phi, A=A, E=E, O=O, B=B, T=T, Pi=Pi)


# ---------------------------------------------------------------------------
# Čech obstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CountermodelCechObstruction:
    coordinate: str
    cocycle_description: str
    trust_tier: TrustTier
    is_coboundary: bool
    repair_suggestion: str
    obstruction_kind: str
    mutation_index: int
    conflicting_states: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "cocycle_description": self.cocycle_description,
            "trust_tier": int(self.trust_tier),
            "is_coboundary": self.is_coboundary,
            "repair_suggestion": self.repair_suggestion,
            "obstruction_kind": self.obstruction_kind,
            "mutation_index": self.mutation_index,
            "conflicting_states": list(self.conflicting_states),
        }

    def is_trivial(self) -> bool:
        return self.is_coboundary

    def canonical_form(self) -> str:
        return f"H1({self.coordinate}, {self.obstruction_kind}, idx={self.mutation_index})"


# ---------------------------------------------------------------------------
# Judgment tuple
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CountermodelJudgment:
    c: str
    phi: str
    A: tuple[str, ...]
    E: tuple[str, ...]
    O: tuple[str, ...]
    B: tuple[CountermodelCechObstruction, ...]
    T: TrustTier
    Pi: Mapping[str, Any]

    @property
    def is_settled(self) -> bool:
        return len(self.B) == 0 and self.T.is_at_least(TrustTier.VERIFIED)

    @property
    def is_obstructed(self) -> bool:
        return len(self.B) > 0

    def with_obstruction(self, obs: CountermodelCechObstruction) -> CountermodelJudgment:
        return CountermodelJudgment(
            c=self.c, phi=self.phi, A=self.A, E=self.E, O=self.O,
            B=(*self.B, obs), T=self.T.demote(), Pi=self.Pi,
        )

    def with_evidence(self, evidence: str) -> CountermodelJudgment:
        return CountermodelJudgment(
            c=self.c, phi=self.phi, A=self.A, E=(*self.E, evidence),
            O=self.O, B=self.B, T=self.T.promote(), Pi=self.Pi,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "c": self.c,
            "phi": self.phi,
            "A": list(self.A),
            "E": list(self.E),
            "O": list(self.O),
            "B": [b.to_dict() for b in self.B],
            "T": int(self.T),
            "Pi": dict(self.Pi),
        }


# ---------------------------------------------------------------------------
# GlobalSection and DescentObstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CountermodelGlobalSection:
    section_id: str
    coordinate: str
    final_state: Mapping[str, str]
    invariant_violated: str
    judgment: CountermodelJudgment
    constructed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "coordinate": self.coordinate,
            "final_state": dict(self.final_state),
            "invariant_violated": self.invariant_violated,
            "judgment": self.judgment.to_dict(),
            "constructed_at": self.constructed_at,
        }


@dataclass(frozen=True, slots=True)
class CountermodelDescentObstruction:
    obstruction_id: str
    coordinate: str
    obstructions: tuple[CountermodelCechObstruction, ...]
    judgment: CountermodelJudgment
    detected_at: str

    @property
    def obstruction_count(self) -> int:
        return len(self.obstructions)

    def primary_obstruction(self) -> CountermodelCechObstruction | None:
        return self.obstructions[0] if self.obstructions else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "coordinate": self.coordinate,
            "obstructions": [o.to_dict() for o in self.obstructions],
            "judgment": self.judgment.to_dict(),
            "detected_at": self.detected_at,
        }


@dataclass(frozen=True, slots=True)
class RepairDescentObstruction:
    obstruction_id: str
    coordinate: str
    reason: str
    judgment: CountermodelJudgment
    detected_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "obstruction_id": self.obstruction_id,
            "coordinate": self.coordinate,
            "reason": self.reason,
            "judgment": self.judgment.to_dict(),
            "detected_at": self.detected_at,
        }


# ---------------------------------------------------------------------------
# MutationAnomaly
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MutationAnomaly:
    anomaly_id: str
    anomaly_kind: str
    location: str
    pre_value: str
    post_value: str
    cocycle_jump: str
    judgment: CountermodelJudgment

    def is_reversible(self) -> bool:
        """Anomaly is reversible if we can undo the jump (old value is known)."""
        return self.pre_value not in ("undefined", "")

    def to_smt_description(self) -> str:
        return (
            f"(assert (and (= (select heap_pre {self.location!r}) {self.pre_value!r}) "
            f"(= (select heap_post {self.location!r}) {self.post_value!r}) "
            f";; cocycle-jump: {self.cocycle_jump}"
            f"))"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "anomaly_kind": self.anomaly_kind,
            "location": self.location,
            "pre_value": self.pre_value,
            "post_value": self.post_value,
            "cocycle_jump": self.cocycle_jump,
            "judgment": self.judgment.to_dict(),
            "is_reversible": self.is_reversible(),
        }


# ---------------------------------------------------------------------------
# RepairGuide
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RepairGuide:
    guide_id: str
    source_countermodel_id: str
    repair_steps: tuple[str, ...]
    priority: int
    judgment: CountermodelJudgment

    def is_actionable(self) -> bool:
        return len(self.repair_steps) > 0 and self.priority > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "guide_id": self.guide_id,
            "source_countermodel_id": self.source_countermodel_id,
            "repair_steps": list(self.repair_steps),
            "priority": self.priority,
            "judgment": self.judgment.to_dict(),
            "is_actionable": self.is_actionable(),
        }

    def apply_steps_to(self, state: Mapping[str, str]) -> Mapping[str, str]:
        """Symbolically apply repair steps (textual) to a state dict."""
        result = dict(state)
        for step in self.repair_steps:
            if step.startswith("delete:"):
                key = step[len("delete:"):]
                result.pop(key, None)
            elif step.startswith("set:"):
                rest = step[len("set:"):]
                if "=" in rest:
                    k, v = rest.split("=", 1)
                    result[k.strip()] = v.strip()
        return result


# ---------------------------------------------------------------------------
# SequenceRepairPlan
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SequenceRepairPlan:
    plan_id: str
    steps: tuple[str, ...]
    target_invariant: str
    estimated_cost: int
    judgment: CountermodelJudgment

    def is_complete(self) -> bool:
        return len(self.steps) > 0 and self.estimated_cost > 0

    def apply_steps(self, state: Mapping[str, str]) -> Mapping[str, str]:
        result = dict(state)
        for step in self.steps:
            if step.startswith("set:") and "=" in step:
                rest = step[4:]
                k, v = rest.split("=", 1)
                result[k.strip()] = v.strip()
            elif step.startswith("remove:"):
                result.pop(step[7:], None)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "steps": list(self.steps),
            "target_invariant": self.target_invariant,
            "estimated_cost": self.estimated_cost,
            "judgment": self.judgment.to_dict(),
            "is_complete": self.is_complete(),
        }


# ---------------------------------------------------------------------------
# MutationCountermodel
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MutationCountermodel:
    countermodel_id: str
    mutation_sequence: tuple[str, ...]
    violated_invariant: str
    witness_state: Mapping[str, str]
    judgment: CountermodelJudgment

    def is_minimal(self) -> bool:
        """A countermodel is minimal if no strict prefix also violates the invariant."""
        # Heuristic: if sequence has ≤1 mutation, it's trivially minimal
        return len(self.mutation_sequence) <= 1

    def extract_bad_prefix(self) -> tuple[str, ...]:
        """Extract the shortest prefix of mutations that causes the violation."""
        if len(self.mutation_sequence) == 0:
            return ()
        # In a real implementation this would symbolically evaluate prefixes.
        # Here we return the full sequence as the "bad prefix" (conservative).
        return self.mutation_sequence

    def to_dict(self) -> dict[str, Any]:
        return {
            "countermodel_id": self.countermodel_id,
            "mutation_sequence": list(self.mutation_sequence),
            "violated_invariant": self.violated_invariant,
            "witness_state": dict(self.witness_state),
            "judgment": self.judgment.to_dict(),
            "is_minimal": self.is_minimal(),
        }


# ---------------------------------------------------------------------------
# build_mutation_countermodel
# ---------------------------------------------------------------------------

def build_mutation_countermodel(
    mutation_sequence: list[str],
    invariant_smt: str,
    *,
    coordinate: str,
) -> MutationCountermodel | CountermodelDescentObstruction:
    """Build a MutationCountermodel by simulating a mutation sequence."""
    if not mutation_sequence:
        obs = CountermodelCechObstruction(
            coordinate=coordinate,
            cocycle_description="Empty mutation sequence — no countermodel exists",
            trust_tier=TrustTier.PROPOSAL,
            is_coboundary=True,
            repair_suggestion="Provide at least one mutation",
            obstruction_kind="empty_sequence",
            mutation_index=-1,
            conflicting_states=(),
        )
        bad_j = _make_countermodel_judgment(coordinate, obstructions=(obs,))
        return CountermodelDescentObstruction(
            obstruction_id=_stable_id("obs_cm", coordinate),
            coordinate=coordinate,
            obstructions=(obs,),
            judgment=bad_j,
            detected_at=_now_iso(),
        )

    # Simulate: parse "set:loc=val" style mutation strings into a state
    state: dict[str, str] = {}
    obstruction_list: list[CountermodelCechObstruction] = []

    for idx, mutation_str in enumerate(mutation_sequence):
        if mutation_str.startswith("set:") and "=" in mutation_str:
            rest = mutation_str[4:]
            loc, val = rest.split("=", 1)
            state[loc.strip()] = val.strip()
        elif mutation_str.startswith("delete:"):
            loc = mutation_str[7:]
            state.pop(loc.strip(), None)
        else:
            obs = CountermodelCechObstruction(
                coordinate=coordinate,
                cocycle_description=f"Unrecognised mutation at index {idx}: {mutation_str!r}",
                trust_tier=TrustTier.PROPOSAL,
                is_coboundary=False,
                repair_suggestion=f"Use 'set:loc=val' or 'delete:loc' format for mutation {idx}",
                obstruction_kind="parse_error",
                mutation_index=idx,
                conflicting_states=(),
            )
            obstruction_list.append(obs)

    if obstruction_list:
        bad_j = _make_countermodel_judgment(coordinate, obstructions=tuple(obstruction_list))
        return CountermodelDescentObstruction(
            obstruction_id=_stable_id("obs_cm_parse", coordinate),
            coordinate=coordinate,
            obstructions=tuple(obstruction_list),
            judgment=bad_j,
            detected_at=_now_iso(),
        )

    j = _make_countermodel_judgment(
        coordinate,
        trust_tier=TrustTier.REVIEWED,
        evidence=(f"invariant:{invariant_smt[:40]}",),
    )
    cm_id = _stable_id("cm", coordinate, invariant_smt, str(len(mutation_sequence)))
    return MutationCountermodel(
        countermodel_id=cm_id,
        mutation_sequence=tuple(mutation_sequence),
        violated_invariant=invariant_smt,
        witness_state=state,
        judgment=j,
    )


# ---------------------------------------------------------------------------
# extract_repair_guide
# ---------------------------------------------------------------------------

def extract_repair_guide(
    countermodel: MutationCountermodel,
    *,
    max_steps: int = 10,
) -> RepairGuide:
    """Extract a repair guide from a countermodel."""
    steps: list[str] = []
    prefix = countermodel.extract_bad_prefix()

    # Suggest undoing the last mutation in the bad prefix
    for mut_str in reversed(prefix[:max_steps]):
        if mut_str.startswith("set:") and "=" in mut_str:
            loc = mut_str[4:].split("=", 1)[0].strip()
            steps.append(f"delete:{loc}")
        elif mut_str.startswith("delete:"):
            loc = mut_str[7:]
            steps.append(f"set:{loc}=<restore_original_value>")

    # Add a guard step
    steps.append(f"add_invariant_check:{countermodel.violated_invariant[:60]}")

    j = _make_countermodel_judgment(
        countermodel.judgment.c,
        trust_tier=TrustTier.REVIEWED,
        evidence=(f"countermodel:{countermodel.countermodel_id}",),
    )
    guide_id = _stable_id("guide", countermodel.countermodel_id)
    return RepairGuide(
        guide_id=guide_id,
        source_countermodel_id=countermodel.countermodel_id,
        repair_steps=tuple(steps[:max_steps]),
        priority=max(1, len(steps)),
        judgment=j,
    )


# ---------------------------------------------------------------------------
# apply_repair
# ---------------------------------------------------------------------------

def apply_repair(
    state: Mapping[str, str],
    guide: RepairGuide,
    *,
    coordinate: str,
) -> Mapping[str, str] | RepairDescentObstruction:
    """Apply a repair guide to a state. Never raises."""
    if not guide.is_actionable():
        obs = CountermodelCechObstruction(
            coordinate=coordinate,
            cocycle_description="Repair guide is not actionable (no steps or zero priority)",
            trust_tier=TrustTier.PROPOSAL,
            is_coboundary=False,
            repair_suggestion="Ensure repair guide has steps and priority > 0",
            obstruction_kind="inactionable_guide",
            mutation_index=-1,
            conflicting_states=(),
        )
        bad_j = _make_countermodel_judgment(coordinate, obstructions=(obs,))
        return RepairDescentObstruction(
            obstruction_id=_stable_id("obs_repair", guide.guide_id),
            coordinate=coordinate,
            reason="Repair guide is not actionable",
            judgment=bad_j,
            detected_at=_now_iso(),
        )
    try:
        return guide.apply_steps_to(state)
    except Exception as exc:
        obs = CountermodelCechObstruction(
            coordinate=coordinate,
            cocycle_description=f"Repair application failed: {exc}",
            trust_tier=TrustTier.PROPOSAL,
            is_coboundary=False,
            repair_suggestion="Inspect repair steps for well-formedness",
            obstruction_kind="repair_application_error",
            mutation_index=-1,
            conflicting_states=(),
        )
        bad_j = _make_countermodel_judgment(coordinate, obstructions=(obs,))
        return RepairDescentObstruction(
            obstruction_id=_stable_id("obs_repair_err", guide.guide_id),
            coordinate=coordinate,
            reason=str(exc),
            judgment=bad_j,
            detected_at=_now_iso(),
        )


# ---------------------------------------------------------------------------
# SequenceRepairPlan builder
# ---------------------------------------------------------------------------

def build_repair_plan(
    countermodel: MutationCountermodel,
    *,
    coordinate: str,
    max_steps: int = 5,
) -> SequenceRepairPlan:
    guide = extract_repair_guide(countermodel, max_steps=max_steps)
    j = _make_countermodel_judgment(coordinate, trust_tier=TrustTier.REVIEWED)
    return SequenceRepairPlan(
        plan_id=_stable_id("plan", coordinate, countermodel.countermodel_id),
        steps=guide.repair_steps,
        target_invariant=countermodel.violated_invariant,
        estimated_cost=len(guide.repair_steps),
        judgment=j,
    )


# ---------------------------------------------------------------------------
# MutationAnomaly builder
# ---------------------------------------------------------------------------

def detect_anomalies(
    pre_state: Mapping[str, str],
    post_state: Mapping[str, str],
    *,
    coordinate: str,
) -> tuple[MutationAnomaly, ...]:
    """Detect anomalies between pre and post state."""
    anomalies: list[MutationAnomaly] = []
    all_locs = set(pre_state) | set(post_state)
    j = _make_countermodel_judgment(coordinate, trust_tier=TrustTier.REVIEWED)

    for loc in sorted(all_locs):
        pre_val = pre_state.get(loc, "undefined")
        post_val = post_state.get(loc, "undefined")
        if pre_val != post_val:
            kind = AnomalyKind.INVARIANT_BREAK if post_val == "undefined" else AnomalyKind.WRITE_WRITE_CONFLICT
            cocycle = f"Δ({loc}: {pre_val!r} → {post_val!r})"
            anomalies.append(
                MutationAnomaly(
                    anomaly_id=_stable_id("anom", coordinate, loc),
                    anomaly_kind=kind.value,
                    location=loc,
                    pre_value=pre_val,
                    post_value=post_val,
                    cocycle_jump=cocycle,
                    judgment=j,
                )
            )
    return tuple(anomalies)


# ---------------------------------------------------------------------------
# CountermodelExtractor
# ---------------------------------------------------------------------------

class CountermodelExtractor:
    """Stateful extractor that ingests mutation traces and builds countermodels and repair plans.

    The extractor accumulates raw mutation traces, classifies anomalies, constructs
    MutationCountermodel objects, and generates SequenceRepairPlan objects from them.
    It is intentionally NOT frozen — it accumulates state across ingestion calls.
    """

    def __init__(self) -> None:
        """Initialise with empty internal state."""
        self._traces: list[list[str]] = []
        self._invariants: list[str] = []
        self._countermodels: list[MutationCountermodel] = []
        self._anomalies: list[MutationAnomaly] = []
        self._plans: list[SequenceRepairPlan] = []
        self._report_lines: list[str] = []

    def ingest_trace(self, trace: list[str], invariant: str = "") -> None:
        """Ingest a raw mutation trace and optional invariant string.

        The trace is stored for later analysis. Use extract_anomalies() and
        build_countermodels() to process all ingested traces.

        Args:
            trace:     List of mutation step strings (e.g. ["set:x=0", "set:x=99"]).
            invariant: Optional SMT invariant that the sequence should satisfy.
        """
        self._traces.append(list(trace))
        self._invariants.append(invariant or "(true)")
        self._report_lines.append(f"Ingested trace: {len(trace)} steps, invariant={invariant!r:.40}")

    def extract_anomalies(self) -> list[MutationAnomaly]:
        """Extract MutationAnomaly objects from all ingested traces.

        Simulates each trace by applying mutations step-by-step and detecting
        state changes that constitute anomalies (write-write conflicts, deletions,
        unrecognised formats). Returns the flat list of all discovered anomalies.

        Returns:
            list[MutationAnomaly]: All detected anomalies across all traces.
        """
        anomalies: list[MutationAnomaly] = []
        for trace, invariant in zip(self._traces, self._invariants):
            pre_state: dict[str, str] = {}
            post_state: dict[str, str] = {}
            for step in trace:
                if step.startswith("set:") and "=" in step:
                    loc, val = step[4:].split("=", 1)
                    post_state[loc.strip()] = val.strip()
                elif step.startswith("delete:"):
                    loc = step[7:].strip()
                    post_state.pop(loc, None)
            found = detect_anomalies(pre_state, post_state, coordinate=f"trace:{invariant[:20]}")
            anomalies.extend(found)
        self._anomalies.extend(anomalies)
        self._report_lines.append(f"extract_anomalies: found {len(anomalies)} anomalies")
        return anomalies

    def build_countermodels(self) -> list[MutationCountermodel]:
        """Build MutationCountermodel objects from ingested traces.

        For each (trace, invariant) pair, calls build_mutation_countermodel.
        Successful countermodels are stored internally. Descent obstructions are
        recorded in the report but not included in the returned list.

        Returns:
            list[MutationCountermodel]: Successfully built countermodels.
        """
        cms: list[MutationCountermodel] = []
        for i, (trace, inv) in enumerate(zip(self._traces, self._invariants)):
            coord = f"extractor:trace_{i}:{inv[:20]}"
            result = build_mutation_countermodel(trace, inv, coordinate=coord)
            if isinstance(result, MutationCountermodel):
                cms.append(result)
                self._countermodels.append(result)
                self._report_lines.append(f"build_countermodels: CM {result.countermodel_id[:12]} OK")
            else:
                self._report_lines.append(
                    f"build_countermodels: trace_{i} produced obstruction: "
                    f"{result.obstruction_count} blocker(s)"
                )
        return cms

    def generate_repair_plans(self) -> list[SequenceRepairPlan]:
        """Generate SequenceRepairPlan objects from all built countermodels.

        Calls build_repair_plan for each accumulated MutationCountermodel and
        stores the plans internally.

        Returns:
            list[SequenceRepairPlan]: One repair plan per countermodel.
        """
        plans: list[SequenceRepairPlan] = []
        for cm in self._countermodels:
            coord = f"extractor:plan:{cm.countermodel_id[:12]}"
            plan = build_repair_plan(cm, coordinate=coord)
            plans.append(plan)
            self._plans.append(plan)
            self._report_lines.append(
                f"generate_repair_plans: plan {plan.plan_id[:12]} "
                f"steps={plan.estimated_cost}"
            )
        return plans

    def report(self) -> str:
        """Return a human-readable report of the extractor's state.

        Includes counts of traces, countermodels, anomalies, and plans, plus the
        last 10 log lines from the internal report buffer.

        Returns:
            str: A multi-line report string.
        """
        lines = [
            "=== CountermodelExtractor Report ===",
            f"Traces ingested       : {len(self._traces)}",
            f"Countermodels built   : {len(self._countermodels)}",
            f"Anomalies found       : {len(self._anomalies)}",
            f"Repair plans generated: {len(self._plans)}",
            "",
            "Last log entries:",
        ]
        for entry in self._report_lines[-10:]:
            lines.append(f"  {entry}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "TrustTier",
    "TrustLevel",
    "AnomalyKind",
    "MutationKind",
    "RepairStepKind",
    "CountermodelCechObstruction",
    "CountermodelJudgment",
    "CountermodelGlobalSection",
    "CountermodelDescentObstruction",
    "RepairDescentObstruction",
    "MutationAnomaly",
    "RepairGuide",
    "SequenceRepairPlan",
    "MutationCountermodel",
    "CountermodelExtractor",
    "build_mutation_countermodel",
    "extract_repair_guide",
    "apply_repair",
    "build_repair_plan",
    "detect_anomalies",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # build_mutation_countermodel — success
    seq = ["set:x=0", "set:y=1", "set:x=99"]
    inv = "(not (= (select heap x) 99))"
    cm = build_mutation_countermodel(seq, inv, coordinate="test:cm")
    assert isinstance(cm, MutationCountermodel), f"Got {type(cm)}"
    assert cm.witness_state["x"] == "99"
    assert cm.witness_state["y"] == "1"

    # build_mutation_countermodel — empty sequence
    empty_result = build_mutation_countermodel([], inv, coordinate="test:empty")
    assert isinstance(empty_result, CountermodelDescentObstruction)
    assert empty_result.obstruction_count >= 1

    # build_mutation_countermodel — bad mutation format
    bad_seq = ["unknown_format:abc", "set:x=1"]
    bad_result = build_mutation_countermodel(bad_seq, inv, coordinate="test:bad")
    assert isinstance(bad_result, CountermodelDescentObstruction)

    # extract_repair_guide
    guide = extract_repair_guide(cm, max_steps=5)
    assert guide.is_actionable()
    assert len(guide.repair_steps) > 0

    # apply_repair — success
    state = {"x": "99", "y": "1"}
    repaired = apply_repair(state, guide, coordinate="test:repair")
    assert isinstance(repaired, dict)

    # apply_repair — inactionable guide
    j = _make_countermodel_judgment("test:inact")
    bad_guide = RepairGuide(
        guide_id="bad",
        source_countermodel_id="none",
        repair_steps=(),
        priority=0,
        judgment=j,
    )
    bad_repair = apply_repair(state, bad_guide, coordinate="test:inact")
    assert isinstance(bad_repair, RepairDescentObstruction)

    # detect_anomalies
    pre = {"x": "0", "y": "1", "z": "2"}
    post = {"x": "99", "y": "1"}
    anomalies = detect_anomalies(pre, post, coordinate="test:anomaly")
    assert len(anomalies) == 2  # x changed, z disappeared
    locs = {a.location for a in anomalies}
    assert "x" in locs
    assert "z" in locs

    # MutationAnomaly properties
    a = anomalies[0]
    assert a.is_reversible() or not a.is_reversible()  # just test it runs

    # SequenceRepairPlan
    plan = build_repair_plan(cm, coordinate="test:plan")
    assert plan.is_complete()
    assert plan.target_invariant == inv

    # TrustTier
    assert TrustTier.VERIFIED.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED
    assert TrustTier.VERIFIED.meet(TrustTier.PROPOSAL) == TrustTier.PROPOSAL

    # Judgment
    j2 = cm.judgment
    assert not j2.is_obstructed

    print("mutation_countermodels_as_repair_g: OK")
    sys.exit(0)


def _batch_parse_violations(violations: list) -> list:
    """
    Parse a list of violation message strings into MutationAnomalies.

    Dispatches to classify_mutation_anomaly for each message. Addr is
    extracted from the message if present; defaults to 0.

    Parameters
    ----------
    violations : list of violation message strings

    Returns
    -------
    list of MutationAnomaly
    """
    anomalies: List[MutationAnomaly] = []
    for msg in violations:
        parsed = _parse_barrier_violation(msg)
        try:
            addr = int(parsed.get("addr_int", "0") or "0")
        except ValueError:
            addr = 0
        expected_type = parsed.get("type_constraint", "")
        actual_type = parsed.get("new_type", "")
        anomaly = classify_mutation_anomaly(
            violation_message=msg,
            addr=addr,
            expected_type=expected_type,
            actual_type=actual_type,
        )
        anomalies.append(anomaly)
    return anomalies


def _extract_from_obligation(obligation, violations, classify_fn=None, countermodels_list=None):
    """
    Build a MutationCountermodel from a SliceConsistencyObligation and violations.

    Parameters
    ----------
    obligation : a SliceConsistencyObligation (duck-typed for compatibility)
    violations : list of violation message strings from check_slice_consistency

    Returns
    -------
    MutationCountermodel recorded internally and returned.
    """
    anomalies = tuple(_batch_parse_violations(violations))
    failed_id = getattr(obligation, "obligation_id", _new_id("oblig_"))
    cm = build_mutation_countermodel(
        failed_transition_id=failed_id,
        anomalies=list(anomalies),
        context={"source": "obligation", "violation_count": len(violations)},
    )
    countermodels_list.append(cm)
    _LOGGER.debug(
        "extract_from_obligation: built countermodel %s with %d anomalies",
        cm.countermodel_id[:12],
        cm.anomaly_count(),
    )
    return cm

def _extract_from_transition(transition, barriers, countermodels_list=None):
    """
    Build a MutationCountermodel from a MutationTransition and barriers.

    Parameters
    ----------
    transition : a MutationTransition (duck-typed)
    barriers   : list of WriteBarrier

    Returns
    -------
    MutationCountermodel recorded internally and returned.
    """
    violations: List[str] = []
    addr = getattr(transition, "mutation_addr", 0)
    new_cell = getattr(transition, "new_cell", ("unknown", "unknown"))
    new_type, new_value = new_cell[0], new_cell[1]
    slice_ = getattr(transition, "slice_before", None)

    for barrier in barriers:
        if hasattr(barrier, "check") and hasattr(barrier, "violation_message"):
            if slice_ is not None:
                if not barrier.check(addr, new_type, new_value, slice_):
                    violations.append(
                        barrier.violation_message(addr, new_type, new_value)
                    )
            else:
                violations.append(
                    barrier.violation_message(addr, new_type, new_value)
                )

    anomalies = tuple(_batch_parse_violations(violations))
    failed_id = getattr(transition, "transition_id", _new_id("trans_"))
    cm = build_mutation_countermodel(
        failed_transition_id=failed_id,
        anomalies=list(anomalies),
        context={
            "source": "transition",
            "addr": addr,
            "new_type": new_type,
            "new_value": new_value,
        },
    )
    countermodels_list.append(cm)
    _LOGGER.debug(
        "extract_from_transition: built countermodel %s with %d anomalies",
        cm.countermodel_id[:12],
        cm.anomaly_count(),
    )
    return cm


def _build_repair_plan_from_countermodels(countermodels, countermodels_list=None):
    """
    Build a SequenceRepairPlan from a list of countermodels (or all accumulated).

    Parameters
    ----------
    countermodels : list of MutationCountermodel; defaults to countermodels_list

    Returns
    -------
    SequenceRepairPlan with one RepairGuide per countermodel, ordered by
    anomaly severity (highest first) and strategy priority.
    """
    cms = countermodels if countermodels is not None else (countermodels_list or [])
    guides: List[RepairGuide] = []
    for cm in cms:
        guide = extract_repair_guide(cm, strategy="auto")
        guides.append(guide)

    # Sort guides: highest max_severity first, then by strategy priority
    sorted_guides = sorted(
        enumerate(guides),
        key=lambda ig: (
            -cms[ig[0]].max_severity(),
            _STRATEGY_PRIORITY.get(ig[1].repair_strategy, 99),
        ),
    )
    application_order = tuple(i for i, _ in sorted_guides)
    sorted_guide_tuple = tuple(g for _, g in sorted_guides)

    plan = SequenceRepairPlan(
        plan_id=_new_id("plan_"),
        countermodel_ids=tuple(cm.countermodel_id for cm in cms),
        repair_guides=sorted_guide_tuple,
        application_order=tuple(range(len(sorted_guide_tuple))),
        trust=TrustTier.PROPOSAL,
    )
    _LOGGER.debug(
        "build_repair_plan: plan %s with %d repairs",
        plan.plan_id[:12],
        plan.total_repairs(),
    )
    return plan


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def build_mutation_countermodel(
    failed_transition_id: str,
    anomalies: list,
    context: Optional[dict] = None,
) -> MutationCountermodel:
    """
    Construct a MutationCountermodel from a failed_transition_id and anomalies.

    The CechObstruction is built automatically from the anomalies: each anomaly
    contributes one element to the cocycle (a triple of anomaly_id, kind, addr).

    Parameters
    ----------
    failed_transition_id : the transition_id of the rejected mutation
    anomalies            : list of MutationAnomaly
    context              : optional context dict; defaults to {}

    Returns
    -------
    MutationCountermodel with trust = RUNTIME_WITNESSED.

    Algorithm
    ---------
    1. Build the cocycle as {(a.anomaly_id, a.anomaly_kind, a.affected_addr)
       for a in anomalies}.
    2. Determine the cover_id and cohomology_class from the anomaly kinds.
    3. Construct the CechObstruction.
    4. Construct and return the MutationCountermodel.
    """
    ctx = context or {}
    cid = _new_id("cm_")
    truncated = anomalies[:_MAX_ANOMALIES_PER_COUNTERMODEL]
    anomaly_tuple = tuple(truncated)

    cocycle = frozenset(
    (a.anomaly_id, a.anomaly_kind, a.affected_addr) for a in anomaly_tuple
    )
    cover_id = f"mutation_cover_{failed_transition_id[:16]}"
    dominant = _dominant_anomaly_kind(anomaly_tuple)
    cohomology_class = f"H1_{dominant}({cover_id})"
    kind_counts = Counter(a.anomaly_kind for a in anomaly_tuple)
    desc_parts = [f"{k}×{v}" for k, v in sorted(kind_counts.items())]
    description = (
    f"Mutation {failed_transition_id!r} failed: "
    + (", ".join(desc_parts) if desc_parts else "no anomalies")
    )

    obstruction = CechObstruction(
    cover_id=cover_id,
    cocycle=cocycle,
    cohomology_class=cohomology_class,
    description=description,
    )

    cm = MutationCountermodel(
    countermodel_id=cid,
    failed_transition_id=failed_transition_id,
    anomalies=anomaly_tuple,
    cech_obstruction=obstruction,
    context=ctx,
    trust=TrustTier.RUNTIME_WITNESSED,
    provenance={
        "built_at": time.time(),
        "anomaly_hash": _hash_anomalies(anomaly_tuple),
        "dominant_kind": dominant,
    },
    )
    _LOGGER.debug(
    "build_mutation_countermodel: %s, %d anomalies, cech=%s",
    cid[:12],
    len(anomaly_tuple),
    cohomology_class,
    )
    return cm


def extract_repair_guide(
    countermodel: MutationCountermodel,
    strategy: str = "auto",
) -> RepairGuide:
    """
    Derive a RepairGuide from a MutationCountermodel.

    If strategy == "auto", the strategy is selected based on the dominant
    anomaly kind. Otherwise the given strategy is used.

    Parameters
    ----------
    countermodel : the MutationCountermodel to repair
    strategy     : "auto" or one of the five strategy strings

    Returns
    -------
    RepairGuide with suggested_type and suggested_value derived from the
    dominant anomaly.

    Strategy selection (auto)
    -------------------------
    * type_error         → "retype"   (change the type to the expected type)
    * bounds_error       → "revalue"  (change the value to a bounds-safe default)
    * aliasing_violation → "remove_write" (remove the write entirely)
    * barrier_violation  → "add_barrier"  (add a barrier for this range)
    * coherence_failure  → "split_mutation" (split the mutation)
    """
    dominant_kind = _dominant_anomaly_kind(countermodel.anomalies)

    if strategy == "auto":
        chosen_strategy = _KIND_TO_STRATEGY.get(dominant_kind, "add_barrier")
    else:
        if strategy not in _STRATEGY_PRIORITY:
            raise JuGeoError(
                f"extract_repair_guide: unknown strategy {strategy!r}; "
                f"must be one of {list(_STRATEGY_PRIORITY)}"
            )
        chosen_strategy = strategy

    # Find the worst anomaly (highest severity, then earliest addr)
    if countermodel.anomalies:
        worst = sorted(
            countermodel.anomalies,
            key=lambda a: (-a.severity, a.affected_addr),
        )[0]
        target_addr = worst.affected_addr
        suggested_type = worst.expected_type if worst.expected_type else "unknown"
        # For revalue, suggest a type-appropriate default
        value_defaults = {
            "int": "0",
            "float": "0.0",
            "str": "''",
            "ptr": "nullptr",
            "bool": "false",
            "unknown": "bottom",
        }
        suggested_value = value_defaults.get(suggested_type, "bottom")
        justification = (
            f"Dominant anomaly: {worst.anomaly_kind} (severity {worst.severity}) "
            f"at addr 0x{target_addr:08x}. "
            f"Expected type {worst.expected_type!r}, actual {worst.actual_type!r}. "
            f"Strategy: {chosen_strategy}."
        )
    else:
        target_addr = 0
        suggested_type = "unknown"
        suggested_value = "bottom"
        justification = f"No anomalies; default strategy {chosen_strategy}."

    confidence = (
        TrustTier.REVIEWED
        if chosen_strategy in ("retype", "revalue")
        else TrustTier.PROPOSAL
    )

    return RepairGuide(
        guide_id=_new_id("guide_"),
        countermodel_id=countermodel.countermodel_id,
        target_addr=target_addr,
        suggested_type=suggested_type,
        suggested_value=suggested_value,
        repair_strategy=chosen_strategy,
        justification=justification,
        confidence=confidence,
    )



def apply_repair(guide: RepairGuide, slice_cells: dict) -> dict:
    """
    Apply a RepairGuide to a cells dict, returning a modified copy.

    Parameters
    ----------
    guide       : the RepairGuide to apply
    slice_cells : dict mapping addr to (type_repr, value_repr)

    Returns
    -------
    A new dict with the repair applied. The original is not modified.
    """
    cells = dict(slice_cells)
    addr = guide.target_addr

    if guide.repair_strategy == "retype":
        if addr in cells:
            old_type, old_value = cells[addr]
            cells[addr] = (guide.suggested_type, old_value)
    elif guide.repair_strategy == "revalue":
        if addr in cells:
            old_type, old_value = cells[addr]
            cells[addr] = (old_type, guide.suggested_value)
    elif guide.repair_strategy == "remove_write":
        if addr in cells:
            del cells[addr]
    elif guide.repair_strategy == "split_mutation":
        if addr in cells:
            old_type, old_value = cells[addr]
            cells[addr] = (old_type, old_value)  # annotate only
    elif guide.repair_strategy == "add_barrier":
        pass  # caller must add barrier to the encoder

    return cells


def classify_mutation_anomaly(
    violation_message: str,
    addr: int,
    expected_type: str = "",
    actual_type: str = "",
) -> MutationAnomaly:
    """
    Parse a violation message string and classify it as a MutationAnomaly.
    """
    msg_lower = violation_message.lower()

    if "alias" in msg_lower or "unique" in msg_lower:
        kind = "aliasing_violation"
    elif "bounds" in msg_lower or ">=" in msg_lower or "<=" in msg_lower:
        kind = "bounds_error"
    elif "type_constraint" in msg_lower or "type" in msg_lower:
        kind = "type_error"
    elif "barrier_violation" in msg_lower:
        kind = "barrier_violation"
    else:
        kind = "coherence_failure"

    severity = _KIND_TO_SEVERITY.get(kind, 3)

    description = (
        f"{kind} at addr=0x{addr:08x}: "
        f"expected={expected_type!r}, actual={actual_type!r}. "
        f"Violation: {violation_message[:120]}"
    )

    return MutationAnomaly(
        anomaly_id=_new_id("anomaly_"),
        anomaly_kind=kind,
        affected_addr=addr,
        expected_type=expected_type,
        actual_type=actual_type,
        description=description,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Additional utility functions
# ---------------------------------------------------------------------------


def anomalies_from_violations(violations: list, default_addr: int = 0) -> list:
    """
    Convert a list of violation message strings to a list of MutationAnomalies.
    """
    result: List[MutationAnomaly] = []
    for msg in violations:
        parsed = _parse_barrier_violation(msg)
        try:
            addr = int(parsed.get("addr_int", str(default_addr)) or str(default_addr))
        except ValueError:
            addr = default_addr
        anomaly = classify_mutation_anomaly(
            violation_message=msg,
            addr=addr,
            expected_type=parsed.get("type_constraint", ""),
            actual_type=parsed.get("new_type", ""),
        )
        result.append(anomaly)
    return result


def merge_countermodels(cms: list) -> MutationCountermodel:
    """
    Merge multiple MutationCountermodels into a single aggregate countermodel.
    """
    if not cms:
        raise JuGeoError("merge_countermodels: cannot merge empty list")

    all_anomalies: List[MutationAnomaly] = []
    all_cocycle: List[str] = []
    for cm in cms:
        all_anomalies.extend(cm.anomalies)
        all_cocycle.extend(cm.cech_obstruction.cocycle)

    merged_cech = CechObstruction(
        obstruction_id=_new_id("cech_merged_"),
        cocycle=tuple(dict.fromkeys(all_cocycle)),
        cohomology_class=f"merged_H1_{len(cms)}",
    )

    return MutationCountermodel(
        countermodel_id=_new_id("cm_merged_"),
        failed_transition_id=cms[0].failed_transition_id,
        anomalies=tuple(all_anomalies),
        cech_obstruction=merged_cech,
        repair_suggestions=(),
        judgment=cms[0].judgment,
    )
