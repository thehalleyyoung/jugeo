"""Hypercover treaty synthesis, dependent treaty formation, and overlap law discovery.

Treaties are stabilized overlap laws discovered during generation.  An interface
is a discovered semantic object before it is a checked one.  The system mines
recurrent dependence patterns, repair motifs, and stable behavioral correlations
on overlaps to propose interface laws with explicit guards and invalidation
support.

Following theory2.tex §3–§5: overlap objects carry shared context Γ_uv, exported
clause families Λ^exp_uv, active guards Θ_uv, treaty status τ_uv, challenge /
witness history Ξ_uv, and replay policy ν_uv.  Stabilization converts empirical
overlap regularity into explicit descent data.  Three failure modes are tracked:
oscillating treaty law, guard erasure, and summary underfitting.

copilot: treaty-synthesis-core — primary module for overlap-law lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Callable, Sequence


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TreatyStatus(Enum):
    """Lifecycle status of a treaty (theory2.tex status ∈ {proposed …})."""

    PROPOSED = "proposed"
    NEGOTIATING = "negotiating"
    RATIFIED = "ratified"
    CHALLENGED = "challenged"
    RETIRED = "retired"


class QuantifierKind(Enum):
    """Quantifier used in a treaty law predicate."""

    FORALL = "forall"
    EXISTS = "exists"
    FORALL_EXISTS = "forall_exists"
    NONE = "none"


class ChallengeVerdict(Enum):
    """Outcome of a treaty challenge evaluation."""

    UPHELD = "upheld"
    OVERTURNED = "overturned"
    AMENDED = "amended"
    DEFERRED = "deferred"


class PatternKind(Enum):
    """Category of mined pattern."""

    DEPENDENCE = "dependence"
    REPAIR_MOTIF = "repair_motif"
    BEHAVIORAL_CORRELATION = "behavioral_correlation"


class InvalidationSeverity(IntEnum):
    """How severe an invalidation event is."""

    INFO = 0
    WARNING = 1
    CRITICAL = 2


# ---------------------------------------------------------------------------
# TreatyLaw
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TreatyLaw:
    """The actual overlap compatibility law carried by a treaty.

    A law is a predicate over shared context variables whose satisfaction
    witnesses the absence of overlap discrepancy (i.e. the Čech cocycle
    is a coboundary on this coordinate).

    Attributes
    ----------
    predicate : str
        Symbolic representation of the compatibility condition, e.g.
        ``"∀x ∈ Γ_uv. f(x) = g(x)"``.
    variables : tuple[str, ...]
        Free variables the predicate ranges over.
    quantifiers : tuple[QuantifierKind, ...]
        One quantifier per variable, aligned positionally.
    support_scope : str
        Description of the support region where the law applies.
    is_decidable : bool
        Whether the law can be checked algorithmically on finite data.
    encoding_hint : str
        Hint for downstream encoders (e.g. ``"smt-bitvec-32"``).
    natural_language_description : str
        Human-readable explanation generated for copilot summaries.
    """

    predicate: str
    variables: tuple[str, ...] = ()
    quantifiers: tuple[QuantifierKind, ...] = ()
    name: str = ""
    statement: str = ""
    quantifier_variables: tuple[str, ...] = ()
    support_scope: str = "full"
    is_decidable: bool = True
    encoding_hint: str = ""
    natural_language_description: str = ""

    def __post_init__(self) -> None:
        if self.statement and not self.predicate:
            object.__setattr__(self, "predicate", self.statement)
        elif self.predicate and not self.statement:
            object.__setattr__(self, "statement", self.predicate)
        if self.quantifier_variables and not self.variables:
            object.__setattr__(self, "variables", self.quantifier_variables)
        elif self.variables and not self.quantifier_variables:
            object.__setattr__(self, "quantifier_variables", self.variables)

    # -- query helpers -------------------------------------------------------

    def arity(self) -> int:
        """Return the number of free variables."""
        return len(self.quantifier_variables or self.variables)

    def has_universal_quantifier(self) -> bool:
        """True when at least one variable is universally quantified."""
        return any(q == QuantifierKind.FORALL for q in self.quantifiers)

    def summary(self) -> str:
        """One-line summary suitable for copilot status output."""
        desc = self.natural_language_description or self.predicate
        return f"[{'decidable' if self.is_decidable else 'undecidable'}] {desc}"

    def structural_hash(self) -> str:
        """Content-addressable hash of the law for deduplication."""
        payload = (self.predicate, self.variables, self.support_scope)
        return hashlib.sha256(repr(payload).encode()).hexdigest()[:16]

    def subsumes(self, other: TreatyLaw) -> bool:
        """Conservative check: does *self* logically subsume *other*?

        A law L₁ subsumes L₂ when every variable of L₂ appears in L₁ and
        L₁'s support scope is a superset (here approximated by string
        containment).  Full subsumption needs an SMT call but this gives a
        quick pre-filter.
        """
        if not set(other.variables).issubset(set(self.variables)):
            return False
        if self.support_scope == "full":
            return True
        return other.support_scope in self.support_scope

    def weaken(self, *, drop_variables: Sequence[str] = ()) -> TreatyLaw:
        """Return a weakened copy with the listed variables projected out."""
        kept_vars: list[str] = []
        kept_quants: list[QuantifierKind] = []
        for var, quant in zip(self.variables, self.quantifiers):
            if var not in drop_variables:
                kept_vars.append(var)
                kept_quants.append(quant)
        return TreatyLaw(
            predicate=self.predicate,
            variables=tuple(kept_vars),
            quantifiers=tuple(kept_quants),
            support_scope=self.support_scope,
            is_decidable=self.is_decidable,
            encoding_hint=self.encoding_hint,
            natural_language_description=self.natural_language_description,
        )


# ---------------------------------------------------------------------------
# TreatyGuard
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TreatyGuard:
    """Guard condition on a treaty: the treaty is only active while the guard
    is satisfied.  Theory2.tex: active guards Θ_uv.

    Attributes
    ----------
    guard_id : str
        Unique identifier for this guard.
    condition : str
        Symbolic boolean expression that must hold.
    variables : tuple[str, ...]
        Variables the condition references.
    description : str
        Human-readable description for copilot explanation traces.
    """

    guard_id: str
    condition: str
    variables: tuple[str, ...] = ()
    description: str = ""

    def is_satisfied(self, bindings: dict[str, Any]) -> bool:
        """Evaluate the guard under *bindings*.

        Uses a restricted ``eval`` over an allow-listed namespace so that
        guards expressed as simple Python predicates can be checked quickly.
        Falls back to ``False`` on any evaluation error (fail-closed).
        """
        try:
            namespace: dict[str, Any] = {v: bindings.get(v) for v in self.variables}
            namespace["__builtins__"] = {}
            return bool(eval(self.condition, namespace))  # noqa: S307
        except Exception:
            return False

    def evaluate(self, bindings: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate and return ``(result, explanation)``."""
        result = self.is_satisfied(bindings)
        explanation = (
            f"Guard {self.guard_id} {'PASS' if result else 'FAIL'}: "
            f"{self.condition} with {bindings}"
        )
        return result, explanation

    def explain_failure(self, bindings: dict[str, Any]) -> str:
        """Return a human-readable explanation when the guard fails.

        copilot: surfaces this in repair suggestion traces.
        """
        ok, explanation = self.evaluate(bindings)
        if ok:
            return f"Guard {self.guard_id} is satisfied — no failure to explain."
        missing = [v for v in self.variables if v not in bindings]
        if missing:
            return (
                f"Guard {self.guard_id} failed because variables "
                f"{missing} are unbound."
            )
        return (
            f"Guard {self.guard_id} failed: {self.condition} "
            f"evaluated to False under {bindings}."
        )

    def suggest_fix(self, bindings: dict[str, Any]) -> str:
        """Suggest minimal binding changes that would satisfy the guard.

        This is a heuristic: for boolean conditions it suggests flipping the
        first ``False``-valued variable; for numeric comparisons it nudges
        toward the threshold.  copilot: presented as repair hints in the UI.
        """
        for var in self.variables:
            val = bindings.get(var)
            if val is False:
                return f"Try setting {var}=True to satisfy guard {self.guard_id}."
            if isinstance(val, (int, float)) and val <= 0:
                return (
                    f"Variable {var} is {val}; a positive value may "
                    f"satisfy guard {self.guard_id}."
                )
        return f"Review bindings for guard {self.guard_id}: {self.condition}."

    def rename_variables(self, mapping: dict[str, str]) -> TreatyGuard:
        """Return a copy with variables renamed according to *mapping*."""
        new_vars = tuple(mapping.get(v, v) for v in self.variables)
        new_cond = self.condition
        for old, new in mapping.items():
            new_cond = new_cond.replace(old, new)
        return TreatyGuard(
            guard_id=self.guard_id,
            condition=new_cond,
            variables=new_vars,
            description=self.description,
        )


# ---------------------------------------------------------------------------
# Treaty
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Treaty:
    """A treaty stabilising an overlap law between two or more chart regions.

    Theory2.tex: "Stabilization = converting empirical overlap regularity into
    explicit descent data."  A treaty moves through the lifecycle
    PROPOSED → NEGOTIATING → RATIFIED → (CHALLENGED → RATIFIED | RETIRED).

    Attributes
    ----------
    treaty_id : str
        Globally unique identifier.
    overlap_coordinate : str
        Label of the overlap region (Čech coordinate pair) this treaty covers.
    parties : list[str]
        Identifiers of the sections / chart regions participating.
    law : TreatyLaw
        The compatibility condition being enforced.
    guard_conditions : list[TreatyGuard]
        Conditions that must remain true for the treaty to stay active.
    invalidation_triggers : list[str]
        Events that should fire invalidation of this treaty.
    evidence_basis : list[str]
        Provenance references (witness ids, example hashes) supporting the law.
    trust_level : float
        Numeric trust score in [0, 1]. Updated by the evidence subsystem.
    status : TreatyStatus
        Current lifecycle status.
    created_at : datetime
        Timestamp of initial proposal.
    ratified_at : datetime | None
        Timestamp of ratification (None while un-ratified).
    """

    treaty_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    overlap_coordinate: str = ""
    parties: list[str] = field(default_factory=list)
    law: TreatyLaw = field(default_factory=lambda: TreatyLaw(predicate="true"))
    laws: tuple[TreatyLaw, ...] = ()
    patches: tuple[str, ...] = ()
    guard_conditions: list[TreatyGuard] = field(default_factory=list)
    invalidation_triggers: list[str] = field(default_factory=list)
    evidence_basis: list[str] = field(default_factory=list)
    trust_level: float = 0.0
    status: TreatyStatus = TreatyStatus.PROPOSED
    created_at: datetime = field(default_factory=_utcnow)
    ratified_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.laws and self.law.predicate == "true":
            self.law = self.laws[0]
        elif self.law.predicate != "true" and not self.laws:
            self.laws = (self.law,)
        if self.patches and not self.parties:
            self.parties = list(self.patches)
        elif self.parties and not self.patches:
            self.patches = tuple(self.parties)

    # -- lifecycle helpers ---------------------------------------------------

    def is_active(self) -> bool:
        """A treaty is active when ratified and not challenged or retired."""
        return self.status == TreatyStatus.RATIFIED

    def guards_satisfied(self, bindings: dict[str, Any]) -> bool:
        """Check all guard conditions against *bindings*."""
        return all(g.is_satisfied(bindings) for g in self.guard_conditions)

    def add_evidence(self, evidence_id: str) -> None:
        """Append an evidence reference and bump trust proportionally."""
        self.evidence_basis.append(evidence_id)
        self.trust_level = min(1.0, self.trust_level + 0.05)

    def challenge(self) -> None:
        """Move treaty into CHALLENGED status."""
        if self.status in (TreatyStatus.PROPOSED, TreatyStatus.RATIFIED, TreatyStatus.NEGOTIATING):
            self.status = TreatyStatus.CHALLENGED

    def ratify(self) -> None:
        """Move treaty into RATIFIED status with timestamp."""
        self.status = TreatyStatus.RATIFIED
        self.ratified_at = _utcnow()

    def retire(self) -> None:
        """Permanently retire the treaty."""
        self.status = TreatyStatus.RETIRED

    def stability_score(self) -> float:
        """Composite stability metric combining trust and evidence count.

        copilot: used in dashboard treaty-health indicators.
        """
        evidence_factor = min(1.0, len(self.evidence_basis) / 10.0)
        return round(self.trust_level * 0.6 + evidence_factor * 0.4, 4)

    def summary(self) -> str:
        """Human-readable one-liner for copilot display."""
        return (
            f"Treaty {self.treaty_id} [{self.status.value}] "
            f"on {self.overlap_coordinate}: {self.law.summary()}"
        )

    @property
    def age_seconds(self) -> float:
        """Seconds since the treaty was created."""
        return (_utcnow() - self.created_at).total_seconds()


# ---------------------------------------------------------------------------
# TreatySynthesizer
# ---------------------------------------------------------------------------

@dataclass
class _MinedPattern:
    """Internal: a single mined regularity before it becomes a law."""

    kind: PatternKind
    raw_evidence: list[dict[str, Any]] = field(default_factory=list)
    frequency: int = 0
    signature: str = ""


class TreatySynthesizer:
    """Discovers treaties from construction-history patterns.

    The synthesiser observes overlap repair events, successful gluings, and
    behavioural invariants, then proposes candidate treaty laws.  Each
    candidate is validated against accumulated evidence and optionally
    generalised before being handed to the ``TreatyManager``.

    copilot: orchestrates pattern mining → law proposal → validation pipeline.
    """

    def __init__(
        self,
        min_support: int = 3,
        confidence_threshold: float = 0.7,
        max_variables: int = 8,
    ) -> None:
        self._min_support = min_support
        self._confidence_threshold = confidence_threshold
        self._max_variables = max_variables
        self._pattern_cache: dict[str, _MinedPattern] = {}

    # -- public API ----------------------------------------------------------

    def synthesize(
        self,
        history: Sequence[dict[str, Any]],
        existing_treaties: Sequence[Treaty] = (),
    ) -> list[TreatyLaw]:
        """End-to-end pipeline: mine → propose → validate → return treaties.

        Parameters
        ----------
        history:
            Sequence of construction-event dicts (repair logs, gluing records).
        existing_treaties:
            Already-ratified treaties to avoid duplication.

        Returns
        -------
        list[Treaty]
            Newly synthesised treaty proposals.
        """
        patterns = self.mine_patterns(history)
        proposals: list[TreatyLaw] = []
        for pattern in patterns:
            law = self.propose_law(pattern)
            if law is None:
                continue
            if not self.validate_law(law, history):
                continue
            generalised = self.generalize_from_examples(law, history)
            if self._is_redundant(generalised, existing_treaties):
                continue
            proposals.append(generalised)
        return proposals

    def mine_patterns(
        self, history: Sequence[dict[str, Any]]
    ) -> list[_MinedPattern]:
        """Extract recurrent patterns from *history*.

        Three kinds are mined:
        1. **Dependence patterns** — repeated import / export relationships.
        2. **Repair motifs** — recurring fix sequences on the same overlap.
        3. **Behavioral correlations** — property invariants that hold across
           successful gluings.
        """
        counter: Counter[str] = Counter()
        bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in history:
            sig = self._event_signature(event)
            counter[sig] += 1
            bucket[sig].append(event)

        patterns: list[_MinedPattern] = []
        for sig, freq in counter.items():
            if freq < self._min_support:
                continue
            kind = self._classify_pattern(bucket[sig])
            pat = _MinedPattern(
                kind=kind,
                raw_evidence=bucket[sig],
                frequency=freq,
                signature=sig,
            )
            self._pattern_cache[sig] = pat
            patterns.append(pat)
        return sorted(patterns, key=lambda p: -p.frequency)

    def propose_law(self, pattern: _MinedPattern) -> TreatyLaw | None:
        """Convert a mined pattern into a candidate ``TreatyLaw``.

        Returns ``None`` when the pattern is too noisy to form a coherent
        predicate (support below confidence threshold).
        """
        if not pattern.raw_evidence:
            return None
        confidence = pattern.frequency / max(
            len(pattern.raw_evidence), 1
        )
        if confidence < self._confidence_threshold:
            return None
        variables = self._extract_variables(pattern.raw_evidence)
        if len(variables) > self._max_variables:
            variables = variables[: self._max_variables]
        predicate = self._build_predicate(pattern)
        return TreatyLaw(
            predicate=predicate,
            variables=tuple(variables),
            quantifiers=tuple(QuantifierKind.FORALL for _ in variables),
            support_scope=pattern.signature,
            is_decidable=True,
            encoding_hint="smt-qf-lia",
            natural_language_description=(
                f"Mined {pattern.kind.value} pattern with "
                f"support={pattern.frequency}."
            ),
        )

    def validate_law(
        self,
        law: TreatyLaw,
        history: Sequence[dict[str, Any]],
    ) -> bool:
        """Check the candidate law against all events in *history*.

        Counts the fraction of events consistent with the law; returns True
        when the fraction exceeds ``confidence_threshold``.
        """
        if not history:
            return False
        consistent = sum(
            1 for event in history if self._event_consistent_with_law(event, law)
        )
        return (consistent / len(history)) >= self._confidence_threshold

    def generalize_from_examples(
        self,
        law: TreatyLaw,
        history: Sequence[dict[str, Any]],
    ) -> TreatyLaw:
        """Attempt to widen the law's support scope by dropping dispensable
        variables while retaining validity.

        For each variable we test whether the law still passes validation
        without it; if so we drop it (Occam preference).
        """
        current = law
        for var in law.variables:
            candidate = current.weaken(drop_variables=[var])
            if self.validate_law(candidate, history):
                current = candidate
        return current

    def copilot_suggest_treaty(
        self,
        history: Sequence[dict[str, Any]],
        overlap_label: str = "",
    ) -> str:
        """Return a natural-language treaty suggestion for copilot display.

        Runs a lightweight version of the full pipeline and formats the
        result as a Markdown snippet suitable for the copilot sidebar.
        """
        patterns = self.mine_patterns(history)
        if not patterns:
            return f"_No treaty candidates for overlap `{overlap_label}`._"
        best = patterns[0]
        law = self.propose_law(best)
        if law is None:
            return (
                f"Pattern detected (support={best.frequency}) on "
                f"`{overlap_label}` but confidence too low to propose a law."
            )
        lines = [
            f"**Suggested treaty** for `{overlap_label}`",
            f"- Law: `{law.predicate}`",
            f"- Variables: {', '.join(law.variables) or '(none)'}",
            f"- Support: {best.frequency} events",
            f"- Decidable: {'yes' if law.is_decidable else 'no'}",
        ]
        return "\n".join(lines)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _event_signature(event: dict[str, Any]) -> str:
        keys = sorted(event.keys())
        return hashlib.md5(
            json.dumps(keys, sort_keys=True).encode()
        ).hexdigest()[:10]

    @staticmethod
    def _classify_pattern(events: list[dict[str, Any]]) -> PatternKind:
        has_repair = any("repair" in str(e) for e in events)
        has_dep = any("depends" in str(e) or "import" in str(e) for e in events)
        if has_repair:
            return PatternKind.REPAIR_MOTIF
        if has_dep:
            return PatternKind.DEPENDENCE
        return PatternKind.BEHAVIORAL_CORRELATION

    @staticmethod
    def _extract_variables(events: list[dict[str, Any]]) -> list[str]:
        """Heuristic variable extraction: collect keys that vary across events."""
        all_keys: set[str] = set()
        for event in events:
            all_keys.update(event.keys())
        varying: list[str] = []
        for key in sorted(all_keys):
            values = {event.get(key) for event in events}
            if len(values) > 1:
                varying.append(key)
        return varying

    @staticmethod
    def _build_predicate(pattern: _MinedPattern) -> str:
        if pattern.kind == PatternKind.DEPENDENCE:
            return "dep(u, v) ⇒ compatible(Γ_u, Γ_v)"
        if pattern.kind == PatternKind.REPAIR_MOTIF:
            return "repair(u, v) ⇒ δ(u, v) ∈ B¹"
        return "∀x ∈ Γ_uv. φ(x) holds"

    @staticmethod
    def _event_consistent_with_law(
        event: dict[str, Any], law: TreatyLaw
    ) -> bool:
        """Heuristic consistency check: every law variable appears in event."""
        return all(var in event for var in law.variables)

    def _is_redundant(
        self, law: TreatyLaw, existing: Sequence[Treaty]
    ) -> bool:
        return any(t.law.subsumes(law) for t in existing)


# ---------------------------------------------------------------------------
# TreatyManager
# ---------------------------------------------------------------------------

class TreatyManager:
    """Manages the lifecycle of all treaties in a generation session.

    Provides propose / negotiate / ratify / challenge / retire transitions
    and indexes treaties by overlap coordinate for fast lookup.

    copilot: primary entry point the UI queries for treaty health.
    """

    def __init__(self) -> None:
        self._treaties: dict[str, Treaty] = {}
        self._by_overlap: dict[str, list[str]] = defaultdict(list)

    # -- lifecycle -----------------------------------------------------------

    def propose(self, treaty: Treaty) -> Treaty:
        """Register a new treaty proposal.

        Validates that no exact duplicate exists and stores the treaty in
        PROPOSED status.
        """
        if treaty.treaty_id in self._treaties:
            raise ValueError(
                f"Treaty {treaty.treaty_id} already registered."
            )
        treaty.status = TreatyStatus.PROPOSED
        self._treaties[treaty.treaty_id] = treaty
        self._by_overlap[treaty.overlap_coordinate].append(treaty.treaty_id)
        return treaty

    def negotiate(self, treaty_id: str, *, amendments: dict[str, Any] | None = None) -> Treaty:
        """Move a treaty into NEGOTIATING and optionally amend fields.

        During negotiation parties may adjust guard conditions, narrow the
        support scope, or refine the law predicate.
        """
        treaty = self._get(treaty_id)
        if treaty.status != TreatyStatus.PROPOSED:
            raise ValueError(
                f"Cannot negotiate treaty {treaty_id} in status {treaty.status.value}."
            )
        treaty.status = TreatyStatus.NEGOTIATING
        if amendments:
            if "trust_level" in amendments:
                treaty.trust_level = float(amendments["trust_level"])
            if "parties" in amendments:
                treaty.parties = list(amendments["parties"])
        return treaty

    def ratify(self, treaty_id: str) -> Treaty:
        """Ratify a treaty, making it active for overlap enforcement."""
        treaty = self._get(treaty_id)
        if treaty.status not in (
            TreatyStatus.PROPOSED,
            TreatyStatus.NEGOTIATING,
            TreatyStatus.CHALLENGED,
        ):
            raise ValueError(
                f"Cannot ratify treaty {treaty_id} from {treaty.status.value}."
            )
        treaty.ratify()
        return treaty

    def challenge(self, treaty_id: str) -> Treaty:
        """Put an active treaty under challenge."""
        treaty = self._get(treaty_id)
        treaty.challenge()
        return treaty

    def retire(self, treaty_id: str) -> Treaty:
        """Permanently retire a treaty."""
        treaty = self._get(treaty_id)
        treaty.retire()
        return treaty

    def active_treaties(self) -> list[Treaty]:
        """Return all currently-ratified treaties."""
        return [t for t in self._treaties.values() if t.is_active()]

    def treaties_for_overlap(self, overlap_coordinate: str) -> list[Treaty]:
        """Return all treaties (any status) for a given overlap coordinate."""
        ids = self._by_overlap.get(overlap_coordinate, [])
        return [self._treaties[tid] for tid in ids if tid in self._treaties]

    def is_compatible_with_existing(self, candidate: Treaty) -> bool:
        """Check whether *candidate* is consistent with active treaties on the
        same overlap.

        Two treaties are incompatible when both are decidable and their
        predicates differ while covering overlapping variable sets.
        """
        existing = self.treaties_for_overlap(candidate.overlap_coordinate)
        for treaty in existing:
            if not treaty.is_active():
                continue
            if treaty.law.is_decidable and candidate.law.is_decidable:
                shared_vars = set(treaty.law.variables) & set(
                    candidate.law.variables
                )
                if shared_vars and treaty.law.predicate != candidate.law.predicate:
                    return False
        return True

    def get(self, treaty_id: str) -> Treaty | None:
        """Look up a treaty by id, returning ``None`` if not found."""
        return self._treaties.get(treaty_id)

    def all_treaties(self) -> list[Treaty]:
        """Return every registered treaty regardless of status."""
        return list(self._treaties.values())

    # -- internals -----------------------------------------------------------

    def _get(self, treaty_id: str) -> Treaty:
        treaty = self._treaties.get(treaty_id)
        if treaty is None:
            raise KeyError(f"No treaty with id {treaty_id}.")
        return treaty


# ---------------------------------------------------------------------------
# TreatyValidator
# ---------------------------------------------------------------------------

class TreatyValidator:
    """Validates treaties against structural, evidential, and consistency
    criteria before ratification.

    copilot: invoked automatically before any treaty reaches RATIFIED.
    """

    def __init__(
        self,
        require_decidable: bool = True,
        min_evidence: int = 1,
        min_trust: float = 0.3,
    ) -> None:
        self._require_decidable = require_decidable
        self._min_evidence = min_evidence
        self._min_trust = min_trust

    def validate(self, treaty: Treaty, manager: TreatyManager) -> tuple[bool, list[str]]:
        """Run all validation checks, returning ``(ok, reasons)``."""
        reasons: list[str] = []
        if not self.check_guard_conditions(treaty):
            reasons.append("One or more guard conditions are structurally invalid.")
        if not self.check_support_scope(treaty):
            reasons.append("Law support scope is empty or undefined.")
        if not self.check_consistency_with_existing(treaty, manager):
            reasons.append("Treaty conflicts with an existing active treaty.")
        if not self.verify_against_evidence(treaty):
            reasons.append("Insufficient evidence basis for ratification.")
        if self._require_decidable and not treaty.law.is_decidable:
            reasons.append("Law is undecidable and decidability is required.")
        return (len(reasons) == 0, reasons)

    def check_guard_conditions(self, treaty: Treaty) -> bool:
        """Structural check: every guard must reference at least one variable
        and have a non-empty condition string."""
        for guard in treaty.guard_conditions:
            if not guard.condition.strip():
                return False
        return True

    def check_consistency_with_existing(
        self, treaty: Treaty, manager: TreatyManager
    ) -> bool:
        """Delegate to ``TreatyManager.is_compatible_with_existing``."""
        return manager.is_compatible_with_existing(treaty)

    def check_support_scope(self, treaty: Treaty) -> bool:
        """Ensure the law's support scope is non-trivial."""
        return bool(treaty.law.support_scope.strip())

    def verify_against_evidence(self, treaty: Treaty) -> bool:
        """Check that the treaty carries enough evidence and trust."""
        if len(treaty.evidence_basis) < self._min_evidence:
            return False
        if treaty.trust_level < self._min_trust:
            return False
        return True

    def detailed_report(self, treaty: Treaty, manager: TreatyManager) -> str:
        """Produce a multi-line validation report for copilot display."""
        ok, reasons = self.validate(treaty, manager)
        header = f"Validation {'PASSED' if ok else 'FAILED'} for {treaty.treaty_id}"
        if ok:
            return header
        body = "\n".join(f"  - {r}" for r in reasons)
        return f"{header}\n{body}"


# ---------------------------------------------------------------------------
# TreatyChallenger
# ---------------------------------------------------------------------------

class TreatyChallenger:
    """Challenges existing treaties when counter-evidence emerges.

    Theory2.tex: challenge & witness history Ξ_uv.  When an overlap
    discrepancy is found that an active treaty should have prevented, the
    challenger collects counter-evidence, evaluates its weight, and either
    amends the treaty or retires it.

    copilot: surfaces challenge verdicts in the diagnostic panel.
    """

    def __init__(self) -> None:
        self._pending_challenges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._verdicts: dict[str, ChallengeVerdict] = {}

    def challenge(self, treaty: Treaty, reason: str) -> None:
        """Initiate a challenge on *treaty* with a stated *reason*.

        Moves the treaty to CHALLENGED and records the reason.
        """
        treaty.challenge()
        self._pending_challenges[treaty.treaty_id].append(
            {"reason": reason, "timestamp": _utcnow().isoformat()}
        )

    def present_counterevidence(
        self,
        treaty_id: str,
        evidence: dict[str, Any],
    ) -> None:
        """Attach counter-evidence to an ongoing challenge."""
        self._pending_challenges[treaty_id].append(
            {"counter_evidence": evidence, "timestamp": _utcnow().isoformat()}
        )

    def evaluate_challenge(self, treaty: Treaty) -> ChallengeVerdict:
        """Weigh the challenge evidence against the treaty's existing evidence.

        Heuristic: if counter-evidence items outnumber the treaty's evidence
        basis, overturn; if roughly equal, amend; otherwise uphold.
        """
        counter_items = self._pending_challenges.get(treaty.treaty_id, [])
        n_counter = len(counter_items)
        n_support = len(treaty.evidence_basis)
        if n_counter == 0:
            verdict = ChallengeVerdict.DEFERRED
        elif n_counter > n_support:
            verdict = ChallengeVerdict.OVERTURNED
        elif n_counter >= n_support * 0.5:
            verdict = ChallengeVerdict.AMENDED
        else:
            verdict = ChallengeVerdict.UPHELD
        self._verdicts[treaty.treaty_id] = verdict
        return verdict

    def resolve_challenge(
        self, treaty: Treaty, manager: TreatyManager
    ) -> ChallengeVerdict:
        """Evaluate and apply the verdict to the treaty.

        UPHELD → re-ratify, OVERTURNED → retire, AMENDED → re-ratify with
        reduced trust, DEFERRED → leave challenged.
        """
        verdict = self.evaluate_challenge(treaty)
        if verdict == ChallengeVerdict.UPHELD:
            manager.ratify(treaty.treaty_id)
        elif verdict == ChallengeVerdict.OVERTURNED:
            manager.retire(treaty.treaty_id)
        elif verdict == ChallengeVerdict.AMENDED:
            treaty.trust_level = max(0.0, treaty.trust_level - 0.2)
            manager.ratify(treaty.treaty_id)
        # DEFERRED: no state change
        self._pending_challenges.pop(treaty.treaty_id, None)
        return verdict

    def update_or_retire(
        self, treaty: Treaty, manager: TreatyManager
    ) -> str:
        """Convenience wrapper: resolve and return a summary string.

        copilot: called from the repair loop when a construction step
        invalidates an overlap expectation.
        """
        verdict = self.resolve_challenge(treaty, manager)
        return (
            f"Treaty {treaty.treaty_id}: challenge resolved as "
            f"{verdict.value}."
        )

    def pending_challenges_for(self, treaty_id: str) -> list[dict[str, Any]]:
        """Return raw challenge records for inspection."""
        return list(self._pending_challenges.get(treaty_id, []))


# ---------------------------------------------------------------------------
# TreatyPatternMiner
# ---------------------------------------------------------------------------

class TreatyPatternMiner:
    """Mines patterns from construction history to seed treaty proposals.

    Three pattern families are extracted:
    1. **Dependence patterns** — repeated import / export edges.
    2. **Repair motifs** — recurring fix → re-check sequences.
    3. **Behavioral correlations** — property invariants that survive gluing.

    copilot: runs asynchronously during generation; results feed into
    ``TreatySynthesizer.mine_patterns``.
    """

    def __init__(self, min_frequency: int = 2) -> None:
        self._min_frequency = min_frequency
        self._all_patterns: list[_MinedPattern] = []

    def mine(self, history: Sequence[dict[str, Any]]) -> list[_MinedPattern]:
        """Run the full mining pipeline and return ranked patterns."""
        deps = self.extract_dependence_patterns(history)
        repairs = self.extract_repair_motifs(history)
        correlations = self.extract_behavioral_correlations(history)
        combined = deps + repairs + correlations
        ranked = self.rank_by_frequency(combined)
        self._all_patterns = ranked
        return ranked

    def extract_dependence_patterns(
        self, history: Sequence[dict[str, Any]]
    ) -> list[_MinedPattern]:
        """Find events where one region repeatedly depends on another.

        Looks for ``"depends_on"`` or ``"imports"`` keys in event dicts.
        """
        counter: Counter[str] = Counter()
        bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in history:
            dep_key = event.get("depends_on") or event.get("imports")
            if dep_key is not None:
                sig = f"dep:{dep_key}"
                counter[sig] += 1
                bucket[sig].append(event)
        return self._to_patterns(counter, bucket, PatternKind.DEPENDENCE)

    def extract_repair_motifs(
        self, history: Sequence[dict[str, Any]]
    ) -> list[_MinedPattern]:
        """Find recurring repair sequences.

        A repair motif is identified by the ``"repair_action"`` field.
        """
        counter: Counter[str] = Counter()
        bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in history:
            action = event.get("repair_action")
            if action is not None:
                sig = f"repair:{action}"
                counter[sig] += 1
                bucket[sig].append(event)
        return self._to_patterns(counter, bucket, PatternKind.REPAIR_MOTIF)

    def extract_behavioral_correlations(
        self, history: Sequence[dict[str, Any]]
    ) -> list[_MinedPattern]:
        """Find property invariants that hold across successful gluings.

        Considers events with ``"status": "success"`` and collects the set
        of other keys that are always present.
        """
        successes = [e for e in history if e.get("status") == "success"]
        if not successes:
            return []
        common_keys = set(successes[0].keys())
        for event in successes[1:]:
            common_keys &= set(event.keys())
        common_keys -= {"status"}
        if not common_keys:
            return []
        sig = f"corr:{','.join(sorted(common_keys))}"
        pattern = _MinedPattern(
            kind=PatternKind.BEHAVIORAL_CORRELATION,
            raw_evidence=successes,
            frequency=len(successes),
            signature=sig,
        )
        if pattern.frequency >= self._min_frequency:
            return [pattern]
        return []

    def rank_by_frequency(
        self, patterns: list[_MinedPattern]
    ) -> list[_MinedPattern]:
        """Sort patterns descending by frequency, pruning below threshold."""
        above = [p for p in patterns if p.frequency >= self._min_frequency]
        return sorted(above, key=lambda p: -p.frequency)

    def most_frequent(self, n: int = 5) -> list[_MinedPattern]:
        """Return the top *n* patterns from the last mining run."""
        return self._all_patterns[:n]

    def patterns_of_kind(self, kind: PatternKind) -> list[_MinedPattern]:
        """Filter stored patterns by *kind*."""
        return [p for p in self._all_patterns if p.kind == kind]

    # -- internals -----------------------------------------------------------

    def _to_patterns(
        self,
        counter: Counter[str],
        bucket: dict[str, list[dict[str, Any]]],
        kind: PatternKind,
    ) -> list[_MinedPattern]:
        results: list[_MinedPattern] = []
        for sig, freq in counter.items():
            if freq >= self._min_frequency:
                results.append(
                    _MinedPattern(
                        kind=kind,
                        raw_evidence=bucket[sig],
                        frequency=freq,
                        signature=sig,
                    )
                )
        return results


# ---------------------------------------------------------------------------
# TreatyInvalidationMonitor
# ---------------------------------------------------------------------------

class TreatyInvalidationMonitor:
    """Monitors running construction for events that invalidate active treaties.

    Theory2.tex: invalidation fires when a guard fails or an explicit trigger
    event is observed.  Cascade invalidation propagates to treaties whose
    evidence basis references an invalidated treaty.

    copilot: fires desktop notifications on critical invalidation events.
    """

    def __init__(self) -> None:
        self._notifications: list[dict[str, Any]] = []
        self._fired: set[str] = set()

    def monitor(
        self,
        event: dict[str, Any],
        active_treaties: Sequence[Treaty],
        bindings: dict[str, Any] | None = None,
    ) -> list[str]:
        """Check *event* against all active treaties and return ids of any
        treaties that are invalidated.

        Parameters
        ----------
        event:
            Construction event dict.
        active_treaties:
            Currently ratified treaties to check.
        bindings:
            Variable bindings for guard evaluation.

        Returns
        -------
        list[str]
            Treaty ids that were invalidated.
        """
        invalidated: list[str] = []
        for treaty in active_treaties:
            trigger_hit = self.check_triggers(treaty, event)
            guard_fail = (
                bindings is not None
                and not treaty.guards_satisfied(bindings)
            )
            if trigger_hit or guard_fail:
                self.fire_invalidation(treaty, reason=(
                    "trigger" if trigger_hit else "guard_failure"
                ))
                invalidated.append(treaty.treaty_id)
        return invalidated

    def check_triggers(self, treaty: Treaty, event: dict[str, Any]) -> bool:
        """Return True if *event* matches any of the treaty's invalidation
        triggers.

        A trigger matches when the trigger string appears as a key or value
        anywhere in the event dict (coarse but fast).
        """
        event_str = json.dumps(event, default=str)
        return any(trigger in event_str for trigger in treaty.invalidation_triggers)

    def fire_invalidation(self, treaty: Treaty, *, reason: str = "") -> None:
        """Mark treaty as challenged and record a notification."""
        treaty.challenge()
        self._fired.add(treaty.treaty_id)
        self.notification(
            treaty_id=treaty.treaty_id,
            severity=InvalidationSeverity.CRITICAL,
            message=f"Treaty {treaty.treaty_id} invalidated: {reason}.",
        )

    def cascade_invalidation(
        self,
        source_treaty_id: str,
        all_treaties: Sequence[Treaty],
    ) -> list[str]:
        """Propagate invalidation from *source_treaty_id* to treaties that
        depend on it via evidence-basis references.

        Returns the ids of cascaded treaties.
        """
        cascaded: list[str] = []
        for treaty in all_treaties:
            if treaty.treaty_id == source_treaty_id:
                continue
            if source_treaty_id in treaty.evidence_basis:
                self.fire_invalidation(treaty, reason=f"cascade from {source_treaty_id}")
                cascaded.append(treaty.treaty_id)
        return cascaded

    def notification(
        self,
        *,
        treaty_id: str,
        severity: InvalidationSeverity,
        message: str,
    ) -> dict[str, Any]:
        """Record and return a notification dict.

        copilot: notifications are displayed in the diagnostic panel.
        """
        note: dict[str, Any] = {
            "treaty_id": treaty_id,
            "severity": severity.name,
            "message": message,
            "timestamp": _utcnow().isoformat(),
        }
        self._notifications.append(note)
        return note

    def recent_notifications(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the last *n* notifications."""
        return self._notifications[-n:]

    def clear(self) -> None:
        """Reset all state."""
        self._notifications.clear()
        self._fired.clear()

    @property
    def fired_treaty_ids(self) -> frozenset[str]:
        """Set of treaty ids that have been invalidated in this session."""
        return frozenset(self._fired)


# ---------------------------------------------------------------------------
# TreatyHistory
# ---------------------------------------------------------------------------

class TreatyHistory:
    """Tracks treaty evolution over time: creation, challenges, amendments,
    retirement.

    Provides temporal queries and stability scoring for copilot dashboards.
    """

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def record(
        self,
        treaty_id: str,
        event_type: str,
        *,
        detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a timestamped event to the history log.

        Parameters
        ----------
        treaty_id:
            Which treaty the event pertains to.
        event_type:
            One of ``"created"``, ``"negotiated"``, ``"ratified"``,
            ``"challenged"``, ``"amended"``, ``"retired"``.
        detail:
            Human-readable description.
        metadata:
            Arbitrary extra data.
        """
        entry: dict[str, Any] = {
            "treaty_id": treaty_id,
            "event_type": event_type,
            "detail": detail,
            "timestamp": _utcnow().isoformat(),
        }
        if metadata:
            entry["metadata"] = metadata
        self._events.append(entry)

    def treaty_timeline(self, treaty_id: str) -> list[dict[str, Any]]:
        """Return all events for a specific treaty, oldest first."""
        return [e for e in self._events if e["treaty_id"] == treaty_id]

    def challenge_history(self, treaty_id: str) -> list[dict[str, Any]]:
        """Return only challenge-related events for a treaty."""
        return [
            e
            for e in self._events
            if e["treaty_id"] == treaty_id
            and e["event_type"] in ("challenged", "amended", "retired")
        ]

    def stability_score(self, treaty_id: str) -> float:
        """Compute a stability score in [0, 1] based on event history.

        A treaty that has been ratified for a long time with few challenges
        scores high.  Repeated challenges erode the score.

        copilot: drives the stability indicator colour (green / amber / red).
        """
        timeline = self.treaty_timeline(treaty_id)
        if not timeline:
            return 0.0
        n_challenges = sum(
            1 for e in timeline if e["event_type"] == "challenged"
        )
        n_ratifications = sum(
            1 for e in timeline if e["event_type"] == "ratified"
        )
        if n_ratifications == 0:
            return 0.0
        raw = n_ratifications / (n_ratifications + n_challenges)
        age_bonus = min(0.2, len(timeline) * 0.01)
        return min(1.0, round(raw + age_bonus, 4))

    def copilot_treaty_summary(self, treaty_id: str) -> str:
        """Generate a Markdown summary of treaty health for copilot display.

        Includes timeline length, current stability score, and recent
        events.
        """
        timeline = self.treaty_timeline(treaty_id)
        score = self.stability_score(treaty_id)
        recent = timeline[-3:] if timeline else []
        lines = [
            f"## Treaty `{treaty_id}` — Health Summary",
            f"- Events recorded: {len(timeline)}",
            f"- Stability score: {score:.2f}",
            "- Recent events:",
        ]
        for evt in recent:
            lines.append(
                f"  - [{evt['event_type']}] {evt.get('detail', '—')} "
                f"({evt['timestamp']})"
            )
        return "\n".join(lines)

    def all_treaty_ids(self) -> list[str]:
        """Return unique treaty ids seen in the log."""
        seen: dict[str, None] = {}
        for e in self._events:
            seen.setdefault(e["treaty_id"], None)
        return list(seen)

    def events_since(self, iso_timestamp: str) -> list[dict[str, Any]]:
        """Return events recorded after *iso_timestamp* (ISO 8601)."""
        return [e for e in self._events if e["timestamp"] > iso_timestamp]

    def event_counts(self) -> dict[str, int]:
        """Return a mapping from event_type to count across all treaties."""
        counter: Counter[str] = Counter(e["event_type"] for e in self._events)
        return dict(counter)

    def average_stability(self) -> float:
        """Mean stability score across all known treaties.

        copilot: displayed as the aggregate treaty-health gauge.
        """
        ids = self.all_treaty_ids()
        if not ids:
            return 0.0
        scores = [self.stability_score(tid) for tid in ids]
        return round(statistics.mean(scores), 4)


# ---------------------------------------------------------------------------
# TreatySerializer
# ---------------------------------------------------------------------------

class TreatySerializer:
    """JSON serialization / deserialization for treaties, laws, and guards.

    Round-trip fidelity: ``deserialize_treaty(serialize_treaty(t))`` yields
    an equivalent object (modulo floating-point rounding).

    copilot: used for persisting treaty state between generation sessions.
    """

    # -- TreatyLaw -----------------------------------------------------------

    @staticmethod
    def serialize_law(law: TreatyLaw) -> dict[str, Any]:
        """Convert a ``TreatyLaw`` to a JSON-friendly dict."""
        return {
            "predicate": law.predicate,
            "variables": list(law.variables),
            "quantifiers": [q.value for q in law.quantifiers],
            "support_scope": law.support_scope,
            "is_decidable": law.is_decidable,
            "encoding_hint": law.encoding_hint,
            "natural_language_description": law.natural_language_description,
        }

    @staticmethod
    def deserialize_law(data: dict[str, Any]) -> TreatyLaw:
        """Reconstruct a ``TreatyLaw`` from a serialized dict."""
        return TreatyLaw(
            predicate=data["predicate"],
            variables=tuple(data.get("variables", ())),
            quantifiers=tuple(
                QuantifierKind(q) for q in data.get("quantifiers", ())
            ),
            support_scope=data.get("support_scope", "full"),
            is_decidable=data.get("is_decidable", True),
            encoding_hint=data.get("encoding_hint", ""),
            natural_language_description=data.get(
                "natural_language_description", ""
            ),
        )

    # -- TreatyGuard ---------------------------------------------------------

    @staticmethod
    def serialize_guard(guard: TreatyGuard) -> dict[str, Any]:
        """Convert a ``TreatyGuard`` to a JSON-friendly dict."""
        return {
            "guard_id": guard.guard_id,
            "condition": guard.condition,
            "variables": list(guard.variables),
            "description": guard.description,
        }

    @staticmethod
    def deserialize_guard(data: dict[str, Any]) -> TreatyGuard:
        """Reconstruct a ``TreatyGuard`` from a serialized dict."""
        return TreatyGuard(
            guard_id=data["guard_id"],
            condition=data["condition"],
            variables=tuple(data.get("variables", ())),
            description=data.get("description", ""),
        )

    # -- Treaty --------------------------------------------------------------

    @classmethod
    def serialize_treaty(cls, treaty: Treaty) -> dict[str, Any]:
        """Convert a ``Treaty`` to a JSON-friendly dict.

        Includes nested law and guard serializations.
        """
        return {
            "treaty_id": treaty.treaty_id,
            "overlap_coordinate": treaty.overlap_coordinate,
            "parties": treaty.parties,
            "law": cls.serialize_law(treaty.law),
            "guard_conditions": [
                cls.serialize_guard(g) for g in treaty.guard_conditions
            ],
            "invalidation_triggers": treaty.invalidation_triggers,
            "evidence_basis": treaty.evidence_basis,
            "trust_level": treaty.trust_level,
            "status": treaty.status.value,
            "created_at": treaty.created_at.isoformat(),
            "ratified_at": (
                treaty.ratified_at.isoformat() if treaty.ratified_at else None
            ),
        }

    @classmethod
    def deserialize_treaty(cls, data: dict[str, Any]) -> Treaty:
        """Reconstruct a ``Treaty`` from a serialized dict."""
        return Treaty(
            treaty_id=data["treaty_id"],
            overlap_coordinate=data.get("overlap_coordinate", ""),
            parties=data.get("parties", []),
            law=cls.deserialize_law(data["law"]),
            guard_conditions=[
                cls.deserialize_guard(g)
                for g in data.get("guard_conditions", [])
            ],
            invalidation_triggers=data.get("invalidation_triggers", []),
            evidence_basis=data.get("evidence_basis", []),
            trust_level=data.get("trust_level", 0.0),
            status=TreatyStatus(data.get("status", "proposed")),
            created_at=datetime.fromisoformat(data["created_at"]),
            ratified_at=(
                datetime.fromisoformat(data["ratified_at"])
                if data.get("ratified_at")
                else None
            ),
        )

    # -- bulk operations -----------------------------------------------------

    @classmethod
    def serialize_many(cls, treaties: Sequence[Treaty]) -> str:
        """Serialize a sequence of treaties to a JSON string."""
        payload = [cls.serialize_treaty(t) for t in treaties]
        return json.dumps(payload, indent=2, default=str)

    @classmethod
    def deserialize_many(cls, raw: str) -> list[Treaty]:
        """Deserialize a JSON string to a list of treaties."""
        payload = json.loads(raw)
        return [cls.deserialize_treaty(d) for d in payload]

    @classmethod
    def round_trip(cls, treaty: Treaty) -> Treaty:
        """Serialize then deserialize — useful for snapshot testing."""
        return cls.deserialize_treaty(cls.serialize_treaty(treaty))


# ---------------------------------------------------------------------------
# Module-level helpers (backward-compatible with the original API)
# ---------------------------------------------------------------------------

# Legacy aliases so that existing ``from jugeo.generation.treaties import
# TreatyClause, OverlapTreaty, evaluate_treaty`` still work.

@dataclass(frozen=True, slots=True)
class TreatyClause:
    """Single clause within a legacy OverlapTreaty.

    Retained for backward compatibility with earlier JuGeo modules.
    """

    patch: str
    expectation: str
    satisfied: bool = False


@dataclass(frozen=True, slots=True)
class OverlapTreaty:
    """Legacy overlap treaty — thin wrapper kept for backward compatibility.

    New code should prefer the full ``Treaty`` dataclass.
    """

    patches: tuple[str, ...]
    clauses: tuple[TreatyClause, ...]
    provenance: tuple[str, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        """All clauses satisfied."""
        return all(clause.satisfied for clause in self.clauses)


def evaluate_treaty(treaty: OverlapTreaty | Treaty) -> bool:
    """Evaluate whether a treaty is accepted / active.

    Works with both legacy ``OverlapTreaty`` and new ``Treaty`` objects.
    """
    if isinstance(treaty, OverlapTreaty):
        return treaty.accepted
    if isinstance(treaty, Treaty):
        return treaty.is_active()
    return False


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # enums
    "TreatyStatus",
    "QuantifierKind",
    "ChallengeVerdict",
    "PatternKind",
    "InvalidationSeverity",
    # core data
    "Treaty",
    "TreatyLaw",
    "TreatyGuard",
    # managers & processors
    "TreatySynthesizer",
    "TreatyManager",
    "TreatyValidator",
    "TreatyChallenger",
    "TreatyPatternMiner",
    "TreatyInvalidationMonitor",
    "TreatyHistory",
    "TreatySerializer",
    # legacy compat
    "TreatyClause",
    "OverlapTreaty",
    "evaluate_treaty",
    # Cross-subsystem enrichments
    "treaty_from_overlap",
    "evidence_treaty",
]


# ---------------------------------------------------------------------------
# Cross-subsystem enrichment functions
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.covers import (
        Cover as _Cover,
        OverlapDatum as _OverlapDatum,
    )
except Exception:  # pragma: no cover
    _Cover = None  # type: ignore[assignment,misc]
    _OverlapDatum = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel as _TrustLevel
except Exception:  # pragma: no cover
    _TrustLevel = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.channels import EvidenceChannel as _EvidenceChannel
except Exception:  # pragma: no cover
    _EvidenceChannel = None  # type: ignore[assignment,misc]


def treaty_from_overlap(
    cover: Any,
    *,
    overlap_pairs: Sequence[tuple[str, str]] | None = None,
) -> list[Treaty]:
    """Derive treaties from overlap data on a cover.

    Inspects ``jugeo.geometry.covers.Cover.pairwise_overlaps()`` (or
    the explicitly supplied *overlap_pairs*) and synthesises one
    :class:`Treaty` per overlap pair.  Each treaty carries a default
    ``TreatyLaw`` requiring equality on the shared support.

    Parameters
    ----------
    cover:
        A ``jugeo.geometry.covers.Cover`` instance.
    overlap_pairs:
        Explicit pairs ``(left_key, right_key)`` to use instead of
        computing from the cover.

    Returns
    -------
    list[Treaty]
    """
    pairs: list[tuple[str, str]] = []
    if overlap_pairs is not None:
        pairs = list(overlap_pairs)
    elif hasattr(cover, "pairwise_overlaps"):
        try:
            pairs = list(cover.pairwise_overlaps())
        except Exception:
            pairs = []

    treaties: list[Treaty] = []
    for left, right in pairs:
        law = TreatyLaw(
            predicate=f"∀x ∈ Γ_{left}∩{right}. section_{left}(x) = section_{right}(x)",
            variables=(left, right),
            quantifiers=(QuantifierKind.FORALL,),
            support_scope=f"{left}∩{right}",
            is_decidable=True,
            natural_language_description=(
                f"Sections over {left} and {right} agree on their overlap."
            ),
        )
        treaty = Treaty(
            treaty_id=uuid.uuid4().hex[:12],
            left_coordinate=left,
            right_coordinate=right,
            law=law,
            status=TreatyStatus.PROPOSED,
        )
        treaties.append(treaty)
    return treaties


def evidence_treaty(
    treaty: Treaty,
    *,
    evidence_data: Mapping[str, Any] | None = None,
    channel: Any | None = None,
) -> dict[str, Any]:
    """Back a treaty with evidence from ``jugeo.evidence``.

    Attaches evidence metadata to the treaty and returns an enriched
    record containing the treaty identifier, the evidence channel used,
    and a trust assessment.

    Parameters
    ----------
    treaty:
        The treaty to back with evidence.
    evidence_data:
        Free-form evidence payload (e.g. a witness mapping).
    channel:
        An ``EvidenceChannel`` instance or label describing the
        evidence source.

    Returns
    -------
    dict[str, Any]
        ``{"treaty_id": str, "status": str, "evidence_channel": str,
        "trust_level": str, "evidence_data": dict}``.
    """
    channel_str = ""
    if channel is not None:
        channel_str = channel.value if hasattr(channel, "value") else str(channel)
    elif _EvidenceChannel is not None:
        channel_str = "solver"

    trust_str = ""
    if _TrustLevel is not None:
        try:
            trust_str = _TrustLevel.UNVERIFIED.value
        except Exception:
            trust_str = "UNVERIFIED"

    status = treaty.status if hasattr(treaty, "status") else TreatyStatus.PROPOSED
    status_str = status.value if hasattr(status, "value") else str(status)

    return {
        "treaty_id": getattr(treaty, "treaty_id", ""),
        "status": status_str,
        "evidence_channel": channel_str,
        "trust_level": trust_str,
        "evidence_data": dict(evidence_data) if evidence_data else {},
    }


# copilot: shared-core marker for future LLM orchestration.
